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
const OUTPUT_LIMIT: usize = 3 * 1024 * 1024;
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

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncWriteExt;
    #[test]
    fn native_sized_body_above_old_cap_is_retained_through_complete_eof() {
        let rt=tokio::runtime::Builder::new_current_thread().build().unwrap();
        let (send,read)=Pipe::channel();let overflow=Arc::new(AtomicBool::new(false));
        let mut output=LimitedPipe::new(send,overflow.clone());let bytes=vec![b'x';69399];
        rt.block_on(output.write_all(&bytes)).unwrap();drop(output);
        assert_eq!(collect_pipe(read).unwrap(),bytes);assert!(!overflow.load(Ordering::Relaxed));
    }
    #[test]
    fn fragmented_large_cgi_keeps_header_boundaries_hex_body_order_and_eof() {
        let rt=tokio::runtime::Builder::new_current_thread().build().unwrap();
        let hex_body=(0..69399).map(|i|format!("{:02x}",i%251)).collect::<String>();
        assert_eq!(hex_body.len(),138798);
        let mut body=serde_json::to_vec(&serde_json::json!({
            "schema":"synthetic.fragmented-cgi.v0.5","body_hex":hex_body,
            "padding":"bounded-public-fixture".repeat(600)
        })).unwrap();
        body.push(b'\n');
        assert!((150000..160000).contains(&body.len()));
        let headers:[&[u8];5]=[b"X-Powered-By: PHP/8.5.12\r",b"\nContent-Type: application/json\r",b"\n",b"\r",b"\n"];
        let mut expected=headers.concat();expected.extend_from_slice(&body);
        for sizes in [vec![1],vec![4095],vec![4096],vec![4097],vec![65536],vec![1,4095,4096,4097,65536]] {
            let (send,read)=Pipe::channel();let overflow=Arc::new(AtomicBool::new(false));
            let mut output=LimitedPipe::new(send,overflow.clone());
            rt.block_on(async {
                for header in headers {output.write_all(header).await.unwrap();}
                let mut offset=0;let mut chunk=0;
                while offset<body.len() {
                    let end=(offset+sizes[chunk%sizes.len()]).min(body.len());
                    output.write_all(&body[offset..end]).await.unwrap();
                    offset=end;chunk+=1;
                }
                output.flush().await.unwrap();
            });
            drop(output);
            let collected=collect_pipe(read).unwrap();
            assert_eq!(collected,expected,"write sizes {sizes:?}");
            assert!(!overflow.load(Ordering::Relaxed));
            let parsed=crate::cgi_response::parse(&collected).unwrap();
            assert_eq!(parsed.status,200);assert_eq!(parsed.headers.len(),2);
            assert_eq!(parsed.body,body);
            assert_eq!(serde_json::from_slice::<serde_json::Value>(&parsed.body).unwrap()["body_hex"].as_str().unwrap().len(),138798);
        }
    }
    #[test]
    fn oversize_is_rejected_before_the_pipe_queue_retains_any_bytes() {
        let rt=tokio::runtime::Builder::new_current_thread().build().unwrap();
        let (send,read)=Pipe::channel();let overflow=Arc::new(AtomicBool::new(false));
        let mut output=LimitedPipe::new(send,overflow.clone());
        assert!(rt.block_on(output.write_all(&vec![b'x';OUTPUT_LIMIT+1])).is_err());drop(output);
        assert!(overflow.load(Ordering::Relaxed));assert!(collect_pipe(read).unwrap().is_empty());
    }
}
