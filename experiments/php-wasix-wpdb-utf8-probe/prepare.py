"""Stage thirteen finite UTF8 CGI sources; no native PHP, Wasm, DB or network."""
import argparse
import importlib.util
import json
from pathlib import Path
import stat

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/php-wasix-wpdb-utf8-probe'
DESTINATION = WORK / 'wp-core'
PREPARED = WORK / 'prepared.json'
FIXTURES = ['bridge.php', 'entry.php', 'corpus.php', 'adapter.php', 'helpers.php']
BASE = 'experiments/php-wasix-wpdb-utf8-probe/fixtures/'
PINS = {
    'experiments/wpdb-utf8-probe/prepare.py': '7b0227012c7b82a8094e96b5cf1adce8d05ebf2087f5fd52fa0bcde2f74cca46',
    'experiments/wpdb-utf8-probe/check.py': '42509030eda642e8ae0696279e575c6f6daf24fc85e55187d407dab9b6c9566e',
    'experiments/wpdb-utf8-probe/fixtures/entry.php': '4c3602b7ed1a4ffbababafd9ced741bf2866c7c7b3b384c896de3ab9199b82a1',
    'experiments/wpdb-utf8-probe/fixtures/native.php': '97b3e6e737328de0a4d6f129dfd73dddf04568689d47ddf755269570c03c56a8',
    'experiments/wpdb-utf8-probe/fixtures/corpus.php': 'a484470e73bf5c2be07d2ad90b510a7769e22adb5a22e7893045adededce9ee9',
    'experiments/php-wasix-wp-bootstrap-probe/fixtures/adapter.php': '55f7042bebb6abbdd73104e52688a913dc38f2ae2b59abba292f303200f91b1f',
    'experiments/php-wasix-wpdb-probe/prepare.py': '2d9b0e6ab95a13e2700bb69801a5ca19b6276dd6247eb4525cfb7a042a357eb3',
    'experiments/wpdb-bridge-probe/fixtures/helpers.php': '1220aa52f8a967f7d4981a1b668e07c7343cdff9c10caf42ea99c32a348fccb1',
}
ORACLES = {
    'mysql': {'path': 'experiments/wpdb-utf8-probe/evidence/windows-mysql-native.json', 'bytes': 140766,
        'sha256': 'c1d7a9e4d55c68b030830e1d8e883d52f42bae031df4bd3062f3bcf4d5370ada'},
    'mariadb': {'path': 'experiments/wpdb-utf8-probe/evidence/windows-mariadb-native.json', 'bytes': 138410,
        'sha256': '18c01e8105f7e41e407ebdd36f4e057233b3d808028d06b37168877e2ec2ba90'},
}
CGI_INPUT = b'''function rp_entry_input(string $variant): array {
    rp_check($variant === 'bridge' && PHP_SAPI === 'cgi-fcgi'
        && ($_SERVER['argc'] ?? 0) === 0 && ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET'
        && ($_SERVER['QUERY_STRING'] ?? '') === 'rp_request=1'
        && ($_SERVER['REQUEST_URI'] ?? '') === '/__rp_utf8.php?rp_request=1'
        && ($_SERVER['CONTENT_LENGTH'] ?? '') === '0'
        && !isset($_SERVER['CONTENT_TYPE']), 'utf8_closed_cgi_entry');
    $stream = fopen('/rp-utf8/context.json', 'rb');
    rp_check(is_resource($stream), 'utf8_context_open');
    try { $wire = stream_get_contents($stream, 4097); } finally { fclose($stream); }
    rp_check(is_string($wire) && strlen($wire) <= 4096, 'utf8_context_quota');
    $value = json_decode($wire, true, 8, JSON_THROW_ON_ERROR);
    rp_check(is_array($value), 'utf8_context_object');
    rp_exact_keys($value, ['schema', 'nonce'], 'utf8_context_exact_keys');
    rp_check($value['schema'] === 'recastpress.php-wasix-wpdb-utf8-context.v0.5'
        && is_string($value['nonce']) && preg_match('/\\A[0-9a-f]{48}\\z/D', $value['nonce']) === 1,
        'utf8_context_profile');
    rp_check(json_encode($value, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) === trim($wire, " \\t\\r\\n"),
        'utf8_context_duplicate_or_noncanonical');
    return ['nonce' => $value['nonce']];
}

'''


def require(value, code):
    if not value: raise ValueError(code)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def sha(wire):
    import hashlib
    return hashlib.sha256(wire).hexdigest()


def identity(wire): return {'bytes': len(wire), 'sha256': sha(wire)}
def canonical(value): return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()


def read(path, maximum=2*1024*1024):
    require(path.resolve(strict=True) == path and path.is_file() and 0 < path.stat().st_size <= maximum, 'fixed_utf8_source')
    wire = path.read_bytes(); require(len(wire) <= maximum, 'fixed_utf8_source_quota'); return wire


def parents():
    result = {}
    for name, expected in PINS.items():
        wire = read(ROOT / name); require(sha(wire) == expected, 'frozen_utf8_parent_changed'); result[name] = wire
    return result


def once(wire, before, after):
    require(wire.count(before) == 1, 'exact_utf8_derivation'); return wire.replace(before, after)


def section(wire, start, end):
    require(wire.count(start) == wire.count(end) == 1, 'utf8_derivation_markers')
    first, last = wire.index(start), wire.index(end); require(first < last, 'utf8_derivation_order'); return wire[first:last]


def expected_fixtures(original):
    helper = original['experiments/wpdb-bridge-probe/fixtures/helpers.php']
    helper = once(helper, section(helper, b'function rp_entry_input(', b'function rp_load_wordpress('), CGI_INPUT)
    entry = original['experiments/wpdb-utf8-probe/fixtures/entry.php']
    entry = once(entry, b"require __DIR__ . '/../../wpdb-bridge-probe/fixtures/helpers.php';", b"require __DIR__ . '/helpers.php';")
    include = b'''function rp_crud_included_sources(string $variant): array {
    rp_check($variant === 'bridge', 'utf8_bridge_include_variant');
    $rows = [];
    foreach (['bridge.php', 'entry.php', 'corpus.php', 'adapter.php', 'helpers.php'] as $name) {
        $rows['experiments/php-wasix-wpdb-utf8-probe/fixtures/' . $name] = __DIR__ . '/' . $name;
    }
    foreach (rp_crud_wp_files() as $name) {
        $rows['.reference/wordpress-7.1.3/wp-includes/' . $name] = ABSPATH . WPINC . '/' . $name;
    }
    $expected = array_map('realpath', array_values($rows));
    $actual = array_map('realpath', get_included_files());
    rp_check(!in_array(false, $expected, true), 'crud_include_missing');
    sort($expected, SORT_STRING); sort($actual, SORT_STRING);
    rp_check($actual === $expected, 'crud_include_set_mismatch');
    $names = array_keys($rows); sort($names, SORT_STRING); return $names;
}

'''
    entry = once(entry, section(entry, b'function rp_crud_included_sources(', b'function rp_crud_run('), include)
    entry = once(entry, b'recastpress.wpdb-utf8-php-observation.v0.5', b'recastpress.php-wasix-wpdb-utf8-php-observation.v0.5')
    entry = once(entry, b"rp_check($variant === 'native', 'utf8_native_only');", b"rp_check($variant === 'bridge', 'utf8_bridge_only');")
    entry = once(entry, b"'wasm_executed' => false,", b"'wasm_executed' => true,")
    entry = once(entry, b"rp_check($report['native_driver_extension_present'] === ($variant === 'native'), 'crud_native_extension_profile');",
        b"rp_check($variant === 'bridge' && $report['native_driver_extension_present'] === true, 'utf8_wasix_builtin_mysqli');")
    entry = once(entry, b"require __DIR__ . '/../../wpdb-bridge-probe/fixtures/adapter.php';", b"require __DIR__ . '/adapter.php';")
    return {'bridge.php': b"<?php\ndeclare(strict_types=1);\nheader('Content-Type: application/json', true, 200);\nrequire __DIR__ . '/entry.php';\nrp_crud_run('bridge');\n",
        'entry.php': entry, 'corpus.php': original['experiments/wpdb-utf8-probe/fixtures/corpus.php'],
        'adapter.php': original['experiments/php-wasix-wp-bootstrap-probe/fixtures/adapter.php'], 'helpers.php': helper}


def modules():
    parents()
    return (load('rp_utf8_native_source_gate', ROOT / 'experiments/wpdb-utf8-probe/prepare.py'),
        load('rp_utf8_eight_source_provider', ROOT / 'experiments/php-wasix-wpdb-probe/prepare.py'))


def derivation_gate():
    original = parents(); expected = expected_fixtures(original)
    require({p.name for p in (HERE / 'fixtures').iterdir()} == set(FIXTURES), 'closed_utf8_fixture_set')
    for name, wire in expected.items():
        require(b'\r' not in wire and read(HERE / 'fixtures' / name) == wire, 'derived_utf8_fixture_bytes')
    return {'parents': {name: identity(wire) for name, wire in original.items()},
        'fixtures': {name: identity(wire) for name, wire in expected.items()},
        'native_corpus_bytes_unchanged': True, 'full_wp_adapter4096_bytes_unchanged': True,
        'original_wpdb_business_and_typed_observer_bytes_unchanged': True,
        'allowed_changes': ['closed_CGI_bridge_entry_and_JSON_header', 'immutable_canonical_nonce_context_reader',
            'owned_include_labels_and_guard', 'wasm_schema_runtime_extension_and_execution_flags'],
        'original_git_objects_verified_by_preparer': False}


def input_hashes():
    native, _ = modules(); result = native.prepare(record=False)['input_files']
    for name in list(PINS) + [pin['path'] for pin in ORACLES.values()]: result[name] = sha(read(ROOT / name))
    for path in [Path(__file__).resolve(), *[HERE / 'fixtures' / name for name in FIXTURES]]:
        result[path.relative_to(ROOT).as_posix()] = sha(read(path))
    return dict(sorted(result.items()))


def owned(path):
    require(WORK.resolve() == WORK and path.resolve() == path and path.is_relative_to(WORK), 'owned_utf8_metadata_path'); return path


def metadata():
    before = input_hashes(); derivation = derivation_gate(); _, provider = modules()
    selected, archive = provider.archive_sources()
    require(owned(DESTINATION).is_dir() and {p.name for p in DESTINATION.iterdir()} == set(provider.WP_PINS), 'utf8_eight_staged_sources')
    files = []; wp = {}
    for name in FIXTURES:
        path = HERE / 'fixtures' / name
        files.append({'source': path.relative_to(ROOT).as_posix(), 'guest': '/' + BASE + name,
            **identity(read(path)), 'guest_read_only': True, 'kind': 'derived_fixture'})
    for name, wire in selected.items():
        path = owned(DESTINATION / name)
        require(read(path, 512*1024) == wire and stat.S_IMODE(path.stat().st_mode) & 0o222 == 0, 'utf8_readonly_original_wp_source')
        wp[name] = {'path': path.relative_to(ROOT).as_posix(), **identity(wire)}
        files.append({'source': wp[name]['path'], 'guest': '/.reference/wordpress-7.1.3/wp-includes/' + name,
            **identity(wire), 'guest_read_only': True, 'kind': 'wordpress_source'})
    require(len(files) == len({row['guest'] for row in files}) == 13, 'thirteen_finite_utf8_files')
    validator = load('rp_utf8_frozen_native_validator', ROOT / 'experiments/wpdb-utf8-probe/check.py')
    native_values = {}
    for backend, pin in ORACLES.items():
        wire = read(ROOT / pin['path']); require(identity(wire) == {k: pin[k] for k in ['bytes', 'sha256']}, 'utf8_native_oracle_identity')
        value = json.loads(wire); require(value['passed'] is True and value['normal_admin_shutdown_verified'] is True
            and value['source_reverified_after'] is True and value['all_command_children_reaped'] is True, 'utf8_native_oracle_passed')
        validator.validate(value['observation'], backend, value['native_reference_before'])
        native_values[backend] = value['observation']
    for key in ['observations', 'query_trace_hex', 'loaded_source_sha256', 'utf8_input_hex', 'bootstrap_num_queries', 'final_num_queries', 'final_row_count']:
        require(canonical(native_values['mysql'][key]) == canonical(native_values['mariadb'][key]), 'utf8_native_backend_public_core_differs')
    result = {'schema': 'recastpress.php-wasix-wpdb-utf8-prepared.v0.5', 'outcome': 'fixed_thirteen_utf8_guest_sources_prepared',
        'input_files': before, 'derivation': derivation, 'source_archive': archive, 'files': files,
        'wordpress_files': wp, 'wordpress_version': '7.1.3',
        'wordpress_source_sha256': {'wp-includes/' + name: row['sha256'] for name, row in wp.items()},
        'expected_include_names': sorted([BASE + name for name in FIXTURES]
            + ['.reference/wordpress-7.1.3/wp-includes/' + name for name in wp]),
        'native_oracles': ORACLES, 'native_api_cases': 31, 'native_public_queries': 22,
        'private_context_guest_path': '/rp-utf8/context.json',
        'private_context_keys': ['schema', 'nonce'], 'guest_context_contains_db_credentials_or_salts': False,
        'full_wordpress_bootstrap': False, 'wordpress_executed': False, 'wasm_executed_by_preparer': False,
        'native_php_executed_by_preparer': False, 'native_reference_binaries_reverified': False,
        'network_used_by_preparer': False, 'guest_network_enabled': False, 'product_runtime_selected': False,
        'guest_readonly_enforcement_executed': False, 'inputs_unchanged': True}
    require(input_hashes() == before, 'utf8_sources_changed_during_preparation'); return result


def verify_prepared():
    value = metadata(); require(canonical(json.loads(read(owned(PREPARED)))) == canonical(value), 'utf8_prepared_changed'); return value


def prepare():
    before = input_hashes(); derivation_gate(); _, provider = modules(); selected, _ = provider.archive_sources()
    if not owned(DESTINATION).exists():
        DESTINATION.mkdir(parents=True, exist_ok=False)
        for name, wire in selected.items():
            path = owned(DESTINATION / name)
            with path.open('xb') as output: output.write(wire)
            path.chmod(0o444)
    value = metadata(); require(value['input_files'] == before, 'utf8_prepare_input_binding')
    wire = (json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    if owned(PREPARED).exists():
        previous = read(PREPARED)
        if previous != wire:
            backup = owned(WORK / ('prepared-before-' + sha(previous)[:16] + '.json'))
            if backup.exists(): require(read(backup) == previous, 'utf8_previous_prepared_changed')
            else:
                with backup.open('xb') as output: output.write(previous)
    PREPARED.write_bytes(wire); require(verify_prepared() == value, 'utf8_prepared_after_verify'); return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--verify-only', action='store_true'); args = parser.parse_args()
    result = verify_prepared() if args.verify_only else prepare(); print(result['outcome'])
