"""Semantic evidence checks for the fixed PHP handle registry bridge."""
from __future__ import annotations

import copy


PHP_FLAGS = (
    "registry_closed", "all_closed_rejected", "external_alias_preserved",
    "reference_rebound", "closure_same", "throwable_same",
    "array_copy_preserved", "resource_external_alive", "slot_reused",
    "stale_rejected", "lifetime_destroyed", "reentrant_release_safe",
    "cycle_collected", "close_reentry_rejected", "limits_preserved", "destructor_failure_safe",
)
SCENARIOS = ("handles", "handles_abort")


def validate_handle_result(value, scenario):
    if scenario not in SCENARIOS:
        raise ValueError("unknown handle scenario")
    if not isinstance(value, dict):
        raise ValueError("handle result must be an object")
    expected_keys = {
        "scenario", "continuation_peer", "frames_from_php", "frames_from_rust",
        "registered_initial", "expired_handles_rejected", "slot_reused",
        "old_generation_rejected", "state_after", "php_observation",
    }
    if set(value) != expected_keys:
        raise ValueError("handle result fields differ")
    if value["scenario"] != scenario or value["continuation_peer"] is not True:
        raise ValueError("handle scenario or peer differs")
    for key, expected in (
        ("frames_from_php", 5), ("frames_from_rust", 4),
        ("registered_initial", 8), ("expired_handles_rejected", 2),
    ):
        if type(value[key]) is not int or value[key] != expected:
            raise ValueError(f"handle result {key} differs")
    if value["slot_reused"] is not True or value["old_generation_rejected"] is not True:
        raise ValueError("handle generation observations differ")
    aborted = scenario == "handles_abort"
    if value["state_after"] != ("Aborted" if aborted else "Finished"):
        raise ValueError("Rust handle request did not terminate as expected")
    php = value["php_observation"]
    if not isinstance(php, dict) or set(php) != set(PHP_FLAGS) | {"abort_cleanup", "live_after_close"}:
        raise ValueError("PHP handle observation fields differ")
    for key in PHP_FLAGS:
        if php[key] is not True:
            raise ValueError(f"PHP handle observation {key} differs")
    if php["abort_cleanup"] is not aborted:
        raise ValueError("PHP handle cleanup mode differs")
    if type(php["live_after_close"]) is not int or php["live_after_close"] != 0:
        raise ValueError("PHP registry still has live slots")


def self_check():
    for scenario in SCENARIOS:
        aborted = scenario == "handles_abort"
        base = {
            "scenario": scenario, "continuation_peer": True,
            "frames_from_php": 5, "frames_from_rust": 4,
            "registered_initial": 8, "expired_handles_rejected": 2,
            "slot_reused": True, "old_generation_rejected": True,
            "state_after": "Aborted" if aborted else "Finished",
            "php_observation": {
                **dict.fromkeys(PHP_FLAGS, True),
                "abort_cleanup": aborted, "live_after_close": 0,
            },
        }
        validate_handle_result(base, scenario)
        mutations = []
        for key in PHP_FLAGS:
            candidate = copy.deepcopy(base)
            candidate["php_observation"][key] = False
            mutations.append(candidate)
        for key in ("frames_from_php", "frames_from_rust", "registered_initial", "expired_handles_rejected"):
            for replacement in (False, float(base[key]), base[key] - 1):
                candidate = copy.deepcopy(base)
                candidate[key] = replacement
                mutations.append(candidate)
        for key, replacement in (
            ("scenario", "handles" if aborted else "handles_abort"),
            ("state_after", "Running"), ("slot_reused", False),
            ("old_generation_rejected", False), ("continuation_peer", 1),
        ):
            candidate = copy.deepcopy(base)
            candidate[key] = replacement
            mutations.append(candidate)
        for key, replacement in (("live_after_close", 1), ("live_after_close", 0.0), ("abort_cleanup", not aborted)):
            candidate = copy.deepcopy(base)
            candidate["php_observation"][key] = replacement
            mutations.append(candidate)
        for candidate in mutations:
            try:
                validate_handle_result(candidate, scenario)
            except ValueError:
                continue
            raise AssertionError("mutated handle observations were accepted")
