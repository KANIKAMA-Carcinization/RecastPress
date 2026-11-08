"""Adapt the pinned dependency monitor to one row in the WordPress options table."""

import argparse
from hashlib import sha256
from pathlib import Path
import runpy
import sys


HERE = Path(__file__).resolve().parent
ACCEPTED = HERE.parent / 'php-wasix-binlog-dependency-probe'
MONITOR_SHA = '128397b66a8a42add2ed16441e0e24afd1e6ed3f158c56577d1fee3a89f89a2d'
DERIVER_SHA = 'da0a682b2d09960959083a4fee83cafbc02ced6eb77b1e639d78d4234184b7f1'


def require(condition, code):
    if not condition:
        raise ValueError(code)


def pinned(path, expected):
    require(path.resolve(strict=True) == path and path.is_file(), 'autoload_binlog_fixed_input')
    wire = path.read_bytes()
    require(sha256(wire).hexdigest() == expected, 'autoload_binlog_source_changed')
    return wire


def replace(source, previous, replacement):
    require(source.count(previous) == 1, 'autoload_binlog_edit_count')
    return source.replace(previous, replacement, 1)


def monitor_bytes():
    source = pinned(ACCEPTED / 'src/monitor.rs', MONITOR_SHA).decode('utf-8')
    start = 'async fn dependency(connection:'
    end = 'impl Monitor {'
    require(source.count(start) == source.count(end) == 1 and source.index(start) < source.index(end),
        'autoload_binlog_dependency_boundary')
    source = replace(source, source[source.index(start):source.index(end)], '')
    for previous, replacement in (
        ('use anyhow::{Context, Result, anyhow, ensure};',
         'use super::{autoload_id, check_row};\nuse anyhow::{Context, Result, anyhow, ensure};'),
        ('const SCHEMA: &str = "rp_dependency_probe";', 'const SCHEMA: &str = "rp_wpdb_probe";'),
        ('const TABLE: &[u8] = b"rp_dependency_fixture";', 'const TABLE: &[u8] = b"rp_options";'),
        ('b"recastpress-shared-fixture"', 'b"rp-autoload-old"'),
        ('b"recastpress-shared-updated"', 'b"rp-autoload-new"'),
        ('FROM rp_dependency_probe.rp_dependency_identity WHERE id=1',
         'FROM `rp_options_guard_fixture`.`identity` WHERE id=1'),
        ('    checkpoint: Coordinate,', '    checkpoint: Coordinate,\n    option_id: u64,'),
        ('let (control, identity, checkpoint) =', 'let (control, identity, checkpoint, option_id) ='),
        ('                dependency(&mut connection, OLD).await?;\n'
         '                Ok::<_, anyhow::Error>((connection, identity, position))',
         '                let option_id = autoload_id(&mut connection, OLD).await?;\n'
         '                Ok::<_, anyhow::Error>((connection, identity, position, option_id))'),
        ('            checkpoint,\n            replayed:', '            checkpoint,\n            option_id,\n            replayed:'),
        ('                    "dependency_monitor_position_changed"\n                );',
         '''                    "dependency_monitor_position_changed"
                );
                let expected = if self.replayed { NEW } else { OLD };
                ensure!(autoload_id(&mut self.control, expected).await? == self.option_id,
                    "dependency_monitor_option_id_changed");'''),
        ('                    start,\n                    &end,',
         '                    start,\n                    &end,\n                    self.option_id,'),
        ('                dependency(&mut self.control, NEW).await?;',
         '                ensure!(autoload_id(&mut self.control, NEW).await? == self.option_id,\n'
         '                    "dependency_monitor_option_id_changed");'),
        ('fn check_update(stream: &BinlogStream, event: &RowsEventData<\'_>) -> Result<()> {',
         'fn check_update(stream: &BinlogStream, event: &RowsEventData<\'_>, option_id: u64) -> Result<()> {'),
        ('            && table.columns_count() == 2', '            && table.columns_count() == 4\n'
         '            && table.null_bitmask().iter().map(|bit| *bit).eq([false; 4])'),
        ('''                column(1)?,
                Some(ColumnType::MYSQL_TYPE_VARCHAR | ColumnType::MYSQL_TYPE_VAR_STRING)
            ),''', '''                column(1)?,
                Some(ColumnType::MYSQL_TYPE_VARCHAR | ColumnType::MYSQL_TYPE_VAR_STRING)
            )
            && column(2)? == Some(ColumnType::MYSQL_TYPE_BLOB)
            && table.get_column_metadata(2) == Some(&[4])
            && matches!(
                column(3)?,
                Some(ColumnType::MYSQL_TYPE_VARCHAR | ColumnType::MYSQL_TYPE_VAR_STRING)
            ),'''),
        ('wire::check_row(&before, OLD)', 'check_row(&before, option_id, OLD)'),
        ('wire::check_row(&after, NEW)', 'check_row(&after, option_id, NEW)'),
        ('    end: &Coordinate,\n)', '    end: &Coordinate,\n    option_id: u64,\n)'),
        ('                check_update(stream, event)?;', '                check_update(stream, event, option_id)?;'),
    ):
        source = replace(source, previous, replacement)
    return source.encode('utf-8')


def outputs():
    deriver = ACCEPTED / 'derive_binlog.py'
    pinned(deriver, DERIVER_SHA)
    return {'src/binlog_buffer.rs': runpy.run_path(str(deriver))['derived_bytes'](),
        'src/monitor_impl.rs': monitor_bytes()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    for name, wire in outputs().items():
        path = HERE / name
        require(path.resolve() == path, 'autoload_binlog_output_alias')
        if path.exists():
            require(path.is_file() and path.read_bytes() == wire, 'autoload_binlog_generated_changed')
        else:
            require(not args.verify_only, 'autoload_binlog_generated_missing')
            with path.open('xb') as stream:
                stream.write(wire)
        print(f'{name} {sha256(wire).hexdigest()}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'Autoload binlog derivation failed ({error})', file=sys.stderr)
        sys.exit(1)
