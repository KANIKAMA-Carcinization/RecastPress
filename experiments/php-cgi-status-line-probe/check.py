"""Native-only prior-status-line setter comparison; no WP, DB, Wasm or network."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/php-cgi-status-line-probe'
HELPER = ROOT / 'experiments/php-cgi-framing-probe/check.py'
HELPER_SHA = '14145b7ede78a03728bb290fc8eb9188923bb585a37f174b59dc31918c709d69'
PROOF = ROOT / 'experiments/php-cgi-framing-probe/evidence/windows-native.json'
PROOF_SHA = 'cb14bab903650d7ab41dac3f65055851cc19c8a22d8ca096e8c83d0bc4b17ab9'
CASES = [('prior200-old', 200, 'old'), ('prior200-third-argument', 200, 'third_argument'),
    ('prior404-third-argument', 404, 'third_argument')]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


framing = load('rp_status_line_frozen_framing', HELPER)
startup = framing.startup
require, sha, identity, read = startup.require, startup.sha, startup.identity, startup.read
require(sha(read(HELPER)) == HELPER_SHA and sha(read(PROOF)) == PROOF_SHA, 'accepted_helpers_changed')


def sources():
    paths = [Path(__file__).resolve(), HERE / 'fixtures/common.php', HELPER, PROOF,
        HELPER.parent / 'prepare.py', framing.STARTUP]
    paths += [HERE / 'fixtures' / (case + '.php') for case, _, _ in CASES]
    require({p.name for p in (HERE / 'fixtures').iterdir()} == {'common.php', *[c + '.php' for c, _, _ in CASES]},
        'fixed_fixture_inventory')
    return {p.relative_to(ROOT).as_posix(): identity(read(p)) for p in paths}


def expected(case, prior, setter):
    value = {'schema': 'recastpress.php-cgi-status-line-observation.v0.5', 'passed': True,
        'case': case, 'prior_status': prior, 'setter': setter, 'prior_status_observed': prior,
        'target_status': 200, 'status_after_setter': 200}
    body = (json.dumps(value, separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    headers = [b'Content-Type: application/json']
    return value, headers, body, b'\r\n'.join(headers) + b'\r\n\r\n' + body


def owned(path):
    require(WORK.resolve() == WORK and path.resolve() == path and path.is_relative_to(WORK)
        and WORK.is_relative_to(ROOT / 'target'), 'owned_target_path'); return path


def warning_counts(wire):
    return {name: wire.count(message) for name, message in [
        ('http_response_code_after_status_line', b'Calling http_response_code() after header('),
        ('cannot_modify_headers', b'Cannot modify header information - headers already sent'),
        ('cannot_set_response_code', b'Cannot set response code - headers already sent')]}


def run(record=False):
    report = {'schema': 'recastpress.php-cgi-status-line-check.v0.5', 'passed': False,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': sys.platform, 'observations': [],
        'native_only': True, 'wordpress_executed': False, 'database_connected': False, 'wasm_executed': False,
        'network_calls_made': False, 'product_configuration_changed': False, 'product_runtime_selected': False,
        'old_setter_warning_required': False, 'unknown_output_recorded': False,
        'owned_cleanup_completed': False, 'owned_scratch_reclaimed': False,
        'fixed_wasix_setter_compatibility_verified': False}
    directory = None; passed = False; reaped_before = len(framing.REAPED_CHILDREN)
    names = ['common.php'] + [case + '.php' for case, _, _ in CASES]
    try:
        require(os.name == 'nt', 'native_windows_only')
        require(not framing.OWNED_CHILDREN, 'previous_child_not_reaped')
        require(not any(key.upper().startswith('PHP') or key.upper() == 'QUERY_STRING' for key in os.environ),
            'ambient_php_or_query_environment')
        report['input_files'] = sources(); report['package_before'] = startup.package_gate()
        native_proof = startup.parse(read(PROOF))
        require(native_proof['passed'] is True and native_proof['source_reverified_after'] is True
            and native_proof['input_files'] == framing.prepared.sources(), 'accepted_native_framing_source')
        report['accepted_framing_proof'] = {'path': PROOF.relative_to(ROOT).as_posix(), **identity(read(PROOF))}
        capture, report['capture_derivation'] = framing.capture_derivation()
        owned(WORK).mkdir(parents=True, exist_ok=True)
        directory = owned(WORK / ('run-' + uuid.uuid4().hex)); directory.mkdir(exist_ok=False)
        ini = directory / 'php.ini'; ini.write_bytes(startup.config())
        report['config_before'] = identity(read(ini))
        report['config_semantics'] = {'selection': 'explicit PHPRC owned INI file', 'mysqli': True,
            'mail_function_disabled': True, 'allow_url_fopen': '0', 'register_argc_argv': '0',
            'expose_php_overridden': False, 'additional_scan_directory': 'empty', 'private_ini_bytes_recorded': False}
        for name in names: (directory / name).write_bytes(read(HERE / 'fixtures' / name))
        new_setters = []
        for case, prior, setter in CASES:
            env = framing.environment(directory, ini, case + '.php')
            process, stdout, stderr = capture([str(startup.PHP / 'php-cgi.exe')], env)
            value, headers, body, wire = expected(case, prior, setter)
            exact = stdout == wire
            row = {'case': case, 'prior_status': prior, 'setter': setter, 'process': process,
                'stdout': identity(stdout), 'stderr': identity(stderr), 'environment_keys': sorted(env),
                'environment_absolute_values_recorded': False, 'command_has_cli_options': False,
                'complete_cgi_wire_matches': exact, 'expected_wire': identity(wire),
                'fixed_warning_counts_stdout': warning_counts(stdout), 'fixed_warning_counts_stderr': warning_counts(stderr),
                'unknown_output_recorded': False}
            report['observations'].append(row)
            require(process['exit_code'] == 0 and process['owned_reaped'] is True
                and process['stdout_eof'] is True and process['stderr_eof'] is True, 'native_exit_or_cleanup')
            if exact and not stderr:
                row['response'] = {'status': 200, 'header_lines_hex': [h.hex() for h in headers], 'body': identity(body)}
                row['observation'] = value
            if setter == 'third_argument':
                new_setters.append(exact and not stderr)
        require(len(new_setters) == 2 and all(new_setters), 'both_new_setter_cgi_frames_exact')
        report['new_setters_both_exact'] = True
        report['old_setter_observed_without_forcing_warning'] = True
        report['all_three_complete_cgi_wire_matches'] = all(r['complete_cgi_wire_matches'] and r['stderr'] == identity(b'')
            for r in report['observations'])
        for name in names: require(read(directory / name) == read(HERE / 'fixtures' / name), 'owned_php_fixture_changed')
        report['config_after'] = identity(read(ini)); require(report['config_before'] == report['config_after'], 'owned_ini_changed')
        report['package_after'] = startup.package_gate(); require(report['package_before'] == report['package_after'], 'package_changed')
        require(report['input_files'] == sources(), 'source_changed'); report['source_reverified_after'] = True
        passed = True
    except (Exception, KeyboardInterrupt) as error:
        report['failure_class'] = type(error).__name__
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)): report['failure'] = str(error)
    finally:
        for pid, child in list(framing.OWNED_CHILDREN.items()):
            try:
                if child.poll() is None: child.kill()
                child.wait(timeout=5)
                for stream in [child.stdin, child.stdout, child.stderr]: stream.close()
                framing.REAPED_CHILDREN.append(pid); framing.OWNED_CHILDREN.pop(pid)
            except (Exception, KeyboardInterrupt): passed = False; report['child_cleanup_failed'] = True
        if directory is not None and not framing.OWNED_CHILDREN:
            try:
                for name in names + ['php.ini']:
                    path = owned(directory / name)
                    if path.exists(): path.unlink()
                owned(directory).rmdir(); report['owned_scratch_reclaimed'] = True
            except (Exception, KeyboardInterrupt): passed = False; report['scratch_cleanup_failed'] = True
        report['owned_child_count'] = len(framing.REAPED_CHILDREN) - reaped_before + len(framing.OWNED_CHILDREN)
        report['owned_reaped_count'] = len(framing.REAPED_CHILDREN) - reaped_before
        report['owned_cleanup_completed'] = not framing.OWNED_CHILDREN and 'child_cleanup_failed' not in report
        passed = passed and report['owned_cleanup_completed'] and report['owned_scratch_reclaimed']
    report.update(passed=passed, outcome='native_status_line_new_setters_exact_old_observed' if passed else 'native_status_line_failed')
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    require(len(wire) <= 256*1024 and str(ROOT).encode() not in wire and ROOT.as_posix().encode() not in wire,
        'public_report_quota_or_private_path')
    if record:
        destination = HERE / 'evidence/windows-native.json'; destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            previous = read(destination, 256*1024); backup = destination.with_name('windows-native-before-' + sha(previous)[:16] + '.json')
            if backup.exists(): require(read(backup) == previous, 'immutable_previous_native_report')
            else:
                with backup.open('xb') as stream: stream.write(previous)
        destination.write_bytes(wire)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--record', action='store_true'); args = parser.parse_args()
    value = run(args.record); print(value['outcome']); sys.exit(0 if value['passed'] else 1)
