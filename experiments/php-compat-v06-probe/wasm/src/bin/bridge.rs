#![forbid(unsafe_code)]

//! Fixed PHP-WASIX JSONL bridge with an in-process Rust continuation peer.
//! Source, argv, environment, mounts and compiler profile are closed by this binary.

#[path = "../../../../../crates/recastpress-wp/examples/v06_bridge_peer/mod.rs"]
mod peer;

use std::fs::File;
use std::future::Future;
use std::io::{BufReader, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
#[cfg(all(not(feature = "llvm"), feature = "cranelift"))]
use wasmer_compiler_cranelift::{Cranelift, CraneliftOptLevel};
use wasmer_types::ModuleHash;
use wasmer_wasix::runtime::module_cache::HashedModuleData;
use wasmer_wasix::runtime::task_manager::tokio::TokioTaskManager;
use wasmer_wasix::virtual_fs::{
    DirEntry, FileOpener, FileSystem, FileType, Metadata, OpenOptions, OpenOptionsConfig, PipeRx,
    PipeTx, ReadDir, StaticFile,
};
use wasmer_wasix::{
    FsError, Pipe, PluggableRuntime, Runtime, UnsupportedVirtualNetworking, VirtualFile,
    runners::wasi::{RuntimeOrEngine, WasiRunner},
};

const ARTIFACT_RELATIVE: &str = ".reference/php-wasix-8.5.12/php.wasm";
const ARTIFACT_BYTES: u64 = 39_727_549;
const ARTIFACT_SHA256: &str = "e64850fde91d98dff0bce2b95ed467591376586469b1f4d8bad5c8759c4edbe4";
const BRIDGE_DRIVER: &[u8] = include_bytes!("../../../fixtures/bridge_driver.php");
const STRICT_JSON: &[u8] = include_bytes!("../../../fixtures/strict_json.php");
const SURFACE: &[u8] = include_bytes!("../../../fixtures/surface_oracle.php");
const DISPATCHER: &[u8] = include_bytes!("../../../fixtures/dispatcher.php");
const STRICT_ORACLE: &[u8] = include_bytes!("../../../fixtures/strict_json_oracle.php");
const OUTPUT_LIMIT: usize = 64 * 1024;
const SCENARIO_TIMEOUT: Duration = Duration::from_secs(120);
const GUEST_JOIN_TIMEOUT: Duration = Duration::from_secs(5);

#[cfg(feature = "llvm")]
const COMPILER_PROFILE: &str = "wasmer-7.2.0/llvm-default";
#[cfg(all(not(feature = "llvm"), feature = "cranelift"))]
const COMPILER_PROFILE: &str = "wasmer-7.2.0/cranelift-noopt";

const FILES: [(&str, &[u8]); 5] = [
    ("surface_oracle.php", SURFACE),
    ("dispatcher.php", DISPATCHER),
    ("strict_json.php", STRICT_JSON),
    ("strict_json_oracle.php", STRICT_ORACLE),
    ("bridge_driver.php", BRIDGE_DRIVER),
];

fn main() {
    if let Err(error) = run() {
        eprintln!("PHP_WASIX_V06_BRIDGE_ERROR={error:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    ensure!(
        std::env::args_os().len() == 1,
        "fixed bridge accepts no CLI arguments"
    );
    let hashes: Vec<_> = FILES
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

    eprintln!("PHP_WASIX_V06_BRIDGE_PHASE=module_compile");
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let guard = runtime.enter();
    let tasks = TokioTaskManager::new(runtime.handle().clone());
    let mut wasix = PluggableRuntime::new(Arc::new(tasks));
    wasix.set_engine(configured_engine());
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
    let surface = run_scenario(&module, module_hash, "surface")?;
    let throwable = run_scenario(&module, module_hash, "throwable")?;
    let execution_ms = execution_started.elapsed().as_millis();
    println!(
        "{}",
        json!({
            "schema":"recastpress.php-compat-v06-wasm-bridge.v1",
            "execution":"passed",
            "runtime":"PHP-WASIX 8.5.12 / Wasmer 7.2.0",
            "compiler_profile":COMPILER_PROFILE,
            "artifact":{"path":ARTIFACT_RELATIVE,"bytes":ARTIFACT_BYTES,"sha256":artifact_hash},
            "fixtures":hashes,
            "cases":{"surface":surface,"throwable":throwable},
            "compile_elapsed_ms":compile_ms,
            "execution_elapsed_ms":execution_ms,
            "scope":"fixed PHP JSONL bridge and Rust continuation API; no WordPress bootstrap or query implementation"
        })
    );
    Ok(())
}

fn run_scenario(module: &wasmer::Module, module_hash: ModuleHash, scenario: &str) -> Result<Value> {
    eprintln!("PHP_WASIX_V06_BRIDGE_PHASE=guest_{scenario}");
    // One full duplex channel: host peer owns one end; WASIX stdin and stdout
    // share the other end, while stderr is an independent bounded observation.
    let (host_wire, guest_wire) = Pipe::channel();
    let (mut host_stdin, host_stdout) = host_wire.split();
    let guest_stdin = guest_wire.clone();
    let guest_stdout = guest_wire;
    let (mut host_stderr, guest_stderr) = Pipe::channel();
    let (stderr_done_tx, stderr_done_rx) = mpsc::channel();
    let stderr_thread = thread::Builder::new()
        .name(format!("php-wasix-{scenario}-stderr"))
        .spawn(move || {
            let result = drain_stderr(&mut host_stderr);
            let _ = stderr_done_tx.send(result);
        })?;

    let (done_tx, done_rx) = mpsc::channel();
    let module = module.clone();
    let scenario_owned = scenario.to_owned();
    let guest_thread = thread::Builder::new()
        .name(format!("php-wasix-{scenario}"))
        .spawn(move || {
            let result = run_guest(
                &module,
                module_hash,
                &scenario_owned,
                guest_stdin,
                guest_stdout,
                guest_stderr,
            );
            let _ = done_tx.send(result);
        })?;

    let peer_started = Instant::now();
    let mut reader = BufReader::new(DeadlinePipeReader {
        pipe: host_stdout,
        deadline: peer_started + SCENARIO_TIMEOUT,
    });
    let (peer_result, peer_elapsed) = {
        let mut writer = DeadlinePipeWriter {
            pipe: &mut host_stdin,
        };
        let result = peer::run_peer(&mut reader, &mut writer, scenario)
            .map_err(|e| anyhow::anyhow!("Rust peer scenario {scenario} failed: {e}"));
        (result, peer_started.elapsed())
    };
    host_stdin.close();
    if peer_result.is_err() {
        host_stdin.close();
    }

    let guest_result = match done_rx.recv_timeout(GUEST_JOIN_TIMEOUT) {
        Ok(result) => result,
        Err(mpsc::RecvTimeoutError::Timeout) => {
            host_stdin.close();
            anyhow::bail!(
                "guest completion deadline exceeded; cancellation requested; outer process deadline is required for a CPU-bound guest"
            )
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => anyhow::bail!("guest supervisor disconnected"),
    };
    guest_thread
        .join()
        .map_err(|_| anyhow::anyhow!("guest runner thread panicked"))?;
    let peer = peer_result?;
    guest_result?;
    let (stderr, stderr_overflow) = stderr_done_rx
        .recv_timeout(GUEST_JOIN_TIMEOUT)
        .context("PHP stderr drainer did not complete")??;
    stderr_thread
        .join()
        .map_err(|_| anyhow::anyhow!("PHP stderr drainer thread panicked"))?;
    ensure!(!stderr_overflow, "PHP stderr exceeded fixed capture bound");
    ensure!(
        stderr.is_empty(),
        "PHP bridge wrote stderr: {}",
        String::from_utf8_lossy(&stderr)
    );
    Ok(json!({
        "status":"passed",
        "php_exit_code":0,
        "guest_completed":true,
        "thread_joined":true,
        "peer_elapsed_ms":peer_elapsed.as_millis(),
        "peer":peer
    }))
}

fn drain_stderr(pipe: &mut Pipe) -> Result<(Vec<u8>, bool)> {
    let mut captured = Vec::new();
    let mut overflow = false;
    let mut buffer = [0u8; 4096];
    loop {
        while let Some(n) = pipe.try_read(&mut buffer) {
            if n == 0 {
                return Ok((captured, overflow));
            }
            let remaining = OUTPUT_LIMIT.saturating_sub(captured.len());
            let keep = n.min(remaining);
            captured.extend_from_slice(&buffer[..keep]);
            overflow |= keep < n;
        }
        thread::sleep(Duration::from_millis(1));
    }
}

fn run_guest(
    module: &wasmer::Module,
    module_hash: ModuleHash,
    scenario: &str,
    stdin: Pipe,
    stdout: Pipe,
    stderr: Pipe,
) -> Result<()> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let _guard = runtime.enter();
    let tasks = TokioTaskManager::new(runtime.handle().clone());
    let mut wasix = PluggableRuntime::new(Arc::new(tasks));
    wasix.set_engine(configured_engine());
    wasix.set_networking_implementation(UnsupportedVirtualNetworking::default());
    wasix.http_client = None;
    let mut runner = WasiRunner::new();
    runner
        .with_forward_host_env(false)
        .with_home_mapped(false)
        .with_asynchronous_threading(false)
        .with_stdin(Box::new(stdin))
        .with_stdout(Box::new(stdout))
        .with_stderr(Box::new(stderr))
        .with_args(["-n", "/fixture/bridge_driver.php", scenario])
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
        .with_context(|| format!("run fixed PHP bridge scenario {scenario}"))?;
    drop(runner);
    drop(_guard);
    runtime.shutdown_timeout(Duration::from_secs(2));
    Ok(())
}

struct DeadlinePipeReader {
    pipe: PipeRx,
    deadline: Instant,
}
impl Read for DeadlinePipeReader {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        loop {
            match self.pipe.try_read(buf) {
                Some(n) => return Ok(n),
                None if Instant::now() < self.deadline => thread::sleep(Duration::from_millis(1)),
                None => {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::TimedOut,
                        "bridge pipe deadline",
                    ));
                }
            }
        }
    }
}

struct DeadlinePipeWriter<'a> {
    pipe: &'a mut PipeTx,
}
impl Write for DeadlinePipeWriter<'_> {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.pipe.write(buf)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.pipe.flush()
    }
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
fn metadata(is_file: bool, len: usize) -> Metadata {
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
                    metadata: Ok(metadata(true, bytes.len())),
                })
                .collect(),
        ))
    }
    fn metadata(&self, path: &Path) -> std::result::Result<Metadata, FsError> {
        Ok(match lookup(path)? {
            Some((_, bytes)) => metadata(true, bytes.len()),
            None => metadata(false, 0),
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

#[cfg(feature = "llvm")]
fn configured_engine() -> wasmer::Engine {
    wasmer_compiler_llvm::LLVM::default().into()
}
#[cfg(all(not(feature = "llvm"), feature = "cranelift"))]
fn configured_engine() -> wasmer::Engine {
    let mut config = Cranelift::default();
    config.opt_level(CraneliftOptLevel::None);
    config.into()
}
