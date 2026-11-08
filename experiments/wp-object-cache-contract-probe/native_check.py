"""Run the fixed stock WordPress object-cache corpus in two Windows PHP processes."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STARTUP = ROOT / 'experiments/php-cgi-startup-probe/check.py'
WP = ROOT / 'target/wp-bootstrap-probe'
STARTUP_SHA = 'f22b503c38feab1e11ceee2c6833350f529f3fe1c59c3f59fb6a0b84da9f96f4'
MANIFEST_SHA = '734275a54c29e1df269ae5fe34782f567de05a1b3230aad7faed252b37d783b3'
CORE_FILES = ('version.php', 'load.php', 'functions.php', 'option.php', 'cache.php', 'class-wp-object-cache.php')


def load_startup():
    from hashlib import sha256

    if STARTUP.resolve(strict=True) != STARTUP or sha256(STARTUP.read_bytes()).hexdigest() != STARTUP_SHA:
        raise ValueError('accepted_capture_source_changed')
    spec = importlib.util.spec_from_file_location('rp_object_cache_owned_capture', STARTUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.STDIN = b''
    return module


def inputs(startup):
    manifest_path = WP / 'source-manifest.json'
    manifest_wire = startup.read(manifest_path, 2 * 1024 * 1024)
    startup.require(startup.sha(manifest_wire) == MANIFEST_SHA, 'fixed_wordpress_source_manifest')
    manifest = startup.parse(manifest_wire)
    startup.require(manifest['wordpress_version'] == '7.1.3' and manifest['file_count'] == 3782,
        'fixed_wordpress_source_version')
    wp_inputs = {}
    for name in CORE_FILES:
        relative = 'wp-includes/' + name
        wire = startup.read(WP / 'site' / relative, 1024 * 1024)
        identity = startup.identity(wire)
        startup.require(identity == manifest['files'][relative], 'fixed_wordpress_cache_source')
        wp_inputs[relative] = identity
    return {'wordpress': {'version': '7.1.3', 'source_manifest': startup.identity(manifest_wire),
        'archive_sha256_from_accepted_manifest': manifest['archive']['sha256'], 'loaded_core_files': wp_inputs},
        'php': startup.package_gate(), 'capture_source_sha256': STARTUP_SHA,
        'experiment_source_sha256': {name: startup.sha(startup.read(HERE / name))
            for name in ('native_check.py', 'fixtures/native.php', 'fixtures/corpus.php')}}


def run():
    report = {'schema': 'recastpress.wp-object-cache-native-check.v0.5', 'passed': False,
        'no_database_or_external_cache': True, 'full_wordpress_bootstrap': False,
        'wasm_executed': False, 'fresh_processes': []}
    startup = None
    stage = 'source_preflight'
    try:
        if os.name != 'nt':
            raise ValueError('fixed_windows_php_distribution')
        startup = load_startup()
        before = inputs(startup)
        report['sources_before'] = before
        system_root = os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT')
        startup.require(system_root and Path(system_root).is_absolute(), 'native_system_root')
        environment = {'SystemRoot': system_root, 'WINDIR': system_root, 'PHP_INI_SCAN_DIR': ''}
        command = [str(startup.PHP / 'php.exe'), '-n', str(HERE / 'fixtures/native.php')]
        wire_baseline = None
        for label in ('first', 'fresh_repeat'):
            stage = label
            process, stdout, stderr = startup.captured(command, environment)
            row = {'case': label, 'process': process, 'stdout': startup.identity(stdout),
                'stderr': startup.identity(stderr), 'stdin_bytes': 0}
            report['fresh_processes'].append(row)
            startup.require(stdout.endswith(b'\n') and stdout.count(b'\n') == 1, 'one_native_observation')
            observed = startup.parse(stdout)
            if type(observed) is dict and observed.get('passed') is False:
                code = observed.get('failure')
                if type(code) is str and re.fullmatch('[a-z][a-z0-9_]{0,80}', code):
                    row['fixture_failure'] = code
            startup.require(process['exit_code'] == 0 and not stderr and process['owned_reaped'] is True,
                'clean_native_cache_exit')
            startup.require(set(observed) == {'schema', 'passed', 'php_version', 'php_integer_bytes',
                'wordpress_version', 'stock_cache_class', 'profile', 'observations'}
                and observed['schema'] == 'recastpress.wp-object-cache-native-observation.v0.5'
                and observed['passed'] is True and observed['php_version'] == '8.5.12'
                and observed['php_integer_bytes'] == 8 and observed['wordpress_version'] == '7.1.3'
                and observed['stock_cache_class'] == 'WP_Object_Cache'
                and observed['profile'] == 'isolated_multisite_cache_prefix', 'closed_native_cache_profile')
            values = observed['observations']
            startup.require(type(values) is dict and len(values) == 56
                and startup.canonical(values['fresh_process_start']) == b'[[false,false],[false,false],[false,false]]'
                and startup.canonical(values['close_leaves_request_values']) ==
                    b'[[true,"local"],[true,"global"],[true,"runtime"]]', 'fresh_cache_boundaries')
            if wire_baseline is None:
                wire_baseline = stdout
                report['observation'] = observed
            else:
                startup.require(stdout == wire_baseline, 'native_cache_fresh_repeat_bytes')
        stage = 'source_postflight'
        after = inputs(startup)
        startup.require(before == after, 'cache_inputs_changed_during_execution')
        startup.require(len(set(startup.REAPED_CHILDREN)) == 2 and not startup.OWNED_CHILDREN,
            'cache_process_reaping')
        report.update(passed=True, observation_count=len(values), identical_fresh_stdout=True,
            sources_reverified_after=True, all_owned_children_reaped=True)
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        if startup is not None:
            for pid, child in tuple(startup.OWNED_CHILDREN.items()):
                try:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)
                    for stream in (child.stdin, child.stdout, child.stderr):
                        stream.close()
                    startup.OWNED_CHILDREN.pop(pid)
                except (Exception, KeyboardInterrupt):
                    report['passed'] = False
                    report['owned_cleanup_failed'] = True
    wire = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    if any(value.encode() in wire or value.encode().hex().encode() in wire for value in (str(ROOT), ROOT.as_posix())):
        raise ValueError('native_cache_report_private_path')
    return report, wire


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report, wire = run()
    if args.record and report['passed']:
        target = HERE / 'evidence/windows-native.json'
        target.parent.mkdir(exist_ok=True)
        with target.open('xb') as stream:
            stream.write(wire)
    sys.stdout.buffer.write(wire)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
