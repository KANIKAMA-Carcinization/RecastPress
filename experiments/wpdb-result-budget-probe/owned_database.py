"""Seed a finite synthetic result in a newly owned, identity-checked DB."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('rp_result_budget_existing_owned_db',
    ROOT / 'experiments/wpdb-bridge-probe/owned_database.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
db = base.db
DDL = b'''CREATE TABLE rp_wpdb_probe.rp_result_fixture(
id INT PRIMARY KEY,payload LONGBLOB NULL,label VARBINARY(32) NOT NULL
) ENGINE=InnoDB;
'''


def expected_rows(count=115):
    rows = []
    for index in range(1, count+1):
        payload = b'\x00\xffA' * 7926 if index == 1 else None if index == 2 else b'' if index == 3 else b'B' * 128
        rows.append([str(index).encode().hex(), None if payload is None else payload.hex(), ('row-' + str(index)).encode().hex()])
    return rows


class OwnedDatabase(base.OwnedDatabase):
    def start(self):
        super().start()
        db.require(self.report['database_identity_verified_before_fixture_ddl'] is True,
            'result_budget_identity_before_schema')
        values = []
        for index in range(1, 258):
            payload = "REPEAT(X'00ff41',7926)" if index == 1 else 'NULL' if index == 2 else "X''" if index == 3 else "REPEAT(X'42',128)"
            values.append('(' + str(index) + ',' + payload + ",X'" + ('row-' + str(index)).encode().hex() + "')")
        sql = DDL + ('INSERT INTO rp_wpdb_probe.rp_result_fixture(id,payload,label) VALUES' + ','.join(values) + ';\n').encode()
        self.harness.checked('create_finite_result_budget_fixture', self.admin, timeout=10, input_bytes=sql)
        self.report['fixed_fixture_sql_sha256'] = db.sha(sql)
        result = self.harness.checked('native_result_budget_values', [*self.admin,
            "--execute=SELECT id,IF(payload IS NULL,'NULL',CONCAT('hex:',HEX(payload))),HEX(label) "
            "FROM rp_wpdb_probe.rp_result_fixture WHERE id<=115 ORDER BY id"], timeout=10, limit=256*1024)
        rows = []
        for line in result.stdout.decode('ascii').strip().splitlines():
            index, payload, label = line.split('\t')
            db.require(payload == 'NULL' or payload.startswith('hex:'), 'native_result_budget_payload_marker')
            rows.append([index.encode().hex(), None if payload == 'NULL' else payload[4:].lower(), label.lower()])
        db.equal(rows, expected_rows(), 'native_result_budget_all_values')
        self.report['independent_native_all_values_verified'] = True
        self.report['expected_positive_rows'] = 115
        self.report['expected_positive_raw_cell_bytes'] = 39048

    def verify_final(self):
        db.identity_ready(self.harness, self.admin, self.port, db.PINS[self.backend]['version'], self.backend)
        result = self.harness.checked('result_budget_unchanged_final_rows', [*self.admin,
            '--execute=SELECT COUNT(*),SUM(OCTET_LENGTH(payload)),MAX(OCTET_LENGTH(payload)) '
            'FROM rp_wpdb_probe.rp_result_fixture'], timeout=5, limit=65536)
        db.equal(result.stdout.strip(), b'257\t56290\t23778', 'result_budget_final_fixture_changed')
        db.equal(db.digest(self.server_config), self.config_sha, 'result_budget_server_config_changed')
        self.report['independent_final_database_rows_verified'] = True
