"""Replay the fixed native WordPress hook oracle for a local demo fallback.

This runs only the pinned PHP CLI oracle. It does not run wp-settings.php,
Rust Dispatcher, PHP-Wasm, a database, or the product runtime.
"""
import hashlib
import json
from pathlib import Path, PureWindowsPath
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PHP = ROOT / '.reference/php-8.5.12/php.exe'
PHP_ARCHIVE = ROOT / '.reference/php-8.5.12-nts-Win32-vs17-x64.zip'
PHP_MANIFEST = ROOT / '.reference/php-8.5.12/recastpress-tool.json'
WP_MANIFEST = ROOT / 'target/wp-bootstrap-probe/source-manifest.json'
PREPARED = ROOT / 'experiments/php-wasix-wp-bootstrap-probe/evidence/windows-source-preparation.json'
BUILD = ROOT / 'experiments/php-wasix-wp-bootstrap-probe/evidence/windows-build.json'
RECEIPT = ROOT / 'experiments/php-wasix-wp-bootstrap-probe/evidence/windows-native-hook-oracle.json'
RUN_RECEIPT = ROOT / 'experiments/php-wasix-wp-bootstrap-probe/evidence/windows-native-hook-demo-run.json'
FIXTURE_RELATIVE = 'experiments/php-wasix-wp-bootstrap-probe/fixtures/hook_native_oracle.php'
FIXTURE = ROOT / FIXTURE_RELATIVE
EXPECTED_VALUE = 'RP Bootstrap Fixture|first|second'
EXPECTED_CALLBACKS = ['rp_boot_hook_first', 'rp_boot_hook_second']


def require(value, reason):
    if not value:
        raise ValueError(reason)


def fixed(path):
    require(path.resolve(strict=True) == path and path.is_file(), 'fixed_input_missing_or_linked')
    return path.read_bytes()


def repo_fixed(relative):
    path = Path(relative)
    windows_path = PureWindowsPath(relative)
    require(not path.is_absolute() and not windows_path.is_absolute()
        and not windows_path.drive and '..' not in path.parts
        and '..' not in windows_path.parts,
        'receipt_path_must_be_repository_relative')
    candidate = ROOT / path
    resolved = candidate.resolve(strict=True)
    require(resolved == candidate
        and resolved.is_relative_to(ROOT.resolve(strict=True)),
        'receipt_path_escapes_repository_or_uses_link')
    return fixed(candidate)


def sha(wire):
    return hashlib.sha256(wire).hexdigest()


def read_json(path):
    return json.loads(fixed(path).decode('utf-8'))


def main():
    php_wire = fixed(PHP)
    archive_wire = fixed(PHP_ARCHIVE)
    tool = read_json(PHP_MANIFEST)
    php_sha = sha(php_wire)
    require(tool['php_version'] == '8.5.12', 'fixed_php_version')
    require(sha(archive_wire) == tool['archive_sha256'], 'fixed_php_archive_identity')
    php_row = [row for row in tool['files'] if row['path'] == 'php.exe']
    require(len(php_row) == 1 and php_sha == php_row[0]['sha256'], 'fixed_php_binary_identity')

    build_wire = fixed(BUILD)
    build = json.loads(build_wire.decode('utf-8'))
    require(build['passed'] is True and build['source_reverified_after'] is True,
        'current_host_build_receipt')
    require(len(build['input_files']) == 149
        and all(sha(repo_fixed(relative)) == digest
                for relative, digest in build['input_files'].items()),
        'host_build_inputs_changed_since_receipt')
    prepared = read_json(PREPARED)
    require(len(build['input_files']) == 149 and len(prepared['input_files']) == 33,
        'source_snapshot_size')
    require(all(build['input_files'].get(name) == digest
        for name, digest in prepared['input_files'].items()), 'prepared_source_snapshot_mismatch')

    fixture_wire = fixed(FIXTURE)
    fixture_sha = sha(fixture_wire)
    require(build['input_files'].get(FIXTURE_RELATIVE) == fixture_sha,
        'hook_oracle_fixture_not_in_current_build')

    wp_archive = fixed(ROOT / '.reference/wordpress-7.1.3.tar.gz')
    require(len(wp_archive) == prepared['core_archive']['bytes']
        and sha(wp_archive) == prepared['core_archive']['sha256'], 'fixed_wordpress_archive_identity')
    source_manifest = read_json(WP_MANIFEST)
    require(len(fixed(WP_MANIFEST)) == prepared['core_manifest']['bytes']
        and sha(fixed(WP_MANIFEST)) == prepared['core_manifest']['sha256'],
        'prepared_wordpress_manifest_identity')
    require(source_manifest['wordpress_version'] == '7.1.3'
        and source_manifest['archive']['sha256'] == prepared['core_archive']['sha256']
        and source_manifest['file_count'] == prepared['core_inventory']['files']
        and source_manifest['directory_count'] == prepared['core_inventory']['directories']
        and source_manifest['file_inventory_sha256'] == prepared['core_inventory']['file_inventory_sha256']
        and source_manifest['directory_inventory_sha256'] == prepared['core_inventory']['directory_inventory_sha256'],
        'prepared_wordpress_tree_identity')
    oracle_source_hashes = json.loads(fixed(RECEIPT).decode('utf-8'))[
        'native_hook_oracle']['wordpress_source_sha256']
    for core_path, expected in oracle_source_hashes.items():
        row = source_manifest['files'].get(core_path)
        require(row is not None and row['sha256'] == expected
            and sha(repo_fixed(Path('target/wp-bootstrap-probe/site') / core_path)) == expected,
            'prepared_wordpress_hook_source_changed')

    receipt_wire = fixed(RECEIPT)
    receipt = json.loads(receipt_wire.decode('utf-8'))
    require(receipt['passed'] is True
        and receipt['source_snapshot']['sha256'] == sha(build_wire)
        and receipt['fixture']['sha256'] == fixture_sha
        and receipt['php_cli']['binary_sha256'] == php_sha,
        'native_oracle_receipt_source_binding')

    version = subprocess.run([str(PHP), '-n', '-r', 'echo PHP_VERSION;'],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=30, check=False)
    require(version.returncode == 0 and version.stdout == b'8.5.12' and not version.stderr,
        'fixed_php_cli_version_execution')

    run = subprocess.run([str(PHP), '-n', str(FIXTURE)], cwd=ROOT,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=30, check=False)
    require(run.returncode == 0 and not run.stderr and 0 < len(run.stdout) <= 8192
        and run.stdout.endswith(b'\n') and run.stdout.count(b'\n') == 1,
        'native_hook_oracle_execution')
    observed = json.loads(run.stdout.decode('utf-8'))
    require(observed == receipt['native_hook_oracle'], 'native_hook_oracle_result_differs')
    require(observed['wordpress_version'] == '7.1.3'
        and observed['event_order'] == EXPECTED_CALLBACKS
        and observed['filtered_value'] == EXPECTED_VALUE,
        'native_hook_demo_expected_result')
    require(sha(run.stdout) == receipt['execution']['stdout_sha256']
        and len(run.stdout) == receipt['execution']['stdout_bytes'],
        'native_hook_demo_output_identity')

    run_record = {
        'schema': 'recastpress.php-wasix-wp-bootstrap-windows-native-hook-demo-run.v0.1',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'passed': True,
        'runner': {'path': 'scripts/run_recastpress_native_hook_demo.py',
                   'sha256': sha(Path(__file__).read_bytes())},
        'host_build_receipt': {'path': BUILD.relative_to(ROOT).as_posix(), 'sha256': sha(build_wire)},
        'oracle_receipt': {'path': RECEIPT.relative_to(ROOT).as_posix(), 'sha256': sha(receipt_wire)},
        'php_cli': {'path': PHP.relative_to(ROOT).as_posix(), 'version': '8.5.12', 'sha256': php_sha},
        'fixture': {'path': FIXTURE_RELATIVE, 'sha256': fixture_sha},
        'execution': {'argv': [PHP.relative_to(ROOT).as_posix(), '-n', FIXTURE_RELATIVE],
                      'exit_code': run.returncode, 'stdout_bytes': len(run.stdout),
                      'stdout_sha256': sha(run.stdout), 'stderr_bytes': len(run.stderr)},
        'result': {'wordpress_version': observed['wordpress_version'],
                   'scenario': observed['scenario'], 'event_order': observed['event_order'],
                   'filtered_value': observed['filtered_value']},
        'scope': {'native_hook_oracle_only': True, 'wp_settings_bootstrap': False,
                  'rust_dispatcher': False, 'php_wasm': False, 'database': False},
    }
    require(RUN_RECEIPT.parent.resolve(strict=True) == RUN_RECEIPT.parent
        and not RUN_RECEIPT.is_symlink()
        and (not RUN_RECEIPT.exists()
             or (RUN_RECEIPT.is_file() and RUN_RECEIPT.resolve(strict=True) == RUN_RECEIPT)),
        'unsafe_demo_receipt_output_path')
    RUN_RECEIPT.write_text(json.dumps(run_record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print('PASS: RecastPress native WordPress hook oracle')
    print('PHP: 8.5.12; WordPress: 7.1.3; scenario: option_blogname')
    print('Callbacks: ' + ' -> '.join(observed['event_order']))
    print('Filtered value: ' + observed['filtered_value'])
    print('Scope: native WP_Hook oracle only; no wp-settings.php, Rust Dispatcher, PHP-Wasm, or DB.')
    print('Receipt: experiments/php-wasix-wp-bootstrap-probe/evidence/windows-native-hook-oracle.json')
    print('Run record: experiments/php-wasix-wp-bootstrap-probe/evidence/windows-native-hook-demo-run.json')


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('Native hook demo failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
