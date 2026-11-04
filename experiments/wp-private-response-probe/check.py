"""Locked/offline codec checks against immutable public native and Wasm records.

This harness starts only owned Rust compiler/test/codec processes. It does not
execute PHP, Wasm, a database or an HTTP server, nor authorize any publication.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/wp-private-response-probe'
TARGET = WORK / 'cargo-target'
NAME = 'recastpress-wp-private-response-probe'
RECEIPT = HERE / ('evidence/' + ('windows' if os.name == 'nt' else 'linux') + '.json')
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe

TESTS = sorted([
    'cgi_response::tests::' + name for name in (
        'default_status_redirect_and_explicit_redirect_are_distinct',
        'header_budgets_include_the_terminating_blank_line',
        'no_content_and_not_modified_keep_their_different_length_rules',
        'preserves_header_value_bytes_but_never_reparses_body',
        'rejects_ambiguous_status_and_nonfinal_status',
        'rejects_folding_controls_mixed_endings_and_hop_headers',
        'rejects_smuggling_length_forms_even_with_identical_duplicates',
        'singleton_fields_cannot_be_combined_but_repeatable_fields_survive',
        'wordpress_error_preserves_separate_cookies_and_binary_body')
] + ['tests::' + name for name in (
    'arbitrary_private_binary_and_original_headers_are_retained',
    'body_json_and_synthetic_status_header_budgets_have_exact_boundaries',
    'empty_no_content_and_304_representation_length_follow_the_original_parser',
    'exact_typed_five_fields_reject_duplicates_unknown_missing_and_trailing_json',
    'framing_singletons_status_and_hop_headers_cannot_smuggle',
    'lowercase_hex_length_and_digest_are_all_checked',
    'public_errors_and_summaries_never_format_unknown_private_input')])


def require(value, code):
    if not value: raise ValueError(code)


def sha(wire): return hashlib.sha256(wire).hexdigest()
def identity(wire): return {'bytes': len(wire), 'sha256': sha(wire)}


def read(path, limit=16 * 1024 * 1024):
    require(path.resolve(strict=True) == path and path.is_file() and path.stat().st_size <= limit,
        'fixed_regular_input')
    wire = path.read_bytes()
    require(len(wire) <= limit, 'input_grew')
    return wire


def native_path(backend):
    return ROOT / f'experiments/wp-request-context-probe/evidence/windows-{backend}-native.json'


def http_path(backend, leaf):
    return ROOT / f'experiments/php-wasix-wp-http-probe/evidence/ci-41750117934-{backend}/{leaf}'


def corpus_inputs():
    paths = []
    for backend in ('mysql', 'mariadb'):
        paths += [native_path(backend), http_path(backend, 'provenance.json'),
            http_path(backend, f'raw/experiments/php-wasix-wp-http-probe/evidence/linux-{backend}.json')]
    return paths


def inputs():
    paths = [HERE / name for name in ('Cargo.toml','Cargo.lock','src/lib.rs','src/main.rs','check.py')]
    paths += [ROOT / 'scripts/owned_probe.py', ROOT / 'Cargo.lock',
        ROOT / 'experiments/php-wasix-wp-http-probe/Cargo.lock',
        ROOT / 'experiments/php-wasi-host-probe/src/cgi_response.rs',
        ROOT / '.github/workflows/wp-private-response.yml'] + corpus_inputs()
    return {p.relative_to(ROOT).as_posix(): sha(read(p)) for p in paths}


def dependency_gate():
    known = set()
    for p in (ROOT / 'Cargo.lock', ROOT / 'experiments/php-wasix-wp-http-probe/Cargo.lock'):
        known.update((x['name'], x['version'], x.get('source'), x.get('checksum'))
            for x in tomllib.loads(read(p).decode())['package'])
    packages = tomllib.loads(read(HERE / 'Cargo.lock').decode())['package']
    for p in packages:
        require(p['name'] == NAME and 'source' not in p or
            (p['name'],p['version'],p.get('source'),p.get('checksum')) in known,
            'dependency_outside_accepted_locks')
    return {'packages':len(packages),'versions_and_checksums_from_accepted_locks':True}


def previous_artifacts():
    result = {}
    for name in ('php-wasix-wp-bootstrap-probe','php-wasix-wp-request-probe',
            'php-wasix-wp-request-context-probe','php-wasix-wp-http-probe'):
        p = ROOT / f'experiments/{name}/evidence/windows-build.json'
        wire = read(p); receipt = json.loads(wire)
        selected = {p.relative_to(ROOT).as_posix():identity(wire)}
        for key, expected in [(receipt['executable'],receipt['executable_identity']),
                (receipt['units']['executable'],receipt['units']['executable_identity'])]:
            binary = ROOT / key
            target = 'php-wasix-wp-http-probe' if name == 'php-wasix-wp-http-probe' else 'php-wasix-wp-bootstrap-probe'
            require(binary.is_relative_to(ROOT / f'target/{target}/cargo-target'), 'old_binary_target')
            actual = identity(read(binary,256*1024*1024))
            require(actual == expected, 'old_binary_identity_changed')
            selected[key] = actual
        result[name] = selected
    return result


def environment():
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('MYSQL_','MARIADB_','RP_','GH_','GITHUB_'))
        and k.upper() not in {'RUST_LOG','RUST_BACKTRACE','PHPRC','PHP_INI_SCAN_DIR','ACTIONS_RUNTIME_TOKEN'}}
    env.update(CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='2', CARGO_INCREMENTAL='0')
    return env


def child(stage, command, report, payload=None, expected=0, timeout=120):
    pid, value = owned_probe.run_captured_probe(command, environment(), timeout=timeout, input_bytes=payload)
    require(WORK.resolve() == WORK, 'owned_work_path'); WORK.mkdir(parents=True,exist_ok=True)
    for label, wire in [('stdout',value.stdout),('stderr',value.stderr)]:
        path = WORK / (stage + '.' + label)
        require(path.resolve() == path, 'owned_capture_path'); path.write_bytes(wire)
    report['commands'].append({'stage':stage,'owned_pid':pid,'owned_reaped':True,
        'exit_code':value.returncode,'timeout_seconds':timeout,
        'stdout':identity(value.stdout),'stderr':identity(value.stderr)})
    require(value.returncode == expected and len(value.stdout) <= 8*1024*1024
        and len(value.stderr) <= 65536, 'owned_child_failed')
    return value


def artifact(wire, tests):
    selected = []; finished = False
    for line in wire.splitlines():
        row = json.loads(line); require(not finished, 'cargo_after_finish')
        if row.get('reason') == 'build-finished':
            require(row['success'] is True, 'cargo_build_failed'); finished = True
        if row.get('reason') != 'compiler-artifact': continue
        expected = NAME.replace('-','_') if tests else NAME
        if row.get('target',{}).get('name') != expected or row['profile']['test'] is not tests: continue
        require(row['target']['kind'] == (['lib'] if tests else ['bin']) and
            Path(row['manifest_path']).resolve() == HERE / 'Cargo.toml', 'cargo_artifact_source')
        binary = Path(row['executable'])
        require(binary.resolve(strict=True) == binary and binary.is_relative_to(TARGET), 'owned_artifact_path')
        selected.append(binary)
    require(finished and len(selected) == 1, 'one_cargo_artifact')
    return selected[0]


def fixture_cases():
    result = []
    for backend in ('mysql','mariadb'):
        native = json.loads(read(native_path(backend)))
        provenance = json.loads(read(http_path(backend,'provenance.json')))
        run_wire = read(http_path(backend,f'raw/experiments/php-wasix-wp-http-probe/evidence/linux-{backend}.json'))
        require(provenance['verification']['overall_positive'] is True and
            provenance['raw_json']['run.json'] == identity(run_wire), 'accepted_http_record_identity')
        run = json.loads(run_wire)
        for index, case in enumerate(('home','missing','missing_repeat')):
            expected = (native['requests']['missing_repeat']['observation']['response'] if case == 'missing_repeat'
                else native['observations'][case]['response'])
            actual = run['http_server_observation']['requests'][index]['observation']['php_result']['response']
            require(actual == expected, 'accepted_native_wasm_response_equality')
            result += [(f'native-{backend}-{case}',expected),(f'wasm-{backend}-{case}',actual)]
    return result


def codec_cases(binary, report):
    records = []
    for label, response in fixture_cases():
        payload = json.dumps(response,separators=(',',':')).encode()
        result = child(label,[str(binary)],report,payload=payload,timeout=10)
        require(not result.stderr, 'codec_stderr')
        observed = json.loads(result.stdout)
        require(observed['schema'] == 'recastpress.wp-private-response-summary.v0.5'
            and observed['passed'] is True, 'codec_summary_schema')
        for key in ('publication_authorized','execution_completion_verified','native_comparison_performed',
                'php_wasm_database_or_http_executed'):
            require(observed[key] is False, 'codec_unclaimed_scope')
        summary = observed['summary']
        require(set(summary) == {'status','header_count','body_bytes','body_sha256','representation_length'}
            and summary['status'] == response['status'] and summary['header_count'] == len(response['headers_hex'])
            and summary['body_bytes'] == response['body_bytes'] and summary['body_sha256'] == response['body_sha256']
            and summary['representation_length'] is None, 'exact_codec_safe_summary')
        records.append({'case':label,'input':identity(payload),'summary':summary})
    report['public_fixture_cases'] = records


def private_cli_cases():
    body=b'private-codec-cli-placeholder\x00\xff'
    response={'status':201,'headers_hex':[b'X-Private: private-header-placeholder'.hex()],
        'body_bytes':len(body),'body_hex':body.hex(),'body_sha256':sha(body)}
    scope={'schema':'recastpress.wp-private-response-summary.v0.5',
        'publication_authorized':False,'execution_completion_verified':False,
        'native_comparison_performed':False,'php_wasm_database_or_http_executed':False}
    payload=json.dumps(response,separators=(',',':')).encode()
    positive={**scope,'passed':True,'summary':{'status':201,'header_count':1,
        'body_bytes':len(body),'body_sha256':sha(body),'representation_length':None}}
    bad={**response,'headers_hex':[b'Transfer-Encoding: private-error-placeholder'.hex()]}
    return [('private-unknown',payload,0,positive),
        ('private-malformed',json.dumps(bad,separators=(',',':')).encode(),1,
            {**scope,'passed':False,'error':'response_cgi_frame'}),
        ('private-duplicate',payload.replace(b'"status":201',b'"status":201,"status":201',1),1,
            {**scope,'passed':False,'error':'response_json_shape'})]


def private_cli_gate(binary,report):
    rows=[]
    for label,payload,exit_code,expected in private_cli_cases():
        result=child(label,[str(binary)],report,payload=payload,expected=exit_code,timeout=10)
        require(not result.stderr and result.stdout.endswith(b'\n')
            and result.stdout.count(b'\n')==1 and json.loads(result.stdout)==expected,
            'private_cli_diagnostics_exact_and_redacted')
        rows.append({'case':label,'input':identity(payload),'output':expected})
    report['private_cli_cases']=rows


def verify():
    value = json.loads(read(RECEIPT))
    require(value['schema']=='recastpress.wp-private-response-check.v0.5'
        and value['outcome']=='private_response_codec_passed'
        and value['passed'] is True and value['input_files'] == inputs()
        and value['dependency_gate'] == dependency_gate() and value['source_reverified_after'] is True,
        'codec_receipt_inputs_changed')
    for key in ('owned_cleanup_completed','locked_offline'):
        require(value[key] is True, 'codec_receipt_completion')
    for key in ('native_comparison_in_codec','publication_authorized','product_runtime_selected',
            'php_wasm_database_or_http_executed'):
        require(value[key] is False, 'codec_receipt_unclaimed_scope')
    require(value['units']['passed'] is True and value['units']['tests']==TESTS,'exact_codec_unit_inventory')
    expected_cases=[]
    for label, response in fixture_cases():
        expected_cases.append({'case':label,
            'input':identity(json.dumps(response,separators=(',',':')).encode()),
            'summary':{'status':response['status'],'header_count':len(response['headers_hex']),
                'body_bytes':response['body_bytes'],'body_sha256':response['body_sha256'],
                'representation_length':None}})
    require(value['public_fixture_cases']==expected_cases,'codec_receipt_corpus')
    expected_private=[{'case':label,'input':identity(payload),'output':expected}
        for label,payload,exit_code,expected in private_cli_cases()]
    require(value['private_cli_cases']==expected_private,'codec_receipt_private_diagnostics')
    stages=['unit-build','unit-list','unit-run','codec-build']+[x['case'] for x in expected_cases]
    stages += [x['case'] for x in expected_private]
    require(len(value['commands'])==len(stages),'codec_receipt_stage_count')
    for row,stage in zip(value['commands'],stages):
        expected_exit=1 if stage in ('private-malformed','private-duplicate') else 0
        require(row['stage']==stage and row['owned_reaped'] is True and type(row['owned_pid']) is int
            and row['owned_pid']>0 and type(row['exit_code']) is int and row['exit_code']==expected_exit,
            'codec_receipt_owned_stage')
        for key in ('stdout','stderr'):
            item=row[key]
            require(set(item)=={'bytes','sha256'} and type(item['bytes']) is int
                and 0<=item['bytes']<=8*1024*1024 and re.fullmatch('[a-f0-9]{64}',item['sha256']) is not None,
                'codec_receipt_capture_identity')
    for entry in (value['units'],value['executable']):
        p=ROOT / entry['path']
        require(p.is_relative_to(TARGET) and identity(read(p,128*1024*1024)) == entry['identity'], 'codec_binary_changed')
    if os.name == 'nt': require(value['old_artifacts_before'] == value['old_artifacts_after'] == previous_artifacts(),
        'accepted_previous_artifacts_changed')
    return value


def run():
    report = {'schema':'recastpress.wp-private-response-check.v0.5','passed':False,
        'observed_at_utc':datetime.now(timezone.utc).isoformat(),'commands':[],
        'native_comparison_in_codec':False,'publication_authorized':False,'product_runtime_selected':False,
        'php_wasm_database_or_http_executed':False,'source_reverified_after':False,
        'owned_cleanup_completed':False,'locked_offline':True}
    passed = False
    try:
        report['input_files']=inputs(); report['dependency_gate']=dependency_gate()
        if os.name == 'nt': report['old_artifacts_before']=previous_artifacts()
        profile = ['--release'] if sys.platform == 'linux' else []
        base = ['--manifest-path',str(HERE/'Cargo.toml'),'--target-dir',str(TARGET),'--locked','--offline']
        built=child('unit-build',['cargo','test',*profile,'--lib','--no-run',*base,'--message-format=json'],report,timeout=600)
        unit=artifact(built.stdout,True)
        listed=child('unit-list',[str(unit),'--list','--format','terse'],report,timeout=10)
        tests=sorted(line[:-6] for line in listed.stdout.decode().splitlines() if line.endswith(': test'))
        require(tests==TESTS and not listed.stderr,'unit_inventory')
        tested=child('unit-run',[str(unit),'--test-threads=1'],report,timeout=60)
        text=tested.stdout.decode().replace('\r\n','\n')
        require(not tested.stderr and f'test result: ok. {len(tests)} passed; 0 failed; 0 ignored;' in text
            and sorted(re.findall(r'^test (\S+) \.\.\. ok$',text,re.M)) == tests,'all_listed_units_passed')
        report['units']={'path':unit.relative_to(ROOT).as_posix(),'identity':identity(read(unit,128*1024*1024)),
            'tests':tests,'passed':True}
        built=child('codec-build',['cargo','build',*profile,*base,'--message-format=json'],report,timeout=600)
        binary=artifact(built.stdout,False)
        report['executable']={'path':binary.relative_to(ROOT).as_posix(),'identity':identity(read(binary,128*1024*1024))}
        codec_cases(binary,report); private_cli_gate(binary,report); passed=True
    except (Exception,KeyboardInterrupt) as error:
        report['failure_class']=type(error).__name__
        if isinstance(error,ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}',str(error)):
            report['failure']=str(error)
    finally:
        try:
            owned_probe.reap_owned_probes(); report['owned_cleanup_completed']=True
            require(inputs()==report['input_files'],'codec_sources_changed')
            if os.name == 'nt':
                report['old_artifacts_after']=previous_artifacts()
                require(report['old_artifacts_before']==report['old_artifacts_after'],'old_artifacts_changed')
            report['source_reverified_after']=True
        except (Exception,KeyboardInterrupt) as error:
            passed=False; report['final_check_failure_class']=type(error).__name__
            if isinstance(error,ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}',str(error)):
                report['final_check_failure']=str(error)
    report['passed']=passed
    report['outcome']='private_response_codec_passed' if passed else 'private_response_codec_failed'
    RECEIPT.parent.mkdir(parents=True,exist_ok=True)
    require(RECEIPT.resolve()==RECEIPT,'owned_receipt_path')
    RECEIPT.write_bytes((json.dumps(report,indent=2,allow_nan=False)+'\n').encode())
    print(report['outcome'])
    return 0 if passed else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    if args.verify_only:
        verify(); print('private_response_codec_receipt_verified')
    else: sys.exit(run())
