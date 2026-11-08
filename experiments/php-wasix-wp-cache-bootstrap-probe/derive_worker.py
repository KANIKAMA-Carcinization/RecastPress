"""Derive the bounded cache bootstrap worker from frozen, accepted inputs."""

import argparse
from hashlib import sha256
from pathlib import Path
import runpy
import sys


HERE = Path(__file__).resolve().parent
ACCEPTED = HERE.parent / 'php-wasix-wp-private-worker-probe'
CACHE = HERE.parent / 'php-wasix-wp-object-cache-probe'
CATALOG = HERE.parent / 'php-wasix-wp-request-probe/src/catalog.rs'
OLD_NAME = 'recastpress-php-wasix-wp-private-worker-probe'
NAME = 'recastpress-php-wasix-wp-cache-bootstrap-probe'
SHIM_SHA = 'c3ad7334e940852b74726a15ef9346d45b6d15ac9d23d726eb3fcb46dd92c5f4'


def require(condition, code):
    if not condition:
        raise ValueError(code)


def pinned(path, digest):
    require(path.resolve(strict=True) == path and path.is_file(), 'cache_worker_fixed_input')
    wire = path.read_bytes()
    require(sha256(wire).hexdigest() == digest, 'cache_worker_input_changed')
    return wire.decode('utf-8')


def replace(source, previous, replacement, count=1):
    require(source.count(previous) == count, 'cache_worker_edit_count')
    return source.replace(previous, replacement)


def catalog():
    source = pinned(CATALOG, '6f6ec144e47dba2612b0976aea13ad6b01cfa6f1e67a925b7f3289aeb8d8aaf7')
    source = replace(source, 'dropin:Arc<[u8]>)->Result<SourceFs>',
        'dropin:Arc<[u8]>,cache_shim:Arc<[u8]>)->Result<SourceFs>')
    source = replace(source, '    let overlay=SourceFs::new(files,dirs)', f'''    check(sha(&cache_shim)=="{SHIM_SHA}","cache_shim_identity")?;
    check(files.insert("wp-content/object-cache.php".into(),cache_shim).is_none(),"cache_shim_collision")?;
    let overlay=SourceFs::new(files,dirs)''')
    return replace(source, 'overlay.file_count()==3783 && overlay.directory_count()==467',
        'overlay.file_count()==3784 && overlay.directory_count()==467').encode('utf-8')


def worker():
    source = pinned(ACCEPTED / 'src/main.rs', 'e6dca46c07b88deb322e8c022f4f957344508e0c8fd9327b08a62a8df4d764bb')
    for old, new in (
        ('#[path = "../../php-wasix-wp-request-probe/src/catalog.rs"]\nmod catalog;', '''mod catalog;
mod cache_host;
#[path = "../../php-wasix-wp-object-cache-probe/src/cache_fs.rs"]
mod cache_fs;
mod trial;'''),
        ('mod strict_json;', '#[path = "../../php-wasix-wp-private-worker-probe/src/strict_json.rs"]\nmod strict_json;'),
        ('        dropin.context("dropin_missing")?,', '''        dropin.context("dropin_missing")?,
        Arc::<[u8]>::from(&include_bytes!("../fixtures/object-cache.php")[..]),'''),
        ('    Ok((finite(fixtures)?, wp, manifest))', '''    ensure!(fixtures.insert("bootstrap.php".into(), Arc::from(&include_bytes!("../fixtures/bootstrap.php")[..])).is_some(), "cache_bootstrap_fixture");
    for (name, bytes) in [
        ("cache-adapter.php", &include_bytes!("../fixtures/cache-adapter.php")[..]),
        ("cache-trial.php", &include_bytes!("../fixtures/cache-trial.php")[..]),
    ] {
        ensure!(fixtures.insert(name.into(), Arc::from(bytes)).is_none(), "cache_fixture_collision");
    }
    report["cache_shim_source"] = json!({"guest":"/wordpress/wp-content/object-cache.php",
        "bytes":include_bytes!("../fixtures/object-cache.php").len(),
        "sha256":db::sha(include_bytes!("../fixtures/object-cache.php")),"guest_read_only":true});
    Ok((finite(fixtures)?, wp, manifest))'''),
        ('target/php-wasix-wp-private-worker-probe/private-capture', 'target/php-wasix-wp-cache-bootstrap-probe/private-capture'),
        ('    rpc: &rpc_fs::RpcFs,\n    mode:', '    rpc: &rpc_fs::RpcFs,\n    cache: &cache_fs::CacheFs,\n    mode:'),
        ('            .with_mount("/rp-wpdb".into(), Arc::new(rpc.clone()))', '''            .with_mount("/rp-wpdb".into(), Arc::new(rpc.clone()))
            .with_mount(cache_fs::MOUNT.into(), Arc::new(cache.clone()))'''),
        ('    let rpc = rpc_fs::RpcFs::new(nonce, actor.dispatch.clone());', '''    let rpc = rpc_fs::RpcFs::new(nonce, actor.dispatch.clone());
    let cache = cache_fs::CacheFs::new();'''),
        ('        &rpc,\n        mode,', '        &rpc,\n        &cache,\n        mode,'),
        ('    report["rpc"] = rpc.finish();', '''    let cache_result = cache.finish();
    report["rpc"] = rpc.finish();'''),
        ('    commit_private_response(report, &php, case)?;', '''    let cache_transport = cache_result?;
    ensure!(trial::complete(&php["cache_trial"], &cache_transport), "cache_trial_completion");
    commit_private_response(report, &php, case)?;
    report["cache_trial"] = php["cache_trial"].clone();
    report["cache_transport"] = cache_transport;'''),
        ('        let (case, input) = parse_input(&wire)?;','''        let (case, input) = parse_input(&wire)?;
        ensure!(case == Case::Home, "cache_trial_home_only");'''),
    ):
        source = replace(source, old, new)
    source = replace(source, 'include_str!("../../php-wasix-wp-request-context-probe/fixtures/bootstrap.php")',
        'include_str!("../fixtures/bootstrap.php")', 2)
    source = replace(source, 'recastpress.php-wasix-wp-private-worker-observation.v0.5',
        'recastpress.php-wasix-wp-cache-bootstrap-observation.v0.5', 2)
    return source.encode('utf-8')


def outputs():
    manifest = pinned(ACCEPTED / 'Cargo.toml', '702057cff379e5d74e4ab5deac5759f16402c4e4b136d146ac95bae2b4f050a7')
    manifest = replace(replace(manifest, OLD_NAME, NAME), '[dependencies]',
        '[dependencies]\nrecastpress-core = { path = "../../crates/recastpress-core" }')
    lock = pinned(ACCEPTED / 'Cargo.lock', '0f4a52e43a1bea2b8b8e4ff438cdfd1dab24fe8288d363071d8a10bae1431266')
    old_package = f'[[package]]\nname = "{OLD_NAME}"\nversion = "0.0.0"\ndependencies = [\n "anyhow",\n "mysql_async",'
    lock = replace(lock, old_package, '[[package]]\nname = "recastpress-core"\nversion = "0.5.0"\n\n'
        + old_package.replace(OLD_NAME, NAME) + '\n "recastpress-core",')
    require((HERE / 'Cargo.toml').read_bytes() == manifest.encode('utf-8'), 'cache_worker_manifest_derivation')
    require((HERE / 'Cargo.lock').read_bytes() == lock.encode('utf-8'), 'cache_worker_lock_derivation')
    pinned(CACHE / 'src/cache_fs.rs', 'c90fe2cbef8388580aeafdb249902ce1a07e70f1c275464b4c3fee922d9b6ead')
    cache_deriver = CACHE / 'derive_cache.py'
    pinned(cache_deriver, '9b12d23fa61d14a3070d2c7ca48cd58edc449d1178579552fb636e2db91481dd')
    import derive_guest
    guest = derive_guest.guest_files()
    require(set(guest) == {'bootstrap.php', 'cache-adapter.php', 'cache-trial.php', 'object-cache.php'},
        'cache_worker_guest_files')
    require(sha256(guest['object-cache.php']).hexdigest() == SHIM_SHA, 'cache_worker_shim_identity')
    return {'src/main.rs': worker(), 'src/catalog.rs': catalog(),
        'src/cache_host.rs': runpy.run_path(str(cache_deriver))['derived_bytes'](),
        **{'fixtures/' + name: wire for name, wire in guest.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    for name, wire in sorted(outputs().items()):
        path = HERE / name
        require(path.resolve() == path, 'cache_worker_output_alias')
        if path.exists():
            require(path.is_file() and path.read_bytes() == wire, 'cache_worker_generated_changed')
        else:
            require(not args.verify_only, 'cache_worker_generated_missing')
            path.parent.mkdir(exist_ok=True)
            with path.open('xb') as stream:
                stream.write(wire)
        print(f'{name} {sha256(wire).hexdigest()}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'Cache bootstrap worker derivation failed ({error})', file=sys.stderr)
        sys.exit(1)
