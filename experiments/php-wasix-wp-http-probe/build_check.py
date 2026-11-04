"""Owned locked/offline Rust-only build; never executes PHP, Wasm or a DB."""
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
TARGET = ROOT / 'target/php-wasix-wp-http-probe/cargo-target'
WORK = ROOT / 'target/php-wasix-wp-http-probe'
NAME = 'recastpress-php-wasix-wp-http-probe'
RECEIPT = HERE / ('evidence/' + ('windows' if os.name == 'nt' else 'linux') + '-build.json')
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


driver = load('rp_full_wasm_driver_readonly', ROOT / 'experiments/wpdb-rpc-probe/prepare_driver.py')
sourcefs = load('rp_full_wasm_source_fs_readonly', ROOT / 'experiments/wp-source-fs-probe/check.py')
derivation = load('rp_full_wasm_host_derivation', HERE / 'derivation_gate.py')
managed = load('rp_home_managed_inputs', ROOT / 'experiments/php-wasix-managed-access-probe/managed_check.py')
oldbuild = load('rp_context_frozen_bootstrap_build', ROOT / 'experiments/php-wasix-wp-bootstrap-probe/build_check.py')
themebuild = load('rp_http_frozen_theme_build', ROOT / 'experiments/php-wasix-wp-request-probe/build_check.py')
contextbuild = load('rp_http_frozen_context_build', ROOT / 'experiments/php-wasix-wp-request-context-probe/build_check.py')
require, read, sha = driver.require, driver.read_fixed, driver.sha
TESTS = sorted([
    *['cgi_response::tests::'+x for x in [
        'wordpress_error_preserves_separate_cookies_and_binary_body',
        'default_status_redirect_and_explicit_redirect_are_distinct',
        'rejects_ambiguous_status_and_nonfinal_status',
        'rejects_smuggling_length_forms_even_with_identical_duplicates',
        'rejects_folding_controls_mixed_endings_and_hop_headers',
        'preserves_header_value_bytes_but_never_reparses_body',
        'header_budgets_include_the_terminating_blank_line',
        'no_content_and_not_modified_keep_their_different_length_rules',
        'singleton_fields_cannot_be_combined_but_repeatable_fields_survive']],
    *['response_contract::tests::'+x for x in [
        'context_snapshot_mismatch_cannot_publish_unknown_values',
        'fixed_native_context_routes_are_distinct_and_require_all_stages',
        'exact_response_keeps_all_original_bytes_and_header_order',
        'mismatched_private_html_and_headers_only_yield_safe_actual_metrics',
        'oversized_incomplete_and_forged_response_frames_are_never_public']],
    *['tests::'+x for x in [
        'private_line_preserves_control_byte_and_rejects_missing_or_large_line',
        'strict_private_input_rejects_unknown_duplicate_and_destination_changes',
        'finite_http_profile_rejects_before_any_worker_is_selected',
        'complete_inner_response_keeps_binary_body_and_per_name_cookie_order',
        'fixed_worker_contract_rejects_incomplete_cleanup_and_unknown_context',
        'mock_capture_child',
        'owned_capture_reaps_normal_overflow_timeout_and_child_panic']],
])

STAGES = [('unit-build', 1200), ('unit-list', 10), ('unit-run', 60), ('normal-build', 1200)]


def inputs():
    paths = [p for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts
        and p.name != 'import_evidence.py'
        and (p.suffix in ('.rs','.py','.toml') or p.name == 'Cargo.lock')]
    paths += [ROOT / name for name in contextbuild.inputs()]
    paths += derivation.inputs()
    paths += [ROOT / 'Cargo.lock', ROOT / 'experiments/php-wasix-wp-request-context-probe/evidence/windows-build.json',
        ROOT / '.github/workflows/php-wasix-wp-http.yml']
    return {p.relative_to(ROOT).as_posix():sha(read(p,4*1024*1024)) for p in sorted(set(paths))}


def driver_gate():
    plan = driver.load_plan()
    files = driver.registry_inputs(plan)
    files['mysql_async'] = driver.patched_sources(plan, files['mysql_async'])
    trees = {p['name']: driver.verify_tree(driver.WORKSPACE / 'source' / (p['name'] + '-' + p['version']),
        files[p['name']]) for p in plan['crates']}
    require(read(driver.WORKSPACE / 'Cargo.toml', 4096) == driver.MANIFEST, 'bootstrap_driver_stage_manifest')
    seed = read(ROOT / plan['existing_reference']['cargo_lock'], 1024*1024)
    require(read(driver.WORKSPACE / 'seed-binlog.Cargo.lock', 1024*1024) == seed, 'bootstrap_driver_stage_seed')
    lock = read(driver.WORKSPACE / 'Cargo.lock', 1024*1024)
    state_path = driver.WORK / 'resolved-lock.json'
    if state_path.exists():
        state = json.loads(read(state_path, 65536))
        require(state['lock'] == driver.identity(lock) and state['seed_sha256'] == sha(seed)
            and state['manifest_sha256'] == sha(driver.MANIFEST), 'bootstrap_driver_stage_lock')
    else:
        # Fresh CI only prepared this historical source stage. Building our
        # independent path-dependent workspace must not resolve its old lock.
        require(lock == seed, 'bootstrap_unresolved_driver_stage_must_match_seed')
    return {'source_trees': trees, 'plan_sha256': sha(read(driver.PLAN, 128*1024)),
        'stage_lock': driver.identity(lock), 'seed_sha256': sha(seed),
        'historical_resolved_lock_state_present':state_path.exists(), 'stage_modified': False}


def environment():
    e = {k:v for k,v in os.environ.items() if not k.upper().startswith(('MYSQL_', 'MARIADB_', 'RP_', 'GH_', 'GITHUB_'))
        and k.upper() not in {'RUST_LOG', 'RUST_BACKTRACE', 'PHPRC', 'PHP_INI_SCAN_DIR', 'ACTIONS_RUNTIME_TOKEN'}}
    e.update(CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='2', CARGO_INCREMENTAL='0')
    return e


def artifact(wire, tests):
    selected, finished = [], False
    require(len(wire) <= 16*1024*1024, 'bootstrap_cargo_capture_quota')
    for line in wire.splitlines():
        value = json.loads(line)
        require(not finished, 'bootstrap_cargo_after_finish')
        if value.get('reason') == 'build-finished':
            require(value['success'] is True, 'bootstrap_cargo_failed')
            finished = True
        if value.get('reason') != 'compiler-artifact' or value.get('target', {}).get('name') != NAME:
            continue
        require(value['target']['kind'] == ['bin'] and value['profile']['test'] is tests,
            'bootstrap_artifact_kind')
        require(Path(value['manifest_path']).resolve() == HERE / 'Cargo.toml'
            and Path(value['target']['src_path']).resolve() == HERE / 'src/main.rs', 'bootstrap_artifact_source')
        path = Path(value['executable'])
        require(path.resolve(strict=True) == path and path.is_relative_to(TARGET), 'bootstrap_artifact_path')
        selected.append(path)
    require(finished and len(selected) == 1, 'bootstrap_one_artifact')
    return selected[0]


def child(stage, command, deadline, report):
    start = time.monotonic()
    pid, result = owned_probe.run_captured_probe(command, environment(), timeout=deadline)
    work = WORK
    require(work.resolve() == work, 'bootstrap_build_work_alias')
    work.mkdir(parents=True, exist_ok=True)
    for name, wire in [('stdout', result.stdout), ('stderr', result.stderr)]:
        path = work / (stage + '.' + name)
        require(path.resolve() == path, 'bootstrap_build_log_alias')
        path.write_bytes(wire)
    report['commands'].append({'stage':stage, 'timeout_seconds':deadline, 'owned_pid':pid,
        'owned_reaped':True, 'exit_code':result.returncode, 'elapsed_seconds':round(time.monotonic()-start,3),
        'stdout_sha256':sha(result.stdout), 'stderr_sha256':sha(result.stderr)})
    require(result.returncode == 0 and len(result.stdout) <= 16*1024*1024 and len(result.stderr) <= 65536,
        'bootstrap_owned_build_child_failed')
    return result


def preserve(path, wire):
    require(path.resolve() == path, 'bootstrap_build_receipt_alias')
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        before = read(path, 2*1024*1024)
        saved = path.with_name(path.stem + '-before-' + sha(before)[:16] + path.suffix)
        if saved.exists():
            require(read(saved, 2*1024*1024) == before, 'bootstrap_previous_receipt_changed')
        else:
            with saved.open('xb') as stream: stream.write(before)
    path.write_bytes(wire)


def verify_build(binary=None):
    value = json.loads(read(RECEIPT, 2*1024*1024))
    require(value['schema'] == 'recastpress.php-wasix-wp-http-build.v0.5'
        and value['passed'] is True and value['outcome'] == 'locked_offline_http_host_build_passed'
        and value['owned_cleanup_completed'] is True and value['source_reverified_after'] is True
        and value['locked_offline'] is True, 'bootstrap_host_build_receipt')
    require(value['input_files'] == inputs() and value['driver_before'] == value['driver_after'] == driver_gate()
        and value['source_fs_registry_before'] == value['source_fs_registry_after'] == sourcefs.registry_gate()
        and value['derivation_before'] == value['derivation_after'] == derivation.verify(),
        'bootstrap_host_build_inputs_changed')
    require(value['old_build_before'] == value['old_build_after'] == old_build_gate(),
        'home_previous_build_changed')
    require(value['units']['passed'] is True and value['units']['tests'] == TESTS, 'bootstrap_host_units')
    require(len(value['commands']) == len(STAGES), 'bootstrap_host_build_stage_count')
    for row, (stage, deadline) in zip(value['commands'], STAGES):
        require(row['stage'] == stage and row['timeout_seconds'] == deadline and row['owned_reaped'] is True
            and type(row['owned_pid']) is int and row['owned_pid'] > 0 and type(row['exit_code']) is int
            and row['exit_code'] == 0, 'bootstrap_host_build_stage')
    selected = Path(binary).absolute() if binary else ROOT / value['executable']
    require(selected.resolve(strict=True) == selected and selected.is_relative_to(TARGET)
        and selected.relative_to(ROOT).as_posix() == value['executable']
        and driver.identity(read(selected, 256*1024*1024)) == value['executable_identity'], 'bootstrap_host_binary_changed')
    unit = ROOT / value['units']['executable']
    require(unit.resolve(strict=True) == unit and unit.is_relative_to(TARGET)
        and driver.identity(read(unit, 256*1024*1024)) == value['units']['executable_identity'], 'bootstrap_host_unit_binary_changed')
    for key in ['database_started', 'database_connected', 'php_executed', 'wasm_executed', 'product_runtime_selected']:
        require(value[key] is False, 'bootstrap_host_build_unclaimed_scope')
    return value


def old_build_gate():
    result = {}
    for name, module, count, units in [('bootstrap',oldbuild,145,31),('theme',themebuild,172,53),('context',contextbuild,195,58)]:
        value = module.verify_build()
        require(len(value['input_files']) == count and len(value['units']['tests']) == units, 'context_frozen_old_build_shape')
        result[name] = {'receipt_path':module.RECEIPT.relative_to(ROOT).as_posix(),
            'receipt_identity':driver.identity(read(module.RECEIPT,2*1024*1024)),
            'executable':value['executable'],'executable_identity':value['executable_identity'],
            'unit_executable':value['units']['executable'],'unit_executable_identity':value['units']['executable_identity'],
            'source_inputs':count,'units':units,'original_build_fully_reverified':True}
    return result


def run_build(record=True):
    report = {'schema':'recastpress.php-wasix-wp-http-build.v0.5', 'outcome':'not_run', 'passed':False,
        'observed_at_utc':datetime.now(timezone.utc).isoformat(), 'commands':[], 'locked_offline':True,
        'owned_cleanup_completed':False, 'source_reverified_after':False,
        'database_started':False, 'database_connected':False, 'php_executed':False,
        'wasm_executed':False, 'product_runtime_selected':False}
    passed = False
    try:
        require(TARGET.resolve() == TARGET and TARGET.is_relative_to(ROOT/'target/php-wasix-wp-http-probe'), 'http_fixed_target')
        TARGET.mkdir(parents=True,exist_ok=True)
        report['old_build_before'] = old_build_gate()
        report['input_files'] = inputs()
        report['driver_before'] = driver_gate()
        report['source_fs_registry_before'] = sourcefs.registry_gate()
        report['derivation_before'] = derivation.verify()
        profile = ['--release'] if sys.platform == 'linux' else []
        report['build_profile'] = 'release' if profile else 'dev'
        base = ['--manifest-path', str(HERE/'Cargo.toml'), '--target-dir', str(TARGET), '--locked', '--offline']
        built = child('unit-build', ['cargo','test',*profile,'--no-run',*base,'--message-format=json'],1200,report)
        unit = artifact(built.stdout, True)
        listed = child('unit-list', [str(unit),'--list','--format','terse'],10,report)
        names = sorted(line[:-6] for line in listed.stdout.decode().splitlines() if line.endswith(': test'))
        require(not listed.stderr and names == TESTS, 'bootstrap_exact_unit_inventory')
        tested = child('unit-run', [str(unit),'--test-threads=1','--show-output'],60,report)
        text = tested.stdout.decode().replace('\r\n','\n')
        require(not tested.stderr and f'test result: ok. {len(TESTS)} passed; 0 failed; 0 ignored;' in text
            and sorted(re.findall(r'^test (\S+) \.\.\. ok$',text,re.M)) == TESTS, 'bootstrap_all_units_passed')
        report['units'] = {'tests':TESTS, 'passed':True, 'executable':unit.relative_to(ROOT).as_posix(),
            'executable_identity':driver.identity(read(unit,256*1024*1024)), 'stdout_sha256':sha(tested.stdout)}
        built = child('normal-build', ['cargo','build',*profile,*base,'--message-format=json'],1200,report)
        exe = artifact(built.stdout, False)
        report.update(executable=exe.relative_to(ROOT).as_posix(),
            executable_identity=driver.identity(read(exe,256*1024*1024)))
        passed = True
    except (Exception, KeyboardInterrupt) as error:
        report['failure_class'] = type(error).__name__
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        try:
            owned_probe.reap_owned_probes()
            report['owned_cleanup_completed'] = True
            if 'input_files' in report:
                require(inputs() == report['input_files'], 'bootstrap_build_sources_changed')
                report['driver_after'] = driver_gate()
                report['source_fs_registry_after'] = sourcefs.registry_gate()
                report['derivation_after'] = derivation.verify()
                require(report['driver_after'] == report['driver_before']
                    and report['source_fs_registry_after'] == report['source_fs_registry_before']
                    and report['derivation_after'] == report['derivation_before'], 'bootstrap_build_gate_changed')
                report['old_build_after'] = old_build_gate()
                require(report['old_build_before'] == report['old_build_after'], 'home_previous_build_changed')
                report['source_reverified_after'] = True
        except (Exception, KeyboardInterrupt) as error:
            passed = False
            report['after_failure_class'] = type(error).__name__
            if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
                report['after_failure'] = str(error)
    report['passed'] = passed
    report['outcome'] = 'locked_offline_http_host_build_passed' if passed else 'http_host_build_failed'
    if record:
        preserve(RECEIPT, (json.dumps(report,indent=2,ensure_ascii=True,allow_nan=False)+'\n').encode())
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    result = verify_build() if args.verify_only else run_build()
    print(result['outcome'])
    sys.exit(0 if result['passed'] else 1)
