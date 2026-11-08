"""Compare a fixed WordPress plugin on PHP-Wasm and the owned Rust DB path."""

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXPERIMENTS = HERE.parent
TARGET = ROOT / 'target/php-wasix-wp-bootstrap-probe/cargo-target'
NAME = 'recastpress-php-wasix-wp-plugin-admission-probe'
PREFIX = 'recastpress.php-wasix-wp-plugin-admission-'
PLUGIN = 'rp-admission-benign/rp-admission-benign.php'
MARKER = b'<!-- rp-admission-benign:wpdb-and-request-cache-ok -->'
LABELS = ('inactive', 'activation', 'active', 'active_repeat')
NATIVE_SHA = {
    'mysql': 'b3b36ce0d56be4b4099baf4a9ce8cf24723b87f880a397c4647017b0b0fb1393',
    'mariadb': '9756bf43bb3fcd2d8ea54c5428593ea82073e69d0f28d964057ea5219c11ab40',
}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


private = load('rp_plugin_fixed_worker', EXPERIMENTS / 'php-wasix-wp-private-worker-probe/run_check.py')
admission = load('rp_plugin_admission', EXPERIMENTS / 'wp-plugin-admission-probe/admission.py')
context = private.context
require, canonical, sha = private.require, private.canonical, private.sha


def exact(value, expected, code):
    require(canonical(value) == canonical(expected), code)


def source_inputs():
    inputs = dict(private.build.inputs())
    paths = [HERE / name for name in ('Cargo.toml', 'Cargo.lock', 'derive_guest.py', 'derive_worker.py', 'run_check.py')]
    paths += [ROOT / '.github/workflows/php-wasix-wp-plugin-admission.yml']
    paths += [EXPERIMENTS / 'wp-plugin-admission-probe/admission.py']
    paths += sorted((EXPERIMENTS / 'wp-plugin-admission-probe/fixtures').glob('*.php'))
    paths += [EXPERIMENTS / 'wp-plugin-admission-probe/evidence' / f'windows-{backend}-native.json' for backend in NATIVE_SHA]
    for path in paths:
        inputs[path.relative_to(ROOT).as_posix()] = sha(path.read_bytes())
    return inputs


def identity(wire):
    return {'bytes': len(wire), 'sha256': sha(wire)}


def generated_inputs():
    paths = sorted((HERE / 'src').glob('*.rs')) + sorted((HERE / 'fixtures').glob('*.php'))
    require(len(paths) == 10, 'plugin_generated_input_inventory')
    return {path.relative_to(HERE).as_posix(): identity(path.read_bytes()) for path in paths}


def build_worker(report):
    commands = report['build_commands'] = []
    environment = private.build.environment()
    command_lines = [
        ('guest_derivation', [sys.executable, '-B', str(HERE / 'derive_guest.py')], 60),
        ('worker_derivation', [sys.executable, '-B', str(HERE / 'derive_worker.py')], 60),
        ('worker_units', ['cargo', 'test', '--locked', '--offline', '--release', '--quiet',
            '--manifest-path', str(HERE / 'Cargo.toml'), '--target-dir', str(TARGET)], 1200),
        ('worker_build', ['cargo', 'build', '--locked', '--offline', '--release', '--message-format=json-render-diagnostics',
            '--manifest-path', str(HERE / 'Cargo.toml'), '--target-dir', str(TARGET)], 1200),
    ]
    for label, command, timeout in command_lines:
        pid, result = context.owned_probe.run_captured_probe(command, environment, timeout=timeout)
        commands.append({'stage': label, 'owned_pid': pid, 'owned_reaped': True, 'exit_code': result.returncode,
            'stdout': identity(result.stdout), 'stderr': identity(result.stderr)})
        require(result.returncode == 0 and len(result.stdout) <= 16 * 1024 * 1024
            and len(result.stderr) <= 65536, 'plugin_' + label + '_failed')
        if label == 'worker_units':
            units = re.findall(rb'test result: ok\. ([0-9]+) passed; 0 failed; 0 ignored;', result.stdout)
            require(len(units) == 1 and int(units[0]) > 0, 'plugin_worker_unit_inventory')
            report['worker_units_passed'] = int(units[0])
    executable = TARGET / 'release' / NAME
    artifacts = []
    for line in result.stdout.splitlines():
        event = private.strict_json(line)
        if event['reason'] == 'compiler-artifact' and event['target']['name'] == NAME:
            require(event['target']['kind'] == ['bin'] and event['profile']['test'] is False
                and Path(event['manifest_path']) == HERE / 'Cargo.toml'
                and Path(event['target']['src_path']) == HERE / 'src/main.rs', 'plugin_fixed_cargo_source')
            artifacts.append(Path(event['executable']))
    require(artifacts == [executable] and event['reason'] == 'build-finished' and event['success'] is True,
        'plugin_fixed_executable_artifact')
    require(executable.resolve(strict=True) == executable and 0 < executable.stat().st_size <= 256 * 1024 * 1024,
        'plugin_fixed_executable_size')
    report['worker_build'] = {'executable': executable.relative_to(ROOT).as_posix(),
        'executable_identity': identity(executable.read_bytes()), 'locked_offline': True}
    report['generated_inputs'] = generated_inputs()
    return executable


def reference(backend):
    path = EXPERIMENTS / 'wp-plugin-admission-probe/evidence' / f'windows-{backend}-native.json'
    wire = path.read_bytes()
    exact(sha(wire), NATIVE_SHA[backend], 'fixed_native_plugin_reference')
    value = private.strict_json(wire)
    require(value['passed'] is True and value['activation_via_wordpress'] is True
        and value['fresh_response_repeat_exact'] is True, 'accepted_native_plugin_reference')
    return value


def sql_active(database, label, active):
    serialized = f'a:1:{{i:0;s:{len(PLUGIN)}:"{PLUGIN}";}}' if active else 'a:0:{}'
    result = database.run('independent_plugin_option_' + label, database.client() + [
        "--execute=SELECT HEX(option_value) FROM rp_wpdb_probe.rp_options WHERE option_name='active_plugins'"])
    require(not result.stderr and result.stdout.strip() == serialized.encode('ascii').hex().upper().encode('ascii'),
        'plugin_independent_active_option')


def lifecycle(value, stage):
    exact(value['schema'], PREFIX + 'observation.v0.5', 'plugin_worker_schema')
    exact(value['stage'], stage, 'plugin_worker_stage')
    exact(value['outcome'], 'fixed_plugin_admission_worker_passed', 'plugin_worker_outcome')
    exact(value['module_compilations'], 1, 'plugin_worker_compile_count')
    for field in ('passed', 'private_ipc_only', 'fresh_runtime', 'full_wordpress_bootstrap',
            'physical_connection_created', 'physical_disconnect_completed', 'db_actor_joined',
            'db_actor_exited', 'source_rechecked_after_execution'):
        exact(value[field], True, 'plugin_worker_completion')
    for field in ('publication_authorized', 'native_comparison_performed', 'native_php_fallback',
            'guest_networking', 'host_environment_forwarded', 'db_credentials_supplied_to_guest'):
        exact(value[field], False, 'plugin_worker_scope')
    exact(value['db_actor_failure'], None, 'plugin_actor_failure')
    capture = value['cgi_capture']
    for field in ('guest_exit_success', 'runtime_shutdown_returned', 'stdout_eof', 'stderr_eof'):
        exact(capture[field], True, 'plugin_capture_completion')
    for field in ('stdout_overflow', 'stderr_overflow'):
        exact(capture[field], False, 'plugin_capture_quota')
    exact(capture['stderr_bytes'], 0, 'plugin_guest_stderr')
    rpc = value['rpc']
    for field in ('finished', 'opened_once', 'close_dispatched'):
        exact(rpc[field], True, 'plugin_rpc_completion')
    for field in ('pending_bytes', 'unread_response_bytes'):
        exact(rpc[field], 0, 'plugin_rpc_pending')
    exact(rpc['failure'], None, 'plugin_rpc_failure')
    require(type(rpc['operations']) is int and 1 <= rpc['operations'] <= 4096, 'plugin_operation_count')
    access = value['managed_access']
    exact(access['finished'], True, 'plugin_access_completion')
    for field in ('pending_bytes', 'unread_response_bytes', 'active_handles', 'invalid', 'io_errors'):
        exact(access[field], 0, 'plugin_access_pending')
    exact(access['protocol_error'], None, 'plugin_access_failure')


def compare(value, label, expected, native_home):
    stage = 'activate' if label == 'activation' else 'render'
    lifecycle(value, stage)
    php = value['php_result']
    for field in ('active_before', 'active_after'):
        exact(php[field], expected[field], 'plugin_native_active_state')
    result = {'stage': stage, 'active_before': expected['active_before'], 'active_after': expected['active_after'],
        'operations': value['rpc']['operations'], 'module_compilations': 1, 'lifecycle_verified': True}
    if stage == 'activate':
        exact(value['phase'], 'full_private_activation_verified', 'plugin_activation_phase')
        exact(php['activation_succeeded'], True, 'plugin_activation_result')
        require('private_response' not in value and 'private_contexts' not in value, 'plugin_activation_no_theme_response')
        result['activation_succeeded'] = True
        return result, None
    exact(value['phase'], 'full_private_response_verified', 'plugin_render_phase')
    exact(php['activation_succeeded'], False, 'plugin_render_cannot_activate')
    response = value['private_response']
    reference = expected['validation']
    body = bytes.fromhex(response['body_hex'])
    for actual, want in ((len(body), reference['inner_body_bytes']), (sha(body), reference['inner_body_sha256']),
            (response['status'], reference['inner_status']), (len(response['headers_hex']), reference['inner_header_count']),
            (body.count(MARKER), expected['marker_count'])):
        exact(actual, want, 'plugin_native_render_difference')
    exact(response['headers_hex'], native_home['response']['headers_hex'], 'plugin_ordered_origin_headers')
    expected_home = copy.deepcopy(native_home)
    expected_home['response'].update(body_hex=body.hex(), body_bytes=len(body), body_sha256=sha(body))
    checked = copy.deepcopy(value)
    checked.update(schema=private.SCHEMA, outcome='fixed_private_wp_worker_passed')
    private.validate_private(checked, 'home', expected_home)
    result['response'] = {'status': response['status'], 'body': identity(body), 'marker_count': body.count(MARKER),
        'ordered_headers_match_native': True, 'public_context_matches_native': True}
    return result, body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=NATIVE_SHA, required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report = {'schema': PREFIX + 'check.v0.5', 'backend': args.backend, 'passed': False, 'requests': {}}
    database = None
    stage = 'build'
    try:
        require(sys.platform == 'linux', 'plugin_wasm_linux_only')
        report['sources_before'] = source_inputs()
        executable = build_worker(report)
        bootstrap = context.oldbuild.verify_build()
        report['module_provenance'] = context.managed.verify_build()
        native = reference(args.backend)
        native_home = context.linux.reference(args.backend)['requests']['home']['observation']
        bootstrap_reference = context.original.reference(args.backend)
        report['preactivation_decisions'] = admission.self_test()['decisions']
        environment = private.build.environment()
        owned = {'observations': {}}
        database = context.linux.Database(args.backend, owned)
        with database:
            for mode in ('install', 'read'):
                stage = mode
                result = context.execute(ROOT / bootstrap['executable'], environment, database.request(mode), owned, mode)
                context.original.validate(result, mode, bootstrap_reference[mode])
            database.seed_public_dates()
            sql_active(database, 'before', False)
            report['independent_active_plugins'] = {'before': []}
            bodies = {}
            for label in LABELS:
                stage = label
                mode = 'activate' if label == 'activation' else 'render'
                payload = {'schema': PREFIX + 'input.v0.5', 'stage': mode, 'database': database.home_request()}
                pid, execution = context.owned_probe.run_captured_probe([str(executable)], environment,
                    timeout=300, input_bytes=canonical(payload))
                report.setdefault('executions', {})[label] = {'owned_pid': pid, 'owned_reaped': True,
                    'exit_code': execution.returncode, 'stdout': identity(execution.stdout), 'stderr': identity(execution.stderr)}
                require(execution.stdout.endswith(b'\n')
                    and execution.stdout.count(b'\n') == 1 and len(execution.stdout) <= 4 * 1024 * 1024,
                    'plugin_worker_owned_capture')
                value = private.strict_json(execution.stdout)
                if type(value) is dict and value.get('passed') is False:
                    diagnostics = report['executions'][label]['diagnostics'] = {}
                    for field in ('phase', 'failure_reason'):
                        code = value.get(field)
                        if type(code) is str and re.fullmatch('[a-z_]{1,80}', code):
                            diagnostics[field] = code
                require(execution.returncode == 0 and not execution.stderr and type(value) is dict
                    and value['passed'] is True, 'plugin_worker_owned_execution')
                report['requests'][label], bodies[label] = compare(value, label, native['requests'][label], native_home)
                sql_active(database, label, label != 'inactive')
                report['independent_active_plugins'][label] = [] if label == 'inactive' else [PLUGIN]
            require(bodies['active'] == bodies['active_repeat']
                and bodies['active'].replace(MARKER, b'', 1) == bodies['inactive'], 'plugin_exact_marker_difference')
        stage = 'postflight'
        require(all(owned.get(field) is True for field in ('container_stopped', 'container_removed',
            'container_normal_exit_verified')), 'plugin_database_normal_cleanup')
        report['database'] = {field: owned[field] for field in ('image', 'image_config_digest', 'sql_server_version',
            'container_stopped', 'container_removed', 'container_normal_exit_verified')}
        require(len({row['owned_pid'] for row in report['executions'].values()}) == 4, 'plugin_distinct_request_processes')
        report['sources_after'] = source_inputs()
        exact(report['sources_before'], report['sources_after'], 'plugin_source_changed')
        exact(generated_inputs(), report['generated_inputs'], 'plugin_generated_inputs_changed')
        exact(identity(executable.read_bytes()), report['worker_build']['executable_identity'], 'plugin_executable_changed')
        exact(context.oldbuild.verify_build(), bootstrap, 'plugin_original_bootstrap_artifacts_changed')
        exact(context.managed.verify_build(), report['module_provenance'], 'plugin_module_changed')
        report.update(passed=True, independent_activation_state_verified=True, native_comparison_controller_only=True,
            one_footer_marker_added=True, fresh_repeat_exact=True, owned_database_normal_cleanup=True)
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if type(error) is ValueError and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        context.owned_probe.reap_owned_probes()
    wire = (json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True, allow_nan=False) + '\n').encode()
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'plugin_secret_guard')
    if args.record:
        private.build.preserve(HERE / f'evidence/linux-{args.backend}.json', wire)
    print('WordPress Wasm plugin trial: ' + ('passed' if report['passed'] else report.get('failure', 'failed')))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
