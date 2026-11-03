"""Import eight public CI JSON records without executing PHP, Wasm, DB or network.

Only a complete response identical to the pinned synthetic native home may
contain public HTML bytes. Private SQL, results, CGI and nonce bytes remain
unavailable: their digests are source-bound CI observations, not recomputed.
"""
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
PREFIX = 'experiments/php-wasix-wp-request-probe/'
BOOT = 'experiments/php-wasix-wp-bootstrap-probe/'
SHARED = BOOT + 'import_evidence.py'
spec = importlib.util.spec_from_file_location('rp_home_offline_bootstrap_verifier', ROOT / SHARED)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
old = base.old
require, sha, read, identity, exact, canonical, parse_json = (
    base.require, base.sha, base.read, base.identity, base.exact, base.canonical, base.parse_json)


def member_map(backend):
    return {'run.json': PREFIX + 'evidence/linux-' + backend + '.json',
        'host-build.json': PREFIX + 'evidence/linux-build.json',
        'bootstrap-build.json': BOOT + 'evidence/linux-build.json',
        'ci-stages.json': PREFIX + 'evidence/ci-stages.json',
        'build.json': old.MANAGED + 'evidence/build.json',
        'toolchain.json': old.CGI + 'evidence/toolchain.json',
        'prepared.json': 'target/php-wasix-wp-request-probe/prepared.json',
        'bootstrap-prepared.json': 'target/php-wasix-wp-bootstrap-probe/prepared.json'}


def native_responses(source):
    pins = source.constants(PREFIX + 'linux_database.py')['ORACLES']
    responses = []
    for backend in ('mysql', 'mariadb'):
        name = 'experiments/wp-request-probe/evidence/windows-' + backend + '-native.json'
        wire = source.read(name)
        require(sha(wire) == pins[backend], 'fixed_native_public_home_oracle')
        value = parse_json(wire)
        require(value['passed'] is True and value['old_native_read_baseline_reproduced'] is True
            and value['normal_admin_shutdown_verified'] is True, 'accepted_native_home_oracle')
        response = value['observations']['home']['response']
        exact(set(response), {'status', 'headers_hex', 'body_bytes', 'body_hex', 'body_sha256'}, 'native_response_shape')
        require(type(response['status']) is int and response['status'] == 200
            and type(response['body_bytes']) is int and response['body_bytes'] == 69399
            and type(response['body_hex']) is str and re.fullmatch('[0-9a-f]+', response['body_hex']) is not None,
            'native_public_body_shape')
        body = bytes.fromhex(response['body_hex'])
        require(identity(body) == {'bytes': response['body_bytes'], 'sha256': response['body_sha256']}
            and response['body_sha256'] == 'ef43f26984536cae43c4e703edef98dbaaf2bca79cc972a4373bf27f01cabdd1',
            'native_complete_public_body_identity')
        responses.append(response)
    exact(responses[0], responses[1], 'native_cross_backend_complete_response')
    return responses[0]


def public_gate(value, response=None):
    # Inspect a temporary view only. Imported raw JSON bytes are never modified.
    def inspect(v):
        if type(v) is dict:
            if 'body_hex' in v:
                require(response is not None, 'unapproved_public_html')
                exact(v, response, 'only_exact_synthetic_native_html_is_public')
                # One public header happens to encode to 48 hex characters;
                # it is not a nonce. Exempt the complete already-equal
                # response atomically, including its exact header bytes.
                v = {k: item for k, item in v.items() if k not in ('body_hex', 'headers_hex')}
            return {k: inspect(item) for k, item in v.items()}
        if type(v) is list:
            return [inspect(item) for item in v]
        return v
    base.public_gate(inspect(value))


def api_gate(api, run, backend):
    # Reuse the strict compact metadata shape, changing only the explicit job
    # and artifact identities; never rewrite metadata to satisfy an old gate.
    exact(set(api), {'repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha',
        'run_conclusion', 'job', 'artifact'}, 'api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'api_repository_id')
    exact(api['repository'], old.REPOSITORY, 'api_repository')
    old.integer(api['run_id'], 1); old.integer(api['run_attempt'], 1, 1000)
    require(str(api['run_id']) == run and api['run_conclusion'] in old.CONCLUSIONS, 'api_run')
    require(type(api['head_sha']) is str and re.fullmatch('[0-9a-f]{40}', api['head_sha']), 'api_commit')
    job, artifact = api['job'], api['artifact']
    exact(set(job), {'id', 'name', 'conclusion', 'steps'}, 'api_job_fields'); old.integer(job['id'], 1)
    require(job['name'] == 'Cranelift WP theme / ' + backend and job['conclusion'] in old.CONCLUSIONS, 'api_job')
    require(type(job['steps']) is list and 1 <= len(job['steps']) <= 64, 'api_steps')
    for step in job['steps']:
        exact(set(step), {'name', 'conclusion'}, 'api_step_fields')
        require(type(step['name']) is str and 0 < len(step['name']) <= 160
            and step['conclusion'] in old.CONCLUSIONS, 'api_step')
    exact(set(artifact), {'id', 'name', 'size_in_bytes', 'digest'}, 'api_artifact_fields')
    old.integer(artifact['id'], 1); old.integer(artifact['size_in_bytes'], 1, 24 * 1024 * 1024)
    require(artifact['name'] == 'php-wasix-wp-request-' + backend + '-' + str(api['run_attempt'])
        and type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']), 'api_artifact')


def archive_gate(path, api, backend, source):
    wire = read(path, 24 * 1024 * 1024)
    exact(identity(wire), {'bytes': api['artifact']['size_in_bytes'],
        'sha256': api['artifact']['digest'][7:]}, 'api_archive_identity')
    reverse = {p: n for n, p in member_map(backend).items()}
    raw, members, expanded = {}, {}, 0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(1 <= len(entries) <= 8 and len({r.filename for r in entries}) == len(entries), 'zip_member_count')
        for entry in entries:
            old.relative(entry.filename)
            require(entry.filename in reverse and not entry.is_dir() and entry.flag_bits & 1 == 0
                and stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFLNK
                and 0 < entry.file_size <= 8 * 1024 * 1024, 'public_json_member')
            expanded += entry.file_size; require(expanded <= 24 * 1024 * 1024, 'zip_expansion_limit')
            value = archive.read(entry); require(len(value) == entry.file_size, 'zip_member_size')
            name = reverse[entry.filename]; raw[name] = value; members[name] = entry.filename
    require('ci-stages.json' in raw, 'stage_record_required')
    data = {n: parse_json(b) for n, b in raw.items()}
    response = native_responses(source)
    for value in data.values(): public_gate(value, response)
    return raw, data, members


def stages_gate(value, api, backend, source):
    exact(set(value), {'schema', 'source_commit', 'run_id', 'run_attempt', 'backend', 'stages'}, 'stage_fields')
    require(value['schema'] == 'recastpress.php-wasix-wp-request-ci-stages.v0.5'
        and value['source_commit'] == api['head_sha'] and value['run_id'] == str(api['run_id'])
        and value['run_attempt'] == str(api['run_attempt']) and value['backend'] == backend, 'stage_identity')
    names = {}
    for block in source.read('.github/workflows/php-wasix-wp-request.yml').decode().split('      - name: ')[1:]:
        match = re.search(r'^        id: ([a-z_]+)$', block, re.M)
        if match: names[match[1]] = block.splitlines()[0].strip()
    exact(set(names), {'php', 'deps', 'parser', 'packages', 'tools', 'module', 'guest', 'rust', 'bootstrap', 'host', 'contract'},
        'workflow_stage_inventory')
    exact(set(value['stages']), set(names), 'actual_stage_inventory')
    for key, name in names.items():
        rows = [r for r in api['job']['steps'] if r['name'] == name]
        require(len(rows) == 1, 'api_stage_step')
        exact(value['stages'][key], rows[0]['conclusion'], 'api_vs_stage_conclusion')
    return all(v == 'success' for v in value['stages'].values())


def return_fields(source, name, function):
    node = next(n for n in source.tree(name).body if isinstance(n, ast.FunctionDef) and n.name == function)
    require(isinstance(node.body[-1], ast.Return) and isinstance(node.body[-1].value, ast.Dict), 'source_return_dictionary')
    value = node.body[-1].value
    return {ast.literal_eval(k): v for k, v in zip(value.keys, value.values)}


def prepared_gate(value, previous, source):
    base.prepared_gate(previous, source)
    name = PREFIX + 'prepare.py'; constants = source.constants(name)
    original = {n: source.read(n) for n in constants['PINS']}
    for n, wire in original.items(): exact((len(wire), sha(wire)), constants['PINS'][n], 'home_parent_whole_pin')
    pure = source.functions(name, ['once', 'section', 'expected_fixtures'], constants)
    fixtures = pure['expected_fixtures'](original)
    for n in constants['FIXTURES']: exact(source.read(PREFIX + 'fixtures/' + n), fixtures[n], 'home_whole_derived_fixture')
    render = source.constants('experiments/wp-request-probe/prepare.py')['RENDER']
    require(fixtures['bootstrap.php'].count(render) == 1, 'unchanged_native_render_block')
    exact(fixtures['adapter.php'], original[BOOT + 'fixtures/adapter.php'], 'unchanged_4096_adapter')
    fields = return_fields(source, name, 'derivation_gate')
    derivation = {'base_commit': constants['BASE_COMMIT'], 'original_git_objects_verified_by_preparer': False,
        'parents': {n: identity(b) for n, b in original.items()},
        'derived': {n: identity(fixtures[n]) for n in constants['FIXTURES']},
        'adapter4096_bytes_unchanged': True, 'native_render_block_sha256': sha(render),
        'native_render_block_bytes_unchanged': True, 'allowed_changes': ast.literal_eval(fields['allowed_changes']),
        'html_normalization_applied': False, 'core_theme_dispatcher_bytes_modified': False, 'owned_db_overlay_files': 1}
    exact(value['derivation'], derivation, 'home_complete_derivation')
    exact(set(value), set(return_fields(source, name, 'metadata')), 'home_complete_preparation_fields')
    exact(value['previous_full_bootstrap_derivation'], previous['derivation'], 'previous_bootstrap_derivation')
    oracles = {}
    for backend in ('mysql', 'mariadb'):
        n = 'experiments/wp-request-probe/evidence/windows-' + backend + '-native.json'
        oracles[backend] = {'path': n, **identity(source.read(n))}
    exact(value['native_oracles'], oracles, 'home_native_oracle_pins')
    native = parse_json(source.read(oracles['mysql']['path']))['preparation_before']
    names = set(previous['input_files']) | set(native['input_files']) | set(constants['PINS'])
    names |= {name, *[PREFIX + 'fixtures/' + n for n in constants['FIXTURES']]}
    require(len(names) == 41, 'forty_one_home_preparation_inputs')
    exact(value['input_files'], source.hashes(names), 'complete_home_preparation_inventory')
    for k in ['core_site', 'core_manifest', 'core_inventory']:
        exact(value[k], native[k], 'home_unchanged_full_' + k)
    exact(value['core_archive'], native['archive'], 'home_original_core_archive')
    exact([value[k] for k in ['core_guest_root', 'private_context_guest_path', 'core_manifest_guest_path']],
        ['/wordpress', '/rp-request/context.json', '/rp-request/source-manifest.json'], 'home_fixed_guest_paths')
    files = [{'source': PREFIX + 'fixtures/' + n, 'guest': '/wordpress/wp-content/db.php' if n == 'db.php'
        else '/' + PREFIX + 'fixtures/' + n, **identity(fixtures[n]), 'guest_read_only': True,
        'kind': 'owned_wpdb_dropin' if n == 'db.php' else 'derived_fixture'} for n in constants['FIXTURES']]
    exact(value['files'], files, 'home_five_guest_files')
    require(value['schema'] == 'recastpress.php-wasix-wp-request-prepared.v0.5'
        and value['outcome'] == 'full_core_and_owned_home_dropin_prepared' and value['inputs_unchanged'] is True,
        'home_prepared_identity')
    exact(value['html_limit_bytes'], 1048576, 'html_limit'); exact(value['wrapper_limit_bytes'], 3145728, 'wrapper_limit')
    for k in ['guest_context_contains_db_credentials', 'native_reference_binaries_reverified', 'core_files_modified',
        'secret_context_in_prepared_metadata', 'php_executed', 'wasm_executed', 'database_connected',
        'product_runtime_selected', 'guest_readonly_enforcement_executed', 'html_normalization_applied']:
        require(value[k] is False, 'home_prepared_unclaimed_' + k)
    return {'preparation_inputs': 41, 'derived_files': 5, 'core_files': 3782, 'core_directories': 467,
        'owned_overlay_files': 1, 'native_render_and_adapter_whole_bytes_verified': True,
        'source_archive_binaries_reverified_offline': False}


def frozen_checker(source):
    previous = base.frozen_checker(source)
    name = PREFIX + 'run_check.py'
    node = next(n for n in source.tree(name).body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'COMPARE' for t in n.targets))
    require(isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.Add)
        and isinstance(node.value.left, ast.Attribute) and isinstance(node.value.left.value, ast.Name)
        and node.value.left.value.id == 'original' and node.value.left.attr == 'COMPARE', 'home_compare_source_shape')
    compared = previous['COMPARE'] + ast.literal_eval(node.value.right)
    exact(compared[-5:], ['request_dispatch_completed', 'template_loader_completed', 'anonymous_request', 'theme_rendered', 'response'],
        'home_additional_comparison_fields')
    def reader(path, maximum):
        wire = source.read(Path(path).relative_to(ROOT).as_posix())
        require(len(wire) <= maximum, 'native_home_read_quota'); return wire
    return source.functions(name, ['canonical', 'reference', 'validate_home'], {
        'BASELINES': source.constants(PREFIX + 'linux_database.py')['ORACLES'], 'COMPARE': compared,
        'build': SimpleNamespace(read=reader)})


def php_diagnostic(value, source, home=False):
    if not home: return base.php_diagnostic(value, source)
    if type(value) is not dict: return {}
    text = source.read(PREFIX + 'fixtures/bootstrap.php').decode('utf-8'); result = {}
    for field, constant in [('phase', 'RP_PUBLIC_PHASES'), ('failure', 'RP_PUBLIC_FAILURE_CODES'),
            ('failure_class', 'RP_PUBLIC_FAILURE_CLASSES')]:
        match = re.search(r'^const ' + constant + r' = (.*);$', text, re.M)
        require(match is not None, 'home_diagnostic_literal')
        allowed = parse_json(match[1].encode())
        require(type(allowed) is list and all(type(v) is str for v in allowed), 'home_diagnostic_literal_shape')
        if field in value:
            require(type(value[field]) is str and value[field] in allowed, 'unapproved_home_diagnostic')
            result[field] = value[field]
    if 'passed' in value:
        require(type(value['passed']) is bool, 'home_php_verdict_type'); result['passed'] = value['passed']
    return result


def host_build_gate(host, source, home):
    prefix = PREFIX if home else BOOT
    anchor = parse_json(source.read(prefix + 'evidence/windows-build.json'))
    require(anchor['passed'] is True and anchor['source_reverified_after'] is True, 'frozen_host_build_anchor')
    exact(host['input_files'], anchor['input_files'], 'complete_host_source_inventory')
    require(host['schema'] == 'recastpress.php-wasix-wp-' + ('request' if home else 'bootstrap') + '-build.v0.5'
        and host['outcome'] == ('locked_offline_home_host_build_passed' if home else 'locked_offline_bootstrap_host_build_passed')
        and host['passed'] is True and host['locked_offline'] is True and host['owned_cleanup_completed'] is True
        and host['source_reverified_after'] is True, 'positive_host_build')
    for key in ['driver', 'source_fs_registry', 'derivation']:
        exact(host[key + '_before'], host[key + '_after'], 'host_unchanged_' + key)
        # Driver locks may differ legitimately between freshly prepared CI and
        # historical local caches; both before/after remain source-bound.
        if key != 'driver': exact(host[key + '_before'], anchor[key + '_before'], 'frozen_host_' + key)
    plan_name = 'experiments/wpdb-rpc-probe/driver-plan.json'
    plan = parse_json(source.read(plan_name)); driver = host['driver_before']
    exact(driver['plan_sha256'], sha(source.read(plan_name)), 'fixed_driver_plan')
    expected_trees = {p['name']: {'files': p['local_regular_files_matching_crate_archive'],
        'inventory_sha256': plan['patch']['source_tree_inventory_after_sha256'] if p['name'] == 'mysql_async'
        else p['source_file_inventory_sha256']} for p in plan['crates']}
    exact(driver['source_trees'], expected_trees, 'complete_patched_driver_source_inventories')
    seed = source.read(plan['existing_reference']['cargo_lock'])
    exact(driver['seed_sha256'], sha(seed), 'original_driver_seed_lock')
    require(driver['stage_modified'] is False and type(driver['historical_resolved_lock_state_present']) is bool,
        'unchanged_owned_driver_stage')
    if driver['historical_resolved_lock_state_present'] is False:
        exact(driver['stage_lock'], identity(seed), 'fresh_ci_stage_uses_original_seed_lock')
    else:
        old.integer(driver['stage_lock']['bytes'], 1, 1024 * 1024); old.digest(driver['stage_lock']['sha256'])
    exact(host['units']['tests'], anchor['units']['tests'], 'frozen_host_unit_inventory')
    require(host['units']['passed'] is True and len(host['units']['tests']) >= 31
        and len(set(host['units']['tests'])) == len(host['units']['tests']), 'all_actual_host_units')
    require(len(host['commands']) == 4, 'four_host_build_commands')
    for row, (stage, deadline) in zip(host['commands'], [('unit-build', 1200), ('unit-list', 10), ('unit-run', 60), ('normal-build', 1200)]):
        exact(row['timeout_seconds'], deadline, 'owned_build_deadline')
        require(row['stage'] == stage and row['owned_reaped'] is True, 'owned_build_stage')
        old.integer(row['owned_pid'], 1); exact(row['exit_code'], 0, 'owned_build_exit')
    for key in ['database_started', 'database_connected', 'php_executed', 'wasm_executed', 'product_runtime_selected']:
        require(host[key] is False, 'host_build_unclaimed_' + key)
    require(host['build_profile'] == 'release', 'linux_release_host')
    old.relative(host['executable']); old.digest(host['executable_identity']['sha256'])
    old.integer(host['executable_identity']['bytes'], 1, 256 * 1024 * 1024)
    return anchor


def operation_types(value, maximum):
    for seq, op in enumerate(value['operations'], 1):
        old.integer(op['seq'], 1, maximum); exact(op['seq'], seq, 'typed_operation_sequence')
        require(type(op['ok']) is bool, 'typed_operation_status')
        for key, bound in [('request_data_bytes', 65536), ('request_frame_bytes', 262144), ('reply_frame_bytes', 524288)]:
            old.integer(op[key], 0 if key == 'request_data_bytes' else 1, bound)
        for key, bound in [('row_count', 256), ('column_count', 32), ('server_error_code', 65535)]:
            if op[key] is not None: old.integer(op[key], 0, bound)
    for scope, keys in [(value['rpc'], ['operations', 'request_bytes', 'response_bytes', 'response_read_bytes',
            'pending_bytes', 'unread_response_bytes']), (value['managed_access'], ['requests', 'generated_bytes',
            'read_bytes', 'open_events', 'closed_handles', 'active_handles', 'allowed', 'denied', 'missing',
            'unmanaged', 'invalid', 'io_errors', 'pending_bytes', 'unread_response_bytes'])]:
        for key in keys: old.integer(scope[key], 0, 2 ** 31)
    exact(value['module_compilations'], 1, 'typed_one_module_compilation')


def positive_gate(data, raw, source, backend):
    run, host, boot = data['run.json'], data['host-build.json'], data['bootstrap-build.json']
    require(run['schema'] == 'recastpress.php-wasix-wp-request-check.v0.5' and run['passed'] is True
        and run['outcome'] == 'fixed_full_wordpress_wasix_home_passed' and run['platform'] == 'linux'
        and run['backend'] == backend and run['source_reverified_after'] is True
        and run['owned_cleanup_completed'] is True, 'positive_home_run')
    anchor = host_build_gate(host, source, True); host_build_gate(boot, source, False)
    exact(run['input_files'], anchor['input_files'], 'complete_runtime_source_inventory')
    exact(run['build_receipt'], host, 'same_job_home_build'); exact(run['bootstrap_build_receipt'], boot, 'same_job_bootstrap_build')
    expected_old = {'receipt_path': BOOT + 'evidence/linux-build.json', 'receipt_identity': identity(raw['bootstrap-build.json']),
        'executable': boot['executable'], 'executable_identity': boot['executable_identity'],
        'unit_executable': boot['units']['executable'], 'unit_executable_identity': boot['units']['executable_identity'],
        'source_inputs': len(boot['input_files']), 'units': len(boot['units']['tests']), 'original_build_fully_reverified': True}
    exact(host['old_build_before'], expected_old, 'same_job_previous_build_before')
    exact(host['old_build_after'], expected_old, 'same_job_previous_build_after')
    preparation = prepared_gate(data['prepared.json'], data['bootstrap-prepared.json'], source)
    exact(run['preparation_before'], data['prepared.json'], 'same_job_home_prepared')
    exact(run['bootstrap_preparation_before'], data['bootstrap-prepared.json'], 'same_job_bootstrap_prepared')
    exact(run['module_provenance'], run['module_provenance_after'], 'module_unchanged')
    managed = old.managed_gate(data, raw, source, run['module_provenance'])
    previous, home = base.frozen_checker(source), frozen_checker(source)
    native_boot, native_home = previous['reference'](backend), home['reference'](backend)
    for prefix in [PREFIX, BOOT]:
        lock = tomllib.loads(source.read(prefix + 'Cargo.lock').decode('utf-8'))
        crates = [p for p in lock['package'] if p['name'] == 'serde_json']
        require(len(crates) == 1 and not any(d.startswith('indexmap') for d in crates[0].get('dependencies', [])),
            'default_sorted_json_map_required')
    exact(set(run['observations']), {'install', 'read', 'home'}, 'three_fresh_guest_modes')
    exact(set(run['validations']), {'install', 'read', 'home'}, 'three_mode_validations')
    for mode in ['install', 'read', 'home']:
        value = run['observations'][mode]; is_home = mode == 'home'
        expected = home['validate_home'](value, mode, native_home) if is_home else previous['validate'](value, mode, native_boot[mode])
        exact(run['validations'][mode], expected, 'source_bound_native_comparison')
        operation_types(value, 4096 if is_home else previous['source_operation_limit'])
        execution = run[mode + '_execution']
        require(execution['owned_reaped'] is True, 'owned_mode_reaped'); old.integer(execution['owned_pid'], 1)
        exact(execution['exit_code'], 0, 'owned_mode_exit'); exact(execution['stderr_bytes'], 0, 'clean_mode_stderr')
        exact(execution['stderr_sha256'], sha(b''), 'empty_mode_stderr_identity')
        stdout = canonical(value) + b'\n'
        exact({'bytes': execution['stdout_bytes'], 'sha256': execution['stdout_sha256']}, identity(stdout), 'public_host_stdout_identity')
        php = value['php_result']
        require(php['schema'] == 'recastpress.php-wasix-wp-' + ('request' if is_home else 'bootstrap') + '-php-observation.v0.5'
            and php['outcome'] == ('fixed_wasix_home_passed' if is_home else 'fixed_wasix_bootstrap_passed'), 'php_mode_identity')
        php_diagnostic(php, source, is_home)
        module = data['build.json']['php_wasm']
        exact(value['module'], {'path': module['path'], 'bytes': module['bytes'], 'sha256': module['sha256'], 'verified': True}, 'same_job_guest_module')
        exact(value['build_receipt'], {'path': old.MANAGED + 'evidence/build.json', 'sha256': sha(raw['build.json']),
            'verified': True, 'full_chain_verified_by_host': False}, 'same_job_guest_build_receipt')
    for key in ['container_started', 'container_stopped', 'container_removed', 'container_normal_exit_verified',
        'owned_database_identity_verified_before_fixture_ddl', 'independent_wordpress_final_verified',
        'installer_schema_ddl_and_crud', 'reader_schema_crud']:
        require(run[key] is True, 'normal_container_' + key)
    exact(run['wordpress_table_count'], 12, 'twelve_original_wordpress_tables')
    for key in ['forced_owned_container_cleanup', 'normal_container_stop_failed', 'production_database_used',
        'management_credentials_supplied_to_php', 'response_normalized', 'product_runtime_selected',
        'http_listener_exercised', 'standard_wpdb_compatibility_verified',
        'memory_cpu_limits_verified_for_this_integration', 'all_resources_immediately_reclaimed']:
        require(run.get(key, False) is False, 'unclaimed_or_failure_scope_' + key)
    for command in run['commands']: require(command['owned_reaped'] is True, 'owned_database_command_reaped')
    stamp = source.constants(PREFIX + 'linux_database.py')['STAMP']
    seed = run['fixed_public_date_seed']
    require(seed['timestamp'] == stamp and seed['actual_database_updated'] is True
        and seed['response_body_normalized'] is False, 'fixed_public_date_seed')
    native_record = parse_json(source.read('experiments/wp-request-probe/evidence/windows-' + backend + '-native.json'))
    exact(seed['sql_sha256'], native_record['fixed_public_date_seed']['sql_sha256'], 'same_original_date_seed_sql')
    for phase in ['before_home', 'after_home']:
        exact(run['public_date_verification'][phase], {'post_ids': [1, 2], 'comment_ids': [1], 'timestamp': stamp, 'verified': True},
            'independent_public_date_verification')
    pin = parse_json(source.read('experiments/php-wasix-wpdb-probe/container-images.json'))['images'][backend]
    exact(run['image'], pin['repository'] + '@' + pin['platform_digest'], 'fixed_database_image')
    exact(run['image_config_digest'], pin['image_config_digest'], 'database_config_digest')
    require(type(run['sql_server_version']) is str and (run['sql_server_version'] == pin['tag']
        or run['sql_server_version'].startswith(pin['tag'] + '-')), 'actual_database_version')
    return {'all_install_read_home_verified': True, 'prepared': preparation, 'managed': managed,
        'runtime_source_inputs': len(run['input_files']), 'bootstrap_source_inputs': len(boot['input_files']),
        'home_host_units': len(host['units']['tests']), 'bootstrap_host_units': len(boot['units']['tests']),
        'complete_native_response_equal': True, 'body_bytes': native_home['response']['body_bytes'],
        'body_sha256': native_home['response']['body_sha256'], 'public_host_json_reserialization_verified': True,
        'private_query_reply_cgi_and_nonce_digests_recomputed_offline': False}


def immutable(path, wire):
    require(path.resolve() == path and path.is_relative_to(HERE / 'evidence'), 'owned_import_path')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists(): require(read(path, 8 * 1024 * 1024) == wire, 'immutable_import_changed')
    else:
        with path.open('xb') as stream: stream.write(wire)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True); parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--verify-only', action='store_true'); args = parser.parse_args()
    require(re.fullmatch('[1-9][0-9]{0,19}', args.run), 'run_id')
    work = ROOT / 'target/php-wasix-wp-request-probe/ci'
    api_wire = read(work / ('api-' + args.run + '-' + args.backend + '.json'), 128 * 1024)
    api = parse_json(api_wire); api_gate(api, args.run, args.backend); old.privacy_gate(api)
    source = old.GitSource(api['head_sha'])
    for name in [SHARED, base.LEGACY]:
        exact(source.read(name), read(ROOT / name, 512 * 1024), 'frozen_shared_verifier_helpers_changed')
    raw, data, members = archive_gate(work / ('run-' + args.run + '-' + args.backend + '.zip'), api, args.backend, source)
    stages = stages_gate(data['ci-stages.json'], api, args.backend, source)
    positive = api['run_conclusion'] == api['job']['conclusion'] == 'success' and stages
    if positive: exact(set(raw), set(member_map(args.backend)), 'eight_positive_public_records')
    binding_error = None
    try: bindings = sum(old.source_bindings_in_records(source, v) for v in data.values())
    except Exception as error:
        if positive: raise
        bindings = 0; binding_error = type(error).__name__
    summary = {'overall_positive': False, 'raw_verdicts_rewritten': False, 'source_binding_entries_checked': bindings,
        'source_binding_verification_passed': binding_error is None, 'source_binding_failure_class': binding_error}
    if positive:
        summary.update(positive_gate(data, raw, source, args.backend)); summary['overall_positive'] = True
    else:
        run = data.get('run.json', {})
        summary.update(recorded_outcome=run.get('outcome'), recorded_passed=run.get('passed'),
            recorded_failure_stage=run.get('failure_stage'), public_guest_failures={})
        for mode, value in run.get('observations', {}).items():
            require(mode in ('install', 'read', 'home'), 'closed_failure_mode')
            if type(value) is not dict: continue
            public = {k: value[k] for k in ['passed', 'phase', 'failure', 'failure_reason', 'php_diagnostic'] if k in value}
            php = value.get('php_result')
            if type(php) is not dict: php = value.get('cgi_capture', {}).get('safe_php_observation')
            public['php'] = php_diagnostic(php, source, mode == 'home')
            summary['public_guest_failures'][mode] = public
    proof = {'schema': 'recastpress.php-wasix-wp-request-ci-import.v0.5', 'repository': old.REPOSITORY,
        'repository_id': old.REPOSITORY_ID, 'run_id': api['run_id'], 'run_attempt': api['run_attempt'],
        'source_commit': source.commit, 'backend': args.backend, 'run_conclusion': api['run_conclusion'],
        'job': api['job'], 'artifact': api['artifact'], 'api_metadata_sha256': sha(api_wire),
        'archive_member_map': members, 'raw_json': {n: identity(b) for n, b in raw.items()},
        'original_git_sources': {n: identity(b) for n, b in sorted(source.cache.items())},
        'verifier_input_files': {p.relative_to(ROOT).as_posix(): sha(read(p, 512 * 1024))
            for p in [Path(__file__).resolve(), ROOT / SHARED, ROOT / base.LEGACY]},
        'verification': summary, 'offline_nonce_recomputed': False, 'private_source_bytes_reconstructed': False,
        'source_tool_module_archive_binaries_reverified': False, 'network_used_by_importer': False,
        'php_wasm_or_database_executed_by_importer': False, 'product_runtime_selected': False,
        'privacy_exception_scope': 'exact public CI clang workspace and byte-identical pinned synthetic native home HTML only; raw bytes unchanged'}
    public_gate(proof)
    wire = (json.dumps(proof, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    require(len(wire) <= 2 * 1024 * 1024, 'import_proof_quota')
    if not args.verify_only:
        destination = HERE / ('evidence/ci-' + args.run + '-' + args.backend)
        for name, content in raw.items(): immutable(destination / 'raw' / members[name], content)
        immutable(destination / 'api-metadata.json', api_wire); immutable(destination / 'provenance.json', wire)
    print('WP home CI evidence: ' + ('verified positive' if summary['overall_positive'] else 'preserved failure/partial')
        + ' (' + args.backend + ')')
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('WP home import rejected: ' + reason, file=sys.stderr); sys.exit(1)
