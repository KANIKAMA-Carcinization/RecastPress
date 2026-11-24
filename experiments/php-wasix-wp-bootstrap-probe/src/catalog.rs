//! Exact derived fixed-catalog loader; see derivation_gate.py.
use recastpress_wp_source_fs_probe::SourceFs;
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
use std::{collections::{BTreeMap,BTreeSet},fs::File,io::Read,path::{Path,PathBuf},sync::Arc};
use wasmer_wasix::virtual_fs::FileSystem;
type Result<T> = std::result::Result<T, &'static str>;
const FILE_PIN: &str = "afacbaee85821a793af5964b0b797d2aa2d0e6b27bf8cb76763dc3c0cb5d911c";
const DIR_PIN: &str = "7d8e0d997ccd9c7e565c8804a2d8c00a5502eec10c578338fbbd347dc37e57a5";
const HOOK_OVERLAY_RECEIPT: &str = "experiments/php-wasix-wp-hook-dispatch-probe/evidence/overlay-v5/overlay.json";
const HOOK_OVERLAY_RECEIPT_SHA: &str = "73a7efccef5e52e4cbf5ff5f7e2544ec654ef3f7faa03f830f5e333a0a4eb360";
const HOOK_OVERLAY_FILES: [(&str, &str, &str); 2] = [
    (
        "wp-includes/class-wp-hook.php",
        "experiments/php-wasix-wp-hook-dispatch-probe/evidence/overlay-v5/wp-includes/class-wp-hook.php",
        "76eacf182c0e07c4285be0eff1df5bfd5cf4ce2a08c748a1085b241ffff473e4",
    ),
    (
        "wp-includes/plugin.php",
        "experiments/php-wasix-wp-hook-dispatch-probe/evidence/overlay-v5/wp-includes/plugin.php",
        "db8dc3d3c61a43ff225e01881187f47ce8eedfb1a65a56c9cd60bf13aea04d5c",
    ),
];
const MAX_FILE: u64 = 16 * 1024 * 1024;
fn check(value: bool, code: &'static str) -> Result<()> {
    if value { Ok(()) } else { Err(code) }
}
fn sha(wire: &[u8]) -> String {
    format!("{:x}", Sha256::digest(wire))
}
fn fixed(path: &Path, max: u64) -> Result<Vec<u8>> {
    let meta = std::fs::symlink_metadata(path).map_err(|_| "catalog_host_metadata")?;
    check(
        meta.is_file() && !meta.file_type().is_symlink() && meta.len() <= max,
        "catalog_host_file_type_size",
    )?;
    check(
        path.canonicalize().map_err(|_| "catalog_host_canonical")? == path,
        "catalog_host_file_alias",
    )?;
    let mut data = Vec::new();
    File::open(path)
        .map_err(|_| "catalog_host_open")?
        .take(max + 1)
        .read_to_end(&mut data)
        .map_err(|_| "catalog_host_read")?;
    check(data.len() as u64 == meta.len(), "catalog_host_file_grew")?;
    Ok(data)
}
fn relative(value: &str) -> Result<()> {
    check(
        !value.is_empty()
            && value.len() <= 4096
            && !value.contains(['\\', ':', '\0'])
            && !value.chars().any(char::is_control)
            && value
                .split('/')
                .all(|part| !matches!(part, "" | "." | "..")),
        "catalog_relative_path",
    )
}
fn host_path(site: &Path, name: &str) -> Result<PathBuf> {
    relative(name)?;
    Ok(name
        .split('/')
        .fold(site.to_path_buf(), |path, part| path.join(part)))
}
fn child_names(path: &Path) -> Result<BTreeSet<String>> {
    let meta = std::fs::symlink_metadata(path).map_err(|_| "hook_overlay_directory")?;
    check(
        meta.is_dir()
            && !meta.file_type().is_symlink()
            && path.canonicalize().map_err(|_| "hook_overlay_directory_canonical")? == path,
        "hook_overlay_directory_type",
    )?;
    let mut names = BTreeSet::new();
    for entry in std::fs::read_dir(path).map_err(|_| "hook_overlay_read_dir")? {
        let entry = entry.map_err(|_| "hook_overlay_directory_entry")?;
        let child = entry.path();
        let child_meta = std::fs::symlink_metadata(&child).map_err(|_| "hook_overlay_child_meta")?;
        check(
            !child_meta.file_type().is_symlink()
                && (child.is_file() || child.is_dir())
                && child.canonicalize().map_err(|_| "hook_overlay_child_canonical")? == child,
            "hook_overlay_child_type",
        )?;
        names.insert(
            entry
                .file_name()
                .into_string()
                .map_err(|_| "hook_overlay_child_utf8")?,
        );
    }
    Ok(names)
}
pub fn load(root:&Path,record:&mut Value,dropin:Arc<[u8]>)->Result<SourceFs>{
    let manifest = root.join("target/wp-bootstrap-probe/source-manifest.json");
    let site = root.join("target/wp-bootstrap-probe/site");
    check(
        site.canonicalize().map_err(|_| "catalog_site")? == site,
        "catalog_site_alias",
    )?;
    let wire = fixed(&manifest, 2 * 1024 * 1024)?;
    record["source_manifest_sha256"] = json!(sha(&wire));
    let value: Value = serde_json::from_slice(&wire).map_err(|_| "catalog_manifest_json")?;
    check(
        value["schema"] == "recastpress.wp-bootstrap-source-manifest.v0.5"
            && value["wordpress_version"] == "7.1.3"
            && value["archive"]["bytes"] == 35368461_u64
            && value["archive"]["sha256"]
                == "d2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5"
            && value["file_count"] == 3782
            && value["directory_count"] == 467
            && value["expanded_bytes"] == 110077999_u64
            && value["core_theme_plugin_bytes_modified"] == false
            && value["wp_config_created"] == false,
        "catalog_manifest_profile",
    )?;
    let source_files = value["files"].as_object().ok_or("catalog_files_object")?;
    let source_dirs = value["directories"]
        .as_array()
        .ok_or("catalog_directory_array")?;
    check(
        source_files.len() == 3782 && source_dirs.len() == 467,
        "catalog_manifest_counts",
    )?;
    let mut expected_files = BTreeMap::new();
    let mut dirs = BTreeSet::new();
    for item in source_dirs {
        let name = item.as_str().ok_or("catalog_directory_string")?;
        relative(name)?;
        check(dirs.insert(name.to_owned()), "catalog_directory_duplicate")?;
        let path = host_path(&site, name)?;
        let meta = std::fs::symlink_metadata(&path).map_err(|_| "catalog_host_directory")?;
        check(
            meta.is_dir()
                && !meta.file_type().is_symlink()
                && path
                    .canonicalize()
                    .map_err(|_| "catalog_host_dir_canonical")?
                    == path,
            "catalog_host_dir_alias",
        )?;
    }
    let dir_list: Vec<_> = dirs.iter().cloned().collect();
    check(
        sha(&serde_json::to_vec(&dir_list).map_err(|_| "catalog_directory_encoding")?) == DIR_PIN,
        "catalog_directory_pin",
    )?;
    for (name, row) in source_files {
        relative(name)?;
        let fields = row.as_object().ok_or("catalog_file_identity")?;
        check(
            fields.len() == 2 && fields.contains_key("bytes") && fields.contains_key("sha256"),
            "catalog_file_identity_fields",
        )?;
        let size = row["bytes"].as_u64().ok_or("catalog_file_bytes")?;
        let digest = row["sha256"].as_str().ok_or("catalog_file_sha")?;
        check(
            size <= MAX_FILE
                && digest.len() == 64
                && digest
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "catalog_file_budget_or_hash",
        )?;
        expected_files.insert(name.clone(), json!({"bytes":size,"sha256":digest}));
    }
    check(
        sha(&serde_json::to_vec(&expected_files).map_err(|_| "catalog_files_encoding")?)
            == FILE_PIN,
        "catalog_all_files_pin",
    )?;
    let mut files = BTreeMap::new();
    let mut total = 0u64;
    for (name, row) in &expected_files {
        let path = host_path(&site, name)?;
        let data = fixed(&path, MAX_FILE)?;
        check(
            row["bytes"].as_u64() == Some(data.len() as u64) && row["sha256"] == sha(&data),
            "catalog_source_body_identity",
        )?;
        total = total
            .checked_add(data.len() as u64)
            .ok_or("catalog_total_overflow")?;
        check(total <= 256 * 1024 * 1024, "catalog_total_quota")?;
        files.insert(name.clone(), Arc::<[u8]>::from(data));
    }
    let mut retained_files=files.clone();
    let fs = SourceFs::new(files, dirs.clone()).map_err(|_| "catalog_constructor_rejected")?;
    check(
        fs.file_count() == 3782 && fs.directory_count() == 467 && fs.total_bytes() == 110077999,
        "catalog_actual_tree_counts",
    )?;
    for (name, row) in &expected_files {
        let path = PathBuf::from(format!("/{name}"));
        let metadata = fs
            .metadata(&path)
            .map_err(|_| "catalog_virtual_file_metadata")?;
        check(
            metadata.is_file()
                && !metadata.ft.symlink
                && Some(metadata.len) == row["bytes"].as_u64()
                && fs
                    .symlink_metadata(&path)
                    .map_err(|_| "catalog_virtual_symlink_metadata")?
                    == metadata,
            "catalog_virtual_file_identity",
        )?;
    }
    for dir in std::iter::once("").chain(dirs.iter().map(String::as_str)) {
        let guest = PathBuf::from(format!("/{dir}"));
        check(
            fs.metadata(&guest)
                .map_err(|_| "catalog_virtual_dir_metadata")?
                .is_dir(),
            "catalog_virtual_dir_type",
        )?;
        let mut expected = BTreeSet::new();
        for name in expected_files.keys().chain(dirs.iter()) {
            if name.rsplit_once('/').map_or("", |(parent, _)| parent) == dir {
                expected.insert(format!("/{name}"));
            }
        }
        let mut observed = BTreeSet::new();
        for entry in fs
            .read_dir(&guest)
            .map_err(|_| "catalog_virtual_read_dir")?
        {
            let entry = entry.map_err(|_| "catalog_virtual_directory_entry")?;
            let name = entry
                .path
                .to_str()
                .ok_or("catalog_virtual_directory_utf8")?
                .to_owned();
            check(
                observed.insert(name) && entry.metadata.is_ok(),
                "catalog_virtual_directory_duplicate",
            )?;
        }
        check(observed == expected, "catalog_virtual_direct_children")?;
        let host = if dir.is_empty() {
            site.clone()
        } else {
            host_path(&site, dir)?
        };
        let mut host_children = BTreeSet::new();
        for entry in std::fs::read_dir(host).map_err(|_| "catalog_host_read_dir")? {
            let entry = entry.map_err(|_| "catalog_host_directory_entry")?;
            let base = entry
                .file_name()
                .into_string()
                .map_err(|_| "catalog_host_directory_utf8")?;
            host_children.insert(if dir.is_empty() {
                format!("/{base}")
            } else {
                format!("/{dir}/{base}")
            });
        }
        check(
            host_children == expected,
            "catalog_host_missing_or_extra_child",
        )?;
    }
    let overlay_root = root.join("experiments/php-wasix-wp-hook-dispatch-probe/evidence/overlay-v5");
    check(
        child_names(&overlay_root)? == BTreeSet::from(["overlay.json".to_owned(), "wp-includes".to_owned()])
            && child_names(&overlay_root.join("wp-includes"))?
                == BTreeSet::from(["class-wp-hook.php".to_owned(), "plugin.php".to_owned()]),
        "hook_overlay_closed_file_set",
    )?;
    let receipt = fixed(&root.join(HOOK_OVERLAY_RECEIPT), 64 * 1024)?;
    check(sha(&receipt) == HOOK_OVERLAY_RECEIPT_SHA, "hook_overlay_receipt_hash")?;
    let overlay_meta: Value = serde_json::from_slice(&receipt).map_err(|_| "hook_overlay_receipt_json")?;
    check(
        overlay_meta["schema"] == "recastpress.wp-hook-dispatch-overlay.v5"
            && overlay_meta["wordpress_version"] == "7.1.3"
            && overlay_meta["wp_hook_method_has_sync_handoff"] == true
            && overlay_meta["product_ready"] == false,
        "hook_overlay_receipt_profile",
    )?;
    for (guest, source, digest) in HOOK_OVERLAY_FILES {
        let original = expected_files.get(guest).ok_or("hook_overlay_original_missing")?;
        check(
            original["sha256"] == overlay_meta["source_sha256"][guest]
                && overlay_meta["overlay_sha256"][guest] == digest,
            "hook_overlay_source_identity",
        )?;
        let bytes = fixed(&root.join(source), 64 * 1024)?;
        check(sha(&bytes) == digest, "hook_overlay_output_hash")?;
        let previous = retained_files
            .insert(guest.to_owned(), Arc::<[u8]>::from(bytes.clone()))
            .ok_or("hook_overlay_target_missing")?;
        check(
            sha(&previous) == original["sha256"].as_str().unwrap_or_default(),
            "hook_overlay_preimage_changed",
        )?;
        record["hook_overlay"][guest] = json!({
            "source_sha256": original["sha256"],
            "overlay_sha256": digest,
            "overlay_bytes": bytes.len(),
            "receipt_sha256": HOOK_OVERLAY_RECEIPT_SHA,
        });
    }
    record["hook_overlay_receipt_path"] = json!(HOOK_OVERLAY_RECEIPT);
    record["hook_overlay_receipt_sha256"] = json!(HOOK_OVERLAY_RECEIPT_SHA);
    let mut files=retained_files;
    check(files.insert("wp-content/db.php".into(),dropin).is_none(),"owned_dropin_collision")?;
    let overlay=SourceFs::new(files,dirs).map_err(|_|"owned_dropin_tree_rejected")?;
    check(overlay.file_count()==3783 && overlay.directory_count()==467,"owned_dropin_tree_counts")?;
    Ok(overlay)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_original_catalog_then_mounts_two_pinned_hook_files_and_owned_dropin() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .unwrap();
        let mut record = json!({});
        let dropin = Arc::<[u8]>::from(include_bytes!("../fixtures/db.php").as_slice());
        let fs = load(&root, &mut record, dropin).unwrap();

        assert_eq!(fs.file_count(), 3783);
        assert_eq!(fs.directory_count(), 467);
        assert_eq!(record["hook_overlay_receipt_sha256"], HOOK_OVERLAY_RECEIPT_SHA);
        assert_eq!(
            record["hook_overlay"]["wp-includes/plugin.php"]["overlay_sha256"],
            "db8dc3d3c61a43ff225e01881187f47ce8eedfb1a65a56c9cd60bf13aea04d5c"
        );
        assert_eq!(
            record["hook_overlay"]["wp-includes/class-wp-hook.php"]["overlay_sha256"],
            "76eacf182c0e07c4285be0eff1df5bfd5cf4ce2a08c748a1085b241ffff473e4"
        );
        assert_eq!(
            fs.metadata(Path::new("/wp-includes/plugin.php")).unwrap().len,
            37171
        );
        assert_eq!(
            fs.metadata(Path::new("/wp-includes/class-wp-hook.php"))
                .unwrap()
                .len,
            20078
        );
        assert!(
            fs.metadata(Path::new("/wp-content/db.php"))
                .unwrap()
                .is_file()
        );
    }
}
