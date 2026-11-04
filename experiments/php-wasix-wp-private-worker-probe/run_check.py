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
    report['observations'][label]=value
    report['validations'][label]=validation


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
