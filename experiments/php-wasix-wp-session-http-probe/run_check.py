"""Compare three real HTTP responses from one compile-once WP session on owned DBs."""

import argparse
import ast
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys
import time


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREFIX = 'recastpress.php-wasix-wp-session-http-'
HTTP_SOURCE = ROOT / 'experiments/php-wasix-wp-private-http-probe/run_check.py'
LABELS = ('home', 'missing', 'missing_repeat')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


http = load('rp_session_http_baseline', HTTP_SOURCE)
session = load('rp_session_http_session', ROOT / 'experiments/php-wasix-wp-session-probe/run_check.py')
build = load('rp_session_http_build', HERE / 'build_check.py')
process = load('rp_session_http_process', HERE / 'server_process.py')
context, observer = http.context, http.observer
require, canonical, sha = http.require, http.canonical, http.sha
equal, closed, integer, byte_identity = http.equal, http.closed, http.integer, http.byte_identity
identity = http.process.identity


def sources():
    return build.inputs()


def summary_validator():
    wire = HTTP_SOURCE.read_bytes()
    require(sha(wire) == '6b6006fc0fea89b31777605d98bf04289385561b02ab2347084ef16d7e7ba7ab',
        'frozen_private_http_summary_validator')
    source = wire.decode('utf-8')
    node = next(node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == 'validate_summary')
    derived = ast.get_source_segment(source, node)
    for old, new in (
        ('recastpress.php-wasix-wp-private-http-worker-summary.v0.5', PREFIX + 'worker-summary.v0.5'),
        ('recastpress.php-wasix-wp-private-worker-observation.v0.5', session.SCHEMA),
        ('fixed_private_wp_worker_passed', 'fixed_session_request_passed'),
        ("('module_compilations',1)", "('module_compilations',0)"),
        ("'public_context_verified','private_ipc_only','worker_publication_authorized'",
         "'public_context_verified','private_ipc_only','worker_publication_authorized',"
         "'compiled_module_reused','module_compiled_during_request'"),
        ("'completion_verified','public_context_verified','private_ipc_only'",
         "'completion_verified','public_context_verified','private_ipc_only','compiled_module_reused'"),
        ("'worker_publication_authorized','native_comparison_performed'",
         "'worker_publication_authorized','native_comparison_performed','module_compiled_during_request'"),
    ):
        require(derived.count(old) == 1, 'exact_session_http_summary_derivation')
        derived = derived.replace(old, new, 1)
    namespace = dict(http.__dict__)
    exec(compile(derived, '<frozen-http-summary-with-compile-reuse>', 'exec'), namespace)
    return namespace['validate_summary']


def worker_identity(receipt, receipt_wire):
    return {'receipt_path': 'experiments/php-wasix-wp-session-probe/evidence/linux-build.json',
        'receipt_sha256': sha(receipt_wire), 'executable': receipt['executable'],
        'executable_identity': receipt['executable_identity'], 'source_inputs': 241, 'units': 64,
        'source_files_rehashed_by_service': False}


def expected_ready(module_sha):
    http.digest(module_sha)
    return {'schema': 'recastpress.php-wasix-wp-session-ready.v0.5',
        'module_compilations_total': 1, 'module_sha256': module_sha, 'compiled_module_ready': True}


def validate_service(value, execution, worker, module_sha, native, report):
    closed(value, ['schema', 'passed', 'outcome', 'requests', 'worker_executions', 'worker_build',
        'rejected_counts', 'concurrency', 'valid_execution_limit', 'bound_loopback_only',
        'graceful_shutdown_completed', 'stdin_shutdown_observed', 'stdin_protocol_error',
        'startup_private_input_used', 'private_config_on_http', 'transport_delivery_verified',
        'source_files_rehashed_by_service', 'native_comparison_performed', 'native_php_fallback',
        'product_runtime', 'session_ready', 'session_final', 'session_process',
        'module_compilations_total', 'request_module_compilations_total'])
    equal(value['schema'], PREFIX + 'observation.v0.5')
    equal(value['outcome'], 'fixed_session_http_passed')
    for key in ('passed', 'bound_loopback_only', 'graceful_shutdown_completed', 'startup_private_input_used'):
        equal(value[key], True)
    for key in ('stdin_protocol_error', 'private_config_on_http', 'transport_delivery_verified',
            'source_files_rehashed_by_service', 'native_comparison_performed', 'native_php_fallback', 'product_runtime'):
        equal(value[key], False)
    require(type(value['stdin_shutdown_observed']) is bool, 'session_http_control_eof_metric')
    for key, count in (('worker_executions', 3), ('valid_execution_limit', 3), ('concurrency', 1),
            ('module_compilations_total', 1), ('request_module_compilations_total', 0)):
        equal(value[key], count)
    equal(value['worker_build'], worker)
    equal(value['rejected_counts'], {'method': 1, 'target': 1, 'host': 1, 'headers': 3,
        'body': 1, 'busy': 0, 'budget': 0})
    for key in ('owned_reaped', 'captured_output_complete_after_reap', 'process_group_owned', 'parent_shutdown_input_closed'):
        equal(execution[key], True)
    equal(execution['forced_cleanup'], False)
    equal(execution['exit_code'], 0)
    equal(execution['stderr'], identity(b''))
    equal(value['session_ready'], expected_ready(module_sha))
    session.validate_final(value['session_final'], 3, True)
    child = value['session_process']
    closed(child, ['owned_pid', 'reaped', 'exit_code', 'stdout_eof', 'stderr_eof', 'stdout', 'stderr',
        'ready_line', 'final_line', 'identity_after_verified'])
    integer(child['owned_pid'], 2**32 - 1, 1)
    require(child['owned_pid'] != execution['owned_pid'], 'distinct_session_http_child_pid')
    for key in ('reaped', 'stdout_eof', 'stderr_eof', 'identity_after_verified'):
        equal(child[key], True)
    equal(child['exit_code'], 0)
    equal(child['stderr'], identity(b''))
    byte_identity(child['stdout'], 16 * 1024 * 1024, 1)
    for label in ('ready', 'final'):
        equal(child[label + '_line'], identity(canonical(value['session_' + label]) + b'\n'))

    require(type(value['requests']) is list and len(value['requests']) == 3, 'session_http_three_requests')
    validated = {}
    inputs = set()
    stdout_size = child['ready_line']['bytes'] + child['final_line']['bytes']
    validate_summary = summary_validator()
    for seq, (label, row) in enumerate(zip(LABELS, value['requests']), 1):
        case = 'home' if label == 'home' else 'missing'
        closed(row, ['case', 'seq', 'frame', 'worker_summary', 'response_constructed',
            'anonymous_fixture_request_authorized'])
        equal(row['seq'], seq)
        equal(row['case'], case)
        equal(row['response_constructed'], True)
        equal(row['anonymous_fixture_request_authorized'], True)
        frame = row['frame']
        closed(frame, ['input', 'acknowledgement'])
        byte_identity(frame['input'], 24 * 1024, 1)
        byte_identity(frame['acknowledgement'], 4 * 1024 * 1024, 1)
        inputs.add(frame['input']['sha256'])
        stdout_size += frame['acknowledgement']['bytes']
        summary = row['worker_summary']
        validated[label] = validate_summary(summary, case, native['requests'][label]['observation'])
        for key in ('autoload_option_count', 'autoload_max_value_bytes'):
            equal(summary[key], report['independent_context_checks'][label][key])
    equal(len(inputs), 3)
    equal(child['stdout']['bytes'], stdout_size)
    equal(value['requests'][1]['worker_summary']['response_summary'],
        value['requests'][2]['worker_summary']['response_summary'])
    report['validations'].update(validated)


def self_test():
    tests = []
    for backend in ('mysql', 'mariadb'):
        value, execution, worker, native, report = http.synthetic_server(backend)
        module_sha = '1' * 64
        ready = expected_ready(module_sha)
        final = {'schema': 'recastpress.php-wasix-wp-session-observation.v0.5', 'passed': True,
            'outcome': 'fixed_wp_session_passed', 'module_compilations_total': 1, 'requests_completed': 3,
            'all_requests_cleaned': True, 'request_module_compilations_total': 0, 'private_ipc_only': True,
            'publication_authorized': False, 'native_comparison_performed': False, 'error': None}
        ready_line = identity(canonical(ready) + b'\n')
        final_line = identity(canonical(final) + b'\n')
        value.update(schema=PREFIX + 'observation.v0.5', outcome='fixed_session_http_passed',
            session_ready=ready, session_final=final, module_compilations_total=1, request_module_compilations_total=0,
            session_process={'owned_pid': 10001, 'reaped': True, 'exit_code': 0, 'stdout_eof': True,
                'stderr_eof': True, 'stdout': {'bytes': ready_line['bytes'] + final_line['bytes'] + 3, 'sha256': '2' * 64},
                'stderr': identity(b''), 'ready_line': ready_line, 'final_line': final_line, 'identity_after_verified': True})
        execution.update(process_group_owned=True, parent_shutdown_input_closed=True)
        for seq, row in enumerate(value['requests'], 1):
            del row['worker']
            row.update(seq=seq, frame={'input': identity(bytes([seq])), 'acknowledgement': identity(b'x')})
            row['worker_summary'].update(schema=PREFIX + 'worker-summary.v0.5', worker_schema=session.SCHEMA,
                outcome='fixed_session_request_passed', module_compilations=0, compiled_module_reused=True,
                module_compiled_during_request=False)
        validate_service(value, execution, worker, module_sha, native, report)
        tests.append(backend + '-synthetic-success')
        for label, mutate in (
            ('compile_relabelled', lambda v: v['requests'][0]['worker_summary'].update(module_compilations=1)),
            ('raw_ack', lambda v: v['requests'][0]['frame'].update(raw_ack='private')),
            ('wrong_sequence', lambda v: v['requests'][1].update(seq=1)),
            ('bad_ready', lambda v: v['session_ready'].update(module_sha256='0' * 64)),
            ('unreaped', lambda v: v['session_process'].update(reaped=False)),
            ('short_stdout', lambda v: v['session_process']['stdout'].update(bytes=1)),
            ('not_cleaned', lambda v: v['session_final'].update(all_requests_cleaned=False)),
        ):
            changed = copy.deepcopy(value)
            mutate(changed)
            result = {'validations': {}, 'independent_context_checks': report['independent_context_checks']}
            try:
                validate_service(changed, execution, worker, module_sha, native, result)
            except (ValueError, KeyError, TypeError):
                require(not result['validations'], 'partial_session_http_validation_published')
            else:
                raise ValueError('session_http_controller_mutant_accepted')
            tests.append(backend + '-' + label)
    return {'passed': True, 'test_count': len(tests), 'tests': tests, 'synthetic_service_records_only': True}


def observe(server, label, reference=None):
    request = observer.REQUESTS[label]
    started = time.time()
    wire = observer.request(server.port, request, timeout=10 if reference is None else 310)
    finished = time.time()
    value = observer.observe_rejection(wire, label) if reference is None else observer.observe_wire(wire, reference)
    server.check_captures()
    return {**value, 'request_wire': identity(request), 'request_time_window': http.date_window(value, started, finished)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'])
    parser.add_argument('--record', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return 0
    require(args.backend is not None, 'backend_required')
    report = {'schema': PREFIX + 'check.v0.5', 'passed': False, 'backend': args.backend, 'platform': sys.platform,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'observations': {}, 'validations': {},
        'http_observations': {}, 'http_rejections': {}, 'real_http_listener_exercised': False,
        'native_php_executed': False, 'native_php_fallback': False, 'product_runtime_selected': False,
        'response_cache_exercised': False, 'shared_php_state_exercised': False,
        'shared_database_connection_exercised': False, 'owned_cleanup_completed': False}
    passed = False
    database = server = None
    stage = 'sources'
    try:
        require(sys.platform == 'linux', 'actual_php_wasm_session_http_linux_only')
        before = report['sources_before'] = sources()
        adapter = report['build_receipt'] = build.verify_build()
        worker = report['session_build_receipt'] = session.build.verify_build()
        bootstrap = report['bootstrap_build_receipt'] = context.oldbuild.verify_build()
        module = report['module_provenance'] = context.managed.verify_build()
        module_sha = module['module']['sha256']
        expected_worker = worker_identity(worker, session.build.read(session.build.RECEIPT, 2 * 1024 * 1024))
        report['http_observer_pure_checks'] = observer.self_test()
        report['http_controller_pure_checks'] = self_test()
        references = observer.native_references()[args.backend]
        native = context.linux.reference(args.backend)
        native_boot = context.original.reference(args.backend)
        environment = session.build.environment()
        database = context.linux.Database(args.backend, report)
        with database:
            for mode in ('install', 'read'):
                stage = 'php_wasm_' + mode
                result = context.execute(ROOT / bootstrap['executable'], environment, database.request(mode), report, mode)
                report['validations'][mode] = context.original.validate(result, mode, native_boot[mode])
            database.seed_public_dates()
            database.verify_context_state('before_requests')
            stage = 'http_server_startup'
            payload = {'schema': PREFIX + 'input.v0.5', 'database': database.home_request()}
            server = process.Server(ROOT / adapter['executable'], environment, payload, report,
                canonical, identity, http.process.parse_json)
            with server:
                equal(server.ready, expected_ready(module_sha))
                report['real_http_listener_exercised'] = True
                for label in observer.REJECTIONS:
                    stage = 'http_rejection_' + label
                    report['http_rejections'][label] = observe(server, label)
                for label in LABELS:
                    stage = 'http_public_' + label
                    case = 'home' if label == 'home' else 'missing'
                    report['http_observations'][label] = observe(server, case, references[case])
                    database.verify_context_state(label)
                    require(sources() == before, 'sources_after_session_http_request_changed')
                stage = 'http_server_final'
                completed = server.finish()
                validate_service(completed, report['http_server_execution'], expected_worker, module_sha, native, report)
                equal(completed['session_ready'], server.ready)
                report['http_server_observation'] = completed
        require(report['container_stopped'] is True and report['container_removed'] is True
            and report['container_normal_exit_verified'] is True, 'normal_session_http_database_cleanup')
        stage = 'after_verification'
        report['sources_after'] = sources()
        equal(report['sources_after'], before)
        equal(build.verify_build(), adapter)
        equal(context.managed.verify_build(), module)
        report['source_reverified_after'] = True
        passed = True
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        try:
            process.reap_owned_servers()
            context.owned_probe.reap_owned_probes()
            require(server is None or server.row['owned_reaped'] is True, 'session_http_parent_not_reaped')
            report['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt):
            passed = False
            report['cleanup_failed'] = True
    report.update(passed=passed, outcome='fixed_compile_once_wp_real_http_passed' if passed else 'compile_once_wp_real_http_failed')
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'session_http_public_secret_guard')
    require(len(wire) <= 12 * 1024 * 1024, 'session_http_public_record_quota')
    if args.record:
        build.preserve(HERE / f'evidence/linux-{args.backend}.json', wire)
    print(report['outcome'])
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
