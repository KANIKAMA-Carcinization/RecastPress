"""Compare the unchanged 27-case wpdb corpus through native and Rust DB paths.

Each invocation owns a fresh DB. Bridge mode requires this helper's fresh
native baseline, and checks all API observations against the frozen prior
native baseline too. No Wasm/product compatibility is inferred from this test.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import secrets
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import build_check as build
from owned_database import OwnedDatabase, db

require, sha = build.require, build.sha
BASELINES = {'mysql': '9caf9e945e074e158eb085524f3a845f7aa114d29ead954cae0677c196f3cb04',
    'mariadb': 'b0b554bedd299fa063accccb0c58a914fc18fb2d3a07bd598fae7701efa3bcc8'}
CORE = ['observations', 'bootstrap_num_queries', 'final_num_queries', 'server_info_hex',
    'capabilities', 'prefix', 'prepare', 'syntax_error_code', 'syntax_error_state']


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()


def exact(actual, expected, reason):
    require(canonical(actual) == canonical(expected), reason)


def frozen_native(backend):
    path = build.previous / ('evidence/windows-' + backend + '-disabled-native-database.json')
    wire = build.read(path, 256 * 1024)
    require(sha(wire) == BASELINES[backend], 'frozen_native_baseline_changed')
    record = db.parse_json(wire)
    require(record['passed'] is True and record.get('normal_shutdown_failed') is not True, 'frozen_native_baseline_failed')
    require(record['database_closed_and_reaped'] is True and record['all_command_children_reaped'] is True,
        'frozen_native_cleanup_failed')
    return record['native_result']


def validate_php(value, variant, reference, old):
    exact(value['schema'], 'recastpress.wpdb-bridge-php-observation.v0.5', 'php_schema')
    exact(value['variant'], variant, 'php_variant')
    exact(value['outcome'], 'fixed_shared_wpdb_cases_passed', 'php_outcome')
    require(value['suppress_errors_previous'] is False and value['tls_profile'] == 'disabled', 'php_fixed_entry_profile')
    for key in ['passed', 'connected', 'constructor_connection_deferred', 'close_completed', 'included_source_gate_passed']:
        require(value[key] is True, 'php_completion_' + key)
    for key in ['full_wordpress_bootstrap', 'database_started_by_fixture', 'ddl_executed_by_fixture',
            'standard_wpdb_compatibility_verified', 'product_driver_selected', 'product_abi_selected',
            'wasm_executed', 'native_tls_verified', 'raw_non_utf8_err_verified']:
        require(value[key] is False, 'php_scope_' + key)
    require(value['rust_driver_executed'] is (variant == 'bridge'), 'php_driver_variant')
    exact(value['runtime'], {'version': '8.5.12', 'version_id': 80512, 'int_size': 8, 'sapi': 'cli'}, 'php_runtime')
    exact(value['loaded_source_sha256'], reference['wordpress_loaded_source_sha256'], 'php_original_source')
    includes = ['experiments/wpdb-bridge-probe/fixtures/' + p for p in [variant + '.php', 'helpers.php', 'corpus.php']]
    if variant == 'bridge': includes.append('experiments/wpdb-bridge-probe/fixtures/adapter.php')
    includes += ['.reference/wordpress-7.1.3/' + name for name in build.native.WP_FILES]
    exact(value['included_source_names'], sorted(includes), 'php_include_set')
    for key in CORE:
        exact(value[key], old[key], 'frozen_native_' + key + '_differs')
    snapshot = value['session_snapshot']
    require(set(snapshot) == {'character_set_connection_hex', 'collation_connection_hex', 'sql_mode_hex'}, 'session_snapshot_keys')
    for wire in snapshot.values():
        require(type(wire) is str and len(wire) <= 8192 and re.fullmatch('(?:[0-9a-f]{2})*', wire) is not None,
            'session_snapshot_hex')
    require(bytes.fromhex(snapshot['character_set_connection_hex']) == b'utf8mb4', 'actual_session_charset')
    require(bytes.fromhex(snapshot['collation_connection_hex']) == b'utf8mb4_unicode_520_ci', 'actual_session_collation')
    return {'fixed_api_cases': len(value['observations']), 'all_api_fields_match_frozen_native': True,
        'php_query_count': value['final_num_queries'], 'all_original_expectations_preserved': True}


def fresh_native_baseline(backend, before):
    path = HERE / ('evidence/windows-' + backend + '-native.json')
    r = db.parse_json(build.read(path, 512 * 1024))
    require(r['passed'] is True and r['variant'] == 'native' and r['backend'] == backend,
        'fresh_native_baseline_missing_or_failed')
    exact(r['input_files'], before, 'fresh_native_sources_changed')
    require(r['database_closed_and_reaped'] is True and r['normal_admin_shutdown_verified'] is True
        and r['all_command_children_reaped'] is True, 'fresh_native_cleanup_not_verified')
    return r, sha(build.read(path, 512 * 1024))


def validate_host(value, nonce):
    for key in ['passed', 'owned_php_reaped', 'php_exit_success', 'physical_connection_created',
            'physical_disconnect_completed', 'capture_workers_joined', 'php_stdout_eof', 'php_stderr_eof',
            'private_native_loopback_transport']:
        require(value[key] is True, 'host_completion_' + key)
    for key in ['php_forced_kill', 'php_stdout_overflow', 'php_stderr_overflow', 'credentials_forwarded_to_php',
            'transport_nonce_recorded', 'physical_connection_pool_used', 'wasm_networking_enabled',
            'native_php_product_fallback', 'raw_non_utf8_err_verified', 'standard_wpdb_compatibility_verified',
            'product_driver_selected', 'product_abi_selected', 'database_started_by_probe', 'ddl_executed_by_probe']:
        require(value[key] is False, 'host_scope_' + key)
    require(value['php_exit_code'] == 0 and value['php_stderr_bytes'] == 0, 'host_php_exit_or_stderr')
    ops = value['operations']
    require(type(ops) is list and 2 <= len(ops) <= 64, 'host_operation_count')
    exact(ops[0]['op'], 'hello', 'host_first_hello')
    exact(ops[-1]['op'], 'close', 'host_last_close')
    counts = dict.fromkeys(['hello', 'query', 'escape', 'close'], 0)
    for seq, op in enumerate(ops, 1):
        require(type(op['seq']) is int and op['seq'] == seq and op['op'] in counts, 'host_operation_sequence')
        require(type(op['ok']) is bool and type(op['data_hex']) is str
            and re.fullmatch('(?:[0-9a-f]{2})*', op['data_hex']) is not None
            and len(op['data_hex']) <= 8192, 'host_payload_bounds')
        counts[op['op']] += 1
        request = {'schema': 'recastpress.wpdb-rpc-request.v0.5', 'nonce': nonce, 'seq': seq,
            'op': op['op'], 'data_hex': op['data_hex']}
        frame = json.dumps(request, ensure_ascii=True, separators=(',', ':')).encode() + b'\n'
        require(len(frame) <= 16384 and op['request_frame_bytes'] == len(frame)
            and op['request_frame_sha256'] == sha(frame), 'host_actual_request_frame')
        require(type(op['reply_frame_bytes']) is int and 0 < op['reply_frame_bytes'] <= 65536,
            'host_actual_reply_frame')
        reply = canonical({'schema': 'recastpress.wpdb-rpc-reply.v0.5', 'seq': seq,
            'ok': op['ok'], 'data': op['data']}) + b'\n'
        require(op['reply_frame_bytes'] == len(reply) and op['reply_frame_sha256'] == sha(reply),
            'host_actual_reply_identity')
    php = value['php_result']
    transport = php['rpc_stats']['transport']
    exact(counts, transport['counts'], 'host_vs_php_operation_counts')
    require(counts['hello'] == counts['close'] == 1 and transport['requests'] == len(ops)
        and transport['transport_closed'] is True, 'host_rpc_closed_once')
    requested = sum(op['request_frame_bytes'] for op in ops)
    replied = sum(op['reply_frame_bytes'] for op in ops)
    require(requested == value['request_bytes'] == transport['request_bytes'] <= 128 * 1024
        and replied == value['reply_bytes'] == transport['reply_bytes'] <= 512 * 1024, 'host_vs_php_byte_accounting')
    setup = php['rpc_stats']['internal_session_setup']
    expected_queries = [item['sql_hex'] for item in setup]
    expected_queries += [item['query_hex'] for item in php['observations'] if item['state']['delta'] == 1]
    queries = [op for op in ops if op['op'] == 'query']
    exact([op['data_hex'] for op in queries], expected_queries, 'host_query_trace_vs_all_php_calls')
    require(len(setup) == 4 and len(queries) == 29, 'host_bootstrap_and_public_query_counts')
    for op, item in zip(queries, setup):
        require(op['ok'] is True, 'host_bootstrap_query_failed')
        exact(op['data'], item['data'], 'host_bootstrap_actual_reply')
    hello = ops[0]['data']
    require(ops[0]['ok'] is True and type(hello['caps']) is int
        and hello['caps'] & (2 | 128 | 2048 | 65536) == 0, 'host_actual_capabilities')
    exact(hello['server_version_hex'], php['server_info_hex'], 'host_server_version_vs_php')
    require(ops[-1]['ok'] is True and ops[-1]['data']['closed'] is True, 'host_close_reply')
    return {'all_rpc_frames_bound_to_private_nonce_and_sequence': True, 'operations': len(ops),
        'query_operations': len(queries), 'bootstrap_query_operations': len(setup),
        'all_public_queries_match_php_observations': True, 'stdout_used_for_dispatch': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--variant', choices=['native', 'bridge'], required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    r = {'schema': 'recastpress.wpdb-bridge-database.v0.5', 'outcome': 'not_run', 'passed': False,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.system(),
        'backend': args.backend, 'variant': args.variant, 'tls_profile': 'disabled',
        'php_rust_real_database_executed': False, 'native_php_used_as_product_fallback': False,
        'full_wordpress_bootstrap': False, 'wasm_executed': False, 'product_abi_selected': False,
        'standard_wpdb_compatibility_verified': False, 'raw_non_utf8_err_verified': False}
    passed, stage, owned = False, 'preflight', None
    nonce = secrets.token_hex(24)
    try:
        require(os.name == 'nt', 'fixed_windows_bridge_profile')
        before = r['input_files'] = build.inputs()
        binary = build.TARGET / 'debug/recastpress-wpdb-bridge-probe.exe'
        receipt = build.verify_build(binary)
        r['build_receipt_sha256'] = sha(build.read(HERE / 'evidence/windows-build.json', 256 * 1024))
        r['binary_identity'] = receipt['executable_identity']
        r['corpus_gate'] = build.fixture_gate()
        reference = r['native_reference'] = build.native.references()
        old = frozen_native(args.backend)
        r['previous_native_receipt_sha256'] = BASELINES[args.backend]
        fresh = None
        if args.variant == 'bridge':
            fresh, r['fresh_native_receipt_sha256'] = fresh_native_baseline(args.backend, before)
        stage = 'owned_database'
        owned = OwnedDatabase(args.backend, r)
        with owned:
            stage = 'php_execution'
            if args.variant == 'native':
                command = build.native.command()[:-1] + [str(HERE / 'fixtures/native.php')]
                request = owned.request('recastpress.wpdb-native-input.v0.5')
                limit, timeout = 256 * 1024, 30
            else:
                command = [str(binary)]
                request = owned.request('recastpress.wpdb-bridge-input.v0.5')
                request['nonce'] = nonce
                limit, timeout = 1024 * 1024, 80
            result = owned.harness.run('owned_' + args.variant + '_corpus', command, timeout=timeout,
                input_bytes=canonical(request) + b'\n', limit=limit)
            r.update(execution_exit_code=result.returncode, execution_stdout_sha256=sha(result.stdout),
                execution_stderr_sha256=sha(result.stderr))
            require(result.stdout.endswith(b'\n') and result.stdout.count(b'\n') == 1, 'one_completed_observation_required')
            value = db.parse_json(result.stdout)
            r['observation'] = value
            require(result.returncode == 0 and not result.stderr, 'owned_corpus_execution_failed')
            if args.variant == 'bridge':
                require(value['schema'] == 'recastpress.wpdb-bridge-observation.v0.5' and value['passed'] is True,
                    'rust_bridge_verdict_failed')
                require(value['owned_php_reaped'] is True and value['physical_disconnect_completed'] is True,
                    'rust_bridge_cleanup_failed')
                php = value['php_result']
                r['host_validation'] = validate_host(value, nonce)
                r['php_rust_real_database_executed'] = True
            else: php = value
            r['api_validation'] = validate_php(php, args.variant, reference, old)
            if fresh is not None:
                for key in CORE + ['session_snapshot']:
                    exact(php[key], fresh['observation'][key], 'fresh_native_' + key + '_differs')
                r['fresh_native_all_api_fields_and_session_match'] = True
            stage = 'final_verification'
            owned.verify_final()
            exact(build.verify_build(binary), receipt, 'bridge_build_chain_changed')
            exact(build.inputs(), before, 'bridge_input_files_changed')
            exact(build.native.references(), reference, 'native_reference_changed')
            stage = 'normal_shutdown'
        require(r['normal_admin_shutdown_verified'] is True and r['all_command_children_reaped'] is True,
            'owned_cleanup_not_verified')
        passed = True
        r['outcome'] = 'shared_wpdb_' + args.variant + '_database_passed'
    except (Exception, KeyboardInterrupt) as error:
        r.update(outcome='shared_wpdb_database_failed', failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            r['failure'] = str(error)
    r['passed'] = passed
    wire = (json.dumps(r, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    secrets_to_check = [nonce]
    if owned is not None: secrets_to_check += [owned.password, owned.admin_password]
    for secret in secrets_to_check:
        require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'secret_in_bridge_public_record')
    require(len(wire) <= 2 * 1024 * 1024, 'bridge_public_record_quota')
    if args.record:
        path = HERE / ('evidence/windows-' + args.backend + '-' + args.variant + '.json')
        path.parent.mkdir(exist_ok=True)
        require(path.resolve() == path, 'bridge_public_record_linked')
        path.write_bytes(wire)
    print('WPDB shared corpus: ' + r['outcome'] + ' (' + args.backend + '/' + args.variant + ')')
    return 0 if passed else 1


if __name__ == '__main__': sys.exit(main())
