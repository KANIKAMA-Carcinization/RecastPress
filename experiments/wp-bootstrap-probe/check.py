"""Lint native fixtures and reject bad inputs before bootstrap or DB access.

No valid connection request is executed. Root owns all real DB/install trials.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe
spec = importlib.util.spec_from_file_location('rp_bootstrap_nonconnecting_preparation', HERE / 'prepare.py')
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def sources():
    result = prep.inputs()
    for path in [HERE / 'check.py', ROOT / 'scripts/owned_probe.py']:
        result[path.relative_to(ROOT).as_posix()] = prep.sha(prep.read(path, 256 * 1024))
    return result


def request(mode):
    salts = {key: 'b' * 48 for key in ['AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY',
        'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT']}
    value = {'schema': 'recastpress.wp-bootstrap-native-input.v0.5', 'mode': mode, 'host': '127.0.0.1',
        'port': 1, 'database': 'rp_wpdb_probe', 'user': 'rp_wp_setup' if mode == 'install' else 'rp_wpdb_probe',
        'password': 'a' * 48, 'salts': salts}
    if mode == 'install':
        value['admin_password'] = 'c' * 48
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    result = {'schema': 'recastpress.wp-bootstrap-nonconnecting-check.v0.5', 'passed': False,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'outcome': 'not_run', 'commands': [],
        'database_started': False, 'valid_connection_request_executed': False,
        'wordpress_bootstrap_executed': False, 'installer_executed': False,
        'rust_or_wasm_executed': False, 'product_runtime_selected': False,
        'owned_cleanup_completed': False, 'input_files_unchanged_after': False}
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(
        ('MYSQL_', 'MARIADB_', 'RP_', 'GH_', 'GITHUB_')) and k.upper() not in
        {'PHPRC', 'PHP_INI_SCAN_DIR', 'ACTIONS_RUNTIME_TOKEN'}}
    passed = False

    def run(name, command, payload=None):
        start = time.monotonic()
        pid, observed = owned_probe.run_captured_probe(command, environment, timeout=10, input_bytes=payload)
        result['commands'].append({'stage': name, 'owned_pid': pid, 'owned_reaped': True,
            'timeout_seconds': 10, 'exit_code': observed.returncode,
            'elapsed_seconds': round(time.monotonic() - start, 3),
            'stdout_sha256': prep.sha(observed.stdout), 'stderr_sha256': prep.sha(observed.stderr)})
        prep.require(len(observed.stdout) <= 16384 and len(observed.stderr) <= 16384, 'bootstrap_qa_capture_quota')
        return observed

    try:
        before = result['input_files'] = sources()
        result['preparation_before'] = prep.prepare()
        result['native_reference'] = prep.references()
        linted = []
        for name in ['install.php', 'read.php', 'bootstrap.php']:
            observed = run('lint_' + name, [str(prep.native.PHP / 'php.exe'), '-n', '-l', str(HERE / 'fixtures' / name)])
            prep.require(observed.returncode == 0 and not observed.stderr and b'No syntax errors detected' in observed.stdout,
                'bootstrap_fixture_lint')
            linted.append(name)
        result['linted_files'] = linted
        cases = []
        for case in ['schema', 'host', 'mode', 'user', 'missing_salt', 'duplicate_mode',
                'duplicate_salt', 'admin_password', 'extra_admin_in_read', 'password']:
            mode = 'install' if case == 'admin_password' else 'read'
            value = request(mode)
            if case == 'schema': value['schema'] = 'invalid'
            elif case == 'host': value['host'] = '192.0.2.1'
            elif case == 'mode': value['mode'] = 'install'
            elif case == 'user': value['user'] = 'rp_probe_admin'
            elif case == 'missing_salt': del value['salts']['AUTH_KEY']
            elif case == 'admin_password': value['admin_password'] = 'invalid'
            elif case == 'extra_admin_in_read': value['admin_password'] = 'c' * 48
            elif case == 'password': value['password'] = 'invalid'
            wire = json.dumps(value, separators=(',', ':')).encode()
            if case == 'duplicate_mode': wire = wire[:-1] + b',"mode":"read"}'
            elif case == 'duplicate_salt': wire = wire.replace(b'"salts":{', b'"salts":{"AUTH_KEY":"' + b'b' * 48 + b'",')
            observed = run('reject_' + case, prep.native_command(mode), wire)
            prep.require(observed.returncode == 1 and not observed.stderr and observed.stdout.endswith(b'\n')
                and observed.stdout.count(b'\n') == 1, 'bootstrap_preconnection_rejection')
            value = json.loads(observed.stdout)
            prep.require(value['passed'] is False and value['phase'] == 'input'
                and value['bootstrap_completed'] is False and value['source_inventory_verified'] is False
                and value['physical_disconnect_completed'] is False
                and value['blocked_mail_calls'] == value['blocked_http_calls'] == 0,
                'bootstrap_rejection_before_source_and_connection')
            cases.append({'case': case, 'mode': mode, 'observation': value})
        result['rejections'] = cases
        result['preparation_after'] = prep.verify_prepared()
        prep.require(result['preparation_after'] == result['preparation_before'] and sources() == before,
            'bootstrap_qa_inputs_changed')
        result['input_files_unchanged_after'] = True
        passed = True
        result['outcome'] = 'full_source_lint_and_ten_preconnection_rejections_passed'
    except (Exception, KeyboardInterrupt) as error:
        result.update(outcome='bootstrap_nonconnecting_check_failed', failure_class=type(error).__name__)
        if isinstance(error, ValueError):
            result['failure'] = str(error)
    finally:
        try:
            owned_probe.reap_owned_probes()
            result['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt):
            passed = False
            result['outcome'] = 'bootstrap_owned_cleanup_failed'
    result['passed'] = passed
    if args.record:
        path = HERE / 'evidence/windows-nonconnecting-check.json'
        prep.require(path.resolve() == path and path.parent.resolve() == path.parent, 'bootstrap_qa_receipt_path')
        path.parent.mkdir(exist_ok=True)
        with path.open('xb') as output:
            output.write((json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode())
    print('WP bootstrap preparation: ' + result['outcome'])
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
