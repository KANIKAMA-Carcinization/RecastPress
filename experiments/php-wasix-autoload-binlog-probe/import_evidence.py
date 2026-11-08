"""Import source-bound autoload binlog CI evidence without running WordPress."""

import argparse
import ast
import importlib.util
import json
from pathlib import Path
import re
import sys
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREFIX = 'experiments/php-wasix-autoload-binlog-probe/'
WORKFLOW = '.github/workflows/php-wasix-autoload-binlog.yml'
DEPENDENCY_IMPORTER = 'experiments/php-wasix-binlog-dependency-probe/import_evidence.py'
AUTOLOAD_IMPORTER = 'experiments/php-wasix-autoload-cache-probe/import_evidence.py'
RAW_HELPER = 'experiments/php-wasix-wpdb-raw-error-probe/import_evidence.py'
SCHEMA = 'recastpress.php-wasix-autoload-binlog-'
BACKENDS = ('mysql', 'mariadb')
RUNS = {
    42515445130: ('2b18812f13ea34ec4e532cd942d7fe1df99a0c34',
        {'mysql': 128837327395, 'mariadb': 128837327199}),
}
CONTRACT = 'Compare the stock autoload SELECT before and after an observed commit'
UPLOAD = 'Upload only the public autoload binlog report'
STAGES = ('derive.py', 'derive_binlog.py', 'units', 'worker')
LIMIT = 2 * 1024 * 1024


def load_helper(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


autoload = load_helper('rp_autoload_binlog_cache_importer', AUTOLOAD_IMPORTER)
dependency = load_helper('rp_autoload_binlog_dependency_importer', DEPENDENCY_IMPORTER)
plugin, base, old = autoload.plugin, autoload.base, autoload.old
require, exact, sha, read, parse_json, identity, closed = (
    autoload.require, autoload.exact, autoload.sha, autoload.read, autoload.parse_json,
    autoload.identity, autoload.closed)

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
    exact(job['name'], 'Owned PHP-Wasm autoload binlog / ' + backend, 'binlog_api_job_name')
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
        exact(artifact['name'], f"wordpress-wasix-autoload-binlog-{backend}-{api['run_attempt']}", 'binlog_api_artifact_name')
        require(type(artifact['digest']) is str and re.fullmatch('sha256:[a-f0-9]{64}', artifact['digest']),
            'binlog_api_artifact_digest')
    old.privacy_gate(api)


def workflow_gate(api, source):
    workflow = source.read(WORKFLOW).decode('utf-8')
    require('name: Owned PHP-Wasm autoload binlog / ${{ matrix.backend }}' in workflow
        and 'backend: [mysql, mariadb]' in workflow, 'binlog_workflow_job_matrix')
    names = re.findall(r'^    - name: ([^\n]+)$', workflow, re.M)
    require(len(names) == len(set(names)) and names[-2:] == [CONTRACT, UPLOAD], 'binlog_workflow_steps')
    upload = workflow.split(f'    - name: {UPLOAD}\n')[1]
    require('name: wordpress-wasix-autoload-binlog-${{ matrix.backend }}-${{ github.run_attempt }}' in upload
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



SOURCE_INVENTORY = 'da85130e26e30a1db6158c50fd3c6163cb1bd688207bb1bc5372aa0579f16287'
GENERATED = {name: {'bytes': size, 'sha256': digest} for name, size, digest in (
    ('src/main.rs', 78998, '76b5ad1acda922d9fc824ad4830e5151bb5f7591e9b3a07740a027e3c97d1855'),
    ('src/db.rs', 28394, '72a4b0219096b57e0f2d9daaf40003989be487d810c9bd1f5adf08fc3ea0f716'),
    ('src/options_cache.rs', 8575, 'c7fdfaca26d72838c9090a417f89864410023fd33d11078b6f7cd6482965acd2'),
    ('src/binlog_buffer.rs', 19448, '83deca0aac19a48504f444d77b6d1215ad4899e2a1c90cba0e476f3b7225ba27'),
    ('src/monitor_impl.rs', 15542, '92dc637d3d36f60050a0f21f69fbc8955d50873387ad8f2b478edc1a8b0c5748'),
)}
LABELS = ('1', '2', '3', '4')
NATIVE_LABELS = ('home', 'missing', 'missing_repeat', 'missing_repeat')


def source_gate(mapping, source):
    require(type(mapping) is dict and len(mapping) == 262
        and sha(old.canonical(mapping)) == SOURCE_INVENTORY, 'autoload_binlog_source_inventory')
    source.bindings(mapping)
    pins = source.constants(PREFIX + 'derive_binlog.py')
    exact(sha(source.read(dependency.PREFIX + 'src/monitor.rs')), pins['MONITOR_SHA'],
        'autoload_binlog_original_monitor_pin')


def derived_gate(source, path, names, replacements, namespace):
    wire = source.read(path).decode('utf-8')
    functions = [node for node in ast.parse(wire).body if isinstance(node, ast.FunctionDef) and node.name in names]
    exact(len(functions), len(names), 'autoload_binlog_derivation_function_count')
    derived = '\n\n'.join(ast.get_source_segment(wire, node) for node in functions)
    for old_text, new_text in replacements:
        exact(derived.count(old_text), 1, 'autoload_binlog_gate_derivation_boundary')
        derived = derived.replace(old_text, new_text, 1)
    result = dict(namespace)
    exec(compile(derived, '<frozen-autoload-binlog-gates>', 'exec'), result)
    return [result[name] for name in names]


def build_gate(commands, source, passed):
    expected_stdout = b''.join(f'{name} {GENERATED[name]["sha256"]}\n'.encode('ascii')
        for name in ('src/binlog_buffer.rs', 'src/monitor_impl.rs'))
    (gate,) = derived_gate(source, DEPENDENCY_IMPORTER, ('build_gate',),
        (("('src/binlog_buffer.rs ' + generated['src/binlog_buffer.rs']['sha256'] + '\\n').encode()",
            'expected_stdout'),), {**vars(dependency), 'expected_stdout': expected_stdout})
    gate(commands, GENERATED, passed)


def request_gate(requests, source, backend, comparisons_complete):
    session = base.session
    native = session.native_references(source)[backend]['requests']
    anchor = session.PREFIX + f'evidence/ci-42038857146-{backend}/raw/' + session.PREFIX + f'evidence/linux-{backend}.json'
    accepted = parse_json(source.read(anchor))['validations']
    sql = source.constants(autoload.PREFIX + 'run_check.py')['SQL']
    for seq, label in enumerate(NATIVE_LABELS, 1):
        if str(seq) not in requests:
            break
        result = requests[str(seq)]
        exact(result['case'], 'home' if seq == 1 else 'missing', 'autoload_binlog_route')
        expected = native[label]['observation']
        autoload.request_gate({key: item for key, item in result.items() if key != 'case'},
            expected['response'], accepted[label], sql)
        if seq > 1:
            exact(result['autoload_query']['row_count'], expected['autoload_option_count'] + 1,
                'autoload_binlog_one_extra_option')
    if comparisons_complete and '3' in requests:
        exact(requests['3'], requests['2'], 'autoload_binlog_identical_cold_and_hot')
    if comparisons_complete and '4' in requests:
        old_query = requests['2']['autoload_query']
        new_query = requests['4']['autoload_query']
        require(old_query['reply_frame_sha256'] != new_query['reply_frame_sha256'], 'autoload_binlog_changed_reply')
        exact(requests['4'], {**requests['2'], 'autoload_query': {**old_query,
            'reply_frame_sha256': new_query['reply_frame_sha256']}}, 'autoload_binlog_only_reply_digest_changed')


def final_gate(final, source):
    retained = final['options_cache']['retained_reply_bytes']
    old.integer(retained, 1, 128 * 1024)
    exact(final, {'schema': 'recastpress.php-wasix-wp-session-observation.v0.5', 'passed': True,
        'outcome': 'fixed_wp_session_passed', 'module_compilations_total': 1, 'requests_completed': 4,
        'all_requests_cleaned': True, 'request_module_compilations_total': 0, 'private_ipc_only': True,
        'publication_authorized': False, 'native_comparison_performed': False, 'error': None,
        'options_cache': {'sql_reads': 2, 'hits': 1, 'charged_bytes': 0, 'retained_reply_bytes': retained,
            'commit_invalidations': 1}, 'options_guard_released': True, 'binlog_fence': final['binlog_fence']},
        'autoload_binlog_single_compile_two_sql_one_hit')
    fence = final['binlog_fence']
    validator = source.functions(dependency.PREFIX + 'run_check.py', ['verify_fence'])['verify_fence']
    validator(fence, lambda actual, wanted: exact(actual, wanted, 'autoload_binlog_fence'), require)
    require(fence['source_file'] != 'probe-bin.000000' and fence['events'] <= 16
        and fence['through_position'] - fence['from_position'] <= 64 * 1024, 'autoload_binlog_fence_bounds')


def report_gate(value, source, backend, steps):
    common = {'schema', 'backend', 'passed', 'commands', 'requests'}
    progress = {'sources_before', 'generated', 'module_provenance', 'units_passed', 'binary', 'session_ready',
        'session_executions', 'session_final'}
    flags = {'source_reverified_after', 'unchanged_stock_wordpress_guest', 'cold_and_hot_ordered_rpc_exact',
        'only_autoload_reply_digest_changed_after_commit', 'finite_binlog_replay', 'owned_cleanup_completed'}
    success = flags | {'native_comparison_extra_autoload_rows', 'database'}
    failure = {'failure_stage', 'failure_class', 'failure', 'cleanup_failed'}
    require(type(value) is dict and type(value.get('passed')) is bool and common <= set(value),
        'autoload_binlog_report_shape')
    passed = value['passed']
    closed(value, common | progress | success | (failure if not passed else set()),
        'autoload_binlog_report_fields', partial=not passed)
    exact(value['schema'], SCHEMA + 'check.v0.5', 'autoload_binlog_report_schema')
    exact(value['backend'], backend, 'autoload_binlog_report_backend')
    exact(steps.get(CONTRACT), 'success' if passed else 'failure', 'autoload_binlog_report_step')
    old.privacy_gate(value)
    base.record_privacy(value)
    if not passed:
        if 'cleanup_failed' in value:
            exact(value['cleanup_failed'], True, 'autoload_binlog_cleanup_failure_flag')
        if 'failure_stage' in value:
            require(value['failure_stage'] in ('preflight', 'build', 'install', 'read', 'startup', 'request_1',
                'request_2', 'request_3', 'request_4', 'seed_monitor', 'compare_before_commit', 'commit_option',
                'compare_after_commit', 'session_eof', 'postflight')
                and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,127}', value['failure_class']), 'autoload_binlog_failure_stage')
        else:
            require('cleanup_failed' in value and 'failure_class' not in value and 'failure' not in value,
                'autoload_binlog_missing_failure_stage')
        if 'failure' in value:
            require(re.fullmatch('[a-z][a-z0-9_]{0,127}', value['failure']), 'autoload_binlog_failure_code')
    if 'sources_before' in value:
        source_gate(value['sources_before'], source)
    if 'generated' in value:
        exact(value['generated'], GENERATED, 'autoload_binlog_five_generated_identities')
    if 'module_provenance' in value:
        plugin.module_gate(value['module_provenance'], source)
    build_gate(value['commands'], source, passed)
    if 'units_passed' in value:
        exact(value['units_passed'], 66, 'autoload_binlog_locked_rust_units')
    if 'binary' in value:
        base.identity_gate(value['binary'], 256 * 1024 * 1024, 1)
    requests = value['requests']
    closed(requests, LABELS, 'autoload_binlog_request_inventory', partial=not passed)
    exact(set(requests), set(LABELS[:len(requests)]), 'autoload_binlog_request_prefix')
    if requests:
        request_gate(requests, source, backend, passed or value.get('failure_stage') in ('session_eof', 'postflight'))
    session = base.session
    ready, final = value.get('session_ready'), value.get('session_final')
    if 'session_ready' in value:
        module_sha = value['module_provenance']['module']['sha256']
        session.ready_gate(ready, module_sha)
    if 'session_final' in value:
        require(ready is not None and 'session_executions' in value, 'autoload_binlog_final_needs_execution')
        final_gate(final, source)
    if 'session_executions' in value:
        closed(value['session_executions'], ('autoload_binlog',), 'autoload_binlog_one_session')
        row = value['session_executions']['autoload_binlog']
        partial_gate, execution_gate = derived_gate(source, session.PREFIX + 'import_evidence.py',
            ('partial_execution_gate', 'execution_gate'), (("len(row['events'])<=3", "len(row['events'])<=4"),
                ("len(row['events'])==3,'three_session_ack_attestations'", "len(row['events'])==4,'four_session_ack_attestations'")),
            vars(session))
        partial_gate(row, 'autoload_binlog')
        require(len(row['events']) >= len(requests), 'autoload_binlog_request_requires_ack')
        if value.get('owned_cleanup_completed') is True:
            require('owned_pid' not in row or row['owned_reaped'] is True, 'autoload_binlog_child_reaped')
        if final is not None:
            execution_gate(row, ready, final, source, module_sha, True)
            exact(len({event['input']['sha256'] for event in row['events']}), 4, 'autoload_binlog_distinct_inputs')
    if 'database' in value:
        exact(value['database'], {'sql_server_version': '8.4.13' if backend == 'mysql' else '11.8.9-MariaDB-ubu2404-log',
            'container_stopped': True, 'container_removed': True, 'container_normal_exit_verified': True},
            'autoload_binlog_owned_database_cleanup')
    for key in flags & set(value):
        exact(value[key], True, 'autoload_binlog_success_flag')
    if 'native_comparison_extra_autoload_rows' in value:
        exact(value['native_comparison_extra_autoload_rows'], 1, 'autoload_binlog_native_row_delta')
    return {'report_passed': passed, 'validated_native_wordpress_requests': len(requests),
        'options_cache': final['options_cache'] if final else None, 'binlog_fence': final['binlog_fence'] if final else None,
        'failure_stage': value.get('failure_stage')}

def import_evidence(run, backend, verify_only=False):
    directory = ROOT / 'target/php-wasix-autoload-binlog-probe/ci'
    tag = f'{run}-{backend}'
    api_wire = read(directory / f'api-{tag}.json', 128 * 1024)
    api = parse_json(api_wire)
    api_gate(api, backend, run)
    source = base.LocalGitSource(RUNS[run][0])
    session = base.session
    helpers = (AUTOLOAD_IMPORTER, DEPENDENCY_IMPORTER, autoload.SHARED_IMPORTER, dependency.SHARED_IMPORTER,
        RAW_HELPER, autoload.SHARED, plugin.SHARED, base.SHARED, session.SHARED, base.latest.SHARED, session.private.SHARED,
        base.http.SHARED, base.context.SHARED, session.theme.SHARED, base.base.LEGACY)
    for name in dict.fromkeys(helpers):
        exact(source.read(name), read(ROOT / name, LIMIT), 'binlog_frozen_helper_changed')
    steps = workflow_gate(api, source)
    raw, archive = plugin.archive_gate(directory / f'run-{tag}.zip', api, backend)
    summary = None if raw is None else report_gate(parse_json(raw), source, backend, steps)
    passed = api['run_conclusion'] == api['job']['conclusion'] == 'success'
    if api['job']['conclusion'] == 'success':
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
        print('WordPress autoload binlog CI evidence rejected: ' + code, file=sys.stderr)
        sys.exit(1)
    print('WordPress autoload binlog CI evidence: ' + ('verified positive' if passed else 'preserved failure/partial'))
