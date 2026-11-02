"""Build the isolated standard-CRUD native comparison host, never a product ABI.

The original two-patch driver stage is verified read-only, never prepared here.
All build/test children are locked, offline, bounded and owned by this helper.
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGET = ROOT / 'target/wpdb-crud-probe/cargo-target'
RECEIPT = HERE / 'evidence/windows-build.json'
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


previous = ROOT / 'experiments/wpdb-rpc-probe'
bridge = ROOT / 'experiments/wpdb-bridge-probe'
prep = load('rp_crud_readonly_driver', previous / 'prepare_driver.py')
native = load('rp_crud_native_reference_gate', HERE / 'native_check.py')
derivation = load('rp_crud_host_derivation', HERE / 'derivation_gate.py')
require, sha, read = prep.require, prep.sha, prep.read_fixed
FIXTURES = ['bridge.php', 'native.php', 'entry.php', 'corpus.php']
TESTS = ['tests::input_requires_every_key_and_closed_destination',
    'tests::request_hex_ascii_and_frame_quotas_fail_closed',
    'tests::request_nonce_sequence_and_phase_are_checked_before_dispatch',
    'tests::script_argument_is_normal_and_binds_the_canonical_fixture',
    'tests::unknown_and_duplicate_fields_are_rejected']


def inputs():
    paths = [HERE / name for name in ['Cargo.toml', 'Cargo.lock', 'src/main.rs', 'src/completion.rs',
        'derivation_gate.py', 'build_check.py', 'database_check.py', 'native_check.py', 'owned_database.py']]
    paths += [HERE / 'fixtures' / name for name in FIXTURES]
    paths += [bridge / name for name in ['Cargo.toml', 'Cargo.lock', 'src/main.rs', 'build_check.py',
        'owned_database.py', 'database_check.py', 'fixtures/adapter.php', 'fixtures/helpers.php',
        'fixtures/corpus.php', 'fixtures/native.php', 'fixtures/bridge.php']]
    paths += [previous / name for name in ['Cargo.toml', 'Cargo.lock', 'prepare_driver.py', 'wire_check.py',
        'native_check.py', 'database_check.py', 'driver_check.py', 'driver-plan.json',
        'patches/0001-mysql-async-wpdb-handshake.patch', 'fixtures/native_wpdb.php']]
    paths += [ROOT / name for name in ['Cargo.lock', 'experiments/binlog-probe/Cargo.toml',
        'experiments/binlog-probe/Cargo.lock', 'scripts/owned_probe.py', 'scripts/wordpress_reference.py',
        'scripts/setup_mysql.py', 'scripts/setup_mariadb.py', 'scripts/pinned_zip_tool.py',
        '.reference/mysql-8.4.13/recastpress-tool.json', '.reference/mariadb-11.8.9/recastpress-tool.json',
        '.reference/php-8.5.12/recastpress-tool.json', '.reference/mysql-release-key.asc',
        '.reference/mysql-8.4.13-winx64.zip.asc']]
    return {path.relative_to(ROOT).as_posix(): sha(read(path, 2 * 1024 * 1024))
        for path in sorted(set(paths))}


def driver_gate():
    """Verify existing archives, original two-file patch and stage without writes."""
    plan = prep.load_plan()
    files = prep.registry_inputs(plan)
    files['mysql_async'] = prep.patched_sources(plan, files['mysql_async'])
    require(prep.WORKSPACE.is_dir(), 'crud_existing_verified_stage_required')
    trees = {pin['name']: prep.verify_tree(prep.WORKSPACE / 'source' / (pin['name'] + '-' + pin['version']),
        files[pin['name']]) for pin in plan['crates']}
    require(read(prep.WORKSPACE / 'Cargo.toml', 4096) == prep.MANIFEST, 'crud_original_stage_manifest')
    seed = read(ROOT / plan['existing_reference']['cargo_lock'], 1024 * 1024)
    require(read(prep.WORKSPACE / 'seed-binlog.Cargo.lock', 1024 * 1024) == seed, 'crud_original_stage_seed')
    state = json.loads(read(prep.WORK / 'resolved-lock.json', 65536))
    lock = read(prep.WORKSPACE / 'Cargo.lock', 1024 * 1024)
    require(state['lock'] == prep.identity(lock) and state['seed_sha256'] == sha(seed)
        and state['manifest_sha256'] == sha(prep.MANIFEST), 'crud_original_stage_lock')
    return {'schema': 'recastpress.wpdb-crud-readonly-driver.v0.5',
        'plan_sha256': sha(read(prep.PLAN, 128 * 1024)), 'patch_sha256': plan['patch']['sha256'],
        'source_trees': trees, 'stage_workspace_manifest_sha256': sha(prep.MANIFEST),
        'stage_lock': prep.identity(lock), 'seed_lock_sha256': sha(seed), 'stage_modified': False}


def fixture_gate():
    require({p.name for p in (HERE / 'fixtures').iterdir() if p.is_file()} == set(FIXTURES),
        'crud_exact_fixture_set')
    fixtures = {}
    for name in FIXTURES:
        wire = read(HERE / 'fixtures' / name, 128 * 1024)
        require(b'\r' not in wire, 'crud_fixture_requires_lf')
        fixtures[name] = prep.identity(wire)
    # The transport and adapter business are included from their original file.
    old = {'adapter.php': 'e153d831fe35a2e40d4ffc34f434b9c4915e4a233ad4ffbb44f2ee90e2442a6d',
        'helpers.php': '1220aa52f8a967f7d4981a1b668e07c7343cdff9c10caf42ea99c32a348fccb1'}
    for name, expected in old.items():
        require(sha(read(bridge / 'fixtures' / name, 128 * 1024)) == expected, 'crud_original_adapter_helpers_changed')
    return {'schema': 'recastpress.wpdb-crud-fixture-inputs.v0.5', 'fixtures': fixtures,
        'unchanged_original_php_sources': old, 'rust_derivation': derivation.verify(),
        'wasm_executed': False, 'product_abi_selected': False}


def environment():
    result = {k: v for k, v in os.environ.items() if not k.upper().startswith(('MYSQL_', 'MARIADB_', 'RP_'))
        and k.upper() not in {'GH_TOKEN', 'GITHUB_TOKEN', 'ACTIONS_RUNTIME_TOKEN', 'RUST_LOG', 'RUST_BACKTRACE',
            'PHPRC', 'PHP_INI_SCAN_DIR'}}
    result.update(CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='2', CARGO_INCREMENTAL='0')
    return result


def artifact(wire, *, tests=False):
    require(0 < len(wire) <= 16 * 1024 * 1024, 'crud_cargo_capture_quota')
    selected, finished = [], False
    for line in wire.splitlines():
        value = json.loads(line)
        require(not finished, 'crud_cargo_output_after_finished')
        if value.get('reason') == 'build-finished':
            require(value.get('success') is True, 'crud_cargo_build_finished_failed')
            finished = True
        if value.get('reason') != 'compiler-artifact' or value.get('target', {}).get('name') != 'recastpress-wpdb-crud-probe':
            continue
        require(value['target']['kind'] == ['bin'] and value['profile']['test'] is tests,
            'crud_normal_or_test_artifact_kind')
        require(Path(value['manifest_path']).resolve() == HERE / 'Cargo.toml'
            and Path(value['target']['src_path']).resolve() == HERE / 'src/main.rs', 'crud_artifact_source')
        path = Path(value['executable'])
        require(path.resolve(strict=True) == path and path.is_relative_to(TARGET), 'crud_artifact_path')
        selected.append(path)
    require(finished and len(selected) == 1, 'crud_one_executable_artifact')
    return selected[0]


def run_child(name, command, env, timeout, record):
    started = time.monotonic()
    pid, result = owned_probe.run_captured_probe(command, env, timeout=timeout)
    work = TARGET.parent
    require(work.resolve() == work, 'crud_build_work_linked')
    work.mkdir(parents=True, exist_ok=True)
    for suffix, wire in [('stdout', result.stdout), ('stderr', result.stderr)]:
        path = work / (name + '.' + suffix)
        require(path.resolve() == path, 'crud_build_log_linked')
        path.write_bytes(wire)
    record['commands'].append({'stage': name, 'owned_pid': pid, 'owned_reaped': True,
        'exit_code': result.returncode, 'timeout_seconds': timeout,
        'elapsed_seconds': round(time.monotonic() - started, 3),
        'stdout_sha256': sha(result.stdout), 'stderr_sha256': sha(result.stderr)})
    require(len(result.stdout) <= 16 * 1024 * 1024 and len(result.stderr) <= 65536, 'crud_child_capture_quota')
    require(result.returncode == 0, name + '_failed')
    return pid, result


def run_units(env, record):
    command = ['cargo', 'test', '--no-run', '--manifest-path', str(HERE / 'Cargo.toml'),
        '--target-dir', str(TARGET), '--locked', '--offline', '--message-format=json']
    build_pid, result = run_child('unit-build', command, env, 1200, record)
    exe = artifact(result.stdout, tests=True)
    identity = prep.identity(read(exe, 256 * 1024 * 1024))
    list_pid, listed = run_child('unit-list', [str(exe), '--list', '--format', 'terse'], env, 10, record)
    require(not listed.stderr and len(listed.stdout) <= 65536, 'crud_unit_list_capture')
    names = [line[:-6] for line in listed.stdout.decode('ascii').splitlines() if line.endswith(': test')]
    require(sorted(names) == TESTS and len(names) == 5, 'crud_exact_five_units')
    test_pid, tested = run_child('unit-run', [str(exe), '--test-threads=1', '--show-output'], env, 30, record)
    require(not tested.stderr and len(tested.stdout) <= 65536, 'crud_unit_run_capture')
    output = tested.stdout.decode('ascii').replace('\r\n', '\n')
    require('test result: ok. 5 passed; 0 failed; 0 ignored;' in output, 'crud_zero_ignored_failed_units')
    require(sorted(re.findall(r'^test (\S+) \.\.\. ok$', output, re.M)) == TESTS, 'crud_actual_five_unit_passes')
    require(prep.identity(read(exe, 256 * 1024 * 1024)) == identity, 'crud_unit_executable_changed')
    return {'passed': True, 'tests': names, 'build_owned_pid': build_pid, 'owned_build_reaped': True,
        'list_owned_pid': list_pid, 'owned_list_reaped': True, 'test_owned_pid': test_pid,
        'owned_tests_reaped': True, 'executable': exe.relative_to(ROOT).as_posix(),
        'executable_identity': identity, 'stdout_sha256': sha(tested.stdout),
        'stderr_sha256': sha(tested.stderr), 'build_timeout_seconds': 1200,
        'list_timeout_seconds': 10, 'test_timeout_seconds': 30}


def verify_build(binary):
    record = json.loads(read(RECEIPT, 256 * 1024))
    require(record['schema'] == 'recastpress.wpdb-crud-build.v0.5'
        and record['outcome'] == 'locked_offline_crud_build_passed' and record['passed'] is True
        and record['owned_build_reaped'] is True and record['owned_cleanup_completed'] is True
        and record['source_reverified_after'] is True and record['units']['passed'] is True
        and sorted(record['units']['tests']) == TESTS, 'crud_build_receipt_failed')
    require(record['locked_offline'] is True and record['exit_code'] == 0
        and record['driver_before'] == record['driver_after'], 'crud_complete_build_chain')
    commands = record['commands']
    stages = [('unit-build', 1200), ('unit-list', 10), ('unit-run', 30), ('normal-build', 1200)]
    require(type(commands) is list and len(commands) == len(stages), 'crud_four_owned_build_stages')
    for command, (stage, deadline) in zip(commands, stages):
        require(command['stage'] == stage and command['timeout_seconds'] == deadline
            and type(command['owned_pid']) is int and command['owned_pid'] > 0
            and command['owned_reaped'] is True and command['exit_code'] == 0,
            'crud_owned_build_stage_failed')
    units = record['units']
    for index, (key, flag) in enumerate([('build', 'owned_build_reaped'),
            ('list', 'owned_list_reaped'), ('test', 'owned_tests_reaped')]):
        require(units[key + '_owned_pid'] == commands[index]['owned_pid']
            and units[flag] is True, 'crud_unit_owned_reap_binding')
    require(record['owned_build_pid'] == commands[-1]['owned_pid'], 'crud_normal_build_owned_binding')
    unit_path = ROOT / units['executable']
    require(unit_path.resolve(strict=True) == unit_path and unit_path.is_relative_to(TARGET)
        and prep.identity(read(unit_path, 256 * 1024 * 1024)) == units['executable_identity'],
        'crud_test_executable_changed_after_build')
    require(inputs() == record['input_files'] and fixture_gate() == record['corpus_gate']
        and driver_gate() == record['driver_after'] and native.references() == record['native_reference'],
        'crud_build_inputs_or_references_changed')
    binary = Path(binary).absolute()
    require(binary.resolve(strict=True) == binary and binary.is_relative_to(TARGET)
        and binary.relative_to(ROOT).as_posix() == record['executable'], 'crud_binary_path_changed')
    require(prep.identity(read(binary, 256 * 1024 * 1024)) == record['executable_identity'], 'crud_binary_changed')
    require(record['database_started'] is False and record['php_executed'] is False
        and record['wasm_executed'] is False and record['product_abi_selected'] is False
        and record['product_driver_selected'] is False, 'crud_build_scope_claims')
    return record


def save_record(record):
    require(RECEIPT.resolve() == RECEIPT and RECEIPT.parent.resolve() == RECEIPT.parent, 'crud_receipt_linked')
    RECEIPT.parent.mkdir(exist_ok=True)
    if RECEIPT.exists():
        previous_bytes = read(RECEIPT, 256 * 1024)
        saved = RECEIPT.with_name('windows-build-before-' + sha(previous_bytes)[:16] + '.json')
        if saved.exists():
            require(read(saved, 256 * 1024) == previous_bytes, 'crud_prior_record_identity')
        else:
            with saved.open('xb') as output:
                output.write(previous_bytes)
    wire = (json.dumps(record, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    require(len(wire) <= 256 * 1024, 'crud_receipt_quota')
    RECEIPT.write_bytes(wire)


def run_build(record=False):
    value = {'schema': 'recastpress.wpdb-crud-build.v0.5', 'outcome': 'not_run', 'passed': False,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.system(),
        'locked_offline': True, 'commands': [], 'owned_build_reaped': False,
        'owned_cleanup_completed': False, 'source_reverified_after': False,
        'database_started': False, 'php_executed': False, 'wasm_executed': False,
        'native_wpdb_comparison_passed': False, 'product_driver_selected': False, 'product_abi_selected': False}
    passed = False
    try:
        require(os.name == 'nt', 'crud_fixed_windows_comparison')
        before = value['input_files'] = inputs()
        value['corpus_gate'] = fixture_gate()
        value['native_reference'] = native.references()
        value['driver_before'] = driver_gate()
        require(TARGET.resolve() == TARGET, 'crud_build_target_linked')
        env = environment()
        value['units'] = run_units(env, value)
        command = ['cargo', 'build', '--manifest-path', str(HERE / 'Cargo.toml'), '--target-dir', str(TARGET),
            '--locked', '--offline', '--message-format=json']
        pid, result = run_child('normal-build', command, env, 1200, value)
        value.update(owned_build_pid=pid, owned_build_reaped=True, exit_code=result.returncode,
            stdout_sha256=sha(result.stdout), stderr_sha256=sha(result.stderr))
        binary = artifact(result.stdout)
        value.update(executable=binary.relative_to(ROOT).as_posix(),
            executable_identity=prep.identity(read(binary, 256 * 1024 * 1024)))
        passed = True
        value['outcome'] = 'locked_offline_crud_build_passed'
    except (Exception, KeyboardInterrupt) as error:
        value.update(outcome='crud_build_failed', failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch(r'[a-z_]{1,100}', str(error)):
            value['failure'] = str(error)
    finally:
        try:
            owned_probe.reap_owned_probes()
            value['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt):
            passed = False
            value['outcome'] = 'crud_build_cleanup_failed'
        if 'input_files' in value:
            try:
                require(inputs() == value['input_files'] and fixture_gate() == value['corpus_gate']
                    and native.references() == value['native_reference'], 'crud_source_changed_during_build')
                value['driver_after'] = driver_gate()
                require(value['driver_after'] == value['driver_before'], 'crud_existing_driver_changed')
                value['source_reverified_after'] = True
            except (Exception, KeyboardInterrupt) as error:
                passed = False
                value.update(outcome='crud_build_after_verification_failed', after_failure_class=type(error).__name__)
    value['passed'] = passed
    if record:
        save_record(value)
    return value


build = run_build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    result = run_build(record=args.record)
    print('CRUD host build: ' + result['outcome'])
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
