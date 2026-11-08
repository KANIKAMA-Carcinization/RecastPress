"""Preserve source-bound PHP-Wasm plugin CI evidence without replaying it."""

import argparse
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import stat
import sys
from types import SimpleNamespace
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREFIX = 'experiments/php-wasix-wp-plugin-admission-probe/'
NATIVE = 'experiments/wp-plugin-admission-probe/'
PRIVATE = 'experiments/php-wasix-wp-private-worker-probe/'
CONTEXT = 'experiments/php-wasix-wp-request-context-probe/'
WORKFLOW = '.github/workflows/php-wasix-wp-plugin-admission.yml'
SHARED = 'experiments/php-wasix-wp-session-http-probe/import_evidence.py'
SCHEMA = 'recastpress.php-wasix-wp-plugin-admission-'
RUN = 42492425833
COMMIT = '2f62ff76df8e91a74100b7757691cacf2c3dff87'
JOBS = {'mysql': 128767498469, 'mariadb': 128767498532}
CONTRACT = 'Compare fixed plugin admission, activation and fresh WordPress requests'
UPLOAD = 'Upload only the public plugin trial report'
LABELS = ('inactive', 'activation', 'active', 'active_repeat')
BUILD_STAGES = ('guest_derivation', 'worker_derivation', 'worker_units', 'worker_build')
SUCCESS_FLAGS = {'independent_activation_state_verified', 'native_comparison_controller_only',
    'one_footer_marker_added', 'fresh_repeat_exact', 'owned_database_normal_cleanup'}
LIMIT = 2 * 1024 * 1024


spec = importlib.util.spec_from_file_location('rp_plugin_offline_evidence_base', ROOT / SHARED)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
old, session = base.old, base.session
require, exact, sha, read, parse_json, identity = old.require, old.exact, old.sha, old.read, old.parse_json, old.identity


def closed(value, fields, code, partial=False):
    require(type(value) is dict and (set(value) <= set(fields) if partial else set(value) == set(fields)), code)


def order_prefix(value, labels, code):
    require(set(value) == set(labels[:len(value)]), code)


def api_gate(api, backend):
    closed(api, ('repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha', 'run_conclusion', 'job', 'artifact'),
        'plugin_api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'plugin_api_repository_id')
    exact(api['repository'], old.REPOSITORY, 'plugin_api_repository')
    exact(api['run_id'], RUN, 'plugin_api_run')
    exact(api['head_sha'], COMMIT, 'plugin_api_commit')
    old.integer(api['run_attempt'], 1, 1000)
    require(api['run_conclusion'] in old.CONCLUSIONS, 'plugin_api_run_conclusion')
    job = api['job']
    closed(job, ('id', 'name', 'conclusion', 'steps'), 'plugin_api_job_fields')
    old.integer(job['id'], 1)
    if api['run_attempt'] == 1:
        exact(job['id'], JOBS[backend], 'plugin_api_job_id')
    exact(job['name'], 'Owned PHP-Wasm plugin trial / ' + backend, 'plugin_api_job_name')
    require(job['conclusion'] in old.CONCLUSIONS and type(job['steps']) is list
        and len(job['steps']) <= 64, 'plugin_api_job_steps')
    seen = set()
    for step in job['steps']:
        closed(step, ('name', 'conclusion'), 'plugin_api_step_fields')
        name = step['name']
        require(type(name) is str and 0 < len(name) <= 160 and name not in seen
            and step['conclusion'] in old.CONCLUSIONS, 'plugin_api_step')
        seen.add(name)
    artifact = api['artifact']
    if artifact is None:
        require(job['conclusion'] != 'success', 'plugin_successful_job_missing_artifact')
    else:
        closed(artifact, ('id', 'name', 'size_in_bytes', 'digest'), 'plugin_api_artifact_fields')
        old.integer(artifact['id'], 1)
        old.integer(artifact['size_in_bytes'], 1, LIMIT)
        exact(artifact['name'], f"wordpress-wasix-plugin-admission-{backend}-{api['run_attempt']}",
            'plugin_api_artifact_name')
        require(type(artifact['digest']) is str and re.fullmatch('sha256:[a-f0-9]{64}', artifact['digest']),
            'plugin_api_artifact_digest')
    old.privacy_gate(api)


def workflow_gate(api, source):
    workflow = source.read(WORKFLOW).decode('utf-8')
    require('name: Owned PHP-Wasm plugin trial / ${{ matrix.backend }}' in workflow
        and 'backend: [mysql, mariadb]' in workflow, 'plugin_workflow_job_matrix')
    names = re.findall(r'^    - name: ([^\n]+)$', workflow, re.M)
    require(len(names) == len(set(names)) and names[-2:] == [CONTRACT, UPLOAD], 'plugin_workflow_steps')
    upload = workflow.split(f'    - name: {UPLOAD}\n')[1]
    require('name: wordpress-wasix-plugin-admission-${{ matrix.backend }}-${{ github.run_attempt }}' in upload
        and 'path: ' + PREFIX + 'evidence/linux-${{ matrix.backend }}.json' in upload, 'plugin_workflow_artifact')
    steps = {step['name']: step['conclusion'] for step in api['job']['steps']}
    allowed = set(names) | {'Post ' + name for name in names if name.startswith(('Check out ', 'Preseed '))}
    require(set(steps) <= allowed | {'Set up job', 'Complete job'}, 'plugin_api_workflow_step_names')
    exact([name for name in steps if name in names], [name for name in names if name in steps], 'plugin_api_step_order')
    if api['artifact'] is not None:
        exact(steps.get(UPLOAD), 'success', 'plugin_api_successful_upload')
    if api['job']['conclusion'] == 'success':
        require(all(steps.get(name) == 'success' for name in names), 'plugin_successful_steps')
    if api['run_conclusion'] == 'success':
        exact(api['job']['conclusion'], 'success', 'plugin_successful_run_job')
    return steps


def archive_gate(path, api, backend):
    artifact = api['artifact']
    if artifact is None:
        require(not path.exists() and not path.is_symlink(), 'plugin_unbound_archive')
        return None, None
    wire = read(path, LIMIT)
    observed = identity(wire)
    exact(observed, {'bytes': artifact['size_in_bytes'], 'sha256': artifact['digest'][7:]}, 'plugin_archive_identity')
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1, 'plugin_single_zip_member')
        entry = entries[0]
        kind = stat.S_IFMT(entry.external_attr >> 16)
        require(entry.orig_filename == entry.filename == 'linux-' + backend + '.json'
            and not entry.is_dir() and entry.flag_bits & 1 == 0 and kind in (0, stat.S_IFREG), 'plugin_zip_member')
        old.integer(entry.file_size, 1, LIMIT)
        with archive.open(entry) as stream:
            raw = stream.read(entry.file_size + 1)
        require(len(raw) == entry.file_size, 'plugin_zip_member_size')
    return raw, observed


class GitPath(session.SourcePath):
    def __truediv__(self, name):
        return GitPath(self.source, Path(self.name, name).as_posix())


def expected_sources(source, constants):
    accepted = parse_json(source.read(PRIVATE + 'evidence/windows-build.json'))
    require(accepted['passed'] is True and len(accepted['input_files']) == 219, 'plugin_accepted_worker_anchor')
    inputs = dict(accepted['input_files'])
    source.bindings(inputs)
    paths = [PREFIX + name for name in ('Cargo.toml', 'Cargo.lock', 'derive_guest.py', 'derive_worker.py', 'run_check.py')]
    paths += [WORKFLOW, NATIVE + 'admission.py']
    paths += [name for name in source.names(NATIVE + 'fixtures/') if name.endswith('.php')]
    paths += [NATIVE + 'evidence/windows-' + name + '-native.json' for name in constants['NATIVE_SHA']]
    require(len(paths) == 14 and not set(paths) & set(inputs), 'plugin_input_extension_inventory')
    inputs.update({name: sha(source.read(name)) for name in paths})
    return inputs, accepted


def generated_sources(source):
    root = GitPath(source)
    guest_name = PREFIX + 'derive_guest.py'
    guest_constants = source.constants(guest_name)
    failure_node = next(node.value for node in source.tree(guest_name).body if isinstance(node, ast.Assign)
        and any(isinstance(key, ast.Name) and key.id == 'FAILURES' for key in node.targets))
    def pinned_guest(path, expected):
        wire = path.read_bytes()
        exact(sha(wire), expected, 'plugin_guest_pinned_source')
        return wire

    guest = source.functions(guest_name, ['replace', 'failures', 'plugin_inventory', 'activation_or_render', 'guest_files'],
        {**guest_constants, 'FAILURES': ast.literal_eval(failure_node),
            'CONTEXT': root / (CONTEXT + 'fixtures'), 'PLUGIN': root / (NATIVE + 'fixtures/rp-admission-benign.php'),
            'PLUGIN_PATH': 'wp-content/plugins/rp-admission-benign/rp-admission-benign.php',
            'PLUGIN_FILE': 'rp-admission-benign/rp-admission-benign.php', 'pinned': pinned_guest})
    worker_name = PREFIX + 'derive_worker.py'
    worker_constants = source.constants(worker_name)

    def pinned_worker(path, name):
        wire = path.read_bytes()
        exact(sha(wire), worker_constants['INPUTS'][name], 'plugin_worker_pinned_source')
        return wire

    worker = source.functions(worker_name, ['replace', 'worker', 'catalog', 'outputs'],
        {**worker_constants, 'HERE': root / PREFIX.rstrip('/'), 'ACCEPTED': root / PRIVATE.rstrip('/'),
            'CATALOG': root / 'experiments/php-wasix-wp-request-probe/src/catalog.rs',
            'pinned': pinned_worker, 'derive_guest': SimpleNamespace(guest_files=guest['guest_files']),
            'sha256': hashlib.sha256})
    files = worker['outputs']()
    require(len(files) == 10, 'plugin_ten_generated_sources')
    return files


def references(source, constants, backend):
    name = NATIVE + 'evidence/windows-' + backend + '-native.json'
    wire = source.read(name)
    exact(sha(wire), constants['NATIVE_SHA'][backend], 'plugin_native_pin')
    value = parse_json(wire)
    exact(value['backend'], backend, 'plugin_native_backend')
    for key in ('passed', 'activation_via_wordpress', 'fresh_response_repeat_exact',
            'refused_and_held_unmounted_while_active_plugins_empty', 'normal_admin_shutdown_verified'):
        exact(value[key], True, 'plugin_native_reference_scope')
    closed(value['requests'], LABELS, 'plugin_native_request_inventory')
    return value


def build_commands_gate(rows, generated, passed):
    require(type(rows) is list and len(rows) <= 4, 'plugin_build_command_inventory')
    if passed:
        exact(len(rows), 4, 'plugin_four_build_commands')
    for index, row in enumerate(rows):
        closed(row, ('stage', 'owned_pid', 'owned_reaped', 'exit_code', 'stdout', 'stderr'), 'plugin_build_command_fields')
        exact(row['stage'], BUILD_STAGES[index], 'plugin_build_command_stage')
        old.integer(row['owned_pid'], 1)
        exact(row['owned_reaped'], True, 'plugin_owned_build_reap')
        old.integer(row['exit_code'], -255, 255)
        baseline_identity(row['stdout'], 16 * 1024 * 1024)
        baseline_identity(row['stderr'], 65536)
        if index + 1 < len(rows) or passed:
            exact(row['exit_code'], 0, 'plugin_successful_build_step')
        if row['exit_code'] == 0 and index < 2:
            stdout = b'' if index == 0 else b''.join((sha(generated[name]) + '  ' + name + '\n').encode('ascii')
                for name in sorted(generated))
            exact(row['stdout'], identity(stdout), 'plugin_derivation_stdout')
            exact(row['stderr'], identity(b''), 'plugin_derivation_stderr')


baseline_identity = base.identity_gate


def module_gate(value, source):
    closed(value, ('build_receipt_sha256', 'module', 'builder_provenance'), 'plugin_module_provenance_fields')
    old.digest(value['build_receipt_sha256'])
    module = value['module']
    closed(module, ('path', 'bytes', 'sha256', 'wasm_header_verified', 'executed'), 'plugin_module_fields')
    old.integer(module['bytes'], 8, 128 * 1024 * 1024)
    old.digest(module['sha256'])
    exact(module['path'], 'target/php-wasix-managed-access-probe/php-fiber-cgi.wasm', 'plugin_module_path')
    exact(module['wasm_header_verified'], True, 'plugin_module_wasm_header')
    exact(module['executed'], False, 'plugin_builder_execution_scope')
    expected = {'build_receipt_sha256': value['build_receipt_sha256'], 'variant': 'nts-fresh-wasi-zend-fibers-managed-access-v1',
        'module_bytes': module['bytes'], 'module_sha256': module['sha256'], 'input_files': old.managed_inputs(source),
        'scope': 'same-job NTS CGI candidate with managed-access adaptation; not product ABI or full compatibility'}
    files = {'manifest_sha256': old.MANAGED + 'manifest.json',
        'patch_sha256': old.FIBER + 'patches/0001-nts-fresh-wasi-zend-fibers.patch',
        'cgi_patch_sha256': old.CGI + 'patches/0001-wasi-disable-cgi-prefork.patch',
        'assembly_sha256': old.FIBER + 'fixtures/zend_wasix_fiber.S',
        'managed_access_patch_sha256': old.MANAGED + 'patches/0001-wasi-managed-access.patch',
        'managed_access_header_sha256': old.MANAGED + 'fixtures/recastpress_fs_access.h',
        'source_profile_sha256': old.MANAGED + 'source_profile.py'}
    expected.update({key: sha(source.read(name)) for key, name in files.items()})
    observed = value['builder_provenance']
    extras = {'toolchain_receipt_sha256', 'raw_module_sha256'}
    closed(observed, set(expected) | extras, 'plugin_module_builder_fields')
    for key in extras:
        old.digest(observed[key])
    exact({key: observed[key] for key in expected}, expected, 'plugin_module_builder_source_bindings')
    source.bindings(expected['input_files'])


def request_gate(value, expected, label):
    stage = 'activate' if label == 'activation' else 'render'
    old.integer(value['operations'], 1, 4096)
    template = {'stage': stage, 'active_before': expected['active_before'], 'active_after': expected['active_after'],
        'operations': value['operations'], 'module_compilations': 1, 'lifecycle_verified': True}
    if stage == 'activate':
        template['activation_succeeded'] = True
    else:
        native = expected['validation']
        template['response'] = {'status': native['inner_status'], 'body': {'bytes': native['inner_body_bytes'],
            'sha256': native['inner_body_sha256']}, 'marker_count': expected['marker_count'],
            'ordered_headers_match_native': True, 'public_context_matches_native': True}
    exact(value, template, 'plugin_request_native_and_lifecycle')


def database_gate(value, source, backend):
    pin = parse_json(source.read('experiments/php-wasix-wpdb-probe/container-images.json'))['images'][backend]
    exact(value, {'image': pin['repository'] + '@' + pin['platform_digest'], 'image_config_digest': pin['image_config_digest'],
        'sql_server_version': '8.4.13' if backend == 'mysql' else '11.8.9-MariaDB-ubu2404',
        'container_stopped': True, 'container_removed': True, 'container_normal_exit_verified': True}, 'plugin_owned_database_cleanup')


def report_gate(value, source, backend, steps):
    fields = {'schema', 'backend', 'passed', 'requests', 'sources_before', 'build_commands', 'worker_units_passed',
        'worker_build', 'generated_inputs', 'module_provenance', 'preactivation_decisions', 'independent_active_plugins',
        'executions', 'database', 'sources_after'}
    require(type(value) is dict and type(value.get('passed')) is bool, 'plugin_report_schema')
    passed = value['passed']
    closed(value, fields | (SUCCESS_FLAGS if passed else {'failure_stage', 'failure_class', 'failure'}),
        'plugin_report_fields', partial=not passed)
    exact(value['schema'], SCHEMA + 'check.v0.5', 'plugin_report_schema')
    exact(value['backend'], backend, 'plugin_report_backend')
    exact(steps.get(CONTRACT), 'success' if passed else 'failure', 'plugin_trial_step_conclusion')
    if not passed:
        require(value['failure_stage'] in ('build', 'install', 'read', *LABELS, 'postflight')
            and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,127}', value['failure_class']), 'plugin_failure_class')
        if 'failure' in value:
            require(re.fullmatch('[a-z][a-z0-9_]{0,127}', value['failure']), 'plugin_failure_code')
    old.privacy_gate(value)
    base.record_privacy(value)
    constants = source.constants(PREFIX + 'run_check.py')
    inputs, accepted = expected_sources(source, constants)
    generated = generated_sources(source)
    native = references(source, constants, backend)
    for name in ('sources_before', 'sources_after'):
        if name in value:
            if not passed and name == 'sources_after' and value.get('failure') == 'plugin_source_changed':
                closed(value[name], inputs, 'plugin_changed_source_map_paths')
                for digest in value[name].values():
                    old.digest(digest)
            else:
                exact(value[name], inputs, 'plugin_runtime_source_bindings')
    if 'generated_inputs' in value:
        exact(value['generated_inputs'], {name: identity(wire) for name, wire in generated.items()}, 'plugin_generated_identities')
    if 'build_commands' in value:
        build_commands_gate(value['build_commands'], generated, passed)
    if 'worker_units_passed' in value:
        exact(value['worker_units_passed'], len(accepted['units']['tests']) + 1, 'plugin_worker_unit_inventory')
    if 'worker_build' in value:
        worker = value['worker_build']
        closed(worker, ('executable', 'executable_identity', 'locked_offline'), 'plugin_build_artifact_fields')
        exact(worker['executable'], 'target/php-wasix-wp-bootstrap-probe/cargo-target/release/' + constants['NAME'],
            'plugin_fixed_worker_artifact')
        exact(worker['locked_offline'], True, 'plugin_offline_build')
        baseline_identity(worker['executable_identity'], 256 * 1024 * 1024, 1)
    if 'module_provenance' in value:
        module_gate(value['module_provenance'], source)
    if 'preactivation_decisions' in value:
        expected = native['preactivation_decisions']
        exact(value['preactivation_decisions'], [expected['rp-admission-benign'], expected['rp-admission-collision'],
            expected['rp-admission-driver-provider']], 'plugin_pinned_preactivation_decisions')
    requests = value['requests']
    closed(requests, LABELS, 'plugin_request_labels', partial=not passed)
    order_prefix(requests, LABELS, 'plugin_request_prefix')
    executions = value.get('executions', {})
    closed(executions, LABELS, 'plugin_execution_labels', partial=not passed)
    order_prefix(executions, LABELS, 'plugin_execution_prefix')
    require(set(requests) <= set(executions), 'plugin_request_owned_execution')
    for label, execution in executions.items():
        fields = {'owned_pid', 'owned_reaped', 'exit_code', 'stdout', 'stderr'}
        if 'diagnostics' in execution:
            require(not passed and label not in requests, 'plugin_private_failure_diagnostics_scope')
            fields.add('diagnostics')
            diagnostics = execution['diagnostics']
            closed(diagnostics, ('phase', 'failure_reason'), 'plugin_failure_diagnostics_fields', partial=True)
            if 'phase' in diagnostics:
                require(diagnostics['phase'] in ('input', 'module_compile', 'cgi_execution',
                    'full_private_activation_verified', 'full_private_response_verified'), 'plugin_failure_worker_phase')
            if 'failure_reason' in diagnostics:
                require(re.fullmatch('[a-z_]{1,80}', diagnostics['failure_reason']), 'plugin_failure_worker_code')
        closed(execution, fields, 'plugin_owned_execution_fields')
        old.integer(execution['owned_pid'], 1)
        old.integer(execution['exit_code'], -255, 255)
        exact(execution['owned_reaped'], True, 'plugin_owned_request_reap')
        baseline_identity(execution['stdout'], 16 * 1024 * 1024)
        baseline_identity(execution['stderr'], 65536)
        if label in requests:
            exact(execution['exit_code'], 0, 'plugin_owned_request_exit')
            baseline_identity(execution['stdout'], 4 * 1024 * 1024, 1)
            exact(execution['stderr'], identity(b''), 'plugin_owned_request_stderr')
            request_gate(requests[label], native['requests'][label], label)
    if 'independent_active_plugins' in value:
        states = value['independent_active_plugins']
        closed(states, ('before', *LABELS), 'plugin_independent_sql_labels', partial=not passed)
        order_prefix(states, ('before', *LABELS), 'plugin_independent_sql_prefix')
        require(set(states) - {'before'} <= set(requests), 'plugin_independent_sql_request')
        for label, observed in states.items():
            exact(observed, [] if label == 'before' else native['requests'][label]['active_after'],
                'plugin_independent_sql_native_state')
    if 'database' in value:
        database_gate(value['database'], source, backend)
    if passed:
        exact(len({row['owned_pid'] for row in executions.values()}), 4, 'plugin_distinct_request_processes')
        for flag in SUCCESS_FLAGS:
            exact(value[flag], True, 'plugin_success_' + flag)
    return {'report_passed': passed, 'source_inputs': len(inputs), 'generated_sources': len(generated),
        'verified_requests': [label for label in LABELS if label in requests],
        'failure_stage': value.get('failure_stage'), 'failure': value.get('failure')}


def import_evidence(backend, verify_only=False):
    directory = ROOT / 'target/php-wasix-wp-plugin-admission-probe/ci'
    tag = f'{RUN}-{backend}'
    api_wire = read(directory / f'api-{tag}.json', 128 * 1024)
    api = parse_json(api_wire)
    api_gate(api, backend)
    source = base.LocalGitSource(COMMIT)
    helpers = (SHARED, base.SHARED, session.SHARED, base.latest.SHARED, session.private.SHARED,
        base.http.SHARED, base.context.SHARED, session.theme.SHARED, base.base.LEGACY)
    for name in dict.fromkeys(helpers):
        exact(source.read(name), read(ROOT / name, LIMIT), 'plugin_frozen_helper_changed')
    steps = workflow_gate(api, source)
    raw, archive_identity = archive_gate(directory / f'run-{tag}.zip', api, backend)
    summary = None if raw is None else report_gate(parse_json(raw), source, backend, steps)
    passed = api['run_conclusion'] == api['job']['conclusion'] == 'success'
    if passed:
        require(summary is not None and summary['report_passed'], 'plugin_overall_success_evidence')
    proof = {'schema': SCHEMA + 'ci-import.v0.5', 'repository_id': api['repository_id'], 'repository': api['repository'],
        'run_id': RUN, 'run_attempt': api['run_attempt'], 'backend': backend, 'source_commit': COMMIT,
        'run_conclusion': api['run_conclusion'], 'job': api['job'], 'artifact': api['artifact'],
        'api_metadata_sha256': sha(api_wire), 'archive_identity': archive_identity,
        'raw_json': None if raw is None else identity(raw), 'original_git_sources': {name: identity(wire)
            for name, wire in sorted(source.cache.items())}, 'verifier_sha256': sha(read(Path(__file__).resolve(), LIMIT)),
        'verification': {'overall_positive': passed, 'report': summary, 'raw_verdicts_rewritten': False,
            'private_workers_or_binary_bytes_reconstructed': False, 'network_or_runtime_used_by_importer': False}}
    old.privacy_gate(proof)
    if not verify_only:
        output = HERE / 'evidence' / f'ci-{tag}'
        proof_wire = (json.dumps(proof, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
        files = [(output / 'api-metadata.json', api_wire), (output / 'provenance.json', proof_wire)]
        if raw is not None:
            files.append((output / 'raw' / ('linux-' + backend + '.json'), raw))
        for path, wire in files:
            require(path.resolve() == path, 'plugin_owned_evidence_path')
            if path.exists():
                require(path.is_file() and path.stat().st_size == len(wire) and path.read_bytes() == wire,
                    'plugin_immutable_evidence')
        for path, wire in files:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(wire)
    return passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=int, choices=[RUN], required=True)
    parser.add_argument('--backend', choices=JOBS, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    try:
        passed = import_evidence(args.backend, args.verify_only)
    except (KeyError, OSError, RuntimeError, StopIteration, TypeError, ValueError, zipfile.BadZipFile) as error:
        code = str(error) if type(error) is ValueError and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('WordPress plugin CI evidence rejected: ' + code, file=sys.stderr)
        sys.exit(1)
    print('WordPress plugin CI evidence: ' + ('verified positive' if passed else 'preserved failure/partial'))
