#!/usr/bin/env python3
"""Record and verify a target-only $wp_actions seed coercion PoC."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile
import sys

import run_check as native

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / 'fixtures/wp_action_counter_seed_poc.php'
PHP_ROOT = ROOT / '.reference/php-8.5.12'
PHP = PHP_ROOT / 'php.exe'
PHP_MANIFEST = PHP_ROOT / 'recastpress-tool.json'
WP_ROOT = ROOT / '.reference/wordpress-7.1.3'
WP_ARCHIVE = ROOT / '.reference/wordpress-7.1.3.tar.gz'
WP_MANIFEST = WP_ROOT / 'recastpress-reference.json'
WP_SOURCES = ('wp-includes/plugin.php', 'wp-includes/class-wp-hook.php')
ARCHIVE_SHA256 = 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
PHP_ARCHIVE_SHA256 = '2ab242af38d76bdf417cde5ec048b1bcd76a5a40f39a3d15d5f046f38571c477'
RECEIPT = HERE / 'evidence/wp-action-counter-seed-poc-v1.json'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def view(kind: str, value: str | bool | None = None) -> dict:
    if kind == 'null':
        return {'type': 'null'}
    if kind == 'int':
        return {'type': 'int', 'value': value}
    if kind == 'float':
        return {'type': 'float', 'bits': value}
    if kind == 'string':
        return {'type': 'string', 'hex': value}
    if kind == 'bool':
        return {'type': 'bool', 'value': value}
    raise AssertionError(kind)


def expected_oracle() -> dict:
    specs = {
        'null': (view('null'), view('int', '1'), view('int', '2'), None),
        'bool_true': (view('bool', True), view('bool', True), view('bool', True),
                      (2, 'Increment on type bool has no effect, this will change in the next major version of PHP')),
        'int': (view('int', '7'), view('int', '8'), view('int', '9'), None),
        'float': (view('float', '401e000000000000'), view('float', '4021000000000000'),
                  view('float', '4023000000000000'), None),
        'alpha': (view('string', '61'), view('string', '62'), view('string', '63'),
                  (8192, 'Increment on non-numeric string is deprecated, use str_increment() instead')),
        'php_int_max': (view('int', '9223372036854775807'), view('float', '43e0000000000000'),
                        view('float', '43e0000000000000'), None),
    }
    cases = {}
    for name, (seed, after_first, after_second, warning) in specs.items():
        hook = 'counter-seed-' + name
        warnings = []
        if warning:
            severity, message = warning
            message_hex = message.encode().hex()
            warnings = [{'index': index, 'severity': severity, 'message_hex': message_hex}
                        for index in (1, 2)]
        events = []
        for phase, argument, action_count in (
                ('first', 'first', after_first), ('follow-up', 'follow-up', after_second)):
            for event, args in (
                    ('all', [hook, argument]), ('named', [argument]),
                    *([('later', [argument])] if phase == 'follow-up' else [])):
                events.append({
                    'event': event, 'args': args, 'current_action': hook, 'doing_action': True,
                    'did_action': action_count, 'wp_actions': action_count,
                })
        cases[name] = {
            'case': name,
            'before': {'seed': seed, 'key_present': True,
                       'did_action': view('int', '0') if name == 'null' else seed},
            'caught': {'class': 'RuntimeException', 'message': 'counter seed callback boom'},
            'first_return': view('null'),
            'callback_order': ['all', 'named', 'all', 'named', 'later'],
            'events': events,
            'warnings': warnings,
            'after_throw': {'did_action': after_first, 'wp_actions': after_first,
                            'current_action': hook, 'doing_action': True},
            'followup_return': view('null'),
            'after_followup': {'did_action': after_second, 'wp_actions': after_second,
                               'current_action': hook, 'doing_action': True},
        }
    return {'php_version': '8.5.12', 'php_int_size': 8,
            'php_int_max': '9223372036854775807', 'cases': cases}


def fixed_inputs() -> dict[str, str]:
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
    if report.get('schema') != 'recastpress.wp-action-counter-seed-poc.v1' or report.get('status') != 'passed':
        raise ValueError('action counter seed receipt schema/status differs')
    inputs = fixed_inputs()
    if report.get('source_sha256_before') != inputs or report.get('source_sha256_after') != inputs:
        raise ValueError('action counter seed source identities differ')
    if report.get('oracle') != expected_oracle():
        raise ValueError('action counter seed observation differs from expected contract')
    child = report.get('owned_child', {})
    if (set(child) != {'pid', 'returncode', 'reaped'} or type(child.get('pid')) is not int or child['pid'] <= 0
            or child.get('returncode') != 0 or child.get('reaped') is not True):
        raise ValueError('action counter seed child completion differs')
    captures = report.get('raw_capture', {})
    if set(captures) != {'stdout', 'stderr'}:
        raise ValueError('action counter seed capture inventory differs')
    for channel, capture in captures.items():
        if set(capture) != {'path', 'bytes', 'sha256'}:
            raise ValueError('action counter seed capture fields differ')
        path = (ROOT / capture['path']).resolve()
        if not path.is_relative_to((HERE / 'evidence/action-counter-seed-captures').resolve()):
            raise ValueError('action counter seed capture escaped owned directory')
        raw = path.read_bytes()
        if len(raw) != capture['bytes'] or digest(path) != capture['sha256']:
            raise ValueError('action counter seed capture identity differs')
        if channel == 'stdout' and json.loads(raw) != report['oracle']:
            raise ValueError('action counter seed raw output differs')
        if channel == 'stderr' and raw:
            raise ValueError('captured PHP diagnostics were not routed into the observation')
    return report


def record() -> None:
    before = fixed_inputs()
    child = native.captured([str(PHP), '-n', str(FIXTURE), str(WP_ROOT)], timeout=30)
    if child.returncode != 0 or child.stderr:
        raise ValueError(f'action counter seed PHP child failed: {child.returncode}')
    oracle = json.loads(child.stdout)
    if oracle != expected_oracle():
        raise ValueError('measured PHP action counter seeds differ from expected contract')
    after = fixed_inputs()
    if before != after:
        raise ValueError('source changed during action counter seed measurement')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    captures = {}
    for channel in ('stdout', 'stderr'):
        data = getattr(child, channel)
        path = HERE / 'evidence/action-counter-seed-captures' / f'wp-action-counter-seed-{stamp}-{channel}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        captures[channel] = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data), 'sha256': digest(path)}
    report = {
        'schema': 'recastpress.wp-action-counter-seed-poc.v1', 'status': 'passed',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'wordpress_version': '7.1.3', 'wordpress_archive_sha256': ARCHIVE_SHA256,
        'php_version': '8.5.12', 'php_archive_sha256': PHP_ARCHIVE_SHA256,
        'source_sha256_before': before, 'source_sha256_after': after,
        'owned_child': native.CHILD_RUNS[-1], 'raw_capture': captures, 'oracle': oracle,
        'scope': 'Target-only native PHP/WP action counter seed behavior. No Rust bridge or PHP-Wasm request is made.',
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_receipt()
    print('Target-only action counter seed oracle recorded and source-verified')


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
        print('Target-only action counter seed receipt verified')


if __name__ == '__main__':
    main()
