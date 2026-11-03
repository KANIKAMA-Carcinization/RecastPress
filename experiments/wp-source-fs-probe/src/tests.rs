use super::*;
use sha2::{Digest, Sha256};
use std::{ffi::OsString, task::Wake};
use tokio::io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt};
const DATA: &[u8] = b"immutable\0\xffsource\n";
fn fixture() -> SourceFs {
    SourceFs::new(
        BTreeMap::from([
            (
                "wp-settings.php".into(),
                Arc::<[u8]>::from(&b"<?php require\n"[..]),
            ),
            ("wp-includes/class-wpdb.php".into(), Arc::<[u8]>::from(DATA)),
            (
                "wp-content/themes/twentytwentyfive/styles/empty.json".into(),
                Arc::<[u8]>::from(&b""[..]),
            ),
        ]),
        [
            "wp-includes",
            "wp-content",
            "wp-content/languages",
            "wp-content/themes",
            "wp-content/themes/twentytwentyfive",
            "wp-content/themes/twentytwentyfive/styles",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect(),
    )
    .unwrap()
}
fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .build()
        .unwrap()
}
fn open(fs: &SourceFs) -> Box<dyn VirtualFile + Send + Sync> {
    fs.new_open_options()
        .read(true)
        .open("/wp-includes/class-wpdb.php")
        .unwrap()
}
struct Noop;
impl Wake for Noop {
    fn wake(self: Arc<Self>) {}
}
fn ready(file: &mut (dyn VirtualFile + Send + Sync)) -> io::Result<usize> {
    let waker = std::task::Waker::from(Arc::new(Noop));
    let mut cx = Context::from_waker(&waker);
    match Pin::new(file).poll_read_ready(&mut cx) {
        Poll::Ready(value) => value,
        Poll::Pending => panic!("immutable bytes are ready"),
    }
}

#[test]
fn root_deep_metadata_and_empty_file_are_finite_and_not_symlinks() {
    let fs = fixture();
    assert!(fs.metadata(Path::new("/")).unwrap().is_dir());
    assert!(fs.metadata(Path::new(".")).unwrap().is_dir());
    let path = Path::new("/wp-includes/class-wpdb.php");
    let meta = fs.metadata(path).unwrap();
    assert!(meta.is_file() && !meta.ft.symlink);
    assert_eq!(meta.len(), DATA.len() as u64);
    assert_eq!(fs.symlink_metadata(path).unwrap(), meta);
    assert_eq!((meta.accessed, meta.created, meta.modified), (0, 0, 0));
    assert_eq!(
        fs.metadata(Path::new(
            "wp-content/themes/twentytwentyfive/styles/empty.json"
        ))
        .unwrap()
        .len(),
        0
    );
    assert_eq!(fs.file_count(), 3);
    assert_eq!(fs.directory_count(), 6);
}
#[test]
fn sorted_direct_children_empty_directories_missing_and_not_directory() {
    let fs = fixture();
    let entries: Vec<_> = fs
        .read_dir(Path::new("/"))
        .unwrap()
        .map(|entry| entry.unwrap())
        .collect();
    assert_eq!(
        entries
            .iter()
            .map(|e| e.path.to_str().unwrap())
            .collect::<Vec<_>>(),
        ["/wp-content", "/wp-includes", "/wp-settings.php"]
    );
    let theme: Vec<_> = fs
        .read_dir(Path::new("wp-content/themes/twentytwentyfive"))
        .unwrap()
        .map(|entry| entry.unwrap())
        .collect();
    assert_eq!(theme.len(), 1);
    assert_eq!(theme[0].file_name(), OsString::from("styles"));
    assert!(theme[0].metadata().unwrap().is_dir());
    assert!(
        fs.read_dir(Path::new("wp-content/languages"))
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        fs.read_dir(Path::new("wp-settings.php")).unwrap_err(),
        FsError::BaseNotDirectory
    );
    assert_eq!(
        fs.metadata(Path::new("missing")).unwrap_err(),
        FsError::EntryNotFound
    );
    assert!(matches!(
        fs.new_open_options().read(true).open("/"),
        Err(FsError::NotAFile)
    ));
}
#[test]
fn whole_read_preserves_binary_hash_and_repeated_eof() {
    runtime().block_on(async {
        let fs = fixture();
        let mut file = open(&fs);
        let mut bytes = vec![];
        file.read_to_end(&mut bytes).await.unwrap();
        assert_eq!(bytes, DATA);
        assert_eq!(
            format!("{:x}", Sha256::digest(&bytes)),
            "d91292e95860c5f1daecf09a8fcebfaf8f719bca981206c2fba0dad3bedeb0d4"
        );
        assert_eq!(ready(&mut *file).unwrap(), 0);
        assert_eq!(file.read(&mut [0; 1]).await.unwrap(), 0);
        assert_eq!(file.read(&mut [0; 4]).await.unwrap(), 0);
    });
}
#[test]
fn partial_read_seek_overflow_and_beyond_eof_are_bounded() {
    runtime().block_on(async {
        let fs = fixture();
        let mut f = open(&fs);
        let mut part = [0; 3];
        f.read_exact(&mut part).await.unwrap();
        assert_eq!(&part, &DATA[..3]);
        f.seek(SeekFrom::Current(2)).await.unwrap();
        f.read_exact(&mut part).await.unwrap();
        assert_eq!(&part, &DATA[5..8]);
        f.seek(SeekFrom::End(-4)).await.unwrap();
        let mut end = vec![];
        f.read_to_end(&mut end).await.unwrap();
        assert_eq!(end, &DATA[DATA.len() - 4..]);
        f.seek(SeekFrom::Start(u64::MAX)).await.unwrap();
        assert_eq!(ready(&mut *f).unwrap(), 0);
        assert_eq!(f.read(&mut part).await.unwrap(), 0);
        assert!(f.seek(SeekFrom::Current(1)).await.is_err());
        assert!(
            f.seek(SeekFrom::End(-(DATA.len() as i64) - 1))
                .await
                .is_err()
        );
        f.seek(SeekFrom::Start(0)).await.unwrap();
        f.read_exact(&mut part).await.unwrap();
        assert_eq!(&part, &DATA[..3]);
    });
}
#[test]
fn write_open_flags_and_all_file_mutation_apis_fail_without_changes() {
    runtime().block_on(async {
        let fs = fixture();
        for flag in 0..6 {
            let mut options = fs.new_open_options();
            options.read(true);
            match flag {
                0 => {
                    options.write(true);
                }
                1 => {
                    options.append(true);
                }
                2 => {
                    options.truncate(true);
                }
                3 => {
                    options.create(true);
                }
                4 => {
                    options.create_new(true);
                }
                _ => {
                    options.read(false);
                }
            }
            assert!(matches!(
                options.open("wp-includes/class-wpdb.php"),
                Err(FsError::PermissionDenied)
            ));
        }
        let mut f = open(&fs);
        assert_eq!(
            f.write(b"x").await.unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert!(f.set_len(0).is_err());
        assert!(f.set_times(Some(1), Some(1)).is_err());
        assert!(f.unlink().is_err());
        assert!(f.write_from_mmap(0, 0).is_err());
        let empty = fs
            .new_open_options()
            .read(true)
            .open("wp-content/themes/twentytwentyfive/styles/empty.json")
            .unwrap();
        assert!(f.copy_reference(empty).await.is_err());
        assert!(
            f.copy_from_owned_buffer(&OwnedBuffer::from(Vec::<u8>::new()))
                .await
                .is_err()
        );
        let mut bytes = vec![];
        f.read_to_end(&mut bytes).await.unwrap();
        assert_eq!(bytes, DATA);
        assert_eq!(fs.file_count(), 3);
    });
}
#[test]
fn close_rejects_read_seek_write_flush_and_readiness_then_reopen_is_fresh() {
    runtime().block_on(async {
        let fs = fixture();
        let mut f = open(&fs);
        f.shutdown().await.unwrap();
        assert!(!f.is_open());
        assert_eq!(
            f.read(&mut [0; 1]).await.unwrap_err().kind(),
            io::ErrorKind::NotConnected
        );
        assert_eq!(
            f.write(b"x").await.unwrap_err().kind(),
            io::ErrorKind::NotConnected
        );
        assert!(f.seek(SeekFrom::Start(0)).await.is_err());
        assert!(f.flush().await.is_err());
        assert!(ready(&mut *f).is_err());
        f.shutdown().await.unwrap();
        let mut fresh = open(&fs);
        let mut first = [0; 2];
        fresh.read_exact(&mut first).await.unwrap();
        assert_eq!(&first, &DATA[..2]);
    });
}
#[test]
fn cloned_catalog_open_handles_and_directory_iterators_have_independent_cursors() {
    runtime().block_on(async {
        let a = fixture();
        let b = a.clone();
        assert!(Arc::ptr_eq(&a.0, &b.0));
        let mut fa = open(&a);
        let mut fb = open(&b);
        let mut left = [0; 3];
        let mut right = [0; 3];
        fa.read_exact(&mut left).await.unwrap();
        fb.read_exact(&mut right).await.unwrap();
        assert_eq!(left, right);
        fa.read_exact(&mut left).await.unwrap();
        assert_eq!(&left, &DATA[3..6]);
        assert_eq!(&right, &DATA[..3]);
        let mut da = a.read_dir(Path::new("/")).unwrap();
        let mut db = b.read_dir(Path::new("/")).unwrap();
        let first = da.next().unwrap();
        da.next();
        assert_eq!(first, db.next().unwrap());
    });
}
#[test]
fn normalized_parent_paths_cannot_escape_or_use_windows_or_non_utf8_inputs() {
    let fs = fixture();
    assert!(
        fs.metadata(Path::new("/wp-includes/../wp-settings.php"))
            .unwrap()
            .is_file()
    );
    assert!(
        fs.read_dir(Path::new("wp-content//themes/../languages/./"))
            .unwrap()
            .is_empty()
    );
    for path in [
        "../wp-settings.php",
        "wp-includes/../../wp-settings.php",
        "/../../outside",
    ] {
        assert_eq!(
            fs.metadata(Path::new(path)).unwrap_err(),
            FsError::PermissionDenied
        );
    }
    for path in [
        "C:/Windows/file",
        "C:relative",
        "\\\\server\\share",
        "//server/share",
        "wp-includes\\class-wpdb.php",
        "file\0hidden",
    ] {
        assert_eq!(
            fs.metadata(Path::new(path)).unwrap_err(),
            FsError::InvalidInput
        );
    }
    for path in [
        "wp-settings.php/child",
        "wp-settings.php/../wp-includes",
        "wp-settings.php/",
    ] {
        assert_eq!(
            fs.metadata(Path::new(path)).unwrap_err(),
            FsError::BaseNotDirectory
        );
    }
    assert_eq!(
        fs.metadata(Path::new("missing/../wp-settings.php"))
            .unwrap_err(),
        FsError::EntryNotFound
    );
    #[cfg(unix)]
    let invalid = {
        use std::os::unix::ffi::OsStringExt;
        PathBuf::from(OsString::from_vec(vec![0xff]))
    };
    #[cfg(windows)]
    let invalid = {
        use std::os::windows::ffi::OsStringExt;
        PathBuf::from(OsString::from_wide(&[0xd800]))
    };
    #[cfg(any(unix, windows))]
    assert_eq!(fs.metadata(&invalid).unwrap_err(), FsError::InvalidInput);
}
#[test]
fn constructor_rejects_noncanonical_paths_missing_parents_and_node_collisions() {
    for name in [
        "",
        "/absolute",
        "a//b",
        "a/./b",
        "a/../b",
        "a\\b",
        "C:x",
        "a\0b",
    ] {
        assert!(matches!(
            SourceFs::new(
                BTreeMap::from([(name.into(), Arc::<[u8]>::from(&b"x"[..]))]),
                BTreeSet::new()
            ),
            Err(CatalogError::InvalidPath)
        ));
    }
    let file = || BTreeMap::from([("same".into(), Arc::<[u8]>::from(&b"x"[..]))]);
    assert!(matches!(
        SourceFs::new(file(), BTreeSet::from(["same".into()])),
        Err(CatalogError::FileDirectoryCollision)
    ));
    assert!(matches!(
        SourceFs::new(
            BTreeMap::from([("missing/file".into(), Arc::<[u8]>::from(&b"x"[..]))]),
            BTreeSet::new()
        ),
        Err(CatalogError::MissingParent)
    ));
    assert!(matches!(
        SourceFs::new(file(), BTreeSet::from(["same/child".into()])),
        Err(CatalogError::ParentIsFile)
    ));
    let root = SourceFs::new(BTreeMap::new(), BTreeSet::from([String::new()])).unwrap();
    assert_eq!(root.directory_count(), 0);
    assert!(root.read_dir(Path::new("/")).unwrap().is_empty());
}
#[test]
fn namespace_mutations_and_symlink_operations_never_delegate_to_a_host() {
    runtime().block_on(async {
        let fs = fixture();
        let outside = Path::new("../../host");
        let file = Path::new("wp-settings.php");
        assert_eq!(fs.create_dir(outside), Err(FsError::PermissionDenied));
        assert_eq!(
            fs.remove_dir(Path::new("wp-content")),
            Err(FsError::PermissionDenied)
        );
        assert_eq!(fs.remove_file(file), Err(FsError::PermissionDenied));
        assert_eq!(
            fs.create_symlink(outside, file),
            Err(FsError::PermissionDenied)
        );
        assert_eq!(fs.hard_link(file, outside), Err(FsError::PermissionDenied));
        assert_eq!(
            fs.rename(file, outside).await,
            Err(FsError::PermissionDenied)
        );
        assert_eq!(fs.readlink(file), Err(FsError::InvalidInput));
        assert_eq!(
            fs.readlink(Path::new("missing")),
            Err(FsError::EntryNotFound)
        );
        assert_eq!(fs.file_count(), 3);
    });
}
#[test]
fn quotas_enforce_boundaries_and_count_shared_bytes_by_logical_file_size() {
    let limits = Limits {
        files: 2,
        directories: 1,
        file_bytes: 4,
        total_bytes: 6,
        path_bytes: 64,
    };
    let data = Arc::<[u8]>::from(&b"abc"[..]);
    let files = BTreeMap::from([("a".into(), data.clone()), ("b".into(), data.clone())]);
    assert_eq!(
        SourceFs::with_limits(files.clone(), BTreeSet::new(), limits)
            .unwrap()
            .total_bytes(),
        6
    );
    assert!(matches!(
        SourceFs::with_limits(
            files.clone(),
            BTreeSet::new(),
            Limits {
                total_bytes: 5,
                ..limits
            }
        ),
        Err(CatalogError::TotalTooLarge)
    ));
    assert!(matches!(
        SourceFs::with_limits(files, BTreeSet::new(), Limits { files: 1, ..limits }),
        Err(CatalogError::TooManyFiles)
    ));
    assert!(matches!(
        SourceFs::with_limits(
            BTreeMap::new(),
            BTreeSet::from(["a".into(), "b".into()]),
            limits
        ),
        Err(CatalogError::TooManyDirectories)
    ));
    assert!(matches!(
        SourceFs::new(
            BTreeMap::from([(
                "large".into(),
                Arc::<[u8]>::from(vec![0; 16 * 1024 * 1024 + 1])
            )]),
            BTreeSet::new()
        ),
        Err(CatalogError::FileTooLarge)
    ));
    let large = Arc::<[u8]>::from(vec![0; 16 * 1024 * 1024]);
    let files = (0..17).map(|i| (format!("f{i}"), large.clone())).collect();
    assert!(matches!(
        SourceFs::new(files, BTreeSet::new()),
        Err(CatalogError::TotalTooLarge)
    ));
    assert!(matches!(
        SourceFs::with_limits(
            BTreeMap::new(),
            BTreeSet::new(),
            Limits {
                files: 5001,
                ..Limits::default()
            }
        ),
        Err(CatalogError::InvalidLimits)
    ));
    let files = (0..5001)
        .map(|i| (format!("f{i}"), Arc::<[u8]>::from(&b""[..])))
        .collect();
    assert!(matches!(
        SourceFs::new(files, BTreeSet::new()),
        Err(CatalogError::TooManyFiles)
    ));
}
