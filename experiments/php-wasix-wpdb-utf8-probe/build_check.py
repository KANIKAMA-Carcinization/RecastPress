"""Build the independent UTF8 host with unchanged full-WP DB/RPC/result bodies."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/php-wasix-wpdb-utf8-probe'
TARGET = ROOT / 'target/php-wasix-wp-bootstrap-probe/cargo-target'
NAME = 'recastpress-php-wasix-wpdb-utf8-probe'
RECEIPT = HERE / ('evidence/' + ('windows' if os.name == 'nt' else 'linux') + '-build.json')
WORKFLOW = ROOT / '.github/workflows/php-wasix-wpdb-utf8.yml'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


old = load('rp_utf8_unchanged_bootstrap_host', ROOT / 'experiments/php-wasix-wp-bootstrap-probe/build_check.py')
prepared = load('rp_utf8_owned_preparer', HERE / 'prepare.py')
require, read, sha = old.require, old.read, old.sha
identity = old.driver.identity
environment = old.environment
TESTS = sorted([name for name in old.TESTS if name.startswith(('cgi_response::tests::', 'fiber_adapter::tests::',
    'rpc_fs::tests::', 'result_budget::tests::', 'db::tests::'))] + ['tests::' + name for name in [
    'context_contains_only_schema_nonce_and_environment_has_no_host_secrets',
    'all_case_state_query_and_utf8_bytes_must_match_without_normalization',
    'mismatch_diagnostics_expose_only_complete_known_case_ids_counts_and_digests',
    'transport_statistics_reject_private_shapes_and_copy_only_finite_counters',
    'outer_json_cgi_allows_only_one_known_content_type_and_optional_powered_by',
    'missing_wrong_status_or_unknown_outer_header_cannot_pass_as_json',
    'duplicate_json_or_powered_by_headers_remain_rejected',
    'raw_and_hex_host_secrets_are_both_refused_before_output',
    'platform_reference_changes_only_exact_expected_version_and_preserves_nine_fields',
    'unknown_version_suffix_prefix_and_changed_semantic_field_do_not_match',
    'wrong_image_or_original_native_version_rejects_reference_derivation']])
STAGES = [('unit-build', 1200), ('unit-list', 10), ('unit-run', 60), ('normal-build', 1200)]


def inputs():
    result = old.inputs()
    result.update(prepared.input_hashes())
    paths = [path for path in HERE.rglob('*') if path.is_file() and '__pycache__' not in path.parts
        and path.name != 'import_evidence.py' and '/evidence/' not in path.as_posix()
        and (path.suffix in ('.rs', '.py', '.php', '.toml') or path.name == 'Cargo.lock')]
    paths += [WORKFLOW, ROOT / 'experiments/php-wasix-wp-request-probe/src/main.rs']
    for path in paths: result[path.relative_to(ROOT).as_posix()] = sha(read(path, 4*1024*1024))
    return dict(sorted(result.items()))


def derivation():
    parent = ROOT / 'experiments/php-wasix-wp-bootstrap-probe'
    original = read(ROOT / 'experiments/php-wasix-wp-request-probe/src/main.rs', 256*1024)
    start = original.index(b'fn fixed('); end = original.index(b'fn finite(', start)
    require(read(HERE / 'src/module_verifier.rs', 65536) == original[start:end], 'utf8_exact_module_verifier')
    current = read(HERE / 'src/main.rs', 256*1024)
    begin = original.index(b'fn known_json_cgi_headers('); finish = original.index(b'fn observe_cgi(', begin)
    require(current.count(original[begin:finish]) == 1, 'utf8_exact_outer_cgi_header_helpers')
    borrowed = {'db': 'db.rs', 'rpc_fs': 'rpc_fs.rs', 'result_budget': 'result_budget.rs', 'capture': 'capture.rs'}
    bodies = {}
    for module, name in borrowed.items():
        marker = ('#[path = "../../php-wasix-wp-bootstrap-probe/src/' + name + '"]\nmod ' + module + ';').encode()
        require(current.count(marker) == 1 and not (HERE / 'src' / name).exists(), 'utf8_readonly_runtime_import')
        bodies[name] = sha(read(parent / 'src' / name, 256*1024))
    for name in ['Cargo.toml', 'Cargo.lock']:
        wire = read(parent / name, 1024*1024); before = b'recastpress-php-wasix-wp-bootstrap-probe'
        require(wire.count(before) == 1 and wire.replace(before, NAME.encode()) == read(HERE / name, 1024*1024),
            'utf8_workspace_package_name_only')
    return {'module_verifier_body_sha256': sha(original[start:end]), 'module_verifier_body_unchanged': True,
        'outer_cgi_header_helper_sha256': sha(original[begin:finish]), 'outer_cgi_header_helpers_unchanged': True,
        'readonly_original_runtime_bodies': bodies, 'db_actor_rpc_result_capture_semantics_unchanged': True,
        'workspace_lock_package_name_only': True, 'original_sources_modified': False}


def old_build_gate():
    value = old.verify_build()
    return {'receipt_path': old.RECEIPT.relative_to(ROOT).as_posix(), 'receipt_identity': identity(read(old.RECEIPT, 2*1024*1024)),
        'executable': value['executable'], 'executable_identity': value['executable_identity'],
        'unit_executable': value['units']['executable'], 'unit_executable_identity': value['units']['executable_identity'],
        'source_inputs': len(value['input_files']), 'units': len(value['units']['tests']), 'original_build_fully_reverified': True}


def gates():
    return {'old_bootstrap': old_build_gate(), 'driver': old.driver_gate(),
        'source_fs': old.sourcefs.registry_gate(), 'derivation': derivation()}


def child(stage, command, timeout, report):
    pid, result = old.owned_probe.run_captured_probe(command, environment(), timeout=timeout)
    require(WORK.resolve() == WORK, 'owned_utf8_build_path'); WORK.mkdir(parents=True, exist_ok=True)
    for label, wire in [('stdout', result.stdout), ('stderr', result.stderr)]: (WORK / (stage + '.' + label)).write_bytes(wire)
    report['commands'].append({'stage': stage, 'owned_pid': pid, 'owned_reaped': True, 'timeout_seconds': timeout,
        'exit_code': result.returncode, 'stdout_sha256': sha(result.stdout), 'stderr_sha256': sha(result.stderr)})
    require(result.returncode == 0 and len(result.stdout) <= 16*1024*1024 and len(result.stderr) <= 65536, 'owned_utf8_build_command_failed')
    return result


def artifact(wire, tests):
    matches = []; finished = False
    for line in wire.splitlines():
        value = json.loads(line); require(not finished, 'cargo_after_finish')
        if value['reason'] == 'build-finished': require(value['success'] is True, 'cargo_build_failed'); finished = True
        if value['reason'] != 'compiler-artifact' or value.get('target', {}).get('name') != NAME: continue
        require(value['target']['kind'] == ['bin'] and value['profile']['test'] is tests
            and Path(value['manifest_path']).resolve() == HERE / 'Cargo.toml', 'cargo_artifact_kind')
        selected = Path(value['executable'])
        require(selected.resolve(strict=True) == selected and selected.is_relative_to(TARGET), 'cargo_artifact_path')
        matches.append(selected)
    require(finished and len(matches) == 1, 'one_cargo_artifact'); return matches[0]


def preserve(path, wire):
    require(path.resolve() == path and path.is_relative_to(HERE / 'evidence'), 'owned_utf8_receipt_path')
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        before = read(path, 4*1024*1024); backup = path.with_name(path.stem + '-before-' + sha(before)[:16] + path.suffix)
        if backup.exists(): require(read(backup, 4*1024*1024) == before, 'immutable_previous_utf8_receipt')
        else:
            with backup.open('xb') as stream: stream.write(before)
    path.write_bytes(wire)


def run_build(record=True):
    report = {'schema': 'recastpress.php-wasix-wpdb-utf8-build.v0.5', 'passed': False, 'commands': [],
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'locked_offline': True,
        'actual_php_wasm_executed': False, 'native_php_executed': False, 'database_connected': False,
        'full_wordpress_bootstrap': False, 'product_runtime_selected': False,
        'owned_cleanup_completed': False, 'source_reverified_after': False}
    passed = False
    try:
        report['before'] = gates(); report['input_files'] = inputs(); prepared.prepare()
        profile = ['--release'] if sys.platform == 'linux' else []; report['build_profile'] = 'release' if profile else 'dev'
        base = ['--manifest-path', str(HERE / 'Cargo.toml'), '--target-dir', str(TARGET), '--locked', '--offline']
        unit = artifact(child('unit-build', ['cargo', 'test', *profile, '--no-run', *base, '--message-format=json'], 1200, report).stdout, True)
        listed = child('unit-list', [str(unit), '--list', '--format', 'terse'], 10, report)
        names = sorted(line[:-6] for line in listed.stdout.decode().splitlines() if line.endswith(': test'))
        require(not listed.stderr and names == TESTS, 'exact_utf8_unit_inventory')
        tested = child('unit-run', [str(unit), '--test-threads=1', '--show-output'], 60, report)
        text = tested.stdout.decode().replace('\r\n', '\n')
        require(not tested.stderr and f'test result: ok. {len(TESTS)} passed; 0 failed; 0 ignored;' in text
            and sorted(re.findall(r'^test (\S+) \.\.\. ok$', text, re.M)) == TESTS, 'all_utf8_pure_units_passed')
        report['units'] = {'passed': True, 'tests': TESTS, 'executable': unit.relative_to(ROOT).as_posix(),
            'executable_identity': identity(read(unit, 256*1024*1024)), 'stdout_sha256': sha(tested.stdout)}
        executable = artifact(child('normal-build', ['cargo', 'build', *profile, *base, '--message-format=json'], 1200, report).stdout, False)
        report.update(executable=executable.relative_to(ROOT).as_posix(), executable_identity=identity(read(executable, 256*1024*1024)))
        passed = True
    except (Exception, KeyboardInterrupt) as error:
        report['failure_class'] = type(error).__name__
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)): report['failure'] = str(error)
    finally:
        try:
            old.owned_probe.reap_owned_probes(); report['owned_cleanup_completed'] = True
            if 'input_files' in report:
                require(inputs() == report['input_files'], 'utf8_build_sources_changed')
                report['after'] = gates(); require(report['before'] == report['after'], 'original_build_or_utf8_gates_changed')
                report['source_reverified_after'] = True
        except (Exception, KeyboardInterrupt) as error: passed = False; report['after_failure_class'] = type(error).__name__
    report.update(passed=passed, outcome='locked_offline_utf8_host_build_passed' if passed else 'utf8_host_build_failed')
    if record: preserve(RECEIPT, (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode())
    return report


def verify_build(binary=None):
    value = json.loads(read(RECEIPT, 4*1024*1024))
    require(value['schema'] == 'recastpress.php-wasix-wpdb-utf8-build.v0.5' and value['passed'] is True
        and value['outcome'] == 'locked_offline_utf8_host_build_passed' and value['input_files'] == inputs()
        and value['before'] == value['after'] == gates() and value['source_reverified_after'] is True
        and value['owned_cleanup_completed'] is True and value['units']['tests'] == TESTS
        and value['units']['passed'] is True and len(value['commands']) == 4, 'utf8_host_build_receipt')
    for row, (stage, timeout) in zip(value['commands'], STAGES):
        require(row['stage'] == stage and row['timeout_seconds'] == timeout and row['exit_code'] == 0
            and row['owned_reaped'] is True, 'utf8_owned_build_stage')
    selected = Path(binary).absolute() if binary else ROOT / value['executable']
    require(selected.resolve(strict=True) == selected and selected.is_relative_to(TARGET)
        and selected.relative_to(ROOT).as_posix() == value['executable']
        and identity(read(selected, 256*1024*1024)) == value['executable_identity'], 'utf8_host_binary')
    unit = ROOT / value['units']['executable']
    require(unit.resolve(strict=True) == unit and unit.is_relative_to(TARGET)
        and identity(read(unit, 256*1024*1024)) == value['units']['executable_identity'], 'utf8_unit_binary')
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--verify', action='store_true'); args = parser.parse_args()
    value = verify_build() if args.verify else run_build()
    print(value['outcome']); sys.exit(0 if value['passed'] else 1)
