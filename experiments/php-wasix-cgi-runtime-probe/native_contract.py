"""Pinned native CGI reference for the guard-adapted six-case PHP runtime fixture.

No Wasm, patched Zend backend, listener or product fallback is executed. Pure
WordPress hooks use exactly three verified source files, without WP bootstrap
or a database. The native executable and all 78 distribution files are checked
against the same
pinned archive before and after this one owned, ten-second CGI process.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import stat
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1].resolve()
CLI_HELPER = ROOT / 'experiments/php-wasix-fiber-probe/native_contract.py'
_spec = importlib.util.spec_from_file_location('recastpress_runtime_cgi_native_package_reference', CLI_HELPER)
fiber = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fiber)
native = fiber.native
require, equal, digest = fiber.require, fiber.equal, fiber.digest
CANONICAL_FIXTURE = native.FIXTURE
CANONICAL_SHA256 = '5ab341e1207fc6bfb8634758bc38b6af7f6b60ba3c621c8acf52b603e5023080'
FIXTURE = HERE / 'fixtures/runtime_cgi_contract.php'
CANONICAL_GUARD = b"if (($_SERVER['argc'] ?? 0) !== 1) {"
CGI_GUARD = (b"if (PHP_SAPI !== 'cgi-fcgi'\n"
             b"    || ($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET'\n"
             b"    || ($_SERVER['QUERY_STRING'] ?? '') !== 'rp_runtime_cgi=1'\n"
             b"    || ($_SERVER['argc'] ?? 0) !== 0) {")
CASES = ['basic', 'exceptions', 'fiber', 'wordpress_hooks', 'scratch_io', 'readonly_io']
STACK_BYTES = fiber.STACK_BYTES
BASE = ROOT / 'target/php-wasix-cgi-runtime-probe/native-contract'
DESTINATION = HERE / 'evidence/native-cgi-runtime-contract.json'
CGI_EXE_SHA256 = 'f6007e19a06eddaa6c275539f8c8a4b34991bd8b3db061eb965b40e372dfb4a3'
PACKAGE_MAP_SHA256 = '1711770af256947abd7aded6d89b7f32426e0a3a0f80632dcf5dbc8973892d3b'
MANIFEST_SHA256 = '159a065582fef2e837b2a67f02b9b4cd5c3b7397f644b95fd718a3d038696137'


def command_arguments():
    return ['-n', '-d', 'fiber.stack_size=' + str(STACK_BYTES), '-d', 'register_argc_argv=0']


def limitations():
    return [
        'Native Windows CGI PHP 8.5.12 only; no Wasm or patched Zend backend is executed.',
        'Only the fixed request guard differs from the canonical CLI corpus; all six runtime cases are byte-identical.',
        'The fixed query contains an equals sign so Windows CGI processes -n/-d; register_argc_argv=0 avoids deprecated query-to-argv derivation.',
        'WordPress pure hooks use three verified readonly source copies; full bootstrap and database operations are absent.',
        'Windows readonly attributes and the actual denied write are checked; no Unix permission or product capability boundary is established.',
        'Header defaults and the original readonly warning are exact observations of this closed build/config.',
        'Capture size is checked after exit; no native process memory or CPU budget is established.',
        'No exception stack traces, object IDs or absolute source locations are published.',
    ]


def fixture_derivation():
    return {'canonical_path': CANONICAL_FIXTURE.relative_to(ROOT).as_posix(),
            'canonical_bytes': CANONICAL_FIXTURE.stat().st_size, 'canonical_sha256': CANONICAL_SHA256,
            'change': 'fixed CGI request entry guard only', 'all_six_case_bodies_byte_identical': True,
            'verified_before_and_after_execution': True}


def verify_fixture():
    """The request guard is the sole change to the pinned six-case corpus."""
    canonical = CANONICAL_FIXTURE.read_bytes()
    equal(hashlib.sha256(canonical).hexdigest(), CANONICAL_SHA256, 'fixed original CLI runtime corpus')
    require(canonical.count(CANONICAL_GUARD) == 1, 'canonical runtime entry guard count')
    equal(FIXTURE.read_bytes(), canonical.replace(CANONICAL_GUARD, CGI_GUARD, 1),
          'CGI runtime corpus differs outside the sole fixed request guard')


CLI_ORACLE = ROOT / 'experiments/php-runtime-candidate-probe/evidence/native-contract.json'
CLI_ORACLE_SHA256 = '0798461b6ad073fab0f1b16553f09f551d5eab65bd1862a4d50ad8daf8845307'
ORDINARY = native.ORDINARY
READONLY = native.READONLY


def expected_observation():
    equal(digest(CLI_ORACLE), CLI_ORACLE_SHA256, 'fixed native CLI runtime oracle')
    reference = native.parse_json(CLI_ORACLE.read_bytes())['observation']
    native.validate(reference)
    # Only the expectation changes SAPI. Actual CGI values, all six cases and
    # the original permission-denial warning are compared without rewriting.
    reference['runtime']['sapi'] = 'cgi-fcgi'
    return reference


def expected_headers():
    return [{'name': 'x-powered-by', 'value_hex': b'PHP/8.5.12'.hex()},
            {'name': 'content-type', 'value_hex': b'text/html; charset=UTF-8'.hex()}]


def validate_observation(observation):
    equal(observation, expected_observation(), 'fixed native CGI runtime semantics')


def expected_environment(script_filename='/fixture/runtime_cgi_contract.php', wp_root='/wp-core', scratch_root='/scratch'):
    return [
        ['REDIRECT_STATUS', '200'], ['GATEWAY_INTERFACE', 'CGI/1.1'],
        ['SERVER_PROTOCOL', 'HTTP/1.1'], ['SERVER_SOFTWARE', 'recastpress-p0-runtime-cgi'],
        ['SERVER_NAME', 'localhost'], ['SERVER_PORT', '80'], ['HTTP_HOST', 'localhost'],
        ['REMOTE_ADDR', '127.0.0.1'], ['REQUEST_METHOD', 'GET'],
        ['REQUEST_URI', '/__rp_runtime.php?rp_runtime_cgi=1'], ['SCRIPT_NAME', '/__rp_runtime.php'],
        ['SCRIPT_FILENAME', script_filename], ['DOCUMENT_ROOT', '/fixture'],
        ['QUERY_STRING', 'rp_runtime_cgi=1'], ['CONTENT_LENGTH', '0'],
        ['RP_WP_ROOT', wp_root], ['RP_SCRATCH', scratch_root],
    ]


def parse_completed_cgi(wire):
    """Parse only this closed, completed 200 response; never an HTTP gateway."""
    require(type(wire) is bytes and 0 < len(wire) <= 65536, 'native CGI runtime wire budget')
    boundary = wire.find(b'\r\n\r\n')
    require(0 < boundary <= 16380, 'native CGI runtime CRLF header terminator/budget')
    rows, body = wire[:boundary].split(b'\r\n'), wire[boundary + 4:]
    require(len(rows) <= 64, 'native CGI runtime header field budget')
    fields = []
    for row in rows:
        require(row and row[:1] not in (b' ', b'\t') and b':' in row, 'invalid/folded native CGI header')
        name, value = row.split(b':', 1)
        require(re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is not None,
                'invalid native CGI header name')
        value = value.strip(b' \t')
        require(all(x == 9 or 32 <= x != 127 for x in value), 'native CGI header control byte')
        fields.append({'name': name.decode('ascii').lower(), 'value_hex': value.hex()})
    # The guard-adapted fixture emits no explicit Status or Content-Length. This
    # exact header list is an observation of the fixed PHP CGI build/config.
    equal(fields, expected_headers(), 'native CGI runtime fixed response headers')
    response = {'status': 200, 'headers': fields, 'body_bytes': len(body),
                'body_hex': body.hex(), 'body_sha256': hashlib.sha256(body).hexdigest(),
                'representation_length': None}
    return response, body, fields


def source_hashes():
    verify_fixture()
    paths = [Path(__file__).resolve(), CLI_HELPER, CANONICAL_FIXTURE, FIXTURE, CLI_ORACLE, fiber.COMMON,
             ROOT / 'scripts/owned_probe.py', native.WP_MANIFEST]
    for path in paths:
        require(path.resolve(strict=True) == path and path.is_file(), 'linked or missing fixed CGI runtime input')
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in paths}


def validate_receipt(oracle):
    """Read-only Linux-safe receipt gate; never verifies or executes native PHP.

    The historical full-package check is bound by fixed manifest, archive and
    canonical 78-file-map hashes. It is a source-bound observation, not a new
    claim that the native Windows files exist on the validating Linux host.
    """
    require(type(oracle) is dict, 'native CGI runtime receipt object required')
    fixed = {
        'format_version': 1, 'schema': 'recastpress.php-wasix-cgi-runtime-native.v0.5',
        'role': 'native CGI runtime PHP comparison oracle only; never a Wasm or production fallback',
        'platform': {'system': 'Windows', 'machine': 'AMD64'},
        'outcome': 'native_cgi_runtime_contract_passed', 'native_contract_passed': True,
        'owned_host_reaped': True, 'owned_process_cleanup_completed': True,
        'owned_scratch_reclaimed': True, 'stdout_eof': True, 'stderr_eof': True,
        'wasm_executed': False, 'patched_fiber_backend_executed': False,
        'php_wasm_fiber_compatibility_verified': False, 'product_runtime_selected': False,
        'wordpress_executed': True, 'wordpress_pure_hooks_executed': True,
        'full_wordpress_bootstrap_executed': False, 'real_database_executed': False,
        'native_filesystem_contract_verified': True,
        'owned_fixture_cleanup_completed': True, 'owned_wp_copy_cleanup_completed': True,
        'native_cli_substitute_used': False, 'listener_created': False,
        'native_oom_executed': False, 'memory_limit_verified': False,
        'fiber_stack_bytes': STACK_BYTES,
        'fiber_stack_setting_scope': 'explicit experiment setting, not product default',
        'outer_execution_timeout_seconds': 10, 'stdout_limit_bytes': 65536, 'stderr_limit_bytes': 65536,
        'stdin': 'closed empty CGI request body', 'expected_case_names': CASES,
        'exit_code': 0, 'stderr_bytes': 0,
        'stderr_sha256': hashlib.sha256(b'').hexdigest(),
        'sources_unchanged_after_execution': True, 'package_unchanged_after_execution': True,
        'limitations': limitations(), 'fixture_derivation': fixture_derivation(),
        'wordpress_reference': wordpress_reference(), 'filesystem_reference': filesystem_reference(),
        'input_files': source_hashes(),
        'fixture': {'path': FIXTURE.relative_to(ROOT).as_posix(), 'bytes': FIXTURE.stat().st_size,
                    'sha256': digest(FIXTURE), 'modified': False},
        'native_executable': {'path': '.reference/php-8.5.12/php-cgi.exe', 'sha256': CGI_EXE_SHA256},
        'command': ['.reference/php-8.5.12/php-cgi.exe'] + command_arguments(),
    }
    for name, value in fixed.items():
        require(name in oracle, 'native CGI runtime receipt lacks ' + name)
        equal(oracle[name], value, 'native CGI runtime receipt.' + name)
    equal(set(oracle), set(fixed) | {
        'observed_at_utc', 'native_reference', 'environment',
        'owned_process_pid', 'stdout_bytes', 'stdout_sha256', 'observation', 'response',
        'cgi_wire_headers', 'stdout_hex'}, 'native CGI runtime receipt fields')
    require(type(oracle.get('owned_process_pid')) is int and oracle['owned_process_pid'] > 0,
            'native CGI runtime positive owned PID required')
    timestamp = oracle.get('observed_at_utc')
    require(type(timestamp) is str and len(timestamp) <= 128, 'native CGI runtime UTC timestamp')
    require(datetime.fromisoformat(timestamp).utcoffset().total_seconds() == 0, 'native CGI runtime non-UTC timestamp')
    package = oracle.get('native_reference')
    require(type(package) is dict, 'native CGI runtime package reference')
    equal(set(package), {'manifest', 'manifest_sha256', 'version', 'archive_sha256', 'verified_files',
                         'distribution_gate'}, 'native CGI runtime package fields')
    for name, value in {
        'manifest': '.reference/php-8.5.12/recastpress-tool.json', 'manifest_sha256': MANIFEST_SHA256,
        'version': '8.5.12', 'archive_sha256': native.PHP_ARCHIVE_SHA256,
        'distribution_gate': {
            'manifest_files': 78, 'extracted_file_set_verified': True,
            'all_78_extracted_hashes_verified': True, 'archive_bytes': 36306925,
            'archive_sha256_verified': True, 'archive_member_file_set_verified': True,
            'all_78_archive_member_hashes_match_extracted_files': True,
        },
    }.items():
        equal(package.get(name), value, 'native CGI runtime package.' + name)
    files = package['verified_files']
    require(type(files) is dict and len(files) == 78, 'native CGI runtime full package file map')
    canonical = json.dumps(files, sort_keys=True, separators=(',', ':')).encode('utf-8')
    equal(hashlib.sha256(canonical).hexdigest(), PACKAGE_MAP_SHA256, 'native CGI runtime exact 78-file package map')
    equal(files['php-cgi.exe'], CGI_EXE_SHA256, 'native CGI runtime mapped executable')
    keys = sorted({'SystemRoot', 'WINDIR', 'TEMP', 'TMP'} | {key for key, _ in expected_environment()})
    equal(oracle.get('environment'), {
        'provided_keys': keys, 'absolute_values_recorded': False,
        'php_configuration_environment_forwarded': False, 'credentials_forwarded': False,
        'normalized_cgi_rows': expected_environment('<owned-fixture>', '<owned-wp-core>', '<owned-scratch>'),
        'native_path_record_kind': 'three owned verified fixture paths; absolute native values omitted',
    }, 'native CGI runtime fixed environment')
    wire_hex = oracle.get('stdout_hex')
    require(type(wire_hex) is str and 0 < len(wire_hex) <= 131072 and len(wire_hex) % 2 == 0
            and re.fullmatch('[0-9a-f]+', wire_hex) is not None, 'native CGI runtime hex capture')
    wire = bytes.fromhex(wire_hex)
    equal(oracle.get('stdout_bytes'), len(wire), 'native CGI runtime stdout length')
    equal(oracle.get('stdout_sha256'), hashlib.sha256(wire).hexdigest(), 'native CGI runtime stdout hash')
    response, body, fields = parse_completed_cgi(wire)
    equal(oracle.get('response'), response, 'native CGI runtime response/wire identity')
    equal(oracle.get('cgi_wire_headers'), fields, 'native CGI runtime header/wire identity')
    require(body.endswith(b'\n') and body.count(b'\n') == 1, 'native CGI runtime terminated single JSON body')
    observed = native.parse_json(body)
    validate_observation(observed)
    equal(oracle.get('observation'), observed, 'native CGI runtime decoded body identity')
    return {'schema': oracle['schema'], 'native_contract_passed': True,
            'expected_case_names': CASES, 'source_input_count': len(fixed['input_files']),
            'native_execution_performed_by_validator': False}


def owned_path(path):
    require(BASE.resolve() == BASE and BASE.is_relative_to(ROOT / 'target'), 'owned native CGI runtime base escaped target')
    require(path.is_relative_to(BASE) and path.resolve() == path, 'owned native CGI runtime path escaped target')
    return path


def public_message(value):
    return str(value).replace(str(ROOT), '<workspace>').replace(ROOT.as_posix(), '<workspace>')[:8192]


def wordpress_reference():
    equal(digest(native.WP_MANIFEST),
          'f25b298591e266cb2eaa8001807ff89aff44c60d46bade4224f7834ba0385543',
          'fixed WordPress pure-hook source manifest')
    wordpress = native.parse_json(native.WP_MANIFEST.read_bytes())
    equal(wordpress['wordpress_version'], '7.1.3', 'WordPress pure-hook source version')
    equal(set(wordpress['files']), {'plugin.php', 'class-wp-hook.php', 'class-wp-filter-sentinel.php'},
          'fixed three-file pure-hook source set')
    return {'version': '7.1.3', 'manifest_sha256': digest(native.WP_MANIFEST),
            'verified_source_files': wordpress['files'], 'pure_hooks_executed': True,
            'full_bootstrap_executed': False, 'owned_copy_bytes_verified_before_and_after': True,
            'owned_copy_readonly_attributes_verified_before_and_after': True}


def filesystem_reference():
    copies = {name: {'bytes': row['bytes'], 'sha256': row['sha256'],
                     'readonly_attribute_before': True, 'readonly_attribute_after': True}
              for name, row in wordpress_reference()['verified_source_files'].items()}
    return {
        'scope': 'closed native Windows fixture and scratch files only; no product filesystem boundary',
        'ordinary': {'initial_bytes': 0, 'initial_sha256': hashlib.sha256(b'').hexdigest(),
                     'final_bytes': len(ORDINARY), 'final_hex': ORDINARY.hex(),
                     'final_sha256': hashlib.sha256(ORDINARY).hexdigest()},
        'readonly': {'initial_bytes': len(READONLY), 'initial_sha256': hashlib.sha256(READONLY).hexdigest(),
                     'final_bytes': len(READONLY), 'final_hex': READONLY.hex(),
                     'final_sha256': hashlib.sha256(READONLY).hexdigest(),
                     'readonly_attribute_before': True, 'readonly_attribute_after': True,
                     'bytes_unchanged': True},
        'fixture_copy': {'sha256': digest(FIXTURE), 'readonly_attribute_before': True,
                         'readonly_attribute_after': True, 'bytes_unchanged': True},
        'wp_copies': copies, 'exact_owned_entry_set_verified_before_and_after': True,
        'host_final_bytes_cross_checked_against_php_observation': True,
    }


def observe(report):
    require(os.name == 'nt', 'native CGI runtime reference pins Windows x64 PHP only')
    before = source_hashes()
    report['input_files'] = before
    package, wordpress_side_check = fiber.verify_package()
    report['native_reference'] = package
    executable = native.PHP_ROOT / 'php-cgi.exe'
    equal(digest(executable), package['verified_files']['php-cgi.exe'], 'native CGI executable')
    report['native_executable'] = {'path': executable.relative_to(ROOT).as_posix(),
                                   'sha256': package['verified_files']['php-cgi.exe']}
    fixture_bytes = FIXTURE.read_bytes()
    report['fixture'] = {'path': FIXTURE.relative_to(ROOT).as_posix(), 'bytes': len(fixture_bytes),
                         'sha256': hashlib.sha256(fixture_bytes).hexdigest(), 'modified': False}
    wp = wordpress_reference()
    owned_path(BASE).mkdir(parents=True, exist_ok=True)
    directory = BASE / (str(os.getpid()) + '-' + uuid.uuid4().hex)
    owned_path(directory).mkdir(exist_ok=False)
    directories = []
    created = []
    report['owned_scratch_reclaimed'] = False
    fixture_directory = directory / 'fixture'
    wp_directory = directory / 'wp-core'
    scratch_directory = directory / 'scratch'
    fixture = fixture_directory / 'runtime_cgi_contract.php'
    ordinary = scratch_directory / 'ordinary.txt'
    readonly = scratch_directory / 'readonly.txt'

    def create_file(path, data, readonly_file):
        with owned_path(path).open('xb') as destination:
            entry = [path, None]
            created.append(entry)
            destination.write(data)
        entry[1] = stat.S_IMODE(owned_path(path).stat().st_mode)
        if readonly_file:
            os.chmod(owned_path(path), stat.S_IREAD)
            require(path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY,
                    'owned Windows readonly attribute was not applied')
        equal(path.read_bytes(), data, 'owned file initial bytes')

    def verify_entry_set():
        expected_files = {fixture, ordinary, readonly} | {wp_directory / name for name in wp['verified_source_files']}
        expected_directories = {fixture_directory, wp_directory, scratch_directory}
        actual_files, actual_directories = set(), set()
        for path in directory.rglob('*'):
            owned_path(path)
            require(not path.is_symlink(), 'linked entry appeared in native owned directory')
            if path.is_file(): actual_files.add(path)
            elif path.is_dir(): actual_directories.add(path)
            else: raise ValueError('unexpected native owned entry type')
        equal(actual_files, expected_files, 'native owned file set')
        equal(actual_directories, expected_directories, 'native owned directory set')

    try:
        for path in [fixture_directory, wp_directory, scratch_directory]:
            owned_path(path).mkdir(exist_ok=False)
            directories.append(path)
        create_file(fixture, fixture_bytes, True)
        for name, row in wp['verified_source_files'].items():
            source = native.WP_ROOT / name
            data = source.read_bytes()
            equal(len(data), row['bytes'], 'WordPress copy source length')
            equal(hashlib.sha256(data).hexdigest(), row['sha256'], 'WordPress copy source hash')
            create_file(wp_directory / name, data, True)
        create_file(ordinary, b'', False)
        create_file(readonly, READONLY, True)
        verify_entry_set()
        system_root = os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT')
        require(system_root and Path(system_root).is_absolute(), 'Windows SystemRoot is missing')
        scaffold = {'SystemRoot': system_root, 'WINDIR': system_root,
                    'TEMP': str(directory), 'TMP': str(directory)}
        environment = scaffold | dict(expected_environment(fixture.as_posix(), wp_directory.as_posix(),
                                                             scratch_directory.as_posix()))
        report['environment'] = {
            'provided_keys': sorted(environment), 'absolute_values_recorded': False,
            'php_configuration_environment_forwarded': False, 'credentials_forwarded': False,
            'normalized_cgi_rows': expected_environment('<owned-fixture>', '<owned-wp-core>', '<owned-scratch>'),
            'native_path_record_kind': 'three owned verified fixture paths; absolute native values omitted',
        }
        arguments = command_arguments()
        report['command'] = [executable.relative_to(ROOT).as_posix()] + arguments
        pid, result = native.owned_probe.run_captured_probe([str(executable)] + arguments,
                                                           environment, timeout=10, input_bytes=b'')
        report.update(owned_process_pid=pid, exit_code=result.returncode, owned_host_reaped=True,
                      stdout_eof=True, stderr_eof=True)
        for name, wire in [('stdout', result.stdout), ('stderr', result.stderr)]:
            report[name + '_bytes'], report[name + '_sha256'] = len(wire), hashlib.sha256(wire).hexdigest()
        require(0 < len(result.stdout) <= 65536 and len(result.stderr) <= 65536, 'native CGI runtime capture budget')
        require(result.returncode == 0 and result.stderr == b'', 'native CGI runtime exit or stderr failure')
        response, body, fields = parse_completed_cgi(result.stdout)
        require(body.endswith(b'\n') and body.count(b'\n') == 1, 'one terminated native CGI runtime JSON required')
        observed = native.parse_json(body)
        validate_observation(observed)
        require(str(ROOT) not in json.dumps(observed) and ROOT.as_posix() not in json.dumps(observed),
                'absolute native source path in CGI runtime observation')
        report['observation'], report['response'], report['cgi_wire_headers'] = observed, response, fields
        report['stdout_hex'] = result.stdout.hex()
        equal(ordinary.read_bytes(), ORDINARY, 'native ordinary file final bytes')
        equal(readonly.read_bytes(), READONLY, 'native readonly file final bytes')
        equal(digest(fixture), report['fixture']['sha256'], 'owned runtime fixture unchanged')
        for path in [fixture, readonly] + [wp_directory / name for name in wp['verified_source_files']]:
            require(path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY,
                    'owned Windows readonly attribute changed during execution')
        for name, row in wp['verified_source_files'].items():
            equal(digest(wp_directory / name), row['sha256'], 'owned WordPress file unchanged')
            equal((wp_directory / name).stat().st_size, row['bytes'], 'owned WordPress file length unchanged')
        verify_entry_set()
        equal(source_hashes(), before, 'CGI runtime sources before/after execution')
        equal(fiber.verify_package(), (package, wordpress_side_check), 'native CGI runtime package before/after execution')
        report['sources_unchanged_after_execution'] = report['package_unchanged_after_execution'] = True
        report['fixture_derivation'] = fixture_derivation()
        report['wordpress_reference'], report['filesystem_reference'] = wp, filesystem_reference()
        report['wordpress_executed'] = report['wordpress_pure_hooks_executed'] = True
        report['native_filesystem_contract_verified'] = True
    finally:
        # Reap before restoring modes/removing only the files created above.
        # Unexpected entries make rmdir fail and are retained; never recurse.
        native.owned_probe.reap_owned_probes()
        for path, mode in reversed(created):
            if path.exists():
                if mode is not None:
                    os.chmod(owned_path(path), mode)
                owned_path(path).unlink()
        for path in reversed(directories):
            owned_path(path).rmdir()
        owned_path(directory).rmdir()
        report['owned_scratch_reclaimed'] = True
        report['owned_fixture_cleanup_completed'] = report['owned_wp_copy_cleanup_completed'] = True


def main():
    require(sys.argv[1:] in ([], ['--record']), 'usage: python native_contract.py [--record]')
    report = {
        'format_version': 1, 'schema': 'recastpress.php-wasix-cgi-runtime-native.v0.5',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'role': 'native CGI runtime PHP comparison oracle only; never a Wasm or production fallback',
        'platform': {'system': platform.system(), 'machine': platform.machine()},
        'outcome': 'not_run', 'native_contract_passed': False, 'owned_host_reaped': False,
        'owned_process_cleanup_completed': False, 'wasm_executed': False,
        'patched_fiber_backend_executed': False, 'php_wasm_fiber_compatibility_verified': False,
        'product_runtime_selected': False, 'wordpress_executed': False,
        'wordpress_pure_hooks_executed': False, 'full_wordpress_bootstrap_executed': False,
        'real_database_executed': False, 'native_filesystem_contract_verified': False,
        'owned_fixture_cleanup_completed': False, 'owned_wp_copy_cleanup_completed': False,
        'native_cli_substitute_used': False, 'listener_created': False,
        'native_oom_executed': False, 'memory_limit_verified': False,
        'fiber_stack_bytes': STACK_BYTES,
        'fiber_stack_setting_scope': 'explicit experiment setting, not product default',
        'outer_execution_timeout_seconds': 10, 'stdout_limit_bytes': 65536, 'stderr_limit_bytes': 65536,
        'stdin': 'closed empty CGI request body', 'expected_case_names': CASES,
        'limitations': limitations(),
    }
    passed = False
    try:
        observe(report)
        report['outcome'], report['native_contract_passed'], passed = 'native_cgi_runtime_contract_passed', True, True
    except subprocess.TimeoutExpired:
        report['outcome'], report['diagnostics'] = 'native_execution_timeout', 'owned native CGI exceeded ten seconds'
    except (Exception, KeyboardInterrupt) as failure:
        report['outcome'], report['diagnostics'] = 'native_cgi_runtime_contract_failed', public_message(failure)
    finally:
        try:
            native.owned_probe.reap_owned_probes()
            report['owned_process_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt) as failure:
            report['outcome'], report['diagnostics'] = 'cleanup_failed', public_message(failure)
            report['native_contract_passed'], passed = False, False
    if passed:
        try:
            validate_receipt(report)
        except (Exception, KeyboardInterrupt) as failure:
            report['outcome'], report['diagnostics'] = 'native_receipt_validation_failed', public_message(failure)
            report['native_contract_passed'], passed = False, False
    if sys.argv[1:] == ['--record']:
        require(DESTINATION.resolve().is_relative_to(HERE) and not DESTINATION.is_symlink(), 'native CGI receipt escaped experiment')
        DESTINATION.parent.mkdir(exist_ok=True)
        DESTINATION.write_text(json.dumps(report, indent=2, ensure_ascii=True) + '\n', encoding='utf-8', newline='\n')
    print('Native PHP CGI runtime reference: ' + report['outcome'])
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
