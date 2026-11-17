#![forbid(unsafe_code)]

//! Dedicated real PHP-WASIX bridge for the pinned WordPress selected-filter probe.

use std::{
    fs,
    io::{BufRead, BufReader, Read, Write},
    path::{Component, Path, PathBuf},
    sync::{Arc, mpsc},
    thread,
    time::{Duration, Instant},
};

use anyhow::{Context, Result, ensure};
use recastpress_wp::{
    continuation::{PhpHandle, PhpHandleKind, RequestGeneration},
    dispatcher::{DispatchStep, Dispatcher},
    hooks::{CallbackId, ContextCallback, HookArgument, HookPriority, PhpCallMode},
    value::{PhpKey, PhpValue},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use wasmer::Module;
use wasmer_compiler_cranelift::Cranelift;
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
const REQUEST: &str = "surface-probe-1";
const GENERATION: u64 = 81;
const HANDLE_REQUEST: &str = "81";
const TARGET_TOKEN: &str = "wp-hook-1";
const FRAME_LIMIT: usize = 1024 * 1024;
const TOTAL_LIMIT: usize = 32 * 1024 * 1024;
const TIMEOUT: Duration = Duration::from_secs(120);
const PHP_DRIVER: &str = "/fixture/wp_hook_driver.php";

const FILES: [(&str, &[u8]); 7] = [
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
            "../../php-wasix-wp-hook-dispatch-probe/evidence/overlay-v2/wp-includes/class-wp-hook.php"
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
            "../../php-wasix-wp-hook-dispatch-probe/evidence/overlay-v2/wp-includes/plugin.php"
        ),
    ),
    (
        "wp_hook_driver.php",
        include_bytes!("../../php-wasix-wp-hook-dispatch-probe/fixtures/wp_hook_driver.php"),
    ),
];

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn read_frame(
    reader: &mut impl BufRead,
    deadline: Instant,
    total: &mut usize,
    expected_seq: u64,
) -> Result<Value> {
    ensure!(Instant::now() < deadline, "PHP-WASIX bridge deadline");
    let mut line = Vec::new();
    let count = reader
        .take((FRAME_LIMIT + 1) as u64)
        .read_until(b'\n', &mut line)?;
    ensure!(
        count > 0 && count <= FRAME_LIMIT && line.last() == Some(&b'\n'),
        "invalid or oversized bridge frame"
    );
    *total = total
        .checked_add(count)
        .context("bridge byte counter overflow")?;
    ensure!(*total <= TOTAL_LIMIT, "bridge total byte limit");
    let value: Value =
        serde_json::from_slice(&line[..line.len() - 1]).context("decode bridge JSON")?;
    ensure!(
        value.as_object().is_some_and(|o| o.len() == 5),
        "bridge frame object shape"
    );
    ensure!(
        value["v"] == 1 && value["request"] == REQUEST && value["seq"] == expected_seq,
        "bridge version/request/sequence mismatch"
    );
    Ok(value)
}

fn send_frame(
    writer: &mut impl Write,
    seq: u64,
    kind: &str,
    payload: Value,
    total: &mut usize,
    seq_state: &mut u64,
) -> Result<()> {
    ensure!(seq == *seq_state, "outgoing sequence mismatch");
    let bytes = serde_json::to_vec(
        &json!({"v":1,"request":REQUEST,"seq":seq,"kind":kind,"payload":payload}),
    )?;
    ensure!(bytes.len() + 1 <= FRAME_LIMIT, "outgoing frame too large");
    *total = total
        .checked_add(bytes.len() + 1)
        .context("bridge byte counter overflow")?;
    ensure!(*total <= TOTAL_LIMIT, "bridge total byte limit");
    writer.write_all(&bytes)?;
    writer.write_all(b"\n")?;
    writer.flush()?;
    *seq_state += 1;
    Ok(())
}

fn expect_frame(frame: &Value, seq: u64, kind: &str, keys: &[&str]) -> Result<()> {
    ensure!(
        frame["seq"] == seq && frame["kind"] == kind,
        "unexpected frame {kind} at sequence {seq}"
    );
    let payload = frame["payload"]
        .as_object()
        .context("payload must be object")?;
    ensure!(
        payload.len() == keys.len() && keys.iter().all(|key| payload.contains_key(*key)),
        "payload fields mismatch for {kind}"
    );
    Ok(())
}

fn php_string(value: &Value) -> Result<Vec<u8>> {
    Ok(value
        .as_str()
        .context("expected PHP string bytes encoded as UTF-8")?
        .as_bytes()
        .to_vec())
}

fn handle(desc: &Value) -> Result<PhpHandle> {
    let fields = desc
        .as_object()
        .context("handle descriptor must be object")?;
    ensure!(
        fields.len() == 5 && fields["kind"] == "callable" && fields["request"] == "81",
        "handle descriptor schema"
    );
    let slot = u32::try_from(fields["slot"].as_u64().context("handle slot")?)?;
    let generation = u32::try_from(fields["generation"].as_u64().context("handle generation")?)?;
    let token_text = fields["token"].as_str().context("handle token")?;
    ensure!(
        token_text.len() == 16 && token_text.bytes().all(|b| b.is_ascii_hexdigit()),
        "handle token encoding"
    );
    let token = u64::from_str_radix(token_text, 16)?;
    Ok(PhpHandle::new(
        RequestGeneration::new(GENERATION),
        PhpHandleKind::Callable,
        slot,
        generation,
        token,
    ))
}

fn run_dispatch(
    reader: &mut BufReader<impl Read>,
    writer: &mut impl Write,
    total_in: &mut usize,
    total_out: &mut usize,
) -> Result<Value> {
    let deadline = Instant::now() + TIMEOUT;
    let start = read_frame(reader, deadline, total_in, 0)?;
    expect_frame(
        &start,
        0,
        "dispatch_start",
        &[
            "generation",
            "target",
            "hook",
            "value",
            "extra",
            "rows",
            "table_fingerprint",
        ],
    )?;
    ensure!(
        start["payload"]["generation"] == HANDLE_REQUEST,
        "request generation mismatch"
    );
    ensure!(
        start["payload"]["hook"] == "rp-probe-filter" && start["payload"]["target"] == TARGET_TOKEN,
        "selected hook token mismatch"
    );
    let name = HookArgument::bytes("rp-probe-filter");
    let rows = start["payload"]["rows"]
        .as_array()
        .context("rows must be array")?;
    ensure!(rows.len() == 3, "imported callback row count");
    let fingerprint_rows: Vec<Value> = rows
        .iter()
        .map(|row| {
            json!([
                row["priority"],
                row["uid"],
                row["accepted_args"],
                row["function"],
                row["handle"]["request"],
                row["handle"]["kind"],
                row["handle"]["slot"],
                row["handle"]["generation"],
                row["handle"]["token"]
            ])
        })
        .collect();
    let fingerprint = sha256(&serde_json::to_vec(&fingerprint_rows)?);
    ensure!(
        start["payload"]["table_fingerprint"] == fingerprint,
        "PHP callback table fingerprint mismatch"
    );
    let mut dispatcher = Dispatcher::new();
    let mut callback_order = Vec::new();
    let mut handles_by_uid = std::collections::HashMap::new();
    let mut callback_labels = std::collections::HashMap::new();
    for row in rows {
        let obj = row.as_object().context("callback row must be object")?;
        ensure!(
            obj.len() == 5
                && ["priority", "uid", "accepted_args", "function", "handle"]
                    .iter()
                    .all(|k| obj.contains_key(*k)),
            "callback row schema"
        );
        let priority = row["priority"].as_i64().context("callback priority")?;
        let uid = row["uid"].as_str().context("callback uid")?;
        let order = rows
            .iter()
            .position(|candidate| candidate["uid"] == uid)
            .context("callback insertion order")? as u64;
        let callback = match row["function"].as_str() {
            Some("rp_probe_callback_a") => "rp_probe_callback_a",
            Some("rp_probe_callback_b") => "rp_probe_callback_b",
            None => "{closure}",
            Some(other) => anyhow::bail!("unrecognized PHP callback descriptor: {other}"),
        };
        let accepted_args = row["accepted_args"].as_i64().context("accepted_args")?;
        ensure!(
            accepted_args >= 0 && accepted_args <= 32,
            "accepted_args outside probe bounds"
        );
        callback_order.push((priority, order, uid.to_string()));
        let php_handle = handle(&row["handle"])?;
        ensure!(
            handles_by_uid.insert(uid.to_string(), php_handle).is_none(),
            "duplicate WordPress callback uid"
        );
        callback_labels.insert(uid.to_string(), callback.to_string());
        let id = CallbackId {
            module: "wordpress".into(),
            callback: uid.to_string(),
        };
        let callback = ContextCallback::php_callable(id, accepted_args, php_handle)
            .map_err(|e| anyhow::anyhow!("WP dispatcher callback import: {e:?}"))?;
        dispatcher.add_typed(&name, HookPriority::integer(priority), callback);
    }
    let mut sorted = callback_order.clone();
    sorted.sort_by_key(|(priority, order, _)| (*priority, *order));
    ensure!(
        callback_order == sorted,
        "PHP callback table order was not priority/insertion order"
    );
    let target = dispatcher
        .capture_target(&name, false)
        .map_err(|e| anyhow::anyhow!("WP dispatcher target import: {e:?}"))?
        .context("selected hook target absent")?;
    let value = PhpValue::Bytes(php_string(&start["payload"]["value"])?);
    let extras = start["payload"]["extra"]
        .as_array()
        .context("extra args must be array")?
        .iter()
        .map(|v| php_string(v).map(PhpValue::Bytes))
        .collect::<Result<Vec<_>>>()?;
    let ticket = dispatcher
        .begin_filter_on_target_typed(&name, target, value, &extras)
        .map_err(|e| anyhow::anyhow!("WP dispatcher filter start: {e:?}"))?;
    let mut in_seq = 1;
    let mut out_seq = 0;
    let mut imported = Vec::new();
    loop {
        match dispatcher
            .advance(ticket)
            .map_err(|e| anyhow::anyhow!("WP dispatcher advance: {e:?}"))?
        {
            DispatchStep::InvokePhp(invocation) => {
                ensure!(
                    invocation.call_mode != PhpCallMode::All,
                    "all callback should not be in selected table"
                );
                let uid = invocation.callback.callback.as_str();
                ensure!(
                    callback_labels.contains_key(uid),
                    "invocation callback uid missing"
                );
                ensure!(
                    handles_by_uid.get(uid) == Some(&invocation.handle),
                    "Dispatcher callback handle does not match imported PHP uid"
                );
                let priority = match invocation.priority {
                    PhpKey::Integer(n) => n,
                    _ => anyhow::bail!("priority must be integer"),
                };
                let php_args = invocation
                    .args
                    .iter()
                    .map(|value| match value {
                        PhpValue::Bytes(bytes) => Ok(String::from_utf8(bytes.clone())
                            .context("callback argument is not UTF-8")?),
                        _ => anyhow::bail!("fixture only transports string callback arguments"),
                    })
                    .collect::<Result<Vec<_>>>()?;
                let payload = json!({"target":TARGET_TOKEN,"uid":uid,"handle":{"request":HANDLE_REQUEST,"kind":"callable","slot":invocation.handle.slot(),"generation":invocation.handle.generation(),"token":format!("{:016x}",invocation.handle.token())},"priority":priority,"call_mode":if matches!(invocation.call_mode,PhpCallMode::Zero){"zero"}else{"array"},"args":php_args});
                ensure!(
                    payload["call_mode"] == "zero" && invocation.args.is_empty()
                        || payload["call_mode"] == "array" && !invocation.args.is_empty(),
                    "PHP invocation call mode/args mismatch"
                );
                send_frame(
                    writer,
                    out_seq,
                    "invoke_php",
                    payload.clone(),
                    total_out,
                    &mut out_seq,
                )?;
                let reply = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(&reply, in_seq, "resume_value", &["target", "uid", "value"])?;
                ensure!(
                    reply["payload"]["target"] == TARGET_TOKEN && reply["payload"]["uid"] == uid,
                    "callback correlation mismatch"
                );
                in_seq += 1;
                dispatcher
                    .resume_php(
                        ticket,
                        PhpValue::Bytes(php_string(&reply["payload"]["value"])?),
                    )
                    .map_err(|e| anyhow::anyhow!("WP dispatcher resume: {e:?}"))?;
                imported.push(callback_labels[uid].clone());
            }
            DispatchStep::ReleasePhp(h) => {
                let descriptor = json!({"request":HANDLE_REQUEST,"kind":"callable","slot":h.slot(),"generation":h.generation(),"token":format!("{:016x}",h.token())});
                send_frame(
                    writer,
                    out_seq,
                    "release_php",
                    json!({"handle":descriptor}),
                    total_out,
                    &mut out_seq,
                )?;
                let ack = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(&ack, in_seq, "release_ack", &["handle"])?;
                ensure!(
                    ack["payload"]["handle"] == descriptor,
                    "in-dispatch PHP release ACK identity mismatch"
                );
                in_seq += 1;
                dispatcher
                    .resume_php_release(ticket, h)
                    .map_err(|e| anyhow::anyhow!("WP dispatcher release resume: {e:?}"))?;
            }
            DispatchStep::Boundary(_) => {
                anyhow::bail!("unexpected PHP boundary in selected-target dispatch")
            }
            DispatchStep::PhpThrowable(_) => anyhow::bail!("unexpected PHP Throwable"),
            DispatchStep::Complete(result) => {
                let value = match result {
                    PhpValue::Bytes(bytes) => String::from_utf8(bytes)?,
                    _ => anyhow::bail!("unexpected filter result type"),
                };
                send_frame(
                    writer,
                    out_seq,
                    "complete",
                    json!({"target":TARGET_TOKEN,"value":value,"invocations":imported.len()}),
                    total_out,
                    &mut out_seq,
                )?;
                let done = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(&done, in_seq, "request_complete", &["target", "result"])?;
                ensure!(
                    done["payload"]["target"] == TARGET_TOKEN && done["payload"]["result"] == value,
                    "guest completion mismatch"
                );
                in_seq += 1;
                dispatcher.remove_all_typed(&name, None);
                for handle in dispatcher.take_php_releases() {
                    let descriptor = json!({"request":HANDLE_REQUEST,"kind":"callable","slot":handle.slot(),"generation":handle.generation(),"token":format!("{:016x}",handle.token())});
                    send_frame(
                        writer,
                        out_seq,
                        "release_php",
                        json!({"handle":descriptor}),
                        total_out,
                        &mut out_seq,
                    )?;
                    let ack = read_frame(reader, deadline, total_in, in_seq)?;
                    expect_frame(&ack, in_seq, "release_ack", &["handle"])?;
                    ensure!(
                        ack["payload"]["handle"] == descriptor,
                        "callback release ACK identity mismatch"
                    );
                    in_seq += 1;
                }
                dispatcher
                    .release_target(target)
                    .map_err(|e| anyhow::anyhow!("WP dispatcher target release: {e:?}"))?;
                send_frame(
                    writer,
                    out_seq,
                    "request_complete",
                    json!({"target":TARGET_TOKEN,"result":value}),
                    total_out,
                    &mut out_seq,
                )?;
                let done = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(
                    &done,
                    in_seq,
                    "guest_done",
                    &[
                        "result",
                        "events",
                        "filter_count",
                        "current_filter",
                        "doing_filter",
                        "current_priority",
                        "table_fingerprint",
                        "live_handles",
                        "live_handles_before_close",
                        "frames",
                        "invocations",
                    ],
                )?;
                ensure!(
                    done["payload"]["result"] == value
                        && done["payload"]["filter_count"] == 1
                        && done["payload"]["current_filter"] == false
                        && done["payload"]["doing_filter"] == false
                        && done["payload"]["current_priority"] == false
                        && done["payload"]["live_handles"] == 0
                        && done["payload"]["live_handles_before_close"] == 0
                        && done["payload"]["table_fingerprint"] == fingerprint
                        && done["payload"]["invocations"]
                            == json!([rows[0]["uid"], rows[1]["uid"], rows[2]["uid"]]),
                    "guest final observation mismatch"
                );
                ensure!(
                    imported == ["rp_probe_callback_a", "{closure}", "rp_probe_callback_b"],
                    "callback invocation order mismatch"
                );
                let events = done["payload"]["events"]
                    .as_array()
                    .context("guest events missing")?;
                ensure!(
                    events.len() == 4,
                    "native all plus selected callback event count mismatch"
                );
                ensure!(
                    events[0]["callback"] == "all"
                        && events[0]["hook"] == "rp-probe-filter"
                        && events[0]["args"] == json!(["seed", "left", "right"])
                        && events[0]["current_filter"] == "rp-probe-filter"
                        && events[0]["doing_filter"] == true
                        && events[0]["did_filter"] == 1,
                    "WordPress all callback observation mismatch"
                );
                ensure!(
                    events[1]["callback"] == "a"
                        && events[1]["args"] == json!(["seed", "left", "right"])
                        && events[1]["current_filter"] == "rp-probe-filter"
                        && events[1]["doing_filter"] == true
                        && events[1]["did_filter"] == 1
                        && events[1]["current_priority"] == 5,
                    "first selected callback observation mismatch"
                );
                ensure!(
                    events[2]["callback"] == "zero"
                        && events[2]["args"] == json!([])
                        && events[2]["current_filter"] == "rp-probe-filter"
                        && events[2]["doing_filter"] == true
                        && events[2]["did_filter"] == 1
                        && events[2]["current_priority"] == 5,
                    "zero-argument callback observation mismatch"
                );
                ensure!(
                    events[3]["callback"] == "b"
                        && events[3]["args"] == json!(["Z", "left", "right"])
                        && events[3]["current_filter"] == "rp-probe-filter"
                        && events[3]["doing_filter"] == true
                        && events[3]["did_filter"] == 1
                        && events[3]["current_priority"] == 10,
                    "last selected callback observation mismatch"
                );
                return Ok(
                    json!({"status":"passed","result":value,"callbacks":imported,"guest":done["payload"],"bridge":{"input_frames":in_seq+1,"output_frames":out_seq,"input_bytes":*total_in,"output_bytes":*total_out,"timeout_seconds":TIMEOUT.as_secs()}}),
                );
            }
        }
    }
}

fn main() -> Result<()> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()?;
    let wasm_path = root.join(".reference/php-wasix-8.5.12/php.wasm");
    let wasm = fs::read(&wasm_path).context("read pinned PHP-WASIX artifact")?;
    ensure!(
        wasm.len() as u64 == PHP_BYTES && sha256(&wasm) == PHP_SHA256,
        "PHP-WASIX artifact pin mismatch"
    );
    let data = HashedModuleData::new(wasm);
    let module_hash = *data.hash();
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let _guard = runtime.enter();
    let tasks = TokioTaskManager::new(runtime.handle().clone());
    let mut wasix = PluggableRuntime::new(Arc::new(tasks));
    let compiler = Cranelift::default();
    wasix.set_engine(compiler.into());
    wasix.set_networking_implementation(UnsupportedVirtualNetworking::default());
    wasix.http_client = None;
    let module = wasix
        .load_hashed_module_sync(data, None)
        .context("compile pinned PHP-WASIX")?;
    // Mirror the proven bridge topology: the shared wire is split at the host,
    // while the guest keeps a clone for stdin and the original endpoint for stdout.
    let (host_wire, guest_wire) = Pipe::channel();
    let (mut host_stdin, host_stdout) = host_wire.split();
    let guest_stdin = guest_wire.clone();
    let guest_stdout = guest_wire;
    let (mut host_stderr, guest_stderr) = Pipe::channel();
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
    let guest_thread = thread::Builder::new()
        .name("php-wasix-wp-hook-dispatch".into())
        .spawn(move || {
            let result = run_guest(
                &module_copy,
                module_hash,
                guest_stdin,
                guest_stdout,
                guest_stderr,
                done_fs,
            );
            let _ = done_tx.send(result);
        })?;
    let mut total_in = 0;
    let mut total_out = 0;
    let mut reader = BufReader::new(DeadlinePipeReader {
        pipe: host_stdout,
        deadline: Instant::now() + TIMEOUT,
    });
    let observed = run_dispatch(
        &mut reader,
        &mut DeadlinePipeWriter {
            pipe: &mut host_stdin,
            deadline: Instant::now() + TIMEOUT,
        },
        &mut total_in,
        &mut total_out,
    );
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
    drop(wasix);
    drop(_guard);
    runtime.shutdown_timeout(Duration::from_secs(2));
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
        .filter(|line| !line.starts_with("RP_HOOK_PHASE="))
        .collect::<Vec<_>>();
    ensure!(
        unexpected_stderr.is_empty(),
        "PHP guest wrote stderr: {}",
        unexpected_stderr.join("\n")
    );
    println!(
        "{}",
        json!({"status":"passed","php_wasm_sha256":PHP_SHA256,"php_wasm_bytes":PHP_BYTES,"overlay_files":FILES.iter().map(|(n,b)|json!({"path":n,"sha256":sha256(b)})).collect::<Vec<_>>(),"observation":observed})
    );
    Ok(())
}

fn run_guest(
    module: &Module,
    module_hash: wasmer_types::ModuleHash,
    stdin: Pipe,
    stdout: Pipe,
    stderr: Pipe,
    fs_map: Arc<FixtureFs>,
) -> Result<()> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let _guard = runtime.enter();
    let tasks = TokioTaskManager::new(runtime.handle().clone());
    let mut wasix = PluggableRuntime::new(Arc::new(tasks) as Arc<dyn VirtualTaskManager>);
    wasix.set_engine(Cranelift::default().into());
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
        .with_args(["-n", PHP_DRIVER])
        .with_current_dir("/fixture")
        .with_mount("/fixture".to_string(), fs_map)
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
        .context("run pinned PHP-WASIX WordPress hook fixture")?;
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
