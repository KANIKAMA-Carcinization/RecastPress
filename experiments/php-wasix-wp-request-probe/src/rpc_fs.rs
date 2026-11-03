//! One request-local private async file transaction channel; no host paths.
use crate::db::{self, Dispatch, Job};
use serde_json::{Value, json};
use std::{
    future::Future,
    io::{self, SeekFrom},
    path::{Component, Path, PathBuf},
    pin::Pin,
    sync::{Arc, Mutex, MutexGuard},
    task::{Context, Poll},
};
use tokio::{
    io::{AsyncRead, AsyncSeek, AsyncWrite, ReadBuf},
    sync::oneshot,
};
use wasmer_wasix::virtual_fs::{
    FileOpener, FileSystem, FileType, FsError, Metadata, OpenOptions, OpenOptionsConfig, ReadDir,
    VirtualFile,
};
const CHANNEL_NAME: &str = "channel";
struct State {
    nonce: String,
    dispatch: Dispatch,
    pending: Vec<u8>,
    response: Vec<u8>,
    waiting: Option<oneshot::Receiver<db::Result<Vec<u8>>>>,
    request_bytes: usize,
    operations: usize,
    response_bytes: usize,
    response_read_bytes: usize,
    consumed: usize,
    open: bool,
    opened_once: bool,
    finished: bool,
    close_dispatched: bool,
    failure: Option<&'static str>,
}
impl std::fmt::Debug for State {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("PrivateRpcState")
    }
}
impl State {
    fn fail(&mut self, reason: &'static str) -> io::Error {
        self.failure.get_or_insert(reason);
        io::Error::new(io::ErrorKind::InvalidData, reason)
    }
    fn active(&mut self) -> io::Result<()> {
        if let Some(reason) = self.failure {
            return Err(io::Error::other(reason));
        }
        if self.finished || !self.open {
            return Err(self.fail("rpc_not_active"));
        }
        Ok(())
    }
    fn receive(&mut self, bytes: &[u8]) -> io::Result<()> {
        self.active()?;
        if bytes.is_empty() {
            return Ok(());
        }
        if self.close_dispatched || self.waiting.is_some() || self.consumed != self.response.len() {
            return Err(self.fail("rpc_new_request_before_reply_consumed"));
        }
        if bytes.len() > db::REQUEST_TOTAL.saturating_sub(self.request_bytes)
            || bytes.len() > db::FRAME.saturating_sub(self.pending.len())
        {
            return Err(self.fail("rpc_request_quota"));
        }
        self.request_bytes += bytes.len();
        self.pending.extend_from_slice(bytes);
        if let Some(index) = self.pending.iter().position(|&b| b == b'\n') {
            if index + 1 != self.pending.len() {
                return Err(self.fail("rpc_multiple_frames"));
            }
            let parsed = db::parse_request(&self.pending, &self.nonce, self.operations as u64 + 1);
            let (request, data) = match parsed {
                Ok(v) => v,
                Err(e) => return Err(self.fail(e)),
            };
            let closing = request.op == "close";
            let (send, receive) = oneshot::channel();
            let job = Job {
                request,
                bytes: data,
                frame_bytes: self.pending.len(),
                frame_sha256: db::sha(&self.pending),
                reply: send,
            };
            if self.dispatch.0.try_send(job).is_err() {
                return Err(self.fail("rpc_actor_dispatch_failed"));
            }
            self.operations += 1;
            self.pending.clear();
            self.response.clear();
            self.consumed = 0;
            self.waiting = Some(receive);
            self.close_dispatched = closing;
        }
        Ok(())
    }
    fn poll_response(&mut self, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        if let Some(receiver) = self.waiting.as_mut() {
            let result = match Pin::new(receiver).poll(cx) {
                Poll::Pending => return Poll::Pending,
                Poll::Ready(v) => v,
            };
            self.waiting = None;
            match result {
                Ok(Ok(bytes)) => {
                    if bytes.len() > db::REPLY
                        || bytes.len() > db::REPLY_TOTAL.saturating_sub(self.response_bytes)
                    {
                        return Poll::Ready(Err(self.fail("rpc_reply_quota")));
                    }
                    self.response_bytes += bytes.len();
                    self.response = bytes;
                }
                Ok(Err(reason)) => return Poll::Ready(Err(self.fail(reason))),
                Err(_) => return Poll::Ready(Err(self.fail("rpc_actor_reply_lost"))),
            }
        }
        Poll::Ready(Ok(()))
    }
}
#[derive(Clone, Debug)]
pub struct RpcFs(Arc<Mutex<State>>);
impl RpcFs {
    pub fn new(nonce: String, dispatch: Dispatch) -> Self {
        Self(Arc::new(Mutex::new(State {
            nonce,
            dispatch,
            pending: vec![],
            response: vec![],
            waiting: None,
            request_bytes: 0,
            operations: 0,
            response_bytes: 0,
            response_read_bytes: 0,
            consumed: 0,
            open: false,
            opened_once: false,
            finished: false,
            close_dispatched: false,
            failure: None,
        })))
    }
    fn lock(&self) -> io::Result<MutexGuard<'_, State>> {
        self.0
            .lock()
            .map_err(|_| io::Error::other("rpc_state_poisoned"))
    }
    fn deny(&self, reason: &'static str) -> FsError {
        if let Ok(mut state) = self.lock() {
            state.fail(reason);
        }
        FsError::PermissionDenied
    }
    pub fn snapshot(&self) -> Value {
        match self.lock() {
            Ok(s) => {
                json!({"operations":s.operations,"request_bytes":s.request_bytes,"response_bytes":s.response_bytes,"response_read_bytes":s.response_read_bytes,"pending_bytes":s.pending.len(),"unread_response_bytes":s.response.len().saturating_sub(s.consumed),"response_pending":s.waiting.is_some(),"channel_open":s.open,"opened_once":s.opened_once,"finished":s.finished,"close_dispatched":s.close_dispatched,"failure":s.failure,"underlying_virtual_file_counts_only":true,"max_operations":db::OPS,"max_frame_bytes":db::FRAME,"max_response_bytes":db::REPLY,"max_request_bytes":db::REQUEST_TOTAL,"max_reply_bytes":db::REPLY_TOTAL})
            }
            Err(_) => json!({"failure":"rpc_state_poisoned"}),
        }
    }
    pub fn finish(&self) -> Value {
        if let Ok(mut s) = self.lock() {
            if s.open {
                s.fail("rpc_open_at_finish");
            }
            if !s.pending.is_empty() {
                s.fail("rpc_incomplete_frame_at_finish");
            }
            if s.waiting.is_some() {
                s.fail("rpc_pending_at_finish");
            }
            if s.consumed != s.response.len() {
                s.fail("rpc_unread_at_finish");
            }
            if !s.close_dispatched {
                s.fail("rpc_close_missing");
            }
            s.finished = true;
        }
        self.snapshot()
    }
}
fn entry(path: &Path) -> std::result::Result<bool, FsError> {
    let mut name = None;
    for component in path.components() {
        match component {
            Component::RootDir | Component::CurDir => {}
            Component::Normal(part) if name.is_none() => name = Some(part),
            _ => return Err(FsError::EntryNotFound),
        }
    }
    match name {
        None => Ok(false),
        Some(name) if name == CHANNEL_NAME => Ok(true),
        _ => Err(FsError::EntryNotFound),
    }
}

impl FileOpener for RpcFs {
    fn open(
        &self,
        path: &Path,
        options: &OpenOptionsConfig,
    ) -> std::result::Result<Box<dyn VirtualFile + Send + Sync>, FsError> {
        if !entry(path)? {
            return Err(FsError::NotAFile);
        }
        if !options.read
            || !options.write
            || options.append
            || options.truncate
            || options.create
            || options.create_new
        {
            return Err(self.deny("RPC requires existing read/write channel, no namespace changes"));
        }
        let mut state = self.lock().map_err(|_| FsError::IOError)?;
        if state.open || state.opened_once || state.finished || state.failure.is_some() {
            state.fail("RPC channel already open, failed or finished");
            return Err(FsError::PermissionDenied);
        }
        state.open = true;
        state.opened_once = true;
        Ok(Box::new(Channel {
            fs: self.clone(),
            position: 0,
            readback_ready: false,
            closed: false,
        }))
    }
}

impl FileSystem for RpcFs {
    fn readlink(&self, _: &Path) -> std::result::Result<PathBuf, FsError> {
        Err(FsError::InvalidInput)
    }
    fn read_dir(&self, _: &Path) -> std::result::Result<ReadDir, FsError> {
        // No directory listing, public directory or host path is supplied.
        Err(FsError::PermissionDenied)
    }
    fn metadata(&self, path: &Path) -> std::result::Result<Metadata, FsError> {
        let file = entry(path)?;
        let len = if file {
            {
                let state = self.lock().map_err(|_| FsError::IOError)?;
                if state.waiting.is_some() {
                    db::REPLY as u64
                } else {
                    state.response.len() as u64
                }
            }
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
            len,
        })
    }
    fn symlink_metadata(&self, path: &Path) -> std::result::Result<Metadata, FsError> {
        self.metadata(path)
    }
    fn new_open_options(&self) -> OpenOptions<'_> {
        OpenOptions::new(self)
    }
    fn create_dir(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny("RPC create_dir forbidden"))
    }
    fn create_symlink(&self, _: &Path, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny("RPC symlink forbidden"))
    }
    fn hard_link(&self, _: &Path, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny("RPC hard link forbidden"))
    }
    fn remove_dir(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny("RPC remove_dir forbidden"))
    }
    fn remove_file(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny("RPC remove_file forbidden"))
    }
    fn rename<'a>(
        &'a self,
        _: &'a Path,
        _: &'a Path,
    ) -> Pin<Box<dyn Future<Output = std::result::Result<(), FsError>> + Send + 'a>> {
        Box::pin(async { Err(self.deny("RPC rename forbidden")) })
    }
}

#[derive(Debug)]
struct Channel {
    fs: RpcFs,
    position: usize,
    readback_ready: bool,
    closed: bool,
}

impl Channel {
    fn check_open(&self) -> io::Result<()> {
        if self.closed {
            return Err(self
                .fs
                .lock()?
                .fail("RPC operation on closed channel handle"));
        }
        Ok(())
    }

    fn close(&mut self) -> io::Result<()> {
        if self.closed {
            return Ok(());
        }
        self.closed = true;
        let mut state = self.fs.lock()?;
        state.open = false;
        if state.waiting.is_some() || state.consumed != state.response.len() {
            return Err(state.fail("rpc_close_with_unread_or_pending_reply"));
        }
        if !state.pending.is_empty() {
            return Err(state.fail("incomplete RPC frame on channel close"));
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
        cx: &mut Context<'_>,
        buffer: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        this.check_open()?;
        let mut state = this.fs.lock()?;
        state.active()?;
        // A zero-length stdio probe consumes nothing, including a partial frame.
        if buffer.remaining() == 0 {
            return Poll::Ready(Ok(()));
        }
        if !state.pending.is_empty() || !this.readback_ready {
            return Poll::Ready(Err(
                state.fail("RPC read before complete frame and seek zero")
            ));
        }
        match state.poll_response(cx) {
            Poll::Pending => return Poll::Pending,
            Poll::Ready(result) => result?,
        }
        if this.position != state.consumed {
            return Poll::Ready(Err(state.fail("rpc_response_replay")));
        }
        let position = this.position.min(state.response.len());
        let count = (state.response.len() - position).min(buffer.remaining());
        let budget = db::REPLY_TOTAL.saturating_sub(state.response_read_bytes);
        if count > 0 && budget == 0 {
            return Poll::Ready(Err(state.fail("RPC reply read byte limit")));
        }
        let count = count.min(budget);
        buffer.put_slice(&state.response[position..position + count]);
        state.response_read_bytes += count;
        state.consumed += count;
        this.position += count;
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
        state.active()?;
        if !bytes.is_empty() && state.pending.is_empty() && this.position != 0 {
            return Poll::Ready(Err(state.fail("RPC new command requires seek zero")));
        }
        state.receive(bytes)?;
        if !bytes.is_empty() {
            this.readback_ready = false;
        }
        this.position += bytes.len();
        Poll::Ready(Ok(bytes.len()))
    }
    fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
        // A flush is allowed between partial writes, as in the old fixture.
        self.check_open()?;
        Poll::Ready(self.fs.lock()?.active())
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
        state.active()?;
        match seek {
            // WASIX fd_write/fd_read synchronize the underlying file to the
            // descriptor cursor before EVERY IO, including fragmented writes.
            // Keeping the same position is not a protocol rewind or read grant.
            SeekFrom::Start(offset) if offset == this.position as u64 => Ok(()),
            SeekFrom::Start(0) if state.pending.is_empty() => {
                this.position = 0;
                this.readback_ready = true;
                Ok(())
            }
            SeekFrom::Current(0) => Ok(()),
            _ => Err(state.fail("RPC only supports seek zero after a complete frame")),
        }
    }
    fn poll_complete(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<u64>> {
        self.check_open()?;
        let mut state = self.fs.lock()?;
        state.active()?;
        if self.readback_ready {
            match state.poll_response(cx) {
                Poll::Pending => return Poll::Pending,
                Poll::Ready(result) => result?,
            }
        }
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
            .map(|s| {
                if s.waiting.is_some() {
                    db::REPLY as u64
                } else {
                    s.response.len() as u64
                }
            })
            .unwrap_or(0)
    }
    fn is_open(&self) -> bool {
        !self.closed
    }
    fn set_times(&mut self, _: Option<u64>, _: Option<u64>) -> std::result::Result<(), FsError> {
        Err(self.fs.deny("RPC metadata mutation forbidden"))
    }
    fn set_len(&mut self, _: u64) -> std::result::Result<(), FsError> {
        Err(self.fs.deny("RPC truncate forbidden"))
    }
    fn unlink(&mut self) -> std::result::Result<(), FsError> {
        Err(self.fs.deny("RPC unlink forbidden"))
    }
    fn poll_read_ready(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<usize>> {
        self.check_open()?;
        let mut state = self.fs.lock()?;
        state.active()?;
        if !state.pending.is_empty() || !self.readback_ready {
            return Poll::Ready(Err(
                state.fail("RPC read readiness before complete frame and seek zero")
            ));
        }
        match state.poll_response(cx) {
            Poll::Pending => return Poll::Pending,
            Poll::Ready(result) => result?,
        }
        let remaining = state.response.len().saturating_sub(self.position);
        let budget = db::REPLY_TOTAL.saturating_sub(state.response_read_bytes);
        if remaining > 0 && budget == 0 {
            return Poll::Ready(Err(state.fail("RPC reply read byte limit")));
        }
        Poll::Ready(Ok(remaining.min(budget)))
    }
    fn poll_write_ready(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<usize>> {
        self.check_open()?;
        let mut state = self.fs.lock()?;
        state.active()?;
        Poll::Ready(Ok(
            db::FRAME.min(db::REQUEST_TOTAL.saturating_sub(state.request_bytes))
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt};
    fn fixture() -> (RpcFs, std::sync::mpsc::Receiver<Job>) {
        let (s, r) = std::sync::mpsc::sync_channel(1);
        (RpcFs::new("b".repeat(48), Dispatch(s)), r)
    }
    fn wire(seq: u64, op: &str) -> Vec<u8> {
        format!("{}\n",json!({"schema":"recastpress.wpdb-rpc-request.v0.5","nonce":"b".repeat(48),"seq":seq,"op":op,"data_hex":""})).into_bytes()
    }
    fn open(fs: &RpcFs) -> Box<dyn VirtualFile + Send + Sync> {
        fs.new_open_options()
            .read(true)
            .write(true)
            .open("/channel")
            .unwrap()
    }
    #[test]
    fn async_reply_pending_then_consumed_and_closed() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_time()
            .build()
            .unwrap();
        runtime.block_on(async {
            let (fs, jobs) = fixture();
            let mut f = open(&fs);
            let request = wire(1, "hello");
            f.write_all(&request[..13]).await.unwrap();
            f.flush().await.unwrap();
            f.write_all(&request[13..]).await.unwrap();
            let job = jobs.try_recv().unwrap();
            assert_eq!(fs.snapshot()["response_pending"], true);
            assert_eq!(f.size(), db::REPLY as u64);
            {
                let seek = f.seek(SeekFrom::Start(0));
                tokio::pin!(seek);
                assert!(
                    tokio::time::timeout(std::time::Duration::from_millis(1), &mut seek)
                        .await
                        .is_err()
                );
                job.reply.send(Ok(b"{\"ok\":true}\n".to_vec())).unwrap();
                seek.await.unwrap();
            }
            let mut out = vec![];
            f.read_to_end(&mut out).await.unwrap();
            assert_eq!(out, b"{\"ok\":true}\n");
            f.seek(SeekFrom::Start(0)).await.unwrap();
            f.write_all(&wire(2, "close")).await.unwrap();
            jobs.try_recv()
                .unwrap()
                .reply
                .send(Ok(b"{\"closed\":true}\n".to_vec()))
                .unwrap();
            f.seek(SeekFrom::Start(0)).await.unwrap();
            out.clear();
            f.read_to_end(&mut out).await.unwrap();
            drop(f);
            let s = fs.finish();
            assert_eq!(s["failure"], Value::Null);
            assert_eq!(s["operations"], 2);
            assert_eq!(s["response_read_bytes"], s["response_bytes"]);
        });
    }
    #[test]
    fn incomplete_unread_and_open_handles_cannot_finish() {
        let rt = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        rt.block_on(async {
            let (fs, _) = fixture();
            let mut f = open(&fs);
            f.write_all(b"{").await.unwrap();
            drop(f);
            assert!(!fs.finish()["failure"].is_null());
            let (fs, jobs) = fixture();
            let mut f = open(&fs);
            f.write_all(&wire(1, "hello")).await.unwrap();
            jobs.try_recv()
                .unwrap()
                .reply
                .send(Ok(b"{}\n".to_vec()))
                .unwrap();
            f.seek(SeekFrom::Start(0)).await.unwrap();
            drop(f);
            assert!(!fs.finish()["failure"].is_null());
            let (fs, _) = fixture();
            let _f = open(&fs);
            assert!(!fs.finish()["failure"].is_null());
        });
    }
    #[test]
    fn namespace_denial_no_reopen_and_identity_before_dispatch() {
        let (fs, jobs) = fixture();
        assert!(
            fs.new_open_options()
                .read(true)
                .write(true)
                .open("/../channel")
                .is_err()
        );
        assert!(
            fs.new_open_options()
                .read(true)
                .write(true)
                .create(true)
                .open("/channel")
                .is_err()
        );
        assert!(jobs.try_recv().is_err());
        let (fs, jobs) = fixture();
        let rt = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        rt.block_on(async {
            let mut f = open(&fs);
            assert!(f.write_all(&wire(2, "hello")).await.is_err());
            drop(f);
            assert!(
                fs.new_open_options()
                    .read(true)
                    .write(true)
                    .open("/channel")
                    .is_err()
            );
        });
        assert!(jobs.try_recv().is_err());
    }
}
