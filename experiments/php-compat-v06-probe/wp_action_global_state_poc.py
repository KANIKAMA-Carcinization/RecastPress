#!/usr/bin/env python3
"""Measure pinned WordPress behavior when $wp_actions is not an array."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / 'fixtures/wp_action_global_state_poc.php'
PHP_ROOT = ROOT / '.reference/php-8.5.12'
PHP = PHP_ROOT / 'php.exe'
PHP_MANIFEST = PHP_ROOT / 'recastpress-tool.json'
WP_ROOT = ROOT / '.reference/wordpress-7.1.3'
WP_ARCHIVE = ROOT / '.reference/wordpress-7.1.3.tar.gz'
WP_MANIFEST = WP_ROOT / 'recastpress-reference.json'
WP_SOURCES = ('wp-includes/plugin.php', 'wp-includes/class-wp-hook.php')
WP_ARCHIVE_SHA256 = 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
PHP_ARCHIVE_SHA256 = '2ab242af38d76bdf417cde5ec048b1bcd76a5a40f39a3d15d5f046f38571c477'
RECEIPT = HERE / 'evidence/wp-action-global-state-poc-v1.json'
STATES = ('unset', 'null', 'false', 'true', 'int', 'float', 'string', 'array', 'object')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def view(kind: str, value=None) -> dict:
    if kind == 'unset': return {'type': 'unset'}
    if kind == 'null': return {'type': 'null'}
    if kind == 'bool': return {'type': 'bool', 'value': value}
    if kind == 'int': return {'type': 'int', 'value': str(value)}
    if kind == 'float': return {'type': 'float', 'bits': value}
    if kind == 'string': return {'type': 'string', 'hex': value}
    raise AssertionError(kind)


def initial_snapshot(state: str) -> dict:
    if state == 'unset':
        return {'present': False, 'type': 'unset', 'value': view('unset'), 'hook_key_present': False}
    if state == 'object':
        return {'present': True, 'type': 'stdClass', 'value': {'type': 'stdClass'}, 'hook_key_present': False}
    if state == 'array':
        return {'present': True, 'type': 'array', 'entries': [], 'hook_key_present': False}
    nodes = {
        'null': view('null'), 'false': view('bool', False), 'true': view('bool', True),
        'int': view('int', 7), 'float': view('float', '401e000000000000'),
        'string': view('string', '73656564'),
    }
    return {'present': True, 'type': nodes[state]['type'], 'value': nodes[state], 'hook_key_present': False}


def final_snapshot(state: str, operation: str) -> dict:
    if operation == 'do_action' and state in ('unset', 'null', 'false', 'array'):
        hook = ('global-state-' + state).encode().hex()
        return {'present': True, 'type': 'array', 'entries': [{'key_hex': hook, 'value': view('int', 1)}],
                'hook_key_present': True}
    if operation == 'did_action' and state == 'unset':
        return {'present': True, 'type': 'null', 'value': view('null'), 'hook_key_present': False}
    return initial_snapshot(state)


_FALSE_WARNING = '4175746f6d6174696320636f6e76657273696f6e206f662066616c736520746f2061727261792069732064657072656361746564'
_SCALAR_ERROR = '43616e6e6f74207573652061207363616c61722076616c756520617320616e206172726179'
_STRING_ERROR = '43616e6e6f7420616363657373206f6666736574206f66207479706520737472696e67206f6e20737472696e67'
_OBJECT_ERROR = '43616e6e6f7420757365206f626a656374206f66207479706520737464436c617373206173206172726179'


def expected_observation() -> dict:
    observations = {}
    for state in STATES:
        observations[state] = {}
        for operation in ('do_action', 'did_action'):
            initial = initial_snapshot(state)
            log = [{'kind': 'call', 'operation': operation, 'initial': initial}]
            error = None
            warning = state == 'false' and operation == 'do_action'
            if operation == 'do_action' and state in ('true', 'int', 'float'):
                error = ('Error', _SCALAR_ERROR)
            elif operation == 'do_action' and state == 'string':
                error = ('TypeError', _STRING_ERROR)
            elif state == 'object':
                error = ('Error', _OBJECT_ERROR)
            if warning:
                log.append({'kind': 'warning', 'severity': 8192, 'message_hex': _FALSE_WARNING})
            callback_count = 0
            if operation == 'do_action' and state in ('unset', 'null', 'false', 'array'):
                log.append({'kind': 'callback', 'current_action': 'global-state-' + state, 'doing_action': True})
                callback_count = 1
            if error:
                log.append({'kind': 'throwable', 'class': error[0], 'message_hex': error[1]})
            elif operation == 'do_action':
                log.append({'kind': 'result', 'value': view('null')})
            else:
                log.append({'kind': 'result', 'value': view('int', 0)})
            log.append({'kind': 'final', 'state': final_snapshot(state, operation),
                        'callback_count': callback_count, 'current_filter_stack': []})
            observations[state][operation] = log
    return {'php_version': '8.5.12', 'php_int_size': 8, 'wordpress_version': '7.1.3',
            'observations': observations}


def fixed_inputs() -> dict[str, str]:
    import run_check as native

    php_manifest = native.check_php_manifest()
    if php_manifest.get('archive_sha256') != PHP_ARCHIVE_SHA256:
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


def verify_receipt() -> dict:
    report = json.loads(RECEIPT.read_text(encoding='utf-8'))
    if report.get('schema') != 'recastpress.wp-action-global-state-poc.v1' or report.get('status') != 'passed':
        raise ValueError('action global state receipt schema/status differs')
    inputs = fixed_inputs()
    if report.get('source_sha256_before') != inputs or report.get('source_sha256_after') != inputs:
        raise ValueError('action global state source identities differ')
    if report.get('oracle') != expected_observation():
        raise ValueError('action global state observation differs from closed contract')
    child = report.get('owned_child', {})
    if (set(child) != {'pid', 'returncode', 'reaped'} or type(child.get('pid')) is not int or child['pid'] <= 0
            or child.get('returncode') != 0 or child.get('reaped') is not True):
        raise ValueError('action global state child completion differs')
    captures = report.get('raw_capture', {})
    if set(captures) != {'stdout', 'stderr'}:
        raise ValueError('action global state capture inventory differs')
    for channel, capture in captures.items():
        if set(capture) != {'path', 'bytes', 'sha256'}:
            raise ValueError('action global state capture fields differ')
        path = (ROOT / capture['path']).resolve()
        if not path.is_relative_to((HERE / 'evidence/action-global-state-captures').resolve()):
            raise ValueError('action global state capture escaped owned directory')
        raw = path.read_bytes()
        if len(raw) != capture['bytes'] or digest(path) != capture['sha256']:
            raise ValueError('action global state capture identity differs')
        if channel == 'stdout' and json.loads(raw) != report['oracle']:
            raise ValueError('action global state raw output differs')
        if channel == 'stderr' and raw:
            raise ValueError('unexpected PHP stderr')
    return report


def record() -> None:
    import run_check as native

    before = fixed_inputs()
    child = native.captured([str(PHP), '-n', str(FIXTURE), str(WP_ROOT)], timeout=30)
    if child.returncode != 0 or child.stderr:
        raise ValueError(f'action global state PHP child failed: {child.returncode}')
    oracle = json.loads(child.stdout)
    if oracle != expected_observation():
        raise ValueError('measured PHP action global states differ from closed contract')
    after = fixed_inputs()
    if before != after:
        raise ValueError('source changed during action global state measurement')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    captures = {}
    for channel in ('stdout', 'stderr'):
        data = getattr(child, channel)
        path = HERE / 'evidence/action-global-state-captures' / f'wp-action-global-state-{stamp}-{channel}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        captures[channel] = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data), 'sha256': digest(path)}
    report = {
        'schema': 'recastpress.wp-action-global-state-poc.v1', 'status': 'passed',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'wordpress_version': '7.1.3', 'wordpress_archive_sha256': WP_ARCHIVE_SHA256,
        'php_version': '8.5.12', 'php_archive_sha256': PHP_ARCHIVE_SHA256,
        'source_sha256_before': before, 'source_sha256_after': after,
        'owned_child': native.CHILD_RUNS[-1], 'raw_capture': captures, 'oracle': oracle,
        'scope': 'Target-only PHP/WP behavior for unset, null, bool, number, string, array, and object $wp_actions states; no bridge or Wasm request.',
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_receipt()
    print('Target-only action global state oracle recorded and source-verified')


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--record', action='store_true')
    group.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.record:
        record()
    else:
        verify_receipt()
        print('Target-only action global state receipt verified')


if __name__ == '__main__':
    main()
