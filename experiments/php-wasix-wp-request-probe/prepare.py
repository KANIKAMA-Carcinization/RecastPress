"""Derive a private-Rust-DB anonymous home from the unchanged native request.

No native binaries, PHP, Wasm, DB or network executes. Only owned metadata is written.
"""
import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
NATIVE=ROOT/'experiments/wp-request-probe'
OLD=ROOT/'experiments/php-wasix-wp-bootstrap-probe'
WORK=ROOT/'target/php-wasix-wp-request-probe'
PREPARED=WORK/'prepared.json'
FIXTURES=['home.php','bootstrap.php','helpers.php','adapter.php','db.php']
BASE_COMMIT='13702f9d8627f29d46e87f4d123ab3362b2c24ad'
PINS={
    'experiments/wp-request-probe/fixtures/home.php':(104,'6627b1d91bb34724347fc7eae1df10d1b52f7096639eb2273d53641f90093d4b'),
    'experiments/wp-request-probe/fixtures/bootstrap.php':(18419,'5fcc60a8f63c3ca33d0143c71c1911d314c415a281f170b3644f3cf2abb37732'),
    'experiments/wp-request-probe/prepare.py':(11447,'e9fa925c132b64205e05b046bec552eb0a7b267dfd58ec6d0f3d5eca1f25cd43'),
    'experiments/wp-request-probe/evidence/windows-mysql-native.json':(202708,'7cf6c805a7863be81b81fd64177347e2fdea6170bd37594ad446845a90d6bf9a'),
    'experiments/wp-request-probe/evidence/windows-mariadb-native.json':(200332,'d79130b07427eab57d54275341b4bbd271bd53c63499b1d47139ab3b4c88831b'),
    'experiments/php-wasix-wp-bootstrap-probe/fixtures/helpers.php':(3381,'0581034ca4f4ce54e1a4a515c8e803503b8b79ae906ffd2d879b9f8e2b319850'),
    'experiments/php-wasix-wp-bootstrap-probe/fixtures/adapter.php':(16212,'55f7042bebb6abbdd73104e52688a913dc38f2ae2b59abba292f303200f91b1f'),
    'experiments/php-wasix-wp-bootstrap-probe/fixtures/db.php':(606,'2fcc77cd88d2432ea40c65b6a3f425f80f40064857688de64d0a71e68d6d5be8'),
    'experiments/php-wasix-wp-bootstrap-probe/prepare.py':(20822,'032da0f787ba76071bf649bc0f230206b3f4282cbadba8d7df5dd77a455cb570'),
}
CGI_INPUT=b'''function rp_boot_input(string $mode): array {
    rp_boot_check(PHP_SAPI === 'cgi-fcgi' && ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET'
        && ($_SERVER['QUERY_STRING'] ?? '') === 'rp_request=1'
        && ($_SERVER['REQUEST_URI'] ?? '') === '/__rp_request.php?rp_request=1'
        && ($_SERVER['argc'] ?? 0) === 0 && $mode === 'home', 'cgi_entry_guard');
    $stream = fopen('/rp-request/context.json', 'rb');
    rp_boot_check(is_resource($stream), 'context_open');
    try { $wire = stream_get_contents($stream, 4097); } finally { fclose($stream); }
    rp_boot_check(is_string($wire) && strlen($wire) <= 4096, 'context_read');
    return rp_boot_context($wire, $mode);
}
'''


def require(value,code):
    if not value:raise ValueError(code)


def sha(wire):return hashlib.sha256(wire).hexdigest()


def identity(wire):return {'bytes':len(wire),'sha256':sha(wire)}


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def read(path,maximum=4*1024*1024):
    require(path.resolve(strict=True)==path and path.is_file() and 0<path.stat().st_size<=maximum,'request_fixed_source')
    b=path.read_bytes();require(len(b)<=maximum,'request_fixed_source_grew');return b


def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def modules():return load('rp_request_native_derivation',NATIVE/'prepare.py'),load('rp_request_previous_adapter',OLD/'prepare.py')


def once(wire,old,new):
    require(wire.count(old)==1,'request_exact_derivation_context');return wire.replace(old,new,1)


def section(wire,start,end):
    require(wire.count(start)==wire.count(end)==1,'request_derivation_markers')
    a,b=wire.index(start),wire.index(end);require(a<b,'request_marker_order');return wire[a:b]


def parents():
    result={}
    for name,pin in PINS.items():
        b=read(ROOT/name);require((len(b),sha(b))==pin,'request_parent_pin');result[name]=b
    return result


def expected_fixtures(original):
    adapter=original['experiments/php-wasix-wp-bootstrap-probe/fixtures/adapter.php']
    helper=original['experiments/php-wasix-wp-bootstrap-probe/fixtures/helpers.php']
    helper=once(helper,b'recastpress.php-wasix-wp-bootstrap-context.v0.5',b'recastpress.php-wasix-wp-request-context.v0.5')
    helper=once(helper,b"in_array($mode, ['install', 'read'], true)",b"$mode === 'home'")
    helper=once(helper,b"    if ($mode === 'install') { $keys[] = 'admin_password'; }\n",b'')
    helper=once(helper,b"    if ($mode === 'install') { rp_check($secret($value['admin_password']), 'context_admin_shape'); }\n",b'')
    db=original['experiments/php-wasix-wp-bootstrap-probe/fixtures/db.php'].replace(
        b'/experiments/php-wasix-wp-bootstrap-probe/fixtures/',b'/experiments/php-wasix-wp-request-probe/fixtures/')
    b=original['experiments/wp-request-probe/fixtures/bootstrap.php']
    b=once(b,b'declare(strict_types=1);\n',b"declare(strict_types=1);\nrequire __DIR__ . '/helpers.php';\nconst RP_DROPIN_SHA = '"+sha(db).encode()+b"';\n")
    b=once(b,section(b,b'function rp_boot_input(',b'function rp_boot_inventory('),CGI_INPUT+b'\n')
    b=once(b,b"&& ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET' && ($_SERVER['REQUEST_URI'] ?? '') === '/'\n        && ($_SERVER['QUERY_STRING'] ?? '') === ''",
        b"&& ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET'\n        && ($_SERVER['REQUEST_URI'] ?? '') === '/__rp_request.php?rp_request=1'\n        && ($_SERVER['QUERY_STRING'] ?? '') === 'rp_request=1'")
    b=once(b,b'$actual_files = []; $actual_dirs = []; $total = 0;',b'$actual_files = []; $actual_dirs = []; $total = 0; $overlay_seen = false;')
    b=once(b,b"        if ($entry->isDir()) { $actual_dirs[] = $relative; continue; }\n",
        b"        if ($entry->isDir()) { $actual_dirs[] = $relative; continue; }\n"
        b"        if ($relative === 'wp-content/db.php') {\n"
        b"            rp_boot_check(!$overlay_seen && $entry->isFile() && hash_file('sha256', $path) === RP_DROPIN_SHA, 'owned_dropin_bytes');\n"
        b"            $overlay_seen = true; continue;\n        }\n")
    b=once(b,b"&& $total === 110077999 && !file_exists($site . '/wp-config.php')",b"&& $overlay_seen && $total === 110077999 && !file_exists($site . '/wp-config.php')")
    b=b.replace(b'recastpress.wp-request-native-observation.v0.5',b'recastpress.php-wasix-wp-request-php-observation.v0.5')
    b=b.replace(b'fixed_native_home_passed',b'fixed_wasix_home_passed').replace(b'fixed_native_home_failed',b'fixed_wasix_home_failed')
    b=once(b,b"    header_remove(); http_response_code(200); header('Content-Type: application/json');",
        b"    header_remove(); header('Content-Type: application/json', true, 200);")
    b=once(b,b"'rust_executed' => false, 'wasm_executed' => false",b"'rust_executed' => true, 'wasm_executed' => true")
    b=once(b,b"$rp_secrets = array_merge([$rp_input['password']], array_values($rp_input['salts']));",b"$rp_secrets = array_merge([$rp_input['nonce']], array_values($rp_input['salts']));")
    b=once(b,section(b,b"    $rp_root = realpath(",b"    $rp_report['phase'] = 'source_inventory';"),
        b"    $rp_site = '/wordpress';\n    rp_boot_check(realpath($rp_site) === $rp_site, 'fixed_guest_site');\n    $rp_manifest = '/rp-request/source-manifest.json';\n")
    b=once(b,b"define('DB_NAME', $rp_input['database']); define('DB_USER', $rp_input['user']); define('DB_PASSWORD', $rp_input['password']);\n    define('DB_HOST', '127.0.0.1:' . $rp_input['port']);",
        b"define('DB_NAME', 'rp_unused_guest_db'); define('DB_USER', 'rp_unused_guest_user'); define('DB_PASSWORD', '');\n    define('DB_HOST', 'rp-unused.invalid');")
    b=once(b,b"&& $rp_report['native_mysqli_handle'], 'full_normal_bootstrap_required');",
        b"&& !$rp_report['native_mysqli_handle'] && $wpdb instanceof RpBridgeWpdb\n        && ($rp_report['owned_db_dropin_loaded'] ?? false) === true, 'full_rpc_bootstrap_required');")
    b=once(b,b"if (isset($wpdb) && $wpdb instanceof wpdb && $wpdb->dbh instanceof mysqli)",b"if (isset($wpdb) && $wpdb instanceof RpBridgeWpdb && $wpdb->dbh !== null)")
    b=once(b,b'    restore_error_handler(); rp_boot_emit();',b"    if (isset($wpdb) && $wpdb instanceof RpBridgeWpdb) { $rp_report['rpc_stats'] = $wpdb->rpc_stats(); }\n    restore_error_handler(); rp_boot_emit();")
    b=once(b,b"$rp_report['failure'] = $rp_error instanceof RpBootstrapFailure ? $rp_error->getMessage() : 'php_throwable';\n    $rp_report['failure_class'] = get_class($rp_error);",
        b"$rp_failure = ($rp_error instanceof RpBootstrapFailure || $rp_error instanceof RpNativeFailure) ? $rp_error->getMessage() : 'php_throwable';\n"
        b"    $rp_report['failure'] = in_array($rp_failure, RP_PUBLIC_FAILURE_CODES, true) ? $rp_failure : 'unclassified_failure';\n"
        b"    $rp_class = get_class($rp_error);\n    $rp_report['failure_class'] = in_array($rp_class, RP_PUBLIC_FAILURE_CLASSES, true) ? $rp_class : 'unclassified_exception';")
    codes=set(re.findall(rb",\s*'([a-z][a-z0-9_]*)'\);",b+helper+adapter+db))
    codes.update(re.findall(rb"new Rp(?:Native|Bootstrap)Failure\('([a-z][a-z0-9_]*)'\)",b+helper+adapter+db))
    codes.update([b'php_throwable',b'public_secret_guard',b'public_response_quota',b'unexpected_php_shutdown',b'unclassified_failure'])
    phases=['input','source_inventory','wp_settings','wp_install','wp_dispatch','template_loader','public_observations','source_after','complete']
    classes=['RpBootstrapFailure','RpNativeFailure','JsonException','Error','TypeError','ValueError','ArgumentCountError',
        'RuntimeException','Exception','ParseError','ErrorException','unclassified_exception']
    constants=(b'const RP_PUBLIC_PHASES = '+json.dumps(phases,separators=(',',':')).encode()+b';\n'
        +b'const RP_PUBLIC_FAILURE_CODES = '+json.dumps(sorted(c.decode()for c in codes),separators=(',',':')).encode()+b';\n'
        +b'const RP_PUBLIC_FAILURE_CLASSES = '+json.dumps(classes,separators=(',',':')).encode()+b';\n')
    b=once(b,b"require __DIR__ . '/helpers.php';\n",b"require __DIR__ . '/helpers.php';\n"+constants)
    return {'home.php':original['experiments/wp-request-probe/fixtures/home.php'],'bootstrap.php':b,
        'helpers.php':helper,'adapter.php':adapter,'db.php':db}


def derivation_gate():
    original=parents();expected=expected_fixtures(original)
    require({p.name for p in (HERE/'fixtures').iterdir()}==set(FIXTURES),'request_closed_fixture_set')
    for name,b in expected.items():require(b'\r' not in b and read(HERE/'fixtures'/name)==b,'request_derived_fixture_changed')
    native,_=modules();render=native.RENDER
    require(expected['bootstrap.php'].count(render)==1,'native_dispatch_render_capture_not_exact')
    return {'base_commit':BASE_COMMIT,'original_git_objects_verified_by_preparer':False,
        'parents':{n:identity(b) for n,b in original.items()},'derived':{n:identity(expected[n]) for n in FIXTURES},
        'adapter4096_bytes_unchanged':True,'native_render_block_sha256':sha(render),'native_render_block_bytes_unchanged':True,
        'allowed_changes':['hidden-CGI-entry-then-native-home-request-reset','private-nonce-salts-context-no-DB-credentials',
            'owned-db-prefix-only-overlay-and-core-inventory-exception','dummy-DB-constants-and-Rust-handle',
            'Wasm-schema-context-flags-and-public-diagnostic-allowlists','private-count-only-RPC-statistics',
            'owned-JSON-wrapper-status-via-header-third-argument-after-WP-HTTP-status-line'],
        'html_normalization_applied':False,'core_theme_dispatcher_bytes_modified':False,'owned_db_overlay_files':1}


def input_hashes():
    native,old=modules();result={**native.inputs(),**old.input_hashes()}
    for p in [Path(__file__).resolve(),*[HERE/'fixtures'/n for n in FIXTURES],*[ROOT/n for n in PINS]]:
        result[p.relative_to(ROOT).as_posix()]=sha(read(p))
    return dict(sorted(result.items()))


def metadata():
    before=input_hashes();native,old=modules();core=native.verify_prepared();prior=old.verify_prepared();derivation=derivation_gate()
    files=[]
    for name in FIXTURES:
        path=HERE/'fixtures'/name
        files.append({'source':path.relative_to(ROOT).as_posix(),'guest':'/wordpress/wp-content/db.php' if name=='db.php'
            else '/experiments/php-wasix-wp-request-probe/fixtures/'+name,**identity(read(path)),
            'guest_read_only':True,'kind':'owned_wpdb_dropin' if name=='db.php' else 'derived_fixture'})
    oracles={};responses=[]
    for backend in ['mysql','mariadb']:
        path='experiments/wp-request-probe/evidence/windows-'+backend+'-native.json';wire=read(ROOT/path);v=json.loads(wire)
        require(v['passed'] is True and v['normal_admin_shutdown_verified'] is True and v['old_native_read_baseline_reproduced'] is True,'native_home_accepted_reference')
        responses.append(v['observations']['home']['response']);oracles[backend]={'path':path,**identity(wire)}
    require(canonical(responses[0])==canonical(responses[1]),'native_backend_complete_responses_differ')
    require(responses[0]['status']==200 and responses[0]['body_bytes']==69399
        and responses[0]['body_sha256']=='ef43f26984536cae43c4e703edef98dbaaf2bca79cc972a4373bf27f01cabdd1','native_complete_home_identity')
    require(input_hashes()==before,'request_preparation_source_changed')
    return {'schema':'recastpress.php-wasix-wp-request-prepared.v0.5','outcome':'full_core_and_owned_home_dropin_prepared',
        'input_files':before,'core_site':core['core_site'],'core_manifest':core['core_manifest'],'core_inventory':core['core_inventory'],
        'core_archive':core['archive'],'files':files,'native_oracles':oracles,'derivation':derivation,
        'previous_full_bootstrap_derivation':prior['derivation'],'core_guest_root':'/wordpress',
        'private_context_guest_path':'/rp-request/context.json','core_manifest_guest_path':'/rp-request/source-manifest.json',
        'html_limit_bytes':1048576,'wrapper_limit_bytes':3145728,'guest_context_contains_db_credentials':False,
        'native_reference_binaries_reverified':False,'core_files_modified':False,'secret_context_in_prepared_metadata':False,
        'php_executed':False,'wasm_executed':False,'database_connected':False,'product_runtime_selected':False,
        'guest_readonly_enforcement_executed':False,'html_normalization_applied':False,'inputs_unchanged':True}


def owned(path):
    require(WORK.resolve()==WORK and path.resolve()==path and path.is_relative_to(WORK),'request_owned_metadata');return path


def verify_prepared():
    value=metadata();require(canonical(json.loads(read(owned(PREPARED))))==canonical(value),'request_prepared_changed');return value


def prepare():
    value=metadata();owned(WORK).mkdir(parents=True,exist_ok=True);wire=(json.dumps(value,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode()
    if PREPARED.exists():
        old=read(owned(PREPARED))
        if old!=wire:
            saved=owned(WORK/('prepared-before-'+sha(old)[:16]+'.json'))
            if saved.exists():require(read(saved)==old,'request_old_metadata_changed')
            else:
                with saved.open('xb') as stream:stream.write(old)
    owned(PREPARED).write_bytes(wire);require(verify_prepared()==value,'request_prepared_after_changed');return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    print((verify_prepared() if args.verify_only else prepare())['outcome'])
