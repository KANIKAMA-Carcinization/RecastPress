"""Exercise the bypass watcher's held-stream disconnect against an owned Windows DB."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import runpy
import secrets
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FRONTIER = ROOT / 'experiments/binlog-autoload-frontier-probe/run_check.py'
TESTS = {
    'disconnect': 'monitor::implementation::live_gate_tests::owned_disconnect_after_buffered_event',
    'apply': 'monitor::implementation::live_gate_tests::owned_apply_buffered_event',
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def build_test():
    command = ['cargo', 'test', '--offline', '--locked', '--manifest-path', str(HERE / 'Cargo.toml'),
        '--no-run', '--message-format=json']
    result = subprocess.run(command, cwd=ROOT, capture_output=True, check=True)
    binaries = [Path(item['executable']).resolve() for line in result.stdout.splitlines()
        if (item := json.loads(line)).get('reason') == 'compiler-artifact'
        and item.get('profile', {}).get('test') and item.get('target', {}).get('name')
        == 'recastpress-php-wasix-autoload-bypass-probe' and item.get('executable')]
    if len(binaries) != 1 or not binaries[0].is_file():
        raise ValueError('disconnect_test_binary')
    return binaries[0]


def run(backend, binary, case, mysql_client_for_mariadb=False):
    if not __debug__:
        raise ValueError('disconnect_assertions_required')
    frontier = runpy.run_path(str(FRONTIER))
    module = frontier['load_owner']()
    mysql_client = {}
    if backend == 'mariadb' and mysql_client_for_mariadb:
        db = module['db']
        original_config, original_command = db.client_config, db.client_command

        def verified_config(harness, name, user, password, port, selected, tls, ca):
            if selected == 'mariadb':
                if not mysql_client:
                    mysql_client['tool'], mysql_client['verification'] = db.verify_tool('mysql', harness)
                selected = 'mysql'
            return original_config(harness, name, user, password, port, selected, tls, ca)

        def verified_command(tool, selected, config, *, admin=False):
            if selected == 'mariadb':
                return original_command(mysql_client['tool'], 'mysql', config, admin=admin)
            return original_command(tool, selected, config, admin=admin)

        db.client_config, db.client_command = verified_config, verified_command
    report = {}
    owner = module['OwnedDatabase'](backend, report)
    sources = [HERE / name for name in ('Cargo.toml', 'Cargo.lock', 'src/live_watch.rs',
        'src/monitor_impl.rs', 'src/monitor.rs', 'src/options_cache.rs', 'src/main.rs',
        'src/binlog_buffer.rs', 'check_disconnect_owned.py')]
    sources += [FRONTIER, frontier['OWNER']]
    before = {p.relative_to(ROOT).as_posix(): digest(p) for p in sources}
    binary_sha = digest(binary)
    started = time.monotonic()
    test = TESTS[case]
    stage = 'owned_database'
    try:
        with owner:
            assert report['database_identity_verified_before_fixture_ddl'] is True
            stage = 'seed_fixture'
            nonce, password, writer_password = [secrets.token_hex(24) for _ in range(3)]
            status = 'REPLICATION CLIENT' if backend == 'mysql' else 'BINLOG MONITOR'
            sql = ("USE rp_wpdb_probe;\n"
                "CREATE TABLE rp_options (option_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, option_name VARCHAR(191) NOT NULL DEFAULT '', "
                "option_value LONGTEXT NOT NULL, autoload VARCHAR(20) NOT NULL DEFAULT 'yes', PRIMARY KEY(option_id), "
                "UNIQUE KEY option_name(option_name), KEY autoload(autoload)) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
                "INSERT INTO rp_options(option_name,option_value,autoload) VALUES ('rp_autoload_probe_marker','rp-autoload-old','yes');\n"
                "CREATE DATABASE rp_options_guard_fixture;\n"
                "CREATE TABLE `rp_options_guard_fixture`.`identity` (id BIGINT UNSIGNED PRIMARY KEY, nonce VARBINARY(48) NOT NULL) ENGINE=InnoDB;\n"
                f"INSERT INTO `rp_options_guard_fixture`.`identity` VALUES (1,X'{nonce.encode().hex()}');\n"
                f"CREATE USER 'rp_dependency_monitor'@'%' IDENTIFIED BY '{password}';\n"
                f"CREATE USER 'rp_dependency_writer'@'%' IDENTIFIED BY '{writer_password}';\n"
                "GRANT SELECT ON rp_wpdb_probe.rp_options TO 'rp_dependency_monitor'@'%';\n"
                "GRANT SELECT ON `rp_options_guard_fixture`.`identity` TO 'rp_dependency_monitor'@'%';\n"
                f"GRANT REPLICATION SLAVE, {status} ON *.* TO 'rp_dependency_monitor'@'%';\n"
                "GRANT SELECT,UPDATE ON rp_wpdb_probe.rp_options TO 'rp_dependency_writer'@'%';\n").encode('ascii')
            seeded = owner.harness.checked('disconnect_seed_fixture', owner.admin, timeout=10, input_bytes=sql)
            assert not seeded.stdout and not seeded.stderr
            stage = 'disconnect_test'
            payload = dict(port=owner.port, nonce=nonce, password=password, writer_password=writer_password)
            result = owner.harness.run('owned_test', [str(binary), '--ignored', '--exact', test, '--nocapture'],
                timeout=30, input_bytes=json.dumps(payload).encode(), limit=8192)
            if result.returncode:
                # The Rust fixture maps database errors to fixed labels. Keep
                # failure diagnostics finite and redact credentials/paths.
                diagnostic = (result.stdout + result.stderr).decode('utf-8', errors='replace')[-3072:]
                for secret in (nonce, password, writer_password):
                    diagnostic = diagnostic.replace(secret, '<redacted>')
                diagnostic = re.sub(r'[A-Za-z]:[\\/][^\s]+', '<path>', diagnostic)
                raise ValueError('owned_test_failed: ' + diagnostic)
            assert result.returncode == 0 and not result.stderr
            assert ('test ' + test + ' ... ok').encode() in result.stdout
            assert b'1 passed; 0 failed' in result.stdout
            stage = 'verify_independent_value'
            result = owner.harness.checked('disconnect_independent_value', [*owner.admin,
                "--execute=SELECT HEX(option_value) FROM rp_wpdb_probe.rp_options WHERE option_name='rp_autoload_probe_marker'"], timeout=5)
            assert result.stdout.strip() == b'rp-autoload-new'.hex().upper().encode() and not result.stderr
        stage = 'cleanup'
        assert report['normal_admin_shutdown_verified'] and report['database_closed_and_reaped']
        assert report['all_command_children_reaped']
        assert all(digest(ROOT / name) == value for name, value in before.items())
        assert digest(binary) == binary_sha
        out = dict(backend=backend, case=case, version=report['sql_server_version'], passed=True,
            buffered_event_observed=True, pending_event_bypassed=True, old_permit_revoked=True,
            normal_admin_shutdown=True, all_processes_reaped=True, sources=before, binary_sha256=binary_sha)
        if case == 'disconnect':
            out.update(direct_read_new_value=True, monitor_restart_resynced=True,
                previous_cache_not_reused=True)
        else:
            out.update(commit_applied=True, cached_reply_disabled=True, cache_reservation_released=True)
        out['admin_client'] = 'verified_mysql_8.4.13' if mysql_client else backend
        out['admin_tls'] = 'verify_identity'
        if mysql_client:
            out['admin_client_verification'] = mysql_client['verification']
    except Exception as error:
        reason = str(error)
        out = dict(backend=backend, passed=False, failed_stage=stage, failure_class=type(error).__name__,
            server_reaped=report.get('database_closed_and_reaped', False),
            normal_admin_shutdown=report.get('normal_admin_shutdown_verified', False),
            all_processes_reaped=report.get('all_command_children_reaped', False))
        if re.fullmatch('[a-z_]{1,100}', reason):
            out['failure'] = reason
        elif stage == 'disconnect_test' and reason.startswith('owned_test_failed: '):
            out['test_diagnostic'] = reason.removeprefix('owned_test_failed: ')[:3072]
    if hasattr(owner, 'work') and owner.work.exists() and (report.get('database_closed_and_reaped') or owner.harness.server is None):
        work = owner.work.resolve(strict=True)
        assert work.parent == module['db'].RUNS.resolve(strict=True) and work == owner.work
        assert re.fullmatch(f'bridge-{backend}-[0-9a-f]{{32}}', work.name) and not owner.harness.owned_children
        shutil.rmtree(work)
        out['owned_directory_removed'] = not work.exists()
    out['elapsed_seconds'] = round(time.monotonic() - started, 2)
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('mysql', 'mariadb'))
    parser.add_argument('--record', action='store_true')
    parser.add_argument('--case', choices=tuple(TESTS), default='disconnect')
    parser.add_argument('--mysql-client-for-mariadb', action='store_true',
        help='use the verified MySQL client with TLS identity verification for the MariaDB fixture')
    args = parser.parse_args()
    binary = build_test()
    results = []
    for backend in (args.backend,) if args.backend else ('mysql', 'mariadb'):
        result = run(backend, binary, args.case, args.mysql_client_for_mariadb)
        results.append(result)
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.record and result['passed'] and result.get('owned_directory_removed'):
            path = HERE / 'evidence' / f'windows-held-{args.case}-v5-{backend}.json'
            with path.open('x', encoding='utf-8', newline='\n') as output:
                json.dump(result, output, indent=2, sort_keys=True)
                output.write('\n')
    sys.exit(0 if all(r['passed'] and r.get('owned_directory_removed') for r in results) else 1)
