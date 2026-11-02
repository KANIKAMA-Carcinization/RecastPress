//! Fixed finite immutable source mount. No host-path fallback or writable handles.
use std::{
    collections::BTreeMap,
    future::Future,
    path::{Component, Path, PathBuf},
    pin::Pin,
    sync::Arc,
};
use wasmer_wasix::virtual_fs::{
    FileOpener, FileSystem, FileType, FsError, Metadata, OpenOptions, OpenOptionsConfig, ReadDir,
    VirtualFile,
};
#[derive(Debug, Clone)]
pub struct ReadOnlyFs(Arc<BTreeMap<String, Arc<[u8]>>>);
impl ReadOnlyFs {
    pub fn new(files: BTreeMap<String, Arc<[u8]>>) -> Self {
        Self(Arc::new(files))
    }
}
fn deny(_: &'static str) -> FsError {
    FsError::PermissionDenied
}
fn entry(path: &Path) -> std::result::Result<&str, FsError> {
    let mut name = None;
    for c in path.components() {
        match c {
            Component::RootDir | Component::CurDir => {}
            Component::Normal(p) if name.is_none() => name = Some(p),
            _ => return Err(FsError::EntryNotFound),
        }
    }
    name.map(|s| s.to_str().ok_or(FsError::EntryNotFound))
        .unwrap_or(Ok(""))
}
impl FileOpener for ReadOnlyFs {
    fn open(
        &self,
        path: &Path,
        options: &OpenOptionsConfig,
    ) -> std::result::Result<Box<dyn VirtualFile + Send + Sync>, FsError> {
        if !options.read
            || options.write
            || options.append
            || options.truncate
            || options.create
            || options.create_new
        {
            return Err(FsError::PermissionDenied);
        }
        let name = entry(path)?;
        if name.is_empty() {
            return Err(FsError::NotAFile);
        }
        let data = self.0.get(name).ok_or(FsError::EntryNotFound)?;
        Ok(Box::new(ImmutableFile {
            bytes: data.clone(),
            position: 0,
            open: true,
        }))
    }
}
impl FileSystem for ReadOnlyFs {
    fn readlink(&self, _: &Path) -> std::result::Result<PathBuf, FsError> {
        Err(FsError::InvalidInput)
    }
    fn read_dir(&self, _: &Path) -> std::result::Result<ReadDir, FsError> {
        // No directory listing, public directory or host path is supplied.
        Err(FsError::PermissionDenied)
    }
    fn metadata(&self, path: &Path) -> std::result::Result<Metadata, FsError> {
        let name = entry(path)?;
        let file = !name.is_empty();
        let len = if file {
            self.0.get(name).ok_or(FsError::EntryNotFound)?.len() as u64
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
        Err(deny("RPC create_dir forbidden"))
    }
    fn create_symlink(&self, _: &Path, _: &Path) -> std::result::Result<(), FsError> {
        Err(deny("RPC symlink forbidden"))
    }
    fn hard_link(&self, _: &Path, _: &Path) -> std::result::Result<(), FsError> {
        Err(deny("RPC hard link forbidden"))
    }
    fn remove_dir(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(deny("RPC remove_dir forbidden"))
    }
    fn remove_file(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(deny("RPC remove_file forbidden"))
    }
    fn rename<'a>(
        &'a self,
        _: &'a Path,
        _: &'a Path,
    ) -> Pin<Box<dyn Future<Output = std::result::Result<(), FsError>> + Send + 'a>> {
        Box::pin(async { Err(deny("RPC rename forbidden")) })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    #[test]
    fn finite_source_mount_denies_mutation_and_path_escape() {
        let fs = ReadOnlyFs::new(BTreeMap::from([(
            "bridge.php".to_owned(),
            Arc::<[u8]>::from(&b"<?php fixed\n"[..]),
        )]));
        for path in ["/../bridge.php", "/other.php", "/dir/bridge.php"] {
            assert!(fs.new_open_options().read(true).open(path).is_err());
        }
        assert!(
            fs.new_open_options()
                .read(true)
                .write(true)
                .open("/bridge.php")
                .is_err()
        );
        assert!(fs.remove_file(Path::new("/bridge.php")).is_err());
        let rt = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        rt.block_on(async {
            let mut f = fs
                .new_open_options()
                .read(true)
                .open("/bridge.php")
                .unwrap();
            assert!(f.write_all(b"x").await.is_err());
            assert!(f.set_len(0).is_err());
            assert!(f.set_times(Some(1), Some(1)).is_err());
            let mut data = vec![];
            f.read_to_end(&mut data).await.unwrap();
            assert_eq!(data, b"<?php fixed\n");
        });
    }
}

/// Owned shared bytes; metadata and content mutation fail explicitly.
#[derive(Debug)]
struct ImmutableFile {
    bytes: Arc<[u8]>,
    position: u64,
    open: bool,
}
impl tokio::io::AsyncRead for ImmutableFile {
    fn poll_read(
        self: Pin<&mut Self>,
        _: &mut std::task::Context<'_>,
        out: &mut tokio::io::ReadBuf<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        let this = self.get_mut();
        if !this.open {
            return std::task::Poll::Ready(Err(std::io::ErrorKind::NotConnected.into()));
        }
        let start = this.position.min(this.bytes.len() as u64) as usize;
        let count = out.remaining().min(this.bytes.len() - start);
        out.put_slice(&this.bytes[start..start + count]);
        this.position += count as u64;
        std::task::Poll::Ready(Ok(()))
    }
}
impl tokio::io::AsyncWrite for ImmutableFile {
    fn poll_write(
        self: Pin<&mut Self>,
        _: &mut std::task::Context<'_>,
        _: &[u8],
    ) -> std::task::Poll<std::io::Result<usize>> {
        std::task::Poll::Ready(Err(std::io::ErrorKind::PermissionDenied.into()))
    }
    fn poll_flush(
        self: Pin<&mut Self>,
        _: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        std::task::Poll::Ready(Ok(()))
    }
    fn poll_shutdown(
        self: Pin<&mut Self>,
        _: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        self.get_mut().open = false;
        std::task::Poll::Ready(Ok(()))
    }
}
impl tokio::io::AsyncSeek for ImmutableFile {
    fn start_seek(self: Pin<&mut Self>, seek: std::io::SeekFrom) -> std::io::Result<()> {
        let this = self.get_mut();
        let target = match seek {
            std::io::SeekFrom::Start(v) => v as i128,
            std::io::SeekFrom::Current(v) => this.position as i128 + v as i128,
            std::io::SeekFrom::End(v) => this.bytes.len() as i128 + v as i128,
        };
        if !(0..=u64::MAX as i128).contains(&target) {
            return Err(std::io::ErrorKind::InvalidInput.into());
        }
        this.position = target as u64;
        Ok(())
    }
    fn poll_complete(
        self: Pin<&mut Self>,
        _: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<u64>> {
        std::task::Poll::Ready(Ok(self.position))
    }
}
impl VirtualFile for ImmutableFile {
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
        self.bytes.len() as u64
    }
    fn is_open(&self) -> bool {
        self.open
    }
    fn set_times(&mut self, _: Option<u64>, _: Option<u64>) -> std::result::Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn set_len(&mut self, _: u64) -> std::result::Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn unlink(&mut self) -> std::result::Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn poll_read_ready(
        self: Pin<&mut Self>,
        _: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<usize>> {
        std::task::Poll::Ready(Ok(
            (self.bytes.len() as u64).saturating_sub(self.position) as usize
        ))
    }
    fn poll_write_ready(
        self: Pin<&mut Self>,
        _: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<usize>> {
        std::task::Poll::Ready(Err(std::io::ErrorKind::PermissionDenied.into()))
    }
}
