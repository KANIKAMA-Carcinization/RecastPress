//! Bounded capture copied from the established host, no unbounded pipe queue.
use anyhow::{Result, bail, ensure};
use std::{
    io,
    pin::Pin,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    task::{Context as TaskContext, Poll},
    time::{Duration, Instant},
};
use tokio::io::{AsyncRead, AsyncSeek, AsyncWrite, ReadBuf, SeekFrom};
use wasmer_wasix::{
    Pipe,
    virtual_fs::{FsError, VirtualFile},
};
const OUTPUT_LIMIT: usize = 65 * 1024;
pub fn collect_pipe(mut pipe: Pipe) -> Result<Vec<u8>> {
    let deadline = Instant::now() + Duration::from_secs(2);
    let mut collected = Vec::new();
    let mut buffer = [0; 4096];
    loop {
        match pipe.try_read(&mut buffer) {
            Some(0) => return Ok(collected),
            Some(count) => {
                ensure!(
                    count <= OUTPUT_LIMIT.saturating_sub(collected.len()),
                    "capture exceeded limit"
                );
                collected.extend_from_slice(&buffer[..count]);
            }
            None if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(1)),
            None => bail!("pipe did not close within cleanup deadline"),
        }
    }
}

/// Bounds writes before Pipe's unbounded internal queue can retain them.
/// No reader thread, host file or unsafe runtime hook is required.
#[derive(Debug)]
pub struct LimitedPipe {
    inner: Pipe,
    written: usize,
    overflow: Arc<AtomicBool>,
}

impl LimitedPipe {
    pub fn new(inner: Pipe, overflow: Arc<AtomicBool>) -> Self {
        Self {
            inner,
            written: 0,
            overflow,
        }
    }
}

impl AsyncRead for LimitedPipe {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut TaskContext<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_read(cx, buf)
    }
}

impl AsyncWrite for LimitedPipe {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut TaskContext<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        if buf.len() > OUTPUT_LIMIT.saturating_sub(self.written) {
            self.overflow.store(true, Ordering::Relaxed);
            return Poll::Ready(Err(io::Error::new(
                io::ErrorKind::FileTooLarge,
                "fixed probe output limit exceeded",
            )));
        }
        match Pin::new(&mut self.inner).poll_write(cx, buf) {
            Poll::Ready(Ok(count)) => {
                self.written += count;
                Poll::Ready(Ok(count))
            }
            other => other,
        }
    }

    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut TaskContext<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_flush(cx)
    }

    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut TaskContext<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_shutdown(cx)
    }
}

impl AsyncSeek for LimitedPipe {
    fn start_seek(mut self: Pin<&mut Self>, position: SeekFrom) -> io::Result<()> {
        Pin::new(&mut self.inner).start_seek(position)
    }

    fn poll_complete(mut self: Pin<&mut Self>, cx: &mut TaskContext<'_>) -> Poll<io::Result<u64>> {
        Pin::new(&mut self.inner).poll_complete(cx)
    }
}

impl VirtualFile for LimitedPipe {
    fn last_accessed(&self) -> u64 {
        self.inner.last_accessed()
    }
    fn last_modified(&self) -> u64 {
        self.inner.last_modified()
    }
    fn created_time(&self) -> u64 {
        self.inner.created_time()
    }
    fn size(&self) -> u64 {
        self.inner.size()
    }
    fn set_len(&mut self, size: u64) -> std::result::Result<(), FsError> {
        self.inner.set_len(size)
    }
    fn unlink(&mut self) -> std::result::Result<(), FsError> {
        self.inner.unlink()
    }
    fn is_open(&self) -> bool {
        self.inner.is_open()
    }
    fn poll_read_ready(
        mut self: Pin<&mut Self>,
        cx: &mut TaskContext<'_>,
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.inner).poll_read_ready(cx)
    }
    fn poll_write_ready(
        mut self: Pin<&mut Self>,
        cx: &mut TaskContext<'_>,
    ) -> Poll<io::Result<usize>> {
        match Pin::new(&mut self.inner).poll_write_ready(cx) {
            Poll::Ready(Ok(count)) => {
                Poll::Ready(Ok(count.min(OUTPUT_LIMIT.saturating_sub(self.written))))
            }
            other => other,
        }
    }
}
