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
from callable_v2_contract import validate_observation
from hook_contract import _require_exact, _unique_frame_object

ROOT, HERE = hooks.ROOT, hooks.HERE
RECEIPT = HERE / 'evidence/wp-callable-api-v1.json'
FIXTURE = HERE / 'fixtures/callable_wp_oracle.php'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def fixed_inputs():
    inputs = hooks.fixed_inputs()
    for path in (Path(__file__), FIXTURE, HERE / 'fixtures/callable_program.php'):
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
    child = report.get('owned_child', {})
    if (set(child) != {'pid', 'returncode', 'reaped'} or type(child['pid']) is not int or child['pid'] <= 0
            or type(child['returncode']) is not int or child['returncode'] != 0 or child['reaped'] is not True):
        raise ValueError('callable WordPress child completion/reap differs')
    captures = report.get('raw_capture', {})
    if not isinstance(captures, dict) or set(captures) != {'stdout', 'stderr'}:
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
        if channel == 'stdout':
            _require_exact(json.loads(raw, object_pairs_hook=_unique_frame_object), report['oracle'], 'callable raw WP')
        elif raw:
            raise ValueError('callable WordPress emitted diagnostics')
    if check_comparison and 'bridge_comparison' in report:
        comparison = dict(report['bridge_comparison'])
        datetime.fromisoformat(comparison.pop('observed_at_utc'))
        _require_exact(comparison, bridge_comparison(report), 'callable bridge comparison')
    return report

def record():
    before = fixed_inputs()
    child = native.captured([str(native.PHP), '-n', str(FIXTURE), str(hooks.WORDPRESS)])
    if child.returncode != 0 or child.stderr:
        raise ValueError(f'callable WordPress oracle process failed: {child.returncode}')
    oracle = json.loads(child.stdout, object_pairs_hook=_unique_frame_object)
    validate_oracle(oracle)
    after = fixed_inputs()
    if before != after:
        raise ValueError('source changed during callable WordPress oracle')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    captures = {}
    for channel in ('stdout', 'stderr'):
        data = getattr(child, channel)
        path = ROOT / 'target/php-compat-v06-probe' / f'wp-callables-{stamp}-{channel}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        captures[channel] = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data), 'sha256': digest(path)}
    report = {'schema': 'recastpress.wp-callable-api-oracle.v1', 'status': 'passed',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'wordpress_version': hooks.reference.VERSION, 'archive_sha256': hooks.ARCHIVE_SHA256,
        'source_sha256_before': before, 'source_sha256_after': after,
        'owned_child': native.CHILD_RUNS[-1], 'raw_capture': captures, 'oracle': oracle,
        'scope': 'Shared standard hook API program executed by pinned native plugin.php/WP_Hook. Values, ID equivalence, late invocation errors, snapshots and destructor reentry. Not full WordPress bootstrap or heap-valued filter arguments.'}
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
    for label, result in (('native', native_case), ('wasm', wasm_case)):
        _require_exact(result['php_observation'], report['oracle']['cases'], label + ' callable WP observations')
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
        RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('Native and Wasm standard hook observations matched pinned WordPress')
    else:
        verify_receipt()
        print('Callable WordPress oracle source/capture/comparison identities verified')

if __name__ == '__main__':
    main()
