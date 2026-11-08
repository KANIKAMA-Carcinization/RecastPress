from hashlib import sha256
import argparse
import json
from pathlib import Path
import re
import secrets
import shutil
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OWNER = ROOT / 'experiments/wpdb-bridge-probe/owned_database.py'
BINARY = ROOT / 'target/wpdb-rpc-probe/windows-cargo-target/debug/recastpress-binlog-autoload-frontier-probe.exe'
OWNER_SHA = 'c98af5c7916822b203ddecdef2793bfb0ef50ec10649a97b0673aa02c363314c'
SOURCES = ('Cargo.toml', 'Cargo.lock', 'derive.py', 'run_check.py', 'src/main.rs', 'src/live.rs',
    'src/monitor.rs', 'src/monitor_impl.rs', 'src/binlog_buffer.rs')


def source_hashes():
    return {name: sha256((HERE / name).read_bytes()).hexdigest() for name in SOURCES}



def load_owner():
    wire = OWNER.read_bytes()
    assert OWNER.resolve() == OWNER and sha256(wire).hexdigest() == OWNER_SHA
    source = wire.decode('utf-8').replace('\r\n', '\n')
    grant = '''            "GRANT ALL PRIVILEGES ON rp_wpdb_probe.* TO 'rp_probe_admin'@'127.0.0.1' WITH GRANT OPTION;\\n"
'''
    assert source.count(grant) == 1
    source = source.replace(grant, grant + '''            "GRANT ALL PRIVILEGES ON rp_options_guard_fixture.* TO 'rp_probe_admin'@'127.0.0.1' WITH GRANT OPTION;\\n"
            "GRANT REPLICATION SLAVE, " + ("REPLICATION CLIENT" if self.backend == "mysql" else "BINLOG MONITOR")
            + " ON *.* TO 'rp_probe_admin'@'127.0.0.1' WITH GRANT OPTION;\\n"
''')
    assert source.count('skip-log-bin\\n') == 1
    source = source.replace('skip-log-bin\\n', 'gtid-mode=OFF\\nbinlog-transaction-compression=OFF\\n')
    anchor = "        self.server_config = db.write_private(work / 'server.ini', text.encode('utf-8'))"
    assert source.count(anchor) == 1
    source = source.replace(anchor, '''        text += ('server-id=7\\nlog-bin=' + (work / 'probe-bin').as_posix() +
            '\\nbinlog-format=ROW\\nbinlog-row-image=FULL\\nbinlog-checksum=CRC32\\nbinlog-do-db=rp_wpdb_probe\\n')
''' + anchor)
    module = {'__file__': str(OWNER), '__name__': 'scratch_owned_bridge_binlog_monitor'}
    exec(compile(source, str(OWNER), 'exec'), module)
    return module


def run(backend):
    if not __debug__:
        raise ValueError('frontier_runtime_checks_required')
    report = {}
    start = time.monotonic()
    module = load_owner()
    owner = module['OwnedDatabase'](backend, report)
    before = source_hashes()
    assert BINARY.resolve(strict=True) == BINARY
    binary_sha = sha256(BINARY.read_bytes()).hexdigest()
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
                "INSERT INTO rp_options(option_name,option_value,autoload) VALUES ('siteurl','http://rp-fixture.invalid','yes'),"
                "('rp_autoload_probe_marker','rp-autoload-old','yes');\n"
                "CREATE DATABASE rp_options_guard_fixture;\n"
                "CREATE TABLE `rp_options_guard_fixture`.`identity` (id BIGINT UNSIGNED PRIMARY KEY, nonce VARBINARY(48) NOT NULL) ENGINE=InnoDB;\n"
                f"INSERT INTO `rp_options_guard_fixture`.`identity` VALUES (1,X'{nonce.encode().hex()}');\n"
                f"CREATE USER 'rp_dependency_monitor'@'%' IDENTIFIED BY '{password}';\n"
                f"CREATE USER 'rp_dependency_writer'@'%' IDENTIFIED BY '{writer_password}';\n"
                "GRANT SELECT ON rp_wpdb_probe.rp_options TO 'rp_dependency_monitor'@'%';\n"
                "GRANT SELECT ON `rp_options_guard_fixture`.`identity` TO 'rp_dependency_monitor'@'%';\n"
                f"GRANT REPLICATION SLAVE, {status} ON *.* TO 'rp_dependency_monitor'@'%';\n"
                "GRANT SELECT,UPDATE ON rp_wpdb_probe.rp_options TO 'rp_dependency_writer'@'%';\n").encode('ascii')
            result = owner.harness.checked('binlog_monitor_seed_fixture', owner.admin, timeout=10, input_bytes=sql)
            assert not result.stdout and not result.stderr
            stage = 'native_monitor'
            payload = dict(port=owner.port, nonce=nonce, password=password, writer_password=writer_password)
            result = owner.harness.run('native_binlog_monitor', [str(BINARY)], timeout=40,
                input_bytes=json.dumps(payload).encode(), limit=8192)
            actual = json.loads(result.stdout)
            if result.returncode and type(actual) is dict:
                code = actual.get('error')
                if type(code) is str and re.fullmatch('[a-z_]{1,100}', code):
                    raise ValueError(code)
            assert result.returncode == 0 and not result.stderr and actual['passed'] is True
            assert set(actual) == {'passed','backend','source_file','from_position','through_position',
                'events','checksums','commits','frontier_queries','paths','uncached_selects',
                'fixture_refill_reused_quiescent_fallback','watcher_closed'}
            assert actual['backend'] == backend
            assert actual['paths'] == ['hit','mismatched','hit','unavailable']
            assert actual['frontier_queries'] == 4 and actual['uncached_selects'] == 2
            assert actual['fixture_refill_reused_quiescent_fallback'] is True and actual['watcher_closed'] is True
            assert actual['commits'] == 1 and 0 < actual['through_position'] - actual['from_position'] <= 65536
            assert 1 <= actual['checksums'] <= actual['events'] <= 16
            stage = 'verify_independent_values'
            result = owner.harness.checked('verify_native_autoload_frontier_value', [*owner.admin,
                "--execute=SELECT HEX(option_value) FROM rp_wpdb_probe.rp_options WHERE option_name='rp_autoload_probe_marker'"], timeout=5)
            assert result.stdout.strip() == b'rp-autoload-new'.hex().upper().encode() and not result.stderr
        stage = 'owner_cleanup'
        assert report['normal_admin_shutdown_verified'] is True and report['database_closed_and_reaped'] is True
        assert report['all_command_children_reaped'] is True
        assert source_hashes() == before
        assert sha256(BINARY.read_bytes()).hexdigest() == binary_sha
        out = {'backend': backend, 'version': report['sql_server_version'], 'sources': before,
            'binary_sha256': binary_sha, 'actual': actual,
            'normal_admin_shutdown': True, 'all_processes_reaped': True}
    except Exception as error:
        reason = str(error)
        out = {'backend': backend, 'failed_stage': stage, 'failure_class': type(error).__name__,
            'last_owned_stage': next((x['stage'] for x in reversed(report.get('commands', [])) if x.get('exit_code') != 0), None),
            'server_reaped': report.get('database_closed_and_reaped', False),
            'normal_admin_shutdown': report.get('normal_admin_shutdown_verified', False),
            'all_processes_reaped': report.get('all_command_children_reaped', False)}
        if re.fullmatch('[a-z_]{1,100}', reason):
            out['failure'] = reason
    if hasattr(owner, 'work') and owner.work.exists() and (report.get('database_closed_and_reaped') or owner.harness.server is None):
        work = owner.work.resolve(strict=True)
        assert work.parent == module['db'].RUNS.resolve(strict=True) and work == owner.work and re.fullmatch(
            f'bridge-{backend}-[0-9a-f]{{32}}', work.name)
        assert not owner.harness.owned_children
        shutil.rmtree(work)
        out['owned_directory_removed'] = not work.exists()
    out['elapsed_seconds'] = round(time.monotonic() - start, 2)
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run the bounded native autoload frontier fixture.')
    parser.add_argument('--backend', choices=('mysql', 'mariadb'))
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    results = []
    for backend in (args.backend,) if args.backend else ('mysql', 'mariadb'):
        result = run(backend)
        results.append(result)
        wire = json.dumps(result, indent=2, sort_keys=True) + '\n'
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.record and 'actual' in result and result['owned_directory_removed']:
            directory = HERE / 'evidence'
            directory.mkdir(exist_ok=True)
            with (directory / f'windows-{backend}.json').open('x', encoding='utf-8', newline='\n') as output:
                output.write(wire)
    sys.exit(0 if all('actual' in result and result['owned_directory_removed'] for result in results) else 1)
