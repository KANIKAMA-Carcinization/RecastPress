//! One private seekable JSONL file and one request-local Rust cache instance.

use crate::cache_host::Session;
use anyhow::{Result, ensure};
use serde_json::{Value, json};
use std::fmt;
use std::future::Future;
use std::io::{self, SeekFrom};
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::{Arc, Mutex, MutexGuard};
use std::task::{Context, Poll};
use tokio::io::{AsyncRead, AsyncSeek, AsyncWrite, ReadBuf};
use wasmer_wasix::virtual_fs::{
    FileOpener, FileSystem, FileType, FsError, Metadata, OpenOptions, OpenOptionsConfig, ReadDir,
    VirtualFile,
};

pub const MOUNT: &str = "/rp-object-cache";
const FRAME_LIMIT: usize = 16 * 1024;
const TOTAL_LIMIT: usize = 64 * 1024;

struct State {
    session: Session,
    pending: Vec<u8>,
    response: Vec<u8>,
    consumed: usize,
    request_bytes: usize,
    response_bytes: usize,
    response_read_bytes: usize,
    opened_once: bool,
    open: bool,
    finished: bool,
    failure: Option<&'static str>,
}

impl State {
    fn fail(&mut self, reason: &'static str) -> io::Error {
        self.failure.get_or_insert(reason);
        io::Error::new(io::ErrorKind::InvalidData, reason)
    }

    fn active(&mut self) -> io::Result<()> {
        if let Some(reason) = self.failure {
            return Err(io::Error::new(io::ErrorKind::InvalidData, reason));
        }
        if self.finished || !self.open {
            return Err(self.fail("cache_channel_inactive"));
        }
        Ok(())
    }

    fn receive(&mut self, bytes: &[u8]) -> io::Result<()> {
        if bytes.is_empty() {
            return Ok(());
        }
        if self.session.finished() || self.consumed != self.response.len() {
            return Err(self.fail("cache_reply_unread_or_terminal"));
        }
        if bytes.len() > TOTAL_LIMIT - self.request_bytes
            || bytes.len() > FRAME_LIMIT - self.pending.len()
        {
            return Err(self.fail("cache_request_limit"));
        }
        let newline = bytes.iter().position(|byte| *byte == b'\n');
        if newline.is_some_and(|at| at + 1 != bytes.len()) {
            return Err(self.fail("cache_frame_trailer"));
        }
        self.pending.extend_from_slice(bytes);
        self.request_bytes += bytes.len();
        if newline.is_none() {
            if self.pending.len() == FRAME_LIMIT {
                return Err(self.fail("cache_frame_limit"));
            }
            return Ok(());
        }
        let response = match self.session.exchange(&self.pending) {
            Ok(response) => response,
            Err(code) => return Err(self.fail(code)),
        };
        if response.len() > FRAME_LIMIT || response.len() > TOTAL_LIMIT - self.response_bytes {
            return Err(self.fail("cache_response_limit"));
        }
        self.response_bytes += response.len();
        self.response = response;
        self.consumed = 0;
        self.pending.clear();
        Ok(())
    }

    fn readable(&mut self, position: usize, readback_ready: bool) -> io::Result<usize> {
        if !self.pending.is_empty() || !readback_ready || position != self.consumed {
            return Err(self.fail("cache_reply_requires_rewind"));
        }
        Ok(self.response.len() - self.consumed)
    }
}

#[derive(Clone)]
pub struct CacheFs(Arc<Mutex<State>>);

impl fmt::Debug for CacheFs {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.write_str("CacheFs")
    }
}

impl CacheFs {
    pub fn new() -> Self {
        Self(Arc::new(Mutex::new(State {
            session: Session::new(),
            pending: Vec::new(),
            response: Vec::new(),
            consumed: 0,
            request_bytes: 0,
            response_bytes: 0,
            response_read_bytes: 0,
            opened_once: false,
            open: false,
            finished: false,
            failure: None,
        })))
    }

    fn lock(&self) -> io::Result<MutexGuard<'_, State>> {
        self.0
            .lock()
            .map_err(|_| io::Error::other("cache_channel_lock"))
    }

    fn deny(&self) -> FsError {
        if let Ok(mut state) = self.lock() {
            state.fail("cache_namespace_mutation");
        }
        FsError::PermissionDenied
    }

    /// Seal after the guest runner and runtime release their handle ownership.
    pub fn finish(&self) -> Result<Value> {
        let mut state = self.lock()?;
        if state.finished
            || !state.opened_once
            || state.open
            || !state.pending.is_empty()
            || state.response_read_bytes != state.response_bytes
            || !state.session.finished()
        {
            state.fail("cache_channel_incomplete");
        }
        state.finished = true;
        if let Some(code) = state.failure {
            state.session.abort();
            anyhow::bail!(code);
        }
        let mut report = state
            .session
            .summary()
            .map_err(|code| anyhow::anyhow!(code))?;
        ensure!(
            report["entries"] == 0 && report["charged_bytes"] == 0,
            "cache_cleanup"
        );
        report["opened_once"] = json!(true);
        report["channel_open"] = json!(false);
        report["finished"] = json!(true);
        report["request_bytes"] = json!(state.request_bytes);
        report["response_bytes"] = json!(state.response_bytes);
        report["response_read_bytes"] = json!(state.response_read_bytes);
        report["pending_bytes"] = json!(state.pending.len());
        report["unread_response_bytes"] = json!(state.response.len() - state.consumed);
        report["finish_dispatched"] = json!(true);
        Ok(report)
    }
}

fn entry(path: &Path) -> std::result::Result<bool, FsError> {
    match path.as_os_str().as_encoded_bytes() {
        b"" | b"." | b"/" => Ok(false),
        b"channel" | b"/channel" => Ok(true),
        _ => Err(FsError::EntryNotFound),
    }
}

impl FileOpener for CacheFs {
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
            return Err(self.deny());
        }
        let mut state = self.lock().map_err(|_| FsError::IOError)?;
        if state.opened_once || state.finished || state.failure.is_some() {
            state.fail("cache_channel_reopen");
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

impl FileSystem for CacheFs {
    fn readlink(&self, _: &Path) -> std::result::Result<PathBuf, FsError> {
        Err(FsError::InvalidInput)
    }
    fn read_dir(&self, _: &Path) -> std::result::Result<ReadDir, FsError> {
        Err(FsError::PermissionDenied)
    }
    fn metadata(&self, path: &Path) -> std::result::Result<Metadata, FsError> {
        let file = entry(path)?;
        let len = if file {
            self.lock().map_err(|_| FsError::IOError)?.response.len() as u64
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
        Err(self.deny())
    }
    fn create_symlink(&self, _: &Path, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny())
    }
    fn hard_link(&self, _: &Path, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny())
    }
    fn remove_dir(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny())
    }
    fn remove_file(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(self.deny())
    }
    fn rename<'a>(
        &'a self,
        _: &'a Path,
        _: &'a Path,
    ) -> Pin<Box<dyn Future<Output = std::result::Result<(), FsError>> + Send + 'a>> {
        Box::pin(async { Err(self.deny()) })
    }
}

#[derive(Debug)]
struct Channel {
    fs: CacheFs,
    position: usize,
    readback_ready: bool,
    closed: bool,
}

impl Channel {
    fn active(&self) -> io::Result<MutexGuard<'_, State>> {
        let mut state = self.fs.lock()?;
        if self.closed {
            return Err(state.fail("cache_channel_closed"));
        }
        state.active()?;
        Ok(state)
    }

    fn close(&mut self) -> io::Result<()> {
        if self.closed {
            return Ok(());
        }
        self.closed = true;
        let mut state = self.fs.lock()?;
        state.open = false;
        if !state.pending.is_empty()
            || state.consumed != state.response.len()
            || !state.session.finished()
        {
            return Err(state.fail("cache_channel_closed_incomplete"));
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
        let mut state = this.active()?;
        if buffer.remaining() == 0 {
            return Poll::Ready(Ok(()));
        }
        let count = state
            .readable(this.position, this.readback_ready)?
            .min(buffer.remaining());
        buffer.put_slice(&state.response[this.position..this.position + count]);
        state.response_read_bytes += count;
        state.consumed += count;
        drop(state);
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
        let mut state = this.active()?;
        if !bytes.is_empty() && state.pending.is_empty() && this.position != 0 {
            return Poll::Ready(Err(state.fail("cache_request_requires_rewind")));
        }
        state.receive(bytes)?;
        drop(state);
        if !bytes.is_empty() {
            this.readback_ready = false;
        }
        this.position += bytes.len();
        Poll::Ready(Ok(bytes.len()))
    }

    fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
        Poll::Ready(self.active().map(drop))
    }

    fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
        Poll::Ready(self.get_mut().close())
    }
}

impl AsyncSeek for Channel {
    fn start_seek(self: Pin<&mut Self>, seek: SeekFrom) -> io::Result<()> {
        let this = self.get_mut();
        let mut state = this.active()?;
        match seek {
            // WASIX synchronizes the descriptor cursor before each read/write.
            SeekFrom::Start(offset) if offset == this.position as u64 => Ok(()),
            SeekFrom::Start(0)
                if state.pending.is_empty()
                    && (state.consumed == 0 || state.consumed == state.response.len()) =>
            {
                drop(state);
                this.position = 0;
                this.readback_ready = true;
                Ok(())
            }
            SeekFrom::Current(0) => Ok(()),
            _ => Err(state.fail("cache_seek_rejected")),
        }
    }

    fn poll_complete(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<u64>> {
        drop(self.active()?);
        Poll::Ready(Ok(self.position as u64))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt};

    fn open(fs: &CacheFs) -> Box<dyn VirtualFile + Send + Sync> {
        fs.new_open_options()
            .read(true)
            .write(true)
            .open("/channel")
            .unwrap()
    }

    #[tokio::test]
    async fn exact_exchanges_require_clean_terminal_finish_and_close() {
        let fs = CacheFs::new();
        let mut file = open(&fs);
        let requests = [
            b"{\"op\":\"set\",\"group\":\"rp-cache-multiple\",\"key\":\"i:7\",\"value\":{\"kind\":\"null\"}}\n".as_slice(),
            b"{\"op\":\"get\",\"group\":\"rp-cache-multiple\",\"key\":\"i:7\"}\n",
            b"{\"op\":\"finish\"}\n",
        ];
        let expected = [
            json!({"ok":true,"changed":true}),
            json!({"ok":true,"found":true,"value":{"kind":"null"}}),
            json!({"ok":true,"operations":2,"entries":0,"charged_bytes":0}),
        ];
        for (request, reply) in requests.into_iter().zip(expected) {
            file.seek(SeekFrom::Start(0)).await.unwrap();
            file.write_all(&request[..4]).await.unwrap();
            file.write_all(&request[4..]).await.unwrap();
            file.flush().await.unwrap();
            file.seek(SeekFrom::Start(0)).await.unwrap();
            let mut bytes = Vec::new();
            file.read_to_end(&mut bytes).await.unwrap();
            assert!(bytes.ends_with(b"\n"));
            assert_eq!(serde_json::from_slice::<Value>(&bytes).unwrap(), reply);
        }
        file.shutdown().await.unwrap();
        drop(file);
        let report = fs.finish().unwrap();
        assert_eq!(report["operations"], 2);
        assert_eq!(report["groups"]["rp-cache-multiple"], 2);
        assert_eq!(report["response_bytes"], report["response_read_bytes"]);
        assert_eq!(report["entries"], 0);
        assert_eq!(report["charged_bytes"], 0);
    }

    #[tokio::test]
    async fn malformed_or_unread_exchanges_poison_the_request() {
        for request in [
            b"{\"op\":\"finish\"}\n{\"op\":\"flush\"}\n".as_slice(),
            b"{\"op\":\"finish\"}\n",
        ] {
            let fs = CacheFs::new();
            let mut file = open(&fs);
            if request.iter().filter(|byte| **byte == b'\n').count() == 1 {
                file.write_all(request).await.unwrap();
                file.seek(SeekFrom::Start(0)).await.unwrap();
                let mut byte = [0];
                file.read_exact(&mut byte).await.unwrap();
                assert!(file.seek(SeekFrom::Start(0)).await.is_err());
            } else {
                assert!(file.write_all(request).await.is_err());
            }
            drop(file);
            assert!(fs.finish().is_err());
        }
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
            .map_or(0, |state| state.response.len() as u64)
    }
    fn is_open(&self) -> bool {
        !self.closed
    }
    fn set_times(&mut self, _: Option<u64>, _: Option<u64>) -> std::result::Result<(), FsError> {
        Err(self.fs.deny())
    }
    fn set_len(&mut self, _: u64) -> std::result::Result<(), FsError> {
        Err(self.fs.deny())
    }
    fn unlink(&mut self) -> std::result::Result<(), FsError> {
        Err(self.fs.deny())
    }
    fn poll_read_ready(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<usize>> {
        Poll::Ready(self.active()?.readable(self.position, self.readback_ready))
    }
    fn poll_write_ready(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<usize>> {
        let state = self.active()?;
        let room = (FRAME_LIMIT - state.pending.len()).min(TOTAL_LIMIT - state.request_bytes);
        Poll::Ready(Ok(room))
    }
}
