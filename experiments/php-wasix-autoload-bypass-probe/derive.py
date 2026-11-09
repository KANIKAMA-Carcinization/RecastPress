"""Derive the isolated autoload position-mismatch experiment."""

import argparse
from hashlib import sha256
from pathlib import Path
import runpy


HERE = Path(__file__).resolve().parent
BASE = HERE.parent / 'php-wasix-autoload-binlog-probe'
FRONTIER = HERE.parent / 'binlog-autoload-frontier-probe/src/live.rs'
FRONTIER_SHA = '02811fb7e3a8333e78aa371965917e4d238e85af9aed5b1278e8f140892ed165'
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
        b'#[allow(dead_code)]\n#[path = "monitor_impl.rs"]\nmod implementation;\npub(crate) use implementation::{GuardedCache, LiveWatch, Monitor, PositionMismatch};')
    monitor = files['src/monitor_impl.rs'].decode()
    monitor = once(monitor, '#[derive(Eq, PartialEq)]\nstruct Coordinate', '#[derive(Clone, Debug, Eq, PartialEq)]\nstruct Coordinate')
    monitor = once(monitor, 'Builder::new_current_thread()', 'Builder::new_multi_thread().worker_threads(1)')
    monitor = once(monitor, 'impl Source {', POSITION_TYPE + 'impl Source {')
    monitor = once(monitor, '    pub(crate) fn start(port: u16, password: &str, nonce: &str) -> Result<Self> {',
        '    pub(crate) fn start(port: u16, password: &str, nonce: &str) -> Result<Self> {\n'
        '        Self::start_at_value(port, password, nonce, OLD)\n    }\n\n'
        '    pub(crate) fn start_at_value(port: u16, password: &str, nonce: &str, expected_value: &[u8]) -> Result<Self> {\n'
        '        ensure!(expected_value == OLD || expected_value == NEW, "dependency_monitor_expected_value");')
    monitor = once(monitor, 'let option_id = autoload_id(&mut connection, OLD).await?;',
        'let option_id = autoload_id(&mut connection, expected_value).await?;')
    monitor = once(monitor, '            replayed: false,', '            replayed: expected_value == NEW,')
    monitor = once(monitor, '''                ensure!(
                    coordinate(&mut self.control, &self.source).await? == self.checkpoint,
                    "dependency_monitor_position_changed"
                );''', POSITION_CHECK)
    monitor = once(monitor, '    pub(crate) fn replay(', POST_BYPASS + '    pub(crate) fn replay(')
    monitor = once(monitor, '    pub(crate) fn unchanged(&mut self)', '''    pub(crate) fn source_identity(&mut self) -> Result<crate::source_identity::SourceIdentity> {
        self.runtime.block_on(async {
            timeout(DEADLINE, crate::source_identity::SourceIdentity::from_monitor(&mut self.control, &self.nonce))
                .await.context("autoload_binding_monitor_timeout")?
        })
    }

    pub(crate) fn unchanged(&mut self)''')
    frontier = FRONTIER.read_bytes()
    if sha256(frontier).hexdigest() != FRONTIER_SHA or FRONTIER.resolve() != FRONTIER:
        raise ValueError('autoload_live_pinned_preamble')
    preamble = frontier.decode().split('async fn blocking_preamble(', 1)[1].split('\nimpl Monitor {', 1)[0]
    scan = monitor.split('async fn scan(', 1)[1]
    scan = once(scan, '    option_id: u64,', '    option_id: u64,\n    mut preamble: VecDeque<Event>,')
    scan = once(scan, '    while let Some(event) = stream.next().await {', '''    loop {
        let event = match preamble.pop_front() {
            Some(event) => Ok(event),
            None => stream.next().await.context("autoload_live_dump_ended")?,
        };''')
    scan = once(scan, '        events += 1;', '        events += 1;\n        if decoder.applied == 1 { break; }')
    files['src/monitor_impl.rs'] = (monitor + '\ninclude!("live_watch.rs");\nasync fn blocking_preamble('
        + preamble + '\nasync fn scan_live(' + scan).encode()

    actor = files['src/db.rs'].decode()
    actor = once(actor, '    pub operations: Vec<Value>,', '    pub operations: Vec<Value>,\n    pub direct_autoload_sql: bool,')
    actor = once(actor, '    pub direct_autoload_sql: bool,', '    pub direct_autoload_sql: bool,\n    pub source_identity_verified: bool,')
    actor = once(actor, '        cache: Option<Arc<crate::options_cache::OptionsCache>>,',
        '        cache: Option<crate::monitor::GuardedCache>,\n        identity: Option<crate::source_identity::SourceIdentity>,')
    actor = once(actor, 'sequence,cache.as_deref()))', 'sequence,cache.as_ref(),identity.as_ref()))')
    actor = once(actor, '    cache: Option<&crate::options_cache::OptionsCache>,',
        '    cache: Option<&crate::monitor::GuardedCache>,\n    identity: Option<&crate::source_identity::SourceIdentity>,')
    actor = once(actor, '            let c = conn.as_ref().ok_or("connection_missing")?;', '''            let c = conn.as_mut().ok_or("connection_missing")?;
            let initial_status = c.last_ok_packet().map(|packet| packet.status_flags().bits());
            if let Some(identity) = identity {
                identity.verify_actor(c).await?;
                observation.lock().map_err(|_| "actor_lock_failed")?.source_identity_verified = true;
            }
            check(cache.is_none() || identity.is_some(), "autoload_binding_required")?;''')
    actor = once(actor, '''            *status = c
                .last_ok_packet()
                .map(|packet| packet.status_flags().bits());''', '            *status = initial_status;')
    actor = once(actor, '            query(connection, bytes, status).await', '''            let result = query(connection, bytes, status).await?;
            if sequence == 10 && bytes == AUTOLOAD_SQL && result.0 {
                observation.lock().map_err(|_| "actor_lock_failed")?.direct_autoload_sql = true;
            }
            Ok(result)''')
    begin = actor.index('                if let Some(data) = cache.lookup(before_status)')
    end = actor.index('            let result = query(connection, bytes, status).await?', begin)
    actor = once(actor, actor[begin:end], '''                let choice = cache.current(|entry| {
                    let hit = entry.lookup(before_status);
                    if hit.is_none() { entry.record_sql(); }
                    hit
                });
                match choice {
                    Some(Some(data)) => {
                        *status = Some(data["status"].as_u64()
                            .and_then(|flags| u16::try_from(flags).ok())
                            .ok_or("autoload_cached_status")?);
                        return Ok((true, data));
                    }
                    Some(None) => {
                        let (ok, data) = query(connection, bytes, status).await?;
                        if ok { cache.current(|entry| entry.remember(before_status, &data)); }
                        return Ok((ok, data));
                    }
                    None => {}
                }
            }
''')
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
        ('mod options_cache;', 'mod options_cache;\nmod source_identity;'),
        ('cache: Option<Arc<options_cache::OptionsCache>>, report: &mut Value)',
         'cache: Option<monitor::GuardedCache>, identity: Option<source_identity::SourceIdentity>, report: &mut Value)'),
        ('db::Actor::start(input, cache)', 'db::Actor::start(input, cache, identity)'),
        ('&capture_dir,cache,&mut report)', '&capture_dir,cache,guard.as_ref().map(|owner| owner.identity.clone()),&mut report)'),
        ('        report["operations"] = json!(s.operations);', '''        report["operations"] = json!(s.operations);
        report["source_identity_verified"] = json!(s.source_identity_verified);
        if seq == 4 {
            ensure!(s.direct_autoload_sql, "autoload_bypass_direct_sql_missing");
            report["autoload_direct_sql"] = json!(true);
        }'''),
        ('stats["stats"]["sql_reads"] == 2', 'stats["stats"]["sql_reads"] == 1'),
        ('stats["stats"]["commit_invalidations"] == 1', 'stats["stats"]["commit_invalidations"] == 0'),
        ('final_observation["binlog_fence"] = verified["fence"].clone();',
         'final_observation["position_mismatch"] = verified["position_mismatch"].clone();\n        final_observation["live_commit"] = verified["live_commit"].clone();'),
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
