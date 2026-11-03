"""Use only a fresh, identity-checked fixture DB for native WP installation."""
import importlib.util
from pathlib import Path
import secrets

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('rp_bootstrap_original_db',
    ROOT / 'experiments/wpdb-bridge-probe/owned_database.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
db = base.db
TABLES = sorted(['posts','comments','links','options','postmeta','terms','term_taxonomy',
    'term_relationships','termmeta','commentmeta','users','usermeta'])
SALT_NAMES = ['AUTH_KEY','SECURE_AUTH_KEY','LOGGED_IN_KEY','NONCE_KEY',
    'AUTH_SALT','SECURE_AUTH_SALT','LOGGED_IN_SALT','NONCE_SALT']


class OwnedDatabase(base.OwnedDatabase):
    def __init__(self, backend, report):
        super().__init__(backend, report)
        self.setup_password, self.wp_password = secrets.token_hex(24), secrets.token_hex(24)
        self.salts = {name:secrets.token_hex(24) for name in SALT_NAMES}

    def start(self):
        super().start()
        db.require(self.report['database_identity_verified_before_fixture_ddl'] is True,
            'bootstrap_identity_before_installer_account')
        sql = ("CREATE USER 'rp_wp_setup'@'127.0.0.1' IDENTIFIED BY '" + self.setup_password + "' REQUIRE NONE;\n"
            "GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,INDEX,DROP ON rp_wpdb_probe.* "
            "TO 'rp_wp_setup'@'127.0.0.1';\n").encode('ascii')
        self.harness.checked('create_scoped_wordpress_installer', self.admin, timeout=10, input_bytes=sql)
        self.report['installer_scoped_schema_ddl_and_crud'] = True
        self.report['reader_scoped_schema_crud'] = True
        self.report['management_credentials_supplied_to_php'] = False
        self.report['unused_comparison_fixture_table_present'] = True

    def request(self, mode):
        db.require(mode in ('install','read'), 'fixed_bootstrap_mode')
        value = {'schema':'recastpress.wp-bootstrap-native-input.v0.5', 'mode':mode,
            'host':'127.0.0.1', 'port':self.port, 'database':'rp_wpdb_probe',
            'user':'rp_wp_setup' if mode == 'install' else 'rp_wpdb_probe',
            'password':self.setup_password if mode == 'install' else self.password, 'salts':self.salts}
        if mode == 'install': value['admin_password'] = self.wp_password
        return value

    def secrets(self):
        return [self.admin_password,self.password,self.setup_password,self.wp_password,*self.salts.values()]

    def verify_final(self):
        db.identity_ready(self.harness, self.admin, self.port, db.PINS[self.backend]['version'], self.backend)
        result = self.harness.checked('independent_wordpress_tables', [*self.admin,
            '--execute=SHOW TABLES FROM rp_wpdb_probe'], timeout=5, limit=65536)
        expected = sorted(['rp_' + name for name in TABLES] + ['rp_wire_fixture'])
        db.equal(sorted(result.stdout.decode('ascii').strip().splitlines()), expected, 'bootstrap_final_tables')
        result = self.harness.checked('independent_wordpress_identity_options', [*self.admin,
            "--execute=SELECT option_name,HEX(option_value) FROM rp_wpdb_probe.rp_options "
            "WHERE option_name IN ('blogname','siteurl','home') ORDER BY option_name"], timeout=5, limit=65536)
        expected = [('blogname',b'RP Bootstrap Fixture'),('home',b'http://rp-fixture.invalid'),
            ('siteurl',b'http://rp-fixture.invalid')]
        actual = [row.split('\t') for row in result.stdout.decode('ascii').strip().splitlines()]
        db.equal(actual, [[name,value.hex().upper()] for name,value in expected], 'bootstrap_final_options')
        result = self.harness.checked('independent_wordpress_admin_count', [*self.admin,
            "--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_users WHERE user_login='rp_fixture_admin'"],
            timeout=5, limit=65536)
        db.equal(result.stdout.strip(), b'1', 'bootstrap_final_admin_count')
        db.equal(db.digest(self.server_config), self.config_sha, 'bootstrap_server_config_changed')
        self.report['independent_wordpress_final_verified'] = True
        self.report['wordpress_table_count'] = 12
