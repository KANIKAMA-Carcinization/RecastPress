"""Derive the isolated autoload position-mismatch experiment."""

import argparse
from hashlib import sha256
from pathlib import Path
import runpy


HERE = Path(__file__).resolve().parent
BASE = HERE.parent / 'php-wasix-autoload-binlog-probe'
OLD = b'recastpress-php-wasix-autoload-binlog-probe'
NAME = b'recastpress-php-wasix-autoload-bypass-probe'
PINS = {
    'derive.py': 'e5e88b920e01bab384b2e84b03286bbd9164d48c939c1c25977755b936a7bf5a',
    'derive_binlog.py': '28aa22854dea30e8dd8d649461bdf8713cb8cfab81be807f8349a8a37454d125',
    'src/monitor.rs': 'ba5b71bd920dc905fa2bdc43989b8673f0dc16bb75eee34500872f77c56ba2b7',
    'Cargo.toml': '2ed205508ab0a909855ffc96fa06d4a78d3b81193b64722f062ff8156c46f266',
    'Cargo.lock': 'ccb20cfe7c7bc3b6fe2f41657c4fa3d3a8cfedbae39de7473b74e61edb1c17ad',
    'run_check.py': '510ba4abecd105449d75b03105f708174977d37b4e4ad3523ea7d69e3c61dcbb',
    'database.py': '10168b4cafd82c1f6611f407d5992de660a7d210f84dc4a6397edbd11c6690bc',
}

POSITION_TYPE = '''#[derive(Debug)]
pub(crate) struct PositionMismatch {
    before: u64,
    observed: Coordinate,
}

impl std::fmt::Display for PositionMismatch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("autoload_bypass_position_mismatch")
    }
}

impl std::error::Error for PositionMismatch {}

impl PositionMismatch {
    pub(crate) fn observation(&self) -> Value {
        json!({"from_position":self.before,"observed_position":self.observed.position})
    }
}

'''
POSITION_CHECK = '''                let observed = coordinate(&mut self.control, &self.source).await?;
                if observed.file == self.checkpoint.file
                    && observed.position > self.checkpoint.position
                    && observed.position - self.checkpoint.position <= MAX_SPAN
                {
                    return Err(PositionMismatch { before: self.checkpoint.position, observed }.into());
                }
                ensure!(observed == self.checkpoint, "dependency_monitor_position_changed");'''
POST_BYPASS = '''    pub(crate) fn verify_bypass(&mut self, mismatch: &PositionMismatch) -> Result<()> {
        self.runtime.block_on(async {
            timeout(DEADLINE, async {
                ensure!(source(&mut self.control, &self.nonce).await? == self.source,
                    "autoload_bypass_source_changed");
                ensure!(coordinate(&mut self.control, &self.source).await? == mismatch.observed,
                    "autoload_bypass_position_changed_after_read");
                ensure!(autoload_id(&mut self.control, NEW).await? == self.option_id,
                    "autoload_bypass_option_changed_after_read");
                Ok(())
            }).await.context("autoload_bypass_post_fence_timeout")?
        })
    }

'''


def pinned(name):
    path = BASE / name
    wire = path.read_bytes()
    if path.resolve(strict=True) != path or sha256(wire).hexdigest() != PINS[name]:
        raise ValueError('autoload_bypass_source_changed')
    return wire


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('autoload_bypass_derivation_marker')
    return source.replace(old, new, 1)


def outputs():
    pinned('derive.py')
    pinned('derive_binlog.py')
    files = runpy.run_path(str(BASE / 'derive.py'))['outputs']()
    files.update(runpy.run_path(str(BASE / 'derive_binlog.py'))['outputs']())
    files['src/monitor.rs'] = once(pinned('src/monitor.rs'), b'#[path = "monitor_impl.rs"]\nmod implementation;\npub(crate) use implementation::Monitor;',
        b'#[allow(dead_code)]\n#[path = "monitor_impl.rs"]\nmod implementation;\npub(crate) use implementation::{Monitor, PositionMismatch};')
    monitor = files['src/monitor_impl.rs'].decode()
    monitor = once(monitor, '#[derive(Eq, PartialEq)]\nstruct Coordinate', '#[derive(Debug, Eq, PartialEq)]\nstruct Coordinate')
    monitor = once(monitor, 'impl Source {', POSITION_TYPE + 'impl Source {')
    monitor = once(monitor, '''                ensure!(
                    coordinate(&mut self.control, &self.source).await? == self.checkpoint,
                    "dependency_monitor_position_changed"
                );''', POSITION_CHECK)
    monitor = once(monitor, '    pub(crate) fn replay(', POST_BYPASS + '    pub(crate) fn replay(')
    files['src/monitor_impl.rs'] = monitor.encode()

    actor = files['src/db.rs'].decode()
    actor = once(actor, '    pub operations: Vec<Value>,', '    pub operations: Vec<Value>,\n    pub direct_autoload_sql: bool,')
    actor = once(actor, '            query(connection, bytes, status).await', '''            let result = query(connection, bytes, status).await?;
            if cache.is_none() && sequence == 10 && bytes == AUTOLOAD_SQL && result.0 {
                observation.lock().map_err(|_| "actor_lock_failed")?.direct_autoload_sql = true;
            }
            Ok(result)''')
    files['src/db.rs'] = actor.encode()

    cache = files['src/options_cache.rs'].decode()
    start = cache.index('    pub(crate) fn invalidate(')
    end = cache.index('    pub(crate) fn diagnostic', start)
    cache = once(cache, cache[start:end], '')
    files['src/options_cache.rs'] = cache.encode()

    worker = files['src/main.rs'].decode()
    for old, new in (
        ('target/php-wasix-autoload-binlog-probe/private-capture', 'target/php-wasix-autoload-bypass-probe/private-capture'),
        ('owner.prepare(&input, seq)?;\n                Some(owner.cache.clone())', 'owner.prepare(&input, seq)?'),
        ('        report["operations"] = json!(s.operations);', '''        report["operations"] = json!(s.operations);
        if seq == 4 {
            ensure!(s.direct_autoload_sql, "autoload_bypass_direct_sql_missing");
            report["autoload_direct_sql"] = json!(true);
        }'''),
        ('stats["stats"]["sql_reads"] == 2', 'stats["stats"]["sql_reads"] == 1'),
        ('stats["stats"]["commit_invalidations"] == 1', 'stats["stats"]["commit_invalidations"] == 0'),
        ('final_observation["binlog_fence"] = verified["fence"].clone();',
         'final_observation["position_mismatch"] = verified["position_mismatch"].clone();'),
    ):
        worker = once(worker, old, new)
    files['src/main.rs'] = worker.encode()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    for name in ('Cargo.toml', 'Cargo.lock'):
        if (HERE / name).read_bytes() != once(pinned(name), OLD, NAME):
            raise ValueError('autoload_bypass_cargo_changed')
    for name, wire in outputs().items():
        path = HERE / name
        if path.resolve() != path:
            raise ValueError('autoload_bypass_output_alias')
        if path.exists():
            if not path.is_file() or path.read_bytes() != wire:
                raise ValueError('autoload_bypass_generated_changed')
        elif args.verify_only:
            raise ValueError('autoload_bypass_generated_missing')
        else:
            with path.open('xb') as stream:
                stream.write(wire)


if __name__ == '__main__':
    main()
