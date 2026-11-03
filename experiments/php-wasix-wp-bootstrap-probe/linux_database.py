"""Own a digest-pinned fixture container and schema-scoped WP accounts."""
import importlib.util
from pathlib import Path
import secrets

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('rp_full_bootstrap_linux_owner',
    ROOT / 'experiments/php-wasix-wpdb-probe/linux_database.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
require = base.require
SALT_NAMES = ['AUTH_KEY','SECURE_AUTH_KEY','LOGGED_IN_KEY','NONCE_KEY',
    'AUTH_SALT','SECURE_AUTH_SALT','LOGGED_IN_SALT','NONCE_SALT']
TABLES = ['commentmeta','comments','links','options','postmeta','posts','term_relationships',
    'term_taxonomy','termmeta','terms','usermeta','users']


class Database(base.Database):
    def __init__(self, backend, report):
        super().__init__(backend, report)
        self.setup_secret, self.wp_secret = secrets.token_hex(24), secrets.token_hex(24)
        self.salts = {name:secrets.token_hex(24) for name in SALT_NAMES}
        self.mode_nonces = []

    def start(self):
        super().start()
        require(self.report['owned_database_identity_verified_before_fixture_ddl'] is True,
            'bootstrap_verified_owner_before_installer_account')
        sql = ("CREATE USER 'rp_wp_setup'@'%' IDENTIFIED BY '" + self.setup_secret + "';\n"
            "GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,INDEX,DROP ON rp_wpdb_probe.* "
            "TO 'rp_wp_setup'@'%';\n").encode('ascii')
        self.run('create_scoped_wordpress_installer', self.client(), timeout=15, input_bytes=sql)
        self.report.update(installer_schema_ddl_and_crud=True, reader_schema_crud=True,
            management_credentials_supplied_to_php=False, unused_comparison_fixture_table_present=True)

    def request(self, mode):
        require(mode in ('install','read'), 'closed_bootstrap_mode')
        nonce = secrets.token_hex(24)
        self.mode_nonces.append(nonce)
        value = {'schema':'recastpress.php-wasix-wp-bootstrap-input.v0.5','mode':mode,
            'host':'127.0.0.1','port':self.port,'database':'rp_wpdb_probe',
            'user':'rp_wp_setup' if mode == 'install' else 'rp_wpdb_probe',
            'password':self.setup_secret if mode == 'install' else self.secret,
            'nonce':nonce,'salts':self.salts}
        if mode == 'install': value['admin_password'] = self.wp_secret
        return value

    def private_values(self):
        return [self.secret,self.admin_secret,self.setup_secret,self.wp_secret,*self.salts.values(),*self.mode_nonces]

    def final_rows(self):
        self.inspect()
        result = self.run('independent_wordpress_tables', self.client() + ['--execute=SHOW TABLES FROM rp_wpdb_probe'])
        require(sorted(result.stdout.decode('ascii').strip().splitlines())
            == sorted(['rp_' + name for name in TABLES] + ['rp_wire_fixture']), 'bootstrap_final_tables')
        result = self.run('independent_wordpress_options', self.client() + [
            "--execute=SELECT option_name,HEX(option_value) FROM rp_wpdb_probe.rp_options "
            "WHERE option_name IN ('blogname','home','siteurl') ORDER BY option_name"])
        expected = [('blogname',b'RP Bootstrap Fixture'),('home',b'http://rp-fixture.invalid'),
            ('siteurl',b'http://rp-fixture.invalid')]
        require([line.split('\t') for line in result.stdout.decode('ascii').strip().splitlines()]
            == [[name,value.hex().upper()] for name,value in expected], 'bootstrap_final_options')
        result = self.run('independent_wordpress_admin_count', self.client() + [
            "--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_users WHERE user_login='rp_fixture_admin'"])
        require(result.stdout.strip() == b'1', 'bootstrap_final_admin_count')
        self.report.update(independent_wordpress_final_verified=True,wordpress_table_count=12)
