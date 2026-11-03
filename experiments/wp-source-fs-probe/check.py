"""Verify a pinned small readonly FS library and run only its pure units."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import time
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/wp-source-fs-probe'
TARGET = WORK / 'cargo-target'
PIN = 'a1a93b14f49c6ab20c3d4bbdbf58043629474d9b8dedf98a131bb80655938950'
OLD_LOCK = ROOT / 'experiments/php-wasix-wpdb-probe/Cargo.lock'
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe
spec = importlib.util.spec_from_file_location('rp_source_fs_fixed_bootstrap_preparation',
    ROOT / 'experiments/wp-bootstrap-probe/prepare.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def require(value, reason):
    if not value: raise ValueError(reason)


def sha(wire): return hashlib.sha256(wire).hexdigest()


def read(path, maximum):
    require(path.resolve(strict=True) == path and path.is_file() and path.stat().st_size <= maximum,
        'source_fs_fixed_input')
    wire = path.read_bytes()
    require(len(wire) <= maximum, 'source_fs_input_grew')
    return wire


def identity(wire): return {'bytes': len(wire), 'sha256': sha(wire)}


def inputs():
    paths = [HERE / name for name in ['Cargo.toml', 'Cargo.lock', 'src/lib.rs', 'src/tests.rs',
        'src/bin/catalog.rs', 'check.py', 'README.ja.md']]
    paths += [OLD_LOCK, ROOT / 'scripts/owned_probe.py']
    paths += [ROOT / name for name in bootstrap.inputs()]
    return {p.relative_to(ROOT).as_posix(): sha(read(p, 2 * 1024 * 1024)) for p in paths}


def registry_gate():
    lock = tomllib.loads(read(OLD_LOCK, 2 * 1024 * 1024).decode())
    old = [row for row in lock['package'] if row['name'] == 'virtual-fs' and row['version'] == '0.702.0']
    require(len(old) == 1 and old[0]['checksum'] == PIN, 'source_fs_original_virtual_fs_pin')
    current = tomllib.loads(read(HERE / 'Cargo.lock', 2 * 1024 * 1024).decode())
    selected = [row for row in current['package'] if row['name'] == 'virtual-fs']
    require(len(selected) == 1 and selected[0]['version'] == '0.702.0' and selected[0]['checksum'] == PIN,
        'source_fs_independent_virtual_fs_pin')
    home = Path(os.environ.get('CARGO_HOME', str(Path.home() / '.cargo'))).resolve()
    archives = list((home / 'registry/cache').glob('*/virtual-fs-0.702.0.crate'))
    require(len(archives) == 1, 'source_fs_one_cached_archive')
    archive = archives[0]
    wire = read(archive, 4 * 1024 * 1024)
    require(sha(wire) == PIN, 'source_fs_cached_archive_identity')
    source = home / 'registry/src' / archive.parent.name / 'virtual-fs-0.702.0'
    files = {}
    with tarfile.open(fileobj=io.BytesIO(wire), mode='r:gz') as tar:
        members = tar.getmembers()
        require(len(members) < 1000, 'source_fs_archive_member_budget')
        for member in members:
            parts = member.name.split('/')
            require(parts[0] == 'virtual-fs-0.702.0' and all(p not in ('', '.', '..') and ':' not in p
                and '\\' not in p for p in parts), 'source_fs_archive_path')
            if member.isdir(): continue
            require(member.isfile() and 0 <= member.size <= 4 * 1024 * 1024, 'source_fs_archive_regular_file')
            name = '/'.join(parts[1:])
            require(name not in files and name, 'source_fs_archive_duplicate')
            data = tar.extractfile(member).read(4 * 1024 * 1024 + 1)
            require(len(data) == member.size and read(source.joinpath(*parts[1:]), 4 * 1024 * 1024) == data,
                'source_fs_registry_source_differs')
            files[name] = identity(data)
    extras = {p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()} - set(files)
    require(extras <= {'.cargo-ok', '.cargo-checksum.json'}, 'source_fs_registry_extra_source')
    return {'archive_sha256': PIN, 'archive_bytes': len(wire), 'verified_source_files': len(files),
        'source_inventory_sha256': sha(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()),
        'archive_or_registry_modified': False}


def artifact(wire, *, catalog=False):
    selected, finished = [], False
    for line in wire.splitlines():
        row = json.loads(line)
        require(not finished, 'source_fs_cargo_after_finished')
        if row.get('reason') == 'build-finished':
            require(row.get('success') is True, 'source_fs_build_finished_failed')
            finished = True
        expected_name = 'catalog' if catalog else 'recastpress_wp_source_fs_probe'
        if row.get('reason') != 'compiler-artifact' or row.get('target', {}).get('name') != expected_name: continue
        if row.get('profile', {}).get('test') is not (not catalog): continue
        require(row['target']['kind'] == (['bin'] if catalog else ['lib'])
            and Path(row['manifest_path']).resolve() == HERE / 'Cargo.toml'
            and Path(row['target']['src_path']).resolve() == HERE / ('src/bin/catalog.rs' if catalog else 'src/lib.rs'), 'source_fs_test_artifact_source')
        path = Path(row['executable'])
        require(path.resolve(strict=True) == path and path.is_relative_to(TARGET), 'source_fs_test_artifact_path')
        selected.append(path)
    require(finished and len(selected) == 1, 'source_fs_one_test_executable')
    return selected[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    record = {'schema': 'recastpress.wp-source-fs-pure-units.v0.5', 'passed': False, 'outcome': 'not_run',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'steps': [], 'locked_offline': True,
        'database_started': False, 'http_executed': False, 'php_executed': False, 'wasi_executed': False,
        'wasm_executed': False, 'directory_fd_runtime_verified': False, 'native_filemtime_equivalent': False,
        'native_metadata_compatibility_verified': False, 'unix_permission_mode_represented': False,
        'timestamps_are_fixed_zero': True, 'directory_length_is_fixed_zero': True,
        'product_runtime_selected': False, 'owned_cleanup_completed': False, 'source_reverified_after': False}
    passed = False
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('MYSQL_', 'MARIADB_', 'RP_', 'GH_', 'GITHUB_'))
        and k.upper() not in {'ACTIONS_RUNTIME_TOKEN', 'RUST_LOG', 'RUST_BACKTRACE'}}
    env.update(CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='2', CARGO_INCREMENTAL='0')

    def run(stage, command, timeout):
        started = time.monotonic()
        pid, result = owned_probe.run_captured_probe(command, env, timeout=timeout)
        require(WORK.resolve() == WORK, 'source_fs_work_linked'); WORK.mkdir(parents=True, exist_ok=True)
        for name, wire in [('stdout', result.stdout), ('stderr', result.stderr)]:
            path = WORK / (stage + '.' + name); require(path.resolve() == path, 'source_fs_log_linked'); path.write_bytes(wire)
        record['steps'].append({'stage': stage, 'owned_pid': pid, 'owned_reaped': True,
            'exit_code': result.returncode, 'timeout_seconds': timeout,
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'stdout_sha256': sha(result.stdout), 'stderr_sha256': sha(result.stderr)})
        require(len(result.stdout) <= 16 * 1024 * 1024 and len(result.stderr) <= 65536, 'source_fs_child_capture_budget')
        require(result.returncode == 0, stage + '_failed')
        return result
    try:
        before = record['input_files'] = inputs()
        record['registry_before'] = registry_gate()
        metadata = json.loads(run('metadata', ['cargo', 'metadata', '--manifest-path', str(HERE / 'Cargo.toml'),
            '--locked', '--offline', '--format-version', '1'], 120).stdout)
        crate = [p for p in metadata['packages'] if p['name'] == 'virtual-fs' and p['version'] == '0.702.0']
        require(len(crate) == 1, 'source_fs_one_virtual_fs_dependency')
        node = [n for n in metadata['resolve']['nodes'] if n['id'] == crate[0]['id']]
        require(len(node) == 1 and not node[0]['features'], 'source_fs_no_optional_host_or_static_features')
        record['virtual_fs_enabled_features'] = node[0]['features']
        base = ['--manifest-path', str(HERE / 'Cargo.toml'), '--target-dir', str(TARGET), '--locked', '--offline']
        built = run('test-build', ['cargo', 'test', *base, '--lib', '--no-run', '--message-format=json'], 1200)
        exe = artifact(built.stdout); exe_identity = identity(read(exe, 256 * 1024 * 1024))
        listed = run('test-list', [str(exe), '--list', '--format', 'terse'], 10)
        require(not listed.stderr, 'source_fs_list_stderr')
        names = [line[:-6] for line in listed.stdout.decode().splitlines() if line.endswith(': test')]
        require(len(names) == len(set(names)) == 11 and all(name.startswith('tests::') for name in names), 'source_fs_eleven_tests')
        tested = run('test-run', [str(exe), '--test-threads=1', '--show-output'], 30)
        output = tested.stdout.decode().replace('\r\n', '\n')
        require(not tested.stderr and sorted(re.findall(r'^test (\S+) \.\.\. ok$', output, re.M)) == sorted(names)
            and 'test result: ok. 11 passed; 0 failed; 0 ignored;' in output, 'source_fs_actual_eleven_passes')
        record['tests'] = names; record['test_output'] = output
        record['test_executable'] = {'path': exe.relative_to(ROOT).as_posix(), **exe_identity}
        run('library-build', ['cargo', 'build', *base, '--lib'], 1200)
        require(identity(read(exe, 256 * 1024 * 1024)) == exe_identity, 'source_fs_test_executable_changed')
        record['bootstrap_prepared_before'] = bootstrap.verify_prepared()
        catalog_build = run('catalog-build', ['cargo', 'build', *base, '--bin', 'catalog',
            '--features', 'catalog', '--message-format=json'], 1200)
        catalog_exe = artifact(catalog_build.stdout, catalog=True)
        catalog_identity = identity(read(catalog_exe, 256 * 1024 * 1024))
        observed = run('catalog-run', [str(catalog_exe)], 120)
        require(not observed.stderr and observed.stdout.endswith(b'\n') and observed.stdout.count(b'\n') == 1,
            'source_fs_catalog_one_observation')
        value = json.loads(observed.stdout)
        require(value['schema'] == 'recastpress.wp-source-fs-catalog.v0.5' and value['passed'] is True
            and value['outcome'] == 'fixed_complete_wordpress_catalog_passed'
            and value['files'] == 3782 and value['directories'] == 467 and value['bytes'] == 110077999
            and value['all_metadata_verified'] is True and value['all_direct_child_lists_verified'] is True
            and value['deep_php_read_hash_eof_close_verified'] is True,
            'source_fs_actual_complete_catalog')
        for flag in ['wasi_executed', 'php_executed', 'wasm_executed', 'database_connected',
                'native_metadata_compatibility_verified', 'host_filesystem_fallback_in_source_fs']:
            require(value[flag] is False, 'source_fs_catalog_runtime_claim')
        manifest = record['bootstrap_prepared_before']['source_manifest']
        require(value['source_manifest_sha256'] == manifest['sha256'], 'source_fs_catalog_manifest_binding')
        require(identity(read(catalog_exe, 256 * 1024 * 1024)) == catalog_identity, 'source_fs_catalog_executable_changed')
        record['catalog_executable'] = {'path': catalog_exe.relative_to(ROOT).as_posix(), **catalog_identity}
        record['catalog_observation'] = value
        record['native_trait_catalog_executed'] = True
        record['bootstrap_prepared_after'] = bootstrap.verify_prepared()
        require(record['bootstrap_prepared_after'] == record['bootstrap_prepared_before'], 'source_fs_original_catalog_changed')
        passed = True; record['outcome'] = 'fixed_readonly_tree_eleven_units_and_complete_catalog_passed'
    except (Exception, KeyboardInterrupt) as error:
        record.update(outcome='source_fs_check_failed', failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z_]{1,100}', str(error)): record['failure'] = str(error)
    finally:
        try: owned_probe.reap_owned_probes(); record['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt): passed = False; record['outcome'] = 'source_fs_owned_cleanup_failed'
        if 'input_files' in record:
            try:
                record['registry_after'] = registry_gate()
                require(inputs() == record['input_files'] and record['registry_after'] == record['registry_before'], 'source_fs_inputs_changed')
                record['source_reverified_after'] = True
            except (Exception, KeyboardInterrupt) as error:
                passed = False; record.update(outcome='source_fs_after_verification_failed', after_failure_class=type(error).__name__)
    record['passed'] = passed
    if args.record:
        path = HERE / 'evidence/windows-pure-units.json'
        require(path.resolve() == path and path.parent.resolve() == path.parent, 'source_fs_receipt_path'); path.parent.mkdir(exist_ok=True)
        if path.exists():
            old = read(path, 256 * 1024); saved = path.with_name('windows-before-' + sha(old)[:16] + '.json')
            if saved.exists(): require(read(saved, 256 * 1024) == old, 'source_fs_previous_record_changed')
            else:
                with saved.open('xb') as stream: stream.write(old)
        path.write_bytes((json.dumps(record, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode())
    print('WP source FS: ' + record['outcome']); return 0 if passed else 1


if __name__ == '__main__': sys.exit(main())
