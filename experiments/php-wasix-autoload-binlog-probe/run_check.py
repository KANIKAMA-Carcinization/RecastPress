"""Exercise one ordinary WordPress autoload reply across an observed commit."""

import argparse
import ast
from hashlib import sha256
import importlib.util
import inspect
import json
from pathlib import Path
import re
import sys
import textwrap

from database import Database, NEW, SOURCE as OWNER_SOURCE


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AUTOLOAD = HERE.parent / 'php-wasix-autoload-cache-probe/run_check.py'
BINLOG = HERE.parent / 'php-wasix-binlog-dependency-probe/run_check.py'
PINS = {
    AUTOLOAD: '5802a5b35386dfba1a5d6eafae9cc4b71e68c84dbf494d2d73fd97c3017ac5e4',
    BINLOG: '8e0f6c97e3fda28fa41613f5cdf1d0c8ac6040dbf89de1cda69a9513a8c91b6f',
}
NAME = 'recastpress-php-wasix-autoload-binlog-probe'
DERIVATIONS = ('derive.py', 'derive_binlog.py')
GENERATED = ('src/main.rs', 'src/db.rs', 'src/options_cache.rs', 'src/binlog_buffer.rs', 'src/monitor_impl.rs')


def pinned(path):
    wire = path.read_bytes()
    if path.resolve() != path or sha256(wire).hexdigest() != PINS[path]:
        raise ValueError('autoload_binlog_pinned_controller_changed')
    return wire.decode('utf-8')


pinned(AUTOLOAD)
spec = importlib.util.spec_from_file_location('rp_frozen_autoload_controller', AUTOLOAD)
autoload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(autoload)
baseline, context, process = autoload.baseline, autoload.context, autoload.process
require, equal, sha = autoload.require, autoload.equal, autoload.sha


def four_requests():
    source = textwrap.dedent(inspect.getsource(process.Session.request))
    require(source.count('1<=seq<=3') == 1, 'autoload_binlog_parent_session_source')
    namespace = dict(vars(process))
    exec(compile(source.replace('1<=seq<=3', '1<=seq<=4'), '<four-stock-session-requests>', 'exec'), namespace)
    return namespace['request']


class Session(process.Session):
    request = four_requests()


def fence_validator():
    source = pinned(BINLOG)
    functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == 'verify_fence']
    require(len(functions) == 1, 'autoload_binlog_fence_validator')
    namespace = {}
    exec(compile(ast.get_source_segment(source, functions[0]), '<stock-binlog-fence-validator>', 'exec'), namespace)
    return namespace['verify_fence']


verify_fence = fence_validator()


def sources():
    inventory = dict(baseline.build.inputs())
    paths = [*PINS, *autoload.PINS, OWNER_SOURCE, Path(__file__).resolve(), HERE / 'database.py',
        *(HERE / name for name in DERIVATIONS), HERE / 'Cargo.toml', HERE / 'Cargo.lock',
        HERE / 'src/guard.rs', HERE / 'src/monitor.rs', AUTOLOAD.parent / 'src/options_cache.rs',
        AUTOLOAD.parent / 'derive_actor.py', AUTOLOAD.parent / 'derive_worker.py', BINLOG.parent / 'derive_binlog.py',
        ROOT / '.github/workflows/php-wasix-autoload-binlog.yml', ROOT / 'crates/recastpress-core/src/resource_budget.rs',
        ROOT / 'crates/recastpress-db/src/change_buffer.rs', ROOT / 'crates/recastpress-db/src/budgeted_change_buffer.rs',
        ROOT / 'experiments/binlog-probe/src/main.rs', ROOT / 'experiments/binlog-probe/src/buffer_adapter.rs']
    inventory.update({path.relative_to(ROOT).as_posix(): sha(path.read_bytes()) for path in paths})
    return dict(sorted(inventory.items()))


def generated():
    return {name: process.identity((HERE / name).read_bytes()) for name in GENERATED}


def build(report):
    namespace = dict(vars(autoload), HERE=HERE, NAME=NAME, DERIVATIONS=DERIVATIONS)
    exec(compile(inspect.getsource(autoload.build), '<stock-autoload-worker-build>', 'exec'), namespace)
    return namespace['build'](report)


def native_with_marker(native):
    count = native['autoload_option_count']
    require(type(count) is int and 0 < count < 256, 'autoload_binlog_native_count')
    return {**native, 'autoload_option_count': count + 1}


def compare_updated(cold, updated):
    before, after = cold[9], updated[9]
    equal(before['seq'], 10)
    equal(after['seq'], 10)
    require(before['reply_frame_sha256'] != after['reply_frame_sha256'], 'autoload_binlog_reply_unchanged_after_commit')
    replaced = {**before, 'reply_frame_sha256': after['reply_frame_sha256']}
    equal(updated, [*cold[:9], replaced, *cold[10:]])


def check_final(final):
    cache = final['options_cache']
    retained = cache['retained_reply_bytes']
    require(type(retained) is int and 0 < retained <= autoload.MAX_RETAINED_BYTES, 'autoload_binlog_retained_reply_bound')
    equal(cache, {'sql_reads': 2, 'hits': 1, 'charged_bytes': 0,
        'retained_reply_bytes': retained, 'commit_invalidations': 1})
    equal(final['options_guard_released'], True)
    verify_fence(final['binlog_fence'], equal, require)
    baseline.validate_final({key: value for key, value in final.items()
        if key not in ('options_cache', 'options_guard_released', 'binlog_fence')}, 4, True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('mysql', 'mariadb'), required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report = {'schema': 'recastpress.php-wasix-autoload-binlog-check.v0.5', 'backend': args.backend,
        'passed': False, 'commands': [], 'requests': {}}
    database = None
    stage = 'preflight'
    try:
        require(sys.platform == 'linux', 'autoload_binlog_linux_only')
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
        database = Database(args.backend, owned)
        with database:
            for mode in ('install', 'read'):
                stage = mode
                value = context.execute(ROOT / bootstrap['executable'], env, database.request(mode), owned, mode)
                context.original.validate(value, mode, native_boot[mode])
            database.seed_public_dates()
            database.verify_context_state('before_requests')
            requests = [(case, label, database.context_request(case)) for case, label in (
                ('home', 'home'), ('missing', 'missing'), ('missing', 'missing_repeat'), ('missing', 'missing_repeat'))]
            for _, _, payload in requests:
                payload['schema'] = baseline.private.INPUT
            require(len({payload['database']['nonce'] for _, _, payload in requests}) == 4, 'autoload_binlog_distinct_nonces')
            stage = 'startup'
            with Session(binary, env, report, 'autoload_binlog') as session:
                ready = session.ready()
                equal(ready['module_sha256'], report['module_provenance']['module']['sha256'])
                report['session_ready'] = ready
                traces = []
                for seq, (case, label, payload) in enumerate(requests, 1):
                    stage = f'request_{seq}'
                    value = session.request(seq, payload)
                    expected = native['requests'][label]['observation']
                    if seq > 1:
                        expected = native_with_marker(expected)
                    checked = baseline.validate_request(value, case, expected)
                    observation = autoload.query_observation(value)
                    if seq > 1:
                        equal(observation['row_count'], expected['autoload_option_count'])
                    traces.append(autoload.comparable_operations(value))
                    database.verify_context_state(label, value['php_result'])
                    report['requests'][str(seq)] = {'case': case, 'lifecycle_and_native_comparison': checked,
                        'autoload_query': observation, 'response_status': value['private_response']['status'],
                        'response_body_sha256': value['private_response']['body_sha256']}
                    if seq == 1:
                        stage = 'seed_monitor'
                        database.seed_dependency(requests[1][2]['database']['nonce'])
                        session.send(database.monitor_config())
                    elif seq == 3:
                        stage = 'compare_before_commit'
                        equal(traces[1], traces[2])
                        stage = 'commit_option'
                        database.commit_dependency()
                stage = 'compare_after_commit'
                compare_updated(traces[1], traces[3])
                stage = 'session_eof'
                final = session.finish()
                check_final(final)
                report['session_final'] = final
            database.verify_dependency_value('after_session', NEW)
            database.verify_context_state('missing_repeat', value['php_result'])
        stage = 'postflight'
        require(all(owned[key] is True for key in ('container_stopped', 'container_removed',
            'container_normal_exit_verified', 'dependency_binlog_profile_verified', 'dependency_fixture_seeded',
            'dependency_update_committed_and_visible')), 'autoload_binlog_owned_database_completion')
        report['database'] = {key: owned[key] for key in ('sql_server_version', 'container_stopped',
            'container_removed', 'container_normal_exit_verified')}
        equal(sources(), report['sources_before'])
        equal(generated(), report['generated'])
        equal(process.identity(binary.read_bytes()), report['binary'])
        equal(context.managed.verify_build(), report['module_provenance'])
        equal(context.oldbuild.verify_build(), bootstrap)
        report.update(passed=True, source_reverified_after=True, unchanged_stock_wordpress_guest=True,
            native_comparison_extra_autoload_rows=1, cold_and_hot_ordered_rpc_exact=True,
            only_autoload_reply_digest_changed_after_commit=True, finite_binlog_replay=True)
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        try:
            process.reap_owned_sessions()
            context.owned_probe.reap_owned_probes()
            require(all(row.get('owned_pid') is None or row.get('owned_reaped') is True
                for row in report.get('session_executions', {}).values()), 'autoload_binlog_owned_session_cleanup')
            report['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt):
            report.update(passed=False, cleanup_failed=True)
    wire = (json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True, allow_nan=False) + '\n').encode()
    private = database.private_values() if database is not None else []
    require(all(secret.encode() not in wire and secret.encode().hex().encode() not in wire for secret in private),
        'autoload_binlog_secret_guard')
    require(len(wire) <= 12 * 1024 * 1024, 'autoload_binlog_report_bound')
    if args.record:
        baseline.build.preserve(HERE / f'evidence/linux-{args.backend}.json', wire)
    print('WordPress Wasm autoload binlog: ' + ('passed' if report['passed'] else report.get('failure', 'failed')))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
