"""Run and record the pinned native WP_Hook overlay oracle on Windows."""
import hashlib
import json
from pathlib import Path
import subprocess
from hook_oracle_contract import record as cross_oracle_record

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PHP = ROOT / '.reference/php-8.5.12/php.exe'
FIXTURE = HERE / 'fixtures/hook_overlay_native_oracle.php'
BASELINE_FIXTURE = HERE / 'fixtures/hook_native_oracle.php'
ADAPTER_FIXTURE = HERE / 'hook_adapter_native_oracle.php'
OVERLAY = ROOT / 'experiments/php-wasix-wp-hook-dispatch-probe/evidence/overlay-v5'
SITE = ROOT / 'target/wp-bootstrap-probe/site/wp-includes'
EVIDENCE = HERE / 'evidence/windows-real-wp-hook-overlay-native.json'
RECEIPT_SHA = '73a7efccef5e52e4cbf5ff5f7e2544ec654ef3f7faa03f830f5e333a0a4eb360'
SOURCE = {
    'wp-includes/plugin.php': 'd70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
    'wp-includes/class-wp-hook.php': 'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
}
PATCHED = {
    'wp-includes/plugin.php': 'db8dc3d3c61a43ff225e01881187f47ce8eedfb1a65a56c9cd60bf13aea04d5c',
    'wp-includes/class-wp-hook.php': '76eacf182c0e07c4285be0eff1df5bfd5cf4ce2a08c748a1085b241ffff473e4',
}
SENTINEL = '68c9c675d3805a343835728d98a0efedf0df9833cb904bebf909c7e0fda5c61c'


def sha(wire):
    return hashlib.sha256(wire).hexdigest()


def read(path, limit):
    if path.resolve(strict=True) != path or not path.is_file() or path.is_symlink():
        raise ValueError('fixed_file_identity')
    wire = path.read_bytes()
    if not wire or len(wire) > limit:
        raise ValueError('fixed_file_size')
    return wire


def run():
    executable = read(PHP, 64 * 1024 * 1024)
    fixture = read(FIXTURE, 64 * 1024)
    baseline_fixture = read(BASELINE_FIXTURE, 64 * 1024)
    adapter_fixture = read(ADAPTER_FIXTURE, 128 * 1024)
    adapter_source = read(HERE / 'fixtures/hook_adapter.php', 256 * 1024)
    handle_registry_source = read(HERE / 'fixtures/hook_handle_registry.php', 256 * 1024)
    source_path = b"'/rp-wp-hooks/channel'"
    test_path = b"'recastpress-rp-hook-test://channel'"
    if adapter_source.count(source_path) != 1:
        raise ValueError('adapter_test_path_source')
    transformed_adapter = adapter_source.replace(source_path, test_path, 1)
    receipt = read(OVERLAY / 'overlay.json', 64 * 1024)
    if sha(receipt) != RECEIPT_SHA:
        raise ValueError('overlay_receipt_hash')
    overlay_files = {}
    for name, digest in PATCHED.items():
        wire = read(OVERLAY / name, 64 * 1024)
        original = read(SITE / name.removeprefix('wp-includes/'), 64 * 1024)
        if sha(original) != SOURCE[name] or sha(wire) != digest:
            raise ValueError('overlay_source_or_output_hash')
        overlay_files[name] = {'source_sha256': sha(original), 'overlay_sha256': sha(wire)}
    sentinel = read(SITE / 'class-wp-filter-sentinel.php', 16 * 1024)
    if sha(sentinel) != SENTINEL:
        raise ValueError('pinned_overlay_support_file')

    version = subprocess.run([str(PHP), '-n', '-r', 'echo PHP_VERSION;'], cwd=ROOT,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15, check=False)
    if version.returncode != 0 or version.stderr or version.stdout != b'8.5.12':
        raise ValueError('pinned_native_php_version')
    baseline = subprocess.run([str(PHP), '-n', str(BASELINE_FIXTURE)], cwd=ROOT,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=30, check=False)
    if baseline.returncode != 0 or baseline.stderr or len(baseline.stdout) > 8192 \
            or not baseline.stdout.endswith(b'\n') or baseline.stdout.count(b'\n') != 1:
        raise ValueError('native_original_hook_oracle_execution')
    baseline_observation = json.loads(baseline.stdout)
    if baseline_observation.get('status') != 'passed' \
            or baseline_observation.get('wordpress_source_sha256') != SOURCE \
            or baseline_observation.get('filtered_value') != 'RP Bootstrap Fixture|first|second' \
            or baseline_observation.get('event_order') != ['rp_boot_hook_first', 'rp_boot_hook_second']:
        raise ValueError('native_original_hook_oracle_contract')
    result = subprocess.run([str(PHP), '-n', str(FIXTURE)], cwd=ROOT,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=30, check=False)
    if result.returncode != 0 or result.stderr or len(result.stdout) > 8192 \
            or not result.stdout.endswith(b'\n') or result.stdout.count(b'\n') != 1:
        raise ValueError('native_overlay_oracle_execution')
    observation = json.loads(result.stdout)
    expected_table = [
        {'priority': 10, 'uid': 'rp_overlay_oracle_first', 'function': 'rp_overlay_oracle_first', 'accepted_args': 1},
        {'priority': 20, 'uid': 'rp_overlay_oracle_second', 'function': 'rp_overlay_oracle_second', 'accepted_args': 1},
    ]
    expected_events = ['rp_overlay_oracle_first', 'rp_overlay_oracle_second']
    expected_state = {'nesting_level': 0, 'iterations': [], 'current_priority': [], 'doing_action': False}
    if observation.get('schema') != 'recastpress.wp-bootstrap-hook-overlay-native-oracle.v0.1' \
            or observation.get('status') != 'passed' or observation.get('callback_table') != expected_table \
            or observation.get('event_order') != expected_events \
            or observation.get('value') != 'RP Bootstrap Fixture|first|second' \
            or observation.get('after_dispatch', {}).get('wp_hook_state') != expected_state \
            or observation.get('source_sha256') != SOURCE or observation.get('overlay_sha256') != PATCHED \
            or observation.get('overlay_receipt_sha256') != RECEIPT_SHA:
        raise ValueError('native_overlay_oracle_contract')

    adapter_result = subprocess.run([str(PHP), '-n', str(ADAPTER_FIXTURE)], cwd=ROOT,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=30, check=False)
    if adapter_result.returncode != 0 or adapter_result.stderr or len(adapter_result.stdout) > 16384 \
            or not adapter_result.stdout.endswith(b'\n') or adapter_result.stdout.count(b'\n') != 1:
        raise ValueError('native_hook_adapter_execution')
    adapter_observation = json.loads(adapter_result.stdout)
    adapter_failure_result = subprocess.run([str(PHP), '-n', str(ADAPTER_FIXTURE), 'bad-invoke'], cwd=ROOT,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=30, check=False)
    if adapter_failure_result.returncode != 0 or adapter_failure_result.stderr \
            or len(adapter_failure_result.stdout) > 16384 \
            or not adapter_failure_result.stdout.endswith(b'\n') \
            or adapter_failure_result.stdout.count(b'\n') != 1:
        raise ValueError('native_hook_adapter_failure_execution')
    adapter_failure_observation = json.loads(adapter_failure_result.stdout)
    expected_operations = [
        {'op': 'begin', 'seq': 1}, {'op': 'resume', 'seq': 2}, {'op': 'resume', 'seq': 3},
        {'op': 'release_ack', 'seq': 4}, {'op': 'release_ack', 'seq': 5}, {'op': 'close', 'seq': 6},
    ]
    if adapter_observation.get('schema') != 'recastpress.wp-bootstrap-hook-adapter-native-oracle.v0.1' \
            or adapter_observation.get('status') != 'passed' \
            or adapter_observation.get('wordpress_version') != '7.1.3' \
            or adapter_observation.get('adapter_path_transform_count') != 1 \
            or adapter_observation.get('callback_order') != ['rp_boot_hook_first', 'rp_boot_hook_second'] \
            or adapter_observation.get('filtered_value') != 'RP Bootstrap Fixture|first|second' \
            or adapter_observation.get('did_filter') != 1 \
            or adapter_observation.get('hook_state_after') != expected_state \
            or adapter_observation.get('registry_live_handles_after') != 0 \
            or adapter_observation.get('mock_peer_operations') != expected_operations \
            or adapter_observation.get('mock_peer_closed') is not True \
            or adapter_observation.get('temporary_cleanup') is not True \
            or adapter_observation.get('source_sha256', {}).get('adapter') != sha(adapter_source) \
            or adapter_observation.get('source_sha256', {}).get('transformed_adapter') != sha(transformed_adapter) \
            or adapter_observation.get('source_sha256', {}).get('handle_registry') != sha(handle_registry_source) \
            or adapter_observation.get('source_sha256', {}).get('overlay_receipt') != RECEIPT_SHA:
        raise ValueError('native_hook_adapter_contract')
    expected_failed_state = {'nesting_level': 1, 'iterations': [[10, 20]],
        'current_priority': [], 'doing_action': False}
    if adapter_failure_observation.get('schema') != 'recastpress.wp-bootstrap-hook-adapter-native-oracle.v0.1' \
            or adapter_failure_observation.get('status') != 'passed' \
            or adapter_failure_observation.get('scenario') != 'bad-invoke' \
            or adapter_failure_observation.get('failure') != 'hook_dispatch_invoke_contract' \
            or adapter_failure_observation.get('callback_count') != 0 \
            or adapter_failure_observation.get('registry_closed') is not True \
            or adapter_failure_observation.get('registry_live_handles_after') != 0 \
            or adapter_failure_observation.get('channel_closed_without_close_operation') is not True \
            or adapter_failure_observation.get('mock_peer_operations') != [{'op': 'begin', 'seq': 1}] \
            or adapter_failure_observation.get('wp_current_filter_after_failure') != ['option_blogname'] \
            or adapter_failure_observation.get('hook_state_after_failure') != expected_failed_state \
            or adapter_failure_observation.get('same_guest_recovery_verified') is not False \
            or adapter_failure_observation.get('adapter_path_transform_count') != 1 \
            or adapter_failure_observation.get('temporary_cleanup') is not True \
            or adapter_failure_observation.get('source_sha256', {}).get('adapter') != sha(adapter_source) \
            or adapter_failure_observation.get('source_sha256', {}).get('transformed_adapter') != sha(transformed_adapter) \
            or adapter_failure_observation.get('source_sha256', {}).get('handle_registry') != sha(handle_registry_source) \
            or adapter_failure_observation.get('source_sha256', {}).get('overlay_receipt') != RECEIPT_SHA \
            or adapter_failure_observation.get('source_sha256', {}).get('overlay_files') != PATCHED:
        raise ValueError('native_hook_adapter_failure_contract')

    cargo = subprocess.run(['cargo', 'test', '--manifest-path', str(HERE / 'Cargo.toml'),
        '--locked', '--offline'], cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600, check=False)
    if cargo.returncode != 0 or b'test result: ok. 39 passed' not in cargo.stdout \
            or len(cargo.stdout) > 4 * 1024 * 1024 or len(cargo.stderr) > 65536:
        raise ValueError('rust_host_contract_tests')

    oracle_pair = {
        'oracle': observation,
        'unpatched_wordpress_oracle': baseline_observation,
    }
    cross_oracle = cross_oracle_record(oracle_pair)

    inputs = {
        'php_executable': {'path': PHP.relative_to(ROOT).as_posix(), 'bytes': len(executable), 'sha256': sha(executable)},
        'fixture': {'path': FIXTURE.relative_to(ROOT).as_posix(), 'bytes': len(fixture), 'sha256': sha(fixture)},
        'adapter_fixture': {'path': ADAPTER_FIXTURE.relative_to(ROOT).as_posix(),
            'bytes': len(adapter_fixture), 'sha256': sha(adapter_fixture)},
        'adapter_sources': {
            'path': (HERE / 'fixtures/hook_adapter.php').relative_to(ROOT).as_posix(),
            'bytes': len(adapter_source), 'sha256': sha(adapter_source),
        },
        'handle_registry_source': {
            'path': (HERE / 'fixtures/hook_handle_registry.php').relative_to(ROOT).as_posix(),
            'bytes': len(handle_registry_source), 'sha256': sha(handle_registry_source),
        },
        'baseline_fixture': {'path': BASELINE_FIXTURE.relative_to(ROOT).as_posix(),
            'bytes': len(baseline_fixture), 'sha256': sha(baseline_fixture)},
        'overlay_receipt': {'path': (OVERLAY / 'overlay.json').relative_to(ROOT).as_posix(), 'bytes': len(receipt), 'sha256': sha(receipt)},
        'overlay_files': overlay_files,
        'wp_filter_sentinel': {'path': (SITE / 'class-wp-filter-sentinel.php').relative_to(ROOT).as_posix(),
            'bytes': len(sentinel), 'sha256': sha(sentinel)},
        'runner': {'path': Path(__file__).resolve().relative_to(ROOT).as_posix(),
            'sha256': sha(read(Path(__file__).resolve(), 128 * 1024))},
        'host_sources': {
            path.relative_to(ROOT).as_posix(): sha(read(path, 256 * 1024))
            for path in [HERE / 'src/catalog.rs', HERE / 'src/main.rs', HERE / 'build_check.py', HERE / 'Cargo.lock', Path(__file__).resolve().parent / 'hook_oracle_contract.py']
        },
    }
    return {
        'schema': 'recastpress.wp-bootstrap-real-hook-overlay-native-evidence.v0.3',
        'passed': True,
        'outcome': 'native_real_wp_hook_overlay_passed',
        'scope': 'Pinned PHP 8.5.12 CLI + WordPress 7.1.3 plugin.php/WP_Hook overlay; original loop and overlay handoff observation comparison plus exact PHP adapter execution against a deterministic simulated Rust peer. No wp-settings.php bootstrap or PHP-Wasm guest.',
        'native_php_version': version.stdout.decode('ascii'),
        'guest_executed': False,
        'wasm_executed': False,
        'full_wordpress_bootstrap_verified': False,
        'source_inputs': inputs,
        'oracle': observation,
        'unpatched_wordpress_oracle': baseline_observation,
        'cross_oracle_contract': cross_oracle,
        'adapter_native_oracle': adapter_observation,
        'adapter_failure_native_oracle': adapter_failure_observation,
        'execution': {
            'exit_code': result.returncode,
            'stdout_bytes': len(result.stdout),
            'stdout_sha256': sha(result.stdout),
            'stderr_bytes': len(result.stderr),
            'php_cli_mode': '-n',
        },
        'adapter_execution': {
            'exit_code': adapter_result.returncode,
            'stdout_bytes': len(adapter_result.stdout),
            'stdout_sha256': sha(adapter_result.stdout),
            'stderr_bytes': len(adapter_result.stderr),
            'php_cli_mode': '-n',
            'simulated_peer': True,
            'wasm_executed': False,
        },
        'adapter_failure_execution': {
            'exit_code': adapter_failure_result.returncode,
            'stdout_bytes': len(adapter_failure_result.stdout),
            'stdout_sha256': sha(adapter_failure_result.stdout),
            'stderr_bytes': len(adapter_failure_result.stderr),
            'php_cli_mode': '-n',
            'simulated_peer': True,
            'wasm_executed': False,
        },
        'baseline_execution': {
            'exit_code': baseline.returncode,
            'stdout_bytes': len(baseline.stdout),
            'stdout_sha256': sha(baseline.stdout),
            'stderr_bytes': len(baseline.stderr),
            'php_cli_mode': '-n',
        },
        'rust_host_contract_tests': {
            'command': ['cargo', 'test', '--manifest-path', 'experiments/php-wasix-wp-bootstrap-probe/Cargo.toml', '--locked', '--offline'],
            'exit_code': cargo.returncode,
            'tests_passed': 39,
            'stdout_sha256': sha(cargo.stdout),
            'stderr_bytes': len(cargo.stderr),
            'scope': 'host unit contracts including pinned catalog overlay replacement; no Wasm guest execution',
        },
    }


if __name__ == '__main__':
    value = run()
    EVIDENCE.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + '\n', encoding='utf-8')
    print(value['outcome'])
