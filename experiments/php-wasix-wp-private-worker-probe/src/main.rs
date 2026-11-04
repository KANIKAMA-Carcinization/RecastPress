#![forbid(unsafe_code)]
#![recursion_limit = "256"]
#[path = "../../php-wasix-wp-request-probe/src/access_fs.rs"]
mod access_fs;
#[path = "../../php-wasix-wp-request-probe/src/capture.rs"]
mod capture;
#[path = "../../php-wasix-wp-request-probe/src/catalog.rs"]
mod catalog;
#[path = "../../php-wasi-host-probe/src/cgi_response.rs"]
mod cgi_response;
#[path = "../../php-wasix-wp-request-probe/src/db.rs"]
mod db;
#[path = "../../php-wasix-host-probe/src/fiber_adapter.rs"]
mod fiber_adapter;
#[allow(dead_code)]
#[path = "../../php-wasix-wpdb-probe/src/inputs.rs"]
mod inputs;
mod strict_json;
use recastpress_wp_private_response_probe as private_response;
#[path = "../../php-wasix-wp-request-probe/src/result_budget.rs"]
mod result_budget;
#[path = "../../php-wasix-wp-request-probe/src/rpc_fs.rs"]
mod rpc_fs;
#[path = "../../php-wasix-wp-request-probe/src/warning_diagnostic.rs"]
mod warning_diagnostic;
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
const PREPARED: &str = "target/php-wasix-wp-request-context-probe/prepared.json";
const VARIANT: &str = "nts-fresh-wasi-zend-fibers-managed-access-v1";
const BASE: &str = "experiments/php-wasix-wp-request-context-probe/";
// Preserve the accepted managed-access namespace exactly.
const GUEST_BASE: &str = "experiments/php-wasix-wp-request-probe/";
#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub(crate) enum Case { Home, Missing }
impl Case {
    fn as_str(self) -> &'static str { match self { Self::Home => "home", Self::Missing => "missing" } }
    fn status(self) -> u16 { if self == Self::Home { 200 } else { 404 } }
}
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope { schema: String, case: Case, database: db::Input }
fn parse_input(wire: &[u8]) -> Result<(Case, db::Input)> {
    ensure!(wire.len() <= 24 * 1024, "wrapper_input_quota");
    // Typed decoding rejects duplicate keys at every level, including the
    // unchanged database input and nested salts, before the Value pass.
    let typed: Envelope = serde_json::from_slice(wire).map_err(|_| anyhow::anyhow!("invalid_wrapper_input"))?;
    ensure!(typed.schema == "recastpress.php-wasix-wp-private-worker-input.v0.5", "wrapper_input_schema");
    let value: Value = serde_json::from_slice(wire)?;
    let salt_keys: BTreeSet<_> = value["database"]["salts"].as_object()
        .context("wrapper_salts_object")?.keys().map(String::as_str).collect();
    ensure!(salt_keys == BTreeSet::from(["AUTH_KEY","SECURE_AUTH_KEY","LOGGED_IN_KEY","NONCE_KEY",
        "AUTH_SALT","SECURE_AUTH_SALT","LOGGED_IN_SALT","NONCE_SALT"]), "wrapper_salts_exact_keys");
    let input = db::parse_input(&serde_json::to_vec(&value["database"])?).map_err(|e| anyhow::anyhow!(e))?;
    ensure!(typed.database.mode == input.mode && input.mode == db::Mode::Home, "wrapper_database_mode");
    Ok((typed.case, input))
}
fn guest_context(input: &db::Input, case: Case) -> Value {
    let mut context = input.context();
    context["schema"] = json!("recastpress.php-wasix-wp-request-context-guest.v0.5");
    context["case"] = json!(case.as_str());
    context
}
const OWNED: &[(&str, &[u8])] = &[
    ("home.php", include_bytes!("../../php-wasix-wp-request-context-probe/fixtures/home.php")),
    ("missing.php", include_bytes!("../../php-wasix-wp-request-context-probe/fixtures/missing.php")),
    ("context.php", include_bytes!("../../php-wasix-wp-request-context-probe/fixtures/context.php")),
    ("bootstrap.php", include_bytes!("../../php-wasix-wp-request-context-probe/fixtures/bootstrap.php")),
    ("helpers.php", include_bytes!("../../php-wasix-wp-request-context-probe/fixtures/helpers.php")),
    ("adapter.php", include_bytes!("../../php-wasix-wp-request-context-probe/fixtures/adapter.php")),
    ("db.php", include_bytes!("../../php-wasix-wp-request-context-probe/fixtures/db.php")),
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
        p["schema"] == "recastpress.php-wasix-wp-request-context-prepared.v0.5"
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
                row["guest"] == format!("/{GUEST_BASE}fixtures/{name}")
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
        prep_bytes == include_bytes!("../../php-wasix-wp-request-context-probe/prepare.py"),
        "compiled_prepare_source_changed"
    );
    // This independent worker checks only compiled fixtures and original core.
    // Transitive preparation/build bindings remain the controller's job; never
    // open arbitrary metadata paths or WordPress response-oracle evidence here.
    let wp = catalog::load(
        root,
        &mut report["core_source"],
        dropin.context("dropin_missing")?,
    )
    .map_err(|e| anyhow::anyhow!(e))?;
    report["prepared"] = json!({"path":PREPARED,"sha256":db::sha(&wire),"files":entries,"core_files":3782,"owned_dropin_files":1,"source_verified":true,"transitive_inputs_verified_by_worker":false,"verification_scope":"compiled_fixtures_and_core_only"});
    Ok((finite(fixtures)?, wp, manifest))
}
fn environment(case: Case) -> Vec<(String, String)> {
    [
        (
            "SCRIPT_FILENAME",
            format!("/{GUEST_BASE}fixtures/{}.php", case.as_str()),
        ),
        ("SCRIPT_NAME", "/__rp_request.php".into()),
        ("DOCUMENT_ROOT", "/".into()),
        ("REQUEST_METHOD", "GET".into()),
        ("QUERY_STRING", "rp_request=1".into()),
        ("REQUEST_URI", "/__rp_request.php?rp_request=1".into()),
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
    let dir = root.join("target/php-wasix-wp-private-worker-probe/private-capture");
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
fn known_json_cgi_headers(frame: &cgi_response::Response) -> bool {
    frame.status == 200
        && frame.headers.iter().any(|(name, value)| {
            name == "content-type"
                && matches!(
                    value.as_slice(),
                    b"application/json"
                        | b"application/json; charset=UTF-8"
                        | b"application/json; charset=utf-8"
                )
        })
        && frame.headers.iter().all(|(name, value)| {
            name == "content-type" || (name == "x-powered-by" && value.as_slice() == b"PHP/8.5.12")
        })
}
fn fixed_json_cgi_headers(frame: &cgi_response::Response) -> bool {
    known_json_cgi_headers(frame)
        && frame.headers.iter().filter(|(name, _)| name == "content-type").count() == 1
        && frame.headers.iter().filter(|(name, _)| name == "x-powered-by").count() <= 1
}
fn observe_cgi(
    raw: &[u8],
    record: &mut Value,
) -> Result<Value> {
    let response = cgi_response::parse(raw)?;
    record["response"] = json!({"status":response.status,"header_count":response.headers.len(),"body_bytes":response.body.len(),"body_sha256":db::sha(&response.body),"representation_length":response.representation_length});
    let leading = response
        .body
        .iter()
        .take_while(|&&b| matches!(b, b' ' | b'\t' | b'\r' | b'\n'))
        .count();
    let trailing = response
        .body
        .iter()
        .rev()
        .take_while(|&&b| matches!(b, b' ' | b'\t' | b'\r' | b'\n'))
        .count();
    let tail = &response.body[leading..];
    let known_json_header = tail.starts_with(b"Content-Type: application/json")
        || tail.starts_with(b"Content-type: application/json")
        || tail.starts_with(b"X-Powered-By: PHP/8.5.12");
    const EXPECTED_JSON_PREFIX: &[u8] =
        b"{\"schema\":\"recastpress.php-wasix-wp-request-context-php-observation.v0.5\"";
    let mut expected_prefix_count = 0usize;
    let mut expected_prefix_offset = None;
    for (offset, bytes) in response.body.windows(EXPECTED_JSON_PREFIX.len()).enumerate() {
        if bytes == EXPECTED_JSON_PREFIX {
            expected_prefix_count += 1;
            expected_prefix_offset = Some(offset);
        }
    }
    if expected_prefix_count != 1 { expected_prefix_offset = None; }
    let first_byte_class = match response.body.first() {
        None => "empty",
        Some(0) => "nul",
        Some(b'{') => "json_object",
        Some(b'<') => "less_than",
        Some(b' ' | b'\t' | b'\r' | b'\n') => "ascii_whitespace",
        Some(0x20..=0x7e) => "ascii_printable",
        Some(0x80..=0xff) => "non_ascii",
        Some(_) => "other_control",
    };
    let class = if tail.starts_with(b"{") {
        if leading == 0 {
            "json_object"
        } else {
            "json_object_after_whitespace"
        }
    } else if known_json_header {
        "known_json_cgi_header"
    } else if tail.get(..b"<!DOCTYPE html".len()).is_some_and(|bytes| bytes.eq_ignore_ascii_case(b"<!DOCTYPE html")) {
        "html_doctype"
    } else if tail.starts_with(b"PHP Warning:") || tail.starts_with(b"Warning:")
        || tail.starts_with(b"<br />\n<b>Warning</b>") {
        "php_warning"
    } else if tail.starts_with(b"Content-Type:") || tail.starts_with(b"Content-type:")
        || tail.starts_with(b"Status:") || tail.starts_with(b"X-Powered-By:") {
        "known_cgi_header"
    } else if tail.is_empty() {
        "empty"
    } else {
        "other"
    };
    let mut diagnostic = json!({"prefix_class":class,"leading_json_whitespace_bytes":leading,"trailing_json_whitespace_bytes":trailing,
        "lf_bytes":response.body.iter().filter(|&&b|b==b'\n').count(),"cr_bytes":response.body.iter().filter(|&&b|b==b'\r').count(),
        "json_decoded":false,"decode_strategy":"none","acceptance_changed":false,
        "trailing_bytes_after_json":0,"trailing_class":"none",
        "first_byte_class":first_byte_class,"body_utf8_valid":std::str::from_utf8(&response.body).is_ok(),
        "leading_nul_bytes":response.body.iter().take_while(|&&b|b==0).count(),
        "expected_json_prefix_count":expected_prefix_count,"expected_json_prefix_offset":expected_prefix_offset});
    diagnostic["warning_diagnostic"] = warning_diagnostic::classify(
        &response.body[..expected_prefix_offset.unwrap_or(response.body.len())],
        &[warning_diagnostic::KnownSource {
            path: "/experiments/php-wasix-wp-request-probe/fixtures/bootstrap.php",
            label: warning_diagnostic::SourceLabel::HomeBootstrap,
            line_count: include_str!("../../php-wasix-wp-request-context-probe/fixtures/bootstrap.php").lines().count(),
        }],
    );
    let mut decoded = strict_json::parse(&response.body).ok();
    if decoded.is_some() {
        diagnostic["decode_strategy"] = json!("whole_body_json");
    } else {
        // Start at byte zero only (serde may consume leading JSON whitespace).
        // A decoded initial object is diagnostic-only if ANY trailing data
        // remains, including a closed CGI header suffix or an unknown trailer.
        let mut stream = serde_json::Deserializer::from_slice(&response.body).into_iter::<strict_json::Unique>();
        if let Some(Ok(strict_json::Unique(value))) = stream.next() {
            if value.is_object()
                && value["schema"] == "recastpress.php-wasix-wp-request-context-php-observation.v0.5"
            {
                let offset = stream.byte_offset();
                let suffix = &response.body[offset..];
                let whitespace = suffix
                    .iter()
                    .take_while(|&&b| matches!(b, b' ' | b'\t' | b'\r' | b'\n'))
                    .count();
                let known = cgi_response::parse(&suffix[whitespace..])
                    .ok()
                    .is_some_and(|f| f.body.is_empty() && known_json_cgi_headers(&f));
                diagnostic["decode_strategy"] = json!("initial_expected_json_object_with_trailer");
                diagnostic["trailing_bytes_after_json"] = json!(suffix.len());
                diagnostic["trailing_class"] = json!(if known {
                    "known_json_cgi_headers"
                } else {
                    "unknown_trailer"
                });
                decoded = Some(value);
            }
        }
    }
    if decoded.is_none() && known_json_header {
        // Diagnostic-only second frame: never search for an arbitrary '{'.
        if let Ok(inner) = cgi_response::parse(tail) {
            let known = known_json_cgi_headers(&inner);
            if known {
                decoded = strict_json::parse(&inner.body).ok();
                if decoded.is_some() {
                    diagnostic["decode_strategy"] = json!("closed_known_json_cgi_frame");
                }
            }
        }
    }
    if decoded.is_none() {
        // Search only the byte-exact envelope prefix emitted by this fixture.
        // A unique match can recover safe diagnostics, never CGI acceptance.
        if let Some(offset) = expected_prefix_offset {
            let mut stream = serde_json::Deserializer::from_slice(&response.body[offset..])
                .into_iter::<strict_json::Unique>();
            if let Some(Ok(strict_json::Unique(value))) = stream.next() {
                if value.is_object()
                    && value["schema"] == "recastpress.php-wasix-wp-request-context-php-observation.v0.5"
                {
                    let suffix = &response.body[offset + stream.byte_offset()..];
                    diagnostic["decode_strategy"] = json!("unique_exact_fixture_json_envelope");
                    diagnostic["trailing_bytes_after_json"] = json!(suffix.len());
                    diagnostic["trailing_class"] = json!(if suffix.is_empty() {
                        "none"
                    } else if suffix.iter().all(|&b| matches!(b, b' ' | b'\t' | b'\r' | b'\n')) {
                        "json_whitespace"
                    } else { "unknown_trailer" });
                    decoded = Some(value);
                }
            }
        }
    }
    if let Some(value) = &decoded {
        record["safe_php_observation"] = public_php(value);
        diagnostic["json_decoded"] = json!(true);
    }
    record["framing_diagnostic"] = diagnostic;
    ensure!(
        response.body.ends_with(b"\n")
            && response.body.iter().filter(|&&b| b == b'\n').count() == 1,
        "cgi_complete_json_line"
    );
    // Strict acceptance always parses the ORIGINAL body and retains the
    // original newline condition. Diagnostic extraction above grants no pass.
    let value: Value =
        strict_json::parse(&response.body).map_err(|code| anyhow::anyhow!(code))?;
    record["safe_php_observation"] = public_php(&value);
    ensure!(fixed_json_cgi_headers(&response), "cgi_json_header_profile");
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
    case: Case,
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
                "-d",
                "display_errors=1",
                "-d",
                "log_errors=0",
            ])
            .with_envs(environment(case))
            .with_forward_host_env(false)
            .with_home_mapped(false)
            .with_asynchronous_threading(true)
            .with_current_dir("/")
            .with_mount(format!("/{GUEST_BASE}fixtures").into(), Arc::new(php))
            .with_mount("/wordpress".into(), Arc::new(wp))
            .with_mount("/rp-request".into(), Arc::new(context))
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
    value["schema"] == "recastpress.php-wasix-wp-request-context-php-observation.v0.5"
        && value["passed"] == true
        && value["mode"] == mode.as_str()
        && value["phase"] == "complete"
        && value["outcome"] == "fixed_wasix_context_passed"
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
        && value["wp_installing"] == false
        && value["public_options"]
            == json!({"blogname":"RP Bootstrap Fixture","siteurl":"http://rp-fixture.invalid","home":"http://rp-fixture.invalid"})
        && value["public_posts"] == expected_posts()
        && value["install_completed"] == false
        && value["owned_db_dropin_loaded"] == true
        && value["request_dispatch_completed"] == true
        && value["template_loader_completed"] == true
        && value["anonymous_request"] == true
        && value["theme_rendered"] == true
        && value["html_normalized"] == false
        && value["http_listener_exercised"] == false
        && value["product_runtime_selected"] == false
        && value["native_php_product_fallback"] == false
        && value["site_config_file_created"] == false
        && value["source_core_theme_plugin_modified"] == false
        && value["outbound_mail_or_http_sent_by_fixture"] == false
        && value["blocked_mail_calls"] == 0 && value["blocked_http_calls"] == 0
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
        "owned_db_dropin_loaded",
        "request_dispatch_completed",
        "template_loader_completed",
        "anonymous_request",
        "theme_rendered",
        "html_normalized",
        "http_listener_exercised",
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
            vec!["recastpress.php-wasix-wp-request-context-php-observation.v0.5"],
        ),
        ("mode", vec!["home"]),
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
    if ["fixed_wasix_context_passed", "fixed_wasix_context_failed"]
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
    if let Ok(response) = serde_json::to_vec(&value["response"])
        .map_err(|_| ()).and_then(|wire| private_response::decode(&wire).map_err(|_| ())) {
        safe.insert("response_summary".into(), json!(response.summary()));
    }
    Value::Object(safe)
}
fn public_literal(name: &str) -> Vec<String> {
    let prefix = format!("const {name} = ");
    include_str!("../../php-wasix-wp-request-context-probe/fixtures/bootstrap.php")
        .lines()
        .find_map(|line| line.strip_prefix(&prefix))
        .and_then(|s| s.strip_suffix(';'))
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or_default()
}
fn probe(input: db::Input, case: Case, report: &mut Value) -> Result<()> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()?;
    let bytes = load_module(&root, report)?;
    let (php, wp, manifest) = load_sources(&root, report)?;
    let context = serde_json::to_vec(&guest_context(&input, case))?;
    let context_identity = json!({"bytes":context.len(),"sha256":db::sha(&context)});
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
        case,
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
    if let Ok(reasons) = access.invalid_reason_counts() {
        report["managed_access_invalid_reasons"] = reasons;
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
    ensure!(completion(mode, &php) && php["case"] == case.as_str()
        && php["is_home"] == (case == Case::Home) && php["is_404"] == (case == Case::Missing)
        && private_contexts_valid(&php, case)
        && php["transport_body"] == json!({"bytes":0,"sha256":db::sha(b"")})
        && php["private_context_before"] == context_identity && php["private_context_after"] == context_identity
        && php["ini_profile"] == json!({"owned_file_loaded":false,"additional_ini_scanned":false,"display_errors":"1","log_errors":"0","register_argc_argv":"0","allow_url_fopen":"0","mail_function_present":false,"mysqli_loaded":true,"expose_php":"1"}),
        "full_context_php_contract");
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
    commit_private_response(report, &php, case)?;
    report["phase"] = json!("full_private_response_verified");
    Ok(())
}
fn private_contexts_valid(value: &Value, case: Case) -> bool {
    let missing = case == Case::Missing;
    let query = if missing { "p=2147483647" } else { "" };
    let uri = if missing { "/?p=2147483647" } else { "/" };
    let get = if missing { json!({"p":"2147483647"}) } else { json!([]) };
    ["before_bootstrap", "after_bootstrap", "after_dispatch"].iter().all(|stage| {
        let available = *stage != "before_bootstrap";
        value[format!("context_{stage}")] == json!({"stage":stage,"method":"GET","uri":uri,"query":query,
            "get":get,"post":[],"cookie":[],"request":get,"environment_query_present":missing,
            "environment_query":if missing { json!(query) } else { Value::Null },
            "content_type_present":false,"content_type":null,"content_length_present":false,"content_length":null,
            "http_accept_present":false,"http_accept":null,"auth_or_cookie_header_present":false,
            "private_path_environment_present":false,"script_filename_matches_public_entry":true,
            "wp_is_json_request_available":available,"wp_is_json_request":if available { json!(false) } else { Value::Null },
            "display_errors":"1","input":{"bytes":0,"sha256":db::sha(b"")}})
    })
}
fn clear_private_response(report: &mut Value) {
    if let Some(object) = report.as_object_mut() {
        object.remove("private_response"); object.remove("private_contexts");
    }
}
fn private_output_ready(report: &Value) -> bool {
    ["full_wordpress_bootstrap", "source_rechecked_after_execution", "physical_connection_created",
        "physical_disconnect_completed", "db_actor_joined", "db_actor_exited"].iter().all(|key| report[*key] == true)
        && report["db_actor_failure"].is_null()
        && ["guest_exit_success", "runtime_shutdown_returned", "stdout_eof", "stderr_eof"].iter()
            .all(|key| report["cgi_capture"][*key] == true)
        && report["cgi_capture"]["stdout_overflow"] == false && report["cgi_capture"]["stderr_overflow"] == false
        && report["cgi_capture"]["stderr_bytes"] == 0 && report["cgi_capture"]["stderr_sha256"] == db::sha(b"")
        && report["cgi_capture"]["response"]["status"] == 200
        && report["managed_access"]["finished"] == true && report["managed_access"]["protocol_error"].is_null()
        && report["managed_access"]["requests"].as_u64().is_some_and(|n| n > 0)
        && ["pending_bytes", "unread_response_bytes", "active_handles", "invalid", "io_errors"].iter()
            .all(|key| report["managed_access"][*key] == 0)
        && report["rpc"]["finished"] == true && report["rpc"]["failure"].is_null()
        && report["rpc"]["channel_open"] == false && report["rpc"]["response_pending"] == false
        && report["rpc"]["pending_bytes"] == 0 && report["rpc"]["unread_response_bytes"] == 0
}
fn commit_private_response(report: &mut Value, php: &Value, case: Case) -> Result<()> {
    clear_private_response(report);
    ensure!(private_output_ready(report) && private_contexts_valid(php, case)
        && php["transport_body"] == json!({"bytes":0,"sha256":db::sha(b"")}), "private_output_completion");
    let response = private_response::decode(&serde_json::to_vec(&php["response"])?)
        .map_err(|error| anyhow::anyhow!(error.code()))?;
    ensure!(php["case"] == case.as_str() && php["is_home"] == (case == Case::Home)
        && php["is_404"] == (case == Case::Missing) && response.status() == case.status(), "private_output_case");
    let mut contexts = serde_json::Map::new();
    for key in ["case", "is_home", "is_404", "transport_body", "context_before_bootstrap",
        "context_after_bootstrap", "context_after_dispatch"] { contexts.insert(key.into(), php[key].clone()); }
    report["private_response"] = php["response"].clone();
    report["private_contexts"] = Value::Object(contexts);
    report["php_result"]["response_summary"] = json!(response.summary());
    Ok(())
}
fn main() {
    let mut report = json!({"schema":"recastpress.php-wasix-wp-private-worker-observation.v0.5","passed":false,"private_ipc_only":true,"publication_authorized":false,"native_comparison_performed":false,"outcome":"fixed_private_wp_worker_failed","phase":"input","mode":null,"operations":[],"cgi_capture":{},"php_result":null,"physical_connection_created":false,"physical_disconnect_completed":false,"db_actor_joined":false,"full_wordpress_bootstrap":false,"guest_execution_started":false,"fresh_runtime":true,"module_compilations":0,"guest_networking":false,"host_environment_forwarded":false,"host_home_mapped":false,"db_credentials_supplied_to_guest":false,"native_php_fallback":false,"product_runtime":false,"standard_wpdb_compatibility_proven":false,"raw_error_bytes_preserved":false,"server_error_string_lossy_gap_unresolved":true,"native_metadata_compatibility_verified":false,"underlying_virtual_file_counts_only":true,"limits":{"operation_ms":10000,"actor_execution_ms":120000,"actor_join_ms":3000,"disconnect_cleanup_ms":2000,"runtime_shutdown_ms":2000,"capture_cleanup_ms":2000,"query_or_escape_bytes":65536,"request_frame_bytes":262144,"reply_frame_bytes":524288,"operations":4096,"request_total_bytes":8388608,"reply_total_bytes":16777216,"rows":256,"columns":32,"cell_bytes":65536,"raw_result_bytes":262144,"metadata_bytes":4096,"managed_access_max_operations":16384,"managed_access_max_frame_bytes":4103,"managed_access_max_path_bytes":4096,"synchronous_guest_cpu_cancellation":false,"outer_owned_process_deadline_required":true}});
    let mut secrets = vec![];
    let result = (|| -> Result<()> {
        ensure!(std::env::args_os().count() == 1, "closed_arguments");
        let mut wire = vec![];
        std::io::stdin()
            .take(24 * 1024 + 1)
            .read_to_end(&mut wire)?;
        let (case, input) = parse_input(&wire)?;
        secrets = input.secrets();
        report["mode"] = json!(input.mode.as_str());
        report["case"] = json!(case.as_str());
        probe(input, case, &mut report)
    })();
    if result.is_ok() {
        report["passed"] = json!(true);
        report["outcome"] = json!("fixed_private_wp_worker_passed");
    } else {
        clear_private_response(&mut report);
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
        println!("{}", json!({"schema":"recastpress.php-wasix-wp-private-worker-observation.v0.5",
            "passed":false,"outcome":"fixed_private_wp_worker_failed","error":"private_record_secret_gate",
            "private_ipc_only":true,"publication_authorized":false,"native_comparison_performed":false}));
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
    fn private_fixture() -> (Value, Value) {
        let mut report = json!({"php_result":{},"full_wordpress_bootstrap":true,"source_rechecked_after_execution":true,
            "physical_connection_created":true,"physical_disconnect_completed":true,"db_actor_joined":true,
            "db_actor_exited":true,"db_actor_failure":null,
            "cgi_capture":{"guest_exit_success":true,"runtime_shutdown_returned":true,"stdout_eof":true,"stderr_eof":true,
                "stdout_overflow":false,"stderr_overflow":false,"stderr_bytes":0,"stderr_sha256":db::sha(b""),"response":{"status":200}},
            "managed_access":{"finished":true,"protocol_error":null,"requests":1,"pending_bytes":0,
                "unread_response_bytes":0,"active_handles":0,"invalid":0,"io_errors":0},
            "rpc":{"finished":true,"failure":null,"channel_open":false,"response_pending":false,"pending_bytes":0,"unread_response_bytes":0}});
        clear_private_response(&mut report);
        let body = b"unknown private response\0\xff";
        let mut php = json!({"case":"home","is_home":true,"is_404":false,"transport_body":{"bytes":0,"sha256":db::sha(b"")},
            "response":{"status":200,"headers_hex":[db::hex(b"X-Private: opaque unknown header")],"body_bytes":body.len(),
                "body_hex":db::hex(body),"body_sha256":db::sha(body)}});
        for stage in ["before_bootstrap", "after_bootstrap", "after_dispatch"] {
            let available = stage != "before_bootstrap";
            php[format!("context_{stage}")] = json!({"stage":stage,"method":"GET","uri":"/","query":"",
                "get":[],"post":[],"cookie":[],"request":[],"environment_query_present":false,"environment_query":null,
                "content_type_present":false,"content_type":null,"content_length_present":false,"content_length":null,
                "http_accept_present":false,"http_accept":null,"auth_or_cookie_header_present":false,
                "private_path_environment_present":false,"script_filename_matches_public_entry":true,
                "wp_is_json_request_available":available,"wp_is_json_request":if available { json!(false) } else { Value::Null },
                "display_errors":"1","input":{"bytes":0,"sha256":db::sha(b"")}});
        }
        (report, php)
    }
    #[test]
    fn structural_unknown_response_is_private_only_after_all_completion_checks() {
        let (mut report, php) = private_fixture();
        let safe = public_php(&php);
        let safe_text = safe.to_string();
        assert!(safe.get("response").is_none() && safe.get("context_before_bootstrap").is_none());
        assert!(safe.get("transport_body").is_none());
        assert!(!safe_text.contains("opaque") && !safe_text.contains(php["response"]["body_hex"].as_str().unwrap()));
        assert_eq!(safe["response_summary"]["body_bytes"], php["response"]["body_bytes"]);
        commit_private_response(&mut report, &php, Case::Home).unwrap();
        assert_eq!(report["private_response"], php["response"]);
        assert_eq!(report["private_contexts"].as_object().unwrap().len(), 7);
        assert!(report["php_result"].get("response").is_none());
        clear_private_response(&mut report);
        assert!(report.get("private_response").is_none() && report.get("private_contexts").is_none());
    }
    #[test]
    fn partial_source_runtime_rpc_access_or_actor_never_commits_private_fields() {
        let (report, php) = private_fixture();
        for field in ["source_rechecked_after_execution", "physical_disconnect_completed", "db_actor_joined", "db_actor_exited"] {
            let mut changed = report.clone(); changed[field] = json!(false);
            changed["private_response"] = json!("stale private"); changed["private_contexts"] = json!("stale private");
            assert!(commit_private_response(&mut changed, &php, Case::Home).is_err());
            assert!(changed.get("private_response").is_none() && changed.get("private_contexts").is_none());
        }
        for (parent, field, bad) in [("cgi_capture","stdout_eof",json!(false)), ("cgi_capture","stderr_overflow",json!(true)),
            ("rpc","pending_bytes",json!(1)), ("rpc","channel_open",json!(true)),
            ("managed_access","protocol_error",json!("fixed_failure")), ("managed_access","finished",json!(false))] {
            let mut changed = report.clone(); changed[parent][field] = bad;
            assert!(commit_private_response(&mut changed, &php, Case::Home).is_err());
            assert!(changed.get("private_response").is_none() && changed.get("private_contexts").is_none());
        }
        for field in ["pending_bytes", "unread_response_bytes", "active_handles", "invalid", "io_errors"] {
            let mut changed = report.clone(); changed["managed_access"][field] = json!(1);
            assert!(commit_private_response(&mut changed, &php, Case::Home).is_err());
            assert!(changed.get("private_response").is_none() && changed.get("private_contexts").is_none());
        }
    }
    #[test]
    fn malformed_response_or_wrong_private_context_never_commits() {
        let (report, php) = private_fixture();
        for (parent, key, bad) in [("response","body_sha256",json!("0".repeat(64))),
            ("context_after_dispatch","query",json!("private-unknown-query")),
            ("context_after_bootstrap","display_errors",json!("0"))] {
            let mut changed = php.clone(); changed[parent][key] = bad;
            let mut output = report.clone();
            assert!(commit_private_response(&mut output, &changed, Case::Home).is_err());
            assert!(output.get("private_response").is_none() && output.get("private_contexts").is_none());
        }
    }
    #[test]
    fn nested_outer_response_duplicate_keys_are_rejected_without_safe_projection() {
        let raw = b"Content-Type: application/json\r\n\r\n{\"schema\":\"recastpress.php-wasix-wp-request-context-php-observation.v0.5\",\"response\":{\"status\":200,\"status\":404},\"private\":\"never-publish\"}\n";
        let mut record = json!({});
        assert_eq!(observe_cgi(raw, &mut record).unwrap_err().to_string(), "php_json_shape");
        assert!(record.get("safe_php_observation").is_none());
        assert!(!record.to_string().contains("never-publish"));
    }
    fn wrapped(case: &str) -> Value {
        let salt = "a".repeat(48);
        json!({"schema":"recastpress.php-wasix-wp-private-worker-input.v0.5","case":case,
            "database":{"schema":"recastpress.php-wasix-wp-request-input.v0.5","mode":"home",
                "host":"127.0.0.1","port":3306,"database":"rp_wpdb_probe","user":"rp_wpdb_probe",
                "password":salt,"nonce":"b".repeat(48),"salts":{
                    "AUTH_KEY":salt,"SECURE_AUTH_KEY":salt,"LOGGED_IN_KEY":salt,"NONCE_KEY":salt,
                    "AUTH_SALT":salt,"SECURE_AUTH_SALT":salt,"LOGGED_IN_SALT":salt,"NONCE_SALT":salt}}})
    }
    #[test]
    fn wrapper_rejects_duplicate_unknown_and_nested_invalid_input() {
        let valid = wrapped("home");
        assert!(parse_input(&serde_json::to_vec(&valid).unwrap()).is_ok());
        for field in ["extra", "nonce"] {
            let mut bad = valid.clone(); bad[field] = json!("private");
            assert!(parse_input(&serde_json::to_vec(&bad).unwrap()).is_err());
        }
        for (key, value) in [("mode",json!("read")),("host",json!("remote.invalid")),("password",json!("bad"))] {
            let mut bad = valid.clone(); bad["database"][key] = value;
            assert!(parse_input(&serde_json::to_vec(&bad).unwrap()).is_err());
        }
        let mut bad = valid.clone(); bad["database"]["salts"]["UNKNOWN_KEY"] = json!("a".repeat(48));
        assert!(parse_input(&serde_json::to_vec(&bad).unwrap()).is_err());
        let wire = serde_json::to_string(&valid).unwrap();
        for bad in [wire.replacen("\"case\":\"home\"", "\"case\":\"home\",\"case\":\"missing\"",1),
            wire.replacen("\"port\":3306", "\"port\":3306,\"port\":3307",1),
            wire.replacen("\"salts\":{", "\"salts\":{\"AUTH_KEY\":\"bad\",",1)] {
            assert!(parse_input(bad.as_bytes()).is_err());
        }
        assert!(parse_input(&vec![b' ';24*1024+1]).is_err());
    }
    #[test]
    fn guest_context_has_only_five_keys_and_no_database_credentials() {
        let (case,input) = parse_input(&serde_json::to_vec(&wrapped("missing")).unwrap()).unwrap();
        let context = guest_context(&input,case);
        assert_eq!(context.as_object().unwrap().keys().map(String::as_str).collect::<BTreeSet<_>>(),
            BTreeSet::from(["schema","mode","case","nonce","salts"]));
        assert_eq!(context["schema"],"recastpress.php-wasix-wp-request-context-guest.v0.5");
        assert_eq!(context["mode"],"home"); assert_eq!(context["case"],"missing");
        for key in ["host","port","password","user","database"] { assert!(context.get(key).is_none()); }
    }
    #[test]
    fn closed_cases_select_guest_entry_without_changing_database_mode() {
        for (name,status) in [("home",200),("missing",404)] {
            let (case,input) = parse_input(&serde_json::to_vec(&wrapped(name)).unwrap()).unwrap();
            assert_eq!(input.mode,db::Mode::Home); assert_eq!(case.status(),status);
            let env: BTreeMap<_,_> = environment(case).into_iter().collect();
            assert_eq!(env["SCRIPT_FILENAME"],format!("/{GUEST_BASE}fixtures/{name}.php"));
            assert_eq!(env["CONTENT_LENGTH"],"0"); assert!(!env.contains_key("CONTENT_TYPE"));
        }
        assert!(parse_input(&serde_json::to_vec(&wrapped("other")).unwrap()).is_err());
    }
    #[test]
    fn unique_exact_fixture_envelope_recovers_only_safe_diagnostics_after_private_prefix() {
        let php = "{\"schema\":\"recastpress.php-wasix-wp-request-context-php-observation.v0.5\",\"mode\":\"home\",\"phase\":\"wp_dispatch\",\"passed\":false,\"private_message\":\"private-secret-SQL\"}";
        for (prefix, error) in [
            ("private-secret-prefix", "php_json_shape"),
            ("private-secret-prefix\n", "cgi_complete_json_line"),
        ] {
            let raw = format!("\r\n{prefix}{php}\n");
            let mut record = json!({});
            assert_eq!(observe_cgi(raw.as_bytes(), &mut record).unwrap_err().to_string(), error);
            let diagnostic = &record["framing_diagnostic"];
            assert_eq!(diagnostic["expected_json_prefix_count"], 1);
            assert_eq!(diagnostic["expected_json_prefix_offset"], prefix.len());
            assert_eq!(diagnostic["decode_strategy"], "unique_exact_fixture_json_envelope");
            assert_eq!(diagnostic["trailing_bytes_after_json"], 1);
            assert_eq!(diagnostic["acceptance_changed"], false);
            assert_eq!(record["safe_php_observation"]["phase"], "wp_dispatch");
            assert!(!record.to_string().contains("private-secret"));
        }
    }
    #[test]
    fn duplicate_exact_fixture_envelopes_are_counted_without_offset_extraction() {
        let php = "{\"schema\":\"recastpress.php-wasix-wp-request-context-php-observation.v0.5\",\"mode\":\"home\",\"phase\":\"wp_dispatch\"}";
        let raw = format!("\r\nprivate-secret-prefix{php}{php}\n");
        let mut record = json!({});
        assert_eq!(observe_cgi(raw.as_bytes(), &mut record).unwrap_err().to_string(), "php_json_shape");
        assert_eq!(record["framing_diagnostic"]["expected_json_prefix_count"], 2);
        assert!(record["framing_diagnostic"]["expected_json_prefix_offset"].is_null());
        assert_eq!(record["framing_diagnostic"]["json_decoded"], false);
        assert!(record.get("safe_php_observation").is_none());
        assert!(!record.to_string().contains("private-secret"));
    }
    #[test]
    fn absent_fixture_envelope_reports_only_fixed_prefix_classes_and_byte_counts() {
        for (body, class, first, nul, utf8) in [
            (b"private-secret-prefix".as_slice(), "other", "ascii_printable", 0, true),
            (b"\0\0\xffprivate-secret".as_slice(), "other", "nul", 2, false),
            (b"<!doctype html>private-secret".as_slice(), "html_doctype", "less_than", 0, true),
            (b"PHP Warning: private-secret".as_slice(), "php_warning", "ascii_printable", 0, true),
            (b"Content-Type: text/private-secret".as_slice(), "known_cgi_header", "ascii_printable", 0, true),
        ] {
            let raw = [b"\r\n".as_slice(), body, b"\n".as_slice()].concat();
            let mut record = json!({});
            assert_eq!(observe_cgi(&raw, &mut record).unwrap_err().to_string(), "php_json_shape");
            let diagnostic = &record["framing_diagnostic"];
            assert_eq!(diagnostic["expected_json_prefix_count"], 0);
            assert!(diagnostic["expected_json_prefix_offset"].is_null());
            assert_eq!(diagnostic["prefix_class"], class);
            assert_eq!(diagnostic["first_byte_class"], first);
            assert_eq!(diagnostic["leading_nul_bytes"], nul);
            assert_eq!(diagnostic["body_utf8_valid"], utf8);
            assert_eq!(diagnostic["json_decoded"], false);
            assert!(record.get("safe_php_observation").is_none());
            assert!(!record.to_string().contains("private-secret"));
        }
    }
    #[test]
    fn complete_json_cgi_accepts_only_the_known_outer_header_profile() {
        let php = json!({"schema":"recastpress.php-wasix-wp-request-context-php-observation.v0.5","mode":"home","phase":"wp_settings","passed":false});
        for headers in [
            "Content-Type: application/json\r\n",
            "X-Powered-By: PHP/8.5.12\r\nContent-Type: application/json; charset=UTF-8\r\n",
            "Content-Type: application/json; charset=utf-8\r\nX-Powered-By: PHP/8.5.12\r\n",
        ] {
            let raw = format!("{headers}\r\n{php}\n");
            let mut record = json!({});
            assert_eq!(observe_cgi(raw.as_bytes(), &mut record).unwrap(), php);
            assert_eq!(record["safe_php_observation"]["phase"], "wp_settings");
        }
    }
    #[test]
    fn complete_json_cgi_rejects_missing_wrong_status_or_unknown_outer_headers() {
        let php = json!({"schema":"recastpress.php-wasix-wp-request-context-php-observation.v0.5","mode":"home","phase":"wp_settings","passed":false});
        for headers in [
            "",
            "Content-Type: text/html\r\n",
            "Status: 302 Found\r\nContent-Type: application/json\r\n",
            "Content-Type: application/json\r\nX-Private: private-secret-header\r\n",
            "Content-Type: application/json\r\nX-Powered-By: PHP/8.5.11\r\n",
            "Content-Type: application/json\r\nX-Powered-By: PHP/8.5.12\r\nX-Powered-By: PHP/8.5.12\r\n",
        ] {
            let raw = format!("{headers}\r\n{php}\n");
            let mut record = json!({});
            assert_eq!(observe_cgi(raw.as_bytes(), &mut record).unwrap_err().to_string(), "cgi_json_header_profile");
            assert_eq!(record["safe_php_observation"]["phase"], "wp_settings");
            assert!(!record.to_string().contains("private-secret-header"));
        }
    }
    #[test]
    fn duplicate_json_content_type_keeps_the_original_parser_rejection() {
        let raw = b"Content-Type: application/json\r\nContent-Type: application/json\r\n\r\n{}\n";
        let mut record = json!({});
        assert_eq!(observe_cgi(raw, &mut record).unwrap_err().to_string(), "duplicate CGI Content-Type");
        assert!(record.get("safe_php_observation").is_none());
    }
    #[test]
    fn newline_failure_stays_rejected_but_whole_json_diagnostics_are_safe() {
        let php = json!({"schema":"recastpress.php-wasix-wp-request-context-php-observation.v0.5","mode":"home","phase":"wp_dispatch","passed":false,"failure":"php_warning","private_message":"never-publish-private-SQL"});
        let raw = format!("Content-Type: application/json\r\n\r\n\r\n{php}\n");
        let mut record = json!({});
        let error = observe_cgi(raw.as_bytes(), &mut record).unwrap_err();
        assert_eq!(error.to_string(), "cgi_complete_json_line");
        assert_eq!(record["safe_php_observation"]["phase"], "wp_dispatch");
        assert_eq!(
            record["framing_diagnostic"]["prefix_class"],
            "json_object_after_whitespace"
        );
        assert_eq!(
            record["framing_diagnostic"]["decode_strategy"],
            "whole_body_json"
        );
        assert_eq!(record["framing_diagnostic"]["acceptance_changed"], false);
        assert!(!record.to_string().contains("never-publish-private-SQL"));
    }
    #[test]
    fn nested_known_json_cgi_frame_is_diagnostic_only() {
        let php = json!({"schema":"recastpress.php-wasix-wp-request-context-php-observation.v0.5","mode":"home","phase":"template_loader","passed":false});
        let raw = format!("\r\nContent-Type: application/json\r\n\r\n{php}\n");
        let mut record = json!({});
        assert_eq!(
            observe_cgi(raw.as_bytes(), &mut record)
                .unwrap_err()
                .to_string(),
            "cgi_complete_json_line"
        );
        assert_eq!(
            record["framing_diagnostic"]["decode_strategy"],
            "closed_known_json_cgi_frame"
        );
        assert_eq!(record["safe_php_observation"]["phase"], "template_loader");
    }
    #[test]
    fn unknown_prefix_is_never_scanned_or_published() {
        let raw = b"\r\nprivate-SQL-or-secret-prefix{\"phase\":\"wp_dispatch\"}\n";
        let mut record = json!({});
        assert_eq!(
            observe_cgi(raw, &mut record).unwrap_err().to_string(),
            "php_json_shape"
        );
        assert!(record.get("safe_php_observation").is_none());
        assert_eq!(record["framing_diagnostic"]["prefix_class"], "other");
        assert_eq!(record["framing_diagnostic"]["json_decoded"], false);
        assert!(!record.to_string().contains("private-SQL-or-secret-prefix"));
    }
    #[test]
    fn initial_json_with_known_or_private_trailer_is_diagnostic_only() {
        let php = json!({"schema":"recastpress.php-wasix-wp-request-context-php-observation.v0.5","mode":"home","phase":"wp_dispatch","passed":false});
        for (suffix, class, error) in [
            (
                "\nContent-Type: application/json\r\n\r\n",
                "known_json_cgi_headers",
                "cgi_complete_json_line",
            ),
            (
                "private-SQL-or-secret-trailer\n",
                "unknown_trailer",
                "php_json_shape",
            ),
        ] {
            let raw = format!("\r\n{php}{suffix}");
            let mut record = json!({});
            assert_eq!(
                observe_cgi(raw.as_bytes(), &mut record)
                    .unwrap_err()
                    .to_string(),
                error
            );
            assert_eq!(
                record["framing_diagnostic"]["decode_strategy"],
                "initial_expected_json_object_with_trailer"
            );
            assert_eq!(record["framing_diagnostic"]["trailing_class"], class);
            assert_eq!(
                record["framing_diagnostic"]["trailing_bytes_after_json"],
                suffix.len()
            );
            assert_eq!(record["safe_php_observation"]["phase"], "wp_dispatch");
            assert!(!record.to_string().contains("private-SQL-or-secret-trailer"));
            assert_eq!(record["framing_diagnostic"]["acceptance_changed"], false);
        }
    }
    #[test]
    fn complete_cgi_failure_keeps_only_source_bound_public_diagnostics() {
        let php = json!({"schema":"recastpress.php-wasix-wp-request-context-php-observation.v0.5","mode":"home","passed":false,"phase":"wp_settings","failure":"php_warning","failure_class":"RpBootstrapFailure","message":"private-secret-SQL"});
        let raw = format!("Content-Type: application/json\r\n\r\n{php}\n");
        let mut capture = json!({});
        let actual = observe_cgi(raw.as_bytes(), &mut capture).unwrap();
        assert!(!completion(db::Mode::Home, &actual));
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
        let home: BTreeMap<_, _> = environment(Case::Home).into_iter().collect();
        assert_eq!(home["QUERY_STRING"], "rp_request=1");
        assert_eq!(
            home["SCRIPT_FILENAME"],
            "/experiments/php-wasix-wp-request-probe/fixtures/home.php"
        );
        assert_eq!(home["REQUEST_URI"], "/__rp_request.php?rp_request=1");
        assert!(
            home.keys()
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
