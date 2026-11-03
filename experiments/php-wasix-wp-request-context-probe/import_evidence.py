"""Import ten public CI JSON records without executing PHP, Wasm, DB or network.

Only a complete response identical to the pinned synthetic native home and missing-post responses may
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
PREFIX = 'experiments/php-wasix-wp-request-context-probe/'
THEME = 'experiments/php-wasix-wp-request-probe/'
BOOT = 'experiments/php-wasix-wp-bootstrap-probe/'
SHARED = THEME + 'import_evidence.py'
spec = importlib.util.spec_from_file_location('rp_home_offline_bootstrap_verifier', ROOT / SHARED)
theme = importlib.util.module_from_spec(spec)
spec.loader.exec_module(theme)
base = theme.base
old = base.old
require, sha, read, identity, exact, canonical, parse_json = (
    base.require, base.sha, base.read, base.identity, base.exact, base.canonical, base.parse_json)


def member_map(backend):
    return {'run.json': PREFIX + 'evidence/linux-' + backend + '.json',
        'host-build.json': PREFIX + 'evidence/linux-build.json',
        'theme-build.json': THEME + 'evidence/linux-build.json',
        'bootstrap-build.json': BOOT + 'evidence/linux-build.json',
        'ci-stages.json': PREFIX + 'evidence/ci-stages.json',
        'build.json': old.MANAGED + 'evidence/build.json',
        'toolchain.json': old.CGI + 'evidence/toolchain.json',
        'prepared.json': 'target/php-wasix-wp-request-context-probe/prepared.json',
        'theme-prepared.json': 'target/php-wasix-wp-request-probe/prepared.json',
        'bootstrap-prepared.json': 'target/php-wasix-wp-bootstrap-probe/prepared.json'}


def native_references(source):
    constants = source.constants(PREFIX + 'prepare.py')
    result = {}
    for backend, pin in constants['ORACLES'].items():
        wire = source.read(pin['path'])
        exact(identity(wire), {k: pin[k] for k in ('bytes', 'sha256')}, 'fixed_native_context_oracle')
        value = parse_json(wire)
        for key in ('passed', 'source_reverified_after', 'normal_admin_shutdown_verified',
                'all_command_children_reaped', 'all_owned_cgi_reaped', 'owned_database_cleanup_completed',
                'fresh_repeat_404_response_exact', 'original_install_read_baseline_reproduced'):
            require(value[key] is True, 'accepted_native_context_' + key)
        exact(value['backend'], backend, 'native_backend')
        exact(value['schema'], 'recastpress.wp-request-context-native-check.v0.5', 'native_context_schema')
        exact(value['owned_cgi_count'], 3, 'native_three_requests')
        exact(value['owned_cgi_reaped'], 3, 'native_three_reaped')
        for case, status, length, digest in [
                ('home', 200, 69399, 'ef43f26984536cae43c4e703edef98dbaaf2bca79cc972a4373bf27f01cabdd1'),
                ('missing', 404, 58177, 'd2bda2fdf6bf12e075689fd3af5e87eba2ede1ea953d9f0d7085a63fc11f60bc')]:
            observation = value['observations'][case]
            exact(observation, value['requests'][case]['observation'], 'native_primary_observation')
            response = observation['response']
            exact(set(response), {'status', 'headers_hex', 'body_bytes', 'body_hex', 'body_sha256'}, 'native_response_shape')
            exact(response['status'], status, 'native_status')
            exact(response['body_bytes'], length, 'native_body_length')
            exact(response['body_sha256'], digest, 'native_body_digest')
            require(type(response['body_hex']) is str and re.fullmatch('[0-9a-f]+', response['body_hex']), 'native_body_hex')
            exact(identity(bytes.fromhex(response['body_hex'])), {'bytes': length, 'sha256': digest}, 'complete_native_body')
            if backend == 'mariadb':
                exact(response, result['mysql']['observations'][case]['response'], 'native_cross_backend_response')
        exact(value['requests']['missing_repeat']['observation']['response'],
            value['observations']['missing']['response'], 'native_fresh_repeat_response')
        result[backend] = value
    exact(set(result), {'mysql', 'mariadb'}, 'two_native_backends')
    return result


def public_gate(value, references=None):
    responses, snapshots = [], []
    if references is not None:
        for native in references.values():
            for case in ('home', 'missing'):
                observation = native['observations'][case]
                responses.append(observation['response'])
                snapshots.extend(observation[k] for k in ('context_before_bootstrap',
                    'context_after_bootstrap', 'context_after_dispatch'))
    def inspect(v):
        if type(v) is dict:
            if 'body_hex' in v:
                require(any(canonical(v) == canonical(r) for r in responses), 'only_exact_synthetic_native_response_is_public')
                v = {k: item for k, item in v.items() if k not in ('body_hex', 'headers_hex')}
            require('headers_hex' not in v, 'headers_only_in_complete_known_response')
            for key in ('context_before_bootstrap', 'context_after_bootstrap', 'context_after_dispatch'):
                if key in v:
                    require(any(canonical(v[key]) == canonical(r) for r in snapshots), 'only_exact_native_public_snapshot')
            return {k: inspect(item) for k, item in v.items()}
        if type(v) is list: return [inspect(item) for item in v]
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
    require(job['name'] == 'Cranelift WP context / ' + backend and job['conclusion'] in old.CONCLUSIONS, 'api_job')
    require(type(job['steps']) is list and 1 <= len(job['steps']) <= 64, 'api_steps')
    for step in job['steps']:
        exact(set(step), {'name', 'conclusion'}, 'api_step_fields')
        require(type(step['name']) is str and 0 < len(step['name']) <= 160
            and step['conclusion'] in old.CONCLUSIONS, 'api_step')
    exact(set(artifact), {'id', 'name', 'size_in_bytes', 'digest'}, 'api_artifact_fields')
    old.integer(artifact['id'], 1); old.integer(artifact['size_in_bytes'], 1, 24 * 1024 * 1024)
    require(artifact['name'] == 'php-wasix-wp-request-context-' + backend + '-' + str(api['run_attempt'])
        and type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']), 'api_artifact')


def archive_gate(path, api, backend, source):
    wire = read(path, 24 * 1024 * 1024)
    exact(identity(wire), {'bytes': api['artifact']['size_in_bytes'],
        'sha256': api['artifact']['digest'][7:]}, 'api_archive_identity')
    reverse = {p: n for n, p in member_map(backend).items()}
    raw, members, expanded = {}, {}, 0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(1 <= len(entries) <= 10 and len({r.filename for r in entries}) == len(entries), 'zip_member_count')
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
    response = native_references(source)
    for value in data.values(): public_gate(value, response)
    return raw, data, members


def stages_gate(value, api, backend, source):
    exact(set(value), {'schema', 'source_commit', 'run_id', 'run_attempt', 'backend', 'stages'}, 'stage_fields')
    require(value['schema'] == 'recastpress.php-wasix-wp-request-context-ci-stages.v0.5'
        and value['source_commit'] == api['head_sha'] and value['run_id'] == str(api['run_id'])
        and value['run_attempt'] == str(api['run_attempt']) and value['backend'] == backend, 'stage_identity')
    names = {}
    for block in source.read('.github/workflows/php-wasix-wp-request-context.yml').decode().split('      - name: ')[1:]:
        match = re.search(r'^        id: ([a-z_]+)$', block, re.M)
        if match: names[match[1]] = block.splitlines()[0].strip()
    exact(set(names), {'php', 'deps', 'parser', 'packages', 'tools', 'module', 'guest', 'rust', 'bootstrap', 'theme', 'host', 'contract'},
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


def prepared_gate(value, previous, bootstrap, source):
    old_preparation = theme.prepared_gate(previous, bootstrap, source)
    name = PREFIX + 'prepare.py'; constants = source.constants(name)
    original = {n: source.read(n) for n in constants['PINS']}
    for n, wire in original.items(): exact(sha(wire), constants['PINS'][n], 'context_parent_whole_pin')
    pure = source.functions(name, ['once', 'section', 'expected_fixtures'], constants)
    fixtures = pure['expected_fixtures'](original)
    exact(set(fixtures), set(constants['FIXTURES']), 'seven_expected_fixtures')
    for n, wire in fixtures.items(): exact(source.read(PREFIX + 'fixtures/' + n), wire, 'context_whole_derived_fixture')
    native = 'experiments/wp-request-context-probe/fixtures/'
    public = pure['section'](original[native + 'context.php'], b'function rp_context_public(', b'function rp_context_verify(')
    dispatch = pure['section'](original[native + 'bootstrap.php'], b"    $rp_report['phase'] = 'wp_dispatch';", b"    $rp_report['phase'] = 'public_observations';")
    require(fixtures['context.php'].count(public) == fixtures['bootstrap.php'].count(dispatch) == 1,
        'unchanged_native_public_context_and_dispatch')
    for n in ('adapter.php', 'helpers.php', 'db.php'):
        exact(fixtures[n], source.read(THEME + 'fixtures/' + n), 'unchanged_old_' + n.replace('.', '_'))
    fields = theme.return_fields(source, name, 'derivation_gate')
    derivation = {'parent_sources': {n: identity(w) for n, w in original.items()},
        'derived_sources': {n: identity(w) for n, w in fixtures.items()},
        'native_public_context_block': identity(public), 'native_dispatch_render_capture_block': identity(dispatch),
        'native_public_reset_and_snapshots_bytes_unchanged': True, 'native_dispatch_render_capture_bytes_unchanged': True,
        'old_guest_mount_preserved': True, 'adapter_helpers_and_db_bytes_unchanged': True,
        'allowed_changes': ast.literal_eval(fields['allowed_changes']),
        'original_core_files_modified': False, 'original_git_objects_verified_by_preparer': False}
    exact(value['derivation'], derivation, 'context_complete_derivation')
    names = set(previous['input_files']) | set(constants['PINS']) | {p['path'] for p in constants['ORACLES'].values()}
    names |= {name, *[PREFIX + 'fixtures/' + n for n in constants['FIXTURES']]}
    exact(len(names), 55, 'fifty_five_preparation_inputs')
    files = [{'source': PREFIX + 'fixtures/' + n,
        'guest': '/wordpress/wp-content/db.php' if n == 'db.php' else constants['GUEST_BASE'] + n,
        **identity(fixtures[n]), 'guest_read_only': True,
        'kind': 'owned_wpdb_dropin' if n == 'db.php' else 'derived_fixture'} for n in constants['FIXTURES']]
    expected = {'schema': 'recastpress.php-wasix-wp-request-context-prepared.v0.5',
        'outcome': 'full_core_and_seven_context_fixtures_prepared', 'input_files': source.hashes(names),
        'derivation': derivation, 'files': files, 'native_oracles': constants['ORACLES'],
        **{k: previous[k] for k in ('core_site', 'core_manifest', 'core_inventory', 'core_archive')},
        'guest_fixture_mount': constants['GUEST_BASE'].rstrip('/'), 'core_guest_root': '/wordpress',
        'private_context_guest_path': '/rp-request/context.json', 'core_manifest_guest_path': '/rp-request/source-manifest.json',
        'guest_context_keys': ['schema', 'mode', 'case', 'nonce', 'salts'], 'guest_context_contains_db_credentials': False,
        'html_limit_bytes': 1048576, 'wrapper_limit_bytes': 3145728,
        **dict.fromkeys(['php_executed', 'wasm_executed', 'database_connected', 'network_calls_made',
            'native_reference_binaries_reverified', 'guest_readonly_enforcement_executed', 'original_core_files_modified',
            'product_runtime_selected'], False), 'inputs_unchanged': True}
    exact(value, expected, 'complete_context_preparation')
    native_references(source)
    return {'preparation_inputs': 55, 'derived_files': 7, 'old_theme': old_preparation,
        'native_public_context_and_dispatch_whole_bytes_verified': True,
        'source_archive_binaries_reverified_offline': False}


def frozen_checker(source):
    previous = theme.frozen_checker(source)
    name = PREFIX + 'run_check.py'; constants = source.constants(name)
    exact(constants['FROZEN_THEME_CONTROLLER'], sha(source.read(THEME + 'run_check.py')), 'fixed_theme_controller_pin')
    exact(constants['CONTEXT_FIELDS'], ['case', 'is_home', 'is_404', 'transport_body',
        'context_before_bootstrap', 'context_after_bootstrap', 'context_after_dispatch'], 'seven_context_fields')
    assignments = [n for n in source.tree(name).body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'COMPARE' for t in n.targets)]
    require(len(assignments) == 1 and ast.unparse(assignments[0].value) == 'theme.COMPARE + CONTEXT_FIELDS', 'context_compare_derivation')
    text = source.read(THEME + 'run_check.py').decode()
    node = next(n for n in source.tree(THEME + 'run_check.py').body if isinstance(n, ast.FunctionDef) and n.name == 'validate_home')
    original = ast.get_source_segment(text, node); derived = original
    changes = [('recastpress.php-wasix-wp-request-observation.v0.5', 'recastpress.php-wasix-wp-request-context-observation.v0.5'),
        ('fixed_wasix_wp_request_passed', 'fixed_wasix_wp_request_context_passed'), ('full_home_verified', 'full_context_verified')]
    for before, after in changes:
        require(derived.count(before) == 1, 'three_literal_validator_derivation'); derived = derived.replace(before, after, 1)
    namespace = dict(previous)
    exec(compile(derived, '<frozen-context-validator>', 'exec'), namespace)
    derivation = {'original_function_sha256': sha(original.encode()), 'derived_function_sha256': sha(derived.encode()),
        'changed_literals': 3, 'original_semantic_response_rpc_and_cleanup_checks_retained': True}
    def response_validator(): return namespace['validate_home'], derivation
    return source.functions(name, ['canonical', 'validate_context'], {
        'CONTEXT_FIELDS': constants['CONTEXT_FIELDS'], 'COMPARE': previous['COMPARE'] + constants['CONTEXT_FIELDS'],
        'response_validator': response_validator})


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


def host_build_gate(host, source):
    # Reuse every old host gate condition, deriving only the owned identity.
    text = source.read(SHARED).decode()
    node = next(n for n in source.tree(SHARED).body if isinstance(n, ast.FunctionDef) and n.name == 'host_build_gate')
    function = ast.get_source_segment(text, node)
    changes = [
        ("prefix = PREFIX if home else BOOT", "prefix = PREFIX"),
        ("'recastpress.php-wasix-wp-' + ('request' if home else 'bootstrap') + '-build.v0.5'", "'recastpress.php-wasix-wp-request-context-build.v0.5'"),
        ("('locked_offline_home_host_build_passed' if home else 'locked_offline_bootstrap_host_build_passed')", "'locked_offline_context_host_build_passed'"),
    ]
    for before, after in changes:
        require(function.count(before) == 1, 'context_build_gate_derivation'); function = function.replace(before, after, 1)
    namespace = dict(theme.__dict__, PREFIX=PREFIX)
    exec(compile(function, '<frozen-context-build-gate>', 'exec'), namespace)
    anchor = namespace['host_build_gate'](host, source, True)
    exact(len(anchor['input_files']), 195, 'context_host_195_inputs')
    exact(len(host['units']['tests']), 58, 'context_host_58_units')
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
    run, host, boot, oldhost = (data[n] for n in ('run.json', 'host-build.json', 'bootstrap-build.json', 'theme-build.json'))
    require(run['schema'] == 'recastpress.php-wasix-wp-request-context-check.v0.5' and run['passed'] is True
        and run['outcome'] == 'fixed_wasix_public_request_context_passed' and run['platform'] == 'linux'
        and run['backend'] == backend and run['source_reverified_after'] is True
        and run['owned_cleanup_completed'] is True, 'positive_context_run')
    anchor = host_build_gate(host, source)
    theme.host_build_gate(boot, source, False); theme.host_build_gate(oldhost, source, True)
    for key, value in [('build_receipt', host), ('bootstrap_build_receipt', boot), ('theme_build_receipt', oldhost)]:
        exact(run[key], value, 'same_job_' + key)
    nested = {}
    for label, prefix, receipt, member in [('bootstrap', BOOT, boot, 'bootstrap-build.json'), ('theme', THEME, oldhost, 'theme-build.json')]:
        nested[label] = {'receipt_path': prefix + 'evidence/linux-build.json', 'receipt_identity': identity(raw[member]),
            'executable': receipt['executable'], 'executable_identity': receipt['executable_identity'],
            'unit_executable': receipt['units']['executable'], 'unit_executable_identity': receipt['units']['executable_identity'],
            'source_inputs': len(receipt['input_files']), 'units': len(receipt['units']['tests']), 'original_build_fully_reverified': True}
    exact(host['old_build_before'], nested, 'same_job_two_previous_builds_before')
    exact(host['old_build_after'], nested, 'same_job_two_previous_builds_after')
    exact(oldhost['old_build_before'], nested['bootstrap'], 'same_job_theme_bootstrap_before')
    exact(oldhost['old_build_after'], nested['bootstrap'], 'same_job_theme_bootstrap_after')
    preparation = prepared_gate(data['prepared.json'], data['theme-prepared.json'], data['bootstrap-prepared.json'], source)
    expected_sources = {'input_files': anchor['input_files'], 'prepared': data['prepared.json'],
        'bootstrap_prepared': data['bootstrap-prepared.json'], 'theme_prepared': data['theme-prepared.json']}
    exact(run['sources_before'], expected_sources, 'complete_context_sources_before')
    exact(run['sources_after'], expected_sources, 'complete_context_sources_after')
    exact(run['module_provenance'], run['module_provenance_after'], 'module_unchanged')
    managed = old.managed_gate(data, raw, source, run['module_provenance'])
    previous, context = base.frozen_checker(source), frozen_checker(source)
    native_boot, native = previous['reference'](backend), native_references(source)[backend]
    exact(run['native_reference'], {'path': 'experiments/wp-request-context-probe/evidence/windows-' + backend + '-native.json',
        'sha256': source.constants(PREFIX + 'linux_database.py')['ORACLES'][backend]}, 'same_pinned_native_reference')
    for prefix in (PREFIX, THEME, BOOT):
        lock = tomllib.loads(source.read(prefix + 'Cargo.lock').decode())
        crates = [p for p in lock['package'] if p['name'] == 'serde_json']
        require(len(crates) == 1 and not any(d.startswith('indexmap') for d in crates[0].get('dependencies', [])),
            'default_sorted_json_map_required')
    labels = ('install', 'read', 'home', 'missing', 'missing_repeat')
    for key in ('observations', 'validations', 'executions'): exact(set(run[key]), set(labels), 'five_fresh_' + key)
    for label in labels:
        value = run['observations'][label]; request = label not in ('install', 'read')
        case = 'missing' if label == 'missing_repeat' else label
        expected = context['validate_context'](value, case, native['requests'][label]['observation']) if request else previous['validate'](value, label, native_boot[label])
        exact(run['validations'][label], expected, 'source_bound_complete_native_comparison')
        operation_types(value, 4096 if request else previous['source_operation_limit'])
        execution = run['executions'][label]
        exact(set(execution), {'owned_pid', 'owned_reaped', 'exit_code', 'stdout_bytes', 'stderr_bytes', 'stdout_sha256', 'stderr_sha256'}, 'execution_shape')
        require(execution['owned_reaped'] is True, 'owned_guest_reaped'); old.integer(execution['owned_pid'], 1)
        exact(execution['exit_code'], 0, 'owned_guest_exit'); exact(execution['stderr_bytes'], 0, 'clean_guest_stderr')
        exact(execution['stderr_sha256'], sha(b''), 'empty_guest_stderr_identity')
        exact({'bytes': execution['stdout_bytes'], 'sha256': execution['stdout_sha256']}, identity(canonical(value) + b'\n'), 'public_host_stdout_identity')
        php = value['php_result']
        require(php['schema'] == ('recastpress.php-wasix-wp-request-context-php-observation.v0.5' if request else 'recastpress.php-wasix-wp-bootstrap-php-observation.v0.5')
            and php['outcome'] == ('fixed_wasix_context_passed' if request else 'fixed_wasix_bootstrap_passed'), 'php_profile_identity')
        php_diagnostic(php, source, request)
        module = data['build.json']['php_wasm']
        exact(value['module'], {'path': module['path'], 'bytes': module['bytes'], 'sha256': module['sha256'], 'verified': True}, 'same_job_guest_module')
        exact(value['build_receipt'], {'path': old.MANAGED + 'evidence/build.json', 'sha256': sha(raw['build.json']),
            'verified': True, 'full_chain_verified_by_host': False}, 'same_job_guest_build_receipt')
    require(run['fresh_repeat_404_response_exact'] is True, 'fresh_404_repeated')
    exact(run['observations']['missing']['php_result']['response'], run['observations']['missing_repeat']['php_result']['response'], 'complete_repeat_404_equal')
    exact(run['request_module_compilations'], 3, 'three_separately_compiled_request_hosts')
    for key in ['container_started', 'container_stopped', 'container_removed', 'container_normal_exit_verified',
        'owned_database_identity_verified_before_fixture_ddl', 'independent_wordpress_final_verified',
        'installer_schema_ddl_and_crud', 'reader_schema_crud']:
        require(run[key] is True, 'normal_container_' + key)
    exact(run['wordpress_table_count'], 12, 'twelve_original_wordpress_tables')
    for key in ['forced_owned_container_cleanup', 'normal_container_stop_failed', 'production_database_used',
        'management_credentials_supplied_to_php', 'response_normalized', 'product_runtime_selected',
        'http_listener_exercised', 'standard_wpdb_compatibility_verified', 'native_php_executed', 'native_php_fallback',
        'module_shared_across_owned_host_processes', 'memory_cpu_limits_verified_for_this_integration', 'all_resources_immediately_reclaimed']:
        require(run.get(key, False) is False, 'unclaimed_or_failure_scope_' + key)
    require(type(run['commands']) is list and len(run['commands']) > 0, 'owned_database_commands')
    for command in run['commands']: require(command['owned_reaped'] is True, 'owned_database_command_reaped')
    stamp = source.constants(THEME + 'linux_database.py')['STAMP']; seed = run['fixed_public_date_seed']
    require(seed['timestamp'] == stamp and seed['actual_database_updated'] is True
        and seed['response_body_normalized'] is False, 'fixed_public_date_seed')
    exact(seed['sql_sha256'], native['fixed_public_date_seed']['sql_sha256'], 'same_original_date_seed_sql')
    exact(run['public_date_verification'], {phase: {'post_ids': [1, 2], 'comment_ids': [1],
        'timestamp': stamp, 'verified': True} for phase in ('before_home', 'after_home')},
        'independent_exact_public_dates')
    exact(set(run['independent_context_checks']), {'before_requests', 'home', 'missing', 'missing_repeat'}, 'independent_four_context_checks')
    for label, check in run['independent_context_checks'].items():
        expected = {'missing_post_count': 0, 'original_public_state_verified': True,
            'fixed_public_dates_verified': True, 'autoload_option_count': check['autoload_option_count'],
            'autoload_max_value_bytes': check['autoload_max_value_bytes']}
        exact(check, expected, 'independent_public_state_check')
        old.integer(check['autoload_option_count'], 1, 2**31); old.integer(check['autoload_max_value_bytes'], 1, 2**31)
        php = run['observations']['read' if label == 'before_requests' else label]['php_result']
        for k in ('autoload_option_count', 'autoload_max_value_bytes'): exact(check[k], php[k], 'independent_php_' + k)
    pin = parse_json(source.read('experiments/php-wasix-wpdb-probe/container-images.json'))['images'][backend]
    exact(run['image'], pin['repository'] + '@' + pin['platform_digest'], 'fixed_database_image')
    exact(run['image_config_digest'], pin['image_config_digest'], 'database_config_digest')
    # The actual platform string is retained exactly, with no suffix normalization.
    version = '8.4.13' if backend == 'mysql' else '11.8.9-MariaDB-ubu2404'
    exact(run['sql_server_version'], version, 'exact_linux_sql_version')
    return {'all_five_guest_executions_verified': True, 'prepared': preparation, 'managed': managed,
        'runtime_source_inputs': len(anchor['input_files']), 'context_host_units': len(host['units']['tests']),
        'bootstrap_source_inputs': len(boot['input_files']), 'theme_source_inputs': len(oldhost['input_files']),
        'bootstrap_host_units': len(boot['units']['tests']), 'theme_host_units': len(oldhost['units']['tests']),
        'complete_native_home_and_404_responses_equal': True, 'all_three_context_snapshots_exact': True,
        'fresh_repeat_404_exact': True, 'public_host_json_reserialization_verified': True,
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
    work = ROOT / 'target/php-wasix-wp-request-context-probe/ci'
    api_wire = read(work / ('api-' + args.run + '-' + args.backend + '.json'), 128 * 1024)
    api = parse_json(api_wire); api_gate(api, args.run, args.backend); old.privacy_gate(api)
    source = old.GitSource(api['head_sha'])
    for name in [SHARED, theme.SHARED, base.LEGACY]:
        exact(source.read(name), read(ROOT / name, 512 * 1024), 'frozen_shared_verifier_helpers_changed')
    raw, data, members = archive_gate(work / ('run-' + args.run + '-' + args.backend + '.zip'), api, args.backend, source)
    stages = stages_gate(data['ci-stages.json'], api, args.backend, source)
    positive = api['run_conclusion'] == api['job']['conclusion'] == 'success' and stages
    if positive: exact(set(raw), set(member_map(args.backend)), 'ten_positive_public_records')
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
            require(mode in ('install', 'read', 'home', 'missing', 'missing_repeat'), 'closed_failure_mode')
            if type(value) is not dict: continue
            public = {k: value[k] for k in ['passed', 'phase', 'failure', 'failure_reason', 'php_diagnostic'] if k in value}
            php = value.get('php_result')
            if type(php) is not dict: php = value.get('cgi_capture', {}).get('safe_php_observation')
            public['php'] = php_diagnostic(php, source, mode in ('home', 'missing', 'missing_repeat'))
            summary['public_guest_failures'][mode] = public
    proof = {'schema': 'recastpress.php-wasix-wp-request-context-ci-import.v0.5', 'repository': old.REPOSITORY,
        'repository_id': old.REPOSITORY_ID, 'run_id': api['run_id'], 'run_attempt': api['run_attempt'],
        'source_commit': source.commit, 'backend': args.backend, 'run_conclusion': api['run_conclusion'],
        'job': api['job'], 'artifact': api['artifact'], 'api_metadata_sha256': sha(api_wire),
        'archive_member_map': members, 'raw_json': {n: identity(b) for n, b in raw.items()},
        'original_git_sources': {n: identity(b) for n, b in sorted(source.cache.items())},
        'verifier_input_files': {p.relative_to(ROOT).as_posix(): sha(read(p, 512 * 1024))
            for p in [Path(__file__).resolve(), ROOT / SHARED, ROOT / theme.SHARED, ROOT / base.LEGACY]},
        'verification': summary, 'offline_nonce_recomputed': False, 'private_source_bytes_reconstructed': False,
        'source_tool_module_archive_binaries_reverified': False, 'network_used_by_importer': False,
        'php_wasm_or_database_executed_by_importer': False, 'product_runtime_selected': False,
        'privacy_exception_scope': 'exact public CI clang workspace and byte-identical pinned synthetic native home and missing-post responses HTML only; raw bytes unchanged'}
    public_gate(proof)
    wire = (json.dumps(proof, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    require(len(wire) <= 2 * 1024 * 1024, 'import_proof_quota')
    if not args.verify_only:
        destination = HERE / ('evidence/ci-' + args.run + '-' + args.backend)
        for name, content in raw.items(): immutable(destination / 'raw' / members[name], content)
        immutable(destination / 'api-metadata.json', api_wire); immutable(destination / 'provenance.json', wire)
    print('WP context CI evidence: ' + ('verified positive' if summary['overall_positive'] else 'preserved failure/partial')
        + ' (' + args.backend + ')')
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('WP context import rejected: ' + reason, file=sys.stderr); sys.exit(1)
