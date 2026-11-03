#![forbid(unsafe_code)]
#![recursion_limit = "256"]
mod access_fs;
mod capture;
mod catalog;
#[path = "../../php-wasi-host-probe/src/cgi_response.rs"]
mod cgi_response;
mod db;
#[path = "../../php-wasix-host-probe/src/fiber_adapter.rs"]
mod fiber_adapter;
#[allow(dead_code)]
#[path = "../../php-wasix-wpdb-probe/src/inputs.rs"]
mod inputs;
mod result_budget;
mod rpc_fs;
use anyhow::{Context, Result, ensure};
use recastpress_wp_source_fs_probe::SourceFs;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{File, OpenOptions},
    io::{Read, Write},
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
const PREPARED: &str = "target/php-wasix-wp-bootstrap-probe/prepared.json";
const VARIANT: &str = "nts-fresh-wasi-zend-fibers-managed-access-v1";
const BASE: &str = "experiments/php-wasix-wp-bootstrap-probe/";
const OWNED: &[(&str, &[u8])] = &[
    ("install.php", include_bytes!("../fixtures/install.php")),
    ("read.php", include_bytes!("../fixtures/read.php")),
    ("bootstrap.php", include_bytes!("../fixtures/bootstrap.php")),
    ("helpers.php", include_bytes!("../fixtures/helpers.php")),
    ("adapter.php", include_bytes!("../fixtures/adapter.php")),
    ("db.php", include_bytes!("../fixtures/db.php")),
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
    ensure!(path.canonicalize()? == path, "fixed_file_alias");
    let f = File::open(path)?;
    let m = f.metadata()?;
    ensure!(m.is_file() && m.len() <= limit, "fixed_file_quota");
    let mut b = vec![];
    f.take(limit + 1).read_to_end(&mut b)?;
    ensure!(b.len() as u64 == m.len(), "fixed_file_changed");
    Ok(b)
}
fn load_module(root: &Path, report: &mut Value) -> Result<Vec<u8>> {
    let wire = fixed(root, RECEIPT, 2 * 1024 * 1024)?;
    let v: Value = serde_json::from_slice(&wire)?;
    ensure!(
        v["schema"] == "recastpress.php-wasix-managed-access-build.v0.5"
            && v["variant"] == VARIANT
            && v["phase"] == "build_complete"
            && v["outcome"] == "managed_access_candidate_built_not_executed"
            && v["guest_executed"] == false,
        "managed_build_profile"
    );
    for (p, expected) in inputs::MANAGED_SOURCES {
        let b = fixed(root, p, 256 * 1024)?;
        ensure!(b == expected, "compiled_managed_source_changed");
        if !p.contains("/evidence/native-") {
            ensure!(v["input_files"][p] == db::sha(&b), "managed_input_binding");
        }
    }
    let patches = v["source_patches"].as_array().context("source_patches")?;
    ensure!(
        patches.len() == 3 && patches.iter().all(|p| p["applied"] == true),
        "three_patch_chain"
    );
    let b = fixed(root, MODULE, 128 * 1024 * 1024)?;
    ensure!(
        v["php_wasm"]["path"] == MODULE
            && v["php_wasm"]["wasm_header_verified"] == true
            && v["php_wasm"]["executed"] == false
            && b.starts_with(b"\0asm\x01\0\0\0")
            && v["php_wasm"]["bytes"].as_u64() == Some(b.len() as u64)
            && v["php_wasm"]["sha256"] == db::sha(&b),
        "managed_module_identity"
    );
    report["module"] = json!({"path":MODULE,"bytes":b.len(),"sha256":db::sha(&b),"verified":true});
    report["build_receipt"] = json!({"path":RECEIPT,"sha256":db::sha(&wire),"verified":true,"full_chain_verified_by_host":false});
    report["fiber_adapter"] = fiber_adapter::inspect(&b)?;
    Ok(b)
}
fn finite(files: BTreeMap<String, Arc<[u8]>>) -> Result<SourceFs> {
    let mut dirs = BTreeSet::new();
    for name in files.keys() {
        let mut path = name.as_str();
        while let Some((parent, _)) = path.rsplit_once('/') {
            dirs.insert(parent.to_owned());
            path = parent;
        }
    }
    SourceFs::new(files, dirs).map_err(|_| anyhow::anyhow!("finite_source_tree"))
}
fn load_sources(root: &Path, report: &mut Value) -> Result<(SourceFs, SourceFs, Vec<u8>)> {
    let wire = fixed(root, PREPARED, 2 * 1024 * 1024)?;
    let p: Value = serde_json::from_slice(&wire)?;
    ensure!(
        p["schema"] == "recastpress.php-wasix-wp-bootstrap-prepared.v0.5"
            && p["core_site"] == "target/wp-bootstrap-probe/site",
        "prepared_profile"
    );
    let entries = p["files"].as_array().context("prepared_files")?;
    ensure!(entries.len() == OWNED.len(), "prepared_file_count");
    let mut fixtures = BTreeMap::new();
    let mut dropin = None;
    let mut seen = BTreeSet::new();
    for row in entries {
        let source = row["source"].as_str().context("prepared_source")?;
        let name = source
            .strip_prefix(&format!("{BASE}fixtures/"))
            .context("prepared_source_prefix")?;
        let expected = OWNED
            .iter()
            .find(|(n, _)| *n == name)
            .context("prepared_source_name")?
            .1;
        ensure!(
            seen.insert(name.to_owned()) && row["guest_read_only"] == true,
            "prepared_duplicate_or_writable"
        );
        let b = fixed(root, source, 256 * 1024)?;
        ensure!(
            b == expected
                && row["bytes"].as_u64() == Some(b.len() as u64)
                && row["sha256"] == db::sha(&b),
            "compiled_fixture_binding"
        );
        if name == "db.php" {
            ensure!(
                row["guest"] == "/wordpress/wp-content/db.php"
                    && row["kind"] == "owned_wpdb_dropin",
                "prepared_dropin_mapping"
            );
            dropin = Some(Arc::<[u8]>::from(b));
        } else {
            ensure!(
                row["guest"] == format!("/{BASE}fixtures/{name}")
                    && row["kind"] == "derived_fixture",
                "prepared_fixture_mapping"
            );
            fixtures.insert(name.to_owned(), Arc::<[u8]>::from(b));
        }
    }
    let manifest = fixed(
        root,
        "target/wp-bootstrap-probe/source-manifest.json",
        2 * 1024 * 1024,
    )?;
    ensure!(
        p["core_manifest"]["path"] == "target/wp-bootstrap-probe/source-manifest.json"
            && p["core_manifest"]["bytes"].as_u64() == Some(manifest.len() as u64)
            && p["core_manifest"]["sha256"] == db::sha(&manifest),
        "prepared_core_manifest_binding"
    );
    let prep_bytes = fixed(root, &format!("{BASE}prepare.py"), 512 * 1024)?;
    ensure!(
        prep_bytes == include_bytes!("../prepare.py"),
        "compiled_prepare_source_changed"
    );
    for (path, digest) in p["input_files"]
        .as_object()
        .context("prepared_input_files")?
    {
        ensure!(
            digest == &json!(db::sha(&fixed(root, path, 4 * 1024 * 1024)?)),
            "prepared_input_binding"
        );
    }
    let wp = catalog::load(
        root,
        &mut report["core_source"],
        dropin.context("dropin_missing")?,
    )
    .map_err(|e| anyhow::anyhow!(e))?;
    report["prepared"] = json!({"path":PREPARED,"sha256":db::sha(&wire),"files":entries,"core_files":3782,"owned_dropin_files":1,"source_verified":true});
    Ok((finite(fixtures)?, wp, manifest))
}
fn environment(mode: db::Mode) -> Vec<(String, String)> {
    [
        (
            "SCRIPT_FILENAME",
            format!("/{BASE}fixtures/{}.php", mode.as_str()),
        ),
        ("SCRIPT_NAME", "/__rp_bootstrap.php".into()),
        ("DOCUMENT_ROOT", "/".into()),
        ("REQUEST_METHOD", "GET".into()),
        ("QUERY_STRING", "rp_bootstrap=1".into()),
        ("REQUEST_URI", "/__rp_bootstrap.php?rp_bootstrap=1".into()),
        ("CONTENT_LENGTH", "0".into()),
        ("REDIRECT_STATUS", "200".into()),
        ("SERVER_PROTOCOL", "HTTP/1.1".into()),
        ("GATEWAY_INTERFACE", "CGI/1.1".into()),
        ("SERVER_NAME", "rp-fixture.invalid".into()),
        ("SERVER_PORT", "80".into()),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_owned(), v))
    .collect()
}
fn private_capture(root: &Path, mode: db::Mode, label: &str, bytes: &[u8]) -> Result<()> {
    let dir = root.join("target/php-wasix-wp-bootstrap-probe/private-capture");
    std::fs::create_dir_all(&dir)?;
    ensure!(dir.canonicalize()? == dir, "private_capture_alias");
    let path = dir.join(format!(
        "{}-{}.{label}.bin",
        mode.as_str(),
        std::process::id()
    ));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700))?;
        options.mode(0o600);
    }
    options.open(path)?.write_all(bytes)?;
    Ok(())
}
fn observe_cgi(raw: &[u8], record: &mut Value) -> Result<Value> {
    let response = cgi_response::parse(raw)?;
    record["response"] = json!({"status":response.status,"header_count":response.headers.len(),"body_bytes":response.body.len(),"body_sha256":db::sha(&response.body),"representation_length":response.representation_length});
    ensure!(
        response.body.ends_with(b"\n")
            && response.body.iter().filter(|&&b| b == b'\n').count() == 1,
        "cgi_complete_json_line"
    );
    let value: Value =
        serde_json::from_slice(&response.body).map_err(|_| anyhow::anyhow!("php_json_shape"))?;
    record["safe_php_observation"] = public_php(&value);
    Ok(value)
}
fn execute(
    root: &Path,
    engine: &Engine,
    module: Module,
    data: &HashedModuleData,
    php: SourceFs,
    wp: SourceFs,
    context: SourceFs,
    access: &access_fs::ManagedAccessFs,
    rpc: &rpc_fs::RpcFs,
    mode: db::Mode,
    record: &mut Value,
) -> Result<Value> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let guard = runtime.enter();
    let (stdin_send, stdin_read) = Pipe::channel();
    drop(stdin_send);
    let (out_send, out_read) = Pipe::channel();
    let (err_send, err_read) = Pipe::channel();
    let over = Arc::new(AtomicBool::new(false));
    let err_over = Arc::new(AtomicBool::new(false));
    let start = Instant::now();
    let outcome = (|| -> Result<()> {
        let mut implementation =
            PluggableRuntime::new(Arc::new(TokioTaskManager::new(runtime.handle().clone())));
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
                "-d",
                "allow_url_fopen=0",
                "-d",
                "disable_functions=mail",
            ])
            .with_envs(environment(mode))
            .with_forward_host_env(false)
            .with_home_mapped(false)
            .with_asynchronous_threading(true)
            .with_current_dir("/")
            .with_mount(format!("/{BASE}fixtures").into(), Arc::new(php))
            .with_mount("/wordpress".into(), Arc::new(wp))
            .with_mount("/rp-bootstrap".into(), Arc::new(context))
            .with_mount("/rp-p0-fs".into(), Arc::new(access.clone()))
            .with_mount("/rp-wpdb".into(), Arc::new(rpc.clone()))
            .with_stdin(Box::new(stdin_read))
            .with_stdout(Box::new(capture::LimitedPipe::new(out_send, over.clone())))
            .with_stderr(Box::new(capture::LimitedPipe::new(
                err_send,
                err_over.clone(),
            )));
        let caps = runner.capabilities_mut();
        caps.insecure_allow_all = false;
        caps.threading.max_threads = Some(1);
        caps.threading.enable_deep_sleep = false;
        let result = runner.run_wasm(
            RuntimeOrEngine::Runtime(Arc::new(implementation)),
            "php-bootstrap",
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
    let stdout = capture::collect_pipe(out_read);
    let stderr = capture::collect_pipe(err_read);
    record["stdout_eof"] = json!(stdout.is_ok());
    record["stderr_eof"] = json!(stderr.is_ok());
    record["stdout_overflow"] = json!(over.load(Ordering::Relaxed));
    record["stderr_overflow"] = json!(err_over.load(Ordering::Relaxed));
    for (label, bytes) in [("stdout", &stdout), ("stderr", &stderr)] {
        if let Ok(bytes) = bytes {
            private_capture(root, mode, label, bytes)?;
            record[format!("{label}_bytes")] = json!(bytes.len());
            record[format!("{label}_sha256")] = json!(db::sha(bytes));
        }
    }
    // A complete JSON failure can be safely diagnosed even when PHP exits
    // nonzero. Acceptance still requires actual run success and clean capture.
    let decoded = stdout
        .as_ref()
        .map_err(|_| anyhow::anyhow!("stdout_incomplete"))
        .and_then(|b| observe_cgi(b, record));
    outcome?;
    ensure!(
        start.elapsed() < Duration::from_secs(180),
        "guest_elapsed_limit"
    );
    ensure!(
        !over.load(Ordering::Relaxed) && !err_over.load(Ordering::Relaxed),
        "capture_overflow"
    );
    ensure!(stderr?.is_empty(), "guest_stderr");
    ensure!(record["response"]["status"] == 200, "cgi_status");
    decoded
}
fn completion(mode: db::Mode, value: &Value) -> bool {
    value["schema"] == "recastpress.php-wasix-wp-bootstrap-php-observation.v0.5"
        && value["passed"] == true
        && value["mode"] == mode.as_str()
        && value["phase"] == "complete"
        && value["outcome"] == "fixed_wasix_bootstrap_passed"
        && value["rust_executed"] == true
        && value["native_mysqli_handle"] == false
        && value["source_inventory_verified"] == true
        && value["core_files_unchanged"] == true
        && value["bootstrap_completed"] == true
        && value["shortinit_false"] == true
        && value["wp_version"] == "7.1.3"
        && value["wasm_executed"] == true
        && value["known_wp_table_count"] == 12
        && value["physical_disconnect_completed"] == true
        && value["warning_count"] == 0
        && value["wp_installing"] == json!(mode == db::Mode::Install)
        && value["public_options"]
            == json!({"blogname":"RP Bootstrap Fixture","siteurl":"http://rp-fixture.invalid","home":"http://rp-fixture.invalid"})
        && value["public_posts"] == expected_posts()
        && (mode != db::Mode::Install || value["install_completed"] == true)
}
fn expected_posts() -> Value {
    json!([
        {"ID":1,"type":"post","status":"publish","title":"Hello world!","slug":"hello-world"},
        {"ID":2,"type":"page","status":"publish","title":"Sample Page","slug":"sample-page"}
    ])
}
fn public_php(value: &Value) -> Value {
    // Copy only fixed public observations; never preserve unexpected PHP fields.
    let mut safe = serde_json::Map::new();
    for key in [
        "passed",
        "bootstrap_completed",
        "install_completed",
        "physical_disconnect_completed",
        "source_inventory_verified",
        "core_files_unchanged",
        "blocked_mail_calls",
        "blocked_http_calls",
        "warning_count",
        "shortinit_false",
        "wp_installing",
        "known_wp_table_count",
        "autoload_option_count",
        "autoload_max_value_bytes",
        "wasm_executed",
        "rust_executed",
        "native_mysqli_handle",
        "site_config_file_created",
        "source_core_theme_plugin_modified",
        "outbound_mail_or_http_sent_by_fixture",
        "dropin_verified",
    ] {
        if let Some(v) = value.get(key) {
            if v.is_boolean() || v.is_number() {
                safe.insert(key.into(), v.clone());
            }
        }
    }
    for (key, allowed) in [
        (
            "schema",
            vec!["recastpress.php-wasix-wp-bootstrap-php-observation.v0.5"],
        ),
        ("mode", vec!["install", "read"]),
    ] {
        if value[key].as_str().is_some_and(|s| allowed.contains(&s)) {
            safe.insert(key.into(), value[key].clone());
        }
    }
    for (key, name) in [
        ("phase", "RP_PUBLIC_PHASES"),
        ("failure", "RP_PUBLIC_FAILURE_CODES"),
        ("failure_class", "RP_PUBLIC_FAILURE_CLASSES"),
    ] {
        if value[key]
            .as_str()
            .is_some_and(|s| public_literal(name).iter().any(|v| v == s))
        {
            safe.insert(key.into(), value[key].clone());
        }
    }
    if value["wp_version"] == "7.1.3" {
        safe.insert("wp_version".into(), json!("7.1.3"));
    }
    if [
        "fixed_wasix_bootstrap_passed",
        "fixed_wasix_bootstrap_failed",
    ]
    .iter()
    .any(|s| value["outcome"] == *s)
    {
        safe.insert("outcome".into(), value["outcome"].clone());
    }
    if value["public_options"]
        == json!({"blogname":"RP Bootstrap Fixture","siteurl":"http://rp-fixture.invalid","home":"http://rp-fixture.invalid"})
    {
        safe.insert("public_options".into(), value["public_options"].clone());
    }
    if value["public_posts"] == expected_posts() {
        safe.insert("public_posts".into(), expected_posts());
    }
    Value::Object(safe)
}
fn public_literal(name: &str) -> Vec<String> {
    let prefix = format!("const {name} = ");
    include_str!("../fixtures/bootstrap.php")
        .lines()
        .find_map(|line| line.strip_prefix(&prefix))
        .and_then(|s| s.strip_suffix(';'))
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or_default()
}
fn probe(input: db::Input, report: &mut Value) -> Result<()> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()?;
    let bytes = load_module(&root, report)?;
    let (php, wp, manifest) = load_sources(&root, report)?;
    let context = serde_json::to_vec(&input.context())?;
    let context = finite(BTreeMap::from([
        ("context.json".into(), Arc::<[u8]>::from(context)),
        ("source-manifest.json".into(), Arc::<[u8]>::from(manifest)),
    ]))?;
    report["phase"] = json!("module_compile");
    let engine: Engine = wasmer_compiler_cranelift::Cranelift::default().into();
    let data = HashedModuleData::new(bytes);
    let compiled = Instant::now();
    report["module_compilations"] = json!(1);
    let module = Module::new(&engine, &data.wasm()[..])?;
    report["compile_elapsed_ms"] = json!(compiled.elapsed().as_millis());
    let _ = load_module(&root, report)?;
    let _ = load_sources(&root, report)?;
    let mode = input.mode;
    let nonce = input.nonce.clone();
    let mut actor = db::Actor::start(input).map_err(|e| anyhow::anyhow!(e))?;
    let rpc = rpc_fs::RpcFs::new(nonce, actor.dispatch.clone());
    let access = access_fs::ManagedAccessFs::new(wp.clone(), php.clone(), context.clone())?;
    report["phase"] = json!("cgi_execution");
    report["guest_execution_started"] = json!(true);
    let result = execute(
        &root,
        &engine,
        module,
        &data,
        php,
        wp,
        context,
        &access,
        &rpc,
        mode,
        &mut report["cgi_capture"],
    );
    if let Some(safe) = report["cgi_capture"].get("safe_php_observation").cloned() {
        report["php_result"] = safe;
    }
    report["rpc"] = rpc.finish();
    let access_result = access.finish();
    if let Ok(snapshot) = access.snapshot() {
        report["managed_access"] = snapshot.report_value();
    }
    report["db_actor_joined"] = json!(actor.finish());
    {
        let s = actor
            .observation
            .lock()
            .map_err(|_| anyhow::anyhow!("actor_observation_poisoned"))?;
        report["operations"] = json!(s.operations);
        report["physical_connection_created"] = json!(s.connection_created);
        report["physical_disconnect_completed"] = json!(s.disconnect_completed);
        report["physical_disconnect_attempted"] = json!(s.disconnect_attempted);
        report["db_actor_failure"] = json!(s.failure);
        report["db_actor_exited"] = json!(s.exited);
    }
    if let Ok(value) = &result {
        report["php_result"] = public_php(value);
    }
    let check = (|| -> Result<()> {
        let _ = load_module(&root, report)?;
        let _ = load_sources(&root, report)?;
        Ok(())
    })();
    report["source_rechecked_after_execution"] = json!(check.is_ok());
    check?;
    let php = result?;
    access_result?;
    ensure!(
        report["managed_access"]["requests"]
            .as_u64()
            .is_some_and(|n| n > 0)
            && report["managed_access"]["finished"] == true
            && report["managed_access"]["protocol_error"].is_null(),
        "managed_access_completion"
    );
    ensure!(completion(mode, &php), "full_bootstrap_php_contract");
    ensure!(
        report["rpc"]["finished"] == true
            && report["rpc"]["failure"].is_null()
            && report["rpc"]["channel_open"] == false
            && report["rpc"]["pending_bytes"] == 0
            && report["rpc"]["unread_response_bytes"] == 0
            && report["rpc"]["response_pending"] == false
            && report["physical_connection_created"] == true
            && report["physical_disconnect_completed"] == true
            && report["db_actor_joined"] == true
            && report["db_actor_exited"] == true
            && report["db_actor_failure"].is_null(),
        "owned_rpc_completion"
    );
    report["full_wordpress_bootstrap"] = json!(true);
    report["phase"] = json!("full_bootstrap_verified");
    Ok(())
}
fn main() {
    let mut report = json!({"schema":"recastpress.php-wasix-wp-bootstrap-observation.v0.5","passed":false,"outcome":"fixed_wasix_wp_bootstrap_failed","phase":"input","mode":null,"operations":[],"cgi_capture":{},"php_result":null,"physical_connection_created":false,"physical_disconnect_completed":false,"db_actor_joined":false,"full_wordpress_bootstrap":false,"guest_execution_started":false,"fresh_runtime":true,"module_compilations":0,"guest_networking":false,"host_environment_forwarded":false,"host_home_mapped":false,"db_credentials_supplied_to_guest":false,"native_php_fallback":false,"product_runtime":false,"standard_wpdb_compatibility_proven":false,"raw_error_bytes_preserved":false,"server_error_string_lossy_gap_unresolved":true,"native_metadata_compatibility_verified":false,"underlying_virtual_file_counts_only":true,"limits":{"operation_ms":10000,"actor_execution_ms":120000,"actor_join_ms":3000,"disconnect_cleanup_ms":2000,"runtime_shutdown_ms":2000,"capture_cleanup_ms":2000,"query_or_escape_bytes":65536,"request_frame_bytes":262144,"reply_frame_bytes":524288,"operations":1024,"request_total_bytes":8388608,"reply_total_bytes":16777216,"rows":256,"columns":32,"cell_bytes":65536,"raw_result_bytes":262144,"metadata_bytes":4096,"managed_access_max_operations":16384,"managed_access_max_frame_bytes":4103,"managed_access_max_path_bytes":4096,"synchronous_guest_cpu_cancellation":false,"outer_owned_process_deadline_required":true}});
    let mut secrets = vec![];
    let result = (|| -> Result<()> {
        ensure!(std::env::args_os().count() == 1, "closed_arguments");
        let mut wire = vec![];
        std::io::stdin()
            .take(20 * 1024 + 1)
            .read_to_end(&mut wire)?;
        let input = db::parse_input(&wire).map_err(|e| anyhow::anyhow!(e))?;
        secrets = input.secrets();
        report["mode"] = json!(input.mode.as_str());
        probe(input, &mut report)
    })();
    if result.is_ok() {
        report["passed"] = json!(true);
        report["outcome"] = json!("fixed_wasix_wp_bootstrap_passed");
    } else {
        report["error"] = json!("fixed_probe_failed");
        if let Err(e) = &result {
            let code = e.to_string();
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
            .any(|s| encoded.contains(s) || encoded.contains(&db::hex(s.as_bytes())))
    {
        println!(
            "{{\"schema\":\"recastpress.php-wasix-wp-bootstrap-observation.v0.5\",\"passed\":false,\"outcome\":\"fixed_wasix_wp_bootstrap_failed\",\"error\":\"public_record_secret_gate\"}}"
        );
        std::process::exit(1);
    }
    println!("{encoded}");
    if result.is_err() {
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn complete_cgi_failure_keeps_only_source_bound_public_diagnostics() {
        let php = json!({"schema":"recastpress.php-wasix-wp-bootstrap-php-observation.v0.5","mode":"install","passed":false,"phase":"wp_settings","failure":"php_warning","failure_class":"RpBootstrapFailure","message":"private-secret-SQL"});
        let raw = format!("Content-Type: application/json\r\n\r\n{php}\n");
        let mut capture = json!({});
        let actual = observe_cgi(raw.as_bytes(), &mut capture).unwrap();
        assert!(!completion(db::Mode::Install, &actual));
        assert_eq!(capture["safe_php_observation"]["phase"], "wp_settings");
        assert_eq!(capture["safe_php_observation"]["failure"], "php_warning");
        assert!(!capture.to_string().contains("private-secret-SQL"));
        let unknown = public_php(
            &json!({"phase":"unapproved-phase","failure":"unapproved_code","failure_class":"UnapprovedClass"}),
        );
        assert!(unknown.as_object().unwrap().is_empty());
        assert!(!public_literal("RP_PUBLIC_PHASES").is_empty());
        assert!(!public_literal("RP_PUBLIC_FAILURE_CODES").is_empty());
        assert!(!public_literal("RP_PUBLIC_FAILURE_CLASSES").is_empty());
    }
    #[test]
    fn public_php_excludes_unexpected_rows_secrets_and_nonfixture_titles() {
        let secret = "password-hash-must-stay-private";
        let raw = json!({"sql":secret,"data_hex":db::hex(secret.as_bytes()),"unknown":secret,"public_posts":[{"ID":1,"type":"post","status":"publish","title":secret,"slug":"hello-world"}],"warning_count":0,"public_options":{"blogname":secret}});
        let safe = public_php(&raw);
        assert!(!safe.to_string().contains(secret));
        assert!(
            safe.get("sql").is_none()
                && safe.get("data_hex").is_none()
                && safe.get("public_posts").is_none()
        );
    }
    #[test]
    fn cgi_environment_carries_neither_database_nor_nonce_context() {
        let install: BTreeMap<_, _> = environment(db::Mode::Install).into_iter().collect();
        let read: BTreeMap<_, _> = environment(db::Mode::Read).into_iter().collect();
        assert_eq!(install["QUERY_STRING"], "rp_bootstrap=1");
        assert!(install["SCRIPT_FILENAME"].ends_with("install.php"));
        assert!(read["SCRIPT_FILENAME"].ends_with("read.php"));
        assert!(
            install
                .keys()
                .all(|k| !k.contains("PASSWORD") && !k.contains("NONCE") && !k.starts_with("DB_"))
        );
    }
    #[test]
    fn expanded_columns_are_explicit_and_other_result_limits_stay_fixed() {
        use result_budget::{BudgetError, Collector, Column, Limits};
        let col = || Column {
            name: b"x".to_vec(),
            column_type: 253,
            flags: 0,
            character_set: 45,
            declared_length: 16,
            decimals: 0,
            metadata_bytes: 1,
        };
        for n in [23, 32] {
            let mut c = Collector::new(Limits::default(), (0..n).map(|_| col()).collect()).unwrap();
            c.push(&vec![Some(&b"x"[..]); n]).unwrap();
            assert!(c.finish().is_ok());
        }
        assert!(matches!(
            Collector::new(Limits::default(), (0..33).map(|_| col()).collect()),
            Err(BudgetError::ColumnLimit)
        ));
        let l = Limits::default();
        assert_eq!(
            (
                l.rows,
                l.cell_bytes,
                l.total_raw_cell_bytes,
                l.encoded_reply_bytes,
                l.metadata_bytes,
                l.identifier_bytes
            ),
            (256, 65536, 262144, 524288, 4096, 256)
        );
        assert!(matches!(
            Collector::new(Limits { columns: 33, ..l }, vec![]),
            Err(BudgetError::InvalidLimits)
        ));
    }
}
