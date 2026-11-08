"""Run the unchanged PHP cache corpus twice with two narrow Rust-backed groups."""

import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import uuid


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/wp-object-cache-rust-probe'
BINARY = WORK / 'cargo-target/debug/recastpress-wp-object-cache-rust-probe.exe'
NATIVE = ROOT / 'experiments/wp-object-cache-contract-probe'
NATIVE_CONTROLLER_SHA = 'c4741739cef51d7f86efafb532a06b20fd7914902552472820cf1419865f1249'
NATIVE_EVIDENCE_SHA = 'a50e0737e4eb0e61ba1e0bd7416118a9a3970a96c53e4b174dee1fd0c8b4abbf'
GROUP_COUNTS = {'rp-cache-multiple': 14, 'rp-cache-integer': 16}
FIXTURES = ('bridge.php', 'adapter.php')


def require(condition, code):
    if not condition:
        raise ValueError(code)


def read(path, maximum):
    require(path.resolve(strict=True) == path and path.is_file(), 'fixed_cache_bridge_file')
    with path.open('rb') as stream:
        data = stream.read(maximum + 1)
    require(len(data) <= maximum, 'bounded_cache_bridge_file')
    return data


def digest(data):
    return sha256(data).hexdigest()


def native_controller():
    path = NATIVE / 'native_check.py'
    require(digest(read(path, 128 * 1024)) == NATIVE_CONTROLLER_SHA, 'accepted_cache_controller_changed')
    spec = importlib.util.spec_from_file_location('rp_rust_cache_stock_controller', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inputs(native, startup, expected_sha):
    binary = read(BINARY, 128 * 1024 * 1024)
    require(0 < len(binary) and digest(binary) == expected_sha, 'fixed_rust_cache_child_identity')
    fixture = {name: startup.identity(read(HERE / 'fixtures' / name, 128 * 1024)) for name in FIXTURES}
    return {'stock': native.inputs(startup), 'rust_binary': startup.identity(binary),
        'fixtures': fixture, 'native_baseline_sha256': NATIVE_EVIDENCE_SHA,
        'native_controller_sha256': NATIVE_CONTROLLER_SHA, 'runner_sha256': digest(read(Path(__file__).resolve(), 128 * 1024))}


def owned(path):
    require(WORK.resolve() == WORK and path != WORK and path.is_relative_to(WORK)
        and path.resolve() == path, 'owned_cache_bridge_directory')
    return path


def trial(startup, env, expected_sha, label, baseline, report, directories):
    directory = owned(WORK / (label + '-' + uuid.uuid4().hex))
    directory.mkdir(mode=0o700)
    directories.append(directory)
    originals = {name: read(HERE / 'fixtures' / name, 128 * 1024) for name in FIXTURES}
    for name, wire in originals.items():
        with owned(directory / name).open('xb') as stream:
            stream.write(wire)
    command = [str(startup.PHP / 'php.exe'), '-n', str(directory / 'bridge.php'), str(BINARY), expected_sha]
    process, stdout, stderr = startup.captured(command, env)
    row = {'case': label, 'php_process': process, 'php_stdout': startup.identity(stdout),
        'php_stderr': startup.identity(stderr)}
    report['fresh_processes'].append(row)
    require(stdout.endswith(b'\n') and stdout.count(b'\n') == 1, 'one_cache_bridge_observation')
    value = startup.parse(stdout)
    if type(value) is dict and value.get('passed') is False:
        code = value.get('failure')
        if type(code) is str and re.fullmatch('[a-z][a-z0-9_]{0,80}', code):
            row['fixture_failure'] = code
    require(process['exit_code'] == 0 and process['owned_reaped'] and not stderr, 'clean_cache_bridge_php_exit')
    require(type(value) is dict and set(value) == {'schema', 'passed', 'php_version', 'php_integer_bytes',
        'wordpress_version', 'adapter_class', 'stock_parent_class', 'profile', 'observations', 'rust_transport'}
        and value['schema'] == 'recastpress.wp-object-cache-rust-native-observation.v0.5' and value['passed'] is True
        and value['adapter_class'] == 'RpRustBackedCache' and value['stock_parent_class'] == 'WP_Object_Cache',
        'cache_bridge_closed_profile')
    for field in ('php_version', 'php_integer_bytes', 'wordpress_version', 'profile', 'observations'):
        require(startup.canonical(value[field]) == startup.canonical(baseline[field]), 'cache_bridge_baseline_' + field)
    rust = value['rust_transport']
    require(type(rust) is dict and set(rust) == {'owned_pid', 'exit_code', 'owned_reaped', 'stdout_bytes',
        'stderr_bytes', 'groups', 'flushes', 'finish'} and type(rust['owned_pid']) is int and rust['owned_pid'] > 0
        and type(rust['exit_code']) is int and rust['exit_code'] == 0 and rust['owned_reaped'] is True,
        'cache_bridge_owned_rust_exit')
    require(startup.canonical(rust['groups']) == startup.canonical(GROUP_COUNTS) and type(rust['flushes']) is int
        and rust['flushes'] == 2 and startup.canonical(rust['finish']) ==
        startup.canonical({'ok': True, 'operations': 32, 'entries': 0, 'charged_bytes': 0}), 'cache_bridge_rust_scope')
    rust_stdout = read(directory / 'rust.stdout', 65536)
    rust_stderr = read(directory / 'rust.stderr', 65536)
    require(rust['stdout_bytes'] == len(rust_stdout) and rust_stdout.endswith(b'\n')
        and rust_stdout.count(b'\n') == 33 and rust['stderr_bytes'] == 0 and not rust_stderr, 'cache_bridge_rust_capture')
    require(all(read(directory / name, 128 * 1024) == wire for name, wire in originals.items()), 'cache_bridge_fixture_changed')
    row.update(rust_process=rust, rust_stdout=startup.identity(rust_stdout), rust_stderr=startup.identity(rust_stderr))
    row['observations_sha256'] = digest(startup.canonical(value['observations']))
    return value['observations']


def run(expected_sha):
    report = {'schema': 'recastpress.wp-object-cache-rust-native-check.v0.5', 'passed': False,
        'fresh_processes': [], 'owned_trial_directories_removed': False,
        'no_database_or_external_cache': True, 'full_wordpress_bootstrap': False, 'wasm_executed': False}
    startup = None
    directories = []
    stage = 'source_preflight'
    try:
        require(os.name == 'nt' and re.fullmatch('[a-f0-9]{64}', expected_sha), 'cache_bridge_windows_sha')
        native = native_controller()
        startup = native.load_startup()
        wire = read(NATIVE / 'evidence/windows-native.json', 128 * 1024)
        require(digest(wire) == NATIVE_EVIDENCE_SHA, 'accepted_native_cache_baseline_changed')
        evidence = startup.parse(wire)
        require(evidence['passed'] is True and evidence['observation_count'] == 56, 'accepted_native_cache_baseline')
        before = inputs(native, startup, expected_sha)
        require(before['stock'] == evidence['sources_before'] and evidence['sources_reverified_after'] is True,
            'original_cache_sources_changed')
        report['sources_before'] = before
        baseline = evidence['observation']
        system = os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT')
        require(system and Path(system).is_absolute(), 'cache_bridge_system_root')
        env = {'SystemRoot': system, 'WINDIR': system, 'PHP_INI_SCAN_DIR': ''}
        for label in ('first', 'fresh_repeat'):
            stage = label
            report['observations'] = trial(startup, env, expected_sha, label, baseline, report, directories)
        stage = 'source_postflight'
        after = inputs(native, startup, expected_sha)
        require(before == after, 'cache_bridge_sources_changed')
        report['sources_after'] = after
        pids = [row[name]['owned_pid'] for row in report['fresh_processes'] for name in ('php_process', 'rust_process')]
        require(len(set(pids)) == 4 and len(startup.REAPED_CHILDREN) == 2 and not startup.OWNED_CHILDREN,
            'cache_bridge_distinct_owned_children')
        report.update(passed=True, observations_match_native=56, fresh_repeat_exact=True,
            scopes={'rust_blog': 1, 'rust_groups': list(GROUP_COUNTS), 'other_groups_stock': True},
            all_owned_children_reaped=True, sources_reverified_after=True)
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        if startup is not None and not startup.OWNED_CHILDREN:
            try:
                for directory in directories:
                    for name in (*FIXTURES, 'rust.stdout', 'rust.stderr'):
                        path = owned(directory / name)
                        if path.exists():
                            path.unlink()
                    owned(directory).rmdir()
                report['owned_trial_directories_removed'] = True
            except (Exception, KeyboardInterrupt):
                report['passed'] = False
                report['owned_trial_cleanup_failed'] = True
        elif startup is not None:
            report['passed'] = False
            report['owned_trial_cleanup_failed'] = True
    wire = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    require(not any(path.encode() in wire or path.encode().hex().encode() in wire for path in (str(ROOT), ROOT.as_posix())),
        'cache_bridge_report_private_path')
    return report, wire


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report, wire = run(args.sha256)
    if args.record and report['passed']:
        path = HERE / 'evidence/windows-native.json'
        path.parent.mkdir(exist_ok=True)
        with path.open('xb') as stream:
            stream.write(wire)
    sys.stdout.buffer.write(wire)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
