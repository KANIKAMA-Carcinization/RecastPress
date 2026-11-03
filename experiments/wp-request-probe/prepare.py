"""Derive an anonymous-home CGI fixture; verify original WP/PHP read-only.

Only owned preparation metadata is written. No PHP, DB, HTTP or Wasm executes.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
NATIVE=ROOT/'experiments/wp-bootstrap-probe'
WORK=ROOT/'target/wp-request-probe'
PREPARED=WORK/'prepared.json'
FIXTURES=['home.php','bootstrap.php']
PINS={
    'experiments/wp-bootstrap-probe/fixtures/bootstrap.php':(15581,'36cb1216727442fb61712b459e2b2fadea81de0c0aadb0a2041351570c826da1'),
    'experiments/wp-bootstrap-probe/fixtures/read.php':(104,'00ff38f879289d63e7e6089755765a859ff6cad3ffe3b300b71e220c3ae23ccc'),
    'experiments/wp-bootstrap-probe/prepare.py':(10184,'df009b467c1c2452c746ff8f4e6f5fa3a71c04795d32efe77e186b2ee8d776bc'),
}
CAPTURE=b'''$rp_secrets = []; $rp_emitted = false; $rp_buffer = ob_get_level();
$rp_html = ''; $rp_html_seen = 0; $rp_html_overflow = false;
// Outer safety buffer stays empty: the chunk callback never releases HTML.
ob_start();
ob_start(static function (string $bytes): string {
    global $rp_html, $rp_html_seen, $rp_html_overflow;
    $rp_html_seen += strlen($bytes);
    if (!$rp_html_overflow && strlen($bytes) <= 1048576 - strlen($rp_html)) {
        $rp_html .= $bytes;
    } else { $rp_html_overflow = true; }
    return '';
}, 4096);
'''
RENDER=b'''    $rp_report['phase'] = 'wp_dispatch';
    rp_boot_check(!is_user_logged_in(), 'anonymous_home_required');
    $rp_report['anonymous_request'] = true;
    wp();
    $rp_report['request_dispatch_completed'] = did_action('wp') >= 1;
    $rp_report['phase'] = 'template_loader';
    require ABSPATH . WPINC . '/template-loader.php';
    $rp_report['template_loader_completed'] = true;
    rp_boot_check(ob_get_level() === $rp_buffer + 2, 'html_capture_buffer_lost');
    ob_flush();
    rp_boot_check(!$rp_html_overflow && strlen($rp_html) === $rp_html_seen
        && $rp_html_seen > 0 && $rp_html_seen <= 1048576, 'complete_bounded_home_html_required');
    $rp_status = http_response_code();
    $rp_headers = headers_list();
    rp_boot_check(is_int($rp_status) && $rp_status === 200 && !headers_sent()
        && $rp_report['request_dispatch_completed'] && did_action('template_redirect') >= 1
        && did_action('wp_head') >= 1 && did_action('wp_footer') >= 1, 'complete_home_dispatch_required');
    $rp_report['response'] = ['status' => $rp_status, 'headers_hex' => array_map('bin2hex', $rp_headers),
        'body_bytes' => strlen($rp_html), 'body_hex' => bin2hex($rp_html), 'body_sha256' => hash('sha256', $rp_html)];
    $rp_report['theme_rendered'] = true;
'''


def require(value,code):
    if not value:raise ValueError(code)


def sha(wire):return hashlib.sha256(wire).hexdigest()


def identity(wire):return {'bytes':len(wire),'sha256':sha(wire)}


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def read(path,maximum=2*1024*1024):
    require(path.resolve(strict=True)==path and path.is_file() and 0<path.stat().st_size<=maximum,'home_fixed_source')
    wire=path.read_bytes();require(len(wire)<=maximum,'home_fixed_source_grew');return wire


def parent():
    spec=importlib.util.spec_from_file_location('rp_request_unchanged_native_sources',NATIVE/'prepare.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def once(wire,old,new):
    require(wire.count(old)==1,'home_exact_derivation_context');return wire.replace(old,new,1)


def originals():
    result={}
    for name,pin in PINS.items():
        wire=read(ROOT/name);require((len(wire),sha(wire))==pin,'home_original_source_pin');result[name]=wire
    return result


def expected_fixtures(original):
    home=once(original['experiments/wp-bootstrap-probe/fixtures/read.php'],b"'read'",b"'home'")
    b=original['experiments/wp-bootstrap-probe/fixtures/bootstrap.php']
    b=once(b,b'$wire = stream_get_contents(STDIN, 16385);',
        b"$rp_stream = fopen('php://stdin', 'rb');\n    rp_boot_check(is_resource($rp_stream), 'private_stdin_open');\n"
        b"    try { $wire = stream_get_contents($rp_stream, 16385); } finally { fclose($rp_stream); }")
    b=once(b,b'recastpress.wp-bootstrap-native-input.v0.5',b'recastpress.wp-request-native-input.v0.5')
    b=b.replace(b'recastpress.wp-bootstrap-native-observation.v0.5',b'recastpress.wp-request-native-observation.v0.5')
    b=b.replace(b'fixed_native_bootstrap_failed',b'fixed_native_home_failed').replace(b'fixed_native_bootstrap_passed',b'fixed_native_home_passed')
    b=once(b,b"PHP_SAPI === 'cli' && ($argc ?? 0) === 1 && in_array($rp_mode, ['install', 'read'], true)",
        b"PHP_SAPI === 'cgi-fcgi' && ($_SERVER['argc'] ?? 0) === 0 && $rp_mode === 'home'\n"
        b"        && ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET' && ($_SERVER['REQUEST_URI'] ?? '') === '/'\n"
        b"        && ($_SERVER['QUERY_STRING'] ?? '') === ''")
    b=once(b,b'$rp_secrets = []; $rp_emitted = false; $rp_buffer = ob_get_level(); ob_start();\n',CAPTURE)
    b=once(b,b"'source_inventory_verified' => false, 'core_files_unchanged' => false,",
        b"'source_inventory_verified' => false, 'core_files_unchanged' => false,\n"
        b"    'request_dispatch_completed' => false, 'template_loader_completed' => false,\n"
        b"    'anonymous_request' => false, 'theme_rendered' => false, 'response' => null,\n"
        b"    'http_listener_exercised' => false, 'html_normalized' => false,")
    b=once(b,b"    $table_prefix = 'rp_';",b"    define('WP_USE_THEMES', true);\n    $_GET = []; $_POST = []; $_COOKIE = [];\n    $table_prefix = 'rp_';")
    b=once(b,b"$_SERVER['PHP_SELF'] = $_SERVER['SCRIPT_NAME']; $_SERVER['REQUEST_URI'] = $_SERVER['SCRIPT_NAME'];",
        b"$_SERVER['PHP_SELF'] = $_SERVER['SCRIPT_NAME']; $_SERVER['REQUEST_URI'] = '/';\n"
        b"    $_SERVER['QUERY_STRING'] = ''; $_SERVER['PATH_INFO'] = '';\n"
        b"    $_SERVER['HTTPS'] = 'off'; $_SERVER['REQUEST_SCHEME'] = 'http';\n"
        b"    unset($_SERVER['PHP_AUTH_USER'], $_SERVER['PHP_AUTH_PW'], $_SERVER['HTTP_AUTHORIZATION']);")
    b=once(b,b"    $rp_report['phase'] = 'public_observations';\n",RENDER+b"    $rp_report['phase'] = 'public_observations';\n")
    b=once(b,b"} catch (Throwable $rp_error) {\n    $rp_report['failure']",
        b"} catch (Throwable $rp_error) {\n    $rp_report['response'] = null;\n    $rp_report['failure']")
    b=once(b,b"    $rp_emitted = true; echo $wire, \"\\n\";",
        b"    if (strlen($wire) > 3145728) {\n"
        b"        $wire = '{\"schema\":\"recastpress.wp-request-native-observation.v0.5\",\"passed\":false,\"failure\":\"public_response_quota\"}';\n"
        b"        $rp_report['passed'] = false;\n    }\n"
        b"    header_remove(); http_response_code(200); header('Content-Type: application/json');\n"
        b"    $rp_emitted = true; echo $wire, \"\\n\";")
    return {'home.php':home,'bootstrap.php':b}


def derivation_gate():
    original=originals();expected=expected_fixtures(original)
    require({p.name for p in (HERE/'fixtures').iterdir()}==set(FIXTURES),'home_closed_fixture_set')
    for name,wire in expected.items():require(b'\r' not in wire and read(HERE/'fixtures'/name)==wire,'home_derived_source_changed')
    return {'parents':{n:identity(w) for n,w in original.items()},'derived':{n:identity(w) for n,w in expected.items()},
        'allowed_changes':['home-mode-and-exact-CGI-input-profile','private-CGI-stdin-bytes',
            'bounded-suppressing-output-buffer','anonymous-WP_USE_THEMES-and-root-request',
            'original-wp-and-template-loader-dispatch-before-public-observations','complete-inner-response-before-JSON-wrapper-reset'],
        'original_wp_settings_dispatcher_template_theme_modified':False,'html_normalization_applied':False}


def inputs():
    paths=[Path(__file__).resolve(),*[HERE/'fixtures'/n for n in FIXTURES],*[ROOT/n for n in PINS]]
    result=parent().inputs()
    result.update({p.relative_to(ROOT).as_posix():sha(read(p)) for p in paths})
    return dict(sorted(result.items()))


def references():return parent().references()


def native_command(mode='home'):
    require(mode=='home','home_native_command_mode')
    php=parent().native.PHP
    return [str(php/'php-cgi.exe'),'-n','-d','extension_dir='+str(php/'ext'),'-d','extension=mysqli',
        '-d','disable_functions=mail','-d','allow_url_fopen=0','-d','register_argc_argv=0',
        '-d','display_errors=0','-d','log_errors=0']


def native_environment(input_bytes):
    require(type(input_bytes) is bytes and 0<len(input_bytes)<=16384,'home_private_input_bytes')
    # Present-but-empty QUERY_STRING disables PHP CGI's CLI option parsing.
    # Omit it entirely; the controller rejects ambient QUERY_STRING, while
    # the fixture observes/sets the same empty query under REQUEST_URI '/'.
    return {'REDIRECT_STATUS':'200','REQUEST_METHOD':'GET','REQUEST_URI':'/','PATH_INFO':'',
        'SCRIPT_FILENAME':str(HERE/'fixtures/home.php'),'SCRIPT_NAME':'/index.php','PHP_SELF':'/index.php',
        'DOCUMENT_ROOT':str(parent().WP_SITE),'CONTENT_LENGTH':str(len(input_bytes)),'CONTENT_TYPE':'application/json',
        'SERVER_PROTOCOL':'HTTP/1.1','GATEWAY_INTERFACE':'CGI/1.1','SERVER_NAME':'rp-fixture.invalid',
        'HTTP_HOST':'rp-fixture.invalid','SERVER_PORT':'80','HTTPS':'off','REQUEST_SCHEME':'http'}


def metadata():
    before=inputs();core=parent().verify_prepared();derivation=derivation_gate()
    require(inputs()==before,'home_sources_changed')
    return {'schema':'recastpress.wp-request-native-prepared.v0.5','outcome':'fixed_anonymous_home_fixture_prepared',
        'input_files':before,'core_site':core['site'],'core_manifest':core['source_manifest'],'core_inventory':core['source_tree'],
        'archive':{'bytes':core['archive_bytes'],'sha256':core['archive_sha256']},'derivation':derivation,
        'html_limit_bytes':1048576,'wrapper_limit_bytes':3145728,'private_input_schema':'recastpress.wp-request-native-input.v0.5',
        'wordpress_source_modified':False,'php_executed':False,'database_connected':False,'wasm_executed':False,
        'http_listener_exercised':False,'inputs_unchanged':True}


def owned(path):
    require(WORK.resolve()==WORK and path.resolve()==path and path.is_relative_to(WORK),'home_owned_metadata_path');return path


def verify_prepared():
    value=metadata();require(canonical(json.loads(read(owned(PREPARED))))==canonical(value),'home_prepared_metadata_changed');return value


def prepare():
    value=metadata();owned(WORK).mkdir(exist_ok=True,parents=True)
    wire=(json.dumps(value,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode()
    if PREPARED.exists():
        old=read(owned(PREPARED))
        if old!=wire:
            saved=owned(WORK/('prepared-before-'+sha(old)[:16]+'.json'))
            if saved.exists():require(read(saved)==old,'home_previous_metadata_changed')
            else:
                with saved.open('xb') as stream:stream.write(old)
    owned(PREPARED).write_bytes(wire);require(verify_prepared()==value,'home_prepared_after_changed');return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    print((verify_prepared() if args.verify_only else prepare())['outcome'])
