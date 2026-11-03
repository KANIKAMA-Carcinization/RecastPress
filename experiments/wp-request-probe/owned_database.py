"""Fresh fixture ownership, using the frozen full-bootstrap DB/process harness."""
import importlib.util
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('rp_home_original_bootstrap_owner',
    ROOT / 'experiments/wp-bootstrap-probe/owned_database.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
db = base.db
STAMP = '2026-01-01 00:00:00'
AUTOLOAD = "autoload IN ('yes','on','auto-on','auto')"
SEED = ("UPDATE rp_wpdb_probe.rp_posts SET post_date='" + STAMP + "',post_date_gmt='" + STAMP
    + "',post_modified='" + STAMP + "',post_modified_gmt='" + STAMP
    + "' WHERE ID IN (1,2) AND post_status='publish' AND post_type IN ('post','page');\n"
    "UPDATE rp_wpdb_probe.rp_comments SET comment_date='" + STAMP + "',comment_date_gmt='" + STAMP
    + "' WHERE comment_ID=1 AND comment_post_ID=1;\n").encode('ascii')


class OwnedDatabase(base.OwnedDatabase):
    def __init__(self, backend, report):
        super().__init__(backend, report)
        self._fully_closed = False
        self._private_admin_hash = None

    def request(self, mode):
        if mode != 'home':
            return super().request(mode)
        value = super().request('read')
        value.update(schema='recastpress.wp-request-native-input.v0.5', mode='home')
        return value

    def secrets(self):
        values = super().secrets()
        if self._private_admin_hash is not None:
            values.append(self._private_admin_hash)
        return values

    def _sql(self, stage, sql, limit=65536):
        db.require(type(sql) is bytes, 'home_owned_sql_bytes')
        result = self.harness.checked(stage, self.admin, timeout=5, input_bytes=sql, limit=limit)
        db.require(not result.stderr, 'home_owned_sql_stderr')
        return result.stdout

    def capture_private_password_guard(self):
        wire = self._sql('private_installed_admin_hash_guard',
            b"SELECT user_pass FROM rp_wpdb_probe.rp_users WHERE user_login='rp_fixture_admin';\n", 4096)
        value = wire.rstrip(b'\r\n')
        db.require(32 <= len(value) <= 255 and all(33 <= b <= 126 for b in value),
            'home_private_admin_hash_shape')
        self._private_admin_hash = value.decode('ascii')
        self.report['stored_admin_password_hash_guarded_privately'] = True

    def autoload_summary(self, phase):
        db.require(phase in ('before_home', 'after_home'), 'home_autoload_phase')
        wire = self._sql('independent_autoload_summary_' + phase,
            ("SELECT COUNT(*),COALESCE(MAX(OCTET_LENGTH(option_value)),0) FROM rp_wpdb_probe.rp_options WHERE "
                + AUTOLOAD + ";\n").encode('ascii'))
        fields = wire.strip().split(b'\t')
        db.require(len(fields) == 2 and all(re.fullmatch(rb'[0-9]{1,12}', f) for f in fields),
            'home_autoload_summary_shape')
        count, maximum = map(int, fields)
        db.require(count > 0 and maximum > 0, 'home_autoload_summary_empty')
        wire = self._sql('independent_largest_autoload_name_' + phase,
            ("SELECT option_name,OCTET_LENGTH(option_value) FROM rp_wpdb_probe.rp_options WHERE "
                + AUTOLOAD + " ORDER BY OCTET_LENGTH(option_value) DESC,option_name ASC LIMIT 1;\n").encode('ascii'))
        fields = wire.strip().split(b'\t')
        db.require(len(fields) == 2 and re.fullmatch(rb'[A-Za-z0-9_.:-]{1,191}', fields[0])
            and re.fullmatch(rb'[0-9]{1,12}', fields[1]), 'home_largest_autoload_name_shape')
        db.require(int(fields[1]) == maximum, 'home_largest_autoload_length')
        result = {'option_count':count, 'maximum_value_bytes':maximum,
            'largest_option_name':fields[0].decode('ascii'), 'option_values_published':False}
        self.report.setdefault('independent_autoload', {})[phase] = result
        return result

    def seed_public_dates(self):
        db.identity_ready(self.harness, self.admin, self.port, db.PINS[self.backend]['version'], self.backend)
        wire = self._sql('seed_fixed_public_dates', SEED)
        db.require(not wire.strip(), 'home_seed_unexpected_output')
        self.report['fixed_public_date_seed'] = {'timestamp':STAMP, 'post_fields':
            ['post_date','post_date_gmt','post_modified','post_modified_gmt'],
            'comment_fields':['comment_date','comment_date_gmt'], 'sql_sha256':db.sha(SEED),
            'actual_database_updated':True, 'response_body_normalized':False}
        self.verify_public_dates('before_home')

    def verify_public_dates(self, phase):
        db.require(phase in ('before_home', 'after_home'), 'home_fixed_date_phase')
        wire = self._sql('independent_public_post_dates_' + phase,
            b"SELECT ID,post_date,post_date_gmt,post_modified,post_modified_gmt FROM rp_wpdb_probe.rp_posts "
            b"WHERE ID IN (1,2) AND post_status='publish' AND post_type IN ('post','page') ORDER BY ID;\n")
        actual = [line.split('\t') for line in wire.decode('ascii').strip().splitlines()]
        db.equal(actual, [[str(i), *([STAMP] * 4)] for i in (1,2)], 'home_actual_fixed_post_dates')
        wire = self._sql('independent_public_comment_dates_' + phase,
            b"SELECT comment_ID,comment_post_ID,comment_date,comment_date_gmt FROM rp_wpdb_probe.rp_comments "
            b"WHERE comment_ID=1 AND comment_post_ID=1;\n")
        actual_comments = [line.split('\t') for line in wire.decode('ascii').strip().splitlines()]
        db.equal(actual_comments, [['1','1',STAMP,STAMP]], 'home_actual_fixed_comment_dates')
        self.report.setdefault('fixed_public_date_verification', {})[phase] = {
            'post_ids':[1,2], 'post_rows':len(actual), 'comment_ids':[1], 'comment_rows':len(actual_comments),
            'timestamp':STAMP, 'verified':True}

    def verify_final(self):
        super().verify_final()
        self.verify_public_dates('after_home')

    def close(self):
        if self._fully_closed:
            return
        try:
            super().close()
        finally:
            h = self.harness
            self._fully_closed = bool(h is not None and not h.owned_children
                and (h.server is None or h.server.poll() is not None))
