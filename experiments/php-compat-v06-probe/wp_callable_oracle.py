#!/usr/bin/env python3
"""Measure the shared callable program with pinned native WordPress hooks."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import run_check as native
import wp_hooks_oracle as hooks
from callable_v2_contract import validate_observation, validate_action_observation
from hook_contract import _require_exact, _unique_frame_object

ROOT, HERE = hooks.ROOT, hooks.HERE
RECEIPT = HERE / 'evidence/wp-callable-api-v1.json'
FIXTURE = HERE / 'fixtures/callable_wp_oracle.php'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def fixed_inputs():
    inputs = hooks.fixed_inputs()
    for path in (Path(__file__), FIXTURE, HERE / 'fixtures/callable_program.php', HERE / 'run_check.py',
                 HERE / 'callable_contract.py', HERE / 'callable_v2_contract.py',
                 HERE / 'wp_action_global_state_poc.py', HERE / 'wp_action_counter_cow_poc.py',
                 HERE / 'fixtures/wp_action_counter_cow_poc.php',
                 HERE / 'wp_action_singleton_object_poc.py',
                 HERE / 'fixtures/wp-action-singleton-object-poc.php',
                 HERE / 'wp_action_hook_alias_poc.py',
                 HERE / 'fixtures/wp_action_hook_alias_poc.php'):
        inputs[path.relative_to(ROOT).as_posix()] = digest(path)
    return dict(sorted(inputs.items()))

def validate_oracle(value):
    if not isinstance(value, dict) or set(value) != {'php_version', 'cases'} or value['php_version'] != '8.5.12':
        raise ValueError('callable WP oracle envelope differs')
    validate_observation(value['cases'])

def verify_receipt(check_comparison=True):
    report = json.loads(RECEIPT.read_text(encoding='utf-8'), object_pairs_hook=_unique_frame_object)
    if report.get('schema') != 'recastpress.wp-callable-api-oracle.v1' or report.get('status') != 'passed':
        raise ValueError('callable WordPress receipt schema/status differs')
    if report.get('source_sha256_before') != report.get('source_sha256_after') or report['source_sha256_before'] != fixed_inputs():
        raise ValueError('callable WordPress source identities differ')
    validate_oracle(report['oracle'])
    if report.get('action_oracle', {}).get('php_version') != '8.5.12':
        raise ValueError('action API oracle PHP version differs')
    validate_action_observation(report['action_oracle'].get('cases'))
    child = report.get('owned_child', {})
    if (set(child) != {'pid', 'returncode', 'reaped'} or type(child['pid']) is not int or child['pid'] <= 0
            or type(child['returncode']) is not int or child['returncode'] != 0 or child['reaped'] is not True):
        raise ValueError('callable WordPress child completion/reap differs')
    action_child = report.get('action_owned_child', {})
    if (set(action_child) != {'pid', 'returncode', 'reaped'} or type(action_child['pid']) is not int or action_child['pid'] <= 0
            or type(action_child['returncode']) is not int or action_child['returncode'] != 0 or action_child['reaped'] is not True):
        raise ValueError('action API WordPress child completion/reap differs')
    captures = report.get('raw_capture', {})
    if not isinstance(captures, dict) or set(captures) != {'stdout', 'stderr', 'action_stdout', 'action_stderr'}:
        raise ValueError('callable WordPress capture inventory differs')
    for channel, capture in captures.items():
        if not isinstance(capture, dict) or set(capture) != {'path', 'bytes', 'sha256'}:
            raise ValueError('callable WordPress capture fields differ')
        path = (ROOT / capture['path']).resolve()
        if not path.is_relative_to((ROOT / 'target/php-compat-v06-probe').resolve()):
            raise ValueError('callable WordPress capture escaped owned directory')
        if (type(capture['bytes']) is not int or not 0 <= capture['bytes'] <= native.MAX_STDERR
                or path.stat().st_size != capture['bytes'] or digest(path) != capture['sha256']):
            raise ValueError('callable WordPress capture identity differs')
        raw = path.read_bytes()
        if channel in ('stdout', 'action_stdout'):
            if channel == 'stdout':
                filter_cases = {key: item for key, item in report['oracle']['cases'].items() if key != 'action_api'}
                expected = {'php_version': report['oracle']['php_version'], 'cases': filter_cases}
            else:
                expected = report['action_oracle']
            _require_exact(json.loads(raw, object_pairs_hook=_unique_frame_object), expected,
                           'callable raw WP' if channel == 'stdout' else 'action raw WP')
        elif raw:
            raise ValueError('callable WordPress emitted diagnostics')
    if check_comparison and 'bridge_comparison' in report:
        comparison = dict(report['bridge_comparison'])
        datetime.fromisoformat(comparison.pop('observed_at_utc'))
        _require_exact(comparison, bridge_comparison(report), 'callable bridge comparison')
    if check_comparison and 'action_comparison' in report:
        comparison = dict(report['action_comparison'])
        datetime.fromisoformat(comparison.pop('observed_at_utc'))
        _require_exact(comparison, action_bridge_comparison(report), 'action bridge comparison')
    return report

def record():
    before = fixed_inputs()
    child = native.captured([str(native.PHP), '-n', str(FIXTURE), str(hooks.WORDPRESS)])
    if child.returncode != 0 or child.stderr:
        raise ValueError(f'callable WordPress oracle process failed: {child.returncode}')
    filter_oracle = json.loads(child.stdout, object_pairs_hook=_unique_frame_object)
    action_child = native.captured([str(native.PHP), '-n', str(FIXTURE), str(hooks.WORDPRESS), 'action_api'])
    if action_child.returncode != 0 or action_child.stderr:
        raise ValueError(f'action API WordPress oracle process failed: {action_child.returncode}')
    action_oracle = json.loads(action_child.stdout, object_pairs_hook=_unique_frame_object)
    validate_action_observation(action_oracle['cases'])
    oracle = {'php_version': filter_oracle['php_version'],
        'cases': {**filter_oracle['cases'], **action_oracle['cases']}}
    validate_oracle(oracle)
    after = fixed_inputs()
    if before != after:
        raise ValueError('source changed during callable WordPress oracle')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    captures = {}
    for capture_name, source_child, source_channel in (
        ('stdout', child, 'stdout'), ('stderr', child, 'stderr'),
        ('action_stdout', action_child, 'stdout'), ('action_stderr', action_child, 'stderr')):
        data = getattr(source_child, source_channel)
        path = ROOT / 'target/php-compat-v06-probe' / f'wp-callables-{stamp}-{capture_name}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        captures[capture_name] = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data), 'sha256': digest(path)}
    report = {'schema': 'recastpress.wp-callable-api-oracle.v1', 'status': 'passed',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'wordpress_version': hooks.reference.VERSION, 'archive_sha256': hooks.ARCHIVE_SHA256,
        'source_sha256_before': before, 'source_sha256_after': after,
        'owned_child': native.CHILD_RUNS[-2], 'action_owned_child': native.CHILD_RUNS[-1],
        'raw_capture': captures, 'oracle': oracle, 'action_oracle': action_oracle,
        'scope': 'Existing 87 filter observations plus the isolated action_api group, each executed in a fresh process against pinned native plugin.php/WP_Hook. This is not a full WordPress bootstrap or arbitrary heap-valued filter-argument claim.'}
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_receipt()
    print('Shared callable program measured and source-verified against pinned WordPress')

def bridge_comparison(report):
    native.verify_receipt()
    native_report = json.loads(native.RECEIPT.read_text(encoding='utf-8'))
    native_case = native_report['callable_api_bridge']['callable_result']
    spec = importlib.util.spec_from_file_location('wp_callable_bridge_check', HERE / 'wasm/bridge_check.py')
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    bridge.verify_latest(profile='callables')
    entries = [(path, json.loads(path.read_text(encoding='utf-8'))) for path in (HERE / 'wasm/evidence').glob('callables-*.json')]
    wasm_path, wasm = bridge._latest_by_observation(entries)
    wasm_case = wasm['host_result']['case']['peer']
    filter_cases = {key: item for key, item in report['oracle']['cases'].items() if key != 'action_api'}
    for label, result in (('native', native_case), ('wasm', wasm_case)):
        _require_exact(result['php_observation'], filter_cases, label + ' callable WP observations')
    return {'native_receipt': native.RECEIPT.relative_to(ROOT).as_posix(), 'native_receipt_sha256': digest(native.RECEIPT),
        'wasm_receipt': wasm_path.relative_to(ROOT).as_posix(), 'wasm_receipt_sha256': digest(wasm_path),
        'matched_cases': (
            ['base.' + key for key in report['oracle']['cases']['base']]
            + ['coercions.' + key for key in report['oracle']['cases']['coercions']]
            + ['counter_cow.' + group + '.' + phase + '.' + mode
               for group, phases in report['oracle']['cases']['counter_cow'].items()
               for phase, modes in phases.items() for mode in modes]
            + ['priority_cow.' + group + '.' + case
               for group, cases in report['oracle']['cases']['priority_cow'].items()
               for case in cases]
            + ['remove_cow.' + group + '.' + case
               for group, cases in report['oracle']['cases']['remove_cow'].items()
               for case in cases]
            + ['hook_unset_reentry.' + case
               for case in report['oracle']['cases']['hook_unset_reentry']]
        ), 'all_program_observations_matched': True}


def action_bridge_comparison(report):
    native.verify_receipt()
    native_report = json.loads(native.RECEIPT.read_text(encoding='utf-8'))
    native_case = native_report['action_api_bridge']['action_result']
    spec = importlib.util.spec_from_file_location('wp_action_bridge_check', HERE / 'wasm/bridge_check.py')
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    bridge.verify_latest(profile='actions')
    entries = [(path, json.loads(path.read_text(encoding='utf-8'))) for path in (HERE / 'wasm/evidence').glob('actions-*.json')]
    wasm_path, wasm = bridge._latest_by_observation(entries)
    wasm_case = wasm['host_result']['case']['peer']
    for label, result in (('native', native_case), ('wasm', wasm_case)):
        _require_exact(result['php_observation'], report['action_oracle']['cases'], label + ' action WP observations')
    return {'native_receipt': native.RECEIPT.relative_to(ROOT).as_posix(), 'native_receipt_sha256': digest(native.RECEIPT),
        'wasm_receipt': wasm_path.relative_to(ROOT).as_posix(), 'wasm_receipt_sha256': digest(wasm_path),
        'matched_cases': ['action_api.' + key for key in report['action_oracle']['cases']['action_api']],
        'all_action_observations_matched': True}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    for flag in ('record', 'verify-only', 'compare-bridges'):
        group.add_argument('--' + flag, action='store_true')
    args = parser.parse_args()
    if args.record:
        record()
    elif args.compare_bridges:
        report = verify_receipt(check_comparison=False)
        report['bridge_comparison'] = {'observed_at_utc': datetime.now(timezone.utc).isoformat(), **bridge_comparison(report)}
        report['action_comparison'] = {'observed_at_utc': datetime.now(timezone.utc).isoformat(), **action_bridge_comparison(report)}
        RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('Native and Wasm filter and action observations matched pinned WordPress')
    else:
        verify_receipt()
        print('Callable WordPress oracle source/capture/comparison identities verified')

if __name__ == '__main__':
    main()
