"""Reuse the fixed owned CRUD container; salts remain in the Rust host only."""
import importlib.util
from pathlib import Path
import secrets

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('rp_utf8_fixed_linux_owner',
    ROOT / 'experiments/php-wasix-wpdb-probe/linux_database.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
# Isolate the reused module instance's scratch root, without changing its source.
base.WORK = ROOT / 'target/php-wasix-wpdb-utf8-probe/database-runs'
require = base.require
SALT_NAMES = ['AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY',
    'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT']


class Database(base.Database):
    def __init__(self, backend, report):
        super().__init__(backend, report, corpus='crud')
        self.salts = {name: secrets.token_hex(24) for name in SALT_NAMES}

    def start(self):
        super().start()
        result = self.run('verify_new_utf8_fixture_empty', self.client() + [
            '--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_crud_fixture'])
        require(result.stdout.strip() == b'0', 'utf8_fixture_not_empty_before')
        self.report['utf8_fixture_empty_before'] = True

    def input(self):
        return {'schema': 'recastpress.php-wasix-wp-bootstrap-input.v0.5', 'mode': 'read',
            'host': '127.0.0.1', 'port': self.port, 'database': 'rp_wpdb_probe',
            'user': 'rp_wpdb_probe', 'password': self.secret, 'nonce': self.nonce, 'salts': self.salts}

    def private_values(self):
        return [self.secret, self.admin_secret, self.nonce, *self.salts.values()]

    def final_rows(self):
        super().final_rows()
        result = self.run('verify_unused_wire_fixture_empty', self.client() + [
            '--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_wire_fixture'])
        require(result.stdout.strip() == b'0', 'utf8_unused_wire_fixture_changed')
        self.report['unused_wire_fixture_empty_after'] = True
