"""Reuse the owned Linux binlog fixture with a stock WordPress options row."""

from hashlib import sha256
from pathlib import Path
import re


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'php-wasix-binlog-dependency-probe/database.py'
SOURCE_SHA = '47841b6ad47886d7a32f6964fda1baf576a76ed8297077a249d9d1cd32745d82'
OLD = b'rp-autoload-old'
NEW = b'rp-autoload-new'
OPTION = 'rp_autoload_probe_marker'


def load_owner():
    wire = SOURCE.read_bytes()
    marker = b"SCHEMA = 'rp_dependency_probe'"
    if SOURCE.resolve() != SOURCE or sha256(wire).hexdigest() != SOURCE_SHA or wire.count(marker) != 1:
        raise ValueError('autoload_binlog_pinned_owner_changed')
    derived = wire.replace(marker, b"SCHEMA = 'rp_wpdb_probe'")
    namespace = {'__file__': str(SOURCE), '__name__': 'rp_autoload_binlog_owner'}
    exec(compile(derived, str(SOURCE), 'exec'), namespace)
    return namespace


base = load_owner()
require = base['require']


class Database(base['Database']):
    def seed_dependency(self, nonce):
        require(self.monitor_nonce is None and type(nonce) is str and re.fullmatch('[a-f0-9]{48}', nonce) is not None,
            'autoload_binlog_fixture_nonce')
        self.verify_binlog_profile()
        monitor = base['MONITOR']
        status = 'REPLICATION CLIENT' if self.backend == 'mysql' else 'BINLOG MONITOR'
        sql = (
            'USE rp_wpdb_probe;\n'
            f"INSERT INTO rp_options (option_name,option_value,autoload) VALUES ('{OPTION}',X'{OLD.hex()}','yes');\n"
            'SELECT ROW_COUNT();\n'
            'CREATE DATABASE rp_options_guard_fixture;\n'
            'CREATE TABLE `rp_options_guard_fixture`.`identity` (id BIGINT UNSIGNED PRIMARY KEY, '
            'nonce VARBINARY(48) NOT NULL) ENGINE=InnoDB;\n'
            f"INSERT INTO `rp_options_guard_fixture`.`identity` VALUES (1,X'{nonce.encode('ascii').hex()}');\n"
            'SET SESSION sql_log_bin=0;\n'
            f"CREATE USER '{monitor}'@'%' IDENTIFIED BY '{self.monitor_password}';\n"
            f"GRANT SELECT ON `rp_options_guard_fixture`.`identity` TO '{monitor}'@'%';\n"
            f"GRANT SELECT ON rp_wpdb_probe.rp_options TO '{monitor}'@'%';\n"
            f"GRANT REPLICATION SLAVE, {status} ON *.* TO '{monitor}'@'%';\n"
        ).encode('ascii')
        result = self.run('seed_owned_autoload_option', self.client(), timeout=15, input_bytes=sql)
        require(result.stdout == b'1\n' and not result.stderr, 'autoload_binlog_one_seeded_option')
        self.option_id = self.verify_dependency_value('old', OLD)
        self.monitor_nonce = nonce
        self.report['dependency_fixture_seeded'] = True

    def verify_dependency_value(self, stage, expected):
        result = self.run('independent_autoload_marker_' + stage, [*self.client(),
            f"--execute=SELECT option_id,HEX(option_value),autoload FROM rp_wpdb_probe.rp_options WHERE option_name='{OPTION}'"])
        line = result.stdout.removesuffix(b'\n')
        fields = line.split(b'\t')
        require(not result.stderr and result.stdout == line + b'\n' and len(fields) == 3
            and re.fullmatch(rb'[1-9][0-9]{0,19}', fields[0]) is not None and int(fields[0]) < 2**64
            and fields[1:] == [expected.hex().upper().encode('ascii'), b'yes'], 'autoload_binlog_independent_option')
        option_id = int(fields[0])
        require(option_id == getattr(self, 'option_id', option_id), 'autoload_binlog_option_id_changed')
        return option_id

    def commit_dependency(self):
        require(self.monitor_nonce is not None and not self.dependency_committed, 'autoload_binlog_commit_phase')
        self.inspect()
        sql = (
            'USE rp_wpdb_probe;\nSTART TRANSACTION;\n'
            f"UPDATE rp_options SET option_value=X'{NEW.hex()}' WHERE option_id={self.option_id} "
            f"AND option_name='{OPTION}' AND autoload='yes' AND option_value=X'{OLD.hex()}';\n"
            'SELECT ROW_COUNT();\nCOMMIT;\n'
        ).encode('ascii')
        result = self.run('commit_owned_autoload_option', self.client(), timeout=15, input_bytes=sql)
        require(result.stdout == b'1\n' and not result.stderr, 'autoload_binlog_one_committed_option')
        self.dependency_committed = True
        self.verify_dependency_value('new', NEW)
        self.report['dependency_update_committed_and_visible'] = True
