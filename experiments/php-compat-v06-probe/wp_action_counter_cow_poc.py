#!/usr/bin/env python3
"""Record and verify a target-only $wp_actions copy-on-write reentry PoC."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / 'fixtures/wp_action_counter_cow_poc.php'
PHP_ROOT = ROOT / '.reference/php-8.5.12'
PHP = PHP_ROOT / 'php.exe'
PHP_MANIFEST = PHP_ROOT / 'recastpress-tool.json'
WP_ROOT = ROOT / '.reference/wordpress-7.1.3'
WP_ARCHIVE = ROOT / '.reference/wordpress-7.1.3.tar.gz'
WP_MANIFEST = WP_ROOT / 'recastpress-reference.json'
WP_SOURCES = ('wp-includes/plugin.php', 'wp-includes/class-wp-hook.php')
ARCHIVE_SHA256 = 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
PHP_ARCHIVE_SHA256 = '2ab242af38d76bdf417cde5ec048b1bcd76a5a40f39a3d15d5f046f38571c477'
RECEIPT = HERE / 'evidence/wp-action-counter-cow-poc-v1.json'
WARNING_HEX = '496e6372656d656e74206f6e206e6f6e2d6e756d6572696320737472696e6720697320646570726563617465642c20757365207374725f696e6372656d656e74282920696e7374656164'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def string(value: str) -> dict:
    return {'type': 'string', 'hex': value.encode().hex()}


def expected_case(mode: str) -> dict:
    keep_snapshot = mode == 'snapshot'
    hook = f'counter-cow-{mode}'
    a, b = string('a'), string('b')
    warning_base = {'severity': 8192, 'message_hex': WARNING_HEX}

    def state(live: dict, snapshot: dict | None) -> dict:
        return {'live': live, 'snapshot': snapshot, 'did_action': live}

    events = []
    first_warning = {'index': 1, **warning_base, 'phase': 'outer_increment'}
    nested_warning = {'index': 2, **warning_base, 'phase': 'nested_increment'}
    events.append({'event': 'warning-before-nested', **first_warning,
                   **state(a, a if keep_snapshot else None)})
    events.append({'event': 'warning-nested', **nested_warning,
                   **state(a, a if keep_snapshot else None)})
    for event, args in (('all', [hook, 'nested']), ('named', ['nested'])):
        events.append({'event': event, 'args': args, 'current_action': hook,
                       'doing_action': True, **state(b, a if keep_snapshot else None)})
    events.append({'event': 'warning-after-nested', **first_warning,
                   **state(b, a if keep_snapshot else None)})
    for event, args in (('all', [hook, 'outer']), ('named', ['outer'])):
        events.append({'event': event, 'args': args, 'current_action': hook,
                       'doing_action': True, **state(b, b if keep_snapshot else None)})

    return {
        'mode': mode,
        'snapshot_kept': keep_snapshot,
        'before': state(a, None),
        'events': events,
        'warnings': [first_warning, nested_warning],
        'after': state(b, b if keep_snapshot else None),
        'current_filter_stack': [],
    }


def expected_oracle() -> dict:
    return {
        'php_version': '8.5.12',
        'php_int_size': 8,
        'cases': {'live': expected_case('live'), 'snapshot': expected_case('snapshot')},
    }


def fixed_inputs() -> dict[str, str]:
    import run_check as native

    php_manifest = native.check_php_manifest()
    if php_manifest.get('archive_sha256') != PHP_ARCHIVE_SHA256:
        raise ValueError('pinned PHP distribution identity differs')
    wp = json.loads(WP_MANIFEST.read_text(encoding='utf-8'))
    if wp.get('wordpress_version') != '7.1.3' or wp.get('archive_sha256') != ARCHIVE_SHA256:
        raise ValueError('pinned WordPress identity differs')
    if digest(WP_ARCHIVE) != ARCHIVE_SHA256:
        raise ValueError('pinned WordPress archive bytes differ')
    files = {entry['path']: entry['sha256'] for entry in wp['files']}
    paths = [Path(__file__), FIXTURE, PHP_MANIFEST, WP_MANIFEST, WP_ARCHIVE,
             ROOT / 'scripts/wordpress_reference.py']
    for relative in WP_SOURCES:
        path = WP_ROOT / relative
        if digest(path) != files.get(relative):
            raise ValueError('installed WordPress source differs: ' + relative)
        paths.append(path)
    with tarfile.open(WP_ARCHIVE, 'r:gz') as archive:
        for relative in WP_SOURCES:
            source = archive.extractfile('wordpress/' + relative)
            if source is None or hashlib.sha256(source.read()).hexdigest() != digest(WP_ROOT / relative):
                raise ValueError('WordPress execution source differs from pinned archive: ' + relative)
    return dict(sorted((path.relative_to(ROOT).as_posix(), digest(path)) for path in paths))


def verify_receipt() -> dict:
    report = json.loads(RECEIPT.read_text(encoding='utf-8'))
    if report.get('schema') != 'recastpress.wp-action-counter-cow-poc.v1' or report.get('status') != 'passed':
        raise ValueError('action counter COW receipt schema/status differs')
    inputs = fixed_inputs()
    if report.get('source_sha256_before') != inputs or report.get('source_sha256_after') != inputs:
        raise ValueError('action counter COW source identities differ')
    if report.get('oracle') != expected_oracle():
        raise ValueError('action counter COW observation differs from expected contract')
    child = report.get('owned_child', {})
    if (set(child) != {'pid', 'returncode', 'reaped'} or type(child.get('pid')) is not int or child['pid'] <= 0
            or child.get('returncode') != 0 or child.get('reaped') is not True):
        raise ValueError('action counter COW child completion differs')
    captures = report.get('raw_capture', {})
    if set(captures) != {'stdout', 'stderr'}:
        raise ValueError('action counter COW capture inventory differs')
    for channel, capture in captures.items():
        if set(capture) != {'path', 'bytes', 'sha256'}:
            raise ValueError('action counter COW capture fields differ')
        path = (ROOT / capture['path']).resolve()
        if not path.is_relative_to((HERE / 'evidence/action-counter-cow-captures').resolve()):
            raise ValueError('action counter COW capture escaped owned directory')
        raw = path.read_bytes()
        if len(raw) != capture['bytes'] or digest(path) != capture['sha256']:
            raise ValueError('action counter COW capture identity differs')
        if channel == 'stdout' and json.loads(raw) != report['oracle']:
            raise ValueError('action counter COW raw output differs')
        if channel == 'stderr' and raw:
            raise ValueError('captured PHP diagnostics were not routed into the observation')
    return report


def record() -> None:
    import run_check as native

    before = fixed_inputs()
    child = native.captured([str(PHP), '-n', str(FIXTURE), str(WP_ROOT)], timeout=30)
    if child.returncode != 0 or child.stderr:
        raise ValueError(f'action counter COW PHP child failed: {child.returncode}')
    oracle = json.loads(child.stdout)
    if oracle != expected_oracle():
        raise ValueError('measured PHP action counter COW behavior differs from expected contract')
    after = fixed_inputs()
    if before != after:
        raise ValueError('source changed during action counter COW measurement')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    captures = {}
    for channel in ('stdout', 'stderr'):
        data = getattr(child, channel)
        path = HERE / 'evidence/action-counter-cow-captures' / f'wp-action-counter-cow-{stamp}-{channel}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        captures[channel] = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data), 'sha256': digest(path)}
    report = {
        'schema': 'recastpress.wp-action-counter-cow-poc.v1', 'status': 'passed',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'wordpress_version': '7.1.3', 'wordpress_archive_sha256': ARCHIVE_SHA256,
        'php_version': '8.5.12', 'php_archive_sha256': PHP_ARCHIVE_SHA256,
        'source_sha256_before': before, 'source_sha256_after': after,
        'owned_child': native.CHILD_RUNS[-1], 'raw_capture': captures, 'oracle': oracle,
        'scope': 'Target-only native PHP/WP $wp_actions same-hook increment and array snapshot COW reentry. No Rust bridge or PHP-Wasm request is made.',
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_receipt()
    print('Target-only action counter COW oracle recorded and source-verified')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--record', action='store_true')
    group.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.record:
        record()
    else:
        verify_receipt()
        print('Target-only action counter COW receipt verified')


if __name__ == '__main__':
    main()
