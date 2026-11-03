"""Read-only exact derivations; no existing experiment is modified."""
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
IMPORTS = b'''//! Exact derived fixed-catalog loader; see derivation_gate.py.
use recastpress_wp_source_fs_probe::SourceFs;
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
use std::{collections::{BTreeMap,BTreeSet},fs::File,io::Read,path::{Path,PathBuf},sync::Arc};
use wasmer_wasix::virtual_fs::FileSystem;
'''


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(wire):
    return hashlib.sha256(wire).hexdigest()


def inputs():
    return [ROOT / 'experiments/wp-source-fs-probe/src/bin/catalog.rs',
        ROOT / 'experiments/wpdb-result-budget-probe/src/lib.rs',
        ROOT / 'experiments/php-wasix-wpdb-probe/src/rpc_fs.rs',
        ROOT / 'experiments/php-wasix-wpdb-probe/src/capture.rs',
        ROOT / 'experiments/php-wasix-host-probe/src/managed_access_fs.rs']


def verify():
    original = inputs()[0].read_bytes()
    require(sha(original) == '4f020609956f5a33802c911496592206c4b3a8cb2b1f95cfea929cdd1a0faad9',
        'bootstrap_original_catalog_changed')
    end = original.index(b'fn probe(')
    expected = IMPORTS + original[original.index(b'type Result<T>'):end] \
        + b'pub fn load(root:&Path,record:&mut Value)->Result<SourceFs>{\n' \
        + original[original.index(b'    let manifest = root.join(', end):
            original.index(b'    let runtime = tokio::runtime::Builder', end)] + b'    Ok(fs)\n}\n'
    expected = expected.replace(b'pub fn load(root:&Path,record:&mut Value)->Result<SourceFs>{',
        b'pub fn load(root:&Path,record:&mut Value,dropin:Arc<[u8]>)->Result<SourceFs>{')
    expected = expected.replace(b'    let fs = SourceFs::new(files, dirs.clone())',
        b'    let retained_files=files.clone();\n    let fs = SourceFs::new(files, dirs.clone())')
    expected = expected.replace(b'    Ok(fs)\n}', b'''    let mut files=retained_files;
    check(files.insert("wp-content/db.php".into(),dropin).is_none(),"owned_dropin_collision")?;
    let overlay=SourceFs::new(files,dirs).map_err(|_|"owned_dropin_tree_rejected")?;
    check(overlay.file_count()==3783 && overlay.directory_count()==467,"owned_dropin_tree_counts")?;
    Ok(overlay)
}''')
    require(expected == (HERE / 'src/catalog.rs').read_bytes(), 'bootstrap_catalog_exact_derivation')
    budget = inputs()[1].read_bytes()
    require(budget.count(b'columns: 16,') == 1, 'bootstrap_original_column_budget')
    require(budget.replace(b'columns: 16,', b'columns: 32,') == (HERE / 'src/result_budget.rs').read_bytes(),
        'bootstrap_result_budget_exact_derivation')
    for old in inputs()[2:4]:
        require(old.read_bytes() == (HERE / 'src' / old.name).read_bytes(), 'bootstrap_async_copy_exact_derivation')
    old_access = inputs()[4].read_bytes().split(b'#[cfg(test)]', 1)[0]
    access = (HERE / 'src/access_fs.rs').read_bytes().split(b'#[cfg(test)]', 1)[0]
    expected_access = old_access.replace(b'use super::runtime_fixture_fs::{ScratchFs, WpSourceFs};',
        b'use recastpress_wp_source_fs_probe::SourceFs;').replace(
        b'pub(crate) const MAX_OPERATIONS: usize = 64;', b'pub(crate) const MAX_OPERATIONS: usize = 16384;')
    start, end = b'#[derive(Clone, Debug)]\nstruct Policy', b'#[derive(Debug)]\nstruct State'
    new_policy = access[access.index(start):access.index(end)]
    expected_access = expected_access[:expected_access.index(start)] + new_policy \
        + expected_access[expected_access.index(end):]
    start, end = b'    pub(crate) fn new(', b'        Ok(Self(Arc::new(Mutex::new(State {'
    expected_access = expected_access[:expected_access.index(start)] \
        + access[access.index(start):access.index(end)] + expected_access[expected_access.index(end):]
    expected_access = expected_access.replace(b'''            policy: Policy {
                fixture_name,
                wp,
                scratch,
            },''', b'            policy: Policy {wp,php,context},')
    require(expected_access.rstrip() == access.rstrip(), 'bootstrap_access_transaction_body_changed')
    return {'original_catalog_sha256': sha(original), 'derived_catalog_sha256': sha(expected),
        'original_budget_sha256': sha(budget), 'derived_budget_sha256': sha(budget.replace(b'columns: 16,', b'columns: 32,')),
        'changed_default_columns': [16, 32], 'other_collector_bytes_unchanged': True,
        'async_channel_and_capture_bytes_unchanged': True,
        'access_transaction_body_unchanged': True, 'access_max_queries':16384,
        'access_new_readonly_policy_sha256':sha(new_policy),
        'original_sources_modified': False}
