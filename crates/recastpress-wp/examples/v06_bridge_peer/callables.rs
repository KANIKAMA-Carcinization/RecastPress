//! Standard PHP hook API requests driven by the request-local Rust Dispatcher.

use super::*;
use recastpress_wp::{
    continuation::{
        Completion, Continuation, EffectId, LimitsProfile, OperationId, PhpHandle, PhpHandleKind,
        RequestGeneration, RequestState,
    },
    dispatcher::{
        ControlOperation, ControlResult, ControlStep, DispatchStep, DispatchTicket, Dispatcher,
    },
    hooks::{CallbackId, ContextCallback, PhpCallMode},
    value::{PhpArray, PhpKey, PhpValue},
};
use std::collections::{BTreeMap, HashMap, HashSet};

const REQUEST_GENERATION: u64 = 61;
const SCRIPT_EFFECT: &str = "e-script";
const API_NAMES: [&str; 7] = [
    "add_filter",
    "remove_filter",
    "has_filter",
    "apply_filters",
    "current_filter",
    "doing_filter",
    "did_filter",
];
const MAX_RELEASE_PASSES: usize = 64;

fn decode_hex(value: &str, label: &str) -> Result<Vec<u8>, String> {
    if !value.len().is_multiple_of(2)
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(format!("{label}_hex_schema"));
    }
    (0..value.len())
        .step_by(2)
        .map(|offset| {
            u8::from_str_radix(&value[offset..offset + 2], 16).map_err(|_| format!("{label}_hex"))
        })
        .collect()
}

fn encode_hex(value: &[u8]) -> String {
    let mut encoded = String::with_capacity(value.len() * 2);
    for byte in value {
        use std::fmt::Write as _;
        write!(encoded, "{byte:02x}").expect("writing to String cannot fail");
    }
    encoded
}

fn signed_decimal(value: &Value, label: &str) -> Result<i64, String> {
    let text = value
        .as_str()
        .ok_or_else(|| format!("{label}_must_be_string"))?;
    let parsed = text
        .parse::<i64>()
        .map_err(|_| format!("{label}_integer_schema"))?;
    if parsed.to_string() != text {
        return Err(format!("{label}_integer_canonical"));
    }
    Ok(parsed)
}

fn parse_pod(value: &Value, label: &str) -> Result<PhpValue, String> {
    let tag = string(value, "type")?;
    match tag {
        "null" => {
            exact_keys(value, &["type"], label)?;
            Ok(PhpValue::Null)
        }
        "bool" => {
            exact_keys(value, &["type", "value"], label)?;
            value
                .get("value")
                .and_then(Value::as_bool)
                .map(PhpValue::Bool)
                .ok_or_else(|| format!("{label}_bool"))
        }
        "int" => {
            exact_keys(value, &["type", "value"], label)?;
            Ok(PhpValue::Integer(signed_decimal(
                value
                    .get("value")
                    .ok_or_else(|| format!("{label}_value_missing"))?,
                label,
            )?))
        }
        "float" => {
            exact_keys(value, &["type", "bits"], label)?;
            let bits = decode_hex(string(value, "bits")?, label)?;
            let bits: [u8; 8] = bits
                .try_into()
                .map_err(|_| format!("{label}_float_bits_length"))?;
            Ok(PhpValue::Float(f64::from_bits(u64::from_be_bytes(bits))))
        }
        "string" => {
            exact_keys(value, &["type", "hex"], label)?;
            Ok(PhpValue::Bytes(decode_hex(string(value, "hex")?, label)?))
        }
        "array" => {
            exact_keys(value, &["type", "entries"], label)?;
            let entries = value
                .get("entries")
                .and_then(Value::as_array)
                .ok_or_else(|| format!("{label}_entries"))?;
            let mut result = PhpArray::new();
            for (index, entry) in entries.iter().enumerate() {
                exact_keys(entry, &["key", "value"], label)?;
                let entry_label = format!("{label}_entry_{index}");
                let key_node = entry
                    .get("key")
                    .ok_or_else(|| format!("{entry_label}_key_missing"))?;
                let key_type = string(key_node, "type")?;
                let key = match key_type {
                    "int" => {
                        exact_keys(key_node, &["type", "value"], &entry_label)?;
                        PhpKey::Integer(signed_decimal(
                            key_node
                                .get("value")
                                .ok_or_else(|| format!("{entry_label}_key_value_missing"))?,
                            &entry_label,
                        )?)
                    }
                    "string" => {
                        exact_keys(key_node, &["type", "hex"], &entry_label)?;
                        let bytes = decode_hex(string(key_node, "hex")?, &entry_label)?;
                        match PhpKey::from_bytes(&bytes) {
                            PhpKey::Bytes(bytes) => PhpKey::Bytes(bytes),
                            PhpKey::Integer(_) => {
                                return Err(format!("{entry_label}_string_key_not_normalized"));
                            }
                        }
                    }
                    _ => return Err(format!("{entry_label}_key_type")),
                };
                let item = parse_pod(
                    entry
                        .get("value")
                        .ok_or_else(|| format!("{entry_label}_value_missing"))?,
                    &entry_label,
                )?;
                if result.insert(key, item).is_some() {
                    return Err(format!("{entry_label}_duplicate_normalized_key"));
                }
            }
            Ok(PhpValue::Array(result))
        }
        _ => Err(format!("{label}_type")),
    }
}

fn pod(value: &PhpValue, label: &str) -> Result<Value, String> {
    Ok(match value {
        PhpValue::Null => json!({"type":"null"}),
        PhpValue::Bool(value) => json!({"type":"bool","value":value}),
        PhpValue::Integer(value) => json!({"type":"int","value":value.to_string()}),
        PhpValue::Float(value) => json!({
            "type":"float",
            "bits":encode_hex(&value.to_bits().to_be_bytes()),
        }),
        PhpValue::Bytes(value) => json!({"type":"string","hex":encode_hex(value)}),
        PhpValue::Array(values) => {
            let mut entries = Vec::with_capacity(values.len());
            for (key, value) in values.iter() {
                let key = match key {
                    PhpKey::Integer(value) => {
                        json!({"type":"int","value":value.to_string()})
                    }
                    PhpKey::Bytes(value) => {
                        json!({"type":"string","hex":encode_hex(value)})
                    }
                };
                entries.push(json!({"key":key,"value":pod(value,label)?}));
            }
            json!({"type":"array","entries":entries})
        }
        PhpValue::Unsupported => return Err(format!("{label}_unsupported_php_value")),
    })
}

fn callback_key(value: &Value, label: &str) -> Result<Option<CallbackId>, String> {
    if value.is_null() {
        return Ok(None);
    }
    exact_keys(value, &["type", "hex"], label)?;
    let bytes = decode_hex(string(value, "hex")?, label)?;
    let callback = match string(value, "type")? {
        "int" => {
            let text = std::str::from_utf8(&bytes).map_err(|_| format!("{label}_int_utf8"))?;
            let parsed = text
                .parse::<i64>()
                .map_err(|_| format!("{label}_int_schema"))?;
            if parsed.to_string() != text {
                return Err(format!("{label}_int_canonical"));
            }
            format!("i:{text}")
        }
        "string" => {
            if !matches!(PhpKey::from_bytes(&bytes), PhpKey::Bytes(normalized) if normalized == bytes)
            {
                return Err(format!("{label}_string_key_not_normalized"));
            }
            format!("s:{}", encode_hex(&bytes))
        }
        _ => return Err(format!("{label}_type")),
    };
    Ok(Some(CallbackId {
        module: "wp-hook-id".into(),
        callback,
    }))
}

fn internal_hook(raw: &[u8]) -> String {
    if raw == b"all" {
        "all".into()
    } else {
        format!("b:{}", encode_hex(raw))
    }
}

fn raw_hook(internal: &str, label: &str) -> Result<Vec<u8>, String> {
    if internal == "all" {
        return Ok(b"all".to_vec());
    }
    let hex = internal
        .strip_prefix("b:")
        .ok_or_else(|| format!("{label}_internal_hook"))?;
    decode_hex(hex, label)
}

fn parse_handle(
    value: &Value,
    request: RequestGeneration,
    expected_kind: PhpHandleKind,
    label: &str,
) -> Result<PhpHandle, String> {
    exact_keys(
        value,
        &["request", "kind", "slot", "generation", "token"],
        label,
    )?;
    let request_text = string(value, "request")?;
    let request_number = request_text
        .parse::<u64>()
        .map_err(|_| format!("{label}_request_schema"))?;
    if request_number.to_string() != request_text || request_number != request.get() {
        return Err(format!("{label}_request_mismatch"));
    }
    let kind = match string(value, "kind")? {
        "callable" => PhpHandleKind::Callable,
        "throwable" => PhpHandleKind::Throwable,
        _ => return Err(format!("{label}_kind")),
    };
    if kind != expected_kind {
        return Err(format!("{label}_kind"));
    }
    let slot = value
        .get("slot")
        .and_then(Value::as_u64)
        .and_then(|number| u32::try_from(number).ok())
        .ok_or_else(|| format!("{label}_slot"))?;
    let generation = value
        .get("generation")
        .and_then(Value::as_u64)
        .and_then(|number| u32::try_from(number).ok())
        .filter(|number| *number > 0)
        .ok_or_else(|| format!("{label}_generation"))?;
    let token_text = string(value, "token")?;
    if token_text.len() != 16
        || !token_text
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(format!("{label}_token_schema"));
    }
    let token = u64::from_str_radix(token_text, 16).map_err(|_| format!("{label}_token"))?;
    Ok(PhpHandle::new(request, kind, slot, generation, token))
}

fn descriptor(handle: PhpHandle) -> Value {
    let kind = match handle.kind() {
        PhpHandleKind::Callable => "callable",
        PhpHandleKind::Throwable => "throwable",
        _ => unreachable!("peer only emits callable and throwable handles"),
    };
    json!({
        "request":handle.request().get().to_string(),
        "kind":kind,
        "slot":handle.slot(),
        "generation":handle.generation(),
        "token":format!("{:016x}", handle.token()),
    })
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum HandleState {
    Active,
    ReleasePending,
    Throwable,
}

enum ControlOutcome {
    Result(ControlResult),
    Throwable(PhpHandle),
}

#[derive(Clone, Copy)]
enum EffectValueMode {
    Pod,
    ReleaseAck,
    RegistryObservation,
}

struct CallablePeer<'a, R, W> {
    wire: &'a mut Wire<R, W>,
    continuation: &'a mut Continuation<Value>,
    dispatcher: &'a mut Dispatcher,
    request: RequestGeneration,
    handles: HashMap<PhpHandle, HandleState>,
    throwable_diagnostics: HashMap<PhpHandle, (String, String)>,
    effect_ids: HashMap<String, EffectId>,
    hook_names: HashSet<String>,
    api_counts: BTreeMap<String, u64>,
    callback_invocations: u64,
    release_effects: u64,
    next_effect: u64,
    registration_epoch: u64,
}

impl<R: BufRead, W: Write> CallablePeer<'_, R, W> {
    fn fresh_effect(&mut self, prefix: &str) -> String {
        let sequence = self.next_effect;
        self.next_effect += 1;
        format!("e-{prefix}-{sequence}")
    }

    fn register_handle(
        &mut self,
        value: &Value,
        kind: PhpHandleKind,
        label: &str,
    ) -> Result<PhpHandle, String> {
        let handle = parse_handle(value, self.request, kind, label)?;
        match self.handles.get(&handle) {
            Some(HandleState::Active) if kind == PhpHandleKind::Callable => {
                return Err(format!("{label}_handle_reused"));
            }
            Some(HandleState::ReleasePending) => {
                return Err(format!("{label}_handle_release_pending"));
            }
            Some(HandleState::Throwable) if kind == PhpHandleKind::Throwable => return Ok(handle),
            Some(_) => return Err(format!("{label}_handle_state")),
            None => {}
        }
        self.continuation
            .register_handle(handle)
            .map_err(|error| format!("{label}_register:{error:?}"))?;
        self.handles.insert(
            handle,
            if kind == PhpHandleKind::Throwable {
                HandleState::Throwable
            } else {
                HandleState::Active
            },
        );
        Ok(handle)
    }

    fn check_invocable(&self, handle: PhpHandle, label: &str) -> Result<(), String> {
        if self.handles.get(&handle) != Some(&HandleState::Active) {
            return Err(format!("{label}_handle_not_active"));
        }
        self.continuation
            .validate_handle(handle)
            .map_err(|error| format!("{label}_handle_mirror:{error:?}"))
    }

    fn process_completion(
        &mut self,
        value: &Value,
        label: &str,
        value_mode: EffectValueMode,
    ) -> Result<Completion<PhpValue>, String> {
        match string(value, "kind")? {
            "value" => {
                exact_keys(value, &["kind", "value"], label)?;
                let value = value
                    .get("value")
                    .ok_or_else(|| format!("{label}_value_missing"))?;
                let value = match value_mode {
                    EffectValueMode::Pod => parse_pod(value, label)?,
                    EffectValueMode::ReleaseAck => {
                        exact_keys(value, &["released"], label)?;
                        if value.get("released") != Some(&Value::Bool(true)) {
                            return Err(format!("{label}_release_ack"));
                        }
                        let mut array = PhpArray::new();
                        array.insert(PhpKey::Bytes(b"released".to_vec()), PhpValue::Bool(true));
                        PhpValue::Array(array)
                    }
                    EffectValueMode::RegistryObservation => {
                        exact_keys(
                            value,
                            &["closed", "live_after_close", "closed_read_rejected"],
                            label,
                        )?;
                        if value.get("closed") != Some(&Value::Bool(true))
                            || value.get("live_after_close") != Some(&json!(0))
                            || value.get("closed_read_rejected") != Some(&Value::Bool(true))
                        {
                            return Err(format!("{label}_registry_observation"));
                        }
                        let mut array = PhpArray::new();
                        array.insert(PhpKey::Bytes(b"closed".to_vec()), PhpValue::Bool(true));
                        array.insert(
                            PhpKey::Bytes(b"live_after_close".to_vec()),
                            PhpValue::Integer(0),
                        );
                        array.insert(
                            PhpKey::Bytes(b"closed_read_rejected".to_vec()),
                            PhpValue::Bool(true),
                        );
                        PhpValue::Array(array)
                    }
                };
                Ok(Completion::Value(value))
            }
            "php_throwable" => {
                exact_keys(
                    value,
                    &["kind", "handle", "class_hex", "message_hex"],
                    label,
                )?;
                let class_hex = string(value, "class_hex")?;
                let message_hex = string(value, "message_hex")?;
                let class = decode_hex(class_hex, label)?;
                let message = decode_hex(message_hex, label)?;
                if class.is_empty() || message.is_empty() {
                    return Err(format!("{label}_throwable_diagnostic_empty"));
                }
                let handle = self.register_handle(
                    value
                        .get("handle")
                        .ok_or_else(|| format!("{label}_handle_missing"))?,
                    PhpHandleKind::Throwable,
                    label,
                )?;
                self.throwable_diagnostics
                    .insert(handle, (class_hex.to_owned(), message_hex.to_owned()));
                Ok(Completion::PhpThrowable(handle))
            }
            _ => Err(format!("{label}_completion_kind")),
        }
    }

    fn completion_json(
        &self,
        completion: &Completion<PhpValue>,
        label: &str,
    ) -> Result<Value, String> {
        Ok(match completion {
            Completion::Value(value) => json!({"kind":"value","value":pod(value,label)?}),
            Completion::PhpThrowable(handle) => {
                if self.handles.get(handle) != Some(&HandleState::Throwable) {
                    return Err(format!("{label}_throwable_not_registered"));
                }
                let (class_hex, message_hex) = self
                    .throwable_diagnostics
                    .get(handle)
                    .ok_or_else(|| format!("{label}_throwable_diagnostics_missing"))?;
                json!({"kind":"php_throwable","handle":descriptor(*handle),"class_hex":class_hex,"message_hex":message_hex})
            }
        })
    }

    fn drive_effect(
        &mut self,
        effect_wire: &str,
        effect: EffectId,
        caller_wire: Option<&str>,
        value_mode: EffectValueMode,
    ) -> Result<Completion<PhpValue>, String> {
        if self
            .effect_ids
            .insert(effect_wire.to_owned(), effect)
            .is_some()
        {
            return Err("effect_wire_id_reused".into());
        }
        loop {
            let (kind, payload) = self.wire.receive_one_of(&[
                FrameExpectation {
                    kind: "call_core",
                    parent_effect: Some(effect_wire),
                },
                FrameExpectation {
                    kind: "resume",
                    parent_effect: caller_wire,
                },
            ])?;
            if kind == "call_core" {
                let api = self.process_call_core(&payload, effect_wire)?;
                let api_name = string(&payload, "api")?;
                let completion = self.completion_json(&api, "call_core_return")?;
                self.wire.send(
                    "return",
                    Some(effect_wire),
                    json!({"api":api_name,"completion":completion}),
                )?;
                continue;
            }
            exact_keys(&payload, &["effect_id", "completion"], "effect_resume")?;
            if string(&payload, "effect_id")? != effect_wire {
                return Err("effect_resume_id_mismatch".into());
            }
            let completion = self.process_completion(
                payload
                    .get("completion")
                    .ok_or("effect_completion_missing")?,
                "effect_completion",
                value_mode,
            )?;
            let continuation_completion = match &completion {
                Completion::Value(value) => Completion::Value(pod(value, "effect_resume_value")?),
                Completion::PhpThrowable(handle) => Completion::PhpThrowable(*handle),
            };
            let resumed = self
                .continuation
                .resume(effect, continuation_completion)
                .map_err(|error| format!("effect_resume:{error:?}"))?;
            self.effect_ids.remove(effect_wire);
            return match resumed {
                Completion::Value(_) => Ok(completion),
                Completion::PhpThrowable(handle) => Ok(Completion::PhpThrowable(handle)),
            };
        }
    }

    fn process_call_core(
        &mut self,
        payload: &Value,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        let api_name = string(payload, "api")?.to_owned();
        let count = self.api_counts.get_mut(&api_name).ok_or("unknown_api")?;
        *count = count.checked_add(1).ok_or("api_count_overflow")?;
        let parent = self
            .effect_ids
            .get(parent_wire)
            .copied()
            .ok_or("call_core_parent_effect_unknown")?;
        let operation = self
            .continuation
            .begin_child(parent)
            .map_err(|error| format!("begin_api_child:{error:?}"))?;
        let completion = match api_name.as_str() {
            "add_filter" => self.api_add(payload, operation, parent_wire),
            "remove_filter" => self.api_remove(payload, operation, parent_wire),
            "has_filter" => self.api_has(payload),
            "apply_filters" => self.api_apply(payload, operation, parent_wire),
            "current_filter" => self.api_current(payload),
            "doing_filter" => self.api_doing(payload),
            "did_filter" => self.api_did(payload),
            _ => Err("call_core_unknown_api".into()),
        }?;
        let returned_parent = self
            .continuation
            .finish_operation(operation)
            .map_err(|error| format!("finish_api_child:{error:?}"))?
            .ok_or("api_child_missing_parent")?;
        if returned_parent != parent {
            return Err("api_parent_effect_mismatch".into());
        }
        Ok(completion)
    }

    fn parse_hook(payload: &Value, key: &str, nullable: bool) -> Result<(Vec<u8>, String), String> {
        let value = payload.get(key).ok_or_else(|| format!("{key}_missing"))?;
        if value.is_null() && nullable {
            return Ok((Vec::new(), String::new()));
        }
        let hex = value
            .as_str()
            .ok_or_else(|| format!("{key}_must_be_hex_string"))?;
        let raw = decode_hex(hex, key)?;
        Ok((raw.clone(), internal_hook(&raw)))
    }

    fn api_add(
        &mut self,
        payload: &Value,
        operation: OperationId,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        exact_keys(
            payload,
            &[
                "api",
                "hook_hex",
                "key",
                "priority",
                "accepted_args",
                "handle",
            ],
            "add_filter_call",
        )?;
        let (raw, hook) = Self::parse_hook(payload, "hook_hex", false)?;
        let key = callback_key(payload.get("key").ok_or("add_key_missing")?, "add_key")?;
        let priority = payload
            .get("priority")
            .and_then(Value::as_i64)
            .ok_or("add_priority")?;
        self.hook_names.insert(hook.clone());
        if let Some(id) = key {
            let accepted = payload
                .get("accepted_args")
                .and_then(Value::as_i64)
                .ok_or("add_accepted_args")?;
            let handle = self.register_handle(
                payload.get("handle").ok_or("add_handle_missing")?,
                PhpHandleKind::Callable,
                "add_handle",
            )?;
            let callback = ContextCallback::php_callable(id, accepted, handle)
                .map_err(|error| format!("add_callback:{error:?}"))?;
            let mut control = self
                .dispatcher
                .begin_add(&hook, priority, callback)
                .map_err(|error| format!("begin_add:{error:?}"))?;
            self.registration_epoch += 1;
            match self.run_control(&mut control, operation, parent_wire, "add_filter")? {
                ControlOutcome::Result(ControlResult::Added(true)) => {}
                ControlOutcome::Throwable(handle) => return Ok(Completion::PhpThrowable(handle)),
                _ => return Err("add_result_mismatch".into()),
            }
        } else if !payload.get("handle").is_some_and(Value::is_null)
            || !payload.get("accepted_args").is_some_and(Value::is_null)
        {
            return Err("add_null_key_sentinels_must_be_null".into());
        }
        let _ = raw;
        Ok(Completion::Value(PhpValue::Bool(true)))
    }

    fn api_remove(
        &mut self,
        payload: &Value,
        operation: OperationId,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        exact_keys(
            payload,
            &["api", "hook_hex", "key", "priority"],
            "remove_filter_call",
        )?;
        let (_, hook) = Self::parse_hook(payload, "hook_hex", false)?;
        let key = callback_key(
            payload.get("key").ok_or("remove_key_missing")?,
            "remove_key",
        )?;
        let priority = payload
            .get("priority")
            .and_then(Value::as_i64)
            .ok_or("remove_priority")?;
        let removed = if let Some(id) = key {
            let mut control = self
                .dispatcher
                .begin_remove(&hook, priority, &id)
                .map_err(|error| format!("begin_remove:{error:?}"))?;
            match self.run_control(&mut control, operation, parent_wire, "remove_filter")? {
                ControlOutcome::Result(ControlResult::Removed(result)) => result,
                ControlOutcome::Throwable(handle) => return Ok(Completion::PhpThrowable(handle)),
                _ => return Err("remove_result_mismatch".into()),
            }
        } else {
            if !payload.get("key").is_some_and(Value::is_null) {
                return Err("remove_key_schema".into());
            }
            false
        };
        Ok(Completion::Value(PhpValue::Bool(removed)))
    }

    fn api_has(&self, payload: &Value) -> Result<Completion<PhpValue>, String> {
        exact_keys(
            payload,
            &["api", "hook_hex", "key", "any", "priority"],
            "has_filter_call",
        )?;
        let (_, hook) = Self::parse_hook(payload, "hook_hex", false)?;
        let key = callback_key(payload.get("key").ok_or("has_key_missing")?, "has_key")?;
        let any = payload
            .get("any")
            .and_then(Value::as_bool)
            .ok_or("has_any")?;
        let priority = payload.get("priority");
        if !matches!(priority, Some(Value::Null))
            && priority.is_none_or(|value| value.as_i64().is_none())
        {
            return Err("has_priority_schema".into());
        }
        let result = if any {
            if key.is_some() {
                return Err("has_any_key_must_be_null".into());
            }
            PhpValue::Bool(self.dispatcher.has_callbacks(&hook))
        } else if let Some(id) = key {
            if id.callback == "i:0" || id.callback == "s:" {
                PhpValue::Bool(false)
            } else {
                match priority {
                    Some(Value::Number(value)) => PhpValue::Bool(self.dispatcher.has_at(
                        &hook,
                        value.as_i64().ok_or("has_priority_schema")?,
                        &id,
                    )),
                    Some(Value::Null) => self
                        .dispatcher
                        .priority_of(&hook, &id)
                        .map(PhpValue::Integer)
                        .unwrap_or(PhpValue::Bool(false)),
                    _ => unreachable!("priority schema checked above"),
                }
            }
        } else {
            if !payload.get("key").is_some_and(Value::is_null) {
                return Err("has_key_schema".into());
            }
            PhpValue::Bool(false)
        };
        Ok(Completion::Value(result))
    }

    fn api_current(&self, payload: &Value) -> Result<Completion<PhpValue>, String> {
        exact_keys(payload, &["api"], "current_filter_call")?;
        let result = match self.dispatcher.current_hook() {
            Some(name) => PhpValue::Bytes(raw_hook(name, "current_filter")?),
            None => PhpValue::Bool(false),
        };
        Ok(Completion::Value(result))
    }

    fn api_doing(&self, payload: &Value) -> Result<Completion<PhpValue>, String> {
        exact_keys(payload, &["api", "hook_hex"], "doing_filter_call")?;
        let (raw, hook) = Self::parse_hook(payload, "hook_hex", true)?;
        let name = if payload.get("hook_hex").is_some_and(Value::is_null) {
            None
        } else {
            Some(hook.as_str())
        };
        let _ = raw;
        Ok(Completion::Value(PhpValue::Bool(
            self.dispatcher.doing_hook(name),
        )))
    }

    fn api_did(&self, payload: &Value) -> Result<Completion<PhpValue>, String> {
        exact_keys(payload, &["api", "hook_hex"], "did_filter_call")?;
        let (_, hook) = Self::parse_hook(payload, "hook_hex", false)?;
        let count = self.dispatcher.did_filter(&hook);
        let count = i64::try_from(count).map_err(|_| "did_filter_count_overflow")?;
        Ok(Completion::Value(PhpValue::Integer(count)))
    }

    fn api_apply(
        &mut self,
        payload: &Value,
        operation: OperationId,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        exact_keys(
            payload,
            &["api", "hook_hex", "value", "args"],
            "apply_filters_call",
        )?;
        let (_, hook) = Self::parse_hook(payload, "hook_hex", false)?;
        let value = parse_pod(
            payload.get("value").ok_or("apply_value_missing")?,
            "apply_value",
        )?;
        let extra = payload
            .get("args")
            .and_then(Value::as_array)
            .ok_or("apply_args")?
            .iter()
            .enumerate()
            .map(|(i, value)| parse_pod(value, &format!("apply_arg_{i}")))
            .collect::<Result<Vec<_>, _>>()?;
        let ticket = self
            .dispatcher
            .begin_filter(&hook, value, &extra)
            .map_err(|error| format!("begin_filter:{error:?}"))?;
        self.drive_dispatch(ticket, operation, parent_wire)
    }

    fn drive_dispatch(
        &mut self,
        ticket: DispatchTicket,
        operation: OperationId,
        caller_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        loop {
            match self
                .dispatcher
                .advance(ticket)
                .map_err(|error| format!("advance_filter:{error:?}"))?
            {
                DispatchStep::InvokePhp(invocation) => {
                    self.check_invocable(invocation.handle, "callback")?;
                    self.callback_invocations = self
                        .callback_invocations
                        .checked_add(1)
                        .ok_or("callback_count_overflow")?;
                    let effect = self
                        .continuation
                        .invoke_php(operation)
                        .map_err(|error| format!("invoke_callback:{error:?}"))?;
                    let effect_wire = self.fresh_effect("callback");
                    let hook = raw_hook(&invocation.hook, "callback_hook")?;
                    let mut args = invocation.args.clone();
                    if invocation.call_mode == PhpCallMode::All {
                        let first = args
                            .first_mut()
                            .ok_or("all_callback_missing_hook_argument")?;
                        *first = PhpValue::Bytes(hook.clone());
                    }
                    let args = args
                        .iter()
                        .enumerate()
                        .map(|(i, value)| pod(value, &format!("callback_arg_{i}")))
                        .collect::<Result<Vec<_>, _>>()?;
                    let call_mode = match invocation.call_mode {
                        PhpCallMode::Zero => "zero",
                        PhpCallMode::Array => "array",
                        PhpCallMode::All => "all",
                    };
                    self.wire.send("invoke_php", Some(caller_wire), json!({
                        "operation":"callable","effect_id":effect_wire,"handle":descriptor(invocation.handle),
                        "hook_hex":encode_hex(&hook),"priority":invocation.priority,"call_mode":call_mode,"args":args,
                    }))?;
                    let completion = self.drive_effect(
                        &effect_wire,
                        effect,
                        Some(caller_wire),
                        EffectValueMode::Pod,
                    )?;
                    self.dispatcher
                        .resume_php_completion(ticket, completion)
                        .map_err(|error| format!("resume_callback:{error:?}"))?;
                }
                DispatchStep::ReleasePhp(handle) => {
                    let completion = self.release_effect(handle, operation, Some(caller_wire))?;
                    self.dispatcher
                        .resume_php_release_completion(ticket, handle, completion)
                        .map_err(|error| format!("resume_filter_release:{error:?}"))?;
                }
                DispatchStep::Complete(value) => return Ok(Completion::Value(value)),
                DispatchStep::PhpThrowable(handle) => return Ok(Completion::PhpThrowable(handle)),
            }
        }
    }

    fn run_control(
        &mut self,
        operation_cursor: &mut ControlOperation,
        operation: OperationId,
        caller_wire: &str,
        label: &str,
    ) -> Result<ControlOutcome, String> {
        loop {
            match self
                .dispatcher
                .advance_control(operation_cursor)
                .map_err(|error| format!("{label}_advance_control:{error:?}"))?
            {
                ControlStep::ReleasePhp(handle) => {
                    let completion = self.release_effect(handle, operation, Some(caller_wire))?;
                    self.dispatcher
                        .resume_control_release_completion(operation_cursor, handle, completion)
                        .map_err(|error| format!("{label}_resume_release:{error:?}"))?;
                }
                ControlStep::Complete(result) => return Ok(ControlOutcome::Result(result)),
                ControlStep::PhpThrowable(handle) => return Ok(ControlOutcome::Throwable(handle)),
            }
        }
    }

    fn release_effect(
        &mut self,
        handle: PhpHandle,
        operation: OperationId,
        caller_wire: Option<&str>,
    ) -> Result<Completion<PhpValue>, String> {
        if handle.kind() != PhpHandleKind::Callable
            || self.handles.get(&handle) != Some(&HandleState::Active)
        {
            return Err("release_handle_not_active_callable".into());
        }
        self.handles.insert(handle, HandleState::ReleasePending);
        self.release_effects = self
            .release_effects
            .checked_add(1)
            .ok_or("release_count_overflow")?;
        let effect = self
            .continuation
            .invoke_php(operation)
            .map_err(|error| format!("invoke_release:{error:?}"))?;
        let effect_wire = self.fresh_effect("release");
        self.wire.send("invoke_php", caller_wire, json!({"operation":"release_callable","effect_id":effect_wire,"handle":descriptor(handle)}))?;
        let completion = self.drive_effect(
            &effect_wire,
            effect,
            caller_wire,
            EffectValueMode::ReleaseAck,
        )?;
        match &completion {
            Completion::Value(PhpValue::Array(values)) => {
                if values.get(&PhpKey::Bytes(b"released".to_vec())) != Some(&PhpValue::Bool(true)) {
                    return Err("release_ack_mismatch".into());
                }
            }
            Completion::Value(_) => return Err("release_ack_schema".into()),
            Completion::PhpThrowable(handle)
                if self.handles.get(handle) == Some(&HandleState::Throwable) => {}
            Completion::PhpThrowable(_) => return Err("release_throwable_not_registered".into()),
        }
        self.continuation
            .unregister_handle(handle)
            .map_err(|error| format!("unregister_released_handle:{error:?}"))?;
        self.handles.remove(&handle);
        Ok(completion)
    }
}

pub(super) fn run_callables_peer<R: BufRead, W: Write>(
    mut wire: Wire<R, W>,
) -> Result<Value, String> {
    let request = RequestGeneration::new(REQUEST_GENERATION);
    let mut continuation = Continuation::<Value>::new(request, LimitsProfile::default())
        .map_err(|error| format!("callables_continuation:{error:?}"))?;
    let start = wire.receive("start_root", None)?;
    exact_keys(
        &start,
        &["operation", "request_generation"],
        "callables_start",
    )?;
    if string(&start, "operation")? != "callable_api"
        || string(&start, "request_generation")? != "61"
    {
        return Err("callables_start_operation_or_generation".into());
    }
    let root = continuation
        .start_root()
        .map_err(|error| format!("callables_start_root:{error:?}"))?;
    let script_effect = continuation
        .invoke_php(root)
        .map_err(|error| format!("callables_script_effect:{error:?}"))?;
    let mut dispatcher = Dispatcher::new();
    let mut peer = CallablePeer {
        wire: &mut wire,
        continuation: &mut continuation,
        dispatcher: &mut dispatcher,
        request,
        handles: HashMap::new(),
        throwable_diagnostics: HashMap::new(),
        effect_ids: HashMap::new(),
        hook_names: HashSet::new(),
        api_counts: API_NAMES
            .into_iter()
            .map(|name| (name.to_owned(), 0))
            .collect(),
        callback_invocations: 0,
        release_effects: 0,
        next_effect: 1,
        registration_epoch: 0,
    };
    peer.wire.send(
        "invoke_php",
        None,
        json!({"operation":"run_script","effect_id":SCRIPT_EFFECT}),
    )?;
    peer.effect_ids.insert(SCRIPT_EFFECT.into(), script_effect);
    let observation = loop {
        let (kind, payload) = peer.wire.receive_one_of(&[
            FrameExpectation {
                kind: "call_core",
                parent_effect: Some(SCRIPT_EFFECT),
            },
            FrameExpectation {
                kind: "resume",
                parent_effect: None,
            },
        ])?;
        if kind == "call_core" {
            let completion = peer.process_call_core(&payload, SCRIPT_EFFECT)?;
            let api_name = string(&payload, "api")?;
            let value = peer.completion_json(&completion, "script_call_return")?;
            peer.wire.send(
                "return",
                Some(SCRIPT_EFFECT),
                json!({"api":api_name,"completion":value}),
            )?;
            continue;
        }
        exact_keys(&payload, &["effect_id", "completion"], "script_resume")?;
        if string(&payload, "effect_id")? != SCRIPT_EFFECT {
            return Err("script_effect_mismatch".into());
        }
        let completion = payload
            .get("completion")
            .ok_or("script_completion_missing")?;
        exact_keys(completion, &["kind", "value"], "script_completion")?;
        if string(completion, "kind")? != "value" {
            return Err("script_throwable_not_expected".into());
        }
        let observation = completion
            .get("value")
            .ok_or("script_observation_missing")?
            .clone();
        if !observation.is_object() {
            return Err("script_observation_must_be_object".into());
        }
        let resumed = peer
            .continuation
            .resume(script_effect, Completion::Value(observation.clone()))
            .map_err(|error| format!("script_resume:{error:?}"))?;
        if resumed != Completion::Value(observation.clone()) {
            return Err("script_resume_changed_observation".into());
        }
        peer.effect_ids.remove(SCRIPT_EFFECT);
        break observation;
    };
    peer.continuation
        .finish_operation(root)
        .map_err(|error| format!("finish_script:{error:?}"))?;

    // The root operation remains open for request-owned release effects. Each
    // release is its own root effect; destructor CallCore frames are children
    // of that release effect and never of an already-resumed script effect.
    let mut cleanup_complete = false;
    for _ in 0..MAX_RELEASE_PASSES {
        let epoch_before = peer.registration_epoch;
        let names: Vec<String> = peer.hook_names.iter().cloned().collect();
        for name in names {
            let mut control = peer
                .dispatcher
                .begin_remove_all(&name, None)
                .map_err(|error| format!("cleanup_begin_remove_all:{error:?}"))?;
            loop {
                match peer
                    .dispatcher
                    .advance_control(&mut control)
                    .map_err(|error| format!("cleanup_advance:{error:?}"))?
                {
                    ControlStep::ReleasePhp(handle) => {
                        let completion = peer.release_effect(handle, root, None)?;
                        if !matches!(completion, Completion::Value(_)) {
                            return Err("cleanup_release_throwable".into());
                        }
                        peer.dispatcher
                            .resume_control_release(&mut control, handle)
                            .map_err(|error| format!("cleanup_resume_release:{error:?}"))?;
                    }
                    ControlStep::PhpThrowable(_) => return Err("cleanup_control_throwable".into()),
                    ControlStep::Complete(ControlResult::RemovedAll) => break,
                    ControlStep::Complete(_) => return Err("cleanup_control_result".into()),
                }
            }
        }
        let has_active = peer
            .handles
            .values()
            .any(|state| *state == HandleState::Active);
        if !has_active && peer.registration_epoch == epoch_before {
            cleanup_complete = true;
            break;
        }
    }
    if !cleanup_complete
        || peer
            .handles
            .values()
            .any(|state| *state == HandleState::Active)
    {
        return Err("cleanup_handle_limit".into());
    }
    let close_effect = peer
        .continuation
        .invoke_php(root)
        .map_err(|error| format!("close_registry_effect:{error:?}"))?;
    let close_wire = peer.fresh_effect("close");
    peer.wire.send(
        "invoke_php",
        None,
        json!({"operation":"close_registry","effect_id":close_wire}),
    )?;
    let close = peer.drive_effect(
        &close_wire,
        close_effect,
        None,
        EffectValueMode::RegistryObservation,
    )?;
    let registry_observation = match close {
        Completion::Value(PhpValue::Array(values)) => {
            let closed =
                values.get(&PhpKey::Bytes(b"closed".to_vec())) == Some(&PhpValue::Bool(true));
            let live = values.get(&PhpKey::Bytes(b"live_after_close".to_vec()))
                == Some(&PhpValue::Integer(0));
            let read = values.get(&PhpKey::Bytes(b"closed_read_rejected".to_vec()))
                == Some(&PhpValue::Bool(true));
            if !closed || !live || !read {
                return Err("registry_close_observation_mismatch".into());
            }
            json!({"closed":true,"live_after_close":0,"closed_read_rejected":true})
        }
        _ => return Err("registry_close_observation_schema".into()),
    };
    peer.continuation
        .finish_operation(root)
        .map_err(|error| format!("finish_root:{error:?}"))?;
    peer.wire.send(
        "return",
        None,
        json!({"operation":"callable_api","status":"ok"}),
    )?;
    let bytes_before_finished = peer.wire.total;
    let finished = peer.wire.receive("request_finished", None)?;
    exact_keys(
        &finished,
        &[
            "status",
            "php_observation",
            "registry_observation",
            "transport",
        ],
        "callable_request_finished",
    )?;
    if string(&finished, "status")? != "ok"
        || finished.get("php_observation") != Some(&observation)
        || finished.get("registry_observation") != Some(&registry_observation)
    {
        return Err("callable_request_finished_observation_mismatch".into());
    }
    exact_keys(
        finished
            .get("transport")
            .ok_or("callable_transport_missing")?,
        &["frames_sent", "frames_received", "bytes_total"],
        "callable_transport",
    )?;
    let transport = finished
        .get("transport")
        .ok_or("callable_transport_missing")?;
    let expected_sent = peer
        .wire
        .in_seq
        .checked_sub(1)
        .ok_or("callable_sent_count")?;
    if transport.get("frames_sent").and_then(Value::as_u64) != Some(expected_sent)
        || transport.get("frames_received").and_then(Value::as_u64) != Some(peer.wire.out_seq)
        || transport.get("bytes_total").and_then(Value::as_u64)
            != Some(bytes_before_finished as u64)
    {
        return Err("callable_transport_stats_mismatch".into());
    }
    let mut extra = Vec::new();
    if (&mut peer.wire.reader)
        .take((MAX_FRAME + 1) as u64)
        .read_until(b'\n', &mut extra)
        .map_err(|error| format!("callable_eof:{error}"))?
        != 0
    {
        return Err("callable_extra_bytes_after_finished".into());
    }
    peer.continuation
        .finish_request(root)
        .map_err(|error| format!("callable_finish_request:{error:?}"))?;
    let late_resume_rejected = peer
        .continuation
        .resume(script_effect, Completion::Value(Value::Null))
        .is_err();
    let late_call_rejected = peer.continuation.begin_child(script_effect).is_err();
    if !late_resume_rejected || !late_call_rejected {
        return Err("late_operation_was_accepted".into());
    }
    let api_counts = Value::Object(
        peer.api_counts
            .iter()
            .map(|(key, value)| (key.clone(), json!(value)))
            .collect(),
    );
    let summary = json!({
        "scenario":"callable_api","continuation_peer":true,
        "frames_from_php":peer.wire.in_seq,"frames_from_rust":peer.wire.out_seq,
        "api_counts":api_counts,"callback_invocations":peer.callback_invocations,
        "release_effects":peer.release_effects,"late_resume_rejected":late_resume_rejected,
        "late_call_rejected":late_call_rejected,"continuation_depth_after":peer.continuation.depth(),
        "state_after":format!("{:?}",peer.continuation.state()),"php_observation":observation,
        "registry_observation":registry_observation,
    });
    if peer.continuation.depth() != 0 || peer.continuation.state() != &RequestState::Finished {
        return Err("callable_final_continuation_state".into());
    }
    eprintln!("V06_CALLABLES_RESULT {summary}");
    eprintln!("V06_CALLABLES_OK continuation_peer=true");
    Ok(summary)
}
