//! Bounded diagnostics and Windows backing-path separator adaptation.
//! Frozen SourceFs still validates/constrains all resolved paths and file operations.
use recastpress_wp_source_fs_probe::SourceFs;
use serde_json::{Value, json};
use std::{
    future::Future,
    path::{Path, PathBuf},
    pin::Pin,
    sync::{Arc, Mutex},
};
use wasmer_wasix::virtual_fs::{
    FileOpener, FileSystem, FsError, Metadata, OpenOptions, OpenOptionsConfig, ReadDir, VirtualFile,
};

#[derive(Debug, Clone)]
pub struct TraceFs {
    inner: SourceFs,
    calls: Arc<Mutex<Vec<Value>>>,
}
impl TraceFs {
    pub fn new(inner: SourceFs) -> Self {
        Self {
            inner,
            calls: Arc::new(Mutex::new(vec![])),
        }
    }
    pub fn snapshot(&self) -> Vec<Value> {
        self.calls.lock().unwrap().clone()
    }
    fn note<T>(&self, op: &str, path: &Path, result: &Result<T, FsError>) {
        let mut calls = self.calls.lock().unwrap();
        if calls.len() < 256 {
            calls.push(json!({"op":op,"path":path.to_str(),"ok":result.is_ok(),"error":result.as_ref().err().map(|e| format!("{e:?}"))}));
        }
    }
}
fn backing_path(path: &Path) -> Result<PathBuf, FsError> {
    let raw = path.to_str().ok_or(FsError::InvalidInput)?;
    if raw.len() > 4096 {
        return Err(FsError::InvalidInput);
    }
    #[cfg(windows)]
    let raw = raw.replace('\\', "/");
    Ok(PathBuf::from(raw))
}
impl FileOpener for TraceFs {
    fn open(
        &self,
        path: &Path,
        config: &OpenOptionsConfig,
    ) -> Result<Box<dyn VirtualFile + Send + Sync>, FsError> {
        let result = backing_path(path).and_then(|p| FileOpener::open(&self.inner, &p, config));
        self.note("open", path, &result);
        result
    }
}
impl FileSystem for TraceFs {
    fn metadata(&self, path: &Path) -> Result<Metadata, FsError> {
        let r = backing_path(path).and_then(|p| self.inner.metadata(&p));
        self.note("metadata", path, &r);
        r
    }
    fn symlink_metadata(&self, path: &Path) -> Result<Metadata, FsError> {
        let r = backing_path(path).and_then(|p| self.inner.symlink_metadata(&p));
        self.note("symlink_metadata", path, &r);
        r
    }
    fn read_dir(&self, path: &Path) -> Result<ReadDir, FsError> {
        let r = backing_path(path).and_then(|p| self.inner.read_dir(&p));
        self.note("read_dir", path, &r);
        r
    }
    fn readlink(&self, path: &Path) -> Result<PathBuf, FsError> {
        let r = backing_path(path).and_then(|p| self.inner.readlink(&p));
        self.note("readlink", path, &r);
        r
    }
    fn new_open_options(&self) -> OpenOptions<'_> {
        OpenOptions::new(self)
    }
    fn create_dir(&self, path: &Path) -> Result<(), FsError> {
        let r = self.inner.create_dir(path);
        self.note("create_dir", path, &r);
        r
    }
    fn create_symlink(&self, src: &Path, dst: &Path) -> Result<(), FsError> {
        self.inner.create_symlink(src, dst)
    }
    fn hard_link(&self, src: &Path, dst: &Path) -> Result<(), FsError> {
        self.inner.hard_link(src, dst)
    }
    fn remove_dir(&self, path: &Path) -> Result<(), FsError> {
        let r = self.inner.remove_dir(path);
        self.note("remove_dir", path, &r);
        r
    }
    fn remove_file(&self, path: &Path) -> Result<(), FsError> {
        let r = self.inner.remove_file(path);
        self.note("remove_file", path, &r);
        r
    }
    fn rename<'a>(
        &'a self,
        src: &'a Path,
        dst: &'a Path,
    ) -> Pin<Box<dyn Future<Output = Result<(), FsError>> + Send + 'a>> {
        Box::pin(async move {
            let r = self.inner.rename(src, dst).await;
            self.note("rename", src, &r);
            r
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fs() -> TraceFs {
        TraceFs::new(
            SourceFs::new(
                std::collections::BTreeMap::from([(
                    "dir/file.php".to_owned(),
                    Arc::<[u8]>::from(&b"<?php"[..]),
                )]),
                std::collections::BTreeSet::from(["dir".to_owned()]),
            )
            .unwrap(),
        )
    }
    #[test]
    fn native_separator_adapter_keeps_finite_catalog_and_mutation_denial() {
        let fs = fs();
        assert_eq!(fs.metadata(Path::new("/dir/file.php")).unwrap().len, 5);
        #[cfg(windows)]
        assert_eq!(fs.metadata(Path::new("/dir\\file.php")).unwrap().len, 5);
        #[cfg(not(windows))]
        assert!(fs.metadata(Path::new("/dir\\file.php")).is_err());
        assert!(fs.metadata(Path::new("/not-host-file")).is_err());
        assert!(
            fs.new_open_options()
                .write(true)
                .open("/dir/file.php")
                .is_err()
        );
        assert!(fs.remove_file(Path::new("/dir/file.php")).is_err());
        assert_eq!(fs.inner.file_count(), 1);
    }
    #[test]
    fn native_separator_adapter_cannot_escape_or_accept_host_prefix() {
        let fs = fs();
        for path in [
            "/../dir/file.php",
            "/dir/../../file.php",
            "C:/dir/file.php",
            "//host/share",
            "/dir/\0file",
        ] {
            assert!(fs.metadata(Path::new(path)).is_err(), "{path:?}");
        }
        assert!(backing_path(Path::new(&"x".repeat(4097))).is_err());
        assert_eq!(
            fs.metadata(Path::new("/dir/../dir/file.php")).unwrap().len,
            5
        );
    }
}
