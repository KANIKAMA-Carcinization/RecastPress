"""Own a fresh fixed Windows database; add only a closed CRUD fixture schema."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('rp_crud_original_owned_db',
    ROOT / 'experiments/wpdb-bridge-probe/owned_database.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
db = base.db

DDL = b'''CREATE TABLE rp_wpdb_probe.rp_crud_fixture(
id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
label VARCHAR(32) NOT NULL UNIQUE,
note VARCHAR(64) NULL,
count_value INT NOT NULL DEFAULT 0,
ratio DECIMAL(10,3) NULL,
payload VARBINARY(64) NULL
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
'''


class OwnedDatabase(base.OwnedDatabase):
    def start(self):
        super().start()
        db.require(self.report['database_identity_verified_before_fixture_ddl'] is True,
            'crud_identity_before_schema')
        self.harness.checked('create_fixed_crud_fixture', self.admin, timeout=10, input_bytes=DDL)
        result = self.harness.checked('empty_fixed_crud_fixture', [*self.admin,
            '--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_crud_fixture'], timeout=5, limit=65536)
        db.equal(result.stdout.strip(), b'0', 'crud_fixture_not_empty')
        self.report['crud_fixture_empty_before_probe'] = True
        self.report['crud_schema_ddl_sha256'] = db.sha(DDL)

    def verify_final(self):
        db.identity_ready(self.harness, self.admin, self.port, db.PINS[self.backend]['version'], self.backend)
        result = self.harness.checked('independent_final_crud_rows', [*self.admin,
            '--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_crud_fixture'], timeout=5, limit=65536)
        db.equal(result.stdout.strip(), b'0', 'crud_final_rows_not_empty')
        db.equal(db.digest(self.server_config), self.config_sha, 'crud_server_config_changed')
        self.report['independent_final_database_rows_verified'] = True
        self.report['final_crud_row_count'] = 0
