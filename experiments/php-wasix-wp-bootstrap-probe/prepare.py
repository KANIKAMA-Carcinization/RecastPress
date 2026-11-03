"""Bind untouched full WP sources and explicitly derived private-channel fixtures.

No PHP, Wasm, database or network execution. Only owned preparation JSON is written.
The existing native source tree is verified read-only; db.php is a guest overlay.
"""
import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/php-wasix-wp-bootstrap-probe'
PREPARED = WORK / 'prepared.json'
NATIVE = ROOT / 'experiments/wp-bootstrap-probe'
OLD_WASM = ROOT / 'experiments/php-wasix-wpdb-probe'
FIXTURES = ['install.php','read.php','bootstrap.php','helpers.php','adapter.php','db.php']
BASE_COMMIT = 'a1bb591c19dc897f1d41a42d17f8c33088f4362b'
PINS = {
    'experiments/wp-bootstrap-probe/fixtures/bootstrap.php': (15581,'36cb1216727442fb61712b459e2b2fadea81de0c0aadb0a2041351570c826da1'),
    'experiments/wp-bootstrap-probe/fixtures/install.php': (107,'df0befc6ce03224e12aeea68eae19e98ddab40df9dc7400976ee1b9212382925'),
    'experiments/wp-bootstrap-probe/fixtures/read.php': (104,'00ff38f879289d63e7e6089755765a859ff6cad3ffe3b300b71e220c3ae23ccc'),
    'experiments/wp-bootstrap-probe/prepare.py': (10184,'df009b467c1c2452c746ff8f4e6f5fa3a71c04795d32efe77e186b2ee8d776bc'),
    'experiments/wp-bootstrap-probe/evidence/windows-mysql-native.json': (33970,'61c331e5e9f316dd6e2f241a54f1de337bbff397d405c6a5f4ae55cc49dc2c91'),
    'experiments/wp-bootstrap-probe/evidence/windows-mariadb-native.json': (31590,'af07c9ff931f87b2f8d235423fcf5db0101ffc1402904b899c6294d9c6f35f2f'),
    'experiments/php-wasix-wpdb-probe/fixtures/helpers.php': (11721,'1fc2723fbab4a9641d3248db40e49f1d1397f03c0824b58a47aa67807af1cfa7'),
    'experiments/php-wasix-wpdb-probe/fixtures/adapter.php': (15447,'dbbadf65715feb0924bb48b9f5b9cb9eacd35e9239a942e019e754b9ffacc696'),
    'experiments/php-wasix-wpdb-probe/prepare.py': (18605,'2d9b0e6ab95a13e2700bb69801a5ca19b6276dd6247eb4525cfb7a042a357eb3'),
}
CONTEXT = b'''function rp_boot_context(string $wire, string $mode): array {
    rp_check(strlen($wire) <= 4096, 'context_size');
    $value = json_decode($wire, true, 16, JSON_THROW_ON_ERROR);
    rp_check(is_array($value), 'context_object');
    $keys = ['schema', 'mode', 'nonce', 'salts'];
    if ($mode === 'install') { $keys[] = 'admin_password'; }
    rp_exact_keys($value, $keys, 'context_exact_keys');
    rp_check($value['schema'] === 'recastpress.php-wasix-wp-bootstrap-context.v0.5'
        && $value['mode'] === $mode && in_array($mode, ['install', 'read'], true), 'context_profile');
    $secret = static fn($v): bool => is_string($v) && preg_match('/\\A[0-9a-f]{48}\\z/D', $v) === 1;
    rp_check($secret($value['nonce']) && is_array($value['salts']), 'context_nonce_or_salts');
    $salts = ['AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY',
        'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT'];
    rp_exact_keys($value['salts'], $salts, 'context_salt_keys');
    foreach ($value['salts'] as $salt) { rp_check($secret($salt), 'context_salt_shape'); }
    if ($mode === 'install') { rp_check($secret($value['admin_password']), 'context_admin_shape'); }
    $all = array_merge($keys, $salts); sort($all);
    rp_check(rp_context_json_keys($wire) === $all, 'context_duplicate_keys');
    return $value;
}
'''
CGI_INPUT = b'''function rp_boot_input(string $mode): array {
    rp_boot_check(PHP_SAPI === 'cgi-fcgi' && ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET'
        && ($_SERVER['QUERY_STRING'] ?? '') === 'rp_bootstrap=1'
        && ($_SERVER['REQUEST_URI'] ?? '') === '/__rp_bootstrap.php?rp_bootstrap=1'
        && ($_SERVER['argc'] ?? 0) === 0, 'cgi_entry_guard');
    $stream = fopen('/rp-bootstrap/context.json', 'rb');
    rp_boot_check(is_resource($stream), 'context_open');
    try { $wire = stream_get_contents($stream, 4097); }
    finally { fclose($stream); }
    rp_boot_check(is_string($wire) && strlen($wire) <= 4096, 'context_read');
    return rp_boot_context($wire, $mode);
}
'''
DROPIN = b'''<?php
declare(strict_types=1);
// Owned overlay only. require_wp_db() has loaded the untouched parent wpdb.
require_once '/experiments/php-wasix-wp-bootstrap-probe/fixtures/helpers.php';
require_once '/experiments/php-wasix-wp-bootstrap-probe/fixtures/adapter.php';
global $wpdb;
rp_check(!isset($wpdb) && defined('ABSPATH') && ABSPATH === '/wordpress/', 'owned_dropin_context');
rp_check(isset($GLOBALS['rp_input']['nonce']), 'owned_dropin_nonce');
$wpdb = new RpBridgeWpdb(['nonce' => $GLOBALS['rp_input']['nonce']]);
$wpdb->suppress_errors(true);
$GLOBALS['rp_report']['owned_db_dropin_loaded'] = true;
'''


def require(value, code):
    if not value: raise ValueError(code)


def sha(wire): return hashlib.sha256(wire).hexdigest()


def identity(wire): return {'bytes':len(wire),'sha256':sha(wire)}


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def read(path, maximum=2*1024*1024):
    require(path.resolve(strict=True)==path and path.is_file() and 0<path.stat().st_size<=maximum,'fixed_source_path_or_size')
    wire=path.read_bytes();require(len(wire)<=maximum,'fixed_source_grew');return wire


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def parents():
    original={}
    for name,(size,digest) in PINS.items():
        wire=read(ROOT/name);require((len(wire),sha(wire))==(size,digest),'frozen_parent_changed');original[name]=wire
    return original


def once(wire,old,new):
    require(wire.count(old)==1,'derivation_context_changed');return wire.replace(old,new,1)


def section(wire,start,end):
    require(wire.count(start)==wire.count(end)==1,'derivation_marker_count')
    first,last=wire.index(start),wire.index(end);require(first<last,'derivation_marker_order');return wire[first:last]


def expected_fixtures(original, wpdb):
    native=original['experiments/wp-bootstrap-probe/fixtures/bootstrap.php']
    helper=original['experiments/php-wasix-wpdb-probe/fixtures/helpers.php']
    helpers=b'<?php\ndeclare(strict_types=1);\n\nfinal class RpNativeFailure extends RuntimeException {}\n'
    helpers+=section(helper,b'function rp_check(',b'/** Strings remain bytes;')
    helpers+=section(helper,b'function rp_exact_keys(',b'function rp_error_identity(')
    keys=section(native,b'function rp_boot_json_keys(',b'function rp_boot_input(')
    keys=once(keys,b'function rp_boot_json_keys(',b'function rp_context_json_keys(')
    helpers+=keys.replace(b'rp_boot_check(',b'rp_check(')+CONTEXT
    adapter=original['experiments/php-wasix-wpdb-probe/fixtures/adapter.php']
    for old,new in [(b'$this->seq < 64',b'$this->seq < 4096'),(b'strlen($bytes) <= 4096',b'strlen($bytes) <= 65536'),
        (b'strlen($wire) <= 16384',b'strlen($wire) <= 262144'),(b'<= 131072',b'<= 8388608'),
        (b'fgets($this->stream, 65538)',b'fgets($this->stream, 524290)'),(b'strlen($line) <= 65536',b'strlen($line) <= 524288'),
        (b'<= 524288, \'rpc_reply_total_quota\'',b'<= 16777216, \'rpc_reply_total_quota\''),
        (b"'max_requests' => 64",b"'max_requests' => 4096"),
        (b"count($data['columns']) <= 16",b"count($data['columns']) <= 32"),
        (b"count($data['rows']) <= 16",b"count($data['rows']) <= 256"),
        (b'private array $setup = [];',b'private int $setupQueries = 0;'),
        (b"$this->setup[] = ['sql_hex' => bin2hex($sql), 'data' => $data];",b'++$this->setupQueries;')]:
        adapter=once(adapter,old,new)
    adapter=once(adapter,b"        foreach ($data['rows'] as $row) {\n            rp_check(is_array($row)",
        b"        $raw_bytes = 0;\n        foreach ($data['rows'] as $row) {\n            rp_check(is_array($row)")
    adapter=once(adapter,b'                    rp_unhex($cell, 4096);',
        b"                    $raw_bytes += strlen(rp_unhex($cell, 65536));\n                    rp_check($raw_bytes <= 262144, 'rpc_result_raw_byte_quota');")
    adapter=once(adapter,b"rp_unhex($row[$index], 4096)",b"rp_unhex($row[$index], 65536)")
    adapter=once(adapter,b"        rp_check(is_string($query) && preg_match('/\\A[\\x00-\\x7f]*\\z/D', $query) === 1,\n            'rpc_non_ascii_query_out_of_fixed_scope');",
        b"        rp_check(is_string($query), 'rpc_query_string_required');")
    validation=section(wpdb,b"\t\t// If we're writing to the database,",b'\t\t$this->check_current_query = true;\n\n\t\t// Keep track of the last query')
    adapter=once(adapter,b'        $this->func_call = "\\$db->query(\\"$query\\")";\n',
        b'        $this->func_call = "\\$db->query(\\"$query\\")";\n'+validation)
    adapter=once(adapter,b"$message = rp_unhex($error['message_hex'], 4096);",b"$message = rp_unhex($error['message_hex'], 65536);")
    adapter=once(adapter,b"$error['code'] === 1064 && $state === '42000'",
        b"is_int($error['code']) && $error['code'] > 0 && $error['code'] <= 65535\n                && preg_match('/\\A[A-Z0-9]{5}\\z/D', $state) === 1")
    adapter=once(adapter,b"preg_match('/\\A[\\x00-\\x7f]*\\z/D', $message) === 1",b"preg_match('//u', $message) === 1")
    adapter=once(adapter,b"preg_match('/\\A[\\x00-\\x7f]*\\z/D', $bytes) === 1",b"preg_match('//u', $bytes) === 1")
    adapter=once(adapter,b"rp_unhex($reply['data']['escaped_hex'], 8192)",b"rp_unhex($reply['data']['escaped_hex'], 131072)")
    adapter=once(adapter,b"return ['transport' => $this->rpc->stats(), 'internal_session_setup' => $this->setup,\n            'escape_scope' => 'ASCII-only-fixed-utf8mb4-and-cached-last-OK-NO_BACKSLASH_ESCAPES',",
        b"return ['transport' => $this->rpc->stats(), 'internal_setup_queries' => $this->setupQueries,\n            'escape_scope' => 'valid-UTF8-only-fixed-utf8mb4-and-cached-last-OK-NO_BACKSLASH_ESCAPES',")
    bootstrap=once(native,b'declare(strict_types=1);\n',b"declare(strict_types=1);\nrequire __DIR__ . '/helpers.php';\nconst RP_DROPIN_SHA = '"+sha(DROPIN).encode()+b"';\n")
    bootstrap=once(bootstrap,section(bootstrap,b'function rp_boot_input(',b'function rp_boot_inventory('),CGI_INPUT+b'\n')
    bootstrap=once(bootstrap,b'$actual_files = []; $actual_dirs = []; $total = 0;',b'$actual_files = []; $actual_dirs = []; $total = 0; $overlay_seen = false;')
    bootstrap=once(bootstrap,b"        if ($entry->isDir()) { $actual_dirs[] = $relative; continue; }\n",
        b"        if ($entry->isDir()) { $actual_dirs[] = $relative; continue; }\n"
        b"        if ($relative === 'wp-content/db.php') {\n"
        b"            rp_boot_check(!$overlay_seen && $entry->isFile() && hash_file('sha256', $path) === RP_DROPIN_SHA, 'owned_dropin_bytes');\n"
        b"            $overlay_seen = true; continue;\n        }\n")
    bootstrap=once(bootstrap,b"&& $total === 110077999 && !file_exists($site . '/wp-config.php')",b"&& $overlay_seen && $total === 110077999 && !file_exists($site . '/wp-config.php')")
    bootstrap=bootstrap.replace(b'recastpress.wp-bootstrap-native-observation.v0.5',b'recastpress.php-wasix-wp-bootstrap-php-observation.v0.5')
    bootstrap=bootstrap.replace(b'fixed_native_bootstrap_failed',b'fixed_wasix_bootstrap_failed').replace(b'fixed_native_bootstrap_passed',b'fixed_wasix_bootstrap_passed')
    bootstrap=once(bootstrap,b"'rust_executed' => false, 'wasm_executed' => false",b"'rust_executed' => true, 'wasm_executed' => true")
    bootstrap=once(bootstrap,b"rp_boot_check(PHP_SAPI === 'cli' && ($argc ?? 0) === 1 && in_array($rp_mode, ['install', 'read'], true), 'closed_native_entry');",
        b"rp_boot_check(in_array($rp_mode, ['install', 'read'], true), 'closed_bootstrap_mode');")
    bootstrap=once(bootstrap,b"$rp_secrets = array_merge([$rp_input['password']], array_values($rp_input['salts']));",b"$rp_secrets = array_merge([$rp_input['nonce']], array_values($rp_input['salts']));")
    bootstrap=once(bootstrap,section(bootstrap,b"    $rp_root = realpath(",b"    $rp_report['phase'] = 'source_inventory';"),
        b"    $rp_site = '/wordpress';\n    rp_boot_check(realpath($rp_site) === $rp_site, 'fixed_guest_site');\n    $rp_manifest = '/rp-bootstrap/source-manifest.json';\n")
    bootstrap=once(bootstrap,b"define('DB_NAME', $rp_input['database']); define('DB_USER', $rp_input['user']); define('DB_PASSWORD', $rp_input['password']);\n    define('DB_HOST', '127.0.0.1:' . $rp_input['port']);",
        b"define('DB_NAME', 'rp_unused_guest_db'); define('DB_USER', 'rp_unused_guest_user'); define('DB_PASSWORD', '');\n    define('DB_HOST', 'rp-unused.invalid');")
    bootstrap=once(bootstrap,b"&& $rp_report['native_mysqli_handle'], 'full_normal_bootstrap_required');",
        b"&& !$rp_report['native_mysqli_handle'] && $wpdb instanceof RpBridgeWpdb\n        && ($rp_report['owned_db_dropin_loaded'] ?? false) === true, 'full_rpc_bootstrap_required');")
    bootstrap=once(bootstrap,b"if (isset($wpdb) && $wpdb instanceof wpdb && $wpdb->dbh instanceof mysqli)",b"if (isset($wpdb) && $wpdb instanceof RpBridgeWpdb && $wpdb->dbh !== null)")
    bootstrap=once(bootstrap,b'    restore_error_handler(); rp_boot_emit();',
        b"    if (isset($wpdb) && $wpdb instanceof RpBridgeWpdb) { $rp_report['rpc_stats'] = $wpdb->rpc_stats(); }\n    restore_error_handler(); rp_boot_emit();")
    bootstrap=once(bootstrap,b"$rp_report['failure'] = $rp_error instanceof RpBootstrapFailure ? $rp_error->getMessage() : 'php_throwable';\n    $rp_report['failure_class'] = get_class($rp_error);",
        b"$rp_failure = ($rp_error instanceof RpBootstrapFailure || $rp_error instanceof RpNativeFailure) ? $rp_error->getMessage() : 'php_throwable';\n"
        b"    $rp_report['failure'] = in_array($rp_failure, RP_PUBLIC_FAILURE_CODES, true) ? $rp_failure : 'unclassified_failure';\n"
        b"    $rp_class = get_class($rp_error);\n"
        b"    $rp_report['failure_class'] = in_array($rp_class, RP_PUBLIC_FAILURE_CLASSES, true) ? $rp_class : 'unclassified_exception';")
    codes=set(re.findall(rb",\s*'([a-z][a-z0-9_]*)'\);",bootstrap+helpers+adapter+DROPIN))
    codes.update(re.findall(rb"new Rp(?:Native|Bootstrap)Failure\('([a-z][a-z0-9_]*)'\)",bootstrap+helpers+adapter))
    codes.update([b'php_throwable',b'public_secret_guard',b'unexpected_php_shutdown',b'unclassified_failure'])
    phases=['input','source_inventory','wp_settings','wp_install','public_observations','source_after','complete']
    classes=['RpBootstrapFailure','RpNativeFailure','JsonException','Error','TypeError','ValueError',
        'ArgumentCountError','RuntimeException','Exception','ParseError','ErrorException','unclassified_exception']
    constants=(b'const RP_PUBLIC_PHASES = '+json.dumps(phases,separators=(',',':')).encode()+b';\n'
        +b'const RP_PUBLIC_FAILURE_CODES = '+json.dumps(sorted(c.decode() for c in codes),separators=(',',':')).encode()+b';\n'
        +b'const RP_PUBLIC_FAILURE_CLASSES = '+json.dumps(classes,separators=(',',':')).encode()+b';\n')
    bootstrap=once(bootstrap,b"require __DIR__ . '/helpers.php';\n",b"require __DIR__ . '/helpers.php';\n"+constants)
    expected={'bootstrap.php':bootstrap,'helpers.php':helpers,'adapter.php':adapter,'db.php':DROPIN}
    for mode in ['install','read']:
        expected[mode+'.php']=once(original['experiments/wp-bootstrap-probe/fixtures/'+mode+'.php'],b'declare(strict_types=1);\n',b"declare(strict_types=1);\nheader('Content-Type: application/json');\n")
    return expected,validation


def derivation_gate():
    original=parents()
    wpdb=read(NATIVE.parents[1]/'target/wp-bootstrap-probe/site/wp-includes/class-wpdb.php')
    require(identity(wpdb)=={'bytes':121437,'sha256':'e15403e90032dd0508811301505e491d4212de5152581a98f51b744a1a3903bd'},'untouched_wpdb_source')
    expected,validation=expected_fixtures(original,wpdb)
    for name in FIXTURES:
        wire=read(HERE/'fixtures'/name);require(b'\r' not in wire and wire==expected[name],'derived_bootstrap_fixture_changed')
    require({p.name for p in (HERE/'fixtures').iterdir()}==set(FIXTURES),'closed_bootstrap_fixture_set')
    return {'base_commit':BASE_COMMIT,'original_git_objects_verified_by_preparer':False,
        'parents':{n:identity(w) for n,w in original.items()},'derived':{n:identity(expected[n]) for n in FIXTURES},
        'upstream_query_validation_block_sha256':sha(validation),'upstream_query_validation_bytes_preserved':True,
        'allowed_helper_changes':['extract-private-channel-validation-functions',
            'rename-native-duplicate-key-reader-and-check-function','strict-private-context-schema'],
        'allowed_adapter_changes':['finite-large-result-and-channel-budgets','upstream-query-invalid-text-validation',
            'valid-UTF8-escape-only','bounded-general-server-error-code-state-private-message',
            'no-public-SQL-row-or-setup-trace'],
        'allowed_bootstrap_changes':['CGI-fixed-GET-and-private-context','owned-guest-source-paths-and-db-overlay',
            'dummy-DB-constants-and-Rust-handle','Wasm-observation-schema-and-context-flags','private-count-only-RPC-statistics'],
        'original_core_theme_plugin_bytes_modified':False,'owned_db_overlay_files':1}


def modules():
    return load('rp_full_native_sources',NATIVE/'prepare.py'),load('rp_previous_cgi_derivation',OLD_WASM/'prepare.py')


def input_hashes():
    native,old=modules();result={**old.input_hashes(),**native.inputs()}
    for path in [Path(__file__).resolve(),*[HERE/'fixtures'/n for n in FIXTURES],*[ROOT/n for n in PINS]]:
        result[path.relative_to(ROOT).as_posix()]=sha(read(path))
    return dict(sorted(result.items()))


def metadata():
    before=input_hashes();native,old=modules()
    core=native.verify_prepared();parent=old.derivation_gate();derivation=derivation_gate()
    files=[]
    for name in FIXTURES:
        source=HERE/'fixtures'/name
        files.append({'source':source.relative_to(ROOT).as_posix(),
            'guest':'/wordpress/wp-content/db.php' if name=='db.php' else '/experiments/php-wasix-wp-bootstrap-probe/fixtures/'+name,
            **identity(read(source)),'guest_read_only':True,'kind':'owned_wpdb_dropin' if name=='db.php' else 'derived_fixture'})
    native_oracles={}
    for backend in ['mysql','mariadb']:
        name='experiments/wp-bootstrap-probe/evidence/windows-'+backend+'-native.json';wire=read(ROOT/name);value=json.loads(wire)
        require(value['passed'] is True and value['full_wordpress_bootstrap_verified'] is True,'accepted_native_bootstrap_required')
        native_oracles[backend]={'path':name,**identity(wire)}
    require(input_hashes()==before,'bootstrap_prepared_sources_changed')
    return {'schema':'recastpress.php-wasix-wp-bootstrap-prepared.v0.5','outcome':'full_core_and_owned_db_overlay_prepared',
        'input_files':before,'core_site':core['site'],'core_manifest':core['source_manifest'],
        'core_inventory':core['source_tree'],'core_archive':{'bytes':core['archive_bytes'],'sha256':core['archive_sha256']},
        'files':files,'native_oracles':native_oracles,'derivation':derivation,'previous_cgi_derivation':parent,
        'core_guest_root':'/wordpress','private_context_guest_path':'/rp-bootstrap/context.json',
        'core_manifest_guest_path':'/rp-bootstrap/source-manifest.json','guest_context_contains_db_credentials':False,
        'native_reference_binaries_reverified':False,'core_files_modified':False,'secret_context_in_prepared_metadata':False,
        'php_executed':False,'wasm_executed':False,'database_connected':False,'product_runtime_selected':False,
        'guest_readonly_enforcement_executed':False,'inputs_unchanged':True}


def owned(path):
    require(WORK.resolve()==WORK and path.is_relative_to(WORK) and path.resolve()==path,'owned_preparation_path');return path


def verify_prepared():
    expected=metadata();wire=read(owned(PREPARED));require(canonical(json.loads(wire))==canonical(expected),'prepared_metadata_changed');return expected


def prepare():
    expected=metadata();owned(WORK).mkdir(parents=True,exist_ok=True)
    wire=(json.dumps(expected,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode()
    if PREPARED.exists():
        old=read(owned(PREPARED))
        if old!=wire:
            saved=owned(WORK/('prepared-before-'+sha(old)[:16]+'.json'))
            if saved.exists():require(read(saved)==old,'old_prepared_metadata_changed')
            else:
                with saved.open('xb') as stream:stream.write(old)
    owned(PREPARED).write_bytes(wire);require(verify_prepared()==expected,'prepared_after_changed');return expected


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args();print((verify_prepared() if args.verify_only else prepare())['outcome'])
