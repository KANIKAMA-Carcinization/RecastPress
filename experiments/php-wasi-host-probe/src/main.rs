use anyhow::{Context, Result, ensure};
use sha2::{Digest, Sha256};
use std::{
    path::Path,
    sync::mpsc,
    time::{Duration, Instant},
};
use wasmtime::{
    Config, Engine, Linker, Module, ResourceLimiter, Store, StoreLimits, StoreLimitsBuilder, Trap,
};
use wasmtime_wasi::{
    DirPerms, FilePerms, I32Exit, WasiCtxBuilder,
    p2::pipe::{MemoryInputPipe, MemoryOutputPipe},
    preview1::{self, WasiP1Ctx},
};

mod cgi_response;
mod descriptor_bridge;
mod epoch;
mod http_probe;
mod parallel;
mod wasi_rpc;
use descriptor_bridge::Bridge;

const FIXTURE_SHA256: &str = "4fd2e8c42ae529ba72f88a0f1e46de1fc69a4b4f01e01fedd65ca966b8ffe6fa";
const FIXTURE_BYTES: u64 = 6_259_973;
const FUEL: u64 = 200_000_000;
const MEMORY: usize = 256 * 1024 * 1024;
const OUTPUT: usize = descriptor_bridge::OUTPUT_LIMIT;
const BODY: &[u8] = b"rp\0\xff\r\nbody";
const WORDPRESS_FIXTURE: &str = include_str!("../wordpress-fixture.json");

struct RequestInput {
    method: String,
    uri: String,
    query: String,
    cookie: String,
    content_type: String,
    body: Vec<u8>,
    output_ready: Option<mpsc::Sender<u64>>,
}
impl RequestInput {
    fn fixed(nonce: u64) -> Self {
        Self {
            method: "POST".into(),
            uri: "/probe.php".into(),
            query: String::new(),
            cookie: format!("rp_fixture={nonce}"),
            content_type: "application/octet-stream".into(),
            body: BODY.to_vec(),
            output_ready: None,
        }
    }
}

fn wordpress_directory() -> std::path::PathBuf {
    let compiled =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.reference/wordpress-7.1.3/wp-includes");
    if [
        "plugin.php",
        "class-wp-hook.php",
        "class-wp-filter-sentinel.php",
    ]
    .iter()
    .all(|name| compiled.join(name).is_file())
    {
        return compiled;
    }
    std::env::current_dir()
        .unwrap_or_default()
        .join(".reference/wordpress-7.1.3/wp-includes")
}

fn verify_wordpress_sources() -> Result<()> {
    use std::io::Read;
    let fixture: serde_json::Value = serde_json::from_str(WORDPRESS_FIXTURE)?;
    ensure!(
        fixture["wordpress_version"] == "7.1.3",
        "wrong WordPress fixture version"
    );
    for name in [
        "plugin.php",
        "class-wp-hook.php",
        "class-wp-filter-sentinel.php",
    ] {
        let expected = &fixture["files"][name];
        let size = expected["bytes"]
            .as_u64()
            .context("missing WP source length")?;
        ensure!(size <= 1024 * 1024, "WP source is too large");
        let file = std::fs::File::open(wordpress_directory().join(name))
            .context("prepare the pinned WordPress reference first")?;
        ensure!(
            file.metadata()?.len() == size,
            "wrong WP source length: {name}"
        );
        let mut bytes = Vec::new();
        file.take(size + 1).read_to_end(&mut bytes)?;
        ensure!(
            bytes.len() as u64 == size,
            "WP source changed while reading"
        );
        ensure!(
            format!("{:x}", Sha256::digest(&bytes))
                == expected["sha256"]
                    .as_str()
                    .context("missing WP source hash")?,
            "wrong WP source hash: {name}"
        );
    }
    Ok(())
}

struct State {
    wasi: WasiP1Ctx,
    limits: StoreLimits,
    memory_denials: usize,
    bridge: Bridge,
    channel_fd: Option<u32>,
    delegates: Option<wasi_rpc::Delegates>,
}
impl ResourceLimiter for State {
    fn memory_growing(
        &mut self,
        current: usize,
        desired: usize,
        maximum: Option<usize>,
    ) -> Result<bool> {
        if desired > MEMORY {
            self.memory_denials += 1;
        }
        self.limits.memory_growing(current, desired, maximum)
    }
    fn table_growing(
        &mut self,
        current: usize,
        desired: usize,
        maximum: Option<usize>,
    ) -> Result<bool> {
        self.limits.table_growing(current, desired, maximum)
    }
    fn memory_grow_failed(&mut self, error: anyhow::Error) -> Result<()> {
        self.limits.memory_grow_failed(error)
    }
    fn table_grow_failed(&mut self, error: anyhow::Error) -> Result<()> {
        self.limits.table_grow_failed(error)
    }
    fn instances(&self) -> usize {
        self.limits.instances()
    }
    fn memories(&self) -> usize {
        self.limits.memories()
    }
    fn tables(&self) -> usize {
        self.limits.tables()
    }
}

struct Run {
    exit: Result<()>,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
    bridge: Bridge,
    fuel_left: u64,
    memory_denials: usize,
}
impl Run {
    fn completed_response(&self) -> Result<cgi_response::Response> {
        // Never publish a partially successful guest result.
        self.exit
            .as_ref()
            .map_err(|error| anyhow::anyhow!("guest failed: {error:#}"))?;
        ensure!(self.stdout.len() < OUTPUT, "response output limit");
        ensure!(self.stderr.len() < 8192, "stderr output limit");
        self.bridge.validate()?;
        cgi_response::parse(&self.stdout)
    }
    fn completed_body(&self) -> Result<Vec<u8>> {
        Ok(self.completed_response()?.body)
    }
}

fn run(
    engine: &Engine,
    module: &Module,
    mode: &str,
    nonce: u64,
    fuel: u64,
    timeout: Duration,
) -> Result<Run> {
    run_observed(engine, module, mode, nonce, fuel, timeout, None)
}

fn run_observed(
    engine: &Engine,
    module: &Module,
    mode: &str,
    nonce: u64,
    fuel: u64,
    timeout: Duration,
    ready: Option<mpsc::Sender<u64>>,
) -> Result<Run> {
    execute(
        engine,
        module,
        mode,
        nonce,
        fuel,
        timeout,
        ready,
        RequestInput::fixed(nonce),
    )
}

fn run_http(
    engine: &Engine,
    module: &Module,
    mode: &str,
    nonce: u64,
    timeout: Duration,
    input: RequestInput,
) -> Result<Run> {
    let fuel = if mode == "http_partial" {
        u64::MAX
    } else {
        FUEL
    };
    execute(engine, module, mode, nonce, fuel, timeout, None, input)
}

fn execute(
    engine: &Engine,
    module: &Module,
    mode: &str,
    nonce: u64,
    fuel: u64,
    timeout: Duration,
    mut ready: Option<mpsc::Sender<u64>>,
    input: RequestInput,
) -> Result<Run> {
    let RequestInput {
        method,
        uri,
        query,
        cookie,
        content_type,
        body,
        mut output_ready,
    } = input;
    let stderr = MemoryOutputPipe::new(8192);
    let stdout = MemoryOutputPipe::new(OUTPUT);
    let bridge = Bridge::new(nonce)?;
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures");
    ensure!(
        fixtures.parent().unwrap().join("outside.txt").is_file(),
        "outside sentinel is missing"
    );
    ensure!(
        !fixtures.join("blocked.txt").exists(),
        "write-denial sentinel already exists"
    );
    let mut builder = WasiCtxBuilder::new();
    builder
        .args(&[
            "php-cgi",
            "-n",
            "-d",
            "cgi.force_redirect=0",
            "-d",
            "expose_php=0",
            "-d",
            "display_errors=stderr",
            "-d",
            "html_errors=0",
            "-d",
            "memory_limit=-1",
            "-f",
            "/site/probe.php",
        ])
        .env("REQUEST_METHOD", method)
        .env("REQUEST_URI", uri)
        .env("QUERY_STRING", query)
        .env("CONTENT_TYPE", content_type)
        .env("CONTENT_LENGTH", body.len().to_string())
        .env("SCRIPT_FILENAME", "/site/probe.php")
        .env("SCRIPT_NAME", "/probe.php")
        .env("SERVER_PROTOCOL", "HTTP/1.1")
        .env("SERVER_NAME", "rp.invalid")
        .env("REDIRECT_STATUS", "200")
        .env("HTTP_COOKIE", cookie)
        .env("RP_PROBE_MODE", mode)
        .allow_tcp(false)
        .allow_udp(false)
        .allow_ip_name_lookup(false)
        .stdin(MemoryInputPipe::new(body))
        .stdout(stdout.clone())
        .stderr(stderr.clone());
    builder.preopened_dir(
        bridge.directory(),
        "/rp-p0-rpc",
        DirPerms::READ | DirPerms::MUTATE,
        FilePerms::READ | FilePerms::WRITE,
    )?;
    builder.preopened_dir(&fixtures, "/site", DirPerms::READ, FilePerms::READ)?;
    builder.preopened_dir(
        wordpress_directory(),
        "/wp-core",
        DirPerms::READ,
        FilePerms::READ,
    )?;
    let mut store = Store::new(
        engine,
        State {
            wasi: builder.build_p1(),
            limits: StoreLimitsBuilder::new()
                .memory_size(MEMORY)
                .table_elements(100_000)
                .instances(2)
                .memories(1)
                .tables(1)
                .trap_on_grow_failure(true)
                .build(),
            memory_denials: 0,
            bridge: bridge.clone(),
            channel_fd: None,
            delegates: None,
        },
    );
    store.limiter(|state| state);
    store.set_fuel(fuel)?;
    store.set_epoch_deadline(1);
    let deadline = Instant::now()
        .checked_add(timeout)
        .context("request deadline overflow")?;
    let observed_output = stdout.clone();
    store.epoch_deadline_callback(move |_| {
        if let Some(sender) = ready.take() {
            let _ = sender.send(nonce);
        }
        if output_ready.is_some()
            && observed_output
                .contents()
                .windows(b"partial response".len())
                .any(|bytes| bytes == b"partial response")
        {
            if let Some(sender) = output_ready.take() {
                let _ = sender.send(nonce);
            }
        }
        if Instant::now() >= deadline {
            Err(Trap::Interrupt.into())
        } else {
            Ok(wasmtime::UpdateDeadline::Continue(1))
        }
    });
    let mut linker = Linker::new(engine);
    preview1::add_to_linker_sync(&mut linker, |state: &mut State| &mut state.wasi)?;
    let original = wasi_rpc::intercept(&mut linker, &mut store)?;
    let exit = (|| {
        let instance = linker.instantiate(&mut store, module)?;
        wasi_rpc::bind(&mut store, instance, original)?;
        let start = instance.get_typed_func::<(), ()>(&mut store, "_start")?;
        match start.call(&mut store, ()) {
            Err(error)
                if error
                    .downcast_ref::<I32Exit>()
                    .is_some_and(|exit| exit.0 == 0) =>
            {
                Ok(())
            }
            result => result,
        }
    })();
    let fuel_left = store.get_fuel()?;
    let memory_denials = store.data().memory_denials;
    ensure!(
        !fixtures.join("blocked.txt").exists(),
        "guest created a file in its read-only preopen"
    );
    drop(store); // Dispose PHP globals, streams, handles, and memory on every path.
    bridge.cleanup()?;
    Ok(Run {
        exit,
        stdout: stdout.contents().to_vec(),
        stderr: stderr.contents().to_vec(),
        bridge,
        fuel_left,
        memory_denials,
    })
}

fn check_basic(engine: &Engine, module: &Module, nonce: u64) -> Result<()> {
    let run = run(engine, module, "basic", nonce, FUEL, Duration::from_secs(5))?;
    let body = run.completed_body().with_context(|| {
        format!(
            "basic CGI output={:?}; stderr={:?}",
            String::from_utf8_lossy(&run.stdout),
            String::from_utf8_lossy(&run.stderr)
        )
    })?;
    let value: serde_json::Value = serde_json::from_slice(&body).context("PHP response JSON")?;
    ensure!(
        value["version"] == "8.2.6",
        "wrong PHP interpreter version: {value}"
    );
    ensure!(value["include"] == "included-from-wasi", "include failed");
    ensure!(
        value["input_hex"] == "727000ff0d0a626f6479",
        "binary request input mismatch"
    );
    ensure!(
        value["cookie"] == nonce.to_string(),
        "request cookie mismatch"
    );
    ensure!(
        value["counter"] == 1 && value["shared_counter"] == 2,
        "request state mismatch"
    );
    ensure!(
        value["cache_before"].is_null() && value["cache_after"] == 7,
        "cache callback or request cleanup failed"
    );
    ensure!(
        value["db_nonce"] == nonce && value["db_rows"] == 1,
        "live Rust DB fixture reply mismatch"
    );
    ensure!(
        value["outside_denied"] == true
            && value["write_denied"] == true
            && value["env_absent"] == true,
        "capability boundary failed"
    );
    run.bridge.check_calls(4)?;
    ensure!(
        value["rpc_output_empty"] == true,
        "RPC escaped into buffered PHP output"
    );
    ensure!(
        value["wp_hooks_initially_empty"] == true,
        "WP hooks leaked between requests"
    );
    ensure!(
        value["wp_filter"] == "post|plugin|theme",
        "WP filter ordering or arguments failed"
    );
    ensure!(
        value["wp_action_counter"] == 1 && value["wp_action_calls"] == 1,
        "WP action/shared object failed"
    );
    ensure!(
        value["wp_null_result"] == true,
        "WP null/sentinel semantics failed"
    );
    Ok(())
}

fn check_descriptor_bridge(engine: &Engine, module: &Module) -> Result<()> {
    let literal = run(
        engine,
        module,
        "literal",
        5100,
        FUEL,
        Duration::from_secs(5),
    )?;
    ensure!(
        literal.completed_body()? == b"RP_P0 CACHE_SET 7\n\0\xff",
        "literal output was interpreted as an RPC or changed"
    );
    literal.bridge.check_calls(0)?;
    for mode in ["fragmented_rpc", "reopen_rpc"] {
        let response = run(engine, module, mode, 5101, FUEL, Duration::from_secs(5))?;
        let body = response.completed_body()?;
        let value: serde_json::Value = serde_json::from_slice(&body)?;
        ensure!(
            value["set"] == true && value["value"] == 7,
            "descriptor protocol failed: {mode}"
        );
        response.bridge.check_calls(2)?;
    }
    Ok(())
}

fn check_cgi_responses(engine: &Engine, module: &Module) -> Result<()> {
    let metadata = run(
        engine,
        module,
        "response_metadata",
        5200,
        FUEL,
        Duration::from_secs(5),
    )?;
    let response = metadata.completed_response()?;
    ensure!(
        response.status == 201 && response.body == b"ok\0\xffbody",
        "CGI status/binary body mismatch"
    );
    let cookies: Vec<_> = response
        .headers
        .iter()
        .filter(|(name, _)| name == "set-cookie")
        .map(|(_, value)| value.as_slice())
        .collect();
    ensure!(
        cookies
            == [
                b"first=1; Path=/; HttpOnly".as_slice(),
                b"second=2; Path=/; SameSite=Lax".as_slice()
            ],
        "CGI cookies were combined or changed"
    );
    ensure!(
        !response
            .headers
            .iter()
            .any(|(name, _)| name == "status" || name == "content-length"),
        "CGI framing leaked into transport headers"
    );
    let redirect = run(
        engine,
        module,
        "redirect_metadata",
        5201,
        FUEL,
        Duration::from_secs(5),
    )?
    .completed_response()?;
    ensure!(
        redirect.status == 307
            && redirect
                .headers
                .iter()
                .any(|(name, value)| name == "location" && value == b"/next?lang=ja"),
        "CGI redirect mismatch"
    );
    let empty = run(
        engine,
        module,
        "no_content",
        5202,
        FUEL,
        Duration::from_secs(5),
    )?
    .completed_response()?;
    ensure!(
        empty.status == 204 && empty.body.is_empty(),
        "204 CGI response mismatch"
    );
    let cached = run(
        engine,
        module,
        "not_modified",
        5203,
        FUEL,
        Duration::from_secs(5),
    )?
    .completed_response()?;
    ensure!(
        cached.status == 304
            && cached.body.is_empty()
            && cached.representation_length == Some(1234),
        "304 representation length mismatch"
    );
    Ok(())
}

fn verify(trace: bool) -> Result<serde_json::Value> {
    ensure!(
        std::env::args_os().len() == if trace { 2 } else { 1 },
        "fixed-fixture probe accepts only --trace"
    );
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    verify_wordpress_sources()?;
    let artifact = root.join(".reference/php-wasi-8.2.6/php-cgi-8.2.6-slim.wasm");
    let file =
        std::fs::File::open(&artifact).context("prepare the pinned PHP WASI fixture first")?;
    ensure!(
        file.metadata()?.len() == FIXTURE_BYTES,
        "wrong fixture length"
    );
    use std::io::Read;
    let mut bytes = Vec::new();
    file.take(FIXTURE_BYTES + 1).read_to_end(&mut bytes)?;
    ensure!(
        bytes.len() as u64 == FIXTURE_BYTES,
        "fixture changed while reading"
    );
    ensure!(
        format!("{:x}", Sha256::digest(&bytes)) == FIXTURE_SHA256,
        "wrong fixture hash"
    );
    let mut config = Config::new();
    config
        .consume_fuel(true)
        .epoch_interruption(true)
        .cranelift_opt_level(wasmtime::OptLevel::None);
    let engine = Engine::new(&config)?;
    let started = std::time::Instant::now();
    if trace {
        eprintln!("P0: compiling the pinned PHP module");
    }
    let module = Module::new(&engine, &bytes)?;
    if trace {
        eprintln!("P0: module compiled in {:?}", started.elapsed());
    }
    let _clock = epoch::EpochClock::new(engine.clone())?;
    for import in module.imports() {
        ensure!(
            import.module() == "wasi_snapshot_preview1",
            "non-WASI import: {}::{}",
            import.module(),
            import.name()
        );
    }
    if trace {
        eprintln!("P0: first basic request");
    }
    check_basic(&engine, &module, 4117)?;
    if trace {
        eprintln!("P0: second basic request");
    }
    check_basic(&engine, &module, 8229)?;
    check_descriptor_bridge(&engine, &module)?;
    check_cgi_responses(&engine, &module)?;
    let mut checks = vec![
        "real_php_wasi",
        "binary_input",
        "cgi_output",
        "include_read",
        "shared_request_state",
        "live_rust_cache_callback",
        "live_rust_db_fixture_callback",
        "fresh_request_state",
        "read_only_preopen",
        "outside_file_denied",
        "no_inherited_environment",
        "wp_filter_chain",
        "wp_actions_shared_object",
        "wp_null_sentinel",
        "wp_request_hook_cleanup",
        "rpc_output_buffer_independent",
        "literal_stdout_preserved",
        "fragmented_rpc",
        "rpc_descriptor_reopen",
        "cgi_status_and_cookies",
        "cgi_redirect",
        "cgi_no_content",
        "cgi_not_modified",
    ];
    for (mode, fuel, timeout, trap) in [
        ("fuel", FUEL, Duration::from_secs(5), Some(Trap::OutOfFuel)),
        (
            "epoch",
            u64::MAX,
            Duration::from_millis(100),
            Some(Trap::Interrupt),
        ),
        ("fatal", FUEL, Duration::from_secs(5), None),
        ("output", FUEL, Duration::from_secs(5), None),
        ("memory", FUEL, Duration::from_secs(5), None),
        ("bad_rpc", FUEL, Duration::from_secs(5), None),
        ("rpc_oversize", FUEL, Duration::from_secs(5), None),
        ("rpc_incomplete", FUEL, Duration::from_secs(5), None),
        ("cgi_wrong_length", FUEL, Duration::from_secs(5), None),
        ("cgi_hop_header", FUEL, Duration::from_secs(5), None),
        ("cgi_header_limit", FUEL, Duration::from_secs(5), None),
    ] {
        if trace {
            eprintln!("P0: failure/recovery fixture {mode}");
        }
        let result = run(&engine, &module, mode, 123, fuel, timeout)?;
        ensure!(
            result.completed_body().is_err(),
            "failed request became publishable: {mode}"
        );
        match mode {
            "fatal" => {
                ensure!(result.exit.is_err(), "PHP fatal did not fail");
                ensure!(
                    result
                        .stdout
                        .windows(b"partial response".len())
                        .any(|bytes| bytes == b"partial response"),
                    "fatal fixture did not produce partial output"
                );
                result.bridge.check_calls(1)?;
            }
            "output" => ensure!(
                result.stdout.len() == OUTPUT,
                "output fixture did not reach its limit"
            ),
            "memory" => ensure!(
                result.memory_denials > 0,
                "memory fixture did not reach the Wasm limit"
            ),
            "bad_rpc" => {
                ensure!(result.exit.is_err(), "unknown RPC did not trap");
                result.bridge.check_calls(1)?;
            }
            "rpc_oversize" => {
                ensure!(result.exit.is_err(), "oversized RPC did not trap");
                result.bridge.check_calls(0)?;
            }
            "rpc_incomplete" => {
                ensure!(
                    result.exit.is_ok() && result.bridge.validate().is_err(),
                    "incomplete frame did not fail after PHP completion"
                );
                result.bridge.check_calls(0)?;
            }
            "cgi_wrong_length" | "cgi_hop_header" | "cgi_header_limit" => {
                ensure!(
                    result.exit.is_ok(),
                    "CGI rejection fixture did not complete normally: {mode}"
                );
                result.bridge.check_calls(0)?;
            }
            _ => {}
        }
        if let Some(trap) = trap {
            let error = result.exit.as_ref().err().context("expected Wasm trap")?;
            ensure!(
                error.downcast_ref::<Trap>() == Some(&trap),
                "wrong {mode} trap: {error:#}; fuel left={}",
                result.fuel_left
            );
        }
        check_basic(&engine, &module, 9441)?;
        checks.push(mode);
    }
    if trace {
        eprintln!("P0: parallel request isolation and independent deadlines");
    }
    parallel::verify(&engine, &module)?;
    checks.extend([
        "parallel_request_isolation",
        "independent_request_deadlines",
        "normal_request_during_sibling_timeout",
    ]);
    if trace {
        eprintln!("P0: loopback HTTP request/response and completion barrier");
    }
    http_probe::verify(&engine, &module)?;
    checks.extend([
        "http_request_input",
        "http_completed_response_metadata",
        "http_no_output_before_completion",
        "http_timeout_recovery",
    ]);
    Ok(serde_json::json!({
        "format_version":4,"milestone":"v0.5-P0-experiment","php_version":"8.2.6",
        "process_pid": std::process::id(),
        "artifact_sha256":FIXTURE_SHA256,"wasmtime_version":"36.0.19",
        "cranelift_opt_level":"none",
        "checks":checks,"recovery_requests":11,"memory_limit_bytes":MEMORY,
        "parallel_requests":parallel::PARALLEL_REQUESTS,"shared_epoch_tick_ms":epoch::TICK_MILLIS,
        "stdout_limit_bytes":OUTPUT,"fuel_per_request":FUEL,
        "product_ready":false,"wordpress_bootstrapped":false,"real_database_connected":false,
        "wordpress_hook_version":"7.1.3","wordpress_hooks_executed":true,
        "http_executed":true,"http_loopback_only":true,
        "bridge":"fixed-fixture dedicated WASI descriptor channel; not the product ABI"
    }))
}

fn main() {
    let trace = std::env::args_os()
        .nth(1)
        .is_some_and(|arg| arg == "--trace");
    match verify(trace) {
        Ok(result) => println!("PHP_WASI_HOST_PROBE_RESULT={result}"),
        Err(error) => {
            eprintln!("PHP WASI host probe failed: {error:#}");
            std::process::exit(1);
        }
    }
}
