"""Bind public session HTTP CI records to the local Git commit and saved API/ZIP."""

import argparse
import ast
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tomllib
from types import SimpleNamespace
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREFIX = 'experiments/php-wasix-wp-session-http-probe/'
SESSION = 'experiments/php-wasix-wp-session-probe/'
SHARED = SESSION + 'import_evidence.py'
WORKFLOW = '.github/workflows/php-wasix-wp-session-http.yml'
SCHEMA = 'recastpress.php-wasix-wp-session-http-'
LABELS = ('home', 'missing', 'missing_repeat')
LIMIT = 24 * 1024 * 1024

spec = importlib.util.spec_from_file_location('rp_session_http_offline_session', ROOT / SHARED)
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)
latest, http, context, base, old = session.latest, session.http, session.context, session.base, session.old
require, sha, identity, exact, canonical = session.require, session.sha, session.identity, session.exact, session.canonical
read, parse_json, SourcePath = session.read, session.parse_json, session.SourcePath


class LocalGitSource(old.GitSource):
    def command(self, args, maximum):
        environment = dict(os.environ, GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='', GIT_TERMINAL_PROMPT='0')
        result = subprocess.run(['git', *args], cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
        require(result.returncode == 0 and not result.stderr and len(result.stdout) <= maximum,
            'bounded_offline_git_read_failed')
        return result.stdout


def member_map(backend):
    names = session.member_map(backend)
    names['session-build.json'] = names.pop('host-build.json')
    names.update({'run.json': PREFIX + f'evidence/linux-{backend}.json',
        'host-build.json': PREFIX + 'evidence/linux-build.json',
        'ci-stages.json': PREFIX + 'evidence/ci-stages.json'})
    return names


def api_gate(api, run, backend):
    exact(set(api), {'repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha',
        'run_conclusion', 'job', 'artifact'}, 'api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'api_repository_id')
    exact(api['repository'], old.REPOSITORY, 'api_repository')
    old.integer(api['run_id'], 1); old.integer(api['run_attempt'], 1, 1000)
    require(str(api['run_id']) == run and api['run_conclusion'] in old.CONCLUSIONS, 'api_run')
    require(type(api['head_sha']) is str and re.fullmatch('[0-9a-f]{40}', api['head_sha']), 'api_commit')
    job, artifact = api['job'], api['artifact']
    exact(set(job), {'id', 'name', 'conclusion', 'steps'}, 'api_job_fields')
    old.integer(job['id'], 1)
    require(job['name'] == 'Cranelift WP session HTTP / ' + backend
        and job['conclusion'] in old.CONCLUSIONS, 'api_job')
    require(type(job['steps']) is list and 1 <= len(job['steps']) <= 64, 'api_steps')
    for step in job['steps']:
        exact(set(step), {'name', 'conclusion'}, 'api_step_fields')
        require(type(step['name']) is str and 0 < len(step['name']) <= 160
            and step['conclusion'] in old.CONCLUSIONS, 'api_step')
    exact(set(artifact), {'id', 'name', 'size_in_bytes', 'digest'}, 'api_artifact_fields')
    old.integer(artifact['id'], 1); old.integer(artifact['size_in_bytes'], 1, LIMIT)
    require(artifact['name'] == f"php-wasix-wp-session-http-{backend}-{api['run_attempt']}"
        and type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']), 'api_artifact')
    old.privacy_gate(api)


def archive_gate(path, api, backend):
    wire = read(path, LIMIT)
    exact(identity(wire), {'bytes': api['artifact']['size_in_bytes'],
        'sha256': api['artifact']['digest'][7:]}, 'api_archive_identity')
    reverse = {path: name for name, path in member_map(backend).items()}
    raw, members, expanded = {}, {}, 0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(1 <= len(entries) <= len(reverse)
            and len({row.filename for row in entries}) == len(entries), 'zip_member_count')
        for entry in entries:
            old.relative(entry.filename)
            kind = stat.S_IFMT(entry.external_attr >> 16)
            require(entry.orig_filename == entry.filename and entry.filename in reverse and not entry.is_dir()
                and entry.flag_bits & 1 == 0 and kind in (0, stat.S_IFREG), 'public_json_member')
            name = reverse[entry.filename]
            maximum = 12 * 1024 * 1024 if name == 'run.json' else 2 * 1024 * 1024
            old.integer(entry.file_size, 1, maximum)
            expanded += entry.file_size
            require(expanded <= LIMIT, 'zip_expansion_limit')
            with archive.open(entry) as stream:
                value = stream.read(entry.file_size + 1)
            require(len(value) == entry.file_size, 'zip_member_size')
            raw[name], members[name] = value, entry.filename
    require('ci-stages.json' in raw, 'stage_record_required')
    return raw, {name: parse_json(value) for name, value in raw.items()}, members


def stages_gate(value, api, backend, source):
    exact(set(value), {'schema', 'source_commit', 'run_id', 'run_attempt', 'backend', 'stages'}, 'stage_fields')
    require(value['schema'] == SCHEMA + 'ci-stages.v0.5' and value['source_commit'] == api['head_sha']
        and value['run_id'] == str(api['run_id']) and value['run_attempt'] == str(api['run_attempt'])
        and value['backend'] == backend, 'stage_identity')
    workflow = source.read(WORKFLOW).decode()
    require('name: Cranelift WP session HTTP / ${{ matrix.backend }}' in workflow, 'workflow_job_identity')
    upload = workflow.split('    - name: Upload public compile-once HTTP session evidence only\n')
    require(len(upload) == 2 and 'name: php-wasix-wp-session-http-${{ matrix.backend }}-${{ github.run_attempt }}'
        in upload[1], 'workflow_artifact_identity')
    paths = re.findall(r'^          ((?:experiments|target)/[^\n]+\.json)$', upload[1], re.M)
    exact({path.replace('${{ matrix.backend }}', backend) for path in paths}, set(member_map(backend).values()),
        'workflow_archive_path_inventory')
    names = re.findall(r'^    - name: ([^\n]+)\n      id: ([a-z_]+)$', workflow, re.M)
    require(len({stage for _, stage in names}) == len(names), 'unique_workflow_stage_ids')
    names = {stage: name for name, stage in names}
    exact(set(names), {'php', 'deps', 'parser', 'packages', 'tools', 'module', 'guest', 'rust', 'bootstrap',
        'theme', 'context', 'http', 'worker', 'private_http', 'host', 'session_http', 'contract'}, 'workflow_stage_inventory')
    exact(set(value['stages']), set(names), 'actual_stage_inventory')
    for key, name in names.items():
        rows = [row for row in api['job']['steps'] if row['name'] == name]
        require(len(rows) == 1, 'api_stage_step')
        exact(value['stages'][key], rows[0]['conclusion'], 'api_vs_stage_conclusion')
    workflow_names = set(re.findall(r'^    - name: ([^\n]+)$', workflow, re.M))
    allowed = workflow_names | {'Post ' + name for name in workflow_names if name.startswith(('Check out ', 'Preseed '))}
    require(all(row['name'] in allowed | {'Set up job', 'Complete job'} for row in api['job']['steps']),
        'api_steps_belong_to_fixed_workflow')
    for label in ('Record compile-once HTTP session CI stage outcomes',
                  'Upload public compile-once HTTP session evidence only'):
        exact([row['conclusion'] for row in api['job']['steps'] if row['name'] == label], ['success'],
            'published_stage_and_artifact_steps_succeeded')
    return all(result == 'success' for result in value['stages'].values())


def frozen_checker(source, references):
    baseline = latest.frozen_checker(source)
    return source.functions(PREFIX + 'run_check.py',
        ['summary_validator', 'worker_identity', 'expected_ready', 'validate_service', 'self_test'], {
            **baseline, 'ast': ast, 'copy': copy, 'identity': identity,
            'PREFIX': SCHEMA, 'LABELS': LABELS, 'http': SimpleNamespace(**baseline),
            'session': SimpleNamespace(SCHEMA=source.constants(SESSION + 'run_check.py')['SCHEMA'],
                validate_final=session.frozen_checker(source)['validate_final']),
            'HTTP_SOURCE': SourcePath(source, session.LATEST + 'run_check.py'),
            'native_references': references})


def own_build_gate(host, source, worker, old_builds):
    anchor = parse_json(source.read(PREFIX + 'evidence/windows-build.json'))
    require(anchor['passed'] is True and anchor['source_reverified_after'] is True, 'accepted_windows_build_anchor')
    class BuildPath(SourcePath):
        def __truediv__(self, name):
            return BuildPath(self.source, Path(self.name, name).as_posix())

        def __lt__(self, other):
            return self.name < other.name

        def exists(self):
            return self.name in source.names(PREFIX)

    root = BuildPath(source)
    inputs = source.functions(PREFIX + 'derivation_gate.py', ['inputs', 'dependency_gate', 'verify'], {
        'hashlib': __import__('hashlib'), 'tomllib': tomllib, 'HERE': root / PREFIX.rstrip('/'),
        'ROOT': root, 'SESSION': root / SESSION.rstrip('/'), 'PRIVATE_HTTP': root / session.LATEST.rstrip('/'),
        'WORKER': root / session.PRIVATE.rstrip('/'), 'CODEC': root / 'experiments/wp-private-response-probe'})
    derived = inputs['verify']()
    source_map = source.functions(PREFIX + 'build_check.py', ['inputs'], {
        'HERE': root / PREFIX.rstrip('/'), 'ROOT': root, 'sha': sha,
        'read': lambda path, maximum: path.read_bytes(),
        'sessionbuild': SimpleNamespace(inputs=lambda: worker['input_files']),
        'derivation': SimpleNamespace(inputs=inputs['inputs'])})['inputs']()
    exact(host['input_files'], source_map, 'complete_http_session_source_map')
    exact(anchor['input_files'], source_map, 'windows_anchor_same_http_session_sources')
    exact(host['derivation_before'], derived, 'source_derived_service_boundaries')
    for field in ('driver', 'source_fs_registry', 'derivation', 'old_build'):
        exact(host[field + '_before'], host[field + '_after'], 'service_build_boundary_unchanged')
    exact(host['driver_before'], worker['driver_before'], 'same_job_session_driver')
    exact(host['source_fs_registry_before'], worker['source_fs_registry_before'], 'same_job_registry_inventory')
    exact(host['old_build_before'], old_builds, 'same_job_seven_prior_builds')
    exact(host['schema'], SCHEMA + 'build.v0.5', 'service_build_schema')
    exact(host['outcome'], 'locked_offline_session_http_build_passed', 'service_build_outcome')
    for key in ('passed', 'locked_offline', 'owned_cleanup_completed', 'source_reverified_after'):
        require(host[key] is True, 'service_build_' + key)
    for key in ('database_started', 'database_connected', 'php_executed', 'wasm_executed', 'product_runtime_selected'):
        require(host[key] is False, 'service_build_' + key)
    constants = source.constants(PREFIX + 'build_check.py')
    exact(host['units']['tests'], constants['TESTS'], 'service_source_unit_inventory')
    exact(host['units']['tests'], anchor['units']['tests'], 'service_windows_unit_inventory')
    require(host['units']['passed'] is True and len(set(host['units']['tests'])) == len(constants['TESTS']),
        'all_service_build_units_passed')
    exact(host['build_profile'], 'release', 'service_linux_release_build')
    target = 'target/php-wasix-wp-http-probe/cargo-target/release/'
    name = constants['NAME']
    exact(host['executable'], target + name, 'fixed_service_executable')
    require(re.fullmatch(re.escape(target + 'deps/' + name.replace('-', '_')) + r'-[0-9a-f]{16}',
        host['units']['executable']), 'fixed_service_unit_path')
    identity_gate(host['executable_identity'], 256 * 1024 * 1024, 1)
    identity_gate(host['units']['executable_identity'], 256 * 1024 * 1024, 1)
    old.digest(host['units']['stdout_sha256'])
    exact(len(host['commands']), len(constants['STAGES']), 'service_build_stage_count')
    for row, (stage, deadline) in zip(host['commands'], constants['STAGES']):
        exact(row['stage'], stage, 'service_build_stage')
        exact(row['timeout_seconds'], deadline, 'service_build_deadline')
        require(row['owned_reaped'] is True, 'service_build_child_reaped')
        old.integer(row['owned_pid'], 1)
        exact(row['exit_code'], 0, 'service_build_child_exit')
    return source_map, derived


def identity_gate(value, maximum, minimum=0):
    exact(set(value), {'bytes', 'sha256'}, 'closed_byte_identity')
    old.integer(value['bytes'], minimum, maximum)
    old.digest(value['sha256'])


def old_build_gate(data, raw, source):
    worker = data['session-build.json']
    session.host_build_gate(worker, source)
    snapshots = session.old_snapshots_gate(data, raw, source)
    for suffix in ('before', 'after'):
        exact(worker['old_build_' + suffix], snapshots, 'session_same_job_six_old_builds')
    snapshots['session'] = {'receipt_path': SESSION + 'evidence/linux-build.json',
        'receipt_identity': identity(raw['session-build.json']), 'executable': worker['executable'],
        'executable_identity': worker['executable_identity'], 'unit_executable': worker['units']['executable'],
        'unit_executable_identity': worker['units']['executable_identity'], 'source_inputs': 241, 'units': 64,
        'exact_receipt_source_map_rederived': True, 'source_map_bound_to_combined_inputs': True,
        'artifact_bytes_rehashed': True, 'full_recursive_old_gate_invoked': False}
    return snapshots


def native_http_gate(value, label, response, pure, source):
    # The session client allows 340 seconds for a positive wire read.
    # Keep the accepted wire/header rules and derive only its time bound.
    name = session.HTTP + 'import_evidence.py'
    wire = source.read(name).decode()
    node = next(node for node in source.tree(name).body
        if isinstance(node, ast.FunctionDef) and node.name == 'http_metrics_gate')
    function = ast.get_source_segment(wire, node)
    require(function.count('(10 if rejected else 310)') == 1, 'http_observer_deadline_derivation')
    namespace = dict(http.__dict__)
    exec(compile(function.replace('(10 if rejected else 310)', '(10 if rejected else 340)'),
        '<frozen-session-http-wire-check>', 'exec'), namespace)
    namespace['http_metrics_gate'](value, label, response, pure)


def execution_gate(execution, diagnostic=None):
    fields = {'owned_reaped', 'forced_cleanup', 'process_group_owned', 'capture_kind',
        'private_startup_input', 'parent_shutdown_input_closed', 'captured_output_complete_after_reap',
        'owned_pid', 'exit_code', 'ready_line', 'bound_port', 'stdout', 'stderr', 'final_line'}
    require(type(execution) is dict and set(execution) <= fields | {'diagnostic'}, 'closed_parent_execution')
    if 'diagnostic' in execution:
        require(diagnostic is not None and execution['diagnostic'] in diagnostic, 'fixed_failure_diagnostic')
    for key in ('owned_reaped', 'forced_cleanup', 'process_group_owned', 'parent_shutdown_input_closed',
                'captured_output_complete_after_reap'):
        require(type(execution[key]) is bool, 'parent_execution_flag')
    exact(execution['capture_kind'], 'owned_regular_files', 'owned_capture_kind')
    identity_gate(execution['private_startup_input'], 4096, 1)
    if 'owned_pid' in execution: old.integer(execution['owned_pid'], 1, 2**32 - 1)
    if 'exit_code' in execution: old.integer(execution['exit_code'], -255, 255)
    if 'bound_port' in execution: old.integer(execution['bound_port'], 1, 65535)
    for key, maximum in [('ready_line', 512), ('final_line', 4 * 1024 * 1024),
                         ('stdout', 4 * 1024 * 1024), ('stderr', 65536)]:
        if key in execution: identity_gate(execution[key], maximum)
    return fields


def bootstrap_gate(run, data, raw, source, backend, complete):
    previous = base.frozen_checker(source)
    references = previous['reference'](backend)
    labels = {'install', 'read'}
    observations = run['observations']
    executions = run.get('executions', {})
    for values in (observations, executions):
        require(type(values) is dict and set(values) <= labels, 'closed_bootstrap_labels')
        if complete: exact(set(values), labels, 'both_bootstrap_modes')
    results = {}
    for label, event in executions.items():
        exact(set(event), {'owned_pid', 'owned_reaped', 'exit_code', 'stdout_bytes', 'stdout_sha256',
            'stderr_bytes', 'stderr_sha256'}, 'closed_bootstrap_execution')
        old.integer(event['owned_pid'], 1)
        require(event['owned_reaped'] is True, 'bootstrap_owned_reaped')
        old.integer(event['exit_code'], -255, 255)
        for stream, maximum in [('stdout', 4 * 1024 * 1024), ('stderr', 65536)]:
            old.integer(event[stream + '_bytes'], 0, maximum)
            old.digest(event[stream + '_sha256'])
    for label, value in observations.items():
        require(label in executions, 'bootstrap_observation_has_execution')
        event = executions[label]
        exact({'bytes': event['stdout_bytes'], 'sha256': event['stdout_sha256']},
            identity(canonical(value) + b'\n'), 'bootstrap_public_stdout_identity')
        if value['passed'] is not True and not complete:
            context.php_diagnostic(value.get('php_result'), source, False)
            continue
        results[label] = previous['validate'](value, label, references[label])
        context.operation_types(value, previous['source_operation_limit'])
        context.php_diagnostic(value['php_result'], source, False)
        exact(event['exit_code'], 0, 'bootstrap_exit')
        exact({'bytes': event['stderr_bytes'], 'sha256': event['stderr_sha256']}, identity(b''), 'bootstrap_empty_stderr')
        module = data['build.json']['php_wasm']
        exact(value['module'], {'path': module['path'], 'bytes': module['bytes'], 'sha256': module['sha256'],
            'verified': True}, 'bootstrap_same_job_module')
        exact(value['build_receipt'], {'path': old.MANAGED + 'evidence/build.json', 'sha256': sha(raw['build.json']),
            'verified': True, 'full_chain_verified_by_host': False}, 'bootstrap_same_job_module_receipt')
    return results


def database_gate(run, server, native, source, backend):
    for key in ('container_started', 'container_stopped', 'container_removed', 'container_normal_exit_verified',
                'owned_database_identity_verified_before_fixture_ddl', 'independent_wordpress_final_verified',
                'installer_schema_ddl_and_crud', 'reader_schema_crud'):
        require(run[key] is True, 'owned_database_' + key)
    exact(run['wordpress_table_count'], 12, 'twelve_original_wordpress_tables')
    for key in ('forced_owned_container_cleanup', 'normal_container_stop_failed', 'production_database_used',
                'management_credentials_supplied_to_php', 'response_cache_exercised', 'shared_php_state_exercised',
                'shared_database_connection_exercised', 'product_runtime_selected', 'native_php_executed', 'native_php_fallback'):
        require(run.get(key, False) is False, 'unclaimed_or_failure_scope_' + key)
    require(type(run['commands']) is list and run['commands'], 'owned_database_commands')
    stages = []
    for command in run['commands']:
        exact(set(command), {'stage', 'owned_pid', 'owned_reaped', 'exit_code', 'stdout_sha256',
            'stderr_sha256'}, 'closed_database_command')
        require(command['owned_reaped'] is True, 'owned_database_command_reaped')
        old.integer(command['owned_pid'], 1)
        old.digest(command['stdout_sha256']); old.digest(command['stderr_sha256'])
        old.integer(command['exit_code'], -255, 255)
        stages.append(command['stage'])
    stamp = source.constants(session.THEME + 'linux_database.py')['STAMP']
    exact(run['fixed_public_date_seed'], {'timestamp': stamp, 'actual_database_updated': True,
        'response_body_normalized': False, 'sql_sha256': native['fixed_public_date_seed']['sql_sha256']},
        'same_fixed_database_date_seed')
    exact(run['public_date_verification'], {phase: {'post_ids': [1, 2], 'comment_ids': [1],
        'timestamp': stamp, 'verified': True} for phase in ('before_home', 'after_home')}, 'owned_public_date_snapshots')
    requests = {label: row['worker_summary'] for label, row in zip(LABELS, server['requests'])}
    exact(set(run['independent_context_checks']), {'before_requests', *LABELS}, 'four_independent_database_snapshots')
    for label, check in run['independent_context_checks'].items():
        php = run['observations']['read']['php_result'] if label == 'before_requests' else requests[label]
        exact(check, {'missing_post_count': 0, 'original_public_state_verified': True,
            'fixed_public_dates_verified': True, 'autoload_option_count': php['autoload_option_count'],
            'autoload_max_value_bytes': php['autoload_max_value_bytes']}, 'exact_independent_database_snapshot')
        old.integer(check['autoload_option_count'], 1, 2**31)
        old.integer(check['autoload_max_value_bytes'], 1, 2**31)
        for operation in ('independent_missing_post_', 'independent_context_autoload_'):
            exact(stages.count(operation + label), 1, 'one_owned_command_per_database_snapshot_metric')
    pin = parse_json(source.read('experiments/php-wasix-wpdb-probe/container-images.json'))['images'][backend]
    exact(run['image'], pin['repository'] + '@' + pin['platform_digest'], 'fixed_database_image')
    exact(run['image_config_digest'], pin['image_config_digest'], 'fixed_database_config')
    exact(run['sql_server_version'], '8.4.13' if backend == 'mysql' else '11.8.9-MariaDB-ubu2404',
        'exact_linux_sql_server_version')


def service_gate(run, data, raw, checker, native, source):
    server, execution = run['http_server_observation'], run['http_server_execution']
    exact(set(execution), execution_gate(execution), 'complete_parent_execution')
    module_sha = data['build.json']['php_wasm']['sha256']
    worker = checker['worker_identity'](data['session-build.json'], raw['session-build.json'])
    derived = {'validations': {}, 'independent_context_checks': run['independent_context_checks']}
    checker['validate_service'](server, execution, worker, module_sha, native, derived)
    ready = canonical({'schema': SCHEMA + 'ready.v0.5', 'port': execution['bound_port'],
        'session_ready': checker['expected_ready'](module_sha)}) + b'\n'
    final = canonical(server) + b'\n'
    exact(execution['ready_line'], identity(ready), 'parent_ready_line_identity')
    exact(execution['final_line'], identity(final), 'parent_final_line_identity')
    exact(execution['stdout'], identity(ready + final), 'complete_summary_only_parent_stdout')
    for prefix in (PREFIX, SESSION, session.LATEST, session.PRIVATE, session.HTTP, session.CONTEXT, session.THEME, session.BOOT):
        crates = [package for package in tomllib.loads(source.read(prefix + 'Cargo.lock').decode())['package']
            if package['name'] == 'serde_json']
        require(len(crates) == 1 and not any(dep.startswith('indexmap') for dep in crates[0].get('dependencies', [])),
            'sorted_json_maps_required')
    return derived['validations']


def positive_gate(data, raw, source, backend, references, pure):
    run, host = data['run.json'], data['host-build.json']
    require(run['passed'] is True and run['outcome'] == 'fixed_compile_once_wp_real_http_passed'
        and run['source_reverified_after'] is True and run['owned_cleanup_completed'] is True
        and run['real_http_listener_exercised'] is True, 'positive_session_http_run')
    snapshots = old_build_gate(data, raw, source)
    source_map, derived = own_build_gate(host, source, data['session-build.json'], snapshots)
    for field, member in [('build_receipt', 'host-build.json'), ('session_build_receipt', 'session-build.json'),
                          ('bootstrap_build_receipt', 'bootstrap-build.json')]:
        exact(run[field], data[member], 'same_job_' + field)
    exact(run['sources_before'], source_map, 'complete_session_http_sources_before')
    exact(run['sources_after'], source_map, 'complete_session_http_sources_after')
    preparation = context.prepared_gate(data['prepared.json'], data['theme-prepared.json'],
        data['bootstrap-prepared.json'], source)
    managed = old.managed_gate(data, raw, source, run['module_provenance'])
    checker = frozen_checker(source, references)
    exact(run['http_observer_pure_checks'], pure['self_test'](), 'frozen_native_wire_observer_checks')
    exact(run['http_controller_pure_checks'], checker['self_test'](), 'frozen_session_http_controller_checks')
    boot = bootstrap_gate(run, data, raw, source, backend, True)
    native = references[backend]
    service = service_gate(run, data, raw, checker, native, source)
    exact(run['validations'], {**boot, **service}, 'exact_five_source_validations')
    exact(set(run['http_observations']), set(LABELS), 'actual_three_loopback_requests')
    exact(set(run['http_rejections']), set(pure['REJECTIONS']), 'actual_seven_http_rejections')
    database_gate(run, run['http_server_observation'], native, source, backend)
    return {'prepared': preparation, 'managed': managed, 'runtime_source_inputs': len(source_map),
        'service_units': len(host['units']['tests']), 'service_dependency_packages': derived['dependencies']['packages'],
        'all_eight_build_receipts_source_bound': True, 'historical_binary_bytes_rehashed_by_importer': False,
        'complete_service_stdout_reconstructed': True, 'worker_ready_and_final_lines_reconstructed': True,
        'private_ack_or_worker_stdout_reconstructed': False, 'private_ack_input_and_worker_stdout_identities_are_attestations': True,
        'three_http_responses_compared_to_native': True, 'seven_http_rejections_verified': True,
        'raw_http_wire_sha_recomputed': False, 'owned_adapter_worker_and_database_completion_verified': True,
        'native_comparison_in_controller_only': True, 'raw_private_packets_published': False}


def fixed_diagnostics(source):
    node = next(node for node in source.tree(PREFIX + 'server_process.py').body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'DIAGNOSTICS'
            for target in node.targets))
    require(isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
        and node.value.func.id == 'frozenset' and len(node.value.args) == 1 and not node.value.keywords,
        'frozen_failure_diagnostic_set')
    values = ast.literal_eval(node.value.args[0])
    require(type(values) is set and all(re.fullmatch('[a-z][a-z0-9_]{0,127}', value) for value in values),
        'fixed_failure_diagnostic_values')
    return values


def field_shape(value, template, optional=()):
    """Close inherited diagnostics with the corresponding public baseline shape."""
    if type(value) is dict:
        require(type(template) is dict and set(value) <= set(template) | set(optional), 'unknown_public_record_field')
        for key, child in value.items():
            if key in template: field_shape(child, template[key])
            else: require(type(child) is str and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,127}', child),
                'fixed_format_public_failure')
    elif type(value) is list:
        require(type(template) is list and (not value or template), 'unexpected_public_record_list')
        if template and all(type(item) is dict for item in template):
            # Tool assets and build steps have different nested maps. Check
            # each entry against the matching public record when one exists.
            for item in value:
                require(type(item) is dict, 'public_record_list_item')
                choices = template
                for key in ('name', 'kind', 'stage', 'op', 'path'):
                    matching = [row for row in choices if key in item and item[key] == row.get(key)]
                    if matching:
                        choices = matching
                        break
                for shape in choices:
                    try:
                        field_shape(item, shape)
                    except ValueError:
                        continue
                    break
                else:
                    raise ValueError('unknown_public_record_list_fields')
        elif template:
            require(all(type(item) in {type(sample) for sample in template} for item in value),
                'unexpected_public_record_list_item')
    else:
        require(type(template) not in (dict, list) or value is None, 'unexpected_public_record_scalar')
        if type(template) is bool: require(type(value) is bool, 'public_flag_type')
        if type(template) is int: require(type(value) is int or value is None, 'public_numeric_type')


def record_privacy(value):
    forbidden = {'private_response', 'private_contexts', 'raw_ack', 'raw_acknowledgement', 'acknowledgement_hex',
        'raw_request_packet', 'private_packet_hex', 'raw_worker_json', 'worker_packet_hex', 'body_hex', 'headers_hex',
        'stdout_hex', 'stderr_hex', 'raw_http', 'raw_http_wire', 'wire_hex', 'request_wire_hex', 'response_wire_hex'}
    if type(value) is dict:
        require(not any(key.lower() in forbidden for key in value), 'raw_private_packet_or_wire')
        for key, item in value.items():
            if key == 'acknowledgement': identity_gate(item, 4 * 1024 * 1024 + 1, 1)
            record_privacy(item)
    elif type(value) is list:
        for item in value: record_privacy(item)


def run_public_gate(data, raw, source, backend, references, pure, complete):
    if 'run.json' not in data:
        return
    run = data['run.json']
    previous = session.member_map(backend)
    template = parse_json(source.read(SESSION + f'evidence/ci-42038857146-{backend}/raw/' + previous['run.json']))
    permitted = (set(template) - {'controller_pure_checks', 'parent_process_checks', 'session_executions',
        'session_ready', 'session_final', 'fresh_repeat_404_response_exact', 'negative_protocol_sessions',
        'module_provenance_after', 'positive_session_module_compilations', 'positive_request_module_compilations',
        'positive_request_count', 'positive_owned_worker_processes', 'negative_protocol_sessions_count',
        'negative_session_module_compilations'}) | {'session_build_receipt', 'http_observations',
        'http_rejections', 'http_observer_pure_checks', 'http_controller_pure_checks', 'http_server_execution',
        'http_server_observation', 'real_http_listener_exercised', 'failure_stage', 'failure_class', 'failure',
        'cleanup_failed', 'normal_container_stop_failed', 'forced_owned_container_cleanup',
        'creation_no_container_observed', 'container_id_recovered_after_cli_failure'}
    require(type(run) is dict and set(run) <= permitted, 'closed_session_http_controller_fields')
    exact(run['schema'], SCHEMA + 'check.v0.5', 'controller_schema')
    exact(run['backend'], backend, 'controller_backend')
    exact(run['platform'], 'linux', 'controller_linux')
    require(type(run['passed']) is bool, 'controller_passed_type')
    exact(run['outcome'], 'fixed_compile_once_wp_real_http_passed' if run['passed']
        else 'compile_once_wp_real_http_failed', 'controller_outcome')
    for key in ('failure_stage', 'failure_class', 'failure'):
        if key in run: require(re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,127}', run[key]), 'fixed_format_controller_failure')
    if 'http_server_execution' in run:
        execution_gate(run['http_server_execution'], fixed_diagnostics(source))
    if 'http_server_observation' in run and not complete:
        service = service_gate(run, data, raw, frozen_checker(source, references), references[backend], source)
        exact({label: value for label, value in run['validations'].items() if label in LABELS}, service,
            'partial_service_validations')
    for key, labels in [('http_observations', LABELS), ('http_rejections', pure['REJECTIONS'])]:
        values = run[key]
        require(type(values) is dict and set(values) <= set(labels), 'closed_http_wire_labels')
        for label, value in values.items():
            case = 'home' if label == 'home' else 'missing'
            response = references[backend]['observations'][case]['response'] if key == 'http_observations' else None
            native_http_gate(value, label, response, pure, source)
    for label, value in run['observations'].items():
        require(label in ('install', 'read'), 'closed_bootstrap_observation_labels')
        field_shape(value, template['observations'][label], ('failure', 'failure_reason'))
    for label, value in run['validations'].items():
        require(label in ('install', 'read', *LABELS), 'closed_validation_labels')
        if label in ('install', 'read'):
            field_shape(value, template['validations'][label])
        else:
            require('http_server_observation' in run, 'service_validations_require_closed_observation')
    for key in ('commands', 'fixed_public_date_seed', 'public_date_verification', 'independent_context_checks'):
        if key in run: field_shape(run[key], template[key])
    if 'run' in run:
        require(re.fullmatch('target/php-wasix-wpdb-probe/database-runs/rp-wpdb-' + backend + '-[a-f0-9]{32}',
            run['run']), 'owned_database_capture_path')
    if 'container_id' in run:
        old.digest(run['container_id'])
    for key in ('image', 'image_config_digest', 'sql_server_version'):
        if key in run: exact(run[key], template[key], 'fixed_public_database_identity')
    command_stages = {row['stage'] for row in template['commands']} | {
        'recover_own_creation_identity', 'kill_only_owned_after_failed_stop'}
    for command in run.get('commands', []):
        require(command['stage'] in command_stages, 'fixed_database_command_stage')
        for key in ('stdout_sha256', 'stderr_sha256'):
            old.digest(command[key])
    if not complete:
        bootstrap = bootstrap_gate(run, data, raw, source, backend, False)
        for label, value in run['validations'].items():
            if label in ('install', 'read'): exact(value, bootstrap[label], 'partial_bootstrap_comparison')
    for key in ('sources_before', 'sources_after'):
        if key in run: source.bindings(run[key])
    for field, member in [('build_receipt', 'host-build.json'), ('session_build_receipt', 'session-build.json'),
                          ('bootstrap_build_receipt', 'bootstrap-build.json')]:
        if field in run: exact(run[field], data[member], 'same_job_' + field)
    if 'module_provenance' in run and not complete:
        old.managed_gate(data, raw, source, run['module_provenance'])
    for field, expected in [('http_observer_pure_checks', pure['self_test']),
        ('http_controller_pure_checks', lambda: frozen_checker(source, references)['self_test']())]:
        if field in run and not complete: exact(run[field], expected(), 'partial_frozen_' + field)


def public_gate(data, raw, source, backend, complete, references, pure):
    # No HTML body is stored by this controller. Only exact origin headers,
    # compared above to the pinned native response, can appear as raw bytes.
    origins = http.origin_maps(source, references)
    for value in data.values():
        record_privacy(value)
        http.public_gate(value, references, origins, pure)
    for _, prefix, member, *_ in [*session.PROFILES,
            ('session', SESSION, 'session-build.json'), ('service', PREFIX, 'host-build.json')]:
        if member in data:
            value = data[member]
            anchor = parse_json(source.read(prefix + 'evidence/windows-build.json'))
            field_shape(value, anchor, ('failure', 'failure_class', 'after_failure', 'after_failure_class'))
            if 'input_files' in value: source.bindings(value['input_files'])
    previous = session.member_map(backend)
    historical = SESSION + f'evidence/ci-42038857146-{backend}/raw/'
    for member in ('build.json', 'toolchain.json', 'prepared.json', 'theme-prepared.json', 'bootstrap-prepared.json'):
        if member in data:
            template = parse_json(source.read(historical + previous[member]))
            field_shape(data[member], template, ('failure', 'failure_class', 'diagnostics'))
    run_public_gate(data, raw, source, backend, references, pure, complete)


def immutable(path, wire):
    require(path.resolve() == path and path.is_relative_to(HERE / 'evidence'), 'owned_import_path')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(read(path, 12 * 1024 * 1024) == wire, 'immutable_import_changed')
    else:
        with path.open('xb') as stream:
            stream.write(wire)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    require(re.fullmatch('[1-9][0-9]{0,19}', args.run), 'run_id')
    work = ROOT / 'target/php-wasix-wp-session-http-probe/ci'
    name = args.run + '-' + args.backend
    api_wire = read(work / f'api-{name}.json', 128 * 1024)
    api = parse_json(api_wire)
    api_gate(api, args.run, args.backend)
    source = LocalGitSource(api['head_sha'])
    helpers = [SHARED, session.SHARED, latest.SHARED, session.private.SHARED,
        http.SHARED, context.SHARED, session.theme.SHARED, base.LEGACY]
    for helper in helpers:
        exact(source.read(helper), read(ROOT / helper, 512 * 1024), 'frozen_shared_verifier_helpers_changed')
    raw, data, members = archive_gate(work / f'run-{name}.zip', api, args.backend)
    all_stages = stages_gate(data['ci-stages.json'], api, args.backend, source)
    complete = api['run_conclusion'] == api['job']['conclusion'] == 'success' and all_stages
    if complete:
        exact(set(data), set(member_map(args.backend)), 'fifteen_positive_public_records')
    references = session.native_references(source)
    pure = http.observer(source)
    public_gate(data, raw, source, args.backend, complete, references, pure)
    bindings = sum(old.source_bindings_in_records(source, value) for value in data.values())
    verification = {'overall_positive': complete, 'raw_verdicts_rewritten': False,
        'source_binding_entries_checked': bindings, 'source_binding_verification_passed': True}
    if complete:
        verification.update(positive_gate(data, raw, source, args.backend, references, pure))
    else:
        run = data.get('run.json', {})
        verification.update(recorded_outcome=run.get('outcome'), recorded_passed=run.get('passed'),
            recorded_failure_stage=run.get('failure_stage'))
    proof = {'schema': SCHEMA + 'ci-import.v0.5', 'repository': old.REPOSITORY,
        'repository_id': old.REPOSITORY_ID, 'run_id': api['run_id'], 'run_attempt': api['run_attempt'],
        'source_commit': source.commit, 'backend': args.backend, 'run_conclusion': api['run_conclusion'],
        'job': api['job'], 'artifact': api['artifact'], 'api_metadata_sha256': sha(api_wire),
        'archive_member_map': members, 'raw_json': {key: identity(wire) for key, wire in raw.items()},
        'original_git_sources': {key: identity(wire) for key, wire in sorted(source.cache.items())},
        'verifier_input_files': {path.relative_to(ROOT).as_posix(): sha(read(path, 512 * 1024))
            for path in [Path(__file__).resolve(), *(ROOT / helper for helper in helpers)]},
        'verification': verification, 'network_used_by_importer': False,
        'php_wasm_or_database_executed_by_importer': False, 'product_runtime_selected': False,
        'source_tool_module_archive_binaries_reverified': False,
        'privacy_exception_scope': 'exact fixed native origin fields and public CI clang workspace metadata'}
    old.privacy_gate(proof)
    wire = (json.dumps(proof, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    require(len(wire) <= 2 * 1024 * 1024, 'import_proof_quota')
    if not args.verify_only:
        destination = HERE / 'evidence' / f'ci-{name}'
        output = [(destination / 'raw' / members[key], value) for key, value in raw.items()]
        output += [(destination / 'api-metadata.json', api_wire), (destination / 'provenance.json', wire)]
        for path, content in output:
            require(path.resolve() == path, 'owned_import_path')
            if path.exists(): exact(read(path, 12 * 1024 * 1024), content, 'immutable_import_changed')
        for path, content in output:
            immutable(path, content)
    print('WP session HTTP CI evidence: ' + ('verified positive' if complete else 'preserved failure/partial')
        + ' (' + args.backend + ')')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('WP session HTTP import rejected: ' + reason, file=sys.stderr)
        sys.exit(1)
