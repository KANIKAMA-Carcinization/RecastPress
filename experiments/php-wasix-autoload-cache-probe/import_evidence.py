"""Import source-bound autoload cache CI evidence without running WordPress."""

import argparse
import ast
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREFIX = 'experiments/php-wasix-autoload-cache-probe/'
WORKFLOW = '.github/workflows/php-wasix-autoload-cache.yml'
SHARED_IMPORTER = 'experiments/php-wasix-shared-dependency-probe/import_evidence.py'
RAW_HELPER = 'experiments/php-wasix-wpdb-raw-error-probe/import_evidence.py'
SCHEMA = 'recastpress.php-wasix-autoload-cache-'
BACKENDS = ('mysql', 'mariadb')
RUNS = {
    42511203154: ('76d82a853d0a10d01eed274e504fa74b35e59b0b',
        {'mysql': 128824448238, 'mariadb': 128824448573}),
    42513247456: ('69deba9a5125069c48c80729ae9c318d5c490948',
        {'mysql': 128830634972, 'mariadb': 128830635284}),
}
CONTRACT = 'Compare a guarded stock autoload SELECT across fresh WordPress requests'
UPLOAD = 'Upload only the public autoload cache report'
STAGES = ('derive_actor.py', 'derive_worker.py', 'units', 'worker')
LIMIT = 2 * 1024 * 1024


shared_spec = importlib.util.spec_from_file_location('rp_autoload_cache_shared', ROOT / SHARED_IMPORTER)
shared = importlib.util.module_from_spec(shared_spec)
shared_spec.loader.exec_module(shared)
SHARED, GitPath = shared.SHARED, shared.GitPath
plugin, base, old = shared.plugin, shared.base, shared.old
require, exact, sha, read, parse_json, identity, closed = (
    shared.require, shared.exact, shared.sha, shared.read, shared.parse_json,
    shared.identity, shared.closed)


def api_gate(api, backend, run):
    closed(api, ('repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha', 'run_conclusion', 'job', 'artifact'),
        'autoload_api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'autoload_repository_id')
    exact(api['repository'], old.REPOSITORY, 'autoload_repository_name')
    exact(api['run_id'], run, 'autoload_api_run')
    commit, jobs = RUNS[run]
    exact(api['head_sha'], commit, 'autoload_api_source_commit')
    old.integer(api['run_attempt'], 1, 1000)
    require(api['run_conclusion'] in old.CONCLUSIONS, 'autoload_api_run_conclusion')
    job = api['job']
    closed(job, ('id', 'name', 'conclusion', 'steps'), 'autoload_api_job_fields')
    old.integer(job['id'], 1)
    if api['run_attempt'] == 1 and backend in jobs:
        exact(job['id'], jobs[backend], 'autoload_api_job_id')
    exact(job['name'], 'Owned PHP-Wasm autoload cache / ' + backend, 'autoload_api_job_name')
    require(job['conclusion'] in old.CONCLUSIONS and type(job['steps']) is list
        and len(job['steps']) <= 64, 'autoload_api_job_steps')
    names = set()
    for step in job['steps']:
        closed(step, ('name', 'conclusion'), 'autoload_api_step_fields')
        name = step['name']
        require(type(name) is str and 0 < len(name) <= 160 and name not in names
            and step['conclusion'] in old.CONCLUSIONS, 'autoload_api_step')
        names.add(name)
    artifact = api['artifact']
    if artifact is None:
        require(job['conclusion'] != 'success', 'autoload_success_missing_artifact')
    else:
        closed(artifact, ('id', 'name', 'size_in_bytes', 'digest'), 'autoload_api_artifact_fields')
        old.integer(artifact['id'], 1)
        old.integer(artifact['size_in_bytes'], 1, LIMIT)
        exact(artifact['name'], f"wordpress-wasix-autoload-cache-{backend}-{api['run_attempt']}", 'autoload_api_artifact_name')
        require(type(artifact['digest']) is str and re.fullmatch('sha256:[a-f0-9]{64}', artifact['digest']),
            'autoload_api_artifact_digest')
    old.privacy_gate(api)


def workflow_gate(api, source):
    workflow = source.read(WORKFLOW).decode('utf-8')
    require('name: Owned PHP-Wasm autoload cache / ${{ matrix.backend }}' in workflow
        and 'backend: [mysql, mariadb]' in workflow, 'autoload_workflow_job_matrix')
    names = re.findall(r'^    - name: ([^\n]+)$', workflow, re.M)
    require(len(names) == len(set(names)) and names[-2:] == [CONTRACT, UPLOAD], 'autoload_workflow_steps')
    upload = workflow.split(f'    - name: {UPLOAD}\n')[1]
    require('name: wordpress-wasix-autoload-cache-${{ matrix.backend }}-${{ github.run_attempt }}' in upload
        and 'path: ' + PREFIX + 'evidence/linux-${{ matrix.backend }}.json' in upload, 'autoload_workflow_artifact')
    steps = {step['name']: step['conclusion'] for step in api['job']['steps']}
    allowed = set(names) | {'Post ' + name for name in names if name.startswith(('Check out ', 'Preseed '))}
    require(set(steps) <= allowed | {'Set up job', 'Complete job'}, 'autoload_api_workflow_step_names')
    exact([name for name in steps if name in names], [name for name in names if name in steps], 'autoload_api_step_order')
    if api['artifact'] is not None:
        exact(steps.get(UPLOAD), 'success', 'autoload_successful_upload')
    if api['job']['conclusion'] == 'success':
        require(all(steps.get(name) == 'success' for name in names), 'autoload_successful_steps')
    if api['run_conclusion'] == 'success':
        exact(api['job']['conclusion'], 'success', 'autoload_successful_run_job')
    return steps



def source_inventory(source):
    session = base.session
    receipt = parse_json(source.read(session.PREFIX + 'evidence/windows-build.json'))
    paths = receipt['input_files']
    require(receipt['passed'] is True and len(paths) == 241, 'autoload_session_input_anchor')
    base_paths = {name: sha(source.read(name)) for name in paths}
    root = GitPath(source)
    here = root / PREFIX.rstrip('/')
    stock = root / session.PREFIX.rstrip('/')
    name = PREFIX + 'run_check.py'
    node = next(node.value for node in source.tree(name).body if isinstance(node, ast.Assign)
        and any(isinstance(key, ast.Name) and key.id == 'PINS' for key in node.targets))
    require(isinstance(node, ast.Dict) and len(node.keys) == 6, 'autoload_controller_pin_fields')
    pins = eval(compile(ast.Expression(node), '<fixed-autoload-controller-pins>', 'eval'),
        {'__builtins__': {}, 'HERE': here, 'BASE': stock})
    for path, digest in pins.items():
        exact(sha(path.read_bytes()), digest, 'autoload_controller_input_pin')
    functions = source.functions(name, ['sources'], {**source.constants(name), 'PINS': pins,
        'ROOT': root, 'HERE': here, 'Path': lambda name: root / name, '__file__': name,
        'baseline': SimpleNamespace(build=SimpleNamespace(inputs=lambda: base_paths))})
    result = functions['sources']()
    require(len(result) == 249, 'autoload_source_inventory_count')
    return result


def generated_inventory(source):
    root = GitPath(source)
    actor = PREFIX + 'derive_actor.py'
    functions = source.functions(actor, ['pinned', 'prefixes', 'derived_bytes'],
        {**source.constants(actor), 'sha256': sha256,
        'SOURCE': root / 'experiments/php-wasix-wp-request-probe/src/db.rs',
        'EVIDENCE_ROOT': root / 'experiments/php-wasix-wp-private-worker-probe/evidence'})
    worker = PREFIX + 'derive_worker.py'
    worker_functions = source.functions(worker, ['replace_once', 'manifest', 'worker'],
        {**source.constants(worker), 'sha256': sha256, 'STOCK': root / base.session.PREFIX.rstrip('/'),
        'SHARED': root / shared.PREFIX.rstrip('/')})
    exact(worker_functions['manifest'](), source.read(PREFIX + 'Cargo.toml'), 'autoload_manifest_derivation')
    return {'src/db.rs': identity(functions['derived_bytes']()), 'src/main.rs': identity(worker_functions['worker']())}

def build_gate(commands, generated, passed):
    require(type(commands) is list and len(commands) <= 4, 'autoload_build_command_count')
    if passed:
        exact(len(commands), 4, 'autoload_complete_build_commands')
    for i, row in enumerate(commands):
        closed(row, ('stage', 'timeout_seconds', 'owned_pid', 'owned_reaped', 'exit_code', 'elapsed_seconds',
            'stdout_sha256', 'stderr_sha256'), 'autoload_build_command_fields')
        exact(row['stage'], STAGES[i], 'autoload_command_order')
        exact(row['timeout_seconds'], 60 if i < 2 else 1200, 'autoload_command_deadline')
        exact(row['owned_reaped'], True, 'autoload_command_reaped')
        old.integer(row['owned_pid'], 1)
        old.integer(row['exit_code'], -2**31, 2**31-1)
        require(type(row['elapsed_seconds']) in (int, float) and 0 <= row['elapsed_seconds'] <= 1300,
            'autoload_command_elapsed')
        for key in ('stdout_sha256', 'stderr_sha256'):
            old.digest(row[key])
        if passed or i + 1 < len(commands):
            exact(row['exit_code'], 0, 'autoload_command_exit')
        if i < 2 and row['exit_code'] == 0:
            stdout = (generated['src/db.rs']['sha256'] + '\n').encode() if i == 0 else b''
            exact(row['stdout_sha256'], sha(stdout), 'autoload_derive_stdout')
            exact(row['stderr_sha256'], sha(b''), 'autoload_derive_stderr')


LABELS = ('home', 'missing', 'missing_repeat')


def request_gate(value, native, accepted, sql):
    closed(value, ('lifecycle_and_native_comparison', 'autoload_query', 'response_status', 'response_body_sha256'),
        'autoload_request_fields')
    exact(value['response_status'], native['status'], 'autoload_native_status')
    exact(value['response_body_sha256'], native['body_sha256'], 'autoload_native_body')
    validation = value['lifecycle_and_native_comparison']
    closed(validation, accepted, 'autoload_lifecycle_fields')
    measured = {'operation_counts', 'largest_row_count', 'largest_column_count', 'largest_reply_frame_bytes'}
    exact({key: val for key, val in validation.items() if key not in measured},
        {key: val for key, val in accepted.items() if key not in measured}, 'autoload_stock_lifecycle_checks')
    counts = validation['operation_counts']
    closed(counts, ('hello', 'query', 'escape', 'close'), 'autoload_stock_rpc_operations')
    for key in ('hello', 'close'):
        exact(counts[key], 1, 'autoload_stock_rpc_boundary')
    for key in ('query', 'escape'):
        old.integer(counts[key], 1, 4096)
    require(sum(counts.values()) <= 4096, 'autoload_stock_rpc_budget')
    for key, maximum in [('largest_row_count', 4096), ('largest_column_count', 256),
            ('largest_reply_frame_bytes', 4 * 1024 * 1024)]:
        old.integer(validation[key], 1, maximum)
    query = value['autoload_query']
    closed(query, ('seq', 'op', 'request_data_bytes', 'request_data_sha256', 'row_count', 'column_count',
        'reply_frame_bytes', 'reply_frame_sha256'), 'autoload_query_fields')
    for key, expected in [('seq', 10), ('op', 'query'), ('request_data_bytes', len(sql)),
            ('request_data_sha256', sha(sql)), ('column_count', 2)]:
        exact(query[key], expected, 'autoload_exact_query_' + key)
    old.integer(query['row_count'], 1, 256)
    old.integer(query['reply_frame_bytes'], 1, 512 * 1024)
    old.digest(query['reply_frame_sha256'])


def report_gate(value, source, backend, steps):
    common = {'schema', 'backend', 'passed', 'commands', 'requests'}
    progress = {'sources_before', 'generated', 'module_provenance', 'units_passed', 'binary', 'session_ready',
        'session_executions', 'session_final', 'independent_options_writer_blocked', 'cold_and_hot_ordered_rpc_exact'}
    success = {'database', 'source_reverified_after', 'cold_and_hot_reply_bytes_exact',
        'unchanged_stock_wordpress_guest', 'options_table_read_lock', 'owned_cleanup_completed'}
    failure = {'failure_stage', 'failure_class', 'failure', 'cleanup_failed'}
    diagnostics_enabled = source.commit == RUNS[42513247456][0]
    if diagnostics_enabled:
        failure.add('session_failure_diagnostic')
    require(type(value) is dict and type(value.get('passed')) is bool and common <= set(value), 'autoload_report_shape')
    passed = value['passed']
    closed(value, common | progress | success | (failure if not passed else set()), 'autoload_report_fields', partial=not passed)
    exact(value['schema'], SCHEMA + 'check.v0.5', 'autoload_report_schema')
    exact(value['backend'], backend, 'autoload_report_backend')
    exact(steps.get(CONTRACT), 'success' if passed else 'failure', 'autoload_report_step')
    old.privacy_gate(value)
    base.record_privacy(value)
    if not passed:
        if 'cleanup_failed' in value:
            exact(value['cleanup_failed'], True, 'autoload_cleanup_failure_flag')
        if 'failure_stage' in value:
            require(value['failure_stage'] in ('preflight', 'build', 'install', 'read', 'startup',
                'request_home', 'request_missing', 'request_missing_repeat', 'seed_guard', 'blocked_writer',
                'session_eof', 'postflight') and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,127}', value['failure_class']),
                'autoload_failure_stage_or_class')
        else:
            require('cleanup_failed' in value and 'failure_class' not in value and 'failure' not in value,
                'autoload_missing_failure_stage')
        if 'failure' in value:
            require(re.fullmatch('[a-z][a-z0-9_]{0,127}', value['failure']), 'autoload_failure_code')
        if 'session_failure_diagnostic' in value:
            exact((value.get('failure_stage'), value.get('failure')), ('session_eof', 'session_expected_exit'),
                'autoload_session_failure_diagnostic_scope')
            diagnostic_gate(value['session_failure_diagnostic'])
    if 'sources_before' in value:
        exact(value['sources_before'], source_inventory(source), 'autoload_fixed_sources')
    generated = generated_inventory(source)
    if 'generated' in value:
        exact(value['generated'], generated, 'autoload_two_derived_identities')
    if 'module_provenance' in value:
        plugin.module_gate(value['module_provenance'], source)
    build_gate(value['commands'], generated, passed)
    if 'units_passed' in value:
        exact(value['units_passed'], 66 if diagnostics_enabled else 65, 'autoload_locked_rust_units')
    if 'binary' in value:
        base.identity_gate(value['binary'], 256 * 1024 * 1024, 1)

    session = base.session
    requests = value['requests']
    closed(requests, LABELS, 'autoload_request_inventory', partial=not passed)
    exact(set(requests), set(LABELS[:len(requests)]), 'autoload_request_prefix')
    if requests:
        native = session.native_references(source)[backend]['requests']
        anchor = session.PREFIX + f'evidence/ci-42038857146-{backend}/raw/' + session.PREFIX + f'evidence/linux-{backend}.json'
        accepted = parse_json(source.read(anchor))['validations']
        sql = source.constants(PREFIX + 'run_check.py')['SQL']
        for label, result in requests.items():
            request_gate(result, native[label]['observation']['response'], accepted[label], sql)
    if 'cold_and_hot_ordered_rpc_exact' in value or 'cold_and_hot_reply_bytes_exact' in value:
        require(len(requests) == 3, 'autoload_ordered_comparison_requires_requests')
        exact(requests['missing'], requests['missing_repeat'], 'autoload_exact_cold_and_hot_public_summaries')

    ready, final = value.get('session_ready'), value.get('session_final')
    if 'session_ready' in value:
        module_sha = value['module_provenance']['module']['sha256']
        session.ready_gate(ready, module_sha)
    if 'session_final' in value:
        require(ready is not None and 'session_executions' in value, 'autoload_final_requires_execution')
        max_retained = source.constants(PREFIX + 'run_check.py')['MAX_RETAINED_BYTES']
        retained = final['options_cache']['retained_reply_bytes']
        old.integer(retained, 1, max_retained)
        exact(final, {'schema': 'recastpress.php-wasix-wp-session-observation.v0.5', 'passed': True,
            'outcome': 'fixed_wp_session_passed', 'module_compilations_total': 1, 'requests_completed': 3,
            'all_requests_cleaned': True, 'request_module_compilations_total': 0, 'private_ipc_only': True,
            'publication_authorized': False, 'native_comparison_performed': False, 'error': None,
            'options_cache': {'sql_reads': 1, 'hits': 1, 'charged_bytes': 0, 'retained_reply_bytes': retained},
            'options_guard_released': True}, 'autoload_single_compile_read_hit')
    if 'session_executions' in value:
        closed(value['session_executions'], ('autoload_cache',), 'autoload_only_one_owned_session')
        execution = value['session_executions']['autoload_cache']
        session.partial_execution_gate(execution, 'autoload_cache')
        if final is not None:
            session.execution_gate(execution, ready, final, source, module_sha, True)
            require(len({row['input']['sha256'] for row in execution['events']}) == 3,
                'autoload_distinct_request_inputs')
    if 'database' in value:
        exact(value['database'], {'sql_server_version': '8.4.13' if backend == 'mysql' else '11.8.9-MariaDB-ubu2404',
            'container_stopped': True, 'container_removed': True, 'container_normal_exit_verified': True},
            'autoload_owned_database_cleanup')
    flags = (success - {'database'}) | {'independent_options_writer_blocked', 'cold_and_hot_ordered_rpc_exact'}
    for key in flags & set(value):
        exact(value[key], True, 'autoload_' + key)
    return {'report_passed': passed, 'validated_native_wordpress_requests': len(requests),
        'options_cache': final['options_cache'] if final else None, 'failure_stage': value.get('failure_stage'),
        'session_failure_diagnostic': value.get('session_failure_diagnostic'),
        'complete_normalized_rpc_comparison_attested_by_controller': value.get('cold_and_hot_ordered_rpc_exact', False)}


def diagnostic_gate(value):
    closed(value, ('options_guard_released', 'cache', 'candidate'), 'autoload_closed_failure_diagnostic')
    require(type(value['options_guard_released']) is bool, 'autoload_guard_release_type')
    stats = value['cache']
    if stats is not None:
        limits = {'sql_reads': 2, 'hits': 2, 'charged_bytes': 128 * 1024, 'retained_reply_bytes': 128 * 1024}
        closed(stats, limits, 'autoload_closed_failure_counters')
        for key, limit in limits.items():
            old.integer(stats[key], 0, limit)
    candidate = value['candidate']
    if candidate is not None:
        closed(candidate, ('before_status', 'reply_status', 'admission'), 'autoload_closed_failure_candidate')
        old.integer(candidate['before_status'], 0, 65535)
        if candidate['reply_status'] is not None:
            old.integer(candidate['reply_status'], 0, 65535)
        require(candidate['admission'] in ('reply_profile', 'encoding_budget', 'reservation', 'stored'),
            'autoload_failure_admission_label')

def import_evidence(run, backend, verify_only=False):
    directory = ROOT / 'target/php-wasix-autoload-cache-probe/ci'
    tag = f'{run}-{backend}'
    api_wire = read(directory / f'api-{tag}.json', 128 * 1024)
    api = parse_json(api_wire)
    api_gate(api, backend, run)
    source = base.LocalGitSource(RUNS[run][0])
    session = base.session
    helpers = (SHARED_IMPORTER, RAW_HELPER, SHARED, plugin.SHARED, base.SHARED, session.SHARED, base.latest.SHARED, session.private.SHARED,
        base.http.SHARED, base.context.SHARED, session.theme.SHARED, base.base.LEGACY)
    for name in dict.fromkeys(helpers):
        exact(source.read(name), read(ROOT / name, LIMIT), 'autoload_frozen_helper_changed')
    steps = workflow_gate(api, source)
    raw, archive = plugin.archive_gate(directory / f'run-{tag}.zip', api, backend)
    summary = None if raw is None else report_gate(parse_json(raw), source, backend, steps)
    passed = api['run_conclusion'] == api['job']['conclusion'] == 'success'
    if passed:
        require(summary is not None and summary['report_passed'], 'autoload_overall_success_evidence')
    proof = {'schema': SCHEMA + 'ci-import.v0.5', 'repository_id': api['repository_id'], 'repository': api['repository'],
        'run_id': run, 'run_attempt': api['run_attempt'], 'backend': backend, 'source_commit': source.commit,
        'run_conclusion': api['run_conclusion'], 'job': api['job'], 'artifact': api['artifact'],
        'api_metadata_sha256': sha(api_wire), 'archive_identity': archive, 'raw_json': None if raw is None else identity(raw),
        'original_git_sources': {name: identity(wire) for name, wire in sorted(source.cache.items())},
        'verifier_sha256': sha(read(Path(__file__).resolve(), LIMIT)), 'verification': {'overall_positive': passed,
            'report': summary, 'raw_verdicts_rewritten': False, 'private_worker_or_binary_bytes_reconstructed': False,
            'network_or_runtime_used_by_importer': False}}
    old.privacy_gate(proof)
    if not verify_only:
        output = HERE / 'evidence' / f'ci-{tag}'
        proof_wire = (json.dumps(proof, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
        files = [(output / 'api-metadata.json', api_wire), (output / 'provenance.json', proof_wire)]
        if raw is not None:
            files.append((output / 'raw' / ('linux-' + backend + '.json'), raw))
        for path, wire in files:
            require(path.resolve() == path, 'autoload_owned_evidence_path')
            if path.exists():
                require(path.is_file() and path.stat().st_size == len(wire) and path.read_bytes() == wire,
                    'autoload_immutable_evidence')
        for path, wire in files:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(wire)
    return passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=int, choices=RUNS, required=True)
    parser.add_argument('--backend', choices=BACKENDS, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    try:
        passed = import_evidence(args.run, args.backend, args.verify_only)
    except (KeyError, OSError, RuntimeError, StopIteration, TypeError, ValueError, zipfile.BadZipFile) as error:
        code = str(error) if type(error) is ValueError and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('WordPress autoload cache CI evidence rejected: ' + code, file=sys.stderr)
        sys.exit(1)
    print('WordPress autoload cache CI evidence: ' + ('verified positive' if passed else 'preserved failure/partial'))
