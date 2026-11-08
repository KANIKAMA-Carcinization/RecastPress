"""Exercise the pinned stock WordPress object cache loader in fresh Windows PHP processes."""

import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/wp-object-cache-dropin-probe'
RUST = ROOT / 'experiments/wp-object-cache-rust-probe'
ACCEPTED = {
    'run_check.py': '7ae4858ad8373eb884a4b560a487b786e47c66e1af16e3bce267d42170a32572',
    'fixtures/adapter.php': 'c85f12b4d8e827995a820db4b661106d6ffa8c373b8edb9a39415dd24d1a7235',
    'evidence/windows-native.json': 'bb00e9f6a17e31c9295b9a53de65e1e78714c111c99e53c30779b29541e07201',
}
EXTRA_CORE = ('plugin.php', 'class-wp-hook.php', 'class-wp-filter-sentinel.php', 'cache-compat.php')
MODES = ('stock', 'dropin', 'dropin', 'direct-before', 'direct-after', 'explicit-init', 'shim')
OLD_INIT = b"\t$GLOBALS['wp_object_cache'] = new WP_Object_Cache();"
NEW_INIT = b'''\trequire_once __DIR__ . '/adapter.php';
\t$GLOBALS['rp_cache_relay'] ??= new RpCacheRelay( $GLOBALS['rp_cache_probe_binary'] );
\t$GLOBALS['wp_object_cache'] = new RpRustBackedCache( $GLOBALS['rp_cache_relay'] );'''


def require(condition, code):
    if not condition:
        raise ValueError(code)


def read(path, maximum=128 * 1024):
    require(path.resolve(strict=True) == path and path.is_file(), 'fixed_dropin_file')
    with path.open('rb') as stream:
        wire = stream.read(maximum + 1)
    require(len(wire) <= maximum, 'bounded_dropin_file')
    return wire


def derive(cache):
    require(cache.count(OLD_INIT) == 1 and cache.count(b'function wp_cache_init()') == 1, 'fixed_cache_init_body')
    newline = b'\r\n' if b'\r\n' in cache else b'\n'
    replacement = NEW_INIT.replace(b'\n', newline)
    result = cache.replace(OLD_INIT, replacement)
    require(result.replace(replacement, OLD_INIT) == cache, 'only_cache_init_body_changed')
    return result


def load_accepted():
    for relative, expected in ACCEPTED.items():
        require(sha256(read(RUST / relative)).hexdigest() == expected, 'accepted_rust_cache_source_changed')
    spec = importlib.util.spec_from_file_location('rp_accepted_cache_bridge', RUST / 'run_check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inputs(bridge, native, startup, expected_sha):
    accepted = bridge.inputs(native, startup, expected_sha)
    native_proof = read(bridge.NATIVE / 'evidence/windows-native.json')
    require(sha256(native_proof).hexdigest() == bridge.NATIVE_EVIDENCE_SHA, 'fixed_stock_cache_evidence')
    stock = startup.parse(native_proof)
    rust = startup.parse(read(RUST / 'evidence/windows-native.json'))
    require(stock['passed'] is True and rust['passed'] is True and stock['observation_count'] == 56
        and accepted == rust['sources_before'] == rust['sources_after'] and accepted['stock'] == stock['sources_before'],
        'accepted_cache_sources_and_proofs')
    require(startup.canonical(stock['observation']['observations']) == startup.canonical(rust['observations']),
        'accepted_cache_proof_parity')
    manifest = startup.parse(startup.read(native.WP / 'source-manifest.json', 2 * 1024 * 1024))
    extra = {}
    for name in EXTRA_CORE:
        relative = 'wp-includes/' + name
        extra[relative] = startup.identity(read(native.WP / 'site' / relative))
        require(extra[relative] == manifest['files'][relative], 'fixed_extra_loader_core_file')
    cache = read(native.WP / 'site/wp-includes/cache.php')
    dropin = derive(cache)
    source = {'accepted': accepted, 'accepted_rust_evidence_sha256': ACCEPTED['evidence/windows-native.json'],
        'extra_wordpress_core_files': extra, 'derived_dropin': startup.identity(dropin),
        'loader': startup.identity(read(HERE / 'fixtures/loader.php')),
        'stock_transparent_shim': startup.identity(read(HERE / 'fixtures/stock-transparent.php')),
        'runner': startup.identity(read(Path(__file__).resolve()))}
    return source, dropin, stock['observation']['observations']


def trial(bridge, startup, expected_sha, mode, expected, dropin, env, rows):
    WORK.mkdir(exist_ok=True)
    require(WORK.resolve() == WORK, 'owned_dropin_root')
    with tempfile.TemporaryDirectory(prefix=mode + '-', dir=WORK) as raw:
        directory = Path(raw)
        if mode == 'stock':
            files = {}
        elif mode == 'shim':
            files = {'object-cache.php': read(HERE / 'fixtures/stock-transparent.php')}
        else:
            files = {'object-cache.php': dropin, 'adapter.php': read(RUST / 'fixtures/adapter.php')}
        for name, wire in files.items():
            with (directory / name).open('xb') as stream:
                stream.write(wire)
        command = [str(startup.PHP / 'php.exe'), '-n', str(HERE / 'fixtures/loader.php'), mode, expected_sha, str(directory)]
        process, stdout, stderr = startup.captured(command, env)
        row = {'mode': mode, 'php_process': process, 'php_stdout': startup.identity(stdout), 'php_stderr': startup.identity(stderr)}
        rows.append(row)
        require(stdout.endswith(b'\n') and stdout.count(b'\n') == 1, 'one_loader_observation')
        value = startup.parse(stdout)
        if type(value) is dict and value.get('passed') is False and type(value.get('failure')) is str:
            row['fixture_failure'] = value['failure']
        expected_exit = 255 if mode == 'direct-after' else 0
        require(process['owned_reaped'] is True and process['exit_code'] == expected_exit and not stderr, 'loader_php_exit')
        routed = mode not in ('stock', 'shim', 'direct-before')
        fields = {'passed', 'mode', 'ext_before', 'ext_after', 'cache_class', 'stock_parent', 'wrapper_source',
            'repeat_preserved_object_and_blog_scoping', 'observations', 'explicit_init', 'rust_transport', 'shim_late_hook'}
        if mode == 'direct-after':
            fields.add('direct_include_redeclared_init')
        require(type(value) is dict and set(value) == fields, 'closed_loader_fields')
        for field, want in {
            'passed': True, 'mode': mode, 'ext_before': False, 'ext_after': mode not in ('stock', 'shim'),
            'cache_class': 'RpRustBackedCache' if routed else 'WP_Object_Cache',
            'stock_parent': 'WP_Object_Cache' if routed else False, 'wrapper_source': 'dropin' if routed else 'core',
            'repeat_preserved_object_and_blog_scoping': True, 'shim_late_hook': True if mode == 'shim' else None,
        }.items():
            require(startup.canonical(value[field]) == startup.canonical(want), 'loader_observation_' + field)
            row[field] = value[field]
        if mode == 'direct-after':
            require(value['direct_include_redeclared_init'] is True, 'direct_core_include_conflict')
            row['direct_include_redeclared_init'] = True
        if mode in ('stock', 'dropin', 'shim'):
            require(startup.canonical(value['observations']) == startup.canonical(expected), 'loader_corpus_parity')
            row['observations_sha256'] = startup.sha(startup.canonical(value['observations']))
            row['observations_matched'] = len(expected)
        else:
            require(value['observations'] is None, 'no_corpus_in_boundary_case')
        explicit = {'new_object': True, 'stock': [False, False], 'routed': [True, 'rust']} if mode == 'explicit-init' else None
        require(startup.canonical(value['explicit_init']) == startup.canonical(explicit), 'explicit_init_fixture_boundary')
        if explicit is not None:
            row['explicit_init'] = value['explicit_init']
        transport = value['rust_transport']
        if routed:
            groups = bridge.GROUP_COUNTS if mode == 'dropin' else {'rp-cache-multiple': 2 if explicit else 0, 'rp-cache-integer': 0}
            flushes = 2 if mode == 'dropin' else 0
            operations = sum(groups.values()) + flushes
            require(transport['owned_reaped'] is True and type(transport['owned_pid']) is int and transport['owned_pid'] > 0
                and startup.canonical([transport['exit_code'], transport['groups'], transport['flushes'], transport['finish']]) ==
                startup.canonical([0, groups, flushes, {'ok': True, 'operations': operations, 'entries': 0, 'charged_bytes': 0}]), 'loader_rust_scope')
            output = read(directory / 'rust.stdout', 65536)
            errors = read(directory / 'rust.stderr', 65536)
            require(output.count(b'\n') == operations + 1 and output.endswith(b'\n') and not errors
                and transport['stdout_bytes'] == len(output) and transport['stderr_bytes'] == 0, 'loader_rust_capture')
            row.update(rust_transport=transport, rust_stdout=startup.identity(output), rust_stderr=startup.identity(errors))
        else:
            require(transport is None, 'no_rust_for_stock_cache')
        require(all(read(directory / name) == wire for name, wire in files.items()), 'dropin_files_changed')
    require(not directory.exists(), 'owned_dropin_directory_removed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report = {'schema': 'recastpress.wp-object-cache-dropin-native-check.v0.5', 'passed': False,
        'fresh_processes': [], 'full_wordpress_bootstrap': False, 'no_database_or_external_cache': True}
    stage = 'source_preflight'
    try:
        require(os.name == 'nt' and re.fullmatch('[a-f0-9]{64}', args.sha256), 'windows_loader_host_sha')
        bridge = load_accepted()
        native = bridge.native_controller()
        startup = native.load_startup()
        before, dropin, expected = inputs(bridge, native, startup, args.sha256)
        report['sources'] = before
        system = os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT')
        require(system and Path(system).is_absolute(), 'loader_system_root')
        env = {'SystemRoot': system, 'WINDIR': system, 'PHP_INI_SCAN_DIR': ''}
        for mode in MODES:
            stage = mode
            trial(bridge, startup, args.sha256, mode, expected, dropin, env, report['fresh_processes'])
        stage = 'source_postflight'
        require(inputs(bridge, native, startup, args.sha256) == (before, dropin, expected), 'loader_sources_changed')
        rows = report['fresh_processes']
        pids = [row[field]['owned_pid'] for row in rows for field in ('php_process', 'rust_transport') if field in row]
        require(len(set(pids)) == len(pids) == len(MODES) + 4 and not startup.OWNED_CHILDREN
            and len(startup.REAPED_CHILDREN) == len(MODES), 'all_distinct_owned_children_reaped')
        report.update(passed=True, sources_reverified_after=True, owned_trial_directories_removed=True,
            all_owned_children_reaped=True, native_corpus_observations_per_profile=56)
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    wire = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    require(not any(path.encode() in wire or path.encode().hex().encode() in wire for path in (str(ROOT), ROOT.as_posix())),
        'loader_private_path_in_report')
    if args.record and report['passed']:
        path = HERE / 'evidence/windows-native.json'
        path.parent.mkdir(exist_ok=True)
        with path.open('xb') as stream:
            stream.write(wire)
    sys.stdout.buffer.write(wire)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
