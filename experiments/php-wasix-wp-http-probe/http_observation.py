"""Bounded fixed-loopback HTTP experiment observer; no product HTTP-parser claim.

The controller obtains exact synthetic native responses from native_references().
Only successful complete comparisons expose normalized *known* origin fields.
Unknown response bytes never enter an exception message or public observation.
request() performs network IO only when explicitly called by the controller;
imports, native reference checks, parsers and self_test() perform no network IO.
"""
import email.utils
import hashlib
import json
from pathlib import Path
import re
import socket
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HEADER_LIMIT = 16384
BODY_LIMIT = 1048576
TOTAL_LIMIT = HEADER_LIMIT + BODY_LIMIT
FIELD_LIMIT = 64
CGI_SOURCE = 'experiments/php-wasi-host-probe/src/cgi_response.rs'
CGI_SOURCE_SHA256 = 'd7ef796e0bea093c862e67b3591d97ecf4a8ce53428e17196a22a7d06551bc49'
ORACLES = {
    'mysql': {'path': 'experiments/wp-request-context-probe/evidence/windows-mysql-native.json',
        'bytes': 795353, 'sha256': '7c2a0b01fdd5e90a120b8e278368245c18618936a268baefb09ff1adba01e00b'},
    'mariadb': {'path': 'experiments/wp-request-context-probe/evidence/windows-mariadb-native.json',
        'bytes': 792967, 'sha256': 'a2d54133bc368f9945b5f178aafcc5c1edf60778c90247491f1ad60aeb0fe1df'},
}
TOKEN = re.compile(rb"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
TRANSPORT = {'content-length', 'date', 'connection'}
FORBIDDEN_ORIGIN = TRANSPORT | {'status', 'transfer-encoding', 'trailer', 'te',
    'keep-alive', 'proxy-connection', 'upgrade'}


def require(value, code):
    if not value: raise ValueError(code)


def sha(wire): return hashlib.sha256(wire).hexdigest()


def _fixed_request(method=b'GET', target=b'/', host=b'rp-fixture.invalid', extra=b'', body=b''):
    return method + b' ' + target + b' HTTP/1.1\r\nHost: ' + host + b'\r\nConnection: close\r\n' + extra + b'\r\n' + body


REQUESTS = {
    'home': _fixed_request(),
    'missing': _fixed_request(target=b'/?p=2147483647'),
    'wrong_host': _fixed_request(host=b'rp-other.invalid'),
    'post': _fixed_request(method=b'POST', extra=b'Content-Length: 0\r\n'),
    'cookie': _fixed_request(extra=b'Cookie: rp_probe=1\r\n'),
    'authorization': _fixed_request(extra=b'Authorization: Bearer rp-public-rejected-fixture\r\n'),
    'nonzero_length': _fixed_request(extra=b'Content-Length: 1\r\n', body=b'x'),
    'unknown_query': _fixed_request(target=b'/?rp_unknown=1'),
    'unknown_header': _fixed_request(extra=b'X-RP-Private-Probe: rp-public-rejected-fixture\r\n'),
}
REJECTIONS = {name: {'status': 400, 'body': b'fixed request rejected\n'}
    for name in ('wrong_host', 'post', 'cookie', 'authorization', 'nonzero_length', 'unknown_query', 'unknown_header')}


def _json(wire):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_native_json_key'); result[key] = value
        return result
    def bad_number(_): raise ValueError('nonfinite_native_json')
    return json.loads(wire.decode('utf-8'), object_pairs_hook=pairs, parse_constant=bad_number)


def _source(path, maximum):
    absolute = ROOT / path
    require(absolute.resolve(strict=True) == absolute and absolute.is_file()
        and 0 < absolute.stat().st_size <= maximum, 'fixed_http_source_path')
    wire = absolute.read_bytes(); require(0 < len(wire) <= maximum, 'fixed_http_source_size'); return wire


def input_hashes():
    result = {CGI_SOURCE: sha(_source(CGI_SOURCE, 128*1024))}
    require(result[CGI_SOURCE] == CGI_SOURCE_SHA256, 'fixed_cgi_parser_source_changed')
    for pin in ORACLES.values():
        wire = _source(pin['path'], 2*1024*1024)
        require(len(wire) == pin['bytes'] and sha(wire) == pin['sha256'], 'fixed_http_native_oracle_changed')
        result[pin['path']] = sha(wire)
    result[Path(__file__).resolve().relative_to(ROOT).as_posix()] = sha(Path(__file__).read_bytes())
    return dict(sorted(result.items()))


def _field(line):
    require(line and line[:1] not in (b' ', b'\t'), 'http_folded_header')
    name, separator, value = line.partition(b':')
    require(separator and TOKEN.fullmatch(name) is not None, 'http_header_name')
    value = value.strip(b' \t')
    require(all(c == 9 or (c >= 32 and c != 127) for c in value), 'http_header_control')
    return name.decode('ascii').lower(), value


def _native(response):
    require(type(response) is dict and set(response) == {'status', 'headers_hex', 'body_bytes', 'body_hex', 'body_sha256'},
        'native_http_response_shape')
    require(type(response['status']) is int and 200 <= response['status'] <= 599, 'native_http_status')
    require(type(response['body_hex']) is str and len(response['body_hex']) <= BODY_LIMIT*2
        and re.fullmatch('[0-9a-f]*', response['body_hex']) is not None and len(response['body_hex']) % 2 == 0,
        'native_http_body_hex')
    body = bytes.fromhex(response['body_hex'])
    require(type(response['body_bytes']) is int and len(body) == response['body_bytes']
        and sha(body) == response['body_sha256'], 'native_http_body_identity')
    require(type(response['headers_hex']) is list and len(response['headers_hex']) <= FIELD_LIMIT, 'native_http_header_count')
    fields, total = {}, 0
    for value in response['headers_hex']:
        require(type(value) is str and len(value) % 2 == 0 and re.fullmatch('[0-9a-f]+', value) is not None,
            'native_http_header_hex')
        total += len(value)//2 + 2; require(total <= HEADER_LIMIT, 'native_http_header_limit')
        name, raw = _field(bytes.fromhex(value))
        require(name not in FORBIDDEN_ORIGIN, 'native_http_transport_header')
        fields.setdefault(name, []).append(raw)
    return body, fields


def native_references():
    before = input_hashes(); result = {}
    for backend, pin in ORACLES.items():
        value = _json(_source(pin['path'], 2*1024*1024))
        require(value['schema'] == 'recastpress.wp-request-context-native-check.v0.5'
            and value['backend'] == backend, 'native_http_oracle_schema')
        for key in ('passed', 'source_reverified_after', 'normal_admin_shutdown_verified',
            'all_command_children_reaped', 'all_owned_cgi_reaped', 'owned_database_cleanup_completed',
            'fresh_repeat_404_response_exact', 'original_install_read_baseline_reproduced'):
            require(value[key] is True, 'native_http_oracle_completion')
        result[backend] = {}
        for case, status in (('home', 200), ('missing', 404)):
            response = value['observations'][case]['response']; _native(response)
            require(response['status'] == status and response == value['requests'][case]['observation']['response'],
                'native_http_case_response')
            result[backend][case] = response
        require(result[backend]['missing'] == value['requests']['missing_repeat']['observation']['response'],
            'native_http_repeat_response')
    require(result['mysql'] == result['mariadb'], 'native_http_cross_backend_responses')
    require(input_hashes() == before, 'http_observer_sources_changed')
    return result


def _headers(wire):
    require(type(wire) is bytes and len(wire) <= TOTAL_LIMIT, 'http_wire_limit')
    boundary = wire.find(b'\r\n\r\n')
    require(boundary >= 0 and boundary + 4 <= HEADER_LIMIT, 'http_header_terminator_limit')
    lines = wire[:boundary].split(b'\r\n')
    require(lines and re.fullmatch(rb'HTTP/1\.1 ([2-5][0-9]{2}) [\x20-\x7e]*', lines[0]) is not None,
        'http_status_line')
    require(len(lines)-1 <= FIELD_LIMIT, 'http_header_field_limit')
    fields = {}
    for line in lines[1:]:
        name, value = _field(line); fields.setdefault(name, []).append(value)
    require('transfer-encoding' not in fields and 'trailer' not in fields, 'http_transfer_or_trailer')
    lengths = fields.get('content-length', [])
    require(len(lengths) == 1 and re.fullmatch(rb'0|[1-9][0-9]{0,6}', lengths[0]) is not None, 'http_single_canonical_length')
    length = int(lengths[0]); require(length <= BODY_LIMIT, 'http_body_limit')
    return int(lines[0][9:12]), fields, boundary + 4, length


def _parse(wire):
    status, fields, offset, length = _headers(wire)
    body = wire[offset:]
    require(len(body) == length, 'http_complete_body_length')
    transport = {'content_length': length, 'date_gmt': None, 'connection_close': False}
    for name in ('date', 'connection'):
        require(len(fields.get(name, [])) <= 1, 'http_duplicate_transport_header')
    if 'date' in fields:
        try:
            text = fields['date'][0].decode('ascii'); parsed = email.utils.parsedate_to_datetime(text)
            require(parsed.utcoffset().total_seconds() == 0 and email.utils.format_datetime(parsed, usegmt=True) == text,
                'http_canonical_gmt_date')
        except (ValueError, TypeError, OverflowError, AttributeError, UnicodeError):
            raise ValueError('http_canonical_gmt_date') from None
        transport['date_gmt'] = text
    if 'connection' in fields:
        require(fields['connection'] == [b'close'], 'http_connection_close')
        transport['connection_close'] = True
    origin = {name: values for name, values in fields.items() if name not in TRANSPORT}
    return status, origin, transport, body


def observe_wire(wire, native_response):
    """Pure comparison. Caller supplies a response from native_references().

    wire must be the complete EOF result returned privately by request().
    Date is checked as canonical IMF-fixdate/GMT, not against an ambient clock.
    The live controller can compare date_gmt against its own request time window.
    """
    expected_body, expected_fields = _native(native_response)
    status, origin, transport, body = _parse(wire)
    require(status == native_response['status'], 'http_native_status_differs')
    require(origin == expected_fields, 'http_native_origin_headers_differ')
    require(body == expected_body, 'http_native_body_differs')
    return {'status': status, 'origin_header_fields': {name: [value.hex() for value in values]
        for name, values in sorted(expected_fields.items())}, 'transport_headers': transport,
        'body_bytes': len(body), 'body_sha256': sha(body), 'wire_bytes': len(wire), 'wire_sha256': sha(wire),
        'eof': True, 'body_matches_native': True, 'origin_headers_match_native': True}


def observe_rejection(wire, label):
    require(label in REJECTIONS, 'closed_http_rejection_case')
    status, origin, transport, body = _parse(wire)
    require(status == REJECTIONS[label]['status'] and body == REJECTIONS[label]['body'], 'http_fixed_rejection_differs')
    require(not origin, 'http_unknown_rejection_header')
    return {'status': status, 'header_count': len(_headers(wire)[1]), 'transport_headers': transport,
        'body_bytes': len(body), 'body_sha256': sha(body), 'wire_bytes': len(wire), 'wire_sha256': sha(wire),
        'eof': True, 'fixed_rejection_matches': True}


def request(port, wire, timeout=310):
    """Return private wire bytes only after bounded complete EOF, fixed loopback.

    No generic request builder, redirects, DNS, TLS or remote-host support.
    Total monotonic deadline covers connect, send and every receive.
    """
    require(type(port) is int and 1 <= port <= 65535, 'http_loopback_port')
    require(type(wire) is bytes and wire in REQUESTS.values(), 'fixed_http_request_wire')
    require(type(timeout) in (int, float) and 0 < timeout <= 310, 'http_request_deadline')
    deadline = time.monotonic() + timeout
    def remaining():
        left = deadline - time.monotonic(); require(left > 0, 'http_request_timeout'); return left
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        try:
            connection.settimeout(remaining()); connection.connect(('127.0.0.1', port))
            connection.settimeout(remaining()); connection.sendall(wire)
            result = bytearray(); framing = None
            while True:
                connection.settimeout(remaining())
                chunk = connection.recv(min(16384, TOTAL_LIMIT + 1 - len(result)))
                if not chunk: break
                result.extend(chunk); require(len(result) <= TOTAL_LIMIT, 'http_live_wire_limit')
                if framing is None:
                    boundary = result.find(b'\r\n\r\n')
                    require(boundary >= 0 or len(result) < HEADER_LIMIT, 'http_live_header_limit')
                    if boundary >= 0: framing = _headers(bytes(result))
                if framing is not None:
                    require(len(result) <= framing[2] + framing[3], 'http_live_extra_body_or_trailer')
            wire = bytes(result); _parse(wire); return wire
        except (OSError, TimeoutError):
            raise ValueError('http_socket_io_failed') from None


def self_test():
    """Pure deterministic offline checks; never create a socket or run PHP."""
    tests = []
    def check(label, function, rejected=False):
        try: function()
        except ValueError as error:
            require(rejected and re.fullmatch('[a-z][a-z0-9_]*', str(error)) is not None, 'http_self_test_unexpected_error')
        else:
            require(not rejected, 'http_self_test_mutant_accepted')
        tests.append(label)
    def wire(status, headers, body):
        return b'HTTP/1.1 ' + str(status).encode() + b' Fixed\r\n' + b'\r\n'.join(headers) + b'\r\n\r\n' + body
    date = b'Date: Wed, 04 Nov 2026 12:34:56 GMT'
    binary = b'\x00\xff\r\nprivate-looking-but-fixed-test-body\x80'
    origin = [b'Content-Type: application/octet-stream', b'Set-Cookie: first=1', b'X-Fixed: one', b'Set-Cookie: second=2']
    native = {'status': 200, 'headers_hex': [h.hex() for h in origin], 'body_hex': binary.hex(),
        'body_bytes': len(binary), 'body_sha256': sha(binary)}
    headers = [*origin, b'Content-Length: ' + str(len(binary)).encode(), date, b'Connection: close']
    valid = wire(200, headers, binary)
    check('binary_and_repeated_set_cookie', lambda: observe_wire(valid, native))
    reordered = [b'x-fixed:\t one \t', b'set-cookie: first=1', b'CONTENT-TYPE: application/octet-stream',
        b'SET-cookie: second=2', *headers[-3:]]
    check('cross_name_order_case_and_cgi_ows', lambda: observe_wire(wire(200, reordered, binary), native))
    reverse_cookies = [origin[0], origin[3], origin[2], origin[1], *headers[-3:]]
    check('same_name_order_must_remain', lambda: observe_wire(wire(200, reverse_cookies, binary), native), True)
    check('optional_date_connection_absent', lambda: observe_wire(wire(200, headers[:-2], binary), native))
    mutations = {
        'duplicate_content_length': wire(200, [*headers, headers[-3]], binary),
        'comma_content_length': wire(200, [*origin, b'Content-Length: 1, 1'], binary),
        'leading_zero_length': wire(200, [*origin, b'Content-Length: 0' + str(len(binary)).encode()], binary),
        'missing_length': wire(200, origin, binary),
        'transfer_encoding': wire(200, [*headers, b'Transfer-Encoding: chunked'], binary),
        'trailer_field': wire(200, [*headers, b'Trailer: X-Fixed'], binary),
        'folded_field': wire(200, [*headers, b' folded: value'], binary),
        'name_whitespace': wire(200, [*headers, b'X-Fixed : value'], binary),
        'value_control': wire(200, [*headers, b'X-Fixed: private\x00value'], binary),
        'unknown_header': wire(200, [*headers, b'X-Unknown: private-unknown'], binary),
        'unknown_body': wire(200, headers, binary[:-1] + b'x'),
        'wrong_status': wire(404, headers, binary),
        'truncated_body': valid[:-1],
        'extra_body_or_pipeline': valid + b'HTTP/1.1 200 OK\r\n\r\n',
        'bare_lf': valid.replace(b'\r\n', b'\n'),
        'http_10': valid.replace(b'HTTP/1.1', b'HTTP/1.0', 1),
        'oversized_declared_body': wire(200, [b'Content-Length: 1048577'], b''),
        'oversized_header': wire(200, [b'X-Fixed: ' + b'a'*HEADER_LIMIT, b'Content-Length: 0'], b''),
        'too_many_fields': wire(200, [b'X-Fixed: a']*FIELD_LIMIT + [b'Content-Length: 0'], b''),
        'duplicate_date': wire(200, [*headers, date], binary),
        'obsolete_date': wire(200, [*headers[:-2], b'Date: Wednesday, 04-Nov-26 12:34:56 GMT'], binary),
        'incorrect_date_weekday': wire(200, [*headers[:-2], b'Date: Tue, 04 Nov 2026 12:34:56 GMT'], binary),
        'non_gmt_date': wire(200, [*headers[:-2], b'Date: Wed, 04 Nov 2026 12:34:56 +0000'], binary),
        'duplicate_connection': wire(200, [*headers, b'Connection: close'], binary),
        'non_close_connection': wire(200, [*headers[:-1], b'Connection: keep-alive'], binary),
    }
    for label, value in mutations.items():
        check(label, lambda value=value: observe_wire(value, native), True)
    references = native_references()
    for backend, cases in references.items():
        for case, response in cases.items():
            body = bytes.fromhex(response['body_hex'])
            fields = [bytes.fromhex(h) for h in response['headers_hex']]
            value = wire(response['status'], [*fields, b'Content-Length: '+str(len(body)).encode(), date, b'Connection: close'], body)
            check('pinned_native_' + backend + '_' + case, lambda value=value, response=response: observe_wire(value, response))
    for label, expected in REJECTIONS.items():
        body = expected['body']; value = wire(expected['status'], [b'Content-Length: '+str(len(body)).encode(), date, b'Connection: close'], body)
        check('fixed_rejection_' + label, lambda value=value, label=label: observe_rejection(value, label))
        bad = wire(expected['status'], [b'Content-Length: '+str(len(body)).encode(), b'X-Private: unknown'], body)
        check('rejection_unknown_header_' + label, lambda bad=bad, label=label: observe_rejection(bad, label), True)
    require(len(REQUESTS) == 9 and len(REJECTIONS) == 7 and all(len(w) < 1024 for w in REQUESTS.values()), 'finite_http_request_inventory')
    return {'passed': True, 'tests': tests, 'test_count': len(tests), 'network_executed': False,
        'php_wasm_database_executed': False, 'input_files': input_hashes()}
