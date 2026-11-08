"""Verify public PHP-Wasm object-cache evidence using only local Git inputs."""

import argparse
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import sys
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREFIX = 'experiments/php-wasix-wp-object-cache-probe/'
WORKFLOW = '.github/workflows/php-wasix-wp-object-cache.yml'
SHARED = 'experiments/php-wasix-wp-plugin-admission-probe/import_evidence.py'
SCHEMA = 'recastpress.php-wasix-wp-object-cache-'
RUNS = {
    42495113357: ('18739c8e2c44c82e88ebe1a4ec4db1f7785fe4ee', 128775693931),
    42495270303: ('023a8a721aa734f8db4f204e4c35893047d13dd0', 128776153147),
}
JOB = 'Compare owned Rust object cache in two fresh PHP-Wasm requests'
CONTRACT = 'Compare object cache API and clean private channel completion'
UPLOAD = 'Upload only public cache trial evidence'
STAGES = ('derive_cache.py', 'derive_guest.py', 'unit', 'build')
LIMIT = 2 * 1024 * 1024


spec = importlib.util.spec_from_file_location('rp_cache_offline_evidence_base', ROOT / SHARED)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
old, base = plugin.old, plugin.base
require, exact, sha, read, parse_json, identity, closed = (
    old.require, old.exact, old.sha, old.read, old.parse_json, old.identity, plugin.closed)


def api_gate(api, run):
    closed(api, ('repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha', 'run_conclusion', 'job', 'artifact'),
        'cache_api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'cache_api_repository_id')
    exact(api['repository'], old.REPOSITORY, 'cache_api_repository')
    exact(api['run_id'], run, 'cache_api_run')
    commit, first_job = RUNS[run]
    exact(api['head_sha'], commit, 'cache_api_commit')
    old.integer(api['run_attempt'], 1, 1000)
    require(api['run_conclusion'] in old.CONCLUSIONS, 'cache_api_run_conclusion')
    job, artifact = api['job'], api['artifact']
    if job is None:
        require(artifact is None and api['run_conclusion'] != 'success', 'cache_missing_job')
    else:
        closed(job, ('id', 'name', 'conclusion', 'steps'), 'cache_api_job_fields')
        old.integer(job['id'], 1)
        if api['run_attempt'] == 1 and first_job is not None:
            exact(job['id'], first_job, 'cache_api_job_id')
        exact(job['name'], JOB, 'cache_api_job_name')
        require(job['conclusion'] in old.CONCLUSIONS and type(job['steps']) is list
            and len(job['steps']) <= 64, 'cache_api_job_steps')
        names = set()
        for step in job['steps']:
            closed(step, ('name', 'conclusion'), 'cache_api_step_fields')
            name = step['name']
            require(type(name) is str and 0 < len(name) <= 160 and name not in names
                and step['conclusion'] in old.CONCLUSIONS, 'cache_api_step')
            names.add(name)
    if artifact is None:
        require(api['run_conclusion'] != 'success' and (job is None or job['conclusion'] != 'success'),
            'cache_success_missing_artifact')
    else:
        closed(artifact, ('id', 'name', 'size_in_bytes', 'digest'), 'cache_api_artifact_fields')
        old.integer(artifact['id'], 1)
        old.integer(artifact['size_in_bytes'], 1, LIMIT)
        exact(artifact['name'], f"wordpress-wasix-object-cache-{api['run_attempt']}", 'cache_api_artifact_name')
        require(type(artifact['digest']) is str and re.fullmatch('sha256:[a-f0-9]{64}', artifact['digest']),
            'cache_api_artifact_digest')
    old.privacy_gate(api)


def workflow_gate(api, source):
    workflow = source.read(WORKFLOW).decode('utf-8')
    require(f'    name: {JOB}\n' in workflow, 'cache_workflow_job')
    names = re.findall(r'^    - name: ([^\n]+)$', workflow, re.M)
    require(len(names) == len(set(names)) and names[-2:] == [CONTRACT, UPLOAD], 'cache_workflow_steps')
    upload = workflow.split(f'    - name: {UPLOAD}\n')[1]
    require('name: wordpress-wasix-object-cache-${{ github.run_attempt }}' in upload
        and 'path: ' + PREFIX + 'evidence/linux.json' in upload, 'cache_workflow_artifact')
    if api['job'] is None:
        return {}
    steps = {step['name']: step['conclusion'] for step in api['job']['steps']}
    allowed = set(names) | {'Post ' + name for name in names if name.startswith(('Check out ', 'Preseed '))}
    require(set(steps) <= allowed | {'Set up job', 'Complete job'}, 'cache_api_workflow_step_names')
    exact([name for name in steps if name in names], [name for name in names if name in steps], 'cache_api_step_order')
    if api['artifact'] is not None:
        exact(steps.get(UPLOAD), 'success', 'cache_api_successful_upload')
    if api['job']['conclusion'] == 'success':
        require(all(steps.get(name) == 'success' for name in names), 'cache_successful_steps')
    if api['run_conclusion'] == 'success':
        exact(api['job']['conclusion'], 'success', 'cache_successful_run_job')
    return steps


def archive_gate(path, artifact):
    if artifact is None:
        require(not path.exists() and not path.is_symlink(), 'cache_unbound_archive')
        return None, None
    wire = read(path, LIMIT)
    observed = identity(wire)
    exact(observed, {'bytes': artifact['size_in_bytes'], 'sha256': artifact['digest'][7:]}, 'cache_archive_identity')
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1, 'cache_single_zip_member')
        entry = entries[0]
        kind = stat.S_IFMT(entry.external_attr >> 16)
        require(entry.orig_filename == entry.filename == 'linux.json' and not entry.is_dir()
            and entry.flag_bits & 1 == 0 and kind in (0, stat.S_IFREG), 'cache_zip_member')
        old.integer(entry.file_size, 1, LIMIT)
        with archive.open(entry) as stream:
            raw = stream.read(entry.file_size + 1)
        require(len(raw) == entry.file_size, 'cache_zip_member_size')
    return raw, observed


class GitPath:
    """Read-only path operations for replaying selected pure functions."""
    def __init__(self, source, name='.'):
        self.source = source
        self.name = posixpath.normpath(str(name))

    def __truediv__(self, other):
        return GitPath(self.source, posixpath.join(self.name, other))

    def __eq__(self, other):
        return isinstance(other, GitPath) and self.source is other.source and self.name == other.name

    def __hash__(self):
        return hash((id(self.source), self.name))

    def __lt__(self, other):
        return self.name < other.name

    @property
    def parent(self):
        return GitPath(self.source, posixpath.dirname(self.name))

    @property
    def suffix(self):
        return PurePosixPath(self.name).suffix

    def resolve(self, strict=False):
        return self

    def relative_to(self, other):
        return PurePosixPath(self.name).relative_to(other.name)

    def is_relative_to(self, other):
        return PurePosixPath(self.name).is_relative_to(other.name)

    def rglob(self, pattern):
        require(pattern == '*.rs', 'cache_source_tree_pattern')
        return [GitPath(self.source, name) for name in self.source.names(self.name + '/') if name.endswith('.rs')]

    def is_file(self):
        return True  # Git blob identity is enforced by read_bytes().

    def read_bytes(self):
        return self.source.read(self.name)


def expected_sources(source, constants):
    root = GitPath(source)
    here = root / PREFIX.rstrip('/')
    env = {**constants, 'ROOT': root, 'HERE': here, 'EXPERIMENTS': here.parent, 'identity': identity}
    inputs = source.functions(PREFIX + 'run_check.py', ['source_inputs'], env)['source_inputs']()
    require(len(inputs) == 69, 'cache_sixty_nine_source_inputs')
    return inputs


def generated_sources(source):
    root = GitPath(source)
    cache_name = PREFIX + 'derive_cache.py'
    cache = source.functions(cache_name, ['derived_bytes'], {**source.constants(cache_name),
        'SOURCE': root / 'experiments/wp-object-cache-rust-probe/src/main.rs', 'hashlib': hashlib})
    guest_name = PREFIX + 'derive_guest.py'
    guest_constants = source.constants(guest_name)
    adapter = root / 'experiments/wp-object-cache-rust-probe/fixtures/adapter.php'
    bridge = root / 'experiments/wp-object-cache-rust-probe/fixtures/bridge.php'
    corpus = root / 'experiments/wp-object-cache-contract-probe/fixtures/corpus.php'
    pin_node = next(node.value for node in source.tree(guest_name).body if isinstance(node, ast.Assign)
        and any(isinstance(key, ast.Name) and key.id == 'PINS' for key in node.targets))
    require(isinstance(pin_node, ast.Dict) and len(pin_node.values) == 3, 'cache_guest_derivation_pin_inventory')
    pins = {path: ast.literal_eval(value) for path, value in zip((adapter, bridge, corpus), pin_node.values)}

    def pinned(path):
        wire = path.read_bytes()
        exact(sha(wire), pins[path], 'cache_pinned_guest_source')
        return wire

    guest = source.functions(guest_name, ['replace', 'section', 'guest_adapter', 'guest_entry', 'guest_files'],
        {**guest_constants, 'ADAPTER': adapter, 'BRIDGE': bridge, 'CORPUS': corpus, 'pinned': pinned})
    files = guest['guest_files']()
    closed(files, ('entry.php', 'adapter.php', 'corpus.php'), 'cache_generated_guest_inventory')
    return {'src/cache_host.rs': identity(cache['derived_bytes']()),
        **{'fixtures/' + name: identity(wire) for name, wire in files.items()}}


def native_digest(source, constants):
    references = []
    for name in ('STOCK', 'BRIDGE'):
        wire = source.read(constants[name])
        exact(sha(wire), constants[name + '_SHA'], 'cache_native_reference_pin')
        observed = parse_json(wire)
        exact(observed['passed'], True, 'cache_native_reference_passed')
        references.append(observed)
    stock, bridge = references
    exact(bridge['observations_match_native'], 56, 'cache_native_observation_count')
    expected = stock['observation']['observations']
    require(type(expected) is dict and len(expected) == 56, 'cache_native_observation_inventory')
    exact(expected, bridge['observations'], 'cache_native_reference_match')
    return identity(old.canonical(expected))


def execution_gate(row, passed):
    closed(row, ('owned_pid', 'owned_reaped', 'exit_code', 'stdout', 'stderr'), 'cache_execution_fields')
    old.integer(row['owned_pid'], 1)
    old.integer(row['exit_code'], -255, 255)
    exact(row['owned_reaped'], True, 'cache_owned_execution_reap')
    base.identity_gate(row['stdout'], 16 * 1024 * 1024)
    base.identity_gate(row['stderr'], 65536)
    if passed:
        exact(row['exit_code'], 0, 'cache_successful_execution_exit')


def request_gate(row, label, native):
    closed(row, ('case', 'observations', 'observations_identity', 'fresh_runtime', 'cgi_capture_completed', 'cache'),
        'cache_request_fields')
    exact(row['case'], label, 'cache_request_label')
    exact(row['observations'], 56, 'cache_request_observation_count')
    exact(row['observations_identity'], native, 'cache_request_native_digest')
    for key in ('fresh_runtime', 'cgi_capture_completed'):
        exact(row[key], True, 'cache_request_lifecycle')
    cache = row['cache']
    for name in ('request_bytes', 'response_bytes'):
        old.integer(cache[name], 1, 65536)
    exact(cache, {'operations': 32, 'entries': 0, 'charged_bytes': 0, 'flushes': 2,
        'groups': {'rp-cache-multiple': 14, 'rp-cache-integer': 16}, 'opened_once': True,
        'channel_open': False, 'finished': True, 'request_bytes': cache['request_bytes'],
        'response_bytes': cache['response_bytes'], 'response_read_bytes': cache['response_bytes'],
        'pending_bytes': 0, 'unread_response_bytes': 0, 'finish_dispatched': True}, 'cache_clean_channel_record')


def report_gate(value, source, steps):
    common = {'schema', 'passed', 'sources_before', 'module_provenance', 'build_commands', 'generated_inputs',
        'units_passed', 'worker_build', 'host_execution', 'requests', 'sources_after'}
    success = {'module_compilations', 'fresh_requests', 'source_reverified_after'}
    failure = {'failure_stage', 'failure_class', 'failure', 'host_failure', 'guest_failures'}
    require(type(value) is dict and type(value.get('passed')) is bool, 'cache_report_shape')
    passed = value['passed']
    closed(value, common | (success if passed else failure), 'cache_report_fields', partial=not passed)
    exact(value['schema'], SCHEMA + 'check.v0.5', 'cache_report_schema')
    exact(steps.get(CONTRACT), 'success' if passed else 'failure', 'cache_report_step_conclusion')
    old.privacy_gate(value)
    base.record_privacy(value)
    constants = source.constants(PREFIX + 'run_check.py')
    inputs = expected_sources(source, constants)
    generated = generated_sources(source)
    native = native_digest(source, constants)
    if not passed:
        require(value['failure_stage'] in ('inputs', 'build', 'execute', 'compare', 'after_verification')
            and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,127}', value['failure_class']), 'cache_failure_stage_or_class')
        if 'failure' in value:
            require(re.fullmatch('[a-z][a-z0-9_]{0,127}', value['failure']), 'cache_failure_code')
        if 'host_failure' in value:
            require(re.fullmatch('[a-z_]{1,80}', value['host_failure']), 'cache_host_failure_code')
        if 'guest_failures' in value:
            closed(value['guest_failures'], ('first', 'repeat'), 'cache_guest_failure_cases', partial=True)
            require(value['guest_failures'] and all(re.fullmatch('[a-z][a-z0-9_]{0,80}', code)
                for code in value['guest_failures'].values()), 'cache_guest_failure_code')
    for name in ('sources_before', 'sources_after'):
        if name not in value:
            continue
        if not passed and name == 'sources_after' and value.get('failure') == 'cache_source_after_changed':
            closed(value[name], inputs, 'cache_drift_source_paths')
            for entry in value[name].values():
                base.identity_gate(entry, 4 * 1024 * 1024)
        else:
            exact(value[name], inputs, 'cache_source_input_closure')
    if 'module_provenance' in value:
        plugin.module_gate(value['module_provenance'], source)
    if 'generated_inputs' in value:
        exact(value['generated_inputs'], generated, 'cache_four_derived_artifacts')
    if 'build_commands' in value:
        commands = value['build_commands']
        closed(commands, STAGES, 'cache_build_stage_inventory', partial=not passed)
        plugin.order_prefix(commands, STAGES, 'cache_build_stage_prefix')
        for index, name in enumerate(STAGES[:len(commands)]):
            row = commands[name]
            execution_gate(row, passed or index + 1 < len(commands))
            if row['exit_code'] == 0 and name in STAGES[:2]:
                output = (generated['src/cache_host.rs']['sha256'] + '\n').encode('ascii') if index == 0 else b''
                exact(row['stdout'], identity(output), 'cache_derivation_stdout')
                exact(row['stderr'], identity(b''), 'cache_derivation_stderr')
    if 'units_passed' in value:
        exact(value['units_passed'], 23, 'cache_locked_worker_unit_count')
    if 'worker_build' in value:
        worker = value['worker_build']
        closed(worker, ('executable', 'executable_identity', 'locked_offline'), 'cache_worker_build_fields')
        exact(worker['executable'], 'target/php-wasix-wp-object-cache-probe/cargo-target/release/' + constants['NAME'],
            'cache_fixed_worker_executable')
        base.identity_gate(worker['executable_identity'], 256 * 1024 * 1024, 1)
        exact(worker['locked_offline'], True, 'cache_worker_locked_offline')
    if 'host_execution' in value:
        host = value['host_execution']
        closed(host, ('cache_host',), 'cache_owned_host_inventory', partial=not passed)
        if 'cache_host' in host:
            row = host['cache_host']
            execution_gate(row, passed or 'requests' in value)
            if passed or 'requests' in value:
                base.identity_gate(row['stdout'], 2 * 1024 * 1024, 1)
                exact(row['stderr'], identity(b''), 'cache_owned_host_empty_stderr')
    if 'requests' in value:
        rows = value['requests']
        require(type(rows) is list and len(rows) == 2 and 'cache_host' in value.get('host_execution', {}),
            'cache_two_owned_requests')
        for row, label in zip(rows, ('first', 'repeat')):
            request_gate(row, label, native)
    if passed:
        exact(value['module_compilations'], 1, 'cache_host_compile_once')
        exact(value['fresh_requests'], 2, 'cache_host_two_fresh_requests')
        exact(value['source_reverified_after'], True, 'cache_source_reverified_after')
    return {'report_passed': passed, 'source_inputs': len(inputs), 'generated_inputs': len(generated),
        'native_observations_identity': native, 'verified_requests': len(value.get('requests', [])),
        'failure_stage': value.get('failure_stage'), 'failure': value.get('failure'),
        'host_failure': value.get('host_failure'), 'guest_failures': value.get('guest_failures')}


def import_evidence(run, verify_only=False):
    directory = ROOT / 'target/php-wasix-wp-object-cache-probe/ci'
    api_wire = read(directory / f'api-{run}.json', 128 * 1024)
    api = parse_json(api_wire)
    api_gate(api, run)
    source = base.LocalGitSource(RUNS[run][0])
    session = base.session
    helpers = (SHARED, plugin.SHARED, base.SHARED, session.SHARED, base.latest.SHARED, session.private.SHARED,
        base.http.SHARED, base.context.SHARED, session.theme.SHARED, base.base.LEGACY)
    for name in dict.fromkeys(helpers):
        exact(source.read(name), read(ROOT / name, LIMIT), 'cache_frozen_helper_changed')
    steps = workflow_gate(api, source)
    raw, archive = archive_gate(directory / f'run-{run}.zip', api['artifact'])
    summary = None if raw is None else report_gate(parse_json(raw), source, steps)
    passed = api['run_conclusion'] == 'success'
    if passed:
        require(summary is not None and summary['report_passed'], 'cache_overall_success_evidence')
    proof = {'schema': SCHEMA + 'ci-import.v0.5', 'repository_id': api['repository_id'], 'repository': api['repository'],
        'run_id': run, 'run_attempt': api['run_attempt'], 'source_commit': source.commit,
        'run_conclusion': api['run_conclusion'], 'job': api['job'], 'artifact': api['artifact'],
        'api_metadata_sha256': sha(api_wire), 'archive_identity': archive, 'raw_json': None if raw is None else identity(raw),
        'original_git_sources': {name: identity(wire) for name, wire in sorted(source.cache.items())},
        'verifier_sha256': sha(read(Path(__file__).resolve(), LIMIT)), 'verification': {'overall_positive': passed,
            'report': summary, 'raw_verdicts_rewritten': False, 'private_worker_or_binary_bytes_reconstructed': False,
            'network_or_runtime_used_by_importer': False}}
    old.privacy_gate(proof)
    if not verify_only:
        output = HERE / 'evidence' / f'ci-{run}'
        proof_wire = (json.dumps(proof, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
        files = [(output / 'api-metadata.json', api_wire), (output / 'provenance.json', proof_wire)]
        if raw is not None:
            files.append((output / 'raw/linux.json', raw))
        for path, wire in files:
            require(path.resolve() == path, 'cache_owned_evidence_path')
            if path.exists():
                require(path.is_file() and path.stat().st_size == len(wire) and path.read_bytes() == wire,
                    'cache_immutable_evidence')
        for path, wire in files:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(wire)
    return passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=int, choices=RUNS, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    try:
        passed = import_evidence(args.run, args.verify_only)
    except (KeyError, OSError, RuntimeError, StopIteration, TypeError, ValueError, zipfile.BadZipFile) as error:
        code = str(error) if type(error) is ValueError and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('WordPress cache CI evidence rejected: ' + code, file=sys.stderr)
        sys.exit(1)
    print('WordPress cache CI evidence: ' + ('verified positive' if passed else 'preserved failure/partial'))
