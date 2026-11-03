"""Native-only eight-case CGI framing comparison; no WP, DB, Wasm or network."""
import argparse
import ast
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/php-cgi-framing-probe'
STARTUP = ROOT / 'experiments/php-cgi-startup-probe/check.py'
STARTUP_SHA = 'f22b503c38feab1e11ceee2c6833350f529f3fe1c59c3f59fb6a0b84da9f96f4'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


prepared = load('rp_framing_prepared', HERE / 'prepare.py')
require, sha, identity = prepared.require, prepared.sha, prepared.identity
require(sha(STARTUP.read_bytes()) == STARTUP_SHA, 'frozen_startup_helper_changed')
startup = load('rp_framing_frozen_startup', STARTUP)
OWNED_CHILDREN = {}
REAPED_CHILDREN = []


def capture_derivation():
    wire = STARTUP.read_bytes(); require(sha(wire) == STARTUP_SHA, 'frozen_startup_helper_changed')
    source = wire.decode('utf-8'); tree = ast.parse(source)
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'captured']
    require(len(matches) == 1, 'one_frozen_capture_function')
    original = ast.get_source_segment(source, matches[0])
    changes = [('deadline = time.monotonic() + 10;', 'deadline = time.monotonic() + 20;'),
        ("'deadline_seconds': 10", "'deadline_seconds': 20")]
    derived = original
    for before, after in changes:
        require(derived.count(before) == 1, 'exact_capture_derivation'); derived = derived.replace(before, after)
    namespace = {'subprocess': subprocess, 'os': os, 'time': time, 'require': require, 'STDIN': b'',
        'LIMIT': 3*1024*1024, 'OWNED_CHILDREN': OWNED_CHILDREN, 'REAPED_CHILDREN': REAPED_CHILDREN}
    exec(compile(derived, '<owned-framing-capture>', 'exec'), namespace)
    return namespace['captured'], {'source_sha256': STARTUP_SHA, 'original_function_sha256': sha(original.encode()),
        'derived_function_sha256': sha(derived.encode()), 'exact_deadline_replacements': len(changes),
        'stdin_bytes': 0, 'live_capture_limit_each': 3*1024*1024, 'deadline_seconds': 20,
        'original_module_globals_modified': False, 'isolated_child_registry': True}


def owned(path):
    require(WORK.resolve() == WORK and path.resolve() == path and path.is_relative_to(WORK)
        and WORK.is_relative_to(ROOT / 'target'), 'owned_target_path'); return path


def environment(directory, ini, script):
    system_root = os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT')
    require(system_root and Path(system_root).is_absolute(), 'system_root_missing')
    return {'SystemRoot': system_root, 'WINDIR': system_root, 'TEMP': str(directory), 'TMP': str(directory),
        'PHPRC': str(ini), 'PHP_INI_SCAN_DIR': '', 'REDIRECT_STATUS': '200', 'GATEWAY_INTERFACE': 'CGI/1.1',
        'SERVER_PROTOCOL': 'HTTP/1.1', 'SERVER_SOFTWARE': 'recastpress-cgi-framing-probe',
        'SERVER_NAME': 'rp-fixture.invalid', 'SERVER_PORT': '80', 'HTTP_HOST': 'rp-fixture.invalid',
        'REMOTE_ADDR': '127.0.0.1', 'REQUEST_METHOD': 'GET', 'SCRIPT_NAME': '/__rp_framing.php',
        'SCRIPT_FILENAME': str(directory / script), 'DOCUMENT_ROOT': str(directory),
        'REQUEST_URI': '/__rp_framing.php?rp_request=1', 'QUERY_STRING': 'rp_request=1',
        'HTTPS': 'off', 'REQUEST_SCHEME': 'http', 'CONTENT_LENGTH': '0'}


def run(record=False):
    report = {'schema': 'recastpress.php-cgi-framing-check.v0.5', 'passed': False,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': sys.platform, 'observations': [],
        'native_only': True, 'wordpress_executed': False, 'database_connected': False, 'wasm_executed': False,
        'network_calls_made': False, 'product_runtime_selected': False, 'unknown_output_recorded': False,
        'owned_cleanup_completed': False, 'owned_scratch_reclaimed': False}
    passed = False; directory = None; reaped_before = len(REAPED_CHILDREN)
    names = ['common.php'] + [case['script'] for case in prepared.cases()]
    try:
        require(os.name == 'nt', 'native_windows_only')
        require(not OWNED_CHILDREN, 'previous_child_not_reaped')
        require(not any(key.upper().startswith('PHP') or key.upper() == 'QUERY_STRING' for key in os.environ), 'ambient_php_or_query_environment')
        report['prepared'] = prepared.prepare()
        report['input_files'] = prepared.sources(); report['frozen_startup_source_sha256'] = STARTUP_SHA
        report['package_before'] = startup.package_gate()
        capture, report['capture_derivation'] = capture_derivation()
        owned(WORK).mkdir(parents=True, exist_ok=True)
        directory = owned(WORK / ('run-' + uuid.uuid4().hex)); directory.mkdir(exist_ok=False)
        ini = directory / 'php.ini'; ini.write_bytes(startup.config())
        report['config_before'] = identity(startup.read(ini))
        report['config_semantics'] = {'selection': 'explicit PHPRC owned INI file', 'mysqli': True,
            'mail_function_disabled': True, 'allow_url_fopen': '0', 'register_argc_argv': '0',
            'expose_php_overridden': False, 'additional_scan_directory': 'empty', 'private_ini_bytes_recorded': False}
        for name in names: (directory / name).write_bytes(prepared.read(HERE / 'fixtures' / name))
        for case in prepared.cases():
            env = environment(directory, ini, case['script'])
            process, stdout, stderr = capture([str(startup.PHP / 'php-cgi.exe')], env)
            row = {'case': case['case'], 'process': process, 'stdout': identity(stdout), 'stderr': identity(stderr),
                'environment_keys': sorted(env), 'absolute_environment_values_recorded': False,
                'command_has_cli_options': False, 'request_body_bytes': 0}
            report['observations'].append(row)
            value, headers, body = prepared.expected(case)
            expected_wire = b'\r\n'.join(headers) + b'\r\n\r\n' + body
            row['complete_cgi_wire_matches'] = stdout == expected_wire
            require(process['exit_code'] == 0 and not stderr, 'native_exit_or_stderr')
            require(row['complete_cgi_wire_matches'], 'exact_native_cgi_wire')
            row['response'] = {'status': 200, 'header_lines_hex': [line.hex() for line in headers],
                'body': identity(body), 'payload_bytes': value['payload_bytes'], 'exact_json_body_verified': True}
        require(len(report['observations']) == 8, 'all_eight_native_cases')
        for name in names:
            require(startup.read(directory / name) == prepared.read(HERE / 'fixtures' / name), 'owned_fixture_changed')
        report['config_after'] = identity(startup.read(ini)); require(report['config_before'] == report['config_after'], 'owned_config_changed')
        report['package_after'] = startup.package_gate(); require(report['package_before'] == report['package_after'], 'package_changed')
        require(report['input_files'] == prepared.sources() and sha(STARTUP.read_bytes()) == STARTUP_SHA, 'source_changed')
        report['source_reverified_after'] = True; passed = True
    except (Exception, KeyboardInterrupt) as failure:
        report['failure_class'] = type(failure).__name__
        if isinstance(failure, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(failure)): report['failure'] = str(failure)
    finally:
        for pid, child in list(OWNED_CHILDREN.items()):
            try:
                if child.poll() is None: child.kill()
                child.wait(timeout=5)
                for stream in [child.stdin, child.stdout, child.stderr]: stream.close()
                REAPED_CHILDREN.append(pid); OWNED_CHILDREN.pop(pid)
            except (Exception, KeyboardInterrupt): passed = False; report['child_cleanup_failed'] = True
        if directory is not None and not OWNED_CHILDREN:
            try:
                for name in names + ['php.ini']:
                    path = owned(directory / name)
                    if path.exists(): path.unlink()
                owned(directory).rmdir(); report['owned_scratch_reclaimed'] = True
            except (Exception, KeyboardInterrupt): passed = False; report['scratch_cleanup_failed'] = True
        report['owned_child_count'] = len(REAPED_CHILDREN) - reaped_before + len(OWNED_CHILDREN)
        report['owned_reaped_count'] = len(REAPED_CHILDREN) - reaped_before
        report['owned_cleanup_completed'] = not OWNED_CHILDREN and 'child_cleanup_failed' not in report
        passed = passed and report['owned_cleanup_completed'] and report['owned_scratch_reclaimed']
    report.update(passed=passed, outcome='native_all_eight_cgi_frames_exact' if passed else 'native_cgi_framing_probe_failed')
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    require(len(wire) <= 256*1024 and str(ROOT).encode() not in wire and ROOT.as_posix().encode() not in wire, 'public_record_quota_or_private_path')
    if record:
        destination = HERE / 'evidence/windows-native.json'; destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            previous = startup.read(destination, 256*1024)
            backup = destination.with_name('windows-native-before-' + sha(previous)[:16] + '.json')
            if backup.exists(): require(startup.read(backup, 256*1024) == previous, 'immutable_previous_record')
            else:
                with backup.open('xb') as stream: stream.write(previous)
        destination.write_bytes(wire)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--record', action='store_true'); args = parser.parse_args()
    result = run(args.record); print(result['outcome']); sys.exit(0 if result['passed'] else 1)
