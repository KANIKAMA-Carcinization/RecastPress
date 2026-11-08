"""Derive and build the two relocatable Linux preview executables offline."""

import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time

from stage_wordpress_preview_release import (
    EXPERIMENTS, MAX_BINARY, RELOCATED_BINARIES, ROOT, RUNTIME_BUILD_SCHEMA,
    TARGET, load, require,
)


CRATES = {
    'http': 'php-wasix-wp-release-http-probe',
    'worker': 'php-wasix-wp-release-worker-probe',
}
GENERATED = {'http': ('src/main.rs',), 'worker': ('src/main.rs', 'src/catalog.rs')}
MAX_SOURCE = 4 * 1024 * 1024
MAX_STDOUT = 16 * 1024 * 1024
MAX_STDERR = 256 * 1024
DERIVE_TIMEOUT = 60
BUILD_TIMEOUT = 1200


def sha(wire):
    return hashlib.sha256(wire).hexdigest()


def identity(path, limit):
    require(path.resolve(strict=True) == path and path.is_file(), 'runtime_build_fixed_file')
    size = path.stat().st_size
    require(0 < size <= limit, 'runtime_build_file_size')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        remaining = size
        while remaining:
            chunk = stream.read(min(128 * 1024, remaining))
            require(chunk, 'runtime_build_file_truncated')
            digest.update(chunk)
            remaining -= len(chunk)
        require(not stream.read(1), 'runtime_build_file_grew')
    return {'bytes': size, 'sha256': digest.hexdigest()}


def sources(adapter):
    added = [Path(__file__).resolve(), ROOT / 'scripts/stage_wordpress_preview_release.py',
        ROOT / 'scripts/check_wordpress_preview_release.py', ROOT / 'scripts/check_wordpress_preview_runtime.py',
        ROOT / '.github/workflows/wordpress-preview-release.yml',
        ROOT / 'crates/recastpress-server/src/wordpress_release.rs']
    added += [EXPERIMENTS / crate / name for crate in CRATES.values()
        for name in ('Cargo.toml', 'Cargo.lock', 'derive.py')]
    result = dict(adapter.inputs())
    for path in added:
        result[path.relative_to(ROOT).as_posix()] = identity(path, MAX_SOURCE)['sha256']
    return dict(sorted(result.items()))


def captured(stage, command, env, timeout):
    start = time.monotonic()
    deadline = start + timeout
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, start_new_session=True)
        reaped = False
        try:
            # Keep the PID anchored while deciding whether the owned group
            # needs cleanup. Cargo can have compiler descendants.
            while True:
                require(os.fstat(stdout.fileno()).st_size <= MAX_STDOUT
                    and os.fstat(stderr.fileno()).st_size <= MAX_STDERR, stage + '_output_quota')
                status = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
                if status is not None:
                    require(status.si_code == os.CLD_EXITED and status.si_status == 0, stage + '_failed')
                    break
                require(time.monotonic() < deadline, stage + '_timeout')
                time.sleep(0.05)
            stdout.seek(0)
            stderr.seek(0)
            out, err = stdout.read(MAX_STDOUT + 1), stderr.read(MAX_STDERR + 1)
            require(len(out) <= MAX_STDOUT and len(err) <= MAX_STDERR, stage + '_output_quota')
            code = child.wait(timeout=5)
            reaped = True
            require(code == 0, stage + '_failed')
            receipt = {'stage': stage, 'owned_pid': child.pid, 'owned_reaped': True, 'exit_code': code,
                'timeout_seconds': timeout, 'elapsed_seconds': round(time.monotonic() - start, 3),
                'stdout_bytes': len(out), 'stdout_sha256': sha(out),
                'stderr_bytes': len(err), 'stderr_sha256': sha(err)}
            return out, receipt
        finally:
            if not reaped:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                finally:
                    child.wait(timeout=5)


def generated(role):
    crate = EXPERIMENTS / CRATES[role]
    return {path.relative_to(ROOT).as_posix(): identity(path, MAX_SOURCE)
        for path in (crate / name for name in GENERATED[role])}


def artifact(role, wire):
    crate = EXPERIMENTS / CRATES[role]
    expected = ROOT / RELOCATED_BINARIES[role]
    selected = []
    finished = False
    for line in wire.splitlines():
        event = json.loads(line)
        require(not finished, 'runtime_build_cargo_after_finished')
        if event['reason'] == 'build-finished':
            require(event['success'] is True, 'runtime_build_cargo_failed')
            finished = True
            continue
        if event['reason'] != 'compiler-artifact' or event['target']['name'] != expected.name:
            continue
        require(event['target']['kind'] == ['bin'] and event['profile']['test'] is False
            and Path(event['manifest_path']) == crate / 'Cargo.toml'
            and Path(event['target']['src_path']) == crate / 'src/main.rs'
            and Path(event['executable']) == expected, 'runtime_build_cargo_artifact')
        selected.append(expected)
    require(finished and len(selected) == 1, 'runtime_build_cargo_artifact_count')
    return {'executable': RELOCATED_BINARIES[role], 'executable_identity': identity(expected, MAX_BINARY)}


def build_runtime():
    require(sys.platform == 'linux' and platform.machine() == 'x86_64', 'runtime_build_linux_x86_64')
    adapter = load('php-wasix-wp-session-http-probe', 'build_check.py')
    before = sources(adapter)
    driver = adapter.driver_gate()
    registry = adapter.sourcefs.registry_gate()
    environment = adapter.environment()
    commands, derivations, binaries = [], {}, {}
    for role, crate_name in CRATES.items():
        crate = EXPERIMENTS / crate_name
        out, receipt = captured('derive_' + role, [sys.executable, '-B', str(crate / 'derive.py')],
            environment, DERIVE_TIMEOUT)
        commands.append(receipt)
        derivations[role] = generated(role)
        main = derivations[role][(crate / 'src/main.rs').relative_to(ROOT).as_posix()]
        require(out == (main['sha256'].encode('ascii') + b'\n' if role == 'http' else b''),
            'runtime_build_derivation_stdout')
    for role, crate_name in CRATES.items():
        target = (ROOT / RELOCATED_BINARIES[role]).parents[1]
        manifest = EXPERIMENTS / crate_name / 'Cargo.toml'
        out, receipt = captured('build_' + role,
            ['cargo', 'build', '--locked', '--offline', '--release', '--message-format=json-render-diagnostics',
                '--manifest-path', str(manifest), '--target-dir', str(target)], environment, BUILD_TIMEOUT)
        commands.append(receipt)
        binaries[role] = artifact(role, out)
    after = sources(adapter)
    require(after == before and adapter.driver_gate() == driver and adapter.sourcefs.registry_gate() == registry,
        'runtime_build_input_changed')
    for role, record in binaries.items():
        require(generated(role) == derivations[role]
            and identity(ROOT / record['executable'], MAX_BINARY) == record['executable_identity'],
            'runtime_build_output_changed')
    return {'schema': RUNTIME_BUILD_SCHEMA, 'platform': TARGET, **binaries,
        'commands': commands, 'derivations': derivations, 'inputs_before': before, 'inputs_after': after,
        'driver': driver, 'source_fs_registry': registry, 'source_reverified_after': True}


if __name__ == '__main__':
    try:
        if len(sys.argv) != 1:
            raise ValueError('runtime_build_closed_arguments')
        report = build_runtime()
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError):
        print('WordPress preview runtime build failed', file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(report, sort_keys=True, ensure_ascii=True, allow_nan=False))
