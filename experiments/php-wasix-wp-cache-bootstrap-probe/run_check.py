"""Compare a managed cache shim with an ordinary PHP-Wasm WordPress request."""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXPERIMENTS = HERE.parent
BASE = EXPERIMENTS / 'php-wasix-wp-private-worker-probe'
PREFIX = 'recastpress.php-wasix-wp-cache-bootstrap-'
NAME = 'recastpress-php-wasix-wp-cache-bootstrap-probe'
GROUPS = {'rp-cache-multiple': 5, 'rp-cache-integer': 4}
TRIAL = {'shim_late_hook': True, 'external_cache_before': False, 'external_cache_after': False,
    'stock_restored': True, 'stock_sentinel_preserved': True, 'channel_closed': True, 'groups': GROUPS,
    'finish': {'ok': True, 'operations': 9, 'entries': 0, 'charged_bytes': 0}}


def load(path):
    spec = importlib.util.spec_from_file_location('rp_cache_bootstrap_private_baseline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


private = load(BASE / 'run_check.py')
context = private.context
require, canonical, sha = private.require, private.canonical, private.sha


def exact(value, expected, code):
    require(canonical(value) == canonical(expected), code)


def identity(wire):
    return {'bytes': len(wire), 'sha256': sha(wire)}


def sources():
    paths = [ROOT / '.github/workflows/php-wasix-wp-cache-bootstrap.yml',
        EXPERIMENTS / 'php-wasix-wp-object-cache-probe/derive_cache.py',
        EXPERIMENTS / 'php-wasix-wp-object-cache-probe/derive_guest.py',
        EXPERIMENTS / 'php-wasix-wp-object-cache-probe/src/cache_fs.rs',
        EXPERIMENTS / 'wp-object-cache-rust-probe/fixtures/adapter.php',
        EXPERIMENTS / 'wp-object-cache-rust-probe/src/main.rs',
        EXPERIMENTS / 'wp-object-cache-rust-probe/Cargo.lock',
        EXPERIMENTS / 'wp-object-cache-dropin-probe/fixtures/stock-transparent.php',
        HERE / 'src/trial.rs', ROOT / 'crates/recastpress-core/Cargo.toml',
        ROOT / 'crates/recastpress-core/src/lib.rs', ROOT / 'crates/recastpress-core/src/resource_budget.rs']
    paths.extend(path for path in HERE.iterdir() if path.suffix in ('.py', '.toml', '.lock', '.php', '.rs')
        and path.name != 'import_evidence.py')
    inventory = dict(private.build.inputs())
    inventory.update({path.relative_to(ROOT).as_posix(): sha(path.read_bytes()) for path in paths})
    return dict(sorted(inventory.items()))


def generated():
    paths = sorted((HERE / 'src').glob('*.rs')) + sorted((HERE / 'fixtures').glob('*.php'))
    paths = [path for path in paths if path != HERE / 'src/trial.rs']
    require({'src/main.rs', 'src/cache_host.rs', 'src/catalog.rs', 'fixtures/bootstrap.php',
        'fixtures/object-cache.php', 'fixtures/cache-trial.php', 'fixtures/cache-adapter.php'}
        == {p.relative_to(HERE).as_posix() for p in paths},
        'cache_bootstrap_generated_inventory')
    return {path.relative_to(HERE).as_posix(): identity(path.read_bytes()) for path in paths}


def execute(label, command, env, timeout, report, input_bytes=None):
    pid, result = context.owned_probe.run_captured_probe(command, env, timeout=timeout, input_bytes=input_bytes)
    report[label] = {'owned_pid': pid, 'owned_reaped': True, 'exit_code': result.returncode,
        'stdout': identity(result.stdout), 'stderr': identity(result.stderr)}
    return result


def build(directory, name, env, report):
    target = private.build.TARGET
    manifest = directory / 'Cargo.toml'
    args = ['--offline', '--locked', '--release', '--manifest-path', str(manifest), '--target-dir', str(target)]
    if directory == HERE:
        units = execute('units', ['cargo', 'test', '--quiet', *args], env, 1200, report['build_commands'])
        matched = re.findall(rb'test result: ok\. ([0-9]+) passed; 0 failed; 0 ignored;', units.stdout)
        require(units.returncode == 0 and len(matched) == 1 and int(matched[0]) > 0, 'cache_bootstrap_worker_units')
        report['units_passed'] = int(matched[0])
    result = execute(name, ['cargo', 'build', '--message-format=json-render-diagnostics', *args],
        env, 1200, report['build_commands'])
    require(result.returncode == 0, 'cache_bootstrap_worker_build')
    events = [private.strict_json(line) for line in result.stdout.splitlines()]
    artifact = [row for row in events if row['reason'] == 'compiler-artifact' and row['target']['name'] == name]
    require(len(artifact) == 1 and events[-1] == {'reason': 'build-finished', 'success': True},
        'cache_bootstrap_artifact_inventory')
    row = artifact[0]
    binary = target / 'release' / name
    require(row['target']['kind'] == ['bin'] and row['profile']['test'] is False
        and Path(row['manifest_path']) == manifest and Path(row['target']['src_path']) == directory / 'src/main.rs'
        and Path(row['executable']) == binary and binary.resolve(strict=True) == binary
        and 0 < binary.stat().st_size <= 256 * 1024 * 1024, 'cache_bootstrap_artifact_path')
    report['binaries'][name] = identity(binary.read_bytes())
    return binary


def run_home(binary, payload, env, label, native, report):
    execution = execute(label, [str(binary)], env, 300, report['executions'], canonical(payload))
    require(0 < len(execution.stdout) <= 4 * 1024 * 1024 and execution.stdout.endswith(b'\n')
        and execution.stdout.count(b'\n') == 1, 'cache_bootstrap_private_frame')
    worker = private.strict_json(execution.stdout)
    if type(worker) is dict and worker.get('passed') is False:
        for field in ('phase', 'failure_reason'):
            code = worker.get(field)
            if type(code) is str and re.fullmatch('[a-z][a-z0-9_]{0,80}', code):
                report['executions'][label][field] = code
    require(execution.returncode == 0 and not execution.stderr, 'cache_bootstrap_private_execution')
    if label == 'shim':
        exact(worker['schema'], PREFIX + 'observation.v0.5', 'cache_bootstrap_worker_schema')
        exact(worker['cache_trial'], TRIAL, 'cache_bootstrap_guest_ack')
        shim = report['generated']['fixtures/object-cache.php']
        exact(worker['cache_shim_source'], {**shim, 'guest': '/wordpress/wp-content/object-cache.php',
            'guest_read_only': True}, 'cache_bootstrap_shim_source')
        transport = worker['cache_transport']
        expected = {'operations': 9, 'entries': 0, 'charged_bytes': 0, 'flushes': 0, 'groups': GROUPS,
            'opened_once': True, 'channel_open': False, 'finished': True, 'finish_dispatched': True,
            'pending_bytes': 0, 'unread_response_bytes': 0}
        require(set(transport) == set(expected) | {'request_bytes', 'response_bytes', 'response_read_bytes'},
            'cache_bootstrap_rust_channel_fields')
        for key, value in expected.items():
            exact(transport[key], value, 'cache_bootstrap_rust_channel')
        require(type(transport['request_bytes']) is int and 0 < transport['request_bytes'] <= 65536
            and type(transport['response_bytes']) is int and 0 < transport['response_bytes'] <= 65536,
            'cache_bootstrap_rust_byte_bounds')
        exact(transport['response_read_bytes'], transport['response_bytes'], 'cache_bootstrap_rust_reply_drained')
        report['cache_trial'] = TRIAL
        report['cache_transport'] = transport
        worker['schema'] = private.SCHEMA
    else:
        require('cache_trial' not in worker and 'cache_transport' not in worker, 'cache_bootstrap_baseline_has_channel')
    validation = private.validate_private(worker, 'home', native)
    response = worker['private_response']
    report['requests'][label] = {'validation': validation, 'response_status': response['status'],
        'response_body': identity(bytes.fromhex(response['body_hex'])),
        'headers_count': len(response['headers_hex']), 'db_operations': worker['rpc']['operations']}
    return worker


def compare_requests(baseline, shim):
    for key in ('private_response', 'private_contexts'):
        exact(shim[key], baseline[key], 'cache_bootstrap_unchanged_' + key)

    def comparable_rpc(worker):
        hello, *remaining = worker['operations']
        require(hello['op'] == 'hello' and hello['ok'] is True, 'cache_bootstrap_rpc_hello')
        # Each fresh DB connection has its own ID in the hello response.
        # Keep its request and result shape, and compare every subsequent frame.
        first = dict(hello)
        hello_bytes = first.pop('reply_frame_bytes')
        first.pop('reply_frame_sha256')
        totals = dict(worker['rpc'])
        for key in ('response_bytes', 'response_read_bytes'):
            require(totals[key] >= hello_bytes, 'cache_bootstrap_rpc_hello_size')
            totals[key] -= hello_bytes
        return [first, *remaining], totals

    expected_frames, expected_totals = comparable_rpc(baseline)
    frames, totals = comparable_rpc(shim)
    exact(frames, expected_frames, 'cache_bootstrap_unchanged_db_frames')
    exact(totals, expected_totals, 'cache_bootstrap_unchanged_db_totals')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('mysql', 'mariadb'), required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report = {'schema': PREFIX + 'check.v0.5', 'backend': args.backend, 'passed': False,
        'executions': {}, 'requests': {}, 'binaries': {}, 'build_commands': {}}
    database = None
    stage = 'source_preflight'
    try:
        require(sys.platform == 'linux', 'cache_bootstrap_linux_only')
        env = private.build.environment()
        report['sources_before'] = sources()
        bootstrap = context.oldbuild.verify_build()
        report['module'] = context.managed.verify_build()
        stage = 'derive'
        for script in ('derive_guest.py', 'derive_worker.py'):
            result = execute(script, [sys.executable, '-B', str(HERE / script)], env, 60, report['build_commands'])
            require(result.returncode == 0, 'cache_bootstrap_source_derivation')
        report['generated'] = generated()
        stage = 'build'
        baseline = build(BASE, private.build.NAME, env, report)
        shim = build(HERE, NAME, env, report)
        native = context.linux.reference(args.backend)['requests']['home']['observation']
        native_bootstrap = context.original.reference(args.backend)
        owned = {'observations': {}}
        database = context.linux.Database(args.backend, owned)
        with database:
            for mode in ('install', 'read'):
                stage = mode
                value = context.execute(ROOT / bootstrap['executable'], env, database.request(mode), owned, mode)
                context.original.validate(value, mode, native_bootstrap[mode])
            stage = 'seed'
            database.seed_public_dates()
            database.verify_context_state('before_requests')
            payload = database.context_request('home')
            payload['schema'] = private.INPUT
            workers = {}
            # Let the first stock home request finish any one-time database
            # work before comparing two fresh requests against the warmed DB.
            for label, binary in (('warmup', baseline), ('baseline', baseline), ('shim', shim)):
                stage = label
                worker = run_home(binary, payload, env, label, native, report)
                database.verify_context_state('home', worker['php_result'])
                workers[label] = worker
            stage = 'compare'
            compare_requests(workers['baseline'], workers['shim'])
            require(len({row['owned_pid'] for row in report['executions'].values()}) == 3,
                'cache_bootstrap_distinct_workers')
        stage = 'postflight'
        require(all(owned[key] is True for key in ('container_stopped', 'container_removed',
            'container_normal_exit_verified')), 'cache_bootstrap_database_cleanup')
        report['database'] = {key: owned[key] for key in ('sql_server_version', 'container_stopped',
            'container_removed', 'container_normal_exit_verified')}
        exact(sources(), report['sources_before'], 'cache_bootstrap_sources_changed')
        exact(generated(), report['generated'], 'cache_bootstrap_generated_changed')
        for name, binary in ((private.build.NAME, baseline), (NAME, shim)):
            exact(identity(binary.read_bytes()), report['binaries'][name], 'cache_bootstrap_binary_changed')
        exact(context.managed.verify_build(), report['module'], 'cache_bootstrap_module_changed')
        exact(context.oldbuild.verify_build(), bootstrap, 'cache_bootstrap_baseline_build_changed')
        report.update(passed=True, fresh_private_workers=3, stock_warmup_before_comparison=True,
            native_comparison_controller_only=True,
            response_context_and_db_rpc_exact_except_hello_reply=True, source_reverified_after=True)
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        context.owned_probe.reap_owned_probes()
    wire = (json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True, allow_nan=False) + '\n').encode()
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire,
                'cache_bootstrap_secret_guard')
    if args.record:
        private.build.preserve(HERE / f'evidence/linux-{args.backend}.json', wire)
    print('WordPress Wasm cache bootstrap: ' + ('passed' if report['passed'] else report.get('failure', 'failed')))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
