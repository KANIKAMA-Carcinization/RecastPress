"""Run only the fixed native valid-UTF8 corpus on a freshly owned Windows DB."""
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


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


prepared = load('rp_utf8_prepare', HERE / 'prepare.py')
require, canonical, sha = prepared.require, prepared.canonical, prepared.sha


def exact(actual, expected, code): require(canonical(actual) == canonical(expected), code)


def validate(value, backend, reference):
    truth = ['passed', 'connected', 'constructor_connection_deferred', 'close_completed',
        'included_source_gate_passed', 'native_driver_extension_present', 'native_mysqli_handle_supplied', 'mbstring_loaded']
    falsity = ['full_wordpress_bootstrap', 'database_started_by_fixture', 'ddl_executed_by_fixture',
        'standard_wpdb_compatibility_verified', 'product_driver_selected', 'product_abi_selected',
        'wasm_executed', 'rust_driver_executed', 'native_tls_verified', 'raw_non_utf8_err_verified',
        'invalid_utf8_or_overlong_inputs_exercised', 'normalization_applied']
    other = ['schema', 'variant', 'outcome', 'suppress_errors_previous', 'observations', 'query_trace_hex',
        'tls_profile', 'runtime', 'loaded_source_sha256', 'included_source_names', 'server_info_hex',
        'session_snapshot', 'bootstrap_num_queries', 'final_num_queries', 'final_row_count', 'utf8_input_hex']
    require(type(value) is dict and set(value) == set(truth + falsity + other), 'utf8_closed_php_record')
    for name in truth: require(value[name] is True, 'utf8_required_completion')
    for name in falsity: require(value[name] is False, 'utf8_unclaimed_scope')
    require(value['schema'] == 'recastpress.wpdb-utf8-php-observation.v0.5' and value['variant'] == 'native'
        and value['outcome'] == 'fixed_utf8_cases_passed' and value['tls_profile'] == 'disabled'
        and value['suppress_errors_previous'] is False, 'utf8_native_profile')
    exact(value['runtime'], {'version': '8.5.12', 'version_id': 80512, 'int_size': 8, 'sapi': 'cli'}, 'utf8_native_runtime')
    exact(value['loaded_source_sha256'], reference['wordpress_loaded_source_sha256'], 'utf8_original_wp_sources')
    names = ['experiments/wpdb-utf8-probe/fixtures/' + name for name in ['native.php', 'entry.php', 'corpus.php']]
    names += ['experiments/wpdb-bridge-probe/fixtures/helpers.php']
    names += ['.reference/wordpress-7.1.3/' + name for name in reference['wordpress_loaded_source_sha256']]
    exact(value['included_source_names'], sorted(names), 'utf8_exact_include_set')
    baseline = json.loads(prepared.read(ROOT / ('experiments/wpdb-crud-probe/evidence/windows-' + backend + '-native.json')))
    require(baseline['passed'] is True and baseline['normal_admin_shutdown_verified'] is True, 'utf8_accepted_server_reference')
    for name in ['session_snapshot', 'server_info_hex']:
        exact(value[name], baseline['observation'][name], 'utf8_same_fixed_session')
    exact(value['utf8_input_hex'], {label: value.encode('utf-8').hex() for label, value in prepared.VALUES}, 'utf8_known_input_bytes')
    require(value['bootstrap_num_queries'] == 0 and value['final_num_queries'] == 22 and value['final_row_count'] == '0', 'utf8_final_empty_and_count')
    expected = prepared.expected_cases(); rows = value['observations']
    require(type(rows) is list and len(rows) == len(expected), 'utf8_complete_case_count')
    trace = []; previous = 0
    known_queries = {q for row in expected for q in row['queries_hex']}
    for row, wanted in zip(rows, expected):
        require(type(row) is dict and set(row) == set(wanted) | {'state'}, 'utf8_closed_case')
        for name, item in wanted.items(): exact(row[name], item, 'utf8_expected_argument_return_or_sql_bytes')
        state = row['state']
        require(type(state) is dict and set(state) == {'num_queries', 'delta', 'num_rows', 'rows_affected',
            'insert_id', 'last_error_hex', 'last_query_hex'}, 'utf8_closed_query_state')
        for key in ['num_queries', 'delta', 'num_rows', 'rows_affected', 'insert_id']:
            require(type(state[key]) is int and 0 <= state[key] <= 64, 'utf8_finite_state_counter')
        require(state['last_error_hex'] == '' and state['last_query_hex'] in known_queries | {''}, 'utf8_known_query_state_bytes')
        require(state['delta'] == len(wanted['queries_hex']) and state['num_queries'] == previous + state['delta'], 'utf8_query_counter_balance')
        previous = state['num_queries']; trace.extend(wanted['queries_hex'])
    exact(value['query_trace_hex'], trace, 'utf8_complete_synthetic_sql_trace')
    require(previous == 22 and trace.count('SHOW FULL COLUMNS FROM `rp_crud_fixture`'.encode().hex()) == 1, 'utf8_original_schema_lookup')
    return {'api_cases': 31, 'public_queries': 22, 'original_schema_queries': 1,
        'arguments_returns_and_all_sql_bytes_verified': True, 'normalization_applied': False,
        'native_mbstring_required': True, 'invalid_utf8_verified': False}


def run(backend, record=False):
    report = {'schema': 'recastpress.wpdb-utf8-native-check.v0.5', 'passed': False,
        'outcome': 'not_run', 'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'backend': backend,
        'native_only': True, 'full_wordpress_bootstrap': False, 'wasm_executed': False, 'rust_driver_executed': False,
        'product_driver_selected': False, 'native_php_product_fallback': False, 'invalid_utf8_exercised': False,
        'normalization_applied': False, 'fixture_transport': 'disabled', 'unknown_php_output_published': False}
    owned = None; passed = False; stage = 'source_preflight'
    try:
        require(os.name == 'nt' and backend in ('mysql', 'mariadb'), 'utf8_fixed_windows_backend')
        report['prepared'] = prepared.prepare(); before = report['input_files'] = prepared.sources()
        native = load('rp_utf8_frozen_native', ROOT / 'experiments/wpdb-crud-probe/native_check.py')
        owner = load('rp_utf8_frozen_owner', ROOT / 'experiments/wpdb-crud-probe/owned_database.py')
        reference = report['native_reference_before'] = native.references()
        require(reference['php_verified_file_count'] == 78 and reference['wordpress_verified_source_count'] == 8
            and 'ext/php_mbstring.dll' in reference['php_file_sha256'], 'utf8_complete_package_and_mbstring')
        original_command = native.command('native')
        command = original_command[:-1] + ['-d', 'extension=mbstring', str(HERE / 'fixtures/native.php')]
        report['native_command_derivation'] = {'frozen_original_command_used': True,
            'changes': ['require_pinned_mbstring_extension', 'owned_native_entry_path'], 'product_command_changed': False}
        stage = 'owned_database'
        owned = owner.OwnedDatabase(backend, report)
        with owned:
            stage = 'native_utf8_execution'
            request = owned.request('recastpress.wpdb-native-input.v0.5')
            result = owned.harness.run('owned_native_utf8_corpus', command, timeout=30,
                input_bytes=canonical(request) + b'\n', limit=1024*1024)
            report['execution'] = {'exit_code': result.returncode, 'stdout': prepared.identity(result.stdout),
                'stderr': prepared.identity(result.stderr)}
            require(result.returncode == 0 and not result.stderr, 'utf8_native_execution')
            require(result.stdout.endswith(b'\n') and result.stdout.count(b'\n') == 1, 'utf8_one_json_line')
            value = owner.db.parse_json(result.stdout)
            report['validation'] = validate(value, backend, reference)
            # No raw observation reaches a public report unless every string,
            # typed value and SQL byte has matched the fixed synthetic contract.
            report['observation'] = value
            stage = 'final_verification'
            owned.verify_final()
            wire = owned.harness.checked('independent_unused_wire_fixture', [*owned.admin,
                '--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_wire_fixture'], timeout=5, limit=65536)
            require(wire.stdout.strip() == b'0', 'utf8_unused_wire_fixture_changed')
            report['unused_wire_fixture_empty_after'] = True
            exact(prepared.sources(), before, 'utf8_source_changed')
            report['native_reference_after'] = native.references()
            exact(report['native_reference_after'], reference, 'utf8_native_package_or_wp_changed')
            report['source_reverified_after'] = True
        require(report['normal_admin_shutdown_verified'] is True and report['database_closed_and_reaped'] is True
            and report['all_command_children_reaped'] is True, 'utf8_normal_cleanup_required')
        passed = True
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)): report['failure'] = str(error)
    report.update(passed=passed, outcome='fixed_native_utf8_corpus_passed' if passed else 'fixed_native_utf8_corpus_failed')
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    require(len(wire) <= 2*1024*1024, 'utf8_public_receipt_quota')
    if owned is not None:
        for secret in [owned.password, owned.admin_password]:
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'utf8_public_secret_guard')
    if record:
        path = HERE / ('evidence/windows-' + backend + '-native.json')
        require(path.resolve() == path and path.parent.resolve() == path.parent, 'utf8_owned_evidence_path')
        path.parent.mkdir(exist_ok=True)
        if path.exists():
            previous = prepared.read(path)
            backup = path.with_name('windows-' + backend + '-native-before-' + sha(previous)[:16] + '.json')
            if backup.exists(): require(prepared.read(backup) == previous, 'utf8_immutable_previous_receipt')
            else:
                with backup.open('xb') as stream: stream.write(previous)
        path.write_bytes(wire)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--record', action='store_true'); args = parser.parse_args()
    result = run(args.backend, args.record)
    print('WPDB UTF8: ' + result['outcome'] + ' (' + args.backend + ')'); sys.exit(0 if result['passed'] else 1)
