"""Observe server-generated ERR packet bytes alongside native mysqli and stock wpdb."""

import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

from wire_proxy import QUERIES, WireProxy


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIXTURE = HERE / 'fixtures/native.php'
HELPERS = ROOT / 'experiments/wpdb-bridge-probe/fixtures/helpers.php'
OWNER = ROOT / 'experiments/wpdb-bridge-probe/owned_database.py'
NATIVE = ROOT / 'experiments/wpdb-crud-probe/native_check.py'
PINS = {
    HELPERS: '1220aa52f8a967f7d4981a1b668e07c7343cdff9c10caf42ea99c32a348fccb1',
    OWNER: 'c98af5c7916822b203ddecdef2793bfb0ef50ec10649a97b0673aa02c363314c',
    NATIVE: 'ae8116f1d149c3afe0d21969f5cb5b3679d0375568b437c57f2d2df01514de87',
}
ERRORS = {'utf8_signal': (31001, b'45000'), 'latin1_signal': (31002, b'45000'),
    'next_signal': (31003, b'45001')}
COMMANDS = {
    'message': b'SET @rp_err_message = CONVERT(0xC3A9 USING utf8mb4)',
    'utf8_signal': b"SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO = 31001, MESSAGE_TEXT = @rp_err_message",
    'latin1_results': b'SET SESSION character_set_results = latin1',
    'latin1_signal': b"SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO = 31002, MESSAGE_TEXT = @rp_err_message",
    'restore_utf8_results': b'SET SESSION character_set_results = utf8mb4',
    'next_signal': b"SIGNAL SQLSTATE '45001' SET MYSQL_ERRNO = 31003, MESSAGE_TEXT = 'rp followup'",
    'recovery': b'SELECT 42 AS rp_recovered',
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def digest(wire):
    return sha256(wire).hexdigest()


def identity(wire):
    return {'bytes': len(wire), 'sha256': digest(wire)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def exact(value, expected, code):
    require(canonical(value) == canonical(expected), code)


def read(path):
    require(path.resolve(strict=True) == path and path.is_file(), 'raw_err_fixed_input')
    with path.open('rb') as stream:
        wire = stream.read(256 * 1024 + 1)
    require(0 < len(wire) <= 256 * 1024, 'raw_err_input_bound')
    return wire


def sources():
    for path, sha in PINS.items():
        require(digest(read(path)) == sha, 'raw_err_accepted_source_changed')
    names = [Path(__file__).resolve(), HERE / 'wire_proxy.py', FIXTURE, *PINS]
    return {path.relative_to(ROOT).as_posix(): digest(read(path)) for path in names}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unhex(value, maximum, code):
    require(type(value) is str and len(value) <= maximum * 2
        and re.fullmatch('(?:[a-f0-9]{2})*', value) is not None, code)
    return bytes.fromhex(value)


def validate_php(value, reference, backend):
    require(type(value) is dict and set(value) == {'schema', 'passed', 'close_completed', 'loaded_source_sha256',
        'runtime', 'server_info_hex', 'session_snapshot', 'initial_results_charset', 'observations',
        'included_source_gate_passed'}, 'raw_err_closed_php_observation')
    exact(value['schema'], 'recastpress.wpdb-raw-error-wire-native-php.v0.5', 'raw_err_php_schema')
    require(value['passed'] is True and value['close_completed'] is True and value['included_source_gate_passed'] is True,
        'raw_err_php_completion')
    exact(value['runtime'], {'version': '8.5.12', 'version_id': 80512, 'int_size': 8, 'sapi': 'cli'}, 'raw_err_php_runtime')
    exact(value['loaded_source_sha256'], reference['wordpress_loaded_source_sha256'], 'raw_err_php_sources')
    exact(value['initial_results_charset'], 'utf8mb4', 'raw_err_initial_results_charset')
    server_info = unhex(value['server_info_hex'], 64, 'raw_err_server_info_shape')
    require(re.fullmatch(rb'[A-Za-z0-9.+_-]{1,64}', server_info) is not None
        and server_info.startswith(b'8.4.13' if backend == 'mysql' else b'11.8.9-MariaDB'), 'raw_err_php_server_info')
    snapshot = value['session_snapshot']
    require(type(snapshot) is dict and set(snapshot) == {'character_set_connection_hex', 'collation_connection_hex',
        'sql_mode_hex'} and snapshot['character_set_connection_hex'] == b'utf8mb4'.hex(), 'raw_err_session_profile')
    for key in ('collation_connection_hex', 'sql_mode_hex'):
        require(re.fullmatch(rb'[A-Za-z0-9_,]+', unhex(snapshot[key], 512, 'raw_err_session_bytes')) is not None,
            'raw_err_session_value')

    rows = value['observations']
    require(type(rows) is dict and tuple(rows) == tuple(COMMANDS), 'raw_err_exact_php_query_order')
    for number, (label, sql) in enumerate(COMMANDS.items(), 1):
        row = rows[label]
        require(type(row) is dict and set(row) == {'query_hex', 'returned', 'state', 'mysqli_errno',
            'mysqli_sqlstate_hex', 'mysqli_error_hex', 'last_result'}, 'raw_err_closed_php_query')
        exact(row['query_hex'], sql.hex(), 'raw_err_php_exact_query')
        code, state = ERRORS.get(label, (0, b'00000'))
        exact(row['mysqli_errno'], code, 'raw_err_php_errno')
        exact(row['mysqli_sqlstate_hex'], state.hex(), 'raw_err_php_sqlstate')
        message = unhex(row['mysqli_error_hex'], 512, 'raw_err_mysqli_message_shape')
        require(bool(message) == (label in ERRORS), 'raw_err_php_message_expected')
        recovery = label == 'recovery'
        exact(row['returned'], False if label in ERRORS else int(recovery), 'raw_err_php_return_type_or_value')
        wanted = {'num_queries': number, 'delta': 1, 'num_rows': int(recovery), 'rows_affected': 0,
            'insert_id': 0, 'last_query_hex': sql.hex(), 'last_error_hex': row['state']['last_error_hex']}
        exact(row['state'], wanted, 'raw_err_wpdb_exact_state')
        last_error = unhex(row['state']['last_error_hex'], 512, 'raw_err_wpdb_message_shape')
        require(bool(last_error) == (label in ERRORS), 'raw_err_wpdb_message_expected')
        wanted_result = [{'key': 0, 'value': {'type': 'stdClass', 'properties': [
            {'name_hex': b'rp_recovered'.hex(), 'value': {'bytes_hex': b'42'.hex()}}]}}] if recovery else []
        exact(row['last_result'], {'type': 'array', 'entries': wanted_result}, 'raw_err_php_result_value')
    return rows


def validate_wire(value):
    require(type(value) is dict and set(value) == {'connections', 'authenticated', 'tls', 'compression',
        'client_packets', 'server_packets', 'quit_observed', 'closed', 'observations'}, 'raw_err_closed_wire_report')
    exact(value['connections'], 1, 'raw_err_wire_single_connection')
    require(value['authenticated'] is True and value['tls'] is False and value['compression'] is False
        and value['quit_observed'] is True and value['closed'] is True, 'raw_err_wire_completion')
    for name in ('client_packets', 'server_packets'):
        require(type(value[name]) is int and 0 < value[name] <= 4096, 'raw_err_wire_packet_count')
    records = value['observations']
    require(type(records) is dict and tuple(records) == tuple(QUERIES), 'raw_err_exact_wire_query_order')
    exact(records['recovery'], {'kind': 'resultset', 'columns': 1}, 'raw_err_wire_recovery_response')
    for label, (code, state) in ERRORS.items():
        row = records[label]
        require(type(row) is dict and set(row) == {'kind', 'code', 'state_hex', 'message_hex',
            'payload_bytes', 'payload_sha256'} and row['kind'] == 'err', 'raw_err_closed_wire_packet')
        exact(row['code'], code, 'raw_err_wire_code')
        exact(row['state_hex'], state.hex(), 'raw_err_wire_sqlstate')
        message = unhex(row['message_hex'], 512, 'raw_err_wire_message_shape')
        require(bool(message), 'raw_err_wire_message_expected')
        payload = b'\xff' + code.to_bytes(2, 'little') + b'#' + state + message
        exact(row['payload_bytes'], len(payload), 'raw_err_wire_payload_size')
        exact(row['payload_sha256'], digest(payload), 'raw_err_wire_payload_identity')
    return records


def comparisons(wire, php):
    results = {}
    for label in ERRORS:
        packet = wire[label]
        message = bytes.fromhex(packet['message_hex'])
        try:
            message.decode('utf-8', errors='strict')
            is_utf8 = True
        except UnicodeDecodeError:
            is_utf8 = False
        results[label] = {'wire_message_hex': packet['message_hex'], 'wire_valid_utf8': is_utf8,
            'wire_contains_nul': b'\0' in message, 'mysqli_message_hex': php[label]['mysqli_error_hex'],
            'wpdb_last_error_hex': php[label]['state']['last_error_hex'],
            'all_message_bytes_exact': packet['message_hex'] == php[label]['mysqli_error_hex'] == php[label]['state']['last_error_hex']}
    return results


def run(backend):
    report = {'schema': 'recastpress.wpdb-raw-error-wire-native-check.v0.5', 'backend': backend,
        'passed': False, 'native_only': True, 'full_wordpress_bootstrap': False,
        'synthetic_err_injected': False, 'auth_bytes_published': False, 'wasm_executed': False,
        'rust_driver_executed': False, 'backend_transport': 'owned_loopback_plaintext_for_wire_observation'}
    owner = None
    stage = 'source_preflight'
    try:
        require(os.name == 'nt' and backend in ('mysql', 'mariadb'), 'raw_err_fixed_windows_backend')
        require(QUERIES == {label: COMMANDS[label] for label in (*ERRORS, 'recovery')}, 'raw_err_proxy_query_binding')
        report['sources_before'] = sources()
        native = load('rp_raw_err_native_reference', NATIVE)
        before = report['native_reference_before'] = native.references()
        require(before['php_verified_file_count'] == 78 and before['wordpress_verified_source_count'] == 8,
            'raw_err_native_reference_count')
        command = native.command('native')[:-1] + [str(FIXTURE)]
        owned = load('rp_raw_err_owned_database', OWNER)
        db = owned.db
        owner = owned.OwnedDatabase(backend, report)
        stage = 'owned_database'
        with owner:
            require(report['database_identity_verified_before_fixture_ddl'] is True, 'raw_err_owned_database_identity')
            stage = 'native_stock_wpdb_through_proxy'
            with WireProxy(owner.port) as proxy:
                request = owner.request('recastpress.wpdb-native-input.v0.5')
                require(proxy.port != owner.port, 'raw_err_proxy_distinct_port')
                request['port'] = proxy.port
                child = owner.harness.run('owned_native_raw_err_corpus', command, timeout=30,
                    input_bytes=canonical(request) + b'\n', limit=65536)
                report['execution'] = {'exit_code': child.returncode, 'stdout': identity(child.stdout),
                    'stderr': identity(child.stderr)}
                require(child.stdout.endswith(b'\n') and child.stdout.count(b'\n') == 1, 'raw_err_one_php_observation')
                actual = db.parse_json(child.stdout)
                if type(actual) is dict and actual.get('passed') is False:
                    code = actual.get('failure')
                    if type(code) is str and re.fullmatch('[a-z][a-z0-9_]{0,127}', code):
                        report['php_failure'] = code
                require(child.returncode == 0 and not child.stderr, 'raw_err_native_execution')
                wire = proxy.result()
            stage = 'wire_and_native_comparison'
            records = validate_wire(wire)
            rows = validate_php(actual, before, backend)
            report['wire'] = wire
            report['observation'] = actual
            compared = report['comparisons'] = comparisons(records, rows)
            require(all(row['all_message_bytes_exact'] for row in compared.values()), 'raw_err_message_bytes_differ')
            stage = 'final_database_identity'
            db.identity_ready(owner.harness, owner.admin, owner.port, db.PINS[backend]['version'], backend)
            remaining = owner.harness.checked('independent_unused_wire_fixture', [*owner.admin,
                '--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_wire_fixture'], timeout=5, limit=65536)
            require(not remaining.stderr and remaining.stdout.strip() == b'0'
                and db.digest(owner.server_config) == owner.config_sha, 'raw_err_owned_database_final')
            report['owned_fixture_unchanged'] = True
        stage = 'source_postflight'
        report['sources_after'] = sources()
        report['native_reference_after'] = native.references()
        exact(report['sources_after'], report['sources_before'], 'raw_err_sources_changed')
        exact(report['native_reference_after'], before, 'raw_err_native_reference_changed')
        require(report['normal_admin_shutdown_verified'] is True and report['database_closed_and_reaped'] is True
            and report['all_command_children_reaped'] is True, 'raw_err_normal_owned_cleanup')
        report.update(passed=True, sources_reverified_after=True, error_then_success_clears=True)
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    wire = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    secrets = [] if owner is None else [owner.password, owner.admin_password]
    require(not any(secret.encode() in wire or secret.encode().hex().encode() in wire
        for secret in [*secrets, str(ROOT), ROOT.as_posix()]), 'raw_err_private_data_guard')
    require(len(wire) <= 256 * 1024, 'raw_err_receipt_limit')
    return report, wire


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    result, wire = run(args.backend)
    if args.record and result['passed']:
        path = HERE / 'evidence' / ('windows-' + args.backend + '-native.json')
        path.parent.mkdir(exist_ok=True)
        with path.open('xb') as stream:
            stream.write(wire)
    sys.stdout.buffer.write(wire)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
