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
    continuation::{Completion, PhpHandle, PhpHandleKind, RequestGeneration},
    dispatcher::{DispatchStep, Dispatcher},
    hooks::{CallbackId, ContextCallback, HookArgument, HookPriority, PhpCallMode},
    value::{PhpKey, PhpValue},
};
use serde_json::{Value, json};
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
const REQUEST: &str = "surface-probe-1";
const GENERATION: u64 = 81;
const HANDLE_REQUEST: &str = "81";
const TARGET_TOKEN: &str = "wp-hook-1";
const FRAME_LIMIT: usize = 1024 * 1024;
const TOTAL_LIMIT: usize = 32 * 1024 * 1024;
const TIMEOUT: Duration = Duration::from_secs(120);
const PHP_DRIVER: &str = "/fixture/wp_hook_driver.php";
const PHP_ACTION_DRIVER: &str = "/fixture/wp_action_driver.php";
const PHP_ACTION_REF_ARRAY_DRIVER: &str = "/fixture/wp_action_ref_array_driver.php";
const PHP_ACTION_THROWABLE_DRIVER: &str = "/fixture/wp_action_throwable_driver.php";

const FILES: [(&str, &[u8]); 10] = [
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
        "unexpected frame: received seq={:?} kind={:?}; expected seq={seq} kind={kind}",
        frame["seq"],
        frame["kind"]
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
    handle_kind(desc, "callable", PhpHandleKind::Callable)
}

fn argument_handle(desc: &Value) -> Result<PhpHandle> {
    handle_kind(desc, "argument", PhpHandleKind::Argument)
}

fn handle_kind(desc: &Value, expected_kind: &str, kind: PhpHandleKind) -> Result<PhpHandle> {
    let fields = desc
        .as_object()
        .context("handle descriptor must be object")?;
    ensure!(
        fields.len() == 5 && fields["kind"] == expected_kind && fields["request"] == "81",
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
        kind,
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
    ref_array_mode: bool,
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
            "argument_frame",
            "rows",
            "table_fingerprint",
        ],
    )?;
    ensure!(
        start["payload"]["generation"] == HANDLE_REQUEST,
        "request generation mismatch"
    );
    let argument_frame = if ref_array_mode {
        Some(argument_handle(&start["payload"]["argument_frame"])?)
    } else {
        ensure!(
            start["payload"]["argument_frame"].is_null(),
            "unexpected PHP argument frame"
        );
        None
    };
    let expected_hook = if ref_array_mode {
        "rp-probe-ref-array"
    } else {
        "rp-probe-filter"
    };
    ensure!(
        start["payload"]["hook"] == expected_hook && start["payload"]["target"] == TARGET_TOKEN,
        "selected hook token mismatch"
    );
    let name = HookArgument::bytes(expected_hook);
    let rows = start["payload"]["rows"]
        .as_array()
        .context("rows must be array")?;
    ensure!(
        rows.len() == if ref_array_mode { 2 } else { 3 },
        "imported callback row count"
    );
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
            Some("rp_probe_callback_a") if !ref_array_mode => "rp_probe_callback_a",
            Some("rp_probe_callback_b") if !ref_array_mode => "rp_probe_callback_b",
            Some("rp_ref_array_first") if ref_array_mode => "rp_ref_array_first",
            Some("rp_ref_array_second") if ref_array_mode => "rp_ref_array_second",
            None if !ref_array_mode => "{closure}",
            None => anyhow::bail!("ref-array probe does not accept closure callbacks"),
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
    let initial_value = php_string(&start["payload"]["value"])?;
    let mut current_value = initial_value.clone();
    let value = PhpValue::Bytes(initial_value);
    let extra_values = start["payload"]["extra"]
        .as_array()
        .context("extra args must be array")?;
    let extras = if let Some(frame_handle) = argument_frame {
        extra_values
            .iter()
            .map(|_| PhpValue::ArgumentHandle(frame_handle))
            .collect()
    } else {
        extra_values
            .iter()
            .map(|v| php_string(v).map(PhpValue::Bytes))
            .collect::<Result<Vec<_>>>()?
    };
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
                    .enumerate()
                    .map(|(index, value)| match value {
                        PhpValue::Bytes(bytes) => {
                            if ref_array_mode {
                                ensure!(index == 0, "only the filter accumulator is a direct ref-array value");
                            }
                            let value = String::from_utf8(bytes.clone())
                                .context("callback argument is not UTF-8")?;
                            if index == 0 {
                                ensure!(value.as_bytes() == current_value, "Dispatcher filter accumulator drift");
                            }
                            Ok(json!(value))
                        }
                        PhpValue::ArgumentHandle(handle) if ref_array_mode => {
                            ensure!(Some(*handle) == argument_frame, "ref-array slot handle mismatch");
                            Ok(json!({"type":"argument_frame_slot","handle":{"request":HANDLE_REQUEST,"kind":"argument","slot":handle.slot(),"generation":handle.generation(),"token":format!("{:016x}",handle.token())},"index":index}))
                        }
                        _ => anyhow::bail!("unexpected callback argument type for hook fixture"),
                    })
                    .collect::<Result<Vec<_>>>()?;
                let payload = json!({"target":TARGET_TOKEN,"uid":uid,"handle":{"request":HANDLE_REQUEST,"kind":"callable","slot":invocation.handle.slot(),"generation":invocation.handle.generation(),"token":format!("{:016x}",invocation.handle.token())},"priority":priority,"call_mode":if matches!(invocation.call_mode,PhpCallMode::Zero){"zero"}else{"array"},"args":php_args,"value":String::from_utf8(current_value.clone()).context("filter accumulator is not UTF-8")?,"argument_frame":if ref_array_mode {start["payload"]["argument_frame"].clone()} else {Value::Null}});
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
                let returned = php_string(&reply["payload"]["value"])?;
                current_value = returned.clone();
                dispatcher
                    .resume_php(ticket, PhpValue::Bytes(returned))
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
                if let Some(frame_handle) = argument_frame {
                    let descriptor = json!({"request":HANDLE_REQUEST,"kind":"argument","slot":frame_handle.slot(),"generation":frame_handle.generation(),"token":format!("{:016x}",frame_handle.token())});
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
                        "argument-frame release ACK identity mismatch"
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
                        "caller_value",
                        "caller_extra",
                        "live_handles",
                        "live_handles_before_close",
                        "argument_frame_released_before_close",
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
                        && done["payload"]["caller_value"]
                            == if ref_array_mode {
                                json!("replacement-from-first")
                            } else {
                                Value::Null
                            }
                        && done["payload"]["caller_extra"]
                            == if ref_array_mode {
                                json!("extra-mutated-by-second")
                            } else {
                                Value::Null
                            }
                        && done["payload"]["live_handles"] == 0
                        && done["payload"]["live_handles_before_close"] == 0
                        && done["payload"]["argument_frame_released_before_close"] == true
                        && done["payload"]["table_fingerprint"] == fingerprint
                        && done["payload"]["invocations"]
                            == if ref_array_mode {
                                json!([rows[0]["uid"], rows[1]["uid"]])
                            } else {
                                json!([rows[0]["uid"], rows[1]["uid"], rows[2]["uid"]])
                            },
                    "guest final observation mismatch"
                );
                ensure!(
                    imported
                        == if ref_array_mode {
                            vec![
                                "rp_ref_array_first".to_string(),
                                "rp_ref_array_second".to_string(),
                            ]
                        } else {
                            vec![
                                "rp_probe_callback_a".to_string(),
                                "{closure}".to_string(),
                                "rp_probe_callback_b".to_string(),
                            ]
                        },
                    "callback invocation order mismatch"
                );
                let events = done["payload"]["events"]
                    .as_array()
                    .context("guest events missing")?;
                ensure!(
                    events.len() == if ref_array_mode { 3 } else { 4 },
                    "native all plus selected callback event count mismatch"
                );
                ensure!(
                    events[0]["callback"] == "all"
                        && events[0]["hook"] == expected_hook
                        && events[0]["args"]
                            == if ref_array_mode {
                                json!(["seed", "left"])
                            } else {
                                json!(["seed", "left", "right"])
                            }
                        && events[0]["current_filter"] == expected_hook
                        && events[0]["doing_filter"] == true
                        && events[0]["did_filter"] == 1,
                    "WordPress all callback observation mismatch"
                );
                if ref_array_mode {
                    ensure!(
                        events[1]["callback"] == "first"
                            && events[1]["args"] == json!(["seed", "left"])
                            && events[1]["current_filter"] == expected_hook
                            && events[1]["doing_filter"] == true
                            && events[1]["did_filter"] == 1
                            && events[1]["current_priority"] == 5
                            && events[2]["callback"] == "second"
                            && events[2]["args"]
                                == json!(["replacement-from-first", "extra-mutated-by-first"])
                            && events[2]["current_filter"] == expected_hook
                            && events[2]["doing_filter"] == true
                            && events[2]["did_filter"] == 1
                            && events[2]["current_priority"] == 10,
                        "WordPress ref-array callback observations mismatch"
                    );
                } else {
                    ensure!(
                        events[1]["callback"] == "a"
                            && events[1]["args"] == json!(["seed", "left", "right"])
                            && events[1]["current_filter"] == "rp-probe-filter"
                            && events[1]["doing_filter"] == true
                            && events[1]["did_filter"] == 1
                            && events[1]["current_priority"] == 5,
                        "first selected callback observation mismatch"
                    );
                }
                if !ref_array_mode {
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
                }
                return Ok(
                    json!({"status":"passed","result":value,"callbacks":imported,"guest":done["payload"],"bridge":{"input_frames":in_seq+1,"output_frames":out_seq,"input_bytes":*total_in,"output_bytes":*total_out,"timeout_seconds":TIMEOUT.as_secs()}}),
                );
            }
        }
    }
}

fn run_action_dispatch(
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
        "action_dispatch_start",
        &[
            "generation",
            "target",
            "hook",
            "args",
            "rows",
            "table_fingerprint",
        ],
    )?;
    ensure!(
        start["payload"]["generation"] == HANDLE_REQUEST
            && start["payload"]["hook"] == "rp-probe-action"
            && start["payload"]["target"] == "wp-action-1",
        "selected action identity mismatch"
    );
    let name = HookArgument::bytes("rp-probe-action");
    let rows = start["payload"]["rows"]
        .as_array()
        .context("action rows must be array")?;
    ensure!(rows.len() == 3, "imported action callback row count");
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
        "PHP action callback table fingerprint mismatch"
    );
    let mut dispatcher = Dispatcher::new();
    let mut callback_order = Vec::new();
    let mut handles_by_uid = std::collections::HashMap::new();
    let mut callback_labels = std::collections::HashMap::new();
    for row in rows {
        let object = row
            .as_object()
            .context("action callback row must be object")?;
        ensure!(
            object.len() == 5
                && ["priority", "uid", "accepted_args", "function", "handle"]
                    .iter()
                    .all(|key| object.contains_key(*key)),
            "action callback row schema"
        );
        let priority = row["priority"]
            .as_i64()
            .context("action callback priority")?;
        let uid = row["uid"].as_str().context("action callback uid")?;
        let insertion_order = rows
            .iter()
            .position(|candidate| candidate["uid"] == uid)
            .context("action callback insertion order")? as u64;
        let label = match row["function"].as_str() {
            Some("rp_action_callback_a") => "rp_action_callback_a",
            Some("rp_action_callback_b") => "rp_action_callback_b",
            None => "{closure}",
            Some(other) => anyhow::bail!("unrecognized PHP action callback: {other}"),
        };
        let accepted_args = row["accepted_args"]
            .as_i64()
            .context("action accepted_args")?;
        ensure!(
            (0..=32).contains(&accepted_args),
            "action accepted_args outside probe bounds"
        );
        callback_order.push((priority, insertion_order, uid.to_string()));
        let php_handle = handle(&row["handle"])?;
        ensure!(
            handles_by_uid.insert(uid.to_string(), php_handle).is_none(),
            "duplicate WordPress action callback uid"
        );
        callback_labels.insert(uid.to_string(), label.to_string());
        let id = CallbackId {
            module: "wordpress".into(),
            callback: uid.to_string(),
        };
        let callback = ContextCallback::php_callable(id, accepted_args, php_handle)
            .map_err(|error| anyhow::anyhow!("WP action dispatcher callback import: {error:?}"))?;
        dispatcher.add_typed(&name, HookPriority::integer(priority), callback);
    }
    let mut sorted = callback_order.clone();
    sorted.sort_by_key(|(priority, insertion_order, _)| (*priority, *insertion_order));
    ensure!(
        callback_order == sorted,
        "PHP action callback table order mismatch"
    );
    let args = start["payload"]["args"]
        .as_array()
        .context("action args must be array")?
        .iter()
        .map(|value| php_string(value).map(PhpValue::Bytes))
        .collect::<Result<Vec<_>>>()?;
    let ticket = dispatcher
        .begin_action_after_counter_typed(&name, &args)
        .map_err(|error| anyhow::anyhow!("WP action dispatcher start: {error:?}"))?;
    let mut in_seq = 1;
    let mut out_seq = 0;
    let mut imported = Vec::new();
    loop {
        match dispatcher
            .advance(ticket)
            .map_err(|error| anyhow::anyhow!("WP action dispatcher advance: {error:?}"))?
        {
            DispatchStep::InvokePhp(invocation) => {
                ensure!(
                    invocation.call_mode != PhpCallMode::All,
                    "unexpected all action callback import"
                );
                let uid = invocation.callback.callback.as_str();
                ensure!(
                    callback_labels.contains_key(uid),
                    "action invocation callback uid missing"
                );
                ensure!(
                    handles_by_uid.get(uid) == Some(&invocation.handle),
                    "Action dispatcher handle does not match imported PHP uid"
                );
                let priority = match invocation.priority {
                    PhpKey::Integer(value) => value,
                    _ => anyhow::bail!("action priority must be integer"),
                };
                let php_args = invocation
                    .args
                    .iter()
                    .map(|value| match value {
                        PhpValue::Bytes(bytes) => Ok(String::from_utf8(bytes.clone())
                            .context("action callback argument is not UTF-8")?),
                        _ => anyhow::bail!("action fixture only transports UTF-8 string arguments"),
                    })
                    .collect::<Result<Vec<_>>>()?;
                let payload = json!({
                    "target":"wp-action-1",
                    "uid":uid,
                    "handle":{
                        "request":HANDLE_REQUEST,
                        "kind":"callable",
                        "slot":invocation.handle.slot(),
                        "generation":invocation.handle.generation(),
                        "token":format!("{:016x}", invocation.handle.token())
                    },
                    "priority":priority,
                    "call_mode":if matches!(invocation.call_mode, PhpCallMode::Zero) { "zero" } else { "array" },
                    "args":php_args
                });
                ensure!(
                    payload["call_mode"] == "zero" && invocation.args.is_empty()
                        || payload["call_mode"] == "array" && !invocation.args.is_empty(),
                    "action invocation call mode/args mismatch"
                );
                send_frame(
                    writer,
                    out_seq,
                    "invoke_php",
                    payload,
                    total_out,
                    &mut out_seq,
                )?;
                let reply = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(&reply, in_seq, "resume_value", &["target", "uid", "value"])?;
                ensure!(
                    reply["payload"]["target"] == "wp-action-1"
                        && reply["payload"]["uid"] == uid
                        && reply["payload"]["value"].is_null(),
                    "action callback must resume with ignored null result"
                );
                in_seq += 1;
                dispatcher
                    .resume_php(ticket, PhpValue::Null)
                    .map_err(|error| anyhow::anyhow!("WP action dispatcher resume: {error:?}"))?;
                imported.push(callback_labels[uid].clone());
            }
            DispatchStep::ReleasePhp(php_handle) => {
                let descriptor = json!({
                    "request":HANDLE_REQUEST,
                    "kind":"callable",
                    "slot":php_handle.slot(),
                    "generation":php_handle.generation(),
                    "token":format!("{:016x}", php_handle.token())
                });
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
                    "action callback release ACK mismatch"
                );
                in_seq += 1;
                dispatcher
                    .resume_php_release(ticket, php_handle)
                    .map_err(|error| {
                        anyhow::anyhow!("WP action dispatcher release resume: {error:?}")
                    })?;
            }
            DispatchStep::Boundary(_) => {
                anyhow::bail!("unexpected PHP boundary in named action dispatch")
            }
            DispatchStep::PhpThrowable(_) => {
                anyhow::bail!("unexpected PHP Throwable in action probe")
            }
            DispatchStep::Complete(result) => {
                ensure!(
                    result == PhpValue::Null,
                    "WordPress action dispatcher returned non-null value"
                );
                ensure!(
                    imported == ["rp_action_callback_a", "{closure}", "rp_action_callback_b"],
                    "action callback invocation order mismatch"
                );
                send_frame(
                    writer,
                    out_seq,
                    "action_complete",
                    json!({"target":"wp-action-1","invocations":imported.len()}),
                    total_out,
                    &mut out_seq,
                )?;
                let php_done = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(
                    &php_done,
                    in_seq,
                    "request_complete",
                    &["target", "result", "did_action"],
                )?;
                ensure!(
                    php_done["payload"]["target"] == "wp-action-1"
                        && php_done["payload"]["result"].is_null()
                        && php_done["payload"]["did_action"] == 1,
                    "WordPress action entrypoint completion differs"
                );
                in_seq += 1;
                dispatcher.remove_all_typed(&name, None);
                for php_handle in dispatcher.take_php_releases() {
                    let descriptor = json!({
                        "request":HANDLE_REQUEST,
                        "kind":"callable",
                        "slot":php_handle.slot(),
                        "generation":php_handle.generation(),
                        "token":format!("{:016x}", php_handle.token())
                    });
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
                        "action registration release ACK mismatch"
                    );
                    in_seq += 1;
                }
                send_frame(
                    writer,
                    out_seq,
                    "request_complete",
                    json!({"target":"wp-action-1","result":null}),
                    total_out,
                    &mut out_seq,
                )?;
                let done = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(
                    &done,
                    in_seq,
                    "guest_done",
                    &[
                        "events",
                        "action_count",
                        "empty_action_count",
                        "current_action",
                        "current_filter",
                        "doing_action",
                        "wp_hook_doing_action",
                        "current_priority",
                        "table_fingerprint",
                        "live_handles",
                        "live_handles_before_close",
                        "invocations",
                        "empty_action_current_priority",
                        "empty_action_wp_hook_doing_action",
                        "frames",
                    ],
                )?;
                ensure!(
                    done["payload"]["action_count"] == 1
                        && done["payload"]["empty_action_count"] == 1
                        && done["payload"]["current_action"] == false
                        && done["payload"]["current_filter"] == false
                        && done["payload"]["doing_action"] == false
                        && done["payload"]["wp_hook_doing_action"] == false
                        && done["payload"]["current_priority"] == false
                        && done["payload"]["table_fingerprint"] == fingerprint
                        && done["payload"]["live_handles"] == 0
                        && done["payload"]["live_handles_before_close"] == 0
                        && done["payload"]["empty_action_current_priority"] == false
                        && done["payload"]["empty_action_wp_hook_doing_action"] == false
                        && done["payload"]["invocations"]
                            == json!([rows[0]["uid"], rows[1]["uid"], rows[2]["uid"]]),
                    "WordPress action final observation mismatch"
                );
                let events = done["payload"]["events"]
                    .as_array()
                    .context("action events missing")?;
                ensure!(
                    events.len() == 6,
                    "all plus named action event count mismatch"
                );
                ensure!(
                    events[0]["callback"] == "all"
                        && events[0]["hook"] == "rp-probe-action"
                        && events[0]["args"] == json!(["rp-probe-action", "left", "right"])
                        && events[0]["current_action"] == "rp-probe-action"
                        && events[0]["doing_action"] == true
                        && events[0]["did_action"] == 1,
                    "WordPress all action observation mismatch"
                );
                ensure!(
                    events[1]["callback"] == "a"
                        && events[1]["args"] == json!(["left", "right"])
                        && events[1]["current_action"] == "rp-probe-action"
                        && events[1]["doing_action"] == true
                        && events[1]["did_action"] == 1
                        && events[1]["current_priority"] == 5
                        && events[1]["wp_hook_doing_action"] == true,
                    "first named action observation mismatch"
                );
                ensure!(
                    events[2]["callback"] == "zero"
                        && events[2]["args"] == json!([])
                        && events[2]["current_action"] == "rp-probe-action"
                        && events[2]["doing_action"] == true
                        && events[2]["did_action"] == 1
                        && events[2]["current_priority"] == 5
                        && events[2]["wp_hook_doing_action"] == true,
                    "zero-argument action observation mismatch"
                );
                ensure!(
                    events[3]["callback"] == "b"
                        && events[3]["args"] == json!(["left"])
                        && events[3]["current_action"] == "rp-probe-action"
                        && events[3]["doing_action"] == true
                        && events[3]["did_action"] == 1
                        && events[3]["current_priority"] == 10
                        && events[3]["wp_hook_doing_action"] == true,
                    "last named action observation mismatch"
                );
                ensure!(
                    events[4]["callback"] == "all"
                        && events[4]["hook"] == "rp-probe-empty-action"
                        && events[4]["args"] == json!(["rp-probe-empty-action"])
                        && events[4]["current_action"] == "rp-probe-empty-action"
                        && events[4]["doing_action"] == true
                        && events[4]["did_action"] == 1,
                    "empty action all-hook observation mismatch"
                );
                ensure!(
                    events[5]["callback"] == "empty"
                        && events[5]["args"] == json!([""])
                        && events[5]["current_action"] == "rp-probe-empty-action"
                        && events[5]["doing_action"] == true
                        && events[5]["did_action"] == 1
                        && events[5]["current_priority"] == 10
                        && events[5]["wp_hook_doing_action"] == true,
                    "empty action placeholder observation mismatch"
                );
                return Ok(json!({
                    "status":"passed",
                    "result":null,
                    "callbacks":imported,
                    "guest":done["payload"],
                    "bridge":{
                        "input_frames":in_seq + 1,
                        "output_frames":out_seq,
                        "input_bytes":*total_in,
                        "output_bytes":*total_out,
                        "timeout_seconds":TIMEOUT.as_secs()
                    }
                }));
            }
        }
    }
}

fn run_action_throwable_dispatch(
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
        "action_dispatch_start",
        &[
            "generation",
            "target",
            "hook",
            "args",
            "rows",
            "table_fingerprint",
        ],
    )?;
    ensure!(
        start["payload"]["generation"] == HANDLE_REQUEST
            && start["payload"]["target"] == "wp-action-throwable-1"
            && start["payload"]["hook"] == "rp-probe-action-throwable",
        "selected action Throwable identity mismatch"
    );
    let name = HookArgument::bytes("rp-probe-action-throwable");
    let rows = start["payload"]["rows"]
        .as_array()
        .context("action Throwable rows must be array")?;
    ensure!(rows.len() == 2, "action Throwable callback row count");
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
        "PHP action Throwable table fingerprint mismatch"
    );

    let mut dispatcher = Dispatcher::new();
    let mut callback_order = Vec::new();
    let mut handles_by_uid = std::collections::HashMap::new();
    for row in rows {
        let object = row
            .as_object()
            .context("action Throwable callback row must be object")?;
        ensure!(
            object.len() == 5
                && ["priority", "uid", "accepted_args", "function", "handle"]
                    .iter()
                    .all(|key| object.contains_key(*key)),
            "action Throwable callback row schema"
        );
        let priority = row["priority"]
            .as_i64()
            .context("action Throwable callback priority")?;
        let uid = row["uid"]
            .as_str()
            .context("action Throwable callback uid")?;
        let function = row["function"]
            .as_str()
            .context("action Throwable callback function")?;
        let expected = match function {
            "rp_action_throwing_callback" => (5, 2),
            "rp_action_never_callback" => (10, 2),
            other => anyhow::bail!("unexpected action Throwable fixture callback: {other}"),
        };
        ensure!(
            row["accepted_args"].as_i64() == Some(expected.1) && priority == expected.0,
            "action Throwable callback fixture contract mismatch"
        );
        callback_order.push((
            priority,
            rows.iter()
                .position(|candidate| candidate["uid"] == uid)
                .context("insertion order")?,
            uid.to_string(),
        ));
        let php_handle = handle(&row["handle"])?;
        ensure!(
            handles_by_uid.insert(uid.to_string(), php_handle).is_none(),
            "duplicate action Throwable callback uid"
        );
        let id = CallbackId {
            module: "wordpress".into(),
            callback: uid.to_string(),
        };
        let callback = ContextCallback::php_callable(id, expected.1, php_handle)
            .map_err(|error| anyhow::anyhow!("action Throwable callback import: {error:?}"))?;
        dispatcher.add_typed(&name, HookPriority::integer(priority), callback);
    }
    let mut sorted = callback_order.clone();
    sorted.sort_by_key(|(priority, order, _)| (*priority, *order));
    ensure!(
        callback_order == sorted,
        "action Throwable callback order mismatch"
    );

    let args = start["payload"]["args"]
        .as_array()
        .context("action Throwable args must be array")?
        .iter()
        .map(|value| php_string(value).map(PhpValue::Bytes))
        .collect::<Result<Vec<_>>>()?;
    ensure!(
        args == [
            PhpValue::Bytes(b"seed".to_vec()),
            PhpValue::Bytes(b"left".to_vec())
        ],
        "action Throwable fixture arguments mismatch"
    );
    let ticket = dispatcher
        .begin_action_after_counter_typed(&name, &args)
        .map_err(|error| anyhow::anyhow!("action Throwable dispatcher start: {error:?}"))?;
    let mut in_seq = 1;
    let mut out_seq = 0;
    let mut thrown: Option<PhpHandle> = None;

    loop {
        match dispatcher
            .advance(ticket)
            .map_err(|error| anyhow::anyhow!("action Throwable dispatcher advance: {error:?}"))?
        {
            DispatchStep::InvokePhp(invocation) => {
                let uid = invocation.callback.callback.as_str();
                ensure!(
                    uid == "rp_action_throwing_callback"
                        && invocation.priority == PhpKey::Integer(5)
                        && invocation.args.len() == 2
                        && invocation.args[0] == PhpValue::Bytes(b"seed".to_vec())
                        && invocation.args[1] == PhpValue::Bytes(b"left".to_vec())
                        && handles_by_uid.get(uid) == Some(&invocation.handle),
                    "unexpected action Throwable invocation"
                );
                let descriptor = json!({
                    "request":HANDLE_REQUEST,
                    "kind":"callable",
                    "slot":invocation.handle.slot(),
                    "generation":invocation.handle.generation(),
                    "token":format!("{:016x}", invocation.handle.token())
                });
                send_frame(
                    writer,
                    out_seq,
                    "invoke_php",
                    json!({
                        "target":"wp-action-throwable-1",
                        "uid":uid,
                        "handle":descriptor,
                        "priority":5,
                        "call_mode":"array",
                        "args":["seed","left"]
                    }),
                    total_out,
                    &mut out_seq,
                )?;
                let reply = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(
                    &reply,
                    in_seq,
                    "resume_throwable",
                    &["target", "uid", "throwable", "class", "message"],
                )?;
                ensure!(
                    reply["payload"]["target"] == "wp-action-throwable-1"
                        && reply["payload"]["uid"] == uid
                        && reply["payload"]["class"] == "RuntimeException"
                        && reply["payload"]["message"] == "action-throwable-fixture",
                    "PHP action Throwable completion metadata mismatch"
                );
                let descriptor = reply["payload"]["throwable"].clone();
                let throwable = handle_kind(&descriptor, "throwable", PhpHandleKind::Throwable)?;
                ensure!(
                    thrown.replace(throwable).is_none(),
                    "duplicate PHP action Throwable completion"
                );
                in_seq += 1;
                dispatcher
                    .resume_php_completion(ticket, Completion::PhpThrowable(throwable))
                    .map_err(|error| {
                        anyhow::anyhow!("action Throwable dispatcher resume: {error:?}")
                    })?;
            }
            DispatchStep::ReleasePhp(php_handle) => {
                ensure!(
                    handles_by_uid.values().any(|handle| *handle == php_handle),
                    "action Throwable dispatch released an unknown callback handle"
                );
                let descriptor = json!({
                    "request":HANDLE_REQUEST,
                    "kind":"callable",
                    "slot":php_handle.slot(),
                    "generation":php_handle.generation(),
                    "token":format!("{:016x}", php_handle.token())
                });
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
                    "action Throwable callback release ACK mismatch"
                );
                in_seq += 1;
                dispatcher
                    .resume_php_release(ticket, php_handle)
                    .map_err(|error| {
                        anyhow::anyhow!("action Throwable release resume: {error:?}")
                    })?;
            }
            DispatchStep::PhpThrowable(php_throwable) => {
                ensure!(
                    thrown == Some(php_throwable)
                        && dispatcher.did_action_typed(&name) == 1
                        && dispatcher.current_hook() == Some("rp-probe-action-throwable")
                        && dispatcher.hook_stack_depth() == 1
                        && dispatcher.doing_hook(Some("rp-probe-action-throwable"))
                        && dispatcher.current_priority_typed(&name) == Some(PhpKey::Integer(5)),
                    "Rust Dispatcher did not preserve the expected WordPress action-throw state"
                );

                // The request is terminal after this point. Remove the host callback
                // registrations and ACK their PHP leases before rethrowing the same object.
                dispatcher.remove_all_typed(&name, None);
                let mut released_registrations = 0;
                for callback_handle in dispatcher.take_php_releases() {
                    ensure!(
                        handles_by_uid
                            .values()
                            .any(|handle| *handle == callback_handle),
                        "action Throwable registration release used an unknown handle"
                    );
                    let descriptor = json!({
                        "request":HANDLE_REQUEST,
                        "kind":"callable",
                        "slot":callback_handle.slot(),
                        "generation":callback_handle.generation(),
                        "token":format!("{:016x}", callback_handle.token())
                    });
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
                        "action Throwable registration release ACK mismatch"
                    );
                    in_seq += 1;
                    released_registrations += 1;
                }
                ensure!(
                    released_registrations == 2,
                    "action Throwable callback registrations were not fully released"
                );

                let throwable = thrown.context("action Throwable handle missing")?;
                let throwable_descriptor = json!({
                    "request":HANDLE_REQUEST,
                    "kind":"throwable",
                    "slot":throwable.slot(),
                    "generation":throwable.generation(),
                    "token":format!("{:016x}", throwable.token())
                });
                send_frame(
                    writer,
                    out_seq,
                    "throw_php",
                    json!({"target":"wp-action-throwable-1","throwable":throwable_descriptor}),
                    total_out,
                    &mut out_seq,
                )?;
                let caught = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(
                    &caught,
                    in_seq,
                    "action_throwable_caught",
                    &["target", "observation", "throwable"],
                )?;
                ensure!(
                    caught["payload"]["target"] == "wp-action-throwable-1"
                        && caught["payload"]["throwable"] == throwable_descriptor,
                    "re-thrown PHP Throwable identity descriptor mismatch"
                );
                let expected_observation = json!({
                    "events":[
                        {
                            "callback":"all","hook":"rp-probe-action-throwable",
                            "args":["rp-probe-action-throwable","seed","left"],
                            "current_action":"rp-probe-action-throwable","doing_action":true,"did_action":1
                        },
                        {
                            "callback":"throwing","args":["seed","left"],
                            "current_action":"rp-probe-action-throwable","doing_action":true,"did_action":1,
                            "current_priority":5,"wp_hook_doing_action":true
                        }
                    ],
                    "same_throwable":true,
                    "throwable_class":"RuntimeException",
                    "throwable_message":"action-throwable-fixture",
                    "action_count":1,
                    "current_action":"rp-probe-action-throwable",
                    "current_filter":"rp-probe-action-throwable",
                    "doing_action":true,
                    "wp_current_filter":["rp-probe-action-throwable"],
                    "current_priority":5,
                    "wp_hook_state":{
                        "nesting_level":1,
                        "iterations":[[5,10]],
                        "current_priority":[5],
                        "doing_action":true
                    }
                });
                ensure!(
                    caught["payload"]["observation"] == expected_observation,
                    "PHP WordPress state after action Throwable differs"
                );
                in_seq += 1;

                send_frame(
                    writer,
                    out_seq,
                    "release_php",
                    json!({"handle":throwable_descriptor}),
                    total_out,
                    &mut out_seq,
                )?;
                let throwable_ack = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(&throwable_ack, in_seq, "release_ack", &["handle"])?;
                ensure!(
                    throwable_ack["payload"]["handle"] == throwable_descriptor,
                    "Throwable release ACK mismatch"
                );
                in_seq += 1;
                send_frame(
                    writer,
                    out_seq,
                    "request_complete",
                    json!({"target":"wp-action-throwable-1","outcome":"request_aborted_as_expected"}),
                    total_out,
                    &mut out_seq,
                )?;
                let done = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(
                    &done,
                    in_seq,
                    "guest_done",
                    &[
                        "observation",
                        "live_handles",
                        "live_handles_before_close",
                        "registry_closed",
                        "frames",
                    ],
                )?;
                ensure!(
                    done["payload"]["observation"] == expected_observation
                        && done["payload"]["live_handles"] == 0
                        && done["payload"]["live_handles_before_close"] == 0
                        && done["payload"]["registry_closed"] == true,
                    "PHP action Throwable request cleanup mismatch"
                );
                return Ok(json!({
                    "status":"passed",
                    "terminal":"php_throwable_rethrown_and_caught_at_request_boundary",
                    "throwable":{"class":"RuntimeException","message":"action-throwable-fixture"},
                    "callbacks_released":released_registrations,
                    "guest":done["payload"],
                    "bridge":{
                        "input_frames":in_seq + 1,
                        "output_frames":out_seq,
                        "input_bytes":*total_in,
                        "output_bytes":*total_out,
                        "timeout_seconds":TIMEOUT.as_secs()
                    }
                }));
            }
            DispatchStep::Boundary(_) => {
                anyhow::bail!("unexpected boundary in action Throwable fixture")
            }
            DispatchStep::Complete(_) => anyhow::bail!("action callback returned without throwing"),
        }
    }
}

fn run_action_ref_array_dispatch(
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
        "action_dispatch_start",
        &[
            "generation",
            "target",
            "hook",
            "argument_frame",
            "argument_count",
            "rows",
            "table_fingerprint",
        ],
    )?;
    ensure!(
        start["payload"]["generation"] == HANDLE_REQUEST
            && start["payload"]["hook"] == "rp-probe-action-ref-array"
            && start["payload"]["target"] == "wp-action-ref-array-1"
            && start["payload"]["argument_count"] == 2,
        "selected reference-array action identity mismatch"
    );
    let frame_handle = argument_handle(&start["payload"]["argument_frame"])?;
    let name = HookArgument::bytes("rp-probe-action-ref-array");
    let rows = start["payload"]["rows"]
        .as_array()
        .context("reference-array action rows must be array")?;
    ensure!(
        rows.len() == 2,
        "imported reference-array action callback row count"
    );
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
        "PHP reference-array action table fingerprint mismatch"
    );
    let mut dispatcher = Dispatcher::new();
    let mut callback_order = Vec::new();
    let mut handles_by_uid = std::collections::HashMap::new();
    let mut callback_labels = std::collections::HashMap::new();
    for row in rows {
        let object = row
            .as_object()
            .context("reference-array action row must be object")?;
        ensure!(
            object.len() == 5
                && ["priority", "uid", "accepted_args", "function", "handle"]
                    .iter()
                    .all(|key| object.contains_key(*key)),
            "reference-array action row schema"
        );
        let priority = row["priority"]
            .as_i64()
            .context("reference-array action priority")?;
        let uid = row["uid"].as_str().context("reference-array action uid")?;
        let insertion_order =
            rows.iter()
                .position(|candidate| candidate["uid"] == uid)
                .context("reference-array action insertion order")? as u64;
        let label = match row["function"].as_str() {
            Some("rp_action_ref_array_first") => "rp_action_ref_array_first",
            Some("rp_action_ref_array_second") => "rp_action_ref_array_second",
            Some(other) => anyhow::bail!("unrecognized reference-array action callback: {other}"),
            None => anyhow::bail!("reference-array action fixture does not accept closures"),
        };
        let accepted_args = row["accepted_args"]
            .as_i64()
            .context("reference-array action accepted_args")?;
        ensure!(
            accepted_args == 2,
            "reference-array action fixture requires exactly two accepted args"
        );
        callback_order.push((priority, insertion_order, uid.to_string()));
        let php_handle = handle(&row["handle"])?;
        ensure!(
            handles_by_uid.insert(uid.to_string(), php_handle).is_none(),
            "duplicate reference-array action callback uid"
        );
        callback_labels.insert(uid.to_string(), label.to_string());
        let id = CallbackId {
            module: "wordpress".into(),
            callback: uid.to_string(),
        };
        let callback =
            ContextCallback::php_callable(id, accepted_args, php_handle).map_err(|error| {
                anyhow::anyhow!("WP reference-array action callback import: {error:?}")
            })?;
        dispatcher.add_typed(&name, HookPriority::integer(priority), callback);
    }
    let mut sorted = callback_order.clone();
    sorted.sort_by_key(|(priority, insertion_order, _)| (*priority, *insertion_order));
    ensure!(
        callback_order == sorted,
        "PHP reference-array action callback order mismatch"
    );
    let args = [
        PhpValue::ArgumentHandle(frame_handle),
        PhpValue::ArgumentHandle(frame_handle),
    ];
    let ticket = dispatcher
        .begin_action_after_counter_typed(&name, &args)
        .map_err(|error| {
            anyhow::anyhow!("WP reference-array action dispatcher start: {error:?}")
        })?;
    let mut in_seq = 1;
    let mut out_seq = 0;
    let mut imported = Vec::new();
    loop {
        match dispatcher.advance(ticket).map_err(|error| {
            anyhow::anyhow!("WP reference-array action dispatcher advance: {error:?}")
        })? {
            DispatchStep::InvokePhp(invocation) => {
                ensure!(
                    invocation.call_mode == PhpCallMode::Array && invocation.args.len() == 2,
                    "reference-array action fixture requires two callback arguments"
                );
                let uid = invocation.callback.callback.as_str();
                ensure!(
                    callback_labels.contains_key(uid),
                    "reference-array action invocation callback uid missing"
                );
                ensure!(
                    handles_by_uid.get(uid) == Some(&invocation.handle),
                    "reference-array action handle does not match imported PHP uid"
                );
                let priority = match invocation.priority {
                    PhpKey::Integer(value) => value,
                    _ => anyhow::bail!("reference-array action priority must be integer"),
                };
                let frame_descriptor = json!({
                    "request":HANDLE_REQUEST,
                    "kind":"argument",
                    "slot":frame_handle.slot(),
                    "generation":frame_handle.generation(),
                    "token":format!("{:016x}", frame_handle.token())
                });
                let php_args = invocation
                    .args
                    .iter()
                    .enumerate()
                    .map(|(index, value)| {
                        ensure!(
                            *value == PhpValue::ArgumentHandle(frame_handle),
                            "reference-array action callback argument lost its frame handle"
                        );
                        Ok(json!({
                            "type":"argument_frame_slot",
                            "handle":frame_descriptor,
                            "index":index
                        }))
                    })
                    .collect::<Result<Vec<_>>>()?;
                let payload = json!({
                    "target":"wp-action-ref-array-1",
                    "uid":uid,
                    "handle":{
                        "request":HANDLE_REQUEST,
                        "kind":"callable",
                        "slot":invocation.handle.slot(),
                        "generation":invocation.handle.generation(),
                        "token":format!("{:016x}", invocation.handle.token())
                    },
                    "priority":priority,
                    "call_mode":"array",
                    "args":php_args,
                    "argument_frame":frame_descriptor
                });
                send_frame(
                    writer,
                    out_seq,
                    "invoke_php",
                    payload,
                    total_out,
                    &mut out_seq,
                )?;
                let reply = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(&reply, in_seq, "resume_value", &["target", "uid", "value"])?;
                ensure!(
                    reply["payload"]["target"] == "wp-action-ref-array-1"
                        && reply["payload"]["uid"] == uid
                        && reply["payload"]["value"].is_null(),
                    "reference-array action callback must resume with ignored null result"
                );
                in_seq += 1;
                dispatcher
                    .resume_php(ticket, PhpValue::Null)
                    .map_err(|error| {
                        anyhow::anyhow!("WP reference-array action dispatcher resume: {error:?}")
                    })?;
                imported.push(callback_labels[uid].clone());
            }
            DispatchStep::ReleasePhp(php_handle) => {
                let descriptor = json!({
                    "request":HANDLE_REQUEST,
                    "kind":"callable",
                    "slot":php_handle.slot(),
                    "generation":php_handle.generation(),
                    "token":format!("{:016x}", php_handle.token())
                });
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
                    "reference-array action callback release ACK mismatch"
                );
                in_seq += 1;
                dispatcher
                    .resume_php_release(ticket, php_handle)
                    .map_err(|error| {
                        anyhow::anyhow!("WP reference-array action release resume: {error:?}")
                    })?;
            }
            DispatchStep::Boundary(_) => {
                anyhow::bail!("unexpected PHP boundary in reference-array action")
            }
            DispatchStep::PhpThrowable(_) => {
                anyhow::bail!("unexpected PHP Throwable in reference-array action")
            }
            DispatchStep::Complete(result) => {
                ensure!(
                    result == PhpValue::Null,
                    "reference-array action returned non-null value"
                );
                ensure!(
                    imported == ["rp_action_ref_array_first", "rp_action_ref_array_second"],
                    "reference-array action callback order mismatch"
                );
                send_frame(
                    writer,
                    out_seq,
                    "action_complete",
                    json!({"target":"wp-action-ref-array-1","invocations":imported.len()}),
                    total_out,
                    &mut out_seq,
                )?;
                let php_done = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(
                    &php_done,
                    in_seq,
                    "request_complete",
                    &["target", "result", "did_action"],
                )?;
                ensure!(
                    php_done["payload"]["target"] == "wp-action-ref-array-1"
                        && php_done["payload"]["result"].is_null()
                        && php_done["payload"]["did_action"] == 1,
                    "WordPress reference-array action entrypoint completion differs"
                );
                in_seq += 1;
                dispatcher.remove_all_typed(&name, None);
                for php_handle in dispatcher.take_php_releases() {
                    let descriptor = json!({
                        "request":HANDLE_REQUEST,
                        "kind":"callable",
                        "slot":php_handle.slot(),
                        "generation":php_handle.generation(),
                        "token":format!("{:016x}", php_handle.token())
                    });
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
                        "reference-array action registration release ACK mismatch"
                    );
                    in_seq += 1;
                }
                let frame_descriptor = json!({
                    "request":HANDLE_REQUEST,
                    "kind":"argument",
                    "slot":frame_handle.slot(),
                    "generation":frame_handle.generation(),
                    "token":format!("{:016x}", frame_handle.token())
                });
                send_frame(
                    writer,
                    out_seq,
                    "release_php",
                    json!({"handle":frame_descriptor}),
                    total_out,
                    &mut out_seq,
                )?;
                let frame_ack = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(&frame_ack, in_seq, "release_ack", &["handle"])?;
                ensure!(
                    frame_ack["payload"]["handle"] == frame_descriptor,
                    "reference-array action frame release ACK mismatch"
                );
                in_seq += 1;
                send_frame(
                    writer,
                    out_seq,
                    "request_complete",
                    json!({"target":"wp-action-ref-array-1","result":null}),
                    total_out,
                    &mut out_seq,
                )?;
                let done = read_frame(reader, deadline, total_in, in_seq)?;
                expect_frame(
                    &done,
                    in_seq,
                    "guest_done",
                    &[
                        "events",
                        "result",
                        "caller_value",
                        "caller_extra",
                        "action_count",
                        "current_action",
                        "current_filter",
                        "doing_action",
                        "wp_hook_doing_action",
                        "current_priority",
                        "table_fingerprint",
                        "live_handles",
                        "live_handles_before_close",
                        "argument_frame_released_before_close",
                        "invocations",
                        "frames",
                    ],
                )?;
                let expected_events = json!([
                    {
                        "callback":"all", "hook":"rp-probe-action-ref-array",
                        "args":["rp-probe-action-ref-array", ["seed", "left"]],
                        "current_action":"rp-probe-action-ref-array", "doing_action":true, "did_action":1
                    },
                    {
                        "callback":"first", "args":["seed", "left"],
                        "current_action":"rp-probe-action-ref-array", "doing_action":true, "did_action":1,
                        "current_priority":5, "wp_hook_doing_action":true
                    },
                    {
                        "callback":"second", "args":["value-mutated-by-first", "extra-mutated-by-first"],
                        "current_action":"rp-probe-action-ref-array", "doing_action":true, "did_action":1,
                        "current_priority":10, "wp_hook_doing_action":true
                    }
                ]);
                ensure!(
                    done["payload"]["result"].is_null()
                        && done["payload"]["events"] == expected_events
                        && done["payload"]["caller_value"] == "value-mutated-by-first"
                        && done["payload"]["caller_extra"] == "extra-mutated-by-second"
                        && done["payload"]["action_count"] == 1
                        && done["payload"]["current_action"] == false
                        && done["payload"]["current_filter"] == false
                        && done["payload"]["doing_action"] == false
                        && done["payload"]["wp_hook_doing_action"] == false
                        && done["payload"]["current_priority"] == false
                        && done["payload"]["table_fingerprint"] == fingerprint
                        && done["payload"]["live_handles"] == 0
                        && done["payload"]["live_handles_before_close"] == 0
                        && done["payload"]["argument_frame_released_before_close"] == true
                        && done["payload"]["invocations"]
                            == json!([rows[0]["uid"], rows[1]["uid"]]),
                    "WordPress reference-array action final observation mismatch"
                );
                return Ok(json!({
                    "status":"passed",
                    "result":null,
                    "callbacks":imported,
                    "guest":done["payload"],
                    "bridge":{
                        "input_frames":in_seq + 1,
                        "output_frames":out_seq,
                        "input_bytes":*total_in,
                        "output_bytes":*total_out,
                        "timeout_seconds":TIMEOUT.as_secs()
                    }
                }));
            }
        }
    }
}

fn main() -> Result<()> {
    let action_mode = std::env::args().nth(1).as_deref() == Some("action");
    let ref_array_mode = std::env::args().nth(1).as_deref() == Some("ref-array");
    let action_ref_array_mode = std::env::args().nth(1).as_deref() == Some("action-ref-array");
    let action_throwable_mode = std::env::args().nth(1).as_deref() == Some("action-throwable");
    ensure!(
        action_mode
            || ref_array_mode
            || action_ref_array_mode
            || action_throwable_mode
            || std::env::args().nth(1).is_none(),
        "unknown hook-dispatch scenario"
    );
    let php_driver = if action_throwable_mode {
        PHP_ACTION_THROWABLE_DRIVER
    } else if action_ref_array_mode {
        PHP_ACTION_REF_ARRAY_DRIVER
    } else if action_mode {
        PHP_ACTION_DRIVER
    } else {
        PHP_DRIVER
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
    let guest_thread = thread::Builder::new()
        .name("php-wasix-wp-hook-dispatch".into())
        .spawn(move || {
            let result = run_guest(
                &module_copy,
                module_hash,
                guest_stdin,
                guest_stdout,
                guest_stderr,
                php_driver,
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
    let observed = if action_throwable_mode {
        run_action_throwable_dispatch(
            &mut reader,
            &mut DeadlinePipeWriter {
                pipe: &mut host_stdin,
                deadline: Instant::now() + TIMEOUT,
            },
            &mut total_in,
            &mut total_out,
        )
    } else if action_mode {
        run_action_dispatch(
            &mut reader,
            &mut DeadlinePipeWriter {
                pipe: &mut host_stdin,
                deadline: Instant::now() + TIMEOUT,
            },
            &mut total_in,
            &mut total_out,
        )
    } else if action_ref_array_mode {
        run_action_ref_array_dispatch(
            &mut reader,
            &mut DeadlinePipeWriter {
                pipe: &mut host_stdin,
                deadline: Instant::now() + TIMEOUT,
            },
            &mut total_in,
            &mut total_out,
        )
    } else {
        run_dispatch(
            &mut reader,
            &mut DeadlinePipeWriter {
                pipe: &mut host_stdin,
                deadline: Instant::now() + TIMEOUT,
            },
            &mut total_in,
            &mut total_out,
            ref_array_mode,
        )
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
        json!({"status":"passed","scenario":if action_throwable_mode {"action-throwable"} else if action_ref_array_mode {"action-ref-array"} else if action_mode {"action"} else if ref_array_mode {"ref-array"} else {"filter"},"php_wasm_sha256":PHP_SHA256,"php_wasm_bytes":PHP_BYTES,"overlay_files":FILES.iter().map(|(n,b)|json!({"path":n,"sha256":sha256(b)})).collect::<Vec<_>>(),"observation":observed})
    );
    Ok(())
}

fn run_guest(
    module: &Module,
    module_hash: wasmer_types::ModuleHash,
    stdin: Pipe,
    stdout: Pipe,
    stderr: Pipe,
    driver: &'static str,
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
    let runner_args = if driver == PHP_ACTION_THROWABLE_DRIVER {
        vec![
            "-n".to_string(),
            driver.to_string(),
            "action-throwable".to_string(),
        ]
    } else if driver == PHP_ACTION_REF_ARRAY_DRIVER {
        vec![
            "-n".to_string(),
            driver.to_string(),
            "action-ref-array".to_string(),
        ]
    } else if driver == PHP_ACTION_DRIVER {
        vec!["-n".to_string(), driver.to_string(), "action".to_string()]
    } else if driver == PHP_DRIVER && std::env::args().nth(1).as_deref() == Some("ref-array") {
        vec![
            "-n".to_string(),
            driver.to_string(),
            "ref-array".to_string(),
        ]
    } else {
        vec!["-n".to_string(), driver.to_string()]
    };
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
