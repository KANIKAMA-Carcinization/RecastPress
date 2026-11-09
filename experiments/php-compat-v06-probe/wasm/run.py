#!/usr/bin/env python3
"""Offline build and owned-process run for the fixed v0.6 PHP-WASIX profile."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

WASM_DIR = Path(__file__).resolve().parent
ROOT = WASM_DIR.parents[2]
MANIFEST = WASM_DIR / "Cargo.toml"
MAIN_RS = WASM_DIR / "src" / "main.rs"
_target_override = os.environ.get("RECASTPRESS_V06_TARGET_DIR") or os.environ.get("CARGO_TARGET_DIR")
if _target_override:
    TARGET = Path(_target_override).expanduser().resolve()
else:
    TARGET = ROOT / "target" / ("php-wasix-wp-bootstrap-probe/cargo-target" if os.name == "nt" else "php-compat-v06-wasm-linux")
try:
    TARGET.relative_to((ROOT / "target").resolve())
except ValueError as error:
    raise RuntimeError("CARGO_TARGET_DIR must remain under repository target/") from error
EVIDENCE = WASM_DIR / "evidence"
ARTIFACT = ROOT / ".reference" / "php-wasix-8.5.12" / "php.wasm"
FIXTURE_DIR = WASM_DIR.parent / "fixtures"
SOURCES = ["surface_oracle.php", "dispatcher.php", "strict_json.php", "strict_json_oracle.php"]
BOUND_INPUTS = [MANIFEST, WASM_DIR / "Cargo.lock", MAIN_RS, Path(__file__).resolve(), ROOT / "scripts" / "owned_probe.py", ARTIFACT] + [FIXTURE_DIR / name for name in SOURCES]
# The independent package now also builds a bridge against the local WP crate.
# Bind local path inputs as well as registry versions, even for this one-shot bin.
BOUND_INPUTS += [ROOT / "Cargo.toml", ROOT / "Cargo.lock", ROOT / "crates/recastpress-wp/Cargo.toml"]
BOUND_INPUTS += sorted((ROOT / "crates/recastpress-wp/src").rglob("*.rs"))
BUILD_TIMEOUT = 600
HOST_TIMEOUT = 180


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fixed_inputs() -> dict:
    result = {}
    for path in BOUND_INPUTS:
        relative = path.relative_to(ROOT).as_posix()
        result[relative] = {"bytes": path.stat().st_size, "sha256": sha_file(path)}
    return result


def classify_failure(stderr: bytes, exit_code: int) -> tuple[str, str, bool]:
    text = stderr.decode("utf-8", errors="replace")
    # Markers are cumulative on successful later phases; select the deepest one.
    if "PHP_WASIX_V06_PHASE=php_execution_strict_json_oracle" in text:
        return "strict_json_oracle", "php_strict_json_oracle_failed", True
    if "PHP_WASIX_V06_PHASE=php_execution_surface_oracle" in text:
        return "surface_oracle", "php_surface_oracle_failed", True
    if "PHP_WASIX_V06_PHASE=module_compile" in text:
        if "machinst\\lower.rs:1107" in text or "machinst/lower.rs:1107" in text:
            return "module_compile", "cranelift_lowering_panic", False
        return "module_compile", "module_compile_failed", False
    return "build_or_artifact_verification", "runner_failed", False


def source_bound_verdict(status: str, unchanged: bool) -> tuple[str, str | None]:
    if unchanged:
        return status, None
    return "failed", "source_bound_inputs_changed"


def self_check() -> None:
    cases = [
        (b"PHP_WASIX_V06_PHASE=module_compile\n", "module_compile"),
        (b"PHP_WASIX_V06_PHASE=module_compile\nPHP_WASIX_V06_PHASE=php_execution_surface_oracle\n", "surface_oracle"),
        (b"PHP_WASIX_V06_PHASE=module_compile\nPHP_WASIX_V06_PHASE=php_execution_surface_oracle\nPHP_WASIX_V06_PHASE=php_execution_strict_json_oracle\n", "strict_json_oracle"),
    ]
    for stderr, expected in cases:
        actual = classify_failure(stderr, 1)
        if actual[0] != expected:
            raise AssertionError(f"phase classifier: expected {expected}, got {actual}")
    panic = classify_failure(b"PHP_WASIX_V06_PHASE=module_compile\nthread panicked machinst\\lower.rs:1107", 101)
    if panic[:2] != ("module_compile", "cranelift_lowering_panic") or panic[2]:
        raise AssertionError(f"module compile panic classification: {panic}")
    if source_bound_verdict("passed", False) != ("failed", "source_bound_inputs_changed"):
        raise AssertionError("source-bound mismatch must invalidate a successful result")


def run_owned(owned_probe, command: list[str], env: dict, timeout: int):
    try:
        pid, result = owned_probe.run_captured_probe(command, env, timeout=timeout)
        return pid, result, False
    except subprocess.TimeoutExpired as error:
        # The helper retains an owned handle when OS cleanup was incomplete.
        # Confirm retry cleanup before any receipt can claim that it was reaped.
        owned_probe.reap_owned_probes()
        result = subprocess.CompletedProcess(
            command, 124, bytes(error.output or b""), bytes(error.stderr or b"")
        )
        return None, result, True


def owned_probe_module():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import owned_probe  # noqa: PLC0415

    return owned_probe


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_check()
        print("phase classifier self-check passed")
        return 0
    use_llvm = sys.argv[1:] == ["--llvm"]
    if sys.argv[1:] and not use_llvm:
        print("usage: python experiments/php-compat-v06-probe/wasm/run.py [--self-test|--llvm]", file=sys.stderr)
        return 2
    if not ARTIFACT.is_file():
        print("fixed PHP-WASIX artifact is missing", file=sys.stderr)
        return 2

    owned_probe = owned_probe_module()
    TARGET.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(TARGET)
    build_command = ["cargo", "build", "--offline", "--locked", "--manifest-path", str(MANIFEST), "--bin", "recastpress-php-compat-v06-wasm-probe"]
    public_build_command = ["cargo", "build", "--offline", "--locked", "--manifest-path", "experiments/php-compat-v06-probe/wasm/Cargo.toml", "--bin", "recastpress-php-compat-v06-wasm-probe"]
    compiler_profile = "wasmer-7.2.0/llvm-default" if use_llvm else "wasmer-7.2.0/cranelift-noopt"
    if use_llvm:
        build_command.extend(["--no-default-features", "--features", "llvm"])
        public_build_command.extend(["--no-default-features", "--features", "llvm"])
    host_path = TARGET / "debug" / ("recastpress-php-compat-v06-wasm-probe.exe" if os.name == "nt" else "recastpress-php-compat-v06-wasm-probe")
    run_command = [str(host_path)]
    started = datetime.now(timezone.utc)
    before = fixed_inputs()
    build_pid, build, build_timed_out = run_owned(owned_probe, build_command, env, BUILD_TIMEOUT)
    raw_stdout = bytearray(build.stdout)
    raw_stderr = bytearray(build.stderr)
    phase = "build"
    run_pid = None
    host_timed_out = False
    host_binary_before = None
    host_binary_after = None
    host_receipt = None
    if build.returncode == 0:
        phase = "host"
        host_binary_before = sha_file(host_path)
        run_pid, host, host_timed_out = run_owned(owned_probe, run_command, env, HOST_TIMEOUT)
        host_binary_after = sha_file(host_path)
        raw_stdout += b"\n--- HOST STDOUT ---\n" + host.stdout
        raw_stderr += b"\n--- HOST STDERR ---\n" + host.stderr
    else:
        host = None
    ended = datetime.now(timezone.utc)
    after = fixed_inputs()
    unchanged = before == after
    TARGET.mkdir(parents=True, exist_ok=True)
    raw_stdout_rel = TARGET.relative_to(ROOT).as_posix() + "/last-stdout.bin"
    raw_stderr_rel = TARGET.relative_to(ROOT).as_posix() + "/last-stderr.bin"
    (TARGET / "last-stdout.bin").write_bytes(raw_stdout)
    (TARGET / "last-stderr.bin").write_bytes(raw_stderr)

    if build.returncode != 0:
        status, error_phase, failure_class, php_reached = "failed", "build", "cargo_build_timeout" if build_timed_out else "cargo_build_failed", False
        run_exit_code = build.returncode
        cases = {"surface_oracle": "not_reached", "strict_json_oracle": "not_reached"}
        diagnostic = build.stderr
    elif host.returncode != 0:
        error_phase, failure_class, php_reached = classify_failure(host.stderr, host.returncode)
        status = "failed"
        if host_timed_out:
            failure_class = "host_timeout_after_" + error_phase
        run_exit_code = host.returncode
        cases = {
            "surface_oracle": "passed" if error_phase == "strict_json_oracle" else ("failed" if error_phase == "surface_oracle" else "not_reached"),
            "strict_json_oracle": "failed" if error_phase == "strict_json_oracle" else "not_reached",
        }
        diagnostic = host.stderr
    else:
        status, error_phase, failure_class, php_reached = "passed", "complete", None, True
        run_exit_code = 0
        cases = {"surface_oracle": "passed", "strict_json_oracle": "passed"}
        diagnostic = host.stderr
        try:
            host_receipt = json.loads(host.stdout)
            if (host_receipt.get("schema") != "recastpress.php-compat-v06-wasm-surface.v1"
                    or host_receipt.get("execution") != "passed"
                    or host_receipt.get("compiler_profile") != compiler_profile
                    or host_receipt.get("cases", {}).get("surface_oracle", {}).get("status") != "passed"
                    or host_receipt.get("cases", {}).get("strict_json_oracle", {}).get("status") != "passed"):
                raise ValueError("fixed host result did not contain both successful cases")
        except (json.JSONDecodeError, ValueError, AttributeError) as error:
            status, error_phase, failure_class, php_reached = "failed", "host_result_validation", "invalid_host_result", True
            run_exit_code = 1
            cases = {"surface_oracle": "unknown", "strict_json_oracle": "unknown"}
            diagnostic = (host.stderr + b"\nhost JSON output validation failed: " + str(error).encode("utf-8"))

    status, source_failure = source_bound_verdict(status, unchanged)
    if source_failure is not None:
        error_phase, failure_class = "source_bound_verification", source_failure
        run_exit_code = 1
        cases = {"surface_oracle": "invalidated", "strict_json_oracle": "invalidated"}
    binary_unchanged = host_binary_before == host_binary_after if host_binary_before else None
    if binary_unchanged is False:
        status, error_phase, failure_class = "failed", "host_binary_verification", "host_binary_changed"
        run_exit_code = 1
        cases = {"surface_oracle": "invalidated", "strict_json_oracle": "invalidated"}

    # Do not serialize panic text or absolute paths. Keep only classified phase,
    # dependency source location, byte count and digest in the public receipt.
    report = {
        "schema": "recastpress.php-compat-v06-wasm-run.v2",
        "status": status,
        "runtime": "PHP-WASIX 8.5.12 / Wasmer 7.2.0",
        "compiler_profile": compiler_profile,
        "started_at_utc": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "observed_at_utc": ended.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "os": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "command": {
            "build": public_build_command,
            "target_dir": TARGET.relative_to(ROOT).as_posix(),
            "host": TARGET.relative_to(ROOT).as_posix() + "/debug/" + ("recastpress-php-compat-v06-wasm-probe.exe" if os.name == "nt" else "recastpress-php-compat-v06-wasm-probe"),
            "build_timeout_seconds": BUILD_TIMEOUT,
            "host_timeout_seconds": HOST_TIMEOUT,
        },
        "owned_processes": {"build_pid": build_pid, "host_pid": run_pid, "build_timed_out": build_timed_out, "host_timed_out": host_timed_out, "reaped": True},
        "phase": error_phase,
        "failure_class": failure_class,
        "exit_code": run_exit_code,
        "php_reached": php_reached,
        "dependency_source_location": "cranelift-codegen-0.133.0/src/machinst/lower.rs:1107" if failure_class == "cranelift_lowering_panic" else None,
        "source_bound_inputs_before": before,
        "source_bound_inputs_after": after,
        "source_bound_inputs_unchanged": unchanged,
        "host_binary": {"sha256_before": host_binary_before, "sha256_after": host_binary_after, "unchanged": binary_unchanged},
        "host_result": host_receipt,
        "build_environment": {key: env[key] for key in ("CARGO_BUILD_JOBS", "CARGO_INCREMENTAL", "CARGO_PROFILE_DEV_DEBUG", "RUSTUP_TOOLCHAIN") if key in env},
        "stdout_bytes": len(raw_stdout),
        "stdout_sha256": sha_bytes(bytes(raw_stdout)),
        "stderr_bytes": len(diagnostic),
        "stderr_sha256": sha_bytes(diagnostic),
        "raw_capture": {"stdout": raw_stdout_rel, "stderr": raw_stderr_rel},
        "cases": cases,
        "scope": "fixed PHP language fixtures only; no WordPress bootstrap or core algorithm execution",
    }
    stamp = ended.strftime("%Y%m%dT%H%M%SZ")
    if status == "passed":
        evidence_name = f"run-{stamp}-success.json"
    else:
        evidence_name = f"run-{stamp}-{failure_class}.json"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    evidence_path = EVIDENCE / evidence_name
    report["evidence_path"] = (Path("experiments/php-compat-v06-probe/wasm/evidence") / evidence_name).as_posix()
    evidence_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    if status != "passed" or not unchanged:
        return max(1, min(125, run_exit_code or 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
