"""Compare private worker responses before allowing them into public evidence.

The worker has no WordPress native response oracle. This controller retains the
finite accepted native contract and never records an unaccepted private body.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
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
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


context=load('rp_private_worker_frozen_context_controller',ROOT/'experiments/php-wasix-wp-request-context-probe/run_check.py')
build=load('rp_private_worker_build',HERE/'build_check.py')
require,sha=context.require,context.sha
canonical=context.canonical
FIELDS=context.CONTEXT_FIELDS
SCHEMA='recastpress.php-wasix-wp-private-worker-observation.v0.5'
INPUT='recastpress.php-wasix-wp-private-worker-input.v0.5'


def strict_json(wire):
    def pairs(items):
        value={}
        for key,item in items:
            require(key not in value,'duplicate_private_worker_json_key')
            value[key]=item
        return value
    return json.loads(wire,object_pairs_hook=pairs,parse_constant=lambda _:require(False,'nonfinite_private_worker_json'))


def validate_private(value,case,native):
    require(value['schema']==SCHEMA and value['passed'] is True
        and value['outcome']=='fixed_private_wp_worker_passed'
        and value['phase']=='full_private_response_verified','private_worker_outcome')
    require(value['private_ipc_only'] is True and value['publication_authorized'] is False
        and value['native_comparison_performed'] is False,'private_worker_scope')
    # This check precedes all publication of the private fields, including on
    # failure. Structural codec success alone never authorizes public evidence.
    response=value['private_response']; contexts=value['private_contexts']
    require(canonical(response)==canonical(native['response']),'private_response_native_difference')
    require(type(contexts) is dict and set(contexts)==set(FIELDS),'closed_private_contexts')
    for key in FIELDS:
        require(canonical(contexts[key])==canonical(native[key]),'private_context_native_difference')
    summary={'status':response['status'],'header_count':len(response['headers_hex']),
        'body_bytes':response['body_bytes'],'body_sha256':response['body_sha256'],'representation_length':None}
    require(canonical(value['php_result']['response_summary'])==canonical(summary),'private_worker_actual_codec_summary')
    require(not any(k in value['php_result'] for k in ['response',*FIELDS]),'private_bytes_not_in_diagnostics')
    capture_php=value['cgi_capture'].get('safe_php_observation',{})
    require(not any(k in capture_php for k in ['response',*FIELDS]),'private_bytes_not_in_capture_diagnostics')
    # Adapt only the envelope names and the explicitly separated private fields
    # for the unchanged finite lifecycle/RPC/access/native comparison validator.
    # The original worker record is retained unchanged after this gate passes.
    view=copy.deepcopy(value)
    view.update(schema='recastpress.php-wasix-wp-request-context-observation.v0.5',
        outcome='fixed_wasix_wp_request_context_passed',phase='full_context_verified')
    view.pop('private_response'); view.pop('private_contexts')
    view['php_result'].update(contexts); view['php_result']['response']=response
    result=context.validate_context(view,case,native)
    result.update(native_comparison_in_controller_only=True,worker_native_comparison_performed=False,
        private_response_and_contexts_checked_before_evidence=True,
        legacy_validator_envelope_adapter={'schema':SCHEMA,'outcome':'fixed_private_wp_worker_passed',
            'phase':'full_private_response_verified','existing_lifecycle_rpc_and_native_checks_retained':True})
    return result


def publish_verified(value,case,native,report,label):
    validation=validate_private(value,case,native)
    projection=public_projection(value)
    validate_private(projection,case,native)
    report['observations'][label]=projection
    report['validations'][label]=validation


def public_projection(value):
    """Select verified observations, never republish the private IPC wholesale."""
    def pick(source,names): return {key:copy.deepcopy(source[key]) for key in names}
    def integers(source,names,maximum):
        for key in names:
            require(type(source[key]) is int and 0<=source[key]<=maximum,'public_metric_integer')
    def digest(value):
        require(type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None,'public_metric_digest')
    top=['schema','passed','outcome','phase','mode','case','module_compilations','db_actor_failure',
        'physical_connection_created','physical_disconnect_completed','db_actor_joined','db_actor_exited',
        'fresh_runtime','source_rechecked_after_execution','full_wordpress_bootstrap','guest_networking',
        'host_environment_forwarded','host_home_mapped','db_credentials_supplied_to_guest','native_php_fallback',
        'product_runtime','standard_wpdb_compatibility_proven','private_ipc_only','publication_authorized',
        'native_comparison_performed','private_response','private_contexts']
    result=pick(value,top)
    integers(result,['module_compilations'],1)
    php_keys=sorted((set(context.COMPARE)-{'response',*FIELDS})|{'schema','outcome','passed','phase','mode',
        'rust_executed','wasm_executed','native_mysqli_handle','owned_db_dropin_loaded','response_summary'})
    result['php_result']=pick(value['php_result'],php_keys)
    rpc_keys=['finished','opened_once','close_dispatched','failure','channel_open','response_pending',
        'pending_bytes','unread_response_bytes','operations','request_bytes','response_bytes','response_read_bytes']
    result['rpc']=pick(value['rpc'],rpc_keys)
    integers(result['rpc'],['pending_bytes','unread_response_bytes','operations','request_bytes',
        'response_bytes','response_read_bytes'],16777216)
    # validate_private already requires this entire fixed operation shape and
    # its closed op/error-state strings; no unknown field is copied from a row.
    op_keys=['seq','op','request_data_bytes','request_data_sha256','request_frame_bytes',
        'request_frame_sha256','reply_frame_bytes','reply_frame_sha256','ok','row_count',
        'column_count','server_error_code','server_error_state_hex']
    result['operations']=[pick(row,op_keys) for row in value['operations']]
    access_keys=['finished','protocol_error','pending_bytes','unread_response_bytes','active_handles',
        'requests','generated_bytes','read_bytes','open_events','closed_handles','invalid','io_errors',
        'allowed','denied','missing','unmanaged']
    result['managed_access']=pick(value['managed_access'],access_keys)
    integers(result['managed_access'],[k for k in access_keys if k not in ('finished','protocol_error')],16777216)
    capture_keys=['guest_exit_success','runtime_shutdown_returned','stdout_eof','stderr_eof',
        'stdout_overflow','stderr_overflow','stdout_bytes','stderr_bytes','stdout_sha256','stderr_sha256']
    capture=result['cgi_capture']=pick(value['cgi_capture'],capture_keys)
    integers(capture,['stdout_bytes','stderr_bytes'],3*1024*1024)
    for key in ('stdout_sha256','stderr_sha256'): digest(capture[key])
    response=capture['response']=pick(value['cgi_capture']['response'],
        ['status','header_count','body_bytes','body_sha256','representation_length'])
    integers(response,['status','header_count','body_bytes'],3*1024*1024)
    require(response['status']==200 and response['header_count']<=64
        and response['representation_length'] is None,'public_outer_response_metrics')
    digest(response['body_sha256'])
    prepared=pick(value['prepared'],['verification_scope','transitive_inputs_verified_by_worker',
        'source_verified','core_files','owned_dropin_files'])
    require(canonical(prepared)==canonical({'verification_scope':'compiled_fixtures_and_core_only',
        'transitive_inputs_verified_by_worker':False,'source_verified':True,'core_files':3782,
        'owned_dropin_files':1}),'private_worker_prepared_scope')
    result['prepared']=prepared
    result.update(worker_fields_projected=True,original_worker_json_published=False)
    return result


def execute_worker(binary,environment,payload,report,label,native,case):
    pid,result=owned_probe.run_captured_probe([str(binary)],environment,timeout=300,input_bytes=canonical(payload))
    report.setdefault('executions',{})[label]={'owned_pid':pid,'owned_reaped':True,
        'exit_code':result.returncode,'stdout_bytes':len(result.stdout),'stderr_bytes':len(result.stderr),
        'stdout_sha256':sha(result.stdout),'stderr_sha256':sha(result.stderr)}
    require(0<len(result.stdout)<=4*1024*1024 and result.stdout.endswith(b'\n')
        and result.stdout.count(b'\n')==1 and len(result.stderr)<=65536,'private_worker_capture_shape')
    value=strict_json(result.stdout)
    if type(value) is dict:
        safe={'passed':value.get('passed') is True,'private_response_received':'private_response' in value,
            'private_contexts_received':'private_contexts' in value}
        if value.get('phase') in ('input','module_compile','cgi_execution','full_private_response_verified'):
            safe['phase']=value['phase']
        code=value.get('failure_reason')
        if type(code) is str and re.fullmatch('[a-z_]{1,80}',code): safe['failure_reason']=code
        report.setdefault('private_worker_diagnostics',{})[label]=safe
    require(result.returncode==0 and type(value) is dict and value['passed'] is True
        and not result.stderr,'private_worker_execution_failed')
    publish_verified(value,case,native,report,label)
    return value


def sources():
    return {'input_files':build.inputs(),'accepted_context':context.sources()}


def self_test():
    tests=[]
    def rejects(label,value,case,native):
        report={'observations':{},'validations':{}}
        try: publish_verified(value,case,native,report,'test')
        except (ValueError,KeyError,TypeError): pass
        else: raise ValueError('private_publication_mutant_accepted')
        require(not report['observations'] and not report['validations'],'rejected_private_bytes_published')
        tests.append(label)
    for backend in ('mysql','mariadb'):
        native=context.linux.reference(backend)
        path=ROOT/f'experiments/php-wasix-wp-request-context-probe/evidence/ci-41742369145-{backend}/raw/experiments/php-wasix-wp-request-context-probe/evidence/linux-{backend}.json'
        old=json.loads(path.read_bytes())
        for case,label in (('home','home'),('missing','missing'),('missing','missing_repeat')):
            original=old['observations'][label]; value=copy.deepcopy(original)
            value.update(schema=SCHEMA,outcome='fixed_private_wp_worker_passed',phase='full_private_response_verified',
                private_ipc_only=True,publication_authorized=False,native_comparison_performed=False)
            value['prepared'].update(verification_scope='compiled_fixtures_and_core_only',
                transitive_inputs_verified_by_worker=False)
            php=value['php_result']
            value['private_response']=php.pop('response')
            value['private_contexts']={k:php.pop(k) for k in FIELDS}
            php.pop('response_observation',None)
            r=value['private_response']
            php['response_summary']={'status':r['status'],'header_count':len(r['headers_hex']),
                'body_bytes':r['body_bytes'],'body_sha256':r['body_sha256'],'representation_length':None}
            safe=value['cgi_capture'].get('safe_php_observation',{})
            for k in ['response','response_observation',*FIELDS]: safe.pop(k,None)
            expected=native['requests'][label]['observation']
            validate_private(value,case,expected); tests.append(backend+'-'+label+'-positive-synthetic-view')
            poisoned=copy.deepcopy(value); marker='private-unaccepted-controller-placeholder'
            for container in (poisoned,poisoned['php_result'],poisoned['cgi_capture'],poisoned['rpc'],
                    poisoned['managed_access'],poisoned['prepared']):
                container['extra_private_payload']=marker
            clean={'observations':{},'validations':{}}
            publish_verified(poisoned,case,expected,clean,'test')
            encoded=canonical(clean)
            require(marker.encode() not in encoded and marker.encode().hex().encode() not in encoded,
                'unknown_private_fields_published')
            require(clean['observations']['test']['worker_fields_projected'] is True
                and clean['observations']['test']['original_worker_json_published'] is False,'public_projection_scope')
            tests.append(backend+'-'+label+'-unknown_private_fields_excluded')
            bad=copy.deepcopy(value); body=b'private-unaccepted-body-placeholder'
            bad['private_response'].update(body_hex=body.hex(),body_bytes=len(body),body_sha256=sha(body))
            rejects(backend+'-'+label+'-unknown-complete-body',bad,case,expected)
            for field,replacement in [('publication_authorized',True),('db_actor_joined',False)]:
                bad=copy.deepcopy(value); bad[field]=replacement
                rejects(backend+'-'+label+'-'+field,bad,case,expected)
            bad=copy.deepcopy(value); bad['private_contexts']['context_after_dispatch']={'private':'unknown'}
            rejects(backend+'-'+label+'-unknown-context',bad,case,expected)
    try: strict_json(b'{"response":{"status":200,"status":404}}')
    except ValueError: tests.append('nested_duplicate_json_rejected')
    else: raise ValueError('private_duplicate_accepted')
    return {'passed':True,'tests':tests,'test_count':len(tests),'synthetic_worker_views_only':True,
        'php_wasm_database_or_http_executed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['mysql','mariadb']); parser.add_argument('--record',action='store_true')
    parser.add_argument('--self-test',action='store_true'); args=parser.parse_args()
    if args.self_test:
        value=self_test(); print(json.dumps(value,indent=2)); return 0
    require(args.backend is not None,'backend_required')
    report={'schema':'recastpress.php-wasix-wp-private-worker-check.v0.5','passed':False,'outcome':'not_run',
        'backend':args.backend,'platform':sys.platform,'observed_at_utc':datetime.now(timezone.utc).isoformat(),
        'observations':{},'validations':{},'owned_cleanup_completed':False,'source_reverified_after':False,
        'response_normalized':False,'native_comparison_in_controller_only':True,'http_listener_exercised':False,
        'native_php_executed':False,'native_php_fallback':False,'product_runtime_selected':False,
        'unknown_response_publication_authorized':False,'module_shared_across_owned_host_processes':False}
    passed=False; database=None; stage='sources'
    try:
        require(sys.platform=='linux','actual_php_wasm_linux_only')
        before=report['sources_before']=sources()
        host=report['build_receipt']=build.verify_build()
        bootstrap=report['bootstrap_build_receipt']=context.oldbuild.verify_build()
        report['module_provenance']=context.managed.verify_build()
        report['publication_gate_self_test']=self_test()
        native=context.linux.reference(args.backend); native_boot=context.original.reference(args.backend)
        report['native_reference']={'path':f'experiments/wp-request-context-probe/evidence/windows-{args.backend}-native.json',
            'sha256':context.linux.ORACLES[args.backend]}
        environment=build.environment(); database=context.linux.Database(args.backend,report)
        with database:
            for mode in ('install','read'):
                stage='php_wasm_'+mode
                value=context.execute(ROOT/bootstrap['executable'],environment,database.request(mode),report,mode)
                report['validations'][mode]=context.original.validate(value,mode,native_boot[mode])
            stage='public_date_seed'; database.seed_public_dates(); database.verify_context_state('before_requests')
            for case,label in (('home','home'),('missing','missing'),('missing','missing_repeat')):
                stage='private_php_wasm_'+label
                payload=database.context_request(case); payload['schema']=INPUT
                value=execute_worker(ROOT/host['executable'],environment,payload,report,label,
                    native['requests'][label]['observation'],case)
                database.verify_context_state(label,value['php_result'])
                require(sources()==before,'source_after_private_request_changed')
            require(report['observations']['missing']['private_response']==
                report['observations']['missing_repeat']['private_response'],'fresh_private_repeat_404_differs')
            report['fresh_repeat_404_response_exact']=True
        require(report['container_stopped'] is True and report['container_removed'] is True
            and report['container_normal_exit_verified'] is True,'normal_private_worker_db_cleanup')
        stage='after_verification'; report['sources_after']=sources()
        require(report['sources_after']==before and build.verify_build()==host
            and context.oldbuild.verify_build()==bootstrap,'private_worker_inputs_after_cleanup_changed')
        report['module_provenance_after']=context.managed.verify_build()
        require(report['module_provenance_after']==report['module_provenance'],'module_after_private_requests_changed')
        report['request_module_compilations']=sum(report['observations'][x]['module_compilations']
            for x in ('home','missing','missing_repeat'))
        require(report['request_module_compilations']==3,'private_request_module_compiles')
        report['source_reverified_after']=True; passed=True
    except (Exception,KeyboardInterrupt) as error:
        report.update(failure_stage=stage,failure_class=type(error).__name__)
        if isinstance(error,ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}',str(error)):
            report['failure']=str(error)
    finally:
        try: owned_probe.reap_owned_probes(); report['owned_cleanup_completed']=True
        except (Exception,KeyboardInterrupt): passed=False; report['cleanup_failed']=True
    report['passed']=passed
    report['outcome']='fixed_private_worker_comparison_passed' if passed else 'private_worker_comparison_failed'
    wire=(json.dumps(report,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode()
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire,'private_worker_evidence_secret_guard')
    require(len(wire)<=12*1024*1024,'private_worker_public_report_quota')
    if args.record: build.preserve(HERE/f'evidence/linux-{args.backend}.json',wire)
    print(report['outcome']); return 0 if passed else 1


if __name__=='__main__': sys.exit(main())
