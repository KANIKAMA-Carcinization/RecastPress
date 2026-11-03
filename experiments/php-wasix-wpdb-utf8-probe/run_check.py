"""Compare the fixed UTF-8 corpus through WASIX and an owned Rust DB session."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load('rp_wasix_utf8_runtime_build', HERE / 'build_check.py')
prepared = load('rp_wasix_utf8_runtime_sources', HERE / 'prepare.py')
linux = load('rp_wasix_utf8_runtime_database', HERE / 'linux_database.py')
managed = load('rp_wasix_utf8_runtime_module', ROOT / 'experiments/php-wasix-managed-access-probe/build.py')
require, sha = prepared.require, prepared.sha
COMPARE = ['observations', 'query_trace_hex', 'session_snapshot', 'server_info_hex', 'loaded_source_sha256',
    'suppress_errors_previous', 'bootstrap_num_queries', 'final_num_queries', 'final_row_count', 'utf8_input_hex']


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def reference(backend, mapping):
    pin = prepared.ORACLES[backend]
    wire = prepared.read(ROOT / pin['path'])
    require(sha(wire) == pin['sha256'] and len(wire) == pin['bytes'], 'fixed_native_utf8_reference')
    value = json.loads(wire)
    require(value['passed'] is True and value['source_reverified_after'] is True
        and value['normal_admin_shutdown_verified'] is True and value['all_command_children_reaped'] is True,
        'native_utf8_completion')
    original = value['observation']
    profile = mapping['server_metadata']['backends'][backend]
    require(original['server_info_hex'] == profile['native_version_hex'], 'native_server_metadata_pin')
    expected = json.loads(canonical(original))
    expected['server_info_hex'] = profile['linux_version_hex']
    require(all(canonical(expected[key]) == canonical(original[key]) for key in original if key != 'server_info_hex'),
        'only_expected_platform_server_metadata_changes')
    return expected


def validate(value, native, mapping):
    require(value['schema'] == 'recastpress.php-wasix-wpdb-utf8-observation.v0.5'
        and value['mode'] == 'read' and value['profile'] == 'utf8' and value['passed'] is True
        and value['outcome'] == 'fixed_wasix_wpdb_utf8_passed' and value['phase'] == 'full_utf8_verified',
        'utf8_host_outcome')
    for key in ['physical_connection_created', 'physical_disconnect_attempted', 'physical_disconnect_completed',
        'db_actor_joined', 'db_actor_exited', 'fresh_runtime', 'source_rechecked_after_execution']:
        require(value[key] is True, 'host_completion_' + key)
    for key in ['full_wordpress_bootstrap', 'guest_networking', 'host_environment_forwarded', 'host_home_mapped',
        'db_credentials_supplied_to_guest', 'host_salts_supplied_to_guest', 'native_php_fallback', 'product_runtime',
        'standard_wpdb_compatibility_proven', 'memory_cpu_limits_verified_for_this_integration', 'managed_access_exercised']:
        require(value[key] is False, 'host_scope_' + key)
    require(value['module_compilations'] == 1 and value['db_actor_failure'] is None, 'one_clean_utf8_session')
    capture = value['cgi_capture']
    for key in ['guest_exit_success', 'runtime_shutdown_returned', 'stdout_eof', 'stderr_eof']:
        require(capture[key] is True, 'capture_completion_' + key)
    require(0 < capture['stdout_bytes'] <= 65*1024 and capture['stderr_bytes'] == 0
        and capture['stderr_sha256'] == sha(b'') and capture['stdout_overflow'] is False
        and capture['stderr_overflow'] is False and capture['response']['status'] == 200,
        'bounded_clean_utf8_cgi')
    php = value['php_result']
    require(php['schema'] == 'recastpress.php-wasix-wpdb-utf8-php-observation.v0.5'
        and php['variant'] == 'bridge' and php['outcome'] == 'fixed_utf8_cases_passed', 'utf8_php_profile')
    for key in ['passed', 'connected', 'constructor_connection_deferred', 'close_completed',
        'included_source_gate_passed', 'native_driver_extension_present', 'mbstring_loaded',
        'wasm_executed', 'rust_driver_executed']:
        require(php[key] is True, 'php_completion_' + key)
    for key in ['native_mysqli_handle_supplied', 'full_wordpress_bootstrap',
        'standard_wpdb_compatibility_verified', 'normalization_applied']:
        require(php[key] is False, 'php_scope_' + key)
    require(php['runtime'] == {'version': '8.5.12', 'version_id': 80512, 'int_size': 8, 'sapi': 'cgi-fcgi'}
        and php['included_source_names'] == mapping['expected_include_names'], 'utf8_php_runtime_sources')
    for key in COMPARE:
        require(canonical(php[key]) == canonical(native[key]), 'native_utf8_' + key + '_differs')
    rpc, ops = value['rpc'], value['operations']
    require(rpc['finished'] is True and rpc['opened_once'] is True and rpc['close_dispatched'] is True
        and rpc['failure'] is None and rpc['channel_open'] is False and rpc['response_pending'] is False
        and rpc['pending_bytes'] == rpc['unread_response_bytes'] == 0, 'utf8_private_channel_sealed')
    require(2 <= len(ops) <= 4096 and len(ops) == rpc['operations']
        and ops[0]['op'] == 'hello' and ops[-1]['op'] == 'close', 'utf8_rpc_sequence')
    fields = {'seq', 'op', 'request_data_bytes', 'request_data_sha256', 'request_frame_bytes',
        'request_frame_sha256', 'reply_frame_bytes', 'reply_frame_sha256', 'ok', 'row_count',
        'column_count', 'server_error_code', 'server_error_state_hex'}
    counts = dict.fromkeys(['hello', 'query', 'escape', 'close'], 0)
    for seq, operation in enumerate(ops, 1):
        require(set(operation) == fields and operation['seq'] == seq and operation['op'] in counts
            and operation['ok'] is True and operation['server_error_code'] is None
            and operation['server_error_state_hex'] is None, 'closed_successful_utf8_operation')
        counts[operation['op']] += 1
        require(0 <= operation['request_data_bytes'] <= 65536 and 0 < operation['request_frame_bytes'] <= 262144
            and 0 < operation['reply_frame_bytes'] <= 524288, 'utf8_operation_byte_limits')
        for key in ['request_data_sha256', 'request_frame_sha256', 'reply_frame_sha256']:
            require(re.fullmatch('[0-9a-f]{64}', operation[key]) is not None, 'utf8_operation_digest')
        require(operation['row_count'] is None or 0 <= operation['row_count'] <= 256, 'utf8_row_limit')
        require(operation['column_count'] is None or 0 <= operation['column_count'] <= 32, 'utf8_column_limit')
    require(counts['hello'] == counts['close'] == 1 and counts['query'] >= 22, 'utf8_single_physical_session')
    request_bytes = sum(row['request_frame_bytes'] for row in ops)
    reply_bytes = sum(row['reply_frame_bytes'] for row in ops)
    require(request_bytes == rpc['request_bytes'] <= 8388608
        and reply_bytes == rpc['response_bytes'] == rpc['response_read_bytes'] <= 16777216, 'utf8_rpc_byte_balance')
    stats = php['rpc_stats']; transport = stats['transport']
    require(transport['requests'] == len(ops) and transport['counts'] == counts
        and transport['request_bytes'] == request_bytes and transport['reply_bytes'] == reply_bytes
        and transport['transport_closed'] is True and transport['kind'] == 'file-channel'
        and transport['channel_guest_path'] == '/rp-wpdb/channel' and transport['max_requests'] == 4096
        and stats['internal_setup_queries'] == counts['query'] - len(php['query_trace_hex']), 'utf8_php_rpc_balance')
    return {'all_native_case_state_and_query_bytes_equal': True, 'api_cases': 31, 'public_queries': 22,
        'compared_fields': COMPARE, 'operation_counts': counts, 'request_bytes': request_bytes,
        'reply_bytes': reply_bytes, 'normalization_applied': False,
        'private_query_reply_digests_recomputed_offline': False}


def run(backend, record=False):
    report = {'schema': 'recastpress.php-wasix-wpdb-utf8-check.v0.5', 'passed': False,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'backend': backend, 'platform': sys.platform,
        'full_wordpress_bootstrap': False, 'http_listener_exercised': False,
        'native_php_executed': False, 'native_php_fallback': False, 'product_runtime_selected': False,
        'standard_wpdb_compatibility_verified': False, 'normalization_applied': False,
        'owned_cleanup_completed': False, 'memory_cpu_limits_verified_for_this_integration': False}
    passed = False; database = None; stage = 'source_preparation'
    try:
        require(sys.platform == 'linux', 'actual_php_wasm_linux_only')
        before = report['input_files'] = build.inputs()
        mapping = report['preparation_before'] = prepared.verify_prepared()
        host = report['build_before'] = build.verify_build()
        report['module_before'] = managed.verify_build()
        native = reference(backend, mapping)
        profile = mapping['server_metadata']['backends'][backend]
        report['reference_derivation'] = {'source': 'pinned_windows_native_observation',
            'changed_fields': ['server_info_hex'], 'actual_result_modified': False,
            'native_server_info_hex': profile['native_version_hex'], 'linux_server_info_hex': profile['linux_version_hex'],
            'server_metadata_identity': mapping['server_metadata_identity']}
        database = linux.Database(backend, report)
        require(database.pin['platform_digest'] == profile['image']['platform_digest']
            and database.pin['image_config_digest'] == profile['image']['image_config_digest'],
            'exact_linux_metadata_image_pin')
        with database:
            require(report['sql_server_version'].encode('utf-8').hex() == profile['linux_version_hex'],
                'independent_linux_server_metadata_pin')
            stage = 'php_wasm_utf8'
            pid, result = owned_probe.run_captured_probe([str(ROOT / host['executable'])],
                build.environment(), timeout=300, input_bytes=canonical(database.input()))
            report['execution'] = {'owned_pid': pid, 'owned_reaped': True, 'exit_code': result.returncode,
                'stdout_bytes': len(result.stdout), 'stderr_bytes': len(result.stderr),
                'stdout_sha256': sha(result.stdout), 'stderr_sha256': sha(result.stderr)}
            require(0 < len(result.stdout) <= 2*1024*1024 and not result.stderr
                and result.stdout.endswith(b'\n') and result.stdout.count(b'\n') == 1, 'utf8_host_json_frame')
            value = report['observation'] = json.loads(result.stdout)
            require(result.returncode == 0 and value['passed'] is True, 'utf8_host_request_failed')
            report['validation'] = validate(value, native, mapping)
            require(value['php_result']['server_info_hex'] == report['sql_server_version'].encode('utf-8').hex(),
                'actual_handshake_api_and_independent_sql_version_equal')
            stage = 'independent_database_verification'; database.final_rows()
        require(report['container_stopped'] is True and report['container_removed'] is True
            and report['container_normal_exit_verified'] is True, 'utf8_normal_container_cleanup')
        stage = 'after_verification'
        require(build.inputs() == before and prepared.verify_prepared() == mapping, 'utf8_runtime_sources_changed')
        report['build_after'] = build.verify_build(); report['module_after'] = managed.verify_build()
        require(report['build_after'] == host and report['module_after'] == report['module_before'],
            'utf8_host_or_module_changed')
        report['source_reverified_after'] = True; passed = True
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        try:
            owned_probe.reap_owned_probes(); report['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt):
            passed = False; report['cleanup_failed'] = True
    report.update(passed=passed, outcome='fixed_wasix_utf8_corpus_passed' if passed else 'fixed_wasix_utf8_corpus_failed')
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    require(len(wire) <= 4*1024*1024, 'utf8_public_receipt_quota')
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'utf8_public_secret_guard')
    if record: build.preserve(HERE / ('evidence/linux-' + backend + '.json'), wire)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--record', action='store_true'); args = parser.parse_args()
    value = run(args.backend, args.record); print(value['outcome']); sys.exit(0 if value['passed'] else 1)
