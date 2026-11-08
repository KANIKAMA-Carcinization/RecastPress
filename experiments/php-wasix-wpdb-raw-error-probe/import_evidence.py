"""Import source-bound raw ERR CI records without executing PHP, Wasm or a DB."""

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import sys
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREFIX = 'experiments/php-wasix-wpdb-raw-error-probe/'
WORKFLOW = '.github/workflows/php-wasix-wpdb-raw-error.yml'
SHARED = 'experiments/php-wasix-wp-plugin-admission-probe/import_evidence.py'
SCHEMA = 'recastpress.php-wasix-wpdb-raw-error-'
BACKENDS = ('mysql', 'mariadb')
RUNS = {
    42497285887: ('946a9a796c16119dce35ef9c072345f3bc63f1d4',
        {'mysql': 128782253914, 'mariadb': 128782253682}),
    42498452725: ('4ce8bee52173b75974e340f1293ad0f62af09338',
        {'mysql': 128785790551, 'mariadb': 128785790374}),
}
CONTRACT = 'Compare real ERR wire and wpdb state through PHP-Wasm'
UPLOAD = 'Upload only the public raw-error trial report'
STAGES = ('derive_guest.py', 'derive_actor.py', 'derive_host.py', 'unit', 'build')
LIMIT = 2 * 1024 * 1024


spec = importlib.util.spec_from_file_location('rp_raw_err_offline_base', ROOT / SHARED)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
old, base = plugin.old, plugin.base
require, exact, sha, read, parse_json, identity, closed = (
    old.require, old.exact, old.sha, old.read, old.parse_json, old.identity, plugin.closed)


def api_gate(api, backend, run):
    closed(api, ('repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha', 'run_conclusion', 'job', 'artifact'),
        'raw_api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'raw_repository_id')
    exact(api['repository'], old.REPOSITORY, 'raw_repository_name')
    exact(api['run_id'], run, 'raw_api_run')
    commit, jobs = RUNS[run]
    exact(api['head_sha'], commit, 'raw_api_source_commit')
    old.integer(api['run_attempt'], 1, 1000)
    require(api['run_conclusion'] in old.CONCLUSIONS, 'raw_api_run_conclusion')
    job = api['job']
    closed(job, ('id', 'name', 'conclusion', 'steps'), 'raw_api_job_fields')
    old.integer(job['id'], 1)
    if api['run_attempt'] == 1 and backend in jobs:
        exact(job['id'], jobs[backend], 'raw_api_job_id')
    exact(job['name'], 'Owned raw ERR through PHP-Wasm / ' + backend, 'raw_api_job_name')
    require(job['conclusion'] in old.CONCLUSIONS and type(job['steps']) is list
        and len(job['steps']) <= 64, 'raw_api_job_steps')
    names = set()
    for step in job['steps']:
        closed(step, ('name', 'conclusion'), 'raw_api_step_fields')
        name = step['name']
        require(type(name) is str and 0 < len(name) <= 160 and name not in names
            and step['conclusion'] in old.CONCLUSIONS, 'raw_api_step')
        names.add(name)
    artifact = api['artifact']
    if artifact is None:
        require(job['conclusion'] != 'success', 'raw_success_missing_artifact')
    else:
        closed(artifact, ('id', 'name', 'size_in_bytes', 'digest'), 'raw_api_artifact_fields')
        old.integer(artifact['id'], 1)
        old.integer(artifact['size_in_bytes'], 1, LIMIT)
        exact(artifact['name'], f"php-wasix-wpdb-raw-error-{backend}-{api['run_attempt']}", 'raw_api_artifact_name')
        require(type(artifact['digest']) is str and re.fullmatch('sha256:[a-f0-9]{64}', artifact['digest']),
            'raw_api_artifact_digest')
    old.privacy_gate(api)


def workflow_gate(api, source):
    workflow = source.read(WORKFLOW).decode('utf-8')
    require('name: Owned raw ERR through PHP-Wasm / ${{ matrix.backend }}' in workflow
        and 'backend: [mysql, mariadb]' in workflow, 'raw_workflow_job_matrix')
    names = re.findall(r'^    - name: ([^\n]+)$', workflow, re.M)
    require(len(names) == len(set(names)) and names[-2:] == [CONTRACT, UPLOAD], 'raw_workflow_steps')
    upload = workflow.split(f'    - name: {UPLOAD}\n')[1]
    require('name: php-wasix-wpdb-raw-error-${{ matrix.backend }}-${{ github.run_attempt }}' in upload
        and 'path: ' + PREFIX + 'evidence/linux-${{ matrix.backend }}.json' in upload, 'raw_workflow_artifact')
    steps = {step['name']: step['conclusion'] for step in api['job']['steps']}
    allowed = set(names) | {'Post ' + name for name in names if name.startswith(('Check out ', 'Preseed '))}
    require(set(steps) <= allowed | {'Set up job', 'Complete job'}, 'raw_api_workflow_step_names')
    exact([name for name in steps if name in names], [name for name in names if name in steps], 'raw_api_step_order')
    if api['artifact'] is not None:
        exact(steps.get(UPLOAD), 'success', 'raw_successful_upload')
    if api['job']['conclusion'] == 'success':
        require(all(steps.get(name) == 'success' for name in names), 'raw_successful_steps')
    if api['run_conclusion'] == 'success':
        exact(api['job']['conclusion'], 'success', 'raw_successful_run_job')
    return steps


class GitPath(plugin.GitPath):
    """Read-only path facade for the committed source closure and derivations."""
    def __init__(self, source, name='.'):
        super().__init__(source, posixpath.normpath(str(name)))

    def __truediv__(self, name):
        return GitPath(self.source, posixpath.join(self.name, name))

    def __lt__(self, other):
        return self.name < other.name

    @property
    def parent(self):
        return GitPath(self.source, posixpath.dirname(self.name))

    @property
    def suffix(self):
        return PurePosixPath(self.name).suffix

    def is_relative_to(self, other):
        return PurePosixPath(self.name).is_relative_to(other.name)

    def resolve(self, strict=False):
        return self

    def is_file(self):
        return True  # read_bytes() verifies a local Git blob.

    def glob(self, pattern):
        depth = len(PurePosixPath(pattern).parts)
        candidates = [GitPath(self.source, name) for name in self.source.names(self.name + '/')]
        return [path for path in candidates if len(path.relative_to(self).parts) == depth
            and path.relative_to(self).match(pattern)]

    def rglob(self, pattern):
        require(pattern == '*.rs', 'raw_source_tree_pattern')
        return [GitPath(self.source, name) for name in self.source.names(self.name + '/') if name.endswith('.rs')]


def source_inventory(source):
    root = GitPath(source)
    here = root / PREFIX.rstrip('/')
    functions = source.functions(PREFIX + 'run_check.py', ['sources'], {'ROOT': root, 'HERE': here,
        'EXPERIMENTS': here.parent, 'identity': identity})
    result = functions['sources']()
    require(len(result) == 72, 'raw_source_closure_count')
    return result


def generated_inventory(source):
    root = GitPath(source)
    here = root / PREFIX.rstrip('/')
    guest_path = PREFIX + 'derive_guest.py'
    fixture = root / 'experiments/php-wasix-wpdb-utf8-probe/fixtures'
    native = root / 'experiments/wpdb-raw-error-wire-probe/fixtures/native.php'
    pin_node = next(node.value for node in source.tree(guest_path).body if isinstance(node, ast.Assign)
        and any(isinstance(key, ast.Name) and key.id == 'PINS' for key in node.targets))
    require(isinstance(pin_node, ast.Dict) and len(pin_node.values) == 5, 'raw_guest_source_pin_inventory')
    names = [(fixture / name).name for name in ('bridge.php', 'entry.php', 'adapter.php', 'helpers.php')] + [native.name]
    pins = {name: ast.literal_eval(value) for name, value in zip(names, pin_node.values)}

    def pinned_guest(path):
        wire = path.read_bytes()
        exact(sha(wire), pins[path.name], 'raw_pinned_guest_source')
        return wire

    guest = source.functions(guest_path, ['replace', 'region', 'adapter', 'corpus', 'entry', 'helpers', 'guest_files'],
        {**source.constants(guest_path), 'UTF8': fixture, 'NATIVE': native, 'pinned': pinned_guest})
    actor_path = PREFIX + 'derive_actor.py'
    actor = source.functions(actor_path, ['replace', 'derived_bytes'], {**source.constants(actor_path),
        'SOURCE': root / 'experiments/php-wasix-wp-bootstrap-probe/src/db.rs', 'sha256': hashlib.sha256})
    host_path = PREFIX + 'derive_host.py'
    host_constants = source.constants(host_path)

    def pinned_host(name):
        wire = source.read('experiments/php-wasix-wpdb-utf8-probe/' + name)
        exact(sha(wire), host_constants['PINS'][name], 'raw_pinned_host_source')
        return wire

    host = source.functions(host_path, ['once', 'section', 'generated'], {**host_constants,
        'HERE': here, 'pinned': pinned_host})
    files = guest['guest_files']()
    closed(files, ('bridge.php', 'entry.php', 'corpus.php', 'adapter.php', 'helpers.php'), 'raw_guest_five_fixtures')
    return {'src/db.rs': identity(actor['derived_bytes']()), 'src/main.rs': identity(host['generated']()),
        **{'fixtures/' + name: identity(wire) for name, wire in files.items()}}


def native_reference(source, constants, backend):
    path = 'experiments/wpdb-raw-error-wire-probe/evidence/windows-' + backend + '-native.json'
    wire = source.read(path)
    exact(sha(wire), constants['NATIVE'][backend], 'raw_native_reference_pin')
    value = parse_json(wire)
    exact(value['passed'], True, 'raw_native_reference_passed')
    exact(value['error_then_success_clears'], True, 'raw_native_error_cleared')
    errors = constants['ERRORS']
    payloads = value['wire']['observations']
    closed(payloads, (*errors, 'recovery'), 'raw_native_wire_observations')
    exact(payloads['recovery'], {'columns': 1, 'kind': 'resultset'}, 'raw_native_wire_recovery')
    observations = value['observation']['observations']
    require(type(observations) is dict and len(observations) == 7 and set(errors) <= set(observations),
        'raw_native_seven_observations')
    for name in errors:
        row = payloads[name]
        closed(row, ('code', 'kind', 'message_hex', 'payload_bytes', 'payload_sha256', 'state_hex'), 'raw_native_err_fields')
        exact(row['kind'], 'err', 'raw_native_err_kind')
        old.integer(row['code'], 1, 65535)
        require(re.fullmatch('[a-f0-9]{10}', row['state_hex']) and re.fullmatch('(?:[a-f0-9]{2}){1,1024}', row['message_hex']),
            'raw_native_err_hex')
        message = bytes.fromhex(row['message_hex'])
        payload = b'\xff' + row['code'].to_bytes(2, 'little') + b'#' + bytes.fromhex(row['state_hex']) + message
        exact(identity(payload), {'bytes': row['payload_bytes'], 'sha256': row['payload_sha256']}, 'raw_native_err_wire_identity')
        expected = observations[name]
        exact(row['code'], expected['mysqli_errno'], 'raw_native_err_code')
        exact(row['state_hex'], expected['mysqli_sqlstate_hex'], 'raw_native_err_state')
        exact(row['message_hex'], expected['mysqli_error_hex'], 'raw_native_err_message')
    return value


def driver_gate(value, source):
    prefix = 'experiments/wpdb-raw-error-probe/'
    plan_wire = source.read(prefix + 'patch-plan.json')
    plan = parse_json(plan_wire)
    source.bindings(plan['base_inputs'])
    base_plan = source.read('experiments/wpdb-rpc-probe/driver-plan.json')
    for name in ('patch', 'test_fixture'):
        pin = plan[name]
        exact(identity(source.read(pin['path'])), {key: pin[key] for key in ('bytes', 'sha256')}, 'raw_driver_source_pin')
    lock = source.read(prefix + 'driver.Cargo.lock')
    exact(sha(lock), plan['driver_lock_seed']['sha256'], 'raw_driver_lock_pin')
    exact(value, {'schema': 'recastpress.wpdb-raw-error-preparation.v0.5',
        'outcome': 'fixed_raw_error_driver_staged', 'plan_sha256': sha(plan_wire), 'base_plan_sha256': sha(base_plan),
        'base_patch_sha256': parse_json(base_plan)['patch']['sha256'], 'raw_patch_sha256': plan['patch']['sha256'],
        'source_trees': plan['source_trees_after'], 'new_stage': 'target/wpdb-raw-error-probe/workspace',
        'workspace_manifest': identity(source.read(prefix + 'driver-workspace.toml')), 'workspace_lock': identity(lock),
        'registry_and_old_stage_modified': False, 'network_used': False, 'database_started': False,
        'database_connected': False, 'product_driver_selected': False}, 'raw_driver_preparation_scope')


def execution_gate(row, passed):
    closed(row, ('owned_pid', 'owned_reaped', 'exit_code', 'stdout', 'stderr'), 'raw_execution_fields')
    old.integer(row['owned_pid'], 1)
    old.integer(row['exit_code'], -255, 255)
    exact(row['owned_reaped'], True, 'raw_owned_execution_reap')
    base.identity_gate(row['stdout'], 16 * 1024 * 1024)
    base.identity_gate(row['stderr'], 65536)
    if passed:
        exact(row['exit_code'], 0, 'raw_successful_execution_exit')


def report_gate(value, source, backend, steps):
    common = {'schema', 'backend', 'passed', 'sources_before', 'module_provenance', 'driver_preparation', 'build_commands',
        'generated_inputs', 'units_passed', 'worker_build', 'host_execution', 'observations', 'wire_observations', 'sources_after'}
    success = {'independent_database_unchanged', 'owned_database_normal_cleanup', 'source_reverified_after', 'sql_server_version'}
    failure = {'failure_stage', 'failure_class', 'failure', 'host_error', 'host_guest_failure', 'host_phase'}
    require(type(value) is dict and type(value.get('passed')) is bool, 'raw_report_shape')
    passed = value['passed']
    closed(value, common | (success if passed else failure), 'raw_report_fields', partial=not passed)
    exact(value['schema'], SCHEMA + 'check.v0.5', 'raw_report_schema')
    exact(value['backend'], backend, 'raw_report_backend')
    exact(steps.get(CONTRACT), 'success' if passed else 'failure', 'raw_report_step_conclusion')
    old.privacy_gate(value)
    base.record_privacy(value)
    constants = source.constants(PREFIX + 'run_check.py')
    inputs = source_inventory(source)
    generated = generated_inventory(source)
    native = native_reference(source, constants, backend)
    if not passed:
        require(value['failure_stage'] in ('source_preflight', 'build', 'wasm_execution', 'compare', 'postflight')
            and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,127}', value['failure_class']), 'raw_failure_stage_or_class')
        for key in failure - {'failure_stage', 'failure_class'}:
            if key in value:
                require(re.fullmatch('[a-z][a-z0-9_]{0,127}', value[key]), 'raw_failure_code')
    for name in ('sources_before', 'sources_after'):
        if name not in value:
            continue
        if not passed and name == 'sources_after' and value.get('failure') == 'raw_error_source_changed':
            closed(value[name], inputs, 'raw_source_drift_paths')
            for item in value[name].values():
                base.identity_gate(item, 4 * 1024 * 1024)
        else:
            exact(value[name], inputs, 'raw_source_input_closure')
    if 'module_provenance' in value:
        plugin.module_gate(value['module_provenance'], source)
    if 'driver_preparation' in value:
        driver_gate(value['driver_preparation'], source)
    if 'generated_inputs' in value:
        exact(value['generated_inputs'], generated, 'raw_seven_derived_files')
    if 'build_commands' in value:
        commands = value['build_commands']
        closed(commands, STAGES, 'raw_build_stage_inventory', partial=not passed)
        plugin.order_prefix(commands, STAGES, 'raw_build_stage_prefix')
        for index, stage in enumerate(STAGES[:len(commands)]):
            row = commands[stage]
            execution_gate(row, passed or index + 1 < len(commands))
            if index < 3 and row['exit_code'] == 0:
                output = b'' if index == 0 else (generated['src/db.rs' if index == 1 else 'src/main.rs']['sha256'] + '\n').encode('ascii')
                exact(row['stdout'], identity(output), 'raw_source_derivation_stdout')
                exact(row['stderr'], identity(b''), 'raw_source_derivation_stderr')
    if 'units_passed' in value:
        exact(value['units_passed'], 26, 'raw_locked_worker_unit_count')
    if 'worker_build' in value:
        worker = value['worker_build']
        closed(worker, ('executable', 'executable_identity', 'locked_offline'), 'raw_worker_build_fields')
        exact(worker['executable'], 'target/php-wasix-wpdb-raw-error-probe/cargo-target/release/' + constants['NAME'],
            'raw_fixed_worker_executable')
        base.identity_gate(worker['executable_identity'], 256 * 1024 * 1024, 1)
        exact(worker['locked_offline'], True, 'raw_worker_locked_offline')
    if 'host_execution' in value:
        row = value['host_execution']
        execution_gate(row, passed or 'observations' in value)
        if passed or 'observations' in value:
            base.identity_gate(row['stdout'], 4 * 1024 * 1024, 1)
            exact(row['stderr'], identity(b''), 'raw_owned_host_empty_stderr')
    exact('observations' in value, 'wire_observations' in value, 'raw_paired_wire_and_php_observations')
    if 'observations' in value:
        require('host_execution' in value, 'raw_owned_observations_require_host')
        expected = {name: {'query': identity(bytes.fromhex(row['query_hex'])), 'state_matches_native': True,
            'return_matches_native': True, 'last_result_matches_native': True, 'error_matches_native': True}
            for name, row in native['observation']['observations'].items()}
        exact(value['observations'], expected, 'raw_seven_stock_wpdb_matches')
        exact(value['wire_observations'], native['wire']['observations'], 'raw_fixed_wire_observations')
    if passed:
        for key in success - {'sql_server_version'}:
            exact(value[key], True, 'raw_normal_database_and_source_cleanup')
        exact(value['sql_server_version'], '8.4.13' if backend == 'mysql' else '11.8.9-MariaDB-ubu2404',
            'raw_exact_linux_server_version')
    return {'report_passed': passed, 'source_inputs': len(inputs), 'generated_inputs': len(generated),
        'verified_stock_wpdb_observations': len(value.get('observations', {})),
        'verified_fixed_err_packets': len(constants['ERRORS']) if 'wire_observations' in value else 0,
        'failure_stage': value.get('failure_stage'), 'failure': value.get('failure'),
        'host_error': value.get('host_error'), 'host_guest_failure': value.get('host_guest_failure')}


def import_evidence(run, backend, verify_only=False):
    directory = ROOT / 'target/php-wasix-wpdb-raw-error-probe/ci'
    tag = f'{run}-{backend}'
    api_wire = read(directory / f'api-{tag}.json', 128 * 1024)
    api = parse_json(api_wire)
    api_gate(api, backend, run)
    source = base.LocalGitSource(RUNS[run][0])
    session = base.session
    helpers = (SHARED, plugin.SHARED, base.SHARED, session.SHARED, base.latest.SHARED, session.private.SHARED,
        base.http.SHARED, base.context.SHARED, session.theme.SHARED, base.base.LEGACY)
    for name in dict.fromkeys(helpers):
        exact(source.read(name), read(ROOT / name, LIMIT), 'raw_frozen_helper_changed')
    steps = workflow_gate(api, source)
    raw, archive = plugin.archive_gate(directory / f'run-{tag}.zip', api, backend)
    summary = None if raw is None else report_gate(parse_json(raw), source, backend, steps)
    passed = api['run_conclusion'] == api['job']['conclusion'] == 'success'
    if passed:
        require(summary is not None and summary['report_passed'], 'raw_overall_success_evidence')
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
            require(path.resolve() == path, 'raw_owned_evidence_path')
            if path.exists():
                require(path.is_file() and path.stat().st_size == len(wire) and path.read_bytes() == wire,
                    'raw_immutable_evidence')
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
        print('WordPress raw ERR CI evidence rejected: ' + code, file=sys.stderr)
        sys.exit(1)
    print('WordPress raw ERR CI evidence: ' + ('verified positive' if passed else 'preserved failure/partial'))
