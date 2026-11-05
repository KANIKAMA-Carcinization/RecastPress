"""Compare compile-once/fresh-request WordPress sessions on owned databases."""
import argparse
import ast
import copy
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


private=load('rp_session_private_controller',ROOT/'experiments/php-wasix-wp-private-worker-probe/run_check.py')
context=private.context
build=load('rp_session_owned_build',HERE/'build_check.py')
process=load('rp_session_owned_process',HERE/'session_process.py')
require,sha,canonical=private.require,private.sha,private.canonical
SCHEMA='recastpress.php-wasix-wp-session-request-observation.v0.5'
FIELDS=context.CONTEXT_FIELDS


def equal(actual,expected):
    if type(expected) is set:
        require(type(actual) is set and actual==expected,'session_exact_fields')
    else:require(canonical(actual)==canonical(expected),'session_exact_observation')


def request_validator():
    text=context.THEME_CONTROLLER.read_text()
    require(sha(context.THEME_CONTROLLER.read_bytes())==context.FROZEN_THEME_CONTROLLER,'frozen_session_lifecycle_validator')
    node=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='validate_home')
    original=ast.get_source_segment(text,node);derived=original
    for before,after in [
        ('recastpress.php-wasix-wp-request-observation.v0.5',SCHEMA),
        ('fixed_wasix_wp_request_passed','fixed_session_request_passed'),
        ('full_home_verified','full_private_response_verified'),
        ("value['module_compilations']==1","value['module_compilations']==0")]:
        require(derived.count(before)==1,'exact_session_validator_derivation')
        derived=derived.replace(before,after,1)
    namespace=dict(context.theme.__dict__)
    exec(compile(derived,'<frozen-compile-zero-session-validator>','exec'),namespace)
    return namespace['validate_home'],{'original_sha256':sha(original.encode()),'derived_sha256':sha(derived.encode()),
        'changed_literals':4,'actual_request_compile_count_required':0,'request_count_relabelled':False,
        'existing_lifecycle_rpc_and_native_checks_retained':True}


def validate_request(value,case,native):
    require(case in ('home','missing'),'closed_session_request_case')
    equal(value['case'],case)
    equal(value['schema'],SCHEMA);equal(value['outcome'],'fixed_session_request_passed')
    for key in ('passed','private_ipc_only','compiled_module_reused','fresh_runtime'):equal(value[key],True)
    for key in ('publication_authorized','native_comparison_performed','module_compiled_during_request'):equal(value[key],False)
    equal(value['module_compilations'],0)
    equal(value['private_response'],native['response'])
    equal(set(value['private_contexts']),set(FIELDS))
    for key in FIELDS:equal(value['private_contexts'][key],native[key])
    response=native['response']
    equal(value['php_result']['response_summary'],{'status':response['status'],'header_count':len(response['headers_hex']),
        'body_bytes':response['body_bytes'],'body_sha256':response['body_sha256'],'representation_length':None})
    require(not any(k in value['php_result'] for k in ['response',*FIELDS]),'session_private_diagnostics_shape')
    view=copy.deepcopy(value)
    view['php_result'].update(value['private_contexts']);view['php_result']['response']=value['private_response']
    validate,derivation=request_validator()
    result=validate(view,'home',native)
    equal(view['php_result']['schema'],'recastpress.php-wasix-wp-request-context-php-observation.v0.5')
    equal(view['php_result']['outcome'],'fixed_wasix_context_passed')
    result.update(native_comparison_in_controller_only=True,request_module_compilations=0,
        compiled_module_reused=True,actual_compile_count_preserved=True,validator_derivation=derivation)
    return result


def publish_request(value,case,native,report,label):
    validation=validate_request(value,case,native)
    projection=private.public_projection(value)
    projection.update(compiled_module_reused=True,module_compiled_during_request=False)
    validate_request(projection,case,native)
    report['observations'][label]=projection;report['validations'][label]=validation


def validate_final(value,completed,passed):
    equal(set(value),{'schema','passed','outcome','module_compilations_total','requests_completed','all_requests_cleaned',
        'request_module_compilations_total','private_ipc_only','publication_authorized','native_comparison_performed','error'})
    for key,wanted in [('schema','recastpress.php-wasix-wp-session-observation.v0.5'),('passed',passed),
            ('outcome','fixed_wp_session_passed' if passed else 'fixed_wp_session_failed'),
            ('module_compilations_total',1),('requests_completed',completed),('all_requests_cleaned',True),
            ('request_module_compilations_total',0),('private_ipc_only',True),('publication_authorized',False),
            ('native_comparison_performed',False),('error',None if passed else 'fixed_session_failed')]:equal(value[key],wanted)


def sources():return {'input_files':build.inputs(),'private_worker':private.sources()}


def self_test():
    tests=[]
    for backend in ('mysql','mariadb'):
        path=ROOT/f'experiments/php-wasix-wp-private-worker-probe/evidence/ci-41771428584-{backend}/raw/experiments/php-wasix-wp-private-worker-probe/evidence/linux-{backend}.json'
        baseline=json.loads(path.read_bytes());native=context.linux.reference(backend)
        for case,label in (('home','home'),('missing','missing'),('missing','missing_repeat')):
            value=copy.deepcopy(baseline['observations'][label]);value.update(schema=SCHEMA,outcome='fixed_session_request_passed',
                module_compilations=0,compiled_module_reused=True,module_compiled_during_request=False)
            expected=native['requests'][label]['observation'];validate_request(value,case,expected)
            tests.append(backend+'-'+label+'-synthetic-compile-zero')
            for key,replacement in [('module_compilations',1),('compiled_module_reused',False),('db_actor_joined',False),
                    ('module_compiled_during_request',True),('case','private-unaccepted-case-placeholder'),
                    ('case','missing' if case=='home' else 'home')]:
                bad=copy.deepcopy(value);bad[key]=replacement;result={'observations':{},'validations':{}}
                try:publish_request(bad,case,expected,result,'test')
                except (ValueError,KeyError,TypeError):pass
                else:raise ValueError('session_bad_compile_or_cleanup_accepted')
                require(not result['observations'],'invalid_session_request_published')
                name=('case_unknown' if replacement=='private-unaccepted-case-placeholder' else 'case_other_route') if key=='case' else key
                tests.append(backend+'-'+label+'-'+name)
            unknown=copy.deepcopy(value);unknown['extra_private_payload']='private-unaccepted-session-placeholder'
            unknown['php_result']['extra_private_payload']='private-unaccepted-session-placeholder'
            result={'observations':{},'validations':{}};publish_request(unknown,case,expected,result,'test')
            require(b'private-unaccepted-session-placeholder' not in canonical(result),'session_unknown_payload_published')
            tests.append(backend+'-'+label+'-unknown-fields-excluded')
    return {'passed':True,'test_count':len(tests),'tests':tests,'synthetic_session_requests_only':True,
        'php_wasm_database_or_http_executed':False}


def parent_self_test():
    original=process.subprocess.Popen;results=[]
    ready={'schema':'recastpress.php-wasix-wp-session-ready.v0.5','module_compilations_total':1,
        'module_sha256':'0'*64,'compiled_module_ready':True}
    for mode in ('deadline','line_overflow','final_exit_timeout'):
        script='import sys,time;sys.stdout.buffer.write('+repr(canonical(ready)+b'\n')+');sys.stdout.flush();'
        if mode=='line_overflow':script+='sys.stdout.write("x"*'+str(process.LINE_LIMIT+2)+');sys.stdout.flush();'
        if mode=='final_exit_timeout':script+='sys.stdout.write("{}\\n");sys.stdout.flush();'
        script+='time.sleep(30)'
        def launch(command,**options):
            return original([str(Path(sys.executable).resolve()),'-u','-c',script],**options)
        report={};session=None
        process.subprocess.Popen=launch
        try:
            with process.Session(Path(sys.executable).resolve(),build.environment(),report,'mock_'+mode) as session:
                equal(session.ready(),ready)
                try:
                    if mode=='final_exit_timeout':session.finish(timeout=0.05)
                    else:session.read_line(0.05 if mode=='deadline' else 5)
                except (ValueError,subprocess.TimeoutExpired):pass
                else:raise ValueError('session_parent_limit_not_enforced')
            require(session.finished and session.row['owned_reaped'] is True and session.row['forced_cleanup'] is True
                and session.child.poll() is not None,'session_parent_owned_mock_not_reaped')
            results.append({'case':mode,'passed':True,'owned_reaped':True,'forced_cleanup':True})
        finally:
            process.subprocess.Popen=original
            if session is not None and not session.finished:session.abort()
    return {'passed':True,'tests':results,'test_count':len(results),'owned_python_mocks_only':True,
        'php_wasm_database_or_http_executed':False}


def run_negative(binary,environment,database,module_sha,label,report):
    with process.Session(binary,environment,report,label) as session:
        ready=session.ready();equal(ready['module_sha256'],module_sha)
        if label=='bad_first_sequence':
            payload=database.context_request('home');payload['schema']=private.INPUT
            sent=session.send({'schema':'recastpress.php-wasix-wp-session-input.v0.5','seq':2,'request':payload})
            session.row['negative_input']=sent
        final=session.finish(expected_exit=1)
        validate_final(final,0,False)
        report.setdefault('negative_protocol_sessions',{})[label]={'ready':ready,'final':final,
            'guest_requests_executed':0,'module_compilations':1}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['mysql','mariadb']);parser.add_argument('--record',action='store_true')
    parser.add_argument('--self-test',action='store_true');parser.add_argument('--parent-self-test',action='store_true')
    args=parser.parse_args()
    if args.self_test:print(json.dumps(self_test(),indent=2));return 0
    if args.parent_self_test:print(json.dumps(parent_self_test(),indent=2));return 0
    require(args.backend is not None,'backend_required')
    report={'schema':'recastpress.php-wasix-wp-session-check.v0.5','passed':False,'outcome':'not_run','backend':args.backend,
        'platform':sys.platform,'observed_at_utc':datetime.now(timezone.utc).isoformat(),'observations':{},'validations':{},
        'owned_cleanup_completed':False,'source_reverified_after':False,'native_php_executed':False,
        'native_php_fallback':False,'product_runtime_selected':False,'http_listener_exercised':False,
        'response_cache_exercised':False,'shared_php_state_exercised':False,'shared_database_connection_exercised':False,
        'all_resources_immediately_reclaimed':False,'synchronous_guest_cpu_cancellation_verified':False}
    passed=False;database=None;stage='sources'
    try:
        require(sys.platform=='linux','actual_php_wasm_session_linux_only')
        before=report['sources_before']=sources();host=report['build_receipt']=build.verify_build()
        bootstrap=report['bootstrap_build_receipt']=context.oldbuild.verify_build()
        report['module_provenance']=context.managed.verify_build();module_sha=report['module_provenance']['module']['sha256']
        report['controller_pure_checks']=self_test();report['parent_process_checks']=parent_self_test()
        native=context.linux.reference(args.backend)
        native_boot=context.original.reference(args.backend);environment=build.environment()
        database=context.linux.Database(args.backend,report)
        with database:
            for mode in ('install','read'):
                stage='php_wasm_'+mode
                value=context.execute(ROOT/bootstrap['executable'],environment,database.request(mode),report,mode)
                report['validations'][mode]=context.original.validate(value,mode,native_boot[mode])
            database.seed_public_dates();database.verify_context_state('before_requests')
            stage='session_compile_ready'
            with process.Session(ROOT/host['executable'],environment,report,'positive') as session:
                ready=session.ready();equal(ready['module_sha256'],module_sha);report['session_ready']=ready
                for seq,(case,label) in enumerate((('home','home'),('missing','missing'),('missing','missing_repeat')),1):
                    stage='fresh_session_request_'+label
                    payload=database.context_request(case);payload['schema']=private.INPUT
                    value=session.request(seq,payload)
                    publish_request(value,case,native['requests'][label]['observation'],report,label)
                    database.verify_context_state(label,value['php_result'])
                    require(sources()==before,'source_after_session_request_changed')
                stage='session_final_eof'
                final=session.finish();validate_final(final,3,True);report['session_final']=final
            equal(report['observations']['missing']['private_response'],report['observations']['missing_repeat']['private_response'])
            report['fresh_repeat_404_response_exact']=True
            for label in ('bad_first_sequence','early_eof'):
                stage='session_negative_'+label
                run_negative(ROOT/host['executable'],environment,database,module_sha,label,report)
            database.verify_context_state('missing_repeat',report['observations']['missing_repeat']['php_result'])
        require(report['container_stopped'] is True and report['container_removed'] is True
            and report['container_normal_exit_verified'] is True,'normal_session_database_cleanup')
        stage='after_verification';report['sources_after']=sources()
        require(report['sources_after']==before and build.verify_build()==host
            and context.oldbuild.verify_build()==bootstrap,'session_source_or_build_after_cleanup_changed')
        report['module_provenance_after']=context.managed.verify_build()
        equal(report['module_provenance_after'],report['module_provenance'])
        report.update(source_reverified_after=True,positive_session_module_compilations=1,
            positive_request_module_compilations=0,positive_request_count=3,positive_owned_worker_processes=1,
            negative_protocol_sessions_count=2,negative_session_module_compilations=2)
        passed=True
    except (Exception,KeyboardInterrupt) as error:
        report.update(failure_stage=stage,failure_class=type(error).__name__)
        if isinstance(error,ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}',str(error)):report['failure']=str(error)
    finally:
        try:
            process.reap_owned_sessions();context.owned_probe.reap_owned_probes()
            require(all(row.get('owned_pid') is None or row.get('owned_reaped') is True
                for row in report.get('session_executions',{}).values()),'session_owned_children_not_reaped')
            report['owned_cleanup_completed']=True
        except (Exception,KeyboardInterrupt):passed=False;report['cleanup_failed']=True
    report['passed']=passed;report['outcome']='fixed_compile_once_wp_session_passed' if passed else 'compile_once_wp_session_failed'
    wire=(json.dumps(report,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode()
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire,'session_public_secret_guard')
    require(len(wire)<=12*1024*1024,'session_public_report_limit')
    if args.record:build.preserve(HERE/f'evidence/linux-{args.backend}.json',wire)
    print(report['outcome']);return 0 if passed else 1


if __name__=='__main__':sys.exit(main())
