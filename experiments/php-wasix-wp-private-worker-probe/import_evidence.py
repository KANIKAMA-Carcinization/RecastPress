"""Import twelve public CI JSON records without executing PHP, Wasm, DB or network.

Only a complete response identical to the pinned synthetic native home and missing-post responses may
contain public HTML bytes. Private SQL, results, CGI and nonce bytes remain
unavailable: their digests are source-bound CI observations, not recomputed.
"""
import argparse
import copy
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
PREFIX = 'experiments/php-wasix-wp-private-worker-probe/'
CONTEXT = 'experiments/php-wasix-wp-request-context-probe/'
HTTP = 'experiments/php-wasix-wp-http-probe/'
THEME = 'experiments/php-wasix-wp-request-probe/'
BOOT = 'experiments/php-wasix-wp-bootstrap-probe/'
SHARED = HTTP + 'import_evidence.py'
spec = importlib.util.spec_from_file_location('rp_home_offline_bootstrap_verifier', ROOT / SHARED)
http = importlib.util.module_from_spec(spec)
spec.loader.exec_module(http)
context = http.context
theme = context.theme
base = context.base
old = base.old
require, sha, read, identity, exact, canonical, parse_json = (
    base.require, base.sha, base.read, base.identity, base.exact, base.canonical, base.parse_json)


def member_map(backend):
    return {'run.json': PREFIX + 'evidence/linux-' + backend + '.json',
        'host-build.json': PREFIX + 'evidence/linux-build.json',
        'http-build.json': HTTP + 'evidence/linux-build.json',
        'context-build.json': CONTEXT + 'evidence/linux-build.json',
        'theme-build.json': THEME + 'evidence/linux-build.json',
        'bootstrap-build.json': BOOT + 'evidence/linux-build.json',
        'ci-stages.json': PREFIX + 'evidence/ci-stages.json',
        'build.json': old.MANAGED + 'evidence/build.json',
        'toolchain.json': old.CGI + 'evidence/toolchain.json',
        'prepared.json': 'target/php-wasix-wp-request-context-probe/prepared.json',
        'theme-prepared.json': 'target/php-wasix-wp-request-probe/prepared.json',
        'bootstrap-prepared.json': 'target/php-wasix-wp-bootstrap-probe/prepared.json'}


native_references = context.native_references
php_diagnostic = context.php_diagnostic
operation_types = context.operation_types


def public_gate(value, references=None):
    approved, responses = [], []
    if references is not None:
        for native in references.values():
            for case in ('home', 'missing'):
                observation = native['observations'][case]
                responses.append(observation['response'])
                approved.append({key: observation[key] for key in ('case', 'is_home', 'is_404', 'transport_body',
                    'context_before_bootstrap', 'context_after_bootstrap', 'context_after_dispatch')})
    def inspect(v):
        if type(v) is dict:
            if 'private_worker_diagnostics' in v:
                diagnostics = v['private_worker_diagnostics']
                require(type(diagnostics) is dict and set(diagnostics) <= {'home', 'missing', 'missing_repeat'},
                    'closed_private_diagnostic_cases')
                for item in diagnostics.values():
                    require(type(item) is dict and {'passed', 'private_response_received', 'private_contexts_received'} <= set(item)
                        and set(item) <= {'passed', 'private_response_received', 'private_contexts_received', 'phase', 'failure_reason'},
                        'closed_private_diagnostic_fields')
                    require(all(type(item[k]) is bool for k in ('passed', 'private_response_received', 'private_contexts_received')),
                        'typed_private_diagnostic_flags')
                    if 'phase' in item:
                        require(item['phase'] in ('input', 'module_compile', 'cgi_execution', 'full_private_response_verified'),
                            'closed_private_diagnostic_phase')
                    if 'failure_reason' in item:
                        require(type(item['failure_reason']) is str and re.fullmatch('[a-z_]{1,80}', item['failure_reason']),
                            'fixed_private_diagnostic_code_shape')
            if 'private_response' in v:
                require(any(canonical(v['private_response']) == canonical(expected) for expected in responses),
                    'only_complete_native_approved_private_response_public')
            if 'private_contexts' in v:
                require(any(canonical(v['private_contexts']) == canonical(expected) for expected in approved),
                    'only_complete_native_approved_private_contexts_public')
            require(not any(key in v for key in ('stdout_hex', 'stderr_hex', 'raw_private_packet', 'private_packet_hex')),
                'private_worker_raw_capture_in_public_record')
            return {key: inspect(item) for key, item in v.items()}
        if type(v) is list: return [inspect(item) for item in v]
        return v
    context.public_gate(inspect(value), references)


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
    require(job['name'] == 'Cranelift private WP worker / ' + backend and job['conclusion'] in old.CONCLUSIONS, 'api_job')
    require(type(job['steps']) is list and 1 <= len(job['steps']) <= 64, 'api_steps')
    for step in job['steps']:
        exact(set(step), {'name', 'conclusion'}, 'api_step_fields')
        require(type(step['name']) is str and 0 < len(step['name']) <= 160
            and step['conclusion'] in old.CONCLUSIONS, 'api_step')
    exact(set(artifact), {'id', 'name', 'size_in_bytes', 'digest'}, 'api_artifact_fields')
    old.integer(artifact['id'], 1); old.integer(artifact['size_in_bytes'], 1, 24 * 1024 * 1024)
    require(artifact['name'] == 'php-wasix-wp-private-worker-' + backend + '-' + str(api['run_attempt'])
        and type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']), 'api_artifact')


def archive_gate(path, api, backend, source):
    wire = read(path, 24 * 1024 * 1024)
    exact(identity(wire), {'bytes': api['artifact']['size_in_bytes'],
        'sha256': api['artifact']['digest'][7:]}, 'api_archive_identity')
    reverse = {p: n for n, p in member_map(backend).items()}
    raw, members, expanded = {}, {}, 0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(1 <= len(entries) <= 12 and len({r.filename for r in entries}) == len(entries), 'zip_member_count')
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
    require(value['schema'] == 'recastpress.php-wasix-wp-private-worker-ci-stages.v0.5'
        and value['source_commit'] == api['head_sha'] and value['run_id'] == str(api['run_id'])
        and value['run_attempt'] == str(api['run_attempt']) and value['backend'] == backend, 'stage_identity')
    names = {}
    for block in source.read('.github/workflows/php-wasix-wp-private-worker.yml').decode().split('    - name: ')[1:]:
        match = re.search(r'^      id: ([a-z_]+)$', block, re.M)
        if match: names[match[1]] = block.splitlines()[0].strip()
    exact(set(names), {'php', 'deps', 'parser', 'packages', 'tools', 'module', 'guest', 'rust', 'bootstrap', 'theme', 'context', 'http', 'host', 'contract'},
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


class SourcePath:
    """Only Git blob reads for the frozen pure source/lock/controller gates."""
    def __init__(self, source, name='.'):
        self.source, self.name = source, str(name)
    def __truediv__(self, name):
        from pathlib import PurePosixPath
        return SourcePath(self.source, PurePosixPath(self.name) / name)
    def read_bytes(self): return self.source.read(self.name)
    def read_text(self): return self.read_bytes().decode('utf-8')
    def relative_to(self, other):
        from pathlib import PurePosixPath
        return PurePosixPath(self.name).relative_to(other.name)
    def exists(self): return self.name in self.source.names(PREFIX)


def derivation_gate(source):
    name = PREFIX + 'derivation_gate.py'
    root = SourcePath(source)
    namespace = source.functions(name, ['inputs', 'dependency_gate', 'verify'], {
        'HERE': root / PREFIX.rstrip('/'), 'ROOT': root, 'CONTEXT': root / CONTEXT.rstrip('/'),
        'SHARED': root / THEME.rstrip('/'), 'CODEC': root / 'experiments/wp-private-response-probe',
        'REUSED': source.constants(name)['REUSED'], 'tomllib': tomllib})
    result = namespace['verify']()
    exact(result['dependencies']['packages'], 446, 'private_worker_exact_446_packages')
    return result


def host_build_gate(host, source):
    name = context.SHARED
    text = source.read(name).decode()
    node = next(n for n in source.tree(name).body if isinstance(n, ast.FunctionDef) and n.name == 'host_build_gate')
    function = ast.get_source_segment(text, node)
    changes = [
        ("prefix = PREFIX if home else BOOT", "prefix = PREFIX"),
        ("'recastpress.php-wasix-wp-' + ('request' if home else 'bootstrap') + '-build.v0.5'", "'recastpress.php-wasix-wp-private-worker-build.v0.5'"),
        ("('locked_offline_home_host_build_passed' if home else 'locked_offline_bootstrap_host_build_passed')", "'locked_offline_private_worker_build_passed'"),
    ]
    for before, after in changes:
        require(function.count(before) == 1, 'private_build_gate_derivation'); function = function.replace(before, after, 1)
    namespace = dict(theme.__dict__, PREFIX=PREFIX)
    exec(compile(function, '<frozen-private-worker-build-gate>', 'exec'), namespace)
    anchor = namespace['host_build_gate'](host, source, True)
    exact(len(anchor['input_files']), 219, 'private_worker_219_inputs')
    exact(len(host['units']['tests']), 59, 'private_worker_59_units')
    exact(host['derivation_before'], derivation_gate(source), 'source_bound_private_worker_derivation')
    return anchor


def frozen_checker(source):
    previous = context.frozen_checker(source)
    references = native_references(source)
    constants = source.constants(PREFIX + 'run_check.py')
    namespace = source.functions(PREFIX + 'run_check.py', ['strict_json', 'validate_private', 'public_projection', 'publish_verified', 'self_test'], {
        'copy': copy, 'canonical': canonical, 'SCHEMA': constants['SCHEMA'], 'FIELDS': previous['CONTEXT_FIELDS'],
        'ROOT': SourcePath(source), 'context': SimpleNamespace(validate_context=previous['validate_context'],
            COMPARE=previous['COMPARE'],
            linux=SimpleNamespace(reference=lambda backend: references[backend]))})
    return namespace


def positive_gate(data, raw, source, backend):
    run, host, boot, theme_build, context_build, http_build = (data[n] for n in
        ('run.json', 'host-build.json', 'bootstrap-build.json', 'theme-build.json', 'context-build.json', 'http-build.json'))
    require(run['schema'] == 'recastpress.php-wasix-wp-private-worker-check.v0.5' and run['passed'] is True
        and run['outcome'] == 'fixed_private_worker_comparison_passed' and run['platform'] == 'linux'
        and run['backend'] == backend and run['source_reverified_after'] is True
        and run['owned_cleanup_completed'] is True, 'positive_private_worker_run')
    anchor = host_build_gate(host, source)
    theme.host_build_gate(boot, source, False); theme.host_build_gate(theme_build, source, True)
    context.host_build_gate(context_build, source); http.host_build_gate(http_build, source)
    exact(run['build_receipt'], host, 'same_job_private_worker_build')
    exact(run['bootstrap_build_receipt'], boot, 'same_job_bootstrap_build')
    nested = {}
    for label, prefix, receipt, member in [('bootstrap', BOOT, boot, 'bootstrap-build.json'),
        ('theme', THEME, theme_build, 'theme-build.json'), ('context', CONTEXT, context_build, 'context-build.json'),
        ('http', HTTP, http_build, 'http-build.json')]:
        nested[label] = {'receipt_path': prefix + 'evidence/linux-build.json', 'receipt_identity': identity(raw[member]),
            'executable': receipt['executable'], 'executable_identity': receipt['executable_identity'],
            'unit_executable': receipt['units']['executable'], 'unit_executable_identity': receipt['units']['executable_identity'],
            'source_inputs': len(receipt['input_files']), 'units': len(receipt['units']['tests']), 'original_build_fully_reverified': True}
    for suffix in ('before', 'after'):
        exact(host['old_build_' + suffix], nested, 'same_job_four_original_builds_' + suffix)
        exact(http_build['old_build_' + suffix], {k: nested[k] for k in ('bootstrap', 'theme', 'context')}, 'same_job_http_old_builds_' + suffix)
        exact(context_build['old_build_' + suffix], {k: nested[k] for k in ('bootstrap', 'theme')}, 'same_job_context_old_builds_' + suffix)
        exact(theme_build['old_build_' + suffix], nested['bootstrap'], 'same_job_theme_old_build_' + suffix)
    preparation = context.prepared_gate(data['prepared.json'], data['theme-prepared.json'], data['bootstrap-prepared.json'], source)
    expected_sources = {'input_files': anchor['input_files'], 'accepted_context': {
        'input_files': context_build['input_files'], 'prepared': data['prepared.json'],
        'bootstrap_prepared': data['bootstrap-prepared.json'], 'theme_prepared': data['theme-prepared.json']}}
    exact(run['sources_before'], expected_sources, 'complete_private_sources_before')
    exact(run['sources_after'], expected_sources, 'complete_private_sources_after')
    exact(run['module_provenance'], run['module_provenance_after'], 'module_unchanged')
    managed = old.managed_gate(data, raw, source, run['module_provenance'])
    previous, private = base.frozen_checker(source), frozen_checker(source)
    exact(run['publication_gate_self_test'], private['self_test'](), 'source_bound_publication_checks')
    native_boot, native = previous['reference'](backend), native_references(source)[backend]
    exact(run['native_reference'], {'path': 'experiments/wp-request-context-probe/evidence/windows-' + backend + '-native.json',
        'sha256': source.constants(CONTEXT + 'linux_database.py')['ORACLES'][backend]}, 'same_pinned_native_reference')
    for prefix in (PREFIX, CONTEXT, HTTP, THEME, BOOT):
        lock = tomllib.loads(source.read(prefix + 'Cargo.lock').decode())
        crates = [p for p in lock['package'] if p['name'] == 'serde_json']
        require(len(crates) == 1 and not any(d.startswith('indexmap') for d in crates[0].get('dependencies', [])),
            'default_sorted_json_map_required')
    labels = ('install', 'read', 'home', 'missing', 'missing_repeat')
    for key in ('observations', 'validations', 'executions'): exact(set(run[key]), set(labels), 'five_fresh_' + key)
    exact(set(run['private_worker_diagnostics']), {'home', 'missing', 'missing_repeat'}, 'three_private_diagnostics')
    for label in labels:
        value = run['observations'][label]; request = label not in ('install', 'read')
        case = 'missing' if label == 'missing_repeat' else label
        expected = private['validate_private'](value, case, native['requests'][label]['observation']) if request else previous['validate'](value, label, native_boot[label])
        exact(run['validations'][label], expected, 'source_bound_original_worker_comparison')
        operation_types(value, 4096 if request else previous['source_operation_limit'])
        execution = run['executions'][label]
        exact(set(execution), {'owned_pid', 'owned_reaped', 'exit_code', 'stdout_bytes', 'stderr_bytes', 'stdout_sha256', 'stderr_sha256'}, 'execution_shape')
        require(execution['owned_reaped'] is True, 'owned_guest_reaped'); old.integer(execution['owned_pid'], 1)
        exact(execution['exit_code'], 0, 'owned_guest_exit'); exact(execution['stderr_bytes'], 0, 'clean_guest_stderr')
        exact(execution['stderr_sha256'], sha(b''), 'empty_guest_stderr_identity')
        if request:
            # The original private IPC packet is not a public artifact. Its
            # receipt is source-bound; the verified projection cannot recreate
            # the original packet's byte count or digest.
            old.integer(execution['stdout_bytes'], 1, 4 * 1024 * 1024)
            old.digest(execution['stdout_sha256'])
        else:
            exact({'bytes': execution['stdout_bytes'], 'sha256': execution['stdout_sha256']},
                identity(canonical(value) + b'\n'), 'bootstrap_public_stdout_identity')
        if request:
            require(value['worker_fields_projected'] is True and value['original_worker_json_published'] is False,
                'only_verified_worker_projection_published')
            exact(private['public_projection'](value), value, 'idempotent_source_bound_worker_projection')
            require(not any(key in value['php_result'] for key in
                ['response', 'private_response', 'private_contexts', *private['FIELDS']]),
                'private_payload_absent_from_safe_php_diagnostics')
            exact(value['prepared'], {'core_files': 3782, 'owned_dropin_files': 1, 'source_verified': True,
                'transitive_inputs_verified_by_worker': False, 'verification_scope': 'compiled_fixtures_and_core_only'}, 'worker_limited_prepared_scope')
            exact(run['private_worker_diagnostics'][label], {'passed': True, 'private_response_received': True,
                'private_contexts_received': True, 'phase': 'full_private_response_verified'}, 'closed_private_success_diagnostic')
        php = value['php_result']
        require(php['schema'] == ('recastpress.php-wasix-wp-request-context-php-observation.v0.5' if request else 'recastpress.php-wasix-wp-bootstrap-php-observation.v0.5')
            and php['outcome'] == ('fixed_wasix_context_passed' if request else 'fixed_wasix_bootstrap_passed'), 'unchanged_php_fixture_profile')
        php_diagnostic(php, source, request)
        if not request:
            module = data['build.json']['php_wasm']
            exact(value['module'], {'path': module['path'], 'bytes': module['bytes'], 'sha256': module['sha256'], 'verified': True}, 'same_job_bootstrap_module')
            exact(value['build_receipt'], {'path': old.MANAGED + 'evidence/build.json', 'sha256': sha(raw['build.json']),
                'verified': True, 'full_chain_verified_by_host': False}, 'same_job_bootstrap_build_receipt')
    require(run['native_comparison_in_controller_only'] is True and run['unknown_response_publication_authorized'] is False,
        'controller_only_native_publication_authority')
    require(run['fresh_repeat_404_response_exact'] is True, 'fresh_private_404_repeated')
    exact(run['observations']['missing']['private_response'], run['observations']['missing_repeat']['private_response'], 'complete_private_repeat_404_equal')
    exact(run['request_module_compilations'], 3, 'three_separately_compiled_private_hosts')
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
        'runtime_source_inputs': len(anchor['input_files']), 'private_worker_units': len(host['units']['tests']),
        'private_worker_stdout_attestation_shape_verified': True,
        'original_private_worker_stdout_reconstructed_offline': False,
        'worker_schema_and_verdict_relabelled': False,
        'worker_fields_projected': True, 'original_worker_json_published': False,
        'native_response_comparison_by_worker': False, 'native_comparison_in_controller_only': True,
        'worker_prepared_transitive_inputs_reverified': False, 'worker_runtime_source_scope': 'compiled_fixtures_and_core_only',
        'complete_native_response_and_contexts_equal': True, 'fresh_repeat_404_exact': True,
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
    work = ROOT / 'target/php-wasix-wp-private-worker-probe/ci'
    api_wire = read(work / ('api-' + args.run + '-' + args.backend + '.json'), 128 * 1024)
    api = parse_json(api_wire); api_gate(api, args.run, args.backend); old.privacy_gate(api)
    source = old.GitSource(api['head_sha'])
    for name in [SHARED, http.SHARED, context.SHARED, theme.SHARED, base.LEGACY]:
        exact(source.read(name), read(ROOT / name, 512 * 1024), 'frozen_shared_verifier_helpers_changed')
    raw, data, members = archive_gate(work / ('run-' + args.run + '-' + args.backend + '.zip'), api, args.backend, source)
    stages = stages_gate(data['ci-stages.json'], api, args.backend, source)
    positive = api['run_conclusion'] == api['job']['conclusion'] == 'success' and stages
    if positive: exact(set(raw), set(member_map(args.backend)), 'twelve_positive_public_records')
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
    proof = {'schema': 'recastpress.php-wasix-wp-private-worker-ci-import.v0.5', 'repository': old.REPOSITORY,
        'repository_id': old.REPOSITORY_ID, 'run_id': api['run_id'], 'run_attempt': api['run_attempt'],
        'source_commit': source.commit, 'backend': args.backend, 'run_conclusion': api['run_conclusion'],
        'job': api['job'], 'artifact': api['artifact'], 'api_metadata_sha256': sha(api_wire),
        'archive_member_map': members, 'raw_json': {n: identity(b) for n, b in raw.items()},
        'original_git_sources': {n: identity(b) for n, b in sorted(source.cache.items())},
        'verifier_input_files': {p.relative_to(ROOT).as_posix(): sha(read(p, 512 * 1024))
            for p in [Path(__file__).resolve(), ROOT / SHARED, ROOT / http.SHARED, ROOT / context.SHARED, ROOT / theme.SHARED, ROOT / base.LEGACY]},
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
    print('Private WP worker CI evidence: ' + ('verified positive' if summary['overall_positive'] else 'preserved failure/partial')
        + ' (' + args.backend + ')')
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('Private WP worker import rejected: ' + reason, file=sys.stderr); sys.exit(1)
