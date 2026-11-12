#!/usr/bin/env python3
"""Measure pinned WordPress hook Throwable state and compare the bridge fixtures."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tarfile

import run_check as native
from hook_contract import SCENARIOS, _require_exact, _unique_frame_object

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
import wordpress_reference as reference

FIXTURE = HERE / 'fixtures/hooks_wp_oracle.php'
WORDPRESS = reference.REFERENCE / f'wordpress-{reference.VERSION}'
ARCHIVE = reference.REFERENCE / f'wordpress-{reference.VERSION}.tar.gz'
RECEIPT = HERE / 'evidence/wp-hooks-throwable-v1.json'
WP_SOURCES = ('wp-includes/plugin.php', 'wp-includes/class-wp-hook.php',
              'wp-includes/class-wp-filter-sentinel.php')
ARCHIVE_SHA256 = 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
COMMON_FIELDS = ('scenario', 'final_value_hex', 'native_trace', 'root_b_removed',
                 'late_registered', 'root_filters', 'child_filters', 'hook_depth_after')
THROWABLE_FIELDS = ('current_hook_after', 'child_priority_after', 'root_priority_after')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixed_inputs():
    native.check_php_manifest()
    if reference.digest_file(ARCHIVE, 'sha1') != reference.EXPECTED_SHA1 or digest(ARCHIVE) != ARCHIVE_SHA256:
        raise ValueError('fixed WordPress archive identity differs')
    manifest_path = WORDPRESS / reference.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('wordpress_version') != reference.VERSION or manifest.get('archive_sha256') != ARCHIVE_SHA256:
        raise ValueError('fixed WordPress manifest identity differs')
    recorded = {item['path']: item['sha256'] for item in manifest['files']}
    paths = [Path(__file__), FIXTURE, ROOT / 'scripts/wordpress_reference.py', ARCHIVE, manifest_path]
    for relative in WP_SOURCES:
        path = WORDPRESS / relative
        if digest(path) != recorded.get(relative):
            raise ValueError(f'fixed WordPress source differs: {relative}')
        paths.append(path)
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        for relative in WP_SOURCES:
            stream = archive.extractfile("wordpress/" + relative)
            if stream is None or hashlib.sha256(stream.read()).hexdigest() != digest(WORDPRESS / relative):
                raise ValueError(f'WordPress execution source differs from pinned archive: {relative}')
    inputs = native.source_files()
    inputs.update({path.relative_to(ROOT).as_posix(): digest(path) for path in paths})
    return dict(sorted(inputs.items()))


def expected_oracle():
    rows = []
    for scenario in SCENARIOS:
        normal, caught = scenario == 'hooks', scenario == 'hooks_catch'
        value = ('seed:r5:c3:php-child:c7:php-root:php-b:late:r20' if normal else
                 'seed:r5:php-root-caught:php-b:late:r20' if caught else None)
        observations = [{'where': 'child', 'current_hook': 'child', 'hook_depth': 2, 'child_priority': 5}]
        if not normal:
            observations.append({'where': 'root_catch', 'current_hook': 'child', 'hook_depth': 2, 'child_priority': 5})
        if normal or caught:
            observations.extend({'where': place, 'current_hook': 'root' if normal else 'child',
                                 'hook_depth': 1 if normal else 2, 'child_priority': None if normal else 5}
                                for place in ('root_b', 'r20'))
        rows.append({
            'scenario': scenario, 'final_value_hex': value.encode().hex() if value is not None else None,
            'final_name': 'root-before-child:child:root-after-child:b:late' if normal or caught else 'root-before-child:child',
            'native_trace': ['r5', 'c3', 'c7', 'r20'] if normal else ['r5', 'c3', 'r20'] if caught else ['r5', 'c3'],
            'root_b_removed': True, 'late_registered': True, 'root_filters': 1, 'child_filters': 1,
            'hook_depth_after': 0 if normal else 1 if caught else 2,
            'current_hook_after': None if normal else 'root' if caught else 'child',
            'child_priority_after': None if normal else 5,
            'root_priority_after': 10 if not normal and not caught else None,
            'uncaught_root': scenario == 'hooks_abort', 'same_throwable': None if normal else True, 'observations': observations,
        })
    return {'php_version': '8.5.12', 'cases': rows}


def verify_receipt(check_comparison=True):
    report = json.loads(RECEIPT.read_text(encoding='utf-8'))
    if report.get('schema') != 'recastpress.wp-hooks-throwable-oracle.v1' or report.get('status') != 'passed':
        raise ValueError('WordPress hooks oracle receipt schema/status differs')
    if report.get('source_sha256_before') != report.get('source_sha256_after') or report['source_sha256_before'] != fixed_inputs():
        raise ValueError('WordPress hooks oracle source identity differs')
    _require_exact(report.get('oracle'), expected_oracle(), 'WordPress hook oracle')
    child = report.get('owned_child', {})
    if (set(child) != {'pid', 'returncode', 'reaped'} or type(child['pid']) is not int or child['pid'] <= 0
            or type(child['returncode']) is not int or child['returncode'] != 0 or child['reaped'] is not True):
        raise ValueError('WordPress oracle child completion/reap differs')
    captures = report.get('raw_capture', {})
    if not isinstance(captures, dict) or set(captures) != {'stdout', 'stderr'}:
        raise ValueError('WordPress oracle capture inventory differs')
    for channel, capture in captures.items():
        if not isinstance(capture, dict) or set(capture) != {'path', 'bytes', 'sha256'}:
            raise ValueError('WordPress oracle capture identity fields differ')
        path = (ROOT / capture['path']).resolve()
        if not path.is_relative_to((ROOT / 'target/php-compat-v06-probe').resolve()):
            raise ValueError('oracle private capture escaped owned directory')
        if (type(capture['bytes']) is not int or not 0 <= capture['bytes'] <= native.MAX_STDERR
                or path.stat().st_size != capture['bytes'] or digest(path) != capture['sha256']):
            raise ValueError(f'oracle private {channel} capture differs')
        if channel == 'stdout':
            _require_exact(json.loads(path.read_bytes(), object_pairs_hook=_unique_frame_object), report['oracle'], 'WordPress raw oracle')
        elif path.read_bytes():
            raise ValueError('WordPress oracle private stderr is not empty')
    if check_comparison and 'bridge_comparison' in report:
        comparison = dict(report['bridge_comparison'])
        datetime.fromisoformat(comparison.pop('observed_at_utc'))
        _require_exact(comparison, _bridge_comparison(report), 'WordPress bridge comparison')
    return report


def record():
    before = fixed_inputs()
    child = native.captured([str(native.PHP), '-n', str(FIXTURE), str(WORDPRESS)])
    if child.returncode != 0 or child.stderr:
        raise ValueError(f'WordPress hooks oracle process failed: {child.returncode}')
    oracle = json.loads(child.stdout)
    _require_exact(oracle, expected_oracle(), 'WordPress hook oracle')
    after = fixed_inputs()
    if before != after:
        raise ValueError('source changed during WordPress hook oracle')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    captures = {}
    for channel in ('stdout', 'stderr'):
        data = getattr(child, channel)
        path = ROOT / 'target/php-compat-v06-probe' / f'wp-hooks-{stamp}-{channel}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        captures[channel] = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data), 'sha256': digest(path)}
    report = {
        'schema': 'recastpress.wp-hooks-throwable-oracle.v1', 'status': 'passed',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'wordpress_version': reference.VERSION, 'archive_sha256': ARCHIVE_SHA256,
        'source_sha256_before': before, 'source_sha256_after': after,
        'owned_child': native.CHILD_RUNS[-1], 'raw_capture': captures, 'oracle': oracle,
        'scope': 'Pinned native plugin.php/WP_Hook fixture: final filter values, mutation, public stack and retained priorities. Not full WordPress bootstrap or a PHP-Wasm WordPress baseline.',
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_receipt()
    print('WordPress hook value/catch/abort oracle recorded and source-verified')


def _bridge_comparison(report):
    native.verify_receipt()
    native_report = json.loads(native.RECEIPT.read_text(encoding='utf-8'))
    native_cases = [native_report['resumable_hooks_bridge']['hook_result']]
    native_cases.extend(native_report['resumable_hook_throwable_bridges'][name]['hook_result'] for name in SCENARIOS[1:])
    wasm_dir = HERE / 'wasm'
    spec = importlib.util.spec_from_file_location('wp_oracle_bridge_check', wasm_dir / 'bridge_check.py')
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    bridge.verify_latest(profile='hooks')
    entries = [(p, json.loads(p.read_text(encoding='utf-8'))) for p in (wasm_dir / 'evidence').glob('hooks-*.json')]
    wasm_path, wasm_report = bridge._latest_by_observation(entries)
    wasm_cases = [case['peer'] for case in wasm_report['host_result']['cases']]
    for label, cases in (('native', native_cases), ('wasm', wasm_cases)):
        if len(cases) != len(SCENARIOS):
            raise ValueError(f'{label} hook scenario inventory differs')
        for oracle, peer in zip(report['oracle']['cases'], cases):
            keys = COMMON_FIELDS + THROWABLE_FIELDS
            _require_exact({key: peer.get(key) for key in keys}, {key: oracle[key] for key in keys}, label)
            _require_exact(peer.get('php_observation', {}).get('final_name'), oracle['final_name'], label + ' final name')
    return {
        'native_receipt': native.RECEIPT.relative_to(ROOT).as_posix(),
        'native_receipt_sha256': digest(native.RECEIPT),
        'wasm_receipt': wasm_path.relative_to(ROOT).as_posix(),
        'wasm_receipt_sha256': digest(wasm_path),
        'matched_scenarios': list(SCENARIOS),
        'common_fields': list(COMMON_FIELDS), 'hook_state_fields': list(THROWABLE_FIELDS),
        'final_php_object_name_matched': True,
    }


def compare_bridges():
    report = verify_receipt(check_comparison=False)
    report['bridge_comparison'] = {
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        **_bridge_comparison(report),
    }
    RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('All three native/Wasm hook outcomes matched the measured WordPress final values and state')



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--record', action='store_true')
    group.add_argument('--verify-only', action='store_true')
    group.add_argument('--compare-bridges', action='store_true')
    args = parser.parse_args()
    if args.record:
        record()
    elif args.compare_bridges:
        compare_bridges()
    else:
        verify_receipt()
        print('WordPress hook oracle source and private captures verified')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f'wp_hooks_oracle.py: {error}', file=sys.stderr)
        raise SystemExit(1)
