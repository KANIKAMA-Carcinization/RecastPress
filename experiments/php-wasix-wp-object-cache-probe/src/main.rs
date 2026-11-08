//! Run two fresh private CGI requests against one compiled PHP-Wasm module.
#[path = "../../php-wasix-wp-request-probe/src/access_fs.rs"]
mod access_fs;
mod cache_fs;
mod cache_host;
#[path = "../../php-wasix-wp-request-probe/src/capture.rs"]
mod capture;
#[path = "../../php-wasi-host-probe/src/cgi_response.rs"]
mod cgi_response;
#[path = "../../php-wasix-host-probe/src/fiber_adapter.rs"]
mod fiber_adapter;
#[allow(dead_code)]
#[path = "../../php-wasix-wpdb-probe/src/inputs.rs"]
mod inputs;
#[path = "../../php-wasix-wp-private-worker-probe/src/strict_json.rs"]
mod strict_json;

use anyhow::{Context, Result, ensure};
use recastpress_wp_source_fs_probe::SourceFs;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::{Read, Write},
    path::{Component, Path},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use wasmer::{Engine, Module};
use wasmer_wasix::{
    Pipe, PluggableRuntime, UnsupportedVirtualNetworking,
    runners::wasi::{RuntimeOrEngine, WasiRunner},
    runtime::{module_cache::HashedModuleData, task_manager::tokio::TokioTaskManager},
};

const HERE: &str = "experiments/php-wasix-wp-object-cache-probe";
const MODULE: &str = "target/php-wasix-managed-access-probe/php-fiber-cgi.wasm";
const RECEIPT: &str = "experiments/php-wasix-managed-access-probe/evidence/build.json";
const VARIANT: &str = "nts-fresh-wasi-zend-fibers-managed-access-v1";
const MANIFEST: &str = "target/wp-bootstrap-probe/source-manifest.json";
const MANIFEST_SHA: &str = "734275a54c29e1df269ae5fe34782f567de05a1b3230aad7faed252b37d783b3";
const SITE: &str = "target/wp-bootstrap-probe/site";
const MAX_BODY: usize = 65536;
const FIXTURES: [(&str, &[u8]); 3] = [
    ("entry.php", include_bytes!("../fixtures/entry.php")),
    ("adapter.php", include_bytes!("../fixtures/adapter.php")),
    ("corpus.php", include_bytes!("../fixtures/corpus.php")),
];
const WP: [(&str, usize, &str); 6] = [
    (
        "wp-includes/cache.php",
        13486,
        "a219c2b57cc9990b560c6b497c877db443b7e29239645a6a79b2d76839c46e19",
    ),
    (
        "wp-includes/class-wp-object-cache.php",
        17524,
        "1857b774bd9cfbafbd50a1b1424f7df637d202cf3f2253cda9798d4ad56f3cf0",
    ),
    (
        "wp-includes/functions.php",
        295481,
        "cac8a17e293df99f0683a88debd3ba5e5cce274183a4e9fd3c9f18375747f41c",
    ),
    (
        "wp-includes/load.php",
        56475,
        "3efd0934bd8a454a8d7076fbbd21504ea097a753125a48e64b8a1dad380d357a",
    ),
    (
        "wp-includes/option.php",
        105246,
        "fe3f0b7e115b2a50c31162ed6428abe4088c080b0f71b9678d84806168fe90d0",
    ),
    (
        "wp-includes/version.php",
        1103,
        "7513dacf90ade66347561ffc2e79d27e74b6a3f05d869bd455144ed7cd378a04",
    ),
];

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn identity(bytes: &[u8]) -> Value {
    json!({"bytes": bytes.len(), "sha256": sha(bytes)})
}

fn fixed(root: &Path, name: &str, limit: u64) -> Result<Vec<u8>> {
    let relative = Path::new(name);
    ensure!(
        !relative.is_absolute()
            && relative
                .components()
                .all(|part| matches!(part, Component::Normal(_))),
        "fixed_relative_path"
    );
    let path = root.join(relative);
    ensure!(
        path.canonicalize()? == path && path.symlink_metadata()?.is_file(),
        "fixed_file_path"
    );
    let file = File::open(path)?;
    let metadata = file.metadata()?;
    ensure!(
        metadata.is_file() && metadata.len() <= limit,
        "fixed_file_quota"
    );
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes)?;
    ensure!(bytes.len() as u64 == metadata.len(), "fixed_file_changed");
    Ok(bytes)
}

fn load_module(root: &Path) -> Result<(Vec<u8>, Value, Value)> {
    let wire = fixed(root, RECEIPT, 2 * 1024 * 1024)?;
    let receipt = strict_json::parse(&wire).map_err(|_| anyhow::anyhow!("managed_build_json"))?;
    ensure!(
        receipt["schema"] == "recastpress.php-wasix-managed-access-build.v0.5"
            && receipt["variant"] == VARIANT
            && receipt["phase"] == "build_complete"
            && receipt["outcome"] == "managed_access_candidate_built_not_executed"
            && receipt["guest_executed"] == false,
        "managed_build_profile"
    );
    for (path, expected) in inputs::MANAGED_SOURCES {
        let bytes = fixed(root, path, 256 * 1024)?;
        ensure!(bytes == expected, "compiled_managed_source_changed");
        if !path.contains("/evidence/native-") {
            ensure!(
                receipt["input_files"][path] == sha(&bytes),
                "managed_input_binding"
            );
        }
    }
    let patches = receipt["source_patches"]
        .as_array()
        .context("managed_patch_shape")?;
    ensure!(
        patches.len() == 3 && patches.iter().all(|patch| patch["applied"] == true),
        "managed_patch_chain"
    );
    let bytes = fixed(root, MODULE, 128 * 1024 * 1024)?;
    let module = &receipt["php_wasm"];
    let digest = sha(&bytes);
    ensure!(
        module["path"] == MODULE
            && module["wasm_header_verified"] == true
            && module["executed"] == false
            && bytes.starts_with(b"\0asm\x01\0\0\0")
            && module["bytes"].as_u64() == Some(bytes.len() as u64)
            && module["sha256"] == digest,
        "managed_module_identity"
    );
    Ok((
        bytes,
        json!({"path": MODULE, "bytes": module["bytes"], "sha256": digest, "verified": true}),
        json!({"path": RECEIPT, "sha256": sha(&wire), "verified": true, "full_chain_verified_by_host": false}),
    ))
}

struct Sources {
    fixture: SourceFs,
    wordpress: SourceFs,
    identities: Value,
}

fn load_sources(root: &Path) -> Result<Sources> {
    let manifest = fixed(root, MANIFEST, 2 * 1024 * 1024)?;
    ensure!(sha(&manifest) == MANIFEST_SHA, "fixed_wordpress_manifest");
    let catalog =
        strict_json::parse(&manifest).map_err(|_| anyhow::anyhow!("wordpress_manifest_json"))?;
    let mut fixture_files = BTreeMap::new();
    let mut wordpress_files = BTreeMap::new();
    let mut fixtures = serde_json::Map::new();
    let mut wordpress = serde_json::Map::new();
    for (name, compiled) in FIXTURES {
        let bytes = fixed(root, &format!("{HERE}/fixtures/{name}"), 256 * 1024)?;
        ensure!(bytes == compiled, "compiled_fixture_changed");
        fixtures.insert(name.into(), identity(&bytes));
        fixture_files.insert(name.into(), Arc::<[u8]>::from(bytes));
    }
    for (name, size, digest) in WP {
        let bytes = fixed(root, &format!("{SITE}/{name}"), size as u64)?;
        let pin = json!({"bytes": size, "sha256": digest});
        ensure!(
            identity(&bytes) == pin && catalog["files"][name] == pin,
            "fixed_wordpress_cache_source"
        );
        wordpress.insert(name.into(), pin);
        wordpress_files.insert(name.into(), Arc::<[u8]>::from(bytes));
    }
    Ok(Sources {
        fixture: SourceFs::new(fixture_files, BTreeSet::new())
            .map_err(|_| anyhow::anyhow!("fixture_mount"))?,
        wordpress: SourceFs::new(wordpress_files, BTreeSet::from(["wp-includes".into()]))
            .map_err(|_| anyhow::anyhow!("wordpress_mount"))?,
        identities: json!({"source_manifest": identity(&manifest), "wordpress": wordpress, "fixtures": fixtures}),
    })
}

fn environment() -> Vec<(String, String)> {
    [
        ("SCRIPT_FILENAME", "/fixture/entry.php"),
        ("SCRIPT_NAME", "/entry.php"),
        ("DOCUMENT_ROOT", "/fixture"),
        ("REQUEST_METHOD", "GET"),
        ("QUERY_STRING", ""),
        ("REQUEST_URI", "/entry.php"),
        ("CONTENT_LENGTH", "0"),
        ("REDIRECT_STATUS", "200"),
        ("GATEWAY_INTERFACE", "CGI/1.1"),
        ("SERVER_PROTOCOL", "HTTP/1.1"),
        ("SERVER_NAME", "rp-fixture.invalid"),
        ("SERVER_PORT", "80"),
    ]
    .into_iter()
    .map(|(name, value)| (name.into(), value.into()))
    .collect()
}

fn run_request(
    engine: &Engine,
    module: Module,
    data: &HashedModuleData,
    sources: Sources,
    row: &mut Value,
) -> Result<()> {
    let cache = cache_fs::CacheFs::new();
    let empty = || {
        SourceFs::new(BTreeMap::new(), BTreeSet::new()).map_err(|_| anyhow::anyhow!("empty_mount"))
    };
    let access = access_fs::ManagedAccessFs::new(sources.wordpress.clone(), empty()?, empty()?)?;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let guard = runtime.enter();
    let (input, stdin) = Pipe::channel();
    drop(input);
    let (stdout_send, stdout_read) = Pipe::channel();
    let (stderr_send, stderr_read) = Pipe::channel();
    let stdout_overflow = Arc::new(AtomicBool::new(false));
    let stderr_overflow = Arc::new(AtomicBool::new(false));
    let result = {
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
            .with_envs(environment())
            .with_forward_host_env(false)
            .with_home_mapped(false)
            .with_asynchronous_threading(true)
            .with_current_dir("/")
            .with_mount("/fixture".into(), Arc::new(sources.fixture))
            .with_mount("/wordpress".into(), Arc::new(sources.wordpress))
            .with_mount("/rp-p0-fs".into(), Arc::new(access.clone()))
            .with_mount(cache_fs::MOUNT.into(), Arc::new(cache.clone()))
            .with_stdin(Box::new(stdin))
            .with_stdout(Box::new(capture::LimitedPipe::new(
                stdout_send,
                stdout_overflow.clone(),
            )))
            .with_stderr(Box::new(capture::LimitedPipe::new(
                stderr_send,
                stderr_overflow.clone(),
            )));
        let capabilities = runner.capabilities_mut();
        capabilities.insecure_allow_all = false;
        capabilities.threading.max_threads = Some(1);
        capabilities.threading.enable_deep_sleep = false;
        row["guest_execution_started"] = json!(true);
        runner.run_wasm(
            RuntimeOrEngine::Runtime(Arc::new(implementation)),
            "php-object-cache",
            module,
            *data.hash(),
        )
    };
    drop(guard);
    runtime.shutdown_timeout(Duration::from_secs(2));
    let stdout = capture::collect_pipe(stdout_read);
    let stderr = capture::collect_pipe(stderr_read);
    row["cgi_capture"] = json!({"guest_exit_success": result.is_ok(), "runtime_shutdown_returned": true,
        "stdout_eof": stdout.is_ok(), "stderr_eof": stderr.is_ok(),
        "stdout_overflow": stdout_overflow.load(Ordering::Relaxed), "stderr_overflow": stderr_overflow.load(Ordering::Relaxed)});
    if let Ok(bytes) = &stdout {
        row["cgi_capture"]["stdout"] = identity(bytes);
    }
    if let Ok(bytes) = &stderr {
        row["cgi_capture"]["stderr"] = identity(bytes);
    }
    let cache = cache.finish();
    let access = access.finish();
    if let Ok(snapshot) = &cache {
        row["cache"] = snapshot.clone();
    }
    if let Ok(snapshot) = &access {
        row["managed_access"] = snapshot.report_value();
    }
    if result.is_err() {
        // A nonzero guest may still have completed its fixed diagnostic frame.
        // Never retain stdout or arbitrary JSON from an unsuccessful guest.
        if let Ok(bytes) = &stdout {
            if let Ok(frame) = cgi_response::parse(bytes) {
                if frame.body.len() <= 256 {
                    if let Ok(value) = strict_json::parse(&frame.body) {
                        if value.as_object().is_some_and(|map| map.len() == 2)
                            && value["passed"] == false
                        {
                            if let Some(code) = value["failure"].as_str().filter(|code| {
                                !code.is_empty()
                                    && code.len() <= 80
                                    && code
                                        .bytes()
                                        .all(|byte| byte.is_ascii_lowercase() || byte == b'_')
                            }) {
                                row["guest_failure"] = json!(code);
                            }
                        }
                    }
                }
            }
        }
        anyhow::bail!("guest_execution_failed");
    }
    ensure!(
        !stdout_overflow.load(Ordering::Relaxed) && !stderr_overflow.load(Ordering::Relaxed),
        "guest_capture_overflow"
    );
    ensure!(stderr?.is_empty(), "guest_stderr");
    let cgi = cgi_response::parse(&stdout?).map_err(|_| anyhow::anyhow!("guest_cgi_response"))?;
    ensure!(
        cgi.status == 200
            && cgi.body.len() <= MAX_BODY
            && cgi.body.ends_with(b"\n")
            && cgi
                .headers
                .iter()
                .any(|(name, value)| name == "content-type" && value == b"application/json"),
        "guest_cgi_profile"
    );
    row["cgi_capture"]["status"] = json!(cgi.status);
    row["cgi_capture"]["body"] = identity(&cgi.body);
    let cache = cache.map_err(|_| anyhow::anyhow!("guest_cache_completion"))?;
    ensure!(access.is_ok(), "guest_access_completion");
    let php = strict_json::parse(&cgi.body).map_err(|_| anyhow::anyhow!("guest_json"))?;
    let fields = [
        "schema",
        "passed",
        "php_version",
        "php_integer_bytes",
        "wordpress_version",
        "adapter_class",
        "stock_parent_class",
        "profile",
        "observations",
        "rust_transport",
    ];
    ensure!(
        php.as_object().is_some_and(
            |map| map.len() == fields.len() && fields.iter().all(|key| map.contains_key(*key))
        ) && php["schema"] == "recastpress.php-wasix-wp-object-cache-guest-observation.v0.5"
            && php["passed"] == true
            && php["php_version"] == "8.5.12"
            && php["php_integer_bytes"] == 8
            && php["wordpress_version"] == "7.1.3"
            && php["adapter_class"] == "RpRustBackedCache"
            && php["stock_parent_class"] == "WP_Object_Cache"
            && php["profile"] == "isolated_multisite_cache_prefix"
            && php["observations"]
                .as_object()
                .is_some_and(|map| map.len() == 56),
        "guest_observation_scope"
    );
    ensure!(
        php["rust_transport"]
            == json!({"groups": cache["groups"], "flushes": cache["flushes"],
        "finish": {"operations": cache["operations"], "entries": 0, "charged_bytes": 0}, "channel_closed": true}),
        "guest_transport_mismatch"
    );
    row["php_result"] = php;
    row["passed"] = json!(true);
    Ok(())
}

fn probe(root: &Path, report: &mut Value) -> Result<()> {
    let (bytes, module_identity, receipt_identity) = load_module(root)?;
    report["module"] = module_identity.clone();
    report["build_receipt"] = receipt_identity.clone();
    report["sources_before"] = load_sources(root)?.identities;
    fiber_adapter::inspect(&bytes).map_err(|_| anyhow::anyhow!("managed_fiber_profile"))?;
    let engine: Engine = wasmer_compiler_cranelift::Cranelift::default().into();
    let data = HashedModuleData::new(bytes);
    let module =
        Module::new(&engine, &data.wasm()[..]).map_err(|_| anyhow::anyhow!("module_compile"))?;
    report["module_compilations"] = json!(1);
    for case in ["first", "repeat"] {
        let sources = load_sources(root)?;
        ensure!(
            sources.identities == report["sources_before"],
            "request_source_changed"
        );
        let mut row = json!({"case": case, "passed": false, "fresh_runtime": true, "guest_execution_started": false,
            "guest_networking": false, "host_environment_forwarded": false, "host_home_mapped": false, "stdin_bytes": 0});
        let result = run_request(&engine, module.clone(), &data, sources, &mut row);
        report["requests"]
            .as_array_mut()
            .context("request_array")?
            .push(row);
        result?;
        let (_, observed_module, observed_receipt) = load_module(root)?;
        ensure!(
            observed_module == module_identity && observed_receipt == receipt_identity,
            "request_module_changed"
        );
    }
    report["sources_after"] = load_sources(root)?.identities;
    ensure!(
        report["sources_before"] == report["sources_after"],
        "request_source_changed"
    );
    report["source_reverified_after"] = json!(true);
    Ok(())
}

fn main() {
    let mut report = json!({"schema": "recastpress.php-wasix-wp-object-cache-host.v0.5", "passed": false,
        "module_compilations": 0, "source_reverified_after": false, "requests": []});
    let result = (|| -> Result<()> {
        ensure!(cfg!(target_os = "linux"), "actual_php_wasm_linux_only");
        ensure!(std::env::args_os().count() == 1, "closed_arguments");
        probe(&std::env::current_dir()?.canonicalize()?, &mut report)
    })();
    if let Err(error) = &result {
        let code = error.to_string();
        report["failure"] = json!(if code.len() <= 80
            && code.contains('_')
            && code
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte == b'_')
        {
            code.as_str()
        } else {
            "fixed_host_failed"
        });
    } else {
        report["passed"] = json!(true);
    }
    let wire = serde_json::to_vec(&report).expect("bounded private observation");
    let mut stdout = std::io::stdout().lock();
    if wire.len() > 512 * 1024
        || stdout
            .write_all(&wire)
            .and_then(|_| stdout.write_all(b"\n"))
            .is_err()
    {
        std::process::exit(2);
    }
    if result.is_err() {
        std::process::exit(1);
    }
}
