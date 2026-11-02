"""Build the isolated PHP-to-Rust bridge probe; verify its fixed PHP corpus.

Native PHP is an experimental comparison host. No Wasm or product ABI is
accepted here, and this helper starts neither a PHP request nor a database.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGET = ROOT / 'target/wpdb-bridge-probe/cargo-target'
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


previous = ROOT / 'experiments/wpdb-rpc-probe'
prep = load('rp_bridge_fixed_driver_sources', previous / 'prepare_driver.py')
native = load('rp_bridge_fixed_native_sources', previous / 'native_check.py')
require, sha, read = prep.require, prep.sha, prep.read_fixed
FIXTURES = ['helpers.php', 'adapter.php', 'corpus.php', 'native.php', 'bridge.php']
TESTS = ['tests::input_requires_every_key_and_closed_destination',
    'tests::request_hex_ascii_and_frame_quotas_fail_closed',
    'tests::request_nonce_sequence_and_phase_are_checked_before_dispatch',
    'tests::script_argument_is_normal_and_binds_the_canonical_fixture',
    'tests::unknown_and_duplicate_fields_are_rejected']


def fixture_gate():
    original = read(previous / 'fixtures/native_wpdb.php', 64 * 1024)
    require(sha(original) == '1773508c4764cbceea3e827141890dd87a9df550341be169bd14e079a1befd8e', 'original native corpus changed')
    start, end = b'    $old_prefix =', b"    $report['outcome'] ="
    require(original.count(start) == 1, 'corpus start marker')
    offset = original.index(start)
    body = original[offset:original.index(end, offset)]
    old = (b"    $report['syntax_error_code'] = mysqli_errno($db->dbh);\n"
        b"    $report['syntax_error_state'] = mysqli_sqlstate($db->dbh);\n")
    require(body.count(old) == 1, 'original two error accessors')
    expected = (b'<?php\n/** Fixed upstream API corpus; only the two driver-error accessors are injected. */\n'
        b'function rp_corpus(wpdb $db, array &$report): void {\n'
        + body.replace(old, b'    rp_error_identity($db, $report);\n') + b'}\n')
    observed = read(HERE / 'fixtures/corpus.php', 64 * 1024)
    require(observed == expected, 'bridge corpus changed outside two driver-error accessors')
    actual = {p.name for p in (HERE / 'fixtures').iterdir() if p.is_file()}
    require(actual == set(FIXTURES), 'exact bridge fixture file set')
    return {'original_fixture_sha256': sha(original), 'original_corpus_bytes': len(body),
        'original_corpus_sha256': sha(body), 'shared_corpus_bytes': len(observed),
        'shared_corpus_sha256': sha(observed), 'fixed_error_accessor_replacements': 1,
        'other_corpus_bytes_changed': False}


def inputs():
    paths = [HERE / p for p in ['Cargo.toml', 'Cargo.lock', 'src/main.rs', 'build_check.py',
        'owned_database.py', 'database_check.py']]
    paths += [HERE / 'fixtures' / name for name in FIXTURES]
    paths += [previous / p for p in ['prepare_driver.py', 'wire_check.py', 'driver-plan.json', 'native_check.py',
        'database_check.py', 'patches/0001-mysql-async-wpdb-handshake.patch', 'fixtures/native_wpdb.php']]
    paths += [ROOT / p for p in ['Cargo.lock', 'experiments/binlog-probe/Cargo.lock',
        'experiments/binlog-probe/Cargo.toml', 'scripts/owned_probe.py', 'scripts/wordpress_reference.py']]
    return {p.relative_to(ROOT).as_posix(): sha(read(p, 2 * 1024 * 1024)) for p in paths}


def artifact(wire, *, tests=False):
    require(0 < len(wire) <= 16 * 1024 * 1024, 'bridge Cargo capture quota')
    selected, finished = [], False
    for line in wire.splitlines():
        v = json.loads(line)
        require(not finished, 'Cargo output after finish')
        if v.get('reason') == 'build-finished':
            require(v.get('success') is True, 'Cargo build failed')
            finished = True
        if v.get('reason') != 'compiler-artifact' or v.get('target', {}).get('name') != 'recastpress-wpdb-bridge-probe': continue
        require(v['target']['kind'] == ['bin'] and v['profile']['test'] is tests, 'bridge executable artifact required')
        require(Path(v['manifest_path']).resolve() == HERE / 'Cargo.toml'
            and Path(v['target']['src_path']).resolve() == HERE / 'src/main.rs', 'bridge artifact source')
        path = Path(v['executable'])
        require(path.resolve(strict=True) == path and path.is_relative_to(TARGET), 'bridge executable escaped target')
        selected.append(path)
    require(finished and len(selected) == 1, 'one bridge executable required')
    return selected[0]


def run_units(env):
    command = ['cargo', 'test', '--no-run', '--manifest-path', str(HERE / 'Cargo.toml'),
        '--target-dir', str(TARGET), '--locked', '--offline', '--message-format=json']
    pid, result = owned_probe.run_captured_probe(command, env, timeout=1200)
    require(result.returncode == 0 and len(result.stderr) <= 65536, 'bridge units compilation failed')
    exe = artifact(result.stdout, tests=True)
    identity = prep.identity(read(exe, 256 * 1024 * 1024))
    _, listed = owned_probe.run_captured_probe([str(exe), '--list', '--format', 'terse'], env, timeout=10)
    require(listed.returncode == 0 and not listed.stderr and len(listed.stdout) <= 65536, 'bridge units listing failed')
    names = [line[:-6] for line in listed.stdout.decode('ascii').splitlines() if line.endswith(': test')]
    require(sorted(names) == TESTS, 'exact five bridge units required')
    tested_pid, tested = owned_probe.run_captured_probe([str(exe), '--test-threads=1', '--show-output'], env, timeout=30)
    require(tested.returncode == 0 and not tested.stderr and len(tested.stdout) <= 65536, 'bridge unit execution failed')
    output = tested.stdout.decode('ascii').replace('\r\n', '\n')
    require('test result: ok. 5 passed; 0 failed; 0 ignored;' in output, 'bridge ignored/zero/failed units')
    for name in TESTS: require('test ' + name + ' ... ok\n' in output, 'bridge unit missing success')
    require(prep.identity(read(exe, 256 * 1024 * 1024)) == identity, 'bridge unit executable changed')
    return {'passed': True, 'tests': names, 'build_owned_pid': pid, 'owned_build_reaped': True,
        'test_owned_pid': tested_pid, 'owned_tests_reaped': True,
        'executable': exe.relative_to(ROOT).as_posix(), 'executable_identity': identity,
        'stdout_sha256': sha(tested.stdout), 'stderr_sha256': sha(tested.stderr),
        'build_timeout_seconds': 1200, 'list_timeout_seconds': 10, 'test_timeout_seconds': 30}


def verify_build(binary):
    receipt = HERE / 'evidence/windows-build.json'
    record = json.loads(read(receipt, 256 * 1024))
    require(record['schema'] == 'recastpress.wpdb-bridge-build.v0.5' and record['passed'] is True
        and record['owned_build_reaped'] is True and record['owned_cleanup_completed'] is True
        and record['units']['passed'] is True and sorted(record['units']['tests']) == TESTS,
        'bridge build receipt failed')
    require(inputs() == record['input_files'] and fixture_gate() == record['corpus_gate'], 'bridge source/corpus changed')
    require(prep.prepare() == record['driver_after'], 'bridge staged driver changed')
    require(native.references() == record['native_reference'], 'bridge native reference changed')
    binary = Path(binary).absolute()
    require(binary.resolve(strict=True) == binary and binary.is_relative_to(TARGET)
        and binary.relative_to(ROOT).as_posix() == record['executable'], 'bridge binary path changed')
    require(prep.identity(read(binary, 256 * 1024 * 1024)) == record['executable_identity'], 'bridge binary changed')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    passed = False
    r = {'schema': 'recastpress.wpdb-bridge-build.v0.5', 'outcome': 'not_run', 'passed': False,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.system(),
        'locked_offline': True, 'owned_build_reaped': False, 'owned_cleanup_completed': False,
        'database_started': False, 'php_executed': False, 'wasm_executed': False, 'product_abi_selected': False}
    try:
        require(os.name == 'nt', 'fixed Windows bridge experiment')
        before = r['input_files'] = inputs()
        r['corpus_gate'] = fixture_gate()
        r['native_reference'] = native.references()
        r['driver_before'] = prep.prepare()
        require(TARGET.resolve() == TARGET, 'bridge target linked')
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('MYSQL_', 'MARIADB_', 'RP_'))
            and k.upper() not in {'GH_TOKEN', 'GITHUB_TOKEN', 'ACTIONS_RUNTIME_TOKEN', 'RUST_LOG', 'RUST_BACKTRACE'}}
        env.update(CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='2', CARGO_INCREMENTAL='0')
        r['units'] = run_units(env)
        command = ['cargo', 'build', '--manifest-path', str(HERE / 'Cargo.toml'), '--target-dir', str(TARGET),
            '--locked', '--offline', '--message-format=json']
        pid, result = owned_probe.run_captured_probe(command, env, timeout=1200)
        r.update(owned_build_pid=pid, owned_build_reaped=True, exit_code=result.returncode,
            stdout_sha256=sha(result.stdout), stderr_sha256=sha(result.stderr))
        require(result.returncode == 0 and len(result.stderr) <= 65536, 'bridge compilation failed')
        binary = artifact(result.stdout)
        r.update(executable=binary.relative_to(ROOT).as_posix(), executable_identity=prep.identity(read(binary, 256 * 1024 * 1024)))
        r['driver_after'] = prep.prepare()
        require(r['driver_after'] == r['driver_before'] and inputs() == before, 'bridge source changed during build')
        require(fixture_gate() == r['corpus_gate'] and native.references() == r['native_reference'], 'bridge fixtures changed during build')
        passed = True
        r['outcome'] = 'locked_offline_bridge_build_passed'
    except (Exception, KeyboardInterrupt) as error:
        r.update(outcome='bridge_build_failed', failure_class=type(error).__name__)
        # Fixed helper error only; child/environment diagnostics are private.
        if isinstance(error, ValueError) and len(str(error)) < 128: r['failure'] = str(error)
    finally:
        try:
            owned_probe.reap_owned_probes()
            r['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt):
            passed = False
            r['outcome'] = 'bridge_build_cleanup_failed'
    r['passed'] = passed
    if args.record:
        path = HERE / 'evidence/windows-build.json'
        path.parent.mkdir(exist_ok=True)
        require(path.resolve() == path, 'bridge receipt linked')
        wire = (json.dumps(r, indent=2, ensure_ascii=True) + '\n').encode()
        require(len(wire) <= 256 * 1024, 'bridge receipt quota')
        path.write_bytes(wire)
    print('WPDB bridge build: ' + r['outcome'])
    return 0 if passed else 1


if __name__ == '__main__': sys.exit(main())
