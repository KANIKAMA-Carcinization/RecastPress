"""Verify public preview CI evidence offline and preserve its original bytes."""

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


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'target/wordpress-preview-release/ci'
DESTINATION = ROOT / 'evidence/wordpress-preview-release'
WORKFLOW = '.github/workflows/wordpress-preview-release.yml'
BUILD = 'scripts/build_wordpress_preview_runtime.py'
STAGE = 'scripts/stage_wordpress_preview_release.py'
CHECK = 'scripts/check_wordpress_preview_release.py'
RUNTIME_CHECK = 'scripts/check_wordpress_preview_runtime.py'
OLD_HTTP = 'experiments/php-wasix-wp-session-http-probe/'
OLD_BUILD_RECEIPT = OLD_HTTP + 'evidence/ci-42481084326-mysql/raw/' + OLD_HTTP + 'evidence/linux-build.json'
PREFLIGHT_REPORT = 'wordpress-preview-release-check.json'
RUNTIME_REPORT = 'wordpress-preview-runtime-check.json'
PREFLIGHT_STEP = 'Check relocated release and reject drift'
RUNTIME_STEP = 'Compare WordPress HTTP from the relocated runtime on both databases'
JOB = 'Verify staged WordPress preview release'
SCHEMA = 'recastpress.wordpress-preview-'
RUNS = {
    42487489714: ('619fd46ca5db789229e609b23dca948e2c0193e4', 128752451437),
    42488123831: ('0c358ccfe2c829041499b7a516a0abab4eb7fca6', None),
    42488374173: ('7c51b92d810151f4bcd57ac9b0032a4000f1635e', None),
}
ARCHIVE_LIMIT = 2 * 1024 * 1024
REPORT_LIMIT = 512 * 1024
API_LIMIT = 128 * 1024
REJECTIONS = ['existing_stage', 'wrong_manifest_digest', 'duplicate_manifest_field', 'changed_owned_php',
    'extra_wordpress_dropin', 'linked_owned_php', 'missing_owned_php']


def load(name):
    path = ROOT / name
    spec = importlib.util.spec_from_file_location('rp_preview_evidence_base', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHARED = OLD_HTTP + 'import_evidence.py'
baseline = load(SHARED)
old, session = baseline.old, baseline.session
require, exact, sha, read, identity, parse_json = (
    old.require, old.exact, old.sha, old.read, old.identity, old.parse_json)


def closed(value, fields, code):
    require(type(value) is dict and set(value) == set(fields), code)


def api_gate(api, run):
    closed(api, ('repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha', 'run_conclusion', 'job', 'artifact'),
        'preview_api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'preview_repository_id')
    exact(api['repository'], old.REPOSITORY, 'preview_repository_name')
    old.integer(api['run_id'], 1)
    old.integer(api['run_attempt'], 1, 1000)
    exact(api['run_id'], run, 'preview_run_id')
    require(run in RUNS and api['run_conclusion'] in old.CONCLUSIONS, 'preview_run_conclusion')
    expected_commit, expected_job = RUNS[run]
    exact(api['head_sha'], expected_commit, 'preview_run_source')
    job, artifact = api['job'], api['artifact']
    if job is None:
        require(api['run_conclusion'] != 'success' and artifact is None, 'preview_missing_job')
    else:
        closed(job, ('id', 'name', 'conclusion', 'steps'), 'preview_job_fields')
        old.integer(job['id'], 1)
        if expected_job is not None and api['run_attempt'] == 1:
            exact(job['id'], expected_job, 'preview_job_id')
        require(job['name'] == JOB and job['conclusion'] in old.CONCLUSIONS, 'preview_job_identity')
        require(type(job['steps']) is list and len(job['steps']) <= 64, 'preview_api_steps')
        names = set()
        for step in job['steps']:
            closed(step, ('name', 'conclusion'), 'preview_step_fields')
            name = step['name']
            require(type(name) is str and 0 < len(name) <= 160 and name not in names
                and step['conclusion'] in old.CONCLUSIONS, 'preview_api_step')
            names.add(name)
    if artifact is None:
        require(api['run_conclusion'] != 'success', 'preview_success_artifact_missing')
    else:
        closed(artifact, ('id', 'name', 'size_in_bytes', 'digest'), 'preview_artifact_fields')
        old.integer(artifact['id'], 1)
        old.integer(artifact['size_in_bytes'], 1, ARCHIVE_LIMIT)
        exact(artifact['name'], f"wordpress-preview-release-check-{api['run_attempt']}", 'preview_artifact_name')
        require(type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']),
            'preview_artifact_digest')
    old.privacy_gate(api)


def workflow_gate(api, source):
    wire = source.read(WORKFLOW).decode('utf-8')
    require(f'    name: {JOB}\n' in wire, 'preview_workflow_job')
    names = re.findall(r'^    - name: ([^\n]+)$', wire, re.M)
    require(len(names) == len(set(names)), 'preview_workflow_unique_steps')
    runtime = RUNTIME_STEP in names
    upload = 'Upload only public preview reports' if runtime else 'Upload only the public preflight report'
    require(PREFLIGHT_STEP in names and names[-1] == upload, 'preview_workflow_stage_scope')
    tail = wire.split(f'    - name: {upload}\n')[1]
    require('name: wordpress-preview-release-check-${{ github.run_attempt }}' in tail,
        'preview_workflow_artifact_name')
    paths = re.findall(r'^(?:        path: |          )target/([a-z-]+\.json)$', tail, re.M)
    expected = {PREFLIGHT_REPORT: PREFLIGHT_STEP}
    if runtime:
        expected[RUNTIME_REPORT] = RUNTIME_STEP
    exact(set(paths), set(expected), 'preview_workflow_artifact_members')
    require(len(paths) == len(expected), 'preview_workflow_unique_members')
    if api['job'] is None:
        return expected, {}
    job = api['job']
    statuses = {row['name']: row['conclusion'] for row in job['steps']}
    allowed = set(names) | {'Post ' + name for name in names if name.startswith(('Check out ', 'Preseed '))}
    require(set(statuses) <= allowed | {'Set up job', 'Complete job'}, 'preview_api_workflow_steps')
    observed_order = [name for name in statuses if name in names]
    exact(observed_order, [name for name in names if name in statuses], 'preview_api_workflow_order')
    if api['artifact'] is not None:
        exact(statuses.get(upload), 'success', 'preview_artifact_upload_succeeded')
    if api['run_conclusion'] == 'success':
        exact(job['conclusion'], 'success', 'preview_successful_job')
        require(all(statuses.get(name) == 'success' for name in names), 'preview_successful_workflow_steps')
    return expected, statuses


def archive_gate(path, api, expected, statuses):
    if api['artifact'] is None:
        require(not path.exists() and not path.is_symlink(), 'preview_unbound_zip')
        return {}, None
    wire = read(path, ARCHIVE_LIMIT)
    archive_identity = identity(wire)
    exact(archive_identity, {'bytes': api['artifact']['size_in_bytes'],
        'sha256': api['artifact']['digest'][7:]}, 'preview_archive_identity')
    raw = {}
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(1 <= len(entries) <= len(expected), 'preview_zip_count')
        for entry in entries:
            name = entry.filename
            kind = stat.S_IFMT(entry.external_attr >> 16)
            require(entry.orig_filename == name and name in expected and name not in raw and not entry.is_dir()
                and entry.flag_bits & 1 == 0 and kind in (0, stat.S_IFREG), 'preview_zip_member')
            old.integer(entry.file_size, 0, REPORT_LIMIT)
            with archive.open(entry) as stream:
                body = stream.read(entry.file_size + 1)
            require(len(body) == entry.file_size, 'preview_zip_member_size')
            step = statuses.get(expected[name])
            if body:
                exact(step, 'success', 'preview_report_stage_success')
            else:
                # Shell redirection can leave an empty file when the command
                # fails. Preserve those original bytes as missing observation.
                require(step in ('failure', 'cancelled', 'timed_out'), 'preview_empty_report_stage')
            raw[name] = body
    if api['run_conclusion'] == 'success':
        exact(set(raw), set(expected), 'preview_successful_zip_inventory')
    return raw, archive_identity


def verified_release(value):
    closed(value, ('schema', 'target', 'manifest_sha256', 'release_files', 'wordpress_files',
        'wordpress_directories', 'wordpress_bytes'), 'preview_verification_fields')
    old.digest(value['manifest_sha256'])
    exact(value, {'schema': SCHEMA + 'release-verification.v0.5', 'target': 'linux-x86_64',
        'manifest_sha256': value['manifest_sha256'], 'release_files': 11, 'wordpress_files': 3782,
        'wordpress_directories': 467, 'wordpress_bytes': 110077999}, 'preview_verification_scope')


def preflight_gate(value):
    closed(value, ('schema', 'passed', 'verified_release', 'outside_checkout', 'relocated_directory',
        'rejected', 'staged_worker_executed'), 'preview_preflight_fields')
    verified_release(value['verified_release'])
    exact(value, {'schema': SCHEMA + 'release-check.v0.5', 'passed': True,
        'verified_release': value['verified_release'], 'outside_checkout': True, 'relocated_directory': True,
        'rejected': REJECTIONS, 'staged_worker_executed': False}, 'preview_preflight_checks')


class GitPath(session.SourcePath):
    def __truediv__(self, name):
        return GitPath(self.source, Path(self.name, name).as_posix())

    def resolve(self, strict=False):
        return self


def expected_runtime_sources(source, constants):
    accepted, _, _ = baseline.own_build_source(source)
    root = GitPath(source)
    functions = source.functions(BUILD, ['sources'], {'ROOT': root, 'EXPERIMENTS': root / 'experiments',
        '__file__': BUILD, 'Path': lambda name: GitPath(source, name), 'CRATES': constants['CRATES'],
        'MAX_SOURCE': constants['MAX_SOURCE'], 'identity': lambda path, limit: identity(path.read_bytes())})
    return functions['sources'](SimpleNamespace(inputs=lambda: accepted))


def expected_derivations(source, constants):
    root = GitPath(source)
    http = root / ('experiments/' + constants['CRATES']['http'])
    worker = root / ('experiments/' + constants['CRATES']['worker'])
    http_name = (http / 'derive.py').name
    http_pins = source.constants(http_name)

    def pinned_http(name):
        wire = source.read(OLD_HTTP + name)
        exact(sha(wire), http_pins['INPUTS'][name], 'preview_http_derivation_pin')
        return wire

    http_functions = source.functions(http_name, ['replace', 'derive'],
        {**http_pins, 'HERE': http, 'pinned': pinned_http})
    worker_name = (worker / 'derive.py').name
    worker_constants = source.constants(worker_name)
    worker_base = root / 'experiments/php-wasix-wp-session-probe'
    catalog = root / 'experiments/php-wasix-wp-request-probe/src/catalog.rs'
    # The worker's path-keyed pins are not literal constants. Extract only
    # their digest values and bind the four fixed inputs independently.
    pin_node = next(node.value for node in source.tree(worker_name).body if isinstance(node, ast.Assign)
        and any(isinstance(key, ast.Name) and key.id == 'PINS' for key in node.targets))
    require(isinstance(pin_node, ast.Dict) and len(pin_node.values) == 4, 'preview_worker_derivation_pins')
    paths = (worker_base / 'src/main.rs', catalog, worker_base / 'Cargo.toml', worker_base / 'Cargo.lock')
    pins = {path.name: ast.literal_eval(value) for path, value in zip(paths, pin_node.values)}

    def pinned_worker(path):
        wire = path.read_bytes()
        exact(sha(wire), pins[path.name], 'preview_worker_derivation_pin')
        return wire

    worker_functions = source.functions(worker_name, ['once', 'section', 'worker_source', 'catalog_source'],
        {**worker_constants, 'SESSION': worker_base, 'CATALOG': catalog, 'source': pinned_worker})
    for name in ('Cargo.toml', 'Cargo.lock'):
        old_wire = pinned_worker(worker_base / name)
        before, after = worker_constants['SESSION_NAME'], worker_constants['NAME']
        exact(old_wire.count(before), 1, 'preview_worker_package_rename_count')
        exact((worker / name).read_bytes(), old_wire.replace(before, after), 'preview_worker_dependency_derivation')
    return {'http': {(http / 'src/main.rs').name: identity(http_functions['derive']())},
        'worker': {(worker / 'src/main.rs').name: identity(worker_functions['worker_source']()),
            (worker / 'src/catalog.rs').name: identity(worker_functions['catalog_source']())}}


def command_gate(row, stage, deadline, constants):
    closed(row, ('stage', 'owned_pid', 'owned_reaped', 'exit_code', 'timeout_seconds', 'elapsed_seconds',
        'stdout_bytes', 'stdout_sha256', 'stderr_bytes', 'stderr_sha256'), 'preview_owned_command_fields')
    exact(row['stage'], stage, 'preview_owned_command_stage')
    old.integer(row['owned_pid'], 1)
    exact(row['owned_reaped'], True, 'preview_owned_command_reaped')
    exact(row['exit_code'], 0, 'preview_owned_command_exit')
    exact(row['timeout_seconds'], deadline, 'preview_owned_command_deadline')
    require(type(row['elapsed_seconds']) in (int, float) and 0 <= row['elapsed_seconds'] <= deadline + 5,
        'preview_owned_command_elapsed')
    for stream, maximum in [('stdout', constants['MAX_STDOUT']), ('stderr', constants['MAX_STDERR'])]:
        old.integer(row[stream + '_bytes'], 0, maximum)
        old.digest(row[stream + '_sha256'])


def build_gate(value, source):
    closed(value, ('schema', 'platform', 'http', 'worker', 'commands', 'derivations', 'inputs_before',
        'inputs_after', 'driver', 'source_fs_registry', 'source_reverified_after'), 'preview_runtime_build_fields')
    exact(value['schema'], SCHEMA + 'runtime-build.v0.5', 'preview_runtime_build_schema')
    exact(value['platform'], 'linux-x86_64', 'preview_runtime_build_platform')
    exact(value['source_reverified_after'], True, 'preview_runtime_source_reverified')
    constants = source.constants(BUILD)
    paths = source.constants(STAGE)['RELOCATED_BINARIES']
    for role, path in paths.items():
        binary = value[role]
        closed(binary, ('executable', 'executable_identity'), 'preview_runtime_binary_fields')
        exact(binary['executable'], path, 'preview_runtime_executable_path')
        baseline.identity_gate(binary['executable_identity'], 256 * 1024 * 1024, 1)
    expected_inputs = expected_runtime_sources(source, constants)
    exact(value['inputs_before'], expected_inputs, 'preview_runtime_complete_input_map')
    exact(value['inputs_after'], expected_inputs, 'preview_runtime_unchanged_input_map')
    derivations = expected_derivations(source, constants)
    exact(value['derivations'], derivations, 'preview_runtime_derived_source_identities')
    accepted = parse_json(source.read(OLD_BUILD_RECEIPT))
    exact(value['driver'], accepted['driver_before'], 'preview_runtime_fixed_driver_inventory')
    exact(value['source_fs_registry'], accepted['source_fs_registry_before'], 'preview_runtime_fixed_fs_inventory')
    rows = value['commands']
    require(type(rows) is list and len(rows) == 4, 'preview_runtime_command_count')
    commands = [('derive_http', constants['DERIVE_TIMEOUT']), ('derive_worker', constants['DERIVE_TIMEOUT']),
        ('build_http', constants['BUILD_TIMEOUT']), ('build_worker', constants['BUILD_TIMEOUT'])]
    for row, (stage, deadline) in zip(rows, commands):
        command_gate(row, stage, deadline, constants)
    http_stdout = next(iter(derivations['http'].values()))['sha256'].encode('ascii') + b'\n'
    for row, expected in zip(rows, (http_stdout, b'')):
        exact({'bytes': row['stdout_bytes'], 'sha256': row['stdout_sha256']}, identity(expected),
            'preview_runtime_derivation_stdout')
        exact({'bytes': row['stderr_bytes'], 'sha256': row['stderr_sha256']}, identity(b''),
            'preview_runtime_derivation_stderr')
    return len(expected_inputs)


def runtime_fields(source):
    matches = []
    for node in ast.walk(source.tree(RUNTIME_CHECK)):
        if isinstance(node, ast.Dict) and any(isinstance(value, ast.Constant)
                and value.value == SCHEMA + 'runtime-check.v0.5' for value in node.values):
            matches.append(node)
    require(len(matches) == 1, 'preview_source_runtime_report')
    fields = {ast.literal_eval(key): value for key, value in zip(matches[0].keys, matches[0].values)}
    expected = {'schema', 'passed', 'verified_release', 'outside_checkout', 'relocated_directory', 'build', 'backends'}
    launcher = {'product_cli_launch_verified', 'product_cli_wrong_manifest_refused'}
    if launcher & set(fields):
        require(launcher <= set(fields), 'preview_product_cli_source_fields')
        for name in launcher:
            exact(ast.literal_eval(fields[name]), True, 'preview_product_cli_source_claim')
        expected.update(launcher)
    exact(set(fields), expected, 'preview_source_runtime_report_fields')
    return expected


def runtime_gate(value, source):
    fields = runtime_fields(source)
    closed(value, fields, 'preview_runtime_fields')
    exact(value['schema'], SCHEMA + 'runtime-check.v0.5', 'preview_runtime_schema')
    for flag in fields - {'schema', 'verified_release', 'build', 'backends'}:
        exact(value[flag], True, 'preview_runtime_scope_' + flag)
    verified_release(value['verified_release'])
    inputs = build_gate(value['build'], source)
    native = session.native_references(source)
    closed(value['backends'], ('mysql', 'mariadb'), 'preview_runtime_backend_fields')
    for backend, observed in value['backends'].items():
        responses = [{name: native[backend]['requests'][label]['observation']['response'][name]
            for name in ('status', 'body_bytes', 'body_sha256')} for label in ('home', 'missing', 'missing_repeat')]
        exact(observed, {'responses': responses, 'rejected_requests': 7, 'worker_processes': 1,
            'module_compilations_total': 1, 'request_module_compilations_total': 0,
            'owned_processes_reaped': True, 'owned_database_normal_cleanup': True}, 'preview_runtime_backend_observations')
    return inputs, 'product_cli_launch_verified' in fields


def import_evidence(run, verify_only=False):
    api_wire = read(WORK / f'api-{run}.json', API_LIMIT)
    api = parse_json(api_wire)
    api_gate(api, run)
    source = baseline.LocalGitSource(api['head_sha'])
    helpers = (SHARED, baseline.SHARED, session.SHARED, baseline.latest.SHARED, session.private.SHARED,
        baseline.http.SHARED, baseline.context.SHARED, session.theme.SHARED, baseline.base.LEGACY)
    for name in dict.fromkeys(helpers):
        exact(read(ROOT / name, REPORT_LIMIT), source.read(name), 'preview_frozen_verification_helper_changed')
    expected, statuses = workflow_gate(api, source)
    raw, archive_identity = archive_gate(WORK / f'run-{run}.zip', api, expected, statuses)
    verified, source_count, product_cli = [], None, False
    for name, wire in raw.items():
        if not wire:
            continue
        value = parse_json(wire)
        old.privacy_gate(value)
        if name == PREFLIGHT_REPORT:
            preflight_gate(value)
        else:
            source_count, product_cli = runtime_gate(value, source)
        verified.append(name)
    passed = api['run_conclusion'] == 'success'
    if passed:
        exact(set(verified), set(expected), 'preview_all_successful_reports_verified')
    proof = {'schema': SCHEMA + 'ci-import.v0.5', 'repository_id': api['repository_id'],
        'repository': api['repository'], 'run_id': run, 'run_attempt': api['run_attempt'],
        'source_commit': api['head_sha'], 'run_conclusion': api['run_conclusion'], 'job': api['job'],
        'artifact': api['artifact'], 'archive_identity': archive_identity, 'api_metadata_sha256': sha(api_wire),
        'raw_json': {name: identity(wire) for name, wire in raw.items()},
        'git_inputs': {name: identity(wire) for name, wire in sorted(source.cache.items())},
        'verifier_sha256': sha(read(Path(__file__).resolve(), REPORT_LIMIT)),
        'verification': {'overall_positive': passed, 'verified_reports': sorted(verified),
            'empty_reports': sorted(name for name, wire in raw.items() if not wire),
            'runtime_source_inputs': source_count, 'runtime_generated_source_rederived': source_count is not None,
            'product_cli_launch_verified': product_cli, 'actual_binary_or_http_bytes_recomputed_offline': False,
            'release_manifest_reconstructed_offline': False,
            'raw_verdicts_rewritten': False, 'network_or_runtime_used_by_importer': False}}
    old.privacy_gate(proof)
    proof_wire = (json.dumps(proof, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    if not verify_only:
        destination = DESTINATION / f'ci-{run}'
        outputs = [(destination / 'api-metadata.json', api_wire), (destination / 'provenance.json', proof_wire)]
        outputs += [(destination / 'raw' / name, wire) for name, wire in raw.items()]
        for path, wire in outputs:
            require(path.resolve() == path, 'preview_evidence_path')
            if path.exists():
                require(path.is_file() and path.stat().st_size == len(wire) and path.read_bytes() == wire,
                    'preview_evidence_immutable')
        for path, wire in outputs:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(wire)
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=int, choices=RUNS, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    try:
        proof = import_evidence(args.run, args.verify_only)
    except (KeyError, OSError, RuntimeError, StopIteration, TypeError, ValueError, zipfile.BadZipFile) as error:
        code = str(error) if type(error) is ValueError and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('WordPress preview evidence rejected: ' + code, file=sys.stderr)
        sys.exit(1)
    print('WordPress preview evidence: ' + ('verified positive' if proof['verification']['overall_positive']
        else 'preserved failure/partial'))
