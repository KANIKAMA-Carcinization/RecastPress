"""Build, execute, and source-bind the actual PHP-WASIX hook dispatch run."""

from collections.abc import Callable
from dataclasses import dataclass
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
RUN_TIMEOUT = 300
CHECK_STARTED_AT = datetime.now(timezone.utc)
SOURCE_INPUTS_AT_START = None
COMPLETED_SCENARIOS = {}
INPUTS = [
    ".github/workflows/v06-wp-hook-dispatch.yml",
    "experiments/php-wasix-wp-hook-dispatch-probe/Cargo.toml",
    "experiments/php-wasix-wp-hook-dispatch-probe/Cargo.lock",
    "experiments/php-wasix-wp-hook-dispatch-probe/check.py",
    "experiments/php-wasix-wp-hook-dispatch-probe/check_overlay.py",
    *(
        path.relative_to(ROOT).as_posix()
        for path in sorted((HERE / "src").rglob("*.rs"))
    ),
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_hook_driver.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_driver.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_ref_array_driver.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_ref_array_oracle.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_overlarge_accepted_args_oracle.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_throwable_driver.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_throwable_oracle.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_nested_driver.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_nested_oracle.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_mutation_driver.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_mutation_oracle.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_removal_driver.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_removal_oracle.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_remove_all_driver.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_remove_all_oracle.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_same_priority_driver.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_same_priority_oracle.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_deferred_add_oracle.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/fixtures/wp_ref_array_oracle.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/prepare_php.py",
    "experiments/php-wasix-wp-hook-dispatch-probe/prepare_overlay.py",
    "experiments/php-wasix-wp-hook-dispatch-probe/README.runtime.ja.md",
    "scripts/wordpress_reference.py",
    "crates/recastpress-wp/Cargo.toml",
    "experiments/php-wasix-wp-hook-dispatch-probe/evidence/overlay-v5/overlay.json",
    "experiments/php-wasix-wp-hook-dispatch-probe/evidence/overlay-v5/wp-includes/plugin.php",
    "experiments/php-wasix-wp-hook-dispatch-probe/evidence/overlay-v5/wp-includes/class-wp-hook.php",
    ".reference/wordpress-7.1.3/wp-includes/class-wp-filter-sentinel.php",
    "experiments/php-runtime-candidate-probe/artifact.json",
    "crates/recastpress-wp/src/dispatcher.rs",
    "crates/recastpress-wp/src/hooks.rs",
    "crates/recastpress-wp/src/continuation.rs",
    "experiments/php-compat-v06-probe/fixtures/bridge_driver.php",
    "experiments/php-compat-v06-probe/fixtures/strict_json.php",
    "experiments/php-compat-v06-probe/fixtures/handle_registry.php",
    ".reference/wordpress-7.1.3/wp-includes/plugin.php",
    ".reference/wordpress-7.1.3/wp-includes/class-wp-hook.php",
]
INPUTS.extend(
    path.relative_to(ROOT).as_posix()
    for path in sorted((ROOT / "crates/recastpress-wp/src").rglob("*.rs"))
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref_array_contract_matches(native_result, wasm_observation):
    native_observation = native_result["observation"]
    guest = wasm_observation.get("guest", {})
    actual_events = []
    for event in guest.get("events", []):
        if event.get("callback") == "all":
            keys = ("callback", "hook", "args", "current_filter", "doing_filter", "did_filter")
        else:
            keys = ("callback", "args", "current_filter", "doing_filter", "did_filter", "current_priority")
            if event.get("callback") in ("truncated", "zero"):
                keys += ("received_arg_count",)
        actual_events.append({key: event.get(key) for key in keys})
    return (
        actual_events == native_observation.get("events")
        and wasm_observation.get("result") == native_observation.get("result")
        and guest.get("caller_value") == native_observation.get("caller_value")
        and guest.get("caller_extra") == native_observation.get("caller_extra")
        and guest.get("filter_count") == native_observation.get("did_filter")
        and guest.get("current_filter") == native_observation.get("current_filter")
        and guest.get("doing_filter") == native_observation.get("doing_filter")
    )


def action_ref_array_contract_matches(native_result, wasm_observation):
    native_observation = native_result["observation"]
    guest = wasm_observation.get("guest", {})
    actual = {
        "events": guest.get("events"),
        "result": wasm_observation.get("result"),
        "caller_value": guest.get("caller_value"),
        "caller_extra": guest.get("caller_extra"),
        "action_count": guest.get("action_count"),
        "current_action": guest.get("current_action"),
        "doing_action": guest.get("doing_action"),
        "wp_hook_doing_action": guest.get("wp_hook_doing_action"),
        "current_priority": guest.get("current_priority"),
    }
    expected = {
        "events": native_observation.get("events"),
        "result": native_observation.get("result"),
        "caller_value": native_observation.get("caller_value"),
        "caller_extra": native_observation.get("caller_extra"),
        "action_count": native_observation.get("did_action"),
        "current_action": native_observation.get("current_action"),
        "doing_action": native_observation.get("doing_action"),
        "wp_hook_doing_action": native_observation.get("wp_hook_doing_action"),
        "current_priority": native_observation.get("current_priority"),
    }
    return actual == expected


def action_throwable_contract_matches(native_result, wasm_observation):
    native_observation = native_result["observation"]
    guest = wasm_observation.get("guest", {})
    return (
        guest.get("observation") == native_observation
        and guest.get("live_handles") == 0
        and guest.get("live_handles_before_close") == 0
        and guest.get("registry_closed") is True
        and wasm_observation.get("terminal") == "php_throwable_rethrown_and_caught_at_request_boundary"
        and wasm_observation.get("throwable") == {
            "class": "RuntimeException",
            "message": "action-throwable-fixture",
        }
        and wasm_observation.get("callbacks_released") == 2
    )


def accepted_args_match(native_result, wasm_observation):
    return wasm_observation.get("accepted_args_overlarge") == (
        native_result.get("observation", {}).get("received_args")
    )


def guest_observation_matches(native_result, wasm_observation):
    return wasm_observation.get("guest", {}).get("observation") == native_result.get("observation")


@dataclass(frozen=True)
class NativeOracle:
    fixture: str
    expected_scenario: str
    label: str
    matches: Callable[[dict, dict], bool]
    evidence_key: str | None = None
    pass_scenario_arg: bool = False
    match_key: str = "native_oracle_contract_match"
    mismatch_subject: str = "observation"
    stop_on_mismatch: bool = True


ACTION_REF_ARRAY_FIXTURE = "wp_action_ref_array_oracle.php"
# Native preflight order is independent of the WASIX execution order.
NATIVE_ORACLES = {
    "ref-array": NativeOracle(
        "wp_ref_array_oracle.php",
        "wp_ref_array_native_oracle",
        "apply_filters_ref_array",
        ref_array_contract_matches,
        evidence_key="reference_oracle",
    ),
    "action-ref-array": NativeOracle(
        ACTION_REF_ARRAY_FIXTURE,
        "wp_action_ref_array_native_oracle",
        "do_action_ref_array",
        action_ref_array_contract_matches,
        evidence_key="action_reference_oracle",
    ),
    "action-ref-array-empty-bucket": NativeOracle(
        ACTION_REF_ARRAY_FIXTURE,
        "wp_action_ref_array_empty_bucket_native_oracle",
        "empty-bucket action",
        action_ref_array_contract_matches,
        pass_scenario_arg=True,
    ),
    "action-ref-array-empty-bucket-reinsert": NativeOracle(
        ACTION_REF_ARRAY_FIXTURE,
        "wp_action_ref_array_empty_bucket_reinsert_native_oracle",
        "empty-bucket reinsert",
        action_ref_array_contract_matches,
        pass_scenario_arg=True,
    ),
    "action-ref-array-empty-bucket-add-action": NativeOracle(
        ACTION_REF_ARRAY_FIXTURE,
        "wp_action_ref_array_empty_bucket_add_action_native_oracle",
        "empty-bucket add_action",
        action_ref_array_contract_matches,
        pass_scenario_arg=True,
    ),
    "action-ref-array-global-replace": NativeOracle(
        ACTION_REF_ARRAY_FIXTURE,
        "wp_action_ref_array_global_replace_native_oracle",
        "global-replacement action",
        action_ref_array_contract_matches,
        pass_scenario_arg=True,
    ),
    "action-ref-array-global-replace-second-dispatch": NativeOracle(
        ACTION_REF_ARRAY_FIXTURE,
        "wp_action_ref_array_global_replace_second_dispatch_native_oracle",
        "global-replacement second-dispatch",
        action_ref_array_contract_matches,
        pass_scenario_arg=True,
    ),
    "action": NativeOracle(
        "wp_action_overlarge_accepted_args_oracle.php",
        "wp_action_overlarge_accepted_args_native_oracle",
        "overlarge accepted_args",
        accepted_args_match,
        evidence_key="action_overlarge_accepted_args_reference_oracle",
        match_key="native_oracle_accepted_args_match",
        mismatch_subject="accepted_args observation",
        stop_on_mismatch=False,
    ),
    "action-throwable": NativeOracle(
        "wp_action_throwable_oracle.php",
        "wp_action_throwable_native_oracle",
        "action Throwable",
        action_throwable_contract_matches,
        evidence_key="action_throwable_reference_oracle",
    ),
    "action-nested": NativeOracle(
        "wp_action_nested_oracle.php",
        "wp_action_nested_native_oracle",
        "nested action",
        guest_observation_matches,
        evidence_key="action_nested_reference_oracle",
    ),
    "action-mutation": NativeOracle(
        "wp_action_mutation_oracle.php",
        "wp_action_mutation_native_oracle",
        "action mutation",
        guest_observation_matches,
        evidence_key="action_mutation_reference_oracle",
    ),
    "action-removal": NativeOracle(
        "wp_action_removal_oracle.php",
        "wp_action_removal_native_oracle",
        "action removal",
        guest_observation_matches,
        evidence_key="action_removal_reference_oracle",
    ),
    "action-remove-all": NativeOracle(
        "wp_action_remove_all_oracle.php",
        "wp_action_remove_all_native_oracle",
        "action remove-all",
        guest_observation_matches,
        evidence_key="action_remove_all_reference_oracle",
    ),
    "action-deferred-add": NativeOracle(
        "wp_action_deferred_add_oracle.php",
        "wp_action_deferred_add_native_oracle",
        "same-priority action",
        guest_observation_matches,
        evidence_key="action_same_priority_reference_oracle",
    ),
}
FIRST_WASIX_SCENARIOS = ("filter", "ref-array", "action")
WASIX_SCENARIOS = (
    *FIRST_WASIX_SCENARIOS,
    *(scenario for scenario in NATIVE_ORACLES if scenario not in FIRST_WASIX_SCENARIOS),
)


def run_native_oracle(scenario, oracle):
    command = [
        "php", "-n", str(HERE / "fixtures" / oracle.fixture), str(ROOT / ".reference/wordpress-7.1.3"),
    ]
    if oracle.pass_scenario_arg:
        command.append(scenario)
    run = subprocess.run(
        command,
        cwd=ROOT,
        env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if run.returncode != 0 or run.stderr:
        raise SystemExit(
            f"pinned WordPress {oracle.label} oracle failed:\n"
            + run.stderr.decode("utf-8", "replace")[-8192:]
        )
    result = json.loads(run.stdout.decode("utf-8", "strict"))
    if result.get("status") != "passed" or result.get("scenario") != oracle.expected_scenario:
        raise SystemExit(f"pinned WordPress {oracle.label} oracle reported an unexpected result")
    return result


def main():
    global SOURCE_INPUTS_AT_START, COMPLETED_SCENARIOS
    if platform.system() != "Linux":
        raise SystemExit("Real PHP-WASIX execution is supported only on the fixed Linux CI runner.")
    if not ARTIFACT.is_file() or ARTIFACT.stat().st_size != ARTIFACT_BYTES or digest(ARTIFACT) != ARTIFACT_SHA256:
        raise SystemExit("Pinned PHP-WASIX artifact is missing or changed.")
    if digest(ROOT / ".reference/wordpress-7.1.3/wp-includes/class-wp-filter-sentinel.php") != SENTINEL_SHA256:
        raise SystemExit("Pinned WordPress filter sentinel changed.")
    before = {name: digest(ROOT / name) for name in INPUTS}
    SOURCE_INPUTS_AT_START = before
    native_results = {
        scenario: run_native_oracle(scenario, oracle)
        for scenario, oracle in NATIVE_ORACLES.items()
    }
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
    result = {}
    run_code = None
    run_ms = None
    if build.returncode == 0:
        binary = target / "debug/recastpress-wp-hook-dispatch-probe"
        run_ms = 0
        outputs = []
        run_codes = {}
        status = "passed"
        for scenario in WASIX_SCENARIOS:
            args = [] if scenario == "filter" else [scenario]
            t0 = time.monotonic()
            run = subprocess.run([str(binary), *args], cwd=ROOT, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=RUN_TIMEOUT, check=False)
            duration_ms = int((time.monotonic() - t0) * 1000)
            run_ms += duration_ms
            run_code = run.returncode
            run_codes[scenario] = run_code
            stdout = run.stdout.decode("utf-8", "replace")
            stderr = run.stderr.decode("utf-8", "replace")
            if len(run.stdout) > 2 * 1024 * 1024 or len(run.stderr) > 2 * 1024 * 1024:
                raise SystemExit("PHP-WASIX run output exceeded the fixed evidence capture bound.")
            if run.returncode == 0:
                scenario_result = json.loads(stdout)
                if scenario_result.get("status") != "passed" or scenario_result.get("scenario") != scenario:
                    raise SystemExit(f"{scenario} runner reported a non-passing or mismatched result")
                oracle = NATIVE_ORACLES.get(scenario)
                if oracle is not None:
                    scenario_result[oracle.match_key] = oracle.matches(
                        native_results[scenario], scenario_result.get("observation", {})
                    )
                result[scenario] = scenario_result
                COMPLETED_SCENARIOS = result.copy()
                outputs.append(f"--- {scenario} stdout ---\n{stdout}")
                if oracle is not None and not scenario_result[oracle.match_key]:
                    status = "oracle_mismatch"
                    message = (
                        f"PHP-WASIX {scenario} {oracle.mismatch_subject} "
                        "differs from its pinned native WordPress oracle."
                    )
                    outputs.append(message + "\n")
                    print(message, file=sys.stderr)
                    if oracle.stop_on_mismatch:
                        break
            else:
                status = "runtime_failed"
                outputs.append(f"--- {scenario} stdout ---\n{stdout}\n--- {scenario} stderr ---\n{stderr}")
                diagnostic = outputs[-1][-8192:]
                print(f"PHP-WASIX {scenario} runner failed; bounded diagnostic follows:", file=sys.stderr)
                print(diagnostic, file=sys.stderr)
                break
        output = "\n".join(outputs)
    after = {name: digest(ROOT / name) for name in INPUTS}
    unchanged = before == after
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    capture = f"run-{stamp}.log"
    (EVIDENCE / capture).write_text(output, encoding="utf-8", newline="\n")
    evidence = {
        "schema": "recastpress.php-wasix-wp-hook-dispatch.v2",
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
        "run": {"exit_code": run_code, "exit_codes": run_codes if build.returncode == 0 else {},
                "duration_ms": run_ms, "timeout_seconds_per_scenario": RUN_TIMEOUT, "result": result},
        **{
            oracle.evidence_key: native_results[scenario]
            for scenario, oracle in NATIVE_ORACLES.items()
            if oracle.evidence_key is not None
        },
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
        stdout = error.stdout or b""
        stderr = error.stderr or b""
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8", "replace")
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8", "replace")
        stdout = stdout[-1024 * 1024 :]
        stderr = stderr[-1024 * 1024 :]
        raw = b"--- stdout ---\n" + stdout + b"\n--- stderr ---\n" + stderr
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        capture = f"run-{stamp}-timeout.log"
        (EVIDENCE / capture).write_bytes(raw)
        report = {
            "schema": "recastpress.php-wasix-wp-hook-dispatch.v2",
            "status": "timeout",
            "timeout_seconds": error.timeout,
            "command": [str(part) for part in error.cmd] if isinstance(error.cmd, (list, tuple)) else str(error.cmd),
            "source_commit": os.environ.get("GITHUB_SHA", "local"),
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "completed_scenarios": COMPLETED_SCENARIOS,
            "started_at_utc": CHECK_STARTED_AT.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "observed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "source_inputs_before": SOURCE_INPUTS_AT_START,
            "source_inputs_after": {name: digest(ROOT / name) for name in INPUTS if (ROOT / name).is_file()},
            "artifact": {"bytes": ARTIFACT_BYTES, "sha256": ARTIFACT_SHA256},
            "partial_output": {
                "stdout_captured_suffix_bytes": len(stdout),
                "stdout_captured_suffix_sha256": hashlib.sha256(stdout).hexdigest(),
                "stderr_captured_suffix_bytes": len(stderr),
                "stderr_captured_suffix_sha256": hashlib.sha256(stderr).hexdigest(),
            },
            "capture": capture,
            "capture_bytes": len(raw),
            "capture_sha256": hashlib.sha256(raw).hexdigest(),
        }
        if SOURCE_INPUTS_AT_START is not None:
            report["source_inputs_unchanged"] = SOURCE_INPUTS_AT_START == report["source_inputs_after"]
        (EVIDENCE / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print("PHP-WASIX runner timed out; bounded output follows:", file=sys.stderr)
        print(raw.decode("utf-8", "replace")[-8192:], file=sys.stderr)
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        raise SystemExit(124)
