"""Native CGI startup observation only; no DB, WP, Wasm or network calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/php-cgi-startup-probe'
PHP = ROOT / '.reference/php-8.5.12'
ARCHIVE = ROOT / '.reference/php-8.5.12-nts-Win32-vs17-x64.zip'
FIXTURE = HERE / 'fixtures/startup.php'
ARCHIVE_SHA = '2ab242af38d76bdf417cde5ec048b1bcd76a5a40f39a3d15d5f046f38571c477'
MANIFEST_SHA = '159a065582fef2e837b2a67f02b9b4cd5c3b7397f644b95fd718a3d038696137'
MAP_SHA = '1711770af256947abd7aded6d89b7f32426e0a3a0f80632dcf5dbc8973892d3b'
STDIN = b'rp-cgi-startup\x00\xff\r\n'
QUERIES = {'unset': None, 'empty': '', 'flag': 'flag', 'equals': 'rp=1'}
PROFILE = {'mysqli_loaded': True, 'mail_function_present': False, 'allow_url_fopen': '0',
    'register_argc_argv': '0', 'expose_php': '1'}
CLI_EMPTY_PROFILE = {'mysqli_loaded': False, 'mail_function_present': True, 'allow_url_fopen': '1',
    'register_argc_argv': '0', 'expose_php': '1'}
LIMIT = 65536
OWNED_CHILDREN = {}
REAPED_CHILDREN = []


def require(value, code):
    if not value: raise ValueError(code)


def sha(wire): return hashlib.sha256(wire).hexdigest()


def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def identity(wire): return {'bytes': len(wire), 'sha256': sha(wire)}


def read(path, maximum=1024*1024):
    require(path.resolve(strict=True) == path and path.is_file() and 0 < path.stat().st_size <= maximum, 'fixed_input')
    value = path.read_bytes(); require(len(value) <= maximum, 'fixed_input_grew'); return value


def parse(wire):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate_json_key'); result[key] = value
        return result
    def invalid(_): raise ValueError('nonfinite_json')
    return json.loads(wire.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=invalid)


def sources():
    return {p.relative_to(ROOT).as_posix(): sha(read(p)) for p in [Path(__file__).resolve(), FIXTURE]}


def package_gate():
    wire = read(PHP / 'recastpress-tool.json'); require(sha(wire) == MANIFEST_SHA, 'fixed_php_manifest')
    manifest = parse(wire)
    require(manifest['php_version'] == '8.5.12' and manifest['archive_sha256'] == ARCHIVE_SHA, 'php_version_archive_pin')
    files = {}
    for row in manifest['files']:
        name = row['path']
        require(type(name) is str and name and '\\' not in name and not name.startswith('/')
            and all(p not in ('', '.', '..') and ':' not in p for p in name.split('/'))
            and name.casefold() not in {k.casefold() for k in files}, 'package_relative_name')
        b = read(PHP / name, 128*1024*1024)
        require(sha(b) == row['sha256'], 'package_file_changed'); files[name] = row['sha256']
    require(len(files) == 78 and sha(canonical(files)) == MAP_SHA, 'complete_78_file_map')
    actual = set()
    for path in PHP.rglob('*'):
        require(path.resolve() == path and not path.is_symlink(), 'linked_php_distribution')
        if path.is_file(): actual.add(path.relative_to(PHP).as_posix())
    require(actual == set(files) | {'recastpress-tool.json'}, 'no_extra_package_files')
    archive = read(ARCHIVE, 64*1024*1024); require(sha(archive) == ARCHIVE_SHA, 'fixed_php_archive')
    with zipfile.ZipFile(ARCHIVE) as z:
        entries = [e for e in z.infolist() if not e.is_dir()]
        require(len(entries) == 78 and {e.filename for e in entries} == set(files), 'archive_78_members')
        for e in entries:
            require(sha(z.read(e)) == files[e.filename], 'archive_and_installed_file_disagree')
    return {'version': '8.5.12', 'archive': identity(archive), 'manifest_sha256': MANIFEST_SHA,
        'verified_files': files, 'complete_file_map_sha256': MAP_SHA, 'all_78_files_and_archive_members_verified': True}


def owned(path):
    require(WORK.resolve() == WORK and WORK.is_relative_to(ROOT / 'target')
        and path.resolve() == path and path.is_relative_to(WORK), 'owned_target_path'); return path


def config():
    # Host extension path exists only in the ignored owned INI. Public receipts
    # contain its hash, never its bytes or the absolute path.
    extension = (PHP / 'ext').as_posix()
    return ('extension_dir="' + extension + '"\nextension=mysqli\ndisable_functions=mail\n'
        'allow_url_fopen=0\nregister_argc_argv=0\n').encode('ascii')


def environment(directory, ini, query):
    system_root = os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT')
    require(system_root and Path(system_root).is_absolute(), 'system_root_missing')
    value = {'SystemRoot': system_root, 'WINDIR': system_root, 'TEMP': str(directory), 'TMP': str(directory),
        'PHP_INI_SCAN_DIR': '', 'REDIRECT_STATUS': '200', 'GATEWAY_INTERFACE': 'CGI/1.1',
        'SERVER_PROTOCOL': 'HTTP/1.1', 'SERVER_SOFTWARE': 'recastpress-cgi-startup-probe',
        'SERVER_NAME': 'rp-fixture.invalid', 'SERVER_PORT': '80', 'HTTP_HOST': 'rp-fixture.invalid',
        'REMOTE_ADDR': '127.0.0.1', 'REQUEST_METHOD': 'GET', 'SCRIPT_NAME': '/__rp_startup.php',
        'SCRIPT_FILENAME': str(directory / 'startup.php'), 'DOCUMENT_ROOT': str(directory),
        'REQUEST_URI': '/__rp_startup.php' + ('' if query is None or query == '' else '?' + query),
        'HTTPS': 'off', 'REQUEST_SCHEME': 'http', 'CONTENT_LENGTH': str(len(STDIN)),
        'CONTENT_TYPE': 'application/octet-stream'}
    if query is not None: value['QUERY_STRING'] = query
    if ini is not None: value.update(PHPRC=str(ini), RP_EXPECTED_INI=str(ini))
    return value


def captured(command, env):
    """One captured Popen handle, finite live buffers, actual EOF and reap."""
    child = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    OWNED_CHILDREN[child.pid] = child
    output = [bytearray(), bytearray()]; eof = [False, False]; sent = 0; error = None
    deadline = time.monotonic() + 10; started = time.monotonic()
    try:
        for stream in [child.stdin, child.stdout, child.stderr]: os.set_blocking(stream.fileno(), False)
        while True:
            require(time.monotonic() < deadline, 'owned_child_deadline')
            if not child.stdin.closed:
                try:
                    sent += os.write(child.stdin.fileno(), STDIN[sent:])
                    if sent == len(STDIN): child.stdin.close()
                except BlockingIOError: pass
                except BrokenPipeError: child.stdin.close()
            for i, stream in enumerate([child.stdout, child.stderr]):
                if eof[i]: continue
                try: chunk = os.read(stream.fileno(), min(4096, LIMIT + 1 - len(output[i])))
                except BlockingIOError: continue
                if chunk:
                    output[i].extend(chunk); require(len(output[i]) <= LIMIT, 'live_capture_quota')
                else: eof[i] = True
            if child.poll() is not None and all(eof): break
            time.sleep(0.002)
        child.wait(timeout=5)
        require(sent == len(STDIN), 'stdin_not_delivered')
        return {'owned_pid': child.pid, 'exit_code': child.returncode, 'owned_reaped': True,
            'stdout_eof': eof[0], 'stderr_eof': eof[1], 'elapsed_ms': round((time.monotonic()-started)*1000),
            'live_capture_limit_each': LIMIT, 'deadline_seconds': 10}, bytes(output[0]), bytes(output[1])
    except BaseException as failure:
        error = failure; raise
    finally:
        try:
            if child.poll() is None: child.kill()
            child.wait(timeout=5)
            for stream in [child.stdin, child.stdout, child.stderr]: stream.close()
            REAPED_CHILDREN.append(child.pid); OWNED_CHILDREN.pop(child.pid)
        except BaseException:
            if error is not None: error.add_note('owned_child_cleanup_failed')
            else: raise


def complete_cgi(wire):
    require(0 < len(wire) <= LIMIT and b'\r\n\r\n' in wire, 'complete_cgi_headers')
    headers, body = wire.split(b'\r\n\r\n', 1)
    require(0 < len(headers) <= 16384 and headers.count(b'\r\n') <= 63, 'cgi_header_quota')
    parsed = []
    for line in headers.split(b'\r\n'):
        require(b':' in line and line[:1] not in (b' ', b'\t'), 'cgi_header_line')
        key, value = line.split(b':', 1); value = value.strip(b' \t')
        require(re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
            and all(c == 9 or c >= 32 and c != 127 for c in value), 'cgi_header_bytes')
        parsed.append({'name': key.decode('ascii').lower(), 'value_hex': value.hex()})
    require(parsed == [{'name': 'x-powered-by', 'value_hex': b'PHP/8.5.12'.hex()},
        {'name': 'content-type', 'value_hex': b'application/json'.hex()}], 'unchanged_default_expose_php_headers')
    require(body.endswith(b'\n') and body.count(b'\n') == 1, 'single_cgi_json_line')
    return {'status': 200, 'headers': parsed, 'body': identity(body), 'wire': identity(wire)}, parse(body)


def validate_observation(value, query, sealed):
    require(set(value) == {'schema', 'runtime', 'profile', 'ini_loaded', 'ini_loaded_expected',
        'additional_ini_scanned', 'query_environment_present', 'query_string', 'get_values', 'argc', 'stdin'},
        'closed_public_observation')
    require(value['schema'] == 'recastpress.php-cgi-startup-observation.v0.5', 'observation_schema')
    require(canonical(value['runtime']) == canonical({'version': '8.5.12', 'version_id': 80512,
        'int_size': 8, 'sapi': 'cgi-fcgi'}), 'native_cgi_runtime')
    require(value['query_environment_present'] is (query is not None) and value['query_string'] == query,
        'query_shape_changed')
    require(value['stdin'] == identity(STDIN), 'get_stdin_bytes_changed')
    require(value['get_values'] == ([] if query is None or query == '' else {'flag': ''} if query == 'flag' else {'rp': '1'}),
        'query_values_changed')
    if sealed:
        require(canonical(value['profile']) == canonical(PROFILE) and value['ini_loaded'] is True
            and value['ini_loaded_expected'] is True and value['additional_ini_scanned'] is False
            and value['argc'] is None, 'sealed_ini_profile_not_effective')


def run(record=False):
    report = {'schema': 'recastpress.php-cgi-startup-check.v0.5', 'passed': False, 'outcome': 'not_run',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': sys.platform,
        'observations': [], 'owned_cleanup_completed': False, 'owned_scratch_reclaimed': False,
        'wordpress_executed': False, 'database_connected': False, 'network_calls_made': False,
        'wasm_executed': False, 'product_configuration_changed': False, 'header_behavior_changed': False,
        'wasm_ini_loading_verified': False, 'product_request_abi_verified': False,
        'native_process_memory_limit_verified': False}
    directory = None; passed = False
    reaped_before = len(REAPED_CHILDREN)
    try:
        require(os.name == 'nt', 'native_windows_only')
        require(not OWNED_CHILDREN, 'previous_owned_child_not_reaped')
        report['input_files'] = sources(); report['package_before'] = package_gate()
        owned(WORK).mkdir(parents=True, exist_ok=True)
        directory = owned(WORK / ('run-' + uuid.uuid4().hex)); directory.mkdir(exist_ok=False)
        ini = directory / 'php.ini'; ini.write_bytes(config())
        copied = directory / 'startup.php'; copied.write_bytes(read(FIXTURE))
        report['owned_config_before'] = identity(read(ini))
        report['config_semantics'] = {'selection': 'PHPRC absolute owned php.ini file',
            'additional_scan_directory': 'empty', 'extension_dir': 'fixed verified PHP distribution ext directory',
            'extension': 'mysqli', 'disable_functions': 'mail', 'allow_url_fopen': '0', 'register_argc_argv': '0',
            'expose_php_override': False, 'absolute_configuration_values_recorded': False}
        for method, cases in [('owned_ini', QUERIES), ('cli_flags_control', {k: QUERIES[k] for k in ['unset', 'empty']})]:
            for shape, query in cases.items():
                sealed = method == 'owned_ini'
                env = environment(directory, ini if sealed else None, query)
                arguments = [] if sealed else ['-n', '-d', 'extension_dir=' + (PHP / 'ext').as_posix(),
                    '-d', 'extension=mysqli', '-d', 'disable_functions=mail', '-d', 'allow_url_fopen=0',
                    '-d', 'register_argc_argv=0']
                process, stdout, stderr = captured([str(PHP / 'php-cgi.exe'), *arguments], env)
                row = {'method': method, 'query_shape': shape, 'query': query, 'process': process,
                    'command_has_cli_options': bool(arguments), 'environment_keys': sorted(env),
                    'environment_absolute_values_recorded': False, 'stdout': identity(stdout), 'stderr': identity(stderr)}
                report['observations'].append(row)
                require(process['exit_code'] == 0 and not stderr, 'native_cgi_exit_or_stderr')
                row['response'], row['observation'] = complete_cgi(stdout)
                validate_observation(row['observation'], query, sealed)
                row['requested_profile_matched'] = canonical(row['observation']['profile']) == canonical(PROFILE)
                row['native_only'] = True
        sealed = [r for r in report['observations'] if r['method'] == 'owned_ini']
        require(len(sealed) == 4 and all(r['requested_profile_matched'] for r in sealed), 'all_four_ini_profiles')
        controls = {r['query_shape']: r for r in report['observations'] if r['method'] == 'cli_flags_control'}
        require(controls['unset']['requested_profile_matched'] is True, 'cli_unset_control_changed')
        require(controls['empty']['requested_profile_matched'] is False, 'cli_empty_negative_not_reproduced')
        require(canonical(controls['empty']['observation']['profile']) == canonical(CLI_EMPTY_PROFILE),
            'cli_empty_actual_negative_profile_changed')
        report['cli_empty_profile_gap_reproduced'] = True
        report['all_four_sealed_ini_profiles_passed'] = True
        report['owned_config_after'] = identity(read(ini)); require(report['owned_config_before'] == report['owned_config_after'], 'config_changed')
        require(read(copied) == read(FIXTURE), 'owned_fixture_copy_changed')
        report['package_after'] = package_gate(); require(report['package_before'] == report['package_after'], 'package_changed')
        require(report['input_files'] == sources(), 'source_changed'); report['source_reverified_after'] = True
        passed = True
    except (Exception, KeyboardInterrupt) as failure:
        report['failure_class'] = type(failure).__name__
        if isinstance(failure, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(failure)):
            report['failure'] = str(failure)
    finally:
        # Retry only captured handles from this checker. A failed reap is never
        # inferred from an empty list of successful observations.
        for pid, child in list(OWNED_CHILDREN.items()):
            try:
                if child.poll() is None: child.kill()
                child.wait(timeout=5)
                for stream in [child.stdin, child.stdout, child.stderr]: stream.close()
                REAPED_CHILDREN.append(pid); OWNED_CHILDREN.pop(pid)
            except (Exception, KeyboardInterrupt): passed = False; report['child_cleanup_failed'] = True
        if directory is not None and not OWNED_CHILDREN:
            # Remove only the two known files from this freshly owned folder;
            # unknown contents cause a failure and are never recursively erased.
            try:
                for name in ['startup.php', 'php.ini']:
                    path = owned(directory / name)
                    if path.exists(): path.unlink()
                owned(directory).rmdir(); report['owned_scratch_reclaimed'] = True
            except (Exception, KeyboardInterrupt): passed = False; report['cleanup_failed'] = True
        report['owned_child_count'] = len(REAPED_CHILDREN) - reaped_before + len(OWNED_CHILDREN)
        report['owned_reaped_count'] = len(REAPED_CHILDREN) - reaped_before
        report['owned_cleanup_completed'] = not OWNED_CHILDREN and 'child_cleanup_failed' not in report
        passed = passed and report['owned_cleanup_completed']
    report.update(passed=passed, outcome='sealed_ini_all_query_shapes_passed' if passed else 'cgi_startup_probe_failed')
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    require(len(wire) <= 256*1024 and str(ROOT).encode() not in wire and ROOT.as_posix().encode() not in wire,
        'public_receipt_size_or_private_path')
    if record:
        destination = HERE / 'evidence/windows-native.json'; destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            previous = read(destination, 256*1024); backup = destination.with_name('windows-native-before-' + sha(previous)[:16] + '.json')
            if backup.exists(): require(read(backup, 256*1024) == previous, 'old_receipt_changed')
            else:
                with backup.open('xb') as stream: stream.write(previous)
        destination.write_bytes(wire)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--record', action='store_true'); args = parser.parse_args()
    result = run(args.record); print(result['outcome']); sys.exit(0 if result['passed'] else 1)
