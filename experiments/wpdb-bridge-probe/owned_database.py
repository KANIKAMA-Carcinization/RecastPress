"""Reuse the fixed DB/tool/process helpers for a fresh bridge-only fixture.

The previous wire experiment and its receipts remain unchanged. This module
owns only a new UUID run; it never finds, attaches to or stops an existing DB.
"""
import importlib.util
import os
from pathlib import Path
import secrets
import socket
import subprocess
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


db = load('rp_bridge_existing_owned_db', ROOT / 'experiments/wpdb-rpc-probe/database_check.py')
require = db.require


class OwnedDatabase:
    def __init__(self, backend, report):
        require(backend in db.PINS and os.name == 'nt', 'fixed_windows_backend')
        self.backend, self.report = backend, report
        self.admin_password, self.password = secrets.token_hex(24), secrets.token_hex(24)
        self.harness = None
        self.server_config = self.admin = self.tool = None

    def __enter__(self):
        try:
            self.start()
            return self
        except BaseException:
            self.close()
            raise

    def start(self):
        r = self.report
        r.update(commands=[], database_started=False, database_closed_and_reaped=False,
            database_identity_verified_before_fixture_ddl=False, all_command_children_reaped=False,
            controller_account_bootstrapped_by_owned_server=False, admin_transport='verified_tls',
            separate_admin_and_limited_credentials=True, production_database_used=False)
        db.RUNS.mkdir(parents=True, exist_ok=True)
        work = db.owned(db.RUNS / ('bridge-' + self.backend + '-' + uuid.uuid4().hex))
        work.mkdir()
        self.work = work
        r.update(run=work.relative_to(ROOT).as_posix(), run_retained=True)
        h = self.harness = db.Harness(work, r)
        tool, r['database_tool'] = db.verify_tool(self.backend, h)
        self.tool = tool
        ca, cert, key = db.certificates(h)
        self.ca = ca
        r['tls_fixture'] = {'ca_sha256': db.digest(ca), 'server_cert_sha256': db.digest(cert),
            'admin_chain_and_ip_verification_enabled': True, 'bridge_db_transport': 'disabled'}
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            self.port = reservation.getsockname()[1]
        pin = db.PINS[self.backend]
        server_exe = tool / 'bin' / pin['server']
        initialize = ([str(server_exe), '--no-defaults', '--initialize-insecure', '--basedir=' + str(tool),
            '--datadir=' + str(work / 'data'), '--console'] if self.backend == 'mysql' else
            [str(tool / 'bin/mariadb-install-db.exe'), '--datadir=' + str(work / 'data'), '--silent'])
        h.checked('initialize_bridge_database', initialize, timeout=90, cwd=tool, limit=4 * 1024 * 1024)
        controller = db.write_private(work / 'controller-private.sql', (
            "CREATE USER 'rp_probe_admin'@'127.0.0.1' IDENTIFIED BY '" + self.admin_password + "' REQUIRE SSL;\n"
            "GRANT ALL PRIVILEGES ON rp_wpdb_probe.* TO 'rp_probe_admin'@'127.0.0.1' WITH GRANT OPTION;\n"
            "GRANT CREATE USER,SHUTDOWN ON *.* TO 'rp_probe_admin'@'127.0.0.1';\n").encode('ascii'))
        text = ('[mysqld]\nbasedir=' + tool.as_posix() + '\ndatadir=' + (work / 'data').as_posix()
            + '\npid-file=' + (work / 'server.pid').as_posix() + '\nbind-address=127.0.0.1\nport=' + str(self.port)
            + '\nskip-name-resolve\nmax-connections=8\ninnodb-buffer-pool-size=64M\nrequire-secure-transport=OFF\n'
            + 'ssl-ca=' + ca.as_posix() + '\nssl-cert=' + cert.as_posix() + '\nssl-key=' + key.as_posix()
            + '\ninit-file=' + controller.as_posix() + '\n')
        text += ('mysqlx=0\nperformance-schema=OFF\ninnodb-redo-log-capacity=32M\nskip-log-bin\n'
            if self.backend == 'mysql' else 'innodb-log-file-size=32M\n')
        self.server_config = db.write_private(work / 'server.ini', text.encode('utf-8'))
        self.config_sha = db.digest(self.server_config)
        self.admin_config = db.client_config(h, 'admin-private.ini', 'rp_probe_admin', self.admin_password,
            self.port, self.backend, 'required', ca)
        limited_config = db.client_config(h, 'limited-private.ini', 'rp_wpdb_probe', self.password,
            self.port, self.backend, 'disabled', ca)
        command = [str(server_exe), '--defaults-file=' + str(self.server_config)]
        if self.backend == 'mysql': command.append('--no-monitor')
        command.append('--console')
        h.log = db.owned(work / 'server-private.log').open('xb')
        h.server = subprocess.Popen(command, cwd=work, env=db.environment(), stdin=subprocess.DEVNULL,
            stdout=h.log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        h.remember(h.server)
        r.update(database_started=True, database_pid=h.server.pid)
        self.admin = db.client_command(tool, self.backend, self.admin_config)
        version = db.identity_ready(h, self.admin, self.port, pin['version'], self.backend, wait=True)
        r.update(sql_server_version=version, database_identity_verified_before_fixture_ddl=True,
            controller_account_bootstrapped_by_owned_server=True)
        sql = ("CREATE USER 'rp_wpdb_probe'@'127.0.0.1' IDENTIFIED BY '" + self.password + "' REQUIRE NONE;\n").encode('ascii')
        sql += db.DDL + b"GRANT SELECT,INSERT,UPDATE,DELETE ON rp_wpdb_probe.* TO 'rp_wpdb_probe'@'127.0.0.1';\n"
        h.checked('create_bridge_fixture', self.admin, timeout=30, input_bytes=sql)
        limited = db.client_command(tool, self.backend, limited_config)
        result = h.checked('limited_empty_fixture', [*limited,
            '--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_wire_fixture'], timeout=5, limit=65536)
        db.equal(result.stdout.strip(), b'0', 'bridge_fixture_not_empty')
        r['fixture_empty_before_probe'] = True

    def request(self, schema):
        return {'schema': schema, 'host': '127.0.0.1', 'port': self.port, 'database': 'rp_wpdb_probe',
            'user': 'rp_wpdb_probe', 'password': self.password, 'tls': 'disabled', 'tls_ca_pem': None}

    def verify_final(self):
        db.identity_ready(self.harness, self.admin, self.port, db.PINS[self.backend]['version'], self.backend)
        result = self.harness.checked('final_bridge_rows', [*self.admin,
            '--execute=SELECT HEX(payload),COUNT(*) FROM rp_wpdb_probe.rp_wire_fixture GROUP BY payload'], timeout=5, limit=65536)
        db.equal(result.stdout.strip(), b'00FF42\t1', 'bridge_final_database_rows')
        db.equal(db.digest(self.server_config), self.config_sha, 'bridge_server_config_changed')
        self.report['independent_final_database_rows_verified'] = True

    def close(self):
        h = self.harness
        if h is None: return
        normal = False
        try:
            if h.server is not None:
                if h.server.poll() is None and self.report.get('controller_account_bootstrapped_by_owned_server'):
                    try:
                        h.checked('admin_bridge_shutdown', [*db.client_command(self.tool, self.backend,
                            self.admin_config, admin=True), 'shutdown'], timeout=10, limit=65536)
                        normal = True
                    except Exception:
                        self.report['normal_shutdown_failed'] = True
                result = h.cleanup(h.server, normal_wait=10 if normal else 0)
                self.report['database_cleanup'] = result
                self.report['database_closed_and_reaped'] = True
                self.report['normal_admin_shutdown_verified'] = normal and not result['forced_tree_cleanup'] and result['exit_code'] == 0
        finally:
            try:
                h.reap_remaining()
                self.report['all_command_children_reaped'] = all(r['owned_reaped'] is True for r in self.report['commands'])
            finally:
                if h.log is not None: h.log.close()

    def __exit__(self, exception_type, exception, traceback):
        self.close()
        return False
