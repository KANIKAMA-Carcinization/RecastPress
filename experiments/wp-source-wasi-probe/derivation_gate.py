"""Read-only exact adapter around the previous complete-catalog loader."""
import hashlib
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ORIGINAL = ROOT / 'experiments/wp-source-fs-probe/src/bin/catalog.rs'
IMPORTS = b'''//! Exact derived fixed-catalog loader; see derivation_gate.py.
use recastpress_wp_source_fs_probe::SourceFs;
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
use std::{collections::{BTreeMap,BTreeSet},fs::File,io::Read,path::{Path,PathBuf},sync::Arc};
use wasmer_wasix::virtual_fs::FileSystem;
'''


def require(value, reason):
    if not value: raise ValueError(reason)


def verify():
    wire = ORIGINAL.read_bytes()
    require(hashlib.sha256(wire).hexdigest() == '4f020609956f5a33802c911496592206c4b3a8cb2b1f95cfea929cdd1a0faad9',
        'source_wasi_original_loader_changed')
    # The complete source SHA is bound by checker inputs and the frozen prior
    # commit; extraction markers forbid unrelated changes to the new loader.
    start = wire.index(b'type Result<T>')
    end = wire.index(b'fn probe(')
    body_start = wire.index(b'    let manifest = root.join(', end)
    body_end = wire.index(b'    let runtime = tokio::runtime::Builder', end)
    expected = IMPORTS + wire[start:end] + b'pub fn load(root:&Path,record:&mut Value)->Result<SourceFs>{\n' \
        + wire[body_start:body_end] + b'    Ok(fs)\n}\n'
    actual = (HERE / 'src/catalog.rs').read_bytes()
    require(actual == expected, 'source_wasi_catalog_loader_derivation')
    return {'original_source_sha256': hashlib.sha256(wire).hexdigest(),
        'derived_source_sha256': hashlib.sha256(actual).hexdigest(),
        'complete_archive_pin_body_metadata_directory_checks_preserved': True,
        'original_file_modified': False}
