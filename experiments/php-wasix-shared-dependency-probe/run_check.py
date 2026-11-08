"""Exercise one guarded Rust dependency read across fresh PHP-Wasm requests."""

import argparse
import importlib.util
import inspect
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = HERE.parent / 'php-wasix-wp-session-probe'
NAME = 'recastpress-php-wasix-shared-dependency-probe'
VALUE = b'recastpress-shared-fixture'
OP = 'read_fixture_dependency'


def load(path):
    spec = importlib.util.spec_from_file_location('rp_shared_dependency_session_baseline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = load(BASE / 'run_check.py')
context, process = baseline.context, baseline.process
require, equal, sha = baseline.require, baseline.equal, baseline.sha


def validator():
    # Add exactly the new command to the accepted operation vocabulary.
    namespace = dict(vars(baseline))
    source = inspect.getsource(baseline.request_validator)
    anchor = '    namespace=dict(context.theme.__dict__)'
    require(source.count(anchor) == 1, 'dependency_validator_anchor')
    source = source.replace(anchor, '''    operation_list = "['hello','query','escape','close']"
    require(derived.count(operation_list) == 1, 'dependency_validator_operation_list')
    derived = derived.replace(operation_list, "['hello','query','escape','close','read_fixture_dependency']")
''' + anchor)
    exec(compile(source + '\n' + inspect.getsource(baseline.validate_request),
        '<fixed-dependency-validator>', 'exec'), namespace)
    return namespace['validate_request']


def sources():
    inventory = dict(baseline.build.inputs())
    paths = [BASE / name for name in ('run_check.py', 'session_process.py')]
    paths += [ROOT / '.github/workflows/php-wasix-shared-dependency.yml',
        ROOT / 'crates/recastpress-core/src/resource_budget.rs',
        HERE.parent / 'binlog-shared-cache-probe/shared_object_cache.rs',
        HERE / 'src/dependency.rs']
    paths.extend(path for path in HERE.iterdir() if path.suffix in ('.py', '.toml', '.lock')
        and path.name != 'import_evidence.py')
    inventory.update({path.relative_to(ROOT).as_posix(): sha(path.read_bytes()) for path in paths})
    return dict(sorted(inventory.items()))


def build(report):
    target = baseline.build.TARGET
    manifest = HERE / 'Cargo.toml'
    args = ['--offline', '--locked', '--release', '--manifest-path', str(manifest), '--target-dir', str(target)]
    for script in ('derive_guest.py', 'derive_actor.py', 'derive_worker.py'):
        baseline.build.child(script, [sys.executable, '-B', str(HERE / script), '--verify-only'], 60, report)
    unit = baseline.build.child('units', ['cargo', 'test', '--quiet', *args], 1200, report)
    units = re.findall(rb'test result: ok\. ([0-9]+) passed; 0 failed; 0 ignored;', unit.stdout)
    require(len(units) == 1 and int(units[0]) > 0, 'dependency_worker_units')
    report['units_passed'] = int(units[0])
    result = baseline.build.child('worker', ['cargo', 'build', '--message-format=json-render-diagnostics', *args],
        1200, report)
    events = [process.parse(line) for line in result.stdout.splitlines()]
    built = [row for row in events if row['reason'] == 'compiler-artifact' and row['target']['name'] == NAME]
    binary = target / 'release' / NAME
    require(len(built) == 1 and events[-1] == {'reason': 'build-finished', 'success': True}
        and built[0]['target']['kind'] == ['bin'] and built[0]['profile']['test'] is False
        and Path(built[0]['manifest_path']) == manifest and Path(built[0]['target']['src_path']) == HERE / 'src/main.rs'
        and Path(built[0]['executable']) == binary and binary.resolve(strict=True) == binary
        and 0 < binary.stat().st_size <= 256 * 1024 * 1024, 'dependency_worker_executable')
    report['binary'] = process.identity(binary.read_bytes())
    return binary


def fixture(database, nonce):
    require(type(nonce) is str and re.fullmatch('[0-9a-f]{48}', nonce) is not None, 'dependency_fixture_nonce')
    sql = (
        'CREATE TABLE rp_wpdb_probe.rp_dependency_fixture '
        '(id BIGINT UNSIGNED PRIMARY KEY, value VARBINARY(64), fixture_nonce VARBINARY(48)) ENGINE=InnoDB;\n'
        f"INSERT INTO rp_wpdb_probe.rp_dependency_fixture VALUES (1,X'{VALUE.hex()}',X'{nonce.encode().hex()}');\n"
    ).encode('ascii')
    result = database.run('create_owned_dependency_fixture', database.client(), timeout=15, input_bytes=sql)
    require(result.stdout == result.stderr == b'', 'dependency_fixture_creation')


def verify_row_lock(database):
    result = database.run('verify_owned_dependency_row_lock', database.client(), timeout=5, check=False,
        input_bytes=b"SET SESSION innodb_lock_wait_timeout=1;\n"
            b"UPDATE rp_wpdb_probe.rp_dependency_fixture SET value=X'627265616368' WHERE id=1;\n")
    require(result.returncode == 1 and result.stdout == b'' and b'ERROR 1205 ' in result.stderr,
        'dependency_fixture_lock_did_not_block_write')


def check_request(value, seq, native, validate):
    checked = validate(value, 'home', native)
    expected = {'source': 'miss_fill' if seq == 1 else 'hit', 'bytes': len(VALUE),
        'sha256': sha(VALUE), 'stock_cache_unchanged': True}
    equal(value['dependency_trial'], expected)
    reads = [row for row in value['operations'] if row['op'] == OP]
    require(len(reads) == 1 and reads[0]['ok'] is True and reads[0]['request_data_bytes'] == 0
        and reads[0]['request_data_sha256'] == sha(b'') and reads[0]['row_count'] is None
        and reads[0]['column_count'] is None, 'dependency_exact_operation')
    return {'cache': expected, 'lifecycle_and_native_comparison': checked,
        'response_status': value['private_response']['status'],
        'response_body_bytes': value['private_response']['body_bytes'],
        'response_body_sha256': value['private_response']['body_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('mysql', 'mariadb'), required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report = {'schema': 'recastpress.php-wasix-shared-dependency-check.v0.5', 'backend': args.backend,
        'passed': False, 'commands': [], 'requests': {}}
    database = None
    stage = 'preflight'
    try:
        require(sys.platform == 'linux', 'shared_dependency_linux_only')
        report['sources_before'] = sources()
        generated = ('src/main.rs', 'src/db.rs', 'fixtures/bootstrap.php', 'fixtures/adapter.php')
        report['generated'] = {name: process.identity((HERE / name).read_bytes()) for name in generated}
        bootstrap = context.oldbuild.verify_build()
        report['module_provenance'] = context.managed.verify_build()
        validate = validator()
        env = baseline.build.environment()
        stage = 'build'
        binary = build(report)
        native = context.linux.reference(args.backend)['requests']['home']['observation']
        native_boot = context.original.reference(args.backend)
        owned = {'observations': {}}
        database = context.linux.Database(args.backend, owned)
        with database:
            for mode in ('install', 'read'):
                stage = mode
                result = context.execute(ROOT / bootstrap['executable'], env, database.request(mode), owned, mode)
                context.original.validate(result, mode, native_boot[mode])
            stage = 'fixture'
            database.seed_public_dates()
            database.verify_context_state('before_requests')
            payloads = [database.context_request('home') for _ in range(3)]
            for payload in payloads:
                payload['schema'] = baseline.private.INPUT
            require(len({p['database']['nonce'] for p in payloads}) == 3, 'dependency_distinct_request_nonces')
            fixture(database, payloads[0]['database']['nonce'])
            stage = 'startup'
            with process.Session(binary, env, report, 'shared_dependency') as session:
                ready = session.ready()
                equal(ready['module_sha256'], report['module_provenance']['module']['sha256'])
                report['session_ready'] = ready
                for seq, payload in enumerate(payloads, 1):
                    stage = f'request_{seq}'
                    value = session.request(seq, payload)
                    if value.get('passed') is False:
                        report['request_failed'] = seq
                    report['requests'][str(seq)] = check_request(value, seq, native, validate)
                    if seq == 1:
                        verify_row_lock(database)
                        report['independent_writer_blocked_by_fixture_lock'] = True
                stage = 'session_eof'
                final = session.finish()
                equal(final['dependency'], {'dependency_sql_reads': 1, 'shared_hits': 2, 'charged_bytes': 0})
                equal(final['fixture_guard_released'], True)
                baseline.validate_final({key: value for key, value in final.items()
                    if key not in ('dependency', 'fixture_guard_released')}, 3, True)
                report['session_final'] = final
            result = database.run('verify_owned_dependency_fixture_value', database.client() + [
                '--execute=SELECT HEX(value) FROM rp_wpdb_probe.rp_dependency_fixture WHERE id=1'])
            require(result.stdout.strip() == VALUE.hex().upper().encode('ascii') and not result.stderr,
                'dependency_fixture_value_changed')
            result = database.run('drop_owned_dependency_fixture', database.client() + [
                '--execute=DROP TABLE rp_wpdb_probe.rp_dependency_fixture'])
            require(result.stdout == result.stderr == b'', 'dependency_fixture_cleanup')
            database.verify_context_state('home', value['php_result'])
        stage = 'postflight'
        require(all(owned[key] is True for key in ('container_stopped', 'container_removed',
            'container_normal_exit_verified')), 'dependency_database_cleanup')
        report['database'] = {key: owned[key] for key in ('sql_server_version', 'container_stopped',
            'container_removed', 'container_normal_exit_verified')}
        equal(sources(), report['sources_before'])
        equal({name: process.identity((HERE / name).read_bytes()) for name in generated}, report['generated'])
        equal(process.identity(binary.read_bytes()), report['binary'])
        equal(context.managed.verify_build(), report['module_provenance'])
        equal(context.oldbuild.verify_build(), bootstrap)
        report.update(passed=True, source_reverified_after=True, stable_fixture_row_guard=True,
            live_binlog_connected=False)
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        process.reap_owned_sessions()
        context.owned_probe.reap_owned_probes()
    wire = (json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True, allow_nan=False) + '\n').encode()
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire,
                'shared_dependency_secret_guard')
    if args.record:
        baseline.build.preserve(HERE / f'evidence/linux-{args.backend}.json', wire)
    print('WordPress Wasm shared dependency: ' + ('passed' if report['passed'] else report.get('failure', 'failed')))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
