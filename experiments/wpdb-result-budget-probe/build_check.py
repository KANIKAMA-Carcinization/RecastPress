"""Bounded locked/offline build for synthetic result budgets; never starts a DB."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGET = ROOT / 'target/wpdb-result-budget-probe/cargo-target'
RECEIPT = HERE / 'evidence/windows-build.json'
spec = importlib.util.spec_from_file_location('rp_result_readonly_driver_gate',
    ROOT / 'experiments/wpdb-crud-probe/build_check.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
require, read, sha = previous.require, previous.read, previous.sha
owned_probe = previous.owned_probe
NAME = 'recastpress-wpdb-result-budget-probe'
TESTS = sorted('tests::' + name for name in [
    'metadata_and_zero_rows_are_preserved', 'binary_null_empty_and_raw_bytes_are_exact',
    'row_and_width_limits_return_no_materialized_result', 'cell_and_raw_boundaries_are_exact',
    'encoded_boundary_and_metadata_limits_fail_closed', 'limits_cannot_enlarge_candidate_budget',
    'input_unknown_duplicate_missing_and_destination_rejected'])


def inputs():
    paths = [HERE / name for name in ['Cargo.toml','Cargo.lock','src/lib.rs','src/main.rs','build_check.py',
        'database_check.py','owned_database.py']]
    result = previous.inputs()
    result.update({p.relative_to(ROOT).as_posix(): sha(read(p, 2*1024*1024)) for p in paths})
    return dict(sorted(result.items()))


def driver_gate():
    return previous.driver_gate()


def environment():
    result = previous.environment()
    result.update(CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='2', CARGO_INCREMENTAL='0')
    return result


def artifact(wire, tests):
    require(len(wire) <= 16*1024*1024, 'result_build_capture_quota')
    selected, finished = [], False
    for line in wire.splitlines():
        value = json.loads(line)
        require(not finished, 'result_build_after_finished')
        if value.get('reason') == 'build-finished':
            require(value['success'] is True, 'result_build_finished_failed')
            finished = True
        if value.get('reason') != 'compiler-artifact' or not value.get('executable'):
            continue
        if value.get('profile',{}).get('test') is not tests:
            continue
        target = value['target']
        require(target['name'] in (NAME, NAME.replace('-', '_'))
            and target['kind'] == (['lib'] if tests else ['bin']), 'result_artifact_kind')
        require(Path(value['manifest_path']).resolve() == HERE / 'Cargo.toml'
            and Path(target['src_path']).resolve() == HERE / ('src/lib.rs' if tests else 'src/main.rs'),
            'result_artifact_source')
        path = Path(value['executable'])
        require(path.resolve(strict=True) == path and path.is_relative_to(TARGET), 'result_artifact_path')
        selected.append(path)
    require(finished and len(selected) == 1, 'result_one_artifact_required')
    return selected[0]


def child(stage, command, deadline, result):
    start = time.monotonic()
    pid, observed = owned_probe.run_captured_probe(command, environment(), timeout=deadline)
    work = TARGET.parent
    require(work.resolve() == work, 'result_build_work_linked')
    work.mkdir(parents=True, exist_ok=True)
    for label, wire in [('stdout',observed.stdout),('stderr',observed.stderr)]:
        path = work / (stage + '.' + label)
        require(path.resolve() == path, 'result_build_log_linked')
        path.write_bytes(wire)
    result['commands'].append({'stage':stage,'timeout_seconds':deadline,'owned_pid':pid,'owned_reaped':True,
        'exit_code':observed.returncode,'elapsed_seconds':round(time.monotonic()-start,3),
        'stdout_sha256':sha(observed.stdout),'stderr_sha256':sha(observed.stderr)})
    require(len(observed.stdout) <= 16*1024*1024 and len(observed.stderr) <= 65536
        and observed.returncode == 0, 'result_owned_build_child_failed')
    return observed


def verify_build(binary):
    value = json.loads(read(RECEIPT, 256*1024))
    require(value['schema'] == 'recastpress.wpdb-result-budget-build.v0.5'
        and value['passed'] is True and value['outcome'] == 'locked_offline_result_budget_build_passed'
        and value['owned_cleanup_completed'] is True and value['source_reverified_after'] is True
        and value['locked_offline'] is True, 'result_build_receipt_failed')
    require(value['input_files'] == inputs() and value['driver_before'] == value['driver_after'] == driver_gate(),
        'result_driver_or_source_changed')
    require(value['units']['passed'] is True and value['units']['tests'] == TESTS, 'result_actual_unit_inventory')
    require(len(value['commands']) == 4, 'result_four_build_stages')
    for row,(name,deadline) in zip(value['commands'],[('unit-build',1200),('unit-list',10),('unit-run',30),('normal-build',1200)]):
        require(row['stage'] == name and row['timeout_seconds'] == deadline and row['owned_reaped'] is True
            and type(row['owned_pid']) is int and row['owned_pid'] > 0 and type(row['exit_code']) is int
            and row['exit_code'] == 0, 'result_build_stage')
    binary = Path(binary).absolute()
    require(binary.resolve(strict=True) == binary and binary.is_relative_to(TARGET)
        and value['executable'] == binary.relative_to(ROOT).as_posix()
        and previous.prep.identity(read(binary,256*1024*1024)) == value['executable_identity'], 'result_executable_changed')
    unit = ROOT / value['units']['executable']
    require(unit.resolve(strict=True) == unit and unit.is_relative_to(TARGET)
        and previous.prep.identity(read(unit,256*1024*1024)) == value['units']['executable_identity'], 'result_unit_binary_changed')
    for key in ['database_started','database_connected','driver_handshake_executed','wasm_executed','product_budget_accepted']:
        require(value[key] is False, 'result_unclaimed_build_scope')
    return value


def run_build(record=True):
    value = {'schema':'recastpress.wpdb-result-budget-build.v0.5','outcome':'not_run','passed':False,
        'observed_at_utc':datetime.now(timezone.utc).isoformat(),'commands':[],'locked_offline':True,
        'owned_cleanup_completed':False,'source_reverified_after':False,'database_started':False,
        'database_connected':False,'driver_handshake_executed':False,'wasm_executed':False,'product_budget_accepted':False}
    passed = False
    try:
        require(os.name == 'nt' and TARGET.resolve() == TARGET, 'result_fixed_build_environment')
        value['input_files'] = inputs()
        value['driver_before'] = driver_gate()
        base = ['--manifest-path',str(HERE/'Cargo.toml'),'--target-dir',str(TARGET),'--locked','--offline']
        observed = child('unit-build',['cargo','test','--lib','--no-run',*base,'--message-format=json'],1200,value)
        unit = artifact(observed.stdout, True)
        unit_identity = previous.prep.identity(read(unit,256*1024*1024))
        listed = child('unit-list',[str(unit),'--list','--format','terse'],10,value)
        require(not listed.stderr, 'result_unit_list_stderr')
        names = sorted(line[:-6] for line in listed.stdout.decode('ascii').splitlines() if line.endswith(': test'))
        require(names == TESTS, 'result_unit_count_or_names')
        tested = child('unit-run',[str(unit),'--test-threads=1','--show-output'],30,value)
        output = tested.stdout.decode('ascii').replace('\r\n','\n')
        require(not tested.stderr and 'test result: ok. 7 passed; 0 failed; 0 ignored;' in output
            and sorted(re.findall(r'^test (\S+) \.\.\. ok$',output,re.M)) == TESTS, 'result_all_seven_units')
        require(previous.prep.identity(read(unit,256*1024*1024)) == unit_identity, 'result_unit_changed')
        value['units'] = {'passed':True,'tests':names,'executable':unit.relative_to(ROOT).as_posix(),
            'executable_identity':unit_identity,'stdout_sha256':sha(tested.stdout)}
        observed = child('normal-build',['cargo','build','--bin',NAME,*base,'--message-format=json'],1200,value)
        binary = artifact(observed.stdout, False)
        value.update(executable=binary.relative_to(ROOT).as_posix(),
            executable_identity=previous.prep.identity(read(binary,256*1024*1024)))
        passed = True
    except (Exception,KeyboardInterrupt) as error:
        value['failure_class'] = type(error).__name__
        if isinstance(error,ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}',str(error)):value['failure']=str(error)
    finally:
        try:
            owned_probe.reap_owned_probes();value['owned_cleanup_completed']=True
            require(inputs() == value['input_files'], 'result_build_sources_changed')
            value['driver_after']=driver_gate()
            require(value['driver_after'] == value['driver_before'], 'result_driver_stage_changed')
            value['source_reverified_after']=True
        except (Exception,KeyboardInterrupt) as error:
            passed=False;value['after_failure_class']=type(error).__name__
    value['passed']=passed
    value['outcome']='locked_offline_result_budget_build_passed' if passed else 'result_budget_build_failed'
    if record:
        RECEIPT.parent.mkdir(exist_ok=True)
        require(RECEIPT.parent.resolve() == RECEIPT.parent and RECEIPT.resolve() == RECEIPT, 'result_receipt_linked')
        wire=(json.dumps(value,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode()
        if RECEIPT.exists():
            old=read(RECEIPT,256*1024);backup=RECEIPT.with_name('windows-build-before-'+sha(old)[:16]+'.json')
            if backup.exists():require(read(backup,256*1024)==old,'result_prior_receipt_changed')
            else:
                with backup.open('xb') as stream:stream.write(old)
        RECEIPT.write_bytes(wire)
    return value


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--record',action='store_true')
    args=parser.parse_args();result=run_build(record=args.record)
    print('Result budget build: '+result['outcome']);sys.exit(0 if result['passed'] else 1)
