//! Read-only byte identity checks for the fixed WordPress preview release.
//!
//! The caller supplies the manifest digest from its trusted release metadata.
//! Verification describes the files read during this call. The release directory
//! must remain protected against modification between validation and execution.

use serde::{Deserialize, Deserializer, Serialize, de};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    fs::{self, File},
    io::{self, Read},
    path::Path,
};

pub const RELEASE_SCHEMA: &str = "recastpress.wordpress-preview-release.v0.5";
pub const RELEASE_TARGET: &str = "linux-x86_64";
pub const SOURCE_MANIFEST_SHA256: &str =
    "734275a54c29e1df269ae5fe34782f567de05a1b3230aad7faed252b37d783b3";
const SOURCE_MANIFEST_BYTES: u64 = 650_759;
const WORDPRESS_FILES: usize = 3782;
const WORDPRESS_DIRECTORIES: usize = 467;
const WORDPRESS_BYTES: u64 = 110_077_999;
const MAX_WORDPRESS_FILE: u64 = 16 * 1024 * 1024;
const RELEASE_FILES: [(&str, u64); 11] = [
    ("bin/http", 256 * 1024 * 1024),
    ("bin/worker", 256 * 1024 * 1024),
    ("php/php.wasm", 128 * 1024 * 1024),
    ("wordpress/source-manifest.json", SOURCE_MANIFEST_BYTES),
    ("php/home.php", 256 * 1024),
    ("php/missing.php", 256 * 1024),
    ("php/context.php", 256 * 1024),
    ("php/bootstrap.php", 256 * 1024),
    ("php/helpers.php", 256 * 1024),
    ("php/adapter.php", 256 * 1024),
    ("php/db.php", 256 * 1024),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReleaseError {
    InvalidDigest,
    InvalidLayout,
    InvalidManifest,
    ArtifactMismatch,
    WordPressMismatch,
    Io,
}

impl fmt::Display for ReleaseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidDigest => "invalid release manifest digest",
            Self::InvalidLayout => "invalid release directory layout",
            Self::InvalidManifest => "invalid release manifest",
            Self::ArtifactMismatch => "release artifact identity mismatch",
            Self::WordPressMismatch => "WordPress source identity mismatch",
            Self::Io => "release could not be read",
        })
    }
}

impl std::error::Error for ReleaseError {}

impl From<io::Error> for ReleaseError {
    fn from(_: io::Error) -> Self {
        Self::Io
    }
}

#[derive(Debug, Serialize)]
pub struct VerifiedRelease {
    pub schema: &'static str,
    pub target: &'static str,
    pub manifest_sha256: String,
    pub release_files: usize,
    pub wordpress_files: usize,
    pub wordpress_directories: usize,
    pub wordpress_bytes: u64,
    #[serde(skip)]
    artifacts: BTreeMap<String, FileIdentity>,
}

impl VerifiedRelease {
    pub fn artifact(&self, name: &str) -> Option<&FileIdentity> {
        self.artifacts.get(name)
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileIdentity {
    pub bytes: u64,
    pub sha256: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: String,
    target: String,
    #[serde(deserialize_with = "unique_files")]
    files: BTreeMap<String, FileIdentity>,
}

fn unique_files<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<BTreeMap<String, FileIdentity>, D::Error> {
    struct FilesVisitor;
    impl<'de> de::Visitor<'de> for FilesVisitor {
        type Value = BTreeMap<String, FileIdentity>;

        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str("release file identities with unique paths")
        }

        fn visit_map<A: de::MapAccess<'de>>(self, mut entries: A) -> Result<Self::Value, A::Error> {
            let mut files = BTreeMap::new();
            while let Some((name, identity)) = entries.next_entry()? {
                if files.insert(name, identity).is_some() {
                    return Err(de::Error::custom("duplicate release file"));
                }
            }
            Ok(files)
        }
    }
    deserializer.deserialize_map(FilesVisitor)
}

// The whole catalog is byte-pinned before decoding its inventory. Its remaining
// fields are preparation metadata and never supply any host paths.
#[derive(Deserialize)]
struct WordPressCatalog {
    directories: BTreeSet<String>,
    files: BTreeMap<String, FileIdentity>,
}

fn digest_valid(digest: &str) -> bool {
    digest.len() == 64
        && digest
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

fn file(path: &Path) -> Result<File, ReleaseError> {
    if !fs::symlink_metadata(path)?.is_file() || path.canonicalize()? != path {
        return Err(ReleaseError::InvalidLayout);
    }
    let file = File::open(path)?;
    if !file.metadata()?.is_file() {
        return Err(ReleaseError::InvalidLayout);
    }
    Ok(file)
}

fn read_bounded(path: &Path, maximum: u64) -> Result<Vec<u8>, ReleaseError> {
    let mut bytes = Vec::new();
    file(path)?.take(maximum + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > maximum {
        return Err(ReleaseError::InvalidManifest);
    }
    Ok(bytes)
}

fn matches_file(path: &Path, expected: &FileIdentity) -> Result<bool, ReleaseError> {
    let file = file(path)?;
    if file.metadata()?.len() != expected.bytes {
        return Ok(false);
    }
    let mut hasher = Sha256::new();
    let mut reader = file.take(expected.bytes + 1);
    let mut buffer = [0; 64 * 1024];
    let mut count = 0;
    loop {
        let read = reader.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
        count += read as u64;
    }
    Ok(count == expected.bytes && format!("{:x}", hasher.finalize()) == expected.sha256)
}

fn exact_children(path: &Path, names: &[&str]) -> Result<(), ReleaseError> {
    if !fs::symlink_metadata(path)?.is_dir() || path.canonicalize()? != path {
        return Err(ReleaseError::InvalidLayout);
    }
    let mut missing: BTreeSet<&str> = names.iter().copied().collect();
    for entry in fs::read_dir(path)? {
        let name = entry?.file_name();
        if !name.to_str().is_some_and(|name| missing.remove(name)) {
            return Err(ReleaseError::InvalidLayout);
        }
    }
    if !missing.is_empty() {
        return Err(ReleaseError::InvalidLayout);
    }
    Ok(())
}

fn verify_wordpress(root: &Path, wire: &[u8]) -> Result<(), ReleaseError> {
    if wire.len() as u64 != SOURCE_MANIFEST_BYTES
        || format!("{:x}", Sha256::digest(wire)) != SOURCE_MANIFEST_SHA256
    {
        return Err(ReleaseError::WordPressMismatch);
    }
    let mut catalog: WordPressCatalog =
        serde_json::from_slice(wire).map_err(|_| ReleaseError::WordPressMismatch)?;
    if catalog.directories.len() != WORDPRESS_DIRECTORIES || catalog.files.len() != WORDPRESS_FILES
    {
        return Err(ReleaseError::WordPressMismatch);
    }
    let mut pending = vec![(root.to_path_buf(), String::new())];
    let mut bytes = 0;
    while let Some((directory, prefix)) = pending.pop() {
        if !fs::symlink_metadata(&directory)?.is_dir() || directory.canonicalize()? != directory {
            return Err(ReleaseError::InvalidLayout);
        }
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            let name = entry.file_name();
            let name = name.to_str().ok_or(ReleaseError::WordPressMismatch)?;
            let relative = if prefix.is_empty() {
                name.to_owned()
            } else {
                format!("{prefix}/{name}")
            };
            let kind = entry.file_type()?;
            if kind.is_dir() && catalog.directories.remove(&relative) {
                pending.push((entry.path(), relative));
            } else if kind.is_file() {
                let identity = catalog
                    .files
                    .remove(&relative)
                    .ok_or(ReleaseError::WordPressMismatch)?;
                if identity.bytes > MAX_WORDPRESS_FILE || !matches_file(&entry.path(), &identity)? {
                    return Err(ReleaseError::WordPressMismatch);
                }
                bytes += identity.bytes;
            } else {
                return Err(ReleaseError::WordPressMismatch);
            }
        }
    }
    if !catalog.directories.is_empty() || !catalog.files.is_empty() || bytes != WORDPRESS_BYTES {
        return Err(ReleaseError::WordPressMismatch);
    }
    Ok(())
}

pub fn verify(
    root: &Path,
    expected_manifest_sha256: &str,
) -> Result<VerifiedRelease, ReleaseError> {
    if !digest_valid(expected_manifest_sha256) {
        return Err(ReleaseError::InvalidDigest);
    }
    if !fs::symlink_metadata(root)?.is_dir() {
        return Err(ReleaseError::InvalidLayout);
    }
    let root = root.canonicalize()?;
    let wire = read_bounded(&root.join("release.json"), 16 * 1024)?;
    if format!("{:x}", Sha256::digest(&wire)) != expected_manifest_sha256 {
        return Err(ReleaseError::InvalidDigest);
    }
    let manifest: Manifest =
        serde_json::from_slice(&wire).map_err(|_| ReleaseError::InvalidManifest)?;
    if manifest.schema != RELEASE_SCHEMA
        || manifest.target != RELEASE_TARGET
        || manifest.files.len() != RELEASE_FILES.len()
    {
        return Err(ReleaseError::InvalidManifest);
    }
    exact_children(&root, &["release.json", "bin", "php", "wordpress"])?;
    exact_children(&root.join("bin"), &["http", "worker"])?;
    exact_children(
        &root.join("php"),
        &[
            "php.wasm",
            "home.php",
            "missing.php",
            "context.php",
            "bootstrap.php",
            "helpers.php",
            "adapter.php",
            "db.php",
        ],
    )?;
    exact_children(&root.join("wordpress"), &["source-manifest.json", "site"])?;
    let catalog_name = "wordpress/source-manifest.json";
    for (name, maximum) in RELEASE_FILES {
        let identity = manifest
            .files
            .get(name)
            .ok_or(ReleaseError::InvalidManifest)?;
        if identity.bytes == 0 || identity.bytes > maximum || !digest_valid(&identity.sha256) {
            return Err(ReleaseError::InvalidManifest);
        }
        if name == catalog_name {
            if identity.bytes != SOURCE_MANIFEST_BYTES || identity.sha256 != SOURCE_MANIFEST_SHA256
            {
                return Err(ReleaseError::WordPressMismatch);
            }
        } else if !matches_file(&root.join(name), identity)? {
            return Err(ReleaseError::ArtifactMismatch);
        }
    }
    let catalog = read_bounded(&root.join(catalog_name), SOURCE_MANIFEST_BYTES)?;
    verify_wordpress(&root.join("wordpress/site"), &catalog)?;
    Ok(VerifiedRelease {
        schema: "recastpress.wordpress-preview-release-verification.v0.5",
        target: RELEASE_TARGET,
        manifest_sha256: expected_manifest_sha256.to_owned(),
        release_files: RELEASE_FILES.len(),
        wordpress_files: WORDPRESS_FILES,
        wordpress_directories: WORDPRESS_DIRECTORIES,
        wordpress_bytes: WORDPRESS_BYTES,
        artifacts: manifest.files,
    })
}
