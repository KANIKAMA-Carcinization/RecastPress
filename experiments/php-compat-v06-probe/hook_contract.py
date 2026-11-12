"""Observed contract for the fixed resumable Rust/PHP hook bridge."""
from __future__ import annotations

import copy
import json
import re

SCENARIOS = ("hooks", "hooks_catch", "hooks_abort")
THROWABLE_SCENARIOS = SCENARIOS[1:]
FRAME_COUNTS = {"hooks": (7, 6), "hooks_catch": (7, 6), "hooks_abort": (5, 4)}

FINAL_VALUE = b"seed:r5:c3:php-child:c7:php-root:php-b:late:r20".hex()
FINAL_NAME = "root-before-child:child:root-after-child:b:late"
PHP_TRACE = [
    "root_a:enter", "child:enter", "child:exit", "root_a:after-child",
    "root_a:exit", "root_b", "late",
]
NATIVE_TRACE = ["r5", "c3", "c7", "r20"]
PHP_FLAGS = (
    "alias_same", "same_closure_handle", "distinct_closures",
    "parent_hook_restored", "closed_read_rejected",
)


def validate_hook_result(value, scenario="hooks"):
    if scenario != "hooks":
        return validate_hook_throwable_result(value, scenario)
    keys = {
        "scenario", "continuation_peer", "frames_from_php", "frames_from_rust",
        "final_value_hex", "native_trace", "root_b_removed", "late_registered",
        "root_filters", "child_filters", "hook_depth_after",
        "current_hook_after", "child_priority_after", "root_priority_after",
        "continuation_depth_after", "state_after", "php_observation",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("hook result fields differ")
    if value["scenario"] != "hooks" or value["continuation_peer"] is not True:
        raise ValueError("hook peer or scenario differs")
    for key, expected in (
        ("frames_from_php", 7), ("frames_from_rust", 6),
        ("root_filters", 1), ("child_filters", 1),
        ("hook_depth_after", 0), ("continuation_depth_after", 0),
    ):
        if type(value[key]) is not int or value[key] != expected:
            raise ValueError(f"hook result {key} differs")
    if any(value[key] is not None for key in ("current_hook_after", "child_priority_after", "root_priority_after")):
        raise ValueError("normal hook final current/priority differs")
    if value["root_b_removed"] is not True or value["late_registered"] is not True:
        raise ValueError("hook mutation results differ")
    if (value["state_after"] != "Finished" or value["final_value_hex"] != FINAL_VALUE
            or value["native_trace"] != NATIVE_TRACE):
        raise ValueError("Rust hook order or completion differs")
    php = value["php_observation"]
    php_keys = set(PHP_FLAGS) | {
        "registry_live_after_close", "final_name", "returned_value_hex", "trace",
    }
    if not isinstance(php, dict) or set(php) != php_keys:
        raise ValueError("PHP hook observation fields differ")
    if any(php[key] is not True for key in PHP_FLAGS):
        raise ValueError("PHP hook identity, lifetime or nesting differs")
    if type(php["registry_live_after_close"]) is not int or php["registry_live_after_close"] != 0:
        raise ValueError("PHP hook registry has live slots after close")
    if (php["final_name"] != FINAL_NAME or php["returned_value_hex"] != FINAL_VALUE
            or php["trace"] != PHP_TRACE):
        raise ValueError("PHP hook order, result or live object differs")


THROWABLE_MESSAGE = "hooks child fixture throwable"
CATCH_VALUE = b"seed:r5:php-root-caught:php-b:late:r20".hex()
THROWABLE_TRACES = {
    "hooks_catch": [
        "root_a:enter", "child:enter", "child:throw", "child:transport_catch",
        "child:finally", "root_a:child_rethrow", "root_a:caught_same_object",
        "root_a:resume", "root_a:exit", "root_b", "late",
    ],
    "hooks_abort": [
        "root_a:enter", "child:enter", "child:throw", "child:transport_catch",
        "child:finally", "root_a:child_rethrow", "root_a:transport_catch", "root_a:finally",
    ],
}


def _require_exact(actual, expected, label):
    if type(actual) is not type(expected):
        raise ValueError(f"{label} type differs")
    if isinstance(expected, dict):
        if set(actual) != set(expected):
            raise ValueError(f"{label} fields differ")
        for key in expected:
            _require_exact(actual[key], expected[key], f"{label}.{key}")
    elif isinstance(expected, list):
        if len(actual) != len(expected):
            raise ValueError(f"{label} length differs")
        for index, (item, expected_item) in enumerate(zip(actual, expected)):
            _require_exact(item, expected_item, f"{label}[{index}]")
    elif actual != expected:
        raise ValueError(f"{label} value differs")


def throwable_expected_result(scenario):
    if scenario not in THROWABLE_SCENARIOS:
        raise ValueError("unknown Throwable hook scenario")
    caught = scenario == "hooks_catch"
    current, depth, state = ("root", 1, "Finished") if caught else ("child", 2, "Aborted")
    final_value = CATCH_VALUE if caught else None
    php_frames, rust_frames = FRAME_COUNTS[scenario]
    return {
        "scenario": scenario, "continuation_peer": True,
        "frames_from_php": php_frames, "frames_from_rust": rust_frames,
        "final_value_hex": final_value,
        "native_trace": ["r5", "c3", "r20"] if caught else ["r5", "c3"],
        "root_b_removed": True, "late_registered": True,
        "root_filters": 1, "child_filters": 1,
        "hook_depth_after": depth, "current_hook_after": current,
        "child_priority_after": 5, "root_priority_after": None if caught else 10,
        "continuation_depth_after": 0, "state_after": state,
        "parent_ticket_preserved": True, "child_ticket_retired": True,
        "root_ticket_retired_by_throwable": not caught,
        "php_observation": {
            "alias_same": True, "same_closure_handle": True, "distinct_closures": True,
            "parent_ticket_preserved": True, "closed_read_rejected": True,
            "registry_live_after_close": 0,
            "final_name": FINAL_NAME if caught else "root-before-child:child",
            "returned_value_hex": final_value, "trace": THROWABLE_TRACES[scenario].copy(),
            "throwable_class": "RuntimeException", "throwable_message": THROWABLE_MESSAGE,
            "child_throw_same_object": True, "root_rethrow_same_object": True,
            "root_caught_same_object": caught, "root_transport_catch_same_object": not caught,
            "child_throw_count": 1, "root_rethrow_count": 1,
            "root_catch_count": int(caught), "root_transport_catch_count": int(not caught),
            "external_throwable_alive_after_close": True,
            "current_hook_after": current, "hook_depth_after": depth,
        },
    }


def _validate_handle(value, expected_kind):
    fields = {"request", "kind", "slot", "generation", "token"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Throwable handle fields differ")
    if value["request"] != "51" or value["kind"] != expected_kind:
        raise ValueError("Throwable handle request or kind differs")
    for key, lower in (("slot", 0), ("generation", 1)):
        if type(value[key]) is not int or not lower <= value[key] <= 2**32 - 1:
            raise ValueError(f"Throwable handle {key} differs")
    if not isinstance(value["token"], str) or re.fullmatch(r"[0-9a-f]{16}", value["token"]) is None:
        raise ValueError("Throwable handle token differs")


def _validate_throwable_handle(value):
    _validate_handle(value, "throwable")


def validate_hook_throwable_result(value, scenario):
    expected = throwable_expected_result(scenario)
    if not isinstance(value, dict) or set(value) != set(expected) | {"abort_observation"}:
        raise ValueError("Throwable hook result fields differ")
    _require_exact({key: value[key] for key in expected}, expected, scenario)
    abort = value["abort_observation"]
    if scenario == "hooks_catch":
        if abort is not None:
            raise ValueError("caught Throwable unexpectedly aborted the request")
        return
    fields = {
        "reason", "completion", "root_ticket_retired", "root_b_removed", "late_registered", "current_hook", "hook_depth",
        "continuation_depth", "state", "late_resume_rejected", "finish_request_after_abort_rejected",
    }
    if not isinstance(abort, dict) or set(abort) != fields:
        raise ValueError("hook abort fields differ")
    completion = abort["completion"]
    if not isinstance(completion, dict) or set(completion) != {"kind", "handle", "class", "message"}:
        raise ValueError("hook abort Throwable completion fields differ")
    _validate_throwable_handle(completion["handle"])
    _require_exact(completion, {
        "kind": "php_throwable", "handle": completion["handle"],
        "class": "RuntimeException", "message": THROWABLE_MESSAGE,
    }, "hook abort completion")
    _require_exact(abort, {
        "reason": "php_throwable", "completion": completion, "root_ticket_retired": True,
        "root_b_removed": True, "late_registered": True,
        "current_hook": "child", "hook_depth": 2, "continuation_depth": 0, "state": "Aborted",
        "late_resume_rejected": True, "finish_request_after_abort_rejected": True,
    }, "hook abort")


def _unique_frame_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate decoded key in hook transcript")
        result[key] = value
    return result


def validate_hook_throwable_transcript(streams, result, scenario):
    """Independently correlate the actual child/root completion frames and PHP final frame."""
    validate_hook_result(result, scenario)
    if scenario not in THROWABLE_SCENARIOS or set(streams) != {"php", "rust"}:
        raise ValueError("Throwable transcript scenario/streams differ")
    kinds = {
        "php": ["start_root", "call_core", "resume", "resume"],
        "rust": ["invoke_php", "invoke_php", "return"],
    }
    if scenario == "hooks_catch":
        kinds["php"] += ["resume", "resume", "request_finished"]
        kinds["rust"] += ["invoke_php", "invoke_php", "return"]
    else:
        kinds["php"] += ["request_finished"]
        kinds["rust"] += ["abort"]
    frames = {}
    lines = {}
    for direction, expected_kinds in kinds.items():
        raw = streams[direction]
        if not isinstance(raw, bytes) or len(raw) > 33_554_432:
            raise ValueError("hook transcript stream bytes differ")
        lines[direction] = raw.splitlines(keepends=True)
        if len(lines[direction]) != len(expected_kinds):
            raise ValueError("hook transcript frame count differs")
        frames[direction] = []
        for index, (line, kind) in enumerate(zip(lines[direction], expected_kinds)):
            if not line.endswith(b"\n") or len(line) > 1_048_576:
                raise ValueError("hook transcript line framing differs")
            frame = json.loads(line, object_pairs_hook=_unique_frame_object)
            parent = "e-root-a" if (direction == "php" and index in (1, 2)) or (direction == "rust" and index == 2) else None
            expected_keys = {"v", "request", "seq", "kind", "payload"} | ({"parent_effect"} if parent else set())
            if not isinstance(frame, dict) or set(frame) != expected_keys or not isinstance(frame["payload"], dict):
                raise ValueError("hook transcript envelope fields differ")
            _require_exact({key: frame[key] for key in ("v", "request", "seq", "kind")}, {
                "v": 1, "request": "surface-probe-1", "seq": index, "kind": kind,
            }, "hook transcript envelope")
            if parent is not None and frame["parent_effect"] != parent:
                raise ValueError("hook transcript parent correlation differs")
            frames[direction].append(frame)
    php, rust = frames["php"], frames["rust"]
    start = php[0]["payload"]
    if set(start) != {"operation", "request_generation", "handles", "ids", "value_hex"}:
        raise ValueError("hook transcript start fields differ")
    _require_exact({key: start[key] for key in ("operation", "request_generation", "value_hex")}, {
        "operation": scenario, "request_generation": "51", "value_hex": b"seed".hex(),
    }, "hook transcript start")
    handles, ids = start["handles"], start["ids"]
    callback_keys = {"root_a", "root_b", "child", "late"}
    if not isinstance(handles, dict) or set(handles) != callback_keys | {"item"} or not isinstance(ids, dict) or set(ids) != callback_keys:
        raise ValueError("hook transcript handle/ID inventory differs")
    slots = set()
    for key, handle in handles.items():
        _validate_handle(handle, "object" if key == "item" else "closure")
        if handle["slot"] in slots:
            raise ValueError("hook transcript live handle slot collision")
        slots.add(handle["slot"])
    for ident in ids.values():
        if not isinstance(ident, str) or re.fullmatch(r"[1-9][0-9]*", ident) is None or int(ident) > 2**63 - 1:
            raise ValueError("hook transcript callback ID differs")
    if len(set(ids.values())) != len(callback_keys):
        raise ValueError("hook transcript callback IDs collide")
    _require_exact(php[1]["payload"], {
        "operation": "child_hook", "value_hex": b"seed:r5".hex(), "mutate_parent": True,
        "view": {"name": "root-before-child", "alias_same": True},
    }, "hook transcript CallCore")
    invocations = [(0, "root_a", "e-root-a", "root", 10, b"seed:r5"),
                   (1, "child", "e-child", "child", 5, b"seed:r5:c3")]
    if scenario == "hooks_catch":
        invocations += [(3, "root_b", "e-root-b", "root", 10, b"seed:r5:php-root-caught"),
                        (4, "late", "e-late", "root", 15, b"seed:r5:php-root-caught:php-b")]
    for index, callback, effect, hook, priority, value in invocations:
        _require_exact(rust[index]["payload"], {
            "callback": callback, "effect_id": effect, "handle": handles[callback],
            "hook": hook, "priority": priority, "value_hex": value.hex(),
        }, "hook transcript InvokePhp")
    child_resume = php[2]["payload"]
    if set(child_resume) != {"effect_id", "completion"} or child_resume["effect_id"] != "e-child":
        raise ValueError("hook transcript child resume differs")
    completion = child_resume["completion"]
    if not isinstance(completion, dict) or set(completion) != {"kind", "handle", "class", "message"}:
        raise ValueError("hook transcript Throwable completion fields differ")
    _validate_throwable_handle(completion["handle"])
    if completion["handle"]["slot"] in slots:
        raise ValueError("hook transcript Throwable reused a live handle slot")
    _require_exact(completion, {
        "kind": "php_throwable", "handle": completion["handle"],
        "class": "RuntimeException", "message": THROWABLE_MESSAGE,
    }, "hook transcript completion")
    _require_exact(rust[2]["payload"], {
        "operation": "child_hook", "completion": completion, "current_hook": "child",
        "hook_depth": 2, "child_ticket_retired": True, "parent_ticket_preserved": True,
    }, "hook transcript child return")
    if scenario == "hooks_catch":
        _require_exact(php[3]["payload"], {
            "effect_id": "e-root-a", "result": {
                "value_hex": b"seed:r5:php-root-caught".hex(),
                "view": {"name": "root-before-child:child:root-after-child", "alias_same": True},
            },
        }, "hook transcript caught root resume")
        for index, effect, value, name in (
            (4, "e-root-b", b"seed:r5:php-root-caught:php-b", "root-before-child:child:root-after-child:b"),
            (5, "e-late", b"seed:r5:php-root-caught:php-b:late", "root-before-child:child:root-after-child:b:late"),
        ):
            _require_exact(php[index]["payload"], {
                "effect_id": effect, "result": {"value_hex": value.hex(), "view": {"name": name, "alias_same": True}},
            }, "hook transcript resumed callback")
        _require_exact(rust[-1]["payload"], {
            "operation": scenario, "value_hex": CATCH_VALUE,
            "root_b_removed": True, "late_registered": True, "hook_depth": 1, "current_hook": "root",
        }, "hook transcript root return")
    else:
        _require_exact(php[3]["payload"], {"effect_id": "e-root-a", "completion": completion}, "hook transcript root throw")
        _require_exact(rust[-1]["payload"], result["abort_observation"], "hook transcript abort")
        _require_exact(result["abort_observation"]["completion"], completion, "hook transcript same Throwable")
    byte_count_before_final = sum(len(line) for line in lines["php"][:-1]) + len(streams["rust"])
    _require_exact(php[-1]["payload"], {
        "status": "ok" if scenario == "hooks_catch" else "throwable",
        "php_observation": result["php_observation"],
        "transport": {"frames_sent": len(php) - 1, "frames_received": len(rust), "bytes_total": byte_count_before_final},
    }, "hook transcript RequestFinished")


TRANSCRIPT_NEGATIVE_INVENTORY = (
    "wrong_start_generation", "extra_start_field", "duplicate_callback_id", "duplicate_live_slot",
    "wrong_child_input", "false_mutate_parent",
    "wrong_child_parent", "changed_throwable_return", "wrong_handle_kind", "wrong_request",
    "bool_slot", "replayed_root_sequence", "wrong_root_outcome", "false_closed_handle_check",
    "live_registry_after_close", "wrong_transport_count", "duplicate_decoded_key", "unterminated_frame",
)


def reject_mutated_transcripts(streams, result, scenario):
    """Use observed frames to check corruption rejection without changing either peer's inputs."""
    validate_hook_throwable_transcript(streams, result, scenario)
    original = {key: [json.loads(line) for line in value.splitlines()] for key, value in streams.items()}
    completion = original["php"][2]["payload"]["completion"]
    token = completion["handle"]["token"]
    changed_token = token[:-1] + ("0" if token[-1] != "0" else "1")
    wrong_root = ({"effect_id": "e-root-a", "completion": completion} if scenario == "hooks_catch" else
                  {"effect_id": "e-root-a", "result": {"value_hex": "00", "view": {"name": "stale", "alias_same": True}}})
    mutations = [
        ("wrong_start_generation", "php", 0, ("payload", "request_generation"), "052"),
        ("extra_start_field", "php", 0, ("payload", "undeclared"), True),
        ("duplicate_callback_id", "php", 0, ("payload", "ids", "root_b"), original["php"][0]["payload"]["ids"]["root_a"]),
        ("duplicate_live_slot", "php", 0, ("payload", "handles", "root_b", "slot"), original["php"][0]["payload"]["handles"]["root_a"]["slot"]),
        ("wrong_child_input", "php", 1, ("payload", "value_hex"), "00"),
        ("false_mutate_parent", "php", 1, ("payload", "mutate_parent"), False),
        ("wrong_child_parent", "php", 2, ("parent_effect",), "e-root-b"),
        ("changed_throwable_return", "rust", 2, ("payload", "completion", "handle", "token"), changed_token),
        ("wrong_handle_kind", "php", 2, ("payload", "completion", "handle", "kind"), "closure"),
        ("wrong_request", "php", 2, ("payload", "completion", "handle", "request"), "52"),
        ("bool_slot", "php", 2, ("payload", "completion", "handle", "slot"), True),
        ("replayed_root_sequence", "php", 3, ("seq",), 2),
        ("wrong_root_outcome", "php", 3, ("payload",), wrong_root),
        ("false_closed_handle_check", "php", -1, ("payload", "php_observation", "closed_read_rejected"), False),
        ("live_registry_after_close", "php", -1, ("payload", "php_observation", "registry_live_after_close"), 1),
        ("wrong_transport_count", "php", -1, ("payload", "transport", "frames_sent"), 99),
    ]

    def encode(frames):
        line = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        rust = b"".join(line(frame) for frame in frames["rust"])
        prefix = b"".join(line(frame) for frame in frames["php"][:-1])
        frames["php"][-1]["payload"]["transport"]["bytes_total"] = len(rust) + len(prefix)
        return {"php": prefix + line(frames["php"][-1]), "rust": rust}

    rejected = []
    for name, direction, index, path, replacement in mutations:
        frames = copy.deepcopy(original)
        parent = frames[direction][index]
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = replacement
        try:
            validate_hook_throwable_transcript(encode(frames), result, scenario)
        except ValueError:
            rejected.append(name)
            continue
        raise AssertionError(f"mutated hook transcript accepted: {name}")
    for name in ("duplicate_decoded_key", "unterminated_frame"):
        encoded = encode(copy.deepcopy(original))
        if name == "duplicate_decoded_key":
            encoded["php"] = encoded["php"].replace(b'"seq":0', b'"seq":0,"seq":0', 1)
        else:
            encoded["php"] = encoded["php"][:-1]
        try:
            validate_hook_throwable_transcript(encoded, result, scenario)
        except ValueError:
            rejected.append(name)
            continue
        raise AssertionError(f"mutated hook transcript accepted: {name}")
    if tuple(rejected) != TRANSCRIPT_NEGATIVE_INVENTORY:
        raise AssertionError("hook transcript mutation inventory differs")
    return rejected


def _throwable_self_check():
    handle = {"request": "51", "kind": "throwable", "slot": 8, "generation": 1, "token": "0123456789abcdef"}
    for scenario in THROWABLE_SCENARIOS:
        base = throwable_expected_result(scenario)
        base["abort_observation"] = None if scenario == "hooks_catch" else {
            "reason": "php_throwable", "completion": {
                "kind": "php_throwable", "handle": handle.copy(),
                "class": "RuntimeException", "message": THROWABLE_MESSAGE,
            },
            "root_ticket_retired": True, "root_b_removed": True, "late_registered": True,
            "current_hook": "child", "hook_depth": 2,
            "continuation_depth": 0, "state": "Aborted", "late_resume_rejected": True,
            "finish_request_after_abort_rejected": True,
        }
        validate_hook_result(base, scenario)
        mutations = []
        paths = [
            (("scenario",), "hooks"), (("frames_from_php",), 1),
            (("frames_from_rust",), float(base["frames_from_rust"])),
            (("hook_depth_after",), 0), (("current_hook_after",), None),
            (("child_priority_after",), None), (("root_priority_after",), False),
            (("parent_ticket_preserved",), False), (("child_ticket_retired",), False),
            (("root_ticket_retired_by_throwable",), base["state_after"] == "Finished"),
            (("native_trace",), ["r5", "c3", "c7", "r20"]),
            (("root_b_removed",), False), (("late_registered",), False),
            (("php_observation", "closed_read_rejected"), False),
            (("php_observation", "registry_live_after_close"), 0.0),
            (("php_observation", "external_throwable_alive_after_close"), False),
            (("php_observation", "root_caught_same_object"), scenario != "hooks_catch"),
            (("php_observation", "trace"), base["php_observation"]["trace"][:-1]),
        ]
        if scenario == "hooks_abort":
            paths += [
                (("abort_observation", "late_resume_rejected"), False),
                (("abort_observation", "root_b_removed"), False),
                (("abort_observation", "late_registered"), False),
                (("abort_observation", "completion", "handle", "kind"), "closure"),
                (("abort_observation", "completion", "handle", "request"), "051"),
                (("abort_observation", "completion", "handle", "slot"), True),
                (("abort_observation", "completion", "handle", "generation"), 0),
                (("abort_observation", "completion", "handle", "token"), "0123456789ABCDEF"),
            ]
        for path, replacement in paths:
            item = copy.deepcopy(base)
            parent = item
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = replacement
            mutations.append(item)
        for key in (None, "php_observation", "abort_observation"):
            if key == "abort_observation" and base[key] is None:
                continue
            for extra in (False, True):
                item = copy.deepcopy(base)
                mapping = item if key is None else item[key]
                if extra:
                    mapping["undeclared"] = True
                else:
                    mapping.pop(next(iter(mapping)))
                mutations.append(item)
        for item in mutations:
            try:
                validate_hook_result(item, scenario)
            except ValueError:
                continue
            raise AssertionError("mutated Throwable hook result was accepted")


def self_check():
    base = {
        "scenario": "hooks", "continuation_peer": True,
        "frames_from_php": 7, "frames_from_rust": 6,
        "final_value_hex": FINAL_VALUE, "native_trace": NATIVE_TRACE.copy(), "root_b_removed": True,
        "late_registered": True, "root_filters": 1, "child_filters": 1, "hook_depth_after": 0,
        "current_hook_after": None, "child_priority_after": None, "root_priority_after": None,
        "continuation_depth_after": 0, "state_after": "Finished", "php_observation": {
            **dict.fromkeys(PHP_FLAGS, True), "registry_live_after_close": 0, "final_name": FINAL_NAME,
            "returned_value_hex": FINAL_VALUE, "trace": PHP_TRACE.copy(),
        },
    }
    validate_hook_result(base)
    mutations = []
    for key in ("continuation_peer", "root_b_removed", "late_registered"):
        item = copy.deepcopy(base)
        item[key] = False
        mutations.append(item)
    for key in (
        "frames_from_php", "frames_from_rust", "root_filters", "child_filters",
        "hook_depth_after", "continuation_depth_after",
    ):
        for replacement in (float(base[key]), False, base[key] + 1):
            item = copy.deepcopy(base)
            item[key] = replacement
            mutations.append(item)
    for key in ("current_hook_after", "child_priority_after", "root_priority_after"):
        item = copy.deepcopy(base)
        item[key] = False
        mutations.append(item)
    for key in PHP_FLAGS:
        item = copy.deepcopy(base)
        item["php_observation"][key] = False
        mutations.append(item)
    for key, replacement in (
        ("final_value_hex", "00"), ("native_trace", NATIVE_TRACE[::-1]),
        ("state_after", "Running"),
    ):
        item = copy.deepcopy(base)
        item[key] = replacement
        mutations.append(item)
    for key, replacement in (
        ("registry_live_after_close", 0.0), ("registry_live_after_close", 1),
        ("final_name", "stale"), ("returned_value_hex", "00"),
        ("trace", PHP_TRACE[:-1]),
    ):
        item = copy.deepcopy(base)
        item["php_observation"][key] = replacement
        mutations.append(item)
    for item in mutations:
        try:
            validate_hook_result(item)
        except ValueError:
            continue
        raise AssertionError("mutated hook observation was accepted")
    _throwable_self_check()
