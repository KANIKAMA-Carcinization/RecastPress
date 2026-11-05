"""Real loopback HTTP -> owned Rust worker -> PHP WASIX -> owned database."""
import argparse
import copy
from datetime import datetime, timezone
import email.utils
import importlib.util
import json
from pathlib import Path
import re
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


context = load('rp_http_frozen_context_controller', ROOT / 'experiments/php-wasix-wp-request-context-probe/run_check.py')
build = load('rp_http_owned_build', HERE / 'build_check.py')
private = load('rp_http_private_worker_controller', ROOT / 'experiments/php-wasix-wp-private-worker-probe/run_check.py')
observer = load('rp_http_fixed_observer', ROOT / 'experiments/php-wasix-wp-http-probe/http_observation.py')
process = load('rp_http_owned_server', HERE / 'server_process.py')
require, sha, canonical = context.require, context.sha, context.canonical


def sources():
    return {'input_files': build.inputs(), 'context': context.sources(), 'private_worker': private.sources(), 'http_observer_inputs': observer.input_hashes()}


def date_window(observation, started, finished):
    value = observation['transport_headers']['date_gmt']
    if value is not None:
        stamp = email.utils.parsedate_to_datetime(value).timestamp()
        require(started - 2 <= stamp <= finished + 2, 'actual_http_date_outside_request_window')
    return {'started_utc': datetime.fromtimestamp(started, timezone.utc).isoformat(),
        'finished_utc': datetime.fromtimestamp(finished, timezone.utc).isoformat(), 'date_within_window_if_present': True}


def validate_server(value, execution, worker_build, native, report):
    expected=expected_worker_identity(worker_build,private.build.read(private.build.RECEIPT,2*1024*1024))
    return validate_private_server(value,execution,expected,native,report)


def expected_worker_identity(receipt,wire):
    return {'receipt_path':'experiments/php-wasix-wp-private-worker-probe/evidence/linux-build.json',
        'receipt_sha256':sha(wire),'executable':receipt['executable'],
        'executable_identity':receipt['executable_identity'],'source_inputs':219,'units':59,
        'source_files_rehashed_by_service':False}


def closed(value,names):
    require(type(value) is dict and set(value)==set(names),'closed_private_http_record')


def integer(value,maximum,minimum=0):
    require(type(value) is int and minimum<=value<=maximum,'private_http_numeric_metric')


def digest(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None,'private_http_digest')


def byte_identity(value,maximum,minimum=0):
    closed(value,['bytes','sha256']); integer(value['bytes'],maximum,minimum); digest(value['sha256'])


def equal(actual,expected):
    require(canonical(actual)==canonical(expected),'private_http_exact_observation')


def validate_summary(value,case,expected):
    positive=['physical_connection_created','physical_disconnect_completed','db_actor_joined','db_actor_exited',
        'fresh_runtime','source_rechecked_after_execution','full_wordpress_bootstrap']
    negative=['guest_networking','host_environment_forwarded','host_home_mapped','db_credentials_supplied_to_guest',
        'native_php_fallback','product_runtime']
    fixed=['schema','worker_schema','outcome','phase','case','mode','module_compilations','completion_verified',
        'public_context_verified','private_ipc_only','worker_publication_authorized','native_comparison_performed',
        'db_actor_failure','response_summary','private_contexts_identity','autoload_option_count',
        'autoload_max_value_bytes','operations_summary','cgi_capture','rpc','managed_access']
    closed(value,fixed+positive+negative)
    for key,wanted in [('schema','recastpress.php-wasix-wp-private-http-worker-summary.v0.5'),
            ('worker_schema','recastpress.php-wasix-wp-private-worker-observation.v0.5'),
            ('outcome','fixed_private_wp_worker_passed'),('phase','full_private_response_verified'),
            ('case',case),('mode','home'),('module_compilations',1),('db_actor_failure',None)]: equal(value[key],wanted)
    for key in positive+['completion_verified','public_context_verified','private_ipc_only']: equal(value[key],True)
    for key in negative+['worker_publication_authorized','native_comparison_performed']: equal(value[key],False)
    response=expected['response']
    equal(value['response_summary'],{'status':response['status'],'header_count':len(response['headers_hex']),
        'body_bytes':response['body_bytes'],'body_sha256':response['body_sha256'],'representation_length':None})
    contexts={key:expected[key] for key in context.CONTEXT_FIELDS}
    equal(value['private_contexts_identity'],process.identity(canonical(contexts)))
    for key in ('autoload_option_count','autoload_max_value_bytes'):
        integer(value[key],2**32-1,1); equal(value[key],expected[key])
    ops=value['operations_summary']
    closed(ops,['operations','hello','query','escape','close','request_bytes','reply_bytes',
        'largest_row_count','largest_column_count','largest_reply_frame_bytes'])
    counts={'hello':1,'query':50 if case=='home' else 28,'escape':93 if case=='home' else 44,'close':1}
    for key,wanted in counts.items(): equal(ops[key],wanted)
    equal(ops['operations'],sum(counts.values()))
    for key,maximum in [('request_bytes',8388608),('reply_bytes',16777216),
            ('largest_row_count',256),('largest_column_count',32),('largest_reply_frame_bytes',524288)]:
        integer(ops[key],maximum)
    rpc=value['rpc']
    closed(rpc,['finished','opened_once','close_dispatched','failure','channel_open','response_pending',
        'pending_bytes','unread_response_bytes','operations','request_bytes','response_bytes','response_read_bytes'])
    for key in ('finished','opened_once','close_dispatched'): equal(rpc[key],True)
    for key in ('channel_open','response_pending'): equal(rpc[key],False)
    equal(rpc['failure'],None)
    for key in ('pending_bytes','unread_response_bytes'): equal(rpc[key],0)
    for key,wanted in [('operations',ops['operations']),('request_bytes',ops['request_bytes']),
            ('response_bytes',ops['reply_bytes']),('response_read_bytes',ops['reply_bytes'])]: equal(rpc[key],wanted)
    access=value['managed_access']
    numbers=['pending_bytes','unread_response_bytes','active_handles','requests','generated_bytes','read_bytes',
        'open_events','closed_handles','invalid','io_errors','allowed','denied','missing','unmanaged']
    closed(access,['finished','protocol_error',*numbers])
    equal(access['finished'],True); equal(access['protocol_error'],None)
    for key in numbers: integer(access[key],16777216)
    integer(access['requests'],16384,1)
    for key in ('pending_bytes','unread_response_bytes','active_handles','invalid','io_errors'): equal(access[key],0)
    equal(access['generated_bytes'],access['requests']*5); equal(access['read_bytes'],access['requests']*5)
    equal(access['open_events'],access['closed_handles'])
    equal(sum(access[k] for k in ('allowed','denied','missing','unmanaged')),access['requests'])
    capture=value['cgi_capture']
    closed(capture,['guest_exit_success','runtime_shutdown_returned','stdout_eof','stderr_eof','stdout_overflow',
        'stderr_overflow','stdout_bytes','stderr_bytes','stdout_sha256','stderr_sha256','response'])
    for key in ('guest_exit_success','runtime_shutdown_returned','stdout_eof','stderr_eof'): equal(capture[key],True)
    for key in ('stdout_overflow','stderr_overflow'): equal(capture[key],False)
    equal(capture['stderr_bytes'],0); equal(capture['stderr_sha256'],sha(b''))
    integer(capture['stdout_bytes'],3*1024*1024,1); digest(capture['stdout_sha256'])
    outer=capture['response']
    closed(outer,['status','header_count','body_bytes','body_sha256','representation_length'])
    equal(outer['status'],200); equal(outer['representation_length'],None)
    integer(outer['header_count'],2,1); integer(outer['body_bytes'],3*1024*1024,1); digest(outer['body_sha256'])
    return {'complete_private_worker_verified':True,'native_response_and_context_digest_match':True,
        'native_comparison_in_controller_only':True,'original_private_stdout_reconstructed':False,
        'operation_counts':counts,'private_operation_rows_published':False}


def validate_private_server(value,execution,worker_identity,native,report):
    closed(value,['schema','passed','outcome','requests','worker_executions','worker_build','rejected_counts',
        'concurrency','valid_execution_limit','bound_loopback_only','graceful_shutdown_completed',
        'stdin_shutdown_observed','stdin_protocol_error','startup_private_input_used','private_config_on_http',
        'transport_delivery_verified','source_files_rehashed_by_service','native_comparison_performed',
        'native_php_fallback','product_runtime'])
    equal(value['schema'],'recastpress.php-wasix-wp-private-http-observation.v0.5')
    equal(value['outcome'],'fixed_private_http_passed')
    for key in ('passed','bound_loopback_only','graceful_shutdown_completed','startup_private_input_used'): equal(value[key],True)
    for key in ('stdin_protocol_error','private_config_on_http','transport_delivery_verified','source_files_rehashed_by_service',
            'native_comparison_performed','native_php_fallback','product_runtime'): equal(value[key],False)
    require(type(value['stdin_shutdown_observed']) is bool,'private_http_control_eof_metric')
    equal(value['worker_executions'],3); equal(value['valid_execution_limit'],3); equal(value['concurrency'],1)
    equal(value['worker_build'],worker_identity)
    equal(value['rejected_counts'],{'method':1,'target':1,'host':1,'headers':3,'body':1,'busy':0,'budget':0})
    require(execution['owned_reaped'] is True and execution['forced_cleanup'] is False
        and execution['exit_code']==0 and execution['captured_output_complete_after_reap'] is True,'private_http_server_reaped')
    equal(execution['stderr'],process.identity(b''))
    require(type(value['requests']) is list and len(value['requests'])==3,'private_http_three_summaries')
    validations={}; pids=[]
    for row,label in zip(value['requests'],('home','missing','missing_repeat')):
        case='home' if label=='home' else 'missing'
        closed(row,['case','worker','worker_summary','response_constructed','anonymous_fixture_request_authorized'])
        equal(row['case'],case); equal(row['response_constructed'],True); equal(row['anonymous_fixture_request_authorized'],True)
        worker=row['worker']
        closed(worker,['owned_pid','reaped','stdout_eof','stderr_eof','exit_code','stdout','stderr',
            'identity_before','identity_after_verified'])
        integer(worker['owned_pid'],2**32-1,1); pids.append(worker['owned_pid'])
        for key in ('reaped','stdout_eof','stderr_eof','identity_after_verified'): equal(worker[key],True)
        equal(worker['exit_code'],0); equal(worker['stderr'],process.identity(b''))
        byte_identity(worker['stdout'],4*1024*1024,1); equal(worker['identity_before'],worker_identity)
        validations[label]=validate_summary(row['worker_summary'],case,native['requests'][label]['observation'])
        for key in ('autoload_option_count','autoload_max_value_bytes'):
            equal(report['independent_context_checks'][label][key],row['worker_summary'][key])
    require(len(set(pids))==3 and execution['owned_pid'] not in pids,'private_http_distinct_owned_workers')
    equal(value['requests'][1]['worker_summary']['response_summary'],value['requests'][2]['worker_summary']['response_summary'])
    report['validations'].update(validations)
    report.update(fresh_repeat_404_response_exact=True,all_three_owned_workers_reaped=True,request_module_compilations=3)


def synthetic_server(backend):
    folder=ROOT/f'experiments/php-wasix-wp-private-worker-probe/evidence/ci-41771428584-{backend}/raw/experiments/php-wasix-wp-private-worker-probe/evidence'
    baseline=json.loads((folder/f'linux-{backend}.json').read_bytes())
    receipt_wire=(folder/'linux-build.json').read_bytes(); receipt=json.loads(receipt_wire)
    identity=expected_worker_identity(receipt,receipt_wire)
    value={'schema':'recastpress.php-wasix-wp-private-http-observation.v0.5','passed':True,
        'outcome':'fixed_private_http_passed','requests':[],'worker_executions':3,'worker_build':identity,
        'rejected_counts':{'method':1,'target':1,'host':1,'headers':3,'body':1,'busy':0,'budget':0},
        'concurrency':1,'valid_execution_limit':3,'bound_loopback_only':True,'graceful_shutdown_completed':True,
        'stdin_shutdown_observed':False,'stdin_protocol_error':False,'startup_private_input_used':True,
        'private_config_on_http':False,'transport_delivery_verified':False,'source_files_rehashed_by_service':False,
        'native_comparison_performed':False,'native_php_fallback':False,'product_runtime':False}
    report={'validations':{},'independent_context_checks':copy.deepcopy(baseline['independent_context_checks'])}
    for index,label in enumerate(('home','missing','missing_repeat')):
        source=baseline['observations'][label]; php=source['php_result']; ops=source['operations']
        counts={kind:sum(row['op']==kind for row in ops) for kind in ('hello','query','escape','close')}
        summary={'schema':'recastpress.php-wasix-wp-private-http-worker-summary.v0.5',
            'worker_schema':source['schema'],'outcome':source['outcome'],'phase':source['phase'],
            'case':source['case'],'mode':source['mode'],'module_compilations':1,'completion_verified':True,
            'public_context_verified':True,'private_ipc_only':True,'worker_publication_authorized':False,
            'native_comparison_performed':False,'db_actor_failure':None,
            'response_summary':copy.deepcopy(php['response_summary']),
            'private_contexts_identity':process.identity(canonical(source['private_contexts'])),
            'autoload_option_count':php['autoload_option_count'],'autoload_max_value_bytes':php['autoload_max_value_bytes'],
            'operations_summary':{'operations':len(ops),**counts,'request_bytes':source['rpc']['request_bytes'],
                'reply_bytes':source['rpc']['response_bytes'],'largest_row_count':max(row['row_count'] or 0 for row in ops),
                'largest_column_count':max(row['column_count'] or 0 for row in ops),
                'largest_reply_frame_bytes':max(row['reply_frame_bytes'] for row in ops)},
            'cgi_capture':copy.deepcopy(source['cgi_capture']),'rpc':copy.deepcopy(source['rpc']),
            'managed_access':copy.deepcopy(source['managed_access'])}
        for key in ('physical_connection_created','physical_disconnect_completed','db_actor_joined','db_actor_exited',
                'fresh_runtime','source_rechecked_after_execution','full_wordpress_bootstrap','guest_networking',
                'host_environment_forwarded','host_home_mapped','db_credentials_supplied_to_guest','native_php_fallback','product_runtime'):
            summary[key]=source[key]
        value['requests'].append({'case':source['case'],'worker_summary':summary,'response_constructed':True,
            'anonymous_fixture_request_authorized':True,'worker':{'owned_pid':10001+index,'reaped':True,
            'stdout_eof':True,'stderr_eof':True,'exit_code':0,'stdout':process.identity(b'synthetic-unpublished-private-packet'),
            'stderr':process.identity(b''),'identity_before':identity,'identity_after_verified':True}})
    execution={'owned_pid':10000,'owned_reaped':True,'forced_cleanup':False,'exit_code':0,
        'captured_output_complete_after_reap':True,'stderr':process.identity(b'')}
    return value,execution,identity,context.linux.reference(backend),report


def self_test():
    names=[]
    changes=[('top_extra',lambda v:v.update(extra_private_payload='private-not-public')),
        ('row_extra',lambda v:v['requests'][0].update(extra_private_payload='private-not-public')),
        ('summary_extra',lambda v:v['requests'][0]['worker_summary'].update(body_hex='private-not-public')),
        ('rpc_extra',lambda v:v['requests'][0]['worker_summary']['rpc'].update(extra_private_payload='private-not-public')),
        ('access_extra',lambda v:v['requests'][0]['worker_summary']['managed_access'].update(extra_private_payload='private-not-public')),
        ('capture_extra',lambda v:v['requests'][0]['worker_summary']['cgi_capture'].update(extra_private_payload='private-not-public')),
        ('not_admitted',lambda v:v['requests'][0].update(anonymous_fixture_request_authorized=False)),
        ('actor_incomplete',lambda v:v['requests'][0]['worker_summary'].update(db_actor_joined=False)),
        ('contexts_changed',lambda v:v['requests'][0]['worker_summary']['private_contexts_identity'].update(sha256='0'*64)),
        ('body_changed',lambda v:v['requests'][0]['worker_summary']['response_summary'].update(body_sha256='0'*64)),
        ('raw_packet_in_worker',lambda v:v['requests'][0]['worker'].update(stdout_hex='private-not-public')),
        ('not_reaped',lambda v:v['requests'][0]['worker'].update(reaped=False)),
        ('numeric_bool',lambda v:v['requests'][0]['worker_summary']['operations_summary'].update(hello=True)),
        ('access_handle_left',lambda v:v['requests'][0]['worker_summary']['managed_access'].update(active_handles=1)),
        ('rpc_pending',lambda v:v['requests'][0]['worker_summary']['rpc'].update(pending_bytes=1)),
        ('worker_authorized',lambda v:v['requests'][0]['worker_summary'].update(worker_publication_authorized=True)),
        ('worker_native_comparison',lambda v:v['requests'][0]['worker_summary'].update(native_comparison_performed=True)),
        ('source_rehashed',lambda v:v.update(source_files_rehashed_by_service=True)),
        ('not_fresh',lambda v:v['requests'][0]['worker_summary'].update(module_compilations=0))]
    for backend in ('mysql','mariadb'):
        value,execution,identity,native,report=synthetic_server(backend)
        validate_private_server(value,execution,identity,native,report); names.append(backend+'-synthetic-positive')
        for name,change in changes:
            bad=copy.deepcopy(value); change(bad); result={'validations':{},'independent_context_checks':report['independent_context_checks']}
            try: validate_private_server(bad,execution,identity,native,result)
            except (ValueError,KeyError,TypeError): pass
            else: raise ValueError('private_http_controller_mutant_accepted')
            require(not result['validations'],'partial_invalid_http_validation_committed')
            names.append(backend+'-'+name)
    return {'passed':True,'test_count':len(names),'tests':names,'synthetic_service_records_only':True,
        'php_wasm_database_or_http_executed':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'])
    parser.add_argument('--record', action='store_true'); parser.add_argument('--self-test',action='store_true')
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(),indent=2)); return 0
    require(args.backend is not None,'backend_required')
    report = {'schema': 'recastpress.php-wasix-wp-private-http-check.v0.5', 'passed': False, 'outcome': 'not_run',
        'backend': args.backend, 'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': sys.platform,
        'observations': {}, 'validations': {}, 'http_observations': {}, 'http_rejections': {},
        'real_http_listener_exercised': False, 'native_php_executed': False, 'native_php_fallback': False,
        'product_runtime_selected': False, 'standard_wpdb_compatibility_verified': False,
        'request_or_body_normalized': False, 'module_cache_exercised': False,
        'memory_cpu_limits_verified_for_this_integration': False, 'all_resources_immediately_reclaimed': False}
    passed = False; database = None; server = None; stage = 'source_preparation'
    try:
        require(sys.platform == 'linux', 'actual_php_wasm_http_linux_only')
        before = report['sources_before'] = sources()
        http_build = report['build_receipt'] = build.verify_build()
        worker_build = report['private_worker_build_receipt'] = private.build.verify_build()
        bootstrap_build = report['bootstrap_build_receipt'] = context.oldbuild.verify_build()
        report['theme_build_receipt'] = context.themebuild.verify_build()
        report['module_provenance'] = context.managed.verify_build()
        report['http_observer_pure_checks'] = observer.self_test()
        report['http_controller_pure_checks'] = self_test()
        references = observer.native_references()[args.backend]
        native = context.linux.reference(args.backend)
        native_boot = context.original.reference(args.backend)
        database = context.linux.Database(args.backend, report)
        with database:
            for mode in ('install', 'read'):
                stage = 'php_wasm_' + mode
                actual = context.execute(ROOT / bootstrap_build['executable'], build.environment(),
                    database.request(mode), report, mode)
                report['validations'][mode] = context.original.validate(actual, mode, native_boot[mode])
            database.seed_public_dates(); database.verify_context_state('before_requests')
            stage = 'http_server_startup'
            payload = {'schema': 'recastpress.php-wasix-wp-private-http-input.v0.5', 'database': database.home_request()}
            server = process.Server(ROOT / http_build['executable'], build.environment(), payload, report)
            with server:
                report['real_http_listener_exercised'] = True
                for label in observer.REJECTIONS:
                    stage = 'http_rejection_' + label
                    started = time.time()
                    wire = observer.request(server.port, observer.REQUESTS[label], timeout=10)
                    finished = time.time()
                    observation = observer.observe_rejection(wire, label)
                    report['http_rejections'][label] = {**observation, 'request_wire': process.identity(observer.REQUESTS[label]),
                        'request_time_window': date_window(observation, started, finished)}
                    server.check_captures()
                for case, label in (('home', 'home'), ('missing', 'missing'), ('missing', 'missing_repeat')):
                    stage = 'http_public_' + label
                    started = time.time()
                    wire = observer.request(server.port, observer.REQUESTS[case])
                    finished = time.time()
                    observation = observer.observe_wire(wire, references[case])
                    report['http_observations'][label] = {**observation,
                        'request_wire': process.identity(observer.REQUESTS[case]),
                        'request_time_window': date_window(observation, started, finished)}
                    database.verify_context_state(label)
                    require(sources() == before, 'sources_after_actual_http_request_changed')
                    server.check_captures()
                stage = 'http_server_final_observation'
                completed_server=server.finish()
                validate_server(completed_server,report['http_server_execution'],worker_build,native,report)
                report['http_server_observation']=completed_server
        require(report['container_stopped'] is True and report['container_removed'] is True
            and report['container_normal_exit_verified'] is True, 'normal_http_database_cleanup')
        stage = 'after_verification'
        report['sources_after'] = sources()
        require(report['sources_after'] == before and build.verify_build() == http_build
            and private.build.verify_build() == worker_build and context.oldbuild.verify_build() == bootstrap_build
            and context.themebuild.verify_build() == report['theme_build_receipt'], 'http_sources_builds_or_binaries_changed')
        report['module_provenance_after'] = context.managed.verify_build()
        require(report['module_provenance_after'] == report['module_provenance'], 'http_module_changed')
        report['source_reverified_after'] = True; passed = True
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        if server is not None and not server.finished:
            try: server.abort()
            except (Exception, KeyboardInterrupt): passed = False; report['http_server_cleanup_failed'] = True
        try:
            context.owned_probe.reap_owned_probes(); report['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt): passed = False; report['owned_cleanup_failed'] = True
    report.update(passed=passed, outcome='fixed_private_real_http_php_wasm_passed' if passed else 'private_real_http_php_wasm_failed')
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'http_public_secret_guard')
    require(len(wire) <= 12*1024*1024, 'http_public_record_quota')
    if args.record: build.preserve(HERE / ('evidence/linux-' + args.backend + '.json'), wire)
    print(report['outcome']); return 0 if passed else 1


if __name__ == '__main__': sys.exit(main())
