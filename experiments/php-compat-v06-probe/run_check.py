#!/usr/bin/env python3
"""Run the native PHP ↔ Rust continuation probe with bounded, owned children.

This is an isolated development experiment. It does not exercise PHP-Wasm.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from publish_contract import validate_publish_result
from handle_contract import SCENARIOS as HANDLE_SCENARIOS, validate_handle_result

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
PHP_ROOT = ROOT / ".reference" / "php-8.5.12"
PHP = PHP_ROOT / "php.exe"
MANIFEST = PHP_ROOT / "recastpress-tool.json"
EXAMPLE = ROOT / "crates" / "recastpress-wp" / "examples" / "v06_bridge_probe.rs"
PEER_MODULE = ROOT / "crates" / "recastpress-wp" / "examples" / "v06_bridge_peer" / "mod.rs"
RECEIPT = HERE / "evidence" / "native-continuation-v1.json"
MAX_FRAME = 1_048_576
MAX_TOTAL = 33_554_432
TIMEOUT = 40
MAX_STDERR = 1_048_576
PHP_ARCHIVE_SHA256 = "2ab242af38d76bdf417cde5ec048b1bcd76a5a40f39a3d15d5f046f38571c477"
CHILD_RUNS: list[dict] = []
NEGATIVE_INVENTORY = ["correlation", "handle", "sequence", "snapshot", "duplicate_key", "decoded_depth"]
PHP_NEGATIVE_INVENTORY = ["duplicate_key", "escaped_duplicate_key"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def source_files() -> dict[str, str]:
    paths = [
        Path(__file__), EXAMPLE, PEER_MODULE,
        HERE / "README.ja.md",
        ROOT / "Cargo.toml", ROOT / "Cargo.lock",
        ROOT / "crates" / "recastpress-wp" / "Cargo.toml",
        ROOT / "crates" / "recastpress-wp" / "src" / "lib.rs",
        ROOT / "crates" / "recastpress-wp" / "src" / "continuation.rs",
        ROOT / "crates" / "recastpress-wp" / "src" / "public_state.rs",
        ROOT / "crates" / "recastpress-wp" / "tests" / "continuation_contract.rs",
        ROOT / "crates" / "recastpress-wp" / "tests" / "public_state_contract.rs",
        HERE / "publish_contract.py", FIXTURES / "publish_driver.php",
        HERE / "handle_contract.py", FIXTURES / "handle_registry.php", FIXTURES / "handle_driver.php",
        ROOT / "scripts" / "owned_probe.py",
        FIXTURES / "bridge_driver.php", FIXTURES / "dispatcher.php",
        FIXTURES / "surface_oracle.php", FIXTURES / "CONTRACT.md",
        FIXTURES / "strict_json.php", FIXTURES / "strict_json_oracle.php",
        MANIFEST,
    ]
    paths.extend(sorted(PEER_MODULE.parent.glob("*.rs")))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for entry in manifest.get("files", []):
        paths.append(PHP_ROOT / entry["path"])
    return {p.relative_to(ROOT).as_posix(): sha256(p) for p in paths}


def check_php_manifest() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("php_version") != "8.5.12" or manifest.get("archive_sha256") != PHP_ARCHIVE_SHA256:
        raise RuntimeError("fixed PHP manifest version mismatch")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("fixed PHP manifest has no file inventory")
    for entry in files:
        path = (PHP_ROOT / entry["path"]).resolve()
        if not path.is_relative_to(PHP_ROOT.resolve()):
            raise RuntimeError("PHP manifest path escaped the fixed distribution root")
        if not path.is_file() or sha256(path) != entry.get("sha256"):
            raise RuntimeError(f"PHP distribution file does not match fixed manifest: {entry['path']}")
    if not any(f.get("path") == "php.exe" for f in files):
        raise RuntimeError("fixed PHP manifest omits php.exe")
    return manifest


def captured(command: list[str], timeout: int = TIMEOUT, input_bytes: bytes | None = None):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("MYSQL_")}
    child = subprocess.Popen(command, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                             env=env, creationflags=flags)
    cancelled = threading.Event()
    output_lock = threading.Lock()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    failures: list[BaseException] = []
    too_much = threading.Event()
    workers = []

    def reader(stream, name):
        try:
            while not cancelled.is_set():
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    cancelled.wait(0.01)
                    continue
                if not chunk:
                    return
                with output_lock:
                    if sum(map(len, buffers.values())) + len(chunk) <= MAX_STDERR:
                        buffers[name].extend(chunk)
                    else:
                        too_much.set()
        except BaseException as exc:
            failures.append(exc)

    def writer():
        stream = child.stdin
        try:
            view = memoryview(input_bytes or b"")
            while view and not cancelled.is_set():
                try:
                    written = os.write(stream.fileno(), view[:65536])
                except BlockingIOError:
                    cancelled.wait(0.01)
                    continue
                except BrokenPipeError:
                    return
                if written <= 0:
                    raise OSError("captured input writer made no progress")
                view = view[written:]
        except BaseException as exc:
            failures.append(exc)
        finally:
            try:
                stream.close()
            except OSError:
                pass

    original = None
    reaped = False
    try:
        for stream in (child.stdout, child.stderr):
            os.set_blocking(stream.fileno(), False)
        if child.stdin is not None:
            os.set_blocking(child.stdin.fileno(), False)
            worker = threading.Thread(target=writer, name=f"v06-capture-input-{child.pid}", daemon=True)
            worker.start(); workers.append(worker)
        for stream, name in ((child.stdout, "stdout"), (child.stderr, "stderr")):
            worker = threading.Thread(target=reader, args=(stream, name), name=f"v06-capture-{name}-{child.pid}", daemon=True)
            worker.start(); workers.append(worker)
        deadline = time.monotonic() + timeout
        while child.poll() is None:
            if too_much.is_set():
                raise RuntimeError("captured child exceeded combined 1 MiB stdout/stderr limit")
            if failures:
                raise RuntimeError(f"captured child pipe failed: {failures[0]}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"captured child {child.pid} exceeded {timeout}s")
            time.sleep(min(0.02, remaining))
        code = child.wait(timeout=5)
        reaped = True
        for worker in workers:
            worker.join(timeout=2)
        if any(worker.is_alive() for worker in workers):
            raise RuntimeError(f"captured child {child.pid} pipe worker did not stop")
        if too_much.is_set():
            raise RuntimeError("captured child exceeded combined 1 MiB stdout/stderr limit")
        if failures:
            raise RuntimeError(f"captured child pipe failed: {failures[0]}")
        return subprocess.CompletedProcess(command, code, bytes(buffers["stdout"]), bytes(buffers["stderr"]))
    except BaseException as exc:
        original = exc
        raise
    finally:
        cancelled.set()
        if child.poll() is None:
            child.kill()
        try:
            child.wait(timeout=5)
            reaped = True
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"owned capture child {child.pid} could not be reaped") from exc
        for worker in workers:
            worker.join(timeout=2)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()
        if not reaped:
            raise RuntimeError(f"owned capture child {child.pid} has no confirmed reap")
        CHILD_RUNS.append({"pid": child.pid, "returncode": child.returncode, "reaped": True})


def build_adapter() -> Path:
    target_dir = ROOT / "target" / "php-compat-v06-probe" / "cargo-target"
    result = captured(["cargo", "build", "--offline", "--locked", "--target-dir", str(target_dir), "-p", "recastpress-wp", "--example", "v06_bridge_probe", "--no-default-features"])
    if result.returncode:
        raise RuntimeError("Rust adapter build failed:\n" + result.stderr.decode("utf-8", "replace")[-6000:])
    executable = target_dir / "debug" / "examples" / "v06_bridge_probe.exe"
    if not executable.is_file():
        raise RuntimeError(f"built Rust adapter is missing: {executable}")
    return executable


def php_one_shot() -> dict:
    request = {"v": 1, "request": "fixture-1", "seq": 0, "kind": "probe", "case": "surface"}
    result = captured([str(PHP), "-n", "-d", "display_errors=0", str(FIXTURES / "dispatcher.php")], input_bytes=(json.dumps(request, separators=(",", ":")) + "\n").encode())
    if result.returncode != 0:
        raise RuntimeError("PHP one-shot fixture failed: " + result.stderr.decode("utf-8", "replace"))
    frame = json.loads(result.stdout)
    if frame.get("kind") != "probe_result" or frame.get("ok") is not True:
        raise RuntimeError("PHP one-shot returned an invalid result")
    oracle = captured([str(PHP), "-n", "-d", "display_errors=0", str(FIXTURES / "surface_oracle.php")])
    if oracle.returncode != 0 or json.loads(oracle.stdout).get("oracle") != "passed":
        raise RuntimeError("independent PHP surface oracle failed: " + oracle.stderr.decode("utf-8", "replace"))
    strict_oracle = captured([str(PHP), "-n", "-d", "display_errors=0", str(FIXTURES / "strict_json_oracle.php")])
    if strict_oracle.returncode != 0 or json.loads(strict_oracle.stdout).get("checks") != 11:
        raise RuntimeError("strict PHP JSON oracle failed: " + strict_oracle.stderr.decode("utf-8", "replace"))
    return {"surface_result": frame["result"], "oracle": json.loads(oracle.stdout), "strict_oracle": json.loads(strict_oracle.stdout)}


def _reader(stream, label, events, cancelled):
    def emit(event):
        while not cancelled.is_set():
            try:
                events.put(event, timeout=0.1)
                return
            except queue.Full:
                continue
    try:
        while not cancelled.is_set():
            try:
                chunk = os.read(stream.fileno(), 65536)
            except BlockingIOError:
                cancelled.wait(0.01)
                continue
            if not chunk:
                emit((label, None))
                return
            emit((label, chunk))
    except BaseException as exc:
        emit((label, exc))


def run_bridge(executable: Path, scenario: str = "surface") -> dict:
    if scenario not in ("surface", "throwable", "publish", *HANDLE_SCENARIOS):
        raise ValueError("unknown fixed bridge scenario")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("MYSQL_")}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    fixture_name = "publish_driver.php" if scenario == "publish" else "bridge_driver.php"
    if scenario in HANDLE_SCENARIOS:
        fixture_name = "handle_driver.php"
    php_command = [str(PHP), "-n", "-d", "display_errors=0", str(FIXTURES / fixture_name)]
    if scenario != "surface":
        php_command.append(scenario)
    php = subprocess.Popen(php_command,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          bufsize=0, env=env, creationflags=flags)
    procs = {"php": php}
    events: queue.Queue = queue.Queue(maxsize=16)
    cancelled = threading.Event()
    workers = []
    stderr_bytes = {"php": bytearray(), "rust": bytearray()}
    buffers = {"php": bytearray(), "rust": bytearray()}
    ended = set()
    total = 0
    sent_lines = {"php": 0, "rust": 0}
    transcript_observations = {"rust_child": None, "rust_root": None, "rust_throwable_return": None, "rust_abort": None, "php_final": None}
    deadline = time.monotonic() + TIMEOUT

    def close_write(child):
        try:
            if child.stdin and not child.stdin.closed:
                child.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    try:
        # Once PHP is owned by this try/finally, even a failed Rust spawn is reaped.
        rust_command = [str(executable)] + ([scenario] if scenario != "surface" else [])
        rust = subprocess.Popen(rust_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, bufsize=0, env=env, creationflags=flags)
        procs["rust"] = rust
        for label, child in procs.items():
            for stream, stream_label in ((child.stdout, label), (child.stderr, label + "_stderr")):
                os.set_blocking(stream.fileno(), False)
                worker = threading.Thread(target=_reader, args=(stream, stream_label, events, cancelled), daemon=True)
                worker.start()
                workers.append(worker)
        for child in procs.values():
            os.set_blocking(child.stdin.fileno(), False)

        while len(ended) < 4:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("PHP/Rust bridge deadline exceeded")
            try:
                label, data = events.get(timeout=min(0.2, remaining))
            except queue.Empty:
                for name, child in procs.items():
                    code = child.poll()
                    if code not in (None, 0) and name + "_stderr" in ended:
                        diagnostic = stderr_bytes[name].decode("utf-8", "replace")
                        raise RuntimeError(f"{name} exited {code}; stderr={diagnostic}")
                continue
            if data is None:
                ended.add(label)
                continue
            if isinstance(data, BaseException):
                raise RuntimeError(f"{label} reader failed: {data}")
            if label.endswith("_stderr"):
                key = label[:-7]
                stderr_bytes[key].extend(data)
                if len(stderr_bytes[key]) > MAX_STDERR:
                    raise RuntimeError(f"{key} stderr exceeded 1 MiB")
                continue
            total += len(data)
            if total > MAX_TOTAL:
                raise RuntimeError("combined bridge bytes exceeded 32 MiB")
            buffers[label].extend(data)
            if len(buffers[label]) > MAX_FRAME and b"\n" not in buffers[label]:
                raise RuntimeError(f"{label} produced an over-limit unterminated frame")
            while b"\n" in buffers[label]:
                line, rest = buffers[label].split(b"\n", 1)
                line += b"\n"
                buffers[label] = bytearray(rest)
                if len(buffers[label]) > MAX_FRAME and b"\n" not in buffers[label]:
                    raise RuntimeError(f"{label} produced an over-limit unterminated frame")
                if len(line) > MAX_FRAME:
                    raise RuntimeError(f"{label} frame exceeded 1 MiB")
                try:
                    parsed = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"{label} emitted invalid JSON frame") from exc
                if label == "rust" and parsed.get("kind") == "return":
                    operation = parsed.get("payload", {}).get("operation")
                    if operation == "observe_public":
                        transcript_observations["rust_child"] = parsed.get("payload", {}).get("observation")
                    elif operation == "surface_probe":
                        transcript_observations["rust_root"] = parsed.get("payload", {}).get("observation")
                    elif operation == "raise_nested":
                        transcript_observations["rust_throwable_return"] = parsed.get("payload")
                if label == "rust" and parsed.get("kind") == "abort":
                    transcript_observations["rust_abort"] = parsed.get("payload")
                if label == "php" and parsed.get("kind") == "request_finished":
                    transcript_observations["php_final"] = parsed.get("payload", {}).get("php_observation")
                target_name = "rust" if label == "php" else "php"
                target = procs[target_name].stdin
                view = memoryview(line)
                while view:
                    try:
                        written = os.write(target.fileno(), view)
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("bridge write deadline exceeded")
                        time.sleep(0.001)
                        continue
                    if written <= 0:
                        raise BrokenPipeError(f"{target_name} stopped accepting protocol frames")
                    view = view[written:]
                sent_lines[label] += 1
                # The fixture's final RequestFinished has no response; let both exit.
                if label == "php" and parsed.get("kind") == "request_finished":
                    close_write(php)
                    close_write(rust)
        if any(buffers.values()):
            raise RuntimeError("partial bytes remained at stdout EOF")
        for name, child in procs.items():
            code = child.wait(timeout=max(0.1, deadline - time.monotonic()))
            if code != 0:
                raise RuntimeError(f"{name} exited {code}; stderr={stderr_bytes[name].decode('utf-8', 'replace')}")
        rust_err = stderr_bytes["rust"].decode("utf-8", "replace")
        marker = {
            "surface": "V06_BRIDGE_OK continuation_peer=true",
            "throwable": "V06_THROWABLE_OK continuation_peer=true",
            "publish": "V06_PUBLISH_OK continuation_peer=true",
            "handles": "V06_HANDLES_OK continuation_peer=true",
            "handles_abort": "V06_HANDLES_OK continuation_peer=true",
        }[scenario]
        if marker not in rust_err:
            raise RuntimeError("Rust adapter did not emit its success marker")
        if stderr_bytes["php"].strip():
            raise RuntimeError("PHP emitted diagnostics: " + stderr_bytes["php"].decode("utf-8", "replace"))
        final_observation = transcript_observations["php_final"] or {}
        publish_result = None
        handle_result = None
        if scenario in HANDLE_SCENARIOS:
            results = [line.removeprefix("V06_HANDLES_RESULT ") for line in rust_err.splitlines()
                       if line.startswith("V06_HANDLES_RESULT ")]
            if len(results) != 1:
                raise RuntimeError("handle peer must emit one observed result")
            handle_result = json.loads(results[0])
            validate_handle_result(handle_result, scenario)
            if final_observation != handle_result["php_observation"]:
                raise RuntimeError("handle peer result does not match the actual PHP final frame")
        elif scenario == "publish":
            results = [line.removeprefix("V06_PUBLISH_RESULT ") for line in rust_err.splitlines()
                       if line.startswith("V06_PUBLISH_RESULT ")]
            if len(results) != 1:
                raise RuntimeError("publish peer must emit one observed result")
            publish_result = json.loads(results[0])
            validate_publish_result(publish_result)
            if final_observation != publish_result["php_observation"]:
                raise RuntimeError("publish peer result does not match the actual PHP final frame")
        elif scenario == "throwable":
            expected_completion = {"kind": "php_throwable", "handle": "php-throwable-1", "class": "RuntimeException", "message": "nested fixture throwable"}
            expected_return = {"operation": "raise_nested", "completion": expected_completion}
            expected_abort = {"reason": "php_throwable", "handle": "php-throwable-1", "child_recovered": True, "child_depth_before_abort": 1, "depth_after_abort": 0, "double_resume_rejected": True, "resume_after_abort_rejected": True, "finish_request_after_abort_rejected": True}
            expected_trace = ["root:enter", "nested:enter", "nested:transport_catch", "nested:finally", "child:rethrow", "root:catch", "root:finally"]
            if transcript_observations["rust_throwable_return"] != expected_return or transcript_observations["rust_abort"] != expected_abort:
                raise RuntimeError("Rust throwable return/abort observations mismatch")
            if final_observation != {"caught_same_object": True, "throwable_class": "RuntimeException", "throwable_message": "nested fixture throwable", "trace": expected_trace, "nested_callback_calls": 1, "abort_reason": "php_throwable", "child_recovered": True, "child_depth_before_abort": 1, "depth_after_abort": 0, "double_resume_rejected": True, "resume_after_abort_rejected": True, "finish_request_after_abort_rejected": True}:
                raise RuntimeError("PHP did not preserve and rethrow the same Throwable through child Return/Abort")
        else:
            expected_child = {"public_name_before": "changed-before-child", "public_name_after": "changed-before-child:nested", "name_growth_bytes": 7, "setting_flag": True, "alias_same_object": True}
            expected_root = {**expected_child, "global_name": "global-replacement"}
            if transcript_observations["rust_child"] != expected_child or transcript_observations["rust_root"] != expected_root:
                raise RuntimeError("Rust observations did not match the independently expected live PHP values")
            if final_observation.get("rust_child_observation") != expected_child or final_observation.get("rust_root_observation") != expected_root:
                raise RuntimeError("PHP fixture did not confirm Rust observations against its live heap")
        expected_php_frames, expected_rust_frames = {
            "surface": (5, 4), "throwable": (4, 4), "publish": (11, 10),
            "handles": (5, 4), "handles_abort": (5, 4),
        }[scenario]
        if sent_lines != {"php": expected_php_frames, "rust": expected_rust_frames}:
            raise RuntimeError(f"{scenario} bridge frame counts differ from the closed transcript")
        result = {"processes": {name: child.pid for name, child in procs.items()},
                "returncodes": {name: child.returncode for name, child in procs.items()},
                "scenario": scenario, "rust_marker": marker,
                "frames_from_php": sent_lines["php"], "frames_from_rust": sent_lines["rust"],
                "relayed_bytes": total, "all_reaped": all(child.poll() is not None for child in procs.values()),
                "observations": transcript_observations,
                "expected_frame_counts": {"php": expected_php_frames, "rust": expected_rust_frames}}
        if publish_result is not None:
            result["publish_result"] = publish_result
        if handle_result is not None:
            result["handle_result"] = handle_result
        return result
    finally:
        cancelled.set()
        for child in procs.values():
            close_write(child)
        for child in reversed(list(procs.values())):
            if child.poll() is None:
                child.kill()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"owned child {child.pid} could not be reaped") from exc
        for worker in workers:
            worker.join(timeout=2)
        for child in procs.values():
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream:
                    stream.close()
        if any(worker.is_alive() for worker in workers):
            raise RuntimeError("owned-child output worker did not stop")


def negative_checks(executable: Path) -> list[str]:
    """Feed malformed PHP messages to the actual Rust peer and require rejection."""
    cases = []
    start = {"v": 1, "request": "surface-probe-1", "seq": 0, "kind": "start_root", "payload": {"operation": "surface_probe"}}
    call = {"v": 1, "request": "surface-probe-1", "seq": 1, "kind": "call_core", "parent_effect": "e-root",
            "payload": {"operation": "observe_public", "view": {"public_name": "changed-before-child", "setting_flag": True, "alias_same_object": True, "global_replaced": True}}}
    bad_corr = dict(call, parent_effect="e-other")
    bad_seq = dict(call, seq=2)
    bad_snapshot = json.loads(json.dumps(call)); bad_snapshot["payload"]["view"]["public_name"] = "stale-snapshot"
    bad_handle = json.loads(json.dumps(call)); bad_handle["payload"]["php_handle"] = "rp1.7.2.reference.invalid"
    too_deep = json.loads(json.dumps(start))
    nested = "0"
    for _ in range(64):
        nested = "[" + nested + "]"
    too_deep["payload"]["unknown"] = json.loads(nested)
    vectors = [
        ("correlation", json.dumps(start, separators=(",", ":")) + "\n" + json.dumps(bad_corr, separators=(",", ":")) + "\n", b"parent_effect_correlation"),
        ("handle", json.dumps(start, separators=(",", ":")) + "\n" + json.dumps(bad_handle, separators=(",", ":")) + "\n", b"call_core_payload_schema"),
        ("sequence", json.dumps(start, separators=(",", ":")) + "\n" + json.dumps(bad_seq, separators=(",", ":")) + "\n", b"frame_sequence"),
        ("snapshot", json.dumps(start, separators=(",", ":")) + "\n" + json.dumps(bad_snapshot, separators=(",", ":")) + "\n", b"public_snapshot_mismatch"),
        ("duplicate_key", '{"v":1,"request":"surface-probe-1","request":"surface-probe-1","seq":0,"kind":"start_root","payload":{"operation":"surface_probe"}}\n', b"duplicate JSON key"),
        ("decoded_depth", json.dumps(too_deep, separators=(",", ":")) + "\n", b"decoded_depth_limit"),
    ]
    for name, transcript, error_token in vectors:
        result = captured([str(executable)], timeout=10, input_bytes=transcript.encode("utf-8"))
        if result.returncode == 0 or error_token not in result.stderr:
            raise RuntimeError(f"negative {name} vector was not rejected by Rust peer")
        cases.append(name)
    return cases


def php_negative_checks() -> list[str]:
    malformed = [
        ("duplicate_key", b'{"v":1,"request":"fixture-1","request":"fixture-1","seq":0,"kind":"probe","case":"surface"}\n'),
        ("escaped_duplicate_key", b'{"v":1,"request":"fixture-1","re\\u0071uest":"fixture-1","seq":0,"kind":"probe","case":"surface"}\n'),
    ]
    passed = []
    for name, wire in malformed:
        result = captured([str(PHP), "-n", "-d", "display_errors=0", str(FIXTURES / "dispatcher.php")], timeout=10, input_bytes=wire)
        if result.returncode == 0:
            raise RuntimeError(f"PHP strict decoder accepted negative {name} input")
        passed.append(name)
    return passed


def make_receipt(one_shot: dict, bridge: dict, throwable_bridge: dict | None, negatives: list[str], executable_sha256: str, source_snapshot: dict[str, str], publish_bridge: dict | None = None, handle_bridges: dict | None = None) -> dict:
    manifest = check_php_manifest()
    handle_bridges = handle_bridges or {}
    version = captured(["rustc", "--version"])
    php_version = captured([str(PHP), "-n", "-v"])
    return {
        "schema": "recastpress.native-php-continuation-probe.v1",
        "status": "passed",
        "evidence_scope": "native PHP 8.5.12 JSONL ↔ Rust in-memory continuation state machine; not WordPress and not PHP-Wasm",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_sha256": source_snapshot,
        "php_distribution": {"version": manifest["php_version"], "manifest_sha256": sha256(MANIFEST), "manifest_file_count_verified": len(manifest["files"]), "version_output": php_version.stdout.decode("utf-8", "replace").splitlines()[0]},
        "environment": {"python": sys.version.splitlines()[0], "platform": platform.platform(), "architecture": platform.machine(), "working_directory": "."},
        "rust": {"rustc": version.stdout.decode("utf-8", "replace").strip(), "cargo_profile": "debug", "features": "--no-default-features"},
        "adapter_binary_sha256": executable_sha256,
        "limits": {"max_frame_bytes": MAX_FRAME, "max_total_bytes": MAX_TOTAL, "timeout_seconds": TIMEOUT, "max_captured_output_bytes_per_child": MAX_STDERR},
        "one_shot": {"oracle": one_shot["oracle"], "strict_json_oracle": one_shot["strict_oracle"], "selected_independent_php_values": {"same_object_alias": one_shot["surface_result"]["same_object_alias"], "array_reference_cell_after_callback": one_shot["surface_result"]["array_reference_cell_after_callback"], "byte_value_hex": one_shot["surface_result"]["byte_value_hex"]}},
        "interactive_bridge": bridge,
        "throwable_abort_bridge": throwable_bridge,
        "public_state_publish_bridge": publish_bridge,
        "handle_registry_bridges": handle_bridges,
        "negative_vectors_rejected_by_rust": negatives,
        "negative_vector_inventory": NEGATIVE_INVENTORY,
        "php_negative_vectors_rejected": PHP_NEGATIVE_INVENTORY,
        "owned_children": {
            "spawned_and_reaped": len(CHILD_RUNS) + (4 if throwable_bridge else 2) + (2 if publish_bridge else 0) + 2 * len(handle_bridges),
            "all_reaped": all(run["reaped"] for run in CHILD_RUNS) and bridge["all_reaped"]
                and (throwable_bridge is None or throwable_bridge["all_reaped"])
                and (publish_bridge is None or publish_bridge["all_reaped"])
                and all(item["all_reaped"] for item in handle_bridges.values()),
            "captured_children": CHILD_RUNS,
            "bridge_children": {
                "surface": bridge["returncodes"],
                "throwable": throwable_bridge["returncodes"] if throwable_bridge else None,
                "publish": publish_bridge["returncodes"] if publish_bridge else None,
                **{name: item["returncodes"] for name, item in handle_bridges.items()},
            },
        },
        "wasm": {"status": "not_run_by_this_runner", "reason": "This runner executes native PHP only. Fixed-artifact WASIX one-shot and interactive bridge results are recorded separately under wasm/."},
        "known_limits": ["The PHP fixture is synthetic and does not bootstrap WordPress.", "The throwable scenario retains the actual Throwable only in a request-local PHP table; its string handle maps to a local typed Rust handle and is not a cross-process PHP heap pointer or production authorization token.", "No native PHP↔Wasm re-entry, Zend bridge, DB, hooks, Query, or production worker transport was exercised."],
    }


def verify_receipt() -> None:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("schema") != "recastpress.native-php-continuation-probe.v1" or receipt.get("status") != "passed":
        raise RuntimeError("receipt schema/status invalid")
    manifest = check_php_manifest()
    current = source_files()
    if receipt.get("source_sha256") != current:
        differences = sorted(set(receipt.get("source_sha256", {})) | set(current))
        changed = [p for p in differences if receipt.get("source_sha256", {}).get(p) != current.get(p)]
        raise RuntimeError("source-bound receipt mismatch: " + ", ".join(changed))
    if receipt.get("php_distribution", {}).get("manifest_file_count_verified") != len(manifest["files"]):
        raise RuntimeError("receipt PHP manifest verification count mismatch")
    if receipt.get("negative_vector_inventory") != NEGATIVE_INVENTORY or receipt.get("negative_vectors_rejected_by_rust") != NEGATIVE_INVENTORY:
        raise RuntimeError("receipt negative-test inventory/result mismatch")
    if receipt.get("php_negative_vectors_rejected") != PHP_NEGATIVE_INVENTORY:
        raise RuntimeError("receipt PHP duplicate-key negative-test inventory mismatch")
    bridge = receipt.get("interactive_bridge", {})
    if bridge.get("returncodes") != {"php": 0, "rust": 0} or bridge.get("frames_from_php") != 5 or bridge.get("frames_from_rust") != 4 or not bridge.get("all_reaped"):
        raise RuntimeError("receipt bridge completion/reap expectations mismatch")
    throwable = receipt.get("throwable_abort_bridge", {})
    if not throwable or throwable.get("scenario") != "throwable" or throwable.get("returncodes") != {"php": 0, "rust": 0} or throwable.get("frames_from_php") != 4 or throwable.get("frames_from_rust") != 4 or not throwable.get("all_reaped"):
        raise RuntimeError("receipt nested throwable/abort process result mismatch")
    publication = receipt.get("public_state_publish_bridge", {})
    if (publication.get("scenario") != "publish"
            or publication.get("returncodes") != {"php": 0, "rust": 0}
            or publication.get("frames_from_php") != 11
            or publication.get("frames_from_rust") != 10
            or publication.get("all_reaped") is not True):
        raise RuntimeError("receipt public-state publish completion/reap mismatch")
    validate_publish_result(publication.get("publish_result"))
    if publication.get("observations", {}).get("php_final") != publication["publish_result"]["php_observation"]:
        raise RuntimeError("receipt publish result does not match observed PHP final frame")
    handle_bridges = receipt.get("handle_registry_bridges", {})
    if set(handle_bridges) != set(HANDLE_SCENARIOS):
        raise RuntimeError("receipt handle registry scenarios differ")
    for scenario, item in handle_bridges.items():
        if (item.get("scenario") != scenario or item.get("returncodes") != {"php": 0, "rust": 0}
                or item.get("frames_from_php") != 5 or item.get("frames_from_rust") != 4
                or item.get("all_reaped") is not True):
            raise RuntimeError("receipt handle registry completion/reap mismatch")
        validate_handle_result(item.get("handle_result"), scenario)
        if item.get("observations", {}).get("php_final") != item["handle_result"]["php_observation"]:
            raise RuntimeError("receipt handle result does not match observed PHP final frame")
    failure_observations = throwable.get("observations", {})
    expected_failure_return = {"operation": "raise_nested", "completion": {"kind": "php_throwable", "handle": "php-throwable-1", "class": "RuntimeException", "message": "nested fixture throwable"}}
    expected_abort = {"reason": "php_throwable", "handle": "php-throwable-1", "child_recovered": True, "child_depth_before_abort": 1, "depth_after_abort": 0, "double_resume_rejected": True, "resume_after_abort_rejected": True, "finish_request_after_abort_rejected": True}
    expected_trace = ["root:enter", "nested:enter", "nested:transport_catch", "nested:finally", "child:rethrow", "root:catch", "root:finally"]
    if failure_observations.get("rust_throwable_return") != expected_failure_return or failure_observations.get("rust_abort") != expected_abort:
        raise RuntimeError("receipt Rust throwable/abort observations mismatch")
    if failure_observations.get("php_final") != {"caught_same_object": True, "throwable_class": "RuntimeException", "throwable_message": "nested fixture throwable", "trace": expected_trace, "nested_callback_calls": 1, "abort_reason": "php_throwable", "child_recovered": True, "child_depth_before_abort": 1, "depth_after_abort": 0, "double_resume_rejected": True, "resume_after_abort_rejected": True, "finish_request_after_abort_rejected": True}:
        raise RuntimeError("receipt PHP did not confirm same-object throwable propagation and cleanup")
    expected_child = {"public_name_before": "changed-before-child", "public_name_after": "changed-before-child:nested", "name_growth_bytes": 7, "setting_flag": True, "alias_same_object": True}
    expected_root = {**expected_child, "global_name": "global-replacement"}
    observations = bridge.get("observations", {})
    if observations.get("rust_child") != expected_child or observations.get("rust_root") != expected_root:
        raise RuntimeError("receipt does not contain expected Rust derived observations")
    php_final = observations.get("php_final", {})
    if php_final.get("rust_child_observation") != expected_child or php_final.get("rust_root_observation") != expected_root:
        raise RuntimeError("receipt PHP heap oracle did not confirm Rust observations")
    children = receipt.get("owned_children", {})
    if not children.get("all_reaped") or children.get("spawned_and_reaped") != len(children.get("captured_children", [])) + 10:
        raise RuntimeError("receipt owned-child reap counts are inconsistent")
    if receipt.get("one_shot", {}).get("oracle") != {"oracle": "passed", "checks": 15} or receipt.get("one_shot", {}).get("strict_json_oracle", {}).get("checks") != 11:
        raise RuntimeError("receipt independent PHP oracle result mismatch")
    executable = ROOT / "target" / "php-compat-v06-probe" / "cargo-target" / "debug" / "examples" / "v06_bridge_probe.exe"
    if not executable.is_file() or sha256(executable) != receipt.get("adapter_binary_sha256"):
        raise RuntimeError("receipt adapter binary hash mismatch or missing build artifact")
    if receipt.get("wasm", {}).get("status") != "not_run_by_this_runner":
        raise RuntimeError("native-only receipt must not claim Wasm execution")
    print("source-bound receipt verified")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run positive bridge plus malformed-frame rejection checks")
    parser.add_argument("--record", action="store_true", help="run probe and write a source-bound receipt")
    parser.add_argument("--verify-only", action="store_true", help="verify the existing receipt against current source hashes")
    args = parser.parse_args()
    if args.verify_only:
        verify_receipt()
        return 0
    manifest = check_php_manifest()
    source_snapshot = source_files()
    executable = build_adapter()
    binary_snapshot = sha256(executable)
    one_shot = php_one_shot()
    bridge = run_bridge(executable)
    throwable_bridge = run_bridge(executable, "throwable") if args.self_test or args.record else None
    publish_bridge = run_bridge(executable, "publish") if args.self_test or args.record else None
    handle_bridges = {scenario: run_bridge(executable, scenario) for scenario in HANDLE_SCENARIOS} if args.self_test or args.record else {}
    negatives = negative_checks(executable) if args.self_test or args.record else []
    php_negatives = php_negative_checks() if args.self_test or args.record else []
    # Freeze every source and the just-built artifact across execution before persisting evidence.
    if source_files() != source_snapshot:
        raise RuntimeError("source tree changed while the probe was running")
    check_php_manifest()
    if sha256(executable) != binary_snapshot:
        raise RuntimeError("Rust adapter binary changed while the probe was running")
    report = make_receipt(one_shot, bridge, throwable_bridge, negatives, binary_snapshot, source_snapshot, publish_bridge, handle_bridges)
    report["php_negative_vectors_rejected"] = php_negatives
    if args.record:
        if source_files() != source_snapshot:
            raise RuntimeError("source tree changed before receipt write")
        check_php_manifest()
        if sha256(executable) != binary_snapshot:
            raise RuntimeError("Rust adapter binary changed before receipt write")
        RECEIPT.parent.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "bridge": bridge, "throwable_abort_bridge": throwable_bridge, "public_state_publish_bridge": publish_bridge, "handle_registry_bridges": handle_bridges, "oracle": one_shot["oracle"], "strict_json_oracle": one_shot["strict_oracle"], "rust_negative_vectors": negatives, "php_negative_vectors": php_negatives, "receipt": str(RECEIPT) if args.record else None}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"run_check.py: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
