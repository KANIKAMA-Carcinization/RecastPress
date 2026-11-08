"""Compare the fixed stock wpdb error corpus through PHP-Wasm and real DB wire."""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import secrets
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXPERIMENTS = HERE.parent
TARGET = ROOT / 'target/php-wasix-wpdb-raw-error-probe/cargo-target'
NAME = 'recastpress-php-wasix-wpdb-raw-error-probe'
PREFIX = 'recastpress.php-wasix-wpdb-raw-error-'
NATIVE = {
    'mysql': 'effcbe7b643af00336a1ae5a635088220bd37c2f9acf730f1746252fccf56efc',
    'mariadb': '8c4952a87f7aa946c823dcb2e62d3432cfdc6daf07b705799014b10758e91d55',
}
ERRORS = ('utf8_signal', 'latin1_signal', 'next_signal')
SALT_NAMES = ('AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY',
    'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = load('rp_raw_cache_controller_helpers', EXPERIMENTS / 'php-wasix-wp-object-cache-probe/run_check.py')
database_module = load('rp_raw_wasix_db_owner', EXPERIMENTS / 'php-wasix-wpdb-probe/linux_database.py')
require, exact, identity, strict = common.require, common.exact, common.identity, common.strict


def reference(backend):
    path = EXPERIMENTS / 'wpdb-raw-error-wire-probe/evidence' / f'windows-{backend}-native.json'
    wire = path.read_bytes()
    exact(identity(wire)['sha256'], NATIVE[backend], 'raw_error_native_source')
    value = strict(wire)
    exact(value['passed'], True, 'raw_error_native_passed')
    exact(value['error_then_success_clears'], True, 'raw_error_native_recovery')
    return value


def sources():
    paths = [HERE / name for name in ('Cargo.toml', 'Cargo.lock', 'derive_guest.py', 'derive_actor.py',
        'derive_host.py', 'run_check.py')]
    for directory, patterns in (
        ('php-wasix-wpdb-utf8-probe', ('src/*.rs', 'fixtures/*.php', 'Cargo.toml', 'Cargo.lock')),
        ('php-wasix-wp-bootstrap-probe', ('src/db.rs', 'src/result_budget.rs', 'src/rpc_fs.rs', 'src/capture.rs')),
        ('wpdb-raw-error-probe', ('prepare_driver.py', 'patch-plan.json', 'patches/*.patch')),
        ('wpdb-raw-error-wire-probe', ('wire_proxy.py', 'fixtures/native.php', 'evidence/windows-*-native.json')),
    ):
        for pattern in patterns:
            paths.extend(sorted((EXPERIMENTS / directory).glob(pattern)))
    paths += [ROOT / name for name in ('.github/workflows/php-wasix-wpdb-raw-error.yml', 'scripts/owned_probe.py',
        'experiments/php-wasix-wp-object-cache-probe/run_check.py', 'experiments/php-wasix-wpdb-probe/linux_database.py',
        'experiments/php-wasix-wp-private-worker-probe/src/strict_json.rs', 'experiments/php-wasi-host-probe/src/cgi_response.rs',
        'experiments/php-wasix-host-probe/src/fiber_adapter.rs', 'experiments/php-wasix-wpdb-probe/src/inputs.rs',
        'experiments/wp-source-fs-probe/Cargo.toml')]
    paths.extend(sorted((EXPERIMENTS / 'wp-source-fs-probe/src').rglob('*.rs')))
    entries = {}
    while paths:
        path = paths.pop().resolve()
        require(path.is_relative_to(ROOT), 'raw_error_source_scope')
        name = path.relative_to(ROOT).as_posix()
        if name in entries:
            continue
        wire = path.read_bytes()
        entries[name] = identity(wire)
        if path.suffix == '.rs':
            for path_text in re.findall(rb'(?:#\[path\s*=\s*|include_(?:bytes|str)!\(\s*)"([^"]+)"', wire):
                paths.append(path.parent / path_text.decode('ascii'))
    return dict(sorted(entries.items()))


def generated():
    paths = sorted((HERE / 'src').glob('*.rs')) + sorted((HERE / 'fixtures').glob('*.php'))
    require(len(paths) == 7, 'raw_error_generated_inventory')
    return {path.relative_to(HERE).as_posix(): identity(path.read_bytes()) for path in paths}


def build(env, report):
    commands = report['build_commands'] = {}
    for script in ('derive_guest.py', 'derive_actor.py', 'derive_host.py'):
        result = common.execute([sys.executable, '-B', str(HERE / script)], env, 60, commands, script)
        require(result.returncode == 0, 'raw_error_source_derivation')
    report['generated_inputs'] = generated()
    args = ['--locked', '--offline', '--release', '--manifest-path', str(HERE / 'Cargo.toml'), '--target-dir', str(TARGET)]
    result = common.execute(['cargo', 'test', '--quiet', *args], env, 1200, commands, 'unit')
    units = re.findall(rb'test result: ok\. ([0-9]+) passed; 0 failed; 0 ignored;', result.stdout)
    require(result.returncode == 0 and len(units) == 1 and int(units[0]) > 0, 'raw_error_worker_units')
    report['units_passed'] = int(units[0])
    result = common.execute(['cargo', 'build', '--message-format=json-render-diagnostics', *args], env, 1200, commands, 'build')
    require(result.returncode == 0, 'raw_error_worker_build')
    rows = [strict(line) for line in result.stdout.splitlines()]
    binary = TARGET / 'release' / NAME
    candidates = [row for row in rows if row['reason'] == 'compiler-artifact' and row['target']['name'] == NAME]
    require(len(candidates) == 1 and rows[-1] == {'reason': 'build-finished', 'success': True}, 'raw_error_worker_artifact')
    row = candidates[0]
    require(row['target']['kind'] == ['bin'] and row['profile']['test'] is False
        and Path(row['manifest_path']) == HERE / 'Cargo.toml' and Path(row['target']['src_path']) == HERE / 'src/main.rs'
        and Path(row['executable']) == binary and binary.resolve(strict=True) == binary
        and 0 < binary.stat().st_size <= 256 * 1024 * 1024, 'raw_error_worker_artifact_path')
    report['worker_build'] = {'executable': binary.relative_to(ROOT).as_posix(),
        'executable_identity': identity(binary.read_bytes()), 'locked_offline': True}
    return binary


def compare(host, native, wire):
    for field, expected in (('schema', PREFIX + 'observation.v0.5'), ('passed', True),
            ('outcome', 'fixed_wasix_wpdb_raw_error_passed'), ('phase', 'full_private_raw_error_verified'),
            ('module_compilations', 1)):
        exact(host[field], expected, 'raw_error_host_outcome')
    for field in ('physical_connection_created', 'physical_disconnect_completed', 'db_actor_joined', 'db_actor_exited',
            'source_rechecked_after_execution', 'fresh_runtime', 'private_ipc_only'):
        exact(host[field], True, 'raw_error_host_cleanup')
    for field in ('publication_authorized', 'native_comparison_performed', 'native_php_fallback',
            'guest_networking', 'host_environment_forwarded', 'db_credentials_supplied_to_guest'):
        exact(host[field], False, 'raw_error_host_scope')
    exact(host['db_actor_failure'], None, 'raw_error_actor_failure')
    capture = host['cgi_capture']
    for field in ('guest_exit_success', 'runtime_shutdown_returned', 'stdout_eof', 'stderr_eof'):
        exact(capture[field], True, 'raw_error_capture_complete')
    for field in ('stdout_overflow', 'stderr_overflow'):
        exact(capture[field], False, 'raw_error_capture_quota')
    exact(capture['stderr_bytes'], 0, 'raw_error_guest_stderr')
    rpc = host['rpc']
    for field in ('finished', 'opened_once', 'close_dispatched'):
        exact(rpc[field], True, 'raw_error_rpc_finished')
    for field in ('pending_bytes', 'unread_response_bytes'):
        exact(rpc[field], 0, 'raw_error_rpc_drained')
    exact(rpc['failure'], None, 'raw_error_rpc_failure')
    exact(rpc['operations'], 13, 'raw_error_rpc_count')
    exact(wire['observations'], native['wire']['observations'], 'raw_error_real_wire_matches_native')
    php = host['php_result']
    exact(php['schema'], PREFIX + 'php-observation.v0.5', 'raw_error_guest_schema')
    exact(php['outcome'], 'fixed_raw_error_cases_passed', 'raw_error_guest_outcome')
    exact(php['raw_non_utf8_err_verified'], True, 'raw_error_non_utf8_verified')
    exact(php['embedded_nul_exercised'], False, 'raw_error_nul_scope')
    actual, expected = php['observations'], native['observation']['observations']
    require(set(actual) == set(expected) and len(expected) == 7, 'raw_error_seven_sql_observations')
    compared = {}
    for name, want in expected.items():
        row = actual[name]
        require(set(row) == {'query_hex', 'returned', 'state', 'last_result', 'rpc_error'}, 'raw_error_guest_public_fields')
        for key in ('query_hex', 'returned', 'state', 'last_result'):
            exact(row[key], want[key], 'raw_error_stock_wpdb_difference')
        exact(row['rpc_error'], {'code': want['mysqli_errno'], 'state_hex': want['mysqli_sqlstate_hex'],
            'message_hex': want['mysqli_error_hex']}, 'raw_error_private_rpc_error_difference')
        compared[name] = {'query': identity(bytes.fromhex(want['query_hex'])), 'state_matches_native': True,
            'return_matches_native': True, 'last_result_matches_native': True, 'error_matches_native': True}
    return compared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=NATIVE, required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report = {'schema': PREFIX + 'check.v0.5', 'backend': args.backend, 'passed': False}
    owned, database = {}, None
    salts = {name: secrets.token_hex(24) for name in SALT_NAMES}
    stage = 'source_preflight'
    try:
        require(sys.platform == 'linux', 'raw_error_linux_only')
        env = common.environment()
        native = reference(args.backend)
        report['sources_before'] = sources()
        managed = load('rp_raw_managed', EXPERIMENTS / 'php-wasix-managed-access-probe/managed_check.py')
        prepare_driver = load('rp_raw_accessor_prepare', EXPERIMENTS / 'wpdb-raw-error-probe/prepare_driver.py')
        report['module_provenance'] = managed.verify_build()
        report['driver_preparation'] = prepare_driver.prepare()
        stage = 'build'
        binary = build(env, report)
        proxy_module = load('rp_raw_linux_wire', EXPERIMENTS / 'wpdb-raw-error-wire-probe/wire_proxy.py')
        # This owned worker compiles PHP-Wasm before opening its DB connection.
        proxy_module.DEADLINE_SECONDS = 305
        database = database_module.Database(args.backend, owned)
        with database:
            stage = 'wasm_execution'
            with proxy_module.WireProxy(database.port) as proxy:
                payload = {'schema': 'recastpress.php-wasix-wp-bootstrap-input.v0.5', 'mode': 'read',
                    'host': '127.0.0.1', 'port': proxy.port, 'database': 'rp_wpdb_probe', 'user': 'rp_wpdb_probe',
                    'password': database.secret, 'nonce': database.nonce, 'salts': salts}
                pid, result = common.owned_probe.run_captured_probe([str(binary)], env, timeout=300,
                    input_bytes=common.canonical(payload))
                report['host_execution'] = {'owned_pid': pid, 'owned_reaped': True, 'exit_code': result.returncode,
                    'stdout': identity(result.stdout), 'stderr': identity(result.stderr)}
                require(0 < len(result.stdout) <= 4 * 1024 * 1024 and result.stdout.endswith(b'\n')
                    and result.stdout.count(b'\n') == 1, 'raw_error_private_packet')
                host = strict(result.stdout)
                if type(host) is dict and host.get('passed') is False:
                    for key in ('error', 'guest_failure', 'phase'):
                        code = host.get(key)
                        if type(code) is str and re.fullmatch('[a-z][a-z0-9_]{0,80}', code):
                            report['host_' + key] = code
                require(result.returncode == 0 and not result.stderr, 'raw_error_private_execution')
                observed = proxy.result()
            stage = 'compare'
            report['observations'] = compare(host, native, observed)
            report['wire_observations'] = observed['observations']
            database.inspect()
            result = database.run('raw_error_final_rows', database.client() + [
                '--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_wire_fixture'])
            require(result.stdout.strip() == b'0' and not result.stderr, 'raw_error_owned_db_unchanged')
        stage = 'postflight'
        require(all(owned[key] is True for key in ('container_stopped', 'container_removed',
            'container_normal_exit_verified')), 'raw_error_database_cleanup')
        report['sources_after'] = sources()
        exact(report['sources_after'], report['sources_before'], 'raw_error_source_changed')
        exact(generated(), report['generated_inputs'], 'raw_error_generated_changed')
        exact(identity(binary.read_bytes()), report['worker_build']['executable_identity'], 'raw_error_binary_changed')
        exact(managed.verify_build(), report['module_provenance'], 'raw_error_module_changed')
        exact(prepare_driver.prepare(), report['driver_preparation'], 'raw_error_patched_driver_changed')
        report.update(passed=True, independent_database_unchanged=True, owned_database_normal_cleanup=True,
            source_reverified_after=True, sql_server_version=owned['sql_server_version'])
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        common.owned_probe.reap_owned_probes()
    wire = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode()
    if database is not None:
        for secret in (database.secret, database.admin_secret, database.nonce, *salts.values()):
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'raw_error_secret_guard')
    if args.record:
        path = HERE / 'evidence' / f'linux-{args.backend}.json'
        path.parent.mkdir(exist_ok=True)
        require(path.resolve() == path, 'raw_error_evidence_path')
        with path.open('xb') as stream:
            stream.write(wire)
    print('WordPress Wasm raw ERR: ' + ('passed' if report['passed'] else report.get('failure', 'failed')))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
