#!/usr/bin/env python3
"""Record and verify uncaught WordPress Action Throwable shutdown behavior."""
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
FIXTURE = HERE / 'fixtures/wp_action_uncaught_throwable_poc.php'
REFERENCE_ROOT = ROOT
PHP_ROOT = REFERENCE_ROOT / '.reference/php-8.5.12'
PHP = PHP_ROOT / 'php.exe'
PHP_MANIFEST = PHP_ROOT / 'recastpress-tool.json'
WP_ROOT = REFERENCE_ROOT / '.reference/wordpress-7.1.3'
WP_ARCHIVE = REFERENCE_ROOT / '.reference/wordpress-7.1.3.tar.gz'
WP_MANIFEST = WP_ROOT / 'recastpress-reference.json'
WP_SOURCES = ('wp-includes/plugin.php', 'wp-includes/class-wp-hook.php')
WP_ARCHIVE_SHA256 = 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
PHP_ARCHIVE_SHA256 = '2ab242af38d76bdf417cde5ec048b1bcd76a5a40f39a3d15d5f046f38571c477'
RUN_CHECK = REFERENCE_ROOT / 'experiments/php-compat-v06-probe/run_check.py'
RUN_CHECK_DIR = RUN_CHECK.parent
RECEIPT = HERE / 'evidence/wp-action-uncaught-throwable-poc-v1.json'
CAPTURES = HERE / 'evidence/action-uncaught-throwable-captures'
CASES = ('all', 'named')

sys.path.insert(0, str(RUN_CHECK_DIR))
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
        'workspace/runner': (Path(__file__), 'workspace/experiments/php-compat-v06-probe/wp_action_uncaught_throwable_poc.py'),
        'workspace/fixture': (FIXTURE, 'workspace/experiments/php-compat-v06-probe/fixtures/wp_action_uncaught_throwable_poc.php'),
        'owner/run_check.py': (RUN_CHECK, 'owner/experiments/php-compat-v06-probe/run_check.py'),
        'php/manifest': (PHP_MANIFEST, 'reference/.reference/php-8.5.12/recastpress-tool.json'),
        'wordpress/manifest': (WP_MANIFEST, 'reference/.reference/wordpress-7.1.3/recastpress-reference.json'),
        'wordpress/archive': (WP_ARCHIVE, 'reference/.reference/wordpress-7.1.3.tar.gz'),
    }
    for relative in WP_SOURCES:
        source = WP_ROOT / relative
        if digest(source) != files.get(relative):
            raise ValueError('installed WordPress source differs: ' + relative)
        paths['wordpress/' + relative] = (source, 'reference/.reference/wordpress-7.1.3/' + relative)
    with tarfile.open(WP_ARCHIVE, 'r:gz') as archive:
        for relative in WP_SOURCES:
            source = archive.extractfile('wordpress/' + relative)
            if source is None or hashlib.sha256(source.read()).hexdigest() != digest(WP_ROOT / relative):
                raise ValueError('WordPress execution source differs from pinned archive: ' + relative)
    return dict(sorted((label, digest(path)) for label, (path, _) in paths.items()))


def normalize_stderr(raw: bytes) -> str:
    value = raw.decode('utf-8', 'replace').replace('\r\n', '\n').replace('\r', '\n')
    first_line = value.split('\n', 1)[0]
    first_line = re.sub(r' in .+?:\d+$', ' in <fixture>:<line>', first_line)
    if 'Stack trace:' in value:
        return first_line + '\nStack trace: <normalized>\n  thrown in <fixture> on line <line>\n'
    return first_line + '\n'


def validate_observation(case: str, child, observed: dict) -> None:
    expected_marker = 'uncaught-' + case + '-marker'
    if child.returncode != 255:
        raise ValueError(f'{case} PHP child exit differs: {child.returncode}')
    if observed.get('case') != case:
        raise ValueError(f'{case} shutdown case identity differs')
    expected_events = ['all_first'] if case == 'all' else ['all', 'named_first']
    events = observed.get('events')
    if not isinstance(events, list) or [event.get('event') for event in events] != expected_events:
        raise ValueError(f'{case} callback cut point differs')
    if case == 'all':
        if events[0].get('args') != ['uncaught-throwable-all', 'payload']:
            raise ValueError('all callback arguments differ')
    else:
        if events[0].get('args') != ['uncaught-throwable-named', 'payload']:
            raise ValueError('named-case all callback arguments differ')
        if events[1].get('args') != ['payload']:
            raise ValueError('named callback arguments differ')
    state = observed.get('state')
    if not isinstance(state, dict):
        raise ValueError('shutdown dispatcher state is missing')
    if (state.get('did_action') != 1 or state.get('current_action') != 'uncaught-throwable-' + case
            or state.get('doing_action') is not True or state.get('stack') != ['uncaught-throwable-' + case]):
        raise ValueError(f'{case} shutdown action state differs')
    hook_state = state.get('hook_state')
    all_hook_state = state.get('all_hook_state')
    if not isinstance(hook_state, dict) or not isinstance(all_hook_state, dict):
        raise ValueError(f'{case} shutdown WP_Hook state is missing')
    if case == 'all':
        if hook_state.get('nesting_level') != 0 or all_hook_state.get('nesting_level') != 1:
            raise ValueError('all callback throw did not preserve the observed all-hook iteration cut point')
    elif hook_state.get('nesting_level') != 1 or all_hook_state.get('nesting_level') != 0:
        raise ValueError('named callback throw did not preserve the observed named-hook iteration cut point')
    error = observed.get('last_error')
    if (not isinstance(error, dict) or error.get('type') != 1
            or error.get('uncaught_class') != 'RuntimeException'
            or error.get('uncaught_message') != expected_marker):
        raise ValueError(f'{case} shutdown fatal identity differs')
    stderr = normalize_stderr(child.stderr)
    if f'Uncaught RuntimeException: {expected_marker}' not in stderr or '<fixture>' not in stderr:
        raise ValueError(f'{case} fatal stderr does not identify the expected uncaught Throwable')


def run_target() -> tuple[dict, dict]:
    observed_cases = {}
    captures = {}
    for case in CASES:
        before_count = len(native.CHILD_RUNS)
        child = native.captured([str(PHP), '-n', '-d', 'display_errors=stderr', str(FIXTURE), str(WP_ROOT), case], timeout=30)
        if len(native.CHILD_RUNS) != before_count + 1:
            raise ValueError('owned child completion inventory differs')
        owned = native.CHILD_RUNS[-1]
        if owned.get('returncode') != child.returncode or owned.get('reaped') is not True:
            raise ValueError(f'{case} PHP child was not reaped with its observed exit')
        try:
            lines = child.stdout.decode('utf-8').splitlines()
            if len(lines) != 1:
                raise ValueError(f'{case} shutdown observer did not emit one JSON line')
            observed = json.loads(lines[0])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f'{case} shutdown observer output is invalid JSON') from exc
        if observed.get('php_version') != '8.5.12':
            raise ValueError('uncaught Throwable target PHP identity differs')
        validate_observation(case, child, observed)
        observed_cases[case] = {
            'php_version': observed['php_version'],
            'case': observed['case'],
            'events': observed['events'],
            'state': observed['state'],
            'last_error': observed['last_error'],
            'exit_code': child.returncode,
            'stderr_normalized': normalize_stderr(child.stderr),
        }
        captures[case] = {
            'pid': owned['pid'], 'returncode': child.returncode, 'reaped': owned['reaped'],
            'stdout': child.stdout, 'stderr': child.stderr,
        }
    return {'php_version': '8.5.12', 'wordpress_version': '7.1.3', 'cases': observed_cases}, captures


def validate_receipt(report: dict) -> dict:
    if report.get('schema') != 'recastpress.wp-action-uncaught-throwable-poc.v1' or report.get('status') != 'passed':
        raise ValueError('uncaught Throwable receipt schema/status differs')
    inputs = fixed_inputs()
    if report.get('source_sha256_before') != inputs or report.get('source_sha256_after') != inputs:
        raise ValueError('uncaught Throwable source identities differ')
    oracle, _ = run_target()
    if oracle != report.get('oracle'):
        raise ValueError('uncaught Throwable observation changed on replay')
    captures = report.get('raw_capture', {})
    if set(captures) != {case for case in CASES}:
        raise ValueError('uncaught Throwable capture case inventory differs')
    for case, capture in captures.items():
        if set(capture) != {'stdout', 'stderr', 'pid', 'returncode', 'reaped'}:
            raise ValueError(f'{case} child capture descriptor differs')
        if type(capture['pid']) is not int or capture['pid'] <= 0 or capture['returncode'] != 255 or capture['reaped'] is not True:
            raise ValueError(f'{case} owned child completion differs')
        for channel in ('stdout', 'stderr'):
            descriptor = capture[channel]
            if set(descriptor) != {'path', 'bytes', 'sha256'}:
                raise ValueError(f'{case} {channel} capture descriptor differs')
            path = (ROOT / descriptor['path']).resolve()
            if not path.is_relative_to(CAPTURES.resolve()):
                raise ValueError('uncaught Throwable raw capture escaped owned directory')
            raw = path.read_bytes()
            if len(raw) != descriptor['bytes'] or digest(path) != descriptor['sha256']:
                raise ValueError(f'{case} {channel} capture identity differs')
            if channel == 'stdout':
                # The fixture includes the complete native shutdown observation before normalization.
                if len(raw.splitlines()) != 1 or json.loads(raw) != _raw_case_observation(report['oracle'], case):
                    raise ValueError(f'{case} raw shutdown JSON differs from its normalized oracle')
            else:
                normalized = normalize_stderr(raw)
                if normalized != report['oracle']['cases'][case]['stderr_normalized']:
                    raise ValueError(f'{case} raw stderr differs from its normalized oracle')
    return report


def _case_observation(oracle: dict, case: str) -> dict:
    return oracle['cases'][case]


def _raw_case_observation(oracle: dict, case: str) -> dict:
    # Runtime fields in stderr are normalized; the JSON stdout object remains exact.
    normalized = oracle['cases'][case]
    return {key: normalized[key] for key in ('case', 'events', 'state', 'last_error', 'php_version')}


def record() -> None:
    before = fixed_inputs()
    oracle, child_captures = run_target()
    after = fixed_inputs()
    if before != after:
        raise ValueError('uncaught Throwable source changed during measurement')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    raw_capture = {}
    for case, child in child_captures.items():
        row = {'pid': child['pid'], 'returncode': child['returncode'], 'reaped': child['reaped']}
        for channel in ('stdout', 'stderr'):
            data = child[channel]
            path = CAPTURES / f'wp-action-uncaught-throwable-{case}-{stamp}-{channel}.bin'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            row[channel] = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data), 'sha256': digest(path)}
        raw_capture[case] = row
    report = {
        'schema': 'recastpress.wp-action-uncaught-throwable-poc.v1', 'status': 'passed',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'wordpress_version': '7.1.3', 'wordpress_archive_sha256': WP_ARCHIVE_SHA256,
        'php_version': '8.5.12', 'php_archive_sha256': PHP_ARCHIVE_SHA256,
        'source_sha256_before': before, 'source_sha256_after': after,
        'raw_capture': raw_capture, 'oracle': oracle,
        'scope': 'Native pinned PHP/WordPress subprocesses for uncaught Throwable from first all callback and first named callback; shutdown state and process output only. No bridge request is made.',
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    validate_receipt(report)
    print('Target-only uncaught Action Throwable oracle recorded and source-verified')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--record', action='store_true')
    group.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.record:
        record()
    else:
        validate_receipt(json.loads(RECEIPT.read_text(encoding='utf-8')))
        print('Target-only uncaught Action Throwable receipt verified')


if __name__ == '__main__':
    main()
