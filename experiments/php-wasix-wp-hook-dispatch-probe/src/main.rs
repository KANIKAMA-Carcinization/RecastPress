#![forbid(unsafe_code)]

//! Dedicated real PHP-WASIX bridge for the pinned WordPress selected-filter probe.

mod protocol;
mod scenarios;

use scenarios::Scenario;
use std::{
    fs,
    io::{BufReader, Read, Write},
    path::{Component, Path, PathBuf},
    sync::{Arc, mpsc},
    thread,
    time::{Duration, Instant},
};

use anyhow::{Context, Result, ensure};
use serde_json::json;
use sha2::{Digest, Sha256};
use wasmer::Module;
use wasmer_compiler_cranelift::{Cranelift, CraneliftOptLevel};
use wasmer_wasix::{
    Pipe, PluggableRuntime, UnsupportedVirtualNetworking,
    runners::wasi::{RuntimeOrEngine, WasiRunner},
    runtime::Runtime,
    runtime::module_cache::HashedModuleData,
    runtime::task_manager::VirtualTaskManager,
    runtime::task_manager::tokio::TokioTaskManager,
    virtual_fs::{
        DirEntry, FileOpener, FileSystem, FileType, FsError, Metadata, OpenOptions,
        OpenOptionsConfig, PipeRx, PipeTx, ReadDir, StaticFile, VirtualFile,
    },
};

const PHP_SHA256: &str = "e64850fde91d98dff0bce2b95ed467591376586469b1f4d8bad5c8759c4edbe4";
const PHP_BYTES: u64 = 39_727_549;
const TIMEOUT: Duration = Duration::from_secs(120);

const FILES: [(&str, &[u8]); 15] = [
    (
        "bridge_driver.php",
        include_bytes!("../../php-compat-v06-probe/fixtures/bridge_driver.php"),
    ),
    (
        "strict_json.php",
        include_bytes!("../../php-compat-v06-probe/fixtures/strict_json.php"),
    ),
    (
        "handle_registry.php",
        include_bytes!("../../php-compat-v06-probe/fixtures/handle_registry.php"),
    ),
    (
        "class-wp-hook.php",
        include_bytes!(
            "../../php-wasix-wp-hook-dispatch-probe/evidence/overlay-v5/wp-includes/class-wp-hook.php"
        ),
    ),
    (
        "class-wp-filter-sentinel.php",
        include_bytes!(
            "../../../.reference/wordpress-7.1.3/wp-includes/class-wp-filter-sentinel.php"
        ),
    ),
    (
        "plugin.php",
        include_bytes!(
            "../../php-wasix-wp-hook-dispatch-probe/evidence/overlay-v5/wp-includes/plugin.php"
        ),
    ),
    (
        "wp_hook_driver.php",
        include_bytes!("../../php-wasix-wp-hook-dispatch-probe/fixtures/wp_hook_driver.php"),
    ),
    (
        "wp_action_driver.php",
        include_bytes!("../../php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_driver.php"),
    ),
    (
        "wp_action_ref_array_driver.php",
        include_bytes!(
            "../../php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_ref_array_driver.php"
        ),
    ),
    (
        "wp_action_throwable_driver.php",
        include_bytes!(
            "../../php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_throwable_driver.php"
        ),
    ),
    (
        "wp_action_nested_driver.php",
        include_bytes!(
            "../../php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_nested_driver.php"
        ),
    ),
    (
        "wp_action_mutation_driver.php",
        include_bytes!(
            "../../php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_mutation_driver.php"
        ),
    ),
    (
        "wp_action_removal_driver.php",
        include_bytes!(
            "../../php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_removal_driver.php"
        ),
    ),
    (
        "wp_action_remove_all_driver.php",
        include_bytes!(
            "../../php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_remove_all_driver.php"
        ),
    ),
    (
        "wp_action_same_priority_driver.php",
        include_bytes!(
            "../../php-wasix-wp-hook-dispatch-probe/fixtures/wp_action_same_priority_driver.php"
        ),
    ),
];

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let command = args.next();
    let scenarios = if command.as_deref() == Some("--batch") {
        let cases = args
            .take(33)
            .map(|name| Scenario::parse((name != "filter").then_some(name)))
            .collect::<Result<Vec<_>>>()?;
        ensure!((1..=32).contains(&cases.len()), "expected 1–32 scenarios");
        cases
    } else {
        ensure!(args.next().is_none(), "unexpected hook-dispatch arguments");
        vec![Scenario::parse(command)?]
    };
    eprintln!("PHP_WASIX_WP_HOOK_PHASE=artifact_read_start");
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()?;
    let wasm_path = root.join(".reference/php-wasix-8.5.12/php.wasm");
    let wasm = fs::read(&wasm_path).context("read pinned PHP-WASIX artifact")?;
    ensure!(
        wasm.len() as u64 == PHP_BYTES && sha256(&wasm) == PHP_SHA256,
        "PHP-WASIX artifact pin mismatch"
    );
    eprintln!("PHP_WASIX_WP_HOOK_PHASE=artifact_read_done");
    let data = HashedModuleData::new(wasm);
    let module_hash = *data.hash();
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let _guard = runtime.enter();
    let tasks = TokioTaskManager::new(runtime.handle().clone());
    let mut wasix = PluggableRuntime::new(Arc::new(tasks));
    eprintln!("PHP_WASIX_WP_HOOK_PHASE=module_compile_start");
    let mut compiler = Cranelift::default();
    compiler.opt_level(CraneliftOptLevel::None);
    wasix.set_engine(compiler.into());
    wasix.set_networking_implementation(UnsupportedVirtualNetworking::default());
    wasix.http_client = None;
    let module = wasix
        .load_hashed_module_sync(data, None)
        .context("compile pinned PHP-WASIX")?;
    eprintln!("PHP_WASIX_WP_HOOK_PHASE=module_compile_done");
    let result = scenarios
        .into_iter()
        .try_for_each(|scenario| run_scenario(scenario, &module, module_hash));
    drop(wasix);
    drop(_guard);
    runtime.shutdown_timeout(Duration::from_secs(2));
    result
}

fn run_scenario(
    scenario: Scenario,
    module: &Module,
    module_hash: wasmer_types::ModuleHash,
) -> Result<()> {
    // Mirror the proven bridge topology: the shared wire is split at the host,
    // while the guest keeps a clone for stdin and the original endpoint for stdout.
    let (host_wire, guest_wire) = Pipe::channel();
    let (mut host_stdin, host_stdout) = host_wire.split();
    let guest_stdin = guest_wire.clone();
    let guest_stdout = guest_wire;
    let (mut host_stderr, guest_stderr) = Pipe::channel();
    eprintln!("PHP_WASIX_WP_HOOK_PHASE=pipes_ready");
    let (stderr_tx, stderr_rx) = mpsc::channel();
    let stderr_thread = thread::Builder::new()
        .name("php-wasix-wp-hook-dispatch-stderr".into())
        .spawn(move || {
            let result = drain_stderr(&mut host_stderr, true);
            let _ = stderr_tx.send(result);
        })?;
    let fs_map = Arc::new(FixtureFs);
    let done_fs = fs_map.clone();
    let (done_tx, done_rx) = mpsc::channel();
    let module_copy = module.clone();
    let guest_args = scenario.guest_args();
    let guest_thread = thread::Builder::new()
        .name("php-wasix-wp-hook-dispatch".into())
        .spawn(move || {
            let result = run_guest(
                &module_copy,
                module_hash,
                guest_stdin,
                guest_stdout,
                guest_stderr,
                guest_args,
                done_fs,
            );
            let _ = done_tx.send(result);
        })?;
    eprintln!("PHP_WASIX_WP_HOOK_PHASE=guest_thread_spawned");
    let mut total_in = 0;
    let mut total_out = 0;
    let mut reader = BufReader::new(DeadlinePipeReader {
        pipe: host_stdout,
        deadline: Instant::now() + TIMEOUT,
    });
    eprintln!("PHP_WASIX_WP_HOOK_PHASE=host_wait_start");
    let observed = {
        let mut writer = DeadlinePipeWriter {
            pipe: &mut host_stdin,
            deadline: Instant::now() + TIMEOUT,
        };
        scenario.run(&mut reader, &mut writer, &mut total_in, &mut total_out)
    };
    eprintln!("PHP_WASIX_WP_HOOK_PHASE=host_wait_done");
    let observed = match observed {
        Ok(value) => Ok(value),
        Err(error) => {
            host_stdin.close();
            Err(error)
        }
    };
    host_stdin.close();
    drop(reader);
    let guest = done_rx
        .recv_timeout(Duration::from_secs(15))
        .context("PHP guest did not finish after bridge closure");
    if guest.is_err() {
        drop(guest_thread);
    } else {
        guest_thread
            .join()
            .map_err(|_| anyhow::anyhow!("PHP guest thread panicked"))?;
    }
    let (stderr, stderr_overflow) = stderr_rx
        .recv_timeout(Duration::from_secs(15))
        .context("PHP stderr drainer did not finish")??;
    stderr_thread
        .join()
        .map_err(|_| anyhow::anyhow!("PHP stderr drainer thread panicked"))?;
    ensure!(!stderr_overflow, "PHP stderr exceeded fixed capture bound");
    let observed = observed?;
    match guest? {
        Ok(()) => {}
        Err(error) => anyhow::bail!(
            "PHP guest failed: {error:#}; stderr: {}",
            String::from_utf8_lossy(&stderr)
        ),
    }
    let stderr_text = String::from_utf8_lossy(&stderr);
    let unexpected_stderr = stderr_text
        .lines()
        .filter(|line| {
            !line.starts_with("RP_HOOK_PHASE=")
                && !line.starts_with("RP_ACTION_PHASE=")
                && !line.starts_with("RP_ACTION_REF_ARRAY_PHASE=")
                && !line.starts_with("RP_ACTION_THROWABLE_PHASE=")
        })
        .collect::<Vec<_>>();
    ensure!(
        unexpected_stderr.is_empty(),
        "PHP guest wrote stderr: {}",
        unexpected_stderr.join("\n")
    );
    println!(
        "{}",
        json!({
            "status": "passed",
            "scenario": scenario.name(),
            "php_wasm_sha256": PHP_SHA256,
            "php_wasm_bytes": PHP_BYTES,
            "overlay_files": FILES.iter().map(|(name, bytes)| json!({
                "path": name,
                "sha256": sha256(bytes),
            })).collect::<Vec<_>>(),
            "observation": observed,
        })
    );
    Ok(())
}

fn run_guest(
    module: &Module,
    module_hash: wasmer_types::ModuleHash,
    stdin: Pipe,
    stdout: Pipe,
    stderr: Pipe,
    runner_args: Vec<String>,
    fs_map: Arc<FixtureFs>,
) -> Result<()> {
    eprintln!("PHP_WASIX_WP_HOOK_PHASE=guest_runtime_start");
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let _guard = runtime.enter();
    let tasks = TokioTaskManager::new(runtime.handle().clone());
    let mut wasix = PluggableRuntime::new(Arc::new(tasks) as Arc<dyn VirtualTaskManager>);
    let mut compiler = Cranelift::default();
    compiler.opt_level(CraneliftOptLevel::None);
    wasix.set_engine(compiler.into());
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
        .with_args(runner_args)
        .with_current_dir("/fixture")
        .with_mount("/fixture".to_string(), fs_map)
        .capabilities_mut()
        .insecure_allow_all = false;
    runner.capabilities_mut().threading.max_threads = Some(1);
    runner.capabilities_mut().threading.enable_deep_sleep = false;
    eprintln!("PHP_WASIX_WP_HOOK_PHASE=run_wasm_start");
    runner
        .run_wasm(
            RuntimeOrEngine::Runtime(Arc::new(wasix)),
            "php",
            module.clone(),
            module_hash,
        )
        .context("run pinned PHP-WASIX WordPress hook fixture")?;
    eprintln!("PHP_WASIX_WP_HOOK_PHASE=run_wasm_done");
    drop(runner);
    drop(_guard);
    runtime.shutdown_timeout(Duration::from_secs(2));
    Ok(())
}

fn drain_stderr(pipe: &mut Pipe, forward_phase_markers: bool) -> Result<(Vec<u8>, bool)> {
    const STDERR_LIMIT: usize = 1024 * 1024;
    let mut captured = Vec::new();
    let mut overflow = false;
    let mut forwarded = 0usize;
    let mut buffer = [0u8; 4096];
    loop {
        while let Some(n) = pipe.try_read(&mut buffer) {
            if n == 0 {
                return Ok((captured, overflow));
            }
            if forward_phase_markers {
                const FORWARD_LIMIT: usize = 8192;
                let remaining = FORWARD_LIMIT.saturating_sub(forwarded);
                let keep = n.min(remaining);
                if keep > 0 {
                    let _ = std::io::stderr().write_all(&buffer[..keep]);
                    let _ = std::io::stderr().flush();
                    forwarded += keep;
                }
            }
            let remaining = STDERR_LIMIT.saturating_sub(captured.len());
            let keep = n.min(remaining);
            captured.extend_from_slice(&buffer[..keep]);
            overflow |= keep < n;
        }
        thread::sleep(Duration::from_millis(1));
    }
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
                        "PHP bridge read deadline",
                    ));
                }
            }
        }
    }
}
struct DeadlinePipeWriter<'a> {
    pipe: &'a mut PipeTx,
    deadline: Instant,
}
impl Write for DeadlinePipeWriter<'_> {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        if Instant::now() >= self.deadline {
            return Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "PHP bridge write deadline",
            ));
        }
        self.pipe.write(buf)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.pipe.flush()
    }
}

#[derive(Debug)]
struct FixtureFs;
fn fixture_lookup(
    path: &Path,
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
        Some(name) => FILES
            .iter()
            .find(|(allowed, _)| name == *allowed)
            .copied()
            .map(Some)
            .ok_or(FsError::EntryNotFound),
    }
}
impl FileOpener for FixtureFs {
    fn open(
        &self,
        path: &Path,
        options: &OpenOptionsConfig,
    ) -> std::result::Result<Box<dyn VirtualFile + Send + Sync>, FsError> {
        if options.would_mutate() {
            return Err(FsError::PermissionDenied);
        }
        let (_, bytes) = fixture_lookup(path)?.ok_or(FsError::NotAFile)?;
        Ok(Box::new(StaticFile::new(bytes)))
    }
}
impl FileSystem for FixtureFs {
    fn readlink(&self, path: &Path) -> std::result::Result<PathBuf, FsError> {
        fixture_lookup(path)?;
        Err(FsError::InvalidInput)
    }
    fn read_dir(&self, path: &Path) -> std::result::Result<ReadDir, FsError> {
        if fixture_lookup(path)?.is_some() {
            return Err(FsError::BaseNotDirectory);
        }
        let entries = FILES
            .iter()
            .map(|(n, b)| DirEntry {
                path: PathBuf::from(format!("/{n}")),
                metadata: Ok(Metadata {
                    ft: FileType {
                        file: true,
                        ..FileType::default()
                    },
                    accessed: 0,
                    created: 0,
                    modified: 0,
                    len: b.len() as u64,
                }),
            })
            .collect();
        Ok(ReadDir::new(entries))
    }
    fn metadata(&self, path: &Path) -> std::result::Result<Metadata, FsError> {
        Ok(match fixture_lookup(path)? {
            Some((_, bytes)) => Metadata {
                ft: FileType {
                    file: true,
                    ..FileType::default()
                },
                accessed: 0,
                created: 0,
                modified: 0,
                len: bytes.len() as u64,
            },
            None => Metadata {
                ft: FileType {
                    dir: true,
                    ..FileType::default()
                },
                accessed: 0,
                created: 0,
                modified: 0,
                len: 0,
            },
        })
    }
    fn symlink_metadata(&self, p: &Path) -> std::result::Result<Metadata, FsError> {
        self.metadata(p)
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
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = std::result::Result<(), FsError>> + Send + 'a>,
    > {
        Box::pin(async { Err(FsError::PermissionDenied) })
    }
}
