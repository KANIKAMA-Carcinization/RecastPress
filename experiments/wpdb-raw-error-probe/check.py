"""Run only five pure driver units in an owned, offline, isolated workspace.

Three raw-ERR units and the original two policy regressions run without a DB.
Every Cargo/test child has a finite outer deadline and confirmed owned reap.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe
spec = importlib.util.spec_from_file_location('rp_raw_error_preparer', HERE / 'prepare_driver.py')
preparer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparer)
require, sha = preparer.require, preparer.sha
FEATURES = ['minimal-rust', 'rustls-tls', 'ring', 'tls12', 'binlog']
OWN = ['Cargo.toml', 'Cargo.lock', 'driver-workspace.toml', 'driver.Cargo.lock',
    'src/lib.rs', 'patch-plan.json', 'patches/0001-expose-retained-raw-error.patch',
    'fixtures/raw_error_tests.rs', 'prepare_driver.py', 'check.py', 'README.ja.md']


def inputs():
    names = [p.relative_to(ROOT).as_posix() for p in [HERE / name for name in OWN]]
    plan = json.loads(preparer.base.read_fixed(HERE / 'patch-plan.json', 128 * 1024))
    names += list(plan['base_inputs']) + ['scripts/owned_probe.py',
        'experiments/wpdb-rpc-probe/Cargo.toml', 'experiments/wpdb-rpc-probe/Cargo.lock']
    return {name: sha(preparer.base.read_fixed(ROOT / name, 2 * 1024 * 1024)) for name in sorted(set(names))}


def old_stage():
    """Read only: never call the old prepare(), which could write its stage."""
    base = preparer.base
    plan = base.load_plan()
    files = base.registry_inputs(plan)
    files['mysql_async'] = base.patched_sources(plan, files['mysql_async'])
    result = {'source_trees': {}, 'workspace_files': {}}
    for pin in plan['crates']:
        directory = base.WORKSPACE / 'source' / (pin['name'] + '-' + pin['version'])
        if directory.exists():
            result['source_trees'][pin['name']] = base.verify_tree(directory, files[pin['name']])
    for name in ['Cargo.toml', 'Cargo.lock', 'seed-binlog.Cargo.lock']:
        path = base.WORKSPACE / name
        if path.exists():
            result['workspace_files'][name] = preparer.identity(base.read_fixed(path, 2 * 1024 * 1024))
    return result


def registry_rows(wire):
    return {(p['name'], p['version']): p.get('checksum') for p in tomllib.loads(wire.decode())['package']
        if p.get('source', '').startswith('registry+')}


def diagnostic(wire):
    value = wire.decode(errors='replace')
    for path, label in [(ROOT, '<workspace>'), (preparer.base.CARGO_HOME, '<cargo-cache>'), (Path.home(), '<user-home>')]:
        value = value.replace(str(path), label).replace(path.as_posix(), label)
    return value[:8192]


def selected_artifact(wire):
    require(len(wire) <= 16 * 1024 * 1024, 'cargo_stdout_quota')
    selected, finished = [], False
    crate = preparer.WORKSPACE / 'source/mysql_async-0.37.1'
    for line in wire.splitlines():
        item = json.loads(line)
        require(not finished, 'cargo_after_finished')
        if item.get('reason') == 'build-finished':
            require(item.get('success') is True, 'cargo_build_finished_failed')
            finished = True
        if item.get('reason') != 'compiler-artifact' or item.get('target', {}).get('name') != 'mysql_async':
            continue
        if item.get('profile', {}).get('test') is not True:
            continue
        require(item['target']['kind'] == ['lib'], 'driver_test_kind')
        require(Path(item['manifest_path']).resolve() == crate / 'Cargo.toml'
            and Path(item['target']['src_path']).resolve() == crate / 'src/lib.rs', 'driver_test_source')
        path = Path(item['executable']).absolute()
        require(path.resolve(strict=True) == path and path.is_relative_to(preparer.TARGET), 'driver_test_path')
        selected.append(path)
    require(finished and len(selected) == 1, 'one_driver_test_artifact')
    return selected[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report = {'schema': 'recastpress.wpdb-raw-error-units.v0.5', 'passed': False, 'outcome': 'not_run',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.system(),
        'steps': [], 'locked_offline': True, 'dependency_network_access_enabled': False,
        'database_started': False, 'database_connected': False, 'php_executed': False,
        'real_server_err_observed': False, 'transport_packet_frame_preserved': False,
        'native_wpdb_comparison_passed': False, 'standard_wpdb_compatibility_verified': False,
        'product_driver_selected': False, 'product_abi_selected': False,
        'existing_server_error_string_api_changed': False, 'owned_cleanup_completed': False,
        'original_inputs_unchanged_after': False, 'old_stage_unchanged_after': False,
        'source_trees_reverified_after': False, 'raw_error_tests_passed': 0,
        'original_policy_regressions_passed': 0}
    passed = False
    stage = 'preparation'
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(
        ('MYSQL_', 'MARIADB_', 'RP_', 'GH_', 'GITHUB_')) and k.upper() not in
        {'ACTIONS_RUNTIME_TOKEN', 'RUST_LOG', 'RUST_BACKTRACE'}}
    environment.update(CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='2', CARGO_INCREMENTAL='0')

    def run(name, command, timeout):
        start = time.monotonic()
        pid, result = owned_probe.run_captured_probe(command, environment, timeout=timeout)
        preparer.owned(preparer.WORK).mkdir(exist_ok=True)
        for suffix, wire in [('stdout', result.stdout), ('stderr', result.stderr)]:
            path = preparer.owned(preparer.WORK / (name + '.' + suffix))
            path.write_bytes(wire)
        report['steps'].append({'name': name, 'owned_pid': pid, 'owned_reaped': True,
            'exit_code': result.returncode, 'timeout_seconds': timeout,
            'elapsed_seconds': round(time.monotonic() - start, 3),
            'stdout_sha256': sha(result.stdout), 'stderr_sha256': sha(result.stderr)})
        require(len(result.stdout) <= 16 * 1024 * 1024 and len(result.stderr) <= 65536, 'child_capture_quota')
        if result.returncode:
            report['child_diagnostics'] = diagnostic(result.stderr)
            messages = [v['message']['rendered'] for line in result.stdout.splitlines()
                if line.startswith(b'{') and (v := json.loads(line)).get('reason') == 'compiler-message'
                and v.get('message', {}).get('level') == 'error']
            if messages:
                report['compiler_errors'] = diagnostic('\n'.join(messages).encode())
        require(result.returncode == 0, name + '_failed')
        return result

    try:
        before = inputs()
        report['input_files'] = before
        report['old_stage_before'] = old_stage()
        report['preparation_before'] = preparer.prepare()
        raw_plan = json.loads(preparer.base.read_fixed(HERE / 'patch-plan.json', 128 * 1024))
        tests = raw_plan['tests'] + preparer.base.load_plan()['patch']['included_unit_tests']
        require(len(tests) == len(set(tests)) == 5, 'five_closed_pure_tests')
        report['toolchain'] = {name: run(name, command, 10).stdout.decode().strip()
            for name, command in [('rustc-version', ['rustc', '-Vv']), ('cargo-version', ['cargo', '-V'])]}
        stage = 'metadata'
        command = ['cargo', 'metadata', '--manifest-path', str(preparer.WORKSPACE / 'Cargo.toml'),
            '--locked', '--offline', '--format-version', '1', '--no-default-features',
            '--features', ','.join('mysql_async/' + name for name in FEATURES)]
        metadata = json.loads(run('metadata', command, 120).stdout)
        for name, version in [('mysql_async', '0.37.1'), ('mysql_common', '0.37.3')]:
            rows = [p for p in metadata['packages'] if p['name'] == name and p['version'] == version]
            require(len(rows) == 1 and rows[0]['source'] is None
                and Path(rows[0]['manifest_path']).resolve() == preparer.WORKSPACE / 'source' / (name + '-' + version) / 'Cargo.toml',
                'driver_metadata_owned_source')
        seed = preparer.base.read_fixed(ROOT / 'experiments/binlog-probe/Cargo.lock', 1024 * 1024)
        lock = preparer.base.read_fixed(HERE / 'driver.Cargo.lock', 1024 * 1024)
        old_rows, new_rows = registry_rows(seed), registry_rows(lock)
        require(all(old_rows[key] == new_rows[key] for key in old_rows.keys() & new_rows.keys()),
            'original_registry_checksum_changed')
        report['driver_registry_package_count'] = len(new_rows)
        stage = 'build'
        command = ['cargo', 'test', '--manifest-path', str(preparer.WORKSPACE / 'Cargo.toml'),
            '--target-dir', str(preparer.TARGET), '--locked', '--offline', '-p', 'mysql_async',
            '--lib', '--no-default-features', '--features', ','.join(FEATURES), '--no-run', '--message-format=json']
        artifact = selected_artifact(run('driver-test-build', command, 1200).stdout)
        artifact_identity = preparer.identity(preparer.base.read_fixed(artifact, 256 * 1024 * 1024))
        report['test_artifact'] = {'path': artifact.relative_to(ROOT).as_posix(), **artifact_identity}
        stage = 'tests'
        listed = run('driver-test-list', [str(artifact), 'rp_', '--list', '--format', 'terse'], 10)
        require(not listed.stderr and len(listed.stdout) <= 65536, 'test_list_capture')
        names = [line[:-6] for line in listed.stdout.decode().splitlines() if line.endswith(': test')]
        require(sorted(names) == sorted(tests) and len(names) == 5, 'exact_five_listed_tests')
        report['listed_tests'] = names
        result = run('driver-tests', [str(artifact), 'rp_', '--test-threads=1', '--show-output'], 30)
        output = result.stdout.decode().replace('\r\n', '\n')
        require(not result.stderr and len(result.stdout) <= 65536, 'test_run_capture')
        require(sorted(re.findall(r'^test (\S+) \.\.\. ok$', output, re.M)) == sorted(tests)
            and re.search(r'test result: ok\. 5 passed; 0 failed; 0 ignored;', output), 'five_tests_actually_passed')
        report['test_output'] = diagnostic(result.stdout)
        report['raw_error_tests_passed'] = 3
        report['original_policy_regressions_passed'] = 2
        report['parsed_err_fields_and_non_utf8_bytes_verified'] = True
        stage = 'public_api_witness'
        run('public-api-build', ['cargo', 'build', '--manifest-path', str(HERE / 'Cargo.toml'),
            '--target-dir', str(preparer.TARGET), '--lib', '--locked', '--offline', '--message-format=json'], 1200)
        require(preparer.identity(preparer.base.read_fixed(artifact, 256 * 1024 * 1024)) == artifact_identity,
            'driver_test_artifact_changed')
        passed = True
        report['outcome'] = 'fixed_raw_error_three_units_and_two_policy_regressions_passed'
    except (Exception, KeyboardInterrupt) as error:
        report['outcome'] = stage + '_failed'
        report['failure_class'] = type(error).__name__
        if isinstance(error, ValueError) and re.fullmatch(r'[a-z_]{1,100}', str(error)):
            report['failure_reason'] = str(error)
    finally:
        try:
            owned_probe.reap_owned_probes()
            report['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt):
            passed = False
            report['outcome'] = 'owned_cleanup_failed'
        if 'input_files' in report:
            try:
                require(inputs() == report['input_files'], 'original_inputs_changed_after')
                report['original_inputs_unchanged_after'] = True
                report['old_stage_after'] = old_stage()
                require(report['old_stage_after'] == report['old_stage_before'], 'old_stage_changed_after')
                report['old_stage_unchanged_after'] = True
                report['preparation_after'] = preparer.prepare()
                require(report['preparation_after'] == report['preparation_before'], 'new_stage_changed_after')
                report['source_trees_reverified_after'] = True
            except (Exception, KeyboardInterrupt) as error:
                passed = False
                report['outcome'] = 'after_verification_failed'
                report['after_failure_class'] = type(error).__name__
    report['passed'] = passed
    if args.record:
        directory = HERE / 'evidence'
        require(directory.resolve() == directory, 'receipt_directory_linked')
        directory.mkdir(exist_ok=True)
        path = directory / (platform.system().lower() + '-driver-units.json')
        require(path.resolve() == path, 'receipt_path_linked')
        with path.open('xb') as stream:
            stream.write((json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode())
    print('raw ERR units: ' + report['outcome'])
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
