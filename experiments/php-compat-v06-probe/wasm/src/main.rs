#![forbid(unsafe_code)]

//! Fixed PHP-WASIX surface-oracle runner for the v0.6 compatibility probe.
//! No arbitrary PHP source, script path, args, env, network, or host mount.

use std::fs::File;
use std::future::Future;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use wasmer_types::ModuleHash;
use wasmer_wasix::runtime::module_cache::HashedModuleData;
use wasmer_wasix::runtime::task_manager::tokio::TokioTaskManager;
use wasmer_wasix::virtual_fs::{
    DirEntry, FileOpener, FileSystem, FileType, Metadata, OpenOptions, OpenOptionsConfig, ReadDir,
    StaticFile,
};
use wasmer_wasix::{
    FsError, Pipe, PluggableRuntime, Runtime, UnsupportedVirtualNetworking, VirtualFile,
    runners::wasi::{RuntimeOrEngine, WasiRunner},
};

const ARTIFACT_RELATIVE: &str = ".reference/php-wasix-8.5.12/php.wasm";
const ARTIFACT_BYTES: u64 = 39_727_549;
const ARTIFACT_SHA256: &str = "e64850fde91d98dff0bce2b95ed467591376586469b1f4d8bad5c8759c4edbe4";
const GUEST_PATH: &str = "/fixture/surface_oracle.php";
const PHP_FIXTURE: &[u8] = include_bytes!("../../fixtures/surface_oracle.php");
const DISPATCHER: &[u8] = include_bytes!("../../fixtures/dispatcher.php");
const STRICT_JSON: &[u8] = include_bytes!("../../fixtures/strict_json.php");
const STRICT_JSON_ORACLE: &[u8] = include_bytes!("../../fixtures/strict_json_oracle.php");
const FIXTURE_SHA256: &str = "206f97b74578014c24f7539b521745b3df2b507aaecc83c9c8f861b4a4be85e8";
const STRICT_ORACLE_SHA256: &str =
    "cc84086632068e7aa837cca5d4fff3de2c14684c279a1cec4bd39b509069d7e8";
const DISPATCHER_SHA256: &str = "829a2d9dcfe47b3e4799642046cd539f3601ab678656aee6b2e02de1115118d6";
const STRICT_JSON_SHA256: &str = "d93100db6809c2310322f1a0ea6de25be3175e7a2de47b210389495bda17a565";
const OUTPUT_LIMIT: usize = 64 * 1024;
const FILES: [(&str, &[u8]); 4] = [
    ("surface_oracle.php", PHP_FIXTURE),
    ("dispatcher.php", DISPATCHER),
    ("strict_json.php", STRICT_JSON),
    ("strict_json_oracle.php", STRICT_JSON_ORACLE),
];

fn main() {
    if let Err(error) = run() {
        eprintln!("PHP_WASIX_V06_ERROR={error:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    ensure!(
        std::env::args_os().len() == 1,
        "fixed probe accepts no CLI arguments"
    );
    let fixture_hash = format!("{:x}", Sha256::digest(PHP_FIXTURE));
    ensure!(
        fixture_hash == FIXTURE_SHA256,
        "surface oracle source hash mismatch"
    );
    let strict_oracle_hash = format!("{:x}", Sha256::digest(STRICT_JSON_ORACLE));
    ensure!(
        strict_oracle_hash == STRICT_ORACLE_SHA256,
        "strict JSON oracle source hash mismatch"
    );
    ensure!(
        format!("{:x}", Sha256::digest(DISPATCHER)) == DISPATCHER_SHA256,
        "dispatcher source hash mismatch"
    );
    ensure!(
        format!("{:x}", Sha256::digest(STRICT_JSON)) == STRICT_JSON_SHA256,
        "strict JSON utility source hash mismatch"
    );
    let source_hashes: Vec<_> = FILES
        .iter()
        .map(|(name, bytes)| (*name, bytes.len(), format!("{:x}", Sha256::digest(bytes))))
        .collect();

    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let path = root.join(ARTIFACT_RELATIVE);
    let file = File::open(&path).context("open pinned PHP WASIX artifact")?;
    ensure!(
        file.metadata()?.len() == ARTIFACT_BYTES,
        "PHP Wasm artifact byte length mismatch"
    );
    let mut bytes = Vec::with_capacity(ARTIFACT_BYTES as usize);
    file.take(ARTIFACT_BYTES + 1).read_to_end(&mut bytes)?;
    ensure!(
        bytes.len() as u64 == ARTIFACT_BYTES,
        "PHP Wasm artifact changed during read"
    );
    let artifact_hash = format!("{:x}", Sha256::digest(&bytes));
    ensure!(
        artifact_hash == ARTIFACT_SHA256,
        "PHP Wasm artifact SHA-256 mismatch"
    );

    eprintln!("PHP_WASIX_V06_PHASE=module_compile");
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let guard = runtime.enter();
    let tasks = TokioTaskManager::new(runtime.handle().clone());
    let mut wasix = PluggableRuntime::new(Arc::new(tasks));
    wasix.set_engine(wasmer_compiler_cranelift::Cranelift::default().into());
    wasix.set_networking_implementation(UnsupportedVirtualNetworking::default());
    wasix.http_client = None;
    let data = HashedModuleData::new(bytes);
    let module_hash = *data.hash();
    let compile_started = Instant::now();
    let module = wasix
        .load_hashed_module_sync(data, None)
        .context("compile fixed PHP WASIX artifact")?;
    let compile_ms = compile_started.elapsed().as_millis();

    drop(guard);
    runtime.shutdown_timeout(Duration::from_secs(2));
    let execution_started = Instant::now();
    eprintln!("PHP_WASIX_V06_PHASE=php_execution_surface_oracle");
    let output = execute(&module, module_hash, GUEST_PATH)?;
    ensure!(
        output["oracle"] == "passed" && output["checks"] == 15,
        "PHP Wasm surface oracle mismatch: {output}"
    );
    eprintln!("PHP_WASIX_V06_PHASE=php_execution_strict_json_oracle");
    let strict_output = execute(&module, module_hash, "/fixture/strict_json_oracle.php")?;
    ensure!(
        strict_output["oracle"] == "strict_json_unique_keys"
            && strict_output["checks"].as_u64().is_some(),
        "PHP Wasm strict JSON oracle mismatch: {strict_output}"
    );
    let execution_ms = execution_started.elapsed().as_millis();
    println!(
        "{}",
        json!({
            "schema":"recastpress.php-compat-v06-wasm-surface.v1",
            "execution":"passed",
            "runtime":"PHP-WASIX 8.5.12 / Wasmer 7.2.0 / Cranelift",
            "artifact":{"path":ARTIFACT_RELATIVE,"bytes":ARTIFACT_BYTES,"sha256":artifact_hash},
            "fixtures":source_hashes,
            "cases":{"surface_oracle":{"status":"passed","result":output},"strict_json_oracle":{"status":"passed","result":strict_output}},
            "compile_elapsed_ms":compile_ms,
            "execution_elapsed_ms":execution_ms,
            "scope":"PHP-language surface only; no WordPress bootstrap or WP algorithm port"
        })
    );
    Ok(())
}

fn collect(mut pipe: Pipe) -> Result<Vec<u8>> {
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut out = Vec::new();
    let mut buf = [0; 4096];
    loop {
        match pipe.try_read(&mut buf) {
            Some(0) => return Ok(out),
            Some(count) => {
                ensure!(
                    count <= OUTPUT_LIMIT.saturating_sub(out.len()),
                    "PHP output exceeded fixed capture bound"
                );
                out.extend_from_slice(&buf[..count]);
            }
            None if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(1)),
            None => anyhow::bail!("PHP output pipe did not close before cleanup deadline"),
        }
    }
}

fn execute(module: &wasmer::Module, module_hash: ModuleHash, guest_path: &str) -> Result<Value> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let _guard = runtime.enter();
    let tasks = TokioTaskManager::new(runtime.handle().clone());
    let mut wasix = PluggableRuntime::new(Arc::new(tasks));
    wasix.set_engine(wasmer_compiler_cranelift::Cranelift::default().into());
    wasix.set_networking_implementation(UnsupportedVirtualNetworking::default());
    wasix.http_client = None;
    let (_stdin_tx, stdin_rx) = Pipe::channel();
    drop(_stdin_tx);
    let (stdout_tx, stdout_rx) = Pipe::channel();
    let (stderr_tx, stderr_rx) = Pipe::channel();
    let mut runner = WasiRunner::new();
    runner
        .with_forward_host_env(false)
        .with_home_mapped(false)
        .with_asynchronous_threading(false)
        .with_stdin(Box::new(stdin_rx))
        .with_stdout(Box::new(stdout_tx))
        .with_stderr(Box::new(stderr_tx))
        .with_args(["-n", guest_path])
        .with_current_dir("/fixture")
        .with_mount("/fixture".to_owned(), Arc::new(FixedFilesFs))
        .capabilities_mut()
        .insecure_allow_all = false;
    runner.capabilities_mut().threading.max_threads = Some(1);
    runner.capabilities_mut().threading.enable_deep_sleep = false;
    runner
        .run_wasm(
            RuntimeOrEngine::Runtime(Arc::new(wasix)),
            "php",
            module.clone(),
            module_hash,
        )
        .with_context(|| format!("run fixed PHP fixture {guest_path}"))?;
    drop(runner);
    drop(_guard);
    runtime.shutdown_timeout(Duration::from_secs(2));
    let stdout = collect(stdout_rx)?;
    let stderr = collect(stderr_rx)?;
    ensure!(
        stderr.is_empty(),
        "PHP fixture wrote stderr: {}",
        String::from_utf8_lossy(&stderr)
    );
    Ok(serde_json::from_slice(&stdout).context("decode PHP fixture oracle output")?)
}

#[derive(Debug)]
struct FixedFilesFs;

fn lookup(path: &Path) -> std::result::Result<Option<(&'static str, &'static [u8])>, FsError> {
    let mut name = None;
    for part in path.components() {
        match part {
            Component::RootDir | Component::CurDir => {}
            Component::Normal(value) if name.is_none() => name = Some(value),
            _ => return Err(FsError::EntryNotFound),
        }
    }
    match name {
        None => Ok(None),
        Some(name) => FILES
            .iter()
            .find(|(allowed, _)| name == *allowed)
            .copied()
            .map(Some)
            .ok_or(FsError::EntryNotFound),
    }
}

fn file_metadata(is_file: bool, len: usize) -> Metadata {
    Metadata {
        ft: FileType {
            file: is_file,
            dir: !is_file,
            ..FileType::default()
        },
        accessed: 0,
        created: 0,
        modified: 0,
        len: if is_file { len as u64 } else { 0 },
    }
}

impl FileOpener for FixedFilesFs {
    fn open(
        &self,
        path: &Path,
        options: &OpenOptionsConfig,
    ) -> std::result::Result<Box<dyn VirtualFile + Send + Sync>, FsError> {
        if options.would_mutate() {
            return Err(FsError::PermissionDenied);
        }
        let Some((_, bytes)) = lookup(path)? else {
            return Err(FsError::NotAFile);
        };
        Ok(Box::new(StaticFile::new(bytes.to_vec())))
    }
}

impl FileSystem for FixedFilesFs {
    fn readlink(&self, path: &Path) -> std::result::Result<PathBuf, FsError> {
        lookup(path)?;
        Err(FsError::InvalidInput)
    }
    fn read_dir(&self, path: &Path) -> std::result::Result<ReadDir, FsError> {
        if lookup(path)?.is_some() {
            return Err(FsError::BaseNotDirectory);
        }
        Ok(ReadDir::new(
            FILES
                .iter()
                .map(|(name, bytes)| DirEntry {
                    path: PathBuf::from(format!("/{name}")),
                    metadata: Ok(file_metadata(true, bytes.len())),
                })
                .collect(),
        ))
    }
    fn metadata(&self, path: &Path) -> std::result::Result<Metadata, FsError> {
        Ok(match lookup(path)? {
            Some((_, bytes)) => file_metadata(true, bytes.len()),
            None => file_metadata(false, 0),
        })
    }
    fn symlink_metadata(&self, path: &Path) -> std::result::Result<Metadata, FsError> {
        self.metadata(path)
    }
    fn new_open_options(&self) -> OpenOptions<'_> {
        OpenOptions::new(self)
    }
    fn create_dir(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn create_symlink(&self, _: &Path, _: &Path) -> std::result::Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn hard_link(&self, _: &Path, _: &Path) -> std::result::Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn remove_dir(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn remove_file(&self, _: &Path) -> std::result::Result<(), FsError> {
        Err(FsError::PermissionDenied)
    }
    fn rename<'a>(
        &'a self,
        _: &'a Path,
        _: &'a Path,
    ) -> Pin<Box<dyn Future<Output = std::result::Result<(), FsError>> + Send + 'a>> {
        Box::pin(async { Err(FsError::PermissionDenied) })
    }
}
