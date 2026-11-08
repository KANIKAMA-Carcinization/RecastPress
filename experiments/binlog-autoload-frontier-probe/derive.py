"""Derive a bounded live reader from the accepted autoload binlog scanner."""

from hashlib import sha256
from pathlib import Path
import runpy


HERE = Path(__file__).resolve().parent
ACCEPTED = HERE.parent / 'php-wasix-autoload-binlog-probe'
PINS = {
    'derive_binlog.py': '28aa22854dea30e8dd8d649461bdf8713cb8cfab81be807f8349a8a37454d125',
    'src/monitor.rs': 'ba5b71bd920dc905fa2bdc43989b8673f0dc16bb75eee34500872f77c56ba2b7',
}


def pinned(name):
    path = ACCEPTED / name
    wire = path.read_bytes()
    if path.resolve() != path or sha256(wire).hexdigest() != PINS[name]:
        raise ValueError('frontier_pinned_source')
    return wire


def replace(source, marker, replacement):
    if source.count(marker) != 1:
        raise ValueError('frontier_derivation_marker')
    return source.replace(marker, replacement)


def derive():
    pinned('derive_binlog.py')
    files = runpy.run_path(str(ACCEPTED / 'derive_binlog.py'))['outputs']()
    monitor = files['src/monitor_impl.rs']
    marker = b'async fn scan('
    if monitor.count(marker) != 1:
        raise ValueError('frontier_scan_source')
    scan = monitor[monitor.index(marker):]
    scan = replace(scan, marker, b'async fn scan_live(')
    scan = replace(scan, b'    option_id: u64,', b'    option_id: u64,\n    mut preamble: VecDeque<Event>,')
    scan = replace(scan, b'    while let Some(event) = stream.next().await {', b'''    loop {
        let event = match preamble.pop_front() {
            Some(event) => Ok(event),
            None => stream.next().await.context("frontier_live_dump_ended")?,
        };''')
    scan = replace(scan, b'        events += 1;', b'        events += 1;\n        if decoder.applied == 1 { break; }')
    monitor = replace(monitor, b'#[derive(Eq, PartialEq)]\nstruct Coordinate',
        b'#[derive(Clone, Eq, PartialEq)]\nstruct Coordinate')
    files['src/monitor_impl.rs'] = monitor + b'\ninclude!("live.rs");\n' + scan
    files['src/monitor.rs'] = pinned('src/monitor.rs')
    for name, wire in files.items():
        path = HERE / name
        if path.resolve() != path:
            raise ValueError('frontier_generated_path')
        if not path.exists() or path.read_bytes() != wire:
            path.write_bytes(wire)


if __name__ == '__main__':
    derive()
