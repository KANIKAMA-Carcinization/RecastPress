"""Run the WordPress PHP-WASIX install/read contract with a local MariaDB distribution."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments/php-wasix-wp-bootstrap-probe'))
import run_check as probe


def check(condition, name):
    if not condition:
        raise RuntimeError(name)


def loopback_port():
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        return reservation.getsockname()[1]


def run(binary_root, report, private):
    binary_root = binary_root.resolve(strict=True)
    if (binary_root / 'usr/sbin/mariadbd').is_file():
        basedir = binary_root / 'usr'
        server = basedir / 'sbin/mariadbd'
        initializer = basedir / 'bin/mariadb-install-db'
    else:
        basedir = binary_root
        server = basedir / 'bin/mariadbd'
        initializer = basedir / 'scripts/mariadb-install-db'
    client = basedir / 'bin/mariadb'
    check(all(path.is_file() for path in (initializer, server, client)), 'local_mariadb_distribution')
    env = probe.build.environment()
    library_paths = [str(binary_root / name) for name in (
        'usr/lib/x86_64-linux-gnu', 'lib/x86_64-linux-gnu', 'lib')]
    if env.get('LD_LIBRARY_PATH'):
        library_paths.append(env['LD_LIBRARY_PATH'])
    env['LD_LIBRARY_PATH'] = ':'.join(library_paths)
    env['PATH'] = f'{basedir}/bin:{env["PATH"]}'
    actor_env = probe.build.environment()

    receipt = probe.build.verify_build()
    prepared = probe.prepared.verify_prepared()
    module = probe.managed.verify_build()
    native_hook, _ = probe.native_hook_oracle()
    overlay_hook, _ = probe.native_hook_overlay_oracle()
    cross = probe.compare_native_hook_oracles(native_hook, overlay_hook)
    cleanup, _ = probe.native_hook_failure_cleanup()
    report.update(module=module['module'], host_executable_sha256=receipt['executable_identity']['sha256'],
                  source_inputs=receipt['input_files'], native_hook_comparison=cross['status'],
                  native_hook_failure_cleanup=cleanup['status'])
    native_wp = probe.reference('mariadb')

    user = getpass.getuser()
    with tempfile.TemporaryDirectory(prefix='recastpress-wp-') as workspace:
        work = Path(workspace)
        data = work / 'data'
        endpoint = work / 'db.sock'
        pidfile = work / 'db.pid'
        port = loopback_port()
        shared = basedir / 'lib/mysql/plugin'
        commands = [str(client), '--no-defaults', f'--user={user}', f'--socket={endpoint}',
                    '--protocol=socket', '--batch', '--skip-column-names', '--raw', '--connect-timeout=3']
        init = subprocess.run(
            [str(initializer), '--no-defaults', f'--datadir={data}', f'--basedir={basedir}',
             '--auth-root-authentication-method=socket', f'--auth-root-socket-user={user}', '--skip-test-db'],
            env=env, cwd=work, capture_output=True, timeout=60)
        if init.returncode:
            print(init.stderr.decode(errors='replace')[-1800:], file=sys.stderr)
            raise RuntimeError('mariadb_install_db')
        argv = [str(server), '--no-defaults', f'--basedir={basedir}', f'--datadir={data}',
                f'--pid-file={pidfile}', f'--socket={endpoint}', f'--port={port}', '--bind-address=127.0.0.1',
                '--skip-name-resolve', '--skip-log-bin', f'--tmpdir={work}', '--innodb-buffer-pool-size=64M']
        if shared.is_dir():
            argv.append(f'--plugin-dir={shared}')
        log = work / 'server.log'
        with log.open('wb') as stream:
            daemon = subprocess.Popen(argv, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                      stdout=stream, stderr=subprocess.STDOUT)
        try:
            def sql(statement, fail=True):
                result = subprocess.run(commands, cwd=work, env=env, input=statement.encode(),
                                        capture_output=True, timeout=15)
                if fail:
                    check(result.returncode == 0, 'local_mariadb_client')
                return result

            until = time.monotonic() + 30
            while time.monotonic() < until:
                if daemon.poll() is not None:
                    print(log.read_text(errors='replace')[-1600:], file=sys.stderr)
                    raise RuntimeError('local_mariadb_exited')
                reply = sql('SELECT VERSION(),@@datadir,@@port,@@pid_file,@@bind_address;\n', fail=False)
                if reply.returncode == 0:
                    rows = reply.stdout.decode().strip().split('\t')
                    if len(rows) == 5:
                        break
                time.sleep(0.25)
            else:
                print(log.read_text(errors='replace')[-1000:], file=sys.stderr)
                raise RuntimeError('local_mariadb_not_ready')
            version, server_data, server_port, server_pidfile, bind = rows
            check((Path(server_data), int(server_port), Path(server_pidfile), bind,
                   int(pidfile.read_text().strip())) ==
                  (data, port, pidfile, '127.0.0.1', daemon.pid), 'local_mariadb_identity')
            report.update(database_version=version, database_loopback_only=True, database_transport='owned_local_process')
            print(f'Owned MariaDB {version} is ready', flush=True)
            password = {mode: secrets.token_hex(24) for mode in ('install', 'read')}
            wp_password = secrets.token_hex(24)
            salts = {name: secrets.token_hex(24) for name in probe.linux.SALT_NAMES}
            private.extend([*password.values(), wp_password, *salts.values()])
            sql("CREATE DATABASE rp_wpdb_probe CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
                "CREATE TABLE rp_wpdb_probe.rp_wire_fixture(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,"
                "marker VARBINARY(32) NOT NULL UNIQUE,payload VARBINARY(64) NULL,"
                "dec_value DECIMAL(30,10) NOT NULL,big_value BIGINT UNSIGNED NOT NULL) ENGINE=InnoDB;\n"
                f"CREATE USER 'rp_wp_setup'@'127.0.0.1' IDENTIFIED BY '{password['install']}';\n"
                "GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,INDEX,DROP ON rp_wpdb_probe.* TO 'rp_wp_setup'@'127.0.0.1';\n"
                f"CREATE USER 'rp_wpdb_probe'@'127.0.0.1' IDENTIFIED BY '{password['read']}';\n"
                "GRANT SELECT,INSERT,UPDATE,DELETE ON rp_wpdb_probe.* TO 'rp_wpdb_probe'@'127.0.0.1';\n")
            for mode, account in (('install', 'rp_wp_setup'), ('read', 'rp_wpdb_probe')):
                nonce = secrets.token_hex(24)
                private.append(nonce)
                request = {'schema': 'recastpress.php-wasix-wp-bootstrap-input.v0.5',
                           'mode': mode, 'host': '127.0.0.1', 'port': port,
                           'database': 'rp_wpdb_probe', 'user': account, 'password': password[mode],
                           'salts': salts, 'nonce': nonce}
                if mode == 'install':
                    request['admin_password'] = wp_password
                print(f'Executing real PHP-WASIX {mode}', flush=True)
                _, result = probe.owned_probe.run_captured_probe(
                    [str(probe.ROOT / receipt['executable'])], actor_env, timeout=300,
                    input_bytes=probe.canonical(request))
                value = json.loads(result.stdout)
                report['observations'][mode] = value
                check(result.returncode == 0 and value['passed'] is True and not result.stderr, f'{mode}_guest_failed')
                report['validations'][mode] = probe.validate(value, mode, native_wp[mode], native_hook)
                print(f'WordPress {mode} verified', flush=True)
            tables = sorted(sql('SHOW TABLES FROM rp_wpdb_probe;\n').stdout.decode().splitlines())
            check(tables == sorted(['rp_' + t for t in probe.linux.TABLES] + ['rp_wire_fixture']), 'wp_tables')
            options = sql("SELECT option_name,option_value FROM rp_wpdb_probe.rp_options WHERE option_name "
                          "IN ('blogname','home','siteurl') ORDER BY option_name;\n").stdout.decode().splitlines()
            check(options == ['blogname\tRP Bootstrap Fixture', 'home\thttp://rp-fixture.invalid',
                              'siteurl\thttp://rp-fixture.invalid'], 'wp_options')
            admins = sql("SELECT COUNT(*) FROM rp_wpdb_probe.rp_users WHERE user_login='rp_fixture_admin';\n").stdout.strip()
            check(admins == b'1', 'wp_admin_count')
            report.update(independent_database_verification=True, wordpress_table_count=12)
        finally:
            if daemon.poll() is None:
                try:
                    report['normal_database_shutdown_requested'] = sql('SHUTDOWN;\n', fail=False).returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    report['normal_database_shutdown_requested'] = False
            try:
                report['database_process_exit_code'] = daemon.wait(timeout=20)
            except subprocess.TimeoutExpired:
                report['forced_database_shutdown'] = True
                daemon.terminate()
                try:
                    report['database_process_exit_code'] = daemon.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    report['database_process_exit_code'] = daemon.wait()
    check(report.get('normal_database_shutdown_requested') is True
          and not report.get('forced_database_shutdown')
          and report['database_process_exit_code'] == 0, 'mariadb_shutdown')
    check(probe.build.verify_build() == receipt and probe.prepared.verify_prepared() == prepared
          and probe.managed.verify_build() == module, 'source_recheck')
    report['source_reverified_after'] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mariadb-root', type=Path, required=True,
                        help='extracted MariaDB tarball or directory containing extracted usr/')
    parser.add_argument('--report', type=Path,
                        default=ROOT / 'target/php-wasix-wp-bootstrap-probe/local-wp-boot.json')
    args = parser.parse_args()
    binary_root = args.mariadb_root.absolute()
    output = args.report.absolute()
    os.chdir(ROOT)
    report = {'schema': 'recastpress.v06-local-wp-boot.v1', 'passed': False,
              'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'observations': {}, 'validations': {}}
    private = []
    try:
        run(binary_root, report, private)
        report['passed'] = True
    except Exception as error:
        report['failure'] = str(error)
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
    finally:
        probe.owned_probe.reap_owned_probes()
        wire = json.dumps(report, indent=2).encode() + b'\n'
        check(not any(s.encode() in wire or s.encode().hex().encode() in wire for s in private), 'private_output')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(wire)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
