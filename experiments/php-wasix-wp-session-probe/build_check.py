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
TARGET = ROOT / 'target/php-wasix-wp-bootstrap-probe/cargo-target'
WORK = ROOT / 'target/php-wasix-wp-session-probe'
NAME = 'recastpress-php-wasix-wp-session-probe'
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
httpbuild = load('rp_private_worker_frozen_http_build', ROOT / 'experiments/php-wasix-wp-http-probe/build_check.py')
latestbuild = load('rp_session_frozen_private_http_build', ROOT / 'experiments/php-wasix-wp-private-http-probe/build_check.py')
require, read, sha = driver.require, driver.read_fixed, driver.sha
REUSED_TESTS = [name for name in contextbuild.TESTS if not name.startswith(('tests::','response_contract::'))]
# Static owned inventory is finalized with the session source before building.
OWNED_TESTS = [
    'strict_json::tests::recursive_duplicate_keys_and_trailing_json_fail_before_value',
    'strict_json::tests::unique_nested_values_preserve_private_bytes_without_error_text',
    'tests::absent_fixture_envelope_reports_only_fixed_prefix_classes_and_byte_counts',
    'tests::cgi_environment_carries_neither_database_nor_nonce_context',
    'tests::closed_cases_select_guest_entry_without_changing_database_mode',
    'tests::complete_cgi_failure_keeps_only_source_bound_public_diagnostics',
    'tests::complete_json_cgi_accepts_only_the_known_outer_header_profile',
    'tests::complete_json_cgi_rejects_missing_wrong_status_or_unknown_outer_headers',
    'tests::duplicate_exact_fixture_envelopes_are_counted_without_offset_extraction',
    'tests::duplicate_json_content_type_keeps_the_original_parser_rejection',
    'tests::expanded_columns_are_explicit_and_other_result_limits_stay_fixed',
    'tests::guest_context_has_only_five_keys_and_no_database_credentials',
    'tests::initial_json_with_known_or_private_trailer_is_diagnostic_only',
    'tests::jsonl_eof_unterminated_and_oversized_trailers_are_not_extra_requests',
    'tests::malformed_response_or_wrong_private_context_never_commits',
    'tests::nested_known_json_cgi_frame_is_diagnostic_only',
    'tests::nested_outer_response_duplicate_keys_are_rejected_without_safe_projection',
    'tests::newline_failure_stays_rejected_but_whole_json_diagnostics_are_safe',
    'tests::partial_source_runtime_rpc_access_or_actor_never_commits_private_fields',
    'tests::private_ack_secret_gate_covers_raw_and_hex_values_without_echoing_them',
    'tests::public_php_excludes_unexpected_rows_secrets_and_nonfixture_titles',
    'tests::request_template_and_final_summary_keep_real_zero_request_compilations',
    'tests::session_frames_reject_duplicate_unknown_and_noninteger_sequences',
    'tests::session_order_and_nonce_freshness_are_closed_and_errors_poison',
    'tests::structural_unknown_response_is_private_only_after_all_completion_checks',
    'tests::unique_exact_fixture_envelope_recovers_only_safe_diagnostics_after_private_prefix',
    'tests::unknown_prefix_is_never_scanned_or_published',
    'tests::wrapper_rejects_duplicate_unknown_and_nested_invalid_input',
]
TESTS = sorted(REUSED_TESTS + OWNED_TESTS)

STAGES = [('unit-build', 1200), ('unit-list', 10), ('unit-run', 60), ('normal-build', 1200)]


def inputs():
    paths = [p for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts
        and p.name != 'import_evidence.py'
        and (p.suffix in ('.rs','.py','.toml') or p.name == 'Cargo.lock')]
    paths += [ROOT / name for name in latestbuild.inputs()]
    paths += derivation.inputs()
    paths += [ROOT / 'experiments/php-wasix-wp-private-http-probe/evidence/windows-build.json',
        ROOT / '.github/workflows/php-wasix-wp-session.yml']
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
    require(value['schema'] == 'recastpress.php-wasix-wp-session-build.v0.5'
        and value['passed'] is True and value['outcome'] == 'locked_offline_session_build_passed'
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
    # Recheck exact source maps and actual bytes, without recursively invoking
    # six historical driver/registry/old-build gate trees. Our own driver and
    # registry gates run once per before/after boundary below.
    combined = latestbuild.inputs()
    result = {}
    profiles = [
        ('bootstrap',oldbuild,145,31,'bootstrap','locked_offline_bootstrap_host_build_passed'),
        ('theme',themebuild,172,53,'request','locked_offline_home_host_build_passed'),
        ('context',contextbuild,195,58,'request-context','locked_offline_context_host_build_passed'),
        ('http',httpbuild,206,21,'http','locked_offline_http_host_build_passed'),
        ('private_worker',latestbuild.workerbuild,219,59,'private-worker','locked_offline_private_worker_build_passed'),
        ('private_http',latestbuild,232,12,'private-http','locked_offline_private_http_build_passed'),
    ]
    for label,module,count,units,profile,outcome in profiles:
        wire = read(module.RECEIPT,2*1024*1024); value = json.loads(wire)
        require(value['schema'] == 'recastpress.php-wasix-wp-'+profile+'-build.v0.5'
            and value['outcome'] == outcome and value['passed'] is True
            and value['locked_offline'] is True and value['owned_cleanup_completed'] is True
            and value['source_reverified_after'] is True, 'session_old_receipt_profile')
        expected = module.inputs()
        require(len(expected)==count and value['input_files']==expected
            and all(combined.get(k)==digest for k,digest in expected.items()), 'session_old_exact_source_map')
        require(value['units']['passed'] is True and value['units']['tests']==module.TESTS
            and len(module.TESTS)==units, 'session_old_exact_unit_inventory')
        profile_dir='debug' if os.name=='nt' else 'release'
        suffix='.exe' if os.name=='nt' else ''
        require(value['build_profile']==('dev' if os.name=='nt' else 'release')
            and Path(value['executable'])==module.TARGET.relative_to(ROOT)/profile_dir/(module.NAME+suffix),
            'session_old_fixed_executable_path')
        unit_path=Path(value['units']['executable'])
        require(unit_path.parent==module.TARGET.relative_to(ROOT)/profile_dir/'deps'
            and re.fullmatch(re.escape(module.NAME.replace('-','_'))+r'-[a-f0-9]{16}'+re.escape(suffix),unit_path.name),
            'session_old_fixed_unit_path')
        for scope in ('driver','source_fs_registry','derivation','old_build'):
            if scope+'_before' in value:
                require(value[scope+'_before']==value[scope+'_after'], 'session_old_receipt_before_after')
        for field in ('php_executed','wasm_executed','database_started','database_connected','product_runtime_selected'):
            require(value[field] is False, 'session_old_build_scope')
        require(len(value['commands'])==len(module.STAGES), 'session_old_build_stages')
        for row,(stage,deadline) in zip(value['commands'],module.STAGES):
            require(row['stage']==stage and row['timeout_seconds']==deadline and row['owned_reaped'] is True
                and type(row['owned_pid']) is int and row['owned_pid']>0 and type(row['exit_code']) is int
                and row['exit_code']==0, 'session_old_build_stage_completion')
        target = module.TARGET
        for name,expected_identity in [(value['executable'],value['executable_identity']),
            (value['units']['executable'],value['units']['executable_identity'])]:
            path=ROOT/name
            require(path.resolve(strict=True)==path and path.is_relative_to(target)
                and driver.identity(read(path,256*1024*1024))==expected_identity,'session_old_artifact_identity')
        result[label]={'receipt_path':module.RECEIPT.relative_to(ROOT).as_posix(),
            'receipt_identity':driver.identity(wire),'executable':value['executable'],
            'executable_identity':value['executable_identity'],'unit_executable':value['units']['executable'],
            'unit_executable_identity':value['units']['executable_identity'],
            'source_inputs':count,'units':units,'exact_receipt_source_map_rederived':True,
            'source_map_bound_to_combined_inputs':True,'artifact_bytes_rehashed':True,
            'full_recursive_old_gate_invoked':False}
    return result


def run_build(record=True):
    report = {'schema':'recastpress.php-wasix-wp-session-build.v0.5', 'outcome':'not_run', 'passed':False,
        'observed_at_utc':datetime.now(timezone.utc).isoformat(), 'commands':[], 'locked_offline':True,
        'owned_cleanup_completed':False, 'source_reverified_after':False,
        'database_started':False, 'database_connected':False, 'php_executed':False,
        'wasm_executed':False, 'product_runtime_selected':False}
    passed = False
    try:
        require(TARGET.resolve() == TARGET and TARGET.is_dir(), 'private_worker_fixed_shared_target')
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
    report['outcome'] = 'locked_offline_session_build_passed' if passed else 'session_build_failed'
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
