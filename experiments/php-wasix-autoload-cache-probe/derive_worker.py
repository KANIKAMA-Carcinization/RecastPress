"""Derive a stock WordPress session with a guarded autoload SELECT cache."""

import argparse
from hashlib import sha256
from pathlib import Path

HERE = Path(__file__).resolve().parent
STOCK = HERE.parent / 'php-wasix-wp-session-probe'
SHARED = HERE.parent / 'php-wasix-shared-dependency-probe'
OLD = 'recastpress-php-wasix-shared-dependency-probe'
NAME = 'recastpress-php-wasix-autoload-cache-probe'
SOURCE = '3f3992532c100d44e1b1d1897d4003ea7fc6a7fc23406b8ca923f11969829bb0'


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('autoload_worker_derivation_marker')
    return source.replace(old, new, 1)


def manifest():
    return replace_once((SHARED / 'Cargo.toml').read_bytes(), OLD.encode(), NAME.encode())


def worker():
    wire = (STOCK / 'src/main.rs').read_bytes()
    if sha256(wire).hexdigest() != SOURCE:
        raise ValueError('autoload_worker_source_changed')
    source = wire.decode()
    for before, after in (
        ('#[path = "../../php-wasix-wp-request-probe/src/db.rs"]\nmod db;',
         'mod db;\nmod options_cache;'),
        ('target/php-wasix-wp-session-probe/private-capture',
         'target/php-wasix-autoload-cache-probe/private-capture'),
        ('seq: usize, capture_dir: &CaptureDir, report: &mut Value) -> Result<()> {',
         'seq: usize, capture_dir: &CaptureDir, cache: Option<Arc<options_cache::OptionsCache>>, report: &mut Value) -> Result<()> {'),
        ('db::Actor::start(input)', 'db::Actor::start(input, cache)'),
        ('let mut protocol=SessionProtocol::new();let mut completed=0;let mut cleaned=true;let mut secrets=Vec::new();',
         '''let mut protocol=SessionProtocol::new();let mut completed=0;let mut cleaned=true;let mut secrets=Vec::new();
    let mut guard = None::<options_cache::OptionsGuard>;
    let mut warmed_port = None;'''),
        ('            let Some(wire)=read_frame(read)? else {protocol.finish()?;break};',
         '''            let config = if completed == 1 {
                let wire = read_frame(read)?.context("options_guard_config_missing")?;
                let config = options_cache::GuardConfig::parse(&wire)?;
                secrets.extend([config.password.clone(),config.nonce.clone()]);
                Some(config)
            } else { None };
            let Some(wire)=read_frame(read)? else {protocol.finish()?;break};'''),
        ('            secrets.extend(input.secrets());',
         '''            secrets.extend(input.secrets());
            let cache = if seq == 1 {
                ensure!(case == Case::Home,"options_warmup_route");
                warmed_port = Some(input.port);
                None
            } else {
                ensure!(case == Case::Missing && warmed_port == Some(input.port),"options_measured_route");
                if let Some(config) = config {
                    guard = Some(options_cache::OptionsGuard::start(&input,&config)?);
                }
                let owner = guard.as_mut().context("options_guard_missing")?;
                owner.verify(&input)?;
                Some(owner.cache.clone())
            };'''),
        ('let request_result=probe(input,case,&compiled,seq,&capture_dir,&mut report);',
         '''let request_result=probe(input,case,&compiled,seq,&capture_dir,cache,&mut report)
                .and_then(|()| guard.as_mut().map(options_cache::OptionsGuard::active).transpose().map(|_|()));'''),
        ('    let passed=result.is_ok() && completed==3 && !protocol.poisoned && cleaned;\n    emit(output,&final_report(passed,1,completed,cleaned),&secrets)?;',
         '''    let candidate = guard.as_ref().map_or(Value::Null, |owner| owner.cache.diagnostic());
    let stats = guard.take().map(options_cache::OptionsGuard::finish).transpose();
    let cache_verified = stats.as_ref().is_ok_and(|stats| stats.as_ref().is_some_and(|stats|
        stats["sql_reads"] == 1 && stats["hits"] == 1 && stats["charged_bytes"] == 0
        && stats["retained_reply_bytes"].as_u64().is_some_and(|bytes| bytes > 0 && bytes <= 128 * 1024)));
    let passed=result.is_ok() && completed==3 && !protocol.poisoned && cleaned && cache_verified;
    let mut final_observation=final_report(passed,1,completed,cleaned && cache_verified);
    if passed {
        final_observation["options_cache"] = stats?.context("options_cache_stats_missing")?;
        final_observation["options_guard_released"] = json!(true);
    } else {
        final_observation["options_guard_released"] = json!(stats.as_ref().is_ok_and(|value| value.is_some()));
        final_observation["options_cache_diagnostic"] = stats.ok().flatten().unwrap_or(Value::Null);
        final_observation["options_candidate"] = candidate;
    }
    emit(output,&final_observation,&secrets)?;'''),
    ):
        source = replace_once(source, before, after)
    return source.encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if (HERE / 'Cargo.toml').read_bytes() != manifest():
        raise ValueError('autoload_worker_manifest_changed')
    path = HERE / 'src/main.rs'
    wire = worker()
    if path.resolve() != path:
        raise ValueError('autoload_worker_output_alias')
    if path.exists():
        if path.read_bytes() != wire:
            raise ValueError('autoload_worker_generated_changed')
    elif args.verify_only:
        raise ValueError('autoload_worker_generated_missing')
    else:
        path.parent.mkdir(exist_ok=True)
        with path.open('xb') as stream:
            stream.write(wire)


if __name__ == '__main__':
    main()
