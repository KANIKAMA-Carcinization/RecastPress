#!/usr/bin/env python3
"""Offline, source-bound Linux PHP-WASIX continuation bridge experiment."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import selectors
import signal
import subprocess
import sys
import time
import uuid

WASM_DIR = Path(__file__).resolve().parent
ROOT = WASM_DIR.parents[2]
MANIFEST = WASM_DIR / "Cargo.toml"
ARTIFACT = ROOT / ".reference" / "php-wasix-8.5.12" / "php.wasm"
ARTIFACT_SHA256 = "e64850fde91d98dff0bce2b95ed467591376586469b1f4d8bad5c8759c4edbe4"
ARTIFACT_BYTES = 39_727_549
DEFAULT_TARGET = ROOT / "target" / "php-compat-v06-wasm-bridge-linux"
EVIDENCE = WASM_DIR / "evidence"
HOST_NAME = "recastpress-php-compat-v06-wasm-bridge"
SCHEMA = "recastpress.php-compat-v06-wasm-bridge.v1"
COMPILER_PROFILE = "wasmer-7.2.0/cranelift-noopt"
BUILD_TIMEOUT = 600
HOST_TIMEOUT = 300
MAX_STDOUT = 1_048_576
MAX_STDERR = 1_048_576
FIXTURE_NAMES = (
    "bridge_driver.php", "surface_oracle.php", "strict_json.php",
    "strict_json_oracle.php", "dispatcher.php",
)
HOST_FIXTURE_ORDER = (
    "surface_oracle.php", "dispatcher.php", "strict_json.php",
    "strict_json_oracle.php", "bridge_driver.php",
)
PEER = ROOT / "crates" / "recastpress-wp" / "examples" / "v06_bridge_peer" / "mod.rs"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def selected_target() -> Path:
    override = os.environ.get("RECASTPRESS_V06_TARGET_DIR") or os.environ.get("CARGO_TARGET_DIR")
    target = Path(override).expanduser().resolve() if override else DEFAULT_TARGET.resolve()
    target_root = (ROOT / "target").resolve()
    try:
        target.relative_to(target_root)
    except ValueError as error:
        raise ValueError("CARGO_TARGET_DIR must remain under repository target/") from error
    if target == target_root:
        raise ValueError("CARGO_TARGET_DIR must select a dedicated subdirectory under repository target/")
    return target


def source_paths() -> list[Path]:
    paths = [
        ROOT / "Cargo.toml", ROOT / "Cargo.lock",
        ROOT / "crates" / "recastpress-wp" / "Cargo.toml",
        MANIFEST, WASM_DIR / "Cargo.lock", Path(__file__).resolve(),
        ROOT / "scripts" / "owned_probe.py", ARTIFACT, PEER,
    ]
    paths.extend(sorted((ROOT / "crates" / "recastpress-wp" / "src").rglob("*.rs")))
    paths.extend((WASM_DIR.parent / "fixtures" / name for name in FIXTURE_NAMES))
    # The common adapter and the selected binary live below this source tree.
    paths.extend(sorted((WASM_DIR / "src").rglob("*.rs")))
    return sorted(set(path.resolve() for path in paths), key=relative)


def fixed_inputs() -> dict:
    if not ARTIFACT.is_file():
        raise FileNotFoundError("fixed PHP-WASIX artifact is missing")
    artifact_size = ARTIFACT.stat().st_size
    artifact_hash = sha_file(ARTIFACT)
    if artifact_size != ARTIFACT_BYTES or artifact_hash != ARTIFACT_SHA256:
        raise ValueError("fixed PHP-WASIX artifact identity differs")
    return {
        relative(path): {"bytes": path.stat().st_size, "sha256": sha_file(path)}
        for path in source_paths()
    }


def _kill_owned(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)
    if process.poll() is None:
        raise RuntimeError("owned bridge child could not be reaped")


def run_bounded(command: list[str], env: dict, timeout: int, label: str):
    """Run one owned Linux child with capped pipe buffers and confirmed reap."""
    process = subprocess.Popen(
        command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    output = {process.stdout: bytearray(), process.stderr: bytearray()}
    limits = {process.stdout: MAX_STDOUT, process.stderr: MAX_STDERR}
    for stream in output:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    started = time.monotonic()
    reason = None
    try:
        while selector.get_map() or process.poll() is None:
            if time.monotonic() - started > timeout:
                reason = f"{label}_timeout"
                break
            for key, _ in selector.select(timeout=0.05):
                stream = key.fileobj
                remaining = limits[stream] + 1 - len(output[stream])
                chunk = os.read(stream.fileno(), min(65536, remaining))
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                output[stream].extend(chunk)
                if len(output[stream]) > limits[stream]:
                    reason = f"{label}_output_limit"
                    break
            if reason:
                break
        if reason:
            _kill_owned(process)
        else:
            process.wait(timeout=10)
            if process.poll() is None:
                raise RuntimeError("owned bridge child exit could not be confirmed")
        return {
            "pid": process.pid,
            "returncode": process.returncode,
            "timed_out": reason == f"{label}_timeout",
            "output_limited": reason == f"{label}_output_limit",
            "stdout": bytes(output[process.stdout]),
            "stderr": bytes(output[process.stderr]),
            "reaped": process.poll() is not None,
            "failure": reason,
        }
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
        if process.poll() is None:
            _kill_owned(process)


def _mapping(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("bridge host result must be an object")
    return value


def _int_is(value, expected: int) -> bool:
    return type(value) is int and value == expected


def validate_host_result(value: dict) -> None:
    """Validate actual guest observations and both continuation scenarios."""
    result = _mapping(value)
    if (result.get("schema") != SCHEMA or result.get("execution") != "passed"
            or result.get("compiler_profile") != COMPILER_PROFILE):
        raise ValueError("bridge host header mismatch")
    artifact = _mapping(result.get("artifact"))
    if artifact.get("bytes") != ARTIFACT_BYTES or artifact.get("sha256") != ARTIFACT_SHA256:
        raise ValueError("bridge host did not report the fixed PHP-WASIX module")
    fixtures = result.get("fixtures")
    expected_fixtures = []
    fixture_dir = WASM_DIR.parent / "fixtures"
    for name in HOST_FIXTURE_ORDER:
        path = fixture_dir / name
        expected_fixtures.append([name, path.stat().st_size, sha_file(path)])
    if fixtures != expected_fixtures:
        raise ValueError("bridge host fixture identities differ from the fixed files")
    cases = _mapping(result.get("cases"))
    if set(cases) != {"surface", "throwable"}:
        raise ValueError("bridge scenarios are incomplete or unexpected")
    for name in ("surface", "throwable"):
        case = _mapping(cases[name])
        if (case.get("status") != "passed" or not _int_is(case.get("php_exit_code"), 0)
                or case.get("guest_completed") is not True or case.get("thread_joined") is not True):
            raise ValueError(f"{name} guest execution did not complete successfully")
        _mapping(case.get("peer"))
    surface = cases["surface"]["peer"]
    throwable = cases["throwable"]["peer"]
    # The shared peer result is intentionally preserved in the receipt. These
    # checks require semantic observations rather than a phase marker alone.
    surface_obs = _mapping(surface.get("php_observation"))
    if (surface_obs.get("same_object_alias") is not True
            or surface_obs.get("public_name_after_resume") != "changed-before-child:nested"
            or surface_obs.get("global_replacement") != "global-replacement"):
        raise ValueError("surface PHP state observations are missing or unexpected")
    child = _mapping(surface.get("child_observation"))
    root = _mapping(surface.get("root_observation"))
    if (child.get("public_name_before") != "changed-before-child"
            or child.get("public_name_after") != "changed-before-child:nested"
            or not _int_is(child.get("name_growth_bytes"), 7)
            or child.get("alias_same_object") is not True or child.get("setting_flag") is not True
            or root.get("global_name") != "global-replacement"):
        raise ValueError("surface child/root observations do not match fixture expectations")
    if (root.get("public_name_before") != "changed-before-child"
            or root.get("public_name_after") != "changed-before-child:nested"
            or not _int_is(root.get("name_growth_bytes"), 7)
            or root.get("setting_flag") is not True or root.get("alias_same_object") is not True
            or not _int_is(surface.get("frames_from_php"), 5)
            or not _int_is(surface.get("frames_from_rust"), 4)):
        raise ValueError("surface root or transport observations differ")
    trace = _mapping(throwable.get("php_observation"))
    expected_trace = ["root:enter", "nested:enter", "nested:transport_catch",
        "nested:finally", "child:rethrow", "root:catch", "root:finally"]
    if (trace.get("caught_same_object") is not True
            or not _int_is(trace.get("nested_callback_calls"), 1)
            or trace.get("throwable_class") != "RuntimeException"
            or trace.get("throwable_message") != "nested fixture throwable"
            or trace.get("trace") != expected_trace
            or not _int_is(throwable.get("frames_from_php"), 4)
            or not _int_is(throwable.get("frames_from_rust"), 4)):
        raise ValueError("throwable object identity, callback count, or trace differs")
    abort = _mapping(throwable.get("abort"))
    if (abort.get("child_recovered") is not True
            or not _int_is(abort.get("child_depth_before_abort"), 1)
            or not _int_is(abort.get("depth_after_abort"), 0)
            or abort.get("double_resume_rejected") is not True
            or abort.get("resume_after_abort_rejected") is not True
            or abort.get("finish_request_after_abort_rejected") is not True):
        raise ValueError("throwable abort/replay/depth checks did not pass")
    if (throwable.get("child_recovered_before_abort") is not True
            or throwable.get("resume_after_abort_rejected") is not True
            or throwable.get("double_resume_rejected") is not True):
        raise ValueError("throwable continuation safety summary differs")


def self_check() -> None:
    """Exercise validator rejection paths with deliberately mutated observations."""
    base = {
        "schema": SCHEMA, "execution": "passed", "compiler_profile": COMPILER_PROFILE,
        "artifact": {"bytes": ARTIFACT_BYTES, "sha256": ARTIFACT_SHA256},
        "fixtures": [[name, (WASM_DIR.parent / "fixtures" / name).stat().st_size,
            sha_file(WASM_DIR.parent / "fixtures" / name)] for name in HOST_FIXTURE_ORDER],
        "cases": {
            "surface": {"status": "passed", "php_exit_code": 0, "guest_completed": True,
                "thread_joined": True, "peer": {
                    "php_observation": {"same_object_alias": True,
                        "public_name_after_resume": "changed-before-child:nested",
                        "global_replacement": "global-replacement"},
                    "child_observation": {"public_name_before": "changed-before-child",
                        "public_name_after": "changed-before-child:nested", "name_growth_bytes": 7,
                        "alias_same_object": True,
                        "setting_flag": True},
                    "root_observation": {"public_name_before": "changed-before-child",
                        "public_name_after": "changed-before-child:nested", "global_name": "global-replacement",
                        "name_growth_bytes": 7,
                        "setting_flag": True, "alias_same_object": True},
                    "frames_from_php": 5, "frames_from_rust": 4}},
            "throwable": {"status": "passed", "php_exit_code": 0, "guest_completed": True,
                "thread_joined": True, "peer": {
                    "php_observation": {"caught_same_object": True,
                        "throwable_class": "RuntimeException", "throwable_message": "nested fixture throwable",
                        "nested_callback_calls": 1, "trace": ["root:enter", "nested:enter",
                            "nested:transport_catch", "nested:finally", "child:rethrow", "root:catch", "root:finally"]},
                    "abort": {"child_recovered": True, "child_depth_before_abort": 1,
                        "depth_after_abort": 0, "double_resume_rejected": True,
                        "resume_after_abort_rejected": True,
                        "finish_request_after_abort_rejected": True},
                    "frames_from_php": 4, "frames_from_rust": 4,
                    "child_recovered_before_abort": True,
                    "resume_after_abort_rejected": True, "double_resume_rejected": True}},
        },
    }
    validate_host_result(base)
    mutations = [
        lambda x: x["cases"].pop("throwable"),
        lambda x: x["cases"]["throwable"]["peer"]["php_observation"].update(nested_callback_calls=2),
        lambda x: x["cases"]["throwable"]["peer"]["php_observation"].update(nested_callback_calls=True),
        lambda x: x["cases"]["throwable"]["peer"]["abort"].update(resume_after_abort_rejected=False),
        lambda x: x["cases"]["surface"]["peer"]["php_observation"].update(same_object_alias=False),
        lambda x: x["cases"]["surface"]["peer"]["php_observation"].update(public_name_after_resume="invented"),
        lambda x: x["cases"]["surface"]["peer"]["child_observation"].update(name_growth_bytes=8),
        lambda x: x["cases"]["surface"]["peer"]["root_observation"].update(name_growth_bytes=6),
        lambda x: x["cases"]["surface"].update(guest_completed=False),
    ]
    for mutate in mutations:
        candidate = json.loads(json.dumps(base))
        mutate(candidate)
        try:
            validate_host_result(candidate)
        except (KeyError, ValueError, TypeError):
            continue
        raise AssertionError("mutated bridge observation was accepted")
    # A same-second failed run must outrank an earlier success even though
    # its status suffix would sort earlier in a naive filename ordering.
    earlier = {"observed_at_utc": "2026-11-10T10:20:30.100000Z", "status": "passed"}
    later = {"observed_at_utc": "2026-11-10T10:20:30.900000Z", "status": "failed"}
    chosen_path, chosen_report = _latest_by_observation([
        (Path("bridge-20261110T102030.100000Z-success.json"), earlier),
        (Path("bridge-20261110T102030.900000Z-failed.json"), later),
    ])
    if chosen_report is not later or "failed" not in chosen_path.name:
        raise AssertionError("latest receipt selection must retain a same-second failure")
    process_report = {"owned_processes": {"all_reaped": True,
        "build": {"returncode": 0, "timed_out": False, "output_limited": False, "failure": None, "reaped": True},
        "host": {"returncode": 0, "timed_out": False, "output_limited": False, "failure": None, "reaped": True}}}
    _validate_owned_processes(process_report)
    invalid_process = json.loads(json.dumps(process_report))
    invalid_process["owned_processes"]["build"]["returncode"] = 1
    try:
        _validate_owned_processes(invalid_process)
    except ValueError:
        pass
    else:
        raise AssertionError("failed build process record was accepted")
    good_binary = {"host_binary": {"path": "target/debug/bridge", "sha256": "abc",
        "sha256_before_host": "abc", "sha256_after_host": "abc", "unchanged_during_host": True}}
    _validate_binary_receipt(good_binary, "abc", "target/debug/bridge")
    bad_binary = json.loads(json.dumps(good_binary))
    bad_binary["host_binary"]["sha256_before_host"] = "old"
    try:
        _validate_binary_receipt(bad_binary, "abc", "target/debug/bridge")
    except ValueError:
        pass
    else:
        raise AssertionError("changed host binary receipt was accepted")


def _receipt_name(status: str, ended: datetime, failure: str | None) -> str:
    stamp = ended.strftime("%Y%m%dT%H%M%S.%fZ")
    suffix = "success" if status == "passed" else (failure or "failed")
    path = EVIDENCE / f"bridge-{stamp}-{suffix}.json"
    index = 1
    while path.exists():
        path = EVIDENCE / f"bridge-{stamp}-{suffix}-{index}.json"
        index += 1
    return path.name


def _latest_by_observation(entries):
    """Select newest receipt by timestamp, then immutable file write order."""
    def key(entry):
        path, report = entry
        stamp = datetime.fromisoformat(report["observed_at_utc"].replace("Z", "+00:00"))
        written = path.stat().st_mtime_ns if path.exists() else 0
        return stamp, written, path.name
    return max(entries, key=key)


def _validate_owned_processes(report: dict) -> None:
    processes = _mapping(report.get("owned_processes"))
    if processes.get("all_reaped") is not True:
        raise ValueError("bridge receipt does not confirm all owned children were reaped")
    for label in ("build", "host"):
        child = _mapping(processes.get(label))
        if (not _int_is(child.get("returncode"), 0)
                or child.get("timed_out") is not False
                or child.get("output_limited") is not False
                or child.get("failure") is not None
                or child.get("reaped") is not True):
            raise ValueError(f"bridge {label} process record is inconsistent with a successful run")


def _validate_binary_receipt(report: dict, actual_hash: str, expected_path: str) -> None:
    binary = _mapping(report.get("host_binary"))
    if (binary.get("path") != expected_path
            or binary.get("sha256") != actual_hash
            or binary.get("sha256_before_host") != actual_hash
            or binary.get("sha256_after_host") != actual_hash
            or binary.get("unchanged_during_host") is not True):
        raise ValueError("bridge receipt host binary identities are inconsistent")


def _save_private_capture(run_dir: Path, label: str, item) -> dict | None:
    if item is None:
        return None
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    captures = {}
    for stream in ("stdout", "stderr"):
        path = run_dir / f"{label}.{stream}.bin"
        path.write_bytes(item[stream])
        path.chmod(0o600)
        captures[stream] = relative(path)
    return captures


def verify_latest() -> int:
    receipts = sorted(EVIDENCE.glob("bridge-*.json"), key=lambda item: item.name)
    if not receipts:
        raise ValueError("no bridge receipt exists")
    entries = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in receipts]
    receipt_path, report = _latest_by_observation(entries)
    if report.get("status") != "passed" or report.get("schema") != "recastpress.php-compat-v06-wasm-bridge-run.v1":
        raise ValueError("latest bridge receipt is not a successful run")
    current = fixed_inputs()
    if report.get("source_bound_inputs_before") != current or report.get("source_bound_inputs_after") != current:
        raise ValueError("latest bridge receipt has stale source inputs")
    _validate_owned_processes(report)
    target = (ROOT / report.get("target_dir", "")).resolve()
    try:
        target.relative_to((ROOT / "target").resolve())
    except ValueError as error:
        raise ValueError("latest bridge receipt target is outside repository target/") from error
    binary_path = target / "debug" / HOST_NAME
    if not binary_path.is_file():
        raise ValueError("latest bridge receipt host binary is missing or stale")
    _validate_binary_receipt(report, sha_file(binary_path), relative(binary_path))
    validate_host_result(report.get("host_result"))
    print(f"bridge receipt verified: {relative(receipt_path)}")
    return 0


def _isolated_env(target: Path) -> dict:
    env = {"PATH": os.environ.get("PATH", ""), "CARGO_TARGET_DIR": str(target)}
    for key in ("RUSTUP_HOME", "CARGO_HOME", "RUSTUP_TOOLCHAIN", "CARGO_BUILD_JOBS", "CARGO_INCREMENTAL", "CARGO_PROFILE_DEV_DEBUG", "LD_LIBRARY_PATH"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_check()
        print("bridge result validator self-test passed")
        return 0
    if sys.argv[1:] == ["--verify-only"]:
        return verify_latest()
    if sys.argv[1:]:
        print("usage: python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py [--self-test|--verify-only]", file=sys.stderr)
        return 2
    if platform.system() != "Linux":
        print("bridge execution is Linux-only", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc)
    before = {}
    after = {}
    build = host = None
    host_receipt = None
    failure = None
    phase = "source_verification"
    binary_before = binary_after = None
    target = None
    capture_dir = None
    try:
        target = selected_target()
        capture_dir = target / ".bridge-runs" / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}-{uuid.uuid4().hex}"
        before = fixed_inputs()
        env = _isolated_env(target)
        target.mkdir(parents=True, exist_ok=True)
        build_command = ["cargo", "build", "--offline", "--locked", "--manifest-path",
            relative(MANIFEST), "--bin", HOST_NAME, "--target-dir", relative(target)]
        phase = "build"
        build = run_bounded(build_command, env, BUILD_TIMEOUT, "build")
        binary_path = target / "debug" / HOST_NAME
        if build["returncode"] != 0 or build["failure"]:
            failure = build["failure"] or "cargo_build_failed"
        elif not binary_path.is_file():
            failure = "host_binary_missing"
        else:
            phase = "host"
            binary_before = sha_file(binary_path)
            host = run_bounded([str(binary_path)], env, HOST_TIMEOUT, "host")
            binary_after = sha_file(binary_path)
            if host["returncode"] != 0 or host["failure"]:
                failure = host["failure"] or "host_failed"
            else:
                phase = "host_result_validation"
                wire = host["stdout"].decode("utf-8", errors="strict")
                if not wire.endswith("\n") or "\n" in wire[:-1] or "\r" in wire:
                    raise ValueError("host must emit exactly one newline terminated JSON record")
                host_receipt = json.loads(wire)
                validate_host_result(host_receipt)
                if binary_before != binary_after:
                    raise ValueError("host binary changed during execution")
        after = fixed_inputs()
        if before != after:
            failure = "source_bound_inputs_changed"
            phase = "source_bound_verification"
    except BaseException as error:
        failure = failure or f"{type(error).__name__}"
        if not after:
            try:
                after = fixed_inputs()
            except BaseException:
                after = {}

    if failure is None and any(item is None or not item["reaped"] for item in (build, host)):
        failure = "owned_child_reap_unconfirmed"
        phase = "process_reap_verification"

    ended = datetime.now(timezone.utc)
    status = "passed" if failure is None else "failed"
    binary = (target / "debug" / HOST_NAME) if target else (DEFAULT_TARGET / "debug" / HOST_NAME)
    binary_hash = sha_file(binary) if binary.is_file() else None
    captures = {}
    if capture_dir is not None:
        capture_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        captures = {label: _save_private_capture(capture_dir, label, item)
            for label, item in (("build", build), ("host", host))}
    def public_process(item):
        if item is None:
            return None
        return {key: item[key] for key in ("pid", "returncode", "timed_out", "output_limited", "reaped", "failure")}
    report = {
        "schema": "recastpress.php-compat-v06-wasm-bridge-run.v1",
        "status": status,
        "runtime": "PHP-WASIX 8.5.12 / Wasmer 7.2.0",
        "compiler_profile": COMPILER_PROFILE,
        "started_at_utc": started.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "observed_at_utc": ended.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "os": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "target_dir": relative(target) if target else None,
        "phase": phase,
        "failure_class": failure,
        "owned_processes": {"build": public_process(build), "host": public_process(host),
            "all_reaped": all(item is None or item["reaped"] for item in (build, host))},
        "source_bound_inputs_before": before,
        "source_bound_inputs_after": after,
        "source_bound_inputs_unchanged": bool(before and before == after),
        "host_binary": {"path": (relative(binary) if binary.is_file() else None),
            "sha256": binary_hash, "sha256_before_host": binary_before,
            "sha256_after_host": binary_after,
            "unchanged_during_host": binary_before is not None and binary_before == binary_after},
        "process_output": {
            label: (None if item is None else {
                "stdout_bytes": len(item["stdout"]), "stdout_sha256": sha_bytes(item["stdout"]),
                "stderr_bytes": len(item["stderr"]), "stderr_sha256": sha_bytes(item["stderr"]),
            }) for label, item in (("build", build), ("host", host))
        },
        "raw_capture": captures,
        "host_result": host_receipt,
        "build_timeout_seconds": BUILD_TIMEOUT,
        "host_timeout_seconds": HOST_TIMEOUT,
        "scope": "fixed PHP-WASIX callback bridge surface and throwable continuation only; no WordPress bootstrap, database, or product PHP runtime claim",
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    name = _receipt_name(status, ended, failure)
    path = EVIDENCE / name
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{status}: {relative(path)}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"bridge check failed: {error}")
