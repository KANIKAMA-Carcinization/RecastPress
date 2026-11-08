"""Derive the fixture-only binlog decoder from accepted experiment sources."""

import argparse
from hashlib import sha256
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ACCEPTED = HERE.parent / 'binlog-probe/src'
OUTPUT = HERE / 'src/binlog_buffer.rs'
PINS = {
    'buffer_adapter.rs': '44970dfd4c7f606393526df081a3cce99aca035c02a327f400b04635d4c393f2',
    'main.rs': '38d58d027dbda599021d75d4fa03b0ccd1b2d3033aa7e88f7ee71dee6923284f',
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def pinned(name):
    path = ACCEPTED / name
    require(path.resolve(strict=True) == path and path.is_file(), 'dependency_binlog_fixed_input')
    wire = path.read_bytes()
    require(sha256(wire).hexdigest() == PINS[name], 'dependency_binlog_source_changed')
    return wire.decode('utf-8')


def replace(source, previous, replacement):
    require(source.count(previous) == 1, 'dependency_binlog_edit_count')
    return source.replace(previous, replacement)


def region(source, start, end):
    require(source.count(start) == source.count(end) == 1, 'dependency_binlog_region_count')
    first = source.index(start)
    last = source.index(end)
    require(first < last, 'dependency_binlog_region_order')
    return source[first:last]


def derived_bytes():
    buffer = pinned('buffer_adapter.rs')
    buffer = replace(buffer, 'use crate::durable_journal::AtomicJournal;\n', '')
    buffer = replace(buffer, region(buffer, '    pub(crate) async fn accept_durable(',
        '    pub(crate) fn finish('), '')
    wire = region(pinned('main.rs'), '#[derive(Debug)]\nenum ChecksumFault',
        'async fn require_fixture_tls(')
    for name in ('verify_crc', 'normalize_maria_initial_rotate', 'check_row'):
        wire = replace(wire, f'fn {name}(', f'pub(crate) fn {name}(')
    wire = replace(wire, 'enum ChecksumFault', 'pub(crate) enum ChecksumFault')
    return (buffer + '\npub(crate) mod wire {\n'
        'use mysql_async::Value;\n'
        'use mysql_async::binlog::events::{BinlogEventFooter, Event, EventData};\n'
        'use mysql_async::binlog::{row::BinlogRow, value::BinlogValue};\n\n'
        + wire + '}\n').encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    wire = derived_bytes()
    require(OUTPUT.resolve() == OUTPUT, 'dependency_binlog_output_alias')
    if OUTPUT.exists():
        require(OUTPUT.is_file() and OUTPUT.read_bytes() == wire, 'dependency_binlog_generated_changed')
    else:
        require(not args.verify_only, 'dependency_binlog_generated_missing')
        with OUTPUT.open('xb') as stream:
            stream.write(wire)
    print(f'src/binlog_buffer.rs {sha256(wire).hexdigest()}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'Dependency binlog derivation failed ({error})', file=sys.stderr)
        sys.exit(1)
