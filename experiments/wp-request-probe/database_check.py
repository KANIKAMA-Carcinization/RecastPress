"""Native CGI home observation on one freshly owned pinned Windows database."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BODY_LIMIT = 1024*1024
CAPTURE_LIMIT = 3*1024*1024
BASELINES = {'mysql':'61c331e5e9f316dd6e2f241a54f1de337bbff397d405c6a5f4ae55cc49dc2c91',
    'mariadb':'af07c9ff931f87b2f8d235423fcf5db0101ffc1402904b899c6294d9c6f35f2f'}
BASELINE_FIELDS = ['public_options','public_posts','known_wp_table_count',
    'autoload_option_count','autoload_max_value_bytes']


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


owner = load('rp_native_home_owner', HERE/'owned_database.py')
db = owner.db
original_prepare = load('rp_home_original_preparation', ROOT/'experiments/wp-bootstrap-probe/prepare.py')


def original_controller():
    # Frozen controller used unqualified imports. Resolve those imports to its
    # original peers only, without polluting the new controller's module graph.
    sentinel = object()
    saved = {key:sys.modules.get(key, sentinel) for key in ('prepare','owned_database')}
    paths = sys.path[:]
    try:
        sys.modules['prepare'] = original_prepare
        sys.modules['owned_database'] = owner.base
        return load('rp_home_original_controller', ROOT/'experiments/wp-bootstrap-probe/database_check.py')
    finally:
        sys.path[:] = paths
        for key, value in saved.items():
            if value is sentinel:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


original = original_controller()


def new_preparer():
    return load('rp_native_home_preparation', HERE/'prepare.py')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def exact(actual, expected, reason):
    db.require(canonical(actual) == canonical(expected), reason)


def inputs():
    result = original.inputs()
    prepared = new_preparer()
    result.update(prepared.inputs())
    paths = [p for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts
        and p.suffix in ('.py','.php') and p.name != 'import_evidence.py']
    paths += [ROOT/('experiments/wp-bootstrap-probe/evidence/windows-'+backend+'-native.json')
        for backend in BASELINES]
    paths += [ROOT/name for name in ['experiments/wpdb-rpc-probe/driver-plan.json',
        'experiments/wpdb-rpc-probe/patches/0001-mysql-async-wpdb-handshake.patch',
        'experiments/binlog-probe/Cargo.toml','experiments/binlog-probe/Cargo.lock']]
    for path in sorted(set(paths)):
        db.require(path.resolve(strict=True) == path and path.is_file()
            and path.stat().st_size <= 2*1024*1024, 'home_fixed_input')
        result[path.relative_to(ROOT).as_posix()] = db.sha(path.read_bytes())
    return dict(sorted(result.items()))


def baseline(backend):
    path = ROOT/('experiments/wp-bootstrap-probe/evidence/windows-'+backend+'-native.json')
    wire = db.fixed(path, 256*1024).read_bytes()
    db.require(db.sha(wire) == BASELINES[backend], 'home_original_baseline_changed')
    value = db.parse_json(wire)
    db.require(value['passed'] is True and value['full_wordpress_bootstrap_verified'] is True
        and value['normal_admin_shutdown_verified'] is True and value['all_command_children_reaped'] is True,
        'home_original_baseline_failed')
    return value


def parse_cgi(wire):
    """Parse the bounded native CGI envelope while retaining its original bytes."""
    db.require(type(wire) is bytes and 0 < len(wire) <= CAPTURE_LIMIT, 'home_outer_capture_quota')
    header, sep, body = wire.partition(b'\r\n\r\n')
    db.require(sep and len(header)+4 <= 16384 and b'\n' not in header.replace(b'\r\n', b''),
        'home_outer_complete_cgi_header')
    lines = header.split(b'\r\n')
    db.require(0 < len(lines) <= 64, 'home_outer_header_count')
    fields, status, content_type, content_length = [], None, None, None
    for line in lines:
        db.require(line and not line.startswith((b' ',b'\t')) and b':' in line, 'home_outer_header_line')
        name, raw_value = line.split(b':',1)
        db.require(re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+",name)
            and all(b == 9 or 32 <= b <= 126 for b in raw_value), 'home_outer_header_bytes')
        key, value = name.lower(), raw_value.strip(b' \t')
        db.require(key not in (b'transfer-encoding',b'connection',b'upgrade',b'trailer',b'keep-alive'),
            'home_outer_forbidden_hop_header')
        if key == b'status':
            db.require(status is None and re.fullmatch(rb'[1-5][0-9]{2}(?: [ -~]+)?',value),
                'home_outer_status_shape')
            status = int(value[:3])
        elif key == b'content-type':
            db.require(content_type is None, 'home_outer_duplicate_content_type')
            content_type = value
        elif key == b'content-length':
            db.require(content_length is None and re.fullmatch(rb'[0-9]{1,9}',value),
                'home_outer_content_length')
            content_length = int(value)
        fields.append({'name_hex':name.hex(),'value_hex':raw_value.hex(),'line_hex':line.hex()})
    db.require(status in (None,200) and content_type is not None
        and re.fullmatch(rb'application/json(?:;\s*charset=(?:UTF-8|utf-8))?',content_type),
        'home_outer_json_response')
    db.require(content_length is None or content_length == len(body), 'home_outer_body_length')
    db.require(body.endswith(b'\n') and body.count(b'\n') == 1, 'home_one_public_json_observation')
    value = db.parse_json(body)
    db.require(type(value) is dict, 'home_outer_json_object')
    return value, {'status':200 if status is None else status,'headers':fields,
        'header_bytes':len(header)+4,'raw_header_sha256':db.sha(header+sep),
        'body_bytes':len(body),'body_sha256':db.sha(body),
        'complete_stdout_bytes':len(wire),'complete_stdout_sha256':db.sha(wire),
        'header_or_body_normalized':False}


def validate_home(value, mapping):
    db.require(value['schema'] == 'recastpress.wp-request-native-observation.v0.5'
        and value['mode'] == 'home' and value['passed'] is True
        and value['outcome'] == 'fixed_native_home_passed' and value['phase'] == 'complete',
        'home_actual_observation')
    for key in ['bootstrap_completed','shortinit_false','native_mysqli_handle','source_inventory_verified',
        'core_files_unchanged','physical_disconnect_completed','request_dispatch_completed',
        'template_loader_completed','anonymous_request','theme_rendered']:
        db.require(value[key] is True, 'home_completed_'+key)
    db.require(value['wp_version'] == '7.1.3' and value['wp_installing'] is False
        and value['install_completed'] is False and value['known_wp_table_count'] == 12
        and type(value['warning_count']) is int and value['warning_count'] == 0, 'home_full_original_wp')
    for key in ['rust_executed','wasm_executed','product_runtime_selected','native_php_product_fallback',
        'site_config_file_created','source_core_theme_plugin_modified','outbound_mail_or_http_sent_by_fixture',
        'http_listener_exercised','html_normalized']:
        db.require(value[key] is False, 'home_unclaimed_'+key)
    tree = mapping['core_inventory']
    expected_tree = {'files':tree['files'],'directories':tree['directories'],'bytes':tree['expanded_bytes'],
        'file_inventory_sha256':tree['file_inventory_sha256'],
        'manifest_sha256':mapping['core_manifest']['sha256']}
    exact(value['source_before'],expected_tree,'home_original_source_inventory_before')
    exact(value['source_after'],expected_tree,'home_original_source_inventory_after')
    for key in ['blocked_mail_calls','blocked_http_calls','autoload_option_count','autoload_max_value_bytes']:
        db.require(type(value[key]) is int and value[key] >= 0, 'home_numeric_observation')
    exact(value['public_options'], {'blogname':'RP Bootstrap Fixture','siteurl':'http://rp-fixture.invalid',
        'home':'http://rp-fixture.invalid'}, 'home_public_options')
    response = value['response']
    db.require(set(response) == {'status','headers_hex','body_bytes','body_hex','body_sha256'}
        and type(response['status']) is int and response['status'] == 200, 'home_inner_response_shape')
    db.require(type(response['body_hex']) is str and len(response['body_hex']) <= 2*BODY_LIMIT
        and re.fullmatch('[0-9a-f]*',response['body_hex']) is not None, 'home_original_html_hex')
    body = bytes.fromhex(response['body_hex'])
    db.require(0 < len(body) == response['body_bytes'] <= BODY_LIMIT
        and db.sha(body) == response['body_sha256'], 'home_original_html_identity')
    db.require(b'<html' in body.lower() and b'</html>' in body.lower(), 'home_complete_theme_html')
    headers = response['headers_hex']
    db.require(type(headers) is list and len(headers) <= 64, 'home_actual_header_count')
    decoded = []
    for line in headers:
        db.require(type(line) is str and len(line) <= 16384 and re.fullmatch('[0-9a-f]*',line) is not None,
            'home_actual_header_hex')
        raw = bytes.fromhex(line)
        db.require(raw and b':' in raw and not any(b < 32 and b != 9 or b == 127 for b in raw),
            'home_actual_header_bytes')
        decoded.append(raw)
    db.require(sum(len(x)+2 for x in decoded) <= 16384 and any(re.match(rb'(?i)content-type:\s*text/html(?:;|$)',x)
        for x in decoded), 'home_actual_html_content_type')
    return {'full_native_cgi_home_observed':True,'body_and_headers_preserved_without_normalization':True,
        'inner_status':response['status'],'inner_body_bytes':len(body),
        'inner_body_sha256':db.sha(body),'inner_header_count':len(headers),
        'http_listener_exercised':False,'cross_backend_body_identity_required':False}


def source_snapshot(prepared):
    return {'inputs':inputs(),'prepared':prepared.verify_prepared(),
        'original_prepared':original_prepare.verify_prepared(),'native':prepared.references()}


def cgi_environment(prepared, payload):
    # The old harness inherits the native process environment. Override ambient
    # CGI variables as well as the fixed entry fields, avoiding cookies/auth/query drift.
    extra = {key:'' for key in os.environ if key.upper().startswith(
        ('HTTP_','CONTENT_','REQUEST_','SCRIPT_','SERVER_','REDIRECT_','GATEWAY_','PHP_','RP_'))}
    extra.update(prepared.native_environment(payload))
    db.require(not any(key.upper() == 'QUERY_STRING' for key in os.environ)
        and not any(key.upper() == 'QUERY_STRING' for key in extra), 'home_query_string_must_be_absent')
    db.require(extra['CONTENT_LENGTH'] == str(len(payload)) and extra['REQUEST_METHOD'] == 'GET'
        and extra['REQUEST_URI'] == '/'
        and extra['REDIRECT_STATUS'] == '200', 'home_fixed_cgi_environment')
    return extra


def preserve(path, wire):
    db.require(path.resolve() == path and path.parent.resolve() == path.parent, 'home_record_linked')
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        old = db.fixed(path,8*1024*1024).read_bytes()
        saved = path.with_name(path.stem+'-before-'+db.sha(old)[:16]+path.suffix)
        if saved.exists():
            db.equal(db.fixed(saved,8*1024*1024).read_bytes(),old,'home_old_record_changed')
        else:
            with saved.open('xb') as stream: stream.write(old)
    path.write_bytes(wire)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['mysql','mariadb'],required=True)
    parser.add_argument('--record',action='store_true')
    args = parser.parse_args()
    report = {'schema':'recastpress.wp-request-native-database.v0.5','passed':False,'outcome':'not_run',
        'observed_at_utc':datetime.now(timezone.utc).isoformat(),'backend':args.backend,
        'native_comparison_only':True,'wasm_executed':False,'rust_db_bridge_executed':False,
        'http_listener_exercised':False,'standard_wpdb_compatibility_verified':False,
        'product_runtime_selected':False,'external_messages_authorized_or_sent_by_controller':False,
        'home_response_body_normalized':False,'observations':{},'source_reverified_after':False}
    passed, owned, before, prepared, stage = False, None, None, None, 'preflight'
    try:
        db.require(os.name == 'nt','home_fixed_native_windows')
        db.require(not any(key.upper() == 'QUERY_STRING' for key in os.environ),
            'home_query_string_must_be_absent')
        prepared = new_preparer()
        before = source_snapshot(prepared)
        report.update(input_files=before['inputs'],preparation_before=before['prepared'],
            native_reference=before['native'])
        fixed = baseline(args.backend)
        report['original_baseline'] = {'path':'experiments/wp-bootstrap-probe/evidence/windows-'+args.backend+'-native.json',
            'sha256':BASELINES[args.backend]}
        stage = 'owned_database'
        owned = owner.OwnedDatabase(args.backend,report)
        with owned:
            for mode in ['install','read']:
                stage = 'original_native_'+mode
                result = owned.harness.run(stage,original_prepare.native_command(mode),
                    timeout=180 if mode == 'install' else 120,
                    input_bytes=canonical(owned.request(mode))+b'\n',limit=512*1024)
                report[mode+'_execution'] = {'exit_code':result.returncode,'stdout_bytes':len(result.stdout),
                    'stderr_bytes':len(result.stderr),'stdout_sha256':db.sha(result.stdout),'stderr_sha256':db.sha(result.stderr)}
                db.require(result.stdout.endswith(b'\n') and result.stdout.count(b'\n') == 1,'home_original_one_json')
                value = report['observations'][mode] = db.parse_json(result.stdout)
                db.require(result.returncode == 0 and not result.stderr,'home_original_execution_failed')
                original.validate(value,mode)
                for key in BASELINE_FIELDS:
                    exact(value[key],fixed['observations'][mode][key],'home_original_baseline_'+key)
                exact(source_snapshot(prepared),before,'home_sources_after_original_phase')
            report['old_native_read_baseline_reproduced'] = True
            stage = 'public_fixture_seed'
            owned.capture_private_password_guard()
            stats = owned.autoload_summary('before_home')
            db.equal(stats['option_count'],report['observations']['read']['autoload_option_count'],'home_autoload_before_count')
            db.equal(stats['maximum_value_bytes'],report['observations']['read']['autoload_max_value_bytes'],'home_autoload_before_max')
            owned.seed_public_dates()
            stage = 'native_cgi_home'
            payload = canonical(owned.request('home'))+b'\n'
            result = owned.harness.run(stage,prepared.native_command('home'),timeout=180,
                input_bytes=payload,extra_env=cgi_environment(prepared,payload),limit=CAPTURE_LIMIT)
            report['home_execution'] = {'exit_code':result.returncode,'stdout_bytes':len(result.stdout),
                'stderr_bytes':len(result.stderr),'stdout_sha256':db.sha(result.stdout),'stderr_sha256':db.sha(result.stderr)}
            value, report['outer_cgi_response'] = parse_cgi(result.stdout)
            report['observations']['home'] = value
            db.require(result.returncode == 0 and not result.stderr,'home_native_cgi_execution_failed')
            report['validation'] = validate_home(value,before['prepared'])
            for key in ['public_options','public_posts','known_wp_table_count']:
                exact(value[key],report['observations']['read'][key],'home_read_public_'+key)
            stage = 'independent_final_verification'
            owned.verify_final()
            stats = owned.autoload_summary('after_home')
            db.equal(stats['option_count'],value['autoload_option_count'],'home_autoload_after_count')
            db.equal(stats['maximum_value_bytes'],value['autoload_max_value_bytes'],'home_autoload_after_max')
            exact(source_snapshot(prepared),before,'home_sources_after_request')
        db.require(report['normal_admin_shutdown_verified'] is True and report['database_closed_and_reaped'] is True
            and report['all_command_children_reaped'] is True,'home_normal_cleanup_required')
        passed = True
    except (Exception,KeyboardInterrupt) as error:
        report.update(failure_stage=stage,failure_class=type(error).__name__)
        if isinstance(error,ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}',str(error)):
            report['failure'] = str(error)
    finally:
        if owned is not None:
            try:
                owned.close()
                report['owned_cleanup_completed'] = owned._fully_closed
            except (Exception,KeyboardInterrupt):
                passed = False
                report['cleanup_failed'] = True
        if before is not None and prepared is not None:
            try:
                after = source_snapshot(prepared)
                exact(after,before,'home_sources_changed_after_cleanup')
                report['preparation_after'] = after['prepared']
                report['native_reference_after'] = after['native']
                report['source_reverified_after'] = True
            except (Exception,KeyboardInterrupt) as error:
                passed = False
                report['after_failure_class'] = type(error).__name__
    report['passed'] = passed
    report['outcome'] = 'fixed_native_home_request_passed' if passed else 'native_home_request_failed'
    wire = (json.dumps(report,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode()
    if owned is not None:
        for secret in owned.secrets():
            db.require(secret.encode() not in wire and secret.encode().hex().encode() not in wire,'home_public_secret_guard')
    db.require(len(wire) <= 8*1024*1024,'home_public_record_quota')
    if args.record:
        preserve(HERE/('evidence/windows-'+args.backend+'-native.json'),wire)
    print('WP native home: '+report['outcome']+' ('+args.backend+')')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
