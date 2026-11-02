"""Supervise the fixed php-wasm/wpdb/real-DB diagnostic and compare native APIs."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGET = ROOT / 'target/php-wasix-wpdb-probe/cargo-target'
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepared = load('rp_wasm_wpdb_prepared_sources', HERE / 'prepare.py')
driver = load('rp_wasm_wpdb_fixed_driver', ROOT / 'experiments/wpdb-rpc-probe/prepare_driver.py')
managed = load('rp_wasm_wpdb_managed_build_gate', ROOT / 'experiments/php-wasix-managed-access-probe/managed_check.py')
linux = load('rp_wasm_wpdb_owned_linux_container', HERE / 'linux_database.py')
require, sha = driver.require, driver.sha
BASELINES = {'mysql': '9caf9e945e074e158eb085524f3a845f7aa114d29ead954cae0677c196f3cb04',
    'mariadb': 'b0b554bedd299fa063accccb0c58a914fc18fb2d3a07bd598fae7701efa3bcc8'}
CORE = ['observations', 'bootstrap_num_queries', 'final_num_queries', 'capabilities', 'prefix',
    'prepare', 'syntax_error_code', 'syntax_error_state']


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def exact(actual, expected, reason):
    require(canonical(actual) == canonical(expected), reason)


def sources():
    paths = [p for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts
        and (p.suffix in ('.rs', '.php', '.toml') or p.name in
            ('prepare.py', 'run_check.py', 'linux_database.py', 'Cargo.lock', 'container-images.json'))]
    paths += [ROOT / '.github/workflows/php-wasix-wpdb.yml',
        ROOT / 'experiments/php-wasix-host-probe/src/fiber_adapter.rs',
        ROOT / 'experiments/php-wasi-host-probe/src/cgi_response.rs',
        ROOT / 'experiments/wpdb-rpc-probe/prepare_driver.py', ROOT / 'experiments/wpdb-rpc-probe/driver-plan.json',
        ROOT / 'experiments/wpdb-rpc-probe/patches/0001-mysql-async-wpdb-handshake.patch',
        ROOT / 'experiments/binlog-probe/Cargo.lock', ROOT / 'experiments/binlog-probe/Cargo.toml',
        ROOT / 'scripts/owned_probe.py', ROOT / 'scripts/wordpress_reference.py']
    paths += [ROOT / name for name in prepared.input_hashes()]
    paths += [ROOT / name for name in managed.source_hashes()]
    return {p.relative_to(ROOT).as_posix(): sha(driver.read_fixed(p, 4 * 1024 * 1024)) for p in sorted(set(paths))}


def artifact(wire, tests):
    require(len(wire) <= 16 * 1024 * 1024, 'cargo_output_quota')
    found, finished = [], False
    for line in wire.splitlines():
        v = json.loads(line)
        require(not finished, 'cargo_output_after_finish')
        if v.get('reason') == 'build-finished':
            require(v['success'] is True, 'cargo_build_failed')
            finished = True
        if v.get('reason') != 'compiler-artifact' or v.get('target', {}).get('name') != 'recastpress-php-wasix-wpdb-probe': continue
        require(v['target']['kind'] == ['bin'] and v['profile']['test'] is tests, 'host_artifact_kind')
        require(Path(v['manifest_path']).resolve() == HERE / 'Cargo.toml'
            and Path(v['target']['src_path']).resolve() == HERE / 'src/main.rs', 'host_artifact_source')
        exe = Path(v['executable'])
        require(exe.resolve(strict=True) == exe and exe.is_relative_to(TARGET), 'host_artifact_path')
        found.append(exe)
    require(finished and len(found) == 1, 'one_actual_host_executable')
    return found[0]


def build(report):
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(('MYSQL_', 'MARIADB_', 'RP_'))
        and k.upper() not in {'GH_TOKEN', 'GITHUB_TOKEN', 'ACTIONS_RUNTIME_TOKEN', 'RUST_LOG', 'RUST_BACKTRACE'}}
    environment.update(CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='2', CARGO_INCREMENTAL='0')
    profile = ['--release'] if sys.platform == 'linux' else []
    report['build_profile'] = 'release' if profile else 'dev'
    base = ['--manifest-path', str(HERE / 'Cargo.toml'), '--target-dir', str(TARGET), '--locked', '--offline']
    _, unit = owned_probe.run_captured_probe(['cargo', 'test', *profile, '--no-run', *base, '--message-format=json'], environment, timeout=1200)
    require(unit.returncode == 0 and len(unit.stderr) <= 65536, 'host_unit_build_failed')
    test = artifact(unit.stdout, True)
    _, listing = owned_probe.run_captured_probe([str(test), '--list', '--format', 'terse'], environment, timeout=10)
    require(listing.returncode == 0 and not listing.stderr and len(listing.stdout) <= 65536, 'host_unit_listing_failed')
    names = [line[:-6] for line in listing.stdout.decode().splitlines() if line.endswith(': test')]
    require(len(names) >= 10 and len(names) == len(set(names)), 'nonzero_bounded_host_units_required')
    pid, tested = owned_probe.run_captured_probe([str(test), '--test-threads=1', '--show-output'], environment, timeout=60)
    require(tested.returncode == 0 and not tested.stderr and len(tested.stdout) <= 65536, 'host_units_failed')
    output = tested.stdout.decode().replace('\r\n', '\n')
    require(f'test result: ok. {len(names)} passed; 0 failed; 0 ignored;' in output, 'ignored_or_missing_host_units')
    for name in names: require('test ' + name + ' ... ok\n' in output, 'host_unit_not_passed')
    report['units'] = {'tests': names, 'passed': True, 'owned_pid': pid, 'owned_reaped': True,
        'executable_sha256': sha(driver.read_fixed(test, 256 * 1024 * 1024)), 'stdout_sha256': sha(tested.stdout)}
    pid, result = owned_probe.run_captured_probe(['cargo', 'build', *profile, *base, '--message-format=json'], environment, timeout=1200)
    require(result.returncode == 0 and len(result.stderr) <= 65536, 'host_build_failed')
    executable = artifact(result.stdout, False)
    report.update(owned_build_pid=pid, owned_build_reaped=True, executable=executable.relative_to(ROOT).as_posix(),
        executable_identity=driver.identity(driver.read_fixed(executable, 256 * 1024 * 1024)))
    return executable, environment


def reference(backend):
    p = ROOT / ('experiments/wpdb-rpc-probe/evidence/windows-' + backend + '-disabled-native-database.json')
    wire = driver.read_fixed(p, 256 * 1024)
    require(sha(wire) == BASELINES[backend], 'fixed_native_reference_changed')
    value = json.loads(wire)
    require(value['passed'] is True and value['database_closed_and_reaped'] is True, 'fixed_native_reference_failed')
    return value['native_result']


def validate_php(php, backend, mapping):
    old = reference(backend)
    require(php['schema'] == 'recastpress.wpdb-bridge-php-observation.v0.5'
        and php['outcome'] == 'fixed_shared_wpdb_cases_passed' and php['variant'] == 'bridge', 'guest_schema_outcome')
    for key in ['passed', 'connected', 'close_completed', 'constructor_connection_deferred', 'included_source_gate_passed',
            'rust_driver_executed', 'wasm_executed']:
        require(php[key] is True, 'guest_completion_' + key)
    for key in ['full_wordpress_bootstrap', 'database_started_by_fixture', 'ddl_executed_by_fixture',
            'standard_wpdb_compatibility_verified', 'product_driver_selected', 'product_abi_selected',
            'native_tls_verified', 'raw_non_utf8_err_verified']:
        require(php[key] is False, 'guest_unclaimed_' + key)
    exact(php['runtime'], {'version': '8.5.12', 'version_id': 80512, 'int_size': 8, 'sapi': 'cgi-fcgi'}, 'actual_guest_runtime')
    require(php['suppress_errors_previous'] is False and php['tls_profile'] == 'disabled', 'guest_fixed_profile')
    require(php['native_driver_extension_present'] is True and php['native_mysqli_handle_supplied'] is False,
        'guest_builtin_extension_and_rust_owned_handle')
    for key in CORE: exact(php[key], old[key], 'native_api_' + key + '_differs')
    exact(php['included_source_names'], sorted(item['guest'].lstrip('/') for item in mapping['files']), 'guest_actual_include_set')
    require(len(php['observations']) == 27 and php['bootstrap_num_queries'] == 0 and php['final_num_queries'] == 25,
        'guest_api_and_query_counts')
    snapshot = php['session_snapshot']
    require(snapshot['character_set_connection_hex'] == b'utf8mb4'.hex()
        and snapshot['collation_connection_hex'] == b'utf8mb4_unicode_520_ci'.hex(), 'guest_actual_charset')
    require(type(snapshot['sql_mode_hex']) is str and re.fullmatch('(?:[0-9a-f]{2})*', snapshot['sql_mode_hex']) is not None,
        'guest_actual_sql_mode')
    return {'api_cases': 27, 'all_observations_match_fixed_native': True,
        'same_job_native_php_executed': False, 'native_reference_platform': 'Windows x86_64',
        'raw_server_info_compared_across_os_images': False, 'raw_server_info_hex': php['server_info_hex']}


def parse_complete_cgi(wire):
    require(type(wire) is bytes and 0 < len(wire) <= 65536, 'cgi_wire_quota')
    delimiter = b'\r\n\r\n' if b'\r\n\r\n' in wire else b'\n\n'
    require(delimiter in wire, 'cgi_header_terminator')
    head, body = wire.split(delimiter, 1)
    require(len(head) <= 16384, 'cgi_header_quota')
    rows = head.split(b'\r\n' if delimiter.startswith(b'\r') else b'\n')
    require(len(rows) <= 64, 'cgi_header_count')
    fields = []
    for row in rows:
        require(row and row[:1] not in (b' ', b'\t') and b':' in row, 'cgi_folded_or_missing_header')
        name, value = row.split(b':', 1)
        require(re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is not None, 'cgi_header_name')
        value = value.strip(b' \t')
        require(all(c == 9 or 32 <= c != 127 for c in value), 'cgi_header_control')
        name = name.decode('ascii').lower()
        require(name not in {'connection','keep-alive','proxy-connection','te','trailer','transfer-encoding','upgrade'}, 'cgi_hop_header')
        fields.append((name, value))
    statuses = [v for n,v in fields if n == 'status']
    lengths = [v for n,v in fields if n == 'content-length']
    require(len(statuses) <= 1 and len(lengths) <= 1, 'cgi_duplicate_control_header')
    require(not statuses or re.fullmatch(rb'200(?: [^\r\n]*)?', statuses[0]) is not None, 'cgi_expected_success_status')
    require(not lengths or lengths[0].isdigit() and int(lengths[0]) == len(body), 'cgi_complete_content_length')
    content = [v for n,v in fields if n == 'content-type']
    require(content == [b'application/json'], 'cgi_expected_content_type')
    return {'status':200, 'headers':[{'name':n,'value_hex':v.hex()} for n,v in fields if n not in ('status','content-length')],
        'body_bytes':len(body),'body_hex':body.hex(),'body_sha256':sha(body),'representation_length':None}


def validate_host(value, nonce):
    require(value['schema'] == 'recastpress.php-wasix-wpdb-observation.v0.5'
        and value['outcome'] == 'fixed_wasix_wpdb_cases_passed'
        and value['phase'] == 'wpdb_cgi_contract_verified' and value['error'] is None, 'wasm_host_outcome')
    for key in ['passed', 'physical_connection_created', 'physical_disconnect_completed', 'db_actor_joined',
            'db_actor_exited', 'fresh_runtime', 'source_rechecked_after_execution']:
        require(value[key] is True, 'wasm_host_completion_' + key)
    for key in ['guest_networking', 'host_environment_forwarded', 'db_credentials_supplied_to_guest',
            'native_php_fallback', 'product_runtime', 'full_wordpress_bootstrap',
            'standard_wpdb_compatibility_proven', 'managed_permission_query_exercised']:
        require(value[key] is False, 'wasm_host_unclaimed_' + key)
    require(type(value['module_compilations']) is int and value['module_compilations'] == 1
        and value['db_actor_failure'] is None, 'one_module_and_clean_actor')
    capture = value['cgi_capture']
    for key in ['guest_exit_success', 'runtime_shutdown_returned', 'stdout_eof', 'stderr_eof']:
        require(capture[key] is True, 'cgi_capture_' + key)
    require(capture['stdout_overflow'] is False and capture['stderr_overflow'] is False
        and capture['stderr_hex'] == '' and capture['stderr_bytes'] == 0
        and capture['stderr_sha256'] == sha(b''), 'cgi_capture_no_error_or_overflow')
    stdout = bytes.fromhex(capture['stdout_hex'])
    require(len(stdout) == capture['stdout_bytes'] <= 65536 and sha(stdout) == capture['stdout_sha256'], 'cgi_actual_stdout')
    response = capture['response']
    exact(parse_complete_cgi(stdout), response, 'cgi_actual_stdout_response_binding')
    body = bytes.fromhex(response['body_hex'])
    require(response['status'] == 200 and len(body) == response['body_bytes'] and sha(body) == response['body_sha256'], 'cgi_complete_body')
    exact(json.loads(body), value['php_result'], 'cgi_body_is_actual_php_result')
    rpc = value['rpc']
    for key in ['finished', 'opened_once', 'close_dispatched']:
        require(rpc[key] is True, 'private_rpc_' + key)
    require(rpc['failure'] is None and rpc['channel_open'] is False and rpc['response_pending'] is False
        and rpc['pending_bytes'] == rpc['unread_response_bytes'] == 0, 'private_rpc_sealed')
    operations = value['operations']
    require(type(operations) is list and len(operations) == 34 and rpc['operations'] == 34, 'rpc_complete_operation_count')
    counts = dict.fromkeys(['hello', 'query', 'escape', 'close'], 0)
    for seq, op in enumerate(operations, 1):
        require(type(op['seq']) is int and op['seq'] == seq and op['op'] in counts, 'rpc_actual_sequence')
        counts[op['op']] += 1
        frame = json.dumps({'schema':'recastpress.wpdb-rpc-request.v0.5','nonce':nonce,'seq':seq,
            'op':op['op'],'data_hex':op['data_hex']}, separators=(',', ':')).encode() + b'\n'
        reply = canonical({'schema':'recastpress.wpdb-rpc-reply.v0.5','seq':seq,'ok':op['ok'],'data':op['data']}) + b'\n'
        require(op['request_frame_bytes'] == len(frame) <= 16384 and op['request_frame_sha256'] == sha(frame)
            and op['reply_frame_bytes'] == len(reply) <= 65536 and op['reply_frame_sha256'] == sha(reply), 'rpc_actual_frame_identity')
    transport = value['php_result']['rpc_stats']['transport']
    exact(counts, transport['counts'], 'rpc_host_guest_counts')
    exact(counts, {'hello':1,'query':29,'escape':3,'close':1}, 'rpc_expected_operation_counts')
    require(transport['transport_closed'] is True and transport['requests'] == 34, 'guest_channel_closed')
    requested = sum(op['request_frame_bytes'] for op in operations)
    replied = sum(op['reply_frame_bytes'] for op in operations)
    require(requested == rpc['request_bytes'] == transport['request_bytes'] <= 128*1024
        and replied == rpc['response_bytes'] == rpc['response_read_bytes'] == transport['reply_bytes'] <= 512*1024,
        'rpc_actual_byte_balance')
    php = value['php_result']
    setup = php['rpc_stats']['internal_session_setup']
    query_hex = [item['sql_hex'] for item in setup] + [item['query_hex'] for item in php['observations'] if item['state']['delta'] == 1]
    queries = [op for op in operations if op['op'] == 'query']
    exact([op['data_hex'] for op in queries], query_hex, 'rpc_all_queries_equal_php_calls')
    require(len(setup) == 4 and operations[0]['op'] == 'hello' and operations[-1]['op'] == 'close'
        and operations[-1]['data']['closed'] is True, 'rpc_hello_setup_close')
    for op, item in zip(queries, setup): exact(op['data'], item['data'], 'rpc_actual_setup_response')
    exact(operations[0]['data']['server_version_hex'], php['server_info_hex'], 'actual_raw_server_info')
    require(operations[0]['data']['caps'] & (2|128|2048|65536) == 0, 'actual_forbidden_capabilities_off')
    return {'private_file_channel_verified': True, 'stdout_used_for_dispatch': False,
        'actual_frames_and_all_public_queries_verified': True, 'operations':34,'bootstrap_queries':4,'public_queries':25}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'], default='mysql')
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    r = {'schema': 'recastpress.php-wasix-wpdb-check.v0.5', 'outcome': 'not_run', 'passed': False,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': sys.platform,
        'backend': args.backend, 'build_only': args.build_only, 'owned_cleanup_completed': False,
        'product_runtime_selected': False, 'product_abi_selected': False, 'standard_wpdb_compatibility_verified': False,
        'memory_cpu_limits_verified_for_this_integration': False, 'all_resources_immediately_reclaimed': False}
    passed, stage, database = False, 'source_preparation', None
    try:
        mapping = prepared.verify_prepared()
        before = r['input_files'] = sources()
        r['prepared'] = mapping
        r['driver_before'] = driver.prepare()
        executable, environment = build(r)
        if not args.build_only:
            stage = 'same_job_module_gate'
            r['module_provenance'] = managed.verify_build()
            database = linux.Database(args.backend, r)
            with database:
                stage = 'php_wasm_execution'
                pid, result = owned_probe.run_captured_probe([str(executable)], environment, timeout=180,
                    input_bytes=canonical(database.input()))
                r.update(owned_host_pid=pid, owned_host_reaped=True, host_exit_code=result.returncode,
                    host_stdout_sha256=sha(result.stdout), host_stderr_sha256=sha(result.stderr))
                require(len(result.stdout) <= 1024 * 1024 and len(result.stderr) <= 65536 and result.stdout.endswith(b'\n')
                    and result.stdout.count(b'\n') == 1, 'host_completed_output_required')
                value = r['observation'] = json.loads(result.stdout)
                require(result.stderr == b'WPDB_WASIX_PHASE=module_compile\nWPDB_WASIX_PHASE=cgi_execution\n', 'host_fixed_phase_markers')
                require(result.returncode == 0 and value['passed'] is True, 'host_wasm_or_rpc_failed')
                r['rpc_validation'] = validate_host(value, database.nonce)
                r['api_validation'] = validate_php(value['php_result'], args.backend, mapping)
                database.final_rows()
            require(r['container_stopped'] and r['container_removed'] and r['container_normal_exit_verified'], 'owned_container_cleanup_required')
        stage = 'after_verification'
        exact(sources(), before, 'compile_or_helper_sources_changed')
        exact(prepared.verify_prepared(), mapping, 'prepared_guest_sources_changed')
        r['driver_after'] = driver.prepare()
        exact(r['driver_before'], r['driver_after'], 'staged_driver_changed')
        require(driver.identity(driver.read_fixed(executable, 256 * 1024 * 1024)) == r['executable_identity'], 'host_binary_changed')
        passed = True
        r['outcome'] = 'fixed_host_build_passed' if args.build_only else 'fixed_wasm_wpdb_database_passed'
    except (Exception, KeyboardInterrupt) as error:
        r.update(outcome='fixed_wasm_wpdb_check_failed', failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)): r['failure'] = str(error)
    finally:
        try:
            owned_probe.reap_owned_probes()
            r['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt):
            passed = False
            r['outcome'] = 'owned_host_cleanup_failed'
    r['passed'] = passed
    wire = (json.dumps(r, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    if database is not None:
        for secret in [database.secret, database.admin_secret, database.nonce]:
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'public_secret_guard')
    require(len(wire) <= 2 * 1024 * 1024, 'public_metadata_quota')
    if args.record:
        label = sys.platform + ('-build' if args.build_only else '-' + args.backend)
        p = HERE / ('evidence/' + label + '.json')
        p.parent.mkdir(exist_ok=True)
        require(p.resolve() == p, 'evidence_path_linked')
        p.write_bytes(wire)
    print('PHP WASIX wpdb: ' + r['outcome'])
    return 0 if passed else 1


if __name__ == '__main__': sys.exit(main())
