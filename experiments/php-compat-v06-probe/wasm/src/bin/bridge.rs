#![forbid(unsafe_code)]

//! Fixed PHP-WASIX JSONL bridge with an in-process Rust continuation peer.
//! Source, argv, environment, mounts and compiler profile are closed by this binary.

#[path = "../../../../../crates/recastpress-wp/examples/v06_bridge_peer/mod.rs"]
mod peer;

use std::fmt;
use std::fs::File;
use std::future::Future;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, ensure};
use recastpress_wp::continuation::{
    Completion, Continuation, ContinuationError, LimitsProfile, RequestGeneration, RequestState,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use wasmer::sys::CompilerConfig;
use wasmer::wasmparser::Operator;
use wasmer::{AsStoreMut, Global, GlobalType, Instance, Mutability, StoreMut, Type};
#[cfg(all(not(feature = "llvm"), feature = "cranelift"))]
use wasmer_compiler_cranelift::{Cranelift, CraneliftOptLevel};
use wasmer_middlewares::Metering;
use wasmer_types::{ModuleHash, TrapCode};
use wasmer_wasix::VirtualTaskManager;
use wasmer_wasix::WasiThreadError;
use wasmer_wasix::http::DynHttpClient;
use wasmer_wasix::os::TtyBridge;
use wasmer_wasix::runtime::module_cache::HashedModuleData;
use wasmer_wasix::runtime::module_cache::ModuleCache;
use wasmer_wasix::runtime::package_loader::PackageLoader;
use wasmer_wasix::runtime::resolver::Source;
use wasmer_wasix::runtime::task_manager::tokio::TokioTaskManager;
use wasmer_wasix::runtime::task_manager::{SpawnType, TaskWasm, TaskWasmRecycleProperties};
use wasmer_wasix::virtual_fs::{
    DirEntry, FileOpener, FileSystem, FileType, Metadata, OpenOptions, OpenOptionsConfig, PipeRx,
    PipeTx, ReadDir, StaticFile,
};
use wasmer_wasix::virtual_net::DynVirtualNetworking;
use wasmer_wasix::{
    FsError, Pipe, PluggableRuntime, Runtime, UnsupportedVirtualNetworking, VirtualFile,
    runners::wasi::{RuntimeOrEngine, WasiRunner},
    runtime::TaintReason,
};

const ARTIFACT_RELATIVE: &str = ".reference/php-wasix-8.5.12/php.wasm";
const ARTIFACT_BYTES: u64 = 39_727_549;
const ARTIFACT_SHA256: &str = "e64850fde91d98dff0bce2b95ed467591376586469b1f4d8bad5c8759c4edbe4";
const BRIDGE_DRIVER: &[u8] = include_bytes!("../../../fixtures/bridge_driver.php");
const PUBLISH_DRIVER: &[u8] = include_bytes!("../../../fixtures/publish_driver.php");
const HANDLE_DRIVER: &[u8] = include_bytes!("../../../fixtures/handle_driver.php");
const HOOK_DRIVER: &[u8] = include_bytes!("../../../fixtures/hooks_driver.php");
const HANDLE_REGISTRY: &[u8] = include_bytes!("../../../fixtures/handle_registry.php");
const STRICT_JSON: &[u8] = include_bytes!("../../../fixtures/strict_json.php");
const SURFACE: &[u8] = include_bytes!("../../../fixtures/surface_oracle.php");
const DISPATCHER: &[u8] = include_bytes!("../../../fixtures/dispatcher.php");
const STRICT_ORACLE: &[u8] = include_bytes!("../../../fixtures/strict_json_oracle.php");
const RECOVERY: &[u8] = include_bytes!("../../../fixtures/recovery.php");
const TRAP_RECOVERY: &[u8] = include_bytes!("../../../fixtures/trap_recovery.php");
const OUTPUT_LIMIT: usize = 64 * 1024;
const SCENARIO_TIMEOUT: Duration = Duration::from_secs(120);
const GUEST_JOIN_TIMEOUT: Duration = Duration::from_secs(5);

#[cfg(feature = "llvm")]
const COMPILER_PROFILE: &str = "wasmer-7.2.0/llvm-default";
#[cfg(all(not(feature = "llvm"), feature = "cranelift"))]
const COMPILER_PROFILE: &str = "wasmer-7.2.0/cranelift-noopt";

const FILES: [(&str, &[u8]); 7] = [
    ("surface_oracle.php", SURFACE),
    ("dispatcher.php", DISPATCHER),
    ("strict_json.php", STRICT_JSON),
    ("strict_json_oracle.php", STRICT_ORACLE),
    ("bridge_driver.php", BRIDGE_DRIVER),
    ("recovery.php", RECOVERY),
    ("handle_registry.php", HANDLE_REGISTRY),
];
const TRAP_FILES: [(&str, &[u8]); 1] = [("trap_recovery.php", TRAP_RECOVERY)];
const PUBLISH_FILES: [(&str, &[u8]); 8] = [
    FILES[0],
    FILES[1],
    FILES[2],
    FILES[3],
    FILES[4],
    FILES[5],
    FILES[6],
    ("publish_driver.php", PUBLISH_DRIVER),
];
const HANDLE_FILES: [(&str, &[u8]); 8] = [
    FILES[0],
    FILES[1],
    FILES[2],
    FILES[3],
    FILES[4],
    FILES[5],
    FILES[6],
    ("handle_driver.php", HANDLE_DRIVER),
];
const HOOK_FILES: [(&str, &[u8]); 8] = [
    FILES[0],
    FILES[1],
    FILES[2],
    FILES[3],
    FILES[4],
    FILES[5],
    FILES[6],
    ("hooks_driver.php", HOOK_DRIVER),
];
const TRAP_POINTS: u64 = 100_000_000;

#[derive(Debug, Default)]
struct TrapObservation {
    runtime_traps: Vec<Option<TrapCode>>,
    post_start: Option<Value>,
    final_points: Option<Value>,
    observation_error: Option<String>,
    globals: Option<(Global, Global)>,
    setup_count: usize,
    recycle_count: usize,
}

type MeterState = Arc<std::sync::Mutex<TrapObservation>>;

fn capture_meter_globals(store: &mut StoreMut, instance: &Instance) -> Result<(Global, Global)> {
    let remaining = instance
        .exports
        .get_global("wasmer_metering_remaining_points")?
        .clone();
    let exhausted = instance
        .exports
        .get_global("wasmer_metering_points_exhausted")?
        .clone();
    ensure!(
        remaining.ty(store) == GlobalType::new(Type::I64, Mutability::Var),
        "meter remaining global type"
    );
    ensure!(
        exhausted.ty(store) == GlobalType::new(Type::I32, Mutability::Var),
        "meter exhausted global type"
    );
    Ok((remaining, exhausted))
}

fn read_meter_globals(store: &mut impl AsStoreMut, globals: &(Global, Global)) -> Result<Value> {
    ensure!(
        globals.0.is_from_store(store) && globals.1.is_from_store(store),
        "meter globals belong to another store"
    );
    let exhausted = match globals.1.get(store) {
        wasmer::Value::I32(value) => value,
        _ => anyhow::bail!("exhausted global type changed"),
    };
    ensure!(
        exhausted == 0 || exhausted == 1,
        "noncanonical exhausted flag"
    );
    if exhausted == 1 {
        return Ok(json!({"kind":"exhausted","type":"i32","flag":1}));
    }
    let remaining = match globals.0.get(store) {
        wasmer::Value::I64(value) => value as u64,
        _ => anyhow::bail!("remaining global type changed"),
    };
    Ok(json!({"kind":"remaining","type":"i64","points":remaining,"exhausted_flag":0}))
}

fn observe_meter_setup(
    state: &MeterState,
    store: &mut StoreMut,
    instance: &Instance,
) -> Result<()> {
    let globals = capture_meter_globals(store, instance)?;
    let points = read_meter_globals(store, &globals)?;
    let mut state = state
        .lock()
        .map_err(|_| anyhow::anyhow!("meter observation mutex poisoned"))?;
    state.setup_count += 1;
    ensure!(
        state.setup_count == 1 && state.globals.is_none(),
        "meter setup observed more than once"
    );
    state.post_start = Some(points);
    state.globals = Some(globals);
    Ok(())
}

fn observe_meter_recycle(state: &MeterState, props: &mut TaskWasmRecycleProperties) {
    let globals = state.lock().ok().and_then(|mut state| {
        state.recycle_count += 1;
        state.globals.take()
    });
    let observed = globals
        .map(|globals| read_meter_globals(&mut props.store, &globals))
        .unwrap_or_else(|| Err(anyhow::anyhow!("recycle lacked captured meter globals")));
    if let Ok(mut state) = state.lock() {
        match observed {
            Ok(points) => state.final_points = Some(points),
            Err(error) => state.observation_error = Some(format!("{error:#}")),
        }
    }
}

type SharedTask =
    Box<dyn FnOnce() -> Pin<Box<dyn Future<Output = ()> + Send + 'static>> + Send + 'static>;

struct TrapTaskManager {
    inner: TokioTaskManager,
    observed: MeterState,
}
impl fmt::Debug for TrapTaskManager {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("TrapTaskManager").finish_non_exhaustive()
    }
}
impl VirtualTaskManager for TrapTaskManager {
    fn build_memory(
        &self,
        store: &mut StoreMut,
        spawn: SpawnType,
    ) -> Result<Option<wasmer::Memory>, WasiThreadError> {
        self.inner.build_memory(store, spawn)
    }
    fn sleep_now(
        &self,
        time: Duration,
    ) -> Pin<Box<dyn Future<Output = ()> + Send + Sync + 'static>> {
        self.inner.sleep_now(time)
    }
    fn task_shared(&self, task: SharedTask) -> Result<(), WasiThreadError> {
        self.inner.task_shared(task)
    }
    fn task_wasm(&self, mut task: TaskWasm) -> Result<(), WasiThreadError> {
        if task.globals.is_some() || task.callbacks.trigger.is_some() {
            return Err(WasiThreadError::Unsupported);
        }
        let previous = task.callbacks.recycle.take();
        let observed = self.observed.clone();
        task.callbacks.recycle = Some(Box::new(move |mut props| {
            observe_meter_recycle(&observed, &mut props);
            if let Some(previous) = previous {
                previous(props);
            }
        }));
        self.inner.task_wasm(task)
    }
    fn task_dedicated(
        &self,
        task: Box<dyn FnOnce() + Send + 'static>,
    ) -> Result<(), WasiThreadError> {
        self.inner.task_dedicated(task)
    }
    fn thread_parallelism(&self) -> Result<usize, WasiThreadError> {
        self.inner.thread_parallelism()
    }
}

#[derive(Debug)]
struct TrapRuntime {
    inner: PluggableRuntime,
    observed: Arc<std::sync::Mutex<TrapObservation>>,
}

impl Runtime for TrapRuntime {
    fn networking(&self) -> &DynVirtualNetworking {
        &self.inner.networking
    }
    fn task_manager(&self) -> &Arc<dyn VirtualTaskManager> {
        &self.inner.rt
    }
    fn source(&self) -> Arc<dyn Source + Send + Sync> {
        self.inner.source.clone()
    }
    fn engine(&self) -> wasmer::Engine {
        self.inner.engine.clone()
    }
    fn module_cache(&self) -> Arc<dyn ModuleCache + Send + Sync> {
        self.inner.module_cache.clone()
    }
    fn package_loader(&self) -> Arc<dyn PackageLoader + Send + Sync> {
        self.inner.package_loader.clone()
    }
    fn http_client(&self) -> Option<&DynHttpClient> {
        self.inner.http_client.as_ref()
    }
    fn tty(&self) -> Option<&(dyn TtyBridge + Send + Sync)> {
        self.inner.tty.as_deref()
    }
    fn configure_new_instance(
        &self,
        module: &wasmer::Module,
        store: &mut StoreMut,
        instance: &Instance,
        memory: Option<&wasmer::Memory>,
    ) -> Result<()> {
        self.inner
            .configure_new_instance(module, store, instance, memory)
    }
    fn on_taint(&self, reason: TaintReason) {
        if let TaintReason::RuntimeError(error) = reason
            && let Ok(mut observed) = self.observed.lock()
        {
            observed.runtime_traps.push(error.clone().to_trap());
        }
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("PHP_WASIX_V06_BRIDGE_ERROR={error:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let args: Vec<_> = std::env::args_os().collect();
    ensure!(
        args.len() <= 2,
        "fixed bridge accepts at most one mode argument"
    );
    let mode = args
        .get(1)
        .and_then(|arg| arg.to_str())
        .unwrap_or("default");
    ensure!(
        matches!(
            mode,
            "default"
                | "--metered-trap"
                | "--cpu-loop"
                | "--surface-recovery"
                | "--publish"
                | "--handles"
                | "--hooks"
        ),
        "unsupported fixed bridge mode"
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
    wasix.set_engine(if mode == "--metered-trap" {
        metered_engine()
    } else {
        configured_engine()
    });
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
    if mode == "--handles" {
        let normal = run_scenario(&module, module_hash, "handles")?;
        let aborted = run_scenario(&module, module_hash, "handles_abort")?;
        let fixtures: Vec<_> = HANDLE_FILES
            .iter()
            .map(|(name, bytes)| (*name, bytes.len(), format!("{:x}", Sha256::digest(bytes))))
            .collect();
        println!(
            "{}",
            json!({
                "schema": "recastpress.php-compat-v06-wasm-handles.v1",
                "execution": "passed",
                "runtime": "PHP-WASIX 8.5.12 / Wasmer 7.2.0",
                "compiler_profile": COMPILER_PROFILE,
                "artifact": {
                    "path": ARTIFACT_RELATIVE,
                    "bytes": ARTIFACT_BYTES,
                    "sha256": artifact_hash
                },
                "fixtures": fixtures,
                "cases": [normal, aborted],
                "compile_elapsed_ms": compile_ms,
                "execution_elapsed_ms": execution_started.elapsed().as_millis()
            })
        );
        return Ok(());
    }
    if matches!(mode, "--publish" | "--hooks") {
        let hook_profile = mode == "--hooks";
        let scenario = if hook_profile { "hooks" } else { "publish" };
        let files = if hook_profile {
            &HOOK_FILES
        } else {
            &PUBLISH_FILES
        };
        let case = run_scenario(&module, module_hash, scenario)?;
        let fixtures: Vec<_> = files
            .iter()
            .map(|(name, bytes)| (*name, bytes.len(), format!("{:x}", Sha256::digest(bytes))))
            .collect();
        println!(
            "{}",
            json!({
                "schema": format!("recastpress.php-compat-v06-wasm-{scenario}.v1"),
                "execution": "passed",
                "runtime": "PHP-WASIX 8.5.12 / Wasmer 7.2.0",
                "compiler_profile": COMPILER_PROFILE,
                "artifact": {
                    "path": ARTIFACT_RELATIVE,
                    "bytes": ARTIFACT_BYTES,
                    "sha256": artifact_hash
                },
                "fixtures": fixtures,
                "case": case,
                "compile_elapsed_ms": compile_ms,
                "execution_elapsed_ms": execution_started.elapsed().as_millis()
            })
        );
        return Ok(());
    }
    if mode == "--cpu-loop" {
        return run_cpu_loop(&module, module_hash, &artifact_hash, compile_ms);
    }
    if mode == "--surface-recovery" {
        let surface = run_scenario(&module, module_hash, "surface")?;
        println!(
            "{}",
            json!({
                "schema": "recastpress.php-compat-v06-wasm-surface-recovery.v1",
                "execution": "passed",
                "runtime": "PHP-WASIX 8.5.12 / Wasmer 7.2.0",
                "compiler_profile": COMPILER_PROFILE,
                "artifact": {
                    "path": ARTIFACT_RELATIVE,
                    "bytes": ARTIFACT_BYTES,
                    "sha256": artifact_hash
                },
                "fixtures": hashes,
                "surface": surface,
                "module_compile_count": 1
            })
        );
        return Ok(());
    }
    if mode == "--metered-trap" {
        return run_metered_trap(&module, module_hash, &artifact_hash, &hashes, compile_ms);
    }
    let surface = run_scenario(&module, module_hash, "surface")?;
    let throwable = run_scenario(&module, module_hash, "throwable")?;
    let recovery = run_recovery_suite(&module, module_hash)?;
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
            "recovery":recovery,
            "recovery_fixtures":{"path":"fixtures/recovery.php","bytes":RECOVERY.len(),"sha256":format!("{:x}", Sha256::digest(RECOVERY))},
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

fn run_recovery_suite(module: &wasmer::Module, module_hash: ModuleHash) -> Result<Value> {
    let mut records = Vec::new();
    for mode in [
        "recovery_root_exit_23",
        "recovery_partial_frame_exit_24",
        "recovery_host_cancel",
    ] {
        eprintln!("PHP_WASIX_V06_BRIDGE_PHASE={mode}");
        let (host_wire, guest_wire) = Pipe::channel();
        let (mut host_stdin, host_stdout) = host_wire.split();
        let (mut host_stderr, guest_stderr) = Pipe::channel();
        let (stderr_tx, stderr_rx) = mpsc::channel();
        let stderr_thread = thread::Builder::new()
            .name(format!("{mode}-stderr"))
            .spawn(move || {
                let result = drain_stderr(&mut host_stderr);
                let _ = stderr_tx.send(result);
            })?;
        let (done_tx, done_rx) = mpsc::channel();
        let module_copy = module.clone();
        let mode_owned = mode.to_owned();
        let guest_thread = thread::Builder::new()
            .name(mode.to_owned())
            .spawn(move || {
                let result = run_guest(
                    &module_copy,
                    module_hash,
                    &mode_owned,
                    guest_wire.clone(),
                    guest_wire,
                    guest_stderr,
                );
                let _ = done_tx.send(result);
            })?;
        let mut reader = BufReader::new(DeadlinePipeReader {
            pipe: host_stdout,
            deadline: Instant::now() + SCENARIO_TIMEOUT,
        });
        let mut first = Vec::new();
        let first_read = reader
            .by_ref()
            .take((OUTPUT_LIMIT + 1) as u64)
            .read_until(b'\n', &mut first)
            .context("read recovery start frame")?;
        let expected_start = b"{\"v\":1,\"request\":\"recovery-probe-1\",\"seq\":0,\"kind\":\"start_root\",\"payload\":{\"operation\":\"pending_root_effect\"}}\n";
        ensure!(
            first_read > 0 && first_read <= OUTPUT_LIMIT && first == expected_start,
            "recovery start frame malformed, unexpected, or oversize"
        );
        let request = RequestGeneration::new(100 + records.len() as u64);
        let mut continuation = Continuation::<Value>::new(request, LimitsProfile::default())
            .context("create recovery continuation")?;
        let root = continuation.start_root().context("start recovery root")?;
        let effect = continuation
            .invoke_php(root)
            .context("create pending recovery effect")?;
        let invoke = b"{\"v\":1,\"request\":\"recovery-probe-1\",\"seq\":0,\"kind\":\"invoke_php\",\"payload\":{\"callback\":\"pending_root_callback\",\"effect_id\":\"pending-root\"}}\n";
        let host_cancelled = mode == "recovery_host_cancel";
        host_stdin.write_all(invoke)?;
        host_stdin.flush()?;
        let mut checkpoint = Vec::new();
        let checkpoint_read = reader
            .by_ref()
            .take((OUTPUT_LIMIT + 1) as u64)
            .read_until(b'\n', &mut checkpoint)
            .context("read recovery effect-started checkpoint")?;
        let expected_checkpoint = b"{\"v\":1,\"request\":\"recovery-probe-1\",\"seq\":1,\"kind\":\"effect_started\",\"payload\":{\"effect_id\":\"pending-root\"}}\n";
        ensure!(
            checkpoint_read > 0
                && checkpoint_read <= OUTPUT_LIMIT
                && checkpoint == expected_checkpoint,
            "recovery callback checkpoint malformed"
        );
        if host_cancelled {
            host_stdin.close();
        }
        let mut trailing = Vec::new();
        let trailing_read = reader
            .by_ref()
            .take((OUTPUT_LIMIT + 1) as u64)
            .read_until(b'\n', &mut trailing)
            .context("read recovery trailing bytes")?;
        ensure!(
            trailing_read <= OUTPUT_LIMIT,
            "recovery trailing output exceeded bound"
        );
        let peer_eof_observed = !trailing.ends_with(b"\n");
        ensure!(
            peer_eof_observed,
            "unexpected complete trailing recovery frame"
        );
        let guest_result = done_rx
            .recv_timeout(GUEST_JOIN_TIMEOUT)
            .context("recovery guest join deadline")?;
        guest_thread
            .join()
            .map_err(|_| anyhow::anyhow!("recovery guest thread panicked"))?;
        host_stdin.close();
        let (stderr, stderr_overflow) = stderr_rx
            .recv_timeout(GUEST_JOIN_TIMEOUT)
            .context("recovery stderr join deadline")??;
        stderr_thread
            .join()
            .map_err(|_| anyhow::anyhow!("recovery stderr thread panicked"))?;
        ensure!(
            !stderr_overflow,
            "recovery stderr exceeded fixed capture bound"
        );
        ensure!(stderr.is_empty(), "unexpected recovery stderr output");
        let exit = match guest_result {
            Ok(()) => None,
            Err(error) => error.chain().find_map(|cause| {
                cause
                    .downcast_ref::<wasmer_wasix::WasiRuntimeError>()
                    .and_then(|e| e.as_exit_code())
                    .map(|c| c.raw())
            }),
        };
        let expected_exit = match mode {
            "recovery_root_exit_23" => Some(23),
            "recovery_partial_frame_exit_24" => Some(24),
            _ => Some(26),
        };
        ensure!(
            exit == expected_exit,
            "unexpected recovery guest exit for {mode}: {exit:?}"
        );
        let finish_pending_rejected =
            continuation.finish_operation(root) == Err(ContinuationError::PendingEffect);
        let pending_before_abort = continuation.depth() == 1 && finish_pending_rejected;
        ensure!(
            pending_before_abort,
            "recovery continuation did not retain pending PHP effect"
        );
        continuation.abort();
        let depth_after_abort = continuation.depth();
        let resume_after_abort_rejected = continuation
            .resume(effect, Completion::Value(json!("must-not-resume")))
            == Err(ContinuationError::RequestClosed);
        ensure!(
            depth_after_abort == 0
                && continuation.state() == &RequestState::Aborted
                && resume_after_abort_rejected,
            "recovery continuation abort did not close pending request"
        );
        let expected_partial = if mode == "recovery_partial_frame_exit_24" {
            b"{\"v\":1,\"request\":\"recovery-probe-1\",\"seq\":2,\"kind\":\"call_core\",\"payload\":{\"operation\":\"pending\"".as_slice()
        } else {
            &[]
        };
        if !expected_partial.is_empty() {
            ensure!(
                trailing == expected_partial,
                "recovery partial frame mismatch"
            );
        } else {
            ensure!(trailing.is_empty(), "unexpected recovery trailing bytes");
        }
        let fresh_surface = run_scenario(module, module_hash, "surface")?;
        records.push(json!({
            "mode": mode,
            "observed_guest_exit": exit,
            "guest_exit_kind": if host_cancelled { "cancelled_after_pipe_close" } else { "wasi_exit" },
            "peer_failure": if host_cancelled { "stdin_closed_while_guest_waited_for_response" } else { "guest_exit_while_root_effect_pending" },
            "first_frame_validated": true,
            "effect_started_checkpoint": true,
            "peer_eof_observed": peer_eof_observed,
            "partial_frame_bytes": trailing.len(),
            "host_cancelled": host_cancelled,
            "guest_thread_joined": true,
            "stderr_thread_joined": true,
            "stderr": String::from_utf8_lossy(&stderr),
            "stderr_overflow": false,
            "continuation": {
                "pending_before_abort": pending_before_abort,
                "finish_pending_rejected": finish_pending_rejected,
                "depth_after_abort": depth_after_abort,
                "resume_after_abort_rejected": resume_after_abort_rejected
            },
            "fresh_surface": fresh_surface
        }));
    }
    Ok(json!(records))
}

fn run_metered_trap(
    module: &wasmer::Module,
    module_hash: ModuleHash,
    artifact_hash: &str,
    hashes: &[(&str, usize, String)],
    compile_ms: u128,
) -> Result<()> {
    let normal = run_scenario(module, module_hash, "surface")?;
    let (host_wire, guest_wire) = Pipe::channel();
    let (mut host_stdin, host_stdout) = host_wire.split();
    let (mut host_stderr, guest_stderr) = Pipe::channel();
    let (stderr_tx, stderr_rx) = mpsc::channel();
    let stderr_thread = thread::spawn(move || {
        let result = drain_stderr(&mut host_stderr);
        let _ = stderr_tx.send(result);
    });
    let (done_tx, done_rx) = mpsc::channel();
    let module_for_guest = module.clone();
    let trap_observation = Arc::new(std::sync::Mutex::new(TrapObservation::default()));
    let trap_observation_thread = trap_observation.clone();
    let guest_thread = thread::spawn(move || {
        let result = run_metered_guest(
            &module_for_guest,
            module_hash,
            guest_wire.clone(),
            guest_wire,
            guest_stderr,
            trap_observation_thread,
        );
        let _ = done_tx.send(result);
    });
    let mut reader = BufReader::new(DeadlinePipeReader {
        pipe: host_stdout,
        deadline: Instant::now() + SCENARIO_TIMEOUT,
    });
    let mut first = Vec::new();
    let first_read = reader
        .by_ref()
        .take((OUTPUT_LIMIT + 1) as u64)
        .read_until(b'\n', &mut first)?;
    ensure!(
        first_read > 0 && first_read <= OUTPUT_LIMIT,
        "metered start frame missing or oversized"
    );
    ensure!(first == b"{\"v\":1,\"request\":\"trap-probe-1\",\"seq\":0,\"kind\":\"start_root\",\"payload\":{\"operation\":\"metered_callback_loop\"}}\n", "metered fixture start frame mismatch");
    let request = RequestGeneration::new(801);
    let mut continuation = Continuation::<Value>::new(request, LimitsProfile::default())?;
    let root = continuation.start_root()?;
    let effect = continuation.invoke_php(root)?;
    host_stdin.write_all(b"{\"v\":1,\"request\":\"trap-probe-1\",\"seq\":0,\"kind\":\"invoke_php\",\"payload\":{\"callback\":\"metered_callback_loop\",\"effect_id\":\"pending-trap\"}}\n")?;
    host_stdin.flush()?;
    let mut checkpoint = Vec::new();
    let checkpoint_read = reader
        .by_ref()
        .take((OUTPUT_LIMIT + 1) as u64)
        .read_until(b'\n', &mut checkpoint)?;
    ensure!(
        checkpoint_read > 0 && checkpoint_read <= OUTPUT_LIMIT,
        "metered callback checkpoint missing or oversized"
    );
    ensure!(checkpoint == b"{\"v\":1,\"request\":\"trap-probe-1\",\"seq\":1,\"kind\":\"effect_started\",\"payload\":{\"effect_id\":\"pending-trap\"}}\n", "metered callback checkpoint mismatch");
    let guest_error = done_rx
        .recv_timeout(SCENARIO_TIMEOUT)
        .context("metered guest did not exhaust its operator budget")?;
    guest_thread
        .join()
        .map_err(|_| anyhow::anyhow!("metered guest thread panicked"))?;
    host_stdin.close();
    let mut trailing = Vec::new();
    let trailing_read = reader
        .by_ref()
        .take((OUTPUT_LIMIT + 1) as u64)
        .read_to_end(&mut trailing)?;
    ensure!(
        trailing_read <= OUTPUT_LIMIT && trailing.is_empty(),
        "metered guest emitted unexpected trailing stdout"
    );
    let runner_exit_code = guest_error.as_ref().err().and_then(|error| {
        error.chain().find_map(|cause| {
            cause
                .downcast_ref::<wasmer_wasix::WasiRuntimeError>()
                .and_then(|runtime_error| runtime_error.as_exit_code())
                .map(|code| code.raw())
        })
    });
    ensure!(
        runner_exit_code == Some(45),
        "meter exhaustion did not surface expected Wasix process exit code 45: {runner_exit_code:?}"
    );
    let runner_error = guest_error
        .err()
        .map(|error| format!("{error:#}"))
        .unwrap_or_default();
    let observed_traps = trap_observation
        .lock()
        .map_err(|_| anyhow::anyhow!("trap observation poisoned"))?
        .runtime_traps
        .clone();
    let typed_trap =
        observed_traps.len() == 1 && observed_traps[0] == Some(TrapCode::UnreachableCodeReached);
    ensure!(
        typed_trap,
        "metered callback lacked Wasmer UnreachableCodeReached Runtime trap: {observed_traps:?}; runner={runner_error}"
    );
    let observed_trap_names: Vec<_> = observed_traps
        .iter()
        .map(|trap| format!("{trap:?}"))
        .collect();
    let (post_start_points, final_points, setup_count, recycle_count, observation_error) = {
        let observation = trap_observation
            .lock()
            .map_err(|_| anyhow::anyhow!("meter observation poisoned"))?;
        (
            observation.post_start.clone(),
            observation.final_points.clone(),
            observation.setup_count,
            observation.recycle_count,
            observation.observation_error.clone(),
        )
    };
    ensure!(
        setup_count == 1 && recycle_count == 1 && observation_error.is_none(),
        "meter global observation lifecycle failed: {observation_error:?}"
    );
    ensure!(
        post_start_points
            .as_ref()
            .is_some_and(|points| points["kind"] == "remaining"
                && points["points"]
                    .as_u64()
                    .is_some_and(|value| value > 0 && value < TRAP_POINTS)),
        "metered PHP startup did not leave a positive observed budget: {post_start_points:?}"
    );
    ensure!(
        final_points
            .as_ref()
            .is_some_and(|points| points["kind"] == "exhausted"
                && points["type"] == "i32"
                && points["flag"] == 1),
        "meter exhausted global was not independently observed: {final_points:?}"
    );
    let finish_rejected =
        continuation.finish_operation(root) == Err(ContinuationError::PendingEffect);
    continuation.abort();
    let depth_after_abort = continuation.depth();
    let late_resume_rejected = continuation.resume(effect, Completion::Value(json!(null)))
        == Err(ContinuationError::RequestClosed);
    ensure!(
        finish_rejected && depth_after_abort == 0 && late_resume_rejected,
        "trap continuation was not aborted and closed"
    );
    let (stderr, overflow) = stderr_rx
        .recv_timeout(GUEST_JOIN_TIMEOUT)
        .context("metered stderr drainer join deadline")??;
    stderr_thread
        .join()
        .map_err(|_| anyhow::anyhow!("metered stderr thread panicked"))?;
    ensure!(
        !overflow && stderr.is_empty(),
        "metered guest wrote stderr or exceeded capture bound"
    );
    let recovery = run_scenario(module, module_hash, "surface")?;
    let traps = ["normal", "trap", "recovery"];
    let trap_fixture_hashes: Vec<_> = TRAP_FILES
        .iter()
        .map(|(name, bytes)| (*name, bytes.len(), format!("{:x}", Sha256::digest(bytes))))
        .collect();
    println!(
        "{}",
        json!({
            "schema": "recastpress.php-compat-v06-wasm-trap.v1",
            "execution": "passed",
            "runtime": "PHP-WASIX 8.5.12 / Wasmer 7.2.0",
            "compiler_profile": COMPILER_PROFILE,
            "artifact": {
                "path": ARTIFACT_RELATIVE,
                "bytes": ARTIFACT_BYTES,
                "sha256": artifact_hash
            },
            "fixtures": hashes,
            "trap_fixtures": trap_fixture_hashes,
            "compile_count": 1,
            "compile_elapsed_ms": compile_ms,
            "configured_initial_points": {"type": "i64", "points": TRAP_POINTS},
            "post_start_points": post_start_points,
            "final_points": final_points,
            "metering_setup_count": setup_count,
            "metering_recycle_count": recycle_count,
            "metering_observation_error": observation_error,
            "budget_exhausted": true,
            "cost_policy": "one-point-per-original-Wasm-operator",
            "budget_reset_count": 0,
            "cases": {
                "normal": normal,
                "trap": {
                    "status": "passed",
                    "callback_checkpoint": true,
                    "typed_wasmer_trap": "UnreachableCodeReached",
                    "captured_runtime_traps": observed_trap_names,
                    "runner_exit_code": runner_exit_code,
                    "runner_error": runner_error,
                    "post_start_points": post_start_points,
                    "final_points": final_points,
                    "metering_setup_count": setup_count,
                    "metering_recycle_count": recycle_count,
                    "budget_exhausted": true,
                    "guest_thread_joined": true,
                    "stderr_thread_joined": true,
                    "stderr": "",
                    "continuation": {
                        "finish_pending_rejected": finish_rejected,
                        "depth_after_abort": depth_after_abort,
                        "late_resume_rejected": late_resume_rejected
                    }
                },
                "recovery": recovery
            },
            "case_order": traps,
            "scope": "fixed PHP callback metering trap and fresh guest recovery; not a product CPU-time limit"
        })
    );
    Ok(())
}

fn run_cpu_loop(
    module: &wasmer::Module,
    module_hash: ModuleHash,
    artifact_hash: &str,
    compile_ms: u128,
) -> Result<()> {
    let (host_wire, guest_wire) = Pipe::channel();
    let (mut host_stdin, host_stdout) = host_wire.split();
    let (mut host_stderr, guest_stderr) = Pipe::channel();
    let (stderr_tx, stderr_rx) = mpsc::channel();
    let _stderr_thread = thread::spawn(move || {
        let result = drain_stderr(&mut host_stderr);
        let _ = stderr_tx.send(result);
    });
    let (done_tx, done_rx) = mpsc::channel();
    let module = module.clone();
    let _guest_thread = thread::spawn(move || {
        let result = run_guest(
            &module,
            module_hash,
            "cpu_loop",
            guest_wire.clone(),
            guest_wire,
            guest_stderr,
        );
        let _ = done_tx.send(result);
    });
    let mut reader = BufReader::new(DeadlinePipeReader {
        pipe: host_stdout,
        deadline: Instant::now() + SCENARIO_TIMEOUT,
    });
    let mut start = Vec::new();
    let start_read = reader
        .by_ref()
        .take((OUTPUT_LIMIT + 1) as u64)
        .read_until(b'\n', &mut start)?;
    ensure!(start_read == b"{\"v\":1,\"request\":\"trap-probe-1\",\"seq\":0,\"kind\":\"start_root\",\"payload\":{\"operation\":\"metered_callback_loop\"}}\n".len(), "CPU loop start frame missing or oversized");
    ensure!(
        start == b"{\"v\":1,\"request\":\"trap-probe-1\",\"seq\":0,\"kind\":\"start_root\",\"payload\":{\"operation\":\"metered_callback_loop\"}}\n",
        "CPU loop did not emit fixed start frame"
    );
    host_stdin.write_all(b"{\"v\":1,\"request\":\"trap-probe-1\",\"seq\":0,\"kind\":\"invoke_php\",\"payload\":{\"callback\":\"metered_callback_loop\",\"effect_id\":\"pending-trap\"}}\n")?;
    host_stdin.flush()?;
    let mut checkpoint = Vec::new();
    let checkpoint_read = reader
        .by_ref()
        .take((OUTPUT_LIMIT + 1) as u64)
        .read_until(b'\n', &mut checkpoint)?;
    ensure!(
        checkpoint_read > 0 && checkpoint_read <= OUTPUT_LIMIT,
        "CPU loop callback checkpoint missing or oversized"
    );
    ensure!(checkpoint == b"{\"v\":1,\"request\":\"trap-probe-1\",\"seq\":1,\"kind\":\"effect_started\",\"payload\":{\"effect_id\":\"pending-trap\"}}\n", "CPU loop callback checkpoint mismatch");
    println!(
        "{}",
        json!({
            "schema": "recastpress.php-compat-v06-wasm-cpu-loop.v1",
            "status": "ready",
            "host_pid": std::process::id(),
            "callback_loop_entered": true,
            "artifact": {
                "path": ARTIFACT_RELATIVE,
                "bytes": ARTIFACT_BYTES,
                "sha256": artifact_hash
            },
            "fixtures": TRAP_FILES
                .iter()
                .map(|(name, bytes)| (*name, bytes.len(), format!("{:x}", Sha256::digest(bytes))))
                .collect::<Vec<_>>(),
            "compile_count": 1,
            "compile_elapsed_ms": compile_ms,
            "scope": "fixed unmetered PHP callback loop; external owned-process supervisor must kill and reap; no CPU-time limit claim"
        })
    );
    loop {
        match done_rx.try_recv() {
            Ok(result) => anyhow::bail!("CPU-bound guest ended after ready checkpoint: {result:?}"),
            Err(mpsc::TryRecvError::Disconnected) => {
                anyhow::bail!("CPU-bound guest supervisor disconnected")
            }
            Err(mpsc::TryRecvError::Empty) => {}
        }
        match stderr_rx.try_recv() {
            Ok(result) => {
                let (bytes, overflow) = result?;
                ensure!(
                    bytes.is_empty() && !overflow,
                    "CPU-bound guest stderr was nonempty or exceeded bound"
                );
                anyhow::bail!("CPU-bound guest stderr closed before external cancellation");
            }
            Err(mpsc::TryRecvError::Disconnected) => {
                anyhow::bail!("CPU-bound stderr drainer disconnected")
            }
            Err(mpsc::TryRecvError::Empty) => {}
        }
        thread::sleep(Duration::from_millis(1));
    }
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
    let guest_args = if matches!(scenario, "handles" | "handles_abort") {
        vec!["-n", "/fixture/handle_driver.php", scenario]
    } else if scenario == "hooks" {
        vec!["-n", "/fixture/hooks_driver.php", "hooks"]
    } else if scenario == "publish" {
        vec!["-n", "/fixture/publish_driver.php", "publish"]
    } else if scenario.starts_with("trap_") || scenario == "cpu_loop" {
        vec![
            "-n",
            "/fixture/trap_recovery.php",
            if scenario == "cpu_loop" {
                "cpu_loop"
            } else {
                "metered_trap"
            },
        ]
    } else if scenario.starts_with("recovery_") {
        vec!["-n", "/fixture/recovery.php", scenario]
    } else {
        vec!["-n", "/fixture/bridge_driver.php", scenario]
    };
    runner
        .with_forward_host_env(false)
        .with_home_mapped(false)
        .with_asynchronous_threading(false)
        .with_stdin(Box::new(stdin))
        .with_stdout(Box::new(stdout))
        .with_stderr(Box::new(stderr))
        .with_args(guest_args)
        .with_current_dir("/fixture")
        .with_mount(
            "/fixture".to_owned(),
            Arc::new(FixedFilesFs {
                files: if matches!(scenario, "handles" | "handles_abort") {
                    &HANDLE_FILES
                } else if scenario == "hooks" {
                    &HOOK_FILES
                } else if scenario == "publish" {
                    &PUBLISH_FILES
                } else if scenario.starts_with("trap_") || scenario == "cpu_loop" {
                    &TRAP_FILES
                } else {
                    &FILES
                },
            }),
        )
        .capabilities_mut()
        .insecure_allow_all = false;
    runner.capabilities_mut().threading.max_threads = Some(1);
    runner.capabilities_mut().threading.enable_deep_sleep = false;
    let guest_result = runner
        .run_wasm(
            RuntimeOrEngine::Runtime(Arc::new(wasix)),
            "php",
            module.clone(),
            module_hash,
        )
        .with_context(|| format!("run fixed PHP bridge scenario {scenario}"));
    drop(runner);
    drop(_guard);
    runtime.shutdown_timeout(Duration::from_secs(2));
    guest_result
}

fn run_metered_guest(
    module: &wasmer::Module,
    module_hash: ModuleHash,
    stdin: Pipe,
    stdout: Pipe,
    stderr: Pipe,
    observed: Arc<std::sync::Mutex<TrapObservation>>,
) -> Result<()> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let _guard = runtime.enter();
    let tasks = TokioTaskManager::new(runtime.handle().clone());
    let mut inner = PluggableRuntime::new(Arc::new(TrapTaskManager {
        inner: tasks,
        observed: observed.clone(),
    }));
    inner.set_engine(metered_engine());
    inner.set_networking_implementation(UnsupportedVirtualNetworking::default());
    inner.http_client = None;
    let setup_observed = observed.clone();
    inner.with_instance_setup(move |_, store, instance, _| {
        observe_meter_setup(&setup_observed, store, instance)
    });
    let wasix = TrapRuntime { inner, observed };
    let mut runner = WasiRunner::new();
    runner
        .with_forward_host_env(false)
        .with_home_mapped(false)
        .with_asynchronous_threading(false)
        .with_stdin(Box::new(stdin))
        .with_stdout(Box::new(stdout))
        .with_stderr(Box::new(stderr))
        .with_args(["-n", "/fixture/trap_recovery.php", "metered_trap"])
        .with_current_dir("/fixture")
        .with_mount(
            "/fixture".to_owned(),
            Arc::new(FixedFilesFs { files: &TRAP_FILES }),
        );
    runner.capabilities_mut().insecure_allow_all = false;
    runner.capabilities_mut().threading.max_threads = Some(1);
    runner.capabilities_mut().threading.enable_deep_sleep = false;
    let result = runner
        .run_wasm(
            RuntimeOrEngine::Runtime(Arc::new(wasix)),
            "php",
            module.clone(),
            module_hash,
        )
        .context("run fixed metered PHP callback fixture");
    drop(runner);
    drop(_guard);
    runtime.shutdown_timeout(Duration::from_secs(2));
    result
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
struct FixedFilesFs {
    files: &'static [(&'static str, &'static [u8])],
}
fn lookup(
    path: &Path,
    files: &'static [(&'static str, &'static [u8])],
) -> std::result::Result<Option<(&'static str, &'static [u8])>, FsError> {
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
        Some(name) => files
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
        let Some((_, bytes)) = lookup(path, self.files)? else {
            return Err(FsError::NotAFile);
        };
        Ok(Box::new(StaticFile::new(bytes.to_vec())))
    }
}
impl FileSystem for FixedFilesFs {
    fn readlink(&self, path: &Path) -> std::result::Result<PathBuf, FsError> {
        lookup(path, self.files)?;
        Err(FsError::InvalidInput)
    }
    fn read_dir(&self, path: &Path) -> std::result::Result<ReadDir, FsError> {
        if lookup(path, self.files)?.is_some() {
            return Err(FsError::BaseNotDirectory);
        }
        Ok(ReadDir::new(
            self.files
                .iter()
                .map(|(name, bytes)| DirEntry {
                    path: PathBuf::from(format!("/{name}")),
                    metadata: Ok(metadata(true, bytes.len())),
                })
                .collect(),
        ))
    }
    fn metadata(&self, path: &Path) -> std::result::Result<Metadata, FsError> {
        Ok(match lookup(path, self.files)? {
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

fn metered_engine() -> wasmer::Engine {
    let middleware = Arc::new(Metering::new(TRAP_POINTS, |_operator: &Operator| 1));
    #[cfg(feature = "llvm")]
    let mut compiler = wasmer_compiler_llvm::LLVM::default();
    #[cfg(all(not(feature = "llvm"), feature = "cranelift"))]
    let mut compiler = {
        let mut compiler = Cranelift::default();
        compiler.opt_level(CraneliftOptLevel::None);
        compiler
    };
    compiler.push_middleware(middleware);
    compiler.into()
}
#[cfg(all(not(feature = "llvm"), feature = "cranelift"))]
fn configured_engine() -> wasmer::Engine {
    let mut config = Cranelift::default();
    config.opt_level(CraneliftOptLevel::None);
    config.into()
}
