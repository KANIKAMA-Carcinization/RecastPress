"""Build, execute, and source-bind the actual PHP-WASIX hook dispatch run."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"
MANIFEST = HERE / "Cargo.toml"
LOCK = HERE / "Cargo.lock"
ARTIFACT = ROOT / ".reference/php-wasix-8.5.12/php.wasm"
ARTIFACT_SHA256 = "e64850fde91d98dff0bce2b95ed467591376586469b1f4d8bad5c8759c4edbe4"
ARTIFACT_BYTES = 39_727_549
SENTINEL_SHA256 = "68c9c675d3805a343835728d98a0efedf0df9833cb904bebf909c7e0fda5c61c"
TIMEOUT = 600
INPUTS = [
    ".github/workflows/v06-wp-hook-dispatch.yml",
    "experiments/php-wasix-wp-hook-dispatch-probe/Cargo.toml",
    "experiments/php-wasix-wp-hook-dispatch-probe/Cargo.lock",
    "experiments/php-wasix-wp-hook-dispatch-probe/check.py",
    "experiments/php-wasix-wp-hook-dispatch-probe/check_overlay.py",
    "experiments/php-wasix-wp-hook-dispatch-probe/src/main.rs",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_hook_driver.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/prepare_php.py",
    "experiments/php-wasix-wp-hook-dispatch-probe/prepare_overlay.py",
    "experiments/php-wasix-wp-hook-dispatch-probe/README.runtime.ja.md",
    "scripts/wordpress_reference.py",
    "crates/recastpress-wp/Cargo.toml",
    "experiments/php-wasix-wp-hook-dispatch-probe/evidence/overlay-v2/overlay.json",
    "experiments/php-wasix-wp-hook-dispatch-probe/evidence/overlay-v2/wp-includes/plugin.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/evidence/overlay-v2/wp-includes/class-wp-hook.php",
    ".reference/wordpress-7.1.3/wp-includes/class-wp-filter-sentinel.php",
    "experiments/php-runtime-candidate-probe/artifact.json",
    "crates/recastpress-wp/src/dispatcher.rs",
    "crates/recastpress-wp/src/hooks.rs",
    "crates/recastpress-wp/src/continuation.rs",
    "experiments/php-compat-v06-probe/fixtures/bridge_driver.php",
    "experiments/php-compat-v06-probe/fixtures/strict_json.php",
    "experiments/php-compat-v06-probe/fixtures/handle_registry.php",
]
INPUTS.extend(
    path.relative_to(ROOT).as_posix()
    for path in sorted((ROOT / "crates/recastpress-wp/src").rglob("*.rs"))
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if platform.system() != "Linux":
        raise SystemExit("Real PHP-WASIX execution is supported only on the fixed Linux CI runner.")
    if not ARTIFACT.is_file() or ARTIFACT.stat().st_size != ARTIFACT_BYTES or digest(ARTIFACT) != ARTIFACT_SHA256:
        raise SystemExit("Pinned PHP-WASIX artifact is missing or changed.")
    if digest(ROOT / ".reference/wordpress-7.1.3/wp-includes/class-wp-filter-sentinel.php") != SENTINEL_SHA256:
        raise SystemExit("Pinned WordPress filter sentinel changed.")
    before = {name: digest(ROOT / name) for name in INPUTS}
    target = ROOT / "target/php-wasix-wp-hook-dispatch-probe"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "CARGO_HOME": os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")),
        "RUSTUP_HOME": os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")),
        "RUSTUP_TOOLCHAIN": "1.94.0",
        "CARGO_TARGET_DIR": str(target),
        "CARGO_BUILD_JOBS": "2",
        "CARGO_INCREMENTAL": "0",
        "HOME": str(ROOT / "target/php-wasix-hook-empty-home"),
    }
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    build = subprocess.run(
        ["cargo", "+1.94.0", "build", "--locked", "--offline", "--manifest-path", str(MANIFEST)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=TIMEOUT, check=False,
    )
    build_ms = int((time.monotonic() - t0) * 1000)
    output = build.stdout.decode("utf-8", "replace")
    if len(build.stdout) > 2 * 1024 * 1024:
        raise SystemExit("Rust build output exceeded the fixed evidence capture bound.")
    status = "build_failed"
    result = None
    run_code = None
    run_ms = None
    if build.returncode == 0:
        binary = target / "debug/recastpress-wp-hook-dispatch-probe"
        t0 = time.monotonic()
        run = subprocess.run([str(binary)], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=180, check=False)
        run_ms = int((time.monotonic() - t0) * 1000)
        run_code = run.returncode
        output = run.stdout.decode("utf-8", "replace")
        stderr = run.stderr.decode("utf-8", "replace")
        if len(run.stdout) > 2 * 1024 * 1024 or len(run.stderr) > 2 * 1024 * 1024:
            raise SystemExit("PHP-WASIX run output exceeded the fixed evidence capture bound.")
        if run.returncode == 0:
            result = json.loads(output)
            if result.get("status") != "passed":
                raise SystemExit("runner reported a non-passing status")
            status = "passed"
        else:
            output = output + "\n--- stderr ---\n" + stderr
            status = "runtime_failed"
    after = {name: digest(ROOT / name) for name in INPUTS}
    unchanged = before == after
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    capture = f"run-{stamp}.log"
    (EVIDENCE / capture).write_text(output, encoding="utf-8", newline="\n")
    evidence = {
        "schema": "recastpress.php-wasix-wp-hook-dispatch.v1",
        "status": status,
        "source_commit": os.environ.get("GITHUB_SHA", "local"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "started_at_utc": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "observed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "toolchain": {"rustc": subprocess.run(["rustc", "+1.94.0", "--version"], capture_output=True, text=True, check=True).stdout.strip(),
                      "cargo": subprocess.run(["cargo", "+1.94.0", "--version"], capture_output=True, text=True, check=True).stdout.strip(),
                      "wasmer": "7.2.0", "wasmer_wasix": "0.702.0", "compiler": "Cranelift no optimization"},
        "upstream_php": {key: json.loads((ROOT / "experiments/php-runtime-candidate-probe/artifact.json").read_text(encoding="utf-8"))[key]
                         for key in ("declared_php_version", "source_repository", "source_commit", "workflow_run_id", "workflow_run_url", "artifact_id", "artifact_name", "artifact_api_url")},
        "artifact": {"bytes": ARTIFACT_BYTES, "sha256": ARTIFACT_SHA256},
        "build": {"exit_code": build.returncode, "duration_ms": build_ms, "timeout_seconds": TIMEOUT},
        "run": {"exit_code": run_code, "duration_ms": run_ms, "timeout_seconds": 180, "result": result},
        "source_inputs_before": before,
        "source_inputs_after": after,
        "source_inputs_unchanged": unchanged,
        "capture": capture,
    }
    (EVIDENCE / "latest.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))
    return 0 if status == "passed" and unchanged else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired as error:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        raw = error.stdout or b""
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "replace")
        raw = raw[: 2 * 1024 * 1024]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        capture = f"run-{stamp}-timeout.log"
        (EVIDENCE / capture).write_bytes(raw)
        report = {
            "schema": "recastpress.php-wasix-wp-hook-dispatch.v1",
            "status": "timeout",
            "timeout_seconds": error.timeout,
            "command": [str(part) for part in error.cmd] if isinstance(error.cmd, (list, tuple)) else str(error.cmd),
            "source_commit": os.environ.get("GITHUB_SHA", "local"),
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "source_inputs": {name: digest(ROOT / name) for name in INPUTS if (ROOT / name).is_file()},
            "artifact": {"bytes": ARTIFACT_BYTES, "sha256": ARTIFACT_SHA256},
            "capture": capture,
            "capture_bytes": len(raw),
        }
        (EVIDENCE / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        raise SystemExit(124)
