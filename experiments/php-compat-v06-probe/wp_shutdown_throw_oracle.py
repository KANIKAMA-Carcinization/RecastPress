#!/usr/bin/env python3
"""Record and verify a bounded pinned PHP/WordPress shutdown Throwable oracle."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / 'fixtures/wp_shutdown_throw_oracle.php'
PHP_ROOT = ROOT / '.reference/php-8.5.12'
PHP = PHP_ROOT / 'php.exe'
PHP_MANIFEST = PHP_ROOT / 'recastpress-tool.json'
WP_ROOT = ROOT / '.reference/wordpress-7.1.3'
WP_ARCHIVE = ROOT / '.reference/wordpress-7.1.3.tar.gz'
WP_MANIFEST = WP_ROOT / 'recastpress-reference.json'
CAPTURES = HERE / 'evidence/wp-shutdown-throw-captures'
RECEIPT = HERE / 'evidence/wp-shutdown-throw-oracle-v1.json'
RUN_CHECK = HERE / 'run_check.py'
WP_SOURCES = ('wp-includes/plugin.php', 'wp-includes/class-wp-hook.php')
WP_ARCHIVE_SHA256 = 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
PHP_ARCHIVE_SHA256 = '2ab242af38d76bdf417cde5ec048b1bcd76a5a40f39a3d15d5f046f38571c477'

sys.path.insert(0, str(HERE))
import run_check as native  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixed_inputs() -> dict[str, str]:
    if native.check_php_manifest().get('archive_sha256') != PHP_ARCHIVE_SHA256:
        raise ValueError('pinned PHP distribution identity differs')
    wp = json.loads(WP_MANIFEST.read_text(encoding='utf-8'))
    if wp.get('wordpress_version') != '7.1.3' or wp.get('archive_sha256') != WP_ARCHIVE_SHA256:
        raise ValueError('pinned WordPress identity differs')
    if digest(WP_ARCHIVE) != WP_ARCHIVE_SHA256:
        raise ValueError('pinned WordPress archive bytes differ')
    files = {entry['path']: entry['sha256'] for entry in wp['files']}
    paths = {
        'runner': Path(__file__),
        'fixture': FIXTURE,
        'run_check': RUN_CHECK,
        'php_manifest': PHP_MANIFEST,
        'wordpress_manifest': WP_MANIFEST,
        'wordpress_archive': WP_ARCHIVE,
    }
    for relative in WP_SOURCES:
        source = WP_ROOT / relative
        if digest(source) != files.get(relative):
            raise ValueError('installed WordPress source differs: ' + relative)
        paths['wordpress/' + relative] = source
    with tarfile.open(WP_ARCHIVE, 'r:gz') as archive:
        for relative in WP_SOURCES:
            source = archive.extractfile('wordpress/' + relative)
            if source is None or hashlib.sha256(source.read()).hexdigest() != digest(WP_ROOT / relative):
                raise ValueError('WordPress execution source differs from pinned archive: ' + relative)
    return dict(sorted((label, digest(path)) for label, path in paths.items()))


def normalize_stderr(raw: bytes) -> str:
    value = raw.decode('utf-8', 'replace').replace('\r\n', '\n').replace('\r', '\n')
    first = value.split('\n', 1)[0]
    first = re.sub(r' in .+?wp_shutdown_throw_oracle\.php:\d+$', ' in <fixture>:<line>', first)
    return first + '\n'


def run_target() -> tuple[dict, dict]:
    before_children = len(native.CHILD_RUNS)
    child = native.captured([
        str(PHP), '-n', '-d', 'display_errors=stderr', str(FIXTURE), str(WP_ROOT)
    ], timeout=30)
    if len(native.CHILD_RUNS) != before_children + 1:
        raise ValueError('owned child completion inventory differs')
    owned = native.CHILD_RUNS[-1]
    if owned.get('returncode') != child.returncode or owned.get('reaped') is not True:
        raise ValueError('shutdown oracle child was not reaped with its observed exit')
    if child.returncode != 255:
        raise ValueError(f'shutdown Throwable child exit differs: {child.returncode}')
    try:
        lines = child.stdout.decode('utf-8').splitlines()
        if len(lines) != 1:
            raise ValueError('shutdown observer did not emit exactly one JSON line')
        observation = json.loads(lines[0])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('shutdown observer output is invalid JSON') from error
    if set(observation) != {'php_version', 'event', 'wp_state'} or observation['php_version'] != '8.5.12':
        raise ValueError('shutdown observer JSON shape/runtime differs')
    state = observation['wp_state']
    expected_state = {
        'did_action': 1,
        'current_action': 'recastpress_shutdown_throw_probe',
        'doing_action': True,
        'stack': ['recastpress_shutdown_throw_probe'],
        'hook_state': {
            'nesting_level': 1,
            'iterations': [[5, 10, 20]],
            'current_priority': [5],
            'doing_action': True,
        },
        'mutation_before_throw_callback': 'initial',
    }
    if observation['event'] != 'first_shutdown_wp_observer' or state != expected_state:
        raise ValueError(f'first shutdown WP observer state differs: {state!r}')
    if b'SECOND_SHUTDOWN_CALLBACK_REACHED' in child.stdout:
        raise ValueError('second PHP shutdown callback was not suppressed')
    if b'WP_ACTION_SIBLING_REACHED' in child.stdout:
        raise ValueError('later WP action callback was not suppressed')
    if b'Uncaught RuntimeException: shutdown-do-action-throw-marker' not in child.stderr:
        raise ValueError('fatal stderr does not identify the expected uncaught Throwable')
    normalized = normalize_stderr(child.stderr)
    if ' in <fixture>:<line>\n' not in normalized:
        raise ValueError('fatal stderr path was not normalized')
    oracle = {
        'php_version': observation['php_version'],
        'wordpress_version': '7.1.3',
        'observer': observation,
        'stdout_line_count': len(lines),
        'second_shutdown_callback_suppressed': True,
        'later_wp_action_callback_suppressed': True,
        'fatal': {
            'exit_code': child.returncode,
            'uncaught_class': 'RuntimeException',
            'uncaught_message': 'shutdown-do-action-throw-marker',
            'stderr_normalized': normalized,
        },
    }
    return oracle, {
        'pid': owned['pid'], 'returncode': child.returncode, 'reaped': owned['reaped'],
        'stdout': child.stdout, 'stderr': child.stderr,
    }


def verify_receipt(report: dict, replay: bool = True) -> dict:
    if report.get('schema') != 'recastpress.wp-shutdown-throw-oracle.v1' or report.get('status') != 'passed':
        raise ValueError('shutdown Throwable receipt schema/status differs')
    inputs = fixed_inputs()
    if report.get('source_sha256_before') != inputs or report.get('source_sha256_after') != inputs:
        raise ValueError('shutdown Throwable input identities differ')
    if replay:
        current, _ = run_target()
        if current != report.get('oracle'):
            raise ValueError('shutdown Throwable observation changed on replay')
    capture = report.get('raw_capture')
    if not isinstance(capture, dict) or set(capture) != {'stdout', 'stderr', 'pid', 'returncode', 'reaped'}:
        raise ValueError('shutdown Throwable raw capture descriptor differs')
    if type(capture['pid']) is not int or capture['pid'] <= 0 or capture['returncode'] != 255 or capture['reaped'] is not True:
        raise ValueError('shutdown Throwable child completion differs')
    for channel in ('stdout', 'stderr'):
        descriptor = capture[channel]
        if set(descriptor) != {'path', 'bytes', 'sha256'}:
            raise ValueError('shutdown Throwable capture descriptor shape differs')
        path = (ROOT / descriptor['path']).resolve()
        if not path.is_relative_to(CAPTURES.resolve()):
            raise ValueError('shutdown Throwable capture escaped its owned directory')
        raw = path.read_bytes()
        if len(raw) != descriptor['bytes'] or digest(path) != descriptor['sha256']:
            raise ValueError(f'{channel} raw capture identity differs')
        if channel == 'stdout':
            if raw != json.dumps(report['oracle']['observer'], separators=(',', ':')).encode() + b'\n':
                raise ValueError('raw single-line observer JSON differs')
        elif normalize_stderr(raw) != report['oracle']['fatal']['stderr_normalized']:
            raise ValueError('raw fatal stderr differs from oracle')
    return report


def record() -> None:
    before = fixed_inputs()
    oracle, raw = run_target()
    after = fixed_inputs()
    if before != after:
        raise ValueError('shutdown Throwable inputs changed during measurement')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    raw_capture = {'pid': raw['pid'], 'returncode': raw['returncode'], 'reaped': raw['reaped']}
    for channel in ('stdout', 'stderr'):
        data = raw[channel]
        path = CAPTURES / f'wp-shutdown-throw-{stamp}-{channel}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        raw_capture[channel] = {
            'path': path.relative_to(ROOT).as_posix(),
            'bytes': len(data),
            'sha256': digest(path),
        }
    report = {
        'schema': 'recastpress.wp-shutdown-throw-oracle.v1',
        'status': 'passed',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'wordpress_version': '7.1.3',
        'wordpress_archive_sha256': WP_ARCHIVE_SHA256,
        'php_version': '8.5.12',
        'php_archive_sha256': PHP_ARCHIVE_SHA256,
        'source_sha256_before': before,
        'source_sha256_after': after,
        'owned_child': {'pid': raw['pid'], 'returncode': raw['returncode'], 'reaped': raw['reaped']},
        'raw_capture': raw_capture,
        'oracle': oracle,
        'scope': 'One target-only PHP 8.5.12 subprocess with pinned WordPress 7.1.3 plugin.php. The first PHP shutdown callback dispatches a WP action whose first observer records live WP_Hook state before a later callback throws; the next PHP shutdown callback and later WP listener are checked for suppression. No bridge request or full WordPress bootstrap.',
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_receipt(report, replay=False)
    print('Target-only shutdown Throwable oracle recorded and captures verified')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--record', action='store_true')
    group.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.record:
        record()
    else:
        verify_receipt(json.loads(RECEIPT.read_text(encoding='utf-8')))
        print('Target-only shutdown Throwable receipt and captures verified')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, TimeoutError) as error:
        print(f'{Path(__file__).name}: {error}', file=sys.stderr)
        raise SystemExit(1)
