"""Derive the relocatable worker from the accepted compile-once session source."""

import argparse
import hashlib
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SESSION = ROOT / 'experiments/php-wasix-wp-session-probe'
CATALOG = ROOT / 'experiments/php-wasix-wp-request-probe/src/catalog.rs'
NAME = b'recastpress-php-wasix-wp-release-worker-probe'
SESSION_NAME = b'recastpress-php-wasix-wp-session-probe'
PINS = {
    SESSION / 'src/main.rs': '3f3992532c100d44e1b1d1897d4003ea7fc6a7fc23406b8ca923f11969829bb0',
    CATALOG: '6f6ec144e47dba2612b0976aea13ad6b01cfa6f1e67a925b7f3289aeb8d8aaf7',
    SESSION / 'Cargo.toml': '3cca91ad9ecb71e2d521a7eac654cc0c51a54529f9b8dd86e153444bbf24aee4',
    SESSION / 'Cargo.lock': '4da0d5b209827c5004abd30847aec84e3c7cee1b4a45b8eace6bf8d41e3b0227',
}


def source(path):
    wire = path.read_bytes()
    if hashlib.sha256(wire).hexdigest() != PINS[path]:
        raise ValueError('accepted_worker_source_changed')
    return wire


def once(value, old, new):
    if value.count(old) != 1:
        raise ValueError('worker_derivation_expected_one_match')
    return value.replace(old, new, 1)


def section(value, start, end, replacement):
    if value.count(start) != 1 or value.count(end) != 1:
        raise ValueError('worker_derivation_section_markers')
    first, last = value.index(start), value.index(end)
    if first >= last:
        raise ValueError('worker_derivation_section_order')
    return value[:first] + replacement + value[last:]


RELEASE = '''const MODULE: &str = "php/php.wasm";
const CATALOG: &str = "wordpress/source-manifest.json";

struct Release {
    root: PathBuf,
    verified: wordpress_release::VerifiedRelease,
}

impl Release {
    fn open(root: &Path, expected_sha256: &str) -> Result<Self> {
        let verified = wordpress_release::verify(root, expected_sha256)?;
        let root = root.canonicalize()?;
        let executable = std::env::current_exe()?.canonicalize()?;
        ensure!(executable == root.join("bin/worker"), "worker_not_in_release");
        Ok(Self { root, verified })
    }

    fn read(&self, name: &str, limit: u64) -> Result<Vec<u8>> {
        let expected = self.verified.artifact(name).context("unknown_release_artifact")?;
        ensure!(expected.bytes <= limit, "release_artifact_limit");
        let bytes = fixed(&self.root, name, expected.bytes)?;
        ensure!(bytes.len() as u64 == expected.bytes && db::sha(&bytes) == expected.sha256,
            "release_artifact_changed");
        Ok(bytes)
    }
}

'''

LOAD_MODULE = '''fn load_module(release: &Release, report: &mut Value) -> Result<Vec<u8>> {
    let bytes = release.read(MODULE, 128 * 1024 * 1024)?;
    ensure!(bytes.starts_with(b"\\0asm\\x01\\0\\0\\0"), "release_wasm_header");
    report["module"] = json!({"path":MODULE,"bytes":bytes.len(),"sha256":db::sha(&bytes),"verified":true});
    report["fiber_adapter"] = fiber_adapter::inspect(&bytes)?;
    Ok(bytes)
}
'''

LOAD_SOURCES = '''fn load_sources(release: &Release, report: &mut Value) -> Result<(SourceFs, SourceFs, Vec<u8>)> {
    let mut fixtures = BTreeMap::new();
    let mut dropin = None;
    for (name, expected) in OWNED {
        let bytes = release.read(&format!("php/{name}"), 256 * 1024)?;
        ensure!(bytes == *expected, "compiled_fixture_binding");
        if *name == "db.php" {
            dropin = Some(Arc::<[u8]>::from(bytes));
        } else {
            fixtures.insert((*name).to_owned(), Arc::<[u8]>::from(bytes));
        }
    }
    let manifest = release.read(CATALOG, 2 * 1024 * 1024)?;
    let wp = catalog::load(&release.root, &mut report["core_source"], dropin.context("dropin_missing")?)
        .map_err(|error| anyhow::anyhow!(error))?;
    report["prepared"] = json!({"verification_scope":"compiled_fixtures_and_core_only",
        "source_verified":true,"transitive_inputs_verified_by_worker":false,
        "core_files":3782,"owned_dropin_files":1});
    Ok((finite(fixtures)?, wp, manifest))
}
'''

CAPTURE = '''struct CaptureDir { path: PathBuf }
impl CaptureDir {
    fn create(parent: &Path, release: &Release) -> Result<Self> {
        ensure!(std::fs::symlink_metadata(parent)?.is_dir(), "capture_parent_type");
        let parent = parent.canonicalize()?;
        ensure!(!parent.starts_with(&release.root) && !release.root.starts_with(&parent),
            "capture_parent_overlaps_release");
        #[cfg(unix)] {
            use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
            ensure!(std::fs::metadata(&parent)?.permissions().mode() & 0o077 == 0,
                "capture_parent_not_private");
            let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)?.as_nanos();
            let path = parent.join(format!("{}-{stamp}", std::process::id()));
            std::fs::DirBuilder::new().mode(0o700).create(&path)?;
            ensure!(path.canonicalize()? == path, "session_capture_alias");
            Ok(Self { path })
        }
        #[cfg(not(unix))]
        anyhow::bail!("capture_platform")
    }
}
'''

ARGUMENTS = '''        ensure!(cfg!(all(target_os = "linux", target_arch = "x86_64")), "release_platform");
        let args: Vec<_> = std::env::args_os().skip(1).collect();
        let [root, digest, capture_parent] = args.as_slice() else { anyhow::bail!("release_arguments"); };
        let release = Release::open(Path::new(root), digest.to_str().context("release_manifest_digest")?)?;
        let capture_dir = CaptureDir::create(Path::new(capture_parent), &release)?;
        run_session(&release,&capture_dir,&mut std::io::stdin().lock(),&mut std::io::stdout().lock())'''


def worker_source():
    text = source(SESSION / 'src/main.rs').decode('utf-8')
    text = once(text, '#[path = "../../php-wasix-wp-request-probe/src/catalog.rs"]\nmod catalog;',
        'mod catalog;\n#[allow(dead_code)]\n#[path = "../../../crates/recastpress-server/src/wordpress_release.rs"]\nmod wordpress_release;')
    text = once(text, '#[allow(dead_code)]\n#[path = "../../php-wasix-wpdb-probe/src/inputs.rs"]\nmod inputs;\n', '')
    text = section(text, 'const MODULE:', '// Preserve the accepted managed-access namespace exactly.', RELEASE)
    text = section(text, 'fn load_module(', 'fn finite(', LOAD_MODULE)
    text = section(text, 'fn load_sources(', 'fn environment(', LOAD_SOURCES)
    text = section(text, 'struct CaptureDir {', 'fn private_capture(', CAPTURE)
    text = once(text, 'fn startup(root: &Path, report: &mut Value)', 'fn startup(release: &Release, report: &mut Value)')
    text = once(text, 'let bytes = load_module(root,report)?;', 'let bytes = load_module(release,report)?;')
    text = once(text, 'candidate.verify(root,report)?;', 'candidate.verify(release,report)?;')
    text = once(text, 'fn verify(&self, root: &Path, report: &mut Value)', 'fn verify(&self, release: &Release, report: &mut Value)')
    text = once(text, 'db::sha(&load_module(root,report)?)', 'db::sha(&load_module(release,report)?)')
    text = once(text, 'fn probe(input:', 'fn probe(release: &Release, input:')
    text = once(text, '    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize()?;\n', '')
    for old, new in [
        ('    compiled.verify(&root,report)?;\n    let (php,wp,manifest) = load_sources(&root,report)?;',
         '    compiled.verify(release,report)?;\n    let (php,wp,manifest) = load_sources(release,report)?;'),
        ('    compiled.verify(&root,report)?;\n    let _ = load_sources(&root,report)?;',
         '    compiled.verify(release,report)?;\n    let _ = load_sources(release,report)?;'),
        ('        &root,\n        &compiled.engine,', '        &release.root,\n        &compiled.engine,'),
        ('        compiled.verify(&root,report)?;\n        let _ = load_sources(&root, report)?;',
         '        compiled.verify(release,report)?;\n        let _ = load_sources(release, report)?;'),
        ('fn run_session(read:&mut impl Read,output:&mut impl Write)->Result<bool> {\n'
         '    let root=Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize()?;',
         'fn run_session(release:&Release,capture_dir:&CaptureDir,read:&mut impl Read,output:&mut impl Write)->Result<bool> {'),
        ('    let compiled=CompiledCandidate::startup(&root,&mut startup)?;\n'
         '    let _=load_sources(&root,&mut startup)?;\n    let capture_dir=CaptureDir::create(&root)?;',
         '    let compiled=CompiledCandidate::startup(release,&mut startup)?;\n'
         '    let _=load_sources(release,&mut startup)?;'),
        ('probe(input,case,&compiled,seq,&capture_dir,&mut report)',
         'probe(release,input,case,&compiled,seq,capture_dir,&mut report)'),
        ('compiled.verify(&root,&mut verified)?;\n        let _=load_sources(&root,&mut verified)?;',
         'compiled.verify(release,&mut verified)?;\n        let _=load_sources(release,&mut verified)?;'),
        ('        ensure!(std::env::args_os().count()==1,"closed_arguments");\n'
         '        run_session(&mut std::io::stdin().lock(),&mut std::io::stdout().lock())', ARGUMENTS),
    ]:
        text = once(text, old, new)
    if 'CARGO_MANIFEST_DIR' in text or 'target/' in text:
        raise ValueError('worker_runtime_checkout_path_retained')
    return text.encode()


def catalog_source():
    wire = source(CATALOG)
    wire = once(wire, b'root.join("target/wp-bootstrap-probe/source-manifest.json")',
        b'root.join("wordpress/source-manifest.json")')
    return once(wire, b'root.join("target/wp-bootstrap-probe/site")', b'root.join("wordpress/site")')


def derive(verify_only):
    for name in ('Cargo.toml', 'Cargo.lock'):
        if (HERE / name).read_bytes() != once(source(SESSION / name), SESSION_NAME, NAME):
            raise ValueError('worker_dependency_derivation_changed')
    for name, wire in [('main.rs', worker_source()), ('catalog.rs', catalog_source())]:
        path = HERE / 'src' / name
        if verify_only:
            if path.read_bytes() != wire:
                raise ValueError('generated_worker_source_changed')
        else:
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(wire)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    derive(parser.parse_args().verify_only)
