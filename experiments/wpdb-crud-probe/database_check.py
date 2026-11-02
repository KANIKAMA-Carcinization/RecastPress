"""Compare standard wpdb CRUD helpers through fresh native and Rust sessions."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import build_check as build
import native_check as native
from owned_database import OwnedDatabase, db

require, sha = db.require, db.sha
CASES = ['fixture_empty', 'insert_explicit_formats', 'update_same', 'update_changed',
    'update_null', 'update_where_null_empty', 'replace_existing', 'insert_default_formats',
    'insert_exhausted_formats', 'delete_missing', 'delete_where_null', 'delete_existing',
    'remaining_row', 'delete_remaining', 'final_empty']
CORE = ['observations', 'query_trace_hex', 'bootstrap_num_queries', 'final_num_queries',
    'session_snapshot', 'server_info_hex']


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()


def exact(actual, expected, reason):
    require(canonical(actual) == canonical(expected), reason)


def validate_php(value, variant, reference):
    require(value['schema'] == 'recastpress.wpdb-crud-php-observation.v0.5'
        and value['outcome'] == 'fixed_crud_cases_passed' and value['variant'] == variant,
        'crud_php_profile')
    for name in ['passed', 'connected', 'constructor_connection_deferred', 'close_completed',
            'included_source_gate_passed']:
        require(value[name] is True, 'crud_php_completion_' + name)
    for name in ['full_wordpress_bootstrap', 'wasm_executed', 'standard_wpdb_compatibility_verified',
            'product_abi_selected', 'product_driver_selected']:
        require(value[name] is False, 'crud_php_unclaimed_' + name)
    require(value['rust_driver_executed'] is (variant == 'bridge')
        and value['native_driver_extension_present'] is (variant == 'native')
        and value['native_mysqli_handle_supplied'] is (variant == 'native')
        and value['suppress_errors_previous'] is False, 'crud_real_driver_profile')
    exact(value['runtime'], {'version':'8.5.12', 'version_id':80512, 'int_size':8, 'sapi':'cli'},
        'crud_actual_php_runtime')
    exact(value['loaded_source_sha256'], reference['wordpress_loaded_source_sha256'], 'crud_original_wp_sources')
    exact(value['included_source_names'], native.included_sources(variant), 'crud_actual_include_set')
    rows = value['observations']
    require(type(rows) is list and len(rows) == 15, 'crud_fifteen_cases_required')
    exact([row['case'] for row in rows], CASES, 'crud_complete_case_order')
    trace, previous = [], value['bootstrap_num_queries']
    require(type(previous) is int and previous == 0, 'crud_bootstrap_query_count')
    for row in rows:
        require(set(row) == {'case','api','arguments','return_type','returned','state','queries_hex'},
            'crud_case_fields')
        queries, state = row['queries_hex'], row['state']
        require(type(queries) is list and type(state['delta']) is int and state['delta'] == len(queries),
            'crud_including_internal_queries')
        require(type(state['num_queries']) is int and state['num_queries'] == previous + state['delta'],
            'crud_query_counter_progress')
        for wire in queries:
            require(type(wire) is str and re.fullmatch('(?:[0-9a-f]{2}){1,4096}', wire) is not None
                and bytes.fromhex(wire).isascii(), 'crud_fixed_ascii_sql')
        trace += queries
        previous = state['num_queries']
    exact(trace, value['query_trace_hex'], 'crud_all_observation_queries')
    require(previous == value['final_num_queries'] == len(trace) == 16, 'crud_actual_sixteen_public_queries')
    schema = [wire for wire in trace if bytes.fromhex(wire).startswith(b'SHOW FULL COLUMNS FROM ')]
    require(len(schema) == 1 and rows[1]['state']['delta'] == 2, 'crud_parent_schema_lookup_executed')
    require({'insert','replace','update','delete'}.issubset({row['api'] for row in rows}),
        'crud_actual_standard_helper_calls')
    snapshot = value['session_snapshot']
    require(snapshot['character_set_connection_hex'] == b'utf8mb4'.hex()
        and snapshot['collation_connection_hex'] == b'utf8mb4_unicode_520_ci'.hex(), 'crud_actual_charset')
    return {'fixed_api_cases':15, 'public_queries':16, 'parent_schema_queries':1,
        'same_corpus_for_both_drivers': True, 'all_public_counter_deltas_accounted':True}


def validate_host(value, nonce):
    require(value['schema'] == 'recastpress.wpdb-crud-observation.v0.5'
        and value['outcome'] == 'fixed_crud_bridge_cases_passed', 'crud_host_profile')
    for key in ['passed','owned_php_reaped','php_exit_success','physical_connection_created',
            'physical_disconnect_completed','capture_workers_joined','php_stdout_eof','php_stderr_eof',
            'private_native_loopback_transport']:
        require(value[key] is True, 'crud_host_completion_' + key)
    for key in ['php_forced_kill','php_stdout_overflow','php_stderr_overflow','credentials_forwarded_to_php',
            'transport_nonce_recorded','physical_connection_pool_used','wasm_networking_enabled',
            'native_php_product_fallback','raw_non_utf8_err_verified','standard_wpdb_compatibility_verified',
            'product_driver_selected','product_abi_selected','database_started_by_probe','ddl_executed_by_probe']:
        require(value[key] is False, 'crud_host_scope_' + key)
    require(value['php_exit_code'] == 0 and value['php_stderr_bytes'] == 0, 'crud_owned_php_exit')
    ops, php = value['operations'], value['php_result']
    require(type(ops) is list and 2 <= len(ops) <= 64, 'crud_rpc_count')
    counts = dict.fromkeys(['hello','query','escape','close'], 0)
    for seq, op in enumerate(ops, 1):
        require(type(op['seq']) is int and op['seq'] == seq and op['op'] in counts
            and type(op['ok']) is bool, 'crud_rpc_sequence')
        counts[op['op']] += 1
        frame = json.dumps({'schema':'recastpress.wpdb-rpc-request.v0.5', 'nonce':nonce, 'seq':seq,
            'op':op['op'], 'data_hex':op['data_hex']}, separators=(',', ':')).encode() + b'\n'
        reply = canonical({'schema':'recastpress.wpdb-rpc-reply.v0.5', 'seq':seq,
            'ok':op['ok'], 'data':op['data']}) + b'\n'
        require(op['request_frame_bytes'] == len(frame) <= 16384 and op['request_frame_sha256'] == sha(frame)
            and op['reply_frame_bytes'] == len(reply) <= 65536 and op['reply_frame_sha256'] == sha(reply),
            'crud_actual_rpc_frames')
    transport = php['rpc_stats']['transport']
    exact(counts, transport['counts'], 'crud_actual_rpc_counts')
    require(counts['hello'] == counts['close'] == 1 and counts['query'] == 20
        and transport['requests'] == len(ops) and transport['transport_closed'] is True, 'crud_channel_closed')
    requested, replied = sum(o['request_frame_bytes'] for o in ops), sum(o['reply_frame_bytes'] for o in ops)
    require(requested == value['request_bytes'] == transport['request_bytes'] <= 128*1024
        and replied == value['reply_bytes'] == transport['reply_bytes'] <= 512*1024, 'crud_rpc_byte_balance')
    setup = php['rpc_stats']['internal_session_setup']
    require(len(setup) == 4 and all(op['ok'] is True for op in ops), 'crud_successful_setup_and_operations')
    queries = [op for op in ops if op['op'] == 'query']
    exact([op['data_hex'] for op in queries], [q['sql_hex'] for q in setup] + php['query_trace_hex'],
        'crud_all_queries_including_schema')
    for op, item in zip(queries, setup): exact(op['data'], item['data'], 'crud_actual_setup_reply')
    require(ops[0]['op'] == 'hello' and ops[-1]['op'] == 'close' and ops[-1]['data']['closed'] is True,
        'crud_actual_hello_close')
    exact(ops[0]['data']['server_version_hex'], php['server_info_hex'], 'crud_actual_server_version')
    require(type(ops[0]['data']['caps']) is int and ops[0]['data']['caps'] & (2|128|2048|65536) == 0,
        'crud_forbidden_capabilities_off')
    return {'actual_rpc_frames_verified':True, 'all_public_and_schema_queries_match':True,
        'operations':len(ops), 'counts':counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql','mariadb'], required=True)
    parser.add_argument('--variant', choices=['native','bridge'], required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    r = {'schema':'recastpress.wpdb-crud-database.v0.5', 'passed':False, 'outcome':'not_run',
        'observed_at_utc':datetime.now(timezone.utc).isoformat(), 'backend':args.backend, 'variant':args.variant,
        'tls_profile':'disabled', 'php_rust_real_database_executed':False, 'native_php_product_fallback':False,
        'wasm_executed':False, 'full_wordpress_bootstrap':False, 'standard_wpdb_compatibility_verified':False,
        'product_driver_selected':False, 'product_abi_selected':False}
    nonce, owned, passed, stage = secrets.token_hex(24), None, False, 'preflight'
    try:
        require(os.name == 'nt', 'crud_fixed_windows_comparison')
        before = r['input_files'] = build.inputs()
        binary = build.TARGET / 'debug/recastpress-wpdb-crud-probe.exe'
        receipt = build.verify_build(binary)
        r['build_receipt_sha256'] = sha(build.read(HERE / 'evidence/windows-build.json', 256*1024))
        r['binary_identity'] = receipt['executable_identity']
        reference = r['native_reference'] = native.references()
        baseline = None
        if args.variant == 'bridge':
            path = HERE / ('evidence/windows-' + args.backend + '-native.json')
            wire = build.read(path, 512*1024)
            baseline = db.parse_json(wire)
            require(baseline['passed'] is True and baseline['variant'] == 'native'
                and baseline['backend'] == args.backend and baseline['normal_admin_shutdown_verified'] is True
                and baseline['database_closed_and_reaped'] is True and baseline['all_command_children_reaped'] is True,
                'crud_fresh_native_baseline_required')
            exact(baseline['input_files'], before, 'crud_fresh_native_inputs')
            r['fresh_native_receipt_sha256'] = sha(wire)
        stage = 'owned_database'
        owned = OwnedDatabase(args.backend, r)
        with owned:
            stage = 'php_execution'
            if args.variant == 'native':
                command, request, timeout = native.command('native'), owned.request('recastpress.wpdb-native-input.v0.5'), 30
            else:
                command, request, timeout = [str(binary)], owned.request('recastpress.wpdb-crud-input.v0.5'), 80
                request['nonce'] = nonce
            result = owned.harness.run('owned_crud_' + args.variant, command, timeout=timeout,
                input_bytes=canonical(request) + b'\n', limit=1024*1024)
            r.update(execution_exit_code=result.returncode, execution_stdout_sha256=sha(result.stdout),
                execution_stderr_sha256=sha(result.stderr))
            require(result.stdout.endswith(b'\n') and result.stdout.count(b'\n') == 1, 'crud_one_observation_required')
            value = r['observation'] = db.parse_json(result.stdout)
            require(result.returncode == 0 and not result.stderr, 'crud_execution_failed')
            if args.variant == 'bridge':
                r['host_validation'] = validate_host(value, nonce)
                r['php_rust_real_database_executed'] = True
                php = value['php_result']
            else: php = value
            r['api_validation'] = validate_php(php, args.variant, reference)
            if baseline is not None:
                for key in CORE: exact(php[key], baseline['observation'][key], 'crud_native_' + key + '_differs')
                r['all_observations_and_queries_match_fresh_native'] = True
            stage = 'final_verification'
            owned.verify_final()
            exact(build.inputs(), before, 'crud_inputs_changed')
            exact(build.verify_build(binary), receipt, 'crud_build_chain_changed')
            exact(native.references(), reference, 'crud_reference_changed')
        require(r['normal_admin_shutdown_verified'] is True and r['all_command_children_reaped'] is True,
            'crud_normal_cleanup_required')
        passed = True
        r['outcome'] = 'fixed_crud_' + args.variant + '_database_passed'
    except (Exception, KeyboardInterrupt) as error:
        r.update(outcome='fixed_crud_database_failed', failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)): r['failure'] = str(error)
    r['passed'] = passed
    wire = (json.dumps(r, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    for secret in [nonce] + ([] if owned is None else [owned.password, owned.admin_password]):
        require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'crud_public_secret_guard')
    require(len(wire) <= 2*1024*1024, 'crud_public_record_quota')
    if args.record:
        path = HERE / ('evidence/windows-' + args.backend + '-' + args.variant + '.json')
        require(path.parent.resolve() == path.parent and path.resolve() == path, 'crud_record_linked')
        path.parent.mkdir(exist_ok=True)
        with path.open('xb') as stream: stream.write(wire)
    print('WPDB CRUD: ' + r['outcome'] + ' (' + args.backend + '/' + args.variant + ')')
    return 0 if passed else 1


if __name__ == '__main__': sys.exit(main())
