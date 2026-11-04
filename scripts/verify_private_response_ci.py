"""Import a successful public codec CI record; never execute the codec or PHP.

API metadata and ZIP must already exist in the experiment's ignored ci folder.
Original compiler/executable binaries and private input are not reconstructed.
"""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import re
import stat
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PREFIX='experiments/wp-private-response-probe/'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


check=load('rp_private_response_check',ROOT / (PREFIX+'check.py'))
legacy=load('rp_private_response_git_verifier',ROOT/'experiments/php-wasix-wpdb-probe/import_evidence.py')
require,read,sha,identity=check.require,check.read,check.sha,check.identity


def exact(value,expected,code):
    equal=(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)==
        json.dumps(expected,sort_keys=True,separators=(',',':'),allow_nan=False)
        if type(expected) in (dict,list) else value==expected)
    require(type(value) is type(expected) and equal,code)


def metric(item,limit):
    exact(set(item),{'bytes','sha256'},'closed_byte_identity')
    require(type(item['bytes']) is int and 0<=item['bytes']<=limit and type(item['sha256']) is str
        and re.fullmatch('[0-9a-f]{64}',item['sha256']) is not None,'bounded_byte_identity')


def validate_report(value,source):
    exact(set(value),{'schema','passed','observed_at_utc','commands','native_comparison_in_codec',
        'publication_authorized','product_runtime_selected','php_wasm_database_or_http_executed',
        'source_reverified_after','owned_cleanup_completed','locked_offline','input_files',
        'dependency_gate','units','executable','public_fixture_cases','private_cli_cases','outcome'},
        'closed_linux_codec_record')
    exact(value['schema'],'recastpress.wp-private-response-check.v0.5','codec_schema')
    exact(value['outcome'],'private_response_codec_passed','codec_outcome')
    for key in ('passed','source_reverified_after','owned_cleanup_completed','locked_offline'):
        exact(value[key],True,'codec_completion_flag')
    for key in ('native_comparison_in_codec','publication_authorized','product_runtime_selected',
            'php_wasm_database_or_http_executed'):
        exact(value[key],False,'codec_unclaimed_scope')
    require(type(value['observed_at_utc']) is str and re.fullmatch(
        r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+\+00:00',value['observed_at_utc']) is not None,'codec_observation_time')
    names=set(check.inputs())
    exact(set(value['input_files']),names,'exact_codec_source_inputs')
    for name in sorted(names):
        wire=source.read(name)
        exact(sha(wire),value['input_files'][name],'original_git_source_identity')
        exact(wire,read(ROOT/name),'current_codec_verifier_inputs_unchanged')
    exact(value['dependency_gate'],check.dependency_gate(),'codec_dependency_versions_checksums')
    expected=[]
    for label,response in check.fixture_cases():
        expected.append({'case':label,'input':identity(json.dumps(response,separators=(',',':')).encode()),
            'summary':{'status':response['status'],'header_count':len(response['headers_hex']),
                'body_bytes':response['body_bytes'],'body_sha256':response['body_sha256'],
                'representation_length':None}})
    exact(value['public_fixture_cases'],expected,'exact_twelve_codec_fixture_summaries')
    private=[{'case':label,'input':identity(payload),'output':output}
        for label,payload,code,output in check.private_cli_cases()]
    exact(value['private_cli_cases'],private,'exact_private_diagnostics_summaries')
    exact(set(value['units']),{'path','identity','tests','passed'},'closed_codec_units')
    exact(value['units']['tests'],check.TESTS,'sixteen_codec_units')
    exact(value['units']['passed'],True,'codec_units_passed')
    for key in ('units','executable'):
        item=value[key]
        if key=='executable': exact(set(item),{'path','identity'},'closed_codec_executable')
        pattern=(r'target/wp-private-response-probe/cargo-target/release/deps/'
            r'recastpress_wp_private_response_probe-[a-f0-9]{16}' if key=='units' else
            r'target/wp-private-response-probe/cargo-target/release/recastpress-wp-private-response-probe')
        require(type(item['path']) is str and re.fullmatch(pattern,item['path']) is not None,'fixed_linux_codec_binary_path')
        metric(item['identity'],128*1024*1024)
    stages=['unit-build','unit-list','unit-run','codec-build']+[x['case'] for x in expected+private]
    scope={k:v for k,v in check.private_cli_cases()[0][3].items() if k not in ('passed','summary')}
    outputs={x['case']:{**scope,'passed':True,'summary':x['summary']} for x in expected}
    outputs.update({x['case']:x['output'] for x in private})
    exact(len(value['commands']),len(stages),'nineteen_owned_codec_stages')
    for row,stage in zip(value['commands'],stages):
        exact(set(row),{'stage','owned_pid','owned_reaped','exit_code','timeout_seconds','stdout','stderr'},'closed_codec_command')
        exact(row['stage'],stage,'codec_command_order'); exact(row['owned_reaped'],True,'codec_owned_reap')
        require(type(row['owned_pid']) is int and row['owned_pid']>0,'codec_owned_pid')
        exact(row['exit_code'],1 if stage in ('private-malformed','private-duplicate') else 0,'codec_expected_exit')
        exact(row['timeout_seconds'],600 if stage in ('unit-build','codec-build') else 60 if stage=='unit-run' else 10,
            'codec_expected_deadline')
        metric(row['stdout'],8*1024*1024); metric(row['stderr'],65536)
        if stage not in ('unit-build','codec-build'):
            exact(row['stderr'],identity(b''),'empty_noncompiler_stderr')
        if stage in outputs:
            expected_wire=(json.dumps(outputs[stage],sort_keys=True,separators=(',',':'))+'\n').encode()
            exact(row['stdout'],identity(expected_wire),'source_bound_safe_cli_output_bytes')


def immutable(path,wire):
    require(path.resolve()==path,'fixed_evidence_path')
    if path.exists(): exact(read(path),wire,'existing_evidence_differs')
    else: path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(wire)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True); parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args(); require(re.fullmatch('[1-9][0-9]{0,19}',args.run) is not None,'run_id')
    work=ROOT/'target/wp-private-response-probe/ci'
    api_wire=read(work/f'api-{args.run}.json',65536); api=legacy.parse_json(api_wire)
    exact(set(api),{'repository_id','repository','run_id','run_attempt','head_sha','run_conclusion','job','artifact'},'closed_api_metadata')
    exact(api['repository_id'],1432882501,'repository_id'); exact(api['repository'],'KANIKAMA-Carcinization/RecastPress','repository')
    exact(api['run_id'],int(args.run),'api_run_id'); exact(api['run_conclusion'],'success','successful_ci_run')
    require(type(api['run_attempt']) is int and api['run_attempt']>=1,'run_attempt')
    job,artifact=api['job'],api['artifact']
    for value in (job.get('id'),artifact.get('id')):
        require(type(value) is int and value>0,'positive_api_identity')
    exact(set(job),{'id','name','conclusion','steps'},'closed_api_job')
    exact(job['name'],'codec','codec_job'); exact(job['conclusion'],'success','successful_codec_job')
    required={'Install fixed Rust toolchain','Fetch locked compiler dependencies',
        'Verify private codec units and immutable response corpus','Upload public summary evidence'}
    require(required <= {r['name'] for r in job['steps']},'required_codec_ci_steps')
    for row in job['steps']:
        exact(set(row),{'name','conclusion'},'closed_api_step'); exact(row['conclusion'],'success','successful_codec_step')
    exact(set(artifact),{'id','name','size_in_bytes','digest'},'closed_api_artifact')
    exact(artifact['name'],f"wp-private-response-{api['run_attempt']}",'codec_artifact_name')
    require(type(artifact['size_in_bytes']) is int and 0<artifact['size_in_bytes']<=1024*1024,'codec_archive_limit')
    wire=read(work/f'run-{args.run}.zip',1024*1024)
    exact(len(wire),artifact['size_in_bytes'],'api_zip_size'); exact('sha256:'+sha(wire),artifact['digest'],'api_zip_sha')
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        rows=archive.infolist(); require(len(rows)==1,'one_codec_json_member'); row=rows[0]
        require(row.filename=='linux.json' and not row.is_dir() and row.flag_bits&1==0
            and stat.S_IFMT(row.external_attr>>16)!=stat.S_IFLNK and 0<row.file_size<=1024*1024,'fixed_codec_json_member')
        raw=archive.read(row); exact(len(raw),row.file_size,'codec_json_expanded_size')
    source=legacy.GitSource(api['head_sha'])
    value=legacy.parse_json(raw); validate_report(value,source)
    legacy.privacy_gate(value); legacy.privacy_gate(api)
    proof={'schema':'recastpress.wp-private-response-ci-import.v0.5','verification_revision':2,'run_id':api['run_id'],
        'source_commit':source.commit,'overall_positive':True,'api_metadata':identity(api_wire),
        'archive':identity(wire),'raw_json':identity(raw),'original_git_sources':
            {n:identity(b) for n,b in sorted(source.cache.items())},
        'source_input_count':len(value['input_files']),'units':len(check.TESTS),
        'public_fixture_cases':len(value['public_fixture_cases']),'private_cli_cases':len(value['private_cli_cases']),
        'verifier_inputs':{p.relative_to(ROOT).as_posix():sha(read(p)) for p in
            (Path(__file__).resolve(),ROOT/(PREFIX+'check.py'),ROOT/'experiments/php-wasix-wpdb-probe/import_evidence.py')},
        'runtime_or_database_executed_by_verifier':False,'ci_binaries_reverified_offline':False,
        'unknown_response_publication_authorized':False,'raw_evidence_rewritten':False}
    legacy.privacy_gate(proof)
    destination=ROOT/(PREFIX+f'evidence/ci-{args.run}')
    if not args.verify_only:
        immutable(destination/'linux.json',raw); immutable(destination/'api-metadata.json',api_wire)
        immutable(destination/'provenance-v2.json',(json.dumps(proof,indent=2)+'\n').encode())
    print('private_response_ci_verified_positive')


if __name__=='__main__':
    try: main()
    except (Exception,KeyboardInterrupt) as error:
        reason=str(error) if isinstance(error,ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}',str(error)) else type(error).__name__
        print('private_response_ci_rejected: '+reason,file=__import__('sys').stderr)
        raise SystemExit(1)
