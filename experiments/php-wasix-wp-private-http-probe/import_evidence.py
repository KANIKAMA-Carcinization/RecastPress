"""Import thirteen public CI JSON records without executing PHP, Wasm, DB or network.

Only native-normalized origin fields and fixed metrics may be public here.
Private SQL, results, HTTP bodies, worker packets, CGI and nonce bytes remain
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
PREFIX = 'experiments/php-wasix-wp-private-http-probe/'
PRIVATE = 'experiments/php-wasix-wp-private-worker-probe/'
HTTP = 'experiments/php-wasix-wp-http-probe/'
CONTEXT = 'experiments/php-wasix-wp-request-context-probe/'
THEME = 'experiments/php-wasix-wp-request-probe/'
BOOT = 'experiments/php-wasix-wp-bootstrap-probe/'
SHARED = PRIVATE + 'import_evidence.py'
spec = importlib.util.spec_from_file_location('rp_home_offline_bootstrap_verifier', ROOT / SHARED)
private = importlib.util.module_from_spec(spec)
spec.loader.exec_module(private)
http = private.http
context = private.context
theme = context.theme
base = context.base
old = base.old
require, sha, read, identity, exact, canonical, parse_json = (
    base.require, base.sha, base.read, base.identity, base.exact, base.canonical, base.parse_json)


def member_map(backend):
    return {'run.json': PREFIX + 'evidence/linux-' + backend + '.json',
        'host-build.json': PREFIX + 'evidence/linux-build.json',
        'private-worker-build.json': PRIVATE + 'evidence/linux-build.json',
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


observer = http.observer
origin_maps = http.origin_maps
http_metrics_gate = http.http_metrics_gate


def public_gate(value, references=None, fields=None, rejection_contract=None, source=None, data=None, raw=None, backend=None):
    validator = None
    def inspect(v):
        nonlocal validator
        if type(v) is dict:
            if 'http_server_observation' in v:
                service = v['http_server_observation']
                require(type(service) is dict and service.get('schema') ==
                    'recastpress.php-wasix-wp-private-http-observation.v0.5', 'stored_private_http_service_schema')
            if v.get('schema') == 'recastpress.php-wasix-wp-private-http-observation.v0.5':
                require(source is not None and data is not None and raw is not None and backend in references,
                    'source_bound_private_service_validator_required')
                require('private-worker-build.json' in raw and 'run.json' in data, 'same_job_private_worker_receipt_required')
                if validator is None: validator = frozen_checker(source)
                run = data['run.json']
                expected = validator['expected_worker_identity'](data['private-worker-build.json'], raw['private-worker-build.json'])
                checked = {'validations': {}, 'independent_context_checks': run['independent_context_checks']}
                validator['validate_private_server'](v, run['http_server_execution'], expected, references[backend], checked)
            require(not any(key in v for key in ('private_response','private_contexts','raw_worker_json',
                'worker_packet_hex','stdout_hex','stderr_hex','body_hex','headers_hex')), 'raw_private_worker_or_http_payload_in_public_service_record')
            return {key: inspect(item) for key, item in v.items()}
        if type(v) is list: return [inspect(item) for item in v]
        return v
    http.public_gate(inspect(value), references, fields, rejection_contract)


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
    require(job['name'] == 'Cranelift private WP HTTP / ' + backend and job['conclusion'] in old.CONCLUSIONS, 'api_job')
    require(type(job['steps']) is list and 1 <= len(job['steps']) <= 64, 'api_steps')
    for step in job['steps']:
        exact(set(step), {'name', 'conclusion'}, 'api_step_fields')
        require(type(step['name']) is str and 0 < len(step['name']) <= 160
            and step['conclusion'] in old.CONCLUSIONS, 'api_step')
    exact(set(artifact), {'id', 'name', 'size_in_bytes', 'digest'}, 'api_artifact_fields')
    old.integer(artifact['id'], 1); old.integer(artifact['size_in_bytes'], 1, 24 * 1024 * 1024)
    require(artifact['name'] == 'php-wasix-wp-private-http-' + backend + '-' + str(api['run_attempt'])
        and type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']), 'api_artifact')


def archive_gate(path, api, backend, source):
    wire = read(path, 24 * 1024 * 1024)
    exact(identity(wire), {'bytes': api['artifact']['size_in_bytes'],
        'sha256': api['artifact']['digest'][7:]}, 'api_archive_identity')
    reverse = {p: n for n, p in member_map(backend).items()}
    raw, members, expanded = {}, {}, 0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(1 <= len(entries) <= 13 and len({r.filename for r in entries}) == len(entries), 'zip_member_count')
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
    fields = origin_maps(source, response)
    rejection_contract = observer(source)
    for value in data.values(): public_gate(value, response, fields, rejection_contract, source, data, raw, backend)
    return raw, data, members


def stages_gate(value, api, backend, source):
    exact(set(value), {'schema', 'source_commit', 'run_id', 'run_attempt', 'backend', 'stages'}, 'stage_fields')
    require(value['schema'] == 'recastpress.php-wasix-wp-private-http-ci-stages.v0.5'
        and value['source_commit'] == api['head_sha'] and value['run_id'] == str(api['run_id'])
        and value['run_attempt'] == str(api['run_attempt']) and value['backend'] == backend, 'stage_identity')
    names = {}
    for block in source.read('.github/workflows/php-wasix-wp-private-http.yml').decode().split('    - name: ')[1:]:
        match = re.search(r'^      id: ([a-z_]+)$', block, re.M)
        if match: names[match[1]] = block.splitlines()[0].strip()
    exact(set(names), {'php', 'deps', 'parser', 'packages', 'tools', 'module', 'guest', 'rust', 'bootstrap', 'theme', 'context', 'http', 'worker', 'host', 'contract'},
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
    root = SourcePath(source)
    return source.functions(PREFIX + 'derivation_gate.py', ['inputs','dependency_gate','verify'], {
        'HERE': root / PREFIX.rstrip('/'), 'ROOT': root, 'HTTP': root / HTTP.rstrip('/'),
        'WORKER': root / PRIVATE.rstrip('/'), 'CODEC': root / 'experiments/wp-private-response-probe',
        'tomllib': tomllib})['verify']()


def host_build_gate(host, source):
    name = context.SHARED
    text = source.read(name).decode()
    node = next(n for n in source.tree(name).body if isinstance(n, ast.FunctionDef) and n.name == 'host_build_gate')
    function = ast.get_source_segment(text, node)
    changes = [
        ("prefix = PREFIX if home else BOOT", "prefix = PREFIX"),
        ("'recastpress.php-wasix-wp-' + ('request' if home else 'bootstrap') + '-build.v0.5'", "'recastpress.php-wasix-wp-private-http-build.v0.5'"),
        ("('locked_offline_home_host_build_passed' if home else 'locked_offline_bootstrap_host_build_passed')", "'locked_offline_private_http_build_passed'"),
        ("len(host['units']['tests']) >= 31", "len(host['units']['tests']) == 12"),
    ]
    for before, after in changes:
        require(function.count(before) == 1, 'private_http_build_gate_derivation'); function = function.replace(before, after, 1)
    namespace = dict(theme.__dict__, PREFIX=PREFIX)
    exec(compile(function, '<frozen-private-http-build-gate>', 'exec'), namespace)
    anchor = namespace['host_build_gate'](host, source, True)
    exact(len(anchor['input_files']),232,'private_http_232_sources'); exact(len(host['units']['tests']),12,'private_http_12_units')
    derived = derivation_gate(source)
    exact(derived['dependencies']['packages'],58,'private_http_58_packages')
    exact(host['derivation_before'],derived,'source_bound_private_http_derivation')
    return anchor


def frozen_checker(source):
    references = native_references(source)
    return source.functions(PREFIX + 'run_check.py', ['expected_worker_identity','closed','integer','digest','byte_identity','equal',
        'validate_summary','validate_private_server','synthetic_server','self_test'], {'copy': __import__('copy'),
        'canonical': canonical, 'ROOT': SourcePath(source), 'process': SimpleNamespace(identity=identity),
        'context': SimpleNamespace(CONTEXT_FIELDS=source.constants(CONTEXT+'run_check.py')['CONTEXT_FIELDS'],
            linux=SimpleNamespace(reference=lambda backend: references[backend]))})


def positive_gate(data, raw, source, backend):
    run, host, worker, boot, theme_build, context_build, http_build = (data[k] for k in
        ('run.json','host-build.json','private-worker-build.json','bootstrap-build.json','theme-build.json','context-build.json','http-build.json'))
    require(run['schema']=='recastpress.php-wasix-wp-private-http-check.v0.5' and run['passed'] is True
        and run['outcome']=='fixed_private_real_http_php_wasm_passed' and run['platform']=='linux'
        and run['backend']==backend and run['source_reverified_after'] is True and run['owned_cleanup_completed'] is True,
        'positive_private_http_run')
    anchor=host_build_gate(host,source)
    private.host_build_gate(worker,source); context.host_build_gate(context_build,source)
    http.host_build_gate(http_build,source); theme.host_build_gate(boot,source,False); theme.host_build_gate(theme_build,source,True)
    for key,value in [('build_receipt',host),('private_worker_build_receipt',worker),('bootstrap_build_receipt',boot),('theme_build_receipt',theme_build)]:
        exact(run[key],value,'same_job_'+key)
    nested={}
    for label,prefix,receipt,member in [('bootstrap',BOOT,boot,'bootstrap-build.json'),('theme',THEME,theme_build,'theme-build.json'),
        ('context',CONTEXT,context_build,'context-build.json'),('http',HTTP,http_build,'http-build.json'),('private_worker',PRIVATE,worker,'private-worker-build.json')]:
        nested[label]={'receipt_path':prefix+'evidence/linux-build.json','receipt_identity':identity(raw[member]),
            'executable':receipt['executable'],'executable_identity':receipt['executable_identity'],
            'unit_executable':receipt['units']['executable'],'unit_executable_identity':receipt['units']['executable_identity'],
            'source_inputs':len(receipt['input_files']),'units':len(receipt['units']['tests']),'original_build_fully_reverified':True}
    for suffix in ('before','after'):
        exact(host['old_build_'+suffix],nested,'same_job_five_old_builds_'+suffix)
        exact(worker['old_build_'+suffix],{k:nested[k] for k in ('bootstrap','theme','context','http')},'same_job_worker_old_builds_'+suffix)
        exact(http_build['old_build_'+suffix],{k:nested[k] for k in ('bootstrap','theme','context')},'same_job_http_old_builds_'+suffix)
        exact(context_build['old_build_'+suffix],{k:nested[k] for k in ('bootstrap','theme')},'same_job_context_old_builds_'+suffix)
        exact(theme_build['old_build_'+suffix],nested['bootstrap'],'same_job_theme_old_build_'+suffix)
    preparation=context.prepared_gate(data['prepared.json'],data['theme-prepared.json'],data['bootstrap-prepared.json'],source)
    context_sources={'input_files':context_build['input_files'],'prepared':data['prepared.json'],
        'bootstrap_prepared':data['bootstrap-prepared.json'],'theme_prepared':data['theme-prepared.json']}
    pure=observer(source); controller=frozen_checker(source)
    sources={'input_files':anchor['input_files'],'context':context_sources,
        'private_worker':{'input_files':worker['input_files'],'accepted_context':context_sources},'http_observer_inputs':pure['input_hashes']()}
    exact(run['sources_before'],sources,'complete_private_http_sources_before'); exact(run['sources_after'],sources,'complete_private_http_sources_after')
    exact(run['http_observer_pure_checks'],pure['self_test'](),'source_bound_47_http_observer_checks')
    exact(run['http_controller_pure_checks'],controller['self_test'](),'source_bound_40_private_http_controller_checks')
    exact(run['module_provenance'],run['module_provenance_after'],'module_unchanged')
    managed=old.managed_gate(data,raw,source,run['module_provenance'])
    previous=base.frozen_checker(source); native_boot=previous['reference'](backend); native=native_references(source)[backend]
    for prefix in (PREFIX,PRIVATE,HTTP,CONTEXT,THEME,BOOT):
        lock=tomllib.loads(source.read(prefix+'Cargo.lock').decode())
        crates=[p for p in lock['package'] if p['name']=='serde_json']
        require(len(crates)==1 and not any(d.startswith('indexmap') for d in crates[0].get('dependencies',[])),'default_sorted_json_map_required')
    exact(set(run['observations']),{'install','read'},'two_direct_bootstrap_observations')
    exact(set(run['executions']),{'install','read'},'two_direct_bootstrap_executions')
    for label in ('install','read'):
        value=run['observations'][label]; exact(run['validations'][label],previous['validate'](value,label,native_boot[label]),'original_bootstrap_comparison')
        context.operation_types(value,previous['source_operation_limit']); execution=run['executions'][label]
        old.integer(execution['owned_pid'],1); require(execution['owned_reaped'] is True,'owned_bootstrap_reaped')
        exact(execution['exit_code'],0,'owned_bootstrap_exit'); exact(execution['stderr_bytes'],0,'empty_bootstrap_stderr')
        exact(execution['stderr_sha256'],sha(b''),'empty_bootstrap_stderr_sha')
        exact({'bytes':execution['stdout_bytes'],'sha256':execution['stdout_sha256']},identity(canonical(value)+b'\n'),'bootstrap_public_stdout_identity')
        context.php_diagnostic(value['php_result'],source,False)
        module=data['build.json']['php_wasm']
        exact(value['module'],{'path':module['path'],'bytes':module['bytes'],'sha256':module['sha256'],'verified':True},'same_job_bootstrap_module')
        exact(value['build_receipt'],{'path':old.MANAGED+'evidence/build.json','sha256':sha(raw['build.json']),'verified':True,'full_chain_verified_by_host':False},'same_job_bootstrap_module_receipt')
    server=run['http_server_observation']; execution=run['http_server_execution']
    expected_worker=controller['expected_worker_identity'](worker,raw['private-worker-build.json'])
    derived={'validations':{},'independent_context_checks':run['independent_context_checks']}
    controller['validate_private_server'](server,execution,expected_worker,native,derived)
    for key in ('fresh_repeat_404_response_exact','all_three_owned_workers_reaped','request_module_compilations'):
        exact(run[key],derived[key],'source_bound_service_'+key)
    exact(run['validations'],{**{k:run['validations'][k] for k in ('install','read')},**derived['validations']},'five_source_bound_validations')
    requests={label:row['worker_summary'] for label,row in zip(('home','missing','missing_repeat'),server['requests'])}
    old.integer(execution['owned_pid'],1); old.integer(execution['bound_port'],1,65535); old.integer(execution['exit_code'],0,0)
    require(execution['private_input_on_http'] is False and execution['parent_shutdown_input_closed'] is True
        and execution['capture_kind']=='owned_regular_files' and execution['run_retained'] is True,'owned_service_capture_scope')
    require(re.fullmatch(r'target/php-wasix-wp-private-http-probe/server-runs/server-[0-9a-f]{32}',execution['run']) is not None,'owned_service_capture_path')
    old.integer(execution['private_startup_input']['bytes'],1,4096); old.digest(execution['private_startup_input']['sha256'])
    ready=canonical({'schema':'recastpress.php-wasix-wp-private-http-ready.v0.5','port':execution['bound_port']})+b'\n'
    final=canonical(server)+b'\n'
    exact(execution['ready_line'],identity(ready),'service_ready_stdout_identity'); exact(execution['final_line'],identity(final),'service_final_summary_stdout_identity')
    exact(execution['stdout'],identity(ready+final),'whole_summary_only_service_stdout_identity')
    exact(set(run['http_observations']),{'home','missing','missing_repeat'},'three_real_http_observations')
    exact(set(run['http_rejections']),set(pure['REJECTIONS']),'seven_real_http_rejections')
    for label,value in run['http_observations'].items():
        http_metrics_gate(value,label,native['observations']['home' if label=='home' else 'missing']['response'],pure)
    for label,value in run['http_rejections'].items(): http_metrics_gate(value,label,None,pure)
    require(run['real_http_listener_exercised'] is True,'actual_loopback_http_exercised')
    for key in ('request_or_body_normalized','module_cache_exercised'): require(run[key] is False,'unclaimed_http_'+key)
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
        php = run['observations']['read']['php_result'] if label == 'before_requests' else requests[label]
        for k in ('autoload_option_count', 'autoload_max_value_bytes'): exact(check[k], php[k], 'independent_php_' + k)
    pin = parse_json(source.read('experiments/php-wasix-wpdb-probe/container-images.json'))['images'][backend]
    exact(run['image'], pin['repository'] + '@' + pin['platform_digest'], 'fixed_database_image')
    exact(run['image_config_digest'], pin['image_config_digest'], 'database_config_digest')
    # The actual platform string is retained exactly, with no suffix normalization.
    version = '8.4.13' if backend == 'mysql' else '11.8.9-MariaDB-ubu2404'
    exact(run['sql_server_version'], version, 'exact_linux_sql_version')
    return {'all_five_guest_executions_verified':True,'actual_three_http_native_responses_verified':True,
        'all_seven_http_rejections_verified':True,'prepared':preparation,'managed':managed,
        'runtime_source_inputs':232,'private_http_units':12,'service_dependency_packages':58,
        'summary_only_service_stdout_reconstructed':True,'private_worker_stdout_reconstructed_offline':False,
        'private_worker_stdout_size_sha_attestations_only':True,'raw_worker_packet_or_operation_rows_published':False,
        'service_native_response_comparison':False,'service_worker_sources_rehashed':False,
        'native_comparison_in_controller_only':True,'fresh_repeat_404_exact':True,
        'http_wire_digests_recomputed_offline':False,'private_nonce_or_rpc_digests_recomputed_offline':False}


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
    work = ROOT / 'target/php-wasix-wp-private-http-probe/ci'
    api_wire = read(work / ('api-' + args.run + '-' + args.backend + '.json'), 128 * 1024)
    api = parse_json(api_wire); api_gate(api, args.run, args.backend); old.privacy_gate(api)
    source = old.GitSource(api['head_sha'])
    for name in [SHARED, private.SHARED, http.SHARED, context.SHARED, theme.SHARED, base.LEGACY]:
        exact(source.read(name), read(ROOT / name, 512 * 1024), 'frozen_shared_verifier_helpers_changed')
    raw, data, members = archive_gate(work / ('run-' + args.run + '-' + args.backend + '.zip'), api, args.backend, source)
    stages = stages_gate(data['ci-stages.json'], api, args.backend, source)
    positive = api['run_conclusion'] == api['job']['conclusion'] == 'success' and stages
    if positive: exact(set(raw), set(member_map(args.backend)), 'thirteen_positive_public_records')
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
    proof = {'schema': 'recastpress.php-wasix-wp-private-http-ci-import.v0.5', 'repository': old.REPOSITORY,
        'repository_id': old.REPOSITORY_ID, 'run_id': api['run_id'], 'run_attempt': api['run_attempt'],
        'source_commit': source.commit, 'backend': args.backend, 'run_conclusion': api['run_conclusion'],
        'job': api['job'], 'artifact': api['artifact'], 'api_metadata_sha256': sha(api_wire),
        'archive_member_map': members, 'raw_json': {n: identity(b) for n, b in raw.items()},
        'original_git_sources': {n: identity(b) for n, b in sorted(source.cache.items())},
        'verifier_input_files': {p.relative_to(ROOT).as_posix(): sha(read(p, 512 * 1024))
            for p in [Path(__file__).resolve(), ROOT / SHARED, ROOT / private.SHARED, ROOT / http.SHARED, ROOT / context.SHARED, ROOT / theme.SHARED, ROOT / base.LEGACY]},
        'verification': summary, 'offline_nonce_recomputed': False, 'private_source_bytes_reconstructed': False,
        'source_tool_module_archive_binaries_reverified': False, 'network_used_by_importer': False,
        'php_wasm_or_database_executed_by_importer': False, 'product_runtime_selected': False,
        'privacy_exception_scope': 'exact public CI clang workspace; exact normalized native origin maps; typed fixed authorization rejection metrics label only; no raw worker or HTTP body bytes; originals unchanged'}
    public_gate(proof)
    wire = (json.dumps(proof, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    require(len(wire) <= 2 * 1024 * 1024, 'import_proof_quota')
    if not args.verify_only:
        destination = HERE / ('evidence/ci-' + args.run + '-' + args.backend)
        for name, content in raw.items(): immutable(destination / 'raw' / members[name], content)
        immutable(destination / 'api-metadata.json', api_wire); immutable(destination / 'provenance.json', wire)
    print('Private WP HTTP CI evidence: ' + ('verified positive' if summary['overall_positive'] else 'preserved failure/partial')
        + ' (' + args.backend + ')')
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('Private WP HTTP import rejected: ' + reason, file=sys.stderr); sys.exit(1)
