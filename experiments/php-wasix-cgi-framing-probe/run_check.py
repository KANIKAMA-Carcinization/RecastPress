"""Owned Linux-only execution of the eight fresh WASIX CGI framing cases."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/php-wasix-cgi-framing-probe'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


build = load('rp_wasix_framing_runtime_build', HERE / 'build_check.py')
require, sha, identity = build.require, build.sha, build.identity


def validate(value, prepared):
    require(value['schema'] == 'recastpress.php-wasix-cgi-framing-check.v0.5'
        and value['source_inputs'] == prepared['source_inputs']
        and value['native_proof'] == prepared['native_proof']
        and value['module_before'] == value['module_after'] == value['module']
        and value['module_receipt_before'] == value['module_receipt_after'] == value['build_receipt']
        and value['module_compilations'] == 1 and value['source_reverified_after'] is True
        and value['source_and_module_reverified_after_each'] is True, 'runtime_source_binding')
    rows = value['observations']; require(len(rows) == 8, 'eight_actual_cgi_cases')
    for actual, expected in zip(rows, prepared['cases']):
        require(actual['case'] == expected['case'] and actual['fresh_runtime'] is True
            and actual['stdin_bytes'] == 0 and actual['stdout_eof'] is True and actual['stderr_eof'] is True
            and actual['runtime_shutdown_returned'] is True and actual['access_channel_finished'] is True
            and actual['stdout_overflow'] is False and actual['stderr_overflow'] is False
            and actual['stderr'] == identity(b'') and actual['guest_exit_success'] is True, 'cgi_execution_cleanup')
        observation = actual['framing']
        require(observation['unknown_output_recorded'] is False, 'unknown_output_disclosure')
        if value['passed']:
            require(actual['passed'] is True and observation['complete_cgi_wire_matches'] is True
                and observation['actual'] == expected['expected_cgi_wire'], 'eight_exact_native_frames')
    if value['passed']:
        require(value['outcome'] == 'wasix_all_eight_cgi_frames_exact', 'passed_outcome')
    else: require(value['outcome'] == 'wasix_cgi_framing_failed', 'failed_outcome')


def run(record=False):
    report = {'schema': 'recastpress.php-wasix-cgi-framing-owned-check.v0.5', 'passed': False,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': sys.platform,
        'wordpress_executed': False, 'database_connected': False, 'native_php_executed': False,
        'native_php_fallback': False, 'product_runtime_selected': False, 'owned_cleanup_completed': False}
    passed = False
    try:
        require(sys.platform == 'linux', 'actual_php_wasm_linux_only')
        report['build_before'] = build.verify_build(); prepared = build.prepared.prepare()
        report['input_files'] = build.inputs()
        binary = ROOT / report['build_before']['executable']
        pid, child = build.home.owned_probe.run_captured_probe([str(binary)], build.environment(), timeout=300)
        WORK.mkdir(parents=True, exist_ok=True)
        (WORK / 'host.stdout').write_bytes(child.stdout); (WORK / 'host.stderr').write_bytes(child.stderr)
        report['owned_process'] = {'owned_pid': pid, 'exit_code': child.returncode, 'owned_reaped': True,
            'deadline_seconds': 300, 'stdout': identity(child.stdout), 'stderr': identity(child.stderr)}
        require(child.returncode in [0, 1] and len(child.stdout) <= 1024*1024 and not child.stderr
            and child.stdout.endswith(b'\n') and child.stdout.count(b'\n') == 1, 'host_complete_json')
        value = json.loads(child.stdout)
        report['observation'] = value
        report['normal_case_cleanup_completed'] = len(value.get('observations', [])) == 8 and all(
            row.get('runtime_shutdown_returned') is True and row.get('stdout_eof') is True
            and row.get('stderr_eof') is True and row.get('access_channel_finished') is True
            for row in value.get('observations', []))
        validate(value, prepared)
        require((child.returncode == 0) is value['passed'], 'host_exit_verdict')
        passed = value['passed']
    except (Exception, KeyboardInterrupt) as error:
        report['failure_class'] = type(error).__name__
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)): report['failure'] = str(error)
    finally:
        try:
            build.home.owned_probe.reap_owned_probes(); report['owned_cleanup_completed'] = True
            if 'input_files' in report:
                require(build.inputs() == report['input_files'], 'runtime_sources_changed')
                report['build_after'] = build.verify_build()
                require(report['build_before'] == report['build_after'], 'host_build_changed')
                report['source_reverified_after'] = True
        except (Exception, KeyboardInterrupt) as error:
            passed = False; report['after_failure_class'] = type(error).__name__
    passed = passed and report['owned_cleanup_completed']
    report.update(passed=passed, outcome='owned_wasix_all_eight_cgi_frames_exact' if passed else 'owned_wasix_cgi_framing_failed')
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    require(len(wire) <= 3*1024*1024, 'public_record_quota')
    if record: build.preserve(HERE / 'evidence/linux-check.json', wire)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--record', action='store_true'); args = parser.parse_args()
    value = run(args.record); print(value['outcome']); sys.exit(0 if value['passed'] else 1)
