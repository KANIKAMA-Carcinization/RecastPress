#!/usr/bin/env python3
"""Measure non-string Action hook-key warnings against pinned PHP and WordPress."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / 'fixtures/wp_action_hook_key_poc.php'
PHP_ROOT = ROOT / '.reference/php-8.5.12'
PHP = PHP_ROOT / 'php.exe'
PHP_MANIFEST = PHP_ROOT / 'recastpress-tool.json'
WP_ROOT = ROOT / '.reference/wordpress-7.1.3'
WP_ARCHIVE = ROOT / '.reference/wordpress-7.1.3.tar.gz'
WP_MANIFEST = WP_ROOT / 'recastpress-reference.json'
WP_SOURCES = ('wp-includes/plugin.php', 'wp-includes/class-wp-hook.php')
WP_ARCHIVE_SHA256 = 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
PHP_ARCHIVE_SHA256 = '2ab242af38d76bdf417cde5ec048b1bcd76a5a40f39a3d15d5f046f38571c477'
RECEIPT = HERE / 'evidence/wp-action-hook-key-poc-v1.json'
CAPTURES = HERE / 'evidence/action-hook-key-captures'
CASE_NAMES = {f'{label}_{mode}' for label in ('float_5_7', 'null', 'numeric_string_5', 'integer_5', 'boolean_false', 'boolean_true')
              for mode in ('all_only', 'named')}
BOOLEAN_ALIAS_NAMES = {'false_register_zero_dispatch', 'zero_register_false_dispatch',
                       'true_register_one_dispatch', 'one_register_true_dispatch'}
UNUSUAL_CASE_NAMES = {f'{label}_{mode}' for label in
                      ('array', 'ordinary_object', 'stringable_object', 'resource')
                      for mode in ('all_only', 'named')}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        raise ValueError(f'Action hook-key PHP child failed: {child.returncode}')
    observed = json.loads(child.stdout)
    if observed.get('php_version') != '8.5.12' or observed.get('php_int_size') != 8:
        raise ValueError('Action hook-key target PHP identity differs')
    cases = observed.get('cases')
    if not isinstance(cases, dict) or set(cases) != CASE_NAMES:
        raise ValueError('Action hook-key case inventory differs')
    for name, case in cases.items():
        if case.get('label') != name or case.get('named_listener') != name.endswith('_named'):
            raise ValueError('Action hook-key case identity differs')
        events, warnings = case.get('events'), case.get('warnings')
        if not isinstance(events, list) or not isinstance(warnings, list):
            raise ValueError('Action hook-key trace shape differs')
        if [warning.get('index') for warning in warnings] != list(range(1, len(warnings) + 1)):
            raise ValueError('Action hook-key warning sequence differs')
        if any(type(w.get('severity')) is not int or not isinstance(w.get('message_hex'), str)
               for w in warnings):
            raise ValueError('Action hook-key diagnostic shape differs')
        event_names = [event.get('event') for event in events]
        if event_names.count('all') != 1 or event_names.count('named') != int(name.endswith('_named')):
            raise ValueError('Action hook-key callback observation count differs')
        if case.get('after', {}).get('stack') != [] or case.get('after', {}).get('doing_action') is not False:
            raise ValueError('Action hook-key dispatcher did not unwind')
    aliases = observed.get('boolean_aliases')
    if not isinstance(aliases, dict) or set(aliases) != BOOLEAN_ALIAS_NAMES:
        raise ValueError('Action boolean alias case inventory differs')
    for name, case in aliases.items():
        if case.get('label') != name:
            raise ValueError('Action boolean alias identity differs')
        events = case.get('events')
        if not isinstance(events, list) or [e.get('event') for e in events] != ['all', 'named']:
            raise ValueError('Action boolean alias callbacks differ')
        if case.get('before') != {'has_dispatched': True, 'did_registered': 0, 'did_dispatched': 0}:
            raise ValueError('Action boolean alias lookup did not share its integer key')
        if case.get('after_dispatch') != {'did_registered': 1, 'did_dispatched': 1}:
            raise ValueError('Action boolean alias counters did not share their integer key')
        if case.get('removed_by_alias') is not True:
            raise ValueError('Action boolean alias removal did not find callback')
        if case.get('after', {}).get('has_registered') is not False or case.get('after', {}).get('has_dispatched') is not False:
            raise ValueError('Action boolean alias removal left hook registered')
        if case.get('after', {}).get('stack') != [] or case.get('after', {}).get('doing_action') is not False:
            raise ValueError('Action boolean alias dispatcher did not unwind')
        sent = case['dispatched_hook']
        if events[0].get('hook_arg') != sent or events[0].get('current_action') != sent:
            raise ValueError('Action boolean alias did not preserve dispatched type in all/current hook')
    unusual = observed.get('unusual_cases')
    if not isinstance(unusual, dict) or set(unusual) != UNUSUAL_CASE_NAMES:
        raise ValueError('Action unusual hook-key case inventory differs')
    for name, case in unusual.items():
        if case.get('label') != name or case.get('named_listener') != name.endswith('_named'):
            raise ValueError('Action unusual hook-key case identity differs')
        warnings, events = case.get('warnings'), case.get('events')
        if not isinstance(warnings, list) or not isinstance(events, list):
            raise ValueError('Action unusual hook-key trace shape differs')
        if [warning.get('index') for warning in warnings] != list(range(1, len(warnings) + 1)):
            raise ValueError('Action unusual hook-key warning sequence differs')
        if any(type(w.get('severity')) is not int or not isinstance(w.get('message_hex'), str)
               for w in warnings):
            raise ValueError('Action unusual hook-key diagnostic shape differs')
        kind = name.removesuffix('_named').removesuffix('_all_only')
        expected_hook_type = 'resource' if kind == 'resource' else ('array' if kind == 'array' else 'object')
        if case.get('hook', {}).get('type') != expected_hook_type:
            raise ValueError('Action unusual hook-key value type differs')
        after = case.get('after', {})
        if after.get('stack') != [] or after.get('doing_action') is not False or after.get('current_action') != {'type': 'bool', 'value': False}:
            raise ValueError('Action unusual hook-key dispatcher did not unwind')
        if kind == 'resource':
            if case.get('registration_throwable') is not None or case.get('dispatch_throwable') is not None:
                raise ValueError('PHP resource hook key unexpectedly threw')
            event_names = [event.get('event') for event in events]
            if event_names.count('all') != 1 or event_names.count('named') != int(name.endswith('_named')):
                raise ValueError('PHP resource hook callbacks differ')
            if not warnings or any(w.get('severity') != 2 for w in warnings):
                raise ValueError('PHP resource hook key did not emit only expected warnings')
            if any(b'used as offset, casting to integer' not in bytes.fromhex(w['message_hex'])
                   for w in warnings):
                raise ValueError('PHP resource hook key warning text differs')
            entries = after.get('counter_entries')
            if not isinstance(entries, list) or len(entries) != 1 or entries[0].get('key', {}).get('type') != 'int':
                raise ValueError('PHP resource hook counter key was not converted to integer')
            did = after.get('did_action', {}).get('value')
            if did != {'type': 'int', 'value': '1'}:
                raise ValueError('PHP resource hook action counter differs')
        else:
            expected_stage = 'named_registration' if name.endswith('_named') else 'dispatch'
            thrown = case.get('registration_throwable') if expected_stage == 'named_registration' else case.get('dispatch_throwable')
            if not isinstance(thrown, dict) or thrown.get('class') != 'TypeError':
                raise ValueError('Illegal PHP hook-key type did not throw TypeError at expected stage')
            expected_error = ('Cannot access offset of type array in isset or empty' if kind == 'array'
                              else 'Cannot access offset of type stdClass in isset or empty'
                              if kind == 'ordinary_object'
                              else 'Cannot access offset of type ActionHookKeyStringable in isset or empty')
            if bytes.fromhex(thrown.get('message_hex', '')).decode('utf-8') != expected_error:
                raise ValueError('Illegal PHP hook-key TypeError message differs')
            if (case.get('dispatch_throwable') is not None) != (expected_stage == 'dispatch'):
                raise ValueError('Illegal PHP hook-key dispatch cut point differs')
            if any(event.get('event') in ('all', 'named') for event in events):
                raise ValueError('Illegal PHP hook-key unexpectedly invoked callbacks')
            if after.get('counter_entries') != [] or after.get('did_action', {}).get('throwable', {}).get('class') != 'TypeError':
                raise ValueError('Illegal PHP hook-key changed action counter or lookup behavior')
    return child, observed


def verify_receipt() -> dict:
    report = json.loads(RECEIPT.read_text(encoding='utf-8'))
    if report.get('schema') != 'recastpress.wp-action-hook-key-poc.v1' or report.get('status') != 'passed':
        raise ValueError('Action hook-key receipt schema/status differs')
    inputs = fixed_inputs()
    if report.get('source_sha256_before') != inputs or report.get('source_sha256_after') != inputs:
        raise ValueError('Action hook-key source identities differ')
    child, observed = run_target()
    if observed != report.get('oracle'):
        raise ValueError('Action hook-key observation changed on replay')
    owned = report.get('owned_child', {})
    if (set(owned) != {'pid', 'returncode', 'reaped'} or type(owned.get('pid')) is not int
            or owned['pid'] <= 0 or owned.get('returncode') != 0 or owned.get('reaped') is not True):
        raise ValueError('Action hook-key child completion differs')
    captures = report.get('raw_capture', {})
    if set(captures) != {'stdout', 'stderr'}:
        raise ValueError('Action hook-key capture inventory differs')
    for channel, capture in captures.items():
        if set(capture) != {'path', 'bytes', 'sha256'}:
            raise ValueError('Action hook-key capture descriptor differs')
        path = (ROOT / capture['path']).resolve()
        if not path.is_relative_to(CAPTURES.resolve()):
            raise ValueError('Action hook-key capture escaped owned directory')
        raw = path.read_bytes()
        if len(raw) != capture['bytes'] or digest(path) != capture['sha256']:
            raise ValueError('Action hook-key capture identity differs')
        if channel == 'stdout' and json.loads(raw) != report['oracle']:
            raise ValueError('Action hook-key raw output differs')
        if channel == 'stderr' and raw:
            raise ValueError('Action hook-key emitted uncaptured diagnostics')
    return report


def record() -> None:
    import run_check as native
    before = fixed_inputs()
    child, observed = run_target()
    after = fixed_inputs()
    if before != after:
        raise ValueError('source changed during Action hook-key measurement')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    captures = {}
    for channel in ('stdout', 'stderr'):
        data = getattr(child, channel)
        path = CAPTURES / f'wp-action-hook-key-{stamp}-{channel}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        captures[channel] = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data), 'sha256': digest(path)}
    report = {
        'schema': 'recastpress.wp-action-hook-key-poc.v1', 'status': 'passed',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'wordpress_version': '7.1.3', 'wordpress_archive_sha256': WP_ARCHIVE_SHA256,
        'php_version': '8.5.12', 'php_archive_sha256': PHP_ARCHIVE_SHA256,
        'source_sha256_before': before, 'source_sha256_after': after,
        'owned_child': native.CHILD_RUNS[-1], 'raw_capture': captures, 'oracle': observed,
        'scope': 'Target-only native PHP/WP do_action non-string hook-key coercion, bool/int alias lookup/removal, warning/callback order, counter reads and unwind. No Rust bridge or PHP-Wasm request is made.',
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_receipt()
    print('Target-only Action hook-key oracle recorded and source-verified')


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
        print('Target-only Action hook-key receipt verified')


if __name__ == '__main__':
    main()
