"""Generate the release HTTP adapter from the byte-pinned accepted session adapter."""

import argparse
from hashlib import sha256
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ACCEPTED = HERE.parent / 'php-wasix-wp-session-http-probe'
INPUTS = {
    'src/main.rs': '7030760e98a6f5cd1e7bd2596493707caccd88049fd73b4f817ed2293c67626d',
    'Cargo.toml': 'e006b408688f27947fa85d934c0f6f652232bd59e8d6b1a8c48999020fa9e359',
    'Cargo.lock': '8cce4a41334e5ffeac7414017d30f96883e58d8f63b08afc4910e44f2cf5f5c2',
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def pinned(name):
    path = ACCEPTED / name
    require(path.resolve(strict=True) == path and path.is_file(), 'release_http_accepted_input_path')
    wire = path.read_bytes()
    require(sha256(wire).hexdigest() == INPUTS[name], 'release_http_accepted_input_changed')
    return wire


def replace(source, previous, replacement, count=1):
    require(source.count(previous) == count, 'release_http_derivation_edit_count')
    return source.replace(previous, replacement)


RELEASE = '''fn worker_identity(release: &wordpress_release::VerifiedRelease) -> Result<Value> {
    let worker = release.artifact(WORKER).ok_or_else(|| anyhow::anyhow!("release_worker_missing"))?;
    Ok(json!({"manifest_sha256":release.manifest_sha256,"executable":WORKER,
        "executable_identity":{"bytes":worker.bytes,"sha256":worker.sha256}}))
}

fn capture_parent(path: PathBuf, release: &Path) -> Result<PathBuf> {
    let metadata = std::fs::symlink_metadata(&path)?;
    ensure!(metadata.is_dir() && path.canonicalize()? == path
        && !path.starts_with(release) && !release.starts_with(&path), "capture_parent");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        ensure!(metadata.permissions().mode() & 0o077 == 0, "capture_parent_permissions");
    }
    Ok(path)
}
'''


def derive():
    source = pinned('src/main.rs').decode('utf-8')
    for name in ('Cargo.toml', 'Cargo.lock'):
        expected = replace(pinned(name), b'recastpress-php-wasix-wp-session-http-probe',
            b'recastpress-php-wasix-wp-release-http-probe')
        require((HERE / name).read_bytes() == expected, 'release_http_dependency_derivation')
    source = replace(source, 'mod strict_json;\n', '''mod strict_json;
#[path = "../../../crates/recastpress-server/src/wordpress_release.rs"]
mod wordpress_release;
''')
    source = replace(source, 'path::{Component, Path, PathBuf}', 'path::{Path, PathBuf}')
    source = replace(source, '''const WORKER_RECEIPT: &str = "experiments/php-wasix-wp-session-probe/evidence/linux-build.json";
const WORKER: &str = "target/php-wasix-wp-bootstrap-probe/cargo-target/release/recastpress-php-wasix-wp-session-probe";
const WORKER_ANCHOR: &[u8] = include_bytes!("../../php-wasix-wp-session-probe/evidence/windows-build.json");''',
        'const WORKER: &str = "bin/worker";')
    start, end = source.index('fn fixed('), source.index('#[derive(Clone, Copy)]\nenum Rejection')
    source = replace(source, source[start:end], RELEASE)
    source = replace(source, '"target/php-wasix-managed-access-probe/php-fiber-cgi.wasm"', '"php/php.wasm"')
    for suffix, count in [('input', 2), ('ready', 1), ('observation', 2)]:
        source = replace(source, f'recastpress.php-wasix-wp-session-http-{suffix}.v0.5',
            f'recastpress.php-wasix-wp-release-http-{suffix}.v0.5', count)
    source = replace(source, 'worker_build', 'release_worker', 8)
    source = replace(source, '    root: PathBuf,\n', '    root: PathBuf,\n    release_manifest_sha256: String,\n')
    source = replace(source, 'let same = worker_identity(&app.root).is_ok_and(|after| after == *app.release_worker);',
        '''let same = wordpress_release::verify(&app.root, &app.release_manifest_sha256)
                .ok().and_then(|after| worker_identity(&after).ok())
                .is_some_and(|after| after == *app.release_worker);''')
    start, end = source.index('async fn serve('), source.index('    let mut secrets = ', source.index('async fn serve('))
    source = replace(source, source[start:end], '''async fn serve(database: Database, root: PathBuf, release_manifest_sha256: String,
    capture: PathBuf) -> Result<Value> {
    ensure!(cfg!(all(target_os = "linux", target_arch = "x86_64")), "server_linux_only");
    let release = wordpress_release::verify(&root, &release_manifest_sha256)
        .map_err(|_| anyhow::anyhow!("worker_startup_preflight"))?;
    let root = root.canonicalize()?;
    ensure!(std::env::current_exe()?.canonicalize()? == root.join("bin/http"), "worker_startup_preflight");
    let capture = capture_parent(capture, &root).map_err(|_| anyhow::anyhow!("worker_startup_preflight"))?;
    let release_worker = Arc::new(worker_identity(&release)?);
    let module_sha = &release.artifact(MODULE).ok_or_else(|| anyhow::anyhow!("worker_startup_preflight"))?.sha256;
''')
    source = replace(source, 'Worker::spawn(Command::new(root.join(WORKER)).env_clear())',
        'Worker::spawn(Command::new(root.join(WORKER)).env_clear().arg(&root).arg(&release_manifest_sha256).arg(&capture))')
    source = replace(source, 'ready_packet(&ready, &module_sha)', 'ready_packet(&ready, module_sha)')
    source = replace(source, '"port":port,"session_ready":ready}',
        '"port":port,"release_manifest_sha256":release_manifest_sha256,"session_ready":ready}')
    source = replace(source, 'let app = App {root, database,', 'let app = App {root, release_manifest_sha256, database,')
    source = replace(source, '"source_files_rehashed_by_service":false', '"release_files_verified_by_service":true')
    source = replace(source, '''        ensure!(std::env::args_os().count() == 1, "closed_arguments");
        serve(parse_input(&private_line(&mut std::io::stdin())?)?).await''',
        '''        let args = std::env::args_os().skip(1).collect::<Vec<_>>();
        let [root, digest, capture] = args.as_slice() else { anyhow::bail!("closed_arguments") };
        let digest = digest.to_str().ok_or_else(|| anyhow::anyhow!("closed_arguments"))?.to_owned();
        serve(parse_input(&private_line(&mut std::io::stdin())?)?, PathBuf::from(root), digest, PathBuf::from(capture)).await''')
    source = replace(source, 'root: PathBuf::new(), database:', 'root: PathBuf::new(), release_manifest_sha256: String::new(), database:')
    require(not any(marker in source for marker in ['CARGO_MANIFEST_DIR', 'WORKER_RECEIPT', 'WORKER_ANCHOR',
        'worker_build', 'target/php-wasix-']), 'release_http_repo_runtime_dependency')
    return source.encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    generated = HERE / 'src/main.rs'
    wire = derive()
    require(generated.resolve() == generated, 'release_http_generated_alias')
    if generated.exists():
        require(generated.is_file() and generated.read_bytes() == wire, 'release_http_generated_changed')
    else:
        require(not args.verify_only, 'release_http_generated_missing')
        generated.parent.mkdir(exist_ok=True)
        with generated.open('xb') as output:
            output.write(wire)
    print(sha256(wire).hexdigest())


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError):
        print('WordPress release HTTP derivation failed', file=sys.stderr)
        sys.exit(1)
