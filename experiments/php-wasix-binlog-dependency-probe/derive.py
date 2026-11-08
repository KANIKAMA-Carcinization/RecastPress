"""Reuse the accepted shared dependency worker with a bounded binlog fence."""

import argparse
from hashlib import sha256
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / 'php-wasix-shared-dependency-probe'
OLD = 'recastpress-php-wasix-shared-dependency-probe'
NAME = 'recastpress-php-wasix-binlog-dependency-probe'
PINS = {
    'derive_actor.py': '5acf47c78f5ce3a0670a6c113b08698e756c4201082ff3b44a3ad966f28d3c20',
    'derive_guest.py': 'b9297b2c0e91cf64a599ec77c394dd88f0547d8a040b5160f8e2123f7bc1bf5d',
    'derive_worker.py': '7ab985398eea4d6bb8e672f82959caddc22a157fa83856484920f8e9e69cdd92',
}


def load(name):
    path = BASE / name
    if sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError('binlog_dependency_deriver_changed')
    return runpy.run_path(str(path))


def once(source, marker, replacement):
    if source.count(marker) != 1:
        raise ValueError('binlog_dependency_derivation_marker')
    return source.replace(marker, replacement, 1)


def manifest():
    upstream = load('derive_worker.py')['outputs']()['Cargo.toml'].decode()
    return once(once(upstream, OLD, NAME), '[dependencies]',
        '[dependencies]\nfutures-util = { version = "0.3", default-features = false, features = ["std"] }\n'
        'recastpress-db = { path = "../../crates/recastpress-db" }').encode()


def worker():
    source = load('derive_worker.py')['outputs']()['src/main.rs'].decode()
    for marker, replacement in (
        ('mod dependency;', 'mod dependency;\nmod monitor;'),
        ('target/php-wasix-shared-dependency-probe/private-capture',
         'target/php-wasix-binlog-dependency-probe/private-capture'),
        ('let source = if seq == 1 { "miss_fill" } else { "hit" };',
         '''let source = if seq == 2 { "hit" } else { "miss_fill" };
    let bytes = if seq == 3 { dependency::UPDATED_VALUE } else { dependency::FIXTURE_VALUE };'''),
        ('"bytes":dependency::FIXTURE_VALUE.len(),\n        "sha256":db::sha(dependency::FIXTURE_VALUE)',
         '"bytes":bytes.len(),\n        "sha256":db::sha(bytes)'),
        ('    let mut guard = None::<dependency::FixtureGuard>;', '''    let config_wire = read_frame(read)?.context("monitor_config_missing")?;
    let config = dependency::MonitorConfig::parse(&config_wire)?;
    secrets.extend([config.password.clone(), config.nonce.clone()]);
    let mut guard = None::<dependency::FixtureGuard>;'''),
        ('dependency::FixtureGuard::start(&input)?', 'dependency::FixtureGuard::start(&input, &config)?'),
        ('owner.verify(&input)?;', 'owner.prepare(&input, seq)?;'),
        ('let expected = json!({"dependency_sql_reads":1,"shared_hits":2,"charged_bytes":0});',
         'let expected = json!({"dependency_sql_reads":2,"shared_hits":1,"charged_bytes":0,"commit_invalidations":1});'),
        ('stats.as_ref() == Some(&expected)', 'stats.as_ref().is_some_and(|value| value["stats"] == expected)'),
        ('        final_observation["fixture_guard_released"] = json!(true);',
         '''        final_observation["fixture_guard_released"] = json!(true);
        final_observation["binlog_fence"] = stats?.context("binlog_fence_missing")?["fence"].clone();'''),
    ):
        source = once(source, marker, replacement)
    return source.encode()


def outputs():
    guest = load('derive_guest.py')['guest_files']()
    adapter = guest['adapter.php']
    adapter = once(adapter, b"$expected = 'recastpress-shared-fixture';",
        b"$expected = ['recastpress-shared-fixture', 'recastpress-shared-updated'];")
    adapter = once(adapter, b"rp_unhex($data['value_hex'], strlen($expected))",
        b"rp_unhex($data['value_hex'], 26)")
    guest['adapter.php'] = once(adapter, b'$bytes === $expected', b'in_array($bytes, $expected, true)')
    return {'src/main.rs': worker(), 'src/db.rs': load('derive_actor.py')['derived_bytes'](),
        **{'fixtures/' + name: wire for name, wire in guest.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if (HERE / 'Cargo.toml').read_bytes() != manifest():
        raise ValueError('binlog_dependency_manifest_changed')
    for name, wire in outputs().items():
        path = HERE / name
        if path.resolve() != path:
            raise ValueError('binlog_dependency_output_alias')
        if path.exists():
            if path.read_bytes() != wire:
                raise ValueError('binlog_dependency_generated_changed')
        elif args.verify_only:
            raise ValueError('binlog_dependency_generated_missing')
        else:
            path.parent.mkdir(exist_ok=True)
            with path.open('xb') as output:
                output.write(wire)


if __name__ == '__main__':
    main()
