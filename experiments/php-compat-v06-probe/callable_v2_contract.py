"""Closed wire-v2 checks for raw PHP argument effects and typed hook control."""
from __future__ import annotations

import json
import struct
from callable_contract import (APIS, SCENARIO, ACTION_APIS, ACTION_SCENARIO, ACTION_COUNTER_SEED_EXPECTATIONS,
    ACTION_COUNTER_SEED_HOOKS, ALL_APIS, EXPECTED_OBSERVATION, canonical_hex, canonical_integer, validate_key,
    validate_pod)
from hook_contract import _require_exact, _unique_frame_object

REQUEST = '71'
# Freeze this from the independently executed pinned WordPress program before
# recording acceptance evidence. An unset expectation must never pass a gate.
EXPECTED_COERCIONS = {'runtime_integer_width': {'php_version': '8.5.12',
                           'php_int_size': 8,
                           'php_int_min': '-9223372036854775808',
                           'php_int_max': '9223372036854775807',
                           'apply_filter_min_same_integer': True,
                           'apply_filter_max_same_integer': True,
                           'apply_filter_min_type': 'int',
                           'apply_filter_max_type': 'int'},
 'hook_add_numeric_string': {'added': True, 'warnings': []},
 'hook_add_integer_alias': {'added': True, 'warnings': []},
 'hook_add_leading_zero_string': {'added': True, 'warnings': []},
 'hook_add_boolean_key': {'added': True, 'warnings': []},
 'hook_add_integer_boolean_alias': {'added': True, 'warnings': []},
 'hook_add_null_key': {'added': True,
                       'warnings': [{'severity': 8192,
                                     'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'},
                                    {'severity': 8192,
                                     'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'},
                                    {'severity': 8192,
                                     'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'}]},
 'hook_add_empty_string_alias': {'added': True, 'warnings': []},
 'hook_add_float_key': {'added': True,
                        'warnings': [{'severity': 8192,
                                      'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'},
                                     {'severity': 8192,
                                      'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'}]},
 'hook_key_coercions': {'runs': [{'label': 'numeric_string',
                                  'value': 'seed:numeric_string:integer_alias:float_key',
                                  'did_filter': 1,
                                  'did_filter_warnings': [],
                                  'warnings': [{'severity': 8192,
                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'},
                                               {'severity': 8192,
                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'}]},
                                 {'label': 'integer_alias',
                                  'value': 'seed:numeric_string:integer_alias:float_key',
                                  'did_filter': 2,
                                  'did_filter_warnings': [],
                                  'warnings': [{'severity': 8192,
                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'},
                                               {'severity': 8192,
                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'}]},
                                 {'label': 'leading_zero_string',
                                  'value': 'seed:leading_zero_string',
                                  'did_filter': 1,
                                  'did_filter_warnings': [],
                                  'warnings': []},
                                 {'label': 'boolean_key',
                                  'value': 'seed:boolean_key:integer_boolean_alias',
                                  'did_filter': 1,
                                  'did_filter_warnings': [],
                                  'warnings': []},
                                 {'label': 'integer_boolean_alias',
                                  'value': 'seed:boolean_key:integer_boolean_alias',
                                  'did_filter': 2,
                                  'did_filter_warnings': [],
                                  'warnings': []},
                                 {'label': 'null_key',
                                  'value': 'seed:null_key:empty_string_alias',
                                  'did_filter': 1,
                                  'did_filter_warnings': [{'severity': 8192,
                                                           'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'},
                                                          {'severity': 8192,
                                                           'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'}],
                                  'warnings': [{'severity': 8192,
                                                'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'},
                                               {'severity': 8192,
                                                'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'},
                                               {'severity': 8192,
                                                'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'},
                                               {'severity': 8192,
                                                'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'},
                                               {'severity': 8192,
                                                'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'},
                                               {'severity': 8192,
                                                'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'}]},
                                 {'label': 'empty_string_alias',
                                  'value': 'seed:null_key:empty_string_alias',
                                  'did_filter': 2,
                                  'did_filter_warnings': [],
                                  'warnings': [{'severity': 8192,
                                                'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'},
                                               {'severity': 8192,
                                                'message_hex': '5573696e67206e756c6c20617320616e206172726179206f666673657420697320646570726563617465642c2075736520616e20656d70747920737472696e6720696e7374656164'}]},
                                 {'label': 'float_key',
                                  'value': 'seed:numeric_string:integer_alias:float_key',
                                  'did_filter': 3,
                                  'did_filter_warnings': [{'severity': 8192,
                                                           'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'},
                                                          {'severity': 8192,
                                                           'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'}],
                                  'warnings': [{'severity': 8192,
                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'},
                                               {'severity': 8192,
                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'},
                                               {'severity': 8192,
                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'},
                                               {'severity': 8192,
                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'},
                                               {'severity': 8192,
                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'},
                                               {'severity': 8192,
                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'}]}],
                        'callback_trace': [{'callback': 'numeric_string',
                                            'current_filter': {'type': 'string', 'hex': '35'},
                                            'doing_any': True,
                                            'doing_exact': True,
                                            'did_filter': 1},
                                           {'callback': 'integer_alias',
                                            'current_filter': {'type': 'string', 'hex': '35'},
                                            'doing_any': True,
                                            'doing_exact': False,
                                            'did_filter': 1},
                                           {'callback': 'float_key',
                                            'current_filter': {'type': 'string', 'hex': '35'},
                                            'doing_any': True,
                                            'doing_exact': False,
                                            'did_filter': 1},
                                           {'callback': 'numeric_string',
                                            'current_filter': {'type': 'int', 'value': '5'},
                                            'doing_any': True,
                                            'doing_exact': False,
                                            'did_filter': 2},
                                           {'callback': 'integer_alias',
                                            'current_filter': {'type': 'int', 'value': '5'},
                                            'doing_any': True,
                                            'doing_exact': True,
                                            'did_filter': 2},
                                           {'callback': 'float_key',
                                            'current_filter': {'type': 'int', 'value': '5'},
                                            'doing_any': True,
                                            'doing_exact': False,
                                            'did_filter': 2},
                                           {'callback': 'leading_zero_string',
                                            'current_filter': {'type': 'string', 'hex': '3035'},
                                            'doing_any': True,
                                            'doing_exact': True,
                                            'did_filter': 1},
                                           {'callback': 'boolean_key',
                                            'current_filter': {'type': 'bool', 'value': True},
                                            'doing_any': True,
                                            'doing_exact': True,
                                            'did_filter': 1},
                                           {'callback': 'integer_boolean_alias',
                                            'current_filter': {'type': 'bool', 'value': True},
                                            'doing_any': True,
                                            'doing_exact': False,
                                            'did_filter': 1},
                                           {'callback': 'boolean_key',
                                            'current_filter': {'type': 'int', 'value': '1'},
                                            'doing_any': True,
                                            'doing_exact': False,
                                            'did_filter': 2},
                                           {'callback': 'integer_boolean_alias',
                                            'current_filter': {'type': 'int', 'value': '1'},
                                            'doing_any': True,
                                            'doing_exact': True,
                                            'did_filter': 2},
                                           {'callback': 'null_key',
                                            'current_filter': {'type': 'null'},
                                            'doing_any': True,
                                            'doing_exact': True,
                                            'did_filter': 1},
                                           {'callback': 'empty_string_alias',
                                            'current_filter': {'type': 'null'},
                                            'doing_any': True,
                                            'doing_exact': False,
                                            'did_filter': 1},
                                           {'callback': 'null_key',
                                            'current_filter': {'type': 'string', 'hex': ''},
                                            'doing_any': True,
                                            'doing_exact': True,
                                            'did_filter': 2},
                                           {'callback': 'empty_string_alias',
                                            'current_filter': {'type': 'string', 'hex': ''},
                                            'doing_any': True,
                                            'doing_exact': True,
                                            'did_filter': 2},
                                           {'callback': 'numeric_string',
                                            'current_filter': {'type': 'float', 'bits': '4016cccccccccccd'},
                                            'doing_any': True,
                                            'doing_exact': False,
                                            'did_filter': 3},
                                           {'callback': 'integer_alias',
                                            'current_filter': {'type': 'float', 'bits': '4016cccccccccccd'},
                                            'doing_any': True,
                                            'doing_exact': False,
                                            'did_filter': 3},
                                           {'callback': 'float_key',
                                            'current_filter': {'type': 'float', 'bits': '4016cccccccccccd'},
                                            'doing_any': True,
                                            'doing_exact': True,
                                            'did_filter': 3}],
                        'all_raw_names': [{'type': 'string', 'hex': '35'},
                                          {'type': 'int', 'value': '5'},
                                          {'type': 'string', 'hex': '3035'},
                                          {'type': 'bool', 'value': True},
                                          {'type': 'int', 'value': '1'},
                                          {'type': 'null'},
                                          {'type': 'string', 'hex': ''},
                                          {'type': 'float', 'bits': '4016cccccccccccd'}],
                        'aliases': {'numeric_string_to_int': True,
                                    'leading_zero_distinct': True,
                                    'bool_to_int': True,
                                    'null_to_empty_string': True},
                        'current_filter_after': {'type': 'bool', 'value': False},
                        'doing_filter_after': False},
 'special_float_hooks': {'add_false': {'added': True, 'warnings': []},
                         'add_int_zero': {'added': True, 'warnings': []},
                         'add_positive_float_zero': {'added': True, 'warnings': []},
                         'add_negative_float_zero': {'added': True, 'warnings': []},
                         'add_nan': {'added': True,
                                     'warnings': [{'severity': 2,
                                                   'message_hex': '54686520666c6f6174204e414e206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                  {'severity': 8192,
                                                   'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174204e414e20746f20696e74206c6f73657320707265636973696f6e'},
                                                  {'severity': 2,
                                                   'message_hex': '54686520666c6f6174204e414e206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                  {'severity': 8192,
                                                   'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174204e414e20746f20696e74206c6f73657320707265636973696f6e'}]},
                         'add_infinity': {'added': True,
                                          'warnings': [{'severity': 2,
                                                        'message_hex': '54686520666c6f617420494e46206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                       {'severity': 2,
                                                        'message_hex': '54686520666c6f617420494e46206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'}]},
                         'runs': [{'label': 'false',
                                   'hook': {'type': 'bool', 'value': False},
                                   'value': 'seed',
                                   'warnings': []},
                                  {'label': 'int_zero',
                                   'hook': {'type': 'int', 'value': '0'},
                                   'value': 'seed',
                                   'warnings': []},
                                  {'label': 'positive_float_zero',
                                   'hook': {'type': 'float', 'bits': '0000000000000000'},
                                   'value': 'seed',
                                   'warnings': []},
                                  {'label': 'negative_float_zero',
                                   'hook': {'type': 'float', 'bits': '8000000000000000'},
                                   'value': 'seed',
                                   'warnings': []},
                                  {'label': 'nan',
                                   'hook': {'type': 'float', 'special': 'nan'},
                                   'value': 'seed',
                                   'warnings': [{'severity': 2,
                                                 'message_hex': '54686520666c6f6174204e414e206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                {'severity': 8192,
                                                 'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174204e414e20746f20696e74206c6f73657320707265636973696f6e'},
                                                {'severity': 2,
                                                 'message_hex': '54686520666c6f6174204e414e206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                {'severity': 8192,
                                                 'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174204e414e20746f20696e74206c6f73657320707265636973696f6e'},
                                                {'severity': 2,
                                                 'message_hex': '54686520666c6f6174204e414e206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                {'severity': 8192,
                                                 'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174204e414e20746f20696e74206c6f73657320707265636973696f6e'},
                                                {'severity': 2,
                                                 'message_hex': '54686520666c6f6174204e414e206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                {'severity': 8192,
                                                 'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174204e414e20746f20696e74206c6f73657320707265636973696f6e'}]},
                                  {'label': 'infinity',
                                   'hook': {'type': 'float', 'bits': '7ff0000000000000'},
                                   'value': 'seed',
                                   'warnings': [{'severity': 2,
                                                 'message_hex': '54686520666c6f617420494e46206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                {'severity': 2,
                                                 'message_hex': '54686520666c6f617420494e46206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                {'severity': 2,
                                                 'message_hex': '54686520666c6f617420494e46206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                {'severity': 2,
                                                 'message_hex': '54686520666c6f617420494e46206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'}]}],
                         'callback_trace': [{'callback': 'false',
                                             'current_filter': {'type': 'bool', 'value': False},
                                             'doing_any': True,
                                             'doing_exact_original': True,
                                             'doing_false': True,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'int_zero',
                                             'current_filter': {'type': 'bool', 'value': False},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': True,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'positive_float_zero',
                                             'current_filter': {'type': 'bool', 'value': False},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': True,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'negative_float_zero',
                                             'current_filter': {'type': 'bool', 'value': False},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': True,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'nan',
                                             'current_filter': {'type': 'bool', 'value': False},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': True,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'infinity',
                                             'current_filter': {'type': 'bool', 'value': False},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': True,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'false',
                                             'current_filter': {'type': 'int', 'value': '0'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': True,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'int_zero',
                                             'current_filter': {'type': 'int', 'value': '0'},
                                             'doing_any': True,
                                             'doing_exact_original': True,
                                             'doing_false': False,
                                             'doing_int_zero': True,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'positive_float_zero',
                                             'current_filter': {'type': 'int', 'value': '0'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': True,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'negative_float_zero',
                                             'current_filter': {'type': 'int', 'value': '0'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': True,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'nan',
                                             'current_filter': {'type': 'int', 'value': '0'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': True,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'infinity',
                                             'current_filter': {'type': 'int', 'value': '0'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': True,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'false',
                                             'current_filter': {'type': 'float', 'bits': '0000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'int_zero',
                                             'current_filter': {'type': 'float', 'bits': '0000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'positive_float_zero',
                                             'current_filter': {'type': 'float', 'bits': '0000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': True,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'negative_float_zero',
                                             'current_filter': {'type': 'float', 'bits': '0000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': True,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'nan',
                                             'current_filter': {'type': 'float', 'bits': '0000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'infinity',
                                             'current_filter': {'type': 'float', 'bits': '0000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'false',
                                             'current_filter': {'type': 'float', 'bits': '8000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'int_zero',
                                             'current_filter': {'type': 'float', 'bits': '8000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'positive_float_zero',
                                             'current_filter': {'type': 'float', 'bits': '8000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': True,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'negative_float_zero',
                                             'current_filter': {'type': 'float', 'bits': '8000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': True,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'nan',
                                             'current_filter': {'type': 'float', 'bits': '8000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'infinity',
                                             'current_filter': {'type': 'float', 'bits': '8000000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': True,
                                             'doing_negative_float_zero': True,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'false',
                                             'current_filter': {'type': 'float', 'special': 'nan'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'int_zero',
                                             'current_filter': {'type': 'float', 'special': 'nan'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'positive_float_zero',
                                             'current_filter': {'type': 'float', 'special': 'nan'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'negative_float_zero',
                                             'current_filter': {'type': 'float', 'special': 'nan'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'nan',
                                             'current_filter': {'type': 'float', 'special': 'nan'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'infinity',
                                             'current_filter': {'type': 'float', 'special': 'nan'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': False},
                                            {'callback': 'false',
                                             'current_filter': {'type': 'float', 'bits': '7ff0000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': True},
                                            {'callback': 'int_zero',
                                             'current_filter': {'type': 'float', 'bits': '7ff0000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': True},
                                            {'callback': 'positive_float_zero',
                                             'current_filter': {'type': 'float', 'bits': '7ff0000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': True},
                                            {'callback': 'negative_float_zero',
                                             'current_filter': {'type': 'float', 'bits': '7ff0000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': True},
                                            {'callback': 'nan',
                                             'current_filter': {'type': 'float', 'bits': '7ff0000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': False,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': True},
                                            {'callback': 'infinity',
                                             'current_filter': {'type': 'float', 'bits': '7ff0000000000000'},
                                             'doing_any': True,
                                             'doing_exact_original': True,
                                             'doing_false': False,
                                             'doing_int_zero': False,
                                             'doing_positive_float_zero': False,
                                             'doing_negative_float_zero': False,
                                             'doing_nan': False,
                                             'doing_infinity': True}],
                         'all_raw_names': [{'type': 'bool', 'value': False},
                                           {'type': 'int', 'value': '0'},
                                           {'type': 'float', 'bits': '0000000000000000'},
                                           {'type': 'float', 'bits': '8000000000000000'},
                                           {'type': 'float', 'special': 'nan'},
                                           {'type': 'float', 'bits': '7ff0000000000000'}],
                         'remove_false': {'value': True, 'warnings': []},
                         'remove_int_zero': {'value': True, 'warnings': []},
                         'remove_positive_float_zero': {'value': True, 'warnings': []},
                         'remove_negative_float_zero': {'value': True, 'warnings': []},
                         'remove_nan': {'value': True,
                                        'warnings': [{'severity': 2,
                                                      'message_hex': '54686520666c6f6174204e414e206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                     {'severity': 8192,
                                                      'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174204e414e20746f20696e74206c6f73657320707265636973696f6e'},
                                                     {'severity': 2,
                                                      'message_hex': '54686520666c6f6174204e414e206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                     {'severity': 8192,
                                                      'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174204e414e20746f20696e74206c6f73657320707265636973696f6e'},
                                                     {'severity': 2,
                                                      'message_hex': '54686520666c6f6174204e414e206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                     {'severity': 8192,
                                                      'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174204e414e20746f20696e74206c6f73657320707265636973696f6e'}]},
                         'remove_infinity': {'value': True,
                                             'warnings': [{'severity': 2,
                                                           'message_hex': '54686520666c6f617420494e46206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                          {'severity': 2,
                                                           'message_hex': '54686520666c6f617420494e46206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                          {'severity': 2,
                                                           'message_hex': '54686520666c6f617420494e46206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'},
                                                          {'severity': 2,
                                                           'message_hex': '54686520666c6f617420494e46206973206e6f7420726570726573656e7461626c6520617320616e20696e742c2063617374206f63637572726564'}]}},
 'priority_key_coercions': {'value': 'seed:string_07:int_7:ordinary_10:exponent_100:int_100:big_2p53:big_2p53_plus1:overflow_decimal:overflow_exponent',
                            'has_string_priority': {'type': 'string', 'hex': '3037'},
                            'has_int_priority': True,
                            'removed_by_numeric_alias': True,
                            'int_priority_after_remove': False,
                            'trace': ['string_07',
                                      'int_7',
                                      'ordinary_10',
                                      'exponent_100',
                                      'int_100',
                                      'big_2p53',
                                      'big_2p53_plus1',
                                      'overflow_decimal',
                                      'overflow_exponent']},
 'active_priority_mutations': {'current_int7_readd_int': {'value': {'value': 'seed:lower:trigger:tail',
                                                                    'trace': ['lower',
                                                                              'trigger',
                                                                              {'removed': True,
                                                                               'readded': True},
                                                                              'tail']},
                                                          'warnings': []},
                               'current_int7_readd_numeric_string': {'value': {'value': 'seed:lower:trigger',
                                                                               'trace': ['lower',
                                                                                         'trigger',
                                                                                         {'removed': True,
                                                                                          'readded': True}]},
                                                                     'warnings': []},
                               'current_int7_readd_float': {'value': {'value': 'seed:lower:trigger',
                                                                      'trace': ['lower',
                                                                                'trigger',
                                                                                {'removed': True,
                                                                                 'readded': True}]},
                                                            'warnings': [{'severity': 8192,
                                                                          'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3920746f20696e74206c6f73657320707265636973696f6e'},
                                                                         {'severity': 8192,
                                                                          'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3920746f20696e74206c6f73657320707265636973696f6e'},
                                                                         {'severity': 8192,
                                                                          'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3920746f20696e74206c6f73657320707265636973696f6e'},
                                                                         {'severity': 8192,
                                                                          'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3920746f20696e74206c6f73657320707265636973696f6e'},
                                                                         {'severity': 8192,
                                                                          'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3920746f20696e74206c6f73657320707265636973696f6e'},
                                                                         {'severity': 8192,
                                                                          'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3920746f20696e74206c6f73657320707265636973696f6e'}]},
                               'current_int1_readd_bool': {'value': {'value': 'seed:lower:trigger',
                                                                     'trace': ['lower',
                                                                               'trigger',
                                                                               {'removed': True,
                                                                                'readded': True}]},
                                                           'warnings': []},
                               'current_int1_readd_int': {'value': {'value': 'seed:lower:trigger:tail',
                                                                    'trace': ['lower',
                                                                              'trigger',
                                                                              {'removed': True,
                                                                               'readded': True},
                                                                              'tail']},
                                                          'warnings': []},
                               'non_numeric_string_added_at_int_zero': {'value': {'value': 'seed:trigger:late_abc:tail',
                                                                                  'trace': ['trigger',
                                                                                            'late_abc',
                                                                                            'tail']},
                                                                        'warnings': []}},
 'priority_numeric_sort_matrix': {'pairs': {'int_2p53_reverse': {'value': 'seed',
                                                                 'dispatch': ['base', 'plus_one'],
                                                                 'callback_priorities': [{'label': 'plus_one',
                                                                                          'priority': {'type': 'int',
                                                                                                       'value': '9007199254740993'},
                                                                                          'found_at': {'type': 'int',
                                                                                                       'value': '9007199254740993'}},
                                                                                         {'label': 'base',
                                                                                          'priority': {'type': 'int',
                                                                                                       'value': '9007199254740992'},
                                                                                          'found_at': {'type': 'int',
                                                                                                       'value': '9007199254740992'}}]},
                                            'int_2p53_forward': {'value': 'seed',
                                                                 'dispatch': ['base', 'plus_one'],
                                                                 'callback_priorities': [{'label': 'base',
                                                                                          'priority': {'type': 'int',
                                                                                                       'value': '9007199254740992'},
                                                                                          'found_at': {'type': 'int',
                                                                                                       'value': '9007199254740992'}},
                                                                                         {'label': 'plus_one',
                                                                                          'priority': {'type': 'int',
                                                                                                       'value': '9007199254740993'},
                                                                                          'found_at': {'type': 'int',
                                                                                                       'value': '9007199254740993'}}]},
                                            'string_plus_large_reverse': {'value': 'seed',
                                                                          'dispatch': ['int_plus_one',
                                                                                       'plus_string'],
                                                                          'callback_priorities': [{'label': 'int_plus_one',
                                                                                                   'priority': {'type': 'int',
                                                                                                                'value': '9007199254740993'},
                                                                                                   'found_at': {'type': 'int',
                                                                                                                'value': '9007199254740993'}},
                                                                                                  {'label': 'plus_string',
                                                                                                   'priority': {'type': 'string',
                                                                                                                'hex': '2b39303037313939323534373430393933'},
                                                                                                   'found_at': {'type': 'string',
                                                                                                                'hex': '2b39303037313939323534373430393933'}}]},
                                            'string_plus_large_forward': {'value': 'seed',
                                                                          'dispatch': ['plus_string',
                                                                                       'int_plus_one'],
                                                                          'callback_priorities': [{'label': 'plus_string',
                                                                                                   'priority': {'type': 'string',
                                                                                                                'hex': '2b39303037313939323534373430393933'},
                                                                                                   'found_at': {'type': 'string',
                                                                                                                'hex': '2b39303037313939323534373430393933'}},
                                                                                                  {'label': 'int_plus_one',
                                                                                                   'priority': {'type': 'int',
                                                                                                                'value': '9007199254740993'},
                                                                                                   'found_at': {'type': 'int',
                                                                                                                'value': '9007199254740993'}}]},
                                            'string_zero_large_reverse': {'value': 'seed',
                                                                          'dispatch': ['int_plus_one',
                                                                                       'zero_string'],
                                                                          'callback_priorities': [{'label': 'int_plus_one',
                                                                                                   'priority': {'type': 'int',
                                                                                                                'value': '9007199254740993'},
                                                                                                   'found_at': {'type': 'int',
                                                                                                                'value': '9007199254740993'}},
                                                                                                  {'label': 'zero_string',
                                                                                                   'priority': {'type': 'string',
                                                                                                                'hex': '3039303037313939323534373430393933'},
                                                                                                   'found_at': {'type': 'string',
                                                                                                                'hex': '3039303037313939323534373430393933'}}]},
                                            'string_zero_large_forward': {'value': 'seed',
                                                                          'dispatch': ['zero_string',
                                                                                       'int_plus_one'],
                                                                          'callback_priorities': [{'label': 'zero_string',
                                                                                                   'priority': {'type': 'string',
                                                                                                                'hex': '3039303037313939323534373430393933'},
                                                                                                   'found_at': {'type': 'string',
                                                                                                                'hex': '3039303037313939323534373430393933'}},
                                                                                                  {'label': 'int_plus_one',
                                                                                                   'priority': {'type': 'int',
                                                                                                                'value': '9007199254740993'},
                                                                                                   'found_at': {'type': 'int',
                                                                                                                'value': '9007199254740993'}}]},
                                            'string_plus_base_reverse': {'value': 'seed',
                                                                         'dispatch': ['int_plus_one',
                                                                                      'plus_base'],
                                                                         'callback_priorities': [{'label': 'int_plus_one',
                                                                                                  'priority': {'type': 'int',
                                                                                                               'value': '9007199254740993'},
                                                                                                  'found_at': {'type': 'int',
                                                                                                               'value': '9007199254740993'}},
                                                                                                 {'label': 'plus_base',
                                                                                                  'priority': {'type': 'string',
                                                                                                               'hex': '2b39303037313939323534373430393932'},
                                                                                                  'found_at': {'type': 'string',
                                                                                                               'hex': '2b39303037313939323534373430393932'}}]},
                                            'string_plus_base_forward': {'value': 'seed',
                                                                         'dispatch': ['plus_base',
                                                                                      'int_plus_one'],
                                                                         'callback_priorities': [{'label': 'plus_base',
                                                                                                  'priority': {'type': 'string',
                                                                                                               'hex': '2b39303037313939323534373430393932'},
                                                                                                  'found_at': {'type': 'string',
                                                                                                               'hex': '2b39303037313939323534373430393932'}},
                                                                                                 {'label': 'int_plus_one',
                                                                                                  'priority': {'type': 'int',
                                                                                                               'value': '9007199254740993'},
                                                                                                  'found_at': {'type': 'int',
                                                                                                               'value': '9007199254740993'}}]},
                                            'string_fraction_large_reverse': {'value': 'seed',
                                                                              'dispatch': ['int_plus_one',
                                                                                           'fraction_string'],
                                                                              'callback_priorities': [{'label': 'int_plus_one',
                                                                                                       'priority': {'type': 'int',
                                                                                                                    'value': '9007199254740993'},
                                                                                                       'found_at': {'type': 'int',
                                                                                                                    'value': '9007199254740993'}},
                                                                                                      {'label': 'fraction_string',
                                                                                                       'priority': {'type': 'string',
                                                                                                                    'hex': '393030373139393235343734303939322e30'},
                                                                                                       'found_at': {'type': 'string',
                                                                                                                    'hex': '393030373139393235343734303939322e30'}}]},
                                            'string_fraction_large_forward': {'value': 'seed',
                                                                              'dispatch': ['fraction_string',
                                                                                           'int_plus_one'],
                                                                              'callback_priorities': [{'label': 'fraction_string',
                                                                                                       'priority': {'type': 'string',
                                                                                                                    'hex': '393030373139393235343734303939322e30'},
                                                                                                       'found_at': {'type': 'string',
                                                                                                                    'hex': '393030373139393235343734303939322e30'}},
                                                                                                      {'label': 'int_plus_one',
                                                                                                       'priority': {'type': 'int',
                                                                                                                    'value': '9007199254740993'},
                                                                                                       'found_at': {'type': 'int',
                                                                                                                    'value': '9007199254740993'}}]},
                                            'string_prefix_10_reverse': {'value': 'seed',
                                                                         'dispatch': ['int10', 'prefix12'],
                                                                         'callback_priorities': [{'label': 'int10',
                                                                                                  'priority': {'type': 'int',
                                                                                                               'value': '10'},
                                                                                                  'found_at': {'type': 'int',
                                                                                                               'value': '10'}},
                                                                                                 {'label': 'prefix12',
                                                                                                  'priority': {'type': 'string',
                                                                                                               'hex': '3132666f6f'},
                                                                                                  'found_at': {'type': 'string',
                                                                                                               'hex': '3132666f6f'}}]},
                                            'string_prefix_10_forward': {'value': 'seed',
                                                                         'dispatch': ['int10', 'prefix12'],
                                                                         'callback_priorities': [{'label': 'prefix12',
                                                                                                  'priority': {'type': 'string',
                                                                                                               'hex': '3132666f6f'},
                                                                                                  'found_at': {'type': 'string',
                                                                                                               'hex': '3132666f6f'}},
                                                                                                 {'label': 'int10',
                                                                                                  'priority': {'type': 'int',
                                                                                                               'value': '10'},
                                                                                                  'found_at': {'type': 'int',
                                                                                                               'value': '10'}}]},
                                            'string_prefix_20_reverse': {'value': 'seed',
                                                                         'dispatch': ['prefix12', 'int20'],
                                                                         'callback_priorities': [{'label': 'int20',
                                                                                                  'priority': {'type': 'int',
                                                                                                               'value': '20'},
                                                                                                  'found_at': {'type': 'int',
                                                                                                               'value': '20'}},
                                                                                                 {'label': 'prefix12',
                                                                                                  'priority': {'type': 'string',
                                                                                                               'hex': '3132666f6f'},
                                                                                                  'found_at': {'type': 'string',
                                                                                                               'hex': '3132666f6f'}}]},
                                            'string_prefix_20_forward': {'value': 'seed',
                                                                         'dispatch': ['prefix12', 'int20'],
                                                                         'callback_priorities': [{'label': 'prefix12',
                                                                                                  'priority': {'type': 'string',
                                                                                                               'hex': '3132666f6f'},
                                                                                                  'found_at': {'type': 'string',
                                                                                                               'hex': '3132666f6f'}},
                                                                                                 {'label': 'int20',
                                                                                                  'priority': {'type': 'int',
                                                                                                               'value': '20'},
                                                                                                  'found_at': {'type': 'int',
                                                                                                               'value': '20'}}]},
                                            'string_inf_zero_reverse': {'value': 'seed',
                                                                        'dispatch': ['int0', 'string_inf'],
                                                                        'callback_priorities': [{'label': 'int0',
                                                                                                 'priority': {'type': 'int',
                                                                                                              'value': '0'},
                                                                                                 'found_at': {'type': 'int',
                                                                                                              'value': '0'}},
                                                                                                {'label': 'string_inf',
                                                                                                 'priority': {'type': 'string',
                                                                                                              'hex': '494e46'},
                                                                                                 'found_at': {'type': 'string',
                                                                                                              'hex': '494e46'}}]},
                                            'string_inf_zero_forward': {'value': 'seed',
                                                                        'dispatch': ['string_inf', 'int0'],
                                                                        'callback_priorities': [{'label': 'string_inf',
                                                                                                 'priority': {'type': 'string',
                                                                                                              'hex': '494e46'},
                                                                                                 'found_at': {'type': 'string',
                                                                                                              'hex': '494e46'}},
                                                                                                {'label': 'int0',
                                                                                                 'priority': {'type': 'int',
                                                                                                              'value': '0'},
                                                                                                 'found_at': {'type': 'int',
                                                                                                              'value': '0'}}]},
                                            'string_nan_zero_reverse': {'value': 'seed',
                                                                        'dispatch': ['int0', 'string_nan'],
                                                                        'callback_priorities': [{'label': 'int0',
                                                                                                 'priority': {'type': 'int',
                                                                                                              'value': '0'},
                                                                                                 'found_at': {'type': 'int',
                                                                                                              'value': '0'}},
                                                                                                {'label': 'string_nan',
                                                                                                 'priority': {'type': 'string',
                                                                                                              'hex': '4e414e'},
                                                                                                 'found_at': {'type': 'string',
                                                                                                              'hex': '4e414e'}}]},
                                            'string_nan_zero_forward': {'value': 'seed',
                                                                        'dispatch': ['string_nan', 'int0'],
                                                                        'callback_priorities': [{'label': 'string_nan',
                                                                                                 'priority': {'type': 'string',
                                                                                                              'hex': '4e414e'},
                                                                                                 'found_at': {'type': 'string',
                                                                                                              'hex': '4e414e'}},
                                                                                                {'label': 'int0',
                                                                                                 'priority': {'type': 'int',
                                                                                                              'value': '0'},
                                                                                                 'found_at': {'type': 'int',
                                                                                                              'value': '0'}}]},
                                            'abc_zero_reverse': {'value': 'seed',
                                                                 'dispatch': ['zero', 'abc'],
                                                                 'callback_priorities': [{'label': 'zero',
                                                                                          'priority': {'type': 'int',
                                                                                                       'value': '0'},
                                                                                          'found_at': {'type': 'int',
                                                                                                       'value': '0'}},
                                                                                         {'label': 'abc',
                                                                                          'priority': {'type': 'string',
                                                                                                       'hex': '616263'},
                                                                                          'found_at': {'type': 'string',
                                                                                                       'hex': '616263'}}]},
                                            'abc_zero_forward': {'value': 'seed',
                                                                 'dispatch': ['abc', 'zero'],
                                                                 'callback_priorities': [{'label': 'abc',
                                                                                          'priority': {'type': 'string',
                                                                                                       'hex': '616263'},
                                                                                          'found_at': {'type': 'string',
                                                                                                       'hex': '616263'}},
                                                                                         {'label': 'zero',
                                                                                          'priority': {'type': 'int',
                                                                                                       'value': '0'},
                                                                                          'found_at': {'type': 'int',
                                                                                                       'value': '0'}}]},
                                            '07_7_reverse': {'value': 'seed',
                                                             'dispatch': ['int7', 'string07'],
                                                             'callback_priorities': [{'label': 'int7',
                                                                                      'priority': {'type': 'int',
                                                                                                   'value': '7'},
                                                                                      'found_at': {'type': 'int',
                                                                                                   'value': '7'}},
                                                                                     {'label': 'string07',
                                                                                      'priority': {'type': 'string',
                                                                                                   'hex': '3037'},
                                                                                      'found_at': {'type': 'string',
                                                                                                   'hex': '3037'}}]},
                                            '07_7_forward': {'value': 'seed',
                                                             'dispatch': ['string07', 'int7'],
                                                             'callback_priorities': [{'label': 'string07',
                                                                                      'priority': {'type': 'string',
                                                                                                   'hex': '3037'},
                                                                                      'found_at': {'type': 'string',
                                                                                                   'hex': '3037'}},
                                                                                     {'label': 'int7',
                                                                                      'priority': {'type': 'int',
                                                                                                   'value': '7'},
                                                                                      'found_at': {'type': 'int',
                                                                                                   'value': '7'}}]},
                                            'exp100_100_reverse': {'value': 'seed',
                                                                   'dispatch': ['int100', 'exp100'],
                                                                   'callback_priorities': [{'label': 'int100',
                                                                                            'priority': {'type': 'int',
                                                                                                         'value': '100'},
                                                                                            'found_at': {'type': 'int',
                                                                                                         'value': '100'}},
                                                                                           {'label': 'exp100',
                                                                                            'priority': {'type': 'string',
                                                                                                         'hex': '316532'},
                                                                                            'found_at': {'type': 'string',
                                                                                                         'hex': '316532'}}]},
                                            'exp100_100_forward': {'value': 'seed',
                                                                   'dispatch': ['exp100', 'int100'],
                                                                   'callback_priorities': [{'label': 'exp100',
                                                                                            'priority': {'type': 'string',
                                                                                                         'hex': '316532'},
                                                                                            'found_at': {'type': 'string',
                                                                                                         'hex': '316532'}},
                                                                                           {'label': 'int100',
                                                                                            'priority': {'type': 'int',
                                                                                                         'value': '100'},
                                                                                            'found_at': {'type': 'int',
                                                                                                         'value': '100'}}]}},
                                  'active_add': {'int_2p53_to_plus1': {'value': 'seed',
                                                                       'trace': ['trigger', 'late', 'tail'],
                                                                       'trigger_priority': {'type': 'int',
                                                                                            'value': '9007199254740992'},
                                                                       'late_priority': {'type': 'int',
                                                                                         'value': '9007199254740993'},
                                                                       'tail_priority': {'type': 'int',
                                                                                         'value': '9007199254740999'}},
                                                 'plus1_to_int_2p53': {'value': 'seed',
                                                                       'trace': ['trigger', 'tail'],
                                                                       'trigger_priority': {'type': 'int',
                                                                                            'value': '9007199254740993'},
                                                                       'late_priority': {'type': 'int',
                                                                                         'value': '9007199254740992'},
                                                                       'tail_priority': {'type': 'int',
                                                                                         'value': '9007199254740999'}},
                                                 'int_plus1_to_string_plus_large': {'value': 'seed',
                                                                                    'trace': ['trigger',
                                                                                              'late',
                                                                                              'tail'],
                                                                                    'trigger_priority': {'type': 'int',
                                                                                                         'value': '9007199254740992'},
                                                                                    'late_priority': {'type': 'string',
                                                                                                      'hex': '2b39303037313939323534373430393933'},
                                                                                    'tail_priority': {'type': 'int',
                                                                                                      'value': '9007199254740999'}},
                                                 'string_plus_large_to_int_base': {'value': 'seed',
                                                                                   'trace': ['trigger',
                                                                                             'late',
                                                                                             'tail'],
                                                                                   'trigger_priority': {'type': 'string',
                                                                                                        'hex': '2b39303037313939323534373430393933'},
                                                                                   'late_priority': {'type': 'int',
                                                                                                     'value': '9007199254740992'},
                                                                                   'tail_priority': {'type': 'int',
                                                                                                     'value': '9007199254740999'}},
                                                 'int_plus1_to_string_zero_large': {'value': 'seed',
                                                                                    'trace': ['trigger',
                                                                                              'late',
                                                                                              'tail'],
                                                                                    'trigger_priority': {'type': 'int',
                                                                                                         'value': '9007199254740992'},
                                                                                    'late_priority': {'type': 'string',
                                                                                                      'hex': '3039303037313939323534373430393933'},
                                                                                    'tail_priority': {'type': 'int',
                                                                                                      'value': '9007199254740999'}},
                                                 'string_zero_large_to_int_base': {'value': 'seed',
                                                                                   'trace': ['trigger',
                                                                                             'late',
                                                                                             'tail'],
                                                                                   'trigger_priority': {'type': 'string',
                                                                                                        'hex': '3039303037313939323534373430393933'},
                                                                                   'late_priority': {'type': 'int',
                                                                                                     'value': '9007199254740992'},
                                                                                   'tail_priority': {'type': 'int',
                                                                                                     'value': '9007199254740999'}},
                                                 'int_plus1_to_string_plus_base': {'value': 'seed',
                                                                                   'trace': ['trigger',
                                                                                             'late',
                                                                                             'tail'],
                                                                                   'trigger_priority': {'type': 'int',
                                                                                                        'value': '9007199254740993'},
                                                                                   'late_priority': {'type': 'string',
                                                                                                     'hex': '2b39303037313939323534373430393932'},
                                                                                   'tail_priority': {'type': 'int',
                                                                                                     'value': '9007199254740999'}},
                                                 'string_plus_base_to_int_plus1': {'value': 'seed',
                                                                                   'trace': ['trigger',
                                                                                             'late',
                                                                                             'tail'],
                                                                                   'trigger_priority': {'type': 'string',
                                                                                                        'hex': '2b39303037313939323534373430393932'},
                                                                                   'late_priority': {'type': 'int',
                                                                                                     'value': '9007199254740993'},
                                                                                   'tail_priority': {'type': 'int',
                                                                                                     'value': '9007199254740999'}},
                                                 'current_string_prefix_add_int12': {'value': 'seed',
                                                                                     'trace': ['trigger',
                                                                                               'late',
                                                                                               'tail'],
                                                                                     'trigger_priority': {'type': 'string',
                                                                                                          'hex': '3132666f6f'},
                                                                                     'late_priority': {'type': 'int',
                                                                                                       'value': '12'},
                                                                                     'tail_priority': {'type': 'int',
                                                                                                       'value': '9007199254740999'}},
                                                 'current_int12_add_string_prefix': {'value': 'seed',
                                                                                     'trace': ['trigger',
                                                                                               'late',
                                                                                               'tail'],
                                                                                     'trigger_priority': {'type': 'int',
                                                                                                          'value': '12'},
                                                                                     'late_priority': {'type': 'string',
                                                                                                       'hex': '3132666f6f'},
                                                                                     'tail_priority': {'type': 'int',
                                                                                                       'value': '9007199254740999'}},
                                                 'current_string_inf_add_int0': {'value': 'seed',
                                                                                 'trace': ['trigger',
                                                                                           'late',
                                                                                           'tail'],
                                                                                 'trigger_priority': {'type': 'string',
                                                                                                      'hex': '494e46'},
                                                                                 'late_priority': {'type': 'int',
                                                                                                   'value': '0'},
                                                                                 'tail_priority': {'type': 'int',
                                                                                                   'value': '9007199254740999'}},
                                                 'current_int0_add_string_inf': {'value': 'seed',
                                                                                 'trace': ['trigger',
                                                                                           'late',
                                                                                           'tail'],
                                                                                 'trigger_priority': {'type': 'int',
                                                                                                      'value': '0'},
                                                                                 'late_priority': {'type': 'string',
                                                                                                   'hex': '494e46'},
                                                                                 'tail_priority': {'type': 'int',
                                                                                                   'value': '9007199254740999'}},
                                                 'current_abc_add_zero': {'value': 'seed',
                                                                          'trace': ['trigger',
                                                                                    'late',
                                                                                    'tail'],
                                                                          'trigger_priority': {'type': 'string',
                                                                                               'hex': '616263'},
                                                                          'late_priority': {'type': 'int',
                                                                                            'value': '0'},
                                                                          'tail_priority': {'type': 'int',
                                                                                            'value': '9007199254740999'}},
                                                 'current_zero_add_abc': {'value': 'seed',
                                                                          'trace': ['trigger',
                                                                                    'late',
                                                                                    'tail'],
                                                                          'trigger_priority': {'type': 'int',
                                                                                               'value': '0'},
                                                                          'late_priority': {'type': 'string',
                                                                                            'hex': '616263'},
                                                                          'tail_priority': {'type': 'int',
                                                                                            'value': '9007199254740999'}},
                                                 'current_07_add_7': {'value': 'seed',
                                                                      'trace': ['trigger', 'late', 'tail'],
                                                                      'trigger_priority': {'type': 'string',
                                                                                           'hex': '3037'},
                                                                      'late_priority': {'type': 'int',
                                                                                        'value': '7'},
                                                                      'tail_priority': {'type': 'int',
                                                                                        'value': '9007199254740999'}},
                                                 'current_7_add_07': {'value': 'seed',
                                                                      'trace': ['trigger', 'late', 'tail'],
                                                                      'trigger_priority': {'type': 'int',
                                                                                           'value': '7'},
                                                                      'late_priority': {'type': 'string',
                                                                                        'hex': '3037'},
                                                                      'tail_priority': {'type': 'int',
                                                                                        'value': '9007199254740999'}},
                                                 'current_exp_add_100': {'value': 'seed',
                                                                         'trace': ['trigger', 'late', 'tail'],
                                                                         'trigger_priority': {'type': 'string',
                                                                                              'hex': '316532'},
                                                                         'late_priority': {'type': 'int',
                                                                                           'value': '100'},
                                                                         'tail_priority': {'type': 'int',
                                                                                           'value': '9007199254740999'}},
                                                 'current_100_add_exp': {'value': 'seed',
                                                                         'trace': ['trigger', 'late', 'tail'],
                                                                         'trigger_priority': {'type': 'int',
                                                                                              'value': '100'},
                                                                         'late_priority': {'type': 'string',
                                                                                           'hex': '316532'},
                                                                         'tail_priority': {'type': 'int',
                                                                                           'value': '9007199254740999'}}}},
 'null_id_short_circuit': {'added': True, 'any_after_add': False, 'remove_missing': False},
 'invalid_hook_and_priority_types': {'invalid_array_hook': {'throwable': {'class_hex': '547970654572726f72',
                                                                          'message_hex': '43616e6e6f7420616363657373206f6666736574206f66207479706520617272617920696e206973736574206f7220656d707479'}},
                                     'invalid_object_hook': {'throwable': {'class_hex': '547970654572726f72',
                                                                           'message_hex': '43616e6e6f7420616363657373206f6666736574206f66207479706520737464436c61737320696e206973736574206f7220656d707479'}},
                                     'invalid_array_has_hook': {'throwable': {'class_hex': '547970654572726f72',
                                                                              'message_hex': '43616e6e6f7420616363657373206f6666736574206f66207479706520617272617920696e206973736574206f7220656d707479'}},
                                     'invalid_object_remove_hook': {'throwable': {'class_hex': '547970654572726f72',
                                                                                  'message_hex': '43616e6e6f7420616363657373206f6666736574206f66207479706520737464436c61737320696e206973736574206f7220656d707479'}},
                                     'invalid_array_apply_hook': {'throwable': {'class_hex': '547970654572726f72',
                                                                                'message_hex': '43616e6e6f7420616363657373206f6666736574206f66207479706520617272617920696e206973736574206f7220656d707479'}},
                                     'invalid_object_did_hook': {'throwable': {'class_hex': '547970654572726f72',
                                                                               'message_hex': '43616e6e6f7420616363657373206f6666736574206f66207479706520737464436c61737320696e206973736574206f7220656d707479'}},
                                     'invalid_array_priority': {'throwable': {'class_hex': '547970654572726f72',
                                                                              'message_hex': '43616e6e6f7420616363657373206f6666736574206f66207479706520617272617920696e206973736574206f7220656d707479'}},
                                     'invalid_object_priority': {'throwable': {'class_hex': '547970654572726f72',
                                                                               'message_hex': '43616e6e6f7420616363657373206f6666736574206f66207479706520737464436c61737320696e206973736574206f7220656d707479'}},
                                     'remove_existing_array_priority': {'throwable': {'class_hex': '547970654572726f72',
                                                                                      'message_hex': '43616e6e6f7420616363657373206f6666736574206f662074797065206172726179206f6e206172726179'}}},
 'ignored_nonscalar_priority_and_hook': {'has_filter_array_priority': {'type': 'int', 'value': '7'},
                                         'doing_filter_array_hook': False},
 'priority_warning_rebind': {'outer_added': True,
                             'warnings': [{'severity': 8192,
                                           'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'},
                                          {'severity': 8192,
                                           'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420352e3720746f20696e74206c6f73657320707265636973696f6e'}],
                             'reentry': {'sentinel_removed': True,
                                         'replacement_added': True,
                                         'replacement_priority': 5},
                             'outer_visible_after': False,
                             'replacement_priority_after': 5,
                             'value': 'seed:rebound',
                             'trace': ['rebound']},
 'accepted_args_warning_throw': {'warnings': [{'severity': 2,
                                               'message_hex': '4f626a656374206f6620636c61737320737464436c61737320636f756c64206e6f7420626520636f6e76657274656420746f20696e74'}],
                                 'reentry': {'outer_visible_before': False, 'nested_added': True},
                                 'outcome': {'throwable': {'class_hex': '52756e74696d65457863657074696f6e',
                                                           'message_hex': '63616c6c61626c652061636365707465645f61726773207761726e696e672068616e646c6572207468726577'}},
                                 'outer_visible_after': False,
                                 'nested_priority_after': 5,
                                 'value': 'seed:nested',
                                 'trace': ['nested']},
 'all_boundary_reentry': {'base_all_listener_removed': True,
                          'empty_all_without_named_callback': {'all_added': True,
                                                               'apply': {'value': 'seed',
                                                                         'warnings': [{'severity': 8192,
                                                                                       'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030302e3520746f20696e74206c6f73657320707265636973696f6e'},
                                                                                      {'severity': 8192,
                                                                                       'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030302e3520746f20696e74206c6f73657320707265636973696f6e'},
                                                                                      {'severity': 2,
                                                                                       'message_hex': '556e646566696e6564206172726179206b65792030'},
                                                                                      {'severity': 2,
                                                                                       'message_hex': '666f7265616368282920617267756d656e74206d757374206265206f6620747970652061727261797c6f626a6563742c206e756c6c20676976656e'},
                                                                                      {'severity': 8192,
                                                                                       'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030302e3520746f20696e74206c6f73657320707265636973696f6e'}]},
                                                               'current_filter_after': {'type': 'string',
                                                                                        'hex': '63616c6c61626c652d696e76616c69642d6f626a656374'},
                                                               'doing_filter_after': True,
                                                               'all_remove_result': False},
                          'all_added_during_named_read_warning': {'named_add': {'value': True,
                                                                                'warnings': [{'severity': 8192,
                                                                                              'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030312e3520746f20696e74206c6f73657320707265636973696f6e'},
                                                                                             {'severity': 8192,
                                                                                              'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030312e3520746f20696e74206c6f73657320707265636973696f6e'},
                                                                                             {'severity': 8192,
                                                                                              'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030312e3520746f20696e74206c6f73657320707265636973696f6e'}]},
                                                                  'apply_value': None,
                                                                  'apply_throwable': {'class_hex': '4572726f72',
                                                                                      'message_hex': '43616c6c20746f2061206d656d6265722066756e6374696f6e206170706c795f66696c746572732829206f6e206e756c6c'},
                                                                  'warnings': [{'severity': 8192,
                                                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030312e3520746f20696e74206c6f73657320707265636973696f6e'},
                                                                               {'severity': 8192,
                                                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030312e3520746f20696e74206c6f73657320707265636973696f6e'},
                                                                               {'severity': 8192,
                                                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030312e3520746f20696e74206c6f73657320707265636973696f6e'},
                                                                               {'severity': 8192,
                                                                                'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030312e3520746f20696e74206c6f73657320707265636973696f6e'}],
                                                                  'injected': {'warning_count_at_injection': 4,
                                                                               'current_filter_before_add': {'type': 'float',
                                                                                                             'bits': '40f8a89800000000'},
                                                                               'doing_before_add': True,
                                                                               'doing_exact_before_add': True,
                                                                               'all_added': True,
                                                                               'current_filter_after_add': {'type': 'float',
                                                                                                            'bits': '40f8a89800000000'},
                                                                               'doing_after_add': True,
                                                                               'doing_exact_after_add': True},
                                                                  'callback': [],
                                                                  'current_filter_after': {'type': 'float',
                                                                                           'bits': '40f8a89800000000'},
                                                                  'doing_filter_after': True,
                                                                  'all_remove_result': False,
                                                                  'named_remove': {'value': True,
                                                                                   'warnings': [{'severity': 8192,
                                                                                                 'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030312e3520746f20696e74206c6f73657320707265636973696f6e'},
                                                                                                {'severity': 8192,
                                                                                                 'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030312e3520746f20696e74206c6f73657320707265636973696f6e'},
                                                                                                {'severity': 8192,
                                                                                                 'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030312e3520746f20696e74206c6f73657320707265636973696f6e'},
                                                                                                {'severity': 8192,
                                                                                                 'message_hex': '496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130313030312e3520746f20696e74206c6f73657320707265636973696f6e'}]},
                                                                  'current_filter_after_cleanup': {'type': 'float',
                                                                                                   'bits': '40f8a89800000000'},
                                                                  'doing_filter_after_cleanup': True}}}
EXPECTED_COUNTER_COW = json.loads(r'''
{
    "existing_counter": {
        "counter_isset": {
            "control": {
                "hook_bits": "40f8e70800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030302e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030302e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030302e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": "seed:second",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030302e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030302e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030302e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 1
                    },
                    "read_only_other_did_filter": 0,
                    "nested_apply_value": null,
                    "nested_did_filter": null
                },
                "after": {
                    "did_filter": 2,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030302e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030302e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 0
                }
            },
            "nested_write": {
                "hook_bits": "40f8e71800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030312e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": "seed:second",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030312e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 1
                    },
                    "read_only_other_did_filter": null,
                    "nested_apply_value": "child",
                    "nested_did_filter": 1
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030312e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 1
                }
            }
        },
        "increment": {
            "control": {
                "hook_bits": "40f8e72800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030322e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": "seed:second",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030322e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 2
                    },
                    "read_only_other_did_filter": 0,
                    "nested_apply_value": null,
                    "nested_did_filter": null
                },
                "after": {
                    "did_filter": 2,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030322e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 0
                }
            },
            "nested_write": {
                "hook_bits": "40f8e73800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030332e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030332e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030332e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": "seed:second",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030332e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030332e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030332e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 2
                    },
                    "read_only_other_did_filter": null,
                    "nested_apply_value": "child",
                    "nested_did_filter": 1
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030332e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030332e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 1
                }
            }
        },
        "later_hook_lookup": {
            "control": {
                "hook_bits": "40f8e74800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030342e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030342e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030342e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": "seed:second",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030342e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030342e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030342e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 3
                    },
                    "read_only_other_did_filter": 0,
                    "nested_apply_value": null,
                    "nested_did_filter": null
                },
                "after": {
                    "did_filter": 2,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030342e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030342e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 0
                }
            },
            "nested_write": {
                "hook_bits": "40f8e75800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030352e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030352e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030352e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": "seed:second",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030352e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030352e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030352e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 3
                    },
                    "read_only_other_did_filter": null,
                    "nested_apply_value": "child",
                    "nested_did_filter": 1
                },
                "after": {
                    "did_filter": 2,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030352e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030352e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 1
                }
            }
        }
    },
    "missing_counter": {
        "counter_isset": {
            "control": {
                "hook_bits": "40f8e76800000000",
                "setup": {
                    "value": null,
                    "warnings": []
                },
                "operation": {
                    "value": "seed:missing",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030362e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030362e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030362e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 1
                    },
                    "read_only_other_did_filter": 0,
                    "nested_apply_value": null,
                    "nested_did_filter": null
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030362e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030362e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 0
                }
            },
            "nested_write": {
                "hook_bits": "40f8e77800000000",
                "setup": {
                    "value": null,
                    "warnings": []
                },
                "operation": {
                    "value": "seed:missing",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030372e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030372e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030372e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 1
                    },
                    "read_only_other_did_filter": null,
                    "nested_apply_value": "child",
                    "nested_did_filter": 1
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030372e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030372e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 1
                }
            }
        },
        "counter_write": {
            "control": {
                "hook_bits": "40f8e78800000000",
                "setup": {
                    "value": null,
                    "warnings": []
                },
                "operation": {
                    "value": "seed:missing",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030382e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030382e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030382e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 2
                    },
                    "read_only_other_did_filter": 0,
                    "nested_apply_value": null,
                    "nested_did_filter": null
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030382e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030382e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 0
                }
            },
            "nested_write": {
                "hook_bits": "40f8e79800000000",
                "setup": {
                    "value": null,
                    "warnings": []
                },
                "operation": {
                    "value": "seed:missing",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030392e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030392e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030392e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 2
                    },
                    "read_only_other_did_filter": null,
                    "nested_apply_value": "child",
                    "nested_did_filter": 1
                },
                "after": {
                    "did_filter": 0,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323030392e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 1
                }
            }
        },
        "later_hook_lookup": {
            "control": {
                "hook_bits": "40f8e7a800000000",
                "setup": {
                    "value": null,
                    "warnings": []
                },
                "operation": {
                    "value": "seed:missing",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031302e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031302e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031302e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 3
                    },
                    "read_only_other_did_filter": 0,
                    "nested_apply_value": null,
                    "nested_did_filter": null
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031302e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031302e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 0
                }
            },
            "nested_write": {
                "hook_bits": "40f8e7b800000000",
                "setup": {
                    "value": null,
                    "warnings": []
                },
                "operation": {
                    "value": "seed:missing",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031312e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 3
                    },
                    "read_only_other_did_filter": null,
                    "nested_apply_value": "child",
                    "nested_did_filter": 1
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031312e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 1
                }
            }
        }
    },
    "did_filter": {
        "counter_isset": {
            "control": {
                "hook_bits": "40f8e7c800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031322e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031322e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 1
                    },
                    "read_only_other_did_filter": 0,
                    "nested_apply_value": null,
                    "nested_did_filter": null
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031322e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 0
                }
            },
            "nested_write": {
                "hook_bits": "40f8e7d800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031332e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031332e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031332e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": 0,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031332e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 1
                    },
                    "read_only_other_did_filter": null,
                    "nested_apply_value": "child",
                    "nested_did_filter": 1
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031332e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031332e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 1
                }
            }
        },
        "counter_read": {
            "control": {
                "hook_bits": "40f8e7e800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031342e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031342e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031342e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031342e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031342e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 2
                    },
                    "read_only_other_did_filter": 0,
                    "nested_apply_value": null,
                    "nested_did_filter": null
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031342e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031342e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 0
                }
            },
            "nested_write": {
                "hook_bits": "40f8e7f800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031352e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031352e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031352e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": null,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031352e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031352e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 2
                    },
                    "read_only_other_did_filter": null,
                    "nested_apply_value": "child",
                    "nested_did_filter": 1
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031352e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323031352e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 1
                }
            }
        }
    },
    "nested_read_owners": {
        "inner_increment": {
            "control": {
                "hook_bits": "40f8f39800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": null,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 2
                    },
                    "middle_did_filter": 1,
                    "float_apply_value": "seed:middle",
                    "middle_warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "middle_injected_at": {
                        "warning_index": 2
                    },
                    "nested_string_apply_value": null,
                    "inner_did_filter": null,
                    "inner_warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "inner_injected_at": {
                        "warning_index": 2
                    },
                    "read_only_other_did_filter": 0
                },
                "after": {
                    "did_filter": 2,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230312e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 0
                }
            },
            "nested_write": {
                "hook_bits": "40f8f3a800000000",
                "setup": {
                    "value": "seed:first",
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ]
                },
                "operation": {
                    "value": null,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "injected_at": {
                        "warning_index": 2
                    },
                    "middle_did_filter": 1,
                    "float_apply_value": "seed:middle",
                    "middle_warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "middle_injected_at": {
                        "warning_index": 2
                    },
                    "nested_string_apply_value": "inner",
                    "inner_did_filter": 1,
                    "inner_warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "inner_injected_at": {
                        "warning_index": 2
                    },
                    "read_only_other_did_filter": null
                },
                "after": {
                    "did_filter": 1,
                    "warnings": [
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        },
                        {
                            "severity": 8192,
                            "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f6174203130323230322e3520746f20696e74206c6f73657320707265636973696f6e"
                        }
                    ],
                    "other_did_filter": 1
                }
            }
        }
    }
}
''')

# Freeze the priority-bucket COW observations from the independently executed
# pinned WordPress program before recording acceptance evidence.
EXPECTED_PRIORITY_COW = json.loads(r'''
{
    "float_priority": {
        "control": {
            "warning_target": 0,
            "mutation": "control",
            "outer_add_result": true,
            "outer_throwable": null,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                },
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "injection": null,
            "before": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": false,
                "any": true
            },
            "after_first": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": 7,
                "any": true
            },
            "first_apply": {
                "value": "seed:seed:victim:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "anchor"
                ]
            },
            "after_second": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": 7,
                "any": true
            },
            "second_apply": {
                "value": "seed:seed:victim:outer:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "outer",
                    "anchor"
                ]
            }
        },
        "isset_add": {
            "warning_target": 1,
            "mutation": "add",
            "outer_add_result": true,
            "outer_throwable": null,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                },
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "injection": {
                "warning_index": 1,
                "add_result": true,
                "nested_priority_after_add": 7
            },
            "before": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": false,
                "any": true
            },
            "after_first": {
                "seed": 7,
                "victim": 7,
                "nested": 7,
                "outer": 7,
                "any": true
            },
            "first_apply": {
                "value": "seed:seed:victim:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "anchor"
                ]
            },
            "after_second": {
                "seed": 7,
                "victim": 7,
                "nested": 7,
                "outer": 7,
                "any": true
            },
            "second_apply": {
                "value": "seed:seed:victim:nested:outer:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "nested",
                    "outer",
                    "anchor"
                ]
            }
        },
        "isset_remove_bucket": {
            "warning_target": 1,
            "mutation": "remove_bucket",
            "outer_add_result": true,
            "outer_throwable": null,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                },
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "injection": {
                "warning_index": 1,
                "remove_seed_result": true,
                "remove_victim_result": true,
                "seed_priority_after_remove": false,
                "victim_priority_after_remove": false
            },
            "before": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": false,
                "any": true
            },
            "after_first": {
                "seed": false,
                "victim": false,
                "nested": false,
                "outer": 7,
                "any": true
            },
            "first_apply": {
                "value": "seed:seed:victim:outer",
                "trace": [
                    "seed",
                    "victim",
                    "outer"
                ]
            },
            "after_second": {
                "seed": false,
                "victim": false,
                "nested": false,
                "outer": 7,
                "any": true
            },
            "second_apply": {
                "value": "seed:anchor:outer",
                "trace": [
                    "anchor",
                    "outer"
                ]
            }
        },
        "write_add": {
            "warning_target": 2,
            "mutation": "add",
            "outer_add_result": true,
            "outer_throwable": null,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                },
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "injection": {
                "warning_index": 2,
                "add_result": true,
                "nested_priority_after_add": 7
            },
            "before": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": false,
                "any": true
            },
            "after_first": {
                "seed": 7,
                "victim": 7,
                "nested": 7,
                "outer": false,
                "any": true
            },
            "first_apply": {
                "value": "seed:seed:victim:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "anchor"
                ]
            },
            "after_second": {
                "seed": 7,
                "victim": 7,
                "nested": 7,
                "outer": false,
                "any": true
            },
            "second_apply": {
                "value": "seed:seed:victim:nested:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "nested",
                    "anchor"
                ]
            }
        },
        "write_remove_bucket": {
            "warning_target": 2,
            "mutation": "remove_bucket",
            "outer_add_result": true,
            "outer_throwable": null,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                },
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "injection": {
                "warning_index": 2,
                "remove_seed_result": true,
                "remove_victim_result": true,
                "seed_priority_after_remove": false,
                "victim_priority_after_remove": false
            },
            "before": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": false,
                "any": true
            },
            "after_first": {
                "seed": false,
                "victim": false,
                "nested": false,
                "outer": false,
                "any": true
            },
            "first_apply": {
                "value": "seed:seed:victim:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "anchor"
                ]
            },
            "after_second": {
                "seed": false,
                "victim": false,
                "nested": false,
                "outer": false,
                "any": true
            },
            "second_apply": {
                "value": "seed:anchor",
                "trace": [
                    "anchor"
                ]
            }
        }
    },
    "accepted_args_partial_bucket": {
        "control": {
            "mutation": "control",
            "outer_add_result": true,
            "outer_throwable": null,
            "warnings": [
                {
                    "severity": 2,
                    "message_hex": "4f626a656374206f6620636c61737320737464436c61737320636f756c64206e6f7420626520636f6e76657274656420746f20696e74"
                }
            ],
            "injection": null,
            "before": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": false,
                "any": true
            },
            "after_first": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": 7,
                "any": true
            },
            "first_apply": {
                "value": "seed:seed:victim:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "anchor"
                ]
            },
            "after_second": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": 7,
                "any": true
            },
            "second_apply": {
                "value": "seed:seed:victim:outer:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "outer",
                    "anchor"
                ]
            }
        },
        "add_remove_victim": {
            "mutation": "add_remove_victim",
            "outer_add_result": true,
            "outer_throwable": null,
            "warnings": [
                {
                    "severity": 2,
                    "message_hex": "4f626a656374206f6620636c61737320737464436c61737320636f756c64206e6f7420626520636f6e76657274656420746f20696e74"
                }
            ],
            "injection": {
                "warning_index": 1,
                "add_result": true,
                "nested_priority_after_add": 7,
                "remove_result": true,
                "victim_priority_after_remove": false
            },
            "before": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": false,
                "any": true
            },
            "after_first": {
                "seed": 7,
                "victim": false,
                "nested": 7,
                "outer": 7,
                "any": true
            },
            "first_apply": {
                "value": "seed:seed:victim:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "anchor"
                ]
            },
            "after_second": {
                "seed": 7,
                "victim": false,
                "nested": 7,
                "outer": 7,
                "any": true
            },
            "second_apply": {
                "value": "seed:seed:nested:outer:anchor",
                "trace": [
                    "seed",
                    "nested",
                    "outer",
                    "anchor"
                ]
            }
        }
    },
    "accepted_args_reentry": {
        "readonly": {
            "warnings": [
                {
                    "severity": 2,
                    "message_hex": "4f626a656374206f6620636c61737320737464436c61737320636f756c64206e6f7420626520636f6e76657274656420746f20696e74"
                }
            ],
            "handler": [],
            "outer_add_result": true,
            "outer_throwable": null,
            "before": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": false,
                "anchor": 10,
                "any": true,
                "weak_alive": {
                    "seed": true,
                    "victim": true,
                    "nested": false,
                    "outer": false,
                    "anchor": true
                }
            },
            "after_first": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": 7,
                "anchor": 10,
                "any": true,
                "weak_alive": {
                    "seed": true,
                    "victim": true,
                    "nested": false,
                    "outer": true,
                    "anchor": true
                }
            },
            "first_apply": {
                "value": "seed:seed:victim:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "anchor"
                ]
            },
            "events_after_first": [
                {
                    "kind": "invoke",
                    "label": "seed",
                    "phase": "first_apply"
                },
                {
                    "kind": "invoke",
                    "label": "victim",
                    "phase": "after_add"
                },
                {
                    "kind": "invoke",
                    "label": "anchor",
                    "phase": "after_add"
                }
            ],
            "destructors_after_first": [],
            "after_second": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": 7,
                "anchor": 10,
                "any": true,
                "weak_alive": {
                    "seed": true,
                    "victim": true,
                    "nested": false,
                    "outer": true,
                    "anchor": true
                }
            },
            "second_apply": {
                "value": "seed:seed:victim:outer:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "outer",
                    "anchor"
                ]
            },
            "events_after_second": [
                {
                    "kind": "invoke",
                    "label": "seed",
                    "phase": "second_apply"
                },
                {
                    "kind": "invoke",
                    "label": "victim",
                    "phase": "second_apply"
                },
                {
                    "kind": "invoke",
                    "label": "outer",
                    "phase": "second_apply"
                },
                {
                    "kind": "invoke",
                    "label": "anchor",
                    "phase": "second_apply"
                }
            ],
            "destructors_final": [
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "cleanup",
                    "warnings_seen": 1,
                    "trace_count": 4
                },
                {
                    "kind": "destruct",
                    "label": "victim",
                    "phase": "cleanup",
                    "warnings_seen": 1,
                    "trace_count": 4
                },
                {
                    "kind": "destruct",
                    "label": "outer",
                    "phase": "cleanup",
                    "warnings_seen": 1,
                    "trace_count": 4
                },
                {
                    "kind": "destruct",
                    "label": "anchor",
                    "phase": "cleanup",
                    "warnings_seen": 1,
                    "trace_count": 4
                }
            ]
        },
        "remove_bucket": {
            "warnings": [
                {
                    "severity": 2,
                    "message_hex": "4f626a656374206f6620636c61737320737464436c61737320636f756c64206e6f7420626520636f6e76657274656420746f20696e74"
                }
            ],
            "handler": {
                "before": {
                    "seed_priority": 7,
                    "victim_priority": 7
                },
                "remove_seed": true,
                "remove_victim": true,
                "after_remove": {
                    "seed_alive": true,
                    "victim_alive": true,
                    "seed_priority": false,
                    "victim_priority": false
                }
            },
            "outer_add_result": true,
            "outer_throwable": null,
            "before": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": false,
                "anchor": 10,
                "any": true,
                "weak_alive": {
                    "seed": true,
                    "victim": true,
                    "nested": false,
                    "outer": false,
                    "anchor": true
                }
            },
            "after_first": {
                "seed": false,
                "victim": false,
                "nested": false,
                "outer": 7,
                "anchor": 10,
                "any": true,
                "weak_alive": {
                    "seed": false,
                    "victim": false,
                    "nested": false,
                    "outer": true,
                    "anchor": true
                }
            },
            "first_apply": {
                "value": "seed:seed:victim:outer",
                "trace": [
                    "seed",
                    "victim",
                    "outer"
                ]
            },
            "events_after_first": [
                {
                    "kind": "invoke",
                    "label": "seed",
                    "phase": "first_apply"
                },
                {
                    "kind": "invoke",
                    "label": "victim",
                    "phase": "after_add"
                },
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                },
                {
                    "kind": "destruct",
                    "label": "victim",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                },
                {
                    "kind": "invoke",
                    "label": "outer",
                    "phase": "after_add"
                }
            ],
            "destructors_after_first": [
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                },
                {
                    "kind": "destruct",
                    "label": "victim",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                }
            ],
            "after_second": {
                "seed": false,
                "victim": false,
                "nested": false,
                "outer": 7,
                "anchor": 10,
                "any": true,
                "weak_alive": {
                    "seed": false,
                    "victim": false,
                    "nested": false,
                    "outer": true,
                    "anchor": true
                }
            },
            "second_apply": {
                "value": "seed:anchor:outer",
                "trace": [
                    "anchor",
                    "outer"
                ]
            },
            "events_after_second": [
                {
                    "kind": "invoke",
                    "label": "anchor",
                    "phase": "second_apply"
                },
                {
                    "kind": "invoke",
                    "label": "outer",
                    "phase": "second_apply"
                }
            ],
            "destructors_final": [
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                },
                {
                    "kind": "destruct",
                    "label": "victim",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                },
                {
                    "kind": "destruct",
                    "label": "outer",
                    "phase": "cleanup",
                    "warnings_seen": 1,
                    "trace_count": 2
                },
                {
                    "kind": "destruct",
                    "label": "anchor",
                    "phase": "cleanup",
                    "warnings_seen": 1,
                    "trace_count": 2
                }
            ]
        },
        "remove_recreate": {
            "warnings": [
                {
                    "severity": 2,
                    "message_hex": "4f626a656374206f6620636c61737320737464436c61737320636f756c64206e6f7420626520636f6e76657274656420746f20696e74"
                }
            ],
            "handler": {
                "before": {
                    "seed_priority": 7,
                    "victim_priority": 7
                },
                "remove_seed": true,
                "remove_victim": true,
                "after_remove": {
                    "seed_alive": true,
                    "victim_alive": true,
                    "seed_priority": false,
                    "victim_priority": false
                },
                "recreate_result": true,
                "after_recreate": {
                    "nested_alive": true,
                    "nested_priority": 7
                }
            },
            "outer_add_result": true,
            "outer_throwable": null,
            "before": {
                "seed": 7,
                "victim": 7,
                "nested": false,
                "outer": false,
                "anchor": 10,
                "any": true,
                "weak_alive": {
                    "seed": true,
                    "victim": true,
                    "nested": false,
                    "outer": false,
                    "anchor": true
                }
            },
            "after_first": {
                "seed": false,
                "victim": false,
                "nested": 7,
                "outer": 7,
                "anchor": 10,
                "any": true,
                "weak_alive": {
                    "seed": false,
                    "victim": false,
                    "nested": true,
                    "outer": true,
                    "anchor": true
                }
            },
            "first_apply": {
                "value": "seed:seed:victim:anchor",
                "trace": [
                    "seed",
                    "victim",
                    "anchor"
                ]
            },
            "events_after_first": [
                {
                    "kind": "invoke",
                    "label": "seed",
                    "phase": "first_apply"
                },
                {
                    "kind": "invoke",
                    "label": "victim",
                    "phase": "after_add"
                },
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                },
                {
                    "kind": "destruct",
                    "label": "victim",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                },
                {
                    "kind": "invoke",
                    "label": "anchor",
                    "phase": "after_add"
                }
            ],
            "destructors_after_first": [
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                },
                {
                    "kind": "destruct",
                    "label": "victim",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                }
            ],
            "after_second": {
                "seed": false,
                "victim": false,
                "nested": 7,
                "outer": 7,
                "anchor": 10,
                "any": true,
                "weak_alive": {
                    "seed": false,
                    "victim": false,
                    "nested": true,
                    "outer": true,
                    "anchor": true
                }
            },
            "second_apply": {
                "value": "seed:nested:outer:anchor",
                "trace": [
                    "nested",
                    "outer",
                    "anchor"
                ]
            },
            "events_after_second": [
                {
                    "kind": "invoke",
                    "label": "nested",
                    "phase": "second_apply"
                },
                {
                    "kind": "invoke",
                    "label": "outer",
                    "phase": "second_apply"
                },
                {
                    "kind": "invoke",
                    "label": "anchor",
                    "phase": "second_apply"
                }
            ],
            "destructors_final": [
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                },
                {
                    "kind": "destruct",
                    "label": "victim",
                    "phase": "after_add",
                    "warnings_seen": 1,
                    "trace_count": 2
                },
                {
                    "kind": "destruct",
                    "label": "nested",
                    "phase": "cleanup",
                    "warnings_seen": 1,
                    "trace_count": 3
                },
                {
                    "kind": "destruct",
                    "label": "outer",
                    "phase": "cleanup",
                    "warnings_seen": 1,
                    "trace_count": 3
                },
                {
                    "kind": "destruct",
                    "label": "anchor",
                    "phase": "cleanup",
                    "warnings_seen": 1,
                    "trace_count": 3
                }
            ]
        }
    },
    "float_accepted_args_lifetime": {
        "control": {
            "warning_target": 0,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                },
                {
                    "severity": 2,
                    "message_hex": "4f626a656374206f6620636c61737320737464436c61737320636f756c64206e6f7420626520636f6e76657274656420746f20696e74"
                },
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "handler": null,
            "outer_add_result": true,
            "outer_throwable": null,
            "after_outer_add": {
                "seed_alive": true,
                "seed_priority": 7,
                "outer_alive": true,
                "outer_priority": 7,
                "anchor_alive": true,
                "anchor_priority": 10,
                "any": true
            },
            "events": [
                {
                    "kind": "warning",
                    "index": 1
                },
                {
                    "kind": "warning",
                    "index": 2
                },
                {
                    "kind": "warning",
                    "index": 3
                },
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "cleanup",
                    "warnings_seen": 3,
                    "trace_count": 0
                },
                {
                    "kind": "destruct",
                    "label": "outer",
                    "phase": "cleanup",
                    "warnings_seen": 3,
                    "trace_count": 0
                }
            ],
            "destructors": [
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "cleanup",
                    "warnings_seen": 3,
                    "trace_count": 0
                },
                {
                    "kind": "destruct",
                    "label": "outer",
                    "phase": "cleanup",
                    "warnings_seen": 3,
                    "trace_count": 0
                }
            ]
        },
        "remove_at_isset": {
            "warning_target": 1,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                },
                {
                    "severity": 2,
                    "message_hex": "4f626a656374206f6620636c61737320737464436c61737320636f756c64206e6f7420626520636f6e76657274656420746f20696e74"
                },
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "handler": {
                "entry_seed_alive": true,
                "entry_seed_priority": 7,
                "entry_outer_priority": false,
                "remove_seed": true,
                "after_remove_seed_alive": true,
                "after_remove_seed_priority": false
            },
            "outer_add_result": true,
            "outer_throwable": null,
            "after_outer_add": {
                "seed_alive": false,
                "seed_priority": false,
                "outer_alive": true,
                "outer_priority": 7,
                "anchor_alive": true,
                "anchor_priority": 10,
                "any": true
            },
            "events": [
                {
                    "kind": "warning",
                    "index": 1
                },
                {
                    "kind": "handler_enter",
                    "seed_alive": true,
                    "outer_priority": false
                },
                {
                    "kind": "handler_exit",
                    "seed_alive": true,
                    "outer_priority": false
                },
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "handler_after_remove",
                    "warnings_seen": 1,
                    "trace_count": 0
                },
                {
                    "kind": "warning",
                    "index": 2
                },
                {
                    "kind": "warning",
                    "index": 3
                },
                {
                    "kind": "destruct",
                    "label": "outer",
                    "phase": "cleanup",
                    "warnings_seen": 3,
                    "trace_count": 0
                }
            ],
            "destructors": [
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "handler_after_remove",
                    "warnings_seen": 1,
                    "trace_count": 0
                },
                {
                    "kind": "destruct",
                    "label": "outer",
                    "phase": "cleanup",
                    "warnings_seen": 3,
                    "trace_count": 0
                }
            ]
        },
        "remove_at_cast": {
            "warning_target": 2,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                },
                {
                    "severity": 2,
                    "message_hex": "4f626a656374206f6620636c61737320737464436c61737320636f756c64206e6f7420626520636f6e76657274656420746f20696e74"
                },
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "handler": {
                "entry_seed_alive": true,
                "entry_seed_priority": 7,
                "entry_outer_priority": false,
                "remove_seed": true,
                "after_remove_seed_alive": false,
                "after_remove_seed_priority": false
            },
            "outer_add_result": true,
            "outer_throwable": null,
            "after_outer_add": {
                "seed_alive": false,
                "seed_priority": false,
                "outer_alive": true,
                "outer_priority": 7,
                "anchor_alive": true,
                "anchor_priority": 10,
                "any": true
            },
            "events": [
                {
                    "kind": "warning",
                    "index": 1
                },
                {
                    "kind": "warning",
                    "index": 2
                },
                {
                    "kind": "handler_enter",
                    "seed_alive": true,
                    "outer_priority": false
                },
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "handler_enter",
                    "warnings_seen": 2,
                    "trace_count": 0
                },
                {
                    "kind": "handler_exit",
                    "seed_alive": false,
                    "outer_priority": false
                },
                {
                    "kind": "warning",
                    "index": 3
                },
                {
                    "kind": "destruct",
                    "label": "outer",
                    "phase": "cleanup",
                    "warnings_seen": 3,
                    "trace_count": 0
                }
            ],
            "destructors": [
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "handler_enter",
                    "warnings_seen": 2,
                    "trace_count": 0
                },
                {
                    "kind": "destruct",
                    "label": "outer",
                    "phase": "cleanup",
                    "warnings_seen": 3,
                    "trace_count": 0
                }
            ]
        },
        "remove_at_write": {
            "warning_target": 3,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                },
                {
                    "severity": 2,
                    "message_hex": "4f626a656374206f6620636c61737320737464436c61737320636f756c64206e6f7420626520636f6e76657274656420746f20696e74"
                },
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "handler": {
                "entry_seed_alive": true,
                "entry_seed_priority": 7,
                "entry_outer_priority": false,
                "remove_seed": true,
                "after_remove_seed_alive": true,
                "after_remove_seed_priority": false
            },
            "outer_add_result": true,
            "outer_throwable": null,
            "after_outer_add": {
                "seed_alive": false,
                "seed_priority": false,
                "outer_alive": true,
                "outer_priority": false,
                "anchor_alive": true,
                "anchor_priority": 10,
                "any": true
            },
            "events": [
                {
                    "kind": "warning",
                    "index": 1
                },
                {
                    "kind": "warning",
                    "index": 2
                },
                {
                    "kind": "warning",
                    "index": 3
                },
                {
                    "kind": "handler_enter",
                    "seed_alive": true,
                    "outer_priority": false
                },
                {
                    "kind": "handler_exit",
                    "seed_alive": true,
                    "outer_priority": false
                },
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "handler_after_remove",
                    "warnings_seen": 3,
                    "trace_count": 0
                },
                {
                    "kind": "destruct",
                    "label": "outer",
                    "phase": "cleanup",
                    "warnings_seen": 3,
                    "trace_count": 0
                }
            ],
            "destructors": [
                {
                    "kind": "destruct",
                    "label": "seed",
                    "phase": "handler_after_remove",
                    "warnings_seen": 3,
                    "trace_count": 0
                },
                {
                    "kind": "destruct",
                    "label": "outer",
                    "phase": "cleanup",
                    "warnings_seen": 3,
                    "trace_count": 0
                }
            ]
        }
    },
    "throwable_cleanup": {
        "isset_control": {
            "warning_target": 1,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "handler": {
                "warning_index": 1,
                "seed_alive_at_entry": true,
                "seed_priority_at_entry": 7,
                "outer_priority_at_entry": false,
                "remove_seed": true,
                "seed_alive_after_alias_clear": true,
                "seed_priority_after_alias_clear": false,
                "outer_priority_before_throw_a": false
            },
            "outer_add_result": null,
            "caught": {
                "class_hex": "52756e74696d65457863657074696f6e",
                "message_hex": "412066726f6d207761726e696e672068616e646c6572",
                "same_as_a": true,
                "same_as_b": false,
                "previous_chain": [
                    {
                        "class_hex": "52756e74696d65457863657074696f6e",
                        "message_hex": "412066726f6d207761726e696e672068616e646c6572",
                        "same_as_a": true,
                        "same_as_b": false
                    }
                ]
            },
            "after_outer_add": {
                "seed_alive": false,
                "seed_priority": false,
                "outer_alive": true,
                "outer_priority": false,
                "anchor_alive": true,
                "anchor_priority": 10,
                "any": true
            },
            "events": [
                {
                    "kind": "warning",
                    "index": 1
                },
                {
                    "kind": "handler_enter",
                    "index": 1
                },
                {
                    "kind": "handler_before_throw_a",
                    "seed_alive": true
                },
                {
                    "kind": "handler_throw_a"
                },
                {
                    "kind": "seed_destructor_enter",
                    "phase": "handler_before_throw_a"
                },
                {
                    "kind": "seed_destructor_exit",
                    "phase": "handler_before_throw_a"
                },
                {
                    "kind": "outer_caught",
                    "class_hex": "52756e74696d65457863657074696f6e"
                }
            ]
        },
        "isset_throwing_destructor": {
            "warning_target": 1,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "handler": {
                "warning_index": 1,
                "seed_alive_at_entry": true,
                "seed_priority_at_entry": 7,
                "outer_priority_at_entry": false,
                "remove_seed": true,
                "seed_alive_after_alias_clear": true,
                "seed_priority_after_alias_clear": false,
                "outer_priority_before_throw_a": false
            },
            "outer_add_result": null,
            "caught": {
                "class_hex": "52756e74696d65457863657074696f6e",
                "message_hex": "422066726f6d20736565642064657374727563746f72",
                "same_as_a": false,
                "same_as_b": true,
                "previous_chain": [
                    {
                        "class_hex": "52756e74696d65457863657074696f6e",
                        "message_hex": "422066726f6d20736565642064657374727563746f72",
                        "same_as_a": false,
                        "same_as_b": true
                    },
                    {
                        "class_hex": "52756e74696d65457863657074696f6e",
                        "message_hex": "412066726f6d207761726e696e672068616e646c6572",
                        "same_as_a": true,
                        "same_as_b": false
                    }
                ]
            },
            "after_outer_add": {
                "seed_alive": false,
                "seed_priority": false,
                "outer_alive": true,
                "outer_priority": false,
                "anchor_alive": true,
                "anchor_priority": 10,
                "any": true
            },
            "events": [
                {
                    "kind": "warning",
                    "index": 1
                },
                {
                    "kind": "handler_enter",
                    "index": 1
                },
                {
                    "kind": "handler_before_throw_a",
                    "seed_alive": true
                },
                {
                    "kind": "handler_throw_a"
                },
                {
                    "kind": "seed_destructor_enter",
                    "phase": "handler_before_throw_a"
                },
                {
                    "kind": "seed_destructor_throw_b",
                    "phase": "handler_before_throw_a"
                },
                {
                    "kind": "outer_caught",
                    "class_hex": "52756e74696d65457863657074696f6e"
                }
            ]
        },
        "write_control": {
            "warning_target": 2,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                },
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "handler": {
                "warning_index": 2,
                "seed_alive_at_entry": true,
                "seed_priority_at_entry": 7,
                "outer_priority_at_entry": false,
                "remove_seed": true,
                "seed_alive_after_alias_clear": true,
                "seed_priority_after_alias_clear": false,
                "outer_priority_before_throw_a": false
            },
            "outer_add_result": null,
            "caught": {
                "class_hex": "52756e74696d65457863657074696f6e",
                "message_hex": "412066726f6d207761726e696e672068616e646c6572",
                "same_as_a": true,
                "same_as_b": false,
                "previous_chain": [
                    {
                        "class_hex": "52756e74696d65457863657074696f6e",
                        "message_hex": "412066726f6d207761726e696e672068616e646c6572",
                        "same_as_a": true,
                        "same_as_b": false
                    }
                ]
            },
            "after_outer_add": {
                "seed_alive": false,
                "seed_priority": false,
                "outer_alive": true,
                "outer_priority": false,
                "anchor_alive": true,
                "anchor_priority": 10,
                "any": true
            },
            "events": [
                {
                    "kind": "warning",
                    "index": 1
                },
                {
                    "kind": "warning",
                    "index": 2
                },
                {
                    "kind": "handler_enter",
                    "index": 2
                },
                {
                    "kind": "handler_before_throw_a",
                    "seed_alive": true
                },
                {
                    "kind": "handler_throw_a"
                },
                {
                    "kind": "seed_destructor_enter",
                    "phase": "handler_before_throw_a"
                },
                {
                    "kind": "seed_destructor_exit",
                    "phase": "handler_before_throw_a"
                },
                {
                    "kind": "outer_caught",
                    "class_hex": "52756e74696d65457863657074696f6e"
                }
            ]
        },
        "write_throwing_destructor": {
            "warning_target": 2,
            "warnings": [
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                },
                {
                    "severity": 8192,
                    "message_hex": "496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"
                }
            ],
            "handler": {
                "warning_index": 2,
                "seed_alive_at_entry": true,
                "seed_priority_at_entry": 7,
                "outer_priority_at_entry": false,
                "remove_seed": true,
                "seed_alive_after_alias_clear": true,
                "seed_priority_after_alias_clear": false,
                "outer_priority_before_throw_a": false
            },
            "outer_add_result": null,
            "caught": {
                "class_hex": "52756e74696d65457863657074696f6e",
                "message_hex": "422066726f6d20736565642064657374727563746f72",
                "same_as_a": false,
                "same_as_b": true,
                "previous_chain": [
                    {
                        "class_hex": "52756e74696d65457863657074696f6e",
                        "message_hex": "422066726f6d20736565642064657374727563746f72",
                        "same_as_a": false,
                        "same_as_b": true
                    },
                    {
                        "class_hex": "52756e74696d65457863657074696f6e",
                        "message_hex": "412066726f6d207761726e696e672068616e646c6572",
                        "same_as_a": true,
                        "same_as_b": false
                    }
                ]
            },
            "after_outer_add": {
                "seed_alive": false,
                "seed_priority": false,
                "outer_alive": true,
                "outer_priority": false,
                "anchor_alive": true,
                "anchor_priority": 10,
                "any": true
            },
            "events": [
                {
                    "kind": "warning",
                    "index": 1
                },
                {
                    "kind": "warning",
                    "index": 2
                },
                {
                    "kind": "handler_enter",
                    "index": 2
                },
                {
                    "kind": "handler_before_throw_a",
                    "seed_alive": true
                },
                {
                    "kind": "handler_throw_a"
                },
                {
                    "kind": "seed_destructor_enter",
                    "phase": "handler_before_throw_a"
                },
                {
                    "kind": "seed_destructor_throw_b",
                    "phase": "handler_before_throw_a"
                },
                {
                    "kind": "outer_caught",
                    "class_hex": "52756e74696d65457863657074696f6e"
                }
            ]
        }
    }
}
''')


ARGUMENTS = {
    'add_filter': ('hook', 'callback', 'priority', 'accepted_args'),
    'remove_filter': ('hook', 'callback', 'priority'),
    'has_filter': ('hook', 'callback'), 'apply_filters': ('hook',),
    'current_filter': (), 'doing_filter': ('hook',), 'did_filter': ('hook',),
    'add_action': ('hook', 'callback', 'priority', 'accepted_args'),
    'remove_action': ('hook', 'callback', 'priority'),
    'has_action': ('hook', 'callback'), 'do_action': ('hook',),
    'current_action': (), 'doing_action': ('hook',), 'did_action': ('hook',),
}
RESULT_FIELDS = {'scenario', 'continuation_peer', 'frames_from_php', 'frames_from_rust',
    'api_counts', 'callback_invocations', 'release_effects', 'boundary_effects',
    'argument_release_effects', 'late_resume_rejected', 'late_call_rejected',
    'continuation_depth_after', 'state_after', 'php_observation', 'registry_observation',
    'nested_caught_throw'}

EXPECTED_NESTED_CAUGHT_THROW_RESULT = {
    'trace': ['parent-enter', 'child-enter', 'child-throw', 'parent-catch', 'parent-resume'],
    'same_object': True,
    'parent_do_action_return': None,
    'parent_did_action': 1,
    'child_did_action': 1,
    'current_action': 'callable-action-nested-caught-parent',
    'doing_parent': True,
    'doing_child': False,
    'stack_depth': 1,
}


def exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(label + ' fields differ')


def scalar(value):
    validate_pod(value)
    if value['type'] == 'array':
        raise ValueError('hook scalar cannot be array')


def normalized_scalar_key(value):
    """Independent 64-bit PHP scalar array-key projection, not priority ordering."""
    import math
    import struct
    scalar(value)
    kind = value['type']
    if kind == 'null':
        return {'type': 'string', 'hex': ''}
    if kind == 'string':
        raw = canonical_hex(value['hex'], 'original string')
        try:
            text = raw.decode('ascii')
            number = canonical_integer(text, 'candidate numeric key')
        except (ValueError, UnicodeError):
            return {'type': 'string', 'hex': value['hex']}
        return {'type': 'int', 'hex': str(number).encode().hex()}
    if kind == 'int':
        number = canonical_integer(value['value'], 'original int')
    elif kind == 'bool':
        number = int(value['value'])
    else:
        number_float = struct.unpack('>d', bytes.fromhex(value['bits']))[0]
        number = math.trunc(number_float) % (2**64) if math.isfinite(number_float) else 0
        if number >= 2**63:
            number -= 2**64
    return {'type': 'int', 'hex': str(number).encode().hex()}


def handle(value, kind):
    exact(value, ('request', 'kind', 'slot', 'generation', 'token'), 'handle')
    if value['request'] != REQUEST or value['kind'] != kind:
        raise ValueError('handle request/kind differs')
    if type(value['slot']) is not int or not 0 <= value['slot'] < 2**32:
        raise ValueError('handle slot differs')
    if type(value['generation']) is not int or not 1 <= value['generation'] < 2**32:
        raise ValueError('handle generation differs')
    if len(canonical_hex(value['token'], 'token')) != 8:
        raise ValueError('handle token width differs')
    return tuple(value[key] for key in ('request', 'kind', 'slot', 'generation', 'token'))


def validate_did_filter_result(node):
    validate_pod(node)
    if node['type'] == 'null':
        return
    if node['type'] != 'int' or int(node['value']) < 0:
        raise ValueError('did_filter result differs')


EXPECTED_REMOVE_COW = json.loads(r'''{"priority_first":{"control":{"case":"control","trigger_warning":0,"warnings":[{"index":1,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":2,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":3,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":4,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"}],"events":[{"kind":"warning","index":1},{"kind":"warning","index":2},{"kind":"warning","index":3},{"kind":"warning","index":4},{"kind":"after_remove","state":{"target":false,"keeper":false,"replacement":false,"anchor":10,"any":true}}],"remove_result":true,"caught":null,"after_remove":{"target":false,"keeper":false,"replacement":false,"anchor":10,"any":true},"apply_value":"seed:anchor","apply_trace":["anchor"],"callback_calls":{"target":0,"keeper":0,"replacement":0}},"warning1_remove_target":{"case":"warning1_remove_target","trigger_warning":1,"warnings":[{"index":1,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":2,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":3,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":4,"severity":2,"message_hex":"556e646566696e6564206172726179206b65792037"},{"index":5,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"}],"events":[{"kind":"warning","index":1},{"kind":"handler_enter","index":1},{"kind":"handler_remove_target","result":true},{"kind":"handler_exit","index":1},{"kind":"warning","index":2},{"kind":"warning","index":3},{"kind":"warning","index":4},{"kind":"warning","index":5},{"kind":"after_remove","state":{"target":false,"keeper":false,"replacement":false,"anchor":10,"any":true}}],"remove_result":true,"caught":null,"after_remove":{"target":false,"keeper":false,"replacement":false,"anchor":10,"any":true},"apply_value":"seed:anchor","apply_trace":["anchor"],"callback_calls":{"target":0,"keeper":0,"replacement":0}},"warning1_add_missing_target":{"case":"warning1_add_missing_target","trigger_warning":1,"warnings":[{"index":1,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"}],"events":[{"kind":"warning","index":1},{"kind":"handler_enter","index":1},{"kind":"handler_add_target","result":true},{"kind":"handler_exit","index":1},{"kind":"after_remove","state":{"target":7,"keeper":7,"replacement":false,"anchor":10,"any":true}}],"remove_result":false,"caught":null,"after_remove":{"target":7,"keeper":7,"replacement":false,"anchor":10,"any":true},"apply_value":"seed:keeper:target:anchor","apply_trace":["keeper","target","anchor"],"callback_calls":{"target":1,"keeper":1,"replacement":0}},"warning2_empty_priority7":{"case":"warning2_empty_priority7","trigger_warning":2,"warnings":[{"index":1,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":2,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":3,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":4,"severity":2,"message_hex":"556e646566696e6564206172726179206b65792037"},{"index":5,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"}],"events":[{"kind":"warning","index":1},{"kind":"warning","index":2},{"kind":"handler_enter","index":2},{"kind":"handler_remove_target","result":true},{"kind":"handler_exit","index":2},{"kind":"warning","index":3},{"kind":"warning","index":4},{"kind":"warning","index":5},{"kind":"after_remove","state":{"target":false,"keeper":false,"replacement":false,"anchor":10,"any":true}}],"remove_result":true,"caught":null,"after_remove":{"target":false,"keeper":false,"replacement":false,"anchor":10,"any":true},"apply_value":"seed:anchor","apply_trace":["anchor"],"callback_calls":{"target":0,"keeper":0,"replacement":0}},"warning2_recreate_priority7":{"case":"warning2_recreate_priority7","trigger_warning":2,"warnings":[{"index":1,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":2,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":3,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"}],"events":[{"kind":"warning","index":1},{"kind":"warning","index":2},{"kind":"handler_enter","index":2},{"kind":"handler_remove_target","result":true},{"kind":"handler_add_replacement","result":true},{"kind":"handler_exit","index":2},{"kind":"warning","index":3},{"kind":"after_remove","state":{"target":false,"keeper":false,"replacement":7,"anchor":10,"any":true}}],"remove_result":true,"caught":null,"after_remove":{"target":false,"keeper":false,"replacement":7,"anchor":10,"any":true},"apply_value":"seed:replacement:anchor","apply_trace":["replacement","anchor"],"callback_calls":{"target":0,"keeper":0,"replacement":1}}},"priority_read_unset":{"control":{"case":"control","trigger_warning":0,"warnings":[{"index":1,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":2,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":3,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":4,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"}],"events":[{"kind":"warning","index":1},{"kind":"warning","index":2},{"kind":"warning","index":3},{"kind":"warning","index":4},{"kind":"after_remove","state":{"target":false,"keeper":false,"replacement":false,"anchor":10,"any":true}}],"remove_result":true,"caught":null,"after_remove":{"target":false,"keeper":false,"replacement":false,"anchor":10,"any":true},"apply_value":"seed:anchor","apply_trace":["anchor"],"callback_calls":{"target":0,"keeper":0,"replacement":0}},"read_warning_adds_replacement":{"case":"read_warning_adds_replacement","trigger_warning":3,"warnings":[{"index":1,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":2,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":3,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":4,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"}],"events":[{"kind":"warning","index":1},{"kind":"warning","index":2},{"kind":"warning","index":3},{"kind":"handler_enter","index":3},{"kind":"handler_add_replacement","result":true},{"kind":"handler_exit","index":3},{"kind":"warning","index":4},{"kind":"after_remove","state":{"target":false,"keeper":false,"replacement":false,"anchor":10,"any":true}}],"remove_result":true,"caught":null,"after_remove":{"target":false,"keeper":false,"replacement":false,"anchor":10,"any":true},"apply_value":"seed:anchor","apply_trace":["anchor"],"callback_calls":{"target":0,"keeper":0,"replacement":0}},"unset_warning_adds_replacement":{"case":"unset_warning_adds_replacement","trigger_warning":4,"warnings":[{"index":1,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":2,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":3,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":4,"severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"}],"events":[{"kind":"warning","index":1},{"kind":"warning","index":2},{"kind":"warning","index":3},{"kind":"warning","index":4},{"kind":"handler_enter","index":4},{"kind":"handler_add_replacement","result":true},{"kind":"handler_exit","index":4},{"kind":"after_remove","state":{"target":false,"keeper":false,"replacement":7,"anchor":10,"any":true}}],"remove_result":true,"caught":null,"after_remove":{"target":false,"keeper":false,"replacement":7,"anchor":10,"any":true},"apply_value":"seed:replacement:anchor","apply_trace":["replacement","anchor"],"callback_calls":{"target":0,"keeper":0,"replacement":1}}}}''')
EXPECTED_HOOK_UNSET_REENTRY = json.loads(r'''{"control":{"case":"control","hook_value":7.5,"setup_result":true,"remove_result":true,"injection_result":null,"caught":null,"warnings":[{"index":1,"phase":"setup","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":2,"phase":"setup","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":3,"phase":"setup","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":4,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":5,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":6,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":7,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":8,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":9,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":10,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":11,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":12,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":13,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"}],"events":[{"kind":"warning","index":1,"phase":"setup"},{"kind":"warning","index":2,"phase":"setup"},{"kind":"warning","index":3,"phase":"setup"},{"kind":"warning","index":4,"phase":"remove"},{"kind":"warning","index":5,"phase":"remove"},{"kind":"warning","index":6,"phase":"remove"},{"kind":"warning","index":7,"phase":"remove"},{"kind":"warning","index":8,"phase":"observe"},{"kind":"warning","index":9,"phase":"observe"},{"kind":"warning","index":10,"phase":"observe"},{"kind":"warning","index":11,"phase":"observe"},{"kind":"warning","index":12,"phase":"observe"},{"kind":"warning","index":13,"phase":"observe"}],"after_remove":{"target":false,"replacement":false,"any":false},"apply_value":"seed","apply_trace":[]},"inject_on_hook_unset":{"case":"inject_on_hook_unset","hook_value":7.5,"setup_result":true,"remove_result":true,"injection_result":true,"caught":null,"warnings":[{"index":1,"phase":"setup","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":2,"phase":"setup","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":3,"phase":"setup","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":4,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":5,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":6,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":7,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":8,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":9,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":10,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":11,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":12,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":13,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"}],"events":[{"kind":"warning","index":1,"phase":"setup"},{"kind":"warning","index":2,"phase":"setup"},{"kind":"warning","index":3,"phase":"setup"},{"kind":"warning","index":4,"phase":"remove"},{"kind":"warning","index":5,"phase":"remove"},{"kind":"warning","index":6,"phase":"remove"},{"kind":"warning","index":7,"phase":"remove"},{"kind":"injection_enter","warning_index":7},{"kind":"injection_return","warning_index":7,"result":true},{"kind":"warning","index":8,"phase":"observe"},{"kind":"warning","index":9,"phase":"observe"},{"kind":"warning","index":10,"phase":"observe"},{"kind":"warning","index":11,"phase":"observe"},{"kind":"warning","index":12,"phase":"observe"},{"kind":"warning","index":13,"phase":"observe"}],"after_remove":{"target":false,"replacement":false,"any":false},"apply_value":"seed","apply_trace":[]},"throw_during_finally":{"case":"throw_during_finally","hook_value":7.5,"setup_result":true,"remove_result":null,"injection_result":true,"caught":{"class_hex":"52756e74696d65457863657074696f6e","message_hex":"64657374727563746f722042","is_primary_A":false,"is_destructor_B":true,"previous_is_primary_A":true},"warnings":[{"index":1,"phase":"setup","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":2,"phase":"setup","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":3,"phase":"setup","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":4,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":5,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":6,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":7,"phase":"remove","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":8,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":9,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":10,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":11,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":12,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"},{"index":13,"phase":"observe","severity":8192,"message_hex":"496d706c6963697420636f6e76657273696f6e2066726f6d20666c6f617420372e3520746f20696e74206c6f73657320707265636973696f6e"}],"events":[{"kind":"warning","index":1,"phase":"setup"},{"kind":"warning","index":2,"phase":"setup"},{"kind":"warning","index":3,"phase":"setup"},{"kind":"throw_primary_A"},{"kind":"warning","index":4,"phase":"remove"},{"kind":"warning","index":5,"phase":"remove"},{"kind":"warning","index":6,"phase":"remove"},{"kind":"warning","index":7,"phase":"remove"},{"kind":"injection_enter","warning_index":7},{"kind":"injection_return","warning_index":7,"result":true},"replacement_destructor",{"kind":"caught","is_primary_A":false,"is_destructor_B":true},{"kind":"warning","index":8,"phase":"observe"},{"kind":"warning","index":9,"phase":"observe"},{"kind":"warning","index":10,"phase":"observe"},{"kind":"warning","index":11,"phase":"observe"},{"kind":"warning","index":12,"phase":"observe"},{"kind":"warning","index":13,"phase":"observe"}],"after_remove":{"target":false,"replacement":false,"any":false},"apply_value":"seed","apply_trace":[]}}''')

EXPECTED_ACTION_API = json.loads(r'''{"aliases":{"add_action":true,"add_action_second":true,"has_action_any":true,"has_action_zero_priority":0,"has_action_second_priority":10,"has_action_missing":false,"has_action_wrong_priority":false,"remove_action_wrong_priority":false,"remove_action_first":true,"has_action_after_remove":true},"do_action_returns":{"populated":null,"empty_args":null,"all_args":null,"all_without_named":null,"nested":null,"no_listener":null},"callbacks":[{"event":"first","args":["outer","tail"],"current_action":"callable-action-api","doing_action":true,"doing_named":true,"did_action":1,"did_filter":0,"wp_actions":1},{"event":"second","args":["outer","tail"],"current_action":"callable-action-api","doing_action":true,"doing_named":true,"did_action":1,"did_filter":0,"wp_actions":1},{"event":"first","args":["filter-seed"],"current_action":"callable-action-api","doing_action":true,"doing_named":true,"did_action":1,"did_filter":1,"wp_actions":1},{"event":"second","args":["ignored-first-return"],"current_action":"callable-action-api","doing_action":true,"doing_named":true,"did_action":1,"did_filter":1,"wp_actions":1}],"after_outer":{"did_action":1,"did_filter":0,"current_action":false,"doing_action":false},"empty_args":[{"accepted_args":1,"args":[""]},{"accepted_args":0,"args":[]}],"all_hook":{"events":[{"accepted_args":0,"args":["callable-action-all-args","alpha","beta","gamma"],"current_action":"callable-action-all-args","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":1,"args":["callable-action-all-args","alpha","beta","gamma"],"current_action":"callable-action-all-args","doing_action":true,"did_action":1,"wp_actions":1},{"named":true,"args":["alpha"]},{"accepted_args":0,"args":["callable-action-all-without-named-listener","solo","extra"],"current_action":"callable-action-all-without-named-listener","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":1,"args":["callable-action-all-without-named-listener","solo","extra"],"current_action":"callable-action-all-without-named-listener","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":0,"args":["callable-action-api","filter-seed"],"current_action":"callable-action-api","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":1,"args":["callable-action-api","filter-seed"],"current_action":"callable-action-api","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":0,"args":["callable-action-nested","outer"],"current_action":"callable-action-nested","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":1,"args":["callable-action-nested","outer"],"current_action":"callable-action-nested","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":0,"args":["callable-action-nested","inner"],"current_action":"callable-action-nested","doing_action":true,"did_action":2,"wp_actions":2},{"accepted_args":1,"args":["callable-action-nested","inner"],"current_action":"callable-action-nested","doing_action":true,"did_action":2,"wp_actions":2},{"accepted_args":0,"args":["callable-action-nested-child","child"],"current_action":"callable-action-nested-child","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":1,"args":["callable-action-nested-child","child"],"current_action":"callable-action-nested-child","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":0,"args":["callable-action-no-listener"],"current_action":"callable-action-no-listener","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":1,"args":["callable-action-no-listener"],"current_action":"callable-action-no-listener","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":0,"args":["callable-action-throw-all","first"],"current_action":"callable-action-throw-all","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":1,"args":["callable-action-throw-all","first"],"current_action":"callable-action-throw-all","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":0,"args":["callable-action-throw-all","follow-up"],"current_action":"callable-action-throw-all","doing_action":true,"did_action":2,"wp_actions":2},{"accepted_args":1,"args":["callable-action-throw-all","follow-up"],"current_action":"callable-action-throw-all","doing_action":true,"did_action":2,"wp_actions":2},{"accepted_args":0,"args":["callable-action-throw-named","first"],"current_action":"callable-action-throw-named","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":1,"args":["callable-action-throw-named","first"],"current_action":"callable-action-throw-named","doing_action":true,"did_action":1,"wp_actions":1},{"accepted_args":0,"args":["callable-action-throw-named","follow-up"],"current_action":"callable-action-throw-named","doing_action":true,"did_action":2,"wp_actions":2},{"accepted_args":1,"args":["callable-action-throw-named","follow-up"],"current_action":"callable-action-throw-named","doing_action":true,"did_action":2,"wp_actions":2}],"did_action_without_named":1},"counter_independence":{"before_filter":{"did_action":1,"did_filter":0},"after_filter":{"did_action":1,"did_filter":1}},"nested":{"events":[{"event":"outer-enter","args":["outer"],"count":1,"current_action":"callable-action-nested"},{"event":"outer-enter","args":["inner"],"count":2,"current_action":"callable-action-nested"},{"event":"outer-exit","count":2,"current_action":"callable-action-nested"},{"event":"child","args":["child"],"current_action":"callable-action-nested-child","doing_parent":true,"doing_child":true},{"event":"outer-exit","count":2,"current_action":"callable-action-nested"}],"did_action":2,"current_action":"callable-action-throw-named","doing_action":true},"no_listener_count":{"before":0,"after":1},"throw_all":{"caught":{"class":"RuntimeException","message":"action all boom"},"events":[{"event":"all-enter","args":["callable-action-throw-all","first"],"count":1,"current_action":"callable-action-throw-all","doing":true},{"event":"all-enter","args":["callable-action-throw-all","follow-up"],"count":2,"current_action":"callable-action-throw-all","doing":true},{"event":"all-exit"},{"event":"named-after-all","args":["follow-up"]}],"after_catch":{"did_action":1,"current_action":"callable-action-throw-all","doing_action":true,"doing_named":true},"followup_return":null,"after_followup":{"did_action":2,"current_action":"callable-action-throw-all","doing_action":true,"doing_named":true}},"throw_named":{"caught":{"class":"RuntimeException","message":"action named boom"},"events":[{"event":"named-thrower","args":["first"],"count":1,"current_action":"callable-action-throw-named","doing":true},{"event":"named-thrower","args":["follow-up"],"count":2,"current_action":"callable-action-throw-named","doing":true},{"event":"named-later-sibling","args":["follow-up"]}],"after_catch":{"did_action":1,"current_action":"callable-action-throw-named","doing_action":true,"doing_named":true},"followup_return":null,"after_followup":{"did_action":2,"current_action":"callable-action-throw-named","doing_action":true,"doing_named":true}},"nested_caught_throw":{"events":[{"event":"parent-enter","args":["parent-argument"],"current_action":"callable-action-nested-caught-parent","doing_parent":true,"doing_child":false,"parent_count":1,"child_count":0},{"event":"child-enter","args":["child-argument"],"current_action":"callable-action-nested-caught-child","doing_parent":true,"doing_child":true,"parent_count":1,"child_count":1},{"event":"child-throw","class":"RuntimeException","message":"nested action child boom","current_action":"callable-action-nested-caught-child","doing_parent":true,"doing_child":true,"parent_count":1,"child_count":1},{"event":"parent-catch","same_throwable":true,"class":"RuntimeException","message":"nested action child boom","current_action":"callable-action-nested-caught-child","doing_parent":true,"doing_child":true,"parent_count":1,"child_count":1},{"event":"parent-resume","current_action":"callable-action-nested-caught-child","doing_parent":true,"doing_child":true,"parent_count":1,"child_count":1}],"do_action_return":null,"after":{"current_action":"callable-action-nested-caught-parent","doing_parent":true,"doing_child":false,"parent_count":1,"child_count":1,"stack_depth":1}}}''')


EXPECTED_ACTION_API['counter_seed_values'] = ACTION_COUNTER_SEED_EXPECTATIONS
_nested_caught_hook_events = []
for _hook, _args in (
    ('callable-action-nested-caught-parent', ['callable-action-nested-caught-parent', 'parent-argument']),
    ('callable-action-nested-caught-child', ['callable-action-nested-caught-child', 'child-argument']),
):
    for _accepted_args in (0, 1):
        _nested_caught_hook_events.append({
            'accepted_args': _accepted_args,
            'args': _args,
            'current_action': _hook,
            'doing_action': True,
            'did_action': 1,
            'wp_actions': 1,
        })
EXPECTED_ACTION_API['all_hook']['events'][13:13] = _nested_caught_hook_events
_seed_all_hook_events = []
for _seed_case, _seed_hook in ACTION_COUNTER_SEED_HOOKS.items():
    for _event in ACTION_COUNTER_SEED_EXPECTATIONS[_seed_case]['events']:
        def _php_counter_value(value):
            if value['type'] == 'int':
                return int(value['value'])
            if value['type'] == 'float':
                return struct.unpack('>d', bytes.fromhex(value['bits']))[0]
            raise ValueError('unsupported action counter seed scalar')

        for _accepted_args in (0, 1):
            _seed_all_hook_events.append({
                'accepted_args': _accepted_args,
                'args': [_seed_hook],
                'current_action': _seed_hook,
                'doing_action': True,
                'did_action': _php_counter_value(_event['did_action']),
                'wp_actions': _php_counter_value(_event['wp_actions']),
            })
EXPECTED_ACTION_API['all_hook']['events'][19:19] = _seed_all_hook_events


def validate_action_observation(value):
    exact(value, ('action_api',), 'action API program observation')
    if EXPECTED_ACTION_API is None:
        raise ValueError('pinned WordPress action API observation has not been frozen')
    _require_exact(value['action_api'], EXPECTED_ACTION_API, 'measured action API group')


def validate_observation(value):
    if isinstance(value, dict) and set(value) == {'action_api'}:
        validate_action_observation(value)
        return
    action_group = None
    if isinstance(value, dict) and 'action_api' in value:
        exact(value, ('base', 'coercions', 'counter_cow', 'priority_cow', 'remove_cow', 'hook_unset_reentry', 'action_api'), 'program observation')
        action_group = value['action_api']
        value = {key: item for key, item in value.items() if key != 'action_api'}
    exact(value, ('base', 'coercions', 'counter_cow', 'priority_cow', 'remove_cow', 'hook_unset_reentry'), 'program observation')
    _require_exact(value['base'], EXPECTED_OBSERVATION, 'original twenty cases')
    if EXPECTED_COERCIONS is None:
        raise ValueError('pinned WordPress coercion observation has not been frozen')
    _require_exact(value['coercions'], EXPECTED_COERCIONS, 'measured coercion cases')
    _require_exact(value['counter_cow'], EXPECTED_COUNTER_COW, 'measured counter COW cases')
    if EXPECTED_PRIORITY_COW is None:
        raise ValueError('pinned WordPress priority COW observation has not been frozen')
    _require_exact(value['priority_cow'], EXPECTED_PRIORITY_COW, 'measured priority COW cases')
    _require_exact(value['remove_cow'], EXPECTED_REMOVE_COW, 'measured remove-filter COW cases')
    _require_exact(value['hook_unset_reentry'], EXPECTED_HOOK_UNSET_REENTRY, 'measured hook-unset reentry cases')
    if action_group is not None:
        validate_action_observation({'action_api': action_group})


def validate_result(value):
    exact(value, RESULT_FIELDS, 'callable v2 result')
    if value['scenario'] == ACTION_SCENARIO:
        expected_apis = ALL_APIS
        _require_exact(value['nested_caught_throw'], EXPECTED_NESTED_CAUGHT_THROW_RESULT,
                       'nested caught action throw projection')
    elif value['scenario'] == SCENARIO:
        expected_apis = APIS
        if value['nested_caught_throw'] is not None:
            raise ValueError('filter result has action-only nested throw projection')
    else:
        raise ValueError('callable v2 scenario differs')
    if value['continuation_peer'] is not True:
        raise ValueError('callable v2 scenario differs')
    counts = value['api_counts']
    exact(counts, expected_apis, 'API counts')
    if any(type(number) is not int or number < 0 for number in counts.values()):
        raise ValueError('API counts type/range differs')
    for name in ('frames_from_php', 'frames_from_rust', 'callback_invocations',
                 'release_effects', 'boundary_effects', 'argument_release_effects', 'continuation_depth_after'):
        if type(value[name]) is not int or value[name] < 0:
            raise ValueError(name + ' type/range differs')
    base = sum(counts.values()) + sum(value[key] for key in
        ('callback_invocations', 'release_effects', 'boundary_effects', 'argument_release_effects'))
    if value['frames_from_php'] != base + 4 or value['frames_from_rust'] != base + 3:
        raise ValueError('callable v2 frame accounting differs')
    if (value['continuation_depth_after'] != 0 or value['state_after'] != 'Finished'
            or value['late_resume_rejected'] is not True or value['late_call_rejected'] is not True):
        raise ValueError('callable v2 terminal state differs')
    _require_exact(value['registry_observation'],
        {'closed': True, 'live_after_close': 0, 'closed_read_rejected': True}, 'registry close')
    validate_observation(value['php_observation'])


def validate_action_result(value):
    if not isinstance(value, dict) or value.get('scenario') != ACTION_SCENARIO:
        raise ValueError('action API result scenario differs')
    validate_result(value)


def action_self_check():
    """Check the isolated action observation is frozen and rejects drift."""
    if EXPECTED_ACTION_API is None:
        raise ValueError('pinned WordPress action API observation has not been frozen')
    validate_action_observation({'action_api': EXPECTED_ACTION_API})
    action_result = {
        'scenario': ACTION_SCENARIO, 'continuation_peer': True,
        'frames_from_php': 4, 'frames_from_rust': 3,
        'api_counts': {name: 0 for name in ALL_APIS},
        'callback_invocations': 0, 'release_effects': 0, 'boundary_effects': 0,
        'argument_release_effects': 0, 'late_resume_rejected': True,
        'late_call_rejected': True, 'continuation_depth_after': 0,
        'state_after': 'Finished', 'php_observation': {'action_api': EXPECTED_ACTION_API},
        'registry_observation': {'closed': True, 'live_after_close': 0, 'closed_read_rejected': True},
        'nested_caught_throw': dict(EXPECTED_NESTED_CAUGHT_THROW_RESULT),
    }
    validate_action_result(action_result)
    mutations = []
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['do_action_returns']['populated'] = True
    mutations.append(('do_action return', changed))
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['counter_seed_values']['int_7']['events'][0]['did_action'] = {'type': 'float', 'bits': '4020000000000000'}
    mutations.append(('seeded counter wrong type', changed))
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['counter_seed_values']['float_7_5']['events'][0]['did_action']['bits'] = '4023000000000000'
    mutations.append(('seeded counter wrong bits', changed))
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['counter_seed_values']['php_int_max']['events'][0]['wp_actions'] = {
        'type': 'float', 'bits': '43dfffffffffffff'}
    mutations.append(('PHP_INT_MAX overflow progression', changed))
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['counter_seed_values']['php_int_max']['events'][1]['did_action'] = {
        'type': 'float', 'bits': '43dfffffffffffff'}
    mutations.append(('seeded did_action read', changed))
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['nested_caught_throw']['events'][3]['event'] = 'parent-resume'
    mutations.append(('nested caught throw ordering', changed))
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['nested_caught_throw']['events'][3]['same_throwable'] = False
    mutations.append(('nested caught throwable identity', changed))
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['nested_caught_throw']['do_action_return'] = {'type': 'null'}
    mutations.append(('nested parent do_action return', changed))
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['nested_caught_throw']['events'][1]['child_count'] = 2
    mutations.append(('nested child action count', changed))
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['nested_caught_throw']['events'][3]['current_action'] = 'callable-action-nested-caught-parent'
    mutations.append(('nested caught current action', changed))
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['nested_caught_throw']['after']['doing_child'] = True
    mutations.append(('nested caught child doing state', changed))
    changed = json.loads(json.dumps(EXPECTED_ACTION_API))
    changed['nested_caught_throw']['after']['stack_depth'] = 2
    mutations.append(('nested caught action stack depth', changed))
    for label, changed in mutations:
        try:
            validate_action_observation({'action_api': changed})
        except ValueError:
            continue
        raise AssertionError('changed action API observation accepted: ' + label)
    result_mutations = []
    for key, bad_value in (
        ('trace', ['parent-enter', 'child-enter', 'parent-catch', 'child-throw', 'parent-resume']),
        ('same_object', False), ('parent_do_action_return', {'type': 'null'}),
        ('parent_did_action', 2), ('child_did_action', 2),
        ('current_action', 'callable-action-nested-caught-child'),
        ('doing_parent', False), ('doing_child', True), ('stack_depth', 2),
    ):
        changed = json.loads(json.dumps(action_result))
        changed['nested_caught_throw'][key] = bad_value
        result_mutations.append(('result nested throw ' + key, changed))
    for label, changed in result_mutations:
        try:
            validate_action_result(changed)
        except ValueError:
            continue
        raise AssertionError('changed action result accepted: ' + label)
    filter_result = dict(action_result)
    filter_result.update({'scenario': SCENARIO, 'api_counts': {name: 0 for name in APIS},
        'php_observation': {
            'base': EXPECTED_OBSERVATION,
            'coercions': EXPECTED_COERCIONS,
            'counter_cow': EXPECTED_COUNTER_COW,
            'priority_cow': EXPECTED_PRIORITY_COW,
            'remove_cow': EXPECTED_REMOVE_COW,
            'hook_unset_reentry': EXPECTED_HOOK_UNSET_REENTRY,
        }, 'nested_caught_throw': None})
    validate_result(filter_result)
    filter_result['nested_caught_throw'] = dict(EXPECTED_NESTED_CAUGHT_THROW_RESULT)
    try:
        validate_result(filter_result)
    except ValueError:
        pass
    else:
        raise AssertionError('filter result accepted action-only nested throw projection')
    return True


def _bounded_nodes(value):
    count, pending = 0, [(value, 0)]
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > 64 or count > 65_536:
            raise ValueError('frame JSON depth/node limit exceeded')
        if isinstance(node, dict):
            pending.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            pending.extend((child, depth + 1) for child in node)


def validate_transcript(streams, result):
    """Replay nested actual effects, leases and primitive results without a PHP shadow VM."""
    validate_result(result)
    if set(streams) != {'php', 'rust'} or any(not isinstance(raw, bytes) for raw in streams.values()):
        raise ValueError('wire stream inventory differs')
    if sum(map(len, streams.values())) > 33_554_432:
        raise ValueError('combined wire byte limit exceeded')
    frames, lines = {}, {}
    for direction, raw in streams.items():
        lines[direction] = raw.splitlines(keepends=True)
        frames[direction] = []
        for seq, line in enumerate(lines[direction]):
            if len(line) > 1_048_576 or not line.endswith(b'\n'):
                raise ValueError('wire line framing/bounds differ')
            try:
                item = json.loads(line, object_pairs_hook=_unique_frame_object)
            except (RecursionError, UnicodeError) as error:
                raise ValueError('invalid bounded JSON frame') from error
            _bounded_nodes(item)
            fields = {'v', 'request', 'seq', 'kind', 'payload'}
            if not isinstance(item, dict) or set(item) not in (fields, fields | {'parent_effect'}):
                raise ValueError('wire envelope fields differ')
            if (type(item['v']) is not int or item['v'] != 1 or item['request'] != 'surface-probe-1'
                    or type(item['seq']) is not int or item['seq'] != seq or not isinstance(item['payload'], dict)):
                raise ValueError('wire envelope identity differs')
            frames[direction].append(item)
    cursor = dict.fromkeys(frames, 0)
    api_names = ALL_APIS if result['scenario'] == ACTION_SCENARIO else APIS
    counts = dict.fromkeys(api_names, 0)
    observed = dict.fromkeys(('callback_invocations', 'release_effects', 'boundary_effects', 'argument_release_effects'), 0)
    effects, live, pending_release, registrations = set(), {}, set(), {}
    normalized_arguments = {}
    issued_generations = {}
    action_counts = {}
    action_seed_dispatches = {}
    action_seed_reads = {}
    action_seed_keys = {
        ('string', hook.encode('ascii').hex()): case
        for case, hook in ACTION_COUNTER_SEED_HOOKS.items()
    }

    def hook_token(key):
        validate_key(key)
        return key['type'], key['hex']

    def action_counter_expected(key):
        seed_case = action_seed_keys.get(key)
        if seed_case is not None:
            progress = [ACTION_COUNTER_SEED_EXPECTATIONS[seed_case]['before']]
            progress.extend(event['did_action'] for event in ACTION_COUNTER_SEED_EXPECTATIONS[seed_case]['events'])
            return progress[action_seed_dispatches.get(key, 0)]
        return {'type': 'int', 'value': str(action_counts.get(key, 0))}

    def take(direction, kind, parent):
        index = cursor[direction]
        if index >= len(frames[direction]):
            raise ValueError('wire ended early')
        item = frames[direction][index]
        cursor[direction] += 1
        if (item['kind'] != kind or item.get('parent_effect') != parent
                or ('parent_effect' in item) != (parent is not None)):
            raise ValueError('wire kind/parent correlation differs')
        return item['payload']

    def peek(direction):
        index = cursor[direction]
        if index >= len(frames[direction]):
            raise ValueError('wire ended early')
        return frames[direction][index]

    def retain(value, kind):
        ident = handle(value, kind)
        if ident in live or any(old[2] == ident[2] and old not in pending_release for old in live):
            raise ValueError('live descriptor/slot reused')
        if ident[3] <= issued_generations.get(ident[2], 0):
            raise ValueError('retired/release-pending slot generation replayed')
        issued_generations[ident[2]] = ident[3]
        live[ident] = value
        return ident

    def require_live(value, kind):
        ident = handle(value, kind)
        if ident not in live or ident in pending_release:
            raise ValueError('effect descriptor is not active')
        return ident

    def completion(value, pod=False):
        if not isinstance(value, dict):
            raise ValueError('completion must be object')
        if value.get('kind') == 'value':
            exact(value, ('kind', 'value'), 'Value completion')
            if pod:
                validate_pod(value['value'])
        elif value.get('kind') == 'php_throwable':
            exact(value, ('kind', 'handle', 'class_hex', 'message_hex'), 'Throwable completion')
            ident = handle(value['handle'], 'throwable')
            if ident not in live:
                retain(value['handle'], 'throwable')
            canonical_hex(value['class_hex'], 'Throwable class')
            canonical_hex(value['message_hex'], 'Throwable message')
        else:
            raise ValueError('completion kind differs')

    def api(parent):
        payload = take('php', 'call_core', parent)
        name = payload.get('api')
        if name not in api_names:
            raise ValueError('unknown API')
        fields = {'api'} | ({'arguments'} if ARGUMENTS[name] else set())
        fields |= {'value', 'args'} if name == 'apply_filters' else {'args'} if name == 'do_action' else set()
        fields |= {'any', 'priority'} if name in ('has_filter', 'has_action') else {'any'} if name in ('doing_filter', 'doing_action') else set()
        exact(payload, fields, 'CallCore')
        descriptors = payload.get('arguments', {})
        exact(descriptors, ARGUMENTS[name], 'API argument inventory')
        owned = {retain(value, 'argument') for value in descriptors.values()}
        if len(owned) != len(descriptors):
            raise ValueError('API argument aliases not fresh')
        counts[name] += 1
        if 'any' in payload and type(payload['any']) is not bool:
            raise ValueError('API any flag differs')
        if name in ('has_filter', 'has_action') and payload['priority'] is not None:
            validate_pod(payload['priority'])
            if payload['priority']['type'] != 'int':
                raise ValueError('has_filter exact priority is not int')
        if name in ('apply_filters', 'do_action'):
            if not isinstance(payload['args'], list):
                raise ValueError('apply args are not list')
            for value in payload['args']:
                validate_pod(value)
            if name == 'apply_filters':
                validate_pod(payload['value'])
        context = {'name': name, 'payload': payload, 'owned': owned, 'released': not owned,
                   'hook': None, 'priority': None, 'callback_key': None, 'accepted': None,
                   'value': payload.get('value'), 'extra': payload.get('args'), 'throwable': None}
        while peek('rust')['kind'] == 'invoke_php':
            effect(parent, context)
        returned = take('rust', 'return', parent)
        exact(returned, ('api', 'completion'), 'API Return')
        if returned['api'] != name or not context['released']:
            raise ValueError('API Return identity/argument release differs')
        outcome = returned['completion']
        completion(outcome, True)
        if outcome['kind'] == 'value':
            node = outcome['value']; kind = node['type']
            if name in ('add_filter', 'remove_filter', 'doing_filter', 'add_action', 'remove_action', 'doing_action') and kind != 'bool':
                raise ValueError('API bool result differs')
            if name in ('add_filter', 'add_action') and node['value'] is not True:
                raise ValueError('add API result not true')
            if name in ('current_filter', 'current_action'):
                scalar(node)
            if name in ('has_filter', 'has_action'):
                exact_mode = payload['any'] or payload['priority'] is not None
                if exact_mode and kind != 'bool':
                    raise ValueError('has API exact/any result not bool')
                if not exact_mode and kind not in ('int', 'string') and node != {'type': 'bool', 'value': False}:
                    raise ValueError('has API search result differs')
            if name == 'did_filter':
                validate_did_filter_result(node)
            if name == 'did_action':
                validate_pod(node)
                seed_key = hook_token(context['hook']['key']) if context['hook'] is not None else None
                if seed_key in action_seed_keys:
                    _require_exact(node, action_counter_expected(seed_key), 'seeded did_action result')
                elif kind == 'int':
                    if canonical_integer(node['value'], 'did_action') < 0:
                        raise ValueError('did_action result differs')
                else:
                    raise ValueError('did_action result differs')
            if name == 'do_action' and node != {'type': 'null'}:
                raise ValueError('do_action result is not null')
        if name in ('apply_filters', 'do_action'):
            expected = context['throwable'] or {'kind': 'value', 'value': context['value']}
            if name == 'do_action':
                expected = context['throwable'] or {'kind': 'value', 'value': {'type': 'null'}}
            _require_exact(outcome, expected, 'dispatch callback/Return correlation')

    def effect(parent, context=None):
        payload = take('rust', 'invoke_php', parent)
        operation, effect_id = payload.get('operation'), payload.get('effect_id')
        if not isinstance(effect_id, str) or not effect_id or effect_id in effects:
            raise ValueError('effect replay/identity differs')
        effects.add(effect_id)
        action, argument_id, releasers = None, None, []
        if operation == 'run_script':
            exact(payload, ('operation', 'effect_id'), 'script InvokePhp')
            if effect_id != 'e-script' or parent is not None or len(effects) != 1:
                raise ValueError('script effect differs')
        elif operation == 'boundary':
            exact(payload, ('operation', 'effect_id', 'action', 'arguments'), 'boundary InvokePhp')
            if context is None:
                raise ValueError('boundary effect outside API')
            action, args = payload['action'], payload['arguments']
            fields = {'array_key': ('handle', 'access', 'null_to_zero'), 'callback_id': ('handle',),
                      'accepted_args': ('handle',), 'retain_callable': ('handle',),
                      'hook_scalar': ('handle',), 'release_arguments': ('handles',),
                      'missing_offset': ('key',), 'null_property': ('property',), 'null_method': ('method',),
                      'null_foreach': ()}
            if result['scenario'] == ACTION_SCENARIO:
                fields['action_counter_increment'] = ('handle',)
                fields['action_counter_read'] = ('handle',)
            if action not in fields:
                raise ValueError('unknown boundary action')
            exact(args, fields[action], 'boundary arguments')
            if action == 'release_arguments':
                if context['released'] or not isinstance(args['handles'], list):
                    raise ValueError('arguments already released')
                releasers = [require_live(value, 'argument') for value in args['handles']]
                if len(set(releasers)) != len(releasers) or set(releasers) != context['owned']:
                    raise ValueError('argument release batch differs')
                pending_release.update(releasers)
                observed['argument_release_effects'] += 1
            elif action in ('missing_offset', 'null_property', 'null_method', 'null_foreach'):
                if action == 'missing_offset':
                    validate_key(args['key'])
                elif action == 'null_property' and args['property'] != 'callbacks':
                    raise ValueError('null property differs')
                elif action == 'null_method' and (args['method'] not in ('add_filter', 'remove_filter', 'has_filter', 'apply_filters', 'add_action', 'remove_action', 'has_action', 'do_action')
                                                  or args['method'] != context['name']):
                    raise ValueError('null method differs from the active API')
                if action == 'null_foreach' and context['name'] not in ('apply_filters', 'do_action'):
                    raise ValueError('null foreach outside filter iteration')
                observed['boundary_effects'] += 1
            elif action in ('action_counter_increment', 'action_counter_read'):
                expected_api = 'do_action' if action == 'action_counter_increment' else 'did_action'
                if result['scenario'] != ACTION_SCENARIO or context['name'] != expected_api:
                    raise ValueError('action counter boundary used outside its action API')
                argument_id = require_live(args['handle'], 'argument')
                if argument_id not in context['owned']:
                    raise ValueError('action counter boundary uses another API argument')
                by_name = context['payload']['arguments']
                if by_name.get('hook') != args['handle']:
                    raise ValueError('action counter boundary does not use its hook argument')
                if action == 'action_counter_increment' and context['hook'] is None:
                    raise ValueError('action counter boundary precedes hook normalization')
                observed['boundary_effects'] += 1
            else:
                argument_id = require_live(args['handle'], 'argument')
                if argument_id not in context['owned']:
                    raise ValueError('boundary uses another API argument')
                by_name = context['payload']['arguments']
                role = next(name for name, descriptor in by_name.items() if descriptor == args['handle'])
                allowed_roles = {'array_key': ('hook', 'priority'), 'callback_id': ('callback',),
                                 'accepted_args': ('accepted_args',), 'retain_callable': ('callback',),
                                 'hook_scalar': ('hook',)}
                if role not in allowed_roles[action]:
                    raise ValueError('boundary uses the wrong argument role')
                if action == 'array_key':
                    if (args['access'] not in ('isset', 'nested_isset', 'read', 'write', 'unset', 'increment')
                            or type(args['null_to_zero']) is not bool
                            or args['null_to_zero'] != (role == 'priority')):
                        raise ValueError('array access mode/normalization role differs')
                    if context['name'] in ('doing_filter', 'current_filter', 'doing_action', 'current_action'):
                        raise ValueError('strict hook membership was key-coerced')
                observed['boundary_effects'] += 1
        elif operation == 'callable':
            exact(payload, ('operation', 'effect_id', 'handle', 'hook', 'priority', 'call_mode', 'args'), 'callable InvokePhp')
            ident = require_live(payload['handle'], 'callable')
            registration = registrations[ident]
            scalar(payload['hook']); validate_key(payload['priority'])
            if context is None or context['name'] not in ('apply_filters', 'do_action') or context['hook'] is None:
                raise ValueError('callable outside a typed dispatch')
            _require_exact(payload['hook'], context['hook']['original'], 'invoked original hook')
            _require_exact(payload['priority'], registration['priority']['key'], 'invoked priority key')
            mode, accepted = payload['call_mode'], registration['accepted']
            if mode == 'all':
                _require_exact(registration['hook']['key'], {'type': 'string', 'hex': b'all'.hex()}, 'all registration')
                if context['name'] == 'do_action':
                    expected_args = [context['hook']['original'], *context['extra']]
                else:
                    expected_args = [context['hook']['original'], context['value'], *context['extra']]
            else:
                _require_exact(registration['hook']['key'], context['hook']['key'], 'named registration')
                if mode != ('zero' if accepted == 0 else 'array'):
                    raise ValueError('named call mode differs')
                if context['name'] == 'do_action':
                    action_args = context['extra'] or [{'type': 'string', 'hex': ''}]
                    expected_args = [] if accepted == 0 else action_args[:accepted]
                else:
                    expected_args = [] if accepted == 0 else [context['value'], *context['extra']][:accepted]
            _require_exact(payload['args'], expected_args, 'typed argument slicing')
            observed['callback_invocations'] += 1
        elif operation == 'release_callable':
            exact(payload, ('operation', 'effect_id', 'handle'), 'callable release')
            releasers = [require_live(payload['handle'], 'callable')]
            pending_release.update(releasers)
            observed['release_effects'] += 1
        elif operation == 'release_callable_unwind':
            exact(payload, ('operation', 'effect_id', 'handle', 'throwable'), 'callable unwind release')
            if context is None or context['throwable'] is None:
                raise ValueError('callable unwind release without a pending PHP Throwable')
            releasers = [require_live(payload['handle'], 'callable')]
            require_live(payload['throwable'], 'throwable')
            _require_exact(payload['throwable'], context['throwable']['handle'], 'pending unwind Throwable')
            pending_release.update(releasers)
            observed['release_effects'] += 1
        elif operation == 'close_registry':
            exact(payload, ('operation', 'effect_id'), 'registry close')
            if parent is not None or any(ident[1] != 'throwable' for ident in live):
                raise ValueError('registry close with unreleased leases')
        else:
            raise ValueError('unknown operation')
        while peek('php')['kind'] == 'call_core':
            if operation == 'close_registry':
                raise ValueError('API after registry close')
            api(effect_id)
        resumed = take('php', 'resume', parent)
        exact(resumed, ('effect_id', 'completion'), 'Resume')
        if resumed['effect_id'] != effect_id:
            raise ValueError('Resume correlation differs')
        outcome = resumed['completion']
        completion(outcome, operation == 'callable')
        if operation == 'run_script':
            _require_exact(outcome, {'kind': 'value', 'value': result['php_observation']}, 'script observation')
        elif operation == 'boundary' and outcome['kind'] == 'value':
            value = outcome['value']
            if action == 'array_key':
                exact(value, ('original', 'key'), 'array-key result')
                scalar(value['original']); validate_key(value['key'])
                if payload['arguments']['null_to_zero'] and value['original']['type'] == 'null':
                    raise ValueError('null priority was not normalized to effective int zero')
                _require_exact(value['key'], normalized_scalar_key(value['original']), 'PHP scalar/key relationship')
                previous_key = normalized_arguments.get((argument_id, payload['arguments']['null_to_zero']))
                if previous_key is not None:
                    _require_exact(value, previous_key, 'immutable argument normalization across phases')
                normalized_arguments[(argument_id, payload['arguments']['null_to_zero'])] = value
                by_name = context['payload']['arguments']
                if by_name.get('hook') == payload['arguments']['handle']:
                    context['hook'] = value
                elif by_name.get('priority') == payload['arguments']['handle']:
                    context['priority'] = value
            elif action == 'callback_id':
                exact(value, ('key',), 'callback ID result')
                if value['key'] is not None:
                    validate_key(value['key'])
                context['callback_key'] = value['key']
            elif action == 'accepted_args':
                validate_pod(value)
                if value['type'] != 'int':
                    raise ValueError('accepted args result not int')
                context['accepted'] = canonical_integer(value['value'], 'accepted args')
            elif action == 'retain_callable':
                exact(value, ('handle',), 'retained callable result')
                if context['hook'] is None or context['priority'] is None or context['accepted'] is None or context['callback_key'] is None:
                    raise ValueError('callable retained before required conversion/identity')
                ident = retain(value['handle'], 'callable')
                registrations[ident] = {key: context[key] for key in ('hook', 'priority', 'accepted', 'callback_key')}
            elif action == 'hook_scalar':
                exact(value, ('supported', 'value'), 'hook scalar result')
                if type(value['supported']) is not bool:
                    raise ValueError('supported flag differs')
                if value['supported']:
                    scalar(value['value'])
                elif value['value'] is not None:
                    raise ValueError('unsupported scalar was substituted')
            elif action == 'action_counter_increment':
                validate_pod(value)
                key = hook_token(context['hook']['key'])
                seed_case = action_seed_keys.get(key)
                if seed_case is not None:
                    step = action_seed_dispatches.get(key, 0)
                    progress = [ACTION_COUNTER_SEED_EXPECTATIONS[seed_case]['before']]
                    progress.extend(event['did_action'] for event in ACTION_COUNTER_SEED_EXPECTATIONS[seed_case]['events'])
                    if step >= 2:
                        raise ValueError('seeded action counter dispatched too many times')
                    expected = progress[step + 1]
                    _require_exact(value, expected, 'seeded action counter increment')
                    action_seed_dispatches[key] = step + 1
                else:
                    if value['type'] != 'int':
                        raise ValueError('action counter increment result is not an integer')
                    expected = action_counts.get(key, 0) + 1
                    if canonical_integer(value['value'], 'action counter increment') != expected:
                        raise ValueError('action counter increment differs from the measured dispatch count')
                    action_counts[key] = expected
            elif action == 'action_counter_read':
                validate_pod(value)
                if context['hook'] is not None:
                    key = hook_token(context['hook']['key'])
                    if key in action_seed_keys:
                        _require_exact(value, action_counter_expected(key), 'seeded action counter read')
                        action_seed_reads[key] = action_seed_reads.get(key, 0) + 1
                    else:
                        if value['type'] != 'int':
                            raise ValueError('action counter read is not an integer')
                        observed_count = canonical_integer(value['value'], 'action counter read')
                        if observed_count < 0:
                            raise ValueError('action counter read is negative')
                        expected = action_counts.get(key, 0)
                        if observed_count != expected:
                            raise ValueError('action counter read differs from measured action dispatches')
            elif action in ('missing_offset', 'null_property', 'null_foreach'):
                _require_exact(value, {'missing': True}, 'native missing access')
            elif action == 'null_method':
                raise ValueError('null method unexpectedly returned a value')
            elif action == 'release_arguments':
                _require_exact(value, {'released': True}, 'argument release ACK')
                context['released'] = True
        elif operation == 'release_callable' and outcome['kind'] == 'value':
            _require_exact(outcome['value'], {'released': True}, 'callable release ACK')
        elif operation == 'release_callable_unwind' and outcome['kind'] == 'value':
            _require_exact(outcome, {'kind': 'value', 'value': {'released': True}}, 'callable unwind release ACK')
        elif operation == 'close_registry':
            _require_exact(outcome, {'kind': 'value', 'value': result['registry_observation']}, 'registry close observation')
            live.clear()
        for ident in releasers:
            pending_release.remove(ident)
            del live[ident]
        if context is not None:
            if outcome['kind'] == 'php_throwable' and operation in (
                'callable', 'release_callable', 'release_callable_unwind', 'boundary'
            ):
                context['throwable'] = outcome
            elif context['name'] == 'apply_filters' and operation == 'callable' and payload['call_mode'] != 'all':
                context['value'] = outcome['value']
        return operation

    scenario = result['scenario']
    _require_exact(take('php', 'start_root', None),
        {'operation': scenario, 'protocol': 2, 'request_generation': REQUEST}, 'root start')
    if effect(None) != 'run_script':
        raise ValueError('script effect missing')
    closed = False
    while peek('rust')['kind'] == 'invoke_php':
        if closed:
            raise ValueError('effect after close')
        operation = effect(None)
        if operation not in ('release_callable', 'release_callable_unwind', 'close_registry'):
            raise ValueError('invalid root cleanup effect')
        closed = operation == 'close_registry'
    if not closed:
        raise ValueError('missing registry close')
    _require_exact(take('rust', 'return', None), {'operation': scenario, 'status': 'ok'}, 'root Return')
    expected_final = {'status': 'ok', 'php_observation': result['php_observation'],
        'registry_observation': result['registry_observation'], 'transport': {
            'frames_sent': len(frames['php']) - 1, 'frames_received': len(frames['rust']),
            'bytes_total': sum(map(len, lines['php'][:-1])) + len(streams['rust'])}}
    _require_exact(take('php', 'request_finished', None), expected_final, 'RequestFinished')
    if any(cursor[key] != len(frames[key]) or len(frames[key]) != result['frames_from_' + key] for key in frames):
        raise ValueError('wire EOF/frame counts differ')
    if counts != result['api_counts'] or any(observed[key] != result[key] for key in observed):
        raise ValueError('observed effect/API counts differ')
    if result['scenario'] == ACTION_SCENARIO:
        for case, hook in ACTION_COUNTER_SEED_HOOKS.items():
            key = ('string', hook.encode('ascii').hex())
            if (action_seed_dispatches.get(key, 0) != 2
                    or action_seed_reads.get(key, 0) != 8):
                raise ValueError('seeded action counter dispatch/read inventory differs: ' + case)


def self_check():
    """Exercise structural replay while keeping unfrozen oracle data a failing gate."""
    global EXPECTED_COERCIONS, EXPECTED_COUNTER_COW, EXPECTED_PRIORITY_COW, EXPECTED_REMOVE_COW, EXPECTED_HOOK_UNSET_REENTRY
    import copy
    descriptor = {'request': REQUEST, 'kind': 'argument', 'slot': 0, 'generation': 1, 'token': '0102030405060708'}
    handle(descriptor, 'argument')
    for field, value in (('request', '61'), ('kind', 'callable'), ('slot', False), ('generation', 0), ('token', 'FF')):
        bad = dict(descriptor); bad[field] = value
        try:
            handle(bad, 'argument')
        except ValueError:
            continue
        raise AssertionError('bad argument descriptor accepted: ' + field)
    for node in ({'type': 'null'}, {'type': 'bool', 'value': False},
                 {'type': 'float', 'bits': '8000000000000000'}, {'type': 'string', 'hex': '00ff'}):
        scalar(node)
    try:
        scalar({'type': 'array', 'entries': []})
    except ValueError:
        pass
    else:
        raise AssertionError('array treated as scalar')
    too_deep = None
    for _ in range(66):
        too_deep = [too_deep]
    try:
        _bounded_nodes(too_deep)
    except ValueError:
        pass
    else:
        raise AssertionError('deep frame accepted')
    previous = EXPECTED_COERCIONS
    previous_counter_cow = EXPECTED_COUNTER_COW
    previous_priority_cow = EXPECTED_PRIORITY_COW
    previous_remove_cow = EXPECTED_REMOVE_COW
    previous_hook_unset_reentry = EXPECTED_HOOK_UNSET_REENTRY
    sample = {'self_test_only': True}
    sample_counter_cow = {'self_test_only': True}
    sample_priority_cow = {'self_test_only': True}
    sample_remove_cow = {'self_test_only': True}
    sample_hook_unset_reentry = {'self_test_only': True}
    observation = {'base': EXPECTED_OBSERVATION, 'coercions': sample, 'counter_cow': sample_counter_cow,
        'priority_cow': sample_priority_cow, 'remove_cow': sample_remove_cow,
        'hook_unset_reentry': sample_hook_unset_reentry}
    for accepted in ({'type': 'null'}, {'type': 'int', 'value': '0'}):
        validate_did_filter_result(accepted)
    for rejected in ({'type': 'bool', 'value': False}, {'type': 'int', 'value': '-1'}, {'type': 'array', 'entries': []}):
        try:
            validate_did_filter_result(rejected)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid did_filter POD result accepted')
    counts = dict.fromkeys(APIS, 0); counts['current_filter'] = 1
    result = {'scenario': SCENARIO, 'continuation_peer': True, 'frames_from_php': 5, 'frames_from_rust': 4,
        'api_counts': counts, 'callback_invocations': 0, 'release_effects': 0, 'boundary_effects': 0,
        'argument_release_effects': 0, 'late_resume_rejected': True, 'late_call_rejected': True,
        'continuation_depth_after': 0, 'state_after': 'Finished', 'php_observation': observation,
        'registry_observation': {'closed': True, 'live_after_close': 0, 'closed_read_rejected': True}}
    try:
        EXPECTED_COERCIONS = sample
        EXPECTED_COUNTER_COW = sample_counter_cow
        EXPECTED_PRIORITY_COW = sample_priority_cow
        EXPECTED_REMOVE_COW = sample_remove_cow
        EXPECTED_HOOK_UNSET_REENTRY = sample_hook_unset_reentry
        validate_result(result)
        frames = {'php': [], 'rust': []}
        def add(direction, kind, payload, parent=None):
            item = {'v': 1, 'request': 'surface-probe-1', 'seq': len(frames[direction]), 'kind': kind, 'payload': payload}
            if parent is not None:
                item['parent_effect'] = parent
            frames[direction].append(item)
        add('php', 'start_root', {'operation': SCENARIO, 'protocol': 2, 'request_generation': REQUEST})
        add('rust', 'invoke_php', {'operation': 'run_script', 'effect_id': 'e-script'})
        add('php', 'call_core', {'api': 'current_filter'}, 'e-script')
        add('rust', 'return', {'api': 'current_filter', 'completion': {'kind': 'value', 'value': {'type': 'bool', 'value': False}}}, 'e-script')
        add('php', 'resume', {'effect_id': 'e-script', 'completion': {'kind': 'value', 'value': observation}})
        add('rust', 'invoke_php', {'operation': 'close_registry', 'effect_id': 'e-close'})
        add('php', 'resume', {'effect_id': 'e-close', 'completion': {'kind': 'value', 'value': result['registry_observation']}})
        add('rust', 'return', {'operation': SCENARIO, 'status': 'ok'})
        def encode(value):
            return {key: b''.join((json.dumps(item, separators=(',', ':')) + '\n').encode() for item in entries) for key, entries in value.items()}
        encoded = encode(frames)
        add('php', 'request_finished', {'status': 'ok', 'php_observation': observation,
            'registry_observation': result['registry_observation'], 'transport': {
                'frames_sent': 4, 'frames_received': 4, 'bytes_total': sum(map(len, encoded.values()))}})
        validate_transcript(encode(frames), result)
        for name in ('wrong_protocol', 'wrong_parent', 'wrong_sequence', 'wrong_return_type', 'false_close', 'extra_frame'):
            bad = copy.deepcopy(frames)
            if name == 'wrong_protocol': bad['php'][0]['payload']['protocol'] = 1
            elif name == 'wrong_parent': bad['php'][1]['parent_effect'] = 'foreign'
            elif name == 'wrong_sequence': bad['php'][1]['seq'] = 0
            elif name == 'wrong_return_type': bad['rust'][1]['payload']['completion']['value'] = {'type': 'array', 'entries': []}
            elif name == 'false_close': bad['php'][3]['payload']['completion']['value']['closed'] = 1
            elif name == 'extra_frame': bad['php'].append(copy.deepcopy(bad['php'][-1]))
            wire = encode(bad)
            bad['php'][4]['payload']['transport']['bytes_total'] = sum(map(len, wire['php'].splitlines(keepends=True)[:4])) + len(wire['rust'])
            try:
                validate_transcript(encode(bad), result)
            except (ValueError, TypeError, KeyError):
                continue
            raise AssertionError('bad v2 transcript accepted: ' + name)
    finally:
        EXPECTED_COERCIONS = previous
        EXPECTED_COUNTER_COW = previous_counter_cow
        EXPECTED_PRIORITY_COW = previous_priority_cow
        EXPECTED_REMOVE_COW = previous_remove_cow
        EXPECTED_HOOK_UNSET_REENTRY = previous_hook_unset_reentry
    if previous is None:
        try:
            validate_result(result)
        except ValueError as error:
            if 'not been frozen' not in str(error):
                raise
        else:
            raise AssertionError('unfrozen WP oracle accepted')

TRANSCRIPT_NEGATIVE_INVENTORY = (
    'wrong_protocol', 'wrong_generation', 'replayed_sequence', 'wrong_parent', 'unknown_api',
    'wrong_argument_kind', 'duplicate_live_slot', 'unknown_boundary_action', 'wrong_boundary_parent',
    'wrong_boundary_argument', 'wrong_argument_role', 'wrong_array_access', 'wrong_null_normalization_flag',
    'wrong_normalization_role',
    'wrong_normalized_key', 'nonscalar_normalization', 'wrong_accepted_args_type',
    'wrong_retained_callable_kind', 'wrong_callback_hook', 'wrong_callback_priority',
    'wrong_callback_args', 'wrong_call_mode', 'incomplete_argument_release',
    'duplicate_argument_release', 'false_argument_ack', 'false_callable_ack',
    'wrong_null_foreach_arguments', 'false_null_foreach_ack', 'wrong_null_method_api',
    'wrong_api_result_type', 'wrong_resume_effect', 'wrong_script_observation',
    'wrong_unwind_payload_fields', 'wrong_unwind_callable_handle', 'wrong_unwind_throwable_kind',
    'wrong_unwind_throwable_token', 'false_unwind_release_ack',
    'live_registry_close', 'wrong_transport', 'extra_frame', 'duplicate_decoded_key', 'unterminated_frame',
)

ACTION_TRANSCRIPT_NEGATIVE_INVENTORY = (
    'unknown_api', 'wrong_do_action_result', 'wrong_did_action_type',
    'wrong_action_callback_args', 'wrong_counter_seed_increment_type',
    'wrong_counter_seed_float_bits', 'wrong_counter_seed_overflow_progression',
    'wrong_counter_seed_read_value', 'wrong_script_observation', 'wrong_transport',
)


def reject_action_transcripts(streams, result):
    """Check action-specific wire corruptions against the closed replay validator."""
    validate_action_result(result)
    validate_transcript(streams, result)
    source = {key: [json.loads(line, object_pairs_hook=_unique_frame_object)
                    for line in raw.splitlines()] for key, raw in streams.items()}

    def locate(direction, predicate):
        return next(i for i, item in enumerate(source[direction]) if predicate(item))

    action_api = locate('php', lambda item: item.get('kind') == 'call_core'
                        and item.get('payload', {}).get('api') == 'do_action')
    did_action_api = locate('php', lambda item: item.get('kind') == 'call_core'
                            and item.get('payload', {}).get('api') == 'did_action')
    action_return = locate('rust', lambda item: item.get('kind') == 'return'
                           and item.get('payload', {}).get('api') == 'do_action')
    did_action_return = locate('rust', lambda item: item.get('kind') == 'return'
                               and item.get('payload', {}).get('api') == 'did_action')
    callback = locate('rust', lambda item: item.get('kind') == 'invoke_php'
                      and item.get('payload', {}).get('operation') == 'callable')
    script_resume = locate('php', lambda item: item.get('kind') == 'resume'
                           and item.get('payload', {}).get('completion', {}).get('value') == result['php_observation'])
    finished = locate('php', lambda item: item.get('kind') == 'request_finished')
    increment_boundaries = [i for i, item in enumerate(source['rust'])
                            if item.get('kind') == 'invoke_php'
                            and item.get('payload', {}).get('operation') == 'boundary'
                            and item.get('payload', {}).get('action') == 'action_counter_increment']
    read_boundaries = [i for i, item in enumerate(source['rust'])
                       if item.get('kind') == 'invoke_php'
                       and item.get('payload', {}).get('operation') == 'boundary'
                       and item.get('payload', {}).get('action') == 'action_counter_read']

    def resume_index(boundary_index):
        effect_id = source['rust'][boundary_index]['payload']['effect_id']
        return locate('php', lambda item: item.get('kind') == 'resume'
                      and item.get('payload', {}).get('effect_id') == effect_id)

    def handle_id(value):
        return json.dumps(value, sort_keys=True, separators=(',', ':'))

    hook_names_by_handle = {}
    for boundary_index, item in enumerate(source['rust']):
        payload = item.get('payload', {})
        if (item.get('kind') != 'invoke_php' or payload.get('operation') != 'boundary'
                or payload.get('action') != 'array_key'):
            continue
        handle = payload.get('arguments', {}).get('handle')
        if not isinstance(handle, dict):
            continue
        resume = source['php'][resume_index(boundary_index)]
        original = resume.get('payload', {}).get('completion', {}).get('value', {}).get('original')
        if isinstance(original, dict) and original.get('type') == 'string':
            hook_names_by_handle[handle_id(handle)] = bytes.fromhex(original['hex']).decode('ascii')

    def seeded_boundaries(indices):
        grouped = {hook: [] for hook in ACTION_COUNTER_SEED_HOOKS.values()}
        for index in indices:
            handle = source['rust'][index]['payload'].get('arguments', {}).get('handle')
            hook = hook_names_by_handle.get(handle_id(handle)) if isinstance(handle, dict) else None
            if hook in grouped:
                grouped[hook].append(index)
        return grouped

    seeded_increments = seeded_boundaries(increment_boundaries)
    seeded_reads = seeded_boundaries(read_boundaries)
    if (any(len(seeded_increments[hook]) != 2 for hook in seeded_increments)
            or any(len(seeded_reads[hook]) != 8 for hook in seeded_reads)):
        raise ValueError('seeded action counter boundary inventory is incomplete')

    def encode(frames):
        return {direction: b''.join((json.dumps(item, separators=(',', ':')) + '\n').encode()
                                    for item in items) for direction, items in frames.items()}

    accepted = []
    for name in ACTION_TRANSCRIPT_NEGATIVE_INVENTORY:
        frames = json.loads(json.dumps(source))
        if name == 'unknown_api':
            frames['php'][action_api]['payload']['api'] = 'unknown_action_api'
        elif name == 'wrong_do_action_result':
            frames['rust'][action_return]['payload']['completion']['value'] = {'type': 'bool', 'value': True}
        elif name == 'wrong_did_action_type':
            frames['rust'][did_action_return]['payload']['completion']['value'] = {'type': 'bool', 'value': False}
        elif name == 'wrong_action_callback_args':
            frames['rust'][callback]['payload']['args'].append({'type': 'string', 'hex': '00'})
        elif name == 'wrong_counter_seed_increment_type':
            resume = resume_index(seeded_increments[ACTION_COUNTER_SEED_HOOKS['float_7_5']][0])
            frames['php'][resume]['payload']['completion']['value'] = {'type': 'int', 'value': '8'}
        elif name == 'wrong_counter_seed_float_bits':
            resume = resume_index(seeded_increments[ACTION_COUNTER_SEED_HOOKS['float_7_5']][1])
            frames['php'][resume]['payload']['completion']['value'] = {'type': 'float', 'bits': '4021000000000000'}
        elif name == 'wrong_counter_seed_overflow_progression':
            resume = resume_index(seeded_increments[ACTION_COUNTER_SEED_HOOKS['php_int_max']][0])
            frames['php'][resume]['payload']['completion']['value'] = {'type': 'float', 'bits': '43dfffffffffffff'}
        elif name == 'wrong_counter_seed_read_value':
            resume = resume_index(seeded_reads[ACTION_COUNTER_SEED_HOOKS['php_int_max']][-1])
            frames['php'][resume]['payload']['completion']['value'] = {'type': 'float', 'bits': '43dfffffffffffff'}
        elif name == 'wrong_script_observation':
            frames['php'][script_resume]['payload']['completion']['value'] = {}
        elif name == 'wrong_transport':
            frames['php'][finished]['payload']['transport']['frames_sent'] = 0
        try:
            validate_transcript(encode(frames), result)
        except (ValueError, KeyError, TypeError, IndexError, StopIteration):
            accepted.append(name)
            continue
        raise AssertionError('corrupted action transcript accepted: ' + name)
    return accepted


def reject_mutated_transcripts(streams, result):
    """Corrupt observed v2 frames; recompute byte counts to isolate semantic checks."""
    import copy
    validate_transcript(streams, result)
    source = {key: [json.loads(line) for line in raw.splitlines()] for key, raw in streams.items()}
    def find(direction, predicate):
        return next(index for index, item in enumerate(source[direction]) if predicate(item))
    api_index = find('php', lambda item: item['kind'] == 'call_core' and len(item['payload'].get('arguments', {})) >= 2)
    resumed_values = {item['payload']['effect_id']: item['payload']['completion'] for item in source['php'] if item['kind'] == 'resume'}
    boundary_index = find('rust', lambda item: item['payload'].get('operation') == 'boundary' and item['payload'].get('action') == 'array_key' and resumed_values[item['payload']['effect_id']]['kind'] == 'value')
    accepted_index = find('rust', lambda item: item['payload'].get('operation') == 'boundary' and item['payload'].get('action') == 'accepted_args')
    retain_index = find('rust', lambda item: item['payload'].get('operation') == 'boundary' and item['payload'].get('action') == 'retain_callable')
    release_index = find('rust', lambda item: item['payload'].get('operation') == 'boundary' and item['payload'].get('action') == 'release_arguments' and len(item['payload']['arguments']['handles']) >= 2)
    callback_index = find('rust', lambda item: item['payload'].get('operation') == 'callable' and item['payload'].get('args'))
    callable_release_index = find('rust', lambda item: item['payload'].get('operation') == 'release_callable')
    return_index = find('rust', lambda item: item['kind'] == 'return' and item['payload'].get('api') == 'add_filter' and item['payload']['completion']['kind'] == 'value')
    script_index = find('php', lambda item: item['kind'] == 'resume' and item['payload']['effect_id'] == 'e-script')
    close_index = find('rust', lambda item: item['payload'].get('operation') == 'close_registry')
    foreach_index = find('rust', lambda item: item['payload'].get('operation') == 'boundary' and item['payload'].get('action') == 'null_foreach')
    null_method_index = find('rust', lambda item: item['payload'].get('operation') == 'boundary' and item['payload'].get('action') == 'null_method')
    unwind_index = find('rust', lambda item: item['payload'].get('operation') == 'release_callable_unwind')
    def resume_for(index):
        effect_id = source['rust'][index]['payload']['effect_id']
        return find('php', lambda item: item['kind'] == 'resume' and item['payload']['effect_id'] == effect_id)
    boundary_resume, accepted_resume, retained_resume = map(resume_for, (boundary_index, accepted_index, retain_index))
    released_resume, callable_released_resume, closed_resume = map(resume_for, (release_index, callable_release_index, close_index))
    foreach_resume = resume_for(foreach_index)
    unwind_resume = resume_for(unwind_index)
    def encode(frames):
        return {key: b''.join((json.dumps(item, separators=(',', ':')) + '\n').encode() for item in entries) for key, entries in frames.items()}
    def flip_token(descriptor):
        text = descriptor['token']; descriptor['token'] = ('a' if text[0] != 'a' else 'b') + text[1:]
    for name in TRANSCRIPT_NEGATIVE_INVENTORY:
        frames = copy.deepcopy(source)
        api_payload = frames['php'][api_index]['payload']
        boundary_payload = frames['rust'][boundary_index]['payload']
        callback_payload = frames['rust'][callback_index]['payload']
        if name == 'wrong_protocol': frames['php'][0]['payload']['protocol'] = 1
        elif name == 'wrong_generation': frames['php'][0]['payload']['request_generation'] = '61'
        elif name == 'replayed_sequence': frames['php'][api_index]['seq'] = 0
        elif name == 'wrong_parent': frames['php'][api_index]['parent_effect'] = 'foreign'
        elif name == 'unknown_api': api_payload['api'] = 'unknown'
        elif name == 'wrong_argument_kind': next(iter(api_payload['arguments'].values()))['kind'] = 'callable'
        elif name == 'duplicate_live_slot':
            first, second = list(api_payload['arguments'].values())[:2]
            second['slot'] = first['slot']; second['generation'] = first['generation']
        elif name == 'unknown_boundary_action': boundary_payload['action'] = 'unknown'
        elif name == 'wrong_boundary_parent': frames['rust'][boundary_index]['parent_effect'] = 'foreign'
        elif name == 'wrong_boundary_argument': flip_token(boundary_payload['arguments']['handle'])
        elif name == 'wrong_argument_role':
            # Keep a real live hook/priority tuple; callback-ID must use callback.
            boundary_payload['action'] = 'callback_id'
            boundary_payload['arguments'] = {'handle': boundary_payload['arguments']['handle']}
        elif name == 'wrong_array_access': boundary_payload['arguments']['access'] = 'unknown'
        elif name == 'wrong_null_normalization_flag': boundary_payload['arguments']['null_to_zero'] = 0
        elif name == 'wrong_normalization_role': boundary_payload['arguments']['null_to_zero'] = not boundary_payload['arguments']['null_to_zero']
        elif name == 'wrong_normalized_key':
            value = frames['php'][boundary_resume]['payload']['completion']['value']
            value['key'] = {'type': 'string', 'hex': b'changed-key'.hex()} if value['key'] != {'type': 'string', 'hex': b'changed-key'.hex()} else {'type': 'string', 'hex': b'other-key'.hex()}
        elif name == 'nonscalar_normalization': frames['php'][boundary_resume]['payload']['completion']['value']['original'] = {'type': 'array', 'entries': []}
        elif name == 'wrong_accepted_args_type': frames['php'][accepted_resume]['payload']['completion'] = {'kind': 'value', 'value': {'type': 'bool', 'value': True}}
        elif name == 'wrong_retained_callable_kind': frames['php'][retained_resume]['payload']['completion']['value']['handle']['kind'] = 'argument'
        elif name == 'wrong_callback_hook':
            replacement = {'type': 'string', 'hex': b'changed-hook'.hex()}
            callback_payload['hook'] = replacement if callback_payload['hook'] != replacement else {'type': 'bool', 'value': True}
        elif name == 'wrong_callback_priority':
            replacement = {'type': 'string', 'hex': b'changed-priority'.hex()}
            callback_payload['priority'] = replacement if callback_payload['priority'] != replacement else {'type': 'int', 'hex': b'0'.hex()}
        elif name == 'wrong_callback_args': callback_payload['args'] = []
        elif name == 'wrong_call_mode': callback_payload['call_mode'] = 'unknown'
        elif name == 'incomplete_argument_release': frames['rust'][release_index]['payload']['arguments']['handles'].pop()
        elif name == 'duplicate_argument_release': frames['rust'][release_index]['payload']['arguments']['handles'].append(copy.deepcopy(frames['rust'][release_index]['payload']['arguments']['handles'][0]))
        elif name == 'false_argument_ack': frames['php'][released_resume]['payload']['completion']['value']['released'] = 1
        elif name == 'false_callable_ack': frames['php'][callable_released_resume]['payload']['completion']['value']['released'] = False
        elif name == 'wrong_null_foreach_arguments': frames['rust'][foreach_index]['payload']['arguments'] = {'undeclared': True}
        elif name == 'false_null_foreach_ack': frames['php'][foreach_resume]['payload']['completion']['value']['missing'] = False
        elif name == 'wrong_null_method_api': frames['rust'][null_method_index]['payload']['arguments']['method'] = 'has_filter'
        elif name == 'wrong_api_result_type': frames['rust'][return_index]['payload']['completion'] = {'kind': 'value', 'value': {'type': 'int', 'value': '1'}}
        elif name == 'wrong_resume_effect': frames['php'][boundary_resume]['payload']['effect_id'] = 'foreign'
        elif name == 'wrong_script_observation': frames['php'][script_index]['payload']['completion']['value'] = {}
        elif name == 'wrong_unwind_payload_fields': frames['rust'][unwind_index]['payload'].pop('throwable')
        elif name == 'wrong_unwind_callable_handle': flip_token(frames['rust'][unwind_index]['payload']['handle'])
        elif name == 'wrong_unwind_throwable_kind': frames['rust'][unwind_index]['payload']['throwable']['kind'] = 'callable'
        elif name == 'wrong_unwind_throwable_token': flip_token(frames['rust'][unwind_index]['payload']['throwable'])
        elif name == 'false_unwind_release_ack': frames['php'][unwind_resume]['payload']['completion']['value']['released'] = False
        elif name == 'live_registry_close': frames['php'][closed_resume]['payload']['completion']['value']['live_after_close'] = 1
        elif name == 'wrong_transport': frames['php'][-1]['payload']['transport']['frames_received'] = 0
        elif name == 'extra_frame': frames['php'].append(copy.deepcopy(frames['php'][-1]))
        encoded = encode(frames)
        if name != 'wrong_transport':
            # Locate the original final frame even when an extra frame was appended.
            final = frames['php'][len(source['php']) - 1]
            final['payload']['transport']['bytes_total'] = sum(map(len, encoded['php'].splitlines(keepends=True)[:len(source['php']) - 1])) + len(encoded['rust'])
            encoded = encode(frames)
        if name == 'duplicate_decoded_key': encoded['php'] = encoded['php'].replace(b'"v":1', b'"v":1,"v":1', 1)
        elif name == 'unterminated_frame': encoded['php'] = encoded['php'][:-1]
        try:
            validate_transcript(encoded, result)
        except (ValueError, KeyError, TypeError, IndexError, StopIteration):
            continue
        raise AssertionError('corrupted v2 transcript accepted: ' + name)
    return list(TRANSCRIPT_NEGATIVE_INVENTORY)
