"""Compare the retained driver error packet to its actual owned loopback wire."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

from wire_proxy import WireProxy


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BINARY = ROOT / 'target/wpdb-raw-error-probe/cargo-target/debug/recastpress-wpdb-raw-error-wire-probe.exe'
LABELS = ('utf8_signal', 'latin1_signal', 'next_signal')
SCHEMA = 'recastpress.wpdb-raw-error-wire-driver-'
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe


def require(value, code):
    if not value:
        raise ValueError(code)


def identity(wire):
    return {'bytes': len(wire), 'sha256': hashlib.sha256(wire).hexdigest()}


def load_owner():
    spec = importlib.util.spec_from_file_location('rp_driver_raw_error_database',
        ROOT / 'experiments/wpdb-bridge-probe/owned_database.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixed_binary(expected):
    require(re.fullmatch('[a-f0-9]{64}', expected) is not None and BINARY.resolve(strict=True) == BINARY
        and BINARY.is_file() and 0 < BINARY.stat().st_size <= 128 * 1024 * 1024, 'raw_driver_binary')
    actual = identity(BINARY.read_bytes())
    require(actual['sha256'] == expected, 'raw_driver_binary_digest')
    return actual


def run(backend, expected):
    require(sys.platform == 'win32', 'native_raw_driver_windows_only')
    source = load_owner()
    owner_report = {}
    owner = source.OwnedDatabase(backend, owner_report)
    report = {'schema': SCHEMA + 'check.v0.5', 'backend': backend, 'passed': False}
    stage = 'preflight'
    try:
        report['driver_binary'] = fixed_binary(expected)
        with owner:
            stage = 'owned_driver_wire'
            with WireProxy(owner.port) as proxy:
                config = owner.request(SCHEMA + 'input.v0.5')
                del config['tls'], config['tls_ca_pem']
                config['port'] = proxy.port
                pid, completed = owned_probe.run_captured_probe([str(BINARY)], source.db.environment(),
                    timeout=25, input_bytes=json.dumps(config, separators=(',', ':'), ensure_ascii=True).encode('ascii'))
                report['driver_process'] = {'owned_pid': pid, 'owned_reaped': True, 'exit_code': completed.returncode,
                    'stdout': identity(completed.stdout), 'stderr': identity(completed.stderr)}
                require(0 < len(completed.stdout) <= 4096 and completed.stdout.endswith(b'\n')
                    and completed.stdout.count(b'\n') == 1 and not completed.stderr, 'raw_driver_capture')
                value = json.loads(completed.stdout)
                if value.get('passed') is False and re.fullmatch('driver_[a-z_]{1,80}', value.get('failure', '')):
                    report['driver_failure'] = value['failure']
                try:
                    observed = proxy.result()
                except ValueError as error:
                    if re.fullmatch('wire_[a-z_]{1,80}', str(error)):
                        report['proxy_failure'] = str(error)
                    raise
                require(completed.returncode == 0 and value['passed'] is True
                    and value['schema'] == SCHEMA + 'observation.v0.5'
                    and value['owned_connection_closed'] is True, 'raw_driver_execution')
            stage = 'wire_compare'
            observations = value['observations']
            require(set(observations) == {*LABELS, 'recovery'}, 'raw_driver_observation_inventory')
            for label in LABELS:
                driver, wire = observations[label], observed['observations'][label]
                require(wire['kind'] == 'err' and set(driver) == {'code', 'sqlstate', 'message_hex', 'legacy_string_preserves_bytes'}
                    and type(driver['legacy_string_preserves_bytes']) is bool
                    and driver['code'] == wire['code'] and driver['sqlstate'].encode('ascii').hex() == wire['state_hex']
                    and driver['message_hex'] == wire['message_hex'], 'raw_driver_wire_mismatch')
                message = bytes.fromhex(wire['message_hex'])
                require(driver['legacy_string_preserves_bytes'] == (message.decode('utf-8', 'replace').encode('utf-8') == message),
                    'raw_driver_legacy_string_relation')
            require(observations['recovery'] == {'value': 42, 'retained_error_cleared': True}
                and observed['observations']['recovery'] == {'kind': 'resultset', 'columns': 1}, 'raw_driver_recovery_wire')
            report['observations'] = observations
            report['wire'] = observed
            result = owner.harness.checked('raw_err_final_fixture_rows', [*owner.admin,
                '--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_wire_fixture'], timeout=5, limit=65536)
            require(result.stdout.strip() == b'0', 'raw_driver_fixture_unchanged')
        stage = 'postflight'
        require(owner_report['normal_admin_shutdown_verified'] is True and owner_report['database_closed_and_reaped'] is True
            and owner_report['all_command_children_reaped'] is True and fixed_binary(expected) == report['driver_binary'],
            'raw_driver_cleanup_and_binary')
        report.update(passed=True, retained_packet_matches_wire=True, fixture_rows_unchanged=True,
            database_normal_cleanup=True, sql_server_version=owner_report['sql_server_version'])
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if type(error) is ValueError and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        owned_probe.reap_owned_probes()
    wire = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    for secret in (owner.password, owner.admin_password):
        require(secret.encode('ascii') not in wire and secret.encode('ascii').hex().encode('ascii') not in wire,
            'raw_driver_no_credentials')
    return report, wire


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('mysql', 'mariadb'), required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report, wire = run(args.backend, args.sha256)
    if args.record:
        path = HERE / 'evidence' / f'windows-{args.backend}-driver.json'
        path.parent.mkdir(exist_ok=True)
        require(path.resolve() == path, 'raw_driver_evidence_path')
        with path.open('xb') as stream:
            stream.write(wire)
    print('Retained raw DB error: ' + ('passed' if report['passed'] else report.get('failure', 'failed')))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
