"""Offline immutable import of eight framing CI JSON files; executes no runtimes."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import re
import stat
import sys
import tomllib
from types import SimpleNamespace
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREFIX = 'experiments/php-wasix-cgi-framing-probe/'
NATIVE = 'experiments/php-cgi-framing-probe/'
THEME = 'experiments/php-wasix-wp-request-probe/'
BOOT = 'experiments/php-wasix-wp-bootstrap-probe/'
WORKFLOW = '.github/workflows/php-wasix-cgi-framing.yml'
SHARED = BOOT + 'import_evidence.py'
NATIVE_SHA = 'cb14bab903650d7ab41dac3f65055851cc19c8a22d8ca096e8c83d0bc4b17ab9'
spec = importlib.util.spec_from_file_location('rp_framing_offline_bootstrap_helpers', ROOT / SHARED)
base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)
old = base.old
require, sha, read, identity, exact, canonical, parse_json = (
    base.require, base.sha, base.read, base.identity, base.exact, base.canonical, base.parse_json)


def member_map():
    return {'run.json': PREFIX + 'evidence/linux-check.json',
        'host-build.json': PREFIX + 'evidence/linux-build.json',
        'bootstrap-build.json': BOOT + 'evidence/linux-build.json',
        'ci-stages.json': PREFIX + 'evidence/ci-stages.json',
        'build.json': old.MANAGED + 'evidence/build.json',
        'toolchain.json': old.CGI + 'evidence/toolchain.json',
        'prepared.json': 'target/php-wasix-cgi-framing-probe/prepared.json',
        'native-prepared.json': 'target/php-cgi-framing-probe/prepared.json'}


def native_contract(source):
    wire = source.read(NATIVE + 'evidence/windows-native.json')
    require(sha(wire) == NATIVE_SHA, 'fixed_native_framing_proof')
    proof = parse_json(wire)
    require(proof['passed'] is True and proof['outcome'] == 'native_all_eight_cgi_frames_exact'
        and proof['source_reverified_after'] is True and proof['owned_cleanup_completed'] is True
        and proof['owned_scratch_reclaimed'] is True
        and proof['owned_child_count'] == proof['owned_reaped_count'] == 8, 'native_framing_acceptance')
    paths = [NATIVE + 'fixtures/common.php', NATIVE + 'prepare.py', NATIVE + 'check.py']
    paths += [NATIVE + 'fixtures/' + str(Path(name).name) for name in source.names(NATIVE + 'fixtures/')
        if Path(name).name.startswith('case-') and name.endswith('.php')]
    require(len(paths) == len(set(paths)) == 11, 'native_eleven_sources')
    files = {name: identity(source.read(name)) for name in paths}
    exact(proof['input_files'], files, 'native_complete_source_bindings')
    name = NATIVE + 'prepare.py'
    pure = source.functions(name, ['cases', 'wrapper', 'expected', 'encoded', 'prepare'], {
        **source.constants(name), 'identity': identity, 'sources': lambda: files})
    native_prepared = pure['prepare'](record=False)
    cases = list(pure['cases']()); require(len(cases) == 8, 'eight_native_cases')
    require(len(proof['observations']) == 8, 'eight_native_observations')
    rows, wires = [], {}
    for case, observed in zip(cases, proof['observations']):
        script = NATIVE + 'fixtures/' + case['script']
        exact(source.read(script), pure['wrapper'](case), 'unchanged_native_php_wrapper')
        _, headers, body = pure['expected'](case)
        frame = b'\r\n'.join(headers) + b'\r\n\r\n' + body
        require(observed['case'] == case['case'] and observed['complete_cgi_wire_matches'] is True
            and observed['process']['exit_code'] == 0 and observed['process']['owned_reaped'] is True
            and observed['process']['stdout_eof'] is True and observed['process']['stderr_eof'] is True,
            'accepted_native_case')
        exact(observed['stdout'], identity(frame), 'fixed_native_complete_wire')
        exact(observed['stderr'], identity(b''), 'fixed_native_empty_stderr')
        exact(observed['response']['body'], identity(body), 'fixed_native_body')
        exact(observed['response']['header_lines_hex'], [h.hex() for h in headers], 'fixed_native_headers')
        row = {**case, 'expected_body': identity(body), 'expected_status': 200,
            'expected_header_lines_hex': [h.hex() for h in headers],
            'expected_cgi_wire': identity(frame), 'expected_cgi_wire_hex': frame.hex()}
        rows.append(row); wires[case['case']] = frame
    return {'proof': {'path': NATIVE + 'evidence/windows-native.json', **identity(wire), 'all_eight_exact': True},
        'prepared': native_prepared, 'files': files, 'rows': rows, 'wires': wires, 'pure': pure}


def source_inventory(source):
    anchor = parse_json(source.read(PREFIX + 'evidence/windows-build.json'))
    require(anchor['passed'] is True and anchor['source_reverified_after'] is True, 'mini_windows_source_anchor')
    mapping = anchor['input_files']; require(len(mapping) == 191, 'complete_mini_191_sources')
    source.bindings(mapping)
    local = {name for name in source.names(PREFIX) if '/evidence/' not in name and '__pycache__' not in name
        and Path(name).name != 'import_evidence.py'
        and (Path(name).suffix in ('.rs', '.py', '.toml') or Path(name).name == 'Cargo.lock')}
    require(local <= set(mapping) and {WORKFLOW, THEME + 'src/warning_diagnostic.rs',
        THEME + 'src/capture.rs', THEME + 'src/access_fs.rs'} <= set(mapping), 'mini_closed_code_inventory')
    return anchor


def derivation(source):
    original = source.read(THEME + 'src/main.rs'); current = source.read(PREFIX + 'src/main.rs')
    first = original.index(b'fn fixed('); last = original.index(b'fn finite(', first)
    block = original[first:last]; require(current.count(block) == 1, 'unchanged_module_verifier_bodies')
    old_name = b'recastpress-php-wasix-wp-request-probe'; new_name = b'recastpress-php-wasix-cgi-framing-probe'
    for name in ['Cargo.toml', 'Cargo.lock']:
        wire = source.read(THEME + name)
        require(wire.count(old_name) == 1 and wire.replace(old_name, new_name) == source.read(PREFIX + name),
            'workspace_package_name_only')
    return {'fixed_and_module_verifier_sha256': sha(block), 'fixed_and_module_verifier_bodies_unchanged': True,
        'workspace_package_name_only': True, 'old_sources_modified': False}


def prepared_contract(source, native, anchor):
    constants = source.constants(PREFIX + 'prepare.py')
    require(constants['PROOF_SHA'] == NATIVE_SHA, 'preparer_fixed_native_pin')
    return source.functions(PREFIX + 'prepare.py', ['prepare'], {
        'inputs': lambda: anchor['input_files'], 'native_gate': lambda: native['proof'],
        'derivation': lambda: derivation(source), 'native': SimpleNamespace(
            cases=native['pure']['cases'], expected=native['pure']['expected'], identity=identity,
            sources=lambda: native['files']), 'WORK': ROOT / 'target/php-wasix-cgi-framing-probe'})['prepare'](record=False)


def warning_gate(value, source):
    exact(set(value), {'prefix_bytes_examined', 'prefix_truncated', 'message_counts', 'builtin_counts', 'source_lines'},
        'closed_warning_fields')
    old.integer(value['prefix_bytes_examined'], 0, 4096)
    require(type(value['prefix_truncated']) is bool, 'warning_truncation_type')
    for name, keys in [('message_counts', {'cannot_modify_headers_already_sent', 'cannot_set_status_headers_already_sent'}),
            ('builtin_counts', {'header_remove', 'http_response_code', 'header'})]:
        exact(set(value[name]), keys, 'closed_warning_counter_names')
        for count in value[name].values(): old.integer(count, 0, 4096)
    exact(set(value['source_lines']), {'framing_common'}, 'closed_warning_source')
    lines = len(source.read(NATIVE + 'fixtures/common.php').decode().splitlines())
    scope = value['source_lines']['framing_common']
    exact(set(scope), {'output_started_at', 'warning_at'}, 'closed_source_line_roles')
    for rows in scope.values():
        require(type(rows) is list and len(rows) <= lines, 'bounded_warning_line_list')
        seen = set()
        for row in rows:
            exact(set(row), {'line', 'count'}, 'closed_warning_line_fields')
            old.integer(row['line'], 1, lines); old.integer(row['count'], 1, 4096)
            require(row['line'] not in seen, 'unique_warning_source_lines'); seen.add(row['line'])


def framing_gate(value, source):
    exact(set(value), {'actual', 'complete_cgi_wire_matches', 'classification', 'expected_prefix_match_bytes',
        'expected_suffix_match_bytes', 'lf_bytes', 'cr_bytes', 'exact_expected_json_occurrences_capped_two',
        'exact_expected_json_offset', 'warning_diagnostic', 'unknown_output_recorded'}, 'closed_framing_fields')
    exact(set(value['actual']), {'bytes', 'sha256'}, 'closed_actual_wire_identity')
    old.integer(value['actual']['bytes'], 0, 3*1024*1024); old.digest(value['actual']['sha256'])
    require(type(value['complete_cgi_wire_matches']) is bool and value['unknown_output_recorded'] is False
        and value['classification'] in {'exact_native_frame', 'empty', 'known_cgi_header_prefix', 'known_json_prefix', 'other'},
        'closed_framing_verdict_and_class')
    for name in ['expected_prefix_match_bytes', 'expected_suffix_match_bytes', 'lf_bytes', 'cr_bytes']:
        old.integer(value[name], 0, 3*1024*1024)
    old.integer(value['exact_expected_json_occurrences_capped_two'], 0, 2)
    if value['exact_expected_json_offset'] is not None: old.integer(value['exact_expected_json_offset'], 0, 3*1024*1024)
    warning_gate(value['warning_diagnostic'], source)


def public_gate(value, source, native):
    cases = native['rows'] + native['prepared']['cases']
    def visit(v):
        if type(v) is dict:
            if 'framing' in v: framing_gate(v['framing'], source)
            if 'expected_cgi_wire_hex' in v or 'expected_header_lines_hex' in v:
                require(any(canonical(v) == canonical(row) for row in cases), 'only_exact_synthetic_case_hex_is_public')
                v = {k: item for k, item in v.items() if k not in ('expected_cgi_wire_hex', 'expected_header_lines_hex')}
            return {k: visit(item) for k, item in v.items()}
        if type(v) is list: return [visit(item) for item in v]
        return v
    base.public_gate(visit(value))


def api_gate(api, run):
    exact(set(api), {'repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha', 'run_conclusion', 'job', 'artifact'}, 'api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'api_repository_id'); exact(api['repository'], old.REPOSITORY, 'api_repository')
    old.integer(api['run_id'], 1); old.integer(api['run_attempt'], 1, 1000)
    require(str(api['run_id']) == run and api['run_conclusion'] in old.CONCLUSIONS, 'api_run')
    require(type(api['head_sha']) is str and re.fullmatch('[0-9a-f]{40}', api['head_sha']), 'api_commit')
    job, artifact = api['job'], api['artifact']
    exact(set(job), {'id', 'name', 'conclusion', 'steps'}, 'api_job_fields'); old.integer(job['id'], 1)
    require(job['name'] == 'Cranelift eight CGI frames without WP or DB' and job['conclusion'] in old.CONCLUSIONS, 'api_job')
    require(type(job['steps']) is list and 1 <= len(job['steps']) <= 64, 'api_steps')
    for step in job['steps']:
        exact(set(step), {'name', 'conclusion'}, 'api_step_fields')
        require(type(step['name']) is str and 0 < len(step['name']) <= 160 and step['conclusion'] in old.CONCLUSIONS, 'api_step')
    exact(set(artifact), {'id', 'name', 'size_in_bytes', 'digest'}, 'api_artifact_fields')
    old.integer(artifact['id'], 1); old.integer(artifact['size_in_bytes'], 1, 24*1024*1024)
    require(artifact['name'] == 'php-wasix-cgi-framing-' + str(api['run_attempt'])
        and type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']), 'api_artifact')


def archive_gate(path, api, source, native):
    wire = read(path, 24*1024*1024)
    exact(identity(wire), {'bytes': api['artifact']['size_in_bytes'], 'sha256': api['artifact']['digest'][7:]}, 'api_archive_identity')
    reverse = {path: name for name, path in member_map().items()}; raw = {}; members = {}; expanded = 0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(1 <= len(entries) <= 8 and len({item.filename for item in entries}) == len(entries), 'zip_member_count')
        for entry in entries:
            old.relative(entry.filename)
            require(entry.filename in reverse and not entry.is_dir() and entry.flag_bits & 1 == 0
                and stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFLNK and 0 < entry.file_size <= 8*1024*1024,
                'public_json_member')
            expanded += entry.file_size; require(expanded <= 24*1024*1024, 'zip_expansion_limit')
            value = archive.read(entry); require(len(value) == entry.file_size, 'zip_member_size')
            name = reverse[entry.filename]; raw[name] = value; members[name] = entry.filename
    require('ci-stages.json' in raw, 'stage_record_required')
    data = {name: parse_json(wire) for name, wire in raw.items()}
    for value in data.values(): public_gate(value, source, native)
    return raw, data, members


def stages_gate(value, api, source):
    exact(set(value), {'schema', 'source_commit', 'run_id', 'run_attempt', 'stages'}, 'stage_fields')
    require(value['schema'] == 'recastpress.php-wasix-cgi-framing-ci-stages.v0.5'
        and value['source_commit'] == api['head_sha'] and value['run_id'] == str(api['run_id'])
        and value['run_attempt'] == str(api['run_attempt']), 'stage_identity')
    names = {}
    for block in source.read(WORKFLOW).decode().split('      - name: ')[1:]:
        match = re.search(r'^        id: ([a-z_]+)$', block, re.M)
        if match: names[match[1]] = block.splitlines()[0].strip()
    exact(set(names), {'php', 'deps', 'parser', 'packages', 'tools', 'module', 'rust', 'bootstrap', 'guest', 'host', 'contract'},
        'eleven_workflow_stages')
    exact(set(value['stages']), set(names), 'actual_stage_inventory')
    for key, name in names.items():
        rows = [row for row in api['job']['steps'] if row['name'] == name]; require(len(rows) == 1, 'api_stage_step')
        exact(value['stages'][key], rows[0]['conclusion'], 'api_stage_conclusion')
    return all(status == 'success' for status in value['stages'].values())


def driver_gate(value, source):
    plan = parse_json(source.read('experiments/wpdb-rpc-probe/driver-plan.json'))
    exact(value['plan_sha256'], sha(source.read('experiments/wpdb-rpc-probe/driver-plan.json')), 'fixed_driver_plan')
    trees = {p['name']: {'files': p['local_regular_files_matching_crate_archive'],
        'inventory_sha256': plan['patch']['source_tree_inventory_after_sha256'] if p['name'] == 'mysql_async'
        else p['source_file_inventory_sha256']} for p in plan['crates']}
    exact(value['source_trees'], trees, 'fixed_patched_driver_trees')
    seed = source.read(plan['existing_reference']['cargo_lock']); exact(value['seed_sha256'], sha(seed), 'driver_seed_lock')
    require(value['stage_modified'] is False and type(value['historical_resolved_lock_state_present']) is bool, 'driver_scope')
    if value['historical_resolved_lock_state_present'] is False: exact(value['stage_lock'], identity(seed), 'fresh_driver_seed')
    else: old.integer(value['stage_lock']['bytes'], 1, 1024*1024); old.digest(value['stage_lock']['sha256'])


def host_gate(value, source, prefix, expected_units, expected_inputs):
    anchor = parse_json(source.read(prefix + 'evidence/windows-build.json'))
    require(value['passed'] is True and value['locked_offline'] is True and value['owned_cleanup_completed'] is True
        and value['source_reverified_after'] is True and value['build_profile'] == 'release', 'positive_linux_host_build')
    exact(value['schema'], anchor['schema'], 'host_schema'); exact(value['outcome'], anchor['outcome'], 'host_outcome')
    require(anchor['passed'] is True and anchor['source_reverified_after'] is True, 'windows_host_anchor')
    exact(value['input_files'], anchor['input_files'], 'complete_host_source_inventory')
    require(len(value['input_files']) == expected_inputs and len(value['units']['tests']) == expected_units
        and value['units']['passed'] is True, 'host_inventory_counts')
    source.bindings(value['input_files']); exact(value['units']['tests'], anchor['units']['tests'], 'source_bound_unit_inventory')
    require(len(set(value['units']['tests'])) == expected_units and len(value['commands']) == 4, 'host_unique_units_and_commands')
    for row, (stage, timeout) in zip(value['commands'], [('unit-build', 1200), ('unit-list', 10), ('unit-run', 60), ('normal-build', 1200)]):
        require(row['stage'] == stage and row['timeout_seconds'] == timeout and row['owned_reaped'] is True
            and type(row['exit_code']) is int and row['exit_code'] == 0, 'owned_host_build_command')
        old.integer(row['owned_pid'], 1)
    for key in ['executable', 'unit']:
        path = value['executable'] if key == 'executable' else value['units']['executable']
        info = value['executable_identity'] if key == 'executable' else value['units']['executable_identity']
        old.relative(path); old.integer(info['bytes'], 1, 256*1024*1024); old.digest(info['sha256'])
    return anchor


def builds_gate(data, raw, source, anchor, native):
    host, boot = data['host-build.json'], data['bootstrap-build.json']
    host_gate(host, source, PREFIX, 26, 191); boot_anchor = host_gate(boot, source, BOOT, 31, 145)
    for key in ['driver', 'source_fs_registry', 'derivation']:
        exact(boot[key + '_before'], boot[key + '_after'], 'bootstrap_unchanged_' + key)
        if key != 'driver': exact(boot[key + '_before'], boot_anchor[key + '_before'], 'bootstrap_frozen_' + key)
    driver_gate(boot['driver_before'], source)
    for key in ['database_started', 'database_connected', 'php_executed', 'wasm_executed', 'product_runtime_selected']:
        require(boot[key] is False, 'bootstrap_build_only_scope')
    exact(host['before'], host['after'], 'mini_build_gates_unchanged')
    expected = {'receipt_path': BOOT + 'evidence/linux-build.json', 'receipt_identity': identity(raw['bootstrap-build.json']),
        'executable': boot['executable'], 'executable_identity': boot['executable_identity'],
        'unit_executable': boot['units']['executable'], 'unit_executable_identity': boot['units']['executable_identity'],
        'source_inputs': 145, 'units': 31, 'original_build_fully_reverified': True}
    exact(host['before']['old_bootstrap'], expected, 'same_job_bootstrap_receipt_and_binaries')
    exact(host['before']['driver'], boot['driver_before'], 'same_job_driver_source_stage')
    exact(host['before']['source_fs'], boot['source_fs_registry_before'], 'same_job_source_fs_registry')
    exact(host['before']['derivation'], derivation(source), 'mini_exact_source_derivation')
    exact(host['before']['native_proof'], native['proof'], 'mini_native_proof')
    for key in ['actual_php_wasm_executed', 'native_php_executed', 'database_connected', 'product_runtime_selected']:
        require(host[key] is False, 'mini_build_only_scope')
    require(host['plain_wat_unit_executed'] is True, 'plain_wat_capture_unit_executed')


def positive_gate(data, raw, source, native, anchor, prepared):
    exact(set(data), set(member_map()), 'eight_positive_records')
    exact(data['prepared.json'], prepared, 'complete_mini_preparation')
    exact(data['native-prepared.json'], native['prepared'], 'complete_native_preparation')
    builds_gate(data, raw, source, anchor, native)
    managed = old.managed_gate(data, raw, source)
    run = data['run.json']; host = data['host-build.json']
    require(run['schema'] == 'recastpress.php-wasix-cgi-framing-owned-check.v0.5' and run['passed'] is True
        and run['outcome'] == 'owned_wasix_all_eight_cgi_frames_exact' and run['platform'] == 'linux'
        and run['owned_cleanup_completed'] is True and run['normal_case_cleanup_completed'] is True
        and run['source_reverified_after'] is True, 'positive_owned_framing_run')
    exact(run['build_before'], host, 'same_job_host_build_before'); exact(run['build_after'], host, 'same_job_host_build_after')
    exact(run['input_files'], anchor['input_files'], 'runtime_191_input_sources')
    value = run['observation']
    require(value['schema'] == 'recastpress.php-wasix-cgi-framing-check.v0.5' and value['passed'] is True
        and value['outcome'] == 'wasix_all_eight_cgi_frames_exact' and value['actual_php_wasm_executed'] is True
        and value['fresh_runtime_per_case'] is True and value['source_reverified_after'] is True
        and value['source_and_module_reverified_after_each'] is True, 'positive_eight_guest_cases')
    old.integer(value['module_compilations'], 1, 1)
    exact(value['source_inputs'], anchor['input_files'], 'guest_191_sources')
    exact(value['native_proof'], native['proof'], 'runtime_native_pin')
    exact(value['prepared'], {'path': 'target/php-wasix-cgi-framing-probe/prepared.json', 'identity': identity(raw['prepared.json'])},
        'same_job_prepared_bytes')
    module = data['build.json']['php_wasm']
    expected_module = {key: module[key] for key in ['path', 'bytes', 'sha256']}; expected_module['verified'] = True
    expected_receipt = {'path': old.MANAGED + 'evidence/build.json', 'sha256': sha(raw['build.json']),
        'verified': True, 'full_chain_verified_by_host': False}
    for key in ['module_before', 'module_after', 'module']: exact(value[key], expected_module, 'same_job_module_unchanged')
    for key in ['module_receipt_before', 'module_receipt_after', 'build_receipt']:
        exact(value[key], expected_receipt, 'same_job_module_receipt_unchanged')
    require(len(value['observations']) == 8, 'eight_actual_guest_cases')
    for actual, expected in zip(value['observations'], native['rows']):
        require(actual['case'] == expected['case'] and actual['passed'] is True, 'positive_guest_case')
        for key in ['fresh_runtime', 'guest_execution_started', 'guest_exit_success', 'runtime_shutdown_returned',
                'stdout_eof', 'stderr_eof', 'access_channel_finished']:
            require(actual[key] is True, 'guest_normal_' + key)
        for key in ['guest_networking', 'host_environment_forwarded', 'host_home_mapped', 'native_php_fallback', 'stdout_overflow', 'stderr_overflow']:
            require(actual[key] is False, 'guest_scope_' + key)
        exact(actual['stdin_bytes'], 0, 'empty_guest_stdin'); exact(actual['stderr'], identity(b''), 'empty_guest_stderr')
        wire = native['wires'][expected['case']]; body_length = expected['expected_body']['bytes']; prefix_length = len(wire) - body_length
        framing = actual['framing']; framing_gate(framing, source)
        require(framing['complete_cgi_wire_matches'] is True and framing['classification'] == 'exact_native_frame', 'whole_cgi_exact')
        exact(framing['actual'], identity(wire), 'actual_native_whole_cgi_digest_and_size')
        for key in ['expected_prefix_match_bytes', 'expected_suffix_match_bytes']: exact(framing[key], len(wire), 'whole_native_match_length')
        exact(framing['lf_bytes'], wire.count(b'\n'), 'actual_lf_count'); exact(framing['cr_bytes'], wire.count(b'\r'), 'actual_cr_count')
        exact(framing['exact_expected_json_occurrences_capped_two'], 1, 'unique_fixed_json_body')
        exact(framing['exact_expected_json_offset'], prefix_length, 'fixed_json_body_offset')
        warnings = framing['warning_diagnostic']
        exact(warnings['prefix_bytes_examined'], prefix_length, 'fixed_header_diagnostic_prefix')
        require(warnings['prefix_truncated'] is False and not any(warnings['message_counts'].values())
            and not any(warnings['builtin_counts'].values()) and warnings['source_lines']['framing_common'] == {
                'output_started_at': [], 'warning_at': []}, 'no_unexpected_header_warnings')
        access = actual['managed_access']
        require(access['finished'] is True and access['protocol_error'] is None, 'completed_access_protocol')
        for key in ['active_handles', 'pending_bytes', 'unread_response_bytes', 'invalid', 'io_errors']:
            old.integer(access[key], 0, 0)
    for scope in [run, value]:
        for key in ['wordpress_executed', 'database_connected', 'native_php_fallback', 'product_runtime_selected']:
            require(scope[key] is False, 'unclaimed_runtime_scope')
    require(run['native_php_executed'] is False and value['network_calls_made'] is False
        and value['all_physical_resources_reclaimed'] is False and value['outer_owned_process_deadline_required'] is True,
        'bounded_scope_not_product_reclamation')
    execution = run['owned_process']; require(execution['owned_reaped'] is True, 'owned_host_reaped')
    old.integer(execution['owned_pid'], 1); exact(execution['exit_code'], 0, 'owned_host_exit'); exact(execution['deadline_seconds'], 300, 'owned_host_deadline')
    exact(execution['stderr'], identity(b''), 'owned_host_empty_stderr')
    lock = tomllib.loads(source.read(PREFIX + 'Cargo.lock').decode())
    serde = [p for p in lock['package'] if p['name'] == 'serde_json']; require(len(serde) == 1
        and not any(name.startswith('indexmap') for name in serde[0].get('dependencies', [])), 'sorted_serde_public_json')
    exact(execution['stdout'], identity(canonical(value) + b'\n'), 'public_host_json_reserialization')
    return {'all_eight_whole_cgi_frames_native_exact': True, 'module_compilations': 1, 'fresh_runtimes': 8,
        'native_php_sources': 9, 'runtime_source_inputs': 191, 'host_units': 26, 'bootstrap_units': 31,
        'bootstrap_source_inputs': 145, 'managed': managed, 'public_host_json_reserialization_verified': True,
        'actual_guest_cgi_bytes_recomputed_offline': False}


def bindings(source, value):
    count = old.source_bindings_in_records(source, value)
    def more(v):
        total = 0
        if type(v) is dict:
            for key, item in v.items():
                if key == 'source_inputs' and type(item) is dict: source.bindings(item); total += len(item)
                else: total += more(item)
        elif type(v) is list: total += sum(more(item) for item in v)
        return total
    return count + more(value)


def immutable(path, wire):
    require(path.resolve() == path and path.is_relative_to(HERE / 'evidence'), 'owned_import_path')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists(): exact(read(path, 8*1024*1024), wire, 'immutable_import_changed')
    else:
        with path.open('xb') as stream: stream.write(wire)


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--run', required=True)
    parser.add_argument('--verify-only', action='store_true'); args = parser.parse_args()
    require(re.fullmatch('[1-9][0-9]{0,19}', args.run), 'run_id')
    work = ROOT / 'target/php-wasix-cgi-framing-probe/ci'
    api_wire = read(work / ('api-' + args.run + '.json'), 128*1024); api = parse_json(api_wire)
    api_gate(api, args.run); old.privacy_gate(api)
    source = old.GitSource(api['head_sha'])
    for path in [SHARED, base.LEGACY]: exact(source.read(path), read(ROOT / path, 512*1024), 'frozen_shared_verifier_changed')
    native = native_contract(source); anchor = source_inventory(source); prepared = prepared_contract(source, native, anchor)
    raw, data, members = archive_gate(work / ('run-' + args.run + '.zip'), api, source, native)
    stages = stages_gate(data['ci-stages.json'], api, source)
    positive = api['run_conclusion'] == api['job']['conclusion'] == 'success' and stages
    for name, expected in [('prepared.json', prepared), ('native-prepared.json', native['prepared'])]:
        if name in data: exact(data[name], expected, 'source_bound_preparation_even_on_failure')
    error = None
    try: checked = sum(bindings(source, value) for value in data.values())
    except Exception as failure:
        if positive: raise
        checked = 0; error = type(failure).__name__
    summary = {'overall_positive': False, 'raw_verdicts_rewritten': False, 'source_binding_entries_checked': checked,
        'source_binding_verification_passed': error is None, 'source_binding_failure_class': error}
    if positive: summary.update(positive_gate(data, raw, source, native, anchor, prepared)); summary['overall_positive'] = True
    else:
        run = data.get('run.json', {}); observation = run.get('observation', {})
        summary.update(recorded_outcome=run.get('outcome'), recorded_passed=run.get('passed'),
            recorded_inner_passed=observation.get('passed'), public_case_observations=[])
        for row in observation.get('observations', []):
            require(row.get('case') in native['wires'], 'closed_failure_case')
            summary['public_case_observations'].append({key: row[key] for key in [
                'case', 'passed', 'guest_exit_success', 'stdout_eof', 'stderr_eof', 'runtime_shutdown_returned', 'framing'] if key in row})
    proof = {'schema': 'recastpress.php-wasix-cgi-framing-ci-import.v0.5', 'repository': old.REPOSITORY,
        'repository_id': old.REPOSITORY_ID, 'run_id': api['run_id'], 'run_attempt': api['run_attempt'],
        'source_commit': source.commit, 'run_conclusion': api['run_conclusion'], 'job': api['job'], 'artifact': api['artifact'],
        'api_metadata_sha256': sha(api_wire), 'archive_member_map': members,
        'raw_json': {name: identity(wire) for name, wire in raw.items()},
        'original_git_sources': {name: identity(wire) for name, wire in sorted(source.cache.items())},
        'verifier_input_files': {path.relative_to(ROOT).as_posix(): sha(read(path, 512*1024))
            for path in [Path(__file__).resolve(), ROOT / SHARED, ROOT / base.LEGACY]},
        'verification': summary, 'network_used_by_importer': False,
        'php_wasm_or_database_executed_by_importer': False, 'source_tool_module_binaries_reverified': False,
        'actual_guest_stdout_recomputed_offline': False, 'product_runtime_selected': False,
        'privacy_exception_scope': 'whole reconstructed pinned synthetic native case expected CGI/header hex only; raw bytes unchanged'}
    public_gate(proof, source, native)
    wire = (json.dumps(proof, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    require(len(wire) <= 2*1024*1024, 'import_proof_quota')
    if not args.verify_only:
        destination = HERE / ('evidence/ci-' + args.run)
        for name, value in raw.items(): immutable(destination / 'raw' / members[name], value)
        immutable(destination / 'api-metadata.json', api_wire); immutable(destination / 'provenance.json', wire)
    print('CGI framing CI evidence: ' + ('verified positive' if summary['overall_positive'] else 'preserved failure/partial'))
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('CGI framing import rejected: ' + reason, file=sys.stderr); sys.exit(1)
