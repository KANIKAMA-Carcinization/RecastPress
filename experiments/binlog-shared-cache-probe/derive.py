#!/usr/bin/env python3
"""Derive a separate binary without invalidating the existing binlog evidence."""

import subprocess
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = '0c106f3feafd82257d31830d23f42fb7f032a318'
PREFIX = 'experiments/binlog-probe'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def replace_once(wire, marker, replacement):
    if wire.count(marker) != 1:
        raise ValueError('ambiguous pinned Rust insertion')
    return wire.replace(marker, replacement, 1)


def derive_main(wire):
    newline = b'\r\n' if b'\r\n' in wire else b'\n'
    wire = wire.replace(newline, b'\n')
    for before, after in (
        (b'mod row_types_probe;\n',
         b'mod row_types_probe;\nmod shared_cache_probe;\nmod shared_object_cache;\n'),
        (b'        _ => "fixture validation",',
         b'        20 => "shared cache commit boundary",\n        _ => "fixture validation",'),
        (b'    MysqlRotationHandoff,\n', b'    MysqlRotationHandoff,\n    SharedCache,\n'),
        (b'        _ => Err("unknown or extra probe arguments".into()),',
         b'        [flag] if flag == "--shared-cache-probe" => Ok(Mode::SharedCache),\n'
         b'        _ => Err("unknown or extra probe arguments".into()),'),
        (b'    if mode == Mode::MysqlRotationHandoff {', b'''    if mode == Mode::SharedCache {
        stage(20);
        let result = shared_cache_probe::run(
            &mut control,
            options,
            &epoch,
            status,
            version.contains("MariaDB"),
        )
        .await?;
        close_fixture_connection(control, "control_final").await?;
        println!("SHARED_CACHE_RESULT={result}");
        return Ok(());
    }
    if mode == Mode::MysqlRotationHandoff {'''),
    ):
        wire = replace_once(wire, before, after)
    return wire.replace(b'\n', newline)


def stage(relative, wire):
    output = HERE / relative
    if output.is_symlink() or output.parent.is_symlink():
        raise ValueError('refusing linked generated Rust source')
    output.parent.mkdir(exist_ok=True)
    if not output.exists() or output.read_bytes() != wire:
        output.write_bytes(wire)


def derive():
    names = git('ls-tree', '-r', '--name-only', BASE, '--',
                f'{PREFIX}/Cargo.toml', f'{PREFIX}/Cargo.lock', f'{PREFIX}/src').decode('ascii').splitlines()
    if len(names) != 18:
        raise ValueError('pinned binlog source closure changed')
    for name in names:
        relative = PurePosixPath(name).relative_to(PREFIX)
        wire = git('show', f'{BASE}:{name}')
        stage(relative, derive_main(wire) if relative.as_posix() == 'src/main.rs' else wire)
    for name in ('shared_cache_probe.rs', 'shared_object_cache.rs'):
        stage(PurePosixPath('src', name), (HERE / name).read_bytes())


if __name__ == '__main__':
    derive()
