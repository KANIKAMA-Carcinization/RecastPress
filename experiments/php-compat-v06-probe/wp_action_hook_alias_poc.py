#!/usr/bin/env python3
"""Measure cross-type numeric Action hook aliases against pinned WordPress."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / 'fixtures/wp_action_hook_alias_poc.php'
PHP_ROOT = ROOT / '.reference/php-8.5.12'
PHP = PHP_ROOT / 'php.exe'
PHP_MANIFEST = PHP_ROOT / 'recastpress-tool.json'
WP_ROOT = ROOT / '.reference/wordpress-7.1.3'
WP_ARCHIVE = ROOT / '.reference/wordpress-7.1.3.tar.gz'
WP_MANIFEST = WP_ROOT / 'recastpress-reference.json'
WP_SOURCES = ('wp-includes/plugin.php', 'wp-includes/class-wp-hook.php')
WP_ARCHIVE_SHA256 = 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
PHP_ARCHIVE_SHA256 = '2ab242af38d76bdf417cde5ec048b1bcd76a5a40f39a3d15d5f046f38571c477'
RECEIPT = HERE / 'evidence/wp-action-hook-alias-poc-v1.json'
CAPTURES = HERE / 'evidence/action-hook-alias-captures'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def string(value: str) -> dict:
    return {'type': 'string', 'hex': value.encode().hex()}


def integer(value: int) -> dict:
    return {'type': 'int', 'value': str(value)}


def expected_case(name: str, registered: str | int, dispatched: str | int) -> dict:
    hook = string(registered) if type(registered) is str else integer(registered)
    dispatch = string(dispatched) if type(dispatched) is str else integer(dispatched)
    second_value = 5 if type(dispatched) is str else '5'
    second_dispatch = string(second_value) if type(second_value) is str else integer(second_value)
    payload = string('payload')
    start = 0 if name == 'string_registration_integer_dispatch' else 2
    first_count, second_count = start + 1, start + 2
    return {
        'label': name,
        'registered_hook': hook,
        'dispatch_hook': dispatch,
        'second_dispatch_hook': second_dispatch,
        'before': {'string': integer(start), 'integer': integer(start)},
        'has_before_dispatch': {'registered': integer(10), 'dispatch': integer(10)},
        'events': [
            {'event': 'all', 'args': [dispatch, payload], 'current_action': dispatch,
             'did_action_string': integer(first_count), 'did_action_integer': integer(first_count)},
            {'event': 'named', 'args': [payload], 'current_action': dispatch,
             'did_action_string': integer(first_count), 'did_action_integer': integer(first_count)},
            {'event': 'all', 'args': [second_dispatch, payload], 'current_action': second_dispatch,
             'did_action_string': integer(second_count), 'did_action_integer': integer(second_count)},
            {'event': 'named', 'args': [payload], 'current_action': second_dispatch,
             'did_action_string': integer(second_count), 'did_action_integer': integer(second_count)},
        ],
        'after': {'did_action_string': integer(second_count), 'did_action_integer': integer(second_count),
                  'removed_by_dispatch_alias': True,
                  'has_after_remove': {'registered': {'type': 'bool', 'value': False},
                                       'dispatch': {'type': 'bool', 'value': False}},
                  'current_action': {'type': 'bool', 'value': False}, 'doing_action': False,
                  'stack': []},
    }


def expected_oracle() -> dict:
    return {'php_version': '8.5.12', 'php_int_size': 8, 'cases': {
        'string_registration_integer_dispatch': expected_case('string_registration_integer_dispatch', '5', 5),
        'integer_registration_string_dispatch': expected_case('integer_registration_string_dispatch', 5, '5'),
    }}


def fixed_inputs() -> dict[str, str]:
    import run_check as native
    if native.check_php_manifest().get('archive_sha256') != PHP_ARCHIVE_SHA256:
        raise ValueError('pinned PHP distribution identity differs')
    wp = json.loads(WP_MANIFEST.read_text(encoding='utf-8'))
    if wp.get('wordpress_version') != '7.1.3' or wp.get('archive_sha256') != WP_ARCHIVE_SHA256:
        raise ValueError('pinned WordPress identity differs')
    if digest(WP_ARCHIVE) != WP_ARCHIVE_SHA256:
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


def run_target():
    import run_check as native
    child = native.captured([str(PHP), '-n', str(FIXTURE), str(WP_ROOT)], timeout=30)
    if child.returncode != 0 or child.stderr:
        raise ValueError(f'Action hook-alias PHP child failed: {child.returncode}')
    observed = json.loads(child.stdout)
    if observed != expected_oracle():
        raise ValueError('measured cross-type numeric hook alias behavior differs from strict contract')
    return child, observed


def verify_receipt() -> dict:
    report = json.loads(RECEIPT.read_text(encoding='utf-8'))
    if report.get('schema') != 'recastpress.wp-action-hook-alias-poc.v1' or report.get('status') != 'passed':
        raise ValueError('Action hook-alias receipt schema/status differs')
    inputs = fixed_inputs()
    if report.get('source_sha256_before') != inputs or report.get('source_sha256_after') != inputs:
        raise ValueError('Action hook-alias source identities differ')
    if report.get('oracle') != expected_oracle():
        raise ValueError('Action hook-alias observation differs from strict expected contract')
    child, observed = run_target()
    if observed != report.get('oracle'):
        raise ValueError('Action hook-alias observation changed on replay')
    owned = report.get('owned_child', {})
    if (set(owned) != {'pid', 'returncode', 'reaped'} or type(owned.get('pid')) is not int
            or owned['pid'] <= 0 or owned.get('returncode') != 0 or owned.get('reaped') is not True):
        raise ValueError('Action hook-alias child completion/reap differs')
    captures = report.get('raw_capture', {})
    if set(captures) != {'stdout', 'stderr'}:
        raise ValueError('Action hook-alias capture inventory differs')
    for channel, capture in captures.items():
        if set(capture) != {'path', 'bytes', 'sha256'}:
            raise ValueError('Action hook-alias capture descriptor differs')
        path = (ROOT / capture['path']).resolve()
        if not path.is_relative_to(CAPTURES.resolve()):
            raise ValueError('Action hook-alias capture escaped owned directory')
        raw = path.read_bytes()
        if len(raw) != capture['bytes'] or digest(path) != capture['sha256']:
            raise ValueError('Action hook-alias capture identity differs')
        if channel == 'stdout' and json.loads(raw) != report['oracle']:
            raise ValueError('Action hook-alias raw output differs')
        if channel == 'stderr' and raw:
            raise ValueError('Action hook-alias emitted diagnostics')
    return report


def record() -> None:
    import run_check as native
    before = fixed_inputs()
    child, observed = run_target()
    after = fixed_inputs()
    if before != after:
        raise ValueError('source changed during Action hook-alias measurement')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    captures = {}
    for channel in ('stdout', 'stderr'):
        data = getattr(child, channel)
        path = CAPTURES / f'wp-action-hook-alias-{stamp}-{channel}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        captures[channel] = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data), 'sha256': digest(path)}
    report = {
        'schema': 'recastpress.wp-action-hook-alias-poc.v1', 'status': 'passed',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'wordpress_version': '7.1.3', 'wordpress_archive_sha256': WP_ARCHIVE_SHA256,
        'php_version': '8.5.12', 'php_archive_sha256': PHP_ARCHIVE_SHA256,
        'source_sha256_before': before, 'source_sha256_after': after,
        'owned_child': native.CHILD_RUNS[-1], 'raw_capture': captures, 'oracle': observed,
        'scope': 'Target-only cross-type numeric hook-key aliases for do_action: register string 5/dispatch int 5 and the reverse, including all/named arguments, did_action aliases, raw current_action values, and post-dispatch stack unwind. No Rust bridge or PHP-Wasm request is made.',
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_receipt()
    print('Target-only Action hook-alias oracle recorded and source-verified')


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
        print('Target-only Action hook-alias receipt verified')


if __name__ == '__main__':
    main()
