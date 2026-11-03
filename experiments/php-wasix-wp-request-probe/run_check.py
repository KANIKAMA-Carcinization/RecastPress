"""Compare original native theme HTML with php-wasm using a freshly owned DB."""
import argparse
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import owned_probe


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


build=load('rp_wasm_home_build',HERE/'build_check.py')
prepared=load('rp_wasm_home_prepared',HERE/'prepare.py')
linux=load('rp_wasm_home_owned_database',HERE/'linux_database.py')
original=load('rp_wasm_home_original_baseline',ROOT/'experiments/php-wasix-wp-bootstrap-probe/run_check.py')
oldbuild=load('rp_wasm_home_original_build',ROOT/'experiments/php-wasix-wp-bootstrap-probe/build_check.py')
managed=original.managed
require,sha=build.require,build.sha
BASELINES=linux.ORACLES
COMPARE=original.COMPARE+['request_dispatch_completed','template_loader_completed','anonymous_request','theme_rendered','response']


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def reference(backend):
    path=ROOT/('experiments/wp-request-probe/evidence/windows-'+backend+'-native.json')
    wire=build.read(path,1024*1024)
    require(sha(wire)==BASELINES[backend],'fixed_native_home_reference_changed')
    value=json.loads(wire)
    for key in ['passed','native_comparison_only','source_reverified_after','normal_admin_shutdown_verified','all_command_children_reaped']:
        require(value[key] is True,'native_home_reference_'+key)
    return value['observations']['home']


def validate_home(value,mode,native):
    require(value['schema']=='recastpress.php-wasix-wp-request-observation.v0.5'
        and value['mode']==mode and value['passed'] is True
        and value['outcome']=='fixed_wasix_wp_request_passed'
        and value['phase']=='full_home_verified','bootstrap_host_outcome')
    for key in ['physical_connection_created','physical_disconnect_completed','db_actor_joined',
        'db_actor_exited','fresh_runtime','source_rechecked_after_execution','full_wordpress_bootstrap']:
        require(value[key] is True,'host_completion_'+key)
    for key in ['guest_networking','host_environment_forwarded','host_home_mapped',
        'db_credentials_supplied_to_guest','native_php_fallback','product_runtime','standard_wpdb_compatibility_proven']:
        require(value[key] is False,'host_unclaimed_'+key)
    require(value['module_compilations']==1 and value['db_actor_failure'] is None,'one_module_clean_actor')
    capture=value['cgi_capture']
    require(0<capture['stdout_bytes']<=3*1024*1024,'bounded_home_cgi_capture')
    for key in ['guest_exit_success','runtime_shutdown_returned','stdout_eof','stderr_eof']:
        require(capture[key] is True,'capture_completion_'+key)
    require(capture['stdout_overflow'] is False and capture['stderr_overflow'] is False
        and capture['stderr_bytes']==0 and capture['stderr_sha256']==sha(b'')
        and capture['response']['status']==200,'complete_clean_cgi_capture')
    require('stdout_hex' not in capture and 'stderr_hex' not in capture
        and 'body_hex' not in capture['response'],'private_body_excluded')
    php=value['php_result']
    require(php['passed'] is True and php['phase']=='complete' and php['mode']==mode
        and php['rust_executed'] is True and php['wasm_executed'] is True
        and php['native_mysqli_handle'] is False and php['owned_db_dropin_loaded'] is True,
        'php_rust_bootstrap_contract')
    for key in COMPARE:
        require(canonical(php[key])==canonical(native[key]),'native_bootstrap_'+key+'_differs')
    access=value['managed_access']
    require(access['finished'] is True and access['protocol_error'] is None
        and access['pending_bytes']==access['unread_response_bytes']==access['active_handles']==0,
        'managed_access_sealed')
    require(0<access['requests']<=16384 and access['generated_bytes']==access['read_bytes']==access['requests']*5
        and access['open_events']==access['closed_handles'] and access['invalid']==access['io_errors']==0,
        'managed_access_complete_transactions')
    require(sum(access[k] for k in ['allowed','denied','missing','unmanaged','invalid','io_errors'])
        ==access['requests'],'managed_access_status_balance')
    rpc=value['rpc'];ops=value['operations']
    require(rpc['finished'] is True and rpc['opened_once'] is True and rpc['close_dispatched'] is True
        and rpc['failure'] is None and rpc['channel_open'] is False and rpc['response_pending'] is False
        and rpc['pending_bytes']==rpc['unread_response_bytes']==0,'private_channel_sealed')
    require(0<len(ops)<=4096 and len(ops)==rpc['operations'] and ops[0]['op']=='hello'
        and ops[-1]['op']=='close','rpc_sequence_bounds')
    allowed={'seq','op','request_data_bytes','request_data_sha256','request_frame_bytes',
        'request_frame_sha256','reply_frame_bytes','reply_frame_sha256','ok','row_count',
        'column_count','server_error_code','server_error_state_hex'}
    counts=dict.fromkeys(['hello','query','escape','close'],0)
    for seq,op in enumerate(ops,1):
        require(set(op)==allowed and op['seq']==seq and op['op'] in counts,'public_operation_shape')
        counts[op['op']]+=1
        require(type(op['ok']) is bool,'operation_status_type')
        require(op['server_error_code'] is None or type(op['server_error_code']) is int
            and 0<=op['server_error_code']<=65535,'operation_error_code')
        require(op['server_error_state_hex'] is None or re.fullmatch('[0-9a-f]{10}',
            op['server_error_state_hex']) is not None,'operation_error_state')
        require(0<=op['request_data_bytes']<=65536 and 0<op['request_frame_bytes']<=262144
            and 0<op['reply_frame_bytes']<=524288,'operation_frame_bounds')
        for key in ['request_data_sha256','request_frame_sha256','reply_frame_sha256']:
            require(re.fullmatch('[0-9a-f]{64}',op[key]) is not None,'operation_digest_shape')
        require(op['row_count'] is None or 0<=op['row_count']<=256,'operation_row_bounds')
        require(op['column_count'] is None or 0<=op['column_count']<=32,'operation_column_bounds')
    require(counts['hello']==counts['close']==1 and counts['query']>0,'rpc_one_physical_session')
    requested=sum(op['request_frame_bytes'] for op in ops)
    replied=sum(op['reply_frame_bytes'] for op in ops)
    require(requested==rpc['request_bytes']<=8388608
        and replied==rpc['response_bytes']==rpc['response_read_bytes']<=16777216,'rpc_byte_balance')
    return {'fixed_windows_native_public_observations_match':True,'same_job_native_php_executed':False,
        'compared_fields':COMPARE,'operation_counts':counts,
        'largest_row_count':max((op['row_count'] or 0) for op in ops),
        'largest_column_count':max((op['column_count'] or 0) for op in ops),
        'largest_reply_frame_bytes':max(op['reply_frame_bytes'] for op in ops),'private_query_bytes_recomputed_offline':False,
        'private_database_results_published':False}


def execute(binary,environment,payload,report,label):
    pid,result=owned_probe.run_captured_probe([str(binary)],environment,timeout=300,input_bytes=canonical(payload))
    report[label+'_execution']={'owned_pid':pid,'owned_reaped':True,'exit_code':result.returncode,
        'stdout_bytes':len(result.stdout),'stderr_bytes':len(result.stderr),
        'stdout_sha256':sha(result.stdout),'stderr_sha256':sha(result.stderr)}
    require(0<len(result.stdout)<=4*1024*1024 and result.stdout.endswith(b'\n')
        and result.stdout.count(b'\n')==1 and len(result.stderr)<=65536,'host_output_shape')
    value=report['observations'][label]=json.loads(result.stdout)
    require(result.returncode==0 and value['passed'] is True and not result.stderr,'host_request_failed')
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['mysql','mariadb'],default='mysql')
    parser.add_argument('--record',action='store_true');args=parser.parse_args()
    report={'schema':'recastpress.php-wasix-wp-request-check.v0.5','passed':False,'outcome':'not_run',
        'observed_at_utc':datetime.now(timezone.utc).isoformat(),'backend':args.backend,'platform':sys.platform,
        'observations':{},'validations':{},'owned_cleanup_completed':False,'response_normalized':False,
        'product_runtime_selected':False,'http_listener_exercised':False,'standard_wpdb_compatibility_verified':False,
        'memory_cpu_limits_verified_for_this_integration':False,'all_resources_immediately_reclaimed':False}
    passed=False;database=None;stage='source_preparation'
    try:
        require(sys.platform=='linux','linux_actual_php_execution_required')
        before=report['input_files']=build.inputs()
        mapping=report['preparation_before']=prepared.verify_prepared()
        oldmapping=report['bootstrap_preparation_before']=original.prepared.verify_prepared()
        host=report['build_receipt']=build.verify_build()
        oldhost=report['bootstrap_build_receipt']=oldbuild.verify_build()
        environment=build.environment()
        report['module_provenance']=managed.verify_build()
        native_boot=original.reference(args.backend);native_home=reference(args.backend)
        database=linux.Database(args.backend,report)
        with database:
            for mode in ['install','read']:
                stage='php_wasm_'+mode
                value=execute(ROOT/oldhost['executable'],environment,database.request(mode),report,mode)
                report['validations'][mode]=original.validate(value,mode,native_boot[mode])
            stage='fixed_public_date_seed'
            database.seed_public_dates()
            stage='php_wasm_home'
            value=execute(ROOT/host['executable'],environment,database.home_request(),report,'home')
            report['validations']['home']=validate_home(value,'home',native_home)
            stage='independent_database_verification'
            database.final_rows()
        require(report['container_stopped'] and report['container_removed']
            and report['container_normal_exit_verified'],'normal_container_cleanup_required')
        stage='after_verification'
        require(build.inputs()==before and prepared.verify_prepared()==mapping
            and original.prepared.verify_prepared()==oldmapping,'integration_sources_changed')
        require(build.verify_build()==host and oldbuild.verify_build()==oldhost,'integration_binaries_or_builds_changed')
        report['module_provenance_after']=managed.verify_build()
        require(report['module_provenance_after']==report['module_provenance'],'module_changed_after_execution')
        report['source_reverified_after']=True;passed=True
    except (Exception,KeyboardInterrupt) as error:
        report.update(failure_stage=stage,failure_class=type(error).__name__)
        if isinstance(error,ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}',str(error)):
            report['failure']=str(error)
    finally:
        try:
            owned_probe.reap_owned_probes();report['owned_cleanup_completed']=True
        except (Exception,KeyboardInterrupt):
            passed=False;report['cleanup_failed']=True
    report['passed']=passed
    report['outcome']='fixed_full_wordpress_wasix_home_passed' if passed else 'full_wordpress_wasix_home_failed'
    wire=(json.dumps(report,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode()
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire,'public_secret_guard')
    require(len(wire)<=8*1024*1024,'public_report_quota')
    if args.record:build.preserve(HERE/('evidence/linux-'+args.backend+'.json'),wire)
    print(report['outcome']);return 0 if passed else 1


if __name__=='__main__':sys.exit(main())
