#![forbid(unsafe_code)]
mod capture;
#[path = "../../php-wasi-host-probe/src/cgi_response.rs"]
mod cgi_response;
mod db;
#[path = "../../php-wasix-host-probe/src/fiber_adapter.rs"]
mod fiber_adapter;
mod inputs;
mod readonly_fs;
mod rpc_fs;
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::Read,
    path::{Component, Path},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
use wasmer::{Engine, Module};
use wasmer_wasix::{
    Pipe, PluggableRuntime, UnsupportedVirtualNetworking,
    runners::wasi::{RuntimeOrEngine, WasiRunner},
    runtime::{module_cache::HashedModuleData, task_manager::tokio::TokioTaskManager},
};
const MODULE: &str = "target/php-wasix-managed-access-probe/php-fiber-cgi.wasm";
const RECEIPT: &str = "experiments/php-wasix-managed-access-probe/evidence/build.json";
const PREPARED: &str = "target/php-wasix-wpdb-probe/prepared.json";
const VARIANT: &str = "nts-fresh-wasi-zend-fibers-managed-access-v1";
const FIXTURES: [&str; 4] = ["bridge.php", "helpers.php", "adapter.php", "corpus.php"];
const WP: [&str; 5] = [
    "load.php",
    "plugin.php",
    "class-wp-hook.php",
    "class-wp-filter-sentinel.php",
    "class-wpdb.php",
];
const WP_PINS: &[(&str, usize, &str)] = &[
    (
        "load.php",
        56475,
        "3efd0934bd8a454a8d7076fbbd21504ea097a753125a48e64b8a1dad380d357a",
    ),
    (
        "plugin.php",
        37123,
        "d70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d",
    ),
    (
        "class-wp-hook.php",
        17584,
        "b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e",
    ),
    (
        "class-wp-filter-sentinel.php",
        760,
        "68c9c675d3805a343835728d98a0efedf0df9833cb904bebf909c7e0fda5c61c",
    ),
    (
        "class-wpdb.php",
        121437,
        "e15403e90032dd0508811301505e491d4212de5152581a98f51b744a1a3903bd",
    ),
];
fn fixed(root: &Path, name: &str, limit: u64) -> Result<Vec<u8>> {
    ensure!(
        !Path::new(name).is_absolute()
            && Path::new(name)
                .components()
                .all(|c| matches!(c, Component::Normal(_))),
        "fixed_relative_path"
    );
    let path = root.join(name);
    ensure!(path.canonicalize()? == path, "fixed_file_symlink");
    let f = File::open(path)?;
    let metadata = f.metadata()?;
    ensure!(
        metadata.is_file() && metadata.len() > 0 && metadata.len() <= limit,
        "fixed_file_type_size"
    );
    let mut bytes = Vec::new();
    f.take(limit + 1).read_to_end(&mut bytes)?;
    ensure!(bytes.len() as u64 == metadata.len(), "fixed_file_changed");
    Ok(bytes)
}
fn load_module(root: &Path, report: &mut Value) -> Result<Vec<u8>> {
    let wire = fixed(root, RECEIPT, 2 * 1024 * 1024)?;
    let value: Value = serde_json::from_slice(&wire)?;
    ensure!(
        value["schema"] == "recastpress.php-wasix-managed-access-build.v0.5"
            && value["variant"] == VARIANT
            && value["phase"] == "build_complete"
            && value["outcome"] == "managed_access_candidate_built_not_executed"
            && value["guest_executed"] == false,
        "managed_build_profile"
    );
    let mut bindings = vec![];
    for (path, expected) in inputs::MANAGED_SOURCES {
        let bytes = fixed(root, path, 256 * 1024)?;
        ensure!(bytes == expected, "compiled_managed_source_changed");
        if !path.contains("/evidence/native-") {
            ensure!(
                value["input_files"][path] == db::sha(&bytes),
                "managed_input_binding"
            );
        }
        bindings.push(json!({"path":path,"sha256":db::sha(&bytes),"bytes":bytes.len()}));
    }
    let patches = value["source_patches"]
        .as_array()
        .context("source_patches")?;
    ensure!(
        patches.len() == 3 && patches.iter().all(|p| p["applied"] == true),
        "three_patch_chain"
    );
    let bytes = fixed(root, MODULE, 128 * 1024 * 1024)?;
    ensure!(
        value["php_wasm"]["path"] == MODULE
            && value["php_wasm"]["wasm_header_verified"] == true
            && value["php_wasm"]["executed"] == false
            && bytes.starts_with(b"\0asm\x01\0\0\0")
            && value["php_wasm"]["bytes"].as_u64() == Some(bytes.len() as u64)
            && value["php_wasm"]["sha256"] == db::sha(&bytes),
        "managed_module_identity"
    );
    report["module"] =
        json!({"path":MODULE,"bytes":bytes.len(),"sha256":db::sha(&bytes),"verified":true});
    report["build_receipt"] = json!({"path":RECEIPT,"sha256":db::sha(&wire),"verified":true,"full_chain_verified_by_host":false});
    report["managed_sources"] = json!(bindings);
    report["fiber_adapter"] = fiber_adapter::inspect(&bytes)?;
    Ok(bytes)
}
fn load_sources(
    root: &Path,
    report: &mut Value,
) -> Result<(readonly_fs::ReadOnlyFs, readonly_fs::ReadOnlyFs)> {
    let wire = fixed(root, PREPARED, 1024 * 1024)?;
    let prepared: Value = serde_json::from_slice(&wire)?;
    ensure!(
        prepared["schema"] == "recastpress.php-wasix-wpdb-prepared.v0.5"
            && prepared["outcome"] == "fixed_nine_guest_sources_prepared",
        "prepared_profile"
    );
    let mut bound = vec![];
    for &(path, expected) in inputs::PREPARED_SOURCES {
        let actual = fixed(root, path, 512 * 1024)?;
        ensure!(
            actual == expected && prepared["input_files"][path] == db::sha(&actual),
            "prepared_compiled_input"
        );
        bound.push(json!({"path":path,"sha256":db::sha(&actual),"bytes":actual.len()}));
    }
    ensure!(
        prepared["input_files"]
            .as_object()
            .context("prepared_inputs")?
            .len()
            == inputs::PREPARED_SOURCES.len(),
        "prepared_input_count"
    );
    let files = prepared["files"].as_array().context("prepared_files")?;
    ensure!(
        files.len() == 9
            && prepared["wordpress_files"]
                .as_object()
                .context("wordpress_files")?
                .len()
                == 5,
        "nine_prepared_files"
    );
    let mut seen = BTreeSet::new();
    let mut php = BTreeMap::new();
    let mut wp = BTreeMap::new();
    for item in files {
        let guest = item["guest"].as_str().context("guest_path")?;
        ensure!(
            seen.insert(guest.to_owned()) && item["guest_read_only"] == true,
            "prepared_duplicate_or_writable"
        );
        let source = item["source"].as_str().context("prepared_source")?;
        let data = fixed(root, source, 512 * 1024)?;
        ensure!(
            item["bytes"].as_u64() == Some(data.len() as u64) && item["sha256"] == db::sha(&data),
            "prepared_file_identity"
        );
        if let Some(name) = guest.strip_prefix("/experiments/wpdb-bridge-probe/fixtures/") {
            ensure!(
                FIXTURES.contains(&name)
                    && source == format!("experiments/php-wasix-wpdb-probe/fixtures/{name}")
                    && item["kind"] == "derived_fixture",
                "prepared_fixture_profile"
            );
            let expected = inputs::PREPARED_SOURCES
                .iter()
                .find(|(p, _)| *p == source)
                .context("compiled_fixture")?
                .1;
            ensure!(data == expected, "compiled_fixture_bytes");
            php.insert(name.to_owned(), Arc::<[u8]>::from(data));
        } else if let Some(name) = guest.strip_prefix("/.reference/wordpress-7.1.3/wp-includes/") {
            ensure!(
                WP.contains(&name)
                    && source == format!("target/php-wasix-wpdb-probe/wp-core/{name}")
                    && item["kind"] == "wordpress_source",
                "prepared_wordpress_profile"
            );
            let (_, pinned_bytes, pinned_sha) = WP_PINS
                .iter()
                .find(|(file, _, _)| *file == name)
                .context("wordpress_pin")?;
            ensure!(
                data.len() == *pinned_bytes && db::sha(&data) == *pinned_sha,
                "wordpress_compiled_pin"
            );
            let map = &prepared["wordpress_files"][name];
            ensure!(
                map["path"] == source
                    && map["bytes"] == item["bytes"]
                    && map["sha256"] == item["sha256"],
                "wordpress_map_identity"
            );
            wp.insert(name.to_owned(), Arc::<[u8]>::from(data));
        } else {
            anyhow::bail!("prepared_unexpected_guest");
        }
    }
    ensure!(php.len() == 4 && wp.len() == 5, "prepared_mount_counts");
    report["prepared"] = json!({"path":PREPARED,"sha256":db::sha(&wire),"files":files,"input_files":bound,"verified":true,"full_archive_verified_by_host":false});
    Ok((
        readonly_fs::ReadOnlyFs::new(php),
        readonly_fs::ReadOnlyFs::new(wp),
    ))
}
fn environment(nonce: &str) -> Vec<(String, String)> {
    [
        (
            "SCRIPT_FILENAME",
            "/experiments/wpdb-bridge-probe/fixtures/bridge.php",
        ),
        ("SCRIPT_NAME", "/__rp_wpdb.php"),
        ("DOCUMENT_ROOT", "/"),
        ("REQUEST_METHOD", "GET"),
        ("QUERY_STRING", "rp_wpdb=1"),
        ("REQUEST_URI", "/__rp_wpdb.php?rp_wpdb=1"),
        ("CONTENT_LENGTH", "0"),
        ("REDIRECT_STATUS", "200"),
        ("SERVER_PROTOCOL", "HTTP/1.1"),
        ("GATEWAY_INTERFACE", "CGI/1.1"),
        ("SERVER_NAME", "localhost"),
        ("SERVER_PORT", "80"),
        ("RP_WPDB_NONCE", nonce),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_owned(), v.to_owned()))
    .collect()
}
fn execute(
    engine: &Engine,
    module: Module,
    data: &HashedModuleData,
    php: readonly_fs::ReadOnlyFs,
    wp: readonly_fs::ReadOnlyFs,
    rpc: &rpc_fs::RpcFs,
    nonce: &str,
    record: &mut Value,
) -> Result<Value> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let guard = runtime.enter();
    let (stdin_sender, stdin_reader) = Pipe::channel();
    drop(stdin_sender);
    let (stdout_sender, stdout_reader) = Pipe::channel();
    let (stderr_sender, stderr_reader) = Pipe::channel();
    let out_overflow = Arc::new(AtomicBool::new(false));
    let err_overflow = Arc::new(AtomicBool::new(false));
    let start = Instant::now();
    let outcome = (|| -> Result<()> {
        let tasks = TokioTaskManager::new(runtime.handle().clone());
        let mut implementation = PluggableRuntime::new(Arc::new(tasks));
        implementation.set_engine(engine.clone());
        implementation.set_networking_implementation(UnsupportedVirtualNetworking::default());
        implementation.http_client = None;
        let mut runner = WasiRunner::new();
        runner
            .with_args([
                "-n",
                "-d",
                "fiber.stack_size=2097152",
                "-d",
                "register_argc_argv=0",
            ])
            .with_envs(environment(nonce))
            .with_forward_host_env(false)
            .with_home_mapped(false)
            .with_asynchronous_threading(true)
            .with_current_dir("/")
            .with_mount(
                "/experiments/wpdb-bridge-probe/fixtures".into(),
                Arc::new(php),
            )
            .with_mount(
                "/.reference/wordpress-7.1.3/wp-includes".into(),
                Arc::new(wp),
            )
            .with_mount("/rp-wpdb".into(), Arc::new(rpc.clone()))
            .with_stdin(Box::new(stdin_reader))
            .with_stdout(Box::new(capture::LimitedPipe::new(
                stdout_sender,
                out_overflow.clone(),
            )))
            .with_stderr(Box::new(capture::LimitedPipe::new(
                stderr_sender,
                err_overflow.clone(),
            )));
        let caps = runner.capabilities_mut();
        caps.insecure_allow_all = false;
        caps.threading.max_threads = Some(1);
        caps.threading.enable_deep_sleep = false;
        let result = runner.run_wasm(
            RuntimeOrEngine::Runtime(Arc::new(implementation)),
            "php-wpdb",
            module,
            *data.hash(),
        );
        drop(runner);
        result.map_err(|_| anyhow::anyhow!("guest_execution_failed"))
    })();
    record["execution_elapsed_ms"] = json!(start.elapsed().as_millis());
    record["guest_exit_success"] = json!(outcome.is_ok());
    drop(guard);
    runtime.shutdown_timeout(Duration::from_secs(2));
    record["runtime_shutdown_returned"] = json!(true);
    let stdout = capture::collect_pipe(stdout_reader);
    let stderr = capture::collect_pipe(stderr_reader);
    record["stdout_eof"] = json!(stdout.is_ok());
    record["stderr_eof"] = json!(stderr.is_ok());
    record["stdout_overflow"] = json!(out_overflow.load(Ordering::Relaxed));
    record["stderr_overflow"] = json!(err_overflow.load(Ordering::Relaxed));
    for (key, bytes) in [("stdout", &stdout), ("stderr", &stderr)] {
        if let Ok(bytes) = bytes {
            record[format!("{key}_bytes")] = json!(bytes.len());
            record[format!("{key}_hex")] = json!(db::hex(bytes));
            record[format!("{key}_sha256")] = json!(db::sha(bytes));
        }
    }
    outcome?;
    ensure!(
        start.elapsed() < Duration::from_secs(60),
        "guest_execution_elapsed_limit"
    );
    ensure!(
        !out_overflow.load(Ordering::Relaxed) && !err_overflow.load(Ordering::Relaxed),
        "capture_overflow"
    );
    ensure!(stderr?.is_empty(), "guest_stderr");
    let response = cgi_response::parse(&stdout?)?;
    record["response"] = json!({"status":response.status,"headers":response.headers.iter().map(|(name,value)|json!({"name":name,"value_hex":db::hex(value)})).collect::<Vec<_>>(),"body_bytes":response.body.len(),"body_hex":db::hex(&response.body),"body_sha256":db::sha(&response.body),"representation_length":response.representation_length});
    ensure!(
        response.status == 200
            && response.body.ends_with(b"\n")
            && response.body.iter().filter(|&&b| b == b'\n').count() == 1,
        "cgi_complete_json_line"
    );
    let value: Value = serde_json::from_slice(&response.body)?;
    Ok(value)
}
fn probe(input: db::Input, report: &mut Value) -> Result<()> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()?;
    let bytes = load_module(&root, report)?;
    let (php, wp) = load_sources(&root, report)?;
    report["phase"] = json!("module_compile");
    eprintln!("WPDB_WASIX_PHASE=module_compile");
    let engine: Engine = wasmer_compiler_cranelift::Cranelift::default().into();
    let data = HashedModuleData::new(bytes);
    let compiled = Instant::now();
    report["module_compilations"] = json!(1);
    let module = Module::new(&engine, &data.wasm()[..])?;
    report["compile_elapsed_ms"] = json!(compiled.elapsed().as_millis());
    // Re-read identity after compilation before opening a physical session.
    let _ = load_module(&root, report)?;
    let _ = load_sources(&root, report)?;
    let nonce = input.nonce.clone();
    let mut actor = db::Actor::start(input).map_err(|reason| anyhow::anyhow!(reason))?;
    let rpc = rpc_fs::RpcFs::new(nonce.clone(), actor.dispatch.clone());
    report["phase"] = json!("cgi_execution");
    report["guest_execution_started"] = json!(true);
    eprintln!("WPDB_WASIX_PHASE=cgi_execution");
    let outcome = execute(
        &engine,
        module,
        &data,
        php,
        wp,
        &rpc,
        &nonce,
        &mut report["cgi_capture"],
    );
    report["rpc"] = rpc.finish();
    report["db_actor_joined"] = json!(actor.finish());
    {
        let s = actor
            .observation
            .lock()
            .map_err(|_| anyhow::anyhow!("actor_observation_poisoned"))?;
        report["operations"] = json!(s.operations);
        report["physical_connection_created"] = json!(s.connection_created);
        report["physical_disconnect_attempted"] = json!(s.disconnect_attempted);
        report["physical_disconnect_completed"] = json!(s.disconnect_completed);
        report["db_actor_failure"] = json!(s.failure);
        report["db_actor_exited"] = json!(s.exited);
    }
    if let Ok(value) = &outcome {
        report["php_result"] = value.clone();
    }
    let recheck = (|| -> Result<()> {
        let _ = load_module(&root, report)?;
        let _ = load_sources(&root, report)?;
        Ok(())
    })();
    report["source_rechecked_after_execution"] = json!(recheck.is_ok());
    recheck?;
    let value = outcome?;
    ensure!(
        value["schema"] == "recastpress.wpdb-bridge-php-observation.v0.5"
            && value["outcome"] == "fixed_shared_wpdb_cases_passed"
            && value["passed"] == true
            && value["connected"] == true
            && value["close_completed"] == true
            && value["included_source_gate_passed"] == true
            && value["constructor_connection_deferred"] == true
            && value["bootstrap_num_queries"] == 0
            && value["final_num_queries"] == 25
            && value["observations"]
                .as_array()
                .is_some_and(|a| a.len() == 27)
            && value["runtime"]["sapi"] == "cgi-fcgi"
            && value["wasm_executed"] == true,
        "fixed_php_result"
    );
    ensure!(
        report["rpc"]["finished"] == true
            && report["rpc"]["failure"].is_null()
            && report["rpc"]["channel_open"] == false
            && report["rpc"]["pending_bytes"] == 0
            && report["rpc"]["unread_response_bytes"] == 0
            && report["rpc"]["response_pending"] == false
            && report["physical_disconnect_completed"] == true
            && report["db_actor_joined"] == true
            && report["db_actor_exited"] == true
            && report["db_actor_failure"].is_null(),
        "owned_rpc_completion"
    );
    report["phase"] = json!("wpdb_cgi_contract_verified");
    Ok(())
}
fn main() {
    let mut report = json!({"schema":"recastpress.php-wasix-wpdb-observation.v0.5","outcome":"fixed_wasix_wpdb_cases_failed","passed":false,"phase":"input","operations":[],"cgi_capture":{},"php_result":null,"physical_connection_created":false,"physical_disconnect_completed":false,"db_actor_joined":false,"fresh_runtime":true,"module_compilations":0,"guest_networking":false,"host_environment_forwarded":false,"db_credentials_supplied_to_guest":false,"native_php_fallback":false,"product_runtime":false,"full_wordpress_bootstrap":false,"standard_wpdb_compatibility_proven":false,"managed_permission_query_exercised":false,"scope":"fixed-twenty-seven-upstream-wpdb-cases-through-private-WASIX-file-channel","limits":{"db_operation_ms":10000,"db_actor_execution_ms":60000,"disconnect_cleanup_ms":2000,"actor_join_cleanup_ms":3000,"runtime_shutdown_ms":2000,"capture_cleanup_ms":2000,"synchronous_guest_cpu_cancellation":false,"outer_owned_process_deadline_required":true},"error":null});
    let mut wire = Vec::new();
    let mut secrets = Vec::new();
    let result = (|| -> Result<()> {
        ensure!(std::env::args_os().count() == 1, "closed_arguments");
        std::io::stdin()
            .take(20 * 1024 + 1)
            .read_to_end(&mut wire)?;
        let input = db::parse_input(&wire).map_err(|reason| anyhow::anyhow!(reason))?;
        secrets.push(input.password.clone());
        secrets.push(input.nonce.clone());
        probe(input, &mut report)
    })();
    if result.is_ok() {
        report["passed"] = json!(true);
        report["outcome"] = json!("fixed_wasix_wpdb_cases_passed");
    } else {
        report["error"] = json!("fixed_probe_failed");
        // Only our fixed machine identifiers are public. Never format arbitrary
        // driver, guest, OS or compiler diagnostics into a receipt.
        if let Err(error) = &result {
            let code = error.to_string();
            if !code.is_empty()
                && code.len() <= 80
                && code.bytes().all(|b| b.is_ascii_lowercase() || b == b'_')
            {
                report["failure_reason"] = json!(code);
            }
        }
    }
    let encoded = serde_json::to_string(&report).unwrap_or_default();
    if encoded.is_empty()
        || secrets
            .iter()
            .any(|secret| encoded.contains(secret) || encoded.contains(&db::hex(secret.as_bytes())))
    {
        println!(
            "{{\"schema\":\"recastpress.php-wasix-wpdb-observation.v0.5\",\"passed\":false,\"outcome\":\"fixed_wasix_wpdb_cases_failed\",\"error\":\"public_record_secret_gate\"}}"
        );
        std::process::exit(1);
    }
    println!("{encoded}");
    if result.is_err() {
        std::process::exit(1);
    }
}
