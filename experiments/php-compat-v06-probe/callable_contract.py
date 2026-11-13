"""Closed result/value checks for PHP standard hook API backed by Rust."""
from __future__ import annotations

import re

APIS = ('add_filter', 'remove_filter', 'has_filter', 'apply_filters',
        'current_filter', 'doing_filter', 'did_filter')
SCENARIO = 'callable_api'
ACTION_APIS = ('add_action', 'remove_action', 'has_action', 'do_action',
               'current_action', 'doing_action', 'did_action')
ACTION_SCENARIO = 'action_api'
ACTION_COUNTER_SEED_HOOKS = {
    'int_7': 'callable-action-counter-seed-int-7',
    'float_7_5': 'callable-action-counter-seed-float-7-5',
    'php_int_max': 'callable-action-counter-seed-php-int-max',
}
ACTION_COUNTER_DIAGNOSTIC_HOOKS = {
    'bool_true': 'callable-action-counter-seed-diagnostic-bool-true',
    'alpha': 'callable-action-counter-seed-diagnostic-alpha',
}
ACTION_COUNTER_DIAGNOSTIC_EXPECTATIONS = {
    'bool_true': [
        {'type': 'bool', 'value': True},
        {'type': 'bool', 'value': True},
        {'type': 'bool', 'value': True},
    ],
    'alpha': [
        {'type': 'string', 'hex': '61'},
        {'type': 'string', 'hex': '62'},
        {'type': 'string', 'hex': '63'},
    ],
}
ACTION_COUNTER_SEED_EXPECTATIONS = {
    'int_7': {
        'before': {'type': 'int', 'value': '7'},
        'returns': [None, None],
        'events': [
            {'did_action': {'type': 'int', 'value': '8'}, 'wp_actions': {'type': 'int', 'value': '8'}},
            {'did_action': {'type': 'int', 'value': '9'}, 'wp_actions': {'type': 'int', 'value': '9'}},
        ],
        'after': {'type': 'int', 'value': '9'},
    },
    'float_7_5': {
        'before': {'type': 'float', 'bits': '401e000000000000'},
        'returns': [None, None],
        'events': [
            {'did_action': {'type': 'float', 'bits': '4021000000000000'},
             'wp_actions': {'type': 'float', 'bits': '4021000000000000'}},
            {'did_action': {'type': 'float', 'bits': '4023000000000000'},
             'wp_actions': {'type': 'float', 'bits': '4023000000000000'}},
        ],
        'after': {'type': 'float', 'bits': '4023000000000000'},
    },
    'php_int_max': {
        'before': {'type': 'int', 'value': '9223372036854775807'},
        'returns': [None, None],
        'events': [
            {'did_action': {'type': 'float', 'bits': '43e0000000000000'},
             'wp_actions': {'type': 'float', 'bits': '43e0000000000000'}},
            {'did_action': {'type': 'float', 'bits': '43e0000000000000'},
             'wp_actions': {'type': 'float', 'bits': '43e0000000000000'}},
        ],
        'after': {'type': 'float', 'bits': '43e0000000000000'},
    },
}
ALL_APIS = APIS + ACTION_APIS
HEX = re.compile(r'[0-9a-f]*\Z')
INTEGER = re.compile(r'(?:0|-?[1-9][0-9]*)\Z')


def canonical_hex(value, label):
    if not isinstance(value, str) or len(value) % 2 or HEX.fullmatch(value) is None:
        raise ValueError(f'{label}: noncanonical byte hex')
    return bytes.fromhex(value)


def canonical_integer(value, label):
    if not isinstance(value, str) or INTEGER.fullmatch(value) is None:
        raise ValueError(f'{label}: noncanonical integer')
    number = int(value)
    if not -(2**63) <= number <= 2**63 - 1:
        raise ValueError(f'{label}: integer outside signed64')
    return number


def validate_key(value):
    if not isinstance(value, dict) or set(value) != {'type', 'hex'}:
        raise ValueError('callable key fields differ')
    raw = canonical_hex(value['hex'], 'callable key')
    if value['type'] == 'int':
        canonical_integer(raw.decode('ascii'), 'callable int key')
    elif value['type'] == 'string':
        try:
            text = raw.decode('ascii')
            number = canonical_integer(text, 'candidate int key')
        except (UnicodeDecodeError, ValueError):
            return
        if str(number) == text:
            raise ValueError('string key would normalize to a PHP integer key')
    else:
        raise ValueError('callable key type differs')


def validate_pod(value, depth=0, budget=None):
    if budget is None:
        budget = [0]
    budget[0] += 1
    if depth > 64 or budget[0] > 65_536:
        raise ValueError('POD value limit exceeded')
    if not isinstance(value, dict):
        raise ValueError('POD value must be an object')
    kind = value.get('type')
    if kind == 'null':
        if set(value) != {'type'}:
            raise ValueError('null POD fields differ')
    elif kind == 'bool':
        if set(value) != {'type', 'value'} or type(value['value']) is not bool:
            raise ValueError('bool POD fields/type differ')
    elif kind == 'int':
        if set(value) != {'type', 'value'}:
            raise ValueError('integer POD fields differ')
        canonical_integer(value['value'], 'POD int')
    elif kind == 'float':
        if set(value) != {'type', 'bits'} or len(canonical_hex(value['bits'], 'POD float')) != 8:
            raise ValueError('float POD fields/bits differ')
    elif kind == 'string':
        if set(value) != {'type', 'hex'}:
            raise ValueError('string POD fields differ')
        canonical_hex(value['hex'], 'POD string')
    elif kind == 'array':
        if set(value) != {'type', 'entries'} or not isinstance(value['entries'], list):
            raise ValueError('array POD fields differ')
        seen = set()
        for entry in value['entries']:
            if not isinstance(entry, dict) or set(entry) != {'key', 'value'}:
                raise ValueError('array entry fields differ')
            key = entry['key']
            validate_pod(key, depth + 1, budget)
            if key['type'] == 'int':
                identity = ('int', canonical_integer(key['value'], 'array int key'))
            elif key['type'] == 'string':
                validate_key({'type': 'string', 'hex': key['hex']})
                identity = ('string', canonical_hex(key['hex'], 'array string key'))
            else:
                raise ValueError('array key type differs')
            if identity in seen:
                raise ValueError('duplicate normalized array key')
            seen.add(identity)
            validate_pod(entry['value'], depth + 1, budget)
    else:
        raise ValueError('unsupported POD type')


def validate_handle(value, kind):
    if not isinstance(value, dict) or set(value) != {'request', 'kind', 'slot', 'generation', 'token'}:
        raise ValueError('callable handle fields differ')
    if value['request'] != '61' or value['kind'] != kind:
        raise ValueError('callable handle request/kind differs')
    if type(value['slot']) is not int or not 0 <= value['slot'] < 2**32:
        raise ValueError('callable handle slot differs')
    if type(value['generation']) is not int or not 1 <= value['generation'] < 2**32:
        raise ValueError('callable handle generation differs')
    if len(canonical_hex(value['token'], 'callable token')) != 8:
        raise ValueError('callable token width differs')


# Fixed expected observations independently measured with pinned WP plugin.php/WP_Hook.
# The oracle runner re-executes the shared program and checks these observations.
EXPECTED_OBSERVATION = {'identity_matrix': {'function': {'id_hex': '63616c6c61626c655f70726f6772616d5f66756e6374696f6e',
                                  'key_type': 'string',
                                  'key_hex': '63616c6c61626c655f70726f6772616d5f66756e6374696f6e'},
                     'static_string': {'id_hex': '43616c6c61626c65537461746963466978747572653a3a636f6e76657274',
                                       'key_type': 'string',
                                       'key_hex': '43616c6c61626c65537461746963466978747572653a3a636f6e76657274'},
                     'static_array': {'id_hex': '43616c6c61626c65537461746963466978747572653a3a636f6e76657274',
                                      'key_type': 'string',
                                      'key_hex': '43616c6c61626c65537461746963466978747572653a3a636f6e76657274'},
                     'object': {'formula_matches': True, 'key_type': 'string'},
                     'object_method': {'formula_matches': True, 'key_type': 'string'},
                     'invokable_class': {'formula_matches': True, 'key_type': 'string'},
                     'byte_string': {'id_hex': 'ff006964', 'key_type': 'string', 'key_hex': 'ff006964'},
                     'numeric_string': {'id_hex': '313233', 'key_type': 'int', 'key_hex': '313233'},
                     'zero_string': {'id_hex': '30', 'key_type': 'int', 'key_hex': '30'},
                     'null_id': {'id_hex': None, 'key_type': None, 'key_hex': None}},
 'function_string': {'added': True,
                     'priority': 10,
                     'any_before_remove': True,
                     'at_priority': True,
                     'non_int_priority': 10,
                     'value': 'seed:function',
                     'removed': True,
                     'any_after_remove': False,
                     'did_filter': 1,
                     'callback_state': [{'current_filter_hex': '63616c6c61626c652d66756e6374696f6e',
                                         'doing_any': True,
                                         'doing_named': True,
                                         'did_filter': 1}],
                     'current_filter_after': False,
                     'doing_filter_after': False,
                     'doing_named_filter_after': False},
 'pod_filter_values': {'strict_roundtrip': True},
 'zero_accepted_args': {'value': 'zero-only', 'calls': 1},
 'static_replacement': {'first_added': True,
                        'same_id_priority': 10,
                        'replacement_added': True,
                        'second_id_priority': 10,
                        'value': 'seed:static-string:'},
 'static_case_ids': {'first_added': True,
                     'second_added': True,
                     'first_priority': 16,
                     'second_priority': 16,
                     'value': 'seed:static-string::static-string:'},
 'object_method': {'priority': 12, 'removed_by_fresh_array': True, 'after_remove': False},
 'invokable_object': {'added': True, 'value': 'seed:invokable', 'priority': 14},
 'case_sensitive_ids': {'upper_added': True,
                        'lower_added': True,
                        'upper_priority': 15,
                        'lower_priority': 15,
                        'value': 'seed:case:case',
                        'calls': ['called', 'called']},
 'duplicate_replacement_order': {'replacement_added': True,
                                 'value': 'seed:duplicate:extra:other',
                                 'trace': ['duplicate:extra', 'other']},
 'null_priority': {'added': True, 'priority': 0, 'default_remove': False, 'explicit_zero_remove': True},
 'destructor_reentry': {'added': True,
                        'alias_alive_before_remove': True,
                        'removed_by_raw_id': True,
                        'weak_alive_after_remove': False,
                        'observation_during_remove': {'old_priority': False,
                                                      'current_filter': False,
                                                      'added': True,
                                                      'new_priority': 10},
                        'trace': ['destructor-enter', 'destructor-exit'],
                        'old_priority_after': False,
                        'new_priority_after': 10,
                        'value_after': 'seed:destructor-added'},
 'self_remove_throw_lifetime': {'error': {'class_hex': '52756e74696d65457863657074696f6e',
                                          'message_hex': '63616c6c61626c65206c69666574696d652066697874757265'},
                                'weak_alive_in_catch': False,
                                'trace': ['callback-enter', 'alias-unset', 'destructor', 'parent-catch'],
                                'current_filter_after': 'callable-lifetime'},
 'invalid_function_delayed_error': {'added': True,
                                    'priority': 19,
                                    'error': {'class_hex': '547970654572726f72',
                                              'message_hex': '63616c6c5f757365725f66756e635f617272617928293a20417267756d656e7420233120282463616c6c6261636b29206d75737420626520612076616c69642063616c6c6261636b2c2066756e6374696f6e202263616c6c61626c655f6d697373696e675f66756e6374696f6e5f3761323122206e6f7420666f756e64206f7220696e76616c69642066756e6374696f6e206e616d65'}},
 'invalid_object_delayed_error': {'added': True,
                                  'priority': 20,
                                  'error': {'class_hex': '547970654572726f72',
                                            'message_hex': '63616c6c5f757365725f66756e635f617272617928293a20417267756d656e7420233120282463616c6c6261636b29206d75737420626520612076616c69642063616c6c6261636b2c206e6f206172726179206f7220737472696e6720676976656e'}},
 'null_id_noop': {'added': True, 'has_any': False},
 'all_listener_raw_hook': {'all_added': True,
                           'hook_added': True,
                           'value_hex': '736565643a66756e6374696f6e',
                           'all_arguments_hex': [['ff007261772d686f6f6b', '73656564', '6578747261']]},
 'arbitrary_byte_id': {'added': True, 'priority': 22, 'removed': True},
 'array_key_coercion': {'numeric_added': True,
                        'numeric_priority': 23,
                        'zero_added': True,
                        'zero_has_priority': False,
                        'zero_removed': True},
 'cross_form_id_collision': {'object_added': True,
                             'raw_string_added': True,
                             'array_priority': 25,
                             'string_priority': 25,
                             'removed_by_array': True}}


def validate_observation(value):
    from hook_contract import _require_exact
    _require_exact(value, EXPECTED_OBSERVATION, 'standard callable API program')


def validate_result(value):
    fields = {'scenario', 'continuation_peer', 'frames_from_php', 'frames_from_rust',
              'api_counts', 'callback_invocations', 'release_effects',
              'late_resume_rejected', 'late_call_rejected', 'continuation_depth_after',
              'state_after', 'php_observation', 'registry_observation'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('callable result fields differ')
    if value['scenario'] != SCENARIO or value['continuation_peer'] is not True:
        raise ValueError('callable scenario/peer differs')
    counts = value['api_counts']
    if not isinstance(counts, dict) or set(counts) != set(APIS):
        raise ValueError('callable API count inventory differs')
    if any(type(number) is not int or number < 0 for number in counts.values()):
        raise ValueError('callable API count type/range differs')
    for key in ('frames_from_php', 'frames_from_rust', 'callback_invocations', 'release_effects', 'continuation_depth_after'):
        if type(value[key]) is not int or value[key] < 0:
            raise ValueError(f'callable {key} type/range differs')
    expected_base = sum(counts.values()) + value['callback_invocations'] + value['release_effects']
    if value['frames_from_php'] != expected_base + 4 or value['frames_from_rust'] != expected_base + 3:
        raise ValueError('callable API/effect frame accounting differs')
    if (value['continuation_depth_after'] != 0 or value['state_after'] != 'Finished'
            or value['late_resume_rejected'] is not True or value['late_call_rejected'] is not True):
        raise ValueError('callable terminal state differs')
    registry = value['registry_observation']
    if (not isinstance(registry, dict) or set(registry) != {'closed', 'live_after_close', 'closed_read_rejected'}
            or registry['closed'] is not True or registry['closed_read_rejected'] is not True
            or type(registry['live_after_close']) is not int or registry['live_after_close'] != 0):
        raise ValueError('callable registry terminal observation differs')
    validate_observation(value['php_observation'])


def self_check():
    valid = [
        {'type': 'null'}, {'type': 'bool', 'value': False},
        {'type': 'int', 'value': '-9223372036854775808'},
        {'type': 'float', 'bits': '8000000000000000'},
        {'type': 'string', 'hex': '00ff'},
        {'type': 'array', 'entries': [{'key': {'type': 'string', 'hex': '3031'}, 'value': {'type': 'bool', 'value': True}}]},
    ]
    for item in valid:
        validate_pod(item)
    import copy
    counts = dict(zip(APIS, (28, 9, 27, 13, 5, 6, 3)))
    base = sum(counts.values()) + 17 + 27
    result = {'scenario': SCENARIO, 'continuation_peer': True,
              'frames_from_php': base + 4, 'frames_from_rust': base + 3,
              'api_counts': counts, 'callback_invocations': 17, 'release_effects': 27,
              'late_resume_rejected': True, 'late_call_rejected': True,
              'continuation_depth_after': 0, 'state_after': 'Finished',
              'php_observation': EXPECTED_OBSERVATION,
              'registry_observation': {'closed': True, 'live_after_close': 0, 'closed_read_rejected': True}}
    validate_result(result)
    for key, replacement in (('frames_from_php', 0), ('api_counts', {}), ('release_effects', False),
                             ('state_after', 'Running'), ('late_resume_rejected', False),
                             ('php_observation', {}), ('registry_observation', {})):
        broken = copy.deepcopy(result)
        broken[key] = replacement
        try:
            validate_result(broken)
        except ValueError:
            continue
        raise AssertionError('malformed callable result accepted: ' + key)
    malformed = [
        {'type': 'bool', 'value': 0}, {'type': 'int', 'value': '+1'},
        {'type': 'int', 'value': '-0'}, {'type': 'int', 'value': '01'},
        {'type': 'int', 'value': '9223372036854775808'},
        {'type': 'float', 'bits': '0'}, {'type': 'string', 'hex': 'FF'},
        {'type': 'null', 'extra': True},
        {'type': 'array', 'entries': [{'key': {'type': 'string', 'hex': '30'}, 'value': {'type': 'null'}}]},
        {'type': 'array', 'entries': [{'key': {'type': 'int', 'value': '0'}, 'value': {'type': 'null'}},
                                     {'key': {'type': 'int', 'value': '0'}, 'value': {'type': 'null'}}]},
    ]
    for item in malformed:
        try:
            validate_pod(item)
        except ValueError:
            continue
        raise AssertionError('malformed callable POD accepted')

def validate_transcript(streams, result):
    """Independently replay actual bidirectional effect ancestry and handle leases."""
    import json
    from hook_contract import _unique_frame_object, _require_exact
    validate_result(result)
    if set(streams) != {'php', 'rust'}:
        raise ValueError('callable transcript directions differ')
    frames, lines = {}, {}
    for direction, raw in streams.items():
        if not isinstance(raw, bytes) or len(raw) > 33_554_432:
            raise ValueError('callable transcript bounds differ')
        lines[direction] = raw.splitlines(keepends=True)
        frames[direction] = []
        for seq, line in enumerate(lines[direction]):
            if not line.endswith(b'\n') or len(line) > 1_048_576:
                raise ValueError('callable transcript line framing differs')
            item = json.loads(line, object_pairs_hook=_unique_frame_object)
            keys = {'v', 'request', 'seq', 'kind', 'payload'}
            if not isinstance(item, dict) or set(item) not in (keys, keys | {'parent_effect'}):
                raise ValueError('callable envelope fields differ')
            if (type(item['v']) is not int or item['v'] != 1 or item['request'] != 'surface-probe-1'
                    or type(item['seq']) is not int or item['seq'] != seq or not isinstance(item['payload'], dict)):
                raise ValueError('callable envelope identity differs')
            frames[direction].append(item)
    cursor = {'php': 0, 'rust': 0}
    counts = dict.fromkeys(APIS, 0)
    invocations = releases = 0
    effects, live, releasing = set(), {}, set()

    def exact(value, fields):
        if not isinstance(value, dict) or set(value) != set(fields):
            raise ValueError('callable payload fields differ')

    def take(direction, kind, parent):
        index = cursor[direction]
        if index >= len(frames[direction]):
            raise ValueError('callable transcript ended early')
        item = frames[direction][index]
        cursor[direction] += 1
        if item['kind'] != kind or item.get('parent_effect') != parent or ('parent_effect' in item) != (parent is not None):
            raise ValueError('callable transcript kind/parent differs')
        return item['payload']

    def peek(direction):
        index = cursor[direction]
        if index >= len(frames[direction]):
            raise ValueError('callable transcript ended early')
        return frames[direction][index]

    def handle_id(value, kind):
        validate_handle(value, kind)
        return tuple(value[key] for key in ('request', 'kind', 'slot', 'generation', 'token'))

    def completion(value, pod):
        if not isinstance(value, dict):
            raise ValueError('callable completion differs')
        if value.get('kind') == 'value':
            exact(value, ('kind', 'value'))
            if pod:
                validate_pod(value['value'])
        elif value.get('kind') == 'php_throwable':
            exact(value, ('kind', 'handle', 'class_hex', 'message_hex'))
            handle_id(value['handle'], 'throwable')
            canonical_hex(value['class_hex'], 'Throwable class')
            canonical_hex(value['message_hex'], 'Throwable message')
        else:
            raise ValueError('callable completion kind differs')

    def api(parent):
        payload = take('php', 'call_core', parent)
        name = payload.get('api')
        if name not in APIS:
            raise ValueError('unknown callable API')
        fields = {
            'add_filter': ('api', 'hook_hex', 'key', 'priority', 'accepted_args', 'handle'),
            'remove_filter': ('api', 'hook_hex', 'key', 'priority'),
            'has_filter': ('api', 'hook_hex', 'key', 'any', 'priority'),
            'apply_filters': ('api', 'hook_hex', 'value', 'args'),
            'current_filter': ('api',), 'doing_filter': ('api', 'hook_hex'), 'did_filter': ('api', 'hook_hex'),
        }
        exact(payload, fields[name])
        counts[name] += 1
        if 'hook_hex' in payload and not (name == 'doing_filter' and payload['hook_hex'] is None):
            canonical_hex(payload['hook_hex'], 'hook')
        if 'key' in payload and payload['key'] is not None:
            validate_key(payload['key'])
        if 'priority' in payload and not (name == 'has_filter' and payload['priority'] is None):
            if type(payload['priority']) is not int or not -(2**63) <= payload['priority'] < 2**63:
                raise ValueError('callable priority differs')
        if name == 'has_filter':
            if type(payload['any']) is not bool or (payload['any'] and payload['key'] is not None):
                raise ValueError('callable has_filter sentinel differs')
        if name == 'add_filter':
            if (payload['key'] is not None and (type(payload['accepted_args']) is not int or not -(2**63) <= payload['accepted_args'] < 2**63)) or (payload['key'] is None and payload['accepted_args'] is not None):
                raise ValueError('callable accepted_args differs')
            if payload['key'] is None:
                if payload['handle'] is not None:
                    raise ValueError('invalid identity retained a callable')
            else:
                ident = handle_id(payload['handle'], 'callable')
                if ident in live or any(old[2] == ident[2] and old not in releasing for old in live):
                    raise ValueError('callable live slot collision')
                live[ident] = dict(payload)
        if name == 'apply_filters':
            validate_pod(payload['value'])
            if not isinstance(payload['args'], list):
                raise ValueError('callable API args differs')
            for value in payload['args']:
                validate_pod(value)
        context = {'hook': payload['hook_hex'], 'value': payload['value'], 'extra': payload['args'], 'throwable': None} if name == 'apply_filters' else None
        while peek('rust')['kind'] == 'invoke_php':
            effect(parent, context)
        returned = take('rust', 'return', parent)
        exact(returned, ('api', 'completion'))
        if returned['api'] != name:
            raise ValueError('callable API return identity differs')
        completion(returned['completion'], True)
        outcome = returned['completion']
        if outcome['kind'] == 'value':
            node = outcome['value']
            kind = node['type']
            if name in ('add_filter', 'remove_filter', 'doing_filter') and kind != 'bool':
                raise ValueError('callable API requires bool result')
            if name == 'add_filter' and node['value'] is not True:
                raise ValueError('add_filter must return true')
            if name == 'has_filter':
                specific = payload['priority'] is not None
                falsey = payload['key'] is None or payload['key'] in ({'type': 'int', 'hex': '30'}, {'type': 'string', 'hex': ''})
                if payload['any'] or specific or falsey:
                    if kind != 'bool' or (not payload['any'] and falsey and node['value'] is not False):
                        raise ValueError('has_filter boolean mode result differs')
                elif kind != 'int' and node != {'type': 'bool', 'value': False}:
                    raise ValueError('has_filter search result differs')
            if name == 'current_filter' and kind != 'string' and node != {'type': 'bool', 'value': False}:
                raise ValueError('current_filter result differs')
            if name == 'did_filter' and (kind != 'int' or int(node['value']) < 0):
                raise ValueError('did_filter count result differs')
        if context is not None:
            expected = context['throwable'] or {'kind': 'value', 'value': context['value']}
            _require_exact(outcome, expected, 'callable filter callback/return correlation')

    def effect(parent, context=None):
        nonlocal invocations, releases
        payload = take('rust', 'invoke_php', parent)
        operation, effect_id = payload.get('operation'), payload.get('effect_id')
        if not isinstance(effect_id, str) or not effect_id or effect_id in effects:
            raise ValueError('callable effect replay/identity differs')
        effects.add(effect_id)
        ident = None
        if operation == 'run_script':
            exact(payload, ('operation', 'effect_id'))
            if parent is not None or effect_id != 'e-script' or len(effects) != 1:
                raise ValueError('callable root effect differs')
        elif operation == 'callable':
            exact(payload, ('operation', 'effect_id', 'handle', 'hook_hex', 'priority', 'call_mode', 'args'))
            ident = handle_id(payload['handle'], 'callable')
            if ident not in live or ident in releasing:
                raise ValueError('callable invocation lease differs')
            canonical_hex(payload['hook_hex'], 'invoked hook')
            if type(payload['priority']) is not int or payload['call_mode'] not in ('zero', 'array', 'all') or not isinstance(payload['args'], list):
                raise ValueError('callable invocation metadata differs')
            if payload['call_mode'] == 'zero' and payload['args']:
                raise ValueError('zero callable received arguments')
            for value in payload['args']:
                validate_pod(value)
            registration = live[ident]
            if context is None or payload['hook_hex'] != context['hook'] or payload['priority'] != registration['priority']:
                raise ValueError('callable invocation hook/priority correlation differs')
            mode = payload['call_mode']
            if mode == 'all':
                if registration['hook_hex'] != b'all'.hex():
                    raise ValueError('all invocation is not an all registration')
                expected_args = [{'type': 'string', 'hex': context['hook']}, context['value'], *context['extra']]
            else:
                accepted = registration['accepted_args']
                if registration['hook_hex'] != context['hook'] or mode != ('zero' if accepted == 0 else 'array'):
                    raise ValueError('callable invocation registration/mode differs')
                values = [context['value'], *context['extra']]
                expected_args = [] if accepted == 0 else values[:accepted]
            _require_exact(payload['args'], expected_args, 'callable invocation argument slicing')
            invocations += 1
        elif operation == 'release_callable':
            exact(payload, ('operation', 'effect_id', 'handle'))
            ident = handle_id(payload['handle'], 'callable')
            if ident not in live or ident in releasing:
                raise ValueError('callable release lease differs')
            releasing.add(ident)
            releases += 1
        elif operation == 'close_registry':
            exact(payload, ('operation', 'effect_id'))
            if parent is not None or live:
                raise ValueError('callable registry closed with outstanding leases')
        else:
            raise ValueError('unknown callable effect')
        while peek('php')['kind'] == 'call_core':
            if operation == 'close_registry':
                raise ValueError('API entered after terminal registry close')
            api(effect_id)
        resumed = take('php', 'resume', parent)
        exact(resumed, ('effect_id', 'completion'))
        if resumed['effect_id'] != effect_id:
            raise ValueError('callable resume correlation differs')
        outcome = resumed['completion']
        completion(outcome, operation == 'callable')
        if context is not None and operation in ('callable', 'release_callable'):
            if outcome['kind'] == 'php_throwable':
                context['throwable'] = outcome
            elif operation == 'callable' and payload['call_mode'] != 'all':
                context['value'] = outcome['value']
        if operation == 'run_script':
            if outcome != {'kind': 'value', 'value': result['php_observation']}:
                raise ValueError('callable script observation differs')
        elif operation == 'release_callable':
            if outcome['kind'] == 'value' and (not isinstance(outcome['value'], dict) or set(outcome['value']) != {'released'} or outcome['value']['released'] is not True):
                raise ValueError('callable final release failed')
            releasing.remove(ident)
            del live[ident]
        elif operation == 'close_registry' and outcome != {'kind': 'value', 'value': result['registry_observation']}:
            raise ValueError('callable registry observation differs')
        if operation == 'close_registry':
            _require_exact(outcome, {'kind': 'value', 'value': result['registry_observation']}, 'callable close observation')
        return operation

    if take('php', 'start_root', None) != {'operation': SCENARIO, 'request_generation': '61'}:
        raise ValueError('callable root operation differs')
    if effect(None) != 'run_script':
        raise ValueError('callable script effect missing')
    closed = False
    while peek('rust')['kind'] == 'invoke_php':
        if closed:
            raise ValueError('callable effect after registry close')
        operation = effect(None)
        if operation not in ('release_callable', 'close_registry'):
            raise ValueError('invalid callable terminal effect')
        closed = operation == 'close_registry'
    if not closed or take('rust', 'return', None) != {'operation': SCENARIO, 'status': 'ok'}:
        raise ValueError('callable terminal return differs')
    final = take('php', 'request_finished', None)
    expected = {'status': 'ok', 'php_observation': result['php_observation'],
                'registry_observation': result['registry_observation'],
                'transport': {'frames_sent': len(frames['php']) - 1, 'frames_received': len(frames['rust']),
                              'bytes_total': sum(map(len, lines['php'][:-1])) + len(streams['rust'])}}
    if final != expected or any(cursor[key] != len(frames[key]) for key in cursor):
        raise ValueError('callable final observation/transport/EOF differs')
    _require_exact(final, expected, 'callable RequestFinished')
    if counts != result['api_counts'] or invocations != result['callback_invocations'] or releases != result['release_effects']:
        raise ValueError('callable observed API/effect counts differ')
    if any(len(frames[key]) != result['frames_from_' + key] for key in frames):
        raise ValueError('callable observed frame counts differ')


TRANSCRIPT_NEGATIVE_INVENTORY = ('wrong_generation', 'replayed_sequence', 'wrong_parent', 'wrong_api',
    'wrong_return_api', 'wrong_effect_resume', 'wrong_handle_kind', 'wrong_handle_token',
    'wrong_call_mode', 'false_release', 'wrong_script_observation', 'live_registry_close',
    'wrong_transport', 'extra_frame', 'duplicate_decoded_key', 'unterminated_frame',
    'wrong_api_result_type', 'oversized_accepted_args', 'wrong_invocation_hook',
    'wrong_invocation_priority', 'wrong_invocation_args')


def reject_mutated_transcripts(streams, result):
    import copy
    import json
    validate_transcript(streams, result)
    original = {key: [json.loads(line) for line in value.splitlines()] for key, value in streams.items()}
    first_api = next(i for i, item in enumerate(original['php']) if item['kind'] == 'call_core')
    first_return = next(i for i, item in enumerate(original['rust']) if item['kind'] == 'return')
    first_invoke = next(i for i, item in enumerate(original['rust']) if item['payload'].get('operation') == 'callable')
    release_resume = next(i for i, item in enumerate(original['php']) if item['kind'] == 'resume' and item['payload']['completion'].get('value') == {'released': True})
    script_resume = next(i for i, item in enumerate(original['php']) if item['kind'] == 'resume' and item['payload']['effect_id'] == 'e-script')
    close_resume = next(i for i, item in enumerate(original['php']) if item['kind'] == 'resume' and isinstance(item['payload']['completion'].get('value'), dict) and item['payload']['completion']['value'].get('closed') is True)
    for name in TRANSCRIPT_NEGATIVE_INVENTORY:
        frames = copy.deepcopy(original)
        if name == 'wrong_generation': frames['php'][0]['payload']['request_generation'] = '60'
        elif name == 'replayed_sequence': frames['php'][first_api]['seq'] = 0
        elif name == 'wrong_parent': frames['php'][first_api]['parent_effect'] = 'unknown'
        elif name == 'wrong_api': frames['php'][first_api]['payload']['api'] = 'unknown'
        elif name == 'wrong_return_api': frames['rust'][first_return]['payload']['api'] = 'unknown'
        elif name == 'wrong_effect_resume': frames['php'][release_resume]['payload']['effect_id'] = 'unknown'
        elif name == 'wrong_handle_kind': frames['rust'][first_invoke]['payload']['handle']['kind'] = 'closure'
        elif name == 'wrong_handle_token': frames['rust'][first_invoke]['payload']['handle']['token'] = '0000000000000000'
        elif name == 'wrong_call_mode': frames['rust'][first_invoke]['payload']['call_mode'] = 'unknown'
        elif name == 'false_release': frames['php'][release_resume]['payload']['completion']['value']['released'] = False
        elif name == 'wrong_script_observation': frames['php'][script_resume]['payload']['completion']['value'] = {}
        elif name == 'live_registry_close': frames['php'][close_resume]['payload']['completion']['value']['live_after_close'] = 1
        elif name == 'wrong_transport': frames['php'][-1]['payload']['transport']['frames_sent'] = 0
        elif name == 'extra_frame': frames['php'].append(copy.deepcopy(frames['php'][-1]))
        elif name == 'wrong_api_result_type': frames['rust'][first_return]['payload']['completion'] = {'kind': 'value', 'value': {'type': 'int', 'value': '1'}}
        elif name == 'oversized_accepted_args': frames['php'][first_api]['payload']['accepted_args'] = 2**64
        elif name == 'wrong_invocation_hook': frames['rust'][first_invoke]['payload']['hook_hex'] = b'unrelated'.hex()
        elif name == 'wrong_invocation_priority': frames['rust'][first_invoke]['payload']['priority'] += 1
        elif name == 'wrong_invocation_args': frames['rust'][first_invoke]['payload']['args'] = []
        encoded = {key: b''.join((json.dumps(item, separators=(',', ':')) + '\n').encode() for item in values) for key, values in frames.items()}
        if name != 'wrong_transport':
            frames['php'][-1]['payload']['transport']['bytes_total'] = sum(len(line) for line in encoded['php'].splitlines(keepends=True)[:-1]) + len(encoded['rust'])
            encoded = {key: b''.join((json.dumps(item, separators=(',', ':')) + '\n').encode() for item in values) for key, values in frames.items()}
        if name == 'duplicate_decoded_key': encoded['php'] = encoded['php'].replace(b'"v":1', b'"v":1,"v":1', 1)
        elif name == 'unterminated_frame': encoded['php'] = encoded['php'][:-1]
        try:
            validate_transcript(encoded, result)
        except (ValueError, KeyError, TypeError, IndexError):
            continue
        raise AssertionError('corrupted callable transcript accepted: ' + name)
    return list(TRANSCRIPT_NEGATIVE_INVENTORY)
