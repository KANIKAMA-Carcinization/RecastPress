"""Derive an owned Linux WordPress DB with one logged dependency schema."""

from hashlib import sha256
import importlib.util
from pathlib import Path
import re
import secrets


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXPERIMENTS = ROOT / 'experiments'
OWNER_PINS = {
    'php-wasix-wpdb-probe': '6d0d4f6e65edb8ae5029e9bd900ae5996795a80fc4996e52eb3cb2ce662c5ddd',
    'php-wasix-wp-bootstrap-probe': 'c8d01c4b29ed7769e6d5624c9d0835db0bcfc2032cfe89cc05782d9d8092fbae',
    'php-wasix-wp-request-probe': 'c2701e151e53637654618a346f8d903b5c1af65d28d9d80cf9321e356be1a577',
    'php-wasix-wp-request-context-probe': 'f593c8b7e4c5ab65942b44169414b70543c03f0f432b2e549f6e4ef4429d2b3c',
}
SCHEMA = 'rp_dependency_probe'
MONITOR = 'rp_dependency_monitor'
OLD = b'recastpress-shared-fixture'
NEW = b'recastpress-shared-updated'
LOG_ARGS = (
    '--server-id=7', '--log-bin=/var/lib/mysql/probe-bin', '--binlog-format=ROW',
    '--binlog-row-image=FULL', '--binlog-checksum=CRC32', f'--binlog-do-db={SCHEMA}',
)


def load_owner():
    for name, expected in OWNER_PINS.items():
        path = EXPERIMENTS / name / 'linux_database.py'
        if (path.resolve(strict=True) != path or not path.is_file() or path.stat().st_size > 64 * 1024
                or sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError('binlog_dependency_owned_database_source_changed')
    path = EXPERIMENTS / 'php-wasix-wp-request-context-probe/linux_database.py'
    spec = importlib.util.spec_from_file_location('rp_binlog_dependency_context_owner', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_owner()
require = base.require


def startup_args(command, backend):
    """Change only the pinned owner's concrete owned Docker start command."""
    require(command[:3] == ['docker', 'run', '--detach'], 'binlog_dependency_owned_start_command')
    if backend == 'mysql':
        suffix = ['--mysqlx=0', '--performance-schema=OFF', '--innodb-redo-log-capacity=32M', '--skip-log-bin']
        require(command[-len(suffix):] == suffix and command.count('--skip-log-bin') == 1,
            'binlog_dependency_mysql_start_profile')
        return command[:-1] + [*LOG_ARGS, '--gtid-mode=OFF', '--binlog-transaction-compression=OFF']
    require(backend == 'mariadb' and command[-1:] == ['--innodb-log-file-size=32M']
        and '--skip-log-bin' not in command, 'binlog_dependency_mariadb_start_profile')
    return command + list(LOG_ARGS)


class Database(base.Database):
    def __init__(self, backend, report):
        self.monitor_password = secrets.token_hex(24)
        self.monitor_nonce = None
        self.dependency_committed = False
        super().__init__(backend, report)

    def run(self, stage, command, *, timeout=10, input_bytes=None, check=True):
        if stage == 'start_new_owned_container':
            command = startup_args(command, self.backend)
        return super().run(stage, command, timeout=timeout, input_bytes=input_bytes, check=check)

    def verify_binlog_profile(self):
        self.inspect()
        profile = ('SELECT @@GLOBAL.log_bin,@@GLOBAL.binlog_format,@@GLOBAL.binlog_row_image,'
            '@@GLOBAL.binlog_checksum,@@GLOBAL.server_id')
        expected = b'1\tROW\tFULL\tCRC32\t7'
        if self.backend == 'mysql':
            profile += ',@@GLOBAL.gtid_mode,CAST(@@GLOBAL.binlog_transaction_compression AS UNSIGNED)'
            expected += b'\tOFF\t0'
        result = self.run('verify_dependency_binlog_globals', [*self.client(), '--execute=' + profile])
        require(result.stdout == expected + b'\n' and not result.stderr, 'binlog_dependency_globals')
        statement = 'SHOW BINARY LOG STATUS' if self.backend == 'mysql' else 'SHOW MASTER STATUS'
        result = self.run('verify_dependency_binlog_filter', [*self.client(), '--execute=' + statement])
        line = result.stdout.removesuffix(b'\n')
        fields = line.split(b'\t')
        tail = [SCHEMA.encode('ascii'), b'']
        if self.backend == 'mysql':
            tail.append(b'')
        # Image initialization can start and stop an earlier temporary server.
        require(not result.stderr and result.stdout == line + b'\n' and b'\n' not in line
            and fields[2:] == tail and re.fullmatch(rb'probe-bin\.[0-9]{6}', fields[0]) is not None
            and fields[0] != b'probe-bin.000000' and fields[1].isdigit()
            and len(fields[1]) <= 8 and 4 < int(fields[1]) <= 64 * 1024 * 1024, 'binlog_dependency_filter')
        self.report['dependency_binlog_profile_verified'] = True

    def seed_dependency(self, nonce):
        require(self.monitor_nonce is None and type(nonce) is str and re.fullmatch('[a-f0-9]{48}', nonce) is not None,
            'binlog_dependency_fixture_nonce')
        self.verify_binlog_profile()
        status_privilege = 'REPLICATION CLIENT' if self.backend == 'mysql' else 'BINLOG MONITOR'
        sql = (
            f'CREATE DATABASE {SCHEMA};\nUSE {SCHEMA};\n'
            'CREATE TABLE rp_dependency_fixture (id BIGINT UNSIGNED PRIMARY KEY, value VARBINARY(64) NOT NULL) ENGINE=InnoDB;\n'
            'CREATE TABLE rp_dependency_identity (id BIGINT UNSIGNED PRIMARY KEY, nonce VARBINARY(48) NOT NULL) ENGINE=InnoDB;\n'
            f"INSERT INTO rp_dependency_fixture VALUES (1,X'{OLD.hex()}');\n"
            f"INSERT INTO rp_dependency_identity VALUES (1,X'{nonce.encode('ascii').hex()}');\n"
            'SET SESSION sql_log_bin=0;\n'
            f"CREATE USER '{MONITOR}'@'%' IDENTIFIED BY '{self.monitor_password}';\n"
            f"GRANT SELECT ON {SCHEMA}.rp_dependency_fixture TO 'rp_wpdb_probe'@'%';\n"
            f"GRANT SELECT ON {SCHEMA}.rp_dependency_fixture TO '{MONITOR}'@'%';\n"
            f"GRANT SELECT ON {SCHEMA}.rp_dependency_identity TO '{MONITOR}'@'%';\n"
            f"GRANT REPLICATION SLAVE, {status_privilege} ON *.* TO '{MONITOR}'@'%';\n"
        ).encode('ascii')
        result = self.run('seed_binlog_dependency', self.client(), timeout=15, input_bytes=sql)
        require(result.stdout == result.stderr == b'', 'binlog_dependency_fixture_seed')
        self.verify_dependency_value('old', OLD)
        self.monitor_nonce = nonce
        self.report['dependency_fixture_seeded'] = True

    def monitor_config(self):
        require(self.monitor_nonce is not None, 'binlog_dependency_fixture_not_seeded')
        return {'port': self.port, 'password': self.monitor_password, 'nonce': self.monitor_nonce}

    def verify_dependency_value(self, stage, expected):
        result = self.run('independent_dependency_' + stage, [*self.client(),
            f'--execute=SELECT HEX(value) FROM {SCHEMA}.rp_dependency_fixture WHERE id=1'])
        require(result.stdout == expected.hex().upper().encode('ascii') + b'\n' and not result.stderr,
            'binlog_dependency_independent_value')

    def commit_dependency(self):
        require(self.monitor_nonce is not None and not self.dependency_committed,
            'binlog_dependency_commit_phase')
        self.inspect()
        sql = (
            f'USE {SCHEMA};\nSTART TRANSACTION;\n'
            f"UPDATE rp_dependency_fixture SET value=X'{NEW.hex()}' WHERE id=1 AND value=X'{OLD.hex()}';\n"
            'SELECT ROW_COUNT();\nCOMMIT;\n'
        ).encode('ascii')
        result = self.run('commit_owned_dependency_change', self.client(), timeout=15, input_bytes=sql)
        require(result.stdout == b'1\n' and not result.stderr, 'binlog_dependency_one_committed_update')
        self.dependency_committed = True
        self.verify_dependency_value('new', NEW)
        self.report['dependency_update_committed_and_visible'] = True

    def private_values(self):
        return [*super().private_values(), self.monitor_password,
            *([self.monitor_nonce] if self.monitor_nonce is not None else [])]
