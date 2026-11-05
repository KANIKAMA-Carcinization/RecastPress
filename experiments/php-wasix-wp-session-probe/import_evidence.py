"""Import fourteen public CI JSON records without executing PHP, Wasm, DB or network.

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
PREFIX = 'experiments/php-wasix-wp-session-probe/'
PRIVATE = 'experiments/php-wasix-wp-private-worker-probe/'
LATEST = 'experiments/php-wasix-wp-private-http-probe/'
CONTEXT = 'experiments/php-wasix-wp-request-context-probe/'
HTTP = 'experiments/php-wasix-wp-http-probe/'
THEME = 'experiments/php-wasix-wp-request-probe/'
BOOT = 'experiments/php-wasix-wp-bootstrap-probe/'
SHARED = LATEST + 'import_evidence.py'
spec = importlib.util.spec_from_file_location('rp_home_offline_bootstrap_verifier', ROOT / SHARED)
latest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latest)
private = latest.private
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
        'private-http-build.json': LATEST + 'evidence/linux-build.json',
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


def public_gate(value, references=None, source=None, backend=None):
    checker=None
    approved=[]
    if references is not None:
        approved=[{key:native['observations'][case][key] for key in ('case','is_home','is_404','transport_body',
            'context_before_bootstrap','context_after_bootstrap','context_after_dispatch')}
            for native in references.values() for case in ('home','missing')]
    def inspect(v):
        nonlocal checker
        if type(v) is dict:
            if 'session_executions' in v:
                rows=v['session_executions'];require(type(rows) is dict and set(rows)<=
                    {'positive','bad_first_sequence','early_eof'},'closed_partial_session_execution_labels')
                for label,row in rows.items():partial_execution_gate(row,label)
            if 'session_ready' in v:
                ready=v['session_ready'];require(type(ready) is dict,'session_ready_object')
                old.digest(ready.get('module_sha256'));ready_gate(ready,ready['module_sha256'])
            if 'session_final' in v:
                require(source is not None,'source_bound_session_final_required')
                if checker is None:checker=frozen_checker(source)
                checker['validate_final'](v['session_final'],3,True)
            if 'negative_protocol_sessions' in v:
                negatives=v['negative_protocol_sessions']
                require(type(negatives) is dict and set(negatives)<={'bad_first_sequence','early_eof'},'closed_negative_session_labels')
                if negatives:
                    require(source is not None,'source_bound_negative_session_required')
                    if checker is None:checker=frozen_checker(source)
                for item in negatives.values():
                    require(type(item) is dict,'negative_session_object')
                    exact(set(item),{'ready','final','guest_requests_executed','module_compilations'},'closed_public_negative_session')
                    require(type(item['ready']) is dict,'negative_ready_object');old.digest(item['ready'].get('module_sha256'))
                    ready_gate(item['ready'],item['ready']['module_sha256']);checker['validate_final'](item['final'],0,False)
                    exact(item['guest_requests_executed'],0,'negative_public_guest_count');exact(item['module_compilations'],1,'negative_public_compile_count')
            if 'observations' in v:
                observations=v['observations'];require(type(observations) is dict and set(observations)<=
                    {'install','read','home','missing','missing_repeat'},'closed_session_observation_labels')
                for label in ('home','missing','missing_repeat'):
                    if label in observations:
                        require(type(observations[label]) is dict and observations[label].get('schema')==
                            'recastpress.php-wasix-wp-session-request-observation.v0.5','stored_session_request_schema')
                        exact(observations[label].get('case'),'home' if label=='home' else 'missing','stored_session_case')
            if v.get('schema')=='recastpress.php-wasix-wp-session-request-observation.v0.5':
                require(source is not None and references is not None and backend in references,'source_bound_session_projection_required')
                if checker is None:checker=frozen_checker(source)
                projected=checker['project_request'](v);exact(projected,v,'idempotent_session_request_projection')
                case=v.get('case');require(case in ('home','missing'),'closed_session_projection_case')
                labels=('home',) if case=='home' else ('missing','missing_repeat');accepted=False
                for label in labels:
                    try:checker['validate_request'](v,case,references[backend]['requests'][label]['observation']);accepted=True;break
                    except (ValueError,KeyError,TypeError):pass
                require(accepted,'session_projection_native_and_lifecycle_validation')
            if 'private_response' in v:
                responses=[] if references is None else [n['observations'][case]['response'] for n in references.values() for case in ('home','missing')]
                require(any(canonical(v['private_response'])==canonical(r) for r in responses),'only_complete_known_session_response_public')
            if 'private_contexts' in v:require(any(canonical(v['private_contexts'])==canonical(r) for r in approved),'only_complete_known_session_contexts_public')
            require(not any(k in v for k in ('raw_ack','acknowledgement_hex','raw_request_packet','stdout_hex','stderr_hex','private_packet_hex')),
                'raw_session_packet_in_public_record')
            return {k:inspect(item) for k,item in v.items()}
        if type(v) is list:return [inspect(item) for item in v]
        return v
    context.public_gate(inspect(value),references)


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
    require(job['name'] == 'Cranelift WP session / ' + backend and job['conclusion'] in old.CONCLUSIONS, 'api_job')
    require(type(job['steps']) is list and 1 <= len(job['steps']) <= 64, 'api_steps')
    for step in job['steps']:
        exact(set(step), {'name', 'conclusion'}, 'api_step_fields')
        require(type(step['name']) is str and 0 < len(step['name']) <= 160
            and step['conclusion'] in old.CONCLUSIONS, 'api_step')
    exact(set(artifact), {'id', 'name', 'size_in_bytes', 'digest'}, 'api_artifact_fields')
    old.integer(artifact['id'], 1); old.integer(artifact['size_in_bytes'], 1, 24 * 1024 * 1024)
    require(artifact['name'] == 'php-wasix-wp-session-' + backend + '-' + str(api['run_attempt'])
        and type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']), 'api_artifact')


def archive_gate(path, api, backend, source):
    wire = read(path, 24 * 1024 * 1024)
    exact(identity(wire), {'bytes': api['artifact']['size_in_bytes'],
        'sha256': api['artifact']['digest'][7:]}, 'api_archive_identity')
    reverse = {p: n for n, p in member_map(backend).items()}
    raw, members, expanded = {}, {}, 0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(1 <= len(entries) <= 14 and len({r.filename for r in entries}) == len(entries), 'zip_member_count')
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
    for value in data.values(): public_gate(value, response, source, backend)
    return raw, data, members


def stages_gate(value, api, backend, source):
    exact(set(value), {'schema', 'source_commit', 'run_id', 'run_attempt', 'backend', 'stages'}, 'stage_fields')
    require(value['schema'] == 'recastpress.php-wasix-wp-session-ci-stages.v0.5'
        and value['source_commit'] == api['head_sha'] and value['run_id'] == str(api['run_id'])
        and value['run_attempt'] == str(api['run_attempt']) and value['backend'] == backend, 'stage_identity')
    names = {}
    for block in source.read('.github/workflows/php-wasix-wp-session.yml').decode().split('    - name: ')[1:]:
        match = re.search(r'^      id: ([a-z_]+)$', block, re.M)
        if match: names[match[1]] = block.splitlines()[0].strip()
    exact(set(names), {'php', 'deps', 'parser', 'packages', 'tools', 'module', 'guest', 'rust', 'bootstrap', 'theme', 'context', 'http', 'worker', 'private_http', 'host', 'contract'},
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
    root=SourcePath(source);name=PREFIX+'derivation_gate.py'
    return source.functions(name,['inputs','verify'],{'HERE':root/PREFIX.rstrip('/'),'ROOT':root,
        'WORKER':root/PRIVATE.rstrip('/'),'SHARED':root/THEME.rstrip('/'),'CODEC':root/'experiments/wp-private-response-probe',
        'REUSED':source.constants(name)['REUSED'],'tomllib':tomllib})['verify']()


def host_build_gate(host,source):
    name=context.SHARED;text=source.read(name).decode()
    node=next(n for n in source.tree(name).body if isinstance(n,ast.FunctionDef) and n.name=='host_build_gate')
    function=ast.get_source_segment(text,node)
    for before,after in [
        ('prefix = PREFIX if home else BOOT','prefix = PREFIX'),
        ("'recastpress.php-wasix-wp-' + ('request' if home else 'bootstrap') + '-build.v0.5'","'recastpress.php-wasix-wp-session-build.v0.5'"),
        ("('locked_offline_home_host_build_passed' if home else 'locked_offline_bootstrap_host_build_passed')","'locked_offline_session_build_passed'")]:
        require(function.count(before)==1,'session_build_gate_derivation');function=function.replace(before,after,1)
    namespace=dict(theme.__dict__,PREFIX=PREFIX);exec(compile(function,'<frozen-session-build-gate>','exec'),namespace)
    anchor=namespace['host_build_gate'](host,source,True)
    exact(len(anchor['input_files']),241,'session_241_sources');exact(len(host['units']['tests']),64,'session_64_units')
    name=source.constants(PREFIX+'build_check.py')['NAME'];target='target/php-wasix-wp-bootstrap-probe/cargo-target/release/'
    exact(host['executable'],target+name,'fixed_linux_session_executable')
    require(re.fullmatch(re.escape(target+'deps/'+name.replace('-','_'))+r'-[0-9a-f]{16}',host['units']['executable']),
        'fixed_linux_session_unit_path')
    exact(host['derivation_before'],derivation_gate(source),'source_bound_compile_once_derivation')
    return anchor


def frozen_checker(source):
    previous=theme.frozen_checker(source);fields=source.constants(CONTEXT+'run_check.py')['CONTEXT_FIELDS']
    references=native_references(source);private_gate=private.frozen_checker(source)
    context_constants=source.constants(CONTEXT+'run_check.py')
    fake=SimpleNamespace(THEME_CONTROLLER=SourcePath(source,THEME+'run_check.py'),
        FROZEN_THEME_CONTROLLER=context_constants['FROZEN_THEME_CONTROLLER'],theme=SimpleNamespace(**previous),
        linux=SimpleNamespace(reference=lambda backend:references[backend]))
    private_namespace=SimpleNamespace(public_projection=private_gate['public_projection'])
    namespace=source.functions(PREFIX+'run_check.py',['equal','request_validator','validate_request','publish_request','validate_final','self_test'],
        {'copy':copy,'ast':ast,'canonical':canonical,'SCHEMA':source.constants(PREFIX+'run_check.py')['SCHEMA'],
        'FIELDS':fields,'ROOT':SourcePath(source),'context':fake,'private':private_namespace})
    def project(value):
        projected=private_gate['public_projection'](value)
        projected.update(compiled_module_reused=True,module_compiled_during_request=False)
        return projected
    namespace['project_request']=project
    return namespace


def typed_operations(value,maximum,source,session_request):
    if not session_request:return context.operation_types(value,maximum)
    name=CONTEXT+'import_evidence.py';text=source.read(name).decode()
    node=next(n for n in source.tree(name).body if isinstance(n,ast.FunctionDef) and n.name=='operation_types')
    function=ast.get_source_segment(text,node)
    before="exact(value['module_compilations'], 1, 'typed_one_module_compilation')"
    require(function.count(before)==1,'source_typed_operation_compile_literal')
    derived=function.replace(before,"exact(value['module_compilations'], 0, 'typed_one_module_compilation')",1)
    namespace=dict(context.__dict__);exec(compile(derived,'<session-typed-compile-zero>','exec'),namespace)
    namespace['operation_types'](value,maximum)


PROFILES=[('bootstrap',BOOT,'bootstrap-build.json',145,31,'bootstrap','locked_offline_bootstrap_host_build_passed'),
    ('theme',THEME,'theme-build.json',172,53,'request','locked_offline_home_host_build_passed'),
    ('context',CONTEXT,'context-build.json',195,58,'request-context','locked_offline_context_host_build_passed'),
    ('http',HTTP,'http-build.json',206,21,'http','locked_offline_http_host_build_passed'),
    ('private_worker',PRIVATE,'private-worker-build.json',219,59,'private-worker','locked_offline_private_worker_build_passed'),
    ('private_http',LATEST,'private-http-build.json',232,12,'private-http','locked_offline_private_http_build_passed')]


def old_snapshots_gate(data,raw,source):
    combined=data['private-http-build.json']['input_files'];result={}
    for label,prefix,member,count,units,profile,outcome in PROFILES:
        value=data[member];anchor=parse_json(source.read(prefix+'evidence/windows-build.json'))
        exact(value['schema'],'recastpress.php-wasix-wp-'+profile+'-build.v0.5','old_scoped_build_schema')
        exact(value['outcome'],outcome,'old_scoped_build_outcome')
        for key in ('passed','locked_offline','owned_cleanup_completed','source_reverified_after'):require(value[key] is True,'old_scoped_'+key)
        exact(value['input_files'],anchor['input_files'],'old_exact_source_map');exact(len(value['input_files']),count,'old_source_count')
        source.bindings(value['input_files'])
        require(all(combined.get(name)==digest for name,digest in value['input_files'].items()),'old_sources_bound_to_combined')
        exact(value['units']['tests'],anchor['units']['tests'],'old_exact_unit_inventory');exact(len(value['units']['tests']),units,'old_unit_count')
        require(value['units']['passed'] is True,'old_units_passed')
        for scope in ('driver','source_fs_registry','derivation','old_build'):
            if scope+'_before' in value:exact(value[scope+'_before'],value[scope+'_after'],'old_scoped_before_after')
        for scope in ('source_fs_registry','derivation'):exact(value[scope+'_before'],anchor[scope+'_before'],'old_scoped_static_'+scope)
        for key in ('php_executed','wasm_executed','database_started','database_connected','product_runtime_selected'):require(value[key] is False,'old_scoped_unclaimed')
        exact(value['build_profile'],'release','old_linux_release_build')
        name=source.constants(prefix+'build_check.py')['NAME']
        target='target/php-wasix-wp-http-probe/cargo-target' if prefix in (HTTP,LATEST) else 'target/php-wasix-wp-bootstrap-probe/cargo-target'
        exact(value['executable'],target+'/release/'+name,'old_fixed_executable_path')
        require(re.fullmatch(re.escape(target+'/release/deps/'+name.replace('-','_'))+r'-[0-9a-f]{16}',value['units']['executable']), 'old_fixed_unit_path')
        for item in (value['executable_identity'],value['units']['executable_identity']):
            exact(set(item),{'bytes','sha256'},'old_artifact_identity_shape');old.integer(item['bytes'],1,256*1024*1024);old.digest(item['sha256'])
        exact(len(value['commands']),4,'old_four_build_stages')
        for row,(stage,deadline) in zip(value['commands'],[('unit-build',1200),('unit-list',10),('unit-run',60),('normal-build',1200)]):
            exact(row['stage'],stage,'old_build_stage');exact(row['timeout_seconds'],deadline,'old_stage_deadline')
            require(row['owned_reaped'] is True,'old_build_child_reaped');old.integer(row['owned_pid'],1);exact(row['exit_code'],0,'old_build_exit')
        result[label]={'receipt_path':prefix+'evidence/linux-build.json','receipt_identity':identity(raw[member]),
            'executable':value['executable'],'executable_identity':value['executable_identity'],
            'unit_executable':value['units']['executable'],'unit_executable_identity':value['units']['executable_identity'],
            'source_inputs':count,'units':units,'exact_receipt_source_map_rederived':True,
            'source_map_bound_to_combined_inputs':True,'artifact_bytes_rehashed':True,'full_recursive_old_gate_invoked':False}
    return result


def ready_gate(value,module_sha):
    exact(value,{'schema':'recastpress.php-wasix-wp-session-ready.v0.5','module_compilations_total':1,
        'module_sha256':module_sha,'compiled_module_ready':True},'closed_session_ready')


def identity_gate(value,maximum,minimum=0):
    exact(set(value),{'bytes','sha256'},'session_identity_shape');old.integer(value['bytes'],minimum,maximum);old.digest(value['sha256'])


def partial_execution_gate(row,label):
    required={'owned_reaped','forced_cleanup','capture_kind','run','run_retained','parent_input_closed','captured_output_complete_after_reap','events'}
    optional={'owned_pid','ready_line','exit_code','final_line','stdout','stderr','negative_input','descendant_cleanup_verified'}
    require(type(row) is dict and required<=set(row) and set(row)<=required|optional,'closed_partial_session_execution')
    for key in ('owned_reaped','forced_cleanup','parent_input_closed','captured_output_complete_after_reap'):
        require(type(row[key]) is bool,'typed_partial_session_flag')
    exact(row['capture_kind'],'owned_regular_files','partial_session_capture_kind');exact(row['run_retained'],True,'partial_session_run_retained')
    require(type(row['run']) is str and re.fullmatch(r'target/php-wasix-wp-session-probe/session-runs/session-[0-9a-f]{32}',row['run']),
        'partial_session_owned_path')
    if 'owned_pid' in row:old.integer(row['owned_pid'],1)
    if 'exit_code' in row:old.integer(row['exit_code'],-2**31,2**31-1)
    if 'descendant_cleanup_verified' in row:exact(row['descendant_cleanup_verified'],False,'partial_descendant_scope')
    for key in ('ready_line','final_line'):
        if key in row:identity_gate(row[key],4*1024*1024,1)
    for key in ('stdout','stderr'):
        if key in row:identity_gate(row[key],2**40)
    if 'negative_input' in row:
        require(label=='bad_first_sequence','partial_negative_input_scope');identity_gate(row['negative_input'],24*1024,1)
    require(type(row['events']) is list and len(row['events'])<=3,'partial_session_event_count')
    for seq,event in enumerate(row['events'],1):
        require(type(event) is dict,'partial_session_event_object');exact(set(event),{'seq','input','acknowledgement'},'partial_session_event_fields')
        exact(event['seq'],seq,'partial_session_event_sequence');identity_gate(event['input'],24*1024,1);identity_gate(event['acknowledgement'],4*1024*1024,1)


def execution_gate(row,ready,final,source,module_sha,positive):
    keys={'owned_reaped','forced_cleanup','capture_kind','run','run_retained','parent_input_closed','captured_output_complete_after_reap',
        'events','owned_pid','ready_line','exit_code','final_line','stdout','stderr'}
    if 'negative_input' in row:keys.add('negative_input')
    exact(set(row),keys,'closed_session_execution')
    for key in ('owned_reaped','run_retained','parent_input_closed','captured_output_complete_after_reap'):require(row[key] is True,'session_execution_'+key)
    require(row['forced_cleanup'] is False,'session_not_forced');exact(row['capture_kind'],'owned_regular_files','session_capture_kind')
    old.integer(row['owned_pid'],1);exact(row['exit_code'],0 if positive else 1,'session_expected_exit')
    require(re.fullmatch(r'target/php-wasix-wp-session-probe/session-runs/session-[0-9a-f]{32}',row['run']),'session_owned_capture_path')
    ready_gate(ready,module_sha);ready_wire=canonical(ready)+b'\n';final_wire=canonical(final)+b'\n'
    exact(row['ready_line'],identity(ready_wire),'canonical_ready_line');exact(row['final_line'],identity(final_wire),'canonical_final_line')
    exact(row['stderr'],identity(b''),'empty_session_stderr')
    identity_gate(row['stdout'],16*1024*1024,1)
    if positive:
        require(type(row['events']) is list and len(row['events'])==3,'three_session_ack_attestations')
        total=len(ready_wire)+len(final_wire)
        for seq,event in enumerate(row['events'],1):
            exact(set(event),{'seq','input','acknowledgement'},'closed_session_event');exact(event['seq'],seq,'session_event_order')
            identity_gate(event['input'],24*1024,1);identity_gate(event['acknowledgement'],4*1024*1024,1)
            total+=event['acknowledgement']['bytes']
        exact(row['stdout']['bytes'],total,'positive_session_total_line_lengths')
        require('negative_input' not in row,'positive_no_negative_frame')
    else:
        exact(row['events'],[],'zero_negative_guest_acknowledgements')
        exact(row['stdout'],identity(ready_wire+final_wire),'exact_zero_guest_negative_stdout')
        if 'negative_input' in row:identity_gate(row['negative_input'],24*1024,1)


def parent_mock_gate(value,source):
    node=next(n for n in source.tree(PREFIX+'run_check.py').body if isinstance(n,ast.FunctionDef) and n.name=='parent_self_test')
    loop=next(n for n in node.body if isinstance(n,ast.For));cases=ast.literal_eval(loop.iter)
    exact(cases,('deadline','line_overflow','final_exit_timeout'),'source_three_parent_mock_cases')
    exact(value,{'passed':True,'tests':[{'case':case,'passed':True,'owned_reaped':True,'forced_cleanup':True} for case in cases],
        'test_count':3,'owned_python_mocks_only':True,'php_wasm_database_or_http_executed':False},'actual_parent_process_mock_receipts')


def positive_gate(data,raw,source,backend):
    run,host,boot=data['run.json'],data['host-build.json'],data['bootstrap-build.json']
    require(run['schema']=='recastpress.php-wasix-wp-session-check.v0.5' and run['passed'] is True
        and run['outcome']=='fixed_compile_once_wp_session_passed' and run['platform']=='linux' and run['backend']==backend
        and run['source_reverified_after'] is True and run['owned_cleanup_completed'] is True,'positive_session_run')
    anchor=host_build_gate(host,source);snapshots=old_snapshots_gate(data,raw,source)
    exact(host['old_build_before'],snapshots,'same_job_nonrecursive_old_six_before');exact(host['old_build_after'],snapshots,'same_job_nonrecursive_old_six_after')
    exact(run['build_receipt'],host,'same_job_session_build');exact(run['bootstrap_build_receipt'],boot,'same_job_bootstrap_build')
    preparation=context.prepared_gate(data['prepared.json'],data['theme-prepared.json'],data['bootstrap-prepared.json'],source)
    context_sources={'input_files':data['context-build.json']['input_files'],'prepared':data['prepared.json'],
        'bootstrap_prepared':data['bootstrap-prepared.json'],'theme_prepared':data['theme-prepared.json']}
    sources={'input_files':anchor['input_files'],'private_worker':{'input_files':data['private-worker-build.json']['input_files'],'accepted_context':context_sources}}
    exact(run['sources_before'],sources,'session_sources_before');exact(run['sources_after'],sources,'session_sources_after')
    exact(run['module_provenance'],run['module_provenance_after'],'session_module_unchanged')
    managed=old.managed_gate(data,raw,source,run['module_provenance']);module_sha=data['build.json']['php_wasm']['sha256']
    checker=frozen_checker(source);previous=base.frozen_checker(source);native=native_references(source)[backend];native_boot=previous['reference'](backend)
    exact(run['controller_pure_checks'],checker['self_test'](),'source_48_session_controller_checks');parent_mock_gate(run['parent_process_checks'],source)
    for prefix in (PREFIX,PRIVATE,LATEST,HTTP,CONTEXT,THEME,BOOT):
        crates=[p for p in tomllib.loads(source.read(prefix+'Cargo.lock').decode())['package'] if p['name']=='serde_json']
        require(len(crates)==1 and not any(d.startswith('indexmap') for d in crates[0].get('dependencies',[])),'sorted_json_maps_required')
    labels=('install','read','home','missing','missing_repeat')
    for key in ('observations','validations'):exact(set(run[key]),set(labels),'five_session_'+key)
    exact(set(run['executions']),{'install','read'},'two_bootstrap_processes')
    for label in labels:
        value=run['observations'][label];request=label not in ('install','read');case='missing' if label=='missing_repeat' else label
        wanted=checker['validate_request'](value,case,native['requests'][label]['observation']) if request else previous['validate'](value,label,native_boot[label])
        exact(run['validations'][label],wanted,'source_compile_zero_native_lifecycle_validation')
        typed_operations(value,4096 if request else previous['source_operation_limit'],source,request)
        if not request:
            event=run['executions'][label];old.integer(event['owned_pid'],1);require(event['owned_reaped'] is True,'bootstrap_reaped')
            exact(event['exit_code'],0,'bootstrap_exit');exact(event['stderr_bytes'],0,'bootstrap_stderr')
            exact(event['stderr_sha256'],sha(b''),'bootstrap_empty_stderr_sha')
            exact({'bytes':event['stdout_bytes'],'sha256':event['stdout_sha256']},identity(canonical(value)+b'\n'),'bootstrap_stdout')
            module=data['build.json']['php_wasm'];exact(value['module'],{'path':module['path'],'bytes':module['bytes'],'sha256':module['sha256'],'verified':True},'same_job_bootstrap_module')
            exact(value['build_receipt'],{'path':old.MANAGED+'evidence/build.json','sha256':sha(raw['build.json']),'verified':True,'full_chain_verified_by_host':False},'same_job_bootstrap_module_receipt')
        else:
            require(value['worker_fields_projected'] is True and value['original_worker_json_published'] is False,'only_projected_session_requests')
            exact(checker['project_request'](value),value,'session_projection_idempotent')
            exact(value['prepared'],{'verification_scope':'compiled_fixtures_and_core_only','transitive_inputs_verified_by_worker':False,
                'source_verified':True,'core_files':3782,'owned_dropin_files':1},'session_request_source_scope')
    ready=run['session_ready'];final=run['session_final'];ready_gate(ready,module_sha);checker['validate_final'](final,3,True)
    executions=run['session_executions'];exact(set(executions),{'positive','bad_first_sequence','early_eof'},'three_owned_sessions')
    execution_gate(executions['positive'],ready,final,source,module_sha,True)
    negatives=run['negative_protocol_sessions'];exact(set(negatives),{'bad_first_sequence','early_eof'},'two_zero_guest_negatives')
    for label,value in negatives.items():
        exact(set(value),{'ready','final','guest_requests_executed','module_compilations'},'closed_negative_session')
        exact(value['guest_requests_executed'],0,'negative_no_guest_requests');exact(value['module_compilations'],1,'negative_one_own_compile')
        checker['validate_final'](value['final'],0,False);execution_gate(executions[label],value['ready'],value['final'],source,module_sha,False)
        require(('negative_input' in executions[label])==(label=='bad_first_sequence'),'negative_input_case_scope')
    pids=[row['owned_pid'] for row in executions.values()];require(len(set(pids))==3,'three_separate_session_processes')
    for key,wanted in [('positive_session_module_compilations',1),('positive_request_module_compilations',0),('positive_request_count',3),
        ('positive_owned_worker_processes',1),('negative_protocol_sessions_count',2),('negative_session_module_compilations',2)]:exact(run[key],wanted,'separate_positive_negative_compile_counts')
    for key in ('response_cache_exercised','shared_php_state_exercised','shared_database_connection_exercised','synchronous_guest_cpu_cancellation_verified'):
        require(run[key] is False,'unclaimed_session_'+key)
    require(run['fresh_repeat_404_response_exact'] is True,'session_repeat_404');exact(run['observations']['missing']['private_response'],run['observations']['missing_repeat']['private_response'],'session_repeat_body_equal')
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
    return {'all_five_guest_observations_verified':True,'prepared':preparation,'managed':managed,'runtime_source_inputs':241,'session_units':64,
        'positive_session_compilations':1,'positive_request_compilations':0,'separate_negative_compilations':2,
        'compiled_module_reused_with_fresh_request_state':True,'original_acknowledgements_or_positive_stdout_reconstructed':False,
        'ready_final_and_zero_guest_negative_stdout_reconstructed':True,'private_ack_and_input_identities_are_attestations':True,
        'nonrecursive_old_six_snapshot_scope_verified':True,'old_binary_bytes_rehashed_by_importer':False,
        'native_comparison_in_controller_only':True,'raw_private_packets_published':False}


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
    work = ROOT / 'target/php-wasix-wp-session-probe/ci'
    api_wire = read(work / ('api-' + args.run + '-' + args.backend + '.json'), 128 * 1024)
    api = parse_json(api_wire); api_gate(api, args.run, args.backend); old.privacy_gate(api)
    source = old.GitSource(api['head_sha'])
    for name in [SHARED, latest.SHARED, private.SHARED, http.SHARED, context.SHARED, theme.SHARED, base.LEGACY]:
        exact(source.read(name), read(ROOT / name, 512 * 1024), 'frozen_shared_verifier_helpers_changed')
    raw, data, members = archive_gate(work / ('run-' + args.run + '-' + args.backend + '.zip'), api, args.backend, source)
    stages = stages_gate(data['ci-stages.json'], api, args.backend, source)
    positive = api['run_conclusion'] == api['job']['conclusion'] == 'success' and stages
    if positive: exact(set(raw), set(member_map(args.backend)), 'fourteen_positive_public_records')
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
    proof = {'schema': 'recastpress.php-wasix-wp-session-ci-import.v0.5', 'repository': old.REPOSITORY,
        'repository_id': old.REPOSITORY_ID, 'run_id': api['run_id'], 'run_attempt': api['run_attempt'],
        'source_commit': source.commit, 'backend': args.backend, 'run_conclusion': api['run_conclusion'],
        'job': api['job'], 'artifact': api['artifact'], 'api_metadata_sha256': sha(api_wire),
        'archive_member_map': members, 'raw_json': {n: identity(b) for n, b in raw.items()},
        'original_git_sources': {n: identity(b) for n, b in sorted(source.cache.items())},
        'verifier_input_files': {p.relative_to(ROOT).as_posix(): sha(read(p, 512 * 1024))
            for p in [Path(__file__).resolve(), ROOT / SHARED, ROOT / latest.SHARED, ROOT / private.SHARED, ROOT / http.SHARED, ROOT / context.SHARED, ROOT / theme.SHARED, ROOT / base.LEGACY]},
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
    print('WP session CI evidence: ' + ('verified positive' if summary['overall_positive'] else 'preserved failure/partial')
        + ' (' + args.backend + ')')
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('WP session import rejected: ' + reason, file=sys.stderr); sys.exit(1)
