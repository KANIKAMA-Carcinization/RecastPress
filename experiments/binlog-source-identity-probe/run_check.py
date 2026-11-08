"""Prove a fixed source binding across distinct owned SQL connections on Windows."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import shutil
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FRONTIER = HERE.parent / 'binlog-autoload-frontier-probe'
PINS = {
    'run_check.py': 'c9fa8f37d606d928e9864fe6e6ddd25eb88c7f394d4e2202637eb4ea45b0ecca',
    'Cargo.toml': 'e230661b6adddd2f1eddc072269b61fffc8e962aca01312a83ee094b3f82608f',
    'Cargo.lock': '167147a4cf68b410360de3b2ca3e078c590e7d5debff572b06d535b235ed807c',
}
TARGET = ROOT / 'target/wpdb-rpc-probe/windows-cargo-target'
NAME = 'recastpress-binlog-source-identity-probe'
EXPECTED = {'passed': True, 'physical_connections': 2, 'distinct_connection_ids': True,
    'matching_cache_permit': True, 'mismatch_cache_permit': False, 'actor_nonce_read_denied': True,
    'actor_and_monitor_disconnected': True}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def pinned(name):
    path = FRONTIER / name
    wire = path.read_bytes()
    require(path.resolve(strict=True) == path and sha256(wire).hexdigest() == PINS[name], 'identity_source_pin')
    return wire


def build():
    target = ROOT / 'target'
    require(target.resolve(strict=True) == target, 'identity_build_root')
    source = (HERE / 'identity.rs').read_bytes()
    manifest = pinned('Cargo.toml') + f'\n[[bin]]\nname = "{NAME}"\npath = "identity.rs"\n'.encode('ascii')
    lock = pinned('Cargo.lock')
    with tempfile.TemporaryDirectory(prefix='source-identity-', dir=target) as directory:
        stage = Path(directory)
        (stage / 'Cargo.toml').write_bytes(manifest)
        (stage / 'Cargo.lock').write_bytes(lock)
        (stage / 'identity.rs').write_bytes(source)
        result = subprocess.run(['cargo', 'build', '--offline', '--locked', '--manifest-path', str(stage / 'Cargo.toml'),
            '--target-dir', str(TARGET)], cwd=ROOT, capture_output=True, timeout=180)
        require(result.returncode == 0 and (stage / 'Cargo.lock').read_bytes() == lock, 'identity_locked_build')
    binary = TARGET / 'debug' / (NAME + '.exe')
    require(binary.resolve(strict=True) == binary and 0 < binary.stat().st_size <= 64 * 1024 * 1024, 'identity_binary')
    return binary, sha256(source).hexdigest()


def run(backend, owner_module, binary, source_sha):
    report = {}
    owner = owner_module['OwnedDatabase'](backend, report)
    stage = 'owned_database'
    try:
        with owner:
            require(report['database_identity_verified_before_fixture_ddl'] is True, 'identity_fixture_owner')
            stage = 'seed'
            binding, wrong_binding, nonce, monitor_password = [secrets.token_hex(24) for _ in range(4)]
            sql = (
                'CREATE DATABASE rp_options_guard_fixture;\n'
                'CREATE TABLE `rp_options_guard_fixture`.`identity` (id BIGINT UNSIGNED PRIMARY KEY, '
                'binding VARBINARY(48) NOT NULL, nonce VARBINARY(48) NOT NULL) ENGINE=InnoDB;\n'
                f"INSERT INTO `rp_options_guard_fixture`.`identity` VALUES (1,X'{binding.encode().hex()}',X'{nonce.encode().hex()}'),"
                f"(2,X'{wrong_binding.encode().hex()}',X'{nonce.encode().hex()}');\n"
                f"CREATE USER 'rp_dependency_monitor'@'%' IDENTIFIED BY '{monitor_password}';\n"
                "GRANT SELECT ON `rp_options_guard_fixture`.`identity` TO 'rp_dependency_monitor'@'%';\n"
                "GRANT SELECT (id,binding) ON `rp_options_guard_fixture`.`identity` TO 'rp_wpdb_probe'@'127.0.0.1';\n"
            ).encode('ascii')
            seeded = owner.harness.checked('seed_source_identity', owner.admin, timeout=10, input_bytes=sql)
            require(not seeded.stdout and not seeded.stderr, 'identity_fixture_seed')
            stage = 'real_connections'
            payload = dict(port=owner.port, actor_password=owner.password, monitor_password=monitor_password,
                binding=binding, wrong_binding=wrong_binding, nonce=nonce)
            child = owner.harness.run('verify_source_identity', [str(binary)], timeout=20,
                input_bytes=json.dumps(payload).encode('ascii'), limit=4096)
            require(all(value.encode() not in child.stdout and value.encode().hex().encode() not in child.stdout
                for value in payload.values() if type(value) is str), 'identity_private_output')
            observation = json.loads(child.stdout)
            if child.returncode and type(observation) is dict and re.fullmatch(r'identity_[a-z_]{1,72}', observation.get('error', '')):
                raise ValueError(observation['error'])
            require(child.returncode == 0 and not child.stderr and type(observation) is dict
                and all(type(observation.get(key)) is type(value) and observation[key] == value
                    for key, value in EXPECTED.items()) and observation.keys() == EXPECTED.keys(), 'identity_observation')
        stage = 'cleanup'
        require(all(report.get(key) is True for key in ('normal_admin_shutdown_verified', 'database_closed_and_reaped',
            'all_command_children_reaped')), 'identity_owned_cleanup')
        require(sha256((HERE / 'identity.rs').read_bytes()).hexdigest() == source_sha, 'identity_source_changed')
        result = {'passed': True, 'backend': backend, 'version': report['sql_server_version'], 'observation': observation,
            'source_sha256': source_sha, 'normal_shutdown': True, 'all_owned_processes_reaped': True}
    except Exception as error:
        result = {'passed': False, 'backend': backend, 'stage': stage, 'failure_class': type(error).__name__}
        if isinstance(error, ValueError) and re.fullmatch(r'identity_[a-z_]{1,72}', str(error)):
            result['failure'] = str(error)
    if hasattr(owner, 'work') and owner.work.exists() and (report.get('database_closed_and_reaped') or owner.harness.server is None):
        work = owner.work.resolve(strict=True)
        require(work == owner.work and work.parent == owner_module['db'].RUNS.resolve(strict=True)
            and re.fullmatch(f'bridge-{backend}-[a-f0-9]{{32}}', work.name) and not owner.harness.owned_children,
            'identity_owned_cleanup_path')
        shutil.rmtree(work)
        result['owned_directory_removed'] = not work.exists()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('mysql', 'mariadb'))
    args = parser.parse_args()
    require(os.name == 'nt' and __debug__, 'identity_windows_fixture')
    pinned('run_check.py')
    owner = runpy.run_path(str(FRONTIER / 'run_check.py'))['load_owner']()
    binary, source_sha = build()
    passed = True
    for backend in (args.backend,) if args.backend else ('mysql', 'mariadb'):
        result = run(backend, owner, binary, source_sha)
        print(json.dumps(result, sort_keys=True), flush=True)
        passed = passed and result['passed'] and result.get('owned_directory_removed') is True
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
