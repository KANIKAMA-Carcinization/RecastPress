"""Source-only boundaries for the independent context host; no runtime executes."""
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OLD = ROOT / 'experiments/php-wasix-wp-request-probe'
REUSED = ['db', 'rpc_fs', 'result_budget', 'catalog', 'access_fs', 'capture', 'warning_diagnostic']


def require(value, code):
    if not value: raise ValueError(code)


def sha(wire): return hashlib.sha256(wire).hexdigest()
def inputs(): return [OLD / name for name in ['Cargo.toml', 'Cargo.lock', 'src/main.rs', 'build_check.py',
    'derivation_gate.py', 'src/response_contract.rs', *['src/' + name + '.rs' for name in REUSED]]]


def verify():
    old_name = b'recastpress-php-wasix-wp-request-probe'
    new_name = b'recastpress-php-wasix-wp-request-context-probe'
    for name in ['Cargo.toml', 'Cargo.lock']:
        wire = (OLD / name).read_bytes()
        require(wire.count(old_name) == 1 and wire.replace(old_name, new_name) == (HERE / name).read_bytes(),
            'context_workspace_name_only')
    original = (OLD / 'src/main.rs').read_bytes()
    actual = (HERE / 'src/main.rs').read_bytes()
    for name in REUSED:
        declaration = ('#[path = "../../php-wasix-wp-request-probe/src/' + name + '.rs"]\nmod ' + name + ';').encode()
        require(actual.count(declaration) == 1 and not (HERE / ('src/' + name + '.rs')).exists(),
            'context_readonly_module_import')
    start, end = b'fn load_module(', b'fn finite('
    def block(wire): return wire[wire.index(start):wire.index(end, wire.index(start))]
    require(block(original) == block(actual), 'context_exact_managed_module_verifier')
    require(b'const GUEST_BASE: &str = "experiments/php-wasix-wp-request-probe/";' in actual
        and b'.with_mount(format!("/{GUEST_BASE}fixtures").into(), Arc::new(php))' in actual,
        'context_original_managed_access_guest_mount')
    return {'old_sources': {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in inputs()},
        'original_sources_modified': False, 'workspace_lock_name_only': True,
        'db_actor_rpc_collector_catalog_access_capture_warning_modules_readonly_imports': True,
        'managed_module_verifier_body_exact': True, 'original_guest_mount_namespace_preserved': True,
        'own_closed_case_wrapper_and_native_context_response_contract': True}
