"""Owned locked/offline plain-WAT source-FS integration, including raw failures."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import sys
import time
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/wp-source-wasi-probe'
TARGET = WORK / 'cargo-target'
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


bootstrap = load('rp_source_wasi_original_bootstrap', ROOT / 'experiments/wp-bootstrap-probe/prepare.py')
derivation = load('rp_source_wasi_loader_derivation', HERE / 'derivation_gate.py')
OLD_PROOF = ROOT / 'experiments/wp-source-fs-probe/evidence/windows-pure-units.json'
WORDS = ['prestat_errno','preopen_name_bytes','directory_open_errno','directory_fd','readdir_errno','readdir_bytes',
    'file_open_errno','file_fd','prefix_read_errno','prefix_bytes','total_read_bytes','drain_errno','eof_observed',
    'readonly_fd_write_errno','file_close_errno','closed_fd_read_errno','write_open_errno','mkdir_errno','unlink_errno',
    'rename_errno','directory_close_errno','drain_iterations','prestat_name_errno','unexpected_write_fd_close_errno']
SMALL = b'<?php\n// small immutable WordPress-like source\n'
TESTS = ['tests::binary_observer_rejects_partial_and_oversized_frames',
    'tests::fixed_wat_parses_without_eh_and_has_small_bounded_memory',
    'trace_fs::tests::native_separator_adapter_cannot_escape_or_accept_host_prefix',
    'trace_fs::tests::native_separator_adapter_keeps_finite_catalog_and_mutation_denial']


def require(value, reason):
    if not value: raise ValueError(reason)


def sha(wire): return hashlib.sha256(wire).hexdigest()


def read(path, maximum=2*1024*1024):
    require(path.resolve(strict=True) == path and path.is_file() and path.stat().st_size <= maximum, 'source_wasi_fixed_input')
    wire = path.read_bytes(); require(len(wire) <= maximum, 'source_wasi_fixed_input_grew'); return wire


def identity(wire): return {'bytes':len(wire), 'sha256':sha(wire)}


def frozen_source_fs():
    wire = read(OLD_PROOF); value = json.loads(wire)
    require(value['passed'] is True and value['source_reverified_after'] is True
        and len(value['tests']) == 11 and value['catalog_observation']['passed'] is True, 'source_wasi_previous_source_fs_proof')
    for path, expected in value['input_files'].items():
        require(sha(read(ROOT / path)) == expected, 'source_wasi_frozen_input_changed')
    return {'proof_sha256':sha(wire), 'input_files':value['input_files']}


def inputs():
    paths = [HERE / name for name in ['Cargo.toml','Cargo.lock','src/main.rs','src/catalog.rs','src/trace_fs.rs',
        'fixtures/source.wat','derivation_gate.py','check.py']]
    old = frozen_source_fs()
    paths += [ROOT / name for name in old['input_files']]
    paths += [OLD_PROOF, ROOT / 'experiments/php-wasix-wpdb-probe/src/capture.rs',
        ROOT / 'experiments/php-wasix-wpdb-probe/Cargo.lock', ROOT / 'scripts/owned_probe.py']
    return {p.relative_to(ROOT).as_posix():sha(read(p)) for p in sorted(set(paths))}


def artifact(wire, tests):
    selected, finished = [], False
    for line in wire.splitlines():
        value = json.loads(line); require(not finished, 'source_wasi_cargo_after_finished')
        if value.get('reason') == 'build-finished':
            require(value.get('success') is True, 'source_wasi_build_finished_failed'); finished = True
        if value.get('reason') != 'compiler-artifact' or value.get('target',{}).get('name') != 'recastpress-wp-source-wasi-probe': continue
        require(value['target']['kind'] == ['bin'] and value['profile']['test'] is tests, 'source_wasi_artifact_kind')
        require(Path(value['manifest_path']).resolve() == HERE / 'Cargo.toml'
            and Path(value['target']['src_path']).resolve() == HERE / 'src/main.rs', 'source_wasi_artifact_source')
        path = Path(value['executable'])
        require(path.resolve(strict=True) == path and path.is_relative_to(TARGET), 'source_wasi_artifact_path')
        selected.append(path)
    require(finished and len(selected) == 1, 'source_wasi_one_artifact'); return selected[0]


def validate_case(case, expected_entries, expected_body):
    require(type(case['backing_calls']) is list and 0 < len(case['backing_calls']) < 256,
        'source_wasi_complete_bounded_backing_diagnostics')
    for call in case['backing_calls']:
        require(set(call) == {'op','path','ok','error'} and type(call['op']) is str
            and type(call['path']) is str and len(call['path'].encode()) <= 4096
            and type(call['ok']) is bool and (call['error'] is None or type(call['error']) is str),
            'source_wasi_backing_diagnostic_shape')
    for key in ['passed','runtime_run_returned_success','runtime_shutdown_returned','stdout_eof','stderr_eof',
            'source_catalog_counts_unchanged','wasi_syscall_observation_emitted']:
        require(case[key] is True, 'source_wasi_complete_case_' + key)
    require(case['stdout_overflow'] is case['stderr_overflow'] is False and case['stderr_hex'] == ''
        and case['stderr_bytes'] == 0 and case['runtime_shutdown_wait_ms'] == 2000, 'source_wasi_complete_capture')
    require(type(case['stdout_hex']) is str and re.fullmatch('[0-9a-f]*',case['stdout_hex'])
        and len(case['stdout_hex']) <= 2*65*1024, 'source_wasi_raw_stdout_hex')
    raw = bytes.fromhex(case['stdout_hex'])
    require(len(raw) == case['stdout_bytes'] and sha(raw) == case['stdout_sha256'] and raw[:8] == b'RPFSW01\n'
        and len(raw) >= 104, 'source_wasi_raw_frame_identity')
    words = struct.unpack_from('<24I',raw,8); require(case['syscalls'] == dict(zip(WORDS,words)), 'source_wasi_raw_errno_binding')
    require(words[1] <= 128 and words[5] < 32768 and words[9] <= 32
        and len(raw) == 104+words[1]+words[5]+words[9], 'source_wasi_raw_frame_complete')
    for index in [0,2,4,6,8,11,14,20,22]: require(words[index] == 0, 'source_wasi_success_errno')
    require(words[3] > 3 and words[7] > 3 and words[3] != words[7] and words[15] == 8
        and words[12] == 1 and 1 <= words[21] <= 64 and words[10] == len(expected_body), 'source_wasi_fd_read_eof_contract')
    for index in [13,16,17,18,19]: require(words[index] not in (0,0xffffffff), 'source_wasi_mutation_not_denied')
    require(words[23] == 0xffffffff, 'source_wasi_write_open_created_handle')
    preopen = raw[104:104+words[1]]
    require(preopen in (b'/',b'.') and case['preopen_name_hex'] == preopen.hex(), 'source_wasi_actual_preopen')
    start = 104+words[1]; end = start+words[5]; offset = start; entries=[]
    while offset < end:
        require(end-offset >= 24, 'source_wasi_dirent_header')
        cookie, ino, length, kind = struct.unpack_from('<QQIB3x',raw,offset)
        require(0 < length <= 4096 and length <= end-offset-24, 'source_wasi_dirent_length')
        name = raw[offset+24:offset+24+length]
        entries.append({'name_hex':name.hex(),'file_type':kind,'next_cookie':cookie}); offset += 24+length
    require(case['directory_entries'] == entries
        and [(bytes.fromhex(e['name_hex']),e['file_type']) for e in entries] == expected_entries
        and [e['next_cookie'] for e in entries] == list(range(1,len(entries)+1)), 'source_wasi_actual_directory_entries')
    prefix = raw[end:]
    require(prefix == expected_body[:32] and len(prefix) == words[9] and case['prefix_hex'] == prefix.hex(), 'source_wasi_actual_read_prefix')


def validate_result(value, preparation):
    require(value['backing_path_adapter'] == 'windows-separators-to-posix-then-frozen-source-validation'
        and value['windows_backing_separator_conversion_enabled'] is (os.name == 'nt')
        and value['guest_backslash_rejection_verified'] is False, 'source_wasi_backing_adapter_scope')
    require(value['schema'] == 'recastpress.wp-source-wasi-observation.v0.5' and value['passed'] is True
        and value['outcome'] == 'fixed_source_wasi_small_and_complete_catalog_passed'
        and value['module_compilations'] == 1 and value['wasi_executed'] is True, 'source_wasi_actual_guest_success')
    for key in ['php_executed','database_connected','networking_enabled','host_environment_forwarded','host_home_mapped',
            'product_runtime_selected','native_metadata_compatibility_verified','all_runtime_tasks_independently_enumerated']:
        require(value[key] is False, 'source_wasi_unclaimed_' + key)
    require(value['module']['exception_handling_used'] is False and value['module']['max_memory_pages'] == 2
        and value['module']['source_wat_sha256'] == sha(read(HERE / 'fixtures/source.wat')), 'source_wasi_plain_fixed_wat')
    small = [(b'.',3),(b'..',3),(b'class-wpdb.php',4),(b'empty',3),(b'nested',3)]
    validate_case(value['small'],small,SMALL)
    manifest = json.loads(read(bootstrap.SOURCE_MANIFEST))
    names = [(b'.',3),(b'..',3)]
    for group, kind in [('files',4),('directories',3)]:
        for name in manifest[group]:
            parent, _, base = name.rpartition('/')
            if parent == 'wp-includes': names.append((base.encode(),kind))
    names.sort()
    body = read(bootstrap.WP_SITE / 'wp-includes/class-wpdb.php',512*1024)
    require(sha(body) == manifest['files']['wp-includes/class-wpdb.php']['sha256'], 'source_wasi_oracle_source_bytes')
    validate_case(value['full'],names,body)
    require(value['catalog_source']['source_manifest_sha256'] == preparation['source_manifest']['sha256'], 'source_wasi_full_manifest_binding')


def preserve_write(path, wire):
    require(path.resolve() == path and path.parent.resolve() == path.parent, 'source_wasi_evidence_linked')
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        old = read(path,2*1024*1024); saved = path.with_name(path.stem + '-before-' + sha(old)[:16] + path.suffix)
        if saved.exists(): require(read(saved) == old, 'source_wasi_old_evidence_changed')
        else:
            with saved.open('xb') as stream: stream.write(old)
    path.write_bytes(wire)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--record',action='store_true');args=parser.parse_args()
    value={'schema':'recastpress.wp-source-wasi-check.v0.5','passed':False,'outcome':'not_run',
        'observed_at_utc':datetime.now(timezone.utc).isoformat(),'steps':[],'locked_offline':True,
        'owned_cleanup_completed':False,'source_reverified_after':False,'wasi_executed':False,'php_executed':False,
        'database_connected':False,'http_executed':False,'native_metadata_compatibility_verified':False,
        'product_runtime_selected':False,'failure_reclassified_as_pure_success':False}
    environment={k:v for k,v in os.environ.items() if not k.upper().startswith(('MYSQL_','MARIADB_','RP_','GH_','GITHUB_'))
        and k.upper() not in {'PHPRC','PHP_INI_SCAN_DIR','ACTIONS_RUNTIME_TOKEN','RUST_LOG','RUST_BACKTRACE'}}
    environment.update(CARGO_NET_OFFLINE='true',CARGO_BUILD_JOBS='2',CARGO_INCREMENTAL='0')
    passed=False;raw_stdout=b'';raw_stderr=b'';stage='preflight'
    def run(stage, command, timeout, must_pass=True):
        started=time.monotonic();pid,result=owned_probe.run_captured_probe(command,environment,timeout=timeout)
        require(WORK.resolve() == WORK,'source_wasi_work_linked');WORK.mkdir(parents=True,exist_ok=True)
        for suffix,wire in [('stdout',result.stdout),('stderr',result.stderr)]:
            path=WORK/(stage+'.'+suffix);require(path.resolve()==path,'source_wasi_log_linked');path.write_bytes(wire)
        value['steps'].append({'stage':stage,'owned_pid':pid,'owned_reaped':True,'exit_code':result.returncode,
            'timeout_seconds':timeout,'elapsed_seconds':round(time.monotonic()-started,3),
            'stdout_sha256':sha(result.stdout),'stderr_sha256':sha(result.stderr)})
        require(len(result.stdout)<=16*1024*1024 and len(result.stderr)<=65536,'source_wasi_capture_budget')
        if must_pass:require(result.returncode==0,stage+'_failed')
        return result
    try:
        before=value['input_files']=inputs();value['loader_derivation']=derivation.verify();value['frozen_source_fs_before']=frozen_source_fs()
        base=['--manifest-path',str(HERE/'Cargo.toml'),'--target-dir',str(TARGET),'--locked','--offline']
        stage='test_build';built=run(stage,['cargo','test',*base,'--no-run','--message-format=json'],1200)
        test_exe=artifact(built.stdout,True);listed=run('test_list',[str(test_exe),'--list','--format','terse'],10)
        names=[line[:-6] for line in listed.stdout.decode().splitlines() if line.endswith(': test')]
        require(sorted(names)==TESTS and not listed.stderr,'source_wasi_four_pure_units')
        tested=run('test_run',[str(test_exe),'--test-threads=1','--show-output'],30)
        output=tested.stdout.decode().replace('\r\n','\n')
        require(not tested.stderr and 'test result: ok. 4 passed; 0 failed; 0 ignored;' in output
            and sorted(re.findall(r'^test (\S+) \.\.\. ok$',output,re.M))==TESTS,'source_wasi_actual_four_units')
        value['units']={'tests':names,'passed':True,'stdout_sha256':sha(tested.stdout)}
        stage='normal_build';built=run(stage,['cargo','build',*base,'--message-format=json'],1200);exe=artifact(built.stdout,False)
        binary_identity=identity(read(exe,256*1024*1024));value['binary']={'path':exe.relative_to(ROOT).as_posix(),**binary_identity}
        value['preparation_before']=bootstrap.verify_prepared()
        stage='wasi_execution';executed=run(stage,[str(exe)],120,False);raw_stdout,raw_stderr=executed.stdout,executed.stderr
        value['raw_stdout']=identity(raw_stdout);value['raw_stderr']=identity(raw_stderr)
        require(raw_stdout.endswith(b'\n') and raw_stdout.count(b'\n')==1,'source_wasi_one_host_record')
        inner=value['inner_result']=json.loads(raw_stdout);value['wasi_executed']=inner.get('wasi_executed') is True
        require(executed.returncode==0 and not raw_stderr,'source_wasi_native_process_failed')
        validate_result(inner,value['preparation_before'])
        require(identity(read(exe,256*1024*1024))==binary_identity,'source_wasi_binary_changed')
        passed=True;value['outcome']='fixed_plain_wasi_small_and_full_source_contract_passed'
    except (Exception,KeyboardInterrupt) as error:
        value.update(outcome='source_wasi_check_failed',failure_stage=stage,failure_class=type(error).__name__)
        if isinstance(error,ValueError) and re.fullmatch('[a-z_]{1,100}',str(error)):value['failure']=str(error)
    finally:
        try:owned_probe.reap_owned_probes();value['owned_cleanup_completed']=True
        except (Exception,KeyboardInterrupt):passed=False;value['outcome']='source_wasi_owned_cleanup_failed'
        if 'input_files' in value:
            try:
                require(inputs()==value['input_files'],'source_wasi_inputs_changed')
                require(derivation.verify()==value['loader_derivation'],'source_wasi_loader_changed')
                value['frozen_source_fs_after']=frozen_source_fs()
                require(value['frozen_source_fs_after']==value['frozen_source_fs_before'],'source_wasi_previous_proof_changed')
                value['source_reverified_after']=True
                if 'preparation_before' in value:
                    value['preparation_after']=bootstrap.verify_prepared()
                    require(value['preparation_after']==value['preparation_before'],'source_wasi_full_source_after_failure_changed')
            except (Exception,KeyboardInterrupt) as error:passed=False;value.update(outcome='source_wasi_after_verification_failed',after_failure_class=type(error).__name__)
    value['passed']=passed
    if args.record:
        preserve_write(HERE/'evidence/windows-wasi.stdout.bin',raw_stdout)
        preserve_write(HERE/'evidence/windows-wasi.stderr.bin',raw_stderr)
        preserve_write(HERE/'evidence/windows-wasi.json',(json.dumps(value,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode())
    print('Source WASI: '+value['outcome']);return 0 if passed else 1


if __name__=='__main__':sys.exit(main())
