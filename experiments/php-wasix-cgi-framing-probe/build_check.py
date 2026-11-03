"""Locked offline host build and pure/WAT units; never runs actual PHP Wasm."""
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
WORK = ROOT / 'target/php-wasix-cgi-framing-probe'
TARGET = ROOT / 'target/php-wasix-wp-bootstrap-probe/cargo-target'
NAME = 'recastpress-php-wasix-cgi-framing-probe'
RECEIPT = HERE / ('evidence/' + ('windows' if os.name == 'nt' else 'linux') + '-build.json')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


prepared = load('rp_wasix_framing_prepared_build', HERE / 'prepare.py')
home = prepared.home
require, read, sha = prepared.require, prepared.read, prepared.sha
identity = prepared.native.identity
TESTS = sorted([name for name in home.TESTS if name.startswith(('access_fs::tests::', 'capture::tests::',
    'cgi_response::tests::', 'fiber_adapter::tests::', 'warning_diagnostic::tests::'))] + ['tests::' + name for name in [
    'closed_cases_keep_native_query_and_no_private_environment',
    'unknown_bytes_are_only_counts_digests_and_fixed_labels', 'expected_wire_hex_is_bounded_and_lowercase_only',
    'exact_native_json_delimits_warning_prefix_and_duplicates_do_not']])
STAGES = [('unit-build', 1200), ('unit-list', 10), ('unit-run', 60), ('normal-build', 1200)]
inputs = prepared.inputs
environment = home.environment


def child(stage, command, timeout, report):
    pid, result = home.owned_probe.run_captured_probe(command, environment(), timeout=timeout)
    require(WORK.resolve() == WORK, 'owned_build_path'); WORK.mkdir(parents=True, exist_ok=True)
    for label, wire in [('stdout', result.stdout), ('stderr', result.stderr)]: (WORK / (stage + '.' + label)).write_bytes(wire)
    report['commands'].append({'stage': stage, 'owned_pid': pid, 'owned_reaped': True,
        'timeout_seconds': timeout, 'exit_code': result.returncode,
        'stdout_sha256': sha(result.stdout), 'stderr_sha256': sha(result.stderr)})
    require(result.returncode == 0 and len(result.stdout) <= 16*1024*1024 and len(result.stderr) <= 65536, 'owned_build_command_failed')
    return result


def artifact(wire, tests):
    matches = []; finished = False
    for line in wire.splitlines():
        value = json.loads(line); require(not finished, 'cargo_after_finish')
        if value['reason'] == 'build-finished':
            require(value['success'] is True, 'cargo_build_failed'); finished = True
        if value['reason'] != 'compiler-artifact' or value.get('target', {}).get('name') != NAME: continue
        require(value['target']['kind'] == ['bin'] and value['profile']['test'] is tests
            and Path(value['manifest_path']).resolve() == HERE / 'Cargo.toml', 'cargo_artifact_kind')
        selected = Path(value['executable'])
        require(selected.resolve(strict=True) == selected and selected.is_relative_to(TARGET), 'cargo_artifact_path')
        matches.append(selected)
    require(finished and len(matches) == 1, 'one_cargo_artifact'); return matches[0]


def preserve(path, wire):
    require(path.resolve() == path and path.is_relative_to(HERE / 'evidence'), 'owned_receipt_path')
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        old = read(path)
        backup = path.with_name(path.stem + '-before-' + sha(old)[:16] + path.suffix)
        if backup.exists(): require(read(backup) == old, 'previous_receipt_changed')
        else:
            with backup.open('xb') as stream: stream.write(old)
    path.write_bytes(wire)


def gate():
    return {'old_bootstrap': home.old_build_gate(), 'driver': home.driver_gate(),
        'source_fs': home.sourcefs.registry_gate(), 'derivation': prepared.derivation(), 'native_proof': prepared.native_gate()}


def run_build(record=True):
    report = {'schema': 'recastpress.php-wasix-cgi-framing-build.v0.5', 'passed': False,
        'commands': [], 'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'locked_offline': True,
        'actual_php_wasm_executed': False, 'native_php_executed': False, 'database_connected': False,
        'plain_wat_unit_executed': False, 'owned_cleanup_completed': False,
        'source_reverified_after': False, 'product_runtime_selected': False}
    passed = False
    try:
        report['before'] = gate(); report['input_files'] = inputs(); prepared.prepare()
        profile = ['--release'] if sys.platform == 'linux' else []
        report['build_profile'] = 'release' if profile else 'dev'
        base = ['--manifest-path', str(HERE / 'Cargo.toml'), '--target-dir', str(TARGET), '--locked', '--offline']
        unit = artifact(child('unit-build', ['cargo', 'test', *profile, '--no-run', *base, '--message-format=json'], 1200, report).stdout, True)
        listed = child('unit-list', [str(unit), '--list', '--format', 'terse'], 10, report)
        names = sorted(line[:-6] for line in listed.stdout.decode().splitlines() if line.endswith(': test'))
        require(not listed.stderr and names == TESTS, 'exact_unit_inventory')
        tested = child('unit-run', [str(unit), '--test-threads=1', '--show-output'], 60, report)
        text = tested.stdout.decode().replace('\r\n', '\n')
        require(not tested.stderr and f'test result: ok. {len(TESTS)} passed; 0 failed; 0 ignored;' in text
            and sorted(re.findall(r'^test (\S+) \.\.\. ok$', text, re.M)) == TESTS, 'all_pure_units_passed')
        report['units'] = {'passed': True, 'tests': TESTS, 'executable': unit.relative_to(ROOT).as_posix(),
            'executable_identity': identity(read(unit, 256*1024*1024)), 'stdout_sha256': sha(tested.stdout)}
        report['plain_wat_unit_executed'] = True
        exe = artifact(child('normal-build', ['cargo', 'build', *profile, *base, '--message-format=json'], 1200, report).stdout, False)
        report.update(executable=exe.relative_to(ROOT).as_posix(), executable_identity=identity(read(exe, 256*1024*1024)))
        passed = True
    except (Exception, KeyboardInterrupt) as error:
        report['failure_class'] = type(error).__name__
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)): report['failure'] = str(error)
    finally:
        try:
            home.owned_probe.reap_owned_probes(); report['owned_cleanup_completed'] = True
            if 'input_files' in report:
                require(inputs() == report['input_files'], 'build_sources_changed')
                report['after'] = gate(); require(report['before'] == report['after'], 'reused_sources_or_old_build_changed')
                report['source_reverified_after'] = True
        except (Exception, KeyboardInterrupt) as error:
            passed = False; report['after_failure_class'] = type(error).__name__
    report['passed'] = passed
    report['outcome'] = 'locked_offline_framing_host_build_passed' if passed else 'framing_host_build_failed'
    if record: preserve(RECEIPT, (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode())
    return report


def verify_build(binary=None):
    value = json.loads(read(RECEIPT))
    require(value['schema'] == 'recastpress.php-wasix-cgi-framing-build.v0.5' and value['passed'] is True
        and value['outcome'] == 'locked_offline_framing_host_build_passed'
        and value['input_files'] == inputs() and value['before'] == value['after'] == gate()
        and value['source_reverified_after'] is True and value['owned_cleanup_completed'] is True
        and value['units']['tests'] == TESTS and value['units']['passed'] is True, 'framing_build_receipt')
    for row, (stage, timeout) in zip(value['commands'], STAGES):
        require(row['stage'] == stage and row['timeout_seconds'] == timeout and row['exit_code'] == 0
            and row['owned_reaped'] is True, 'framing_build_command')
    require(len(value['commands']) == len(STAGES), 'framing_build_stages')
    selected = Path(binary).absolute() if binary else ROOT / value['executable']
    require(selected.resolve(strict=True) == selected and selected.is_relative_to(TARGET)
        and selected.relative_to(ROOT).as_posix() == value['executable']
        and identity(read(selected, 256*1024*1024)) == value['executable_identity'], 'framing_host_binary')
    unit = ROOT / value['units']['executable']
    require(unit.resolve(strict=True) == unit and unit.is_relative_to(TARGET)
        and identity(read(unit, 256*1024*1024)) == value['units']['executable_identity'], 'framing_unit_binary')
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--verify', action='store_true'); args = parser.parse_args()
    result = verify_build() if args.verify else run_build()
    print(result['outcome']); sys.exit(0 if result['passed'] else 1)
