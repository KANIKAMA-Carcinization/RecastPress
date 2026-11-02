"""Prove the native comparison host differs only at fixed CRUD entry/gates.

The DB/TCP/session/query/escape/timeout/cleanup implementation stays byte exact.
This is an experimental derived copy, not another product implementation.
"""
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ORIGINAL = ROOT / 'experiments/wpdb-bridge-probe/src/main.rs'
ORIGINAL_SHA256 = 'f9d7c5007adc8bb0fc76eda77f0c9e47f57fb43a77041e9e2e47a39299281379'
COMPLETION = b'''            && report["php_result"]["bootstrap_num_queries"] == 0
            && report["php_result"]["final_num_queries"] == 25
            && report["php_result"]["observations"]
                .as_array()
                .is_some_and(|rows| rows.len() == 27),
'''
REPLACEMENTS = [
    (b'#![forbid(unsafe_code)]\n', b'#![forbid(unsafe_code)]\nmod completion;\n', 1),
    (b'experiments/wpdb-bridge-probe/fixtures/bridge.php', b'experiments/wpdb-crud-probe/fixtures/bridge.php', 3),
    (b'recastpress.wpdb-bridge-input.v0.5', b'recastpress.wpdb-crud-input.v0.5', 2),
    (b'recastpress.wpdb-bridge-observation.v0.5', b'recastpress.wpdb-crud-observation.v0.5', 2),
    (b'recastpress.wpdb-bridge-php-observation.v0.5', b'recastpress.wpdb-crud-php-observation.v0.5', 1),
    (b'fixed_shared_wpdb_cases_passed', b'fixed_crud_cases_passed', 1),
    (b'fixed_bridge_cases_passed', b'fixed_crud_bridge_cases_passed', 1),
    (b'fixed_bridge_cases_failed', b'fixed_crud_bridge_cases_failed', 2),
    (COMPLETION, b'            && completion::verify(&report["php_result"], &report["operations"]),\n', 1),
]


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(wire):
    return hashlib.sha256(wire).hexdigest()


def read(path, maximum=128 * 1024):
    require(path.resolve(strict=True) == path and path.is_file() and path.stat().st_size <= maximum,
        'derived_host_fixed_file')
    wire = path.read_bytes()
    require(len(wire) <= maximum, 'derived_host_file_quota')
    return wire


def expected():
    original = read(ORIGINAL)
    require(sha(original) == ORIGINAL_SHA256, 'original_bridge_host_changed')
    result = original
    for old, new, count in REPLACEMENTS:
        require(result.count(old) == count, 'derived_host_replacement_count')
        result = result.replace(old, new)
    require(b'\r' not in result, 'derived_host_requires_lf')
    return result


def verify():
    result = expected()
    actual = read(HERE / 'src/main.rs')
    require(actual == result, 'derived_host_changed_outside_fixed_replacements')
    return {'schema': 'recastpress.wpdb-crud-host-derivation.v0.5',
        'original_path': ORIGINAL.relative_to(ROOT).as_posix(), 'original_sha256': ORIGINAL_SHA256,
        'derived_path': 'experiments/wpdb-crud-probe/src/main.rs', 'derived_sha256': sha(actual),
        'replacement_groups': len(REPLACEMENTS), 'original_db_tcp_session_cleanup_bytes_preserved': True,
        'private_transport_input_schema_changed': False, 'product_implementation_selected': False}


if __name__ == '__main__':
    print(verify()['schema'] + ': exact fixed replacements verified')
