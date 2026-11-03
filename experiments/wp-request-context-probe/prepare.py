"""Prepare a clean native request fixture; never starts PHP, a DB or network."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/wp-request-context-probe'
PINS = {
    'experiments/wp-request-probe/prepare.py': 'e9fa925c132b64205e05b046bec552eb0a7b267dfd58ec6d0f3d5eca1f25cd43',
    'experiments/wp-request-probe/database_check.py': '458634885106e8dda07e18cff40d23f93a2bd18e39971eb1994971688e550e02',
    'experiments/wp-request-probe/owned_database.py': 'edb193c26af598dbf62564107123e3ce6e0aa7083c22141da847c9b79aa5321d',
    'experiments/wp-request-probe/fixtures/bootstrap.php': '5fcc60a8f63c3ca33d0143c71c1911d314c415a281f170b3644f3cf2abb37732',
    'experiments/wp-request-probe/fixtures/home.php': '6627b1d91bb34724347fc7eae1df10d1b52f7096639eb2273d53641f90093d4b',
    'experiments/wp-bootstrap-probe/prepare.py': 'df009b467c1c2452c746ff8f4e6f5fa3a71c04795d32efe77e186b2ee8d776bc',
    'experiments/wp-bootstrap-probe/database_check.py': 'dabc6ddd31ec35cb675f75c0aea27e4d742c4d51e4db09f461e2cfbfb5920a2a',
    'experiments/wp-bootstrap-probe/owned_database.py': 'c398cef60c9233acb1012c96691d6dc9c718a1eac71d9855a47ce0c7a1c7da42',
    'experiments/php-cgi-startup-probe/check.py': 'f22b503c38feab1e11ceee2c6833350f529f3fe1c59c3f59fb6a0b84da9f96f4',
    'experiments/wp-bootstrap-probe/evidence/windows-mysql-native.json': '61c331e5e9f316dd6e2f241a54f1de337bbff397d405c6a5f4ae55cc49dc2c91',
    'experiments/wp-bootstrap-probe/evidence/windows-mariadb-native.json': 'af07c9ff931f87b2f8d235423fcf5db0101ffc1402904b899c6294d9c6f35f2f',
}
FIXTURES = ['bootstrap.php', 'context.php', 'home.php', 'missing.php']
CASES = {'home': {'uri': '/', 'query': '', 'status': 200, 'get': []},
    'missing': {'uri': '/?p=2147483647', 'query': 'p=2147483647', 'status': 404, 'get': {'p': '2147483647'}}}


def require(value, code):
    if not value: raise ValueError(code)


def sha(wire): return hashlib.sha256(wire).hexdigest()
def identity(wire): return {'bytes': len(wire), 'sha256': sha(wire)}
def canonical(value): return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def read(path, maximum=2*1024*1024):
    require(path.resolve(strict=True) == path and path.is_file() and 0 < path.stat().st_size <= maximum, 'fixed_context_source')
    wire = path.read_bytes(); require(len(wire) <= maximum, 'context_source_quota'); return wire


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def parents():
    result = {}
    for name, expected in PINS.items():
        wire = read(ROOT / name); require(sha(wire) == expected, 'frozen_context_parent_changed'); result[name] = wire
    return result


def once(wire, before, after):
    require(wire.count(before) == 1, 'exact_context_derivation'); return wire.replace(before, after)


def expected_fixtures():
    original = parents(); b = original['experiments/wp-request-probe/fixtures/bootstrap.php']
    b = once(b, b'declare(strict_types=1);\n', b"declare(strict_types=1);\nrequire __DIR__ . '/context.php';\n")
    before = b"    $rp_stream = fopen('php://stdin', 'rb');\n    rp_boot_check(is_resource($rp_stream), 'private_stdin_open');\n    try { $wire = stream_get_contents($rp_stream, 16385); } finally { fclose($rp_stream); }\n    rp_boot_check(is_string($wire) && strlen($wire) <= 16384, 'input_size');\n"
    b = once(b, before, b'    [$wire, $context] = rp_context_read($mode);\n')
    b = once(b, b'    return $value;\n', b"    $value['repository_root'] = $context['repository_root'];\n    return $value;\n")
    b = b.replace(b'recastpress.wp-request-native-observation.v0.5', b'recastpress.wp-request-context-native-observation.v0.5')
    b = b.replace(b'fixed_native_home_passed', b'fixed_native_context_passed').replace(b'fixed_native_home_failed', b'fixed_native_context_failed')
    b = once(b, b"'mode' => $rp_mode,", b"'mode' => $rp_mode, 'case' => RP_REQUEST_CASE,")
    b = once(b, b"    header_remove(); http_response_code(200); header('Content-Type: application/json');",
        b"    header_remove(); header('Content-Type: application/json', true, 200);")
    b = once(b, b"&& ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET' && ($_SERVER['REQUEST_URI'] ?? '') === '/'\n        && ($_SERVER['QUERY_STRING'] ?? '') === '', 'closed_native_entry');",
        b"&& ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET'\n        && ($_SERVER['REQUEST_URI'] ?? '') === '/__rp_context.php?rp_request=1'\n        && ($_SERVER['QUERY_STRING'] ?? '') === 'rp_request=1'\n        && ($_SERVER['CONTENT_LENGTH'] ?? '') === '0' && !isset($_SERVER['CONTENT_TYPE'])\n        && !isset($_SERVER['HTTP_ACCEPT']), 'closed_native_entry');")
    b = once(b, b"    $rp_root = realpath(__DIR__ . '/../../..');", b"    $rp_root = realpath($rp_input['repository_root']);")
    first = b.index(b'    $_GET = []; $_POST = []; $_COOKIE = [];\n')
    last = b.index(b'    // No WPINC definition here:')
    require(first < last, 'context_public_reset_markers')
    b = b[:first] + b"    $table_prefix = 'rp_';\n    rp_context_public();\n    $rp_report['context_before_bootstrap'] = rp_context_snapshot('before_bootstrap');\n" + b[last:]
    marker = b"        && $rp_report['native_mysqli_handle'], 'full_normal_bootstrap_required');\n"
    b = once(b, marker, marker + b"    $rp_report['context_after_bootstrap'] = rp_context_snapshot('after_bootstrap');\n")
    marker = b"    $rp_report['request_dispatch_completed'] = did_action('wp') >= 1;\n"
    b = once(b, marker, marker + b"    $rp_report['is_home'] = is_home(); $rp_report['is_404'] = is_404();\n"
        b"    rp_boot_check($rp_report['is_home'] === (RP_REQUEST_CASE === 'home')\n"
        b"        && $rp_report['is_404'] === (RP_REQUEST_CASE === 'missing'), 'fixed_public_route');\n")
    marker = b"    $rp_report['template_loader_completed'] = true;\n"
    b = once(b, marker, marker + b"    $rp_report['context_after_dispatch'] = rp_context_snapshot('after_dispatch');\n")
    b = once(b, b'is_int($rp_status) && $rp_status === 200 && !headers_sent()',
        b"is_int($rp_status) && $rp_status === (RP_REQUEST_CASE === 'home' ? 200 : 404) && !headers_sent()")
    marker = b"    $rp_report['core_files_unchanged'] = true;\n"
    b = once(b, marker, marker + b'    rp_context_verify();\n')
    wrappers = {}
    for case in CASES:
        wrappers[case + '.php'] = once(original['experiments/wp-request-probe/fixtures/home.php'],
            b"define('RP_BOOTSTRAP_MODE', 'home');\n", b"define('RP_BOOTSTRAP_MODE', 'home');\ndefine('RP_REQUEST_CASE', '" + case.encode('ascii') + b"');\n")
    require(b"    wp();\n" in b and b"    require ABSPATH . WPINC . '/template-loader.php';\n" in b, 'original_wp_dispatch_calls')
    return {'bootstrap.php': b, **wrappers}


def inputs():
    parents(); result = dict(PINS)
    old = load('rp_context_frozen_home_source_graph', ROOT / 'experiments/wp-request-probe/database_check.py')
    result.update(old.inputs())
    for path in [Path(__file__).resolve(), HERE / 'database_check.py', *[HERE / 'fixtures' / name for name in FIXTURES]]:
        result[path.relative_to(ROOT).as_posix()] = sha(read(path))
    return dict(sorted(result.items()))


def metadata():
    expected = expected_fixtures()
    require({p.name for p in (HERE / 'fixtures').iterdir()} == set(FIXTURES), 'finite_context_fixture_set')
    for name, wire in expected.items(): require(read(HERE / 'fixtures' / name) == wire and b'\r' not in wire, 'derived_context_fixture_bytes')
    require(b'\r' not in read(HERE / 'fixtures/context.php'), 'context_helper_lf')
    before = inputs()
    return {'schema': 'recastpress.wp-request-context-prepared.v0.5', 'input_files': before,
        'fixtures': {name: identity(read(HERE / 'fixtures' / name)) for name in FIXTURES}, 'cases': CASES,
        'derivation': {'original_bootstrap_sha256': PINS['experiments/wp-request-probe/fixtures/bootstrap.php'],
            'original_core_files_modified': False, 'original_wp_load_order_changed': False,
            'original_wp_and_template_loader_calls_unchanged': True,
            'changes': ['private_file_context_and_copied_owned_location', 'sealed_INI_profile', 'exact_public_request_reset',
                'request_context_snapshots', 'fixed_missing_post_404_status_check', 'owned_wrapper_status_header_third_argument']},
        'php_executed': False, 'database_connected': False, 'network_calls_made': False, 'wasm_executed': False,
        'product_runtime_selected': False}


def prepare(record=True):
    value = metadata()
    if record:
        require(WORK.resolve() == WORK and WORK.is_relative_to(ROOT / 'target'), 'context_owned_metadata')
        WORK.mkdir(parents=True, exist_ok=True); path = WORK / 'prepared.json'; require(path.resolve() == path, 'context_metadata_alias')
        path.write_bytes((json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii'))
    require(inputs() == value['input_files'], 'context_sources_changed'); return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.parse_args()
    prepare(); print('Clean request sources prepared; no PHP, DB or network executed')
