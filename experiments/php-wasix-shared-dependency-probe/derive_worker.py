"""Derive the fixed shared dependency worker from the accepted WordPress session."""

import argparse
from hashlib import sha256
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / 'php-wasix-wp-session-probe'
OLD = 'recastpress-php-wasix-wp-session-probe'
NAME = 'recastpress-php-wasix-shared-dependency-probe'
PINS = {
    'Cargo.toml': '3cca91ad9ecb71e2d521a7eac654cc0c51a54529f9b8dd86e153444bbf24aee4',
    'Cargo.lock': '4da0d5b209827c5004abd30847aec84e3c7cee1b4a45b8eace6bf8d41e3b0227',
    'src/main.rs': '3f3992532c100d44e1b1d1897d4003ea7fc6a7fc23406b8ca923f11969829bb0',
}


def original(name):
    wire = (BASE / name).read_bytes()
    if sha256(wire).hexdigest() != PINS[name]:
        raise ValueError('shared_dependency_source_changed')
    return wire.decode()


def once(source, old, new, count=1):
    if source.count(old) != count:
        raise ValueError('shared_dependency_derivation_marker')
    return source.replace(old, new)


def outputs():
    manifest = once(once(original('Cargo.toml'), OLD, NAME), '[dependencies]',
        '[dependencies]\nrecastpress-core = { path = "../../crates/recastpress-core" }')
    lock = original('Cargo.lock')
    old = f'[[package]]\nname = "{OLD}"\nversion = "0.0.0"\ndependencies = [\n "anyhow",\n "mysql_async",'
    lock = once(lock, old, '[[package]]\nname = "recastpress-core"\nversion = "0.5.0"\n\n'
        + old.replace(OLD, NAME) + '\n "recastpress-core",')
    source = original('src/main.rs')
    for before, after, count in (
        ('#[path = "../../php-wasix-wp-request-probe/src/db.rs"]\nmod db;', '''mod db;
mod dependency;
#[allow(dead_code)]
#[path = "../../binlog-shared-cache-probe/shared_object_cache.rs"]
mod shared_object_cache;''', 1),
        ('    Ok((finite(fixtures)?, wp, manifest))', '''    for (name, wire) in [
        ("bootstrap.php", &include_bytes!("../fixtures/bootstrap.php")[..]),
        ("adapter.php", &include_bytes!("../fixtures/adapter.php")[..]),
    ] {
        ensure!(fixtures.insert(name.into(), Arc::from(wire)).is_some(), "dependency_fixture_mapping");
    }
    Ok((finite(fixtures)?, wp, manifest))''', 1),
        ('include_str!("../../php-wasix-wp-request-context-probe/fixtures/bootstrap.php")',
         'include_str!("../fixtures/bootstrap.php")', 2),
        ('target/php-wasix-wp-session-probe/private-capture', 'target/php-wasix-shared-dependency-probe/private-capture', 1),
        ('seq: usize, capture_dir: &CaptureDir, report: &mut Value) -> Result<()> {',
         'seq: usize, capture_dir: &CaptureDir, shared: Arc<dependency::Dependency>, report: &mut Value) -> Result<()> {', 1),
        ('db::Actor::start(input)', 'db::Actor::start(input, shared)', 1),
        ('    commit_private_response(report, &php, case)?;', '''    let source = if seq == 1 { "miss_fill" } else { "hit" };
    let expected = json!({"source":source,"bytes":dependency::FIXTURE_VALUE.len(),
        "sha256":db::sha(dependency::FIXTURE_VALUE),"stock_cache_unchanged":true});
    ensure!(php["dependency_trial"] == expected, "dependency_guest_ack");
    commit_private_response(report, &php, case)?;
    report["dependency_trial"] = expected;''', 1),
        ('let mut protocol=SessionProtocol::new();let mut completed=0;let mut cleaned=true;let mut secrets=Vec::new();',
         '''let mut protocol=SessionProtocol::new();let mut completed=0;let mut cleaned=true;let mut secrets=Vec::new();
    let mut guard = None::<dependency::FixtureGuard>;''', 1),
        ('            secrets.extend(input.secrets());', '''            secrets.extend(input.secrets());
            ensure!(case == Case::Home, "dependency_home_only");
            if guard.is_none() {
                guard = Some(dependency::FixtureGuard::start(&input)?);
            }
            let owner = guard.as_mut().context("dependency_fixture_guard_missing")?;
            owner.verify(&input)?;''', 1),
        ('let request_result=probe(input,case,&compiled,seq,&capture_dir,&mut report);',
         '''let request_result=probe(input,case,&compiled,seq,&capture_dir,owner.dependency.clone(),&mut report)
                .and_then(|()| owner.active());''', 1),
        ('    let passed=result.is_ok() && completed==3 && !protocol.poisoned && cleaned;\n    emit(output,&final_report(passed,1,completed,cleaned),&secrets)?;',
         '''    let stats = guard.take().map(dependency::FixtureGuard::finish).transpose();
    let expected = json!({"dependency_sql_reads":1,"shared_hits":2,"charged_bytes":0});
    let dependency_verified = stats.as_ref().is_ok_and(|stats| stats.as_ref() == Some(&expected));
    let passed=result.is_ok() && completed==3 && !protocol.poisoned && cleaned && dependency_verified;
    let mut final_observation=final_report(passed,1,completed,cleaned && dependency_verified);
    if passed {
        final_observation["dependency"] = expected;
        final_observation["fixture_guard_released"] = json!(true);
    }
    emit(output,&final_observation,&secrets)?;''', 1),
    ):
        source = once(source, before, after, count)
    return {'Cargo.toml': manifest.encode(), 'Cargo.lock': lock.encode(), 'src/main.rs': source.encode()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    for name, wire in outputs().items():
        path = HERE / name
        if path.resolve() != path:
            raise ValueError('shared_dependency_output_alias')
        if path.exists():
            if path.read_bytes() != wire:
                raise ValueError('shared_dependency_generated_changed')
        elif args.verify_only:
            raise ValueError('shared_dependency_generated_missing')
        else:
            path.parent.mkdir(exist_ok=True)
            with path.open('xb') as output:
                output.write(wire)


if __name__ == '__main__':
    main()
