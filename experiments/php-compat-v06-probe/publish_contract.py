"""Shared semantic checks for the v0.6 publish probe observations."""
from __future__ import annotations

import copy


TRACE = ["root:before-child", "nested:mutated", "root:after-child"]


def _obj(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _int(value, expected):
    return type(value) is int and value == expected


def _int_array(value, expected):
    return (isinstance(value, list) and len(value) == len(expected)
            and all(_int(actual, wanted) for actual, wanted in zip(value, expected)))


def validate_publish_result(summary):
    """Validate semantic evidence shared by the native and Wasm runners."""
    root = _obj(summary, "publish summary")
    obs = _obj(root.get("observation"), "observation")
    php = _obj(root.get("php_observation"), "php_observation")
    expected_numbers = {
        "initial_observation": 1, "root_publish_observation": 2,
        "child_entry_observation": 3, "child_publish_observation": 4,
        "stale_rejection_observation": 5, "replay_rejection_observation": 6,
        "atomic_rejection_observation": 7, "nested_observation": 8,
        "outer_observation": 9, "final_publish_observation": 10,
    }
    for key, expected in expected_numbers.items():
        if not _int(obs.get(key), expected):
            raise ValueError(f"publish observation {key} differs")
    if not _int(root.get("frames_from_php"), 11) or not _int(root.get("frames_from_rust"), 10):
        raise ValueError("publish frame counts differ")
    if root.get("continuation_peer") is not True:
        raise ValueError("publish continuation peer marker is missing")
    expected_flags = {
        "stale_rejected_before_write": True, "replay_rejected_before_write": True,
        "atomic_batch_no_partial_write": True, "alias_and_reference_values_preserved": True,
        "copy_on_write_preserved": True, "child_and_php_edits_preserved": True,
    }
    for key, expected in expected_flags.items():
        if obs.get(key) is not expected:
            raise ValueError(f"publish observation {key} differs")
    if obs.get("global_rebind_preserved") != "replacement":
        raise ValueError("publish global rebind observation differs")
    view = _obj(obs.get("view"), "final live view")
    item = _obj(view.get("item"), "final item")
    settings = _obj(view.get("settings"), "final settings")
    if (not _int(item.get("count"), 5) or item.get("unknown") != "php-outer"
            or not _int(item.get("final_marker"), 104) or settings.get("enabled") is not True
            or not _int_array(view.get("items"), [2, 11, 77])
            or not _int_array(view.get("copy_before"), [2, 4])
            or view.get("alias_same") is not True or not _int(view.get("property_reference_value"), 5)
            or not _int(view.get("array_reference_value"), 11)
            or view.get("global_name") != "replacement"):
        raise ValueError("final live PHP heap differs")
    if php.get("view") != view or php.get("trace") != TRACE:
        raise ValueError("PHP final view or callback trace differs")
    if (not _int(php.get("negative_publish_rejections"), 3)
            or php.get("alias_same_object") is not True
            or php.get("reference_cell_same_value") is not True
            or php.get("copy_on_write_preserved") is not True
            or php.get("global_rebound_without_alias_rebind") is not True):
        raise ValueError("PHP alias, reference, copy, global, or rejection observations differ")
    published = _obj(obs.get("published_values"), "published values")
    expected_published = {"root_count": 4, "root_array_entry": 7,
        "child_count": 5, "child_array_entry": 11, "final_marker": 104}
    if (set(published) != set(expected_published)
            or any(not _int(published.get(key), expected)
                for key, expected in expected_published.items())):
        raise ValueError("published values differ")
    return None


def self_check():
    """Prove each requested stale/lost-write/replay mutation is rejected."""
    view = {"item": {"count": 5, "unknown": "php-outer", "final_marker": 104},
        "settings": {"enabled": True}, "items": [2, 11, 77], "copy_before": [2, 4],
        "alias_same": True, "property_reference_value": 5, "array_reference_value": 11,
        "global_name": "replacement"}
    base = {"observation": {
        "view": view, "initial_observation": 1, "root_publish_observation": 2,
        "child_entry_observation": 3, "child_publish_observation": 4,
        "stale_rejection_observation": 5, "replay_rejection_observation": 6,
        "atomic_rejection_observation": 7, "nested_observation": 8,
        "outer_observation": 9, "final_publish_observation": 10,
        "published_values": {"root_count": 4, "root_array_entry": 7,
            "child_count": 5, "child_array_entry": 11, "final_marker": 104},
        "stale_rejected_before_write": True, "replay_rejected_before_write": True,
        "atomic_batch_no_partial_write": True, "alias_and_reference_values_preserved": True,
        "copy_on_write_preserved": True, "child_and_php_edits_preserved": True,
        "global_rebind_preserved": "replacement"},
        "php_observation": {"view": copy.deepcopy(view), "trace": TRACE[:],
            "negative_publish_rejections": 3, "alias_same_object": True,
            "reference_cell_same_value": True, "copy_on_write_preserved": True,
            "global_rebound_without_alias_rebind": True},
        "frames_from_php": 11, "frames_from_rust": 10, "continuation_peer": True}
    validate_publish_result(base)
    mutations = [
        lambda x: x["observation"].update(stale_rejected_before_write=False),
        lambda x: x["observation"].update(replay_rejected_before_write=False),
        lambda x: x["observation"].update(atomic_batch_no_partial_write=False),
        lambda x: x["observation"].update(alias_and_reference_values_preserved=False),
        lambda x: x["observation"].update(copy_on_write_preserved=False),
        lambda x: x["php_observation"].update(copy_on_write_preserved=False),
        lambda x: x["observation"].update(child_and_php_edits_preserved=False),
        lambda x: x["observation"]["view"]["item"].pop("unknown"),
        lambda x: x["observation"]["view"]["item"].update(unknown="php-nested"),
        lambda x: x["observation"]["view"]["item"].update(final_marker=103),
        lambda x: x["observation"].update(final_publish_observation=9),
        lambda x: x["observation"].update(nested_observation=10),
        lambda x: x["observation"].update(child_publish_observation=3),
        lambda x: x.update(frames_from_php=10),
        lambda x: x.update(frames_from_rust=9),
        lambda x: x["observation"].update(published_values={"root_count": 4,
            "root_array_entry": 7, "child_count": 5, "child_array_entry": 11, "final_marker": 105}),
        lambda x: x["observation"]["view"].update(items=[2, 11.0, 77]),
        lambda x: x["observation"]["view"].update(copy_before=[2, 4.0]),
        lambda x: x["observation"].update(published_values={"root_count": 4.0,
            "root_array_entry": 7, "child_count": 5, "child_array_entry": 11, "final_marker": 104}),
    ]
    for mutate in mutations:
        candidate = copy.deepcopy(base)
        mutate(candidate)
        try:
            validate_publish_result(candidate)
        except (ValueError, TypeError):
            continue
        raise AssertionError("mutated publish observation was accepted")
