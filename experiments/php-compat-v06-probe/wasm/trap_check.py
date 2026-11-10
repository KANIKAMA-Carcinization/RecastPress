#!/usr/bin/env python3
"""Linux-only, source-bound Wasmer metered trap and owned CPU-loop probe."""
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

import bridge_check as bridge

WASM_DIR = Path(__file__).resolve().parent
ROOT = WASM_DIR.parents[2]
EVIDENCE = WASM_DIR / "evidence"
HOST_NAME = bridge.HOST_NAME
TRAP_FIXTURE = ROOT / "experiments" / "php-compat-v06-probe" / "fixtures" / "trap_recovery.php"
CPU_READY_TIMEOUT = 300
HOST_TIMEOUT = 300
BUILD_TIMEOUT = 600
READY_GRACE_SECONDS = 0.2
MAX_STDOUT = bridge.MAX_STDOUT
MAX_STDERR = bridge.MAX_STDERR
SCHEMA = "recastpress.php-compat-v06-wasm-trap.v1"
SIGKILL = getattr(signal, "SIGKILL", 9)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_paths() -> list[Path]:
    paths = bridge.source_paths() + [Path(__file__).resolve(), TRAP_FIXTURE]
    return sorted(set(path.resolve() for path in paths), key=bridge.relative)


def fixed_inputs() -> dict:
    if not TRAP_FIXTURE.is_file():
        raise FileNotFoundError("fixed metered trap fixture is missing")
    artifact = bridge.ARTIFACT
    if (not artifact.is_file() or artifact.stat().st_size != bridge.ARTIFACT_BYTES
            or sha_file(artifact) != bridge.ARTIFACT_SHA256):
        raise ValueError("fixed PHP-WASIX artifact identity differs")
    return {bridge.relative(path): {"bytes": path.stat().st_size, "sha256": sha_file(path)}
        for path in source_paths()}


def _kill_owned(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)
    if process.poll() is None:
        raise RuntimeError("owned trap probe child could not be reaped")


def _start(command: list[str], env: dict) -> subprocess.Popen:
    return subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, start_new_session=True)


def run_bounded(command: list[str], env: dict, timeout: int, label: str) -> dict:
    """Run and reap an owned process with independent bounded stdout/stderr."""
    started_at_utc = _utc_now()
    item = bridge.run_bounded(command, env, timeout, label)
    item["process_group"] = item["pid"]
    item["started_at_utc"] = started_at_utc
    item["ended_at_utc"] = _utc_now()
    return item


def run_cpu_loop(command: list[str], env: dict) -> dict:
    """Wait for the guest's ready checkpoint, prove it remains live, then kill its group."""
    process = _start(command, env)
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    output = {process.stdout: bytearray(), process.stderr: bytearray()}
    limits = {process.stdout: MAX_STDOUT, process.stderr: MAX_STDERR}
    for stream in output:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    started = time.monotonic()
    started_at_utc = _utc_now()
    ready = None
    ready_at = None
    cpu_ticks_before = cpu_ticks_after = None
    failure = None
    try:
        while ready is None and process.poll() is None:
            if time.monotonic() - started > CPU_READY_TIMEOUT:
                failure = "cpu_ready_timeout"
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
                    failure = "cpu_loop_output_limit"
                    break
                if stream is process.stdout and b"\n" in output[stream]:
                    first, _, extra = bytes(output[stream]).partition(b"\n")
                    if extra:
                        failure = "cpu_loop_unexpected_stdout_before_kill"
                    else:
                        try:
                            ready = json.loads(first.decode("utf-8", errors="strict"))
                            ready_at = time.monotonic()
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            failure = "cpu_ready_record_invalid"
                    break
            if failure:
                break
        if failure is None and ready is None:
            failure = "cpu_loop_exited_before_ready"
        if failure is None:
            try:
                _validate_ready(ready, process.pid)
            except ValueError:
                failure = "cpu_ready_record_mismatch"
        if failure is None:
            cpu_ticks_before = _cpu_ticks(process.pid)
            grace_until = time.monotonic() + READY_GRACE_SECONDS
            while time.monotonic() < grace_until:
                if process.poll() is not None:
                    failure = "cpu_loop_exited_after_ready"
                    break
                for key, _ in selector.select(timeout=min(0.02, max(0, grace_until-time.monotonic()))):
                    stream = key.fileobj
                    remaining = limits[stream] + 1 - len(output[stream])
                    chunk = os.read(stream.fileno(), min(65536, remaining))
                    if chunk and stream is process.stdout:
                        output[stream].extend(chunk)
                        failure = "cpu_loop_unexpected_output_after_ready"
                        break
                    if chunk:
                        output[stream].extend(chunk)
                        if len(output[stream]) > limits[stream]:
                            failure = "cpu_loop_output_limit"
                            break
                        continue
                    selector.unregister(stream)
                    stream.close()
                if failure:
                    break
        was_live_before_kill = process.poll() is None
        cpu_ticks_after = _cpu_ticks(process.pid) if was_live_before_kill else None
        ready_confirmed_for_ms = (int((time.monotonic() - ready_at) * 1000)
            if ready_at is not None else 0)
        if process.poll() is None:
            _kill_owned(process)
        # Drain output remaining after the owned process group has been killed.
        for stream in tuple(output):
            if stream.closed:
                continue
            while True:
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    break
                if not chunk:
                    if stream in selector.get_map() and selector.get_key(stream):
                        selector.unregister(stream)
                    stream.close()
                    break
                output[stream].extend(chunk)
                if len(output[stream]) > limits[stream]:
                    failure = failure or "cpu_loop_output_limit"
                    break
        return {"pid": process.pid, "process_group": process.pid,
            "ready": ready, "ready_checkpoint_valid": failure is None and ready is not None,
            "still_running_after_grace": was_live_before_kill,
            "ready_confirmed_for_ms": ready_confirmed_for_ms,
            "cpu_ticks_before": cpu_ticks_before, "cpu_ticks_after": cpu_ticks_after,
            "kill_signal": SIGKILL if was_live_before_kill else None,
            "returncode": process.returncode, "expected_signal_exit": process.returncode == -SIGKILL,
            "failure": failure, "timed_out": failure == "cpu_ready_timeout",
            "output_limited": failure == "cpu_loop_output_limit", "stdout": bytes(output[process.stdout]),
            "stderr": bytes(output[process.stderr]), "reaped": process.poll() is not None,
            "started_at_utc": started_at_utc, "ended_at_utc": _utc_now()}
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
        if process.poll() is None:
            _kill_owned(process)


def _validate_ready(value: dict, pid: int) -> None:
    """Validate the host's explicit guest-loop-entered readiness record."""
    if (not isinstance(value, dict)
            or value.get("schema") != "recastpress.php-compat-v06-wasm-cpu-loop.v1"
            or value.get("status") != "ready"
            or value.get("host_pid") != pid
            or value.get("callback_loop_entered") is not True
            or not _int_eq(value.get("compile_count"), 1)
            or not _int_eq(value.get("compile_elapsed_ms"), value.get("compile_elapsed_ms"))
            or value.get("compile_elapsed_ms", -1) < 0
            or value.get("fixtures") != _expected_trap_fixtures()):
        raise ValueError("CPU loop readiness checkpoint does not match the owned child")
    artifact = bridge._mapping(value.get("artifact"))
    if (artifact.get("bytes") != bridge.ARTIFACT_BYTES
            or artifact.get("sha256") != bridge.ARTIFACT_SHA256):
        raise ValueError("CPU loop readiness artifact identity differs")


def _cpu_ticks(pid: int) -> int | None:
    """Read Linux process user+system ticks after the comm field."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError:
        return None
    _, separator, tail = stat.rpartition(")")
    if not separator:
        return None
    fields = tail.strip().split()  # starts at field 3 (state); utime/stime are fields 14/15.
    if len(fields) < 13:
        return None
    try:
        return int(fields[11]) + int(fields[12])
    except ValueError:
        return None


def _validate_metered(value: dict) -> dict:
    """Validate metered normal/trap/normal on one compiled module."""
    result = bridge._mapping(value)
    artifact = bridge._mapping(result.get("artifact"))
    if (result.get("schema") != SCHEMA or result.get("execution") != "passed"
            or result.get("runtime") != "PHP-WASIX 8.5.12 / Wasmer 7.2.0"
            or result.get("compiler_profile") != bridge.COMPILER_PROFILE):
        raise ValueError("metered trap result header mismatch")
    if (artifact.get("bytes") != bridge.ARTIFACT_BYTES
            or artifact.get("sha256") != bridge.ARTIFACT_SHA256
            or result.get("fixtures") != _expected_fixtures()
            or result.get("trap_fixtures") != _expected_trap_fixtures()):
        raise ValueError("metered trap artifact or fixture identities differ")
    initial = bridge._mapping(result.get("configured_initial_points"))
    post_start = bridge._mapping(result.get("post_start_points"))
    final_points = bridge._mapping(result.get("final_points"))
    if (not _int_eq(result.get("compile_count"), 1)
            or not _int_eq(result.get("compile_elapsed_ms"), result.get("compile_elapsed_ms"))
            or initial.get("type") != "i64" or not _int_eq(initial.get("points"), 100_000_000)
            or post_start.get("kind") != "remaining" or post_start.get("type") != "i64"
            or type(post_start.get("points")) is not int
            or not 0 < post_start["points"] < initial["points"]
            or not _int_eq(post_start.get("exhausted_flag"), 0)
            or final_points.get("kind") != "exhausted" or final_points.get("type") != "i32"
            or not _int_eq(final_points.get("flag"), 1)
            or not _int_eq(result.get("metering_setup_count"), 1)
            or not _int_eq(result.get("metering_recycle_count"), 1)
            or result.get("metering_observation_error") is not None
            or result.get("budget_exhausted") is not True
            or result.get("cost_policy") != "one-point-per-original-Wasm-operator"
            or not _int_eq(result.get("budget_reset_count"), 0)
            or result.get("case_order") != ["normal", "trap", "recovery"]):
        raise ValueError("metered trap configuration, budget, or execution order differs")
    cases = bridge._mapping(result.get("cases"))
    if set(cases) != {"normal", "trap", "recovery"}:
        raise ValueError("metered trap cases are incomplete or unexpected")
    _validate_surface_case(cases["normal"])
    _validate_surface_case(cases["recovery"])
    trap = bridge._mapping(cases["trap"])
    continuation = bridge._mapping(trap.get("continuation"))
    if (trap.get("status") != "passed" or trap.get("callback_checkpoint") is not True
            or trap.get("typed_wasmer_trap") != "UnreachableCodeReached"
            or not _int_eq(trap.get("runner_exit_code"), 45)
            or trap.get("guest_thread_joined") is not True
            or trap.get("stderr_thread_joined") is not True
            or trap.get("stderr") != ""
            or not isinstance(trap.get("captured_runtime_traps"), list)
            or len(trap.get("captured_runtime_traps", [])) != 1
            or trap["captured_runtime_traps"] != ["Some(UnreachableCodeReached)"]
            or not isinstance(trap.get("runner_error"), str)
            or not trap.get("runner_error") or len(trap.get("runner_error").encode("utf-8")) > 65_536
            or trap.get("budget_exhausted") is not True
            or trap.get("final_points") != final_points
            or trap.get("post_start_points") != post_start
            or not _int_eq(trap.get("metering_setup_count"), 1)
            or not _int_eq(trap.get("metering_recycle_count"), 1)
            or continuation.get("finish_pending_rejected") is not True
            or not _int_eq(continuation.get("depth_after_abort"), 0)
            or continuation.get("late_resume_rejected") is not True):
        raise ValueError("metered trap type, joins, stderr, or continuation closure differs")
    return result


def _int_eq(value, expected: int) -> bool:
    return type(value) is int and value == expected


def _expected_fixtures() -> list:
    fixture_dir = WASM_DIR.parent / "fixtures"
    return [[name, (fixture_dir / name).stat().st_size, sha_file(fixture_dir / name)]
        for name in bridge.HOST_FIXTURE_ORDER]


def _expected_trap_fixtures() -> list:
    return [[TRAP_FIXTURE.name, TRAP_FIXTURE.stat().st_size, sha_file(TRAP_FIXTURE)]]


def _validate_surface_case(value) -> dict:
    case = bridge._mapping(value)
    if (case.get("status") != "passed" or not _int_eq(case.get("php_exit_code"), 0)
            or case.get("guest_completed") is not True or case.get("thread_joined") is not True):
        raise ValueError("normal or post-trap surface guest did not complete and join")
    peer = bridge._mapping(case.get("peer"))
    obs = bridge._mapping(peer.get("php_observation"))
    child = bridge._mapping(peer.get("child_observation"))
    root = bridge._mapping(peer.get("root_observation"))
    if (obs.get("same_object_alias") is not True
            or obs.get("public_name_after_resume") != "changed-before-child:nested"
            or obs.get("global_replacement") != "global-replacement"
            or child.get("public_name_before") != "changed-before-child"
            or child.get("public_name_after") != "changed-before-child:nested"
            or not _int_eq(child.get("name_growth_bytes"), 7)
            or child.get("alias_same_object") is not True or child.get("setting_flag") is not True
            or root.get("public_name_before") != "changed-before-child"
            or root.get("public_name_after") != "changed-before-child:nested"
            or root.get("global_name") != "global-replacement"
            or not _int_eq(root.get("name_growth_bytes"), 7)
            or root.get("setting_flag") is not True or root.get("alias_same_object") is not True
            or not _int_eq(peer.get("frames_from_php"), 5)
            or not _int_eq(peer.get("frames_from_rust"), 4)):
        raise ValueError("normal or post-trap semantic surface observations differ")
    return case


def self_check() -> None:
    """Check killed-process and readiness mutations without launching guests."""
    valid_ready = {"schema": "recastpress.php-compat-v06-wasm-cpu-loop.v1", "status": "ready",
        "host_pid": 100, "callback_loop_entered": True, "compile_count": 1,
        "compile_elapsed_ms": 1, "fixtures": _expected_trap_fixtures(),
        "artifact": {"bytes": bridge.ARTIFACT_BYTES, "sha256": bridge.ARTIFACT_SHA256}}
    _validate_ready(valid_ready, 100)
    for candidate, expected_pid in [
        ({**valid_ready, "callback_loop_entered": False}, 100),
        ({**valid_ready, "host_pid": 101}, 100),
        (valid_ready, 101),
    ]:
        try:
            _validate_ready(candidate, expected_pid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid CPU readiness checkpoint was accepted")
    _self_test_metered_mutations()
    valid_kill = {"returncode": -SIGKILL, "expected_signal_exit": True,
        "still_running_after_grace": True, "ready_checkpoint_valid": True, "reaped": True,
        "failure": None, "pid": 100, "process_group": 100, "kill_signal": SIGKILL,
        "timed_out": False, "output_limited": False, "ready_confirmed_for_ms": 200,
        "cpu_ticks_before": 100, "cpu_ticks_after": 120}
    _validate_kill_report(valid_kill)
    mutations = [
        {**valid_kill, "returncode": 0},
        {**valid_kill, "expected_signal_exit": False},
        {**valid_kill, "still_running_after_grace": False},
        {**valid_kill, "ready_checkpoint_valid": False},
        {**valid_kill, "reaped": False},
        {**valid_kill, "failure": "cpu_ready_timeout"},
        {**valid_kill, "process_group": 101},
        {**valid_kill, "kill_signal": getattr(signal, "SIGTERM", 15)},
        {**valid_kill, "timed_out": True},
        {**valid_kill, "output_limited": True},
        {**valid_kill, "ready_confirmed_for_ms": 0},
        {**valid_kill, "cpu_ticks_after": 100},
    ]
    for candidate in mutations:
        try:
            _validate_kill_report(candidate)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid CPU kill report was accepted")
    ordered = {"metered": {"ended_at_utc": "2026-11-10T00:00:01.000000Z"},
        "cpu_loop": {"started_at_utc": "2026-11-10T00:00:02.000000Z",
            "ended_at_utc": "2026-11-10T00:00:03.000000Z"},
        "recovery": {"started_at_utc": "2026-11-10T00:00:04.000000Z"}}
    _validate_order(ordered)
    bad_order = json.loads(json.dumps(ordered))
    bad_order["recovery"]["started_at_utc"] = "2026-11-10T00:00:02.500000Z"
    try:
        _validate_order(bad_order)
    except ValueError:
        pass
    else:
        raise AssertionError("recovery before CPU child reap was accepted")
    if platform.system() == "Linux":
        _self_test_owned_cpu_kill()
    earlier = {"observed_at_utc": "2026-11-10T10:20:30.100000Z", "status": "passed"}
    later_failure = {"observed_at_utc": "2026-11-10T10:20:30.900000Z", "status": "failed"}
    _, selected = bridge._latest_by_observation([
        (Path("trap-20261110T102030.100000Z-success.json"), earlier),
        (Path("trap-20261110T102030.900000Z-validation.json"), later_failure),
    ])
    if selected is not later_failure:
        raise AssertionError("newer failed trap receipt must block an earlier pass")


def _surface_for_selftest() -> dict:
    peer = {
        "php_observation": {"same_object_alias": True,
            "public_name_after_resume": "changed-before-child:nested",
            "global_replacement": "global-replacement"},
        "child_observation": {"public_name_before": "changed-before-child",
            "public_name_after": "changed-before-child:nested", "name_growth_bytes": 7,
            "alias_same_object": True, "setting_flag": True},
        "root_observation": {"public_name_before": "changed-before-child",
            "public_name_after": "changed-before-child:nested", "global_name": "global-replacement",
            "name_growth_bytes": 7, "setting_flag": True, "alias_same_object": True},
        "frames_from_php": 5, "frames_from_rust": 4}
    return {"status": "passed", "php_exit_code": 0, "guest_completed": True,
        "thread_joined": True, "peer_elapsed_ms": 1, "peer": peer}


def _self_test_metered_mutations() -> None:
    surface = _surface_for_selftest()
    final_points = {"kind": "exhausted", "type": "i32", "flag": 1}
    post_start = {"kind": "remaining", "type": "i64", "points": 99_999_000, "exhausted_flag": 0}
    trap = {"status": "passed", "callback_checkpoint": True,
        "typed_wasmer_trap": "UnreachableCodeReached", "runner_exit_code": 45,
        "captured_runtime_traps": ["Some(UnreachableCodeReached)"],
        "runner_error": "typed UnreachableCodeReached trap", "guest_thread_joined": True,
        "stderr_thread_joined": True, "stderr": "", "post_start_points": post_start,
        "final_points": final_points, "metering_setup_count": 1, "metering_recycle_count": 1,
        "budget_exhausted": True, "continuation": {"finish_pending_rejected": True,
            "depth_after_abort": 0, "late_resume_rejected": True}}
    base = {"schema": SCHEMA, "execution": "passed", "runtime": "PHP-WASIX 8.5.12 / Wasmer 7.2.0",
        "compiler_profile": bridge.COMPILER_PROFILE,
        "artifact": {"bytes": bridge.ARTIFACT_BYTES, "sha256": bridge.ARTIFACT_SHA256},
        "fixtures": _expected_fixtures(), "trap_fixtures": _expected_trap_fixtures(),
        "compile_count": 1, "compile_elapsed_ms": 1,
        "configured_initial_points": {"type": "i64", "points": 100_000_000},
        "post_start_points": post_start, "final_points": final_points,
        "metering_setup_count": 1, "metering_recycle_count": 1,
        "metering_observation_error": None, "budget_exhausted": True,
        "cost_policy": "one-point-per-original-Wasm-operator", "budget_reset_count": 0,
        "cases": {"normal": surface, "trap": trap, "recovery": surface},
        "case_order": ["normal", "trap", "recovery"]}
    _validate_metered(base)
    mutations = [
        lambda item: item["cases"]["trap"].update(typed_wasmer_trap="exit_0"),
        lambda item: item["cases"]["trap"].update(runner_exit_code=23),
        lambda item: item["cases"]["trap"].update(runner_exit_code=45.0),
        lambda item: item["cases"]["trap"].update(runner_exit_code=True),
        lambda item: item["cases"]["trap"].update(captured_runtime_traps=[]),
        lambda item: item["cases"]["trap"].update(status="passed_without_trap"),
        lambda item: item["final_points"].update(flag=0),
        lambda item: item["final_points"].update(type="i64"),
        lambda item: item["cases"]["trap"].update(budget_exhausted=False),
        lambda item: item.update(budget_reset_count=1),
        lambda item: item.pop("final_points"),
        lambda item: item.update(metering_observation_error="read failed"),
        lambda item: item["cases"]["trap"].update(guest_thread_joined=False),
        lambda item: item["cases"]["trap"].update(stderr_thread_joined=False),
        lambda item: item["cases"]["trap"]["continuation"].update(late_resume_rejected=False),
        lambda item: item.update(metering_setup_count=2),
        lambda item: item.update(case_order=["normal", "recovery", "trap"]),
        lambda item: item["cases"]["recovery"].update(guest_completed=False),
    ]
    for mutate in mutations:
        candidate = json.loads(json.dumps(base))
        mutate(candidate)
        try:
            _validate_metered(candidate)
        except (ValueError, TypeError, KeyError):
            continue
        raise AssertionError("mutated metered trap observation was accepted")


def _self_test_owned_cpu_kill() -> None:
    """Exercise the real owned-PGID kill/reap path with a tiny fake child."""
    base_ready = {"schema": "recastpress.php-compat-v06-wasm-cpu-loop.v1", "status": "ready",
        "callback_loop_entered": True, "compile_count": 1, "compile_elapsed_ms": 1,
        "fixtures": _expected_trap_fixtures(), "artifact": {"bytes": bridge.ARTIFACT_BYTES,
            "sha256": bridge.ARTIFACT_SHA256}}
    program = ("import json,os,sys; value=json.loads(sys.argv[1]); value['host_pid']=os.getpid(); "
        "print(json.dumps(value,separators=(',',':')),flush=True); exec('while True: pass')")
    for entered in (True, False):
        ready = dict(base_ready, callback_loop_entered=entered)
        item = run_cpu_loop([sys.executable, "-c", program, json.dumps(ready)], os.environ.copy())
        if item.get("returncode") != -SIGKILL or item.get("reaped") is not True:
            raise AssertionError("fake owned CPU child was not killed and reaped")
        if entered:
            _validate_kill_report(item)
        elif item.get("failure") != "cpu_ready_record_mismatch":
            raise AssertionError("false callback checkpoint was accepted")


def _validate_kill_report(report: dict) -> None:
    if (report.get("returncode") != -SIGKILL
            or type(report.get("pid")) is not int
            or report.get("process_group") != report.get("pid")
            or report.get("kill_signal") != SIGKILL
            or report.get("expected_signal_exit") is not True
            or report.get("still_running_after_grace") is not True
            or not _int_eq(report.get("ready_confirmed_for_ms"), report.get("ready_confirmed_for_ms"))
            or report.get("ready_confirmed_for_ms", 0) < 100
            or not _int_eq(report.get("cpu_ticks_before"), report.get("cpu_ticks_before"))
            or not _int_eq(report.get("cpu_ticks_after"), report.get("cpu_ticks_after"))
            or report.get("cpu_ticks_after", 0) <= report.get("cpu_ticks_before", 0)
            or report.get("ready_checkpoint_valid") is not True
            or report.get("reaped") is not True or report.get("failure") is not None
            or report.get("timed_out") is not False
            or report.get("output_limited") is not False):
        raise ValueError("CPU-loop process was not successfully killed and reaped")
    if (len(report.get("stdout", b"")) > MAX_STDOUT
            or len(report.get("stderr", b"")) > MAX_STDERR):
        raise ValueError("CPU-loop output exceeded fixed capture bounds")


def _validate_order(processes: dict) -> None:
    metered = bridge._mapping(processes.get("metered"))
    cpu = bridge._mapping(processes.get("cpu_loop"))
    recovery = bridge._mapping(processes.get("recovery"))
    metered_end = datetime.fromisoformat(metered["ended_at_utc"].replace("Z", "+00:00"))
    cpu_start = datetime.fromisoformat(cpu["started_at_utc"].replace("Z", "+00:00"))
    cpu_end = datetime.fromisoformat(cpu["ended_at_utc"].replace("Z", "+00:00"))
    recovery_start = datetime.fromisoformat(recovery["started_at_utc"].replace("Z", "+00:00"))
    if not (metered_end <= cpu_start < cpu_end <= recovery_start):
        raise ValueError("metered trial, owned CPU kill, and fresh recovery were out of order")


def _isolated_env(target: Path) -> dict:
    return bridge._isolated_env(target)


def _parse_single_json(item: dict, label: str) -> dict:
    wire = item["stdout"].decode("utf-8", errors="strict")
    if not wire.endswith("\n") or "\n" in wire[:-1] or "\r" in wire:
        raise ValueError(f"{label} must emit exactly one newline-terminated JSON record")
    value = json.loads(wire)
    if not isinstance(value, dict):
        raise ValueError(f"{label} JSON result must be an object")
    return value


def _validate_surface_recovery(value: dict) -> dict:
    result = bridge._mapping(value)
    if (result.get("schema") != "recastpress.php-compat-v06-wasm-surface-recovery.v1"
            or result.get("execution") != "passed"
            or result.get("runtime") != "PHP-WASIX 8.5.12 / Wasmer 7.2.0"
            or result.get("compiler_profile") != bridge.COMPILER_PROFILE
            or not _int_eq(result.get("module_compile_count"), 1)):
        raise ValueError("post-kill recovery host result header differs")
    artifact = bridge._mapping(result.get("artifact"))
    if (artifact.get("bytes") != bridge.ARTIFACT_BYTES
            or artifact.get("sha256") != bridge.ARTIFACT_SHA256
            or result.get("fixtures") != _expected_fixtures()):
        raise ValueError("post-kill recovery artifact or fixture identities differ")
    _validate_surface_case(result.get("surface"))
    return result


def _public_process(item: dict | None) -> dict | None:
    if item is None:
        return None
    keys = ("pid", "process_group", "returncode", "timed_out", "output_limited",
        "failure", "reaped", "started_at_utc", "ended_at_utc", "kill_signal",
        "expected_signal_exit", "ready_checkpoint_valid", "still_running_after_grace",
        "ready_confirmed_for_ms", "cpu_ticks_before", "cpu_ticks_after",
        "host_binary_before", "host_binary_after")
    return {key: item.get(key) for key in keys}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_successful_process(item: dict, label: str) -> None:
    if item["returncode"] != 0 or item["failure"] is not None or item["reaped"] is not True:
        raise ValueError(f"{label} owned process failed, timed out, or was not reaped")


def _save_capture(run_dir: Path, label: str, item: dict | None) -> dict | None:
    if item is None:
        return None
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    captures = {}
    for stream in ("stdout", "stderr"):
        path = run_dir / f"{label}.{stream}.bin"
        path.write_bytes(item[stream])
        path.chmod(0o600)
        captures[stream] = bridge.relative(path)
    return captures


def _receipt_name(status: str, ended: datetime, failure: str | None) -> str:
    stamp = ended.strftime("%Y%m%dT%H%M%S.%fZ")
    suffix = "success" if status == "passed" else (failure or "failed")
    path = EVIDENCE / f"trap-{stamp}-{suffix}.json"
    index = 1
    while path.exists():
        path = EVIDENCE / f"trap-{stamp}-{suffix}-{index}.json"
        index += 1
    return path.name


def _latest_by_observation(entries):
    def key(entry):
        path, report = entry
        stamp = datetime.fromisoformat(report["observed_at_utc"].replace("Z", "+00:00"))
        written = path.stat().st_mtime_ns if path.exists() else 0
        return stamp, written, path.name
    return max(entries, key=key)


def verify_latest() -> int:
    receipts = sorted(EVIDENCE.glob("trap-*.json"), key=lambda path: path.name)
    if not receipts:
        raise ValueError("no trap receipt exists")
    entries = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in receipts]
    receipt_path, report = _latest_by_observation(entries)
    if report.get("status") != "passed" or report.get("schema") != "recastpress.php-compat-v06-wasm-trap-run.v1":
        raise ValueError("latest trap receipt is not a successful run")
    current = fixed_inputs()
    if report.get("source_bound_inputs_before") != current or report.get("source_bound_inputs_after") != current:
        raise ValueError("latest trap receipt has stale source inputs")
    processes = bridge._mapping(report.get("owned_processes"))
    if processes.get("all_reaped") is not True:
        raise ValueError("latest trap receipt does not confirm every child was reaped")
    for name in ("build", "metered", "cpu_loop", "recovery"):
        child = bridge._mapping(processes.get(name))
        if (type(child.get("pid")) is not int or child["pid"] <= 0
                or child.get("process_group") != child.get("pid")
                or child.get("reaped") is not True):
            raise ValueError(f"latest trap receipt {name} child was not reaped")
        if name != "cpu_loop" and (child.get("returncode") != 0
                or child.get("timed_out") is not False
                or child.get("output_limited") is not False or child.get("failure") is not None):
            raise ValueError(f"latest trap receipt {name} child process record is inconsistent")
    _validate_kill_report(processes["cpu_loop"])
    _validate_ready(report.get("cpu_ready"), processes["cpu_loop"].get("pid"))
    _validate_order(processes)
    outputs = bridge._mapping(report.get("process_output"))
    for label in ("build", "metered", "cpu_loop", "recovery"):
        output = bridge._mapping(outputs.get(label))
        if (not _int_eq(output.get("stdout_bytes"), output.get("stdout_bytes"))
                or not _int_eq(output.get("stderr_bytes"), output.get("stderr_bytes"))
                or output["stdout_bytes"] > MAX_STDOUT or output["stderr_bytes"] > MAX_STDERR):
            raise ValueError(f"latest trap receipt {label} output exceeds fixed bounds")
    target = (ROOT / report.get("target_dir", "")).resolve()
    target.relative_to((ROOT / "target").resolve())
    binary = target / "debug" / HOST_NAME
    bridge._validate_binary_receipt(report, sha_file(binary), bridge.relative(binary))
    for label in ("metered", "cpu_loop", "recovery"):
        child = processes[label]
        if (child.get("host_binary_before") != sha_file(binary)
                or child.get("host_binary_after") != sha_file(binary)):
            raise ValueError(f"latest trap receipt binary changed during {label}")
    if processes["build"].get("host_binary_after") != sha_file(binary):
        raise ValueError("latest trap receipt build binary identity differs")
    _validate_metered(report.get("metered_result"))
    _validate_surface_recovery(report.get("recovery_result"))
    print(f"trap receipt verified: {bridge.relative(receipt_path)}")
    return 0


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_check()
        print("trap runner validator self-test passed")
        return 0
    if sys.argv[1:] == ["--verify-only"]:
        return verify_latest()
    if sys.argv[1:]:
        print("usage: python3 -B experiments/php-compat-v06-probe/wasm/trap_check.py [--self-test|--verify-only]", file=sys.stderr)
        return 2
    if platform.system() != "Linux":
        print("trap execution is Linux-only", file=sys.stderr)
        return 2
    started = datetime.now(timezone.utc)
    target = bridge.selected_target()
    run_dir = target / ".trap-runs" / f"{started.strftime('%Y%m%dT%H%M%S.%fZ')}-{uuid.uuid4().hex}"
    before = after = {}
    build = metered = cpu = recovery = None
    metered_result = recovery_result = None
    binary_before = binary_after = None
    phase = "source_verification"
    failure = None
    try:
        before = fixed_inputs()
        target.mkdir(parents=True, exist_ok=True)
        env = _isolated_env(target)
        build_command = ["cargo", "build", "--offline", "--locked", "--manifest-path",
            bridge.relative(bridge.MANIFEST), "--bin", HOST_NAME, "--target-dir", bridge.relative(target)]
        phase = "build"
        build = run_bounded(build_command, env, BUILD_TIMEOUT, "build")
        binary_path = target / "debug" / HOST_NAME
        if build["returncode"] != 0 or build["failure"] or not binary_path.is_file():
            raise ValueError("fixed trap host build failed or binary is missing")
        build["host_binary_before"] = None
        build["host_binary_after"] = sha_file(binary_path)
        command = [str(binary_path), "--metered-trap"]
        phase = "metered_trap"
        binary_before = sha_file(binary_path)
        metered = run_bounded(command, env, HOST_TIMEOUT, "metered")
        binary_after = sha_file(binary_path)
        metered["host_binary_before"] = binary_before
        metered["host_binary_after"] = binary_after
        _require_successful_process(metered, "metered")
        metered_result = _validate_metered(_parse_single_json(metered, "metered"))
        if binary_before != binary_after:
            raise ValueError("host binary changed during metered trap execution")
        phase = "cpu_loop"
        cpu_binary_before = sha_file(binary_path)
        cpu = run_cpu_loop([str(binary_path), "--cpu-loop"], env)
        cpu_binary_after = sha_file(binary_path)
        cpu["host_binary_before"] = cpu_binary_before
        cpu["host_binary_after"] = cpu_binary_after
        if cpu_binary_before != cpu_binary_after:
            raise ValueError("host binary changed during CPU-loop execution")
        _validate_kill_report(cpu)
        phase = "post_kill_recovery"
        binary_before = sha_file(binary_path)
        recovery = run_bounded([str(binary_path), "--surface-recovery"], env, HOST_TIMEOUT, "recovery")
        _validate_order({"metered": metered, "cpu_loop": cpu, "recovery": recovery})
        binary_after = sha_file(binary_path)
        recovery["host_binary_before"] = binary_before
        recovery["host_binary_after"] = binary_after
        _require_successful_process(recovery, "recovery")
        recovery_result = _validate_surface_recovery(_parse_single_json(recovery, "recovery"))
        if binary_before != binary_after:
            raise ValueError("host binary changed during post-kill recovery")
        after = fixed_inputs()
        if before != after:
            raise ValueError("source-bound inputs changed during trap trial")
    except BaseException as error:
        failure = failure or type(error).__name__
        if not after:
            try:
                after = fixed_inputs()
            except BaseException:
                after = {}
    ended = datetime.now(timezone.utc)
    status = "passed" if failure is None else "failed"
    binary_path = target / "debug" / HOST_NAME
    binary_hash = sha_file(binary_path) if binary_path.is_file() else None
    captures = {label: _save_capture(run_dir, label, item)
        for label, item in (("build", build), ("metered", metered), ("cpu_loop", cpu), ("recovery", recovery))}
    all_reaped = all(item is None or item.get("reaped") is True for item in (build, metered, cpu, recovery))
    report = {
        "schema": "recastpress.php-compat-v06-wasm-trap-run.v1", "status": status,
        "runtime": "PHP-WASIX 8.5.12 / Wasmer 7.2.0", "compiler_profile": bridge.COMPILER_PROFILE,
        "started_at_utc": started.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "observed_at_utc": ended.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "os": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "target_dir": bridge.relative(target), "phase": phase, "failure_class": failure,
        "source_bound_inputs_before": before, "source_bound_inputs_after": after,
        "source_bound_inputs_unchanged": bool(before and before == after),
        "owned_processes": {"build": _public_process(build), "metered": _public_process(metered),
            "cpu_loop": _public_process(cpu), "recovery": _public_process(recovery), "all_reaped": all_reaped},
        "host_binary": {"path": bridge.relative(binary_path) if binary_path.is_file() else None,
            "sha256": binary_hash, "sha256_before_host": binary_before,
            "sha256_after_host": binary_after,
            "unchanged_during_host": binary_before is not None and binary_before == binary_after},
        "process_output": {label: (None if item is None else {
            "stdout_bytes": len(item["stdout"]), "stdout_sha256": sha_bytes(item["stdout"]),
            "stderr_bytes": len(item["stderr"]), "stderr_sha256": sha_bytes(item["stderr"]),
        }) for label, item in (("build", build), ("metered", metered), ("cpu_loop", cpu), ("recovery", recovery))},
        "raw_capture": captures, "metered_result": metered_result, "cpu_ready": cpu.get("ready") if cpu else None,
        "recovery_result": recovery_result, "build_timeout_seconds": BUILD_TIMEOUT,
        "host_timeout_seconds": HOST_TIMEOUT, "cpu_ready_timeout_seconds": CPU_READY_TIMEOUT,
        "cpu_ready_grace_seconds": READY_GRACE_SECONDS,
        "scope": "fixed Wasmer metered PHP trap and externally reaped unmetered callback loop; no product CPU-time limit claim",
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    receipt = EVIDENCE / _receipt_name(status, ended, failure)
    receipt.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{status}: {bridge.relative(receipt)}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"trap check failed: {error}")
