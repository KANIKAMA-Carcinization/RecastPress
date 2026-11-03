"""Execute nine synthetic result-budget cases in one newly owned fixture DB."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import build_check as build
from owned_database import OwnedDatabase, db, expected_rows

require, sha, read = build.require, build.sha, build.read
CASES = ['positive_115','zero_rows','row_limit_257','cell_limit_4096','raw_limit_exact_115',
    'raw_limit_below_115','encoded_limit_exact_115','encoded_limit_below_115','fresh_recovery_115']
POSITIVE = b'SELECT id,payload,label FROM rp_result_fixture WHERE id<=115 ORDER BY id'
QUERIES = [POSITIVE,
    b'SELECT id,payload,label FROM rp_result_fixture WHERE id<0 ORDER BY id',
    b'SELECT id,payload,label FROM rp_result_fixture ORDER BY id',
    b'SELECT id,payload,label FROM rp_result_fixture WHERE id=1 ORDER BY id',
    *[POSITIVE]*5]
ERRORS = [None,None,'row_limit','cell_limit',None,'raw_bytes_limit',None,'encoded_bytes_limit',None]
LIMITS = {'rows':256,'columns':16,'cell_bytes':65536,'total_raw_cell_bytes':262144,
    'metadata_bytes':4096,'identifier_bytes':256,'encoded_reply_bytes':524288}


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def exact(actual, expected, code):
    require(canonical(actual) == canonical(expected), code)


def integer(value, low=0, high=2**63-1):
    require(type(value) is int and low <= value <= high, 'result_integer_type_or_range')


def encoded_result(value):
    # Independently reconstruct the collector's fixed-order wire, without
    # normalizing or replacing any observed row, column or numeric value.
    columns = [{key:column[key] for key in ['name_hex','type','flags','character_set','declared_length','decimals']}
        for column in value['columns']]
    return json.dumps({'columns':columns,'rows':value['rows'],'row_count':value['row_count'],
        'raw_cell_bytes':value['raw_cell_bytes'],'metadata_bytes':value['metadata_bytes']},
        separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def validate(value, backend):
    require(value['schema'] == 'recastpress.wpdb-result-budget-observation.v0.5'
        and value['outcome'] == 'fixed_result_budget_cases_passed' and value['passed'] is True,
        'result_probe_success_required')
    for key in ['physical_connection_pool_used','database_started_by_probe','ddl_executed_by_probe',
            'wordpress_api_executed','wasm_executed','rpc_frames_exercised','product_budget_accepted',
            'driver_packet_allocation_budget_verified','all_resources_reclaimed','unbounded_result_drain_used',
            'raw_non_utf8_err_preserved','input_sql_modified']:
        require(value[key] is False, 'result_unclaimed_' + key)
    require(value['all_cases_disconnected'] is True, 'result_all_disconnected')
    exact(value['candidate_limits'],LIMITS,'result_candidate_limits')
    exact([value[k] for k in ['whole_probe_timeout_seconds','operation_timeout_seconds','disconnect_timeout_seconds']],
        [60,10,5],'result_bounded_deadlines')
    integer(value['elapsed_ms'],0,60000)
    rows=value['observations']
    require(type(rows) is list and len(rows)==9,'result_nine_actual_cases')
    exact([row['case'] for row in rows],CASES,'result_case_order')
    positive=expected_rows()
    raw_expected=sum(len(bytes.fromhex(cell)) for row in positive for cell in row if cell is not None)
    require(raw_expected==39048,'result_known_fixture_raw_bytes')
    n=rows[0]['encoded_result_bytes'];integer(n,65537,524288)
    identifiers=[]
    for index,(row,sql,error) in enumerate(zip(rows,QUERIES,ERRORS)):
        require(set(row)=={'case','query_hex','limits','physical_connection_created','physical_disconnect_completed',
            'connection_id','raw_server_version_hex','negotiated_capabilities','rows_observed','elapsed_ms',
            'partial_result_returned','result','encoded_result_bytes','encoded_result_sha256','budget_error','passed'},
            'result_case_fields')
        for key in ['passed','physical_connection_created','physical_disconnect_completed']:
            require(row[key] is True,'result_case_' + key)
        require(row['partial_result_returned'] is False,'result_no_partial_response')
        integer(row['connection_id'],1,2**32-1);identifiers.append(row['connection_id'])
        integer(row['negotiated_capabilities'],0,2**32-1)
        require(row['negotiated_capabilities'] & (2|128|2048|65536) == 0,'result_forbidden_capabilities_off')
        version=row['raw_server_version_hex']
        require(type(version) is str and re.fullmatch('(?:[0-9a-f]{2}){1,256}',version),'result_version_bytes')
        version=bytes.fromhex(version).decode('ascii',errors='strict')
        pin=db.PINS[backend]['version']
        require(version==pin if backend=='mysql' else version.startswith(pin+'-MariaDB')
            or version.startswith('5.5.5-'+pin+'-MariaDB'),'result_actual_driver_version')
        exact(row['query_hex'],sql.hex(),'result_sql_never_modified')
        limits=LIMITS.copy()
        if index==3:limits['cell_bytes']=4096
        elif index==4:limits['total_raw_cell_bytes']=39048
        elif index==5:limits['total_raw_cell_bytes']=39047
        elif index==6:limits['encoded_reply_bytes']=n
        elif index==7:limits['encoded_reply_bytes']=n-1
        exact(row['limits'],limits,'result_case_exact_limits')
        exact(row['budget_error'],error,'result_typed_expected_budget_verdict')
        integer(row['elapsed_ms'],0,60000)
        exact(row['rows_observed'],[115,0,257,1,115,115,115,115,115][index], 'result_actual_row_read_count')
        if error is not None:
            require(row['result'] is None and row['encoded_result_bytes']==0
                and type(row['encoded_result_bytes']) is int and row['encoded_result_sha256'] is None,
                'result_failure_has_no_partial_result')
            continue
        result=row['result']
        require(type(result) is dict and set(result)=={'columns','rows','row_count','raw_cell_bytes','metadata_bytes'},
            'result_complete_fields')
        expected=[] if index==1 else positive
        exact(result['rows'],expected,'result_all_actual_values_match_native_known_fixture')
        exact(result['row_count'],len(expected),'result_actual_row_count')
        exact(result['raw_cell_bytes'],0 if index==1 else raw_expected,'result_total_raw_cell_bytes')
        integer(result['metadata_bytes'],15,4096)
        columns=result['columns'];require(type(columns) is list and len(columns)==3,'result_three_columns')
        exact([c['name_hex'] for c in columns],[b'id'.hex(),b'payload'.hex(),b'label'.hex()], 'result_column_names_order')
        for column in columns:
            require(set(column)=={'name_hex','type','flags','character_set','declared_length','decimals'},'result_column_fields')
            for key,maximum in [('type',255),('flags',65535),('character_set',65535),('declared_length',2**32-1),('decimals',255)]:
                integer(column[key],0,maximum)
        exact(columns,rows[0]['result']['columns'],'result_metadata_same_for_zero_and_positive')
        wire=encoded_result(result)
        require(len(wire)==row['encoded_result_bytes']<=limits['encoded_reply_bytes']
            and sha(wire)==row['encoded_result_sha256'],'result_actual_bounded_encoded_identity')
        if index!=1:require(row['encoded_result_bytes']==n and len(wire)>65536,'result_old_reply_limit_exceeded')
    require(len(set(identifiers))==9,'result_nine_fresh_physical_sessions')
    return {'fixed_cases':9,'positive_rows':115,'positive_raw_cell_bytes':raw_expected,
        'positive_encoded_bytes':n,'zero_row_metadata_preserved':True,'all_values_compared_to_native_cli_fixture':True,
        'typed_budget_failures':4,'fresh_connections':9,'all_disconnected':True,
        'partial_result_accepted':False,'product_budget_accepted':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['mysql','mariadb'],required=True)
    parser.add_argument('--record',action='store_true')
    args=parser.parse_args()
    r={'schema':'recastpress.wpdb-result-budget-database.v0.5','passed':False,'outcome':'not_run',
        'observed_at_utc':datetime.now(timezone.utc).isoformat(),'backend':args.backend,
        'synthetic_result_shape_only':True,'wordpress_api_executed':False,'wasm_executed':False,
        'rpc_frames_exercised':False,'product_budget_accepted':False,'source_binary_published':False}
    passed,owned,stage=False,None,'preflight'
    try:
        require(os.name=='nt','result_fixed_windows_comparison')
        before=r['input_files']=build.inputs()
        binary=build.TARGET/'debug/recastpress-wpdb-result-budget-probe.exe'
        receipt=build.verify_build(binary)
        r['build_receipt_sha256']=sha(read(build.RECEIPT,256*1024))
        r['binary_identity']=receipt['executable_identity']
        stage='owned_database';owned=OwnedDatabase(args.backend,r)
        with owned:
            stage='result_budget_execution'
            result=owned.harness.run('owned_result_budget_probe',[str(binary)],timeout=70,
                input_bytes=canonical(owned.request('recastpress.wpdb-result-budget-input.v0.5'))+b'\n',limit=2*1024*1024)
            r['execution']={'exit_code':result.returncode,'stdout_bytes':len(result.stdout),
                'stdout_sha256':sha(result.stdout),'stderr_bytes':len(result.stderr),'stderr_sha256':sha(result.stderr)}
            require(result.stdout.endswith(b'\n') and result.stdout.count(b'\n')==1,'result_one_complete_observation')
            r['observation']=db.parse_json(result.stdout)
            require(result.returncode==0 and not result.stderr,'result_execution_failed')
            r['validation']=validate(r['observation'],args.backend)
            stage='independent_final_verification';owned.verify_final()
            exact(build.inputs(),before,'result_sources_changed')
            exact(build.verify_build(binary),receipt,'result_build_or_driver_changed')
        for key in ['normal_admin_shutdown_verified','database_closed_and_reaped','all_command_children_reaped',
                'independent_native_all_values_verified','independent_final_database_rows_verified']:
            require(r[key] is True,'result_normal_completion_' + key)
        passed=True;r['outcome']='fixed_result_budget_database_passed'
    except (Exception,KeyboardInterrupt) as error:
        r.update(outcome='fixed_result_budget_database_failed',failure_stage=stage,failure_class=type(error).__name__)
        if isinstance(error,ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}',str(error)):r['failure']=str(error)
    r['passed']=passed
    wire=(json.dumps(r,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode()
    if owned is not None:
        for secret in [owned.password,owned.admin_password]:
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire,'result_public_secret_guard')
    require(len(wire)<=2*1024*1024,'result_public_record_quota')
    if args.record:
        destination=HERE/'evidence';destination.mkdir(exist_ok=True)
        name='windows-'+args.backend+('' if passed else '-failed-'+sha(wire)[:16])+'.json'
        path=destination/name
        require(path.resolve()==path and destination.resolve()==destination,'result_record_path_linked')
        if path.exists():require(read(path,2*1024*1024)==wire,'result_immutable_record_changed')
        else:
            with path.open('xb') as stream:stream.write(wire)
    print('Result budget DB: '+r['outcome']+' ('+args.backend+')')
    return 0 if passed else 1


if __name__=='__main__':sys.exit(main())
