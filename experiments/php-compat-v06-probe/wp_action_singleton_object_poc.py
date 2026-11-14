#!/usr/bin/env python3
"""Record and verify pinned WordPress do_action singleton-object compatibility."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / 'fixtures/wp-action-singleton-object-poc.php'
PHP_ROOT = ROOT / '.reference/php-8.5.12'
PHP = PHP_ROOT / 'php.exe'
PHP_MANIFEST = PHP_ROOT / 'recastpress-tool.json'
WP_ROOT = ROOT / '.reference/wordpress-7.1.3'
WP_ARCHIVE = ROOT / '.reference/wordpress-7.1.3.tar.gz'
WP_MANIFEST = WP_ROOT / 'recastpress-reference.json'
WP_SOURCES = ('wp-includes/plugin.php', 'wp-includes/class-wp-hook.php')
ARCHIVE_SHA256 = 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
PHP_ARCHIVE_SHA256 = '2ab242af38d76bdf417cde5ec048b1bcd76a5a40f39a3d15d5f046f38571c477'
RECEIPT = HERE / 'evidence/wp-action-singleton-object-poc-v1.json'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strview(value: str) -> dict:
    return {'type': 'string', 'hex': value.encode().hex()}


def objectview(case: str) -> dict:
    return {'type': 'object', 'class': 'stdClass', 'marker': 'object-' + case, 'same_object': True}


def arrayview(case: str, *items: tuple[str, dict]) -> dict:
    return {'type': 'array', 'count': len(items), 'items': [
        {'key': key, 'value': value} for key, value in items
    ]}


def case_expected(case: str, named_args: list[dict] | None) -> dict:
    label = {
        'zero_key_singleton': 'zero-key-singleton',
        'nonzero_key_singleton': 'nonzero-key-singleton',
        'multi_element': 'multi-element',
        'direct_object': 'direct-object',
        'extra_args': 'extra-args',
        'missing_named_listener': 'missing-named-listener',
        'do_action_ref_array_control': 'ref-array-control',
    }[case]
    hook = 'singleton-object-' + label
    obj = objectview(label)
    all_args = [strview(hook)]
    if case in ('zero_key_singleton', 'missing_named_listener'):
        all_args.append(arrayview(label, ('0', obj)))
    elif case == 'nonzero_key_singleton':
        all_args.append(arrayview(label, ('1', obj)))
    elif case == 'multi_element':
        all_args.append(arrayview(label, ('0', obj), ('1', strview('tail'))))
    elif case == 'direct_object':
        all_args.append(obj)
    elif case == 'extra_args':
        all_args.extend((arrayview(label, ('0', obj)), strview('tail')))
    elif case == 'do_action_ref_array_control':
        all_args.append(arrayview(label, ('0', arrayview(label, ('0', obj)))))
    events = [{'event': 'all', 'args': all_args}]
    if named_args is not None:
        events.append({'event': 'named', 'args': named_args})
    return {'events': events, 'did_action': 1, 'current_filter_stack': []}


def expected_oracle() -> dict:
    return {'php_version': '8.5.12', 'cases': {
        'zero_key_singleton': case_expected('zero_key_singleton', [objectview('zero-key-singleton')]),
        'nonzero_key_singleton': case_expected('nonzero_key_singleton', [arrayview('nonzero-key-singleton', ('1', objectview('nonzero-key-singleton')))]),
        'multi_element': case_expected('multi_element', [arrayview('multi-element', ('0', objectview('multi-element')), ('1', strview('tail')))]),
        'direct_object': case_expected('direct_object', [objectview('direct-object')]),
        'extra_args': case_expected('extra_args', [objectview('extra-args'), strview('tail')]),
        'missing_named_listener': case_expected('missing_named_listener', None),
        'do_action_ref_array_control': case_expected('do_action_ref_array_control', [arrayview('ref-array-control', ('0', objectview('ref-array-control')))]),
    }}


def fixed_inputs() -> dict[str, str]:
    import run_check as native

    if native.check_php_manifest().get('archive_sha256') != PHP_ARCHIVE_SHA256:
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
        source = WP_ROOT / relative
        if digest(source) != files.get(relative):
            raise ValueError('installed WordPress source differs: ' + relative)
        paths.append(source)
    with tarfile.open(WP_ARCHIVE, 'r:gz') as archive:
        for relative in WP_SOURCES:
            source = archive.extractfile('wordpress/' + relative)
            if source is None or hashlib.sha256(source.read()).hexdigest() != digest(WP_ROOT / relative):
                raise ValueError('WordPress execution source differs from pinned archive: ' + relative)
    return dict(sorted((path.relative_to(ROOT).as_posix(), digest(path)) for path in paths))


def verify_receipt() -> dict:
    report = json.loads(RECEIPT.read_text(encoding='utf-8'))
    if report.get('schema') != 'recastpress.wp-action-singleton-object-poc.v1' or report.get('status') != 'passed':
        raise ValueError('singleton-object receipt schema/status differs')
    inputs = fixed_inputs()
    if report.get('source_sha256_before') != inputs or report.get('source_sha256_after') != inputs:
        raise ValueError('singleton-object source identities differ')
    if report.get('oracle') != expected_oracle():
        raise ValueError('singleton-object observations differ from strict expected contract')
    child = report.get('owned_child', {})
    if (set(child) != {'pid', 'returncode', 'reaped'} or type(child.get('pid')) is not int or child['pid'] <= 0
            or type(child.get('returncode')) is not int or child['returncode'] != 0 or child.get('reaped') is not True):
        raise ValueError('singleton-object child completion/reap differs')
    captures = report.get('raw_capture', {})
    if set(captures) != {'stdout', 'stderr'}:
        raise ValueError('singleton-object capture inventory differs')
    for channel, capture in captures.items():
        if set(capture) != {'path', 'bytes', 'sha256'}:
            raise ValueError('singleton-object capture fields differ')
        path = (ROOT / capture['path']).resolve()
        if not path.is_relative_to((HERE / 'evidence/action-singleton-object-captures').resolve()):
            raise ValueError('singleton-object capture escaped owned directory')
        raw = path.read_bytes()
        if len(raw) != capture['bytes'] or digest(path) != capture['sha256']:
            raise ValueError('singleton-object capture identity differs')
        if channel == 'stdout' and json.loads(raw) != report['oracle']:
            raise ValueError('singleton-object raw output differs')
        if channel == 'stderr' and raw:
            raise ValueError('unexpected PHP diagnostics were captured')
    return report


def record() -> None:
    import run_check as native

    before = fixed_inputs()
    child = native.captured([str(PHP), '-n', str(FIXTURE), str(WP_ROOT)], timeout=30)
    if child.returncode != 0 or child.stderr:
        raise ValueError(f'singleton-object PHP child failed: {child.returncode}')
    oracle = json.loads(child.stdout)
    if oracle != expected_oracle():
        raise ValueError('measured singleton-object behavior differs from expected contract')
    after = fixed_inputs()
    if before != after:
        raise ValueError('source changed during singleton-object measurement')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    captures = {}
    for channel in ('stdout', 'stderr'):
        data = getattr(child, channel)
        path = HERE / 'evidence/action-singleton-object-captures' / f'wp-action-singleton-object-{stamp}-{channel}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        captures[channel] = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data), 'sha256': digest(path)}
    report = {'schema': 'recastpress.wp-action-singleton-object-poc.v1', 'status': 'passed',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'wordpress_version': '7.1.3',
        'wordpress_archive_sha256': ARCHIVE_SHA256, 'php_version': '8.5.12',
        'php_archive_sha256': PHP_ARCHIVE_SHA256, 'source_sha256_before': before,
        'source_sha256_after': after, 'owned_child': native.CHILD_RUNS[-1],
        'raw_capture': captures, 'oracle': oracle,
        'scope': 'Target-only native PHP/WP do_action legacy singleton-object unwrapping, all-vs-named argument views, missing named listener and do_action_ref_array control. No Rust bridge or PHP-Wasm request is made.'}
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_receipt()
    print('Target-only singleton-object oracle recorded and source-verified')


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
        print('Target-only singleton-object receipt verified')


if __name__ == '__main__':
    main()
