//! Closed /rp-p0-fs/access P0 channel, not a host-FS or product authorization API.
//! Queries consult the same request's fixed mounts without opening target files.

use std::future::Future;
use std::io::{self, SeekFrom};
use std::path::{Component, Path, PathBuf};
use std::pin::Pin;
use std::sync::{Arc, Mutex, MutexGuard};
use std::task::{Context, Poll};

use anyhow::{Result, ensure};
use tokio::io::{AsyncRead, AsyncSeek, AsyncWrite, ReadBuf};
use wasmer_wasix::virtual_fs::{
    FileOpener, FileSystem, FileType, FsError, Metadata, OpenOptions, OpenOptionsConfig, ReadDir,
    VirtualFile,
};

use recastpress_wp_source_fs_probe::SourceFs;

pub(crate) const CHANNEL_NAME: &str = "access";
pub(crate) const MAX_PATH_BYTES: usize = 4096;
pub(crate) const MAX_FRAME_BYTES: usize = 7 + MAX_PATH_BYTES;
pub(crate) const MAX_OPERATIONS: usize = 16384;
pub(crate) const MAX_REQUEST_BYTES: usize = MAX_OPERATIONS * MAX_FRAME_BYTES;
pub(crate) const MAX_RESPONSE_BYTES: usize = MAX_OPERATIONS * 5;
// Includes byte-at-a-time request/reply IO, seeks, flushes and EOF probes.
const MAX_IO_CALLS: usize = MAX_REQUEST_BYTES + MAX_RESPONSE_BYTES * 2 + MAX_OPERATIONS * 16;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
enum Status {
    Allow = 0,
    Denied = 1,
    Missing = 2,
    Unmanaged = 3,
    Invalid = 4,
    Io = 5,
}

#[derive(Clone, Debug)]
struct Policy { wp:SourceFs,php:SourceFs,context:SourceFs, invalid_reasons:Arc<Mutex<InvalidReasons>> }
const INVALID_REASON_NAMES:[&str;9]=["double_leading_slash","colon","backslash","control","metadata_invalid","invalid_utf8","not_absolute","path_length","mode"];
#[derive(Debug,Default)]
struct InvalidReasons {counts:[usize;9]}
impl Policy {
    fn invalid(&self,reason:usize)->Status {
        let Ok(mut reasons)=self.invalid_reasons.lock() else{return Status::Io};
        reasons.counts[reason]=reasons.counts[reason].saturating_add(1);Status::Invalid
    }
    fn query(&self,bytes:&[u8],mode:u8)->Status {
        if mode>7{return self.invalid(8);}
        if bytes.is_empty() || bytes.len()>MAX_PATH_BYTES {return self.invalid(7);}
        let Ok(path)=std::str::from_utf8(bytes) else{return self.invalid(5)};
        if !path.starts_with('/'){return self.invalid(6);}
        if path.starts_with("//"){return self.invalid(0);}
        if path.contains(':'){return self.invalid(1);}
        if path.contains('\\'){return self.invalid(2);}
        if path.contains('\0') || path.chars().any(char::is_control){return self.invalid(3);}
        let mount=|root:&str| path.strip_prefix(root).filter(|rest|rest.is_empty()||rest.starts_with('/'));
        let selected=if let Some(p)=mount("/wordpress"){Some((&self.wp,p))}
            else if let Some(p)=mount("/experiments/php-wasix-wp-request-probe/fixtures"){Some((&self.php,p))}
            else {mount("/rp-request").map(|p|(&self.context,p))};
        let Some((fs,entry))=selected else{return Status::Unmanaged};
        // Pass the remaining lexical path unchanged to the SAME frozen tree.
        // Missing prefixes/file traversal/root escape are never normalized away.
        match fs.metadata(Path::new(if entry.is_empty(){"/"}else{entry})) {
            Ok(meta)=>if mode&2!=0 || (mode&1!=0 && !meta.is_dir()){Status::Denied}else{Status::Allow},
            Err(FsError::PermissionDenied)=>Status::Denied,
            Err(FsError::EntryNotFound|FsError::BaseNotDirectory|FsError::NotAFile)=>Status::Missing,
            Err(FsError::InvalidInput)=>self.invalid(4),
            Err(_)=>Status::Io,
        }
    }
}

#[derive(Debug)]
struct State {
    policy: Policy,
    pending: Vec<u8>,
    response: Option<[u8; 5]>,
    response_read: usize,
    requests: usize,
    allowed: usize,
    denied: usize,
    missing: usize,
    unmanaged: usize,
    invalid: usize,
    io_errors: usize,
    input_bytes: usize,
    generated_bytes: usize,
    read_bytes: usize,
    active_handles: usize,
    closed_handles: usize,
    open_events: usize,
    reused_transactions: usize,
    eof_reads: usize,
    io_calls: usize,
    finished: bool,
    protocol_error: Option<&'static str>,
}

impl State {
    fn fail(&mut self, reason: &'static str) -> io::Error {
        self.protocol_error.get_or_insert(reason);
        io::Error::new(io::ErrorKind::InvalidData, reason)
    }

    fn live(&mut self) -> io::Result<()> {
        if self.finished || self.active_handles != 1 {
            return Err(self.fail("access channel is not active"));
        }
        Ok(())
    }

    fn io_call(&mut self) -> io::Result<()> {
        self.live()?;
        if self.io_calls >= MAX_IO_CALLS {
            return Err(self.fail("access IO call limit"));
        }
        self.io_calls += 1;
        Ok(())
    }

    fn writable(&mut self) -> io::Result<()> {
        if let Some(reason) = self.protocol_error {
            return Err(io::Error::new(io::ErrorKind::InvalidData, reason));
        }
        Ok(())
    }

    fn receive(&mut self, bytes: &[u8]) -> io::Result<()> {
        self.writable()?;
        if bytes.is_empty() {
            return Ok(());
        }
        if self.response.is_some() {
            if self.response_read != 5 {
                return Err(self.fail("access next request before reply consumed"));
            }
            self.response = None;
            self.response_read = 0;
            self.reused_transactions += 1;
        }
        if self.requests == MAX_OPERATIONS {
            return Err(self.fail("access operation limit"));
        }
        if bytes.len() > MAX_FRAME_BYTES.saturating_sub(self.pending.len())
            || bytes.len() > MAX_REQUEST_BYTES.saturating_sub(self.input_bytes)
        {
            return Err(self.fail("access frame/request byte limit"));
        }
        self.input_bytes += bytes.len();
        self.pending.extend_from_slice(bytes);
        if self.pending.len() >= 4 && &self.pending[..4] != b"RPA1" {
            return Err(self.fail("access request magic"));
        }
        if self.pending.len() >= 5 && self.pending[4] > 7 {
            return Err(self.fail("access request mode"));
        }
        if self.pending.len() < 7 {
            return Ok(());
        }
        let length = usize::from(u16::from_le_bytes([self.pending[5], self.pending[6]]));
        if length == 0 || length > MAX_PATH_BYTES {
            return Err(self.fail("access path length"));
        }
        let expected = 7 + length;
        if self.pending.len() > expected {
            return Err(self.fail("access trailing bytes or concatenated frame"));
        }
        if self.pending.len() < expected {
            return Ok(());
        }
        let status = self.policy.query(&self.pending[7..], self.pending[4]);
        self.requests += 1;
        match status {
            Status::Allow => self.allowed += 1,
            Status::Denied => self.denied += 1,
            Status::Missing => self.missing += 1,
            Status::Unmanaged => self.unmanaged += 1,
            Status::Invalid => self.invalid += 1,
            Status::Io => self.io_errors += 1,
        }
        if self.generated_bytes > MAX_RESPONSE_BYTES - 5 {
            return Err(self.fail("access generated reply byte limit"));
        }
        self.generated_bytes += 5;
        self.response = Some([b'R', b'P', b'R', b'1', status as u8]);
        self.pending.clear();
        Ok(())
    }

    fn readable_bytes(&mut self, position: usize, readback_ready: bool) -> usize {
        if self.protocol_error.is_some() {
            return 0;
        }
        if !self.pending.is_empty() || self.response.is_none() || !readback_ready {
            self.fail("access read before complete request and seek zero");
            return 0;
        }
        if position != self.response_read {
            self.fail("access reply replay or cursor mismatch");
            return 0;
        }
        5 - self.response_read
    }
}

/// Clones share one request-local channel. Construct a new provider per request.
#[derive(Clone, Debug)]
pub(crate) struct ManagedAccessFs(Arc<Mutex<State>>);

#[derive(Clone, Debug, PartialEq, Eq)]
// Handle counters cover VFiles created here, not aliases in WASIX's fd/inode cache.
pub(crate) struct AccessSnapshot {
    pub requests: usize,
    pub allowed: usize,
    pub denied: usize,
    pub missing: usize,
    pub unmanaged: usize,
    pub invalid: usize,
    pub io_errors: usize,
    pub input_bytes: usize,
    pub generated_bytes: usize,
    pub read_bytes: usize,
    pub pending_bytes: usize,
    pub unread_response_bytes: usize,
    pub active_handles: usize,
    pub closed_handles: usize,
    pub open_events: usize,
    pub reused_transactions: usize,
    pub eof_reads: usize,
    pub io_calls: usize,
    pub finished: bool,
    pub protocol_error: Option<&'static str>,
}

impl AccessSnapshot {
    pub(crate) fn report_value(&self) -> serde_json::Value {
        serde_json::json!({"requests":self.requests,"allowed":self.allowed,"denied":self.denied,"missing":self.missing,"unmanaged":self.unmanaged,"invalid":self.invalid,"io_errors":self.io_errors,"input_bytes":self.input_bytes,"generated_bytes":self.generated_bytes,"read_bytes":self.read_bytes,"pending_bytes":self.pending_bytes,"unread_response_bytes":self.unread_response_bytes,"active_handles":self.active_handles,"closed_handles":self.closed_handles,"open_events":self.open_events,"reused_transactions":self.reused_transactions,"eof_reads":self.eof_reads,"io_calls":self.io_calls,"finished":self.finished,"protocol_error":self.protocol_error})
    }
}

impl ManagedAccessFs {
    pub(crate) fn new(wp:SourceFs,php:SourceFs,context:SourceFs)->Result<Self> {
        Ok(Self(Arc::new(Mutex::new(State {
            policy: Policy {wp,php,context,invalid_reasons:Arc::new(Mutex::new(InvalidReasons::default()))},
            pending: Vec::new(),
            response: None,
            response_read: 0,
            requests: 0,
            allowed: 0,
            denied: 0,
            missing: 0,
            unmanaged: 0,
            invalid: 0,
            io_errors: 0,
            input_bytes: 0,
            generated_bytes: 0,
            read_bytes: 0,
            active_handles: 0,
            closed_handles: 0,
            open_events: 0,
            reused_transactions: 0,
            eof_reads: 0,
            io_calls: 0,
            finished: false,
            protocol_error: None,
        }))))
    }

    fn lock(&self) -> io::Result<MutexGuard<'_, State>> {
        self.0
            .lock()
            .map_err(|_| io::Error::other("access state poisoned"))
    }

    /// Counts only; paths and path hashes never enter a public observation.
    pub(crate) fn invalid_reason_counts(&self)->Result<serde_json::Value> {
        let state=self.lock()?;
        let reasons=state.policy.invalid_reasons.lock().map_err(|_|io::Error::other("access reason state poisoned"))?;
        Ok(serde_json::Value::Object(INVALID_REASON_NAMES.iter().zip(reasons.counts).map(|(name,n)|((*name).to_owned(),serde_json::json!(n))).collect()))
    }

    pub(crate) fn snapshot(&self) -> Result<AccessSnapshot> {
        let state = self.lock()?;
        Ok(AccessSnapshot {
            requests: state.requests,
            allowed: state.allowed,
            denied: state.denied,
            missing: state.missing,
            unmanaged: state.unmanaged,
            invalid: state.invalid,
            io_errors: state.io_errors,
            input_bytes: state.input_bytes,
            generated_bytes: state.generated_bytes,
            read_bytes: state.read_bytes,
            pending_bytes: state.pending.len(),
            unread_response_bytes: if state.response.is_some() {
                5 - state.response_read
            } else {
                0
            },
            active_handles: state.active_handles,
            closed_handles: state.closed_handles,
            open_events: state.open_events,
            reused_transactions: state.reused_transactions,
            eof_reads: state.eof_reads,
            io_calls: state.io_calls,
            finished: state.finished,
            protocol_error: state.protocol_error,
        })
    }

    /// Seal only after the runner/runtime/underlying VirtualFile has dropped.
    /// Zero requests are permitted here; the fixed host comparison must require
    /// its expected positive query count before claiming the provider was used.
    pub(crate) fn finish(&self) -> Result<AccessSnapshot> {
        {
            let mut state = self.lock()?;
            if state.active_handles != 0 {
                state.fail("access channel still active at completion");
            }
            if !state.pending.is_empty() {
                state.fail("access incomplete frame at completion");
            }
            if state.response.is_some() && state.response_read != 5 {
                state.fail("access unread reply at completion");
            }
            state.finished = true;
        }
        let snapshot = self.snapshot()?;
        ensure!(
            snapshot.protocol_error.is_none(),
            "access protocol failed: {:?}",
            snapshot.protocol_error
        );
        Ok(snapshot)
    }

    fn deny(&self, reason: &'static str) -> FsError {
        if let Ok(mut state) = self.lock() {
            state.fail(reason);
        }
        FsError::PermissionDenied
    }
}

fn channel_entry(path: &Path) -> std::result::Result<bool, FsError> {
    let raw = path.to_str().ok_or(FsError::EntryNotFound)?;
    if raw.contains(['\\', ':', '\0']) {
        return Err(FsError::EntryNotFound);
    }
    let mut name = None;
    for part in path.components() {
        match part {
            Component::RootDir | Component::CurDir => {}
            Component::Normal(value) if name.is_none() => name = Some(value),
            _ => return Err(FsError::EntryNotFound),
        }
    }
    match name {
        None => Ok(false),
        Some(name) if name == CHANNEL_NAME => Ok(true),
        _ => Err(FsError::EntryNotFound),
    }
}

impl FileOpener for ManagedAccessFs {
    fn open(
        &self,
        path: &Path,
        options: &OpenOptionsConfig,
    ) -> std::result::Result<Box<dyn VirtualFile + Send + Sync>, FsError> {
        if !channel_entry(path)? {
            return Err(FsError::NotAFile);
        }
        if !options.read
            || !options.write
            || options.create
            || options.create_new
            || options.append
            || options.truncate
        {
            return Err(self.deny("access requires existing read/write channel"));
        }
        let mut state = self.lock().map_err(|_| FsError::IOError)?;
        if state.active_handles != 0
            || state.finished
            || state.protocol_error.is_some()
            || state.open_events == MAX_OPERATIONS
        {
            state.fail("access channel concurrent, failed, finished or open limit");
            return Err(FsError::PermissionDenied);
        }
        state.active_handles = 1;
        state.open_events += 1;
        Ok(Box::new(Channel {
            fs: self.clone(),
            position: 0,
            readback_ready: false,
            closed: false,
        }))
    }
}

impl FileSystem for ManagedAccessFs {
    fn readlink(&self, _: &Path) -> std::result::Result<PathBuf, FsError> {
        Err(FsError::InvalidInput)
    }
    fn read_dir(&self, _: &Path) -> std::result::Result<ReadDir, FsError> {
        Err(FsError::PermissionDenied)
    }
    fn metadata(&self, path: &Path) -> std::result::Result<Metadata, FsError> {
        let file = channel_entry(path)?;
        let length = if file
            && self
                .lock()
                .map_err(|_| FsError::IOError)?
                .response
                .is_some()
        {
            5
        } else {
            0
        };
        Ok(Metadata {
            ft: FileType {
                file,
                dir: !file,
                ..FileType::default()
            },
            accessed: 0,
            created: 0,
            modified: 0,
            len: length,
        })
    }
    fn symlink_metadata(&self, path: &Path) -> std::result::Result<Metadata, FsError> {
        self.metadata(path)
    }
    fn new_open_options(&self) -> OpenOptions<'_> {
        OpenOptions::new(self)
    }
    fn create_dir(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny("access create_dir forbidden"))
    }
    fn create_symlink(&self, _: &Path, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny("access symlink forbidden"))
    }
    fn hard_link(&self, _: &Path, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny("access hardlink forbidden"))
    }
    fn remove_dir(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny("access remove_dir forbidden"))
    }
    fn remove_file(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny("access remove_file forbidden"))
    }
    fn rename<'a>(
        &'a self,
        _: &'a Path,
        _: &'a Path,
    ) -> Pin<Box<dyn Future<Output = std::result::Result<(), FsError>> + Send + 'a>> {
        Box::pin(async { Err(self.deny("access rename forbidden")) })
    }
}

#[derive(Debug)]
struct Channel {
    fs: ManagedAccessFs,
    position: usize,
    readback_ready: bool,
    closed: bool,
}

impl Channel {
    fn check_open(&self) -> io::Result<()> {
        if self.closed {
            return Err(self.fs.lock()?.fail("access use after handle close"));
        }
        Ok(())
    }
    fn close(&mut self) -> io::Result<()> {
        if self.closed {
            return Ok(());
        }
        self.closed = true;
        let mut state = self.fs.lock()?;
        state.active_handles = 0;
        state.closed_handles += 1;
        if !state.pending.is_empty() {
            return Err(state.fail("access incomplete frame on close"));
        }
        if state.response.is_some() && state.response_read != 5 {
            return Err(state.fail("access unread reply on close"));
        }
        Ok(())
    }
}
impl Drop for Channel {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

impl AsyncRead for Channel {
    fn poll_read(
        self: Pin<&mut Self>,
        _: &mut Context<'_>,
        buffer: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        this.check_open()?;
        let mut state = this.fs.lock()?;
        state.io_call()?;
        if buffer.remaining() == 0 {
            return Poll::Ready(Ok(()));
        }
        let remaining = state.readable_bytes(this.position, this.readback_ready);
        if remaining == 0 {
            state.eof_reads += 1;
            return Poll::Ready(Ok(()));
        }
        let count = remaining.min(buffer.remaining());
        if count > MAX_RESPONSE_BYTES.saturating_sub(state.read_bytes) {
            return Poll::Ready(Err(state.fail("access reply read byte limit")));
        }
        let reply = state.response.expect("readable_bytes requires a reply");
        buffer.put_slice(&reply[this.position..this.position + count]);
        this.position += count;
        state.response_read += count;
        state.read_bytes += count;
        Poll::Ready(Ok(()))
    }
}
impl AsyncWrite for Channel {
    fn poll_write(
        self: Pin<&mut Self>,
        _: &mut Context<'_>,
        bytes: &[u8],
    ) -> Poll<io::Result<usize>> {
        let this = self.get_mut();
        this.check_open()?;
        let mut state = this.fs.lock()?;
        state.io_call()?;
        let starting =
            state.pending.is_empty() && (state.response.is_none() || state.response_read == 5);
        state.receive(bytes)?;
        if !bytes.is_empty() {
            if starting {
                this.position = 0;
            }
            this.position += bytes.len();
            this.readback_ready = false;
        }
        Poll::Ready(Ok(bytes.len()))
    }
    fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
        self.check_open()?;
        let mut state = self.fs.lock()?;
        state.io_call()?;
        Poll::Ready(state.writable())
    }
    fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
        Poll::Ready(self.get_mut().close())
    }
}
impl AsyncSeek for Channel {
    fn start_seek(self: Pin<&mut Self>, seek: SeekFrom) -> io::Result<()> {
        let this = self.get_mut();
        this.check_open()?;
        let mut state = this.fs.lock()?;
        state.io_call()?;
        state.writable()?;
        match seek {
            // WASIX synchronizes the VFile cursor before every fragmented IO.
            SeekFrom::Start(offset) if offset == this.position as u64 => Ok(()),
            SeekFrom::Start(0) if state.pending.is_empty() => {
                this.position = 0;
                this.readback_ready = state.response.is_some();
                Ok(())
            }
            SeekFrom::Current(0) => Ok(()),
            _ => Err(state.fail("access unsupported seek")),
        }
    }
    fn poll_complete(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<u64>> {
        self.check_open()?;
        let mut state = self.fs.lock()?;
        state.io_call()?;
        state.writable()?;
        Poll::Ready(Ok(self.position as u64))
    }
}
impl VirtualFile for Channel {
    fn last_accessed(&self) -> u64 {
        0
    }
    fn last_modified(&self) -> u64 {
        0
    }
    fn created_time(&self) -> u64 {
        0
    }
    fn size(&self) -> u64 {
        self.fs
            .lock()
            .map(|s| if s.response.is_some() { 5 } else { 0 })
            .unwrap_or(0)
    }
    fn is_open(&self) -> bool {
        !self.closed
    }
    fn set_times(&mut self, _: Option<u64>, _: Option<u64>) -> std::result::Result<(), FsError> {
        Err(self.fs.deny("access metadata mutation forbidden"))
    }
    fn set_len(&mut self, _: u64) -> std::result::Result<(), FsError> {
        Err(self.fs.deny("access truncate forbidden"))
    }
    fn unlink(&mut self) -> std::result::Result<(), FsError> {
        Err(self.fs.deny("access unlink forbidden"))
    }
    fn poll_read_ready(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<usize>> {
        self.check_open()?;
        let mut state = self.fs.lock()?;
        state.io_call()?;
        Poll::Ready(Ok(state.readable_bytes(self.position, self.readback_ready)))
    }
    fn poll_write_ready(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<usize>> {
        self.check_open()?;
        let mut state = self.fs.lock()?;
        state.io_call()?;
        state.writable()?;
        if state.response.is_some() && state.response_read != 5 {
            return Poll::Ready(Err(
                state.fail("access write readiness before reply consumed")
            ));
        }
        if state.requests == MAX_OPERATIONS {
            return Poll::Ready(Err(state.fail("access operation limit")));
        }
        Poll::Ready(Ok(MAX_FRAME_BYTES
            .saturating_sub(state.pending.len())
            .min(MAX_REQUEST_BYTES.saturating_sub(state.input_bytes))))
    }
}


#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::{BTreeMap,BTreeSet};
    use tokio::io::{AsyncReadExt,AsyncSeekExt,AsyncWriteExt};
    fn source()->SourceFs {SourceFs::new(BTreeMap::from([("file.php".to_owned(),Arc::<[u8]>::from(&b"<?php"[..])),("dir/child.php".to_owned(),Arc::<[u8]>::from(&b"<?php"[..]))]),BTreeSet::from(["dir".to_owned()])).unwrap()}
    fn fixture_fs()->ManagedAccessFs {ManagedAccessFs::new(source(),source(),source()).unwrap()}
    fn frame(path:&str,mode:u8)->Vec<u8>{let mut b=b"RPA1".to_vec();b.push(mode);b.extend_from_slice(&(path.len() as u16).to_le_bytes());b.extend_from_slice(path.as_bytes());b}
    #[test]
    fn invalid_reason_counts_retain_rejections_without_paths() {
        let fs=fixture_fs();
        {let s=fs.lock().unwrap();let p=&s.policy;
            for path in ["//wordpress/file.php","/wordpress/colon:file","/wordpress\\file.php","/wordpress/\0file","/wordpress//file.php"] {
                assert_eq!(p.query(path.as_bytes(),4),Status::Invalid);
            }
        }
        let report=fs.invalid_reason_counts().unwrap();
        for name in ["double_leading_slash","colon","backslash","control","metadata_invalid"] {assert_eq!(report[name],1);}
        assert_eq!(report.as_object().unwrap().values().map(|v|v.as_u64().unwrap()).sum::<u64>(),5);
        assert!(!report.to_string().contains("file.php"));
    }
    #[test]
    fn same_readonly_catalog_exists_readable_and_write_denial() {
        let fs=fixture_fs();let state=fs.lock().unwrap();let p=&state.policy;
        for root in ["/wordpress","/experiments/php-wasix-wp-request-probe/fixtures","/rp-request"] {
            assert_eq!(p.query(format!("{root}/file.php").as_bytes(),0),Status::Allow);
            assert_eq!(p.query(format!("{root}/file.php").as_bytes(),4),Status::Allow);
            assert_eq!(p.query(format!("{root}/file.php").as_bytes(),2),Status::Denied);
            assert_eq!(p.query(format!("{root}/file.php").as_bytes(),1),Status::Denied);
            assert_eq!(p.query(format!("{root}/dir").as_bytes(),5),Status::Allow);
            assert_eq!(p.query(format!("{root}/missing").as_bytes(),0),Status::Missing);
        }
        assert_eq!(p.query(b"/",0),Status::Unmanaged);assert_eq!(p.query(b"/tmp",2),Status::Unmanaged);
        assert_eq!(p.query(b"/wordpress-not-mounted/file.php",4),Status::Unmanaged);
        assert_eq!(p.query(b"/wordpress/../file.php",4),Status::Denied);
        assert_eq!(p.query(b"/wordpress/file.php/../file.php",4),Status::Missing);
        assert_eq!(p.query(b"/wordpress\\file.php",4),Status::Invalid);
    }
    #[test]
    fn permission_transaction_complete_reply_eof_close_and_finite_counts() {
        let rt=tokio::runtime::Builder::new_current_thread().build().unwrap();rt.block_on(async {
            let fs=fixture_fs();
            for (mode,status) in [(4,0u8),(2,1)] {
                let mut f=fs.new_open_options().read(true).write(true).open("/access").unwrap();
                f.write_all(&frame("/wordpress/file.php",mode)).await.unwrap();f.flush().await.unwrap();f.seek(SeekFrom::Start(0)).await.unwrap();
                let mut b=[0;5];f.read_exact(&mut b).await.unwrap();assert_eq!(&b[..4],b"RPR1");assert_eq!(b[4],status);
                assert_eq!(f.read(&mut [0]).await.unwrap(),0);drop(f);
            }
            let s=fs.finish().unwrap();assert_eq!(s.requests,2);assert_eq!((s.allowed,s.denied),(1,1));assert_eq!((s.generated_bytes,s.read_bytes),(10,10));assert_eq!((s.open_events,s.closed_handles,s.active_handles),(2,2,0));
            assert_eq!(s.pending_bytes,0);assert_eq!(s.unread_response_bytes,0);
        });
    }
    #[test]
    fn incomplete_and_unread_access_transactions_cannot_finish() {
        let rt=tokio::runtime::Builder::new_current_thread().build().unwrap();rt.block_on(async {
            let fs=fixture_fs();let mut f=fs.new_open_options().read(true).write(true).open("/access").unwrap();f.write_all(b"RPA").await.unwrap();drop(f);assert!(fs.finish().is_err());
            let fs=fixture_fs();let mut f=fs.new_open_options().read(true).write(true).open("/access").unwrap();f.write_all(&frame("/wordpress/file.php",4)).await.unwrap();drop(f);assert!(fs.finish().is_err());
        });
    }
}
