"""Import the fixed cache-bootstrap CI artifact using local Git and API metadata."""

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
PREFIX = 'experiments/php-wasix-wp-cache-bootstrap-probe/'
WORKFLOW = '.github/workflows/php-wasix-wp-cache-bootstrap.yml'
SHARED = 'experiments/php-wasix-wp-plugin-admission-probe/import_evidence.py'
SCHEMA = 'recastpress.php-wasix-wp-cache-bootstrap-'
BACKENDS = ('mysql', 'mariadb')
ORIGINAL_RUN = 42500397987
WARMUP_RUN = 42502178019
RUNS = {
    ORIGINAL_RUN: ('dcac5bbe1b4f169633cf4ae360ab34ed6bb89775',
        '739d5beb17856ffc3a070d1956735f920111429121e60b78316939d3408b6528',
        {'mysql': 128791682070, 'mariadb': 128791681972}),
    42502105524: ('dc22a8d1e4076affe6a3e9a8550d1dfeb3e05979',
        '0782599215d89fb64849bd3f031a6795ba475a187c079953040a67ef661cd2fc',
        {'mysql': 128796883241, 'mariadb': 128796882973}),
    WARMUP_RUN: ('7419322c7533d6a3bb5b2deef954ec74f77facb7',
        '7bb4a5ddd26a029b2ab2f2392afe13da32c4fd67434e1057d9bdaa29bca05aa5',
        {'mysql': 128797106285, 'mariadb': 128797106470}),
}
CONTRACT = 'Compare a fixed cache shim with an ordinary WordPress request'
UPLOAD = 'Upload only the public cache bootstrap report'
BASELINE = 'recastpress-php-wasix-wp-private-worker-probe'
SHIM = 'recastpress-php-wasix-wp-cache-bootstrap-probe'
LIMIT = 2 * 1024 * 1024
GROUPS = {'rp-cache-multiple': 5, 'rp-cache-integer': 4}
TRIAL = {'shim_late_hook': True, 'external_cache_before': False, 'external_cache_after': False,
    'stock_restored': True, 'stock_sentinel_preserved': True, 'channel_closed': True, 'groups': GROUPS,
    'finish': {'ok': True, 'operations': 9, 'entries': 0, 'charged_bytes': 0}}
GENERATED = {name: {'bytes': size, 'sha256': digest} for name, size, digest in (
    ('src/cache_host.rs', 13636, '5f3efe250214e98d8dbcb1c028008c4c2f1f955531f6fd0de16db05715dfd0a4'),
    ('src/catalog.rs', 9439, '480974c63f7eb704a74be0a9b75011b9ba5c07dbd16ea6d4e211785fefa71eec'),
    ('src/main.rs', 67712, '1c59103786b9e2231d38751191280d9c730b921b21b408a2ccae29bdd449f3d7'),
    ('fixtures/bootstrap.php', 21516, 'b8ee768d168405f19e841954b2008b275d984a62865b6a13c6f527856452c90d'),
    ('fixtures/cache-adapter.php', 8492, '3e64b755e0f101fbe9b11fa5ea6e286f4f1b68a4aa6c0b3460afe2ac2463636b'),
    ('fixtures/cache-trial.php', 2750, '82d4c8a70419306a536c7f267649765753f10460f9723834384de777846c3086'),
    ('fixtures/object-cache.php', 214, 'c3ad7334e940852b74726a15ef9346d45b6d15ac9d23d726eb3fcb46dd92c5f4'),
)}

spec = importlib.util.spec_from_file_location('rp_cache_bootstrap_evidence_helpers', ROOT / SHARED)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
old, base = plugin.old, plugin.base
require, exact, sha, read, parse_json, identity, closed = (
    old.require, old.exact, old.sha, old.read, old.parse_json, old.identity, plugin.closed)


def api_gate(api, backend, run):
    closed(api, ('repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha', 'run_conclusion', 'job', 'artifact'),
        'cache_bootstrap_api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'cache_bootstrap_repository_id')
    exact(api['repository'], old.REPOSITORY, 'cache_bootstrap_repository_name')
    exact(api['run_id'], run, 'cache_bootstrap_api_run')
    commit, _, jobs = RUNS[run]
    exact(api['head_sha'], commit, 'cache_bootstrap_api_source_commit')
    old.integer(api['run_attempt'], 1, 1000)
    require(api['run_conclusion'] in old.CONCLUSIONS, 'cache_bootstrap_api_run_conclusion')
    job = api['job']
    closed(job, ('id', 'name', 'conclusion', 'steps'), 'cache_bootstrap_api_job_fields')
    old.integer(job['id'], 1)
    if api['run_attempt'] == 1:
        exact(job['id'], jobs[backend], 'cache_bootstrap_api_job_id')
    exact(job['name'], 'Owned PHP-Wasm cache bootstrap / ' + backend, 'cache_bootstrap_api_job_name')
    require(job['conclusion'] in old.CONCLUSIONS and type(job['steps']) is list
        and len(job['steps']) <= 64, 'cache_bootstrap_api_job_steps')
    names = set()
    for step in job['steps']:
        closed(step, ('name', 'conclusion'), 'cache_bootstrap_api_step_fields')
        name = step['name']
        require(type(name) is str and 0 < len(name) <= 160 and name not in names
            and step['conclusion'] in old.CONCLUSIONS, 'cache_bootstrap_api_step')
        names.add(name)
    artifact = api['artifact']
    if artifact is None:
        require(job['conclusion'] != 'success', 'cache_bootstrap_success_missing_artifact')
    else:
        closed(artifact, ('id', 'name', 'size_in_bytes', 'digest'), 'cache_bootstrap_api_artifact_fields')
        old.integer(artifact['id'], 1)
        old.integer(artifact['size_in_bytes'], 1, LIMIT)
        exact(artifact['name'], f"wordpress-wasix-cache-bootstrap-{backend}-{api['run_attempt']}",
            'cache_bootstrap_api_artifact_name')
        require(type(artifact['digest']) is str and re.fullmatch('sha256:[a-f0-9]{64}', artifact['digest']),
            'cache_bootstrap_api_artifact_digest')
    old.privacy_gate(api)


def workflow_gate(api, source):
    workflow = source.read(WORKFLOW).decode('utf-8')
    require('name: Owned PHP-Wasm cache bootstrap / ${{ matrix.backend }}' in workflow
        and 'backend: [mysql, mariadb]' in workflow, 'cache_bootstrap_workflow_job_matrix')
    names = re.findall(r'^    - name: ([^\n]+)$', workflow, re.M)
    require(len(names) == len(set(names)) and names[-2:] == [CONTRACT, UPLOAD], 'cache_bootstrap_workflow_steps')
    upload = workflow.split(f'    - name: {UPLOAD}\n')[1]
    require('name: wordpress-wasix-cache-bootstrap-${{ matrix.backend }}-${{ github.run_attempt }}' in upload
        and 'path: ' + PREFIX + 'evidence/linux-${{ matrix.backend }}.json' in upload, 'cache_bootstrap_workflow_artifact')
    steps = {step['name']: step['conclusion'] for step in api['job']['steps']}
    allowed = set(names) | {'Post ' + name for name in names if name.startswith(('Check out ', 'Preseed '))}
    require(set(steps) <= allowed | {'Set up job', 'Complete job'}, 'cache_bootstrap_api_workflow_step_names')
    exact([name for name in steps if name in names], [name for name in names if name in steps],
        'cache_bootstrap_api_step_order')
    if api['artifact'] is not None:
        exact(steps.get(UPLOAD), 'success', 'cache_bootstrap_successful_upload')
    if api['job']['conclusion'] == 'success':
        require(all(steps.get(name) == 'success' for name in names), 'cache_bootstrap_successful_steps')
    if api['run_conclusion'] == 'success':
        exact(api['job']['conclusion'], 'success', 'cache_bootstrap_successful_run_job')
    return steps


def source_gate(mapping, source, run):
    require(type(mapping) is dict and len(mapping) == 237
        and sha(old.canonical(mapping)) == RUNS[run][1], 'cache_bootstrap_frozen_source_inventory')
    source.bindings(mapping)


def execution_gate(row, build=False, passed=True):
    fields = {'owned_pid', 'owned_reaped', 'exit_code', 'stdout', 'stderr'}
    if not build and not passed:
        closed(row, fields | {'phase', 'failure_reason'}, 'cache_bootstrap_partial_execution_fields', partial=True)
        require(fields <= set(row), 'cache_bootstrap_execution_required_fields')
        for key in set(row) - fields:
            require(type(row[key]) is str and re.fullmatch('[a-z][a-z0-9_]{0,80}', row[key]),
                'cache_bootstrap_execution_diagnostic')
    else:
        closed(row, fields, 'cache_bootstrap_execution_fields')
    old.integer(row['owned_pid'], 1)
    exact(row['owned_reaped'], True, 'cache_bootstrap_owned_reaped')
    old.integer(row['exit_code'], -255, 255)
    base.identity_gate(row['stdout'], (16 if build else 4) * 1024 * 1024)
    base.identity_gate(row['stderr'], 65536)
    if passed:
        exact(row['exit_code'], 0, 'cache_bootstrap_execution_passed')
        if not build:
            require(row['stdout']['bytes'] > 0, 'cache_bootstrap_worker_output')
            exact(row['stderr'], identity(b''), 'cache_bootstrap_empty_worker_stderr')


def request_gate(row, native, source):
    closed(row, ('validation', 'response_status', 'response_body', 'headers_count', 'db_operations'),
        'cache_bootstrap_request_fields')
    response = native['response']
    exact(row['response_status'], 200, 'cache_bootstrap_home_status')
    exact(row['response_body'], {'bytes': response['body_bytes'], 'sha256': response['body_sha256']},
        'cache_bootstrap_native_body_identity')
    exact(row['headers_count'], len(response['headers_hex']), 'cache_bootstrap_native_header_count')
    old.integer(row['db_operations'], 1, 4096)
    validation = row['validation']
    fixed = {'fixed_windows_native_public_observations_match': True, 'same_job_native_php_executed': False,
        'private_query_bytes_recomputed_offline': False, 'private_database_results_published': False,
        'context_snapshots_exact': True, 'actual_public_case': 'home', 'response_normalized': False,
        'native_comparison_in_controller_only': True, 'worker_native_comparison_performed': False,
        'private_response_and_contexts_checked_before_evidence': True,
        'legacy_validator_envelope_adapter': {'schema': 'recastpress.php-wasix-wp-private-worker-observation.v0.5',
            'outcome': 'fixed_private_wp_worker_passed', 'phase': 'full_private_response_verified',
            'existing_lifecycle_rpc_and_native_checks_retained': True}}
    counts = validation['operation_counts']
    closed(counts, ('hello', 'query', 'escape', 'close'), 'cache_bootstrap_db_operation_counts')
    for op, n in counts.items():
        old.integer(n, 0 if op == 'escape' else 1, 4096)
    require(counts['hello'] == counts['close'] == 1 and sum(counts.values()) == row['db_operations'],
        'cache_bootstrap_db_operation_balance')
    bounds = {'largest_row_count': (0, 256), 'largest_column_count': (0, 32),
        'largest_reply_frame_bytes': (1, 524288)}
    for key, (low, high) in bounds.items():
        old.integer(validation[key], low, high)
    context_path = 'experiments/php-wasix-wp-request-context-probe/run_check.py'
    theme_path = 'experiments/php-wasix-wp-request-probe/run_check.py'
    bootstrap_path = 'experiments/php-wasix-wp-bootstrap-probe/run_check.py'
    compare_expression = next(node.value for node in source.tree(theme_path).body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'COMPARE' for target in node.targets))
    require(isinstance(compare_expression, ast.BinOp) and isinstance(compare_expression.op, ast.Add)
        and ast.unparse(compare_expression.left) == 'original.COMPARE', 'cache_bootstrap_compare_source')
    compared = (source.constants(bootstrap_path)['COMPARE'] + ast.literal_eval(compare_expression.right)
        + source.constants(context_path)['CONTEXT_FIELDS'])
    exact(validation['compared_fields'], compared, 'cache_bootstrap_native_compare_fields')
    text = source.read(theme_path).decode('utf-8')
    original = ast.get_source_segment(text, next(node for node in source.tree(theme_path).body
        if isinstance(node, ast.FunctionDef) and node.name == 'validate_home'))
    changes = (
        ('recastpress.php-wasix-wp-request-observation.v0.5', 'recastpress.php-wasix-wp-request-context-observation.v0.5'),
        ('fixed_wasix_wp_request_passed', 'fixed_wasix_wp_request_context_passed'),
        ('full_home_verified', 'full_context_verified'),
    )
    derived = original
    for old_text, new_text in changes:
        require(derived.count(old_text) == 1, 'cache_bootstrap_validator_derivation_boundary')
        derived = derived.replace(old_text, new_text, 1)
    exact(validation['validator_derivation'], {'original_function_sha256': sha(original.encode()),
        'derived_function_sha256': sha(derived.encode()), 'changed_literals': len(changes),
        'original_semantic_response_rpc_and_cleanup_checks_retained': True}, 'cache_bootstrap_validator_derivation')
    checked = {'compared_fields', 'operation_counts', 'validator_derivation', *bounds}
    exact({key: value for key, value in validation.items() if key not in checked}, fixed,
        'cache_bootstrap_native_validation')


def report_gate(report, source, backend, steps, run):
    fields = {'schema', 'backend', 'passed', 'executions', 'requests', 'binaries', 'build_commands',
        'sources_before', 'module', 'generated', 'units_passed', 'cache_trial', 'cache_transport', 'database'}
    labels = ('warmup', 'baseline', 'shim') if run == WARMUP_RUN else ('baseline', 'shim')
    success = {'fresh_private_workers': len(labels), 'native_comparison_controller_only': True,
        'source_reverified_after': True}
    if run == ORIGINAL_RUN:
        success['response_context_and_db_rpc_exact'] = True
    else:
        success['response_context_and_db_rpc_exact_except_hello_reply'] = True
    if run == WARMUP_RUN:
        success['stock_warmup_before_comparison'] = True
    require(type(report) is dict and type(report.get('passed')) is bool, 'cache_bootstrap_report_shape')
    passed = report['passed']
    closed(report, fields | (set(success) if passed else {'failure_stage', 'failure_class', 'failure'}),
        'cache_bootstrap_report_fields', partial=not passed)
    exact(report['schema'], SCHEMA + 'check.v0.5', 'cache_bootstrap_report_schema')
    exact(report['backend'], backend, 'cache_bootstrap_report_backend')
    exact(steps.get(CONTRACT), 'success' if passed else 'failure', 'cache_bootstrap_trial_step_conclusion')
    old.privacy_gate(report)
    base.record_privacy(report)
    if not passed:
        require(report['failure_stage'] in ('source_preflight', 'derive', 'build', 'install', 'read', 'seed',
            *labels, 'compare', 'postflight'), 'cache_bootstrap_failure_stage')
        require(type(report['failure_class']) is str and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,127}', report['failure_class']),
            'cache_bootstrap_failure_class')
        if 'failure' in report:
            require(type(report['failure']) is str and re.fullmatch('[a-z][a-z0-9_]{0,127}', report['failure']),
                'cache_bootstrap_failure_code')
    if 'sources_before' in report:
        source_gate(report['sources_before'], source, run)
    if 'module' in report:
        plugin.module_gate(report['module'], source)
    if 'generated' in report:
        exact(report['generated'], GENERATED, 'cache_bootstrap_generated_identities')
    if 'units_passed' in report:
        exact(report['units_passed'], 62, 'cache_bootstrap_unit_count')
    if 'database' in report:
        exact(report['database'], {'sql_server_version': '8.4.13' if backend == 'mysql' else '11.8.9-MariaDB-ubu2404',
            'container_stopped': True, 'container_removed': True, 'container_normal_exit_verified': True},
            'cache_bootstrap_owned_database_cleanup')
    closed(report['binaries'], (BASELINE, SHIM), 'cache_bootstrap_binary_inventory', partial=not passed)
    for row in report['binaries'].values():
        base.identity_gate(row, 256 * 1024 * 1024, 1)
    commands = ('derive_guest.py', 'derive_worker.py', BASELINE, 'units', SHIM)
    closed(report['build_commands'], commands, 'cache_bootstrap_build_inventory', partial=not passed)
    for name, row in report['build_commands'].items():
        execution_gate(row, build=True, passed=passed)
        if row['exit_code'] == 0 and name in commands[:2]:
            stdout = b'' if name == 'derive_guest.py' else b''.join(
                f"{path} {row['sha256']}\n".encode('ascii') for path, row in sorted(GENERATED.items()))
            exact(row['stdout'], identity(stdout), 'cache_bootstrap_derivation_stdout')
            exact(row['stderr'], identity(b''), 'cache_bootstrap_derivation_stderr')
    closed(report['executions'], labels, 'cache_bootstrap_worker_inventory', partial=not passed)
    for label, row in report['executions'].items():
        execution_gate(row, passed=passed or label in report['requests'])
    closed(report['requests'], labels, 'cache_bootstrap_request_inventory', partial=not passed)
    if report['requests']:
        native = base.context.native_references(source)[backend]['requests']['home']['observation']
        for label, row in report['requests'].items():
            require(label in report['executions'], 'cache_bootstrap_request_without_execution')
            request_gate(row, native, source)
    if 'cache_trial' in report or 'cache_transport' in report:
        exact(report['cache_trial'], TRIAL, 'cache_bootstrap_guest_ack')
        transport = report['cache_transport']
        expected = {'operations': 9, 'entries': 0, 'charged_bytes': 0, 'flushes': 0, 'groups': GROUPS,
            'opened_once': True, 'channel_open': False, 'finished': True, 'finish_dispatched': True,
            'pending_bytes': 0, 'unread_response_bytes': 0}
        counts = {'request_bytes', 'response_bytes', 'response_read_bytes'}
        closed(transport, set(expected) | counts, 'cache_bootstrap_channel_fields')
        exact({key: transport[key] for key in expected}, expected, 'cache_bootstrap_closed_channel')
        for key in counts:
            old.integer(transport[key], 1, 65536)
        exact(transport['response_read_bytes'], transport['response_bytes'], 'cache_bootstrap_reply_drain')
    if passed:
        require(len({row['owned_pid'] for row in report['executions'].values()}) == len(labels),
            'cache_bootstrap_distinct_workers')
        baseline, shim = (report['requests'][label] for label in ('baseline', 'shim'))
        if run != ORIGINAL_RUN:
            # A fresh connection ID can change the first reply's size. The
            # controller compares every subsequent raw frame and both totals.
            baseline, shim = ({**row, 'validation': {key: item for key, item in row['validation'].items()
                if key != 'largest_reply_frame_bytes'}} for row in (baseline, shim))
        exact(baseline, shim, 'cache_bootstrap_baseline_shim_comparison')
        exact({key: report[key] for key in success}, success, 'cache_bootstrap_success_flags')
    return {'report_passed': passed, 'source_inputs': len(report.get('sources_before', {})),
        'generated_sources': len(report.get('generated', {})), 'unit_tests': report.get('units_passed'),
        'published_comparison_exact': passed, 'private_operations_or_responses_recomputed': False}


def import_evidence(run, backend, verify_only=False):
    directory = ROOT / 'target/php-wasix-wp-cache-bootstrap-probe/ci'
    tag = f'{run}-{backend}'
    api_wire = read(directory / f'api-{tag}.json', 128 * 1024)
    api = parse_json(api_wire)
    api_gate(api, backend, run)
    source = base.LocalGitSource(RUNS[run][0])
    session = base.session
    helpers = (SHARED, plugin.SHARED, base.SHARED, session.SHARED, base.latest.SHARED, session.private.SHARED,
        base.http.SHARED, base.context.SHARED, session.theme.SHARED, base.base.LEGACY)
    for name in dict.fromkeys(helpers):
        exact(source.read(name), read(ROOT / name, LIMIT), 'cache_bootstrap_frozen_helper_changed')
    steps = workflow_gate(api, source)
    raw, archive = plugin.archive_gate(directory / f'run-{tag}.zip', api, backend)
    summary = None if raw is None else report_gate(parse_json(raw), source, backend, steps, run)
    if api['job']['conclusion'] == 'success':
        require(summary is not None and summary['report_passed'], 'cache_bootstrap_success_evidence')
    passed = api['run_conclusion'] == api['job']['conclusion'] == 'success'
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
        output = ROOT / PREFIX / 'evidence' / f'ci-{tag}'
        proof_wire = (json.dumps(proof, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
        files = [(output / 'api-metadata.json', api_wire), (output / 'provenance.json', proof_wire)]
        if raw is not None:
            files.append((output / 'raw' / ('linux-' + backend + '.json'), raw))
        for path, wire in files:
            require(path.resolve() == path, 'cache_bootstrap_owned_evidence_path')
            if path.exists():
                require(path.is_file() and path.stat().st_size == len(wire) and path.read_bytes() == wire,
                    'cache_bootstrap_immutable_evidence')
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
        print('WordPress Wasm cache bootstrap CI evidence rejected: ' + code, file=sys.stderr)
        sys.exit(1)
    print('WordPress Wasm cache bootstrap CI evidence: ' + ('verified positive' if passed else 'preserved failure/partial'))
