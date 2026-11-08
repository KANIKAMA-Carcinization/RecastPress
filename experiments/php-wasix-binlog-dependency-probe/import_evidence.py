"""Import source-bound binlog dependency CI evidence without running WordPress."""

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
PREFIX = 'experiments/php-wasix-binlog-dependency-probe/'
WORKFLOW = '.github/workflows/php-wasix-binlog-dependency.yml'
SHARED_IMPORTER = 'experiments/php-wasix-shared-dependency-probe/import_evidence.py'
RAW_HELPER = 'experiments/php-wasix-wpdb-raw-error-probe/import_evidence.py'
SCHEMA = 'recastpress.php-wasix-binlog-dependency-'
BACKENDS = ('mysql', 'mariadb')
RUNS = {
    42508028753: ('190cc8391be6c3febea905757771badcb6cfd205',
        {'mysql': 128814802112, 'mariadb': 128814802341}),
}
CONTRACT = 'Compare an observed binlog commit across fresh WordPress requests'
UPLOAD = 'Upload only the public binlog dependency report'
STAGES = ('derive.py', 'derive_binlog.py', 'units', 'worker')
LIMIT = 2 * 1024 * 1024


shared_spec = importlib.util.spec_from_file_location('rp_binlog_dependency_shared', ROOT / SHARED_IMPORTER)
shared = importlib.util.module_from_spec(shared_spec)
shared_spec.loader.exec_module(shared)
SHARED, GitPath = shared.SHARED, shared.GitPath
plugin, base, old = shared.plugin, shared.base, shared.old
require, exact, sha, read, parse_json, identity, closed = (
    shared.require, shared.exact, shared.sha, shared.read, shared.parse_json,
    shared.identity, shared.closed)


def api_gate(api, backend, run):
    closed(api, ('repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha', 'run_conclusion', 'job', 'artifact'),
        'binlog_api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'binlog_repository_id')
    exact(api['repository'], old.REPOSITORY, 'binlog_repository_name')
    exact(api['run_id'], run, 'binlog_api_run')
    commit, jobs = RUNS[run]
    exact(api['head_sha'], commit, 'binlog_api_source_commit')
    old.integer(api['run_attempt'], 1, 1000)
    require(api['run_conclusion'] in old.CONCLUSIONS, 'binlog_api_run_conclusion')
    job = api['job']
    closed(job, ('id', 'name', 'conclusion', 'steps'), 'binlog_api_job_fields')
    old.integer(job['id'], 1)
    if api['run_attempt'] == 1 and backend in jobs:
        exact(job['id'], jobs[backend], 'binlog_api_job_id')
    exact(job['name'], 'Owned PHP-Wasm binlog dependency / ' + backend, 'binlog_api_job_name')
    require(job['conclusion'] in old.CONCLUSIONS and type(job['steps']) is list
        and len(job['steps']) <= 64, 'binlog_api_job_steps')
    names = set()
    for step in job['steps']:
        closed(step, ('name', 'conclusion'), 'binlog_api_step_fields')
        name = step['name']
        require(type(name) is str and 0 < len(name) <= 160 and name not in names
            and step['conclusion'] in old.CONCLUSIONS, 'binlog_api_step')
        names.add(name)
    artifact = api['artifact']
    if artifact is None:
        require(job['conclusion'] != 'success', 'binlog_success_missing_artifact')
    else:
        closed(artifact, ('id', 'name', 'size_in_bytes', 'digest'), 'binlog_api_artifact_fields')
        old.integer(artifact['id'], 1)
        old.integer(artifact['size_in_bytes'], 1, LIMIT)
        exact(artifact['name'], f"wordpress-wasix-binlog-dependency-{backend}-{api['run_attempt']}", 'binlog_api_artifact_name')
        require(type(artifact['digest']) is str and re.fullmatch('sha256:[a-f0-9]{64}', artifact['digest']),
            'binlog_api_artifact_digest')
    old.privacy_gate(api)


def workflow_gate(api, source):
    workflow = source.read(WORKFLOW).decode('utf-8')
    require('name: Owned PHP-Wasm binlog dependency / ${{ matrix.backend }}' in workflow
        and 'backend: [mysql, mariadb]' in workflow, 'binlog_workflow_job_matrix')
    names = re.findall(r'^    - name: ([^\n]+)$', workflow, re.M)
    require(len(names) == len(set(names)) and names[-2:] == [CONTRACT, UPLOAD], 'binlog_workflow_steps')
    upload = workflow.split(f'    - name: {UPLOAD}\n')[1]
    require('name: wordpress-wasix-binlog-dependency-${{ matrix.backend }}-${{ github.run_attempt }}' in upload
        and 'path: ' + PREFIX + 'evidence/linux-${{ matrix.backend }}.json' in upload, 'binlog_workflow_artifact')
    steps = {step['name']: step['conclusion'] for step in api['job']['steps']}
    allowed = set(names) | {'Post ' + name for name in names if name.startswith(('Check out ', 'Preseed '))}
    require(set(steps) <= allowed | {'Set up job', 'Complete job'}, 'binlog_api_workflow_step_names')
    exact([name for name in steps if name in names], [name for name in names if name in steps], 'binlog_api_step_order')
    if api['artifact'] is not None:
        exact(steps.get(UPLOAD), 'success', 'binlog_successful_upload')
    if api['job']['conclusion'] == 'success':
        require(all(steps.get(name) == 'success' for name in names), 'binlog_successful_steps')
    if api['run_conclusion'] == 'success':
        exact(api['job']['conclusion'], 'success', 'binlog_successful_run_job')
    return steps



def controller_source(source):
    root = GitPath(source)
    name = PREFIX + 'run_check.py'
    return source.functions(name, ['controller'], {**source.constants(name), 'sha256': sha256,
        'UPSTREAM': root / shared.PREFIX.rstrip('/')})['controller']()


class InventoryPath(GitPath):
    def iterdir(self):
        return [GitPath(self.source, name) for name in self.source.names(self.name + '/')
            if Path(name).parent.as_posix() == self.name]


def source_inventory(source):
    root = GitPath(source)
    here = InventoryPath(source, PREFIX.rstrip('/'))
    initial = shared.source_inventory(source)
    baseline_inputs = {name: value for name, value in initial.items()
        if not name.startswith(shared.PREFIX) and name != shared.WORKFLOW}
    node = next(node for node in ast.parse(controller_source(source)).body
        if isinstance(node, ast.FunctionDef) and node.name == 'sources')
    namespace = {'ROOT': root, 'HERE': here, 'BASE': root / base.session.PREFIX.rstrip('/'),
        'UPSTREAM': root / shared.PREFIX.rstrip('/'), 'sha': sha,
        'baseline': SimpleNamespace(build=SimpleNamespace(inputs=lambda: baseline_inputs))}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<fixed-binlog-source-inventory>', 'exec'), namespace)
    result = namespace['sources']()
    require(len(result) == 262, 'binlog_source_inventory_count')
    return result


def generated_inventory(source):
    root = GitPath(source)
    guest = shared.PREFIX + 'derive_guest.py'
    actor = shared.PREFIX + 'derive_actor.py'
    worker = shared.PREFIX + 'derive_worker.py'
    upstream = {
        'derive_guest.py': source.functions(guest, ['require', 'pinned', 'replace', 'guest_adapter',
            'guest_bootstrap', 'guest_files'], {**source.constants(guest), 'sha256': sha256,
            'ACCEPTED': root / 'experiments/php-wasix-wp-request-context-probe/fixtures',
            'read': lambda path: path.read_bytes()}),
        'derive_actor.py': source.functions(actor, ['derived_bytes'], {**source.constants(actor), 'sha256': sha256,
            'SOURCE': root / 'experiments/php-wasix-wp-request-probe/src/db.rs'}),
        'derive_worker.py': source.functions(worker, ['original', 'once', 'outputs'], {**source.constants(worker),
            'sha256': sha256, 'BASE': root / base.session.PREFIX.rstrip('/')}),
    }
    derive = PREFIX + 'derive.py'
    constants = source.constants(derive)
    for name in upstream:
        exact(sha(source.read(shared.PREFIX + name)), constants['PINS'][name], 'binlog_upstream_deriver_pin')
    generators = source.functions(derive, ['once', 'manifest', 'worker', 'outputs'],
        {**constants, 'load': upstream.__getitem__})
    exact(generators['manifest'](), source.read(PREFIX + 'Cargo.toml'), 'binlog_derived_manifest')
    outputs = generators['outputs']()
    buffer = PREFIX + 'derive_binlog.py'
    functions = source.functions(buffer, ['require', 'pinned', 'replace', 'region', 'derived_bytes'],
        {**source.constants(buffer), 'sha256': sha256, 'ACCEPTED': root / 'experiments/binlog-probe/src'})
    outputs['src/binlog_buffer.rs'] = functions['derived_bytes']()
    return {name: identity(wire) for name, wire in outputs.items()}


def build_gate(commands, generated, passed):
    require(type(commands) is list and len(commands) <= 4, 'binlog_build_command_count')
    if passed:
        exact(len(commands), 4, 'binlog_complete_build_commands')
    for i, row in enumerate(commands):
        closed(row, ('stage', 'timeout_seconds', 'owned_pid', 'owned_reaped', 'exit_code', 'elapsed_seconds',
            'stdout_sha256', 'stderr_sha256'), 'binlog_build_command_fields')
        exact(row['stage'], STAGES[i], 'binlog_command_order')
        exact(row['timeout_seconds'], 60 if i < 2 else 1200, 'binlog_command_deadline')
        exact(row['owned_reaped'], True, 'binlog_command_reaped')
        old.integer(row['owned_pid'], 1)
        old.integer(row['exit_code'], -2**31, 2**31-1)
        require(type(row['elapsed_seconds']) in (int, float) and 0 <= row['elapsed_seconds'] <= 1300,
            'binlog_command_elapsed')
        for key in ('stdout_sha256', 'stderr_sha256'):
            old.digest(row[key])
        if passed or i + 1 < len(commands):
            exact(row['exit_code'], 0, 'binlog_command_exit')
        if i < 2 and row['exit_code'] == 0:
            stdout = ('src/binlog_buffer.rs ' + generated['src/binlog_buffer.rs']['sha256'] + '\n').encode() if i == 1 else b''
            exact(row['stdout_sha256'], sha(stdout), 'binlog_derive_stdout')
            exact(row['stderr_sha256'], sha(b''), 'binlog_derive_stderr')


def report_gate(value, source, backend, steps):
    common = {'schema', 'backend', 'passed', 'commands', 'requests'}
    completed = {'sources_before', 'generated', 'module_provenance', 'units_passed', 'binary', 'session_ready',
        'session_executions', 'session_final'}
    success = {'database', 'source_reverified_after', 'finite_binlog_replay', 'independently_committed_and_visible', 'fixture_row_lock_used'}
    failure = {'failure_stage', 'failure_class', 'failure', 'request_failed'}
    require(type(value) is dict and type(value.get('passed')) is bool and common <= set(value), 'binlog_report_shape')
    passed = value['passed']
    closed(value, common | completed | success | (failure if not passed else set()),
        'binlog_report_fields', partial=not passed)
    exact(value['schema'], SCHEMA + 'check.v0.5', 'binlog_report_schema')
    exact(value['backend'], backend, 'binlog_report_backend')
    exact(steps.get(CONTRACT), 'success' if passed else 'failure', 'binlog_report_step')
    old.privacy_gate(value)
    base.record_privacy(value)
    if not passed:
        require(value['failure_stage'] in ('preflight', 'build', 'install', 'read', 'fixture', 'startup',
            'request_1', 'request_2', 'request_3', 'source_commit', 'session_eof', 'postflight')
            and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,127}', value['failure_class']), 'binlog_failure_stage_or_class')
        if 'failure' in value:
            require(re.fullmatch('[a-z][a-z0-9_]{0,127}', value['failure']), 'binlog_failure_code')
        if 'request_failed' in value:
            old.integer(value['request_failed'], 1, 3)
            exact(value['failure_stage'], 'request_' + str(value['request_failed']), 'binlog_failed_request')
    if 'sources_before' in value:
        exact(value['sources_before'], source_inventory(source), 'binlog_fixed_sources')
    generated = generated_inventory(source)
    if 'generated' in value:
        exact(value['generated'], generated, 'binlog_five_derived_identities')
    if 'module_provenance' in value:
        plugin.module_gate(value['module_provenance'], source)
    build_gate(value['commands'], generated, passed)
    if 'units_passed' in value:
        exact(value['units_passed'], 68, 'binlog_locked_rust_units')
    if 'binary' in value:
        base.identity_gate(value['binary'], 256 * 1024 * 1024, 1)
    session = base.session
    requests = value['requests']
    labels = ('1', '2', '3')
    closed(requests, labels, 'binlog_request_inventory', partial=not passed)
    exact(set(requests), set(labels[:len(requests)]), 'binlog_request_prefix')
    if requests:
        native = session.native_references(source)[backend]['requests']['home']['observation']['response']
        template = shared.validation_template(source, backend)
        fixture = source.constants(shared.PREFIX + 'run_check.py')['VALUE']
        for label, result in requests.items():
            # The third request is a miss with the committed replacement.
            shared.request_gate(result, 1 if label == '3' else int(label), native, template,
                b'recastpress-shared-updated' if label == '3' else fixture)

    ready, final = value.get('session_ready'), value.get('session_final')
    if 'session_ready' in value:
        module_sha = value['module_provenance']['module']['sha256']
        session.ready_gate(ready, module_sha)
    if 'session_final' in value:
        require(ready is not None and 'session_executions' in value, 'binlog_final_requires_execution')
        exact(final, {'schema': 'recastpress.php-wasix-wp-session-observation.v0.5', 'passed': True,
            'outcome': 'fixed_wp_session_passed', 'module_compilations_total': 1, 'requests_completed': 3,
            'all_requests_cleaned': True, 'request_module_compilations_total': 0, 'private_ipc_only': True,
            'publication_authorized': False, 'native_comparison_performed': False, 'error': None,
            'dependency': {'dependency_sql_reads': 2, 'shared_hits': 1, 'charged_bytes': 0, 'commit_invalidations': 1},
            'fixture_guard_released': True, 'binlog_fence': final['binlog_fence']}, 'binlog_single_compile_two_reads_one_hit')
        fence = source.functions(PREFIX + 'run_check.py', ['verify_fence'])['verify_fence']
        fence(final['binlog_fence'], lambda actual, wanted: exact(actual, wanted, 'binlog_fence'), require)
    if 'session_executions' in value:
        closed(value['session_executions'], ('shared_dependency',), 'binlog_only_one_owned_session')
        execution = value['session_executions']['shared_dependency']
        session.partial_execution_gate(execution, 'shared_dependency')
        if final is not None:
            session.execution_gate(execution, ready, final, source, module_sha, True)
            require(len({row['input']['sha256'] for row in execution['events']}) == 3,
                'binlog_distinct_request_inputs')
    if 'database' in value:
        exact(value['database'], {'sql_server_version': '8.4.13' if backend == 'mysql' else '11.8.9-MariaDB-ubu2404-log',
            'container_stopped': True, 'container_removed': True, 'container_normal_exit_verified': True},
            'binlog_owned_database_cleanup')
    for key in ('source_reverified_after', 'finite_binlog_replay', 'independently_committed_and_visible'):
        if key in value:
            exact(value[key], True, 'binlog_' + key)
    if 'fixture_row_lock_used' in value:
        exact(value['fixture_row_lock_used'], False, 'binlog_fixture_row_lock_scope')
    return {'report_passed': passed, 'validated_native_wordpress_requests': len(requests),
        'dependency': final['dependency'] if final else None,
        'binlog_fence': final['binlog_fence'] if final else None, 'failure_stage': value.get('failure_stage')}


def import_evidence(run, backend, verify_only=False):
    directory = ROOT / 'target/php-wasix-binlog-dependency-probe/ci'
    tag = f'{run}-{backend}'
    api_wire = read(directory / f'api-{tag}.json', 128 * 1024)
    api = parse_json(api_wire)
    api_gate(api, backend, run)
    source = base.LocalGitSource(RUNS[run][0])
    session = base.session
    helpers = (SHARED_IMPORTER, RAW_HELPER, SHARED, plugin.SHARED, base.SHARED, session.SHARED, base.latest.SHARED, session.private.SHARED,
        base.http.SHARED, base.context.SHARED, session.theme.SHARED, base.base.LEGACY)
    for name in dict.fromkeys(helpers):
        exact(source.read(name), read(ROOT / name, LIMIT), 'binlog_frozen_helper_changed')
    steps = workflow_gate(api, source)
    raw, archive = plugin.archive_gate(directory / f'run-{tag}.zip', api, backend)
    summary = None if raw is None else report_gate(parse_json(raw), source, backend, steps)
    passed = api['run_conclusion'] == api['job']['conclusion'] == 'success'
    if passed:
        require(summary is not None and summary['report_passed'], 'binlog_overall_success_evidence')
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
            require(path.resolve() == path, 'binlog_owned_evidence_path')
            if path.exists():
                require(path.is_file() and path.stat().st_size == len(wire) and path.read_bytes() == wire,
                    'binlog_immutable_evidence')
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
        print('WordPress binlog dependency CI evidence rejected: ' + code, file=sys.stderr)
        sys.exit(1)
    print('WordPress binlog dependency CI evidence: ' + ('verified positive' if passed else 'preserved failure/partial'))
