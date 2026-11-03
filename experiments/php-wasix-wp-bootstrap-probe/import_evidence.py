"""Import six public JSON CI records; never run PHP, Wasm, DBs or network IO.

Private SQL/result/CGI bytes are intentionally unavailable. Their SHA values
remain source-bound CI observations, never an offline recomputation claim.
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

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
PREFIX='experiments/php-wasix-wp-bootstrap-probe/'
LEGACY='experiments/php-wasix-wpdb-probe/import_evidence.py'
spec=importlib.util.spec_from_file_location('rp_full_bootstrap_json_verifier_helpers',ROOT/LEGACY)
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
require,sha,read,identity,exact,canonical,parse_json=old.require,old.sha,old.read,old.identity,old.exact,old.canonical,old.parse_json
UPSTREAM_VALIDATION_SHA='d5f916f8a8493acec1f1e0fc634bfc90d6e7207acf9cc1ee2dc1782608db02b8'


def member_map(backend):
    return {'run.json':PREFIX+'evidence/linux-'+backend+'.json',
        'host-build.json':PREFIX+'evidence/linux-build.json','ci-stages.json':PREFIX+'evidence/ci-stages.json',
        'build.json':old.MANAGED+'evidence/build.json','toolchain.json':old.CGI+'evidence/toolchain.json',
        'prepared.json':'target/php-wasix-wp-bootstrap-probe/prepared.json'}


def public_gate(value):
    old.privacy_gate(value)
    def visit(v):
        if type(v) is dict:
            for key,item in v.items():
                require(key not in {'sql_hex','query_hex','data_hex','message_hex','stdout_hex','stderr_hex',
                    'body_hex','user_pass','salts','admin_password','password','nonce'},'private_payload_in_public_record')
                require(key!='rows' or type(item) is int,'private_rows_in_public_record')
                visit(item)
        elif type(v) is list:
            for item in v:visit(item)
    visit(value)


def api_gate(api,run,backend):
    exact(set(api),{'repository_id','repository','run_id','run_attempt','head_sha','run_conclusion','job','artifact'},'api_fields')
    exact(api['repository_id'],old.REPOSITORY_ID,'api_repository_id')
    exact(api['repository'],old.REPOSITORY,'api_repository')
    old.integer(api['run_id'],1);old.integer(api['run_attempt'],1,1000)
    require(str(api['run_id'])==run and api['run_conclusion'] in old.CONCLUSIONS,'api_run')
    require(type(api['head_sha']) is str and re.fullmatch('[0-9a-f]{40}',api['head_sha']),'api_commit')
    job,artifact=api['job'],api['artifact']
    exact(set(job),{'id','name','conclusion','steps'},'api_job_fields');old.integer(job['id'],1)
    require(job['name']=='Cranelift full WP / '+backend and job['conclusion'] in old.CONCLUSIONS,'api_job')
    require(type(job['steps']) is list and 1<=len(job['steps'])<=64,'api_steps')
    for step in job['steps']:
        exact(set(step),{'name','conclusion'},'api_step_fields')
        require(type(step['name']) is str and 0<len(step['name'])<=160 and step['conclusion'] in old.CONCLUSIONS,'api_step')
    exact(set(artifact),{'id','name','size_in_bytes','digest'},'api_artifact_fields')
    old.integer(artifact['id'],1);old.integer(artifact['size_in_bytes'],1,16*1024*1024)
    require(artifact['name']=='php-wasix-wp-bootstrap-'+backend+'-'+str(api['run_attempt'])
        and type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}',artifact['digest']),'api_artifact')


def archive_gate(path,api,backend):
    wire=read(path,16*1024*1024)
    exact(identity(wire),{'bytes':api['artifact']['size_in_bytes'],'sha256':api['artifact']['digest'][7:]},'api_archive_identity')
    reverse={p:n for n,p in member_map(backend).items()};raw={};members={};expanded=0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries=archive.infolist();require(1<=len(entries)<=6 and len({r.filename for r in entries})==len(entries),'zip_member_count')
        for entry in entries:
            old.relative(entry.filename)
            require(entry.filename in reverse and not entry.is_dir() and entry.flag_bits&1==0
                and stat.S_IFMT(entry.external_attr>>16)!=stat.S_IFLNK and 0<entry.file_size<=4*1024*1024,'public_json_member')
            expanded+=entry.file_size;require(expanded<=16*1024*1024,'zip_expansion_limit')
            value=archive.read(entry);require(len(value)==entry.file_size,'zip_member_size')
            name=reverse[entry.filename];raw[name]=value;members[name]=entry.filename
    require('ci-stages.json' in raw,'stage_record_required')
    data={n:parse_json(b) for n,b in raw.items()}
    for value in data.values():public_gate(value)
    return raw,data,members


def stages_gate(value,api,backend,source):
    exact(set(value),{'schema','source_commit','run_id','run_attempt','backend','stages'},'stage_fields')
    require(value['schema']=='recastpress.php-wasix-wp-bootstrap-ci-stages.v0.5'
        and value['source_commit']==api['head_sha'] and value['run_id']==str(api['run_id'])
        and value['run_attempt']==str(api['run_attempt']) and value['backend']==backend,'stage_identity')
    names={}
    for block in source.read('.github/workflows/php-wasix-wp-bootstrap.yml').decode().split('      - name: ')[1:]:
        match=re.search(r'^        id: ([a-z_]+)$',block,re.M)
        if match:names[match[1]]=block.splitlines()[0].strip()
    exact(set(names),{'php','deps','parser','packages','tools','module','guest','rust','host','contract'},'workflow_stage_inventory')
    exact(set(value['stages']),set(names),'actual_stage_inventory')
    for key,name in names.items():
        rows=[r for r in api['job']['steps'] if r['name']==name];require(len(rows)==1,'api_stage_step')
        exact(value['stages'][key],rows[0]['conclusion'],'api_vs_stage_conclusion')
    return all(v=='success' for v in value['stages'].values())


def prepared_gate(value,source):
    constants=source.constants(PREFIX+'prepare.py')
    original={n:source.read(n) for n in constants['PINS']}
    for name,wire in original.items():exact((len(wire),sha(wire)),constants['PINS'][name],'parent_whole_file_pin')
    # The known upstream query-validation block is independently pinned here.
    adapter=source.read(PREFIX+'fixtures/adapter.php')
    first=adapter.index(b"\t\t// If we're writing to the database,")
    last=adapter.index(b'        $this->check_current_query = true;',first)
    validation=adapter[first:last];require(sha(validation)==UPSTREAM_VALIDATION_SHA,'upstream_query_validation_block')
    end=b'\t\t$this->check_current_query = true;\n\n\t\t// Keep track of the last query'
    pure=source.functions(PREFIX+'prepare.py',['once','section','expected_fixtures'],constants)
    fixtures,_=pure['expected_fixtures'](original,validation+end)
    for name in constants['FIXTURES']:exact(source.read(PREFIX+'fixtures/'+name),fixtures[name],'whole_derived_fixture')
    tree=source.tree(PREFIX+'prepare.py')
    returns={node.name:node.body[-1].value for node in tree.body if isinstance(node,ast.FunctionDef)
        and node.name in {'metadata','derivation_gate'} and isinstance(node.body[-1],ast.Return)}
    require(set(returns)=={'metadata','derivation_gate'} and all(isinstance(v,ast.Dict) for v in returns.values()),
        'source_preparation_return_shapes')
    fields={ast.literal_eval(k):v for k,v in zip(returns['derivation_gate'].keys,returns['derivation_gate'].values)}
    expected_derivation={'base_commit':constants['BASE_COMMIT'],'original_git_objects_verified_by_preparer':False,
        'parents':{n:identity(w) for n,w in original.items()},'derived':{n:identity(fixtures[n]) for n in constants['FIXTURES']},
        'upstream_query_validation_block_sha256':UPSTREAM_VALIDATION_SHA,'upstream_query_validation_bytes_preserved':True,
        'original_core_theme_plugin_bytes_modified':False,'owned_db_overlay_files':1}
    for key in ['allowed_helper_changes','allowed_adapter_changes','allowed_bootstrap_changes']:
        expected_derivation[key]=ast.literal_eval(fields[key])
    exact(value['derivation'],expected_derivation,'complete_fixture_derivation_report')
    exact(set(value),{ast.literal_eval(k) for k in returns['metadata'].keys},'complete_preparation_fields')
    previous_name='experiments/php-wasix-wpdb-probe/evidence/ci-41550553907-mysql/raw/target/php-wasix-wpdb-probe/prepared.json'
    previous=parse_json(source.read(previous_name));old.prepared_gate(previous,source)
    exact(value['previous_cgi_derivation'],previous['derivation'],'previous_cgi_derivation_unchanged')
    oracles={}
    for backend in ['mysql','mariadb']:
        name='experiments/wp-bootstrap-probe/evidence/windows-'+backend+'-native.json';wire=source.read(name)
        oracles[backend]={'path':name,**identity(wire)}
    exact(value['native_oracles'],oracles,'native_oracle_byte_pins')
    baseline=parse_json(source.read(oracles['mysql']['path']))['preparation_before']
    names=set(previous['input_files'])|set(baseline['input_files'])|set(constants['PINS'])
    names|={PREFIX+'prepare.py',*[PREFIX+'fixtures/'+n for n in constants['FIXTURES']]}
    require(len(names)==30,'thirty_preparation_inputs');exact(value['input_files'],source.hashes(names),'complete_preparation_inventory')
    for key,native_key in [('core_site','site'),('core_manifest','source_manifest'),('core_inventory','source_tree')]:
        exact(value[key],baseline[native_key],'unchanged_full_core_'+key)
    exact(value['core_archive'],{'bytes':baseline['archive_bytes'],'sha256':baseline['archive_sha256']},'full_wp_archive_metadata')
    exact([value[k] for k in ['core_guest_root','private_context_guest_path','core_manifest_guest_path']],
        ['/wordpress','/rp-bootstrap/context.json','/rp-bootstrap/source-manifest.json'],'fixed_guest_mount_labels')
    files=[]
    for name in constants['FIXTURES']:
        files.append({'source':PREFIX+'fixtures/'+name,'guest':'/wordpress/wp-content/db.php' if name=='db.php'
            else '/experiments/php-wasix-wp-bootstrap-probe/fixtures/'+name,**identity(fixtures[name]),
            'guest_read_only':True,'kind':'owned_wpdb_dropin' if name=='db.php' else 'derived_fixture'})
    exact(value['files'],files,'all_six_guest_files_and_single_overlay')
    require(value['schema']=='recastpress.php-wasix-wp-bootstrap-prepared.v0.5'
        and value['outcome']=='full_core_and_owned_db_overlay_prepared' and value['inputs_unchanged'] is True,'prepared_identity')
    for key in ['guest_context_contains_db_credentials','native_reference_binaries_reverified','core_files_modified',
        'secret_context_in_prepared_metadata','php_executed','wasm_executed','database_connected',
        'product_runtime_selected','guest_readonly_enforcement_executed']:
        require(value[key] is False,'prepared_unclaimed_'+key)
    return {'fixture_derivations_verified':True,'preparation_inputs':30,'core_files':3782,'core_directories':467,
        'owned_overlay_files':1,'source_archive_binaries_reverified_offline':False}


def operation_limit(source):
    name=PREFIX+'run_check.py';constants=source.constants(name)
    function=next(node for node in source.tree(name).body if isinstance(node,ast.FunctionDef) and node.name=='validate')
    bounds=[]
    for node in ast.walk(function):
        if not isinstance(node,ast.Compare):continue
        values=[node.left,*node.comparators]
        for index,operator in enumerate(node.ops):
            left,right=values[index:index+2]
            if isinstance(operator,ast.LtE) and isinstance(left,ast.Call) and isinstance(left.func,ast.Name) \
                    and left.func.id=='len' and len(left.args)==1 and isinstance(left.args[0],ast.Name) and left.args[0].id=='ops':
                value=constants.get(right.id) if isinstance(right,ast.Name) else ast.literal_eval(right)
                require(type(value) is int and value in (1024,4096),'closed_source_operation_limit')
                bounds.append(value)
    require(len(bounds)==1,'one_source_operation_limit')
    return bounds[0]


def frozen_checker(source):
    name=PREFIX+'run_check.py';constants=source.constants(name)
    def reader(path,maximum):
        wire=source.read(Path(path).relative_to(ROOT).as_posix());require(len(wire)<=maximum,'native_reference_quota');return wire
    namespace=source.functions(name,['canonical','reference','validate'],{**constants,
        'build':SimpleNamespace(read=reader)})
    namespace['source_operation_limit']=operation_limit(source)
    return namespace


def php_diagnostic(value,source):
    if type(value) is not dict:return {}
    text=source.read(PREFIX+'fixtures/bootstrap.php').decode('utf-8')
    result={}
    for field,constant in [('phase','RP_PUBLIC_PHASES'),('failure','RP_PUBLIC_FAILURE_CODES'),
            ('failure_class','RP_PUBLIC_FAILURE_CLASSES')]:
        match=re.search(r'^const '+constant+r' = (.*);$',text,re.M)
        require(match is not None,'source_public_diagnostic_literal')
        allowed=parse_json(match[1].encode())
        require(type(allowed) is list and all(type(v) is str for v in allowed),'source_diagnostic_literal_shape')
        if field in value:
            require(type(value[field]) is str and value[field] in allowed,'unapproved_public_php_diagnostic')
            result[field]=value[field]
    if 'passed' in value:
        require(type(value['passed']) is bool,'public_php_verdict_type');result['passed']=value['passed']
    return result


def positive_gate(data,raw,source,backend):
    run,host=data['run.json'],data['host-build.json']
    require(run['schema']=='recastpress.php-wasix-wp-bootstrap-check.v0.5' and run['passed'] is True
        and run['outcome']=='fixed_full_wordpress_wasix_database_passed' and run['platform']=='linux'
        and run['backend']==backend and run['source_reverified_after'] is True and run['owned_cleanup_completed'] is True,'positive_run')
    anchor=parse_json(source.read(PREFIX+'evidence/windows-build.json'))
    require(anchor['passed'] is True and anchor['source_reverified_after'] is True,'frozen_host_inventory_anchor')
    exact(run['input_files'],anchor['input_files'],'complete_runtime_source_inventory')
    exact(host['input_files'],anchor['input_files'],'complete_linux_build_inventory')
    exact(run['build_receipt'],host,'same_job_host_receipt')
    require(host['schema']=='recastpress.php-wasix-wp-bootstrap-build.v0.5' and host['passed'] is True
        and host['outcome']=='locked_offline_bootstrap_host_build_passed' and host['locked_offline'] is True
        and host['owned_cleanup_completed'] is True and host['source_reverified_after'] is True,'host_build_success')
    exact(host['driver_before'],host['driver_after'],'driver_stage_unchanged')
    exact(host['units']['tests'],anchor['units']['tests'],'same_frozen_unit_inventory')
    require(host['units']['passed'] is True and len(host['units']['tests'])>=31,'all_actual_host_units')
    stages=[('unit-build',1200),('unit-list',10),('unit-run',60),('normal-build',1200)]
    require(len(host['commands'])==4,'four_host_build_commands')
    for row,(name,deadline) in zip(host['commands'],stages):
        require(row['stage']==name and row['timeout_seconds']==deadline and row['owned_reaped'] is True
            and type(row['exit_code']) is int and row['exit_code']==0,'owned_host_build_stage')
    summary=prepared_gate(data['prepared.json'],source);exact(run['preparation_before'],data['prepared.json'],'same_job_prepared')
    require(run['module_provenance']==run['module_provenance_after'],'module_unchanged')
    managed=old.managed_gate(data,raw,source,run['module_provenance'])
    checker=frozen_checker(source);native=checker['reference'](backend)
    lock=tomllib.loads(source.read(PREFIX+'Cargo.lock').decode('utf-8'))
    json_crates=[p for p in lock['package'] if p['name']=='serde_json']
    require(len(json_crates)==1 and not any(d.startswith('indexmap') for d in json_crates[0].get('dependencies',[])),
        'source_default_sorted_json_map_required')
    exact(set(run['observations']),{'install','read'},'two_fresh_guest_modes')
    for mode in ['install','read']:
        value=run['observations'][mode]
        expected=checker['validate'](value,mode,native[mode]);exact(run['validations'][mode],expected,'source_bound_native_comparison')
        execution=run[mode+'_execution'];require(execution['owned_reaped'] is True and execution['exit_code']==0
            and execution['stderr_bytes']==0 and execution['stderr_sha256']==sha(b''),'owned_mode_execution')
        require(execution['stdout_sha256']==sha(canonical(value)+b'\n'),'public_host_stdout_identity')
        php=value['php_result'];require(php['schema']=='recastpress.php-wasix-wp-bootstrap-php-observation.v0.5'
            and php['outcome']=='fixed_wasix_bootstrap_passed','owned_dropin_php_identity')
        php_diagnostic(php,source)
        for index,op in enumerate(value['operations'],1):
            old.integer(op['seq'],1,checker['source_operation_limit']);require(op['seq']==index and type(op['ok']) is bool,'typed_operation_sequence')
            for key,maximum in [('request_data_bytes',65536),('request_frame_bytes',262144),('reply_frame_bytes',524288)]:old.integer(op[key],0,maximum)
        module=data['build.json']['php_wasm']
        exact(value['module'],{'path':module['path'],'bytes':module['bytes'],'sha256':module['sha256'],'verified':True},'same_job_guest_module')
        exact(value['build_receipt'],{'path':old.MANAGED+'evidence/build.json','sha256':sha(raw['build.json']),
            'verified':True,'full_chain_verified_by_host':False},'same_job_guest_build_receipt')
    for key in ['container_started','container_stopped','container_removed','container_normal_exit_verified',
        'owned_database_identity_verified_before_fixture_ddl','independent_wordpress_final_verified']:
        require(run[key] is True,'normal_container_'+key)
    for key in ['forced_owned_container_cleanup','normal_container_stop_failed','production_database_used']:
        require(run.get(key,False) is False,'container_failure_scope')
    for command in run['commands']:require(command['owned_reaped'] is True,'owned_database_command_reaped')
    pin=parse_json(source.read('experiments/php-wasix-wpdb-probe/container-images.json'))['images'][backend]
    exact(run['image'],pin['repository']+'@'+pin['platform_digest'],'fixed_database_image')
    exact(run['image_config_digest'],pin['image_config_digest'],'database_config_digest')
    require(type(run['sql_server_version']) is str and (run['sql_server_version']==pin['tag']
        or run['sql_server_version'].startswith(pin['tag']+'-')),'actual_database_version')
    return {'both_install_and_read_verified':True,'prepared':summary,'managed':managed,
        'runtime_source_inputs':len(run['input_files']),'host_units':len(host['units']['tests']),
        'source_operation_limit':checker['source_operation_limit'],
        'public_host_json_reserialization_verified':True,'raw_dropin_flag_omitted_by_public_projection':True,
        'dropin_raw_php_assertion_bound_to_frozen_source':True,
        'private_query_reply_cgi_and_nonce_digests_recomputed_offline':False}


def immutable(path,wire):
    require(path.resolve()==path and path.is_relative_to(HERE/'evidence'),'owned_import_path')
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():require(read(path,4*1024*1024)==wire,'immutable_import_changed')
    else:
        with path.open('xb') as stream:stream.write(wire)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True);parser.add_argument('--backend',choices=['mysql','mariadb'],required=True)
    parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    require(re.fullmatch('[1-9][0-9]{0,19}',args.run),'run_id')
    work=ROOT/'target/php-wasix-wp-bootstrap-probe/ci';api_path=work/('api-'+args.run+'-'+args.backend+'.json')
    api_wire=read(api_path,128*1024);api=parse_json(api_wire);api_gate(api,args.run,args.backend);old.privacy_gate(api)
    source=old.GitSource(api['head_sha'])
    require(source.read(LEGACY)==read(ROOT/LEGACY,512*1024),'frozen_shared_verifier_helpers_changed')
    raw,data,members=archive_gate(work/('run-'+args.run+'-'+args.backend+'.zip'),api,args.backend)
    stages=stages_gate(data['ci-stages.json'],api,args.backend,source)
    positive=api['run_conclusion']==api['job']['conclusion']=='success' and stages
    if positive:exact(set(raw),set(member_map(args.backend)),'six_positive_public_records')
    binding_error=None
    try:bindings=sum(old.source_bindings_in_records(source,v) for v in data.values())
    except Exception as error:
        if positive:raise
        bindings=0;binding_error=type(error).__name__
    summary={'overall_positive':False,'raw_verdicts_rewritten':False,'source_binding_entries_checked':bindings,
        'source_binding_verification_passed':binding_error is None,'source_binding_failure_class':binding_error}
    if positive:
        summary.update(positive_gate(data,raw,source,args.backend));summary['overall_positive']=True
    else:
        run=data.get('run.json',{});summary['recorded_outcome']=run.get('outcome');summary['recorded_passed']=run.get('passed')
        summary['recorded_failure_stage']=run.get('failure_stage')
        summary['public_guest_failures']={mode:{k:v[k] for k in ['passed','phase','failure','failure_reason','php_diagnostic'] if k in v}
            for mode,v in run.get('observations',{}).items() if type(v) is dict}
        for mode,value in run.get('observations',{}).items():
            if type(value) is dict:
                php=value.get('php_result')
                if type(php) is not dict:php=value.get('cgi_capture',{}).get('safe_php_observation')
                summary['public_guest_failures'][mode]['php']=php_diagnostic(php,source)
    proof={'schema':'recastpress.php-wasix-wp-bootstrap-ci-import.v0.5','repository':old.REPOSITORY,
        'repository_id':old.REPOSITORY_ID,'run_id':api['run_id'],'run_attempt':api['run_attempt'],'source_commit':source.commit,
        'backend':args.backend,'run_conclusion':api['run_conclusion'],'job':api['job'],'artifact':api['artifact'],
        'api_metadata_sha256':sha(api_wire),'archive_member_map':members,'raw_json':{n:identity(b) for n,b in raw.items()},
        'original_git_sources':{n:identity(b) for n,b in sorted(source.cache.items())},
        'verifier_input_files':{p.relative_to(ROOT).as_posix():sha(read(p,512*1024)) for p in [Path(__file__).resolve(),ROOT/LEGACY]},
        'verification':summary,'offline_nonce_recomputed':False,'private_source_bytes_reconstructed':False,
        'source_tool_module_archive_binaries_reverified':False,'network_used_by_importer':False,
        'php_wasm_or_database_executed_by_importer':False,'product_runtime_selected':False,
        'privacy_exception_scope':'exact public CI workspace only within original clang version; raw bytes unchanged'}
    public_gate(proof);wire=(json.dumps(proof,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode()
    require(len(wire)<=1024*1024,'import_proof_quota')
    if not args.verify_only:
        destination=HERE/('evidence/ci-'+args.run+'-'+args.backend)
        for name,content in raw.items():immutable(destination/'raw'/members[name],content)
        immutable(destination/'api-metadata.json',api_wire);immutable(destination/'provenance.json',wire)
    print('Full WP CI evidence: '+('verified positive' if summary['overall_positive'] else 'preserved failure/partial')+' ('+args.backend+')')
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as error:
        reason=str(error) if isinstance(error,ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}',str(error)) else type(error).__name__
        print('Full WP import rejected: '+reason,file=sys.stderr);sys.exit(1)
