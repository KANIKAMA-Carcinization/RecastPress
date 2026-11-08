"""Import source-bound shared dependency CI evidence without running WordPress."""

import argparse
import ast
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import sys
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREFIX = 'experiments/php-wasix-shared-dependency-probe/'
WORKFLOW = '.github/workflows/php-wasix-shared-dependency.yml'
RAW_HELPER = 'experiments/php-wasix-wpdb-raw-error-probe/import_evidence.py'
SCHEMA = 'recastpress.php-wasix-shared-dependency-'
BACKENDS = ('mysql', 'mariadb')
RUNS = {
    42504791015: ('8a5e892ab39c17d40480bdc212b941a80d565228',
        {'mysql': 128804979395, 'mariadb': 128804979581}),
}
CONTRACT = 'Compare a guarded shared read across fresh WordPress requests'
UPLOAD = 'Upload only the public shared dependency report'
STAGES = ('derive_guest.py', 'derive_actor.py', 'derive_worker.py', 'units', 'worker')
LIMIT = 2 * 1024 * 1024


spec = importlib.util.spec_from_file_location('rp_shared_dependency_offline_base', ROOT / RAW_HELPER)
raw_helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(raw_helper)
SHARED, GitPath = raw_helper.SHARED, raw_helper.GitPath
plugin, base, old = raw_helper.plugin, raw_helper.base, raw_helper.old
require, exact, sha, read, parse_json, identity, closed = (
    raw_helper.require, raw_helper.exact, raw_helper.sha, raw_helper.read, raw_helper.parse_json,
    raw_helper.identity, raw_helper.closed)


def api_gate(api, backend, run):
    closed(api, ('repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha', 'run_conclusion', 'job', 'artifact'),
        'shared_api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'shared_repository_id')
    exact(api['repository'], old.REPOSITORY, 'shared_repository_name')
    exact(api['run_id'], run, 'shared_api_run')
    commit, jobs = RUNS[run]
    exact(api['head_sha'], commit, 'shared_api_source_commit')
    old.integer(api['run_attempt'], 1, 1000)
    require(api['run_conclusion'] in old.CONCLUSIONS, 'shared_api_run_conclusion')
    job = api['job']
    closed(job, ('id', 'name', 'conclusion', 'steps'), 'shared_api_job_fields')
    old.integer(job['id'], 1)
    if api['run_attempt'] == 1 and backend in jobs:
        exact(job['id'], jobs[backend], 'shared_api_job_id')
    exact(job['name'], 'Owned PHP-Wasm shared dependency / ' + backend, 'shared_api_job_name')
    require(job['conclusion'] in old.CONCLUSIONS and type(job['steps']) is list
        and len(job['steps']) <= 64, 'shared_api_job_steps')
    names = set()
    for step in job['steps']:
        closed(step, ('name', 'conclusion'), 'shared_api_step_fields')
        name = step['name']
        require(type(name) is str and 0 < len(name) <= 160 and name not in names
            and step['conclusion'] in old.CONCLUSIONS, 'shared_api_step')
        names.add(name)
    artifact = api['artifact']
    if artifact is None:
        require(job['conclusion'] != 'success', 'shared_success_missing_artifact')
    else:
        closed(artifact, ('id', 'name', 'size_in_bytes', 'digest'), 'shared_api_artifact_fields')
        old.integer(artifact['id'], 1)
        old.integer(artifact['size_in_bytes'], 1, LIMIT)
        exact(artifact['name'], f"wordpress-wasix-shared-dependency-{backend}-{api['run_attempt']}", 'shared_api_artifact_name')
        require(type(artifact['digest']) is str and re.fullmatch('sha256:[a-f0-9]{64}', artifact['digest']),
            'shared_api_artifact_digest')
    old.privacy_gate(api)


def workflow_gate(api, source):
    workflow = source.read(WORKFLOW).decode('utf-8')
    require('name: Owned PHP-Wasm shared dependency / ${{ matrix.backend }}' in workflow
        and 'backend: [mysql, mariadb]' in workflow, 'shared_workflow_job_matrix')
    names = re.findall(r'^    - name: ([^\n]+)$', workflow, re.M)
    require(len(names) == len(set(names)) and names[-2:] == [CONTRACT, UPLOAD], 'shared_workflow_steps')
    upload = workflow.split(f'    - name: {UPLOAD}\n')[1]
    require('name: wordpress-wasix-shared-dependency-${{ matrix.backend }}-${{ github.run_attempt }}' in upload
        and 'path: ' + PREFIX + 'evidence/linux-${{ matrix.backend }}.json' in upload, 'shared_workflow_artifact')
    steps = {step['name']: step['conclusion'] for step in api['job']['steps']}
    allowed = set(names) | {'Post ' + name for name in names if name.startswith(('Check out ', 'Preseed '))}
    require(set(steps) <= allowed | {'Set up job', 'Complete job'}, 'shared_api_workflow_step_names')
    exact([name for name in steps if name in names], [name for name in names if name in steps], 'shared_api_step_order')
    if api['artifact'] is not None:
        exact(steps.get(UPLOAD), 'success', 'shared_successful_upload')
    if api['job']['conclusion'] == 'success':
        require(all(steps.get(name) == 'success' for name in names), 'shared_successful_steps')
    if api['run_conclusion'] == 'success':
        exact(api['job']['conclusion'], 'success', 'shared_successful_run_job')
    return steps



def source_inventory(source):
    session = base.session
    receipt = parse_json(source.read(session.PREFIX + 'evidence/windows-build.json'))
    require(receipt['passed'] is True and len(receipt['input_files']) == 241, 'accepted_session_inventory')
    paths = set(receipt['input_files'])
    paths.update((session.PREFIX + 'run_check.py', session.PREFIX + 'session_process.py', WORKFLOW,
        'crates/recastpress-core/src/resource_budget.rs',
        'experiments/binlog-shared-cache-probe/shared_object_cache.rs', PREFIX + 'src/dependency.rs'))
    paths.update(name for name in source.names(PREFIX)
        if Path(name).parent.as_posix() == PREFIX.rstrip('/')
        and Path(name).suffix in ('.py', '.toml', '.lock') and not name.endswith('/import_evidence.py'))
    require(len(paths) == 251, 'shared_source_inventory_count')
    return {name: sha(source.read(name)) for name in sorted(paths)}


def generated_inventory(source):
    root = GitPath(source)
    guest = PREFIX + 'derive_guest.py'
    fixtures = source.functions(guest, ['require', 'pinned', 'replace', 'guest_adapter',
        'guest_bootstrap', 'guest_files'], {**source.constants(guest), 'sha256': sha256,
        'ACCEPTED': root / 'experiments/php-wasix-wp-request-context-probe/fixtures',
        'read': lambda path: path.read_bytes()})['guest_files']()
    actor = PREFIX + 'derive_actor.py'
    db = source.functions(actor, ['derived_bytes'], {**source.constants(actor), 'sha256': sha256,
        'SOURCE': root / 'experiments/php-wasix-wp-request-probe/src/db.rs'})['derived_bytes']()
    worker = PREFIX + 'derive_worker.py'
    outputs = source.functions(worker, ['original', 'once', 'outputs'], {**source.constants(worker),
        'sha256': sha256, 'BASE': root / base.session.PREFIX.rstrip('/')})['outputs']()
    for name in ('Cargo.toml', 'Cargo.lock'):
        exact(outputs[name], source.read(PREFIX + name), 'shared_derived_locked_manifest')
    return {'src/main.rs': identity(outputs['src/main.rs']), 'src/db.rs': identity(db),
        **{'fixtures/' + name: identity(wire) for name, wire in fixtures.items()}}


def validation_template(source, backend):
    session = base.session
    anchor = session.PREFIX + f'evidence/ci-42038857146-{backend}/raw/' + session.PREFIX + f'evidence/linux-{backend}.json'
    wanted = parse_json(source.read(anchor))['validations']['home']
    checker = source.read('experiments/php-wasix-wp-request-probe/run_check.py').decode()
    original = ast.get_source_segment(checker, next(node for node in ast.parse(checker).body
        if isinstance(node, ast.FunctionDef) and node.name == 'validate_home'))
    derivation = wanted['validator_derivation']
    exact(sha(original.encode()), derivation['original_sha256'], 'shared_original_validator')
    derived = original
    for before, after in (
        ('recastpress.php-wasix-wp-request-observation.v0.5', source.constants(session.PREFIX + 'run_check.py')['SCHEMA']),
        ('fixed_wasix_wp_request_passed', 'fixed_session_request_passed'),
        ('full_home_verified', 'full_private_response_verified'),
        ("value['module_compilations']==1", "value['module_compilations']==0"),
    ):
        require(derived.count(before) == 1, 'shared_validator_literal')
        derived = derived.replace(before, after)
    exact(sha(derived.encode()), derivation['derived_sha256'], 'shared_session_validator')
    operations = "['hello','query','escape','close']"
    require(derived.count(operations) == 1, 'shared_validator_operation_list')
    derived = derived.replace(operations, "['hello','query','escape','close','read_fixture_dependency']")
    wanted['validator_derivation']['derived_sha256'] = sha(derived.encode())
    wanted['operation_counts']['read_fixture_dependency'] = 1
    return wanted


def request_gate(value, sequence, native, template, fixture):
    closed(value, ('cache', 'lifecycle_and_native_comparison', 'response_status', 'response_body_bytes',
        'response_body_sha256'), 'shared_request_fields')
    for key in ('status', 'body_bytes', 'body_sha256'):
        exact(value['response_' + key], native[key], 'shared_native_wordpress_response')
    exact(value['cache'], {'source': 'miss_fill' if sequence == 1 else 'hit', 'bytes': len(fixture),
        'sha256': sha(fixture), 'stock_cache_unchanged': True}, 'shared_cache_request')
    validation = value['lifecycle_and_native_comparison']
    closed(validation, template, 'shared_closed_lifecycle')
    dynamic = {'largest_row_count': 4096, 'largest_column_count': 256, 'largest_reply_frame_bytes': 4 * 1024 * 1024}
    measured = set(dynamic) | {'operation_counts'}
    exact({key: val for key, val in validation.items() if key not in measured},
        {key: val for key, val in template.items() if key not in measured}, 'shared_retained_lifecycle_checks')
    counts = validation['operation_counts']
    closed(counts, template['operation_counts'], 'shared_closed_rpc_operation_counts')
    for key in ('hello', 'close', 'read_fixture_dependency'):
        exact(counts[key], 1, 'shared_single_' + key)
    for key in ('query', 'escape'):
        old.integer(counts[key], 1, 4096)
    require(sum(counts.values()) <= 4096, 'shared_rpc_operation_budget')
    for key, maximum in dynamic.items():
        old.integer(validation[key], 1, maximum)


def build_gate(commands, generated, passed):
    require(type(commands) is list and len(commands) <= 5, 'shared_build_command_count')
    if passed:
        exact(len(commands), 5, 'shared_complete_build_commands')
    for i, row in enumerate(commands):
        closed(row, ('stage', 'timeout_seconds', 'owned_pid', 'owned_reaped', 'exit_code', 'elapsed_seconds',
            'stdout_sha256', 'stderr_sha256'), 'shared_build_command_fields')
        exact(row['stage'], STAGES[i], 'shared_command_order')
        exact(row['timeout_seconds'], 60 if i < 3 else 1200, 'shared_command_deadline')
        exact(row['owned_reaped'], True, 'shared_command_reaped')
        old.integer(row['owned_pid'], 1)
        old.integer(row['exit_code'], -2**31, 2**31-1)
        require(type(row['elapsed_seconds']) in (int, float) and 0 <= row['elapsed_seconds'] <= 1300,
            'shared_command_elapsed')
        for key in ('stdout_sha256', 'stderr_sha256'):
            old.digest(row[key])
        if passed or i + 1 < len(commands):
            exact(row['exit_code'], 0, 'shared_command_exit')
        if i < 3 and row['exit_code'] == 0:
            stdout = (generated['src/db.rs']['sha256'] + '\n').encode() if i == 1 else b''
            exact(row['stdout_sha256'], sha(stdout), 'shared_derive_stdout')
            exact(row['stderr_sha256'], sha(b''), 'shared_derive_stderr')


def report_gate(value, source, backend, steps):
    common = {'schema', 'backend', 'passed', 'commands', 'requests'}
    completed = {'sources_before', 'generated', 'module_provenance', 'units_passed', 'binary', 'session_ready',
        'session_executions', 'session_final', 'independent_writer_blocked_by_fixture_lock'}
    success = {'database', 'source_reverified_after', 'stable_fixture_row_guard', 'live_binlog_connected'}
    failure = {'failure_stage', 'failure_class', 'failure', 'request_failed'}
    require(type(value) is dict and type(value.get('passed')) is bool and common <= set(value), 'shared_report_shape')
    passed = value['passed']
    closed(value, common | completed | success | (failure if not passed else set()),
        'shared_report_fields', partial=not passed)
    exact(value['schema'], SCHEMA + 'check.v0.5', 'shared_report_schema')
    exact(value['backend'], backend, 'shared_report_backend')
    exact(steps.get(CONTRACT), 'success' if passed else 'failure', 'shared_report_step')
    old.privacy_gate(value)
    base.record_privacy(value)
    if not passed:
        require(value['failure_stage'] in ('preflight', 'build', 'install', 'read', 'fixture', 'startup',
            'request_1', 'request_2', 'request_3', 'session_eof', 'postflight')
            and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,127}', value['failure_class']), 'shared_failure_stage_or_class')
        if 'failure' in value:
            require(re.fullmatch('[a-z][a-z0-9_]{0,127}', value['failure']), 'shared_failure_code')
        if 'request_failed' in value:
            old.integer(value['request_failed'], 1, 3)
            exact(value['failure_stage'], 'request_' + str(value['request_failed']), 'shared_failed_request')
    if 'sources_before' in value:
        exact(value['sources_before'], source_inventory(source), 'shared_fixed_sources')
    generated = generated_inventory(source)
    if 'generated' in value:
        exact(value['generated'], generated, 'shared_four_derived_identities')
    if 'module_provenance' in value:
        plugin.module_gate(value['module_provenance'], source)
    build_gate(value['commands'], generated, passed)
    if 'units_passed' in value:
        exact(value['units_passed'], 67, 'shared_locked_rust_units')
    if 'binary' in value:
        base.identity_gate(value['binary'], 256 * 1024 * 1024, 1)
    session = base.session
    requests = value['requests']
    labels = ('1', '2', '3')
    closed(requests, labels, 'shared_request_inventory', partial=not passed)
    exact(set(requests), set(labels[:len(requests)]), 'shared_request_prefix')
    if requests:
        native = session.native_references(source)[backend]['requests']['home']['observation']['response']
        template = validation_template(source, backend)
        fixture = source.constants(PREFIX + 'run_check.py')['VALUE']
        for label, result in requests.items():
            request_gate(result, int(label), native, template, fixture)

    ready, final = value.get('session_ready'), value.get('session_final')
    if 'session_ready' in value:
        module_sha = value['module_provenance']['module']['sha256']
        session.ready_gate(ready, module_sha)
    if 'session_final' in value:
        require(ready is not None and 'session_executions' in value, 'shared_final_requires_execution')
        exact(final, {'schema': 'recastpress.php-wasix-wp-session-observation.v0.5', 'passed': True,
            'outcome': 'fixed_wp_session_passed', 'module_compilations_total': 1, 'requests_completed': 3,
            'all_requests_cleaned': True, 'request_module_compilations_total': 0, 'private_ipc_only': True,
            'publication_authorized': False, 'native_comparison_performed': False, 'error': None,
            'dependency': {'dependency_sql_reads': 1, 'shared_hits': 2, 'charged_bytes': 0},
            'fixture_guard_released': True}, 'shared_single_compile_read_two_hits')
    if 'session_executions' in value:
        closed(value['session_executions'], ('shared_dependency',), 'shared_only_one_owned_session')
        execution = value['session_executions']['shared_dependency']
        session.partial_execution_gate(execution, 'shared_dependency')
        if final is not None:
            session.execution_gate(execution, ready, final, source, module_sha, True)
            require(len({row['input']['sha256'] for row in execution['events']}) == 3,
                'shared_distinct_request_inputs')
    if 'database' in value:
        exact(value['database'], {'sql_server_version': '8.4.13' if backend == 'mysql' else '11.8.9-MariaDB-ubu2404',
            'container_stopped': True, 'container_removed': True, 'container_normal_exit_verified': True},
            'shared_owned_database_cleanup')
    for key in ('independent_writer_blocked_by_fixture_lock', 'source_reverified_after', 'stable_fixture_row_guard'):
        if key in value:
            exact(value[key], True, 'shared_' + key)
    if 'live_binlog_connected' in value:
        exact(value['live_binlog_connected'], False, 'shared_live_binlog_scope')
    return {'report_passed': passed, 'validated_native_wordpress_requests': len(requests),
        'dependency': final['dependency'] if final else None, 'failure_stage': value.get('failure_stage')}


def import_evidence(run, backend, verify_only=False):
    directory = ROOT / 'target/php-wasix-shared-dependency-probe/ci'
    tag = f'{run}-{backend}'
    api_wire = read(directory / f'api-{tag}.json', 128 * 1024)
    api = parse_json(api_wire)
    api_gate(api, backend, run)
    source = base.LocalGitSource(RUNS[run][0])
    session = base.session
    helpers = (RAW_HELPER, SHARED, plugin.SHARED, base.SHARED, session.SHARED, base.latest.SHARED, session.private.SHARED,
        base.http.SHARED, base.context.SHARED, session.theme.SHARED, base.base.LEGACY)
    for name in dict.fromkeys(helpers):
        exact(source.read(name), read(ROOT / name, LIMIT), 'shared_frozen_helper_changed')
    steps = workflow_gate(api, source)
    raw, archive = plugin.archive_gate(directory / f'run-{tag}.zip', api, backend)
    summary = None if raw is None else report_gate(parse_json(raw), source, backend, steps)
    passed = api['run_conclusion'] == api['job']['conclusion'] == 'success'
    if passed:
        require(summary is not None and summary['report_passed'], 'shared_overall_success_evidence')
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
            require(path.resolve() == path, 'shared_owned_evidence_path')
            if path.exists():
                require(path.is_file() and path.stat().st_size == len(wire) and path.read_bytes() == wire,
                    'shared_immutable_evidence')
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
        print('WordPress shared dependency CI evidence rejected: ' + code, file=sys.stderr)
        sys.exit(1)
    print('WordPress shared dependency CI evidence: ' + ('verified positive' if passed else 'preserved failure/partial'))
