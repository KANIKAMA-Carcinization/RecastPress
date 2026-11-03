"""Fresh owned native WP requests with private file context and empty CGI body."""
import argparse
import ast
import copy
from datetime import datetime, timezone
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


def load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


prepared = load('rp_clean_request_sources', HERE / 'prepare.py')
require, sha, identity, canonical = prepared.require, prepared.sha, prepared.identity, prepared.canonical
prepared.parents()
home = load('rp_clean_request_frozen_home', ROOT / 'experiments/wp-request-probe/database_check.py')
startup = load('rp_clean_request_frozen_startup', ROOT / 'experiments/php-cgi-startup-probe/check.py')
OWNED_CHILDREN = {}
REAPED_CHILDREN = []


def exact(actual, expected, code): require(canonical(actual) == canonical(expected), code)


def source_function(path, name):
    source = prepared.read(path).decode('utf-8'); tree = ast.parse(source)
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    require(len(matches) == 1, 'one_frozen_function'); return ast.get_source_segment(source, matches[0])


def capture_function():
    source = source_function(ROOT / 'experiments/php-cgi-startup-probe/check.py', 'captured')
    changes = [('deadline = time.monotonic() + 10;', 'deadline = time.monotonic() + 180;'),
        ("'deadline_seconds': 10", "'deadline_seconds': 180")]
    derived = source
    for before, after in changes:
        require(derived.count(before) == 1, 'closed_capture_derivation'); derived = derived.replace(before, after)
    namespace = {'subprocess': subprocess, 'os': os, 'time': time, 'require': require, 'STDIN': b'',
        'LIMIT': 3*1024*1024, 'OWNED_CHILDREN': OWNED_CHILDREN, 'REAPED_CHILDREN': REAPED_CHILDREN}
    exec(compile(derived, '<clean-native-context-capture>', 'exec'), namespace)
    return namespace['captured'], {'original_function_sha256': sha(source.encode()), 'derived_function_sha256': sha(derived.encode()),
        'deadline_seconds': 180, 'live_capture_limit_each': 3*1024*1024, 'stdin_bytes': 0,
        'isolated_child_registry': True, 'original_module_globals_modified': False}


def response_validator():
    source = source_function(ROOT / 'experiments/wp-request-probe/database_check.py', 'validate_home')
    changes = [('def validate_home(value, mapping):', 'def validate_response(value, mapping, case):'),
        ('recastpress.wp-request-native-observation.v0.5', 'recastpress.wp-request-context-native-observation.v0.5'),
        ('fixed_native_home_passed', 'fixed_native_context_passed'),
        ("response['status'] == 200", "response['status'] == (200 if case == 'home' else 404)"),
        ('full_native_cgi_home_observed', 'full_native_cgi_request_observed')]
    derived = source
    for before, after in changes:
        require(derived.count(before) == 1, 'closed_response_validator_derivation'); derived = derived.replace(before, after)
    namespace = {'db': home.db, 'exact': exact, 'BODY_LIMIT': 1024*1024, 're': re}
    exec(compile(derived, '<clean-native-response-validator>', 'exec'), namespace)
    return namespace['validate_response'], {'original_function_sha256': sha(source.encode()),
        'derived_function_sha256': sha(derived.encode()), 'changes': len(changes), 'actual_response_modified': False}


def owned(path):
    work = prepared.WORK
    require(work.resolve() == work and path.resolve() == path and path.is_relative_to(work), 'owned_context_path'); return path


def environment(directory, case):
    system_root = os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT')
    require(system_root and Path(system_root).is_absolute(), 'native_system_root')
    return {'SystemRoot': system_root, 'WINDIR': system_root, 'TEMP': str(directory), 'TMP': str(directory),
        'PHPRC': str(directory / 'php.ini'), 'PHP_INI_SCAN_DIR': '', 'REDIRECT_STATUS': '200',
        'GATEWAY_INTERFACE': 'CGI/1.1', 'SERVER_PROTOCOL': 'HTTP/1.1', 'SERVER_SOFTWARE': 'rp-context-probe',
        'SERVER_NAME': 'rp-fixture.invalid', 'HTTP_HOST': 'rp-fixture.invalid', 'SERVER_PORT': '80',
        'REMOTE_ADDR': '127.0.0.1', 'REQUEST_METHOD': 'GET', 'SCRIPT_NAME': '/__rp_context.php',
        'SCRIPT_FILENAME': str(directory / (case + '.php')), 'DOCUMENT_ROOT': str(directory),
        'REQUEST_URI': '/__rp_context.php?rp_request=1', 'QUERY_STRING': 'rp_request=1',
        'CONTENT_LENGTH': '0', 'HTTPS': 'off', 'REQUEST_SCHEME': 'http'}


def expected_snapshot(case, stage):
    route = prepared.CASES[case]; available = stage != 'before_bootstrap'
    return {'stage': stage, 'method': 'GET', 'uri': route['uri'], 'query': route['query'], 'get': route['get'],
        'post': [], 'cookie': [], 'request': route['get'], 'content_type_present': False, 'content_type': None,
        'content_length_present': False, 'content_length': None, 'http_accept_present': False, 'http_accept': None,
        'auth_or_cookie_header_present': False, 'private_path_environment_present': False,
        'script_filename_matches_public_entry': True, 'wp_is_json_request_available': available,
        'wp_is_json_request': False if available else None, 'display_errors': '1',
        'input': {'bytes': 0, 'sha256': sha(b'')}}


def context_case(case, owner, report, mapping, capture, validate_response, label):
    require(case in prepared.CASES and label in ['home', 'missing', 'missing_repeat'], 'finite_context_case')
    directory = owned(prepared.WORK / ('cgi-' + uuid.uuid4().hex)); directory.mkdir(exist_ok=False)
    names = [case + '.php', 'bootstrap.php', 'context.php', 'context.json', 'php.ini']
    row = {'case': case, 'context_file_used': True, 'stdin_bytes': 0, 'cli_options_used': False,
        'unknown_capture_bytes_published': False, 'owned_scratch_reclaimed': False}
    report.setdefault('requests', {})[label] = row
    try:
        envelope = {'schema': 'recastpress.wp-request-context-private.v0.5', 'case': case,
            'repository_root': str(ROOT), 'request': owner.request('home')}
        private = canonical(envelope)
        require(0 < len(private) <= 32768, 'bounded_private_context')
        for name in [case + '.php', 'bootstrap.php', 'context.php']:
            (directory / name).write_bytes(prepared.read(HERE / 'fixtures' / name))
        (directory / 'context.json').write_bytes(private)
        config = startup.config() + b'display_errors=1\nlog_errors=0\n'
        (directory / 'php.ini').write_bytes(config)
        for name in names: (directory / name).chmod(0o444)
        before = {name: identity(prepared.read(directory / name)) for name in names}
        row['owned_files_before'] = before
        env = environment(directory, case)
        require('CONTENT_TYPE' not in env and 'HTTP_ACCEPT' not in env and env['CONTENT_LENGTH'] == '0', 'empty_cgi_transport')
        row['environment_keys'] = sorted(env); row['absolute_environment_values_recorded'] = False
        process, stdout, stderr = capture([str(startup.PHP / 'php-cgi.exe')], env)
        row.update(process=process, stdout=identity(stdout), stderr=identity(stderr))
        require(process['exit_code'] == 0 and not stderr, 'clean_context_php_exit')
        value, outer = home.parse_cgi(stdout)
        require(outer['status'] == 200 and len(outer['headers']) == 1
            and bytes.fromhex(outer['headers'][0]['line_hex']) == b'Content-Type: application/json', 'owned_json_wrapper_headers')
        require(value['case'] == case, 'actual_context_case')
        row['validation'] = validate_response(value, mapping, case)
        require(value['is_home'] is (case == 'home') and value['is_404'] is (case == 'missing'), 'actual_original_wp_route')
        exact(value['transport_body'], {'bytes': 0, 'sha256': sha(b'')}, 'no_private_http_input')
        for stage in ['before_bootstrap', 'after_bootstrap', 'after_dispatch']:
            exact(value['context_' + stage], expected_snapshot(case, stage), 'exact_public_request_snapshot')
        exact(value['private_context_before'], before['context.json'], 'php_private_file_before_identity')
        exact(value['private_context_after'], before['context.json'], 'php_private_file_after_identity')
        exact(value['ini_profile'], {'owned_file_loaded': True, 'additional_ini_scanned': False,
            'display_errors': '1', 'log_errors': '0', 'register_argc_argv': '0', 'allow_url_fopen': '0',
            'mail_function_present': False, 'mysqli_loaded': True, 'expose_php': '1'}, 'exact_sealed_ini')
        headers = [bytes.fromhex(line) for line in value['response']['headers_hex']]
        require(not any(line.lower().startswith(b'location:') for line in headers), 'unexpected_original_redirect')
        # Publish only after complete source/context/response validation.
        row['observation'] = value; row['outer_response'] = outer
        after = {name: identity(prepared.read(directory / name)) for name in names}
        exact(after, before, 'copied_owned_files_changed'); row['owned_files_after'] = after
        return value
    finally:
        if not OWNED_CHILDREN:
            for name in names:
                path = owned(directory / name)
                if path.exists(): path.chmod(0o600); path.unlink()
            owned(directory).rmdir(); row['owned_scratch_reclaimed'] = True


def source_snapshot(old_prepare):
    return {'new_sources': prepared.inputs(), 'old': home.source_snapshot(old_prepare), 'php_package': startup.package_gate()}


def independent_phase(owner, label, report):
    wire = owner._sql('independent_missing_post_' + label,
        b'SELECT COUNT(*) FROM rp_wpdb_probe.rp_posts WHERE ID=2147483647;\n')
    require(wire.strip() == b'0', 'fixed_missing_post_exists')
    owner.verify_final()
    stats = owner.autoload_summary('after_home')
    report.setdefault('independent_request_checks', {})[label] = {'missing_post_count': 0,
        'fixed_public_dates_verified': True, 'original_public_state_verified': True, 'autoload': copy.deepcopy(stats)}
    return stats


def run(backend, record=False):
    report = {'schema': 'recastpress.wp-request-context-native-check.v0.5', 'passed': False,
        'outcome': 'not_run', 'backend': backend, 'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'native_only': True, 'wasm_executed': False, 'rust_db_executed': False, 'http_listener_exercised': False,
        'product_runtime_selected': False, 'normalization_applied': False, 'observations': {}}
    owner = None; before = None; passed = False; stage = 'source_preflight'; old_prepare = None
    reaped_before = len(REAPED_CHILDREN)
    try:
        require(os.name == 'nt' and backend in ('mysql', 'mariadb'), 'fixed_native_context_backend')
        report['preparation'] = prepared.prepare()
        old_prepare = home.new_preparer(); before = source_snapshot(old_prepare)
        report['source_before'] = before
        validate_response, report['response_validation_derivation'] = response_validator()
        capture, report['capture_derivation'] = capture_function()
        fixed = home.baseline(backend)
        owner = home.owner.OwnedDatabase(backend, report)
        stage = 'owned_database'
        with owner:
            for mode in ['install', 'read']:
                stage = 'original_native_' + mode
                result = owner.harness.run(stage, home.original_prepare.native_command(mode), timeout=180,
                    input_bytes=canonical(owner.request(mode)) + b'\n', limit=512*1024)
                require(result.returncode == 0 and not result.stderr and result.stdout.endswith(b'\n')
                    and result.stdout.count(b'\n') == 1, 'original_native_phase_exit')
                value = home.db.parse_json(result.stdout); home.original.validate(value, mode)
                for key in home.BASELINE_FIELDS: exact(value[key], fixed['observations'][mode][key], 'original_native_baseline')
                report['observations'][mode] = value
            report['original_install_read_baseline_reproduced'] = True
            owner.capture_private_password_guard(); owner.seed_public_dates()
            independent_phase(owner, 'before_requests', report)
            for case, label in [('home', 'home'), ('missing', 'missing'), ('missing', 'missing_repeat')]:
                stage = 'native_context_' + label
                value = context_case(case, owner, report, before['old']['prepared'], capture, validate_response, label)
                for key in ['public_options', 'public_posts', 'known_wp_table_count']:
                    exact(value[key], report['observations']['read'][key], 'original_public_state_after_request')
                stats = independent_phase(owner, label, report)
                require(stats['option_count'] == value['autoload_option_count']
                    and stats['maximum_value_bytes'] == value['autoload_max_value_bytes'], 'independent_autoload_after_request')
                exact(source_snapshot(old_prepare), before, 'sources_after_clean_request')
                if label != 'missing_repeat': report['observations'][case] = value
            exact(report['observations']['missing']['response'], report['requests']['missing_repeat']['observation']['response'], 'fresh_repeat_404_response_bytes')
            report['fresh_repeat_404_response_exact'] = True
        require(report['normal_admin_shutdown_verified'] is True and report['database_closed_and_reaped'] is True
            and report['all_command_children_reaped'] is True, 'normal_owned_database_cleanup')
        passed = True
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)): report['failure'] = str(error)
    finally:
        for pid, child in list(OWNED_CHILDREN.items()):
            try:
                if child.poll() is None: child.kill()
                child.wait(timeout=5)
                for stream in [child.stdin, child.stdout, child.stderr]: stream.close()
                REAPED_CHILDREN.append(pid); OWNED_CHILDREN.pop(pid)
            except (Exception, KeyboardInterrupt): passed = False; report['cgi_cleanup_failed'] = True
        report['owned_cgi_count'] = len(REAPED_CHILDREN) - reaped_before + len(OWNED_CHILDREN)
        report['owned_cgi_reaped'] = len(REAPED_CHILDREN) - reaped_before
        report['all_owned_cgi_reaped'] = not OWNED_CHILDREN and 'cgi_cleanup_failed' not in report
        if owner is not None:
            try: owner.close(); report['owned_database_cleanup_completed'] = owner._fully_closed
            except (Exception, KeyboardInterrupt): passed = False; report['database_cleanup_failed'] = True
        if before is not None and old_prepare is not None:
            try:
                report['source_after'] = source_snapshot(old_prepare); exact(report['source_after'], before, 'sources_after_cleanup')
                report['source_reverified_after'] = True
            except (Exception, KeyboardInterrupt): passed = False; report['source_after_failed'] = True
    passed = (passed and report['all_owned_cgi_reaped'] and report.get('owned_database_cleanup_completed') is True
        and report['owned_cgi_count'] == report['owned_cgi_reaped'] == 3
        and set(report.get('requests', {})) == {'home', 'missing', 'missing_repeat'}
        and all(row['owned_scratch_reclaimed'] is True for row in report['requests'].values()))
    report.update(passed=passed, outcome='clean_native_home_and_repeat_404_passed' if passed else 'clean_native_context_failed')
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    require(len(wire) <= 8*1024*1024, 'context_public_receipt_quota')
    for private in ([] if owner is None else owner.secrets()) + [str(ROOT), ROOT.as_posix()]:
        require(private.encode() not in wire and private.encode().hex().encode() not in wire, 'context_public_secret_or_path')
    if record: home.preserve(HERE / ('evidence/windows-' + backend + '-native.json'), wire)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--record', action='store_true'); args = parser.parse_args()
    result = run(args.backend, args.record); print(result['outcome']); sys.exit(0 if result['passed'] else 1)
