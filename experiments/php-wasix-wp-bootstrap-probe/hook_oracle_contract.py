"""Compare the pinned ordinary WP_Hook loop and the selected overlay handoff."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / 'experiments/php-wasix-wp-bootstrap-probe/evidence/windows-real-wp-hook-overlay-native.json'


def require(value: bool, code: str) -> None:
    if not value:
        raise ValueError(code)


def comparable(observation: dict, names: tuple[str, str]) -> dict:
    events = observation.get('events')
    require(type(events) is list and len(events) == 2, 'oracle_event_count')
    normalized = []
    for event, name, priority, value in zip(
        events,
        names,
        [10, 20],
        ['RP Bootstrap Fixture', 'RP Bootstrap Fixture|first'],
        strict=True,
    ):
        require(type(event) is dict, 'oracle_event_shape')
        require(event.get('callback') == name, 'oracle_callback_order')
        require(event.get('value') == value, 'oracle_callback_value')
        require(event.get('current_filter') == 'option_blogname', 'oracle_current_filter')
        require(event.get('doing_filter') is True, 'oracle_doing_filter')
        require(event.get('current_priority') == priority, 'oracle_priority')
        require(event.get('stack') == ['option_blogname'], 'oracle_filter_stack')
        if 'did_filter' in event:
            require(type(event['did_filter']) is int and event['did_filter'] == 1, 'oracle_did_filter')
        if 'argument_count' in event:
            require(type(event['argument_count']) is int and event['argument_count'] == 1,
                    'oracle_argument_count')
        normalized.append({key: event[key] for key in (
            'callback', 'value', 'current_filter', 'doing_filter', 'current_priority', 'stack')})

    after = observation.get('after_dispatch')
    require(type(after) is dict, 'oracle_after_shape')
    hook_state = after.get('hook_state', after.get('wp_hook_state'))
    require(hook_state == {
        'nesting_level': 0,
        'iterations': [],
        'current_priority': [],
        'doing_action': False,
    }, 'oracle_reset_state')
    require(after.get('current_filter') is False, 'oracle_current_filter_reset')
    require(after.get('doing_filter') is False, 'oracle_doing_filter_reset')
    require(after.get('stack') == [], 'oracle_stack_reset')
    require(after.get('did_filter') == 1, 'oracle_did_filter_final')
    return {
        'events': normalized,
        'filtered_value': observation.get('filtered_value', observation.get('value')),
        'after_dispatch': {
            'current_filter': after['current_filter'],
            'doing_filter': after['doing_filter'],
            'did_filter': after['did_filter'],
            'stack': after['stack'],
            'hook_state': hook_state,
        },
    }


def validate(native: dict, overlay: dict) -> dict:
    require(native.get('status') == 'passed', 'native_oracle_not_passed')
    require(overlay.get('status') == 'passed', 'overlay_oracle_not_passed')
    require(native.get('wordpress_version') == overlay.get('wordpress_version') == '7.1.3',
            'wordpress_version_mismatch')
    require(native.get('scenario') == overlay.get('scenario') == 'option_blogname',
            'hook_name_mismatch')
    native_table = native.get('registered_callbacks')
    overlay_table = overlay.get('callback_table')
    expected = [
        {'priority': 10, 'callback': 'rp_boot_hook_first', 'accepted_args': 1},
        {'priority': 20, 'callback': 'rp_boot_hook_second', 'accepted_args': 1},
    ]
    require(native_table == expected, 'native_callback_table_mismatch')
    expected_overlay = [
        {'priority': 10, 'uid': 'rp_overlay_oracle_first', 'function': 'rp_overlay_oracle_first', 'accepted_args': 1},
        {'priority': 20, 'uid': 'rp_overlay_oracle_second', 'function': 'rp_overlay_oracle_second', 'accepted_args': 1},
    ]
    require(overlay_table == expected_overlay, 'overlay_callback_table_mismatch')
    left = comparable(native, ('rp_boot_hook_first', 'rp_boot_hook_second'))
    right = comparable(overlay, ('rp_overlay_oracle_first', 'rp_overlay_oracle_second'))
    require(left['filtered_value'] == 'RP Bootstrap Fixture|first|second'
            and right['filtered_value'] == 'RP Bootstrap Fixture|first|second',
            'oracle_final_value')
    for source, target in zip(
        ('rp_boot_hook_first', 'rp_boot_hook_second'),
        ('rp_overlay_oracle_first', 'rp_overlay_oracle_second'),
        strict=True,
    ):
        for event in right['events']:
            if event['callback'] == target:
                event['callback'] = source
    require(left == right, 'native_overlay_observation_mismatch')
    return {
        'schema': 'recastpress.wp-bootstrap-hook-cross-oracle.v0.1',
        'status': 'passed',
        'scope': 'same callback-value-priority-filter-stack and WP_Hook reset observations across pinned original and selected overlay fixtures; overlay handoff invokes the two callback rows itself; no full bootstrap or Wasm guest',
        'wordpress_version': '7.1.3',
        'hook': 'option_blogname',
        'original_loop': {'callback_order': ['rp_boot_hook_first', 'rp_boot_hook_second'],
                          'filtered_value': left['filtered_value']},
        'overlay_handoff': {'callback_order': ['rp_overlay_oracle_first', 'rp_overlay_oracle_second'],
                            'filtered_value': right['filtered_value']},
        'compared_observations': left,
    }


def record(receipt: dict) -> dict:
    return validate(receipt['unpatched_wordpress_oracle'], receipt['oracle'])


if __name__ == '__main__':
    receipt = json.loads(NATIVE.read_text(encoding='utf-8'))
    print(json.dumps(record(receipt), ensure_ascii=False, sort_keys=True))
