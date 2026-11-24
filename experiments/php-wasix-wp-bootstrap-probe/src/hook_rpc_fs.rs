//! Request-local, one-filter PHP/Rust dispatch channel for the pinned
//! WordPress `option_blogname` bootstrap fixture.
//!
//! The virtual file is deliberately independent from the WPDB channel. Each
//! complete JSON line advances exactly one Dispatcher event and returns one
//! JSON-line reply. This module is a bounded probe adapter, not a generic PHP
//! RPC or WordPress hook transport.

use recastpress_wp::{
    continuation::{PhpHandle, PhpHandleKind, RequestGeneration},
    dispatcher::{DispatchStep, DispatchTicket, Dispatcher},
    hooks::{CallbackId, ContextCallback, HookArgument, HookPriority, PhpCallMode},
    value::PhpValue,
};
use serde_json::{Map, Value, json};
use std::{
    collections::VecDeque,
    future::Future,
    io::{self, SeekFrom},
    path::{Component, Path, PathBuf},
    pin::Pin,
    sync::{Arc, Mutex, MutexGuard},
    task::{Context, Poll},
};
use wasmer_wasix::virtual_fs::{
    FileOpener, FileSystem, FileType, FsError, Metadata, OpenOptions, OpenOptionsConfig, ReadDir,
    VirtualFile,
};

const CHANNEL_NAME: &str = "channel";
const REQUEST_SCHEMA: &str = "recastpress.wp-hooks-request.v0.1";
const REPLY_SCHEMA: &str = "recastpress.wp-hooks-reply.v0.1";
const REQUEST_GENERATION: u64 = 1;
const HOOK_NAME: &str = "option_blogname";
const TARGET: &str = "rp-option-blogname-1";
const INPUT_VALUE: &str = "RP Bootstrap Fixture";
const CALLBACKS: [(&str, i64); 2] = [("rp_boot_hook_first", 10), ("rp_boot_hook_second", 20)];
const MAX_FRAME: usize = 64 * 1024;
const MAX_TOTAL: usize = 256 * 1024;
const MAX_OPERATIONS: usize = 12;

#[derive(Clone, Copy, Debug)]
struct PendingRelease {
    handle: PhpHandle,
    resume_dispatch: bool,
}

struct State {
    nonce: String,
    pending: Vec<u8>,
    response: Vec<u8>,
    response_bytes: usize,
    response_read_bytes: usize,
    request_bytes: usize,
    consumed: usize,
    operations: usize,
    last_sequence: u64,
    open: bool,
    opened_once: bool,
    finished: bool,
    close_dispatched: bool,
    failure: Option<&'static str>,
    dispatcher: Option<Dispatcher>,
    ticket: Option<DispatchTicket>,
    hook_target: Option<recastpress_wp::dispatcher::HookTarget>,
    callback_handles: Vec<PhpHandle>,
    live_handles: Vec<PhpHandle>,
    pending_invoke: Option<(String, PhpHandle)>,
    pending_release: Option<PendingRelease>,
    cleanup_releases: VecDeque<PhpHandle>,
    invoked: Vec<String>,
    dispatcher_complete: bool,
    dispatcher_cancel_attempted: bool,
    dispatcher_cancelled: bool,
    complete_sent: bool,
    target_seen: bool,
    current_value: Vec<u8>,
}

impl std::fmt::Debug for State {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("PrivateWpHooksState")
    }
}

impl State {
    fn fail(&mut self, reason: &'static str) -> io::Error {
        self.failure.get_or_insert(reason);
        io::Error::new(io::ErrorKind::InvalidData, reason)
    }

    fn active(&mut self) -> io::Result<()> {
        if let Some(reason) = self.failure {
            return Err(io::Error::other(reason));
        }
        if self.finished || !self.open {
            return Err(self.fail("wp_hooks_channel_not_active"));
        }
        Ok(())
    }

    fn receive(&mut self, bytes: &[u8]) -> io::Result<()> {
        self.active()?;
        if bytes.is_empty() {
            return Ok(());
        }
        if self.close_dispatched || self.consumed != self.response.len() {
            return Err(self.fail("wp_hooks_request_before_reply_consumed"));
        }
        if bytes.len() > MAX_TOTAL.saturating_sub(self.request_bytes)
            || bytes.len() > MAX_FRAME.saturating_sub(self.pending.len())
        {
            return Err(self.fail("wp_hooks_request_quota"));
        }
        self.request_bytes += bytes.len();
        self.pending.extend_from_slice(bytes);
        if let Some(index) = self.pending.iter().position(|&b| b == b'\n') {
            if index + 1 != self.pending.len() {
                return Err(self.fail("wp_hooks_multiple_frames"));
            }
            let wire = std::mem::take(&mut self.pending);
            let request: Value = serde_json::from_slice(&wire[..index])
                .map_err(|_| self.fail("wp_hooks_invalid_json"))?;
            let reply = match self.handle_request(request) {
                Ok(reply) => reply,
                Err(reason) => return Err(self.fail(reason)),
            };
            let mut response =
                serde_json::to_vec(&reply).map_err(|_| self.fail("wp_hooks_reply_json"))?;
            response.push(b'\n');
            if response.len() > MAX_FRAME
                || response.len() > MAX_TOTAL.saturating_sub(self.response_bytes)
            {
                return Err(self.fail("wp_hooks_reply_quota"));
            }
            self.response_bytes += response.len();
            self.response = response;
            self.consumed = 0;
            self.operations += 1;
        }
        Ok(())
    }

    fn handle_request(&mut self, request: Value) -> Result<Value, &'static str> {
        let object = request.as_object().ok_or("wp_hooks_request_object")?;
        exact_keys(object, &["schema", "nonce", "seq", "op", "payload"])
            .map_err(|_| "wp_hooks_request_keys")?;
        if object["schema"] != REQUEST_SCHEMA {
            return Err("wp_hooks_request_schema");
        }
        if object["nonce"].as_str() != Some(self.nonce.as_str()) {
            return Err("wp_hooks_request_nonce");
        }
        let sequence = object["seq"].as_u64().ok_or("wp_hooks_request_sequence")?;
        if sequence != self.last_sequence + 1 || sequence == 0 {
            return Err("wp_hooks_request_sequence_order");
        }
        if self.operations >= MAX_OPERATIONS {
            return Err("wp_hooks_operation_limit");
        }
        let operation = object["op"].as_str().ok_or("wp_hooks_request_operation")?;
        let payload = object["payload"].clone();
        let (event, event_payload) = match operation {
            "begin" => self.begin(payload)?,
            "resume" => self.resume(payload)?,
            "release_ack" => self.release_ack(payload)?,
            "close" => self.close_request(payload)?,
            _ => return Err("wp_hooks_unknown_operation"),
        };
        self.last_sequence = sequence;
        Ok(json!({
            "schema": REPLY_SCHEMA,
            "nonce": self.nonce,
            "seq": sequence,
            "event": event,
            "payload": event_payload
        }))
    }

    fn begin(&mut self, payload: Value) -> Result<(&'static str, Value), &'static str> {
        if self.dispatcher.is_some() || self.target_seen || self.close_dispatched {
            return Err("wp_hooks_begin_repeated");
        }
        let object = payload.as_object().ok_or("wp_hooks_begin_payload")?;
        exact_keys(
            object,
            &["hook", "target", "value", "extra", "rows", "fingerprint"],
        )
        .map_err(|_| "wp_hooks_begin_keys")?;
        if object["hook"] != HOOK_NAME || object["target"] != TARGET {
            return Err("wp_hooks_target_identity");
        }
        if object["value"] != INPUT_VALUE {
            return Err("wp_hooks_filter_input");
        }
        let extra_args = object["extra"].as_array().ok_or("wp_hooks_filter_extra")?;
        if extra_args.len() != 1 || extra_args[0].as_str() != Some("blogname") {
            return Err("wp_hooks_filter_extra_contract");
        }
        let extra: Vec<PhpValue> = extra_args
            .iter()
            .map(|argument| {
                argument
                    .as_str()
                    .map(|value| PhpValue::Bytes(value.as_bytes().to_vec()))
                    .ok_or("wp_hooks_filter_extra_type")
            })
            .collect::<Result<_, _>>()?;
        let rows = object["rows"].as_array().ok_or("wp_hooks_rows")?;
        if rows.len() != CALLBACKS.len() {
            return Err("wp_hooks_callback_count");
        }
        let mut parsed = Vec::with_capacity(rows.len());
        let mut handles = Vec::with_capacity(rows.len());
        for (index, row) in rows.iter().enumerate() {
            let row = row.as_object().ok_or("wp_hooks_row_object")?;
            exact_keys(
                row,
                &["priority", "uid", "accepted_args", "function", "handle"],
            )
            .map_err(|_| "wp_hooks_row_keys")?;
            let (expected_uid, expected_priority) = CALLBACKS[index];
            if row["uid"].as_str() != Some(expected_uid)
                || row["function"].as_str() != Some(expected_uid)
                || row["priority"].as_i64() != Some(expected_priority)
                || row["accepted_args"].as_i64() != Some(1)
            {
                return Err("wp_hooks_callback_contract");
            }
            let handle = parse_handle(&row["handle"])?;
            if handles.contains(&handle)
                || handles
                    .iter()
                    .any(|known: &PhpHandle| known.slot() == handle.slot())
            {
                return Err("wp_hooks_duplicate_handle");
            }
            handles.push(handle);
            parsed.push((expected_uid, expected_priority, handle));
        }
        let fingerprint_rows: Vec<Value> = rows
            .iter()
            .map(|row| {
                let handle = &row["handle"];
                json!([
                    row["priority"],
                    row["uid"],
                    row["accepted_args"],
                    row["function"],
                    handle["request"],
                    handle["kind"],
                    handle["slot"],
                    handle["generation"],
                    handle["token"]
                ])
            })
            .collect();
        let fingerprint_wire =
            serde_json::to_vec(&fingerprint_rows).map_err(|_| "wp_hooks_fingerprint_json")?;
        if object["fingerprint"].as_str() != Some(crate::db::sha(&fingerprint_wire).as_str()) {
            return Err("wp_hooks_callback_fingerprint");
        }

        let name = HookArgument::bytes(HOOK_NAME);
        let mut dispatcher = Dispatcher::new();
        let target = dispatcher
            .capture_target(&HookArgument::bytes(HOOK_NAME), true)
            .map_err(|_| "wp_hooks_target_capture")?
            .ok_or("wp_hooks_target_missing")?;
        for (uid, priority, handle) in &parsed {
            let id = CallbackId {
                module: "wordpress".to_owned(),
                callback: (*uid).to_owned(),
            };
            let callback = ContextCallback::<Dispatcher>::php_callable(id, 1, *handle)
                .map_err(|_| "wp_hooks_callback_import")?;
            dispatcher
                .add_target(target, HookPriority::integer(*priority), callback)
                .map_err(|_| "wp_hooks_target_callback_import")?;
        }
        // WordPress get_option() calls option_blogname with ($value, $option).
        // The exact option argument came from the adapter frame above;
        // accepted_args=1 means both callbacks receive only the filter value.
        let ticket = dispatcher
            .begin_filter_on_target_typed(
                &name,
                target,
                PhpValue::Bytes(INPUT_VALUE.as_bytes().to_vec()),
                &extra,
            )
            .map_err(|_| "wp_hooks_dispatch_start")?;
        self.callback_handles = handles.clone();
        self.live_handles = handles;
        self.dispatcher = Some(dispatcher);
        self.ticket = Some(ticket);
        self.hook_target = Some(target);
        self.current_value = INPUT_VALUE.as_bytes().to_vec();
        self.target_seen = true;
        self.dispatch_step()
    }

    fn resume(&mut self, payload: Value) -> Result<(&'static str, Value), &'static str> {
        let object = payload.as_object().ok_or("wp_hooks_resume_payload")?;
        exact_keys(object, &["target", "uid", "value"]).map_err(|_| "wp_hooks_resume_keys")?;
        if object["target"] != TARGET {
            return Err("wp_hooks_resume_target");
        }
        let (uid, handle) = self
            .pending_invoke
            .take()
            .ok_or("wp_hooks_resume_without_invoke")?;
        let returned = object["value"].as_str().ok_or("wp_hooks_resume_value")?;
        let expected_handle = CALLBACKS
            .iter()
            .position(|(candidate, _)| *candidate == uid)
            .and_then(|index| self.callback_handles.get(index))
            .copied();
        if returned.len() > MAX_FRAME / 4
            || object["uid"].as_str() != Some(uid.as_str())
            || expected_handle != Some(handle)
        {
            self.pending_invoke = Some((uid, handle));
            return Err("wp_hooks_resume_callback_identity");
        }
        self.invoked.push(uid);
        self.current_value = returned.as_bytes().to_vec();
        let dispatcher = self
            .dispatcher
            .as_mut()
            .ok_or("wp_hooks_dispatcher_missing")?;
        dispatcher
            .resume_php(
                self.ticket.ok_or("wp_hooks_ticket_missing")?,
                PhpValue::Bytes(returned.as_bytes().to_vec()),
            )
            .map_err(|_| "wp_hooks_resume_failed")?;
        self.dispatch_step()
    }

    fn release_ack(&mut self, payload: Value) -> Result<(&'static str, Value), &'static str> {
        let object = payload.as_object().ok_or("wp_hooks_release_ack_payload")?;
        exact_keys(object, &["handle"]).map_err(|_| "wp_hooks_release_ack_keys")?;
        let pending = self
            .pending_release
            .take()
            .ok_or("wp_hooks_release_ack_without_release")?;
        let ack = parse_handle(&object["handle"])?;
        if ack != pending.handle {
            self.pending_release = Some(pending);
            return Err("wp_hooks_release_ack_identity");
        }
        let index = self
            .live_handles
            .iter()
            .position(|handle| *handle == ack)
            .ok_or("wp_hooks_release_not_live")?;
        self.live_handles.remove(index);
        if pending.resume_dispatch {
            self.dispatcher
                .as_mut()
                .ok_or("wp_hooks_dispatcher_missing")?
                .resume_php_release(self.ticket.ok_or("wp_hooks_ticket_missing")?, ack)
                .map_err(|_| "wp_hooks_release_resume_failed")?;
            return self.dispatch_step();
        }
        if let Some(next) = self.cleanup_releases.pop_front() {
            return self.release_event(next, false);
        }
        if self.dispatcher_complete {
            return self.complete_event();
        }
        Err("wp_hooks_release_state")
    }

    fn close_request(&mut self, payload: Value) -> Result<(&'static str, Value), &'static str> {
        let object = payload.as_object().ok_or("wp_hooks_close_payload")?;
        if !object.is_empty() || !self.complete_sent || !self.dispatcher_complete {
            return Err("wp_hooks_close_before_complete");
        }
        if self.pending_invoke.is_some()
            || self.pending_release.is_some()
            || !self.cleanup_releases.is_empty()
            || !self.live_handles.is_empty()
        {
            return Err("wp_hooks_close_with_live_dispatch_state");
        }
        self.close_dispatched = true;
        Ok(("close", json!({"closed":true})))
    }

    fn dispatch_step(&mut self) -> Result<(&'static str, Value), &'static str> {
        let step = self
            .dispatcher
            .as_mut()
            .ok_or("wp_hooks_dispatcher_missing")?
            .advance(self.ticket.ok_or("wp_hooks_ticket_missing")?)
            .map_err(|_| "wp_hooks_dispatch_advance")?;
        match step {
            DispatchStep::InvokePhp(invocation) => {
                if invocation.hook
                    != recastpress_wp::hooks::HookScalar::Bytes(HOOK_NAME.as_bytes().to_vec())
                {
                    return Err("wp_hooks_invocation_hook");
                }
                let uid = invocation.callback.callback;
                let Some((expected_uid, expected_priority)) =
                    CALLBACKS.iter().find(|(candidate, _)| *candidate == uid)
                else {
                    return Err("wp_hooks_invocation_callback");
                };
                let priority = match invocation.priority {
                    recastpress_wp::value::PhpKey::Integer(priority) => priority,
                    _ => return Err("wp_hooks_invocation_priority"),
                };
                if priority != *expected_priority
                    || invocation.handle.request().get() != REQUEST_GENERATION
                    || invocation.call_mode != PhpCallMode::Array
                    || invocation.args.len() != 1
                    || invocation.args[0] != PhpValue::Bytes(self.current_value.clone())
                    || self.pending_invoke.is_some()
                    || self.pending_release.is_some()
                {
                    return Err("wp_hooks_invocation_contract");
                }
                let args = invocation
                    .args
                    .iter()
                    .map(php_value_json)
                    .collect::<Result<Vec<_>, _>>()?;
                self.pending_invoke = Some((uid.clone(), invocation.handle));
                Ok((
                    "invoke",
                    json!({
                        "target": TARGET,
                        "uid": expected_uid,
                        "handle": handle_json(invocation.handle),
                        "priority": priority,
                        "call_mode": "array",
                        "args": args,
                        "value": String::from_utf8(self.current_value.clone()).map_err(|_| "wp_hooks_filter_value_utf8")?
                    }),
                ))
            }
            DispatchStep::ReleasePhp(handle) => self.release_event(handle, true),
            DispatchStep::Complete(result) => {
                if result != PhpValue::Bytes(self.current_value.clone())
                    || self.invoked
                        != CALLBACKS
                            .iter()
                            .map(|(uid, _)| (*uid).to_owned())
                            .collect::<Vec<_>>()
                    || self.pending_invoke.is_some()
                    || self.pending_release.is_some()
                {
                    return Err("wp_hooks_dispatch_completion_contract");
                }
                self.dispatcher
                    .as_mut()
                    .ok_or("wp_hooks_dispatcher_missing")?
                    .remove_all_typed(&HookArgument::bytes(HOOK_NAME), None);
                self.dispatcher
                    .as_mut()
                    .ok_or("wp_hooks_dispatcher_missing")?
                    .release_target(self.hook_target.ok_or("wp_hooks_target_missing")?)
                    .map_err(|_| "wp_hooks_target_release")?;
                self.cleanup_releases = self
                    .dispatcher
                    .as_mut()
                    .ok_or("wp_hooks_dispatcher_missing")?
                    .take_php_releases()
                    .into();
                self.dispatcher_complete = true;
                if let Some(handle) = self.cleanup_releases.pop_front() {
                    self.release_event(handle, false)
                } else {
                    self.complete_event()
                }
            }
            DispatchStep::Boundary(_) => Err("wp_hooks_unexpected_dispatch_boundary"),
            DispatchStep::PhpThrowable(_) => Err("wp_hooks_unexpected_php_throwable"),
        }
    }

    fn release_event(
        &mut self,
        handle: PhpHandle,
        resume_dispatch: bool,
    ) -> Result<(&'static str, Value), &'static str> {
        if !self.live_handles.contains(&handle)
            || self.pending_invoke.is_some()
            || self.pending_release.is_some()
        {
            return Err("wp_hooks_release_contract");
        }
        self.pending_release = Some(PendingRelease {
            handle,
            resume_dispatch,
        });
        Ok(("release", json!({"handle":handle_json(handle)})))
    }

    fn complete_event(&mut self) -> Result<(&'static str, Value), &'static str> {
        if !self.dispatcher_complete
            || !self.live_handles.is_empty()
            || self.pending_invoke.is_some()
            || self.pending_release.is_some()
            || !self.cleanup_releases.is_empty()
            || self.invoked
                != CALLBACKS
                    .iter()
                    .map(|(uid, _)| (*uid).to_owned())
                    .collect::<Vec<_>>()
        {
            return Err("wp_hooks_not_clean_at_complete");
        }
        self.complete_sent = true;
        Ok((
            "complete",
            json!({
                "target": TARGET,
                "value": String::from_utf8(self.current_value.clone()).map_err(|_| "wp_hooks_filter_value_utf8")?,
                "invocations": self.invoked.len()
            }),
        ))
    }
}

fn exact_keys(object: &Map<String, Value>, keys: &[&str]) -> Result<(), ()> {
    if object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key)) {
        Ok(())
    } else {
        Err(())
    }
}

fn parse_handle(value: &Value) -> Result<PhpHandle, &'static str> {
    let object = value.as_object().ok_or("wp_hooks_handle_object")?;
    exact_keys(object, &["request", "kind", "slot", "generation", "token"])
        .map_err(|_| "wp_hooks_handle_keys")?;
    if object["request"].as_str() != Some("1") || object["kind"] != "callable" {
        return Err("wp_hooks_handle_request_or_kind");
    }
    let slot = object["slot"].as_u64().ok_or("wp_hooks_handle_slot")?;
    let generation = object["generation"]
        .as_u64()
        .ok_or("wp_hooks_handle_generation")?;
    if slot > u32::MAX as u64 || generation > u32::MAX as u64 {
        return Err("wp_hooks_handle_integer_range");
    }
    let token = object["token"].as_str().ok_or("wp_hooks_handle_token")?;
    if token.len() != 16
        || !token
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("wp_hooks_handle_token_shape");
    }
    let token = u64::from_str_radix(token, 16).map_err(|_| "wp_hooks_handle_token")?;
    Ok(PhpHandle::new(
        RequestGeneration::new(REQUEST_GENERATION),
        PhpHandleKind::Callable,
        slot as u32,
        generation as u32,
        token,
    ))
}

fn handle_json(handle: PhpHandle) -> Value {
    json!({
        "request": "1",
        "kind": "callable",
        "slot": handle.slot(),
        "generation": handle.generation(),
        "token": format!("{:016x}", handle.token())
    })
}

fn php_value_json(value: &PhpValue) -> Result<Value, &'static str> {
    match value {
        PhpValue::Bytes(bytes) => Ok(json!(
            String::from_utf8(bytes.clone()).map_err(|_| "wp_hooks_php_bytes_utf8")?
        )),
        _ => Err("wp_hooks_php_argument_type"),
    }
}

#[derive(Clone, Debug)]
pub struct HookRpcFs(Arc<Mutex<State>>);

impl HookRpcFs {
    /// Create a channel for one bootstrap request. The supplied nonce is never
    /// included in snapshots, and is echoed only on request/reply frames.
    pub fn new(nonce: String) -> Self {
        Self(Arc::new(Mutex::new(State {
            nonce,
            pending: Vec::new(),
            response: Vec::new(),
            response_bytes: 0,
            response_read_bytes: 0,
            request_bytes: 0,
            consumed: 0,
            operations: 0,
            last_sequence: 0,
            open: false,
            opened_once: false,
            finished: false,
            close_dispatched: false,
            failure: None,
            dispatcher: None,
            ticket: None,
            hook_target: None,
            callback_handles: Vec::new(),
            live_handles: Vec::new(),
            pending_invoke: None,
            pending_release: None,
            cleanup_releases: VecDeque::new(),
            invoked: Vec::new(),
            dispatcher_complete: false,
            dispatcher_cancel_attempted: false,
            dispatcher_cancelled: false,
            complete_sent: false,
            target_seen: false,
            current_value: Vec::new(),
        })))
    }

    fn lock(&self) -> io::Result<MutexGuard<'_, State>> {
        self.0
            .lock()
            .map_err(|_| io::Error::other("wp_hooks_state_poisoned"))
    }

    fn deny(&self, reason: &'static str) -> FsError {
        if let Ok(mut state) = self.lock() {
            state.fail(reason);
        }
        FsError::PermissionDenied
    }

    pub fn snapshot(&self) -> Value {
        match self.lock() {
            Ok(state) => json!({
                "hook": if state.target_seen { Some(HOOK_NAME) } else { None },
                "target": if state.target_seen { Some(TARGET) } else { None },
                "operations": state.operations,
                "request_bytes": state.request_bytes,
                "response_bytes": state.response_bytes,
                "response_read_bytes": state.response_read_bytes,
                "pending_bytes": state.pending.len(),
                "unread_response_bytes": state.response.len().saturating_sub(state.consumed),
                "channel_open": state.open,
                "opened_once": state.opened_once,
                "finished": state.finished,
                "close_dispatched": state.close_dispatched,
                "dispatcher_complete": state.dispatcher_complete,
                "dispatcher_cancel_attempted": state.dispatcher_cancel_attempted,
                "dispatcher_cancelled": state.dispatcher_cancelled,
                "complete_sent": state.complete_sent,
                "invoked_callbacks": state.invoked,
                "live_handles": state.live_handles.len(),
                "pending_release": state.pending_release.is_some(),
                "pending_invoke": state.pending_invoke.is_some(),
                "failure": state.failure,
                "max_frame_bytes": MAX_FRAME,
                "max_request_bytes": MAX_TOTAL,
                "max_reply_bytes": MAX_TOTAL,
                "max_operations": MAX_OPERATIONS
            }),
            Err(_) => json!({"failure":"wp_hooks_state_poisoned"}),
        }
    }

    pub fn finish(&self) -> Value {
        if let Ok(mut state) = self.lock() {
            if state.open {
                state.fail("wp_hooks_channel_open_at_finish");
            }
            if !state.pending.is_empty() {
                state.fail("wp_hooks_incomplete_frame_at_finish");
            }
            if state.consumed != state.response.len() {
                state.fail("wp_hooks_unread_reply_at_finish");
            }
            if !state.close_dispatched {
                state.fail("wp_hooks_close_missing");
            }
            if !state.dispatcher_complete
                || !state.complete_sent
                || !state.live_handles.is_empty()
                || state.pending_invoke.is_some()
                || state.pending_release.is_some()
                || !state.cleanup_releases.is_empty()
            {
                state.fail("wp_hooks_dispatch_not_clean_at_finish");
            }
            if state.failure.is_some()
                && !state.dispatcher_complete
                && !state.dispatcher_cancel_attempted
            {
                let cancel_ticket = state.ticket;
                let cancelled = match (state.dispatcher.as_mut(), cancel_ticket) {
                    (Some(dispatcher), Some(ticket)) => {
                        let result = dispatcher.cancel(ticket).is_ok();
                        state.dispatcher_cancel_attempted = true;
                        result
                    }
                    _ => false,
                };
                state.dispatcher_cancelled = cancelled;
            }
            state.finished = true;
        }
        self.snapshot()
    }
}

fn entry(path: &Path) -> std::result::Result<bool, FsError> {
    let mut name = None;
    for component in path.components() {
        match component {
            Component::RootDir | Component::CurDir => {}
            Component::Normal(part) if name.is_none() => name = Some(part),
            _ => return Err(FsError::EntryNotFound),
        }
    }
    match name {
        None => Ok(false),
        Some(name) if name == CHANNEL_NAME => Ok(true),
        _ => Err(FsError::EntryNotFound),
    }
}

impl FileOpener for HookRpcFs {
    fn open(
        &self,
        path: &Path,
        options: &OpenOptionsConfig,
    ) -> Result<Box<dyn VirtualFile + Send + Sync>, FsError> {
        if !entry(path)? {
            return Err(FsError::NotAFile);
        }
        if !options.read
            || !options.write
            || options.append
            || options.truncate
            || options.create
            || options.create_new
        {
            return Err(self.deny("wp_hooks_requires_existing_read_write_channel"));
        }
        let mut state = self.lock().map_err(|_| FsError::IOError)?;
        if state.open || state.opened_once || state.finished || state.failure.is_some() {
            state.fail("wp_hooks_channel_already_open_or_finished");
            return Err(FsError::PermissionDenied);
        }
        state.open = true;
        state.opened_once = true;
        Ok(Box::new(Channel {
            fs: self.clone(),
            position: 0,
            readback_ready: false,
            closed: false,
        }))
    }
}

impl FileSystem for HookRpcFs {
    fn readlink(&self, _: &Path) -> Result<PathBuf, FsError> {
        Err(FsError::PermissionDenied)
    }

    fn read_dir(&self, _: &Path) -> Result<ReadDir, FsError> {
        Err(FsError::PermissionDenied)
    }

    fn metadata(&self, path: &Path) -> Result<Metadata, FsError> {
        let file = entry(path)?;
        let len = if file {
            self.lock().map_err(|_| FsError::IOError)?.response.len() as u64
        } else {
            0
        };
        Ok(Metadata {
            ft: FileType {
                file,
                dir: !file,
                ..FileType::default()
            },
            accessed: 0,
            created: 0,
            modified: 0,
            len,
        })
    }

    fn symlink_metadata(&self, path: &Path) -> Result<Metadata, FsError> {
        self.metadata(path)
    }

    fn new_open_options(&self) -> OpenOptions<'_> {
        OpenOptions::new(self)
    }

    fn create_dir(&self, _: &Path) -> Result<(), FsError> {
        Err(self.deny("wp_hooks_create_dir_forbidden"))
    }

    fn create_symlink(&self, _: &Path, _: &Path) -> Result<(), FsError> {
        Err(self.deny("wp_hooks_symlink_forbidden"))
    }

    fn hard_link(&self, _: &Path, _: &Path) -> Result<(), FsError> {
        Err(self.deny("wp_hooks_hard_link_forbidden"))
    }

    fn remove_dir(&self, _: &Path) -> Result<(), FsError> {
        Err(self.deny("wp_hooks_remove_dir_forbidden"))
    }

    fn remove_file(&self, _: &Path) -> Result<(), FsError> {
        Err(self.deny("wp_hooks_remove_file_forbidden"))
    }

    fn rename<'a>(
        &'a self,
        _: &'a Path,
        _: &'a Path,
    ) -> Pin<Box<dyn Future<Output = Result<(), FsError>> + Send + 'a>> {
        Box::pin(async { Err(FsError::PermissionDenied) })
    }
}

#[derive(Debug)]
struct Channel {
    fs: HookRpcFs,
    position: usize,
    readback_ready: bool,
    closed: bool,
}

impl Channel {
    fn check_open(&self) -> io::Result<()> {
        if self.closed {
            return Err(self.fs.lock()?.fail("wp_hooks_operation_on_closed_channel"));
        }
        Ok(())
    }

    fn close(&mut self) -> io::Result<()> {
        if self.closed {
            return Ok(());
        }
        self.closed = true;
        let mut state = self.fs.lock()?;
        state.open = false;
        if state.consumed != state.response.len() {
            return Err(state.fail("wp_hooks_close_with_unread_reply"));
        }
        if !state.pending.is_empty() {
            return Err(state.fail("wp_hooks_close_with_incomplete_frame"));
        }
        Ok(())
    }
}

impl Drop for Channel {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

impl tokio::io::AsyncRead for Channel {
    fn poll_read(
        self: Pin<&mut Self>,
        _: &mut Context<'_>,
        buffer: &mut tokio::io::ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        this.check_open()?;
        let mut state = this.fs.lock()?;
        state.active()?;
        if buffer.remaining() == 0 {
            return Poll::Ready(Ok(()));
        }
        if !state.pending.is_empty() || !this.readback_ready {
            return Poll::Ready(Err(
                state.fail("wp_hooks_read_before_complete_frame_and_seek_zero")
            ));
        }
        if this.position != state.consumed {
            return Poll::Ready(Err(state.fail("wp_hooks_reply_replay")));
        }
        let position = this.position.min(state.response.len());
        let count = (state.response.len() - position).min(buffer.remaining());
        buffer.put_slice(&state.response[position..position + count]);
        state.response_read_bytes += count;
        state.consumed += count;
        this.position += count;
        Poll::Ready(Ok(()))
    }
}

impl tokio::io::AsyncWrite for Channel {
    fn poll_write(
        self: Pin<&mut Self>,
        _: &mut Context<'_>,
        bytes: &[u8],
    ) -> Poll<io::Result<usize>> {
        let this = self.get_mut();
        this.check_open()?;
        let mut state = this.fs.lock()?;
        state.active()?;
        if !bytes.is_empty() && state.pending.is_empty() && this.position != 0 {
            return Poll::Ready(Err(state.fail("wp_hooks_new_frame_requires_seek_zero")));
        }
        state.receive(bytes)?;
        if !bytes.is_empty() {
            this.readback_ready = false;
        }
        this.position += bytes.len();
        Poll::Ready(Ok(bytes.len()))
    }

    fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
        self.check_open()?;
        Poll::Ready(self.fs.lock()?.active())
    }

    fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
        Poll::Ready(self.get_mut().close())
    }
}

impl tokio::io::AsyncSeek for Channel {
    fn start_seek(self: Pin<&mut Self>, seek: SeekFrom) -> io::Result<()> {
        let this = self.get_mut();
        this.check_open()?;
        let mut state = this.fs.lock()?;
        state.active()?;
        match seek {
            SeekFrom::Start(offset) if offset == this.position as u64 => Ok(()),
            SeekFrom::Start(0) if state.pending.is_empty() => {
                this.position = 0;
                this.readback_ready = true;
                Ok(())
            }
            SeekFrom::Current(0) => Ok(()),
            _ => Err(state.fail("wp_hooks_only_seek_zero_after_complete_frame")),
        }
    }

    fn poll_complete(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<u64>> {
        self.check_open()?;
        let state = self.fs.lock()?;
        if self.readback_ready && !state.pending.is_empty() {
            return Poll::Ready(Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "wp_hooks_reply_not_ready",
            )));
        }
        Poll::Ready(Ok(self.position as u64))
    }
}

impl VirtualFile for Channel {
    fn last_accessed(&self) -> u64 {
        0
    }
    fn last_modified(&self) -> u64 {
        0
    }
    fn created_time(&self) -> u64 {
        0
    }
    fn size(&self) -> u64 {
        self.fs
            .lock()
            .map(|state| state.response.len() as u64)
            .unwrap_or(0)
    }
    fn is_open(&self) -> bool {
        !self.closed
    }
    fn set_times(&mut self, _: Option<u64>, _: Option<u64>) -> Result<(), FsError> {
        Err(self.fs.deny("wp_hooks_metadata_mutation_forbidden"))
    }
    fn set_len(&mut self, _: u64) -> Result<(), FsError> {
        Err(self.fs.deny("wp_hooks_truncate_forbidden"))
    }
    fn unlink(&mut self) -> Result<(), FsError> {
        Err(self.fs.deny("wp_hooks_unlink_forbidden"))
    }
    fn poll_read_ready(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<usize>> {
        self.check_open()?;
        let state = self.fs.lock()?;
        if !state.pending.is_empty() || !self.readback_ready {
            return Poll::Ready(Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "wp_hooks_readiness_before_complete_frame_and_seek_zero",
            )));
        }
        Poll::Ready(Ok(state.response.len().saturating_sub(self.position)))
    }
    fn poll_write_ready(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<usize>> {
        self.check_open()?;
        let state = self.fs.lock()?;
        Poll::Ready(Ok(
            MAX_FRAME.min(MAX_TOTAL.saturating_sub(state.request_bytes))
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt};

    const NONCE: &str = "0123456789abcdef0123456789abcdef0123456789abcdef";
    const FILTERED_VALUES: [&str; 2] = [
        "RP Bootstrap Fixture|first",
        "RP Bootstrap Fixture|first|second",
    ];

    fn handle(slot: u64, token: &str) -> Value {
        json!({
            "request": "1",
            "kind": "callable",
            "slot": slot,
            "generation": 1,
            "token": token
        })
    }

    fn rows() -> Vec<Value> {
        CALLBACKS
            .iter()
            .enumerate()
            .map(|(index, (uid, priority))| {
                let (slot, token) = if index == 0 {
                    (1, "0000000000000001")
                } else {
                    (2, "0000000000000002")
                };
                json!({
                    "priority": priority,
                    "uid": uid,
                    "accepted_args": 1,
                    "function": uid,
                    "handle": handle(slot, token)
                })
            })
            .collect()
    }

    fn begin_payload() -> Value {
        let rows = rows();
        let fingerprint_rows: Vec<Value> = rows
            .iter()
            .map(|row| {
                let handle = &row["handle"];
                json!([
                    row["priority"],
                    row["uid"],
                    row["accepted_args"],
                    row["function"],
                    handle["request"],
                    handle["kind"],
                    handle["slot"],
                    handle["generation"],
                    handle["token"]
                ])
            })
            .collect();
        let fingerprint = crate::db::sha(&serde_json::to_vec(&fingerprint_rows).unwrap());
        json!({
            "hook": HOOK_NAME,
            "target": TARGET,
            "value": INPUT_VALUE,
            "extra": ["blogname"],
            "rows": rows,
            "fingerprint": fingerprint
        })
    }

    fn frame(nonce: &str, sequence: u64, operation: &str, payload: Value) -> Vec<u8> {
        frame_with_schema(nonce, REQUEST_SCHEMA, sequence, operation, payload)
    }

    fn frame_with_schema(
        nonce: &str,
        schema: &str,
        sequence: u64,
        operation: &str,
        payload: Value,
    ) -> Vec<u8> {
        let mut wire = serde_json::to_vec(&json!({
            "schema": schema,
            "nonce": nonce,
            "seq": sequence,
            "op": operation,
            "payload": payload
        }))
        .unwrap();
        wire.push(b'\n');
        wire
    }

    fn open(fs: &HookRpcFs) -> Box<dyn VirtualFile + Send + Sync> {
        fs.new_open_options()
            .read(true)
            .write(true)
            .open("/channel")
            .unwrap()
    }

    async fn exchange(
        file: &mut Box<dyn VirtualFile + Send + Sync>,
        sequence: u64,
        operation: &str,
        payload: Value,
    ) -> Value {
        file.seek(SeekFrom::Start(0)).await.unwrap();
        file.write_all(&frame(NONCE, sequence, operation, payload))
            .await
            .unwrap();
        file.seek(SeekFrom::Start(0)).await.unwrap();
        let mut reply = Vec::new();
        file.read_to_end(&mut reply).await.unwrap();
        assert!(reply.ends_with(b"\n"));
        let reply: Value = serde_json::from_slice(&reply[..reply.len() - 1]).unwrap();
        assert_eq!(reply["schema"], REPLY_SCHEMA);
        assert_eq!(reply["nonce"], NONCE);
        assert_eq!(reply["seq"], sequence);
        reply
    }

    #[test]
    fn full_two_callback_dispatch_release_complete_and_close() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        runtime.block_on(async {
            let fs = HookRpcFs::new(NONCE.to_owned());
            let mut file = open(&fs);
            let expected_handles: Vec<Value> =
                rows().iter().map(|row| row["handle"].clone()).collect();
            let mut next_sequence = 1;
            let mut reply = exchange(&mut file, next_sequence, "begin", begin_payload()).await;
            next_sequence += 1;
            assert_eq!(reply["event"], "invoke");

            let callback_names = CALLBACKS.map(|(uid, _)| uid);
            let input_values = [INPUT_VALUE, FILTERED_VALUES[0]];
            let mut next_callback = 0;
            let mut releases = 0;
            loop {
                match reply["event"].as_str().unwrap() {
                    "invoke" => {
                        assert!(next_callback < callback_names.len());
                        let uid = callback_names[next_callback];
                        assert_eq!(reply["payload"]["uid"], uid);
                        assert_eq!(reply["payload"]["target"], TARGET);
                        assert_eq!(reply["payload"]["priority"], CALLBACKS[next_callback].1);
                        assert_eq!(reply["payload"]["call_mode"], "array");
                        assert_eq!(reply["payload"]["handle"], expected_handles[next_callback]);
                        assert_eq!(reply["payload"]["value"], input_values[next_callback]);
                        assert_eq!(
                            reply["payload"]["args"],
                            json!([input_values[next_callback]])
                        );
                        reply = exchange(
                            &mut file,
                            next_sequence,
                            "resume",
                            json!({
                                "target": TARGET,
                                "uid": uid,
                                "value": FILTERED_VALUES[next_callback]
                            }),
                        )
                        .await;
                        next_sequence += 1;
                        next_callback += 1;
                    }
                    "release" => {
                        let released = reply["payload"]["handle"].clone();
                        assert!(releases < expected_handles.len());
                        assert_eq!(released, expected_handles[releases]);
                        reply = exchange(
                            &mut file,
                            next_sequence,
                            "release_ack",
                            json!({"handle": released}),
                        )
                        .await;
                        next_sequence += 1;
                        releases += 1;
                    }
                    "complete" => break,
                    event => panic!("unexpected hook RPC event: {event}"),
                }
                assert!(next_sequence <= MAX_OPERATIONS as u64);
            }

            assert_eq!(next_callback, 2);
            assert_eq!(releases, expected_handles.len());
            assert_eq!(reply["payload"]["target"], TARGET);
            assert_eq!(reply["payload"]["value"], FILTERED_VALUES[1]);
            assert_eq!(reply["payload"]["invocations"], 2);
            let live = fs.snapshot();
            assert_eq!(live["dispatcher_complete"], true);
            assert_eq!(live["complete_sent"], true);
            assert_eq!(live["live_handles"], 0);
            assert_eq!(live["invoked_callbacks"], json!(callback_names));
            assert_eq!(live["pending_invoke"], false);
            assert_eq!(live["pending_release"], false);

            let closed = exchange(&mut file, next_sequence, "close", json!({})).await;
            assert_eq!(closed["event"], "close");
            assert_eq!(closed["payload"]["closed"], true);
            file.shutdown().await.unwrap();
            drop(file);

            let finished = fs.finish();
            assert_eq!(finished["failure"], Value::Null);
            assert_eq!(finished["finished"], true);
            assert_eq!(finished["close_dispatched"], true);
            assert_eq!(finished["dispatcher_complete"], true);
            assert_eq!(finished["dispatcher_cancel_attempted"], false);
            assert_eq!(finished["dispatcher_cancelled"], false);
            assert_eq!(finished["complete_sent"], true);
            assert_eq!(finished["live_handles"], 0);
            assert_eq!(finished["unread_response_bytes"], 0);
            assert_eq!(finished["response_read_bytes"], finished["response_bytes"]);
        });
    }

    #[test]
    fn rejects_bad_schema_fingerprint_and_release_ack_identity() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        runtime.block_on(async {
            let fs = HookRpcFs::new(NONCE.to_owned());
            let mut file = open(&fs);
            assert!(
                file.write_all(&frame_with_schema(
                    NONCE,
                    "recastpress.wp-hooks-request.invalid",
                    1,
                    "begin",
                    begin_payload()
                ))
                .await
                .is_err()
            );
            assert_eq!(fs.snapshot()["failure"], "wp_hooks_request_schema");

            let fs = HookRpcFs::new(NONCE.to_owned());
            let mut file = open(&fs);
            let mut payload = begin_payload();
            payload["fingerprint"] = Value::String("0".repeat(64));
            assert!(
                file.write_all(&frame(NONCE, 1, "begin", payload))
                    .await
                    .is_err()
            );
            assert_eq!(fs.snapshot()["failure"], "wp_hooks_callback_fingerprint");

            let fs = HookRpcFs::new(NONCE.to_owned());
            let mut file = open(&fs);
            let first_invoke = exchange(&mut file, 1, "begin", begin_payload()).await;
            assert_eq!(first_invoke["event"], "invoke");
            let second_invoke = exchange(
                &mut file,
                2,
                "resume",
                json!({
                    "target": TARGET,
                    "uid": CALLBACKS[0].0,
                    "value": FILTERED_VALUES[0]
                }),
            )
            .await;
            assert_eq!(second_invoke["event"], "invoke");
            assert_eq!(second_invoke["payload"]["uid"], CALLBACKS[1].0);
            let release = exchange(
                &mut file,
                3,
                "resume",
                json!({
                    "target": TARGET,
                    "uid": CALLBACKS[1].0,
                    "value": FILTERED_VALUES[1]
                }),
            )
            .await;
            assert_eq!(release["event"], "release");
            file.seek(SeekFrom::Start(0)).await.unwrap();
            let wrong_handle = handle(9, "0000000000000009");
            assert!(
                file.write_all(&frame(
                    NONCE,
                    4,
                    "release_ack",
                    json!({"handle": wrong_handle})
                ))
                .await
                .is_err()
            );
            assert_eq!(fs.snapshot()["failure"], "wp_hooks_release_ack_identity");
        });
    }

    #[test]
    fn finish_cancels_incomplete_dispatch_once_and_preserves_first_failure() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        runtime.block_on(async {
            let fs = HookRpcFs::new(NONCE.to_owned());
            let mut file = open(&fs);
            let invoke = exchange(&mut file, 1, "begin", begin_payload()).await;
            assert_eq!(invoke["event"], "invoke");

            file.seek(SeekFrom::Start(0)).await.unwrap();
            assert!(file
                .write_all(&frame(NONCE, 2, "invalid", json!({})))
                .await
                .is_err());
            assert_eq!(fs.snapshot()["failure"], "wp_hooks_unknown_operation");
            file.shutdown().await.unwrap();
            drop(file);

            let failed = fs.finish();
            assert_eq!(failed["failure"], "wp_hooks_unknown_operation");
            assert_eq!(failed["dispatcher_cancel_attempted"], true);
            assert_eq!(failed["dispatcher_cancelled"], true);

            let repeated = fs.finish();
            assert_eq!(repeated["failure"], "wp_hooks_unknown_operation");
            assert_eq!(repeated["dispatcher_cancel_attempted"], true);
            assert_eq!(repeated["dispatcher_cancelled"], true);
        });
    }

    #[test]
    fn unread_reply_wrong_sequence_nonce_and_multiple_frames_fail_closed() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        runtime.block_on(async {
            let fs = HookRpcFs::new(NONCE.to_owned());
            let mut file = open(&fs);
            file.seek(SeekFrom::Start(0)).await.unwrap();
            file.write_all(&frame(NONCE, 1, "begin", begin_payload()))
                .await
                .unwrap();
            file.seek(SeekFrom::Start(0)).await.unwrap();
            let next = frame(NONCE, 2, "resume", json!({}));
            assert!(file.write_all(&next).await.is_err());
            assert_eq!(
                fs.snapshot()["failure"],
                "wp_hooks_request_before_reply_consumed"
            );
            drop(file);
            assert!(!fs.finish()["failure"].is_null());

            let fs = HookRpcFs::new(NONCE.to_owned());
            let mut file = open(&fs);
            assert!(
                file.write_all(&frame(NONCE, 2, "begin", begin_payload()))
                    .await
                    .is_err()
            );
            assert_eq!(fs.snapshot()["failure"], "wp_hooks_request_sequence_order");

            let fs = HookRpcFs::new(NONCE.to_owned());
            let mut file = open(&fs);
            let other_nonce = "f".repeat(48);
            assert!(
                file.write_all(&frame(&other_nonce, 1, "begin", begin_payload()))
                    .await
                    .is_err()
            );
            assert_eq!(fs.snapshot()["failure"], "wp_hooks_request_nonce");

            let fs = HookRpcFs::new(NONCE.to_owned());
            let mut file = open(&fs);
            let mut joined = frame(NONCE, 1, "begin", begin_payload());
            joined.extend_from_slice(&frame(NONCE, 2, "resume", json!({})));
            assert!(file.write_all(&joined).await.is_err());
            assert_eq!(fs.snapshot()["failure"], "wp_hooks_multiple_frames");
        });
    }
}
