"""Offline audit of the fixed clean-request native evidence; never runs PHP/DB."""
import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BASE = 'experiments/wp-request-context-probe/'
SOURCE = 'afe8781256bfa490cd70c810b6c43c8c6ef23795'
INITIAL = '0bee0e570885477de2f813de5ac3d5cecf1bf0fd'
LIMIT = 8 * 1024 * 1024
INPUTS = {}
CACHE = {}


def require(value, code):
    if not value: raise ValueError(code)


def sha(wire): return hashlib.sha256(wire).hexdigest()
def identity(wire): return {'bytes': len(wire), 'sha256': sha(wire)}
def canonical(value): return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')
def exact(left, right, code): require(canonical(left) == canonical(right), code)


def read(name):
    parts = PurePosixPath(name)
    require(not parts.is_absolute() and '..' not in parts.parts, 'audit_relative_path')
    path = ROOT / name
    require(path.resolve(strict=True) == path and path.is_file() and 0 < path.stat().st_size <= LIMIT, 'audit_fixed_file')
    wire = path.read_bytes(); require(len(wire) <= LIMIT, 'audit_file_quota')
    INPUTS[name] = identity(wire); return wire


def git_source(commit, name):
    require(commit in (SOURCE, INITIAL) and not name.startswith('/') and '..' not in PurePosixPath(name).parts
        and re.fullmatch(r'[A-Za-z0-9_./-]+', name), 'audit_fixed_git_source')
    key = (commit, name)
    if key not in CACHE:
        # Fixed commits and finite source names only; no checkout, mutation or network.
        result = subprocess.run(['git', 'show', commit + ':' + name], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
        require(result.returncode == 0 and 0 < len(result.stdout) <= LIMIT, 'audit_git_blob')
        CACHE[key] = result.stdout
    return CACHE[key]


def function(commit, name, function_name):
    source = git_source(commit, name).decode('utf-8')
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == function_name]
    require(len(nodes) == 1, 'audit_unique_source_function')
    return ast.get_source_segment(source, nodes[0])


def contracts():
    runner = BASE + 'database_check.py'
    namespace = {'prepared': SimpleNamespace(CASES={
        'home': {'uri': '/', 'query': '', 'get': []},
        'missing': {'uri': '/?p=2147483647', 'query': 'p=2147483647', 'get': {'p': '2147483647'}}}), 'sha': sha}
    exec(compile(function(SOURCE, runner, 'expected_snapshot'), '<frozen-request-snapshot>', 'exec'), namespace)
    old = function(SOURCE, 'experiments/wp-request-probe/database_check.py', 'validate_home')
    derived = old
    changes = [('def validate_home(value, mapping):', 'def validate_response(value, mapping, case):'),
        ('recastpress.wp-request-native-observation.v0.5', 'recastpress.wp-request-context-native-observation.v0.5'),
        ('fixed_native_home_passed', 'fixed_native_context_passed'),
        ("response['status'] == 200", "response['status'] == (200 if case == 'home' else 404)"),
        ('full_native_cgi_home_observed', 'full_native_cgi_request_observed')]
    for before, after in changes:
        require(derived.count(before) == 1, 'audit_response_derivation'); derived = derived.replace(before, after)
    validation = {'db': SimpleNamespace(require=require, sha=sha), 'exact': exact, 're': re, 'BODY_LIMIT': 1024*1024}
    exec(compile(derived, '<frozen-response-validation>', 'exec'), validation)
    return namespace['expected_snapshot'], validation['validate_response'], {
        'original_function_sha256': sha(old.encode()), 'derived_function_sha256': sha(derived.encode()),
        'changes': 5, 'actual_response_modified': False}


def bind_sources(value, initial):
    mapping = value['source_before']['new_sources']
    names = set(initial['source_before']['new_sources'])
    require(len(names) == len(mapping) == 40 and set(mapping) == names, 'audit_finite_40_source_names')
    references = {name for name in names if name.startswith('.reference/')}
    require(len(references) == 5, 'audit_five_reference_metadata_files')
    for name, digest in mapping.items():
        require(re.fullmatch('[0-9a-f]{64}', digest) is not None, 'audit_source_sha_shape')
        if name in references:
            require(digest == initial['source_before']['new_sources'][name], 'audit_reference_metadata_binding')
        else:
            require(sha(git_source(SOURCE, name)) == digest, 'audit_runtime_git_source_binding')
    exact(value['preparation']['input_files'], mapping, 'audit_prepared_input_map')
    exact(value['source_after'], value['source_before'], 'audit_source_before_after')
    exact(value['source_before']['old'], initial['source_before']['old'], 'audit_old_source_and_package_binding')
    exact(value['source_before']['php_package'], initial['source_before']['php_package'], 'audit_php_package_before_after_binding')
    package = value['source_before']['php_package']
    require(type(package['verified_files']) is dict and len(package['verified_files']) == 78
        and package['all_78_files_and_archive_members_verified'] is True,
        'audit_recorded_native_package_gate')
    require(value['preparation']['derivation']['original_core_files_modified'] is False
        and value['preparation']['derivation']['original_wp_load_order_changed'] is False
        and value['preparation']['derivation']['original_wp_and_template_loader_calls_unchanged'] is True,
        'audit_original_dispatch_source_gate')
    return {'source_files': 40, 'git_source_blobs': 35, 'reference_metadata_pins_from_immutable_initial_proof': 5}


def request(value, label, expected_snapshot, validate_response, derivation):
    row = value['requests'][label]; case = 'home' if label == 'home' else 'missing'
    require(row['case'] == case and row['context_file_used'] is True and row['stdin_bytes'] == 0
        and row['cli_options_used'] is False and row['unknown_capture_bytes_published'] is False
        and row['absolute_environment_values_recorded'] is False and row['owned_scratch_reclaimed'] is True,
        'audit_request_transport_and_scratch')
    process = row['process']
    require(process['exit_code'] == 0 and process['owned_reaped'] is True and process['stdout_eof'] is True
        and process['stderr_eof'] is True and process['live_capture_limit_each'] == 3145728
        and process['deadline_seconds'] == 180, 'audit_owned_cgi_completion')
    exact(row['stderr'], identity(b''), 'audit_no_stderr')
    observation = row['observation']
    require(observation['case'] == case and observation['is_home'] is (case == 'home')
        and observation['is_404'] is (case == 'missing'), 'audit_actual_wp_route')
    result = validate_response(observation, value['source_before']['old']['prepared'], case)
    exact(row['validation'], result, 'audit_original_response_validation')
    exact(value['response_validation_derivation'], derivation, 'audit_response_validator_source')
    exact(observation['transport_body'], identity(b''), 'audit_empty_http_body')
    for stage in ('before_bootstrap', 'after_bootstrap', 'after_dispatch'):
        exact(observation['context_' + stage], expected_snapshot(case, stage), 'audit_exact_request_context')
    before = row['owned_files_before']
    require(set(before) == {case + '.php', 'bootstrap.php', 'context.php', 'context.json', 'php.ini'}, 'audit_five_owned_files')
    exact(row['owned_files_after'], before, 'audit_owned_file_before_after')
    for name in (case + '.php', 'bootstrap.php', 'context.php'):
        exact(before[name], identity(git_source(SOURCE, BASE + 'fixtures/' + name)), 'audit_copied_fixture_source')
    exact(observation['private_context_before'], before['context.json'], 'audit_recorded_private_file_before')
    exact(observation['private_context_after'], before['context.json'], 'audit_recorded_private_file_after')
    exact(observation['ini_profile'], {'owned_file_loaded': True, 'additional_ini_scanned': False,
        'display_errors': '1', 'log_errors': '0', 'register_argc_argv': '0', 'allow_url_fopen': '0',
        'mail_function_present': False, 'mysqli_loaded': True, 'expose_php': '1'}, 'audit_sealed_ini')
    response = observation['response']
    require(not any(bytes.fromhex(h).lower().startswith(b'location:') for h in response['headers_hex']), 'audit_no_redirect')
    # Public JSON has only known synthetic data. Reconstruct exactly the emitter's
    # insertion-order serializer, not the private context or original raw stdin.
    body = (json.dumps(observation, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    header = b'Content-Type: application/json\r\n\r\n'; wire = header + body
    outer = row['outer_response']
    exact(outer, {'status': 200, 'headers': [{'name_hex': b'Content-Type'.hex(),
        'value_hex': b' application/json'.hex(), 'line_hex': b'Content-Type: application/json'.hex()}],
        'header_bytes': len(header), 'raw_header_sha256': sha(header), 'body_bytes': len(body), 'body_sha256': sha(body),
        'complete_stdout_bytes': len(wire), 'complete_stdout_sha256': sha(wire), 'header_or_body_normalized': False}, 'audit_complete_cgi_wire')
    exact(row['stdout'], identity(wire), 'audit_stdout_identity')
    if label != 'missing_repeat': exact(value['observations'][case], observation, 'audit_request_projection')
    return {'status': response['status'], 'body': {'bytes': response['body_bytes'], 'sha256': response['body_sha256']},
        'headers_sha256': sha(canonical(response['headers_hex'])), 'complete_cgi_wire': identity(wire)}


def backend(name, expected_snapshot, validate_response, derivation):
    path = BASE + 'evidence/windows-' + name + '-native.json'
    initial_wire = git_source(INITIAL, path); initial = json.loads(initial_wire)
    require(git_source(SOURCE, path) == initial_wire, 'audit_initial_proof_preserved_in_runtime_commit')
    backup = BASE + 'evidence/windows-' + name + '-native-before-' + sha(initial_wire)[:16] + '.json'
    require(read(backup) == initial_wire, 'audit_initial_backup_byte_identity')
    require(initial['passed'] is True, 'audit_initial_raw_passed_preserved')
    require('environment_query' not in initial['observations']['missing']['context_before_bootstrap'], 'audit_initial_known_snapshot_omission')
    value = json.loads(read(path))
    require(value['schema'] == 'recastpress.wp-request-context-native-check.v0.5' and value['backend'] == name
        and value['passed'] is True and value['outcome'] == 'clean_native_home_and_repeat_404_passed', 'audit_current_native_success')
    for key in ('normal_admin_shutdown_verified', 'database_closed_and_reaped', 'all_command_children_reaped',
        'all_owned_cgi_reaped', 'owned_database_cleanup_completed', 'source_reverified_after',
        'original_install_read_baseline_reproduced', 'fresh_repeat_404_response_exact', 'native_only'):
        require(value[key] is True, 'audit_cleanup_and_completion')
    for key in ('wasm_executed', 'rust_db_executed', 'http_listener_exercised', 'product_runtime_selected', 'normalization_applied'):
        require(value[key] is False, 'audit_no_broader_runtime_claim')
    require(type(value['owned_cgi_count']) is int and value['owned_cgi_count'] == value['owned_cgi_reaped'] == 3,
        'audit_three_fresh_cgi_children')
    cleanup = value['database_cleanup']
    require(cleanup['exit_code'] == 0 and cleanup['reaped'] is True and cleanup['forced_tree_cleanup'] is False
        and cleanup['direct_captured_kill_used'] is False, 'audit_normal_database_shutdown')
    sources = bind_sources(value, initial)
    checks = value['independent_request_checks']
    require(set(checks) == {'before_requests', 'home', 'missing', 'missing_repeat'}, 'audit_independent_four_state_checks')
    for label, check in checks.items():
        require(type(check['missing_post_count']) is int and check['missing_post_count'] == 0
            and check['fixed_public_dates_verified'] is True and check['original_public_state_verified'] is True,
            'audit_independent_missing_post_and_state')
        if label != 'before_requests':
            observation = value['requests'][label]['observation']
            require(check['autoload']['option_count'] == observation['autoload_option_count']
                and check['autoload']['maximum_value_bytes'] == observation['autoload_max_value_bytes'],
                'audit_independent_autoload')
    require(set(value['requests']) == {'home', 'missing', 'missing_repeat'}, 'audit_finite_three_requests')
    requests = {label: request(value, label, expected_snapshot, validate_response, derivation)
        for label in ('home', 'missing', 'missing_repeat')}
    exact(value['observations']['missing']['response'], value['requests']['missing_repeat']['observation']['response'], 'audit_repeat_404_exact')
    old_path = 'experiments/wp-request-probe/evidence/windows-' + name + '-native.json'
    old = json.loads(git_source(SOURCE, old_path))
    require(old['passed'] is True, 'audit_old_home_accepted_reference')
    exact(value['observations']['home']['response'], old['observations']['home']['response'], 'audit_old_complete_home_response')
    return value, {'evidence': INPUTS[path], 'initial_backup': INPUTS[backup], 'initial_recorded_passed': True,
        'initial_query_environment_gate_complete': False, 'initial_clean_context_accepted_by_this_audit': False,
        'historical_raw_verdict_rewritten': False, 'sources': sources, 'requests': requests,
        'old_home_complete_response_equal': True, 'fresh_repeat_404_complete_response_equal': True}


def audit(record=False):
    INPUTS.clear(); read('scripts/verify_request_context_native.py')
    expected_snapshot, validate_response, derivation = contracts()
    values = {}; rows = {}
    for name in ('mysql', 'mariadb'):
        values[name], rows[name] = backend(name, expected_snapshot, validate_response, derivation)
    for case in ('home', 'missing'):
        exact(values['mysql']['observations'][case]['response'], values['mariadb']['observations'][case]['response'], 'audit_cross_backend_complete_response')
    proof = {'schema': 'recastpress.wp-request-context-native-offline-audit.v0.5', 'passed': True,
        'runtime_source_commit': SOURCE, 'initial_evidence_commit': INITIAL, 'backends': rows,
        'both_backends_whole_home_and_404_equal': True, 'input_files': dict(INPUTS),
        'git_source_blob_identities': {commit + ':' + name: identity(wire) for (commit, name), wire in sorted(CACHE.items())},
        'raw_verdicts_rewritten': False, 'request_or_response_normalized': False,
        'php_wasm_db_or_network_executed': False, 'binary_archive_or_private_context_reverified_offline': False,
        'native_package_before_after_claims_bound_to_recorded_evidence_only': True, 'product_runtime_selected': False}
    wire = (json.dumps(proof, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    require(len(wire) <= 128*1024, 'audit_output_quota')
    if record:
        output = ROOT / BASE / 'evidence/clean-context-offline-audit.json'
        require(output.resolve() == output and output.parent.resolve() == output.parent, 'audit_output_path')
        if output.exists(): require(output.read_bytes() == wire, 'audit_immutable_output')
        else:
            with output.open('xb') as stream: stream.write(wire)
    return proof


def self_test():
    expected, validate_response, derivation = contracts()
    row = expected('missing', 'after_dispatch')
    require(row['environment_query_present'] is True and row['environment_query'] == 'p=2147483647', 'qa_fixed_query_gate')
    for key in ('environment_query', 'environment_query_present', 'display_errors', 'input'):
        mutant = copy.deepcopy(row); mutant.pop(key)
        try: exact(mutant, row, 'qa_rejected')
        except ValueError: pass
        else: raise ValueError('qa_mutant_accepted')
    require(b"putenv('QUERY_STRING='" in git_source(SOURCE, BASE + 'fixtures/context.php'), 'qa_public_query_source')
    # In-memory mutants only: never replace or relabel an actual receipt.
    actual = json.loads(read(BASE + 'evidence/windows-mysql-native.json'))
    request(actual, 'missing', expected, validate_response, derivation)
    changes = [
        ('snapshot_query', lambda v: v['requests']['missing']['observation']['context_after_bootstrap'].update(environment_query='rp_request=1')),
        ('private_file', lambda v: v['requests']['missing']['observation']['private_context_after'].update(sha256='0'*64)),
        ('stdout_eof', lambda v: v['requests']['missing']['process'].update(stdout_eof=False)),
        ('scratch_cleanup', lambda v: v['requests']['missing'].update(owned_scratch_reclaimed=False)),
        ('copied_source', lambda v: v['requests']['missing']['owned_files_before']['context.php'].update(sha256='0'*64)),
        ('inner_status', lambda v: v['requests']['missing']['observation']['response'].update(status=200)),
        ('whole_wire', lambda v: v['requests']['missing']['stdout'].update(sha256='0'*64)),
        ('json_header', lambda v: v['requests']['missing']['outer_response'].update(headers=[])),
    ]
    for _, change in changes:
        mutant = copy.deepcopy(actual); change(mutant)
        try: request(mutant, 'missing', expected, validate_response, derivation)
        except ValueError: pass
        else: raise ValueError('qa_evidence_mutant_accepted')
    initial = json.loads(git_source(INITIAL, BASE + 'evidence/windows-mysql-native.json'))
    mutant = copy.deepcopy(actual)
    mutant['source_before']['new_sources'][BASE + 'fixtures/context.php'] = '0'*64
    try: bind_sources(mutant, initial)
    except ValueError: pass
    else: raise ValueError('qa_source_mutant_accepted')
    return {'passed': True, 'snapshot_missing_field_mutants_rejected': 4,
        'in_memory_evidence_and_source_mutants_rejected': 9, 'php_db_or_wasm_executed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', action='store_true'); parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    result = self_test() if args.self_test else audit(args.record)
    print('Fixed native request-context ' + ('source QA' if args.self_test else 'offline audit') + ' passed')
