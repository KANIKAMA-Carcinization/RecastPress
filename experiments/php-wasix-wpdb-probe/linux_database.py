"""A digest-pinned, disposable Linux container owned only by this probe.

Only this new labelled container ID may be inspected, stopped or removed. The
guest never receives its credentials or network. No local DB is discovered.
"""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/php-wasix-wpdb-probe/database-runs'
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe


def require(value, reason):
    if not value: raise ValueError(reason)


def owned(path):
    require(path.is_absolute() and path.is_relative_to(WORK) and path.resolve() == path, 'owned_container_path')
    return path


class Database:
    def __init__(self, backend, report):
        require(sys.platform == 'linux' and backend in ('mysql', 'mariadb'), 'linux_owned_container_only')
        self.backend, self.report = backend, report
        self.secret, self.admin_secret, self.nonce = [secrets.token_hex(24) for _ in range(3)]
        self.owner = uuid.uuid4().hex
        self.cid = None
        self.name = 'rp-wpdb-' + backend + '-' + self.owner
        pins = json.loads((HERE / 'container-images.json').read_bytes())
        require(pins['schema'] == 'recastpress.php-wasix-wpdb-images.v0.5', 'container_pin_schema')
        self.pin = pins['images'][backend]
        require(self.pin['platform'] == 'linux/amd64' and self.pin['repository'] == 'docker.io/library/' + backend,
            'container_closed_image')
        self.image = self.pin['repository'] + '@' + self.pin['platform_digest']
        WORK.mkdir(parents=True, exist_ok=True)
        self.work = owned(WORK / self.name)
        self.work.mkdir()
        self.environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(('MYSQL_', 'MARIADB_', 'RP_'))
            and k.upper() not in {'GH_TOKEN', 'GITHUB_TOKEN', 'ACTIONS_RUNTIME_TOKEN', 'DOCKER_HOST', 'DOCKER_CONTEXT'}}
        self.environment['DOCKER_CONFIG'] = str(owned(self.work / 'docker-config'))
        Path(self.environment['DOCKER_CONFIG']).mkdir()
        self.report.update(container_started=False, container_stopped=False, container_removed=False,
            container_normal_exit_verified=False, commands=[],
            run=self.work.relative_to(ROOT).as_posix(), run_retained=True,
            production_database_used=False, publisher_signature_verified=False)

    def private(self, name, wire):
        path = owned(self.work / name)
        with path.open('xb') as stream: stream.write(wire)
        path.chmod(0o600)
        return path

    def run(self, stage, command, *, timeout=10, input_bytes=None, check=True):
        import hashlib
        pid, result = owned_probe.run_captured_probe(command, self.environment, timeout=timeout, input_bytes=input_bytes)
        require(len(result.stdout) <= 2 * 1024 * 1024 and len(result.stderr) <= 65536, 'container_command_capture_quota')
        self.report['commands'].append({'stage': stage, 'owned_pid': pid, 'owned_reaped': True,
            'exit_code': result.returncode, 'stdout_sha256': hashlib.sha256(result.stdout).hexdigest(),
            'stderr_sha256': hashlib.sha256(result.stderr).hexdigest()})
        if check: require(result.returncode == 0, stage + '_failed')
        return result

    def inspect(self):
        require(self.cid is not None, 'container_not_created')
        result = self.run('inspect_owned_container', ['docker', 'inspect', self.cid])
        rows = json.loads(result.stdout)
        require(type(rows) is list and len(rows) == 1, 'one_owned_container')
        value = rows[0]
        require(value['Id'] == self.cid and value['Name'] == '/' + self.name
            and value['Config']['Labels'].get('recastpress.wpdb.owner') == self.owner
            and value['Image'] == self.pin['image_config_digest'], 'container_identity_or_image_changed')
        return value

    def recover_created_container(self):
        # Docker may create the resource before its CLI reports the ID. Recover
        # only the exact preselected UUID name with both ownership/image checks.
        result = self.run('recover_own_creation_identity', ['docker', 'inspect', '--type=container', self.name], check=False)
        if result.returncode:
            require(b'No such object' in result.stderr or b'No such container' in result.stderr,
                'container_creation_cleanup_unconfirmed')
            self.report['creation_no_container_observed'] = True
            return
        values = json.loads(result.stdout)
        require(len(values) == 1, 'recover_one_owned_container')
        item = values[0]
        require(item['Name'] == '/' + self.name and item['Config']['Labels'].get('recastpress.wpdb.owner') == self.owner
            and item['Image'] == self.pin['image_config_digest'], 'recover_container_identity_mismatch')
        self.cid = item['Id']
        require(type(self.cid) is str and len(self.cid) == 64 and all(c in '0123456789abcdef' for c in self.cid),
            'recovered_container_id_shape')
        self.report.update(container_started=True, container_id=self.cid, container_id_recovered_after_cli_failure=True)

    def client(self):
        exe = 'mysql' if self.backend == 'mysql' else 'mariadb'
        result = ['docker', 'exec', '--interactive', '--user=0', self.cid, exe, '--defaults-file=/rp-fixture/admin.ini']
        if self.backend == 'mysql': result.append('--no-login-paths')
        return result + ['--protocol=socket', '--batch', '--skip-column-names', '--raw', '--connect-timeout=3']

    def __enter__(self):
        try:
            self.start()
            return self
        except BaseException:
            self.close()
            raise

    def start(self):
        self.run('pull_pinned_database_image', ['docker', 'pull', '--platform=linux/amd64', self.image], timeout=600)
        image_info = self.run('inspect_pinned_database_image', ['docker', 'image', 'inspect', self.image])
        images = json.loads(image_info.stdout)
        require(len(images) == 1 and images[0]['Id'] == self.pin['image_config_digest'], 'pulled_image_config_changed')
        env_key = 'MYSQL_ROOT_PASSWORD' if self.backend == 'mysql' else 'MARIADB_ROOT_PASSWORD'
        env_file = self.private('private.env', (env_key + '=' + self.admin_secret + '\n').encode())
        self.private('admin.ini', ('[client]\nuser=root\npassword=' + self.admin_secret + '\n').encode())
        args = ['docker', 'run', '--detach', '--platform=linux/amd64', '--name', self.name,
            '--cidfile', str(owned(self.work / 'container.id')),
            '--label', 'recastpress.wpdb.owner=' + self.owner, '--env-file', str(env_file),
            '--publish', '127.0.0.1::3306', '--memory', '1g',
            '--tmpfs', '/var/lib/mysql:rw,nosuid,size=536870912', '--mount',
            'type=bind,source=' + str(self.work) + ',target=/rp-fixture,readonly', self.image,
            '--bind-address=0.0.0.0', '--port=3306', '--skip-name-resolve',
            '--pid-file=/var/lib/mysql/rp-fixture.pid', '--max-connections=8', '--innodb-buffer-pool-size=64M']
        args += (['--mysqlx=0', '--performance-schema=OFF', '--innodb-redo-log-capacity=32M', '--skip-log-bin']
            if self.backend == 'mysql' else ['--innodb-log-file-size=32M'])
        result = self.run('start_new_owned_container', args, timeout=30)
        cid = result.stdout.decode('ascii').strip()
        require(len(cid) == 64 and all(c in '0123456789abcdef' for c in cid), 'owned_container_id_shape')
        self.cid = cid
        self.report.update(container_started=True, container_id=cid, image=self.image,
            image_config_digest=self.pin['image_config_digest'])
        deadline = time.monotonic() + 120
        while True:
            value = self.inspect()
            require(value['State']['Running'] is True, 'owned_container_exited_before_ready')
            ready = self.run('read_only_owned_database_identity', self.client() +
                ['--execute=SELECT VERSION(),@@datadir,@@port,@@pid_file'], timeout=5, check=False)
            pid = self.run('owned_server_pidfile', ['docker', 'exec', self.cid,
                'cat', '/var/lib/mysql/rp-fixture.pid'], timeout=5, check=False)
            if ready.returncode == 0 and pid.returncode == 0 and pid.stdout.strip() == b'1':
                fields = ready.stdout.decode('ascii').strip().split('\t')
                require(len(fields) == 4 and fields[0].startswith(self.pin['tag'])
                    and fields[1].rstrip('/') == '/var/lib/mysql' and fields[2] == '3306'
                    and fields[3] == '/var/lib/mysql/rp-fixture.pid', 'owned_database_identity_mismatch')
                self.report['sql_server_version'] = fields[0]
                break
            require(time.monotonic() < deadline, 'owned_database_ready_deadline')
            time.sleep(0.25)
        value = self.inspect()
        ports = value['NetworkSettings']['Ports']['3306/tcp']
        require(len(ports) == 1 and ports[0]['HostIp'] == '127.0.0.1', 'container_published_outside_loopback')
        self.port = int(ports[0]['HostPort'])
        require(1 <= self.port <= 65535, 'container_published_port')
        require(value['HostConfig']['Memory'] == 1073741824 and '/var/lib/mysql' in value['HostConfig']['Tmpfs'],
            'container_fixture_resource_configuration')
        self.report['owned_database_identity_verified_before_fixture_ddl'] = True
        sql = ("CREATE DATABASE rp_wpdb_probe CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
            "CREATE TABLE rp_wpdb_probe.rp_wire_fixture(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,"
            "marker VARBINARY(32) NOT NULL UNIQUE,payload VARBINARY(64) NULL,"
            "dec_value DECIMAL(30,10) NOT NULL,big_value BIGINT UNSIGNED NOT NULL) ENGINE=InnoDB;\n"
            "CREATE USER 'rp_wpdb_probe'@'%' IDENTIFIED BY '" + self.secret + "';\n"
            "GRANT SELECT,INSERT,UPDATE,DELETE ON rp_wpdb_probe.* TO 'rp_wpdb_probe'@'%';\n").encode()
        self.run('create_owned_schema_and_limited_user', self.client(), timeout=15, input_bytes=sql)

    def input(self):
        return {'schema': 'recastpress.php-wasix-wpdb-input.v0.5', 'host': '127.0.0.1', 'port': self.port,
            'database': 'rp_wpdb_probe', 'user': 'rp_wpdb_probe', 'password': self.secret,
            'tls': 'disabled', 'tls_ca_pem': None, 'nonce': self.nonce}

    def final_rows(self):
        self.inspect()
        r = self.run('independent_final_database_rows', self.client() + ['--execute=SELECT HEX(payload),COUNT(*) '
            'FROM rp_wpdb_probe.rp_wire_fixture GROUP BY payload'])
        require(r.stdout.strip() == b'00FF42\t1', 'final_database_rows_differ')
        self.report['independent_final_database_rows_verified'] = True

    def close(self):
        if self.cid is None:
            self.recover_created_container()
            if self.cid is None: return
        try:
            value = self.inspect()
            if value['State']['Running']:
                stopped = self.run('stop_only_owned_container', ['docker', 'stop', '--time', '10', self.cid], timeout=25, check=False)
                if stopped.returncode:
                    self.report['normal_container_stop_failed'] = True
            value = self.inspect()
            require(value['State']['Running'] is False and value['State']['Pid'] == 0, 'owned_container_still_running')
            self.report.update(container_stopped=True,
                container_normal_exit_verified=value['State']['ExitCode'] == 0 and value['State']['OOMKilled'] is False)
        finally:
            value = self.inspect()  # Refuse removal if identity changed or a process is still running.
            if value['State']['Running']:
                self.run('kill_only_owned_after_failed_stop', ['docker', 'kill', self.cid], timeout=15, check=False)
                self.report['forced_owned_container_cleanup'] = True
                value = self.inspect()
                require(value['State']['Running'] is False, 'owned_container_cleanup_failed')
                self.report['container_stopped'] = True
            if value['State']['Running'] is False:
                self.run('remove_only_owned_container', ['docker', 'rm', '--volumes', self.cid])
                self.report['container_removed'] = True
            owned_probe.reap_owned_probes()

    def __exit__(self, typ, error, traceback):
        self.close()
        return False
