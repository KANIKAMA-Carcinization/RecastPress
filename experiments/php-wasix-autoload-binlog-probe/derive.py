"""Combine the accepted stock autoload actor and finite binlog coordinator."""

import argparse
from hashlib import sha256
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
AUTOLOAD = HERE.parent / 'php-wasix-autoload-cache-probe'
BINLOG = HERE.parent / 'php-wasix-binlog-dependency-probe'
OLD = b'recastpress-php-wasix-binlog-dependency-probe'
NAME = b'recastpress-php-wasix-autoload-binlog-probe'
PINS = {
    'derive_worker.py': 'b04aeb73de18338c2ee1f160ee9433ecd5fb7ba9a0734dd1ccf9ea5eb8fb7a9a',
    'derive_actor.py': 'efcd25346c9d39951bd79e777911ce9e82f040e8fb9eddd0fa0dedc08a67a199',
    'src/options_cache.rs': '8a0f0887e98ab484f40e15fcf8ef88ace00954ddf769b948c777b86407911321',
}


def pinned(path, digest):
    wire = path.read_bytes()
    if sha256(wire).hexdigest() != digest:
        raise ValueError('autoload_binlog_base_source_changed')
    return wire


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('autoload_binlog_derivation_marker')
    return source.replace(old, new, 1)


def manifest():
    wire = pinned(BINLOG / 'Cargo.toml', '0e87235d10fa9643e64d12bc15ebab1dd9bb41ed1969ed3947191b58aeb1a6db')
    return once(wire, OLD, NAME)


def load(name, function):
    path = AUTOLOAD / name
    pinned(path, PINS[name])
    return runpy.run_path(str(path))[function]()


def options_cache():
    source = pinned(AUTOLOAD / 'src/options_cache.rs', PINS['src/options_cache.rs']).decode()
    start, end = source.index('pub(crate) struct OptionsGuard'), source.index('#[cfg(test)]')
    source = source[:start] + source[end:]
    start, end = source.index('const DEADLINE'), source.index('#[derive(Deserialize)]')
    source = source[:start] + source[end:]
    for before, after in (
        ("//! A one-reply cache scoped to the fixture's live, read-locked SQL connection.",
         '//! The fixed autoload result across one verified binlog invalidation.'),
        ('use crate::{db::Input, result_budget::BoundedWriter};', 'use crate::result_budget::BoundedWriter;'),
        ('use anyhow::{Context, Result, anyhow, ensure};', 'use anyhow::{Result, anyhow, ensure};'),
        ('use mysql_async::{Conn, OptsBuilder, prelude::Queryable};\n', ''),
        ('use std::{\n    sync::{Arc, Mutex},\n    time::Duration,\n};\nuse tokio::{runtime::Runtime, time::timeout};',
         'use std::sync::Mutex;'),
        ('    port: u16,', '    pub(crate) port: u16,'),
        ('    sql_reads: usize,', '    sql_reads: usize,\n    commit_invalidations: usize,'),
        ('                sql_reads: 0,', '                sql_reads: 0,\n                commit_invalidations: 0,'),
        ('    fn new()', '    pub(crate) fn new()'),
        ('state.sql_reads != 1', 'state.sql_reads != state.commit_invalidations + 1'),
        ('    pub(crate) fn diagnostic', '''    pub(crate) fn invalidate(&self) -> Result<()> {
        let mut state = self.state.lock().map_err(|_| anyhow!("autoload_cache_poisoned"))?;
        ensure!(state.active && state.commit_invalidations == 0 && state.entry.is_some()
            && state.sql_reads == 1 && state.hits == 1, "autoload_cache_invalidation_phase");
        state.entry = None;
        state.commit_invalidations = 1;
        ensure!(self.budget.stats().map_err(|_| anyhow!("autoload_cache_budget"))?
            .claimed_for(ResourceKind::ObjectCache) == 0, "autoload_cache_invalidation_budget");
        Ok(())
    }

    pub(crate) fn diagnostic'''),
        ('    fn disable(&self)', '    pub(crate) fn disable(&self)'),
        ('    fn finish(&self)', '    pub(crate) fn finish(&self)'),
        ('"retained_reply_bytes":state.retained_reply_bytes}',
         '"retained_reply_bytes":state.retained_reply_bytes,"commit_invalidations":state.commit_invalidations}'),
    ):
        source = once(source, before, after)
    return source.encode()


def worker():
    source = load('derive_worker.py', 'worker').decode()
    source = source.replace('options_cache::OptionsGuard', 'guard::OptionsGuard')
    for before, after in (
        ('mod options_cache;', 'mod options_cache;\nmod guard;\nmod monitor;'),
        ('target/php-wasix-autoload-cache-probe/private-capture',
         'target/php-wasix-autoload-binlog-probe/private-capture'),
        ('(1..=3).contains(&seq)', '(1..=4).contains(&seq)'),
        ('self.next_seq<=3', 'self.next_seq<=4'),
        ('self.next_seq!=4', 'self.next_seq!=5'),
        ('completed==3', 'completed==4'),
        ('for seq in 1..=3 {', 'for seq in 1..=4 {'),
        ('session_frame(4,&"d".repeat(48))', 'session_frame(5,&"e".repeat(48))'),
        ('for seq in [0,2,3,4] {', 'for seq in [0,2,3,4,5] {'),
        ('owner.verify(&input)?;', 'owner.prepare(&input, seq)?;'),
        ('''stats["sql_reads"] == 1 && stats["hits"] == 1 && stats["charged_bytes"] == 0
        && stats["retained_reply_bytes"]''',
         '''stats["stats"]["sql_reads"] == 2 && stats["stats"]["hits"] == 1
        && stats["stats"]["charged_bytes"] == 0 && stats["stats"]["commit_invalidations"] == 1
        && stats["stats"]["retained_reply_bytes"]'''),
        ('''        final_observation["options_cache"] = stats?.context("options_cache_stats_missing")?;''',
         '''        let verified = stats?.context("options_cache_stats_missing")?;
        final_observation["options_cache"] = verified["stats"].clone();
        final_observation["binlog_fence"] = verified["fence"].clone();'''),
        ('''stats.ok().flatten().unwrap_or(Value::Null)''',
         '''stats.ok().flatten().map_or(Value::Null, |verified| verified["stats"].clone())'''),
    ):
        source = once(source, before, after)
    return source.encode()


def outputs():
    return {'src/main.rs': worker(), 'src/db.rs': load('derive_actor.py', 'derived_bytes'),
        'src/options_cache.rs': options_cache()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if (HERE / 'Cargo.toml').read_bytes() != manifest():
        raise ValueError('autoload_binlog_manifest_changed')
    for name, wire in outputs().items():
        path = HERE / name
        if path.resolve() != path:
            raise ValueError('autoload_binlog_output_alias')
        if path.exists():
            if path.read_bytes() != wire:
                raise ValueError('autoload_binlog_generated_changed')
        elif args.verify_only:
            raise ValueError('autoload_binlog_generated_missing')
        else:
            path.parent.mkdir(exist_ok=True)
            with path.open('xb') as stream:
                stream.write(wire)


if __name__ == '__main__':
    main()
