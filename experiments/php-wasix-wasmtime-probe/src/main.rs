use anyhow::{Context, Result, ensure};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::path::Path;
use wasmtime::{
    Config, Engine, ExternType, Linker, MemoryType, Module, SharedMemory, Store,
    StoreLimitsBuilder, Trap,
};
use wasmtime_wasi::{
    I32Exit, WasiCtxBuilder,
    p2::pipe::{MemoryInputPipe, MemoryOutputPipe},
    preview1::{self, WasiP1Ctx},
};

const ARTIFACT: &str = ".reference/php-wasix-8.5.12/php.wasm";
const ARTIFACT_BYTES: usize = 39_727_549;
const ARTIFACT_SHA256: &str = "e64850fde91d98dff0bce2b95ed467591376586469b1f4d8bad5c8759c4edbe4";
const MAX_SHARED_MEMORY_PAGES: u32 = 8192;
const FUEL: u64 = 200_000_000;
const OUTPUT_LIMIT: usize = 64 * 1024;

struct State {
    wasi: WasiP1Ctx,
    limits: wasmtime::StoreLimits,
}

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn main() {
    let mut phase = "input";
    let mut guest_executed = false;
    let outcome = run(&mut phase, &mut guest_executed);
    match outcome {
        Ok(report) => println!("{}", report),
        Err(error) => {
            println!(
                "{}",
                json!({
                    "schema": "recastpress.php-wasix-wasmtime-version-probe.v0.1",
                    "passed": false,
                    "phase": phase,
                    "failure": format!("{error:#}"),
                    "guest_executed": guest_executed,
                    "product_runtime": false,
                    "hook_adapter_executed": false,
                })
            );
            std::process::exit(1);
        }
    }
}

fn run(phase: &mut &'static str, guest_executed: &mut bool) -> Result<serde_json::Value> {
    ensure!(
        std::env::args_os().len() == 1,
        "fixed_probe_accepts_no_arguments"
    );
    *phase = "artifact_read";
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let artifact = root.join(ARTIFACT);
    let bytes = std::fs::read(&artifact).context("fixed_php_wasix_module_read")?;
    ensure!(bytes.len() == ARTIFACT_BYTES, "fixed_php_wasix_module_size");
    let artifact_sha256 = sha(&bytes);
    ensure!(
        artifact_sha256 == ARTIFACT_SHA256,
        "fixed_php_wasix_module_hash"
    );

    let mut config = Config::new();
    config
        .consume_fuel(true)
        .wasm_threads(true)
        .wasm_exceptions(true)
        .cranelift_opt_level(wasmtime::OptLevel::None);
    let engine = Engine::new(&config)?;
    *phase = "module_compile";
    let module = Module::new(&engine, &bytes).context("php_wasix_module_compile")?;
    *phase = "module_import_validation";
    let mut namespaces = std::collections::BTreeMap::<String, usize>::new();
    let mut imported_memory_count = 0usize;
    let mut unknown_function_count = 0usize;
    for import in module.imports() {
        *namespaces.entry(import.module().to_owned()).or_default() += 1;
        match import.ty() {
            ExternType::Memory(_) => imported_memory_count += 1,
            ExternType::Func(_) if import.module() != "wasi_snapshot_preview1" => {
                unknown_function_count += 1;
            }
            ExternType::Func(_) => {}
            _ => anyhow::bail!("fixed_probe_unsupported_nonfunction_import"),
        }
    }
    ensure!(
        imported_memory_count == 1,
        "fixed_probe_memory_import_count"
    );
    ensure!(
        namespaces.get("wasi_snapshot_preview1") == Some(&37)
            && namespaces.get("wasix_32v1") == Some(&43)
            && namespaces.get("wasi") == Some(&1)
            && namespaces.get("env") == Some(&1),
        "fixed_php_wasix_import_surface"
    );

    let stdout = MemoryOutputPipe::new(OUTPUT_LIMIT);
    let stderr = MemoryOutputPipe::new(OUTPUT_LIMIT);
    let mut wasi = WasiCtxBuilder::new();
    wasi.args(&["php.wasm", "--version"])
        .env("LC_ALL", "C")
        .stdin(MemoryInputPipe::new(Vec::new()))
        .stdout(stdout.clone())
        .stderr(stderr.clone());
    let mut store = Store::new(
        &engine,
        State {
            wasi: wasi.build_p1(),
            limits: StoreLimitsBuilder::new()
                .memory_size(256 * 1024 * 1024)
                .instances(1)
                .memories(1)
                .tables(1)
                .build(),
        },
    );
    store.limiter(|state| &mut state.limits);
    store.set_fuel(FUEL)?;

    *phase = "linker_setup";
    let mut linker = Linker::new(&engine);
    preview1::add_to_linker_sync(&mut linker, |state: &mut State| &mut state.wasi)?;
    for import in module.imports() {
        if let ExternType::Memory(memory_type) = import.ty() {
            ensure!(
                memory_type.is_shared(),
                "unexpected_nonshared_imported_memory"
            );
            let minimum = u32::try_from(memory_type.minimum())?;
            let expected_maximum = u32::try_from(
                memory_type
                    .maximum()
                    .context("shared_memory_maximum_missing")?,
            )?;
            let maximum = expected_maximum.min(MAX_SHARED_MEMORY_PAGES);
            ensure!(minimum <= maximum, "shared_memory_minimum_exceeds_cap");
            let shared = SharedMemory::new(&engine, MemoryType::shared(minimum, maximum))?;
            linker.define(&store, import.module(), import.name(), shared)?;
        }
    }
    // WASIX-only functions are deliberately traps. This proves only the fixed
    // version request; any unsupported host call prevents a passing receipt.
    linker.define_unknown_imports_as_traps(&module)?;

    *phase = "module_instantiate";
    let instance = linker
        .instantiate(&mut store, &module)
        .context("php_wasix_module_instantiate")?;
    let start = instance.get_typed_func::<(), ()>(&mut store, "_start")?;
    *phase = "guest_execution";
    *guest_executed = true;
    let guest = match start.call(&mut store, ()) {
        Err(error)
            if error
                .downcast_ref::<I32Exit>()
                .is_some_and(|exit| exit.0 == 0) =>
        {
            Ok(())
        }
        Err(error) if error.downcast_ref::<Trap>().is_some() => Err(error),
        result => result.map_err(Into::into),
    };
    guest.context("php_wasix_version_guest_execution")?;
    let stdout_bytes = stdout.contents().to_vec();
    let stderr_bytes = stderr.contents().to_vec();
    ensure!(stderr_bytes.is_empty(), "php_wasix_version_stderr_nonempty");
    let version_output = String::from_utf8(stdout_bytes.clone())?;
    ensure!(
        version_output.starts_with("PHP 8.5.12 ") && version_output.ends_with('\n'),
        "php_wasix_version_output_mismatch"
    );
    Ok(json!({
        "schema": "recastpress.php-wasix-wasmtime-version-probe.v0.1",
        "passed": true,
        "outcome": "pinned_php_wasix_8_5_12_version_executed_under_wasmtime",
        "artifact": {"path":ARTIFACT,"bytes":bytes.len(),"sha256":artifact_sha256},
        "host": {"wasmtime":"36.0.19","compiler":"cranelift-noopt","platform":std::env::consts::OS},
        "guest_executed": true,
        "php_version": "8.5.12",
        "version_output": version_output,
        "module_namespaces": namespaces,
        "unknown_function_imports_trap_unless_called": unknown_function_count,
        "shared_memory": {"initial_pages":403,"host_max_pages":MAX_SHARED_MEMORY_PAGES,
            "module_max_pages":65536,"shared_memory_imports":imported_memory_count},
        "fuel_limit": FUEL,
        "product_runtime": false,
        "hook_adapter_executed": false,
        "full_php_compatibility_verified": false,
        "wordpress_bootstrapped": false,
        "rust_peer_executed": false,
        "real_database_connected": false,
    }))
}
