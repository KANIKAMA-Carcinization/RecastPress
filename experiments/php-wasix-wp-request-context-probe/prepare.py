"""Portable source preparation for clean home/404 PHP WASIX requests only."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/php-wasix-wp-request-context-probe'
PREPARED = WORK / 'prepared.json'
GUEST_BASE = '/experiments/php-wasix-wp-request-probe/fixtures/'
FIXTURES = ['home.php', 'missing.php', 'bootstrap.php', 'context.php', 'helpers.php', 'adapter.php', 'db.php']
PINS = {
    'experiments/wp-request-context-probe/fixtures/bootstrap.php': '3aaa6a9399d48e2f6e4ab362b9f83fadf407dd26614771f731c785b92aa1a10d',
    'experiments/wp-request-context-probe/fixtures/context.php': '90298bb19119200ab6db8458aef4e6ffabf8fdbc85708ecb5aff54627081c2c4',
    'experiments/wp-request-context-probe/fixtures/home.php': '7df64fb630fc9b3710f549cba26ebee97b2ace4501bb99d07a23539d24c9ea6b',
    'experiments/wp-request-context-probe/fixtures/missing.php': '6375acb23e91e200f22c0ee5422e0f6b4f502f9e851f857e7cb62b31e5e4f379',
    'experiments/php-wasix-wp-request-probe/prepare.py': '95f28e3893068ee3f875175001e65263605b16a5dc1f349f48cc6e5837d60f1d',
    'experiments/php-wasix-wp-request-probe/fixtures/adapter.php': '55f7042bebb6abbdd73104e52688a913dc38f2ae2b59abba292f303200f91b1f',
    'experiments/php-wasix-wp-request-probe/fixtures/helpers.php': 'e854d9b6c4b8ac3d27381086f8a8c9b984de949782ca6f24350c6e0294ef421d',
    'experiments/php-wasix-wp-request-probe/fixtures/db.php': '5e48035bdc93b159237e9742eb90b296748e5a20f96b83c2ef2e1148f225df15',
}
ORACLES = {
    'mysql': {'path': 'experiments/wp-request-context-probe/evidence/windows-mysql-native.json', 'bytes': 795353,
        'sha256': '7c2a0b01fdd5e90a120b8e278368245c18618936a268baefb09ff1adba01e00b'},
    'mariadb': {'path': 'experiments/wp-request-context-probe/evidence/windows-mariadb-native.json', 'bytes': 792967,
        'sha256': 'a2d54133bc368f9945b5f178aafcc5c1edf60778c90247491f1ad60aeb0fe1df'},
}
GUEST_READER = b'''function rp_context_read(string $mode): array {
    global $rp_report;
    rp_boot_check($mode === 'home' && in_array(RP_REQUEST_CASE, ['home', 'missing'], true), 'context_case');
    $body = file_get_contents('php://input');
    rp_boot_check($body === '', 'public_request_body_not_empty');
    $rp_report['transport_body'] = ['bytes' => strlen($body), 'sha256' => hash('sha256', $body)];
    $stream = fopen('/rp-request/context.json', 'rb');
    rp_boot_check(is_resource($stream), 'private_context_open');
    try { $wire = stream_get_contents($stream, 4097); } finally { fclose($stream); }
    rp_boot_check(is_string($wire) && strlen($wire) <= 4096, 'private_context_size');
    $context = json_decode($wire, true, 16, JSON_THROW_ON_ERROR);
    rp_boot_check(is_array($context), 'private_context_object');
    rp_boot_exact($context, ['schema', 'mode', 'case', 'nonce', 'salts'], 'private_context_keys');
    rp_boot_check($context['schema'] === 'recastpress.php-wasix-wp-request-context-guest.v0.5'
        && $context['mode'] === $mode && $context['case'] === RP_REQUEST_CASE
        && rp_boot_secret($context['nonce']) && is_array($context['salts']), 'private_context_profile');
    $keys = ['AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY',
        'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT'];
    rp_boot_exact($context['salts'], $keys, 'private_context_salt_keys');
    foreach ($context['salts'] as $salt) { rp_boot_check(rp_boot_secret($salt), 'private_context_salt_shape'); }
    rp_boot_check(json_encode($context, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) === trim($wire, " \\t\\r\\n"),
        'private_context_duplicate_or_noncanonical');
    $rp_report['private_context_before'] = ['bytes' => strlen($wire), 'sha256' => hash('sha256', $wire)];
    $loaded = php_ini_loaded_file(); $scanned = php_ini_scanned_files();
    $rp_report['ini_profile'] = ['owned_file_loaded' => $loaded !== false,
        'additional_ini_scanned' => $scanned !== false && $scanned !== '',
        'display_errors' => ini_get('display_errors'), 'log_errors' => ini_get('log_errors'),
        'register_argc_argv' => ini_get('register_argc_argv'), 'allow_url_fopen' => ini_get('allow_url_fopen'),
        'mail_function_present' => function_exists('mail'), 'mysqli_loaded' => extension_loaded('mysqli'),
        'expose_php' => ini_get('expose_php')];
    rp_boot_check($rp_report['ini_profile'] === ['owned_file_loaded' => false, 'additional_ini_scanned' => false,
        'display_errors' => '1', 'log_errors' => '0', 'register_argc_argv' => '0', 'allow_url_fopen' => '0',
        'mail_function_present' => false, 'mysqli_loaded' => true, 'expose_php' => '1'], 'fixed_wasm_ini_profile');
    return $context;
}

'''


def require(value, code):
    if not value: raise ValueError(code)


def sha(wire): return hashlib.sha256(wire).hexdigest()
def identity(wire): return {'bytes': len(wire), 'sha256': sha(wire)}
def canonical(value): return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def read(path, maximum=4*1024*1024):
    require(path.resolve(strict=True) == path and path.is_file() and 0 < path.stat().st_size <= maximum, 'fixed_context_wasm_source')
    wire = path.read_bytes(); require(len(wire) <= maximum, 'context_wasm_source_quota'); return wire


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def parents():
    result = {}
    for name, expected in PINS.items():
        wire = read(ROOT / name); require(sha(wire) == expected, 'frozen_context_wasm_parent_changed'); result[name] = wire
    return result


def once(wire, before, after):
    require(wire.count(before) == 1, 'exact_context_wasm_derivation'); return wire.replace(before, after)


def section(wire, start, end):
    require(wire.count(start) == wire.count(end) == 1, 'context_wasm_section_markers')
    first, last = wire.index(start), wire.index(end); require(first < last, 'context_wasm_section_order'); return wire[first:last]


def expected_fixtures(original):
    native = 'experiments/wp-request-context-probe/fixtures/'
    old = 'experiments/php-wasix-wp-request-probe/fixtures/'
    context = original[native + 'context.php']
    context = once(context, section(context, b'function rp_context_read(', b'function rp_context_public('), GUEST_READER)
    context = once(context, b"fopen(__DIR__ . '/context.json', 'rb')", b"fopen('/rp-request/context.json', 'rb')")
    context = once(context, b'stream_get_contents($stream, 32769)', b'stream_get_contents($stream, 4097)')
    context = once(context, b'strlen($wire) <= 32768', b'strlen($wire) <= 4096')
    b = original[native + 'bootstrap.php']; db = original[old + 'db.php']
    b = once(b, b"require __DIR__ . '/context.php';\n", b"require __DIR__ . '/context.php';\nrequire_once __DIR__ . '/helpers.php';\n"
        + b"const RP_DROPIN_SHA = '" + sha(db).encode('ascii') + b"';\n")
    b = once(b, section(b, b'function rp_boot_input(', b'function rp_boot_inventory('),
        b'function rp_boot_input(string $mode): array {\n    return rp_context_read($mode);\n}\n')
    b = once(b, b'$actual_files = []; $actual_dirs = []; $total = 0;', b'$actual_files = []; $actual_dirs = []; $total = 0; $overlay_seen = false;')
    marker = b'        if ($entry->isDir()) { $actual_dirs[] = $relative; continue; }\n'
    b = once(b, marker, marker + b"        if ($relative === 'wp-content/db.php') {\n"
        b"            rp_boot_check(!$overlay_seen && $entry->isFile() && hash_file('sha256', $path) === RP_DROPIN_SHA, 'owned_dropin_bytes');\n"
        b"            $overlay_seen = true; continue;\n        }\n")
    b = once(b, b"&& $total === 110077999 && !file_exists($site . '/wp-config.php')",
        b"&& $overlay_seen && $total === 110077999 && !file_exists($site . '/wp-config.php')")
    b = b.replace(b'recastpress.wp-request-context-native-observation.v0.5', b'recastpress.php-wasix-wp-request-context-php-observation.v0.5')
    b = b.replace(b'fixed_native_context_passed', b'fixed_wasix_context_passed').replace(b'fixed_native_context_failed', b'fixed_wasix_context_failed')
    b = once(b, b"'rust_executed' => false, 'wasm_executed' => false", b"'rust_executed' => true, 'wasm_executed' => true")
    b = once(b, b'/__rp_context.php?rp_request=1', b'/__rp_request.php?rp_request=1')
    b = once(b, b"$rp_secrets = array_merge([$rp_input['password']], array_values($rp_input['salts']));",
        b"$rp_secrets = array_merge([$rp_input['nonce']], array_values($rp_input['salts']));")
    b = once(b, section(b, b"    $rp_root = realpath(", b"    $rp_report['phase'] = 'source_inventory';"),
        b"    $rp_site = '/wordpress';\n    rp_boot_check(realpath($rp_site) === $rp_site, 'fixed_guest_site');\n"
        b"    $rp_manifest = '/rp-request/source-manifest.json';\n")
    b = once(b, b"define('DB_NAME', $rp_input['database']); define('DB_USER', $rp_input['user']); define('DB_PASSWORD', $rp_input['password']);\n    define('DB_HOST', '127.0.0.1:' . $rp_input['port']);",
        b"define('DB_NAME', 'rp_unused_guest_db'); define('DB_USER', 'rp_unused_guest_user'); define('DB_PASSWORD', '');\n    define('DB_HOST', 'rp-unused.invalid');")
    b = once(b, b"&& $rp_report['native_mysqli_handle'], 'full_normal_bootstrap_required');",
        b"&& !$rp_report['native_mysqli_handle'] && $wpdb instanceof RpBridgeWpdb\n"
        b"        && ($rp_report['owned_db_dropin_loaded'] ?? false) === true, 'full_rpc_bootstrap_required');")
    b = once(b, b'if (isset($wpdb) && $wpdb instanceof wpdb && $wpdb->dbh instanceof mysqli)',
        b'if (isset($wpdb) && $wpdb instanceof RpBridgeWpdb && $wpdb->dbh !== null)')
    b = once(b, b'    restore_error_handler(); rp_boot_emit();',
        b"    if (isset($wpdb) && $wpdb instanceof RpBridgeWpdb) { $rp_report['rpc_stats'] = $wpdb->rpc_stats(); }\n    restore_error_handler(); rp_boot_emit();")
    b = once(b, b"$rp_report['failure'] = $rp_error instanceof RpBootstrapFailure ? $rp_error->getMessage() : 'php_throwable';\n    $rp_report['failure_class'] = get_class($rp_error);",
        b"$code = ($rp_error instanceof RpBootstrapFailure || $rp_error instanceof RpNativeFailure) ? $rp_error->getMessage() : 'php_throwable';\n"
        b"    $rp_report['failure'] = in_array($code, RP_PUBLIC_FAILURE_CODES, true) ? $code : 'unclassified_failure';\n"
        b"    $class = get_class($rp_error);\n    $rp_report['failure_class'] = in_array($class, RP_PUBLIC_FAILURE_CLASSES, true) ? $class : 'unclassified_exception';")
    sources = b + context + original[old + 'helpers.php'] + original[old + 'adapter.php'] + db
    codes = set(re.findall(rb",\s*'([a-z][a-z0-9_]*)'\);", sources))
    codes.update(re.findall(rb"new Rp(?:Native|Bootstrap)Failure\('([a-z][a-z0-9_]*)'\)", sources))
    codes.update([b'php_throwable', b'public_secret_guard', b'public_response_quota', b'unexpected_php_shutdown', b'unclassified_failure'])
    phases = ['input', 'source_inventory', 'wp_settings', 'wp_install', 'wp_dispatch', 'template_loader', 'public_observations', 'source_after', 'complete']
    classes = ['RpBootstrapFailure', 'RpNativeFailure', 'JsonException', 'Error', 'TypeError', 'ValueError',
        'ArgumentCountError', 'RuntimeException', 'Exception', 'ParseError', 'ErrorException', 'unclassified_exception']
    constants = b'const RP_PUBLIC_PHASES = ' + json.dumps(phases, separators=(',', ':')).encode('ascii') + b';\n'
    constants += b'const RP_PUBLIC_FAILURE_CODES = ' + json.dumps(sorted(code.decode() for code in codes), separators=(',', ':')).encode('ascii') + b';\n'
    constants += b'const RP_PUBLIC_FAILURE_CLASSES = ' + json.dumps(classes, separators=(',', ':')).encode('ascii') + b';\n'
    b = once(b, b"require_once __DIR__ . '/helpers.php';\n", b"require_once __DIR__ . '/helpers.php';\n" + constants)
    return {'home.php': original[native + 'home.php'], 'missing.php': original[native + 'missing.php'],
        'bootstrap.php': b, 'context.php': context,
        **{name: original[old + name] for name in ['helpers.php', 'adapter.php', 'db.php']}}


def previous():
    parents(); return load('rp_context_previous_portable_wasm_home', ROOT / 'experiments/php-wasix-wp-request-probe/prepare.py')


def input_hashes():
    result = previous().input_hashes()
    for name in list(PINS) + [pin['path'] for pin in ORACLES.values()]: result[name] = sha(read(ROOT / name))
    for path in [Path(__file__).resolve(), *[HERE / 'fixtures' / name for name in FIXTURES]]:
        result[path.relative_to(ROOT).as_posix()] = sha(read(path))
    return dict(sorted(result.items()))


def derivation_gate():
    original = parents(); expected = expected_fixtures(original)
    require({path.name for path in (HERE / 'fixtures').iterdir()} == set(FIXTURES), 'finite_context_wasm_fixtures')
    for name, wire in expected.items(): require(read(HERE / 'fixtures' / name) == wire and b'\r' not in wire, 'exact_context_wasm_fixture')
    native = 'experiments/wp-request-context-probe/fixtures/'
    public = section(original[native + 'context.php'], b'function rp_context_public(', b'function rp_context_verify(')
    require(expected['context.php'].count(public) == 1, 'native_public_reset_and_snapshots_changed')
    dispatch = section(original[native + 'bootstrap.php'], b"    $rp_report['phase'] = 'wp_dispatch';", b"    $rp_report['phase'] = 'public_observations';")
    require(expected['bootstrap.php'].count(dispatch) == 1, 'native_dispatch_render_capture_changed')
    return {'parent_sources': {name: identity(wire) for name, wire in original.items()},
        'derived_sources': {name: identity(wire) for name, wire in expected.items()},
        'native_public_context_block': identity(public), 'native_dispatch_render_capture_block': identity(dispatch),
        'native_public_reset_and_snapshots_bytes_unchanged': True, 'native_dispatch_render_capture_bytes_unchanged': True,
        'old_guest_mount_preserved': True, 'adapter_helpers_and_db_bytes_unchanged': True,
        'allowed_changes': ['guest_immutable_nonce_salts_case_context_and_profile', 'Wasm_schema_and_execution_flags',
            'guest_core_root_dummy_DB_and_one_owned_dropin', 'opaque_CGI_entry', 'closed_failure_allowlists_and_RPC_stats'],
        'original_core_files_modified': False, 'original_git_objects_verified_by_preparer': False}


def metadata():
    before = input_hashes(); old = previous().verify_prepared(); derivation = derivation_gate()
    files = []
    for name in FIXTURES:
        path = HERE / 'fixtures' / name
        files.append({'source': path.relative_to(ROOT).as_posix(),
            'guest': '/wordpress/wp-content/db.php' if name == 'db.php' else GUEST_BASE + name,
            **identity(read(path)), 'guest_read_only': True, 'kind': 'owned_wpdb_dropin' if name == 'db.php' else 'derived_fixture'})
    responses = {}
    for backend, pin in ORACLES.items():
        wire = read(ROOT / pin['path']); require(identity(wire) == {key: pin[key] for key in ['bytes', 'sha256']}, 'fixed_native_context_oracle')
        value = json.loads(wire)
        require(value['passed'] is True and value['fresh_repeat_404_response_exact'] is True
            and value['normal_admin_shutdown_verified'] is True and value['source_reverified_after'] is True
            and value['all_owned_cgi_reaped'] is True, 'accepted_native_context_oracle')
        for case in ['home', 'missing']:
            observation = value['observations'][case]
            require(observation['case'] == case and observation['passed'] is True, 'native_context_case')
            if case in responses: require(canonical(responses[case]) == canonical(observation['response']), 'native_context_response_backend_difference')
            responses[case] = observation['response']
    value = {'schema': 'recastpress.php-wasix-wp-request-context-prepared.v0.5', 'outcome': 'full_core_and_seven_context_fixtures_prepared',
        'input_files': before, 'derivation': derivation, 'files': files, 'native_oracles': ORACLES,
        **{name: old[name] for name in ['core_site', 'core_manifest', 'core_inventory', 'core_archive']},
        'guest_fixture_mount': GUEST_BASE.rstrip('/'), 'core_guest_root': '/wordpress',
        'private_context_guest_path': '/rp-request/context.json', 'core_manifest_guest_path': '/rp-request/source-manifest.json',
        'guest_context_keys': ['schema', 'mode', 'case', 'nonce', 'salts'], 'guest_context_contains_db_credentials': False,
        'html_limit_bytes': 1048576, 'wrapper_limit_bytes': 3145728,
        'php_executed': False, 'wasm_executed': False, 'database_connected': False, 'network_calls_made': False,
        'native_reference_binaries_reverified': False, 'guest_readonly_enforcement_executed': False,
        'original_core_files_modified': False, 'product_runtime_selected': False, 'inputs_unchanged': True}
    require(input_hashes() == before, 'context_wasm_inputs_changed'); return value


def owned(path):
    require(WORK.resolve() == WORK and path.resolve() == path and path.is_relative_to(WORK), 'owned_context_wasm_metadata'); return path


def verify_prepared():
    value = metadata(); require(canonical(json.loads(read(owned(PREPARED)))) == canonical(value), 'context_wasm_prepared_changed'); return value


def prepare():
    value = metadata(); owned(WORK).mkdir(parents=True, exist_ok=True)
    wire = (json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    if owned(PREPARED).exists():
        old = read(PREPARED)
        if old != wire:
            saved = owned(WORK / ('prepared-before-' + sha(old)[:16] + '.json'))
            if saved.exists(): require(read(saved) == old, 'context_wasm_previous_prepared_changed')
            else:
                with saved.open('xb') as stream: stream.write(old)
    PREPARED.write_bytes(wire); require(verify_prepared() == value, 'context_wasm_prepared_after_verify'); return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--verify-only', action='store_true'); args = parser.parse_args()
    result = verify_prepared() if args.verify_only else prepare(); print(result['outcome'])
