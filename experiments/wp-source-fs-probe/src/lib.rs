//! A finite immutable source tree. It never opens or delegates a host path.
#![forbid(unsafe_code)]
use shared_buffer::OwnedBuffer;
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    future::Future,
    io::{self, SeekFrom},
    path::{Path, PathBuf},
    pin::Pin,
    sync::Arc,
    task::{Context, Poll},
};
use tokio::io::{AsyncRead, AsyncSeek, AsyncWrite, ReadBuf};
use virtual_fs::{
    DirEntry, FileOpener, FileSystem, FileType, FsError, Metadata, OpenOptions, OpenOptionsConfig,
    ReadDir, VirtualFile,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Limits {
    pub files: usize,
    pub directories: usize,
    pub file_bytes: usize,
    pub total_bytes: u64,
    pub path_bytes: usize,
}
impl Default for Limits {
    fn default() -> Self {
        Self {
            files: 5000,
            directories: 5000,
            file_bytes: 16 * 1024 * 1024,
            total_bytes: 256 * 1024 * 1024,
            path_bytes: 4096,
        }
    }
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CatalogError {
    InvalidLimits,
    InvalidPath,
    FileDirectoryCollision,
    MissingParent,
    ParentIsFile,
    TooManyFiles,
    TooManyDirectories,
    FileTooLarge,
    TotalTooLarge,
}
impl fmt::Display for CatalogError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for CatalogError {}
#[derive(Debug)]
struct Catalog {
    files: BTreeMap<String, Arc<[u8]>>,
    directories: BTreeSet<String>,
    children: BTreeMap<String, Vec<String>>,
    total_bytes: u64,
    limits: Limits,
}
#[derive(Clone, Debug)]
pub struct SourceFs(Arc<Catalog>);

fn parent(name: &str) -> &str {
    name.rsplit_once('/').map_or("", |(parent, _)| parent)
}
fn safe_text(text: &str, max: usize) -> bool {
    text.len() <= max
        && !text.contains(['\\', ':', '\0'])
        && !text.starts_with("//")
        && !text.chars().any(char::is_control)
}
fn catalog_path(text: &str, max: usize, root: bool) -> bool {
    if text.is_empty() {
        return root;
    }
    safe_text(text, max) && text.split('/').all(|part| !matches!(part, "" | "." | ".."))
}
fn metadata(file: bool, len: u64) -> Metadata {
    Metadata {
        ft: FileType {
            file,
            dir: !file,
            ..FileType::default()
        },
        accessed: 0,
        created: 0,
        modified: 0,
        len,
    }
}

impl SourceFs {
    /// Root is implicit; `directories` may additionally contain the empty root.
    /// All other parents must be explicit, including parents of empty dirs.
    pub fn new(
        files: BTreeMap<String, Arc<[u8]>>,
        directories: BTreeSet<String>,
    ) -> Result<Self, CatalogError> {
        Self::with_limits(files, directories, Limits::default())
    }
    /// Limits can only restrict the fixed maximums, never enlarge them.
    pub fn with_limits(
        files: BTreeMap<String, Arc<[u8]>>,
        mut directories: BTreeSet<String>,
        limits: Limits,
    ) -> Result<Self, CatalogError> {
        let max = Limits::default();
        if limits.files > max.files
            || limits.directories > max.directories
            || limits.file_bytes > max.file_bytes
            || limits.total_bytes > max.total_bytes
            || limits.path_bytes == 0
            || limits.path_bytes > max.path_bytes
        {
            return Err(CatalogError::InvalidLimits);
        }
        directories.remove("");
        if files.len() > limits.files {
            return Err(CatalogError::TooManyFiles);
        }
        if directories.len() > limits.directories {
            return Err(CatalogError::TooManyDirectories);
        }
        if files
            .keys()
            .any(|p| !catalog_path(p, limits.path_bytes, false))
            || directories
                .iter()
                .any(|p| !catalog_path(p, limits.path_bytes, false))
        {
            return Err(CatalogError::InvalidPath);
        }
        if files.keys().any(|p| directories.contains(p)) {
            return Err(CatalogError::FileDirectoryCollision);
        }
        let mut total_bytes = 0u64;
        for data in files.values() {
            if data.len() > limits.file_bytes {
                return Err(CatalogError::FileTooLarge);
            }
            total_bytes = total_bytes
                .checked_add(data.len() as u64)
                .ok_or(CatalogError::TotalTooLarge)?;
            if total_bytes > limits.total_bytes {
                return Err(CatalogError::TotalTooLarge);
            }
        }
        for name in files.keys().chain(directories.iter()) {
            let prefix = parent(name);
            if files.contains_key(prefix) {
                return Err(CatalogError::ParentIsFile);
            }
            if !prefix.is_empty() && !directories.contains(prefix) {
                return Err(CatalogError::MissingParent);
            }
        }
        directories.insert(String::new());
        let mut children: BTreeMap<String, Vec<String>> = directories
            .iter()
            .map(|p| (p.clone(), Vec::new()))
            .collect();
        for name in files
            .keys()
            .chain(directories.iter().filter(|p| !p.is_empty()))
        {
            children
                .get_mut(parent(name))
                .ok_or(CatalogError::MissingParent)?
                .push(name.clone());
        }
        for list in children.values_mut() {
            list.sort();
        }
        Ok(Self(Arc::new(Catalog {
            files,
            directories,
            children,
            total_bytes,
            limits,
        })))
    }
    pub fn file_count(&self) -> usize {
        self.0.files.len()
    }
    pub fn directory_count(&self) -> usize {
        self.0.directories.len() - 1
    }
    pub fn total_bytes(&self) -> u64 {
        self.0.total_bytes
    }

    fn meta_name(&self, name: &str) -> Result<Metadata, FsError> {
        if let Some(bytes) = self.0.files.get(name) {
            Ok(metadata(true, bytes.len() as u64))
        } else if self.0.directories.contains(name) {
            Ok(metadata(false, 0))
        } else {
            Err(FsError::EntryNotFound)
        }
    }
    fn directory(&self, name: &str) -> Result<(), FsError> {
        if self.0.directories.contains(name) {
            Ok(())
        } else if self.0.files.contains_key(name) {
            Err(FsError::BaseNotDirectory)
        } else {
            Err(FsError::EntryNotFound)
        }
    }
    fn resolve(&self, path: &Path) -> Result<String, FsError> {
        let raw = path.to_str().ok_or(FsError::InvalidInput)?;
        if !safe_text(raw, self.0.limits.path_bytes) {
            return Err(FsError::InvalidInput);
        }
        let mut parts = Vec::new();
        for part in raw.split('/').filter(|part| !part.is_empty()) {
            // Traversing a file or missing directory is not made valid by a
            // later '..'. No OS resolution or symlink following occurs here.
            if !parts.is_empty() {
                self.directory(&parts.join("/"))?;
            }
            match part {
                "." => {}
                ".." => {
                    parts.pop().ok_or(FsError::PermissionDenied)?;
                }
                name => parts.push(name),
            }
        }
        let name = parts.join("/");
        self.meta_name(&name)?;
        if raw.ends_with('/') {
            self.directory(&name)?;
        }
        Ok(name)
    }
}
impl FileOpener for SourceFs {
    fn open(
        &self,
        path: &Path,
        conf: &OpenOptionsConfig,
    ) -> Result<Box<dyn VirtualFile + Send + Sync>, FsError> {
        if !conf.read
            || conf.write
            || conf.append
            || conf.truncate
            || conf.create
            || conf.create_new
        {
            return Err(FsError::PermissionDenied);
        }
        let name = self.resolve(path)?;
        // Like virtual-fs mem_fs, directory inodes are not regular file handles.
        // WASIX owns directory FDs separately; this library does not emulate one.
        let bytes = self.0.files.get(&name).ok_or(FsError::NotAFile)?;
        Ok(Box::new(SourceFile {
            bytes: bytes.clone(),
            position: 0,
            open: true,
        }))
    }
}
impl FileSystem for SourceFs {
    fn metadata(&self, path: &Path) -> Result<Metadata, FsError> {
        self.meta_name(&self.resolve(path)?)
    }
    fn symlink_metadata(&self, path: &Path) -> Result<Metadata, FsError> {
        self.metadata(path)
    }
    fn read_dir(&self, path: &Path) -> Result<ReadDir, FsError> {
        let name = self.resolve(path)?;
        self.directory(&name)?;
        let mut entries = Vec::new();
        for child in self.0.children.get(&name).ok_or(FsError::EntryNotFound)? {
            entries.push(DirEntry {
                path: PathBuf::from(format!("/{child}")),
                metadata: Ok(self.meta_name(child)?),
            });
        }
        Ok(ReadDir::new(entries))
    }
    fn readlink(&self, path: &Path) -> Result<PathBuf, FsError> {
        self.resolve(path)?;
        Err(FsError::InvalidInput)
    }
    fn new_open_options(&self) -> OpenOptions<'_> {
        OpenOptions::new(self)
    }
    fn create_dir(&self, _: &Path) -> Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn create_symlink(&self, _: &Path, _: &Path) -> Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn hard_link(&self, _: &Path, _: &Path) -> Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn remove_dir(&self, _: &Path) -> Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn remove_file(&self, _: &Path) -> Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn rename<'a>(
        &'a self,
        _: &'a Path,
        _: &'a Path,
    ) -> Pin<Box<dyn Future<Output = Result<(), FsError>> + Send + 'a>> {
        Box::pin(async { Err(FsError::PermissionDenied) })
    }
}

#[derive(Debug)]
struct SourceFile {
    bytes: Arc<[u8]>,
    position: u64,
    open: bool,
}
impl SourceFile {
    fn active(&self) -> io::Result<()> {
        if self.open {
            Ok(())
        } else {
            Err(io::ErrorKind::NotConnected.into())
        }
    }
}
impl AsyncRead for SourceFile {
    fn poll_read(
        self: Pin<&mut Self>,
        _: &mut Context<'_>,
        buffer: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        this.active()?;
        let start = this.position.min(this.bytes.len() as u64) as usize;
        let count = buffer.remaining().min(this.bytes.len() - start);
        buffer.put_slice(&this.bytes[start..start + count]);
        this.position += count as u64;
        Poll::Ready(Ok(()))
    }
}
impl AsyncWrite for SourceFile {
    fn poll_write(self: Pin<&mut Self>, _: &mut Context<'_>, _: &[u8]) -> Poll<io::Result<usize>> {
        self.active()?;
        Poll::Ready(Err(io::ErrorKind::PermissionDenied.into()))
    }
    fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
        Poll::Ready(self.active())
    }
    fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
        self.get_mut().open = false;
        Poll::Ready(Ok(()))
    }
}
impl AsyncSeek for SourceFile {
    fn start_seek(self: Pin<&mut Self>, seek: SeekFrom) -> io::Result<()> {
        let this = self.get_mut();
        this.active()?;
        let position = match seek {
            SeekFrom::Start(n) => n as i128,
            SeekFrom::Current(n) => this.position as i128 + n as i128,
            SeekFrom::End(n) => this.bytes.len() as i128 + n as i128,
        };
        if !(0..=u64::MAX as i128).contains(&position) {
            return Err(io::ErrorKind::InvalidInput.into());
        }
        this.position = position as u64;
        Ok(())
    }
    fn poll_complete(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<u64>> {
        self.active()?;
        Poll::Ready(Ok(self.position))
    }
}
impl VirtualFile for SourceFile {
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
    fn set_len(&mut self, _: u64) -> Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn set_times(&mut self, _: Option<u64>, _: Option<u64>) -> Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn unlink(&mut self) -> Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn write_from_mmap(&mut self, _: u64, _: u64) -> io::Result<()> {
        Err(io::ErrorKind::PermissionDenied.into())
    }
    fn copy_reference(
        &mut self,
        _: Box<dyn VirtualFile + Send + Sync>,
    ) -> Pin<Box<dyn Future<Output = io::Result<()>> + Send + '_>> {
        Box::pin(async { Err(io::ErrorKind::PermissionDenied.into()) })
    }
    fn copy_from_owned_buffer(
        &mut self,
        _: &OwnedBuffer,
    ) -> Pin<Box<dyn Future<Output = io::Result<()>> + Send + '_>> {
        Box::pin(async { Err(io::ErrorKind::PermissionDenied.into()) })
    }
    fn poll_read_ready(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<usize>> {
        self.active()?;
        Poll::Ready(Ok(
            (self.bytes.len() as u64).saturating_sub(self.position) as usize
        ))
    }
    fn poll_write_ready(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<usize>> {
        self.active()?;
        Poll::Ready(Err(io::ErrorKind::PermissionDenied.into()))
    }
}

#[cfg(test)]
mod tests;
