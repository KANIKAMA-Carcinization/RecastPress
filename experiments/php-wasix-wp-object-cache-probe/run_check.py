"""Compare stock WordPress cache observations with PHP-Wasm and a Rust store."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXPERIMENTS = HERE.parent
TARGET = ROOT / 'target/php-wasix-wp-object-cache-probe/cargo-target'
NAME = 'recastpress-php-wasix-wp-object-cache-probe'
PREFIX = 'recastpress.php-wasix-wp-object-cache-'
STOCK = 'experiments/wp-object-cache-contract-probe/evidence/windows-native.json'
BRIDGE = 'experiments/wp-object-cache-rust-probe/evidence/windows-native.json'
STOCK_SHA = 'a50e0737e4eb0e61ba1e0bd7416118a9a3970a96c53e4b174dee1fd0c8b4abbf'
BRIDGE_SHA = 'bb00e9f6a17e31c9295b9a53de65e1e78714c111c99e53c30779b29541e07201'

sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe


def require(value, code):
    if not value:
        raise ValueError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def identity(wire):
    return {'bytes': len(wire), 'sha256': hashlib.sha256(wire).hexdigest()}


def exact(actual, expected, code):
    require(canonical(actual) == canonical(expected), code)


def strict(wire):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'cache_duplicate_json_key')
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError('cache_nonfinite_json')

    return json.loads(wire, object_pairs_hook=pairs, parse_constant=nonfinite)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pinned(name, expected_sha):
    wire = (ROOT / name).read_bytes()
    exact(identity(wire)['sha256'], expected_sha, 'cache_pinned_reference')
    return strict(wire)


def reference():
    stock = pinned(STOCK, STOCK_SHA)
    bridge = pinned(BRIDGE, BRIDGE_SHA)
    require(stock['passed'] is True and bridge['passed'] is True, 'cache_accepted_native_references')
    exact(bridge['observations_match_native'], 56, 'cache_bridge_observation_count')
    expected = stock['observation']['observations']
    exact(bridge['observations'], expected, 'cache_native_references_differ')
    require(len(expected) == 56, 'cache_native_observation_inventory')
    return stock, bridge, expected


def environment():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('GH_', 'GITHUB_', 'RP_', 'MYSQL_', 'MARIADB_'))}
    for key in ('RUST_BACKTRACE', 'RUST_LOG', 'ACTIONS_RUNTIME_TOKEN', 'PHPRC', 'PHP_INI_SCAN_DIR'):
        env.pop(key, None)
    env.update(CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='2', CARGO_INCREMENTAL='0')
    return env


def source_inputs():
    paths = [HERE / name for name in ('Cargo.toml', 'Cargo.lock', 'derive_cache.py', 'derive_guest.py',
        'run_check.py', 'src/main.rs', 'src/cache_fs.rs')]
    paths += [ROOT / name for name in (
        '.github/workflows/php-wasix-wp-object-cache.yml', 'scripts/owned_probe.py', 'Cargo.toml', STOCK, BRIDGE,
        'crates/recastpress-core/Cargo.toml', 'experiments/wp-source-fs-probe/Cargo.toml',
        'experiments/wp-object-cache-contract-probe/fixtures/corpus.php',
        'experiments/wp-object-cache-contract-probe/fixtures/native.php',
        'experiments/wp-object-cache-rust-probe/fixtures/adapter.php',
        'experiments/wp-object-cache-rust-probe/fixtures/bridge.php',
        'experiments/wp-object-cache-rust-probe/src/main.rs',
        'experiments/wp-bootstrap-probe/prepare.py',
        'experiments/php-wasix-managed-access-probe/managed_check.py',
    )]
    paths += sorted((ROOT / 'crates/recastpress-core/src').rglob('*.rs'))
    paths += sorted((EXPERIMENTS / 'wp-source-fs-probe/src').rglob('*.rs'))
    generated_paths = {HERE / 'src/cache_host.rs', *(HERE / 'fixtures' / name for name in ('entry.php', 'adapter.php', 'corpus.php'))}
    files = {}
    while paths:
        path = paths.pop().resolve()
        require(path.is_relative_to(ROOT), 'cache_source_path_scope')
        name = path.relative_to(ROOT).as_posix()
        if name in files or path in generated_paths:
            continue
        data = path.read_bytes()
        files[name] = identity(data)
        if path.suffix == '.rs':
            for reference in re.findall(rb'(?:#\[path\s*=\s*|include_(?:bytes|str)!\(\s*)"([^"]+)"', data):
                paths.append(path.parent / reference.decode('ascii'))
    return dict(sorted(files.items()))


def generated():
    paths = [HERE / 'src/cache_host.rs', *(HERE / 'fixtures' / name for name in ('entry.php', 'adapter.php', 'corpus.php'))]
    return {path.relative_to(HERE).as_posix(): identity(path.read_bytes()) for path in paths}


def execute(command, env, timeout, record, stage):
    pid, result = owned_probe.run_captured_probe(command, env, timeout=timeout)
    require(len(result.stdout) <= 16 * 1024 * 1024 and len(result.stderr) <= 65536, 'cache_owned_capture_quota')
    record[stage] = {'owned_pid': pid, 'owned_reaped': True, 'exit_code': result.returncode,
        'stdout': identity(result.stdout), 'stderr': identity(result.stderr)}
    return result


def build(report, env):
    commands = report['build_commands'] = {}
    args = ['--locked', '--offline', '--release', '--manifest-path', str(HERE / 'Cargo.toml'), '--target-dir', str(TARGET)]
    for script in ('derive_cache.py', 'derive_guest.py'):
        result = execute([sys.executable, '-B', str(HERE / script)], env, 60, commands, script)
        require(result.returncode == 0, 'cache_source_derivation')
    report['generated_inputs'] = generated()
    result = execute(['cargo', 'test', '--quiet', *args], env, 1200, commands, 'unit')
    units = re.findall(rb'test result: ok\. ([0-9]+) passed; 0 failed; 0 ignored;', result.stdout)
    require(result.returncode == 0 and len(units) == 1 and int(units[0]) > 0, 'cache_worker_units')
    report['units_passed'] = int(units[0])
    result = execute(['cargo', 'build', '--message-format=json-render-diagnostics', *args], env, 1200, commands, 'build')
    require(result.returncode == 0, 'cache_worker_build')
    events = [strict(line) for line in result.stdout.splitlines()]
    binary = TARGET / 'release' / NAME
    selected = [row for row in events if row['reason'] == 'compiler-artifact' and row['target']['name'] == NAME]
    require(len(selected) == 1 and events[-1] == {'reason': 'build-finished', 'success': True}, 'cache_worker_artifact')
    row = selected[0]
    require(row['target']['kind'] == ['bin'] and row['profile']['test'] is False
        and Path(row['manifest_path']) == HERE / 'Cargo.toml' and Path(row['target']['src_path']) == HERE / 'src/main.rs'
        and Path(row['executable']) == binary and binary.resolve(strict=True) == binary
        and 0 < binary.stat().st_size <= 256 * 1024 * 1024, 'cache_worker_artifact_path')
    report['worker_build'] = {'executable': binary.relative_to(ROOT).as_posix(),
        'executable_identity': identity(binary.read_bytes()), 'locked_offline': True}
    return binary


def compare(host, stock, expected, provenance, generated_inputs):
    module = provenance['module']
    exact(host['schema'], PREFIX + 'host.v0.5', 'cache_host_schema')
    exact(host['passed'], True, 'cache_host_outcome')
    exact(host['module_compilations'], 1, 'cache_host_compile_once')
    exact(host['source_reverified_after'], True, 'cache_host_source_reverified')
    exact(host['sources_before'], host['sources_after'], 'cache_host_sources_changed')
    native_sources = stock['sources_before']['wordpress']
    exact(host['sources_before'], {'source_manifest': native_sources['source_manifest'],
        'wordpress': native_sources['loaded_core_files'],
        'fixtures': {name: generated_inputs['fixtures/' + name] for name in ('entry.php', 'adapter.php', 'corpus.php')}},
        'cache_exact_mounted_source_inventory')
    exact(host['module']['sha256'], module['sha256'], 'cache_host_module_identity')
    exact(host['module']['bytes'], module['bytes'], 'cache_host_module_bytes')
    exact(host['build_receipt']['sha256'], provenance['build_receipt_sha256'], 'cache_host_managed_receipt')
    require(type(host['requests']) is list and len(host['requests']) == 2, 'cache_host_two_requests')
    groups = {'rp-cache-multiple': 14, 'rp-cache-integer': 16}
    finish = {'ok': True, 'operations': 32, 'entries': 0, 'charged_bytes': 0}
    expected_php = {'schema': PREFIX + 'guest-observation.v0.5', 'passed': True,
        'php_version': '8.5.12', 'php_integer_bytes': 8, 'wordpress_version': '7.1.3',
        'adapter_class': 'RpRustBackedCache', 'stock_parent_class': 'WP_Object_Cache',
        'profile': 'isolated_multisite_cache_prefix', 'observations': expected,
        'rust_transport': {'groups': groups, 'flushes': 2, 'finish': finish, 'channel_closed': True}}
    rows = []
    for label, row in zip(('first', 'repeat'), host['requests']):
        exact(row['case'], label, 'cache_host_request_label')
        for field in ('passed', 'fresh_runtime', 'guest_execution_started'):
            exact(row[field], True, 'cache_host_fresh_runtime')
        for field in ('guest_networking', 'host_environment_forwarded', 'host_home_mapped'):
            exact(row[field], False, 'cache_host_runtime_scope')
        exact(row['stdin_bytes'], 0, 'cache_guest_stdin')
        exact(row['php_result'], expected_php, 'cache_guest_native_difference')
        cache = row['cache']
        request_bytes, response_bytes = cache['request_bytes'], cache['response_bytes']
        require(all(type(x) is int and 1 <= x <= 65536 for x in (request_bytes, response_bytes)),
            'cache_channel_byte_budgets')
        exact(cache, {'operations': 32, 'entries': 0, 'charged_bytes': 0, 'flushes': 2, 'groups': groups,
            'opened_once': True, 'channel_open': False, 'finished': True, 'request_bytes': request_bytes,
            'response_bytes': response_bytes, 'response_read_bytes': response_bytes, 'pending_bytes': 0,
            'unread_response_bytes': 0, 'finish_dispatched': True}, 'cache_host_channel_completion')
        capture = row['cgi_capture']
        for field in ('guest_exit_success', 'runtime_shutdown_returned', 'stdout_eof', 'stderr_eof'):
            exact(capture[field], True, 'cache_guest_capture_completion')
        for field in ('stdout_overflow', 'stderr_overflow'):
            exact(capture[field], False, 'cache_guest_capture_quota')
        exact(capture['status'], 200, 'cache_guest_cgi_status')
        exact(capture['stderr'], identity(b''), 'cache_guest_empty_stderr')
        rows.append({'case': label, 'observations': len(expected), 'observations_identity': identity(canonical(expected)),
            'fresh_runtime': True, 'cgi_capture_completed': True, 'cache': cache})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report = {'schema': PREFIX + 'check.v0.5', 'passed': False}
    stage = 'inputs'
    try:
        require(sys.platform == 'linux', 'actual_cache_wasm_linux_only')
        env = environment()
        stock, _, expected = reference()
        report['sources_before'] = source_inputs()
        managed = load('rp_cache_managed', EXPERIMENTS / 'php-wasix-managed-access-probe/managed_check.py')
        report['module_provenance'] = managed.verify_build()
        stage = 'build'
        binary = build(report, env)
        stage = 'execute'
        result = execute([str(binary)], env, 300, report.setdefault('host_execution', {}), 'cache_host')
        require(result.stdout.endswith(b'\n') and result.stdout.count(b'\n') == 1
            and len(result.stdout) <= 2 * 1024 * 1024, 'cache_host_private_packet')
        host = strict(result.stdout)
        if type(host) is dict and host.get('passed') is False:
            failure = host.get('failure')
            if type(failure) is str and re.fullmatch('[a-z_]{1,80}', failure):
                report['host_failure'] = failure
            rows = host.get('requests')
            if type(rows) is list and 1 <= len(rows) <= 2:
                diagnostics = {}
                for row in rows:
                    if type(row) is not dict or row.get('case') not in ('first', 'repeat'):
                        continue
                    code = row.get('guest_failure')
                    if type(code) is str and re.fullmatch('[a-z][a-z0-9_]{0,80}', code):
                        diagnostics[row['case']] = code
                if diagnostics:
                    report['guest_failures'] = diagnostics
        require(result.returncode == 0 and not result.stderr, 'cache_host_execution_failed')
        stage = 'compare'
        report['requests'] = compare(host, stock, expected, report['module_provenance'], report['generated_inputs'])
        stage = 'after_verification'
        report['sources_after'] = source_inputs()
        exact(report['sources_after'], report['sources_before'], 'cache_source_after_changed')
        exact(generated(), report['generated_inputs'], 'cache_generated_after_changed')
        exact(identity(binary.read_bytes()), report['worker_build']['executable_identity'], 'cache_executable_after_changed')
        exact(managed.verify_build(), report['module_provenance'], 'cache_module_after_changed')
        report.update(passed=True, module_compilations=1, fresh_requests=2, source_reverified_after=True)
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if type(error) is ValueError and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        owned_probe.reap_owned_probes()
    if args.record:
        path = HERE / 'evidence/linux.json'
        path.parent.mkdir(exist_ok=True)
        require(path.resolve() == path, 'cache_evidence_path')
        with path.open('xb') as stream:
            stream.write(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False).encode() + b'\n')
    print('WordPress Wasm object cache: ' + ('passed' if report['passed'] else report.get('failure', 'failed')))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
