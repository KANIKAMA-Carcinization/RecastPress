"""Real loopback HTTP -> owned Rust worker -> PHP WASIX -> owned database."""
import argparse
from datetime import datetime, timezone
import email.utils
import importlib.util
import json
from pathlib import Path
import re
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


context = load('rp_http_frozen_context_controller', ROOT / 'experiments/php-wasix-wp-request-context-probe/run_check.py')
build = load('rp_http_owned_build', HERE / 'build_check.py')
observer = load('rp_http_fixed_observer', HERE / 'http_observation.py')
process = load('rp_http_owned_server', HERE / 'server_process.py')
require, sha, canonical = context.require, context.sha, context.canonical


def sources():
    return {'input_files': build.inputs(), 'context': context.sources(), 'http_observer_inputs': observer.input_hashes()}


def date_window(observation, started, finished):
    value = observation['transport_headers']['date_gmt']
    if value is not None:
        stamp = email.utils.parsedate_to_datetime(value).timestamp()
        require(started - 2 <= stamp <= finished + 2, 'actual_http_date_outside_request_window')
    return {'started_utc': datetime.fromtimestamp(started, timezone.utc).isoformat(),
        'finished_utc': datetime.fromtimestamp(finished, timezone.utc).isoformat(), 'date_within_window_if_present': True}


def validate_server(value, execution, worker_build, native, report):
    require(value['schema'] == 'recastpress.php-wasix-wp-http-observation.v0.5'
        and value['passed'] is True and value['outcome'] == 'fixed_http_context_passed', 'actual_http_server_success')
    require(value['worker_executions'] == value['valid_execution_limit'] == 3 and value['concurrency'] == 1,
        'three_serial_fresh_http_executions')
    for key in ('bound_loopback_only', 'graceful_shutdown_completed', 'startup_private_input_used'):
        require(value[key] is True, 'actual_http_' + key)
    for key in ('private_config_on_http', 'transport_delivery_verified', 'native_php_fallback', 'product_runtime'):
        require(value[key] is False, 'server_unclaimed_' + key)
    require(execution['owned_reaped'] is True and execution['forced_cleanup'] is False
        and execution['exit_code'] == 0 and execution['captured_output_complete_after_reap'] is True,
        'actual_http_server_normal_reap')
    require(execution['stderr'] == {'bytes': 0, 'sha256': sha(b'')}, 'actual_http_server_empty_stderr')
    expected_worker = {'receipt_path': 'experiments/php-wasix-wp-request-context-probe/evidence/linux-build.json',
        'receipt_sha256': sha(context.build.read(context.build.RECEIPT, 2*1024*1024)),
        'executable': worker_build['executable'], 'executable_identity': worker_build['executable_identity'],
        'source_inputs': 195, 'units': 58}
    require(value['worker_build'] == expected_worker, 'same_job_http_worker_build')
    require(value['rejected_counts'] == {'method': 1, 'target': 1, 'host': 1, 'headers': 3, 'body': 1, 'busy': 0, 'budget': 0},
        'seven_http_rejections_without_worker_execution')
    require(type(value['requests']) is list and len(value['requests']) == 3, 'three_http_worker_records')
    pids = []
    for row, label in zip(value['requests'], ('home', 'missing', 'missing_repeat')):
        case = 'home' if label == 'home' else 'missing'
        require(row['case'] == case and row['response_constructed'] is True, 'actual_http_worker_case')
        worker = row['worker']; pids.append(worker['owned_pid'])
        require(type(worker['owned_pid']) is int and worker['owned_pid'] > 0
            and worker['reaped'] is True and worker['exit_code'] == 0
            and worker['stdout_eof'] is True and worker['stderr_eof'] is True
            and worker['identity_before'] == expected_worker and worker['identity_after_verified'] is True,
            'actual_http_worker_owned_completion')
        require(worker['stderr'] == {'bytes': 0, 'sha256': sha(b'')}, 'actual_http_worker_clean_stderr')
        actual = row['observation']
        wire = canonical(actual) + b'\n'
        require(worker['stdout'] == {'bytes': len(wire), 'sha256': sha(wire)}, 'actual_http_worker_public_stdout')
        report['validations'][label] = context.validate_context(actual, case, native['requests'][label]['observation'])
        state = report['independent_context_checks'][label]
        for key in ('autoload_option_count', 'autoload_max_value_bytes'):
            require(state[key] == actual['php_result'][key], 'http_independent_' + key)
    require(len(set(pids)) == 3 and execution['owned_pid'] not in pids, 'distinct_http_worker_processes')
    require(value['requests'][1]['observation']['php_result']['response']
        == value['requests'][2]['observation']['php_result']['response'], 'fresh_http_repeat_404_origin_response')
    report['fresh_repeat_404_response_exact'] = True
    report['all_three_owned_workers_reaped'] = True
    report['request_module_compilations'] = sum(row['observation']['module_compilations'] for row in value['requests'])
    require(report['request_module_compilations'] == 3, 'http_three_fresh_worker_module_compilations')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--record', action='store_true'); args = parser.parse_args()
    report = {'schema': 'recastpress.php-wasix-wp-http-check.v0.5', 'passed': False, 'outcome': 'not_run',
        'backend': args.backend, 'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': sys.platform,
        'observations': {}, 'validations': {}, 'http_observations': {}, 'http_rejections': {},
        'real_http_listener_exercised': False, 'native_php_executed': False, 'native_php_fallback': False,
        'product_runtime_selected': False, 'standard_wpdb_compatibility_verified': False,
        'request_or_body_normalized': False, 'module_cache_exercised': False,
        'memory_cpu_limits_verified_for_this_integration': False, 'all_resources_immediately_reclaimed': False}
    passed = False; database = None; server = None; stage = 'source_preparation'
    try:
        require(sys.platform == 'linux', 'actual_php_wasm_http_linux_only')
        before = report['sources_before'] = sources()
        http_build = report['build_receipt'] = build.verify_build()
        worker_build = report['context_build_receipt'] = context.build.verify_build()
        bootstrap_build = report['bootstrap_build_receipt'] = context.oldbuild.verify_build()
        report['theme_build_receipt'] = context.themebuild.verify_build()
        report['module_provenance'] = context.managed.verify_build()
        report['http_observer_pure_checks'] = observer.self_test()
        references = observer.native_references()[args.backend]
        native = context.linux.reference(args.backend)
        native_boot = context.original.reference(args.backend)
        database = context.linux.Database(args.backend, report)
        with database:
            for mode in ('install', 'read'):
                stage = 'php_wasm_' + mode
                actual = context.execute(ROOT / bootstrap_build['executable'], build.environment(),
                    database.request(mode), report, mode)
                report['validations'][mode] = context.original.validate(actual, mode, native_boot[mode])
            database.seed_public_dates(); database.verify_context_state('before_requests')
            stage = 'http_server_startup'
            payload = {'schema': 'recastpress.php-wasix-wp-http-input.v0.5', 'database': database.home_request()}
            server = process.Server(ROOT / http_build['executable'], build.environment(), payload, report)
            with server:
                report['real_http_listener_exercised'] = True
                for label in observer.REJECTIONS:
                    stage = 'http_rejection_' + label
                    started = time.time()
                    wire = observer.request(server.port, observer.REQUESTS[label], timeout=10)
                    finished = time.time()
                    observation = observer.observe_rejection(wire, label)
                    report['http_rejections'][label] = {**observation, 'request_wire': process.identity(observer.REQUESTS[label]),
                        'request_time_window': date_window(observation, started, finished)}
                    server.check_captures()
                for case, label in (('home', 'home'), ('missing', 'missing'), ('missing', 'missing_repeat')):
                    stage = 'http_public_' + label
                    started = time.time()
                    wire = observer.request(server.port, observer.REQUESTS[case])
                    finished = time.time()
                    observation = observer.observe_wire(wire, references[case])
                    report['http_observations'][label] = {**observation,
                        'request_wire': process.identity(observer.REQUESTS[case]),
                        'request_time_window': date_window(observation, started, finished)}
                    database.verify_context_state(label)
                    require(sources() == before, 'sources_after_actual_http_request_changed')
                    server.check_captures()
                stage = 'http_server_final_observation'
                report['http_server_observation'] = server.finish()
            validate_server(report['http_server_observation'], report['http_server_execution'], worker_build, native, report)
        require(report['container_stopped'] is True and report['container_removed'] is True
            and report['container_normal_exit_verified'] is True, 'normal_http_database_cleanup')
        stage = 'after_verification'
        report['sources_after'] = sources()
        require(report['sources_after'] == before and build.verify_build() == http_build
            and context.build.verify_build() == worker_build and context.oldbuild.verify_build() == bootstrap_build
            and context.themebuild.verify_build() == report['theme_build_receipt'], 'http_sources_builds_or_binaries_changed')
        report['module_provenance_after'] = context.managed.verify_build()
        require(report['module_provenance_after'] == report['module_provenance'], 'http_module_changed')
        report['source_reverified_after'] = True; passed = True
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        if server is not None and not server.finished:
            try: server.abort()
            except (Exception, KeyboardInterrupt): passed = False; report['http_server_cleanup_failed'] = True
        try:
            context.owned_probe.reap_owned_probes(); report['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt): passed = False; report['owned_cleanup_failed'] = True
    report.update(passed=passed, outcome='fixed_real_http_php_wasm_passed' if passed else 'real_http_php_wasm_failed')
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'http_public_secret_guard')
    require(len(wire) <= 12*1024*1024, 'http_public_record_quota')
    if args.record: build.preserve(HERE / ('evidence/linux-' + args.backend + '.json'), wire)
    print(report['outcome']); return 0 if passed else 1


if __name__ == '__main__': sys.exit(main())
