"""Compare one stock autoload query across fresh, guarded WordPress requests."""

import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import secrets
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = HERE.parent / 'php-wasix-wp-session-probe'
NAME = 'recastpress-php-wasix-autoload-cache-probe'
PINS = {
    BASE / 'run_check.py': '18238bcc3927ebdb3b5f85bc63f9fc65aa1beb671e7580bbe9a2055ceee956b0',
    BASE / 'session_process.py': 'a9f385032707c6f5903d0a687e8eb2934f6e7bc371af22549443502a2c514e16',
    HERE.parent / 'php-wasix-wpdb-probe/linux_database.py': '6d0d4f6e65edb8ae5029e9bd900ae5996795a80fc4996e52eb3cb2ce662c5ddd',
    HERE.parent / 'php-wasix-wp-bootstrap-probe/linux_database.py': 'c8d01c4b29ed7769e6d5624c9d0835db0bcfc2032cfe89cc05782d9d8092fbae',
    HERE.parent / 'php-wasix-wp-request-probe/linux_database.py': 'c2701e151e53637654618a346f8d903b5c1af65d28d9d80cf9321e356be1a577',
    HERE.parent / 'php-wasix-wp-request-context-probe/linux_database.py': 'f593c8b7e4c5ab65942b44169414b70543c03f0f432b2e549f6e4ef4429d2b3c',
}
GENERATED = ('src/main.rs', 'src/db.rs')
DERIVATIONS = ('derive_actor.py', 'derive_worker.py')
SQL = b"SELECT option_name, option_value FROM rp_options WHERE autoload IN ( 'yes', 'on', 'auto-on', 'auto' )"
MAX_RETAINED_BYTES = 128 * 1024


def load_session():
    for path, expected in PINS.items():
        if (path.resolve(strict=True) != path or not path.is_file() or path.stat().st_size > 128 * 1024
                or sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError('autoload_pinned_session_source_changed')
    spec = importlib.util.spec_from_file_location('rp_autoload_stock_session', BASE / 'run_check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = load_session()
context, process = baseline.context, baseline.process
require, equal, sha = baseline.require, baseline.equal, baseline.sha


def sources():
    inventory = dict(baseline.build.inputs())
    paths = [*PINS, Path(__file__).resolve(), *(HERE / name for name in DERIVATIONS),
        HERE / 'Cargo.toml', HERE / 'Cargo.lock', HERE / 'src/options_cache.rs',
        ROOT / '.github/workflows/php-wasix-autoload-cache.yml', ROOT / 'crates/recastpress-core/src/resource_budget.rs']
    inventory.update({path.relative_to(ROOT).as_posix(): sha(path.read_bytes()) for path in paths})
    return dict(sorted(inventory.items()))


def generated():
    return {name: process.identity((HERE / name).read_bytes()) for name in GENERATED}


def build(report):
    target = baseline.build.TARGET
    manifest = HERE / 'Cargo.toml'
    args = ['--offline', '--locked', '--release', '--manifest-path', str(manifest), '--target-dir', str(target)]
    for script in DERIVATIONS:
        baseline.build.child(script, [sys.executable, '-B', str(HERE / script), '--verify-only'], 60, report)
    unit = baseline.build.child('units', ['cargo', 'test', '--quiet', *args], 1200, report)
    units = re.findall(rb'test result: ok\. ([0-9]+) passed; 0 failed; 0 ignored;', unit.stdout)
    require(len(units) == 1 and int(units[0]) > 0, 'autoload_worker_units')
    report['units_passed'] = int(units[0])
    result = baseline.build.child('worker', ['cargo', 'build', '--message-format=json-render-diagnostics', *args], 1200, report)
    events = [process.parse(line) for line in result.stdout.splitlines()]
    built = [row for row in events if row['reason'] == 'compiler-artifact' and row['target']['name'] == NAME]
    binary = target / 'release' / NAME
    require(len(built) == 1 and events[-1] == {'reason': 'build-finished', 'success': True}
        and built[0]['target']['kind'] == ['bin'] and built[0]['profile']['test'] is False
        and Path(built[0]['manifest_path']) == manifest and Path(built[0]['target']['src_path']) == HERE / 'src/main.rs'
        and Path(built[0]['executable']) == binary and binary.resolve(strict=True) == binary
        and 0 < binary.stat().st_size <= 256 * 1024 * 1024, 'autoload_worker_executable')
    report['binary'] = process.identity(binary.read_bytes())
    return binary


def seed_guard(database, nonce, password):
    require(all(type(value) is str and re.fullmatch('[a-f0-9]{48}', value) is not None
        for value in (nonce, password)) and nonce != password, 'autoload_guard_private_input')
    database.inspect()
    sql = (
        'CREATE DATABASE rp_options_guard_fixture;\n'
        'CREATE TABLE rp_options_guard_fixture.identity (id BIGINT UNSIGNED PRIMARY KEY, '
        'nonce VARBINARY(48) NOT NULL) ENGINE=InnoDB;\n'
        f"INSERT INTO rp_options_guard_fixture.identity VALUES (1,X'{nonce.encode('ascii').hex()}');\n"
        f"CREATE USER 'rp_options_guard'@'%' IDENTIFIED BY '{password}';\n"
        "GRANT LOCK TABLES ON rp_wpdb_probe.* TO 'rp_options_guard'@'%';\n"
        "GRANT LOCK TABLES ON rp_options_guard_fixture.* TO 'rp_options_guard'@'%';\n"
        "GRANT SELECT ON rp_wpdb_probe.rp_options TO 'rp_options_guard'@'%';\n"
        "GRANT SELECT ON rp_options_guard_fixture.identity TO 'rp_options_guard'@'%';\n"
    ).encode('ascii')
    result = database.run('seed_owned_options_guard', database.client(), timeout=15, input_bytes=sql)
    require(result.stdout == result.stderr == b'', 'autoload_guard_seed_failed')
    return {'port': database.port, 'password': password, 'nonce': nonce}


def verify_writer_blocked(database):
    result = database.run('verify_owned_options_writer_blocked', database.client(), timeout=5, check=False,
        input_bytes=b"SET SESSION lock_wait_timeout=1;\n"
            b"UPDATE rp_wpdb_probe.rp_options SET option_value='http://rp-guard-blocked.invalid' WHERE option_name='siteurl';\n")
    require(result.returncode == 1 and not result.stdout
        and re.findall(rb'(?m)^ERROR ([0-9]+) ', result.stderr) == [b'1205'],
        'autoload_options_writer_not_blocked')


def query_observation(request):
    rows = [row for row in request['operations'] if row['request_data_sha256'] == sha(SQL)]
    require(len(rows) == 1, 'autoload_exact_query_count')
    row = rows[0]
    equal(row['seq'], 10)
    equal(row['op'], 'query')
    equal(row['request_data_bytes'], len(SQL))
    equal(row['column_count'], 2)
    equal(row['ok'], True)
    equal(row['server_error_code'], None)
    equal(row['server_error_state_hex'], None)
    require(type(row['row_count']) is int and 0 < row['row_count'] <= 256
        and type(row['reply_frame_bytes']) is int and 0 < row['reply_frame_bytes'] <= 512 * 1024
        and type(row['reply_frame_sha256']) is str and re.fullmatch('[0-9a-f]{64}', row['reply_frame_sha256']),
        'autoload_exact_query_shape')
    # The complete reply digest binds ordered row bytes and every column/status field.
    return {key: row[key] for key in ('seq', 'op', 'request_data_bytes', 'request_data_sha256', 'row_count',
        'column_count', 'reply_frame_bytes', 'reply_frame_sha256')}


def comparable_operations(request):
    return [{key: value for key, value in row.items()
        if key != 'request_frame_sha256'
        and (row['op'] != 'hello' or key not in ('reply_frame_sha256', 'reply_frame_bytes'))}
        for row in request['operations']]


def failure_diagnostic(session):
    try:
        with session.stdout.open('rb') as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - 2048))
            tail = stream.read(2048)
        if not tail.endswith(b'\n'):
            return None
        final = process.parse(tail.splitlines()[-1])
        if final['schema'] != 'recastpress.php-wasix-wp-session-observation.v0.5' or final['passed'] is not False:
            return None
        released = final['options_guard_released']
        stats = final['options_cache_diagnostic']
        candidate = final['options_candidate']
        if type(released) is not bool:
            return None
        if stats is not None:
            limits = {'sql_reads': 2, 'hits': 2, 'charged_bytes': 128 * 1024, 'retained_reply_bytes': 128 * 1024}
            if type(stats) is not dict or stats.keys() != limits.keys() or any(
                type(stats[key]) is not int or not 0 <= stats[key] <= limit for key, limit in limits.items()
            ):
                return None
        if candidate is not None:
            if type(candidate) is not dict or set(candidate) != {'before_status', 'reply_status', 'admission'}:
                return None
            before, after = candidate['before_status'], candidate['reply_status']
            if type(before) is not int or not 0 <= before <= 65535 or (
                after is not None and (type(after) is not int or not 0 <= after <= 65535)
            ) or candidate['admission'] not in ('reply_profile', 'encoding_budget', 'reservation', 'stored'):
                return None
        return {'options_guard_released': released, 'cache': stats, 'candidate': candidate}
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def check_final(final):
    cache = final['options_cache']
    retained = cache['retained_reply_bytes']
    require(type(retained) is int and 0 < retained <= MAX_RETAINED_BYTES, 'autoload_retained_reply_bound')
    equal(cache, {'sql_reads': 1, 'hits': 1, 'charged_bytes': 0, 'retained_reply_bytes': retained})
    equal(final['options_guard_released'], True)
    baseline.validate_final({key: value for key, value in final.items()
        if key not in ('options_cache', 'options_guard_released')}, 3, True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('mysql', 'mariadb'), required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report = {'schema': 'recastpress.php-wasix-autoload-cache-check.v0.5', 'backend': args.backend,
        'passed': False, 'commands': [], 'requests': {}}
    database = None
    password = None
    stage = 'preflight'
    try:
        require(sys.platform == 'linux', 'autoload_cache_linux_only')
        report['sources_before'] = sources()
        report['generated'] = generated()
        bootstrap = context.oldbuild.verify_build()
        report['module_provenance'] = context.managed.verify_build()
        native = context.linux.reference(args.backend)
        native_boot = context.original.reference(args.backend)
        env = baseline.build.environment()
        stage = 'build'
        binary = build(report)
        owned = {'observations': {}}
        database = context.linux.Database(args.backend, owned)
        with database:
            for mode in ('install', 'read'):
                stage = mode
                value = context.execute(ROOT / bootstrap['executable'], env, database.request(mode), owned, mode)
                context.original.validate(value, mode, native_boot[mode])
            database.seed_public_dates()
            database.verify_context_state('before_requests')
            requests = [(case, label, database.context_request(case))
                for case, label in (('home', 'home'), ('missing', 'missing'), ('missing', 'missing_repeat'))]
            for _, _, payload in requests:
                payload['schema'] = baseline.private.INPUT
            require(len({payload['database']['nonce'] for _, _, payload in requests}) == 3, 'autoload_distinct_nonces')
            stage = 'startup'
            with process.Session(binary, env, report, 'autoload_cache') as session:
                ready = session.ready()
                equal(ready['module_sha256'], report['module_provenance']['module']['sha256'])
                report['session_ready'] = ready
                cold_operations = None
                for seq, (case, label, payload) in enumerate(requests, 1):
                    stage = 'request_' + label
                    value = session.request(seq, payload)
                    checked = baseline.validate_request(value, case, native['requests'][label]['observation'])
                    observation = query_observation(value)
                    report['requests'][label] = {'lifecycle_and_native_comparison': checked,
                        'autoload_query': observation, 'response_status': value['private_response']['status'],
                        'response_body_sha256': value['private_response']['body_sha256']}
                    database.verify_context_state(label, value['php_result'])
                    if seq == 1:
                        stage = 'seed_guard'
                        password = secrets.token_hex(24)
                        session.send(seed_guard(database, requests[1][2]['database']['nonce'], password))
                    elif seq == 2:
                        cold_operations = comparable_operations(value)
                        stage = 'blocked_writer'
                        verify_writer_blocked(database)
                        database.verify_context_state(label, value['php_result'])
                        report['independent_options_writer_blocked'] = True
                    else:
                        equal(comparable_operations(value), cold_operations)
                        report['cold_and_hot_ordered_rpc_exact'] = True
                stage = 'session_eof'
                equal(report['requests']['missing']['autoload_query'], report['requests']['missing_repeat']['autoload_query'])
                equal(report['requests']['missing']['response_body_sha256'], report['requests']['missing_repeat']['response_body_sha256'])
                try:
                    final = session.finish()
                except ValueError as error:
                    if str(error) == 'session_expected_exit':
                        diagnostic = failure_diagnostic(session)
                        if diagnostic is not None:
                            report['session_failure_diagnostic'] = diagnostic
                    raise
                check_final(final)
                report['session_final'] = final
            database.verify_context_state('missing_repeat', value['php_result'])
        stage = 'postflight'
        require(all(owned[key] is True for key in ('container_stopped', 'container_removed',
            'container_normal_exit_verified')), 'autoload_database_cleanup')
        report['database'] = {key: owned[key] for key in ('sql_server_version', 'container_stopped',
            'container_removed', 'container_normal_exit_verified')}
        equal(sources(), report['sources_before'])
        equal(generated(), report['generated'])
        equal(process.identity(binary.read_bytes()), report['binary'])
        equal(context.managed.verify_build(), report['module_provenance'])
        equal(context.oldbuild.verify_build(), bootstrap)
        report.update(passed=True, source_reverified_after=True, cold_and_hot_reply_bytes_exact=True,
            unchanged_stock_wordpress_guest=True, options_table_read_lock=True)
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        try:
            process.reap_owned_sessions()
            context.owned_probe.reap_owned_probes()
            require(all(row.get('owned_pid') is None or row.get('owned_reaped') is True
                for row in report.get('session_executions', {}).values()), 'autoload_owned_session_cleanup')
            report['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt):
            report.update(passed=False, cleanup_failed=True)
    wire = (json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True, allow_nan=False) + '\n').encode()
    private = database.private_values() if database is not None else []
    if password is not None:
        private.append(password)
    require(all(secret.encode() not in wire and secret.encode().hex().encode() not in wire for secret in private),
        'autoload_cache_secret_guard')
    require(len(wire) <= 12 * 1024 * 1024, 'autoload_cache_report_bound')
    if args.record:
        baseline.build.preserve(HERE / f'evidence/linux-{args.backend}.json', wire)
    print('WordPress Wasm autoload cache: ' + ('passed' if report['passed'] else report.get('failure', 'failed')))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
