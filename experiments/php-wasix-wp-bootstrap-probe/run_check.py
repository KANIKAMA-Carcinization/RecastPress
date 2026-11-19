"""Run full unchanged WordPress through PHP WASIX and an owned Rust DB actor."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import owned_probe


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


build=load('rp_full_bootstrap_build',HERE/'build_check.py')
prepared=load('rp_full_bootstrap_prepared',HERE/'prepare.py')
linux=load('rp_full_bootstrap_database',HERE/'linux_database.py')
managed=load('rp_full_bootstrap_module',ROOT/'experiments/php-wasix-managed-access-probe/managed_check.py')
require,sha=build.require,build.sha
BASELINES={'mysql':'61c331e5e9f316dd6e2f241a54f1de337bbff397d405c6a5f4ae55cc49dc2c91',
    'mariadb':'af07c9ff931f87b2f8d235423fcf5db0101ffc1402904b899c6294d9c6f35f2f'}
COMPARE=['bootstrap_completed','install_completed','physical_disconnect_completed','source_inventory_verified',
    'core_files_unchanged','blocked_mail_calls','blocked_http_calls','warning_count','shortinit_false',
    'wp_installing','wp_version','public_options','public_posts','known_wp_table_count',
    'autoload_option_count','autoload_max_value_bytes','site_config_file_created',
    'source_core_theme_plugin_modified','outbound_mail_or_http_sent_by_fixture']
HOOK_TRACE_FIELDS=['callback','value','current_filter','doing_filter','did_filter','current_priority']
HOOK_RESET_FIELDS=['current_filter','doing_filter','doing_selected_filter','did_filter','stack']
HOOK_ORACLE_SCHEMA='recastpress.wp-bootstrap-hook-native-oracle.v0.1'
HOOK_PHP_FILES=['fixtures/hook_adapter.php','fixtures/hook_native_oracle.php']


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def reference(backend):
    path=ROOT/('experiments/wp-bootstrap-probe/evidence/windows-'+backend+'-native.json')
    wire=build.read(path,256*1024)
    require(sha(wire)==BASELINES[backend],'fixed_native_bootstrap_reference_changed')
    value=json.loads(wire)
    require(value['passed'] is True and value['full_wordpress_bootstrap_verified'] is True
        and value['normal_admin_shutdown_verified'] is True,'native_bootstrap_reference_failed')
    return value['observations']


def native_hook_oracle():
    path=HERE/'fixtures/hook_native_oracle.php'
    result=subprocess.run(['php','-n',str(path)],cwd=ROOT,stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
    require(result.returncode==0 and not result.stderr and 0<len(result.stdout)<=8192
        and result.stdout.endswith(b'\n') and result.stdout.count(b'\n')==1,
        'native_hook_oracle_execution_failed')
    value=json.loads(result.stdout)
    require(value.get('schema')==HOOK_ORACLE_SCHEMA, 'native_hook_oracle_schema')
    if value.get('status')!='passed':
        failure=value.get('failure')
        allowed={'cli_entry_guard','fixed_wordpress_tree_missing','pinned_wordpress_source_mismatch',
            'pinned_wordpress_version_missing','pinned_wordpress_version_mismatch',
            'native_callback_table_mismatch','native_dispatch_incomplete','native_callback_order_mismatch',
            'native_filter_state_mismatch','native_filter_value_mismatch','native_filter_reset_mismatch',
            'selected_hook_missing','result_size_limit','native_oracle_execution_failed'}
        require(failure in allowed, 'native_hook_oracle_failed')
        require(False, 'native_hook_php_'+failure)
    require(value.get('wordpress_version')=='7.1.3', 'native_hook_oracle_wordpress_version')
    require(value.get('scenario')=='option_blogname', 'native_hook_oracle_scenario')
    require(value.get('event_order')==['rp_boot_hook_first','rp_boot_hook_second'],
        'native_hook_oracle_event_order')
    require(value.get('filtered_value')=='RP Bootstrap Fixture|first|second',
        'native_hook_oracle_filtered_value')
    return value,{'exit_code':result.returncode,'stdout_bytes':len(result.stdout),
        'stdout_sha256':sha(result.stdout),'stderr_bytes':len(result.stderr),
        'php_cli_mode':'-n'}


def lint_hook_fixtures():
    results=[]
    for relative in HOOK_PHP_FILES:
        path=HERE/relative
        result=subprocess.run(['php','-n','-l',str(path)],cwd=ROOT,stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
        require(result.returncode==0 and not result.stderr and 0<len(result.stdout)<=4096
            and result.stdout.count(b'\n')==1,'hook_php_lint_failed')
        results.append({'path':path.relative_to(ROOT).as_posix(),'exit_code':result.returncode,
            'stdout_bytes':len(result.stdout),'stdout_sha256':sha(result.stdout),'stderr_bytes':0})
    return results


def normalize_hook_trace(events,did_filter_baseline):
    require(isinstance(did_filter_baseline,int) and not isinstance(did_filter_baseline,bool)
        and 0<=did_filter_baseline<=4096,'hook_did_filter_baseline_shape')
    require(isinstance(events,list) and len(events)==2,'hook_trace_shape')
    normalized=[]
    for event in events:
        require(isinstance(event,dict) and set(HOOK_TRACE_FIELDS)<=set(event),'hook_trace_event_shape')
        require(isinstance(event['did_filter'],int) and not isinstance(event['did_filter'],bool)
            and event['did_filter']==did_filter_baseline+1,'hook_trace_did_filter_count')
        normalized.append({key:(event[key]-did_filter_baseline if key=='did_filter' else event[key])
            for key in HOOK_TRACE_FIELDS})
    return normalized


def compare_hook_oracle(php,native_hook):
    hook=php.get('hook_dispatch')
    require(isinstance(hook,dict) and hook.get('hook')=='option_blogname'
        and hook.get('value')=='RP Bootstrap Fixture|first|second'
        and hook.get('value')==native_hook['filtered_value'], 'wasm_hook_result')
    baseline=hook.get('did_filter_before')
    require(isinstance(baseline,int) and not isinstance(baseline,bool) and 0<=baseline<=4096,
        'wasm_hook_did_filter_baseline')
    expected_trace=normalize_hook_trace(native_hook['events'],0)
    # public_hook_dispatch has already validated raw baseline+1 counts and\n    # normalized the public trace to one. Do not subtract the retained baseline\n    # a second time here.\n    actual_trace=normalize_hook_trace(hook.get('events'),0)
    require(actual_trace==expected_trace,'native_wasm_hook_trace_differs')
    require(hook.get('invocations')==native_hook['event_order']
        and [event['callback'] for event in actual_trace]==native_hook['event_order'],
        'native_wasm_hook_order_differs')
    native_reset=native_hook['after_dispatch']
    wasm_reset=hook.get('after_dispatch')
    wasm_after_count=wasm_reset.get('did_filter') if isinstance(wasm_reset,dict) else None
    require(isinstance(wasm_after_count,int) and not isinstance(wasm_after_count,bool)
        and wasm_after_count==baseline+1,'wasm_hook_after_did_filter_count')
    normalized_wasm_reset=dict(wasm_reset)
    normalized_wasm_reset['did_filter']=wasm_after_count-baseline
    require(native_reset.get('current_filter') is False
        and normalized_wasm_reset.get('current_filter') is False,
        'native_wasm_hook_current_filter_reset')
    require(isinstance(wasm_reset,dict)
        and {key:normalized_wasm_reset.get(key) for key in HOOK_RESET_FIELDS}
            =={key:native_reset.get(key) for key in HOOK_RESET_FIELDS}
        and wasm_reset.get('current_priority') is False
        and native_reset['hook_state']['current_priority']==[],
        'native_wasm_hook_reset_differs')
    return {'native_hook_oracle_match':True,'hook':'option_blogname',
        'native_oracle_identity':{'schema':native_hook['schema'],
            'wordpress_version':native_hook['wordpress_version'],
            'wordpress_source_sha256':native_hook['wordpress_source_sha256'],
            'scenario':native_hook['scenario'],'output_sha256':sha(canonical(native_hook))},
        'event_order':native_hook['event_order'],'filtered_value':native_hook['filtered_value'],
        'normalization':{'trace_fields':HOOK_TRACE_FIELDS,'reset_fields':HOOK_RESET_FIELDS,
            'wasm_did_filter_baseline':baseline,'native_did_filter_baseline':0,
            'normalization_applied_by':'rust_public_hook_dispatch',\n            'normalized_did_filter_count':1,
            'native_trace_sha256':sha(canonical(expected_trace)),
            'wasm_trace_sha256':sha(canonical(actual_trace)),
            'native_reset_did_filter':native_reset.get('did_filter'),
            'wasm_reset_did_filter':wasm_after_count,
            'normalized_reset_did_filter':normalized_wasm_reset['did_filter'],
            'trace_equal':True,'reset_equal':True,'current_filter_reset_false':True}}


def validate(value,mode,native,native_hook):
    require(value['schema']=='recastpress.php-wasix-wp-bootstrap-observation.v0.5'
        and value['mode']==mode and value['passed'] is True
        and value['outcome']=='fixed_wasix_wp_bootstrap_passed'
        and value['phase']=='full_bootstrap_verified','bootstrap_host_outcome')
    for key in ['physical_connection_created','physical_disconnect_completed','db_actor_joined',
        'db_actor_exited','fresh_runtime','source_rechecked_after_execution','full_wordpress_bootstrap']:
        require(value[key] is True,'host_completion_'+key)
    for key in ['guest_networking','host_environment_forwarded','host_home_mapped',
        'db_credentials_supplied_to_guest','native_php_fallback','product_runtime','standard_wpdb_compatibility_proven']:
        require(value[key] is False,'host_unclaimed_'+key)
    require(value['module_compilations']==1 and value['db_actor_failure'] is None,'one_module_clean_actor')
    capture=value['cgi_capture']
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
    hook_validation=compare_hook_oracle(php,native_hook)
    for key in COMPARE:
        if key=='public_options':
            require(isinstance(php[key],dict) and isinstance(native[key],dict)
                and php[key].get('blogname')==native_hook['filtered_value'],
                'native_bootstrap_blogname_hook_result')
            normalized_options=dict(php[key])
            normalized_options['blogname']=native[key]['blogname']
            require(canonical(normalized_options)==canonical(native[key]),
                'native_bootstrap_public_options_differs')
        else:
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
    hook_rpc=value['hook_rpc']
    require(hook_rpc['finished'] is True and hook_rpc['failure'] is None
        and hook_rpc['opened_once'] is True and hook_rpc['close_dispatched'] is True
        and hook_rpc['dispatcher_complete'] is True and hook_rpc['complete_sent'] is True
        and hook_rpc['channel_open'] is False
        and hook_rpc['invoked_callbacks']==['rp_boot_hook_first','rp_boot_hook_second']
        and hook_rpc['live_handles']==0
        and hook_rpc['pending_bytes']==hook_rpc['unread_response_bytes']==0
        and 0<hook_rpc['operations']<=12
        and 0<hook_rpc['request_bytes']<=262144
        and hook_rpc['response_bytes']==hook_rpc['response_read_bytes']
        and 0<hook_rpc['response_bytes']<=262144,
        'hook_rpc_lifecycle')
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
    return {'fixed_windows_native_public_observations_match':True,'same_job_native_php_executed':True,
        'native_wasm_hook_oracle':hook_validation,'compared_fields':COMPARE,
        'public_options_blogname_checked_by_hook_oracle':True,'operation_counts':counts,
        'largest_row_count':max((op['row_count'] or 0) for op in ops),
        'largest_column_count':max((op['column_count'] or 0) for op in ops),
        'largest_reply_frame_bytes':max(op['reply_frame_bytes'] for op in ops),'private_query_bytes_recomputed_offline':False,
        'private_database_results_published':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['mysql','mariadb'],default='mysql')
    parser.add_argument('--record',action='store_true')
    args=parser.parse_args()
    report={'schema':'recastpress.php-wasix-wp-bootstrap-check.v0.5','passed':False,'outcome':'not_run',
        'observed_at_utc':datetime.now(timezone.utc).isoformat(),'backend':args.backend,'platform':sys.platform,
        'product_runtime_selected':False,'standard_wpdb_compatibility_verified':False,
        'memory_cpu_limits_verified_for_this_integration':False,'all_resources_immediately_reclaimed':False,
        'observations':{},'validations':{},'owned_cleanup_completed':False}
    passed=False;database=None;stage='source_preparation'
    try:
        require(sys.platform=='linux','linux_actual_php_execution_required')
        before=report['input_files']=build.inputs()
        mapping=report['preparation_before']=prepared.verify_prepared()
        receipt=build.verify_build()
        report['build_receipt']=receipt
        binary=ROOT/receipt['executable']
        environment=build.environment()
        report['module_provenance']=managed.verify_build()
        native=reference(args.backend)
        stage='hook_php_lint'
        report['hook_php_lint']=lint_hook_fixtures()
        stage='native_hook_oracle'
        native_hook,native_hook_execution=native_hook_oracle()
        report['native_hook_oracle']=native_hook
        report['native_hook_oracle_execution']=native_hook_execution
        database=linux.Database(args.backend,report)
        with database:
            for mode in ['install','read']:
                stage='php_wasm_'+mode
                pid,result=owned_probe.run_captured_probe([str(binary)],environment,timeout=300,
                    input_bytes=canonical(database.request(mode)))
                report[mode+'_execution']={'owned_pid':pid,'owned_reaped':True,'exit_code':result.returncode,
                    'stdout_bytes':len(result.stdout),'stderr_bytes':len(result.stderr),
                    'stdout_sha256':sha(result.stdout),'stderr_sha256':sha(result.stderr)}
                require(0<len(result.stdout)<=2*1024*1024 and result.stdout.endswith(b'\n')
                    and result.stdout.count(b'\n')==1 and len(result.stderr)<=65536,'host_output_shape')
                value=report['observations'][mode]=json.loads(result.stdout)
                require(result.returncode==0 and value['passed'] is True and not result.stderr,'host_bootstrap_failed')
                report['validations'][mode]=validate(value,mode,native[mode],native_hook)
            stage='independent_database_verification'
            database.final_rows()
        require(report['container_stopped'] and report['container_removed']
            and report['container_normal_exit_verified'],'normal_container_cleanup_required')
        stage='after_verification'
        require(build.inputs()==before and prepared.verify_prepared()==mapping,'integration_sources_changed')
        require(build.verify_build()==receipt,'integration_binary_or_build_changed')
        report['module_provenance_after']=managed.verify_build()
        require(report['module_provenance_after']==report['module_provenance'],'module_changed_after_execution')
        report['source_reverified_after']=True
        passed=True
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
    report['outcome']='fixed_full_wordpress_wasix_database_passed' if passed else 'full_wordpress_wasix_database_failed'
    wire=(json.dumps(report,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode()
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire,'public_secret_guard')
    require(len(wire)<=4*1024*1024,'public_report_quota')
    if args.record:build.preserve(HERE/('evidence/linux-'+args.backend+'.json'),wire)
    print(report['outcome'])
    return 0 if passed else 1


if __name__=='__main__':sys.exit(main())
