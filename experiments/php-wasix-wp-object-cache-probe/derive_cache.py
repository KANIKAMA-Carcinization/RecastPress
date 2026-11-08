"""Expose the byte-pinned cache worker as an in-process request session."""

import argparse
import hashlib
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'wp-object-cache-rust-probe/src/main.rs'
SOURCE_SHA = 'f586a46b52f73613a0ae21f3b940e88cde7bca954602528f08dadab91dbd2820'
GENERATED = HERE / 'src/cache_host.rs'

SESSION = '''
pub(crate) struct Session {
    cache: Cache,
    operations: usize,
    multiple: usize,
    integer: usize,
    flushes: usize,
    finished: bool,
}

impl Session {
    pub(crate) fn new() -> Self {
        Self { cache: Cache::new(BUDGET), operations: 0, multiple: 0, integer: 0, flushes: 0, finished: false }
    }

    pub(crate) fn exchange(&mut self, frame: &[u8]) -> Result<Vec<u8>> {
        if self.finished {
            return Err("cache_session_finished");
        }
        if frame.is_empty() || frame.len() > FRAME_LIMIT || !frame.ends_with(b"\\n")
            || frame[..frame.len()-1].contains(&b'\\n') {
            return Err("invalid_frame");
        }
        let request: Request = serde_json::from_slice(&frame[..frame.len()-1]).map_err(|_| "invalid_request")?;
        let reply = if matches!(request, Request::Finish {}) {
            let reply = self.cache.finish(self.operations)?;
            self.finished = true;
            reply
        } else {
            if self.operations == OPERATIONS_LIMIT {
                return Err("operation_limit");
            }
            let group = match &request {
                Request::Get { group, .. } | Request::Delete { group, .. } | Request::Set { group, .. }
                | Request::Add { group, .. } | Request::Replace { group, .. } | Request::Incr { group, .. }
                | Request::Decr { group, .. } | Request::FlushGroup { group } => Some(*group),
                Request::Flush {} | Request::Finish {} => None,
            };
            let reply = self.cache.execute(request)?;
            match group {
                Some(Group::Multiple) => self.multiple += 1,
                Some(Group::Integer) => self.integer += 1,
                None => self.flushes += 1,
            }
            self.operations += 1;
            reply
        };
        let mut wire = serde_json::to_vec(&reply).map_err(|_| "reply_encode")?;
        if wire.len() >= FRAME_LIMIT {
            return Err("reply_limit");
        }
        wire.push(b'\\n');
        Ok(wire)
    }

    pub(crate) fn finished(&self) -> bool { self.finished }

    pub(crate) fn summary(&self) -> Result<Value> {
        let stats = self.cache.budget.stats().map_err(|_| "cache_budget")?;
        Ok(json!({"operations":self.operations,"entries":self.cache.entries.len(),
            "charged_bytes":stats.claimed_for(ResourceKind::ObjectCache),"flushes":self.flushes,
            "groups":{"rp-cache-multiple":self.multiple,"rp-cache-integer":self.integer}}))
    }

    pub(crate) fn abort(&mut self) {
        self.cache.entries.clear();
    }
}
'''


def derived_bytes():
    if SOURCE.resolve(strict=True) != SOURCE or not SOURCE.is_file():
        raise ValueError('cache_source_path')
    wire = SOURCE.read_bytes()
    if hashlib.sha256(wire).hexdigest() != SOURCE_SHA:
        raise ValueError('cache_source_changed')
    entry = b'fn read_frame('
    imports = b'use std::io::{self, BufRead, Write};\n'
    if wire.count(entry) != 1 or wire.count(imports) != 1:
        raise ValueError('cache_source_boundary')
    return wire[:wire.index(entry)].replace(imports, b'', 1) + SESSION.encode('ascii')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    wire = derived_bytes()
    if GENERATED.resolve() != GENERATED:
        raise ValueError('cache_generated_alias')
    if GENERATED.exists():
        if not GENERATED.is_file() or GENERATED.read_bytes() != wire:
            raise ValueError('cache_generated_changed')
    else:
        if args.verify_only:
            raise ValueError('cache_generated_missing')
        GENERATED.parent.mkdir(exist_ok=True)
        with GENERATED.open('xb') as output:
            output.write(wire)
    print(hashlib.sha256(wire).hexdigest())


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError):
        print('WordPress cache session derivation failed', file=sys.stderr)
        sys.exit(1)
