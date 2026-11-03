"""Prepare a valid-UTF8-only native wpdb corpus without executing PHP or a DB."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/wpdb-utf8-probe'
PINS = {
    'experiments/wpdb-crud-probe/fixtures/entry.php': '6dce4d2b66602eee8a3fde14bc6d7d6f6a06573e5400a84e7119e0605ade5caa',
    'experiments/wpdb-crud-probe/fixtures/native.php': '97b3e6e737328de0a4d6f129dfd73dddf04568689d47ddf755269570c03c56a8',
    'experiments/wpdb-crud-probe/native_check.py': 'ae8116f1d149c3afe0d21969f5cb5b3679d0375568b437c57f2d2df01514de87',
    'experiments/wpdb-crud-probe/owned_database.py': 'bf275a13036b764f080485bd61e2ab2873ce90c14fe047a119bba32bd11b6c86',
    'experiments/wpdb-bridge-probe/fixtures/helpers.php': '1220aa52f8a967f7d4981a1b668e07c7343cdff9c10caf42ea99c32a348fccb1',
    'experiments/wpdb-bridge-probe/owned_database.py': 'c98af5c7916822b203ddecdef2793bfb0ef50ec10649a97b0673aa02c363314c',
    'experiments/wpdb-rpc-probe/native_check.py': 'b275027d59436e7db69ef59f1c235be0f9f429839ef76f9d05106dad129f5df0',
    'experiments/wpdb-rpc-probe/database_check.py': '86e9e6ddc38aa579013f7281ff5295e5a26f3a4907b1eebb70440b0fc9e83900',
    'experiments/wpdb-rpc-probe/wire_check.py': '5293179ba3ee1b42bbe9b79186c08b0aeca2fb4048e04766ff83d32df31f532d',
    'experiments/wpdb-rpc-probe/prepare_driver.py': '2dd38414fefa10147b6d25b54bdb2bbd264c11e0d5e0063ecb2a5c6734b2bc7c',
    'scripts/owned_probe.py': 'b372f65a9fcedc2c3f498d747c5c2886ac662069124315d661edc3a615d20761',
    'scripts/wordpress_reference.py': 'b1ef6a84c6fd704913fa6dc1e87877f951ca808a10d48a5aab1882f25e969303',
    'experiments/wpdb-crud-probe/evidence/windows-mysql-native.json': 'eeae9d70025bc23874efbab9fd55368fa21b898996ffe63c6349ac73cd3c1b00',
    'experiments/wpdb-crud-probe/evidence/windows-mariadb-native.json': '345b1ae77f1cd0dbc622cdb8a292cbca0a699c411fa697bcfae20c289c244e51',
}
VALUES = [('bmp', '\u65e5\u672c\u8a9e\u30fb\u6f22\u5b57'), ('emoji', '\U0001f600\U0001f680\U00020bb7'),
    ('quote', "\u5f15\u7528'\\\u7d4c\u8def%_"), ('composed', '\u00e9'), ('combining', 'e\u0301')]
UPDATED = '\u66f4\u65b0\U0001f600\u00e9'


def require(value, code):
    if not value: raise ValueError(code)


def sha(wire): return hashlib.sha256(wire).hexdigest()
def canonical(value): return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
def identity(wire): return {'bytes': len(wire), 'sha256': sha(wire)}


def read(path, maximum=2*1024*1024):
    require(path.resolve(strict=True) == path and path.is_file() and path.stat().st_size <= maximum, 'fixed_source_file')
    wire = path.read_bytes(); require(len(wire) <= maximum, 'source_file_quota'); return wire


def frozen():
    result = {}
    for name, expected in PINS.items():
        wire = read(ROOT / name); require(sha(wire) == expected, 'frozen_helper_changed'); result[name] = wire
    return result


def once(wire, before, after):
    require(wire.count(before) == 1, 'exact_native_entry_derivation'); return wire.replace(before, after)


def derived_entry(original):
    before = b'experiments/wpdb-crud-probe/fixtures/'
    require(original.count(before) == 3, 'exact_include_prefix_count')
    wire = original.replace(before, b'experiments/wpdb-utf8-probe/fixtures/')
    wire = once(wire, b'recastpress.wpdb-crud-php-observation.v0.5', b'recastpress.wpdb-utf8-php-observation.v0.5')
    wire = once(wire, b"rp_check(in_array($variant, ['native', 'bridge'], true), 'crud_fixed_variant');",
        b"rp_check($variant === 'native', 'utf8_native_only');")
    marker = b"        rp_check(PHP_VERSION_ID === 80512 && PHP_INT_SIZE === 8, 'fixed_native_php_version');\n"
    wire = once(wire, marker, marker + b"        $report['mbstring_loaded'] = extension_loaded('mbstring');\n"
        b"        rp_check($report['mbstring_loaded'], 'utf8_mbstring_profile');\n")
    wire = once(wire, b'fixed_crud_cases_passed', b'fixed_utf8_cases_passed')
    require(wire.count(b'fixed_crud_cases_failed') == 3, 'exact_failure_scope_count')
    return wire.replace(b'fixed_crud_cases_failed', b'fixed_utf8_cases_failed')


def typed(value):
    if isinstance(value, str): return {'bytes_hex': value.encode('utf-8').hex()}
    if isinstance(value, bytes): return {'bytes_hex': value.hex()}
    if value is None or type(value) in (bool, int): return value
    pairs = list(value.items()) if isinstance(value, dict) else list(enumerate(value))
    return {'type': 'array', 'entries': [{'key': typed(k), 'value': typed(v)} for k, v in pairs]}


def quote(value):
    # Exact fixed-corpus literals under the inherited utf8mb4 session without NO_BACKSLASH_ESCAPES.
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def esc_like(value): return ''.join('\\' + c if c in ('_', '%', '\\') else c for c in value)


def expected_cases():
    rows = []
    def add(case, api, arguments, returned, queries):
        rows.append({'case': case, 'api': api, 'arguments': typed(arguments), 'returned': typed(returned),
            'return_type': 'int' if type(returned) is int else 'array' if isinstance(returned, list) else 'string',
            'queries_hex': [q.encode('utf-8').hex() for q in queries]})
    count = 'SELECT COUNT(*) FROM rp_crud_fixture'
    add('fixture_empty', 'get_var', [count], '0', [count])
    for index, (label, value) in enumerate(VALUES, 1):
        sql = 'SELECT ' + quote(value) + ' AS utf8_value'
        add('prepare_' + label, 'prepare/remove_placeholder_escape', ['SELECT %s AS utf8_value', value], sql, [])
        add('esc_like_' + label, 'esc_like', [value], esc_like(value), [])
        add('select_' + label, 'get_var', [sql], value, [sql])
        data = {'label': label, 'note': value, 'payload': value}; formats = ['%s', '%s', '%s']
        insert = 'INSERT INTO `rp_crud_fixture` (`label`, `note`, `payload`) VALUES (' + ', '.join(quote(v) for v in data.values()) + ')'
        queries = (['SHOW FULL COLUMNS FROM `rp_crud_fixture`'] if index == 1 else []) + [insert]
        add('insert_' + label, 'insert', ['rp_crud_fixture', data, formats], 1, queries)
    sql = 'SELECT id,label,note,payload FROM rp_crud_fixture ORDER BY id'
    initial = [[str(i), label, value, value] for i, (label, value) in enumerate(VALUES, 1)]
    add('rows_initial', 'get_results/ARRAY_N', [sql, 'ARRAY_N'], initial, [sql])
    data = {'note': UPDATED, 'payload': UPDATED}; where = {'id': 3}
    update = 'UPDATE `rp_crud_fixture` SET `note` = ' + quote(UPDATED) + ', `payload` = ' + quote(UPDATED) + ' WHERE `id` = 3'
    arguments = ['rp_crud_fixture', data, where, ['%s', '%s'], ['%d']]
    add('update_unicode', 'update', arguments, 1, [update])
    add('update_same', 'update', arguments, 0, [update])
    updated = [row[:] for row in initial]; updated[2][2:] = [UPDATED, UPDATED]
    add('rows_updated', 'get_results/ARRAY_N', [sql, 'ARRAY_N'], updated, [sql])
    for index in range(1, 6):
        add('delete_' + str(index), 'delete', ['rp_crud_fixture', {'id': index}, '%d'], 1,
            ['DELETE FROM `rp_crud_fixture` WHERE `id` = ' + str(index)])
    add('final_empty', 'get_var', [count], '0', [count])
    require(len(rows) == 31 and sum(len(r['queries_hex']) for r in rows) == 22, 'finite_expected_case_inventory')
    return rows


def sources():
    result = {name: sha(wire) for name, wire in frozen().items()}
    for path in sorted(HERE.rglob('*')):
        if path.is_file() and path.suffix in ('.py', '.php') and '__pycache__' not in path.parts:
            wire = read(path); require(b'\r' not in wire, 'new_source_lf')
            result[path.relative_to(ROOT).as_posix()] = sha(wire)
    return result


def prepare(record=True):
    original = frozen(); entry = derived_entry(original['experiments/wpdb-crud-probe/fixtures/entry.php'])
    require(read(HERE / 'fixtures/entry.php') == entry, 'derived_entry_bytes')
    require(read(HERE / 'fixtures/native.php') == original['experiments/wpdb-crud-probe/fixtures/native.php'], 'native_wrapper_bytes')
    require({p.name for p in (HERE / 'fixtures').iterdir()} == {'native.php', 'entry.php', 'corpus.php'}, 'finite_fixture_set')
    require(all(0 < len(value.encode('utf-8')) <= 64 for _, value in VALUES) and VALUES[3][1] != VALUES[4][1], 'valid_utf8_fixed_values')
    before = sources()
    tree = ast.parse(original['experiments/wpdb-crud-probe/owned_database.py'])
    ddl = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'DDL']
    require(len(ddl) == 1 and type(ddl[0]) is bytes, 'frozen_schema_ddl')
    value = {'schema': 'recastpress.wpdb-utf8-prepared.v0.5', 'input_files': before,
        'schema_ddl': identity(ddl[0]), 'values_hex': {label: value.encode('utf-8').hex() for label, value in VALUES},
        'updated_hex': UPDATED.encode('utf-8').hex(), 'expected_cases': expected_cases(),
        'native_extension_profile': ['mysqli', 'mbstring'],
        'derivation': {'entry_original': identity(original['experiments/wpdb-crud-probe/fixtures/entry.php']),
            'entry_derived': identity(entry), 'typed_observers_and_original_wp_loader_unchanged': True,
            'changes': ['owned_include_prefix', 'utf8_schema_and_outcomes', 'native_only_variant', 'require_mbstring']},
        'php_executed': False, 'database_connected': False, 'network_calls_made': False, 'wasm_executed': False,
        'invalid_utf8_exercised': False, 'normalization_applied': False, 'product_runtime_selected': False}
    require(sources() == before, 'source_changed')
    if record:
        require(WORK.resolve() == WORK and WORK.is_relative_to(ROOT / 'target'), 'owned_metadata_path')
        WORK.mkdir(parents=True, exist_ok=True); path = WORK / 'prepared.json'
        require(path.resolve() == path, 'owned_metadata_alias')
        path.write_bytes((json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii'))
    return value


if __name__ == '__main__':
    prepare(); print('Prepared valid UTF-8 wpdb corpus; no PHP or database executed')
