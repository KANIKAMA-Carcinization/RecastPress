"""Record the fixed Windows Wasmtime probe, including an expected engine rejection."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / 'Cargo.toml'
TARGET = ROOT / 'target/recastpress-local-wasi-host'
ARTIFACT = ROOT / '.reference/php-wasix-8.5.12/php.wasm'
EVIDENCE = HERE / 'evidence/windows-wasmtime-exception-rejection.json'
EXPECTED_ARTIFACT_SHA = 'e64850fde91d98dff0bce2b95ed467591376586469b1f4d8bad5c8759c4edbe4'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fixed(path, limit):
    if path.is_symlink():
        raise ValueError('fixed_input_symlink')
    resolved = path.resolve(strict=True)
    if resolved != path or not resolved.is_relative_to(ROOT) or not resolved.is_file():
        raise ValueError('fixed_input_identity')
    wire = resolved.read_bytes()
    if not wire or len(wire) > limit:
        raise ValueError('fixed_input_size')
    return wire


def cargo_executable(wire):
    found = []
    finished = False
    for line in wire.splitlines():
        item = json.loads(line)
        if finished:
            raise ValueError('cargo_output_after_completion')
        if item.get('reason') == 'compiler-artifact' \
                and item.get('target', {}).get('name') == 'recastpress-php-wasix-wasmtime-probe' \
                and item.get('target', {}).get('kind') == ['bin'] \
                and item.get('profile', {}).get('test') is False:
            found.append(Path(item['executable']).resolve(strict=True))
        if item.get('reason') == 'build-finished':
            if item.get('success') is not True:
                raise ValueError('cargo_build_failed')
            finished = True
    if not finished or len(found) != 1 or not found[0].is_relative_to(TARGET.resolve()):
        raise ValueError('fixed_cargo_executable')
    return found[0]


def run():
    if platform.system() != 'Windows':
        raise ValueError('windows_only_host_probe')
    module = fixed(ARTIFACT, 64 * 1024 * 1024)
    if len(module) != 39_727_549 or sha(module) != EXPECTED_ARTIFACT_SHA:
        raise ValueError('pinned_php_wasix_module')
    source_paths = [MANIFEST, HERE / 'Cargo.lock', HERE / 'src/main.rs', Path(__file__).resolve()]
    source_inputs = {
        path.relative_to(ROOT).as_posix(): {'bytes': len(data), 'sha256': sha(data)}
        for path in source_paths for data in [fixed(path, 1024 * 1024)]
    }
    environment = os.environ.copy()
    for name in ['GITHUB_TOKEN', 'GH_TOKEN', 'ACTIONS_RUNTIME_TOKEN', 'RUST_LOG', 'RUST_BACKTRACE']:
        environment.pop(name, None)
    build = subprocess.run([
        'cargo', 'build', '--manifest-path', str(MANIFEST), '--target-dir', str(TARGET),
        '--locked', '--offline', '--release', '--message-format=json',
    ], cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=600, check=False)
    if build.returncode != 0 or len(build.stdout) > 16 * 1024 * 1024 or len(build.stderr) > 1024 * 1024:
        raise ValueError('fixed_wasmtime_probe_build')
    executable = cargo_executable(build.stdout)
    binary = fixed(executable, 128 * 1024 * 1024)
    execution = subprocess.run([str(executable)], cwd=ROOT, env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=300, check=False)
    if len(execution.stdout) > 16 * 1024 or len(execution.stderr) > 16 * 1024 \
            or not execution.stdout.endswith(b'\n') or execution.stdout.count(b'\n') != 1:
        raise ValueError('fixed_wasmtime_probe_capture')
    observation = json.loads(execution.stdout)
    expected_failure = 'Unsupported feature: proposed exception handling operator Throw { tag_index: 0 }'
    if execution.returncode != 1 or execution.stderr \
            or observation.get('schema') != 'recastpress.php-wasix-wasmtime-version-probe.v0.1' \
            or observation.get('passed') is not False \
            or observation.get('phase') != 'module_compile' \
            or observation.get('guest_executed') is not False \
            or observation.get('hook_adapter_executed') is not False \
            or expected_failure not in observation.get('failure', ''):
        raise ValueError('wasmtime_exception_rejection_contract')
    return {
        'schema': 'recastpress.php-wasix-wasmtime-exception-rejection.v0.1',
        'status': 'expected_engine_rejection',
        'outcome': 'wasmtime_cranelift_rejected_php_wasix_exception_operator_before_guest_start',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'platform': {'system': platform.system(), 'machine': platform.machine()},
        'scope': 'fixed PHP-WASIX 8.5.12 module compile attempt under Wasmtime 36 Cranelift on the local host; no guest execution or hook adapter run',
        'artifact': {'path': ARTIFACT.relative_to(ROOT).as_posix(), 'bytes': len(module), 'sha256': sha(module)},
        'host_binary': {'path': executable.relative_to(ROOT).as_posix(), 'bytes': len(binary), 'sha256': sha(binary)},
        'source_inputs': source_inputs,
        'build': {'command': ['cargo', 'build', '--manifest-path', MANIFEST.relative_to(ROOT).as_posix(),
            '--target-dir', TARGET.relative_to(ROOT).as_posix(), '--locked', '--offline', '--release'],
            'exit_code': build.returncode, 'stderr_bytes': len(build.stderr), 'stdout_sha256': sha(build.stdout)},
        'execution': {'exit_code': execution.returncode, 'stdout_bytes': len(execution.stdout),
            'stdout_sha256': sha(execution.stdout), 'stderr_bytes': len(execution.stderr)},
        'observation': observation,
        'claims': {'guest_executed': False, 'php_version_verified': False,
            'hook_adapter_executed': False, 'rust_peer_executed': False,
            'wordpress_bootstrapped': False, 'product_runtime': False},
    }


if __name__ == '__main__':
    value = run()
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')
    print(value['outcome'])
