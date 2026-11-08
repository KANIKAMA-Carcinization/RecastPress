"""Build the bounded Rust object-cache probe with the fixed lockfile."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGET = ROOT / 'target/wp-object-cache-rust-probe/cargo-target'
NAME = 'recastpress-wp-object-cache-rust-probe'
RECEIPT = HERE / 'evidence' / ('windows-build.json' if os.name == 'nt' else 'linux-build.json')
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe


def require(value, code):
    if not value:
        raise ValueError(code)


def identity(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def sources():
    paths = [HERE / name for name in ('Cargo.toml', 'Cargo.lock', 'src/main.rs', 'build_check.py')]
    paths += [ROOT / name for name in ('Cargo.toml', 'crates/recastpress-core/Cargo.toml', 'scripts/owned_probe.py')]
    paths += sorted((ROOT / 'crates/recastpress-core/src').rglob('*.rs'))
    return {path.relative_to(ROOT).as_posix(): identity(path.read_bytes()) for path in paths}


def build():
    before = sources()
    binary = TARGET / 'debug' / (NAME + ('.exe' if os.name == 'nt' else ''))
    environment = os.environ.copy()
    environment.update(CARGO_NET_OFFLINE='true', CARGO_INCREMENTAL='0')
    arguments = ['--offline', '--locked', '--manifest-path', str(HERE / 'Cargo.toml'), '--target-dir', str(TARGET)]
    commands = []
    for stage, command in [('test', ['cargo', 'test', '--quiet', *arguments]),
                           ('build', ['cargo', 'build', '--message-format=json-render-diagnostics', *arguments])]:
        pid, completed = owned_probe.run_captured_probe(command, environment, timeout=180)
        require(completed.returncode == 0 and len(completed.stdout) <= 8 * 1024 * 1024
            and len(completed.stderr) <= 65536, 'object_cache_rust_' + stage + '_failed')
        commands.append({'stage': stage, 'owned_pid': pid, 'owned_reaped': True, 'exit_code': completed.returncode,
            'stdout': identity(completed.stdout), 'stderr': identity(completed.stderr)})
        if stage == 'test':
            matches = re.findall(rb'test result: ok\. ([0-9]+) passed; 0 failed; 0 ignored;', completed.stdout)
            require(len(matches) == 1 and int(matches[0]) > 0, 'object_cache_rust_unit_inventory')
            units = int(matches[0])
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    selected = [event for event in events if event['reason'] == 'compiler-artifact' and event['target']['name'] == NAME]
    require(len(selected) == 1 and events[-1] == {'reason': 'build-finished', 'success': True}, 'object_cache_rust_artifact')
    row = selected[0]
    require(row['target']['kind'] == ['bin'] and row['profile']['test'] is False
        and Path(row['manifest_path']) == HERE / 'Cargo.toml' and Path(row['target']['src_path']) == HERE / 'src/main.rs'
        and Path(row['executable']) == binary and binary.resolve(strict=True) == binary, 'object_cache_rust_artifact_path')
    after = sources()
    require(after == before, 'object_cache_rust_build_inputs_changed')
    return {'schema': 'recastpress.wp-object-cache-rust-build.v0.5', 'passed': True,
        'platform': sys.platform, 'locked_offline': True, 'unit_tests_passed': units,
        'inputs_before': before, 'inputs_after': after, 'commands': commands,
        'executable': binary.relative_to(ROOT).as_posix(), 'executable_identity': identity(binary.read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report = build()
    if args.record:
        RECEIPT.parent.mkdir(exist_ok=True)
        with RECEIPT.open('xb') as stream:
            stream.write((json.dumps(report, sort_keys=True, indent=2) + '\n').encode())
    print(report['executable_identity']['sha256'])


if __name__ == '__main__':
    try:
        main()
    finally:
        owned_probe.reap_owned_probes()
