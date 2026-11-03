"""Prepare deterministic CGI framing sources; executes no PHP, Wasm, DB or network."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/php-cgi-framing-probe'
SCHEMA = 'recastpress.php-cgi-framing-php-observation.v0.5'


def require(value, code):
    if not value: raise ValueError(code)


def sha(wire): return hashlib.sha256(wire).hexdigest()
def identity(wire): return {'bytes': len(wire), 'sha256': sha(wire)}
def encoded(value): return (json.dumps(value, separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def cases():
    for label, size in [('small', 64), ('large', 70000)]:
        for buffered in [False, True]:
            for remove in [False, True]:
                name = label + '-' + ('buffered' if buffered else 'plain') + '-' + ('remove' if remove else 'keep')
                yield {'case': name, 'script': 'case-' + name + '.php', 'payload_bytes': size,
                    'buffered': buffered, 'header_remove': remove}


def wrapper(case):
    return ("<?php\ndeclare(strict_types=1);\ndefine('RP_FRAMING_CASE', '" + case['case']
        + "');\ndefine('RP_FRAMING_BYTES', " + str(case['payload_bytes'])
        + ");\ndefine('RP_FRAMING_BUFFERED', " + str(case['buffered']).lower()
        + ");\ndefine('RP_FRAMING_REMOVE', " + str(case['header_remove']).lower()
        + ");\nrequire __DIR__ . '/common.php';\n").encode('ascii')


def expected(case):
    payload = b'0123456789abcdef' * (case['payload_bytes'] // 16)
    value = {'schema': SCHEMA, 'passed': True, 'case': case['case'], 'buffered': case['buffered'],
        'header_remove': case['header_remove'], 'payload_bytes': len(payload),
        'payload_hex': payload.hex(), 'payload_sha256': sha(payload)}
    headers = ([] if case['header_remove'] else [b'X-Powered-By: PHP/8.5.12']) + [b'Content-Type: application/json']
    body = encoded(value)
    return value, headers, body


def read(path):
    require(path.resolve(strict=True) == path and path.is_file() and 0 < path.stat().st_size <= 1024*1024, 'source_file')
    wire = path.read_bytes(); require(len(wire) <= 1024*1024 and b'\r' not in wire, 'source_bytes_or_lf'); return wire


def sources():
    names = ['common.php'] + [case['script'] for case in cases()]
    require({p.name for p in (HERE / 'fixtures').iterdir()} == set(names), 'finite_fixture_inventory')
    result = {}
    for name in names:
        path = HERE / 'fixtures' / name; wire = read(path)
        if name != 'common.php':
            case = next(case for case in cases() if case['script'] == name)
            require(wire == wrapper(case), 'fixed_wrapper_bytes')
        result[path.relative_to(ROOT).as_posix()] = identity(wire)
    for name in ['prepare.py', 'check.py']:
        path = HERE / name; result[path.relative_to(ROOT).as_posix()] = identity(read(path))
    return result


def prepare(record=True):
    before = sources(); rows = []
    for case in cases():
        value, headers, body = expected(case)
        rows.append({**case, 'expected_body': identity(body), 'expected_status': 200,
            'expected_header_lines_hex': [line.hex() for line in headers],
            'expected_cgi_wire': identity(b'\r\n'.join(headers) + b'\r\n\r\n' + body)})
    result = {'schema': 'recastpress.php-cgi-framing-prepared.v0.5', 'source_files': before, 'cases': rows,
        'cgi_context': {'query': 'rp_request=1', 'request_uri': '/__rp_framing.php?rp_request=1',
            'method': 'GET', 'content_length': 0, 'argc': 0},
        'php_executed': False, 'wasm_executed': False, 'database_connected': False,
        'network_calls_made': False, 'product_runtime_selected': False}
    require(before == sources(), 'source_changed')
    if record:
        require(WORK.resolve() == WORK and WORK.is_relative_to(ROOT / 'target'), 'owned_prepared_path')
        WORK.mkdir(parents=True, exist_ok=True)
        path = WORK / 'prepared.json'; require(path.resolve() == path, 'prepared_alias')
        path.write_bytes((json.dumps(result, indent=2, ensure_ascii=True) + '\n').encode('ascii'))
    return result


if __name__ == '__main__':
    result = prepare(); print('Prepared eight framing cases; no PHP or Wasm executed')
