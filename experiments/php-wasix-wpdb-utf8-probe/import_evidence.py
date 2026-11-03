"""Import eight UTF8 CI JSON records using frozen Git sources, without execution."""
import argparse
import ast
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
PREFIX = 'experiments/php-wasix-wpdb-utf8-probe/'
NATIVE = 'experiments/wpdb-utf8-probe/'
BOOT = 'experiments/php-wasix-wp-bootstrap-probe/'
THEME = 'experiments/php-wasix-wp-request-probe/'
PROVIDER = 'experiments/php-wasix-wpdb-probe/prepare.py'
WORKFLOW = '.github/workflows/php-wasix-wpdb-utf8.yml'
HELPER = 'experiments/php-wasix-cgi-framing-probe/import_evidence.py'
SHARED = BOOT + 'import_evidence.py'
spec = importlib.util.spec_from_file_location('rp_utf8_offline_build_helpers', ROOT / HELPER)
build_helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(build_helpers)
base, old = build_helpers.base, build_helpers.old
require, sha, read, identity, exact, canonical, parse_json = (
    base.require, base.sha, base.read, base.identity, base.exact, base.canonical, base.parse_json)
PINS = {'mysql': 'c1d7a9e4d55c68b030830e1d8e883d52f42bae031df4bd3062f3bcf4d5370ada',
    'mariadb': '18c01e8105f7e41e407ebdd36f4e057233b3d808028d06b37168877e2ec2ba90'}


def member_map(backend):
    return {'run.json': PREFIX + 'evidence/linux-' + backend + '.json',
        'host-build.json': PREFIX + 'evidence/linux-build.json',
        'bootstrap-build.json': BOOT + 'evidence/linux-build.json',
        'ci-stages.json': PREFIX + 'evidence/ci-stages.json',
        'build.json': old.MANAGED + 'evidence/build.json', 'toolchain.json': old.CGI + 'evidence/toolchain.json',
        'prepared.json': 'target/php-wasix-wpdb-utf8-probe/prepared.json',
        'native-prepared.json': 'target/wpdb-utf8-probe/prepared.json'}


def frozen_parents(source, mapping):
    result = {name: source.read(name) for name in mapping}
    for name, expected in mapping.items(): exact(sha(result[name]), expected, 'frozen_parent_whole_file_pin')
    return result


def source_fields(source, name, function, variable=None):
    fn = next(node for node in source.tree(name).body if isinstance(node, ast.FunctionDef) and node.name == function)
    if variable is None:
        node = fn.body[-1]; require(isinstance(node, ast.Return) and isinstance(node.value, ast.Dict), 'source_return_dict')
        value = node.value
    else:
        values = [node.value for node in fn.body if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets)]
        require(len(values) == 1 and isinstance(values[0], ast.Dict), 'source_assigned_dict'); value = values[0]
    return {ast.literal_eval(key): value for key, value in zip(value.keys, value.values)}


def native_contract(source):
    name = NATIVE + 'prepare.py'; constants = source.constants(name); original = frozen_parents(source, constants['PINS'])
    pure = source.functions(name, ['once', 'derived_entry', 'typed', 'quote', 'esc_like', 'expected_cases'], constants)
    entry = pure['derived_entry'](original['experiments/wpdb-crud-probe/fixtures/entry.php'])
    exact(source.read(NATIVE + 'fixtures/entry.php'), entry, 'whole_derived_native_entry')
    exact(source.read(NATIVE + 'fixtures/native.php'), original['experiments/wpdb-crud-probe/fixtures/native.php'], 'native_wrapper_unchanged')
    local = {p for p in source.names(NATIVE) if '/evidence/' not in p and '__pycache__' not in p and Path(p).suffix in ('.py', '.php')}
    names = set(constants['PINS']) | local; require(len(names) == 19, 'native_nineteen_input_sources')
    mapping = source.hashes(names)
    owner = source.constants('experiments/wpdb-crud-probe/owned_database.py')
    prepared = {'schema': 'recastpress.wpdb-utf8-prepared.v0.5', 'input_files': mapping,
        'schema_ddl': identity(owner['DDL']), 'values_hex': {label: value.encode().hex() for label, value in constants['VALUES']},
        'updated_hex': constants['UPDATED'].encode().hex(), 'expected_cases': pure['expected_cases'](),
        'native_extension_profile': ['mysqli', 'mbstring'],
        'derivation': {'entry_original': identity(original['experiments/wpdb-crud-probe/fixtures/entry.php']),
            'entry_derived': identity(entry), 'typed_observers_and_original_wp_loader_unchanged': True,
            'changes': ['owned_include_prefix', 'utf8_schema_and_outcomes', 'native_only_variant', 'require_mbstring']},
        **dict.fromkeys(['php_executed', 'database_connected', 'network_calls_made', 'wasm_executed',
            'invalid_utf8_exercised', 'normalization_applied', 'product_runtime_selected'], False)}
    exact(set(prepared), set(source_fields(source, name, 'prepare', 'value')), 'complete_native_preparation_fields')
    references = {}; rows = list(prepared['expected_cases']); traces = []
    for backend, pin in PINS.items():
        path = NATIVE + 'evidence/windows-' + backend + '-native.json'; wire = source.read(path)
        require(sha(wire) == pin, 'fixed_native_utf8_whole_oracle')
        value = parse_json(wire)
        require(value['passed'] is True and value['source_reverified_after'] is True
            and value['normal_admin_shutdown_verified'] is True and value['all_command_children_reaped'] is True,
            'native_utf8_oracle_completion')
        exact(value['prepared'], prepared, 'native_oracle_preparation_reconstructed')
        def reader(path, maximum=2*1024*1024):
            wire = source.read(Path(path).relative_to(ROOT).as_posix()); require(len(wire) <= maximum, 'native_read_quota'); return wire
        validator = source.functions(NATIVE + 'check.py', ['exact', 'validate'], {
            'canonical': canonical, 'prepared': SimpleNamespace(expected_cases=pure['expected_cases'], read=reader,
                VALUES=constants['VALUES'])})
        validator['validate'](value['observation'], backend, value['native_reference_before'])
        references[backend] = value
        rows.extend(value['observation']['observations']); traces.append(value['observation']['query_trace_hex'])
    exact(traces[0], traces[1], 'same_native_query_trace')
    return {'prepared': prepared, 'proofs': references, 'rows': rows, 'trace': traces[0]}


def prepared_contract(source, native):
    name = PREFIX + 'prepare.py'; constants = source.constants(name); original = frozen_parents(source, constants['PINS'])
    pure = source.functions(name, ['once', 'section', 'expected_fixtures'], constants)
    fixtures = pure['expected_fixtures'](original)
    exact(set(fixtures), set(constants['FIXTURES']), 'five_derived_fixtures')
    for filename, wire in fixtures.items(): exact(source.read(PREFIX + 'fixtures/' + filename), wire, 'whole_derived_utf8_fixture')
    exact(fixtures['adapter.php'], source.read(BOOT + 'fixtures/adapter.php'), 'full_wp_4096_adapter_unchanged')
    exact(fixtures['corpus.php'], source.read(NATIVE + 'fixtures/corpus.php'), 'native_utf8_corpus_unchanged')
    names = set(native['prepared']['input_files']) | set(constants['PINS'])
    names |= {p['path'] for p in constants['ORACLES'].values()}
    names |= {name, *[PREFIX + 'fixtures/' + f for f in constants['FIXTURES']]}
    require(len(names) == 29, 'twenty_nine_preparation_inputs')
    derivation_fields = source_fields(source, name, 'derivation_gate')
    derived = {'parents': {p: identity(w) for p, w in original.items()},
        'fixtures': {p: identity(w) for p, w in fixtures.items()}, 'native_corpus_bytes_unchanged': True,
        'full_wp_adapter4096_bytes_unchanged': True, 'original_wpdb_business_and_typed_observer_bytes_unchanged': True,
        'allowed_changes': ast.literal_eval(derivation_fields['allowed_changes']), 'original_git_objects_verified_by_preparer': False}
    provider = source.constants(PROVIDER); require(len(provider['WP_PINS']) == 8, 'eight_original_wordpress_pins')
    wp = {filename: {'path': 'target/php-wasix-wpdb-utf8-probe/wp-core/' + filename, 'bytes': pin[0], 'sha256': pin[1]}
        for filename, pin in sorted(provider['WP_PINS'].items())}
    files = [{'source': PREFIX + 'fixtures/' + f, 'guest': '/' + PREFIX + 'fixtures/' + f,
        **identity(fixtures[f]), 'guest_read_only': True, 'kind': 'derived_fixture'} for f in constants['FIXTURES']]
    files += [{'source': row['path'], 'guest': '/.reference/wordpress-7.1.3/wp-includes/' + f,
        'bytes': row['bytes'], 'sha256': row['sha256'], 'guest_read_only': True, 'kind': 'wordpress_source'} for f, row in wp.items()]
    # Reuse the immutable receipt for the SAME archive. This binds its recorded
    # inventory counters; the archive and extracted PHP bytes are unavailable.
    previous = parse_json(source.read('experiments/php-wasix-wpdb-probe/evidence/ci-41550553907-mysql/raw/target/php-wasix-wpdb-probe/prepared.json'))
    old.prepared_gate(previous, source); archive = previous['source_archive']
    exact({k: archive[k] for k in ['path', 'bytes', 'sha256']}, {'path': '.reference/wordpress-7.1.3.tar.gz',
        'bytes': provider['ARCHIVE_BYTES'], 'sha256': provider['ARCHIVE_SHA']}, 'same_fixed_archive_metadata')
    expected = {'schema': 'recastpress.php-wasix-wpdb-utf8-prepared.v0.5', 'outcome': 'fixed_thirteen_utf8_guest_sources_prepared',
        'input_files': source.hashes(names), 'derivation': derived, 'source_archive': archive, 'files': files,
        'wordpress_files': wp, 'wordpress_version': '7.1.3',
        'wordpress_source_sha256': {'wp-includes/' + f: row['sha256'] for f, row in wp.items()},
        'expected_include_names': sorted([PREFIX + 'fixtures/' + f for f in fixtures]
            + ['.reference/wordpress-7.1.3/wp-includes/' + f for f in wp]),
        'native_oracles': constants['ORACLES'], 'native_api_cases': 31, 'native_public_queries': 22,
        'private_context_guest_path': '/rp-utf8/context.json', 'private_context_keys': ['schema', 'nonce'],
        **dict.fromkeys(['guest_context_contains_db_credentials_or_salts', 'full_wordpress_bootstrap', 'wordpress_executed',
            'wasm_executed_by_preparer', 'native_php_executed_by_preparer', 'native_reference_binaries_reverified',
            'network_used_by_preparer', 'guest_network_enabled', 'product_runtime_selected', 'guest_readonly_enforcement_executed'], False),
        'inputs_unchanged': True}
    exact(set(expected), set(source_fields(source, name, 'metadata', 'result')), 'complete_preparation_fields')
    for backend, row in constants['ORACLES'].items():
        exact(row, {'path': NATIVE + 'evidence/windows-' + backend + '-native.json',
            **identity(source.read(NATIVE + 'evidence/windows-' + backend + '-native.json'))}, 'native_oracle_whole_pin')
    return expected


def public_gate(value, native):
    def visit(v):
        if type(v) is dict:
            if 'case' in v and any(key in v for key in ['arguments', 'returned', 'queries_hex', 'state']):
                require(any(canonical(v) == canonical(row) for row in native['rows']), 'only_whole_fixed_synthetic_rows_public')
                v = {key: item for key, item in v.items() if key not in ('arguments', 'returned', 'queries_hex', 'state')}
            if 'query_trace_hex' in v:
                exact(v['query_trace_hex'], native['trace'], 'only_complete_fixed_sql_trace_public')
                v = {key: item for key, item in v.items() if key != 'query_trace_hex'}
            return {key: visit(item) for key, item in v.items()}
        if type(v) is list: return [visit(item) for item in v]
        return v
    base.public_gate(visit(value))


def api_gate(api, run, backend):
    exact(set(api), {'repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha', 'run_conclusion', 'job', 'artifact'}, 'api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'api_repository_id'); exact(api['repository'], old.REPOSITORY, 'api_repository')
    old.integer(api['run_id'], 1); old.integer(api['run_attempt'], 1, 1000)
    require(str(api['run_id']) == run and api['run_conclusion'] in old.CONCLUSIONS, 'api_run')
    require(type(api['head_sha']) is str and re.fullmatch('[0-9a-f]{40}', api['head_sha']), 'api_commit')
    job, artifact = api['job'], api['artifact']; exact(set(job), {'id', 'name', 'conclusion', 'steps'}, 'api_job_fields')
    old.integer(job['id'], 1)
    require(job['name'] == 'Cranelift UTF-8 wpdb / ' + backend and job['conclusion'] in old.CONCLUSIONS, 'api_job')
    require(type(job['steps']) is list and 1 <= len(job['steps']) <= 64, 'api_steps')
    for step in job['steps']:
        exact(set(step), {'name', 'conclusion'}, 'api_step_fields')
        require(type(step['name']) is str and 0 < len(step['name']) <= 160 and step['conclusion'] in old.CONCLUSIONS, 'api_step')
    exact(set(artifact), {'id', 'name', 'size_in_bytes', 'digest'}, 'api_artifact_fields')
    old.integer(artifact['id'], 1); old.integer(artifact['size_in_bytes'], 1, 24*1024*1024)
    require(artifact['name'] == 'php-wasix-wpdb-utf8-' + backend + '-' + str(api['run_attempt'])
        and type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']), 'api_artifact')


def archive_gate(path, api, backend, native):
    wire = read(path, 24*1024*1024)
    exact(identity(wire), {'bytes': api['artifact']['size_in_bytes'], 'sha256': api['artifact']['digest'][7:]}, 'api_archive_identity')
    reverse = {path: name for name, path in member_map(backend).items()}; raw = {}; members = {}; expanded = 0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(1 <= len(entries) <= 8 and len({e.filename for e in entries}) == len(entries), 'zip_member_count')
        for entry in entries:
            old.relative(entry.filename)
            require(entry.filename in reverse and not entry.is_dir() and entry.flag_bits & 1 == 0
                and stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFLNK and 0 < entry.file_size <= 8*1024*1024, 'public_json_member')
            expanded += entry.file_size; require(expanded <= 24*1024*1024, 'zip_expansion_limit')
            value = archive.read(entry); require(len(value) == entry.file_size, 'zip_member_size')
            name = reverse[entry.filename]; raw[name] = value; members[name] = entry.filename
    require('ci-stages.json' in raw, 'stage_record_required')
    data = {name: parse_json(wire) for name, wire in raw.items()}
    for value in data.values(): public_gate(value, native)
    return raw, data, members


def stages_gate(value, api, backend, source):
    exact(set(value), {'schema', 'source_commit', 'run_id', 'run_attempt', 'backend', 'stages'}, 'stage_fields')
    require(value['schema'] == 'recastpress.php-wasix-wpdb-utf8-ci-stages.v0.5' and value['source_commit'] == api['head_sha']
        and value['run_id'] == str(api['run_id']) and value['run_attempt'] == str(api['run_attempt']) and value['backend'] == backend, 'stage_identity')
    names = {}
    for block in source.read(WORKFLOW).decode().split('      - name: ')[1:]:
        match = re.search(r'^        id: ([a-z_]+)$', block, re.M)
        if match: names[match[1]] = block.splitlines()[0].strip()
    exact(set(names), {'php', 'deps', 'parser', 'packages', 'tools', 'module', 'guest', 'rust', 'bootstrap', 'host', 'contract'}, 'eleven_stages')
    exact(set(value['stages']), set(names), 'actual_stage_inventory')
    for key, name in names.items():
        rows = [row for row in api['job']['steps'] if row['name'] == name]; require(len(rows) == 1, 'api_stage_step')
        exact(value['stages'][key], rows[0]['conclusion'], 'api_stage_conclusion')
    return all(status == 'success' for status in value['stages'].values())


def source_inventory(source):
    anchor = parse_json(source.read(PREFIX + 'evidence/windows-build.json'))
    require(anchor['passed'] is True and anchor['source_reverified_after'] is True, 'utf8_windows_source_anchor')
    mapping = anchor['input_files']; require(len(mapping) == 175, 'complete_175_sources'); source.bindings(mapping)
    local = {name for name in source.names(PREFIX) if '/evidence/' not in name and '__pycache__' not in name
        and Path(name).name != 'import_evidence.py'
        and (Path(name).suffix in ('.rs', '.py', '.php', '.toml') or Path(name).name == 'Cargo.lock')}
    require(local <= set(mapping) and WORKFLOW in mapping, 'complete_runtime_and_workflow_inventory')
    return anchor


def runtime_derivation(source):
    theme = source.read(THEME + 'src/main.rs'); module = theme[theme.index(b'fn fixed('):theme.index(b'fn finite(')]
    exact(source.read(PREFIX + 'src/module_verifier.rs'), module, 'exact_module_verifier')
    start = theme.index(b'fn known_json_cgi_headers('); end = theme.index(b'fn observe_cgi(', start); headers = theme[start:end]
    rust = source.read(PREFIX + 'src/main.rs'); require(rust.count(headers) == 1, 'exact_strict_json_headers')
    bodies = {}
    for name, filename in {'db': 'db.rs', 'rpc_fs': 'rpc_fs.rs', 'result_budget': 'result_budget.rs', 'capture': 'capture.rs'}.items():
        marker = ('#[path = "../../php-wasix-wp-bootstrap-probe/src/' + filename + '"]\nmod ' + name + ';').encode()
        require(rust.count(marker) == 1 and PREFIX + 'src/' + filename not in source.names(PREFIX + 'src/'), 'readonly_original_runtime_import')
        bodies[filename] = sha(source.read(BOOT + 'src/' + filename))
    for name in ['Cargo.toml', 'Cargo.lock']:
        before = source.read(BOOT + name); old_name = b'recastpress-php-wasix-wp-bootstrap-probe'
        require(before.count(old_name) == 1 and before.replace(old_name, b'recastpress-php-wasix-wpdb-utf8-probe') == source.read(PREFIX + name), 'package_name_only_workspace')
    return {'module_verifier_body_sha256': sha(module), 'module_verifier_body_unchanged': True,
        'outer_cgi_header_helper_sha256': sha(headers), 'outer_cgi_header_helpers_unchanged': True,
        'readonly_original_runtime_bodies': bodies, 'db_actor_rpc_result_capture_semantics_unchanged': True,
        'workspace_lock_package_name_only': True, 'original_sources_modified': False}


def build_gate(data, raw, source):
    host, boot = data['host-build.json'], data['bootstrap-build.json']
    build_helpers.host_gate(host, source, PREFIX, 32, 175); boot_anchor = build_helpers.host_gate(boot, source, BOOT, 31, 145)
    for key in ['driver', 'source_fs_registry', 'derivation']:
        exact(boot[key + '_before'], boot[key + '_after'], 'bootstrap_unchanged_' + key)
        if key != 'driver': exact(boot[key + '_before'], boot_anchor[key + '_before'], 'bootstrap_fixed_' + key)
    build_helpers.driver_gate(boot['driver_before'], source)
    exact(host['before'], host['after'], 'utf8_before_after_build_gates')
    snapshot = {'receipt_path': BOOT + 'evidence/linux-build.json', 'receipt_identity': identity(raw['bootstrap-build.json']),
        'executable': boot['executable'], 'executable_identity': boot['executable_identity'],
        'unit_executable': boot['units']['executable'], 'unit_executable_identity': boot['units']['executable_identity'],
        'source_inputs': 145, 'units': 31, 'original_build_fully_reverified': True}
    exact(host['before']['old_bootstrap'], snapshot, 'old_31_receipt_and_binaries_preserved')
    exact(host['before']['driver'], boot['driver_before'], 'same_job_driver_gate')
    exact(host['before']['source_fs'], boot['source_fs_registry_before'], 'same_job_source_fs_gate')
    exact(host['before']['derivation'], runtime_derivation(source), 'unchanged_actor_rpc_collector_module_bodies')
    for key in ['actual_php_wasm_executed', 'native_php_executed', 'database_connected', 'full_wordpress_bootstrap', 'product_runtime_selected']:
        require(host[key] is False, 'host_build_only_scope')
    for key in ['database_started', 'database_connected', 'php_executed', 'wasm_executed', 'product_runtime_selected']:
        require(boot[key] is False, 'bootstrap_build_only_scope')


def frozen_validator(source):
    return source.functions(PREFIX + 'run_check.py', ['canonical', 'validate'], source.constants(PREFIX + 'run_check.py'))


def positive_gate(data, raw, source, backend, native, prepared, anchor):
    exact(set(data), set(member_map(backend)), 'eight_positive_public_records')
    build_gate(data, raw, source); exact(data['prepared.json'], prepared, 'complete_thirteen_file_preparation')
    exact(data['native-prepared.json'], native['prepared'], 'complete_native_nineteen_preparation')
    run, host = data['run.json'], data['host-build.json']
    require(run['schema'] == 'recastpress.php-wasix-wpdb-utf8-check.v0.5' and run['passed'] is True
        and run['outcome'] == 'fixed_wasix_utf8_corpus_passed' and run['backend'] == backend
        and run['platform'] == 'linux' and run['source_reverified_after'] is True
        and run['owned_cleanup_completed'] is True, 'positive_utf8_runtime')
    exact(run['input_files'], anchor['input_files'], 'runtime_175_inputs')
    exact(run['preparation_before'], prepared, 'same_job_prepared_sources')
    exact(run['build_before'], host, 'same_job_host_before'); exact(run['build_after'], host, 'same_job_host_after')
    exact(run['module_before'], run['module_after'], 'same_module_before_after')
    managed = old.managed_gate(data, raw, source, run['module_before'])
    value = run['observation']; expected = native['proofs'][backend]['observation']
    validation = frozen_validator(source)['validate'](value, expected, prepared)
    exact(run['validation'], validation, 'source_frozen_full_controller_validation')
    require(value['native_backend_match'] == backend and value['guest_execution_started'] is True, 'correct_native_backend_and_actual_guest')
    module = data['build.json']['php_wasm']
    exact(value['module'], {**{k: module[k] for k in ['path', 'bytes', 'sha256']}, 'verified': True}, 'same_job_php_module')
    exact(value['build_receipt'], {'path': old.MANAGED + 'evidence/build.json', 'sha256': sha(raw['build.json']),
        'verified': True, 'full_chain_verified_by_host': False}, 'same_job_module_receipt')
    prep_wire = raw['prepared.json']
    exact(value['prepared'], {'path': 'target/php-wasix-wpdb-utf8-probe/prepared.json', 'bytes': len(prep_wire),
        'sha256': sha(prep_wire), 'files': prepared['files'], 'input_files': prepared['input_files'], 'verified': True}, 'same_job_prepared_bytes')
    exact(value['native_oracles'], prepared['native_oracles'], 'same_native_whole_oracle_pins')
    old.integer(value['module_compilations'], 1, 1)
    for seq, operation in enumerate(value['operations'], 1):
        old.integer(operation['seq'], seq, seq); require(type(operation['ok']) is bool and operation['ok'] is True, 'typed_success_operation')
        for key, maximum in [('request_data_bytes', 65536), ('request_frame_bytes', 262144), ('reply_frame_bytes', 524288)]:
            old.integer(operation[key], 0 if key == 'request_data_bytes' else 1, maximum)
        for key, maximum in [('row_count', 256), ('column_count', 32)]:
            if operation[key] is not None: old.integer(operation[key], 0, maximum)
    for key in ['physical_connection_created', 'physical_disconnect_attempted', 'physical_disconnect_completed',
        'db_actor_joined', 'db_actor_exited', 'fresh_runtime', 'source_rechecked_after_execution']:
        require(value[key] is True, 'actual_completion_' + key)
    capture = value['cgi_capture']; old.integer(capture['stdout_bytes'], 1, 65*1024)
    require(capture['response']['header_count'] in (1, 2), 'strict_json_cgi_header_count')
    for key in ['container_started', 'container_stopped', 'container_removed', 'container_normal_exit_verified',
        'owned_database_identity_verified_before_fixture_ddl', 'utf8_fixture_empty_before', 'unused_wire_fixture_empty_after',
        'independent_final_database_rows_verified']:
        require(run[key] is True, 'owned_database_' + key)
    require(type(run['final_crud_row_count']) is int and run['final_crud_row_count'] == 0, 'independent_empty_crud_table')
    for key in ['full_wordpress_bootstrap', 'http_listener_exercised', 'native_php_executed', 'native_php_fallback',
        'product_runtime_selected', 'standard_wpdb_compatibility_verified', 'normalization_applied',
        'memory_cpu_limits_verified_for_this_integration', 'forced_owned_container_cleanup', 'normal_container_stop_failed']:
        require(run.get(key, False) is False, 'runtime_unclaimed_' + key)
    for command in run['commands']: require(command['owned_reaped'] is True, 'owned_database_child_reaped')
    pin = parse_json(source.read('experiments/php-wasix-wpdb-probe/container-images.json'))['images'][backend]
    exact(run['image'], pin['repository'] + '@' + pin['platform_digest'], 'fixed_db_image')
    exact(run['image_config_digest'], pin['image_config_digest'], 'fixed_db_config_digest')
    execution = run['execution']; require(execution['owned_reaped'] is True, 'owned_host_reaped'); old.integer(execution['owned_pid'], 1)
    exact(execution['exit_code'], 0, 'owned_host_exit'); exact(execution['stderr_bytes'], 0, 'owned_host_stderr_empty')
    exact(execution['stderr_sha256'], sha(b''), 'owned_host_stderr_digest')
    lock = tomllib.loads(source.read(PREFIX + 'Cargo.lock').decode())
    serde = [p for p in lock['package'] if p['name'] == 'serde_json']; require(len(serde) == 1
        and not any(p.startswith('indexmap') for p in serde[0].get('dependencies', [])), 'sorted_serde_map')
    exact({'bytes': execution['stdout_bytes'], 'sha256': execution['stdout_sha256']}, identity(canonical(value) + b'\n'), 'public_host_json_reserialization')
    return {'api_cases': 31, 'public_queries': 22, 'runtime_source_inputs': 175, 'host_units': 32,
        'previous_host_units': 31, 'previous_host_inputs': 145, 'prepared_inputs': 29, 'native_prepared_inputs': 19,
        'guest_files': 13, 'original_wp_sources': 8, 'whole_native_observations_state_and_sql_equal': True,
        'read_only_original_actor_rpc_collector_imports_verified': True, 'normalization_applied': False,
        'public_host_json_reserialization_verified': True, 'managed': managed,
        'private_nonce_query_reply_cgi_digests_recomputed_offline': False}


def immutable(path, wire):
    require(path.resolve() == path and path.is_relative_to(HERE / 'evidence'), 'owned_import_path')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists(): exact(read(path, 8*1024*1024), wire, 'immutable_import_changed')
    else:
        with path.open('xb') as stream: stream.write(wire)


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--run', required=True)
    parser.add_argument('--backend', required=True, choices=['mysql', 'mariadb']); parser.add_argument('--verify-only', action='store_true'); args = parser.parse_args()
    require(re.fullmatch('[1-9][0-9]{0,19}', args.run), 'run_id')
    work = ROOT / 'target/php-wasix-wpdb-utf8-probe/ci'
    api_wire = read(work / ('api-' + args.run + '-' + args.backend + '.json'), 128*1024); api = parse_json(api_wire)
    api_gate(api, args.run, args.backend); old.privacy_gate(api); source = old.GitSource(api['head_sha'])
    for name in [HELPER, SHARED, base.LEGACY]: exact(source.read(name), read(ROOT / name, 512*1024), 'frozen_shared_verifier_changed')
    native = native_contract(source); prepared = prepared_contract(source, native); anchor = source_inventory(source)
    raw, data, members = archive_gate(work / ('run-' + args.run + '-' + args.backend + '.zip'), api, args.backend, native)
    stages = stages_gate(data['ci-stages.json'], api, args.backend, source)
    positive = api['run_conclusion'] == api['job']['conclusion'] == 'success' and stages
    for name, expected in [('prepared.json', prepared), ('native-prepared.json', native['prepared'])]:
        if name in data: exact(data[name], expected, 'complete_preparation_even_on_failure')
    failure = None
    try: checked = sum(old.source_bindings_in_records(source, value) for value in data.values())
    except Exception as error:
        if positive: raise
        checked = 0; failure = type(error).__name__
    summary = {'overall_positive': False, 'raw_verdicts_rewritten': False, 'source_binding_entries_checked': checked,
        'source_binding_verification_passed': failure is None, 'source_binding_failure_class': failure}
    if positive: summary.update(positive_gate(data, raw, source, args.backend, native, prepared, anchor)); summary['overall_positive'] = True
    else:
        run = data.get('run.json', {}); value = run.get('observation', {})
        summary.update(recorded_passed=run.get('passed'), recorded_outcome=run.get('outcome'),
            recorded_failure_stage=run.get('failure_stage'), recorded_host_passed=value.get('passed'),
            recorded_host_phase=value.get('phase'))
    proof = {'schema': 'recastpress.php-wasix-wpdb-utf8-ci-import.v0.5', 'repository': old.REPOSITORY,
        'repository_id': old.REPOSITORY_ID, 'run_id': api['run_id'], 'run_attempt': api['run_attempt'], 'backend': args.backend,
        'source_commit': source.commit, 'run_conclusion': api['run_conclusion'], 'job': api['job'], 'artifact': api['artifact'],
        'api_metadata_sha256': sha(api_wire), 'archive_member_map': members, 'raw_json': {name: identity(wire) for name, wire in raw.items()},
        'original_git_sources': {name: identity(wire) for name, wire in sorted(source.cache.items())},
        'verifier_input_files': {path.relative_to(ROOT).as_posix(): sha(read(path, 512*1024))
            for path in [Path(__file__).resolve(), ROOT / HELPER, ROOT / SHARED, ROOT / base.LEGACY]}, 'verification': summary,
        'network_used_by_importer': False, 'php_wasm_or_database_executed_by_importer': False,
        'source_archive_module_tool_binaries_reverified': False, 'private_nonce_or_guest_bytes_recomputed_offline': False,
        'product_runtime_selected': False,
        'privacy_exception_scope': 'whole fixed synthetic native or expected case rows and exact full SQL trace only; raw bytes unchanged'}
    public_gate(proof, native)
    wire = (json.dumps(proof, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    require(len(wire) <= 2*1024*1024, 'import_proof_quota')
    if not args.verify_only:
        destination = HERE / ('evidence/ci-' + args.run + '-' + args.backend)
        for name, value in raw.items(): immutable(destination / 'raw' / members[name], value)
        immutable(destination / 'api-metadata.json', api_wire); immutable(destination / 'provenance.json', wire)
    print('UTF8 CI evidence: ' + ('verified positive' if summary['overall_positive'] else 'preserved failure/partial') + ' (' + args.backend + ')')
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('UTF8 import rejected: ' + reason, file=sys.stderr); sys.exit(1)
