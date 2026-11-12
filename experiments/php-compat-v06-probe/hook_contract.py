"""Observed contract for the fixed resumable Rust/PHP hook bridge."""
from __future__ import annotations

import copy

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


def validate_hook_result(value):
    keys = {
        "scenario", "continuation_peer", "frames_from_php", "frames_from_rust",
        "final_value_hex", "native_trace", "root_b_removed", "late_registered",
        "root_filters", "child_filters", "hook_depth_after",
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


def self_check():
    base = {
        "scenario": "hooks", "continuation_peer": True,
        "frames_from_php": 7, "frames_from_rust": 6,
        "final_value_hex": FINAL_VALUE, "native_trace": NATIVE_TRACE.copy(), "root_b_removed": True,
        "late_registered": True, "root_filters": 1, "child_filters": 1, "hook_depth_after": 0,
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
