//! Standard PHP hook API requests driven by the request-local Rust Dispatcher.

use super::*;
use recastpress_wp::{
    continuation::{
        Completion, Continuation, EffectId, LimitsProfile, OperationId, PhpHandle, PhpHandleKind,
        RequestGeneration, RequestState,
    },
    dispatcher::{
        ControlOperation, ControlResult, ControlStep, DispatchBoundary, DispatchStep,
        DispatchTicket, Dispatcher, FilterCounterLookup, FilterCounterMutation,
        FilterCounterOperandAccess, FilterCounterResult, HookBindingLookupResult,
    },
    hooks::{CallbackId, ContextCallback, HookArgument, HookPriority, HookScalar, PhpCallMode},
    value::{PhpArray, PhpKey, PhpValue},
};
use std::collections::{BTreeMap, HashMap};

const REQUEST_GENERATION: u64 = 71;
const SCRIPT_EFFECT: &str = "e-script";
const FILTER_API_NAMES: [&str; 7] = [
    "add_filter",
    "remove_filter",
    "has_filter",
    "apply_filters",
    "current_filter",
    "doing_filter",
    "did_filter",
];
const API_NAMES: [&str; 14] = [
    "add_filter",
    "add_action",
    "remove_filter",
    "remove_action",
    "has_filter",
    "has_action",
    "apply_filters",
    "do_action",
    "current_filter",
    "current_action",
    "doing_filter",
    "doing_action",
    "did_filter",
    "did_action",
];
const MAX_RELEASE_PASSES: usize = 64;
const NESTED_CAUGHT_PARENT_HOOK: &str = "callable-action-nested-caught-parent";
const NESTED_CAUGHT_CHILD_HOOK: &str = "callable-action-nested-caught-child";

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

fn validate_action_counter_value(value: PhpValue, label: &str) -> Result<PhpValue, String> {
    match value {
        count @ (PhpValue::Integer(_) | PhpValue::Bool(_) | PhpValue::Bytes(_)) => Ok(count),
        count @ PhpValue::Float(value) if value.is_finite() => Ok(count),
        PhpValue::Float(_) => Err(format!("{label}_not_finite")),
        _ => Err(format!("{label}_not_number")),
    }
}

fn action_counter_increment_ack(value: PhpValue) -> Result<Completion<PhpValue>, String> {
    validate_action_counter_value(value, "action_counter_increment")?;
    Ok(Completion::Value(PhpValue::Null))
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

fn hook_scalar_from_value(value: PhpValue, label: &str) -> Result<HookScalar, String> {
    match value {
        PhpValue::Null => Ok(HookScalar::Null),
        PhpValue::Bool(value) => Ok(HookScalar::Bool(value)),
        PhpValue::Integer(value) => Ok(HookScalar::Integer(value)),
        PhpValue::Float(value) => Ok(HookScalar::Float(value)),
        PhpValue::Bytes(value) => Ok(HookScalar::Bytes(value)),
        PhpValue::Array(_) | PhpValue::Unsupported => Err(format!("{label}_not_scalar")),
    }
}

fn parse_php_key(value: &Value, label: &str) -> Result<PhpKey, String> {
    exact_keys(value, &["type", "hex"], label)?;
    let bytes = decode_hex(string(value, "hex")?, label)?;
    match string(value, "type")? {
        "int" => {
            let text = std::str::from_utf8(&bytes).map_err(|_| format!("{label}_int_utf8"))?;
            let number = text
                .parse::<i64>()
                .map_err(|_| format!("{label}_int_schema"))?;
            if number.to_string() != text {
                return Err(format!("{label}_int_canonical"));
            }
            Ok(PhpKey::Integer(number))
        }
        "string" => match PhpKey::from_bytes(&bytes) {
            PhpKey::Bytes(normalized) if normalized == bytes => Ok(PhpKey::Bytes(bytes)),
            PhpKey::Integer(_) => Err(format!("{label}_string_key_not_normalized")),
            PhpKey::Bytes(_) => unreachable!(),
        },
        _ => Err(format!("{label}_type")),
    }
}

fn php_key_json(key: &PhpKey) -> Value {
    match key {
        PhpKey::Integer(value) => {
            json!({"type":"int","hex":encode_hex(value.to_string().as_bytes())})
        }
        PhpKey::Bytes(value) => json!({"type":"string","hex":encode_hex(value)}),
    }
}

fn php_key_value(key: &PhpKey) -> PhpValue {
    match key {
        PhpKey::Integer(value) => PhpValue::Integer(*value),
        PhpKey::Bytes(value) => PhpValue::Bytes(value.clone()),
    }
}

fn scalar_pod(value: &HookScalar, label: &str) -> Result<Value, String> {
    pod(&value.as_php_value(), label)
}

fn same_scalar_representation(left: &HookScalar, right: &HookScalar) -> bool {
    match (left, right) {
        (HookScalar::Null, HookScalar::Null) => true,
        (HookScalar::Bool(left), HookScalar::Bool(right)) => left == right,
        (HookScalar::Integer(left), HookScalar::Integer(right)) => left == right,
        (HookScalar::Float(left), HookScalar::Float(right)) => left.to_bits() == right.to_bits(),
        (HookScalar::Bytes(left), HookScalar::Bytes(right)) => left == right,
        _ => false,
    }
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
        "argument" => PhpHandleKind::Argument,
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
        PhpHandleKind::Argument => "argument",
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

enum BoundaryCompletion {
    Value(Value),
    PhpThrowable(PhpHandle),
}

#[derive(Clone, Copy)]
enum BoundaryEffectCount {
    Regular,
    ArgumentRelease,
}

struct CallablePeer<'a, R, W> {
    wire: &'a mut Wire<R, W>,
    continuation: &'a mut Continuation<Value>,
    dispatcher: &'a mut Dispatcher,
    request: RequestGeneration,
    handles: HashMap<PhpHandle, HandleState>,
    slot_generation_high_water: HashMap<u32, u32>,
    throwable_diagnostics: HashMap<PhpHandle, (String, String)>,
    nested_caught_child_throwable: Option<PhpHandle>,
    effect_ids: HashMap<String, EffectId>,
    api_arguments: Vec<HashMap<String, PhpHandle>>,
    api_action_argument_counts: Vec<Option<usize>>,
    hook_names: Vec<HookArgument>,
    api_counts: BTreeMap<String, u64>,
    callback_invocations: u64,
    release_effects: u64,
    boundary_effects: u64,
    argument_release_effects: u64,
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
            Some(HandleState::Active)
                if matches!(kind, PhpHandleKind::Callable | PhpHandleKind::Argument) =>
            {
                return Err(format!("{label}_handle_reused"));
            }
            Some(HandleState::ReleasePending) => {
                return Err(format!("{label}_handle_release_pending"));
            }
            Some(HandleState::Throwable) if kind == PhpHandleKind::Throwable => return Ok(handle),
            Some(_) => return Err(format!("{label}_handle_state")),
            None => {}
        }
        for (existing, state) in &self.handles {
            if *existing == handle || existing.slot() != handle.slot() {
                continue;
            }
            match state {
                HandleState::ReleasePending if handle.generation() > existing.generation() => {}
                HandleState::ReleasePending => {
                    return Err(format!("{label}_slot_generation"));
                }
                HandleState::Active | HandleState::Throwable => {
                    return Err(format!("{label}_slot_collision"));
                }
            }
        }
        if self
            .slot_generation_high_water
            .get(&handle.slot())
            .is_some_and(|generation| handle.generation() <= *generation)
        {
            return Err(format!("{label}_slot_generation"));
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
        self.slot_generation_high_water
            .insert(handle.slot(), handle.generation());
        Ok(handle)
    }

    fn register_argument(&mut self, value: &Value, label: &str) -> Result<PhpHandle, String> {
        let handle = self.register_handle(value, PhpHandleKind::Argument, label)?;
        if self.handles.get(&handle) != Some(&HandleState::Active) {
            return Err(format!("{label}_argument_not_active"));
        }
        Ok(handle)
    }

    fn current_argument(&self, name: &str) -> Result<PhpHandle, String> {
        self.api_arguments
            .last()
            .and_then(|arguments| arguments.get(name))
            .copied()
            .ok_or_else(|| format!("argument_handle_{name}_missing"))
    }

    fn remember_hook(&mut self, hook: HookArgument) {
        if !self
            .hook_names
            .iter()
            .any(|existing| existing.array_key == hook.array_key)
        {
            self.hook_names.push(hook);
        }
    }

    fn register_api_arguments(
        &mut self,
        payload: &Value,
        api: &str,
    ) -> Result<Vec<(String, PhpHandle)>, String> {
        let names: &[&str] = match api {
            "add_filter" | "add_action" => &["hook", "callback", "priority", "accepted_args"],
            "remove_filter" | "remove_action" => &["hook", "callback", "priority"],
            "has_filter" | "has_action" => &["hook", "callback"],
            "apply_filters" | "do_action" | "doing_filter" | "doing_action" | "did_filter"
            | "did_action" => &["hook"],
            "current_filter" | "current_action" => &[],
            _ => return Err("unknown_api_arguments".into()),
        };
        if names.is_empty() {
            if payload.get("arguments").is_some() {
                return Err("unexpected_api_arguments".into());
            }
            return Ok(Vec::new());
        }
        let values = payload.get("arguments").ok_or("api_arguments_missing")?;
        exact_keys(values, names, "api_arguments")?;
        let mut registered = names
            .iter()
            .map(|name| {
                let descriptor = values
                    .get(*name)
                    .ok_or_else(|| format!("api_argument_{name}_missing"))?;
                self.register_argument(descriptor, &format!("api_argument_{name}"))
                    .map(|handle| ((*name).to_owned(), handle))
            })
            .collect::<Result<Vec<_>, String>>()?;
        if api == "do_action" {
            let action_args = payload
                .get("args")
                .and_then(Value::as_array)
                .ok_or("do_action_args")?;
            for (index, marker) in action_args.iter().enumerate() {
                let label = format!("do_action_arg_{index}");
                exact_keys(marker, &["type", "handle"], &label)?;
                if string(marker, "type")? != "handle" {
                    return Err(format!("{label}_marker_type"));
                }
                let descriptor = marker
                    .get("handle")
                    .ok_or_else(|| format!("{label}_handle_missing"))?;
                let handle = self.register_argument(descriptor, &label)?;
                registered.push((format!("action_arg_{index}"), handle));
            }
        }
        Ok(registered)
    }

    fn action_argument_handles(&self) -> Result<Option<Vec<PhpHandle>>, String> {
        let Some(count) = self.api_action_argument_counts.last().copied().flatten() else {
            return Ok(None);
        };
        let frame = self
            .api_arguments
            .last()
            .ok_or("action_api_argument_frame_missing")?;
        let mut handles = Vec::with_capacity(count);
        for index in 0..count {
            let name = format!("action_arg_{index}");
            let handle = frame
                .get(&name)
                .copied()
                .ok_or_else(|| format!("{name}_missing"))?;
            self.check_argument(handle, &name)?;
            handles.push(handle);
        }
        for name in frame.keys().filter(|name| name.starts_with("action_arg_")) {
            let suffix = name
                .strip_prefix("action_arg_")
                .ok_or("action_arg_name_prefix")?;
            let index = suffix
                .parse::<usize>()
                .map_err(|_| "action_arg_index_schema")?;
            if index.to_string() != suffix || index >= count {
                return Err("action_arg_order_or_count".into());
            }
        }
        Ok(Some(handles))
    }

    fn encode_callback_arguments(
        &self,
        call_mode: PhpCallMode,
        args: &[PhpValue],
    ) -> Result<Vec<Value>, String> {
        let Some(handles) = self.action_argument_handles()? else {
            return args
                .iter()
                .enumerate()
                .map(|(index, value)| pod(value, &format!("callback_arg_{index}")))
                .collect();
        };
        if call_mode == PhpCallMode::Array
            && handles.is_empty()
            && args.len() == 1
            && matches!(&args[0], PhpValue::Bytes(bytes) if bytes.is_empty())
        {
            return Ok(vec![pod(&args[0], "action_empty_argument_placeholder")?]);
        }
        let (prefix, start) = match call_mode {
            PhpCallMode::All => {
                if args.len() != handles.len() + 1 {
                    return Err("action_all_argument_count".into());
                }
                (Some(pod(&args[0], "action_all_hook_prefix")?), 1)
            }
            PhpCallMode::Zero => {
                if !args.is_empty() {
                    return Err("action_zero_argument_count".into());
                }
                (None, 0)
            }
            PhpCallMode::Array => {
                if handles.is_empty() {
                    if args.len() > 1
                        || args
                            .first()
                            .is_some_and(|value| *value != PhpValue::Bytes(Vec::new()))
                    {
                        return Err("action_empty_argument_placeholder".into());
                    }
                } else if args.len() > handles.len() {
                    return Err("action_named_argument_count".into());
                }
                (None, 0)
            }
        };
        let mut encoded = Vec::with_capacity(args.len());
        if let Some(prefix) = prefix {
            encoded.push(prefix);
        }
        for (offset, value) in args.iter().skip(start).enumerate() {
            let handle = handles
                .get(offset)
                .copied()
                .ok_or("action_arg_order_or_count")?;
            if *value != PhpValue::Unsupported {
                return Err("action_arg_placeholder_expected".into());
            }
            encoded.push(json!({"type":"handle","handle":descriptor(handle)}));
        }
        Ok(encoded)
    }

    fn release_arguments(
        &mut self,
        arguments: &[(String, PhpHandle)],
        operation: OperationId,
        caller_wire: &str,
    ) -> Result<Option<PhpHandle>, String> {
        if arguments.is_empty() {
            return Ok(None);
        }
        for (_, handle) in arguments {
            if handle.kind() != PhpHandleKind::Argument
                || self.handles.get(handle) != Some(&HandleState::Active)
            {
                return Err("release_arguments_handle_not_active".into());
            }
            self.continuation
                .validate_handle(*handle)
                .map_err(|error| format!("release_arguments_mirror:{error:?}"))?;
        }
        for (_, handle) in arguments {
            self.handles.insert(*handle, HandleState::ReleasePending);
        }
        let handles = arguments
            .iter()
            .map(|(_, handle)| descriptor(*handle))
            .collect::<Vec<_>>();
        let result = self.drive_boundary(
            "release_arguments",
            json!({"handles":handles}),
            operation,
            caller_wire,
            BoundaryEffectCount::ArgumentRelease,
        )?;
        match result {
            BoundaryCompletion::Value(value) => {
                exact_keys(&value, &["released"], "release_arguments_result")?;
                if value.get("released") != Some(&Value::Bool(true)) {
                    return Err("release_arguments_ack".into());
                }
                for (_, handle) in arguments {
                    self.continuation
                        .unregister_handle(*handle)
                        .map_err(|error| format!("unregister_argument:{error:?}"))?;
                    self.handles.remove(handle);
                }
                Ok(None)
            }
            BoundaryCompletion::PhpThrowable(handle) => Ok(Some(handle)),
        }
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

    fn drive_boundary(
        &mut self,
        action: &str,
        arguments: Value,
        operation: OperationId,
        caller_wire: &str,
        count_as: BoundaryEffectCount,
    ) -> Result<BoundaryCompletion, String> {
        let effect = self
            .continuation
            .invoke_php(operation)
            .map_err(|error| format!("invoke_boundary:{error:?}"))?;
        let effect_wire = self.fresh_effect("boundary");
        if self
            .effect_ids
            .insert(effect_wire.clone(), effect)
            .is_some()
        {
            return Err("boundary_effect_wire_id_reused".into());
        }
        match count_as {
            BoundaryEffectCount::Regular => {
                self.boundary_effects = self
                    .boundary_effects
                    .checked_add(1)
                    .ok_or("boundary_effect_count_overflow")?;
            }
            BoundaryEffectCount::ArgumentRelease => {
                self.argument_release_effects = self
                    .argument_release_effects
                    .checked_add(1)
                    .ok_or("argument_release_effect_count_overflow")?;
            }
        }
        self.wire.send(
            "invoke_php",
            Some(caller_wire),
            json!({
                "operation":"boundary",
                "effect_id":effect_wire,
                "action":action,
                "arguments":arguments,
            }),
        )?;
        loop {
            let (kind, payload) = self.wire.receive_one_of(&[
                FrameExpectation {
                    kind: "call_core",
                    parent_effect: Some(&effect_wire),
                },
                FrameExpectation {
                    kind: "resume",
                    parent_effect: Some(caller_wire),
                },
            ])?;
            if kind == "call_core" {
                let completion = self.process_call_core(&payload, &effect_wire)?;
                let api_name = string(&payload, "api")?;
                let completion = self.completion_json(&completion, "boundary_call_core_return")?;
                self.wire.send(
                    "return",
                    Some(&effect_wire),
                    json!({"api":api_name,"completion":completion}),
                )?;
                continue;
            }
            exact_keys(&payload, &["effect_id", "completion"], "boundary_resume")?;
            if string(&payload, "effect_id")? != effect_wire {
                return Err("boundary_effect_resume_id_mismatch".into());
            }
            let completion = payload
                .get("completion")
                .ok_or("boundary_completion_missing")?;
            let (continuation_completion, result) = match string(completion, "kind")? {
                "value" => {
                    exact_keys(completion, &["kind", "value"], "boundary_value_completion")?;
                    let result = completion
                        .get("value")
                        .ok_or("boundary_value_missing")?
                        .clone();
                    (
                        Completion::Value(result.clone()),
                        BoundaryCompletion::Value(result),
                    )
                }
                "php_throwable" => {
                    let parsed = self.process_completion(
                        completion,
                        "boundary_throwable_completion",
                        EffectValueMode::Pod,
                    )?;
                    let Completion::PhpThrowable(handle) = parsed else {
                        return Err("boundary_throwable_completion_kind".into());
                    };
                    (
                        Completion::PhpThrowable(handle),
                        BoundaryCompletion::PhpThrowable(handle),
                    )
                }
                _ => return Err("boundary_completion_kind".into()),
            };
            let resumed = self
                .continuation
                .resume(effect, continuation_completion)
                .map_err(|error| format!("boundary_resume:{error:?}"))?;
            self.effect_ids.remove(&effect_wire);
            return match (resumed, result) {
                (Completion::Value(_), value @ BoundaryCompletion::Value(_)) => Ok(value),
                (Completion::PhpThrowable(resumed), BoundaryCompletion::PhpThrowable(expected))
                    if resumed == expected =>
                {
                    Ok(BoundaryCompletion::PhpThrowable(resumed))
                }
                _ => Err("boundary_resume_changed_completion".into()),
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
        let arguments = self.register_api_arguments(payload, &api_name)?;
        let action_argument_count = if api_name == "do_action" {
            Some(
                arguments
                    .iter()
                    .filter(|(name, _)| name.starts_with("action_arg_"))
                    .count(),
            )
        } else {
            None
        };
        self.api_arguments.push(arguments.iter().cloned().collect());
        self.api_action_argument_counts.push(action_argument_count);
        let completion = match api_name.as_str() {
            "add_filter" | "add_action" => self.api_add(payload, operation, parent_wire),
            "remove_filter" | "remove_action" => self.api_remove(payload, operation, parent_wire),
            "has_filter" | "has_action" => self.api_has(payload, operation, parent_wire),
            "apply_filters" => self.api_apply(payload, operation, parent_wire),
            "do_action" => self.api_do_action(payload, operation, parent_wire),
            "current_filter" | "current_action" => self.api_current(payload),
            "doing_filter" | "doing_action" => self.api_doing(payload, operation, parent_wire),
            "did_filter" => self.api_did(payload, operation, parent_wire),
            "did_action" => self.api_did_action(payload, operation, parent_wire),
            _ => Err("call_core_unknown_api".into()),
        }?;
        let release_throwable = self.release_arguments(&arguments, operation, parent_wire)?;
        self.api_arguments
            .pop()
            .ok_or("api_argument_stack_underflow")?;
        self.api_action_argument_counts
            .pop()
            .ok_or("api_action_argument_count_stack_underflow")?;
        let completion = release_throwable
            .map(Completion::PhpThrowable)
            .unwrap_or(completion);
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

    fn boundary_scalar_from_argument(
        &mut self,
        handle: PhpHandle,
        operation: OperationId,
        parent_wire: &str,
        label: &str,
    ) -> Result<Result<Option<HookScalar>, PhpHandle>, String> {
        self.check_argument(handle, label)?;
        let result = self.drive_boundary(
            "hook_scalar",
            json!({"handle":descriptor(handle)}),
            operation,
            parent_wire,
            BoundaryEffectCount::Regular,
        )?;
        match result {
            BoundaryCompletion::PhpThrowable(handle) => Ok(Err(handle)),
            BoundaryCompletion::Value(value) => {
                exact_keys(&value, &["supported", "value"], label)?;
                if value.get("supported") == Some(&Value::Bool(false)) {
                    if value.get("value") != Some(&Value::Null) {
                        return Err(format!("{label}_unsupported_scalar_value"));
                    }
                    return Ok(Ok(None));
                }
                if value.get("supported") != Some(&Value::Bool(true)) {
                    return Err(format!("{label}_supported"));
                }
                let scalar = parse_pod(
                    value.get("value").ok_or("hook_scalar_value_missing")?,
                    label,
                )?;
                let scalar = hook_scalar_from_value(scalar, label)?;
                Ok(Ok(Some(scalar)))
            }
        }
    }

    fn check_argument(&self, handle: PhpHandle, label: &str) -> Result<(), String> {
        if handle.kind() != PhpHandleKind::Argument
            || self.handles.get(&handle) != Some(&HandleState::Active)
        {
            return Err(format!("{label}_argument_not_active"));
        }
        self.continuation
            .validate_handle(handle)
            .map_err(|error| format!("{label}_argument_mirror:{error:?}"))
    }

    fn boundary_value(
        &mut self,
        action: &str,
        arguments: Value,
        operation: OperationId,
        parent_wire: &str,
        label: &str,
    ) -> Result<Result<Value, PhpHandle>, String> {
        let result: Result<Result<Value, PhpHandle>, String> = match self.drive_boundary(
            action,
            arguments,
            operation,
            parent_wire,
            BoundaryEffectCount::Regular,
        )? {
            BoundaryCompletion::Value(value) => Ok(Ok(value)),
            BoundaryCompletion::PhpThrowable(handle) => Ok(Err(handle)),
        };
        result.map_err(|error| format!("{label}:{error}"))
    }

    fn boundary_array_key(
        &mut self,
        handle: PhpHandle,
        access: &str,
        null_to_zero: bool,
        operation: OperationId,
        parent_wire: &str,
        label: &str,
    ) -> Result<Result<(HookScalar, PhpKey), PhpHandle>, String> {
        self.check_argument(handle, label)?;
        let result = self.boundary_value(
            "array_key",
            json!({"handle":descriptor(handle),"access":access,"null_to_zero":null_to_zero}),
            operation,
            parent_wire,
            label,
        )?;
        match result {
            Err(handle) => Ok(Err(handle)),
            Ok(value) => {
                exact_keys(&value, &["original", "key"], label)?;
                let original = parse_pod(
                    value
                        .get("original")
                        .ok_or_else(|| format!("{label}_original_missing"))?,
                    label,
                )?;
                let original = hook_scalar_from_value(original, label)?;
                let key = parse_php_key(
                    value
                        .get("key")
                        .ok_or_else(|| format!("{label}_key_missing"))?,
                    label,
                )?;
                Ok(Ok((original, key)))
            }
        }
    }

    fn boundary_callback_id(
        &mut self,
        handle: PhpHandle,
        operation: OperationId,
        parent_wire: &str,
        label: &str,
    ) -> Result<Result<Option<CallbackId>, PhpHandle>, String> {
        self.check_argument(handle, label)?;
        let result = self.boundary_value(
            "callback_id",
            json!({"handle":descriptor(handle)}),
            operation,
            parent_wire,
            label,
        )?;
        match result {
            Err(handle) => Ok(Err(handle)),
            Ok(value) => {
                exact_keys(&value, &["key"], label)?;
                let key = callback_key(
                    value
                        .get("key")
                        .ok_or_else(|| format!("{label}_key_missing"))?,
                    label,
                )?;
                Ok(Ok(key))
            }
        }
    }

    fn boundary_accepted_args(
        &mut self,
        handle: PhpHandle,
        operation: OperationId,
        parent_wire: &str,
        label: &str,
    ) -> Result<Result<i64, PhpHandle>, String> {
        self.check_argument(handle, label)?;
        let result = self.boundary_value(
            "accepted_args",
            json!({"handle":descriptor(handle)}),
            operation,
            parent_wire,
            label,
        )?;
        match result {
            Err(handle) => Ok(Err(handle)),
            Ok(value) => {
                let cast = parse_pod(&value, label)?;
                match cast {
                    PhpValue::Integer(value) => Ok(Ok(value)),
                    _ => Err(format!("{label}_result_not_integer")),
                }
            }
        }
    }

    fn boundary_retain_callable(
        &mut self,
        handle: PhpHandle,
        operation: OperationId,
        parent_wire: &str,
        label: &str,
    ) -> Result<Result<PhpHandle, PhpHandle>, String> {
        self.check_argument(handle, label)?;
        let result = self.boundary_value(
            "retain_callable",
            json!({"handle":descriptor(handle)}),
            operation,
            parent_wire,
            label,
        )?;
        match result {
            Err(handle) => Ok(Err(handle)),
            Ok(value) => {
                exact_keys(&value, &["handle"], label)?;
                let handle = self.register_handle(
                    value
                        .get("handle")
                        .ok_or_else(|| format!("{label}_handle_missing"))?,
                    PhpHandleKind::Callable,
                    label,
                )?;
                Ok(Ok(handle))
            }
        }
    }

    fn api_add(
        &mut self,
        payload: &Value,
        operation: OperationId,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        exact_keys(payload, &["api", "arguments"], "add_filter_call")?;
        let argument = self.current_argument("hook")?;
        let (_hook, target) = match self.hook_target_for_api(
            argument,
            "add_filter",
            true,
            operation,
            parent_wire,
            "add_filter",
        )? {
            Err(handle) => return Ok(Completion::PhpThrowable(handle)),
            Ok(Some(target)) => target,
            Ok(None) => return Err("add_filter_missing_target".into()),
        };

        let callback_handle = self.current_argument("callback")?;
        let completion = match self.boundary_callback_id(
            callback_handle,
            operation,
            parent_wire,
            "add_callback_id",
        )? {
            Err(handle) => Completion::PhpThrowable(handle),
            Ok(None) => Completion::Value(PhpValue::Bool(true)),
            Ok(Some(id)) => {
                let priority_handle = self.current_argument("priority")?;
                let mut existence = self
                    .dispatcher
                    .begin_priority_existence(target)
                    .map_err(|error| format!("add_priority_existence:{error:?}"))?;
                let (priority_original, priority_key) = match self.boundary_array_key(
                    priority_handle,
                    "isset",
                    true,
                    operation,
                    parent_wire,
                    "add_priority_isset",
                )? {
                    Err(handle) => {
                        self.dispatcher
                            .cancel_priority_existence(&mut existence, handle)
                            .map_err(|error| format!("add_cancel_priority_existence:{error:?}"))?;
                        let completion = match self.run_control(
                            &mut existence,
                            operation,
                            parent_wire,
                            "add_cancel_priority_existence",
                        )? {
                            ControlOutcome::Throwable(observed) => {
                                Completion::PhpThrowable(observed)
                            }
                            ControlOutcome::Result(_) => {
                                return Err("add_priority_existence_cancel_completed".into());
                            }
                        };
                        return self.finish_target(
                            target,
                            completion,
                            operation,
                            parent_wire,
                            "add_filter",
                        );
                    }
                    Ok(priority) => priority,
                };
                let priority = HookPriority::new(priority_original, priority_key);
                self.dispatcher
                    .resume_priority_existence(&mut existence, priority.clone())
                    .map_err(|error| format!("add_resume_priority_existence:{error:?}"))?;
                let snapshot = match self.run_control(
                    &mut existence,
                    operation,
                    parent_wire,
                    "add_priority_existence",
                )? {
                    ControlOutcome::Result(ControlResult::PrioritySnapshot(snapshot)) => snapshot,
                    ControlOutcome::Throwable(handle) => {
                        return self.finish_target(
                            target,
                            Completion::PhpThrowable(handle),
                            operation,
                            parent_wire,
                            "add_filter",
                        );
                    }
                    _ => return Err("add_priority_existence_result".into()),
                };
                let accepted_handle = self.current_argument("accepted_args")?;
                let accepted = match self.boundary_accepted_args(
                    accepted_handle,
                    operation,
                    parent_wire,
                    "add_accepted_args",
                )? {
                    Err(handle) => {
                        return self.finish_target(
                            target,
                            Completion::PhpThrowable(handle),
                            operation,
                            parent_wire,
                            "add_filter",
                        );
                    }
                    Ok(accepted) => accepted,
                };
                let mut write = self
                    .dispatcher
                    .begin_priority_write(target)
                    .map_err(|error| format!("add_priority_write:{error:?}"))?;
                match self.boundary_array_key(
                    priority_handle,
                    "write",
                    true,
                    operation,
                    parent_wire,
                    "add_priority_write",
                )? {
                    Err(handle) => {
                        self.dispatcher
                            .cancel_priority_write(&mut write, handle)
                            .map_err(|error| format!("add_cancel_priority_write:{error:?}"))?;
                        let completion = match self.run_control(
                            &mut write,
                            operation,
                            parent_wire,
                            "add_cancel_priority_write",
                        )? {
                            ControlOutcome::Throwable(observed) => {
                                Completion::PhpThrowable(observed)
                            }
                            ControlOutcome::Result(_) => {
                                return Err("add_priority_write_cancel_completed".into());
                            }
                        };
                        return self.finish_target(
                            target,
                            completion,
                            operation,
                            parent_wire,
                            "add_filter",
                        );
                    }
                    Ok((write_original, write_key)) => {
                        if !same_scalar_representation(&write_original, &priority.original)
                            || write_key != *priority.key()
                        {
                            return Err("add_priority_write_changed".into());
                        }
                    }
                }
                self.dispatcher
                    .resume_priority_write(&mut write, priority.clone())
                    .map_err(|error| format!("add_resume_priority_write:{error:?}"))?;
                let token = match self.run_control(
                    &mut write,
                    operation,
                    parent_wire,
                    "add_priority_write",
                )? {
                    ControlOutcome::Result(ControlResult::PriorityWrite(token)) => token,
                    ControlOutcome::Throwable(handle) => {
                        return self.finish_target(
                            target,
                            Completion::PhpThrowable(handle),
                            operation,
                            parent_wire,
                            "add_filter",
                        );
                    }
                    _ => return Err("add_priority_write_result".into()),
                };
                let handle = match self.boundary_retain_callable(
                    callback_handle,
                    operation,
                    parent_wire,
                    "add_retain_callable",
                )? {
                    Err(handle) => {
                        let mut cancel = self
                            .dispatcher
                            .cancel_priority_write_token(token, handle)
                            .map_err(|error| {
                                format!("add_cancel_priority_write_token:{error:?}")
                            })?;
                        let completion = match self.run_control(
                            &mut cancel,
                            operation,
                            parent_wire,
                            "add_cancel_priority_write_token",
                        )? {
                            ControlOutcome::Throwable(observed) => {
                                Completion::PhpThrowable(observed)
                            }
                            ControlOutcome::Result(_) => {
                                return Err("add_priority_write_token_cancel_completed".into());
                            }
                        };
                        return self.finish_target(
                            target,
                            completion,
                            operation,
                            parent_wire,
                            "add_filter",
                        );
                    }
                    Ok(handle) => handle,
                };
                let callback = ContextCallback::php_callable(id, accepted, handle)
                    .map_err(|error| format!("add_callback:{error:?}"))?;
                let mut control = self
                    .dispatcher
                    .begin_add_target_with_priority_write(
                        target, priority, snapshot, token, callback,
                    )
                    .map_err(|error| format!("begin_add_target:{error:?}"))?;
                self.registration_epoch = self
                    .registration_epoch
                    .checked_add(1)
                    .ok_or("registration_epoch_overflow")?;
                match self.run_control(&mut control, operation, parent_wire, "add_filter")? {
                    ControlOutcome::Result(ControlResult::PriorityWriteCommitted) => {
                        Completion::Value(PhpValue::Bool(true))
                    }
                    ControlOutcome::Throwable(handle) => Completion::PhpThrowable(handle),
                    _ => return Err("add_result_mismatch".into()),
                }
            }
        };
        self.finish_target(target, completion, operation, parent_wire, "add_filter")
    }

    fn api_remove(
        &mut self,
        payload: &Value,
        operation: OperationId,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        exact_keys(payload, &["api", "arguments"], "remove_filter_call")?;
        let hook_handle = self.current_argument("hook")?;
        let (hook, target) = match self.hook_target_for_api(
            hook_handle,
            "remove_filter",
            false,
            operation,
            parent_wire,
            "remove_filter",
        )? {
            Err(handle) => return Ok(Completion::PhpThrowable(handle)),
            Ok(None) => return Ok(Completion::Value(PhpValue::Bool(false))),
            Ok(Some(target)) => target,
        };
        let callback_handle = self.current_argument("callback")?;
        let id = match self.boundary_callback_id(
            callback_handle,
            operation,
            parent_wire,
            "remove_callback_id",
        )? {
            Err(handle) => {
                return self.finish_target(
                    target,
                    Completion::PhpThrowable(handle),
                    operation,
                    parent_wire,
                    "remove_filter",
                );
            }
            Ok(None) => None,
            Ok(Some(id)) => Some(id),
        };
        let priority_handle = self.current_argument("priority")?;
        let mut inner_removed = false;
        let mut existence = match id.as_ref() {
            Some(_) => Some(
                self.dispatcher
                    .begin_remove_existence(target)
                    .map_err(|error| format!("remove_begin_existence:{error:?}"))?,
            ),
            None => None,
        };
        let (priority_original, priority_key) = match self.boundary_array_key(
            priority_handle,
            "nested_isset",
            true,
            operation,
            parent_wire,
            "remove_priority_isset",
        )? {
            Err(handle) => {
                let completion = if let Some(mut cursor) = existence.take() {
                    self.dispatcher
                        .cancel_remove_existence(&mut cursor, handle)
                        .map_err(|error| format!("remove_cancel_existence:{error:?}"))?;
                    match self.run_control(
                        &mut cursor,
                        operation,
                        parent_wire,
                        "remove_cancel_existence",
                    )? {
                        ControlOutcome::Throwable(observed) => Completion::PhpThrowable(observed),
                        ControlOutcome::Result(_) => {
                            return Err("remove_existence_cancel_completed".into());
                        }
                    }
                } else {
                    Completion::PhpThrowable(handle)
                };
                return self.finish_target(
                    target,
                    completion,
                    operation,
                    parent_wire,
                    "remove_filter",
                );
            }
            Ok(priority) => priority,
        };
        let priority = HookPriority::new(priority_original, priority_key);
        let snapshot = if let (Some(mut cursor), Some(id)) = (existence.take(), id.as_ref()) {
            self.dispatcher
                .resume_remove_existence(&mut cursor, priority.clone(), id)
                .map_err(|error| format!("remove_resume_existence:{error:?}"))?;
            match self.run_control(&mut cursor, operation, parent_wire, "remove_existence")? {
                ControlOutcome::Result(ControlResult::RemoveExistence(snapshot)) => Some(snapshot),
                ControlOutcome::Throwable(handle) => {
                    return self.finish_target(
                        target,
                        Completion::PhpThrowable(handle),
                        operation,
                        parent_wire,
                        "remove_filter",
                    );
                }
                _ => return Err("remove_existence_result_mismatch".into()),
            }
        } else {
            None
        };
        if let (Some(id), Some(snapshot)) = (id.as_ref(), snapshot)
            && snapshot.exists()
        {
            match self.boundary_array_key(
                priority_handle,
                "unset",
                true,
                operation,
                parent_wire,
                "remove_priority_unset",
            )? {
                Err(handle) => {
                    return self.finish_target(
                        target,
                        Completion::PhpThrowable(handle),
                        operation,
                        parent_wire,
                        "remove_filter",
                    );
                }
                Ok((unset_original, unset_key)) => {
                    if !same_scalar_representation(&unset_original, &priority.original)
                        || unset_key != *priority.key()
                    {
                        return Err("remove_priority_unset_changed".into());
                    }
                }
            }
            let mut control = self
                .dispatcher
                .begin_remove_target_with_snapshot(target, priority.clone(), id, snapshot)
                .map_err(|error| format!("remove_begin_target:{error:?}"))?;
            inner_removed =
                match self.run_control(&mut control, operation, parent_wire, "remove_filter")? {
                    ControlOutcome::Result(ControlResult::Removed(result)) => result,
                    ControlOutcome::Throwable(handle) => {
                        return self.finish_target(
                            target,
                            Completion::PhpThrowable(handle),
                            operation,
                            parent_wire,
                            "remove_filter",
                        );
                    }
                    _ => return Err("remove_result_mismatch".into()),
                };
            if !inner_removed {
                return Err("remove_snapshot_result_changed".into());
            }
        }
        match self.release_hook_target(target, operation, parent_wire, "remove_target")? {
            ControlOutcome::Result(ControlResult::TargetReleased) => {}
            ControlOutcome::Throwable(handle) => return Ok(Completion::PhpThrowable(handle)),
            _ => return Err("remove_target_release_result".into()),
        }
        let (read_hook, read_result) = match self.hook_binding_lookup(
            hook_handle,
            "read",
            operation,
            parent_wire,
            "remove_outer_callbacks_read",
        )? {
            Err(handle) => return Ok(Completion::PhpThrowable(handle)),
            Ok(result) => result,
        };
        if !same_scalar_representation(&read_hook.original, &hook.original)
            || read_hook.array_key != hook.array_key
        {
            return Err("remove_outer_hook_read_changed".into());
        }
        let empty = match read_result {
            HookBindingLookupResult::Found(read_target) => {
                let empty = self
                    .dispatcher
                    .target_callbacks_empty(read_target)
                    .map_err(|error| format!("remove_outer_target_empty:{error:?}"))?;
                match self.release_hook_target(
                    read_target,
                    operation,
                    parent_wire,
                    "remove_outer_read_target_release",
                )? {
                    ControlOutcome::Result(ControlResult::TargetReleased) => {}
                    ControlOutcome::Throwable(handle) => {
                        return Ok(Completion::PhpThrowable(handle));
                    }
                    _ => return Err("remove_outer_read_target_release_result".into()),
                }
                empty
            }
            HookBindingLookupResult::Missing => {
                if let Some(handle) = self.boundary_missing_offset(
                    &hook.array_key,
                    operation,
                    parent_wire,
                    "remove_outer_missing_offset",
                )? {
                    return Ok(Completion::PhpThrowable(handle));
                }
                if let Some(handle) = self.boundary_null_property(
                    "callbacks",
                    operation,
                    parent_wire,
                    "remove_outer_null_callbacks",
                )? {
                    return Ok(Completion::PhpThrowable(handle));
                }
                true
            }
            HookBindingLookupResult::NullOperand => {
                if let Some(handle) = self.boundary_null_property(
                    "callbacks",
                    operation,
                    parent_wire,
                    "remove_outer_null_callbacks",
                )? {
                    return Ok(Completion::PhpThrowable(handle));
                }
                true
            }
        };
        if empty {
            let mut unset_lookup = self
                .dispatcher
                .begin_hook_binding_lookup()
                .map_err(|error| format!("remove_begin_unset_lookup:{error:?}"))?;
            let unset = self.boundary_array_key(
                hook_handle,
                "unset",
                false,
                operation,
                parent_wire,
                "remove_outer_hook_unset",
            )?;
            match unset {
                Err(handle) => {
                    self.dispatcher
                        .cancel_hook_binding_lookup(&mut unset_lookup, handle)
                        .map_err(|error| format!("remove_cancel_unset_lookup:{error:?}"))?;
                    return match self.run_control(
                        &mut unset_lookup,
                        operation,
                        parent_wire,
                        "remove_cancel_unset_lookup",
                    )? {
                        ControlOutcome::Throwable(observed) => {
                            Ok(Completion::PhpThrowable(observed))
                        }
                        ControlOutcome::Result(_) => {
                            Err("remove_unset_lookup_cancel_completed".into())
                        }
                    };
                }
                Ok((unset_original, unset_key)) => {
                    if !same_scalar_representation(&unset_original, &hook.original)
                        || unset_key != hook.array_key
                    {
                        return Err("remove_outer_hook_unset_changed".into());
                    }
                }
            }
            self.dispatcher
                .resume_hook_binding_lookup(&mut unset_lookup, &hook)
                .map_err(|error| format!("remove_resume_unset_lookup:{error:?}"))?;
            let unset_target = match self.run_control(
                &mut unset_lookup,
                operation,
                parent_wire,
                "remove_unset_lookup",
            )? {
                ControlOutcome::Result(ControlResult::HookBindingLookup(result)) => match result {
                    HookBindingLookupResult::Found(target) => Some(target),
                    HookBindingLookupResult::Missing | HookBindingLookupResult::NullOperand => None,
                },
                ControlOutcome::Throwable(handle) => {
                    return Ok(Completion::PhpThrowable(handle));
                }
                _ => return Err("remove_unset_lookup_result".into()),
            };
            let mut unbind = self
                .dispatcher
                .begin_unbind_hook(&hook)
                .map_err(|error| format!("remove_begin_unbind_hook:{error:?}"))?;
            let unbind_completion =
                match self.run_control(&mut unbind, operation, parent_wire, "remove_unbind")? {
                    ControlOutcome::Result(ControlResult::HookUnbound) => {
                        Completion::Value(PhpValue::Null)
                    }
                    ControlOutcome::Throwable(handle) => Completion::PhpThrowable(handle),
                    _ => return Err("remove_unbind_result".into()),
                };
            let unbind_completion = if let Some(unset_target) = unset_target {
                self.release_hook_target_completion(
                    unset_target,
                    unbind_completion,
                    operation,
                    parent_wire,
                    "remove_unset_target_release",
                )?
            } else {
                unbind_completion
            };
            match remove_result_after_unbind(inner_removed, unbind_completion)
                .map_err(str::to_owned)?
            {
                Completion::PhpThrowable(handle) => {
                    return Ok(Completion::PhpThrowable(handle));
                }
                Completion::Value(value) => return Ok(Completion::Value(value)),
            }
        }
        Ok(Completion::Value(PhpValue::Bool(inner_removed)))
    }

    fn api_has(
        &mut self,
        payload: &Value,
        operation: OperationId,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        exact_keys(
            payload,
            &["api", "arguments", "any", "priority"],
            "has_filter_call",
        )?;
        let any = payload
            .get("any")
            .and_then(Value::as_bool)
            .ok_or("has_any")?;
        let priority = match payload.get("priority") {
            Some(Value::Null) => None,
            Some(value) => Some(parse_pod(value, "has_priority")?),
            None => return Err("has_priority_missing".into()),
        };
        let priority = match priority {
            None => None,
            Some(PhpValue::Integer(value)) => Some(value),
            Some(_) => return Err("has_priority_must_be_int_or_null".into()),
        };
        let hook_handle = self.current_argument("hook")?;
        let (_hook, target) = match self.hook_target_for_api(
            hook_handle,
            "has_filter",
            false,
            operation,
            parent_wire,
            "has_filter",
        )? {
            Err(handle) => return Ok(Completion::PhpThrowable(handle)),
            Ok(None) => return Ok(Completion::Value(PhpValue::Bool(false))),
            Ok(Some(target)) => target,
        };
        let result = if any {
            PhpValue::Bool(
                self.dispatcher
                    .has_callbacks_target(target)
                    .map_err(|error| format!("has_callbacks_target:{error:?}"))?,
            )
        } else {
            let callback_handle = self.current_argument("callback")?;
            match self.boundary_callback_id(
                callback_handle,
                operation,
                parent_wire,
                "has_callback_id",
            )? {
                Err(handle) => {
                    return self.finish_target(
                        target,
                        Completion::PhpThrowable(handle),
                        operation,
                        parent_wire,
                        "has_filter",
                    );
                }
                Ok(None) => PhpValue::Bool(false),
                Ok(Some(id)) if id.callback == "i:0" || id.callback == "s:" => {
                    PhpValue::Bool(false)
                }
                Ok(Some(id)) => match priority {
                    Some(priority) => PhpValue::Bool(
                        self.dispatcher
                            .has_at_target(target, &HookPriority::integer(priority), &id)
                            .map_err(|error| format!("has_at_target:{error:?}"))?,
                    ),
                    None => self
                        .dispatcher
                        .priority_of_target(target, &id)
                        .map_err(|error| format!("priority_of_target:{error:?}"))?
                        .map(|key| php_key_value(&key))
                        .unwrap_or(PhpValue::Bool(false)),
                },
            }
        };
        let completion = Completion::Value(result);
        self.finish_target(target, completion, operation, parent_wire, "has_filter")
    }

    fn api_current(&self, payload: &Value) -> Result<Completion<PhpValue>, String> {
        exact_keys(payload, &["api"], "current_filter_call")?;
        let result = match self.dispatcher.current_hook_typed() {
            Some(name) => name.as_php_value(),
            None => PhpValue::Bool(false),
        };
        Ok(Completion::Value(result))
    }

    fn api_doing(
        &mut self,
        payload: &Value,
        operation: OperationId,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        exact_keys(payload, &["api", "arguments", "any"], "doing_filter_call")?;
        let any = payload
            .get("any")
            .and_then(Value::as_bool)
            .ok_or("doing_any_schema")?;
        let handle = self.current_argument("hook")?;
        let scalar = match self.boundary_scalar_from_argument(
            handle,
            operation,
            parent_wire,
            "doing_hook_scalar",
        )? {
            Err(handle) => return Ok(Completion::PhpThrowable(handle)),
            Ok(scalar) => scalar,
        };
        let name = match scalar {
            Some(HookScalar::Null) if any => None,
            Some(HookScalar::Null) => return Err("doing_any_null_mismatch".into()),
            Some(name) if !any => Some(name),
            Some(_) => return Err("doing_any_nonnull_mismatch".into()),
            None if !any => return Ok(Completion::Value(PhpValue::Bool(false))),
            None => return Err("doing_any_unsupported_mismatch".into()),
        };
        Ok(Completion::Value(PhpValue::Bool(
            self.dispatcher.doing_hook_typed(name.as_ref()),
        )))
    }

    fn counter_operand(
        &mut self,
        access: FilterCounterOperandAccess,
        boundary_access: &str,
        handle: PhpHandle,
        operation: OperationId,
        parent_wire: &str,
        label: &str,
    ) -> Result<Result<(HookArgument, FilterCounterResult), PhpHandle>, String> {
        self.check_argument(handle, label)?;
        let mut cursor = self
            .dispatcher
            .begin_filter_counter_operand(access)
            .map_err(|error| format!("{label}_begin_counter_operand:{error:?}"))?;
        let (original, array_key) = match self.boundary_array_key(
            handle,
            boundary_access,
            false,
            operation,
            parent_wire,
            label,
        )? {
            Ok(parts) => parts,
            Err(thrown) => {
                self.dispatcher
                    .cancel_filter_counter_operand(&mut cursor, thrown)
                    .map_err(|error| format!("{label}_cancel_counter_operand:{error:?}"))?;
                return match self.run_control(&mut cursor, operation, parent_wire, label)? {
                    ControlOutcome::Throwable(observed) if observed == thrown => Ok(Err(thrown)),
                    ControlOutcome::Throwable(_) => {
                        Err(format!("{label}_cancel_throwable_changed"))
                    }
                    ControlOutcome::Result(_) => Err(format!("{label}_cancel_completed_normally")),
                };
            }
        };
        let hook = HookArgument::new(original, array_key);
        self.dispatcher
            .resume_filter_counter_operand(&mut cursor, &hook)
            .map_err(|error| format!("{label}_resume_counter_operand:{error:?}"))?;
        match self.run_control(&mut cursor, operation, parent_wire, label)? {
            ControlOutcome::Result(ControlResult::FilterCounter(result)) => Ok(Ok((hook, result))),
            ControlOutcome::Throwable(thrown) => Ok(Err(thrown)),
            ControlOutcome::Result(_) => Err(format!("{label}_counter_result_mismatch")),
        }
    }

    fn api_did(
        &mut self,
        payload: &Value,
        operation: OperationId,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        exact_keys(payload, &["api", "arguments"], "did_filter_call")?;
        let handle = self.current_argument("hook")?;
        let (hook, isset) = match self.counter_operand(
            FilterCounterOperandAccess::Isset,
            "isset",
            handle,
            operation,
            parent_wire,
            "did_filter_hook_isset",
        )? {
            Ok(result) => result,
            Err(thrown) => return Ok(Completion::PhpThrowable(thrown)),
        };
        self.remember_hook(hook.clone());
        let count = match isset {
            FilterCounterResult::Lookup(FilterCounterLookup::Value(_)) => {
                match self.counter_operand(
                    FilterCounterOperandAccess::Read,
                    "read",
                    handle,
                    operation,
                    parent_wire,
                    "did_filter_hook_read",
                )? {
                    Ok((
                        read_hook,
                        FilterCounterResult::Lookup(FilterCounterLookup::Value(count)),
                    )) => {
                        if read_hook.array_key != hook.array_key
                            || !same_scalar_representation(&read_hook.original, &hook.original)
                        {
                            return Err("did_filter_hook_key_changed".into());
                        }
                        count
                    }
                    Ok((
                        read_hook,
                        FilterCounterResult::Lookup(
                            FilterCounterLookup::Missing | FilterCounterLookup::NullOperand,
                        ),
                    )) => {
                        if read_hook.array_key != hook.array_key
                            || !same_scalar_representation(&read_hook.original, &hook.original)
                        {
                            return Err("did_filter_hook_key_changed".into());
                        }
                        return Ok(Completion::Value(PhpValue::Null));
                    }
                    Ok(_) => return Err("did_filter_read_result_mismatch".into()),
                    Err(thrown) => return Ok(Completion::PhpThrowable(thrown)),
                }
            }
            FilterCounterResult::Lookup(
                FilterCounterLookup::Missing | FilterCounterLookup::NullOperand,
            ) => 0,
            FilterCounterResult::Mutation(_) => {
                return Err("did_filter_isset_result_mismatch".into());
            }
        };
        let count = i64::try_from(count).map_err(|_| "did_filter_count_overflow")?;
        Ok(Completion::Value(PhpValue::Integer(count)))
    }

    fn api_did_action(
        &mut self,
        payload: &Value,
        operation: OperationId,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        exact_keys(payload, &["api", "arguments"], "did_action_call")?;
        let handle = self.current_argument("hook")?;
        let (hook, array_key) = match self.boundary_array_key(
            handle,
            "isset",
            false,
            operation,
            parent_wire,
            "did_action_hook_key",
        )? {
            Ok(result) => result,
            Err(handle) => return Ok(Completion::PhpThrowable(handle)),
        };
        let hook = HookArgument::new(hook, array_key);
        self.remember_hook(hook);
        let value = match self.boundary_value(
            "action_counter_read",
            json!({"handle":descriptor(handle)}),
            operation,
            parent_wire,
            "did_action_read",
        )? {
            Ok(value) => value,
            Err(handle) => return Ok(Completion::PhpThrowable(handle)),
        };
        let count = parse_pod(&value, "did_action_count")?;
        validate_action_counter_value(count, "did_action_count").map(Completion::Value)
    }

    fn api_do_action(
        &mut self,
        payload: &Value,
        operation: OperationId,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        exact_keys(payload, &["api", "arguments", "args"], "do_action_call")?;
        let handle = self.current_argument("hook")?;
        let (hook, array_key) = match self.boundary_array_key(
            handle,
            "isset",
            false,
            operation,
            parent_wire,
            "do_action_hook_key",
        )? {
            Ok(result) => result,
            Err(handle) => return Ok(Completion::PhpThrowable(handle)),
        };
        let hook = HookArgument::new(hook, array_key);
        self.remember_hook(hook.clone());
        let args = self
            .action_argument_handles()?
            .ok_or("do_action_argument_frame_missing")?
            .into_iter()
            .map(|_| PhpValue::Unsupported)
            .collect::<Vec<_>>();
        let ticket = self
            .dispatcher
            .begin_action_effectful_typed(&hook, &args)
            .map_err(|error| format!("begin_action:{error:?}"))?;
        let completion = self.drive_dispatch(ticket, &hook, operation, parent_wire)?;
        if hook.original == HookScalar::Bytes(NESTED_CAUGHT_CHILD_HOOK.as_bytes().to_vec()) {
            let Completion::PhpThrowable(handle) = completion else {
                return Err("nested_caught_child_did_not_throw".into());
            };
            if handle.kind() != PhpHandleKind::Throwable
                || self.handles.get(&handle) != Some(&HandleState::Throwable)
                || self.nested_caught_child_throwable.replace(handle).is_some()
            {
                return Err("nested_caught_child_throwable_handle_mismatch".into());
            }
            return Ok(Completion::PhpThrowable(handle));
        }
        if hook.original == HookScalar::Bytes(NESTED_CAUGHT_PARENT_HOOK.as_bytes().to_vec()) {
            if completion != Completion::Value(PhpValue::Null) {
                return Err("nested_caught_parent_did_not_return_null".into());
            }
            self.validate_nested_caught_dispatch_state()?;
        }
        Ok(completion)
    }

    fn validate_nested_caught_dispatch_state(&self) -> Result<(), String> {
        let parent = HookArgument::bytes(NESTED_CAUGHT_PARENT_HOOK);
        let child = HookArgument::bytes(NESTED_CAUGHT_CHILD_HOOK);
        if self.nested_caught_child_throwable.is_none()
            || self.dispatcher.did_action_typed(&parent) != 1
            || self.dispatcher.did_action_typed(&child) != 1
            || self.dispatcher.current_hook_typed()
                != Some(&HookScalar::Bytes(
                    NESTED_CAUGHT_PARENT_HOOK.as_bytes().to_vec(),
                ))
            || !self.dispatcher.doing_hook_typed(Some(&HookScalar::Bytes(
                NESTED_CAUGHT_PARENT_HOOK.as_bytes().to_vec(),
            )))
            || self.dispatcher.doing_hook_typed(Some(&HookScalar::Bytes(
                NESTED_CAUGHT_CHILD_HOOK.as_bytes().to_vec(),
            )))
            || self.dispatcher.hook_stack_depth() != 1
        {
            return Err("nested_caught_dispatch_state_mismatch".into());
        }
        Ok(())
    }

    fn api_apply(
        &mut self,
        payload: &Value,
        operation: OperationId,
        parent_wire: &str,
    ) -> Result<Completion<PhpValue>, String> {
        exact_keys(
            payload,
            &["api", "arguments", "value", "args"],
            "apply_filters_call",
        )?;
        let hook_handle = self.current_argument("hook")?;
        let (hook, isset) = match self.counter_operand(
            FilterCounterOperandAccess::Isset,
            "isset",
            hook_handle,
            operation,
            parent_wire,
            "apply_counter_isset",
        )? {
            Ok(result) => result,
            Err(thrown) => return Ok(Completion::PhpThrowable(thrown)),
        };
        self.remember_hook(hook.clone());
        let counter_access = match isset {
            FilterCounterResult::Lookup(FilterCounterLookup::Value(_)) => {
                FilterCounterOperandAccess::Increment
            }
            FilterCounterResult::Lookup(
                FilterCounterLookup::Missing | FilterCounterLookup::NullOperand,
            ) => FilterCounterOperandAccess::WriteOne,
            FilterCounterResult::Mutation(_) => {
                return Err("apply_counter_isset_result_mismatch".into());
            }
        };
        let counter_boundary = match counter_access {
            FilterCounterOperandAccess::Increment => "increment",
            FilterCounterOperandAccess::WriteOne => "write",
            _ => return Err("apply_counter_access_mismatch".into()),
        };
        let (mutation_hook, mutation) = match self.counter_operand(
            counter_access,
            counter_boundary,
            hook_handle,
            operation,
            parent_wire,
            "apply_counter_mutation",
        )? {
            Ok(result) => result,
            Err(thrown) => return Ok(Completion::PhpThrowable(thrown)),
        };
        if mutation_hook.array_key != hook.array_key
            || !same_scalar_representation(&mutation_hook.original, &hook.original)
        {
            return Err("apply_counter_mutation_key_changed".into());
        }
        if !matches!(
            mutation,
            FilterCounterResult::Mutation(
                FilterCounterMutation::Applied | FilterCounterMutation::Discarded
            )
        ) {
            return Err("apply_counter_mutation_result_mismatch".into());
        }
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
            .begin_filter_after_counter_typed(&hook, value, &extra)
            .map_err(|error| format!("begin_filter_after_counter:{error:?}"))?;
        self.drive_dispatch(ticket, &hook, operation, parent_wire)
    }

    fn drive_dispatch(
        &mut self,
        ticket: DispatchTicket,
        hook: &HookArgument,
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
                    let args =
                        self.encode_callback_arguments(invocation.call_mode, &invocation.args)?;
                    let call_mode = match invocation.call_mode {
                        PhpCallMode::Zero => "zero",
                        PhpCallMode::Array => "array",
                        PhpCallMode::All => "all",
                    };
                    self.wire.send("invoke_php", Some(caller_wire), json!({
                        "operation":"callable","effect_id":effect_wire,"handle":descriptor(invocation.handle),
                        "hook":scalar_pod(&invocation.hook,"callback_hook")?,
                        "priority":php_key_json(&invocation.priority),"call_mode":call_mode,"args":args,
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
                    let pending = self
                        .dispatcher
                        .pending_dispatch_throwable(ticket)
                        .map_err(|error| format!("pending_dispatch_throwable:{error:?}"))?;
                    let completion = match pending {
                        Some(throwable) => self.release_effect_unwind(
                            handle,
                            throwable,
                            operation,
                            Some(caller_wire),
                        )?,
                        None => self.release_effect(handle, operation, Some(caller_wire))?,
                    };
                    self.dispatcher
                        .resume_php_release_completion(ticket, handle, completion)
                        .map_err(|error| format!("resume_filter_release:{error:?}"))?;
                }
                DispatchStep::Boundary(boundary) => {
                    let completion = match boundary {
                        DispatchBoundary::ActionCounterIncrement => {
                            let handle = self.current_argument("hook")?;
                            match self.boundary_value(
                                "action_counter_increment",
                                json!({"handle":descriptor(handle)}),
                                operation,
                                caller_wire,
                                "action_counter_increment",
                            )? {
                                Err(handle) => Completion::PhpThrowable(handle),
                                Ok(value) => {
                                    let count =
                                        parse_pod(&value, "action_counter_increment_count")?;
                                    action_counter_increment_ack(count)?
                                }
                            }
                        }
                        DispatchBoundary::FilterCounterWrite
                        | DispatchBoundary::FilterCounterIncrement => {
                            return Err("counter_boundary_after_counted_filter_start".into());
                        }
                        DispatchBoundary::NamedHookIsset => {
                            match self.boundary_array_key(
                                self.current_argument("hook")?,
                                "isset",
                                false,
                                operation,
                                caller_wire,
                                "apply_named_isset",
                            )? {
                                Ok((original, key)) => {
                                    if !same_scalar_representation(&original, &hook.original)
                                        || key != hook.array_key
                                    {
                                        return Err("apply_named_isset_key_changed".into());
                                    }
                                    Completion::Value(PhpValue::Null)
                                }
                                Err(handle) => Completion::PhpThrowable(handle),
                            }
                        }
                        DispatchBoundary::NamedHookRead => {
                            match self.boundary_array_key(
                                self.current_argument("hook")?,
                                "read",
                                false,
                                operation,
                                caller_wire,
                                "apply_named_read",
                            )? {
                                Ok((original, key)) => {
                                    if !same_scalar_representation(&original, &hook.original)
                                        || key != hook.array_key
                                    {
                                        return Err("apply_named_read_key_changed".into());
                                    }
                                    Completion::Value(PhpValue::Null)
                                }
                                Err(handle) => Completion::PhpThrowable(handle),
                            }
                        }
                        DispatchBoundary::MissingOffsetAll => {
                            match self.boundary_missing_offset(
                                &PhpKey::Integer(0),
                                operation,
                                caller_wire,
                                "apply_all_missing_offset",
                            )? {
                                Some(handle) => Completion::PhpThrowable(handle),
                                None => Completion::Value(PhpValue::Null),
                            }
                        }
                        DispatchBoundary::NullForeachAll => {
                            match self.boundary_null_foreach(
                                operation,
                                caller_wire,
                                "apply_all_null_foreach",
                            )? {
                                Some(handle) => Completion::PhpThrowable(handle),
                                None => Completion::Value(PhpValue::Null),
                            }
                        }
                        DispatchBoundary::MissingOffset => {
                            match self.boundary_missing_offset(
                                &hook.array_key,
                                operation,
                                caller_wire,
                                "apply_named_missing_offset",
                            )? {
                                Some(handle) => Completion::PhpThrowable(handle),
                                None => Completion::Value(PhpValue::Null),
                            }
                        }
                        DispatchBoundary::NullMethodApplyFilters => {
                            let handle = self.boundary_null_method(
                                "apply_filters",
                                operation,
                                caller_wire,
                                "apply_named_null_method",
                            )?;
                            Completion::PhpThrowable(handle)
                        }
                    };
                    self.dispatcher
                        .resume_boundary(ticket, completion)
                        .map_err(|error| format!("resume_filter_boundary:{error:?}"))?;
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
                    let pending = self
                        .dispatcher
                        .pending_control_throwable(operation_cursor)
                        .map_err(|error| format!("{label}_pending_throwable:{error:?}"))?;
                    let completion = match pending {
                        Some(throwable) => self.release_effect_unwind(
                            handle,
                            throwable,
                            operation,
                            Some(caller_wire),
                        )?,
                        None => self.release_effect(handle, operation, Some(caller_wire))?,
                    };
                    self.dispatcher
                        .resume_control_release_completion(operation_cursor, handle, completion)
                        .map_err(|error| format!("{label}_resume_release:{error:?}"))?;
                }
                ControlStep::PriorityRead {
                    priority,
                    missing: _,
                    state: _,
                } => {
                    let handle = self.current_argument("priority")?;
                    let completion = match self.boundary_array_key(
                        handle,
                        "read",
                        true,
                        operation,
                        caller_wire,
                        &format!("{label}_priority_read"),
                    )? {
                        Ok((original, key)) => {
                            if !same_scalar_representation(&original, &priority.original)
                                || key != *priority.key()
                            {
                                return Err(format!("{label}_priority_read_changed"));
                            }
                            Completion::Value(PhpValue::Null)
                        }
                        Err(handle) => Completion::PhpThrowable(handle),
                    };
                    self.dispatcher
                        .resume_control_priority_read(operation_cursor, completion)
                        .map_err(|error| format!("{label}_resume_priority_read:{error:?}"))?;
                }
                ControlStep::PriorityMissingOffset(priority) => {
                    let completion = match self.boundary_missing_offset(
                        priority.key(),
                        operation,
                        caller_wire,
                        &format!("{label}_priority_missing_offset"),
                    )? {
                        Some(handle) => Completion::PhpThrowable(handle),
                        None => Completion::Value(PhpValue::Null),
                    };
                    self.dispatcher
                        .resume_control_priority_missing_offset(operation_cursor, completion)
                        .map_err(|error| {
                            format!("{label}_resume_priority_missing_offset:{error:?}")
                        })?;
                }
                ControlStep::PriorityUnset(priority) => {
                    let handle = self.current_argument("priority")?;
                    let completion = match self.boundary_array_key(
                        handle,
                        "unset",
                        true,
                        operation,
                        caller_wire,
                        &format!("{label}_priority_unset"),
                    )? {
                        Ok((original, key)) => {
                            if !same_scalar_representation(&original, &priority.original)
                                || key != *priority.key()
                            {
                                return Err(format!("{label}_priority_unset_changed"));
                            }
                            Completion::Value(PhpValue::Null)
                        }
                        Err(handle) => Completion::PhpThrowable(handle),
                    };
                    self.dispatcher
                        .resume_control_priority_unset(operation_cursor, completion)
                        .map_err(|error| format!("{label}_resume_priority_unset:{error:?}"))?;
                }
                ControlStep::Complete(result) => return Ok(ControlOutcome::Result(result)),
                ControlStep::PhpThrowable(handle) => return Ok(ControlOutcome::Throwable(handle)),
            }
        }
    }

    fn release_hook_target(
        &mut self,
        target: recastpress_wp::dispatcher::HookTarget,
        operation: OperationId,
        caller_wire: &str,
        label: &str,
    ) -> Result<ControlOutcome, String> {
        let mut cursor = self
            .dispatcher
            .begin_release_target(target)
            .map_err(|error| format!("{label}_begin_release_target:{error:?}"))?;
        self.run_control(&mut cursor, operation, caller_wire, label)
    }

    fn finish_target(
        &mut self,
        target: recastpress_wp::dispatcher::HookTarget,
        completion: Completion<PhpValue>,
        operation: OperationId,
        caller_wire: &str,
        label: &str,
    ) -> Result<Completion<PhpValue>, String> {
        self.release_hook_target_completion(target, completion, operation, caller_wire, label)
    }

    fn release_hook_target_completion(
        &mut self,
        target: recastpress_wp::dispatcher::HookTarget,
        completion: Completion<PhpValue>,
        operation: OperationId,
        caller_wire: &str,
        label: &str,
    ) -> Result<Completion<PhpValue>, String> {
        match completion {
            Completion::Value(value) => {
                match self.release_hook_target(target, operation, caller_wire, label)? {
                    ControlOutcome::Result(ControlResult::TargetReleased) => {
                        Ok(Completion::Value(value))
                    }
                    ControlOutcome::Throwable(handle) => Ok(Completion::PhpThrowable(handle)),
                    _ => Err(format!("{label}_target_release_result")),
                }
            }
            Completion::PhpThrowable(throwable) => {
                let mut cursor = self
                    .dispatcher
                    .begin_release_target_with_throwable(target, throwable)
                    .map_err(|error| format!("{label}_begin_release_target_unwind:{error:?}"))?;
                match self.run_control(&mut cursor, operation, caller_wire, label)? {
                    ControlOutcome::Throwable(handle) => Ok(Completion::PhpThrowable(handle)),
                    ControlOutcome::Result(_) => Err(format!("{label}_unwind_release_completed")),
                }
            }
        }
    }

    fn boundary_null_method(
        &mut self,
        method: &str,
        operation: OperationId,
        caller_wire: &str,
        label: &str,
    ) -> Result<PhpHandle, String> {
        match self.boundary_value(
            "null_method",
            json!({"method":method}),
            operation,
            caller_wire,
            label,
        )? {
            Err(handle) => Ok(handle),
            Ok(_) => Err(format!("{label}_null_method_returned_value")),
        }
    }

    fn boundary_null_property(
        &mut self,
        property: &str,
        operation: OperationId,
        caller_wire: &str,
        label: &str,
    ) -> Result<Option<PhpHandle>, String> {
        match self.boundary_value(
            "null_property",
            json!({"property":property}),
            operation,
            caller_wire,
            label,
        )? {
            Err(handle) => Ok(Some(handle)),
            Ok(value) => {
                exact_keys(&value, &["missing"], label)?;
                if value.get("missing") != Some(&Value::Bool(true)) {
                    return Err(format!("{label}_null_property_ack"));
                }
                Ok(None)
            }
        }
    }

    fn boundary_null_foreach(
        &mut self,
        operation: OperationId,
        caller_wire: &str,
        label: &str,
    ) -> Result<Option<PhpHandle>, String> {
        match self.boundary_value("null_foreach", json!({}), operation, caller_wire, label)? {
            Err(handle) => Ok(Some(handle)),
            Ok(value) => {
                exact_keys(&value, &["missing"], label)?;
                if value.get("missing") != Some(&Value::Bool(true)) {
                    return Err(format!("{label}_null_foreach_ack"));
                }
                Ok(None)
            }
        }
    }

    fn boundary_missing_offset(
        &mut self,
        key: &PhpKey,
        operation: OperationId,
        caller_wire: &str,
        label: &str,
    ) -> Result<Option<PhpHandle>, String> {
        let result = self.boundary_value(
            "missing_offset",
            json!({"key":php_key_json(key)}),
            operation,
            caller_wire,
            label,
        )?;
        match result {
            Err(handle) => Ok(Some(handle)),
            Ok(value) => {
                exact_keys(&value, &["missing"], label)?;
                if value.get("missing") != Some(&Value::Bool(true)) {
                    return Err(format!("{label}_missing_ack"));
                }
                Ok(None)
            }
        }
    }

    fn hook_target_for_api(
        &mut self,
        argument: PhpHandle,
        method: &str,
        create_if_missing: bool,
        operation: OperationId,
        caller_wire: &str,
        label: &str,
    ) -> Result<
        Result<Option<(HookArgument, recastpress_wp::dispatcher::HookTarget)>, PhpHandle>,
        String,
    > {
        let (hook, lookup) = match self.hook_binding_lookup(
            argument,
            "isset",
            operation,
            caller_wire,
            &format!("{label}_hook_isset"),
        )? {
            Ok(result) => result,
            Err(handle) => return Ok(Err(handle)),
        };
        let hook_is_set = match lookup {
            HookBindingLookupResult::Found(target) => {
                match self.release_hook_target(
                    target,
                    operation,
                    caller_wire,
                    &format!("{label}_isset_target_release"),
                )? {
                    ControlOutcome::Result(ControlResult::TargetReleased) => true,
                    ControlOutcome::Throwable(handle) => return Ok(Err(handle)),
                    _ => return Err(format!("{label}_isset_target_release_result")),
                }
            }
            HookBindingLookupResult::Missing | HookBindingLookupResult::NullOperand => false,
        };
        if !hook_is_set {
            if !create_if_missing {
                return Ok(Ok(None));
            }
            match self.boundary_array_key(
                argument,
                "write",
                false,
                operation,
                caller_wire,
                &format!("{label}_hook_write"),
            )? {
                Ok((write_original, write_key)) => {
                    if write_key != hook.array_key
                        || !same_scalar_representation(&write_original, &hook.original)
                    {
                        return Err(format!("{label}_hook_write_key_changed"));
                    }
                }
                Err(handle) => return Ok(Err(handle)),
            }
            let mut write = self
                .dispatcher
                .begin_write_hook(&hook)
                .map_err(|error| format!("{label}_begin_write_hook:{error:?}"))?;
            match self.run_control(&mut write, operation, caller_wire, label)? {
                ControlOutcome::Result(ControlResult::HookWritten) => {}
                ControlOutcome::Throwable(handle) => return Ok(Err(handle)),
                _ => return Err(format!("{label}_write_hook_result")),
            }
        }
        match self.hook_binding_lookup(
            argument,
            "read",
            operation,
            caller_wire,
            &format!("{label}_hook_read"),
        )? {
            Ok((read_hook, HookBindingLookupResult::Found(target))) => {
                if read_hook.array_key != hook.array_key
                    || !same_scalar_representation(&read_hook.original, &hook.original)
                {
                    return Err(format!("{label}_hook_read_key_changed"));
                }
                Ok(Ok(Some((hook, target))))
            }
            Ok((read_hook, HookBindingLookupResult::Missing)) => {
                if read_hook.array_key != hook.array_key
                    || !same_scalar_representation(&read_hook.original, &hook.original)
                {
                    return Err(format!("{label}_hook_read_key_changed"));
                }
                if let Some(handle) = self.boundary_missing_offset(
                    &hook.array_key,
                    operation,
                    caller_wire,
                    &format!("{label}_missing_offset"),
                )? {
                    return Ok(Err(handle));
                }
                let handle = self.boundary_null_method(method, operation, caller_wire, label)?;
                Ok(Err(handle))
            }
            Ok((read_hook, HookBindingLookupResult::NullOperand)) => {
                if read_hook.array_key != hook.array_key
                    || !same_scalar_representation(&read_hook.original, &hook.original)
                {
                    return Err(format!("{label}_hook_read_key_changed"));
                }
                let handle = self.boundary_null_method(method, operation, caller_wire, label)?;
                Ok(Err(handle))
            }
            Err(handle) => Ok(Err(handle)),
        }
    }

    fn hook_binding_lookup(
        &mut self,
        argument: PhpHandle,
        access: &str,
        operation: OperationId,
        caller_wire: &str,
        label: &str,
    ) -> Result<Result<(HookArgument, HookBindingLookupResult), PhpHandle>, String> {
        let mut cursor = self
            .dispatcher
            .begin_hook_binding_lookup()
            .map_err(|error| format!("{label}_begin_binding_lookup:{error:?}"))?;
        let (original, key) = match self.boundary_array_key(
            argument,
            access,
            false,
            operation,
            caller_wire,
            label,
        )? {
            Ok(value) => value,
            Err(handle) => {
                self.dispatcher
                    .cancel_hook_binding_lookup(&mut cursor, handle)
                    .map_err(|error| format!("{label}_cancel_binding_lookup:{error:?}"))?;
                return match self.run_control(&mut cursor, operation, caller_wire, label)? {
                    ControlOutcome::Throwable(handle) => Ok(Err(handle)),
                    ControlOutcome::Result(_) => {
                        Err(format!("{label}_cancel_binding_lookup_completed"))
                    }
                };
            }
        };
        let hook = HookArgument::new(original, key);
        self.remember_hook(hook.clone());
        self.dispatcher
            .resume_hook_binding_lookup(&mut cursor, &hook)
            .map_err(|error| format!("{label}_resume_binding_lookup:{error:?}"))?;
        match self.run_control(&mut cursor, operation, caller_wire, label)? {
            ControlOutcome::Result(ControlResult::HookBindingLookup(result)) => {
                Ok(Ok((hook, result)))
            }
            ControlOutcome::Throwable(handle) => Ok(Err(handle)),
            _ => Err(format!("{label}_binding_lookup_result")),
        }
    }

    fn release_effect(
        &mut self,
        handle: PhpHandle,
        operation: OperationId,
        caller_wire: Option<&str>,
    ) -> Result<Completion<PhpValue>, String> {
        self.release_effect_inner(handle, None, operation, caller_wire)
    }

    fn release_effect_unwind(
        &mut self,
        handle: PhpHandle,
        throwable: PhpHandle,
        operation: OperationId,
        caller_wire: Option<&str>,
    ) -> Result<Completion<PhpValue>, String> {
        self.release_effect_inner(handle, Some(throwable), operation, caller_wire)
    }

    fn release_effect_inner(
        &mut self,
        handle: PhpHandle,
        pending_throwable: Option<PhpHandle>,
        operation: OperationId,
        caller_wire: Option<&str>,
    ) -> Result<Completion<PhpValue>, String> {
        if handle.kind() != PhpHandleKind::Callable
            || self.handles.get(&handle) != Some(&HandleState::Active)
        {
            return Err("release_handle_not_active_callable".into());
        }
        if pending_throwable.is_some_and(|throwable| {
            throwable.kind() != PhpHandleKind::Throwable
                || self.handles.get(&throwable) != Some(&HandleState::Throwable)
        }) {
            return Err("release_unwind_throwable_not_registered".into());
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
        let payload = match pending_throwable {
            Some(throwable) => json!({
                "operation":"release_callable_unwind",
                "effect_id":effect_wire,
                "handle":descriptor(handle),
                "throwable":descriptor(throwable),
            }),
            None => json!({
                "operation":"release_callable",
                "effect_id":effect_wire,
                "handle":descriptor(handle),
            }),
        };
        self.wire.send("invoke_php", caller_wire, payload)?;
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
        &["operation", "protocol", "request_generation"],
        "callables_start",
    )?;
    if !matches!(string(&start, "operation")?, "callable_api" | "action_api")
        || start.get("protocol") != Some(&json!(2))
        || string(&start, "request_generation")? != "71"
    {
        return Err("callables_start_operation_or_generation".into());
    }
    let scenario = string(&start, "operation")?.to_owned();
    let api_names = if scenario == "action_api" {
        &API_NAMES[..]
    } else {
        &FILTER_API_NAMES[..]
    };
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
        slot_generation_high_water: HashMap::new(),
        throwable_diagnostics: HashMap::new(),
        nested_caught_child_throwable: None,
        effect_ids: HashMap::new(),
        api_arguments: Vec::new(),
        api_action_argument_counts: Vec::new(),
        hook_names: Vec::new(),
        api_counts: api_names
            .iter()
            .map(|name| ((*name).to_owned(), 0))
            .collect(),
        callback_invocations: 0,
        release_effects: 0,
        boundary_effects: 0,
        argument_release_effects: 0,
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
        let names = peer.hook_names.clone();
        for name in names {
            let mut control = peer
                .dispatcher
                .begin_remove_all_typed(&name, None)
                .map_err(|error| format!("cleanup_begin_remove_all:{error:?}"))?;
            loop {
                match peer
                    .dispatcher
                    .advance_control(&mut control)
                    .map_err(|error| format!("cleanup_advance:{error:?}"))?
                {
                    ControlStep::ReleasePhp(handle) => {
                        let pending = peer
                            .dispatcher
                            .pending_control_throwable(&control)
                            .map_err(|error| format!("cleanup_pending_throwable:{error:?}"))?;
                        let completion = match pending {
                            Some(throwable) => {
                                peer.release_effect_unwind(handle, throwable, root, None)?
                            }
                            None => peer.release_effect(handle, root, None)?,
                        };
                        peer.dispatcher
                            .resume_control_release_completion(&mut control, handle, completion)
                            .map_err(|error| format!("cleanup_resume_release:{error:?}"))?;
                    }
                    ControlStep::PhpThrowable(_) => return Err("cleanup_control_throwable".into()),
                    ControlStep::Complete(ControlResult::RemovedAll) => break,
                    ControlStep::PriorityRead { .. }
                    | ControlStep::PriorityMissingOffset(_)
                    | ControlStep::PriorityUnset(_) => {
                        return Err("cleanup_unexpected_priority_phase".into());
                    }
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
    peer.wire
        .send("return", None, json!({"operation":scenario,"status":"ok"}))?;
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
    let nested_caught_throw = if scenario == "action_api" {
        Some(nested_caught_throw_summary(&observation)?)
    } else {
        None
    };
    let summary = json!({
        "scenario":scenario,"continuation_peer":true,
        "frames_from_php":peer.wire.in_seq,"frames_from_rust":peer.wire.out_seq,
        "api_counts":api_counts,"callback_invocations":peer.callback_invocations,
        "release_effects":peer.release_effects,"late_resume_rejected":late_resume_rejected,
        "boundary_effects":peer.boundary_effects,"argument_release_effects":peer.argument_release_effects,
        "late_call_rejected":late_call_rejected,"continuation_depth_after":peer.continuation.depth(),
        "state_after":format!("{:?}",peer.continuation.state()),"php_observation":observation,
        "nested_caught_throw":nested_caught_throw,
        "registry_observation":registry_observation,
    });
    if peer.continuation.depth() != 0 || peer.continuation.state() != &RequestState::Finished {
        return Err("callable_final_continuation_state".into());
    }
    if scenario == "action_api" {
        eprintln!("V06_ACTIONS_RESULT {summary}");
        eprintln!("V06_ACTIONS_OK continuation_peer=true");
    } else {
        eprintln!("V06_CALLABLES_RESULT {summary}");
        eprintln!("V06_CALLABLES_OK continuation_peer=true");
    }
    Ok(summary)
}

fn nested_caught_throw_summary(observation: &Value) -> Result<Value, String> {
    let nested = observation
        .pointer("/action_api/nested_caught_throw")
        .ok_or("nested_caught_throw_observation_missing")?;
    exact_keys(
        nested,
        &["events", "do_action_return", "after"],
        "nested_caught_throw_observation",
    )?;
    let events = nested
        .get("events")
        .and_then(Value::as_array)
        .ok_or("nested_caught_throw_events")?;
    let trace = events
        .iter()
        .map(|event| string(event, "event"))
        .collect::<Result<Vec<_>, _>>()?;
    let expected_trace = [
        "parent-enter",
        "child-enter",
        "child-throw",
        "parent-catch",
        "parent-resume",
    ];
    if trace != expected_trace {
        return Err("nested_caught_throw_trace_mismatch".into());
    }
    let parent_catch = events
        .iter()
        .find(|event| event.get("event").and_then(Value::as_str) == Some("parent-catch"))
        .ok_or("nested_caught_throw_parent_catch_missing")?;
    if parent_catch.get("same_throwable") != Some(&Value::Bool(true)) {
        return Err("nested_caught_throw_identity_mismatch".into());
    }
    if nested.get("do_action_return") != Some(&Value::Null) {
        return Err("nested_caught_throw_parent_return_mismatch".into());
    }
    let after = nested
        .get("after")
        .ok_or("nested_caught_throw_after_missing")?;
    exact_keys(
        after,
        &[
            "current_action",
            "doing_parent",
            "doing_child",
            "parent_count",
            "child_count",
            "stack_depth",
        ],
        "nested_caught_throw_after",
    )?;
    if string(after, "current_action")? != NESTED_CAUGHT_PARENT_HOOK
        || after.get("doing_parent") != Some(&Value::Bool(true))
        || after.get("doing_child") != Some(&Value::Bool(false))
        || after.get("parent_count").and_then(Value::as_u64) != Some(1)
        || after.get("child_count").and_then(Value::as_u64) != Some(1)
        || after.get("stack_depth").and_then(Value::as_u64) != Some(1)
    {
        return Err("nested_caught_throw_after_state_mismatch".into());
    }
    Ok(json!({
        "trace":trace,
        "same_object":true,
        "parent_do_action_return":null,
        "parent_did_action":1,
        "child_did_action":1,
        "current_action":after["current_action"],
        "doing_parent":true,
        "doing_child":false,
        "stack_depth":1,
    }))
}

fn remove_result_after_unbind(
    removed: bool,
    completion: Completion<PhpValue>,
) -> Result<Completion<PhpValue>, &'static str> {
    match completion {
        Completion::PhpThrowable(handle) => Ok(Completion::PhpThrowable(handle)),
        Completion::Value(PhpValue::Null) => Ok(Completion::Value(PhpValue::Bool(removed))),
        Completion::Value(_) => Err("remove_unbind_completion_mismatch"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn make_peer<'a>(
        wire: &'a mut Wire<Cursor<Vec<u8>>, Vec<u8>>,
        continuation: &'a mut Continuation<Value>,
        dispatcher: &'a mut Dispatcher,
        request: RequestGeneration,
    ) -> CallablePeer<'a, Cursor<Vec<u8>>, Vec<u8>> {
        CallablePeer {
            wire,
            continuation,
            dispatcher,
            request,
            handles: HashMap::new(),
            slot_generation_high_water: HashMap::new(),
            throwable_diagnostics: HashMap::new(),
            nested_caught_child_throwable: None,
            effect_ids: HashMap::new(),
            api_arguments: Vec::new(),
            api_action_argument_counts: Vec::new(),
            hook_names: Vec::new(),
            api_counts: BTreeMap::new(),
            callback_invocations: 0,
            release_effects: 0,
            boundary_effects: 0,
            argument_release_effects: 0,
            next_effect: 1,
            registration_epoch: 0,
        }
    }

    fn handle_descriptor(kind: &str, slot: u32, generation: u32, token: u64) -> Value {
        json!({
            "request":"71",
            "kind":kind,
            "slot":slot,
            "generation":generation,
            "token":format!("{token:016x}"),
        })
    }

    fn start_continuation(continuation: &mut Continuation<Value>) {
        let root = continuation.start_root().unwrap();
        continuation.invoke_php(root).unwrap();
    }

    macro_rules! create_peer {
        ($request:ident, $wire:ident, $continuation:ident, $dispatcher:ident, $peer:ident) => {
            let $request = RequestGeneration::new(71);
            let mut $wire = Wire {
                reader: Cursor::new(Vec::new()),
                writer: Vec::new(),
                in_seq: 0,
                out_seq: 0,
                total: 0,
            };
            let mut $continuation =
                Continuation::<Value>::new($request, LimitsProfile::default()).unwrap();
            start_continuation(&mut $continuation);
            let mut $dispatcher = Dispatcher::new();
            let mut $peer = make_peer(&mut $wire, &mut $continuation, &mut $dispatcher, $request);
        };
    }

    #[test]
    fn rejects_live_slot_collision() {
        create_peer!(request, wire, continuation, dispatcher, peer);
        peer.register_handle(
            &handle_descriptor("callable", 5, 3, 1),
            PhpHandleKind::Callable,
            "first",
        )
        .unwrap();
        let result = peer.register_handle(
            &handle_descriptor("argument", 5, 4, 2),
            PhpHandleKind::Argument,
            "collision",
        );
        assert_eq!(result.unwrap_err(), "collision_slot_collision");
    }

    #[test]
    fn rejects_retired_generation_replay() {
        create_peer!(request, wire, continuation, dispatcher, peer);
        let handle = peer
            .register_handle(
                &handle_descriptor("argument", 6, 3, 1),
                PhpHandleKind::Argument,
                "first",
            )
            .unwrap();
        peer.continuation.unregister_handle(handle).unwrap();
        peer.handles.remove(&handle);
        let result = peer.register_handle(
            &handle_descriptor("callable", 6, 3, 2),
            PhpHandleKind::Callable,
            "replay",
        );
        assert_eq!(result.unwrap_err(), "replay_slot_generation");
    }

    #[test]
    fn allows_higher_generation_while_old_slot_release_is_pending() {
        create_peer!(request, wire, continuation, dispatcher, peer);
        let old = peer
            .register_handle(
                &handle_descriptor("callable", 7, 3, 1),
                PhpHandleKind::Callable,
                "old",
            )
            .unwrap();
        peer.handles.insert(old, HandleState::ReleasePending);
        let replacement = peer
            .register_handle(
                &handle_descriptor("argument", 7, 4, 2),
                PhpHandleKind::Argument,
                "replacement",
            )
            .unwrap();
        assert_eq!(replacement.slot(), old.slot());
        assert!(replacement.generation() > old.generation());
        assert_eq!(replacement.kind(), PhpHandleKind::Argument);
    }

    #[test]
    fn permits_repeat_of_same_throwable_descriptor() {
        create_peer!(request, wire, continuation, dispatcher, peer);
        let descriptor = handle_descriptor("throwable", 8, 2, 3);
        let handle = peer
            .register_handle(&descriptor, PhpHandleKind::Throwable, "throwable")
            .unwrap();
        assert_eq!(
            peer.register_handle(&descriptor, PhpHandleKind::Throwable, "repeat"),
            Ok(handle)
        );
    }

    #[test]
    fn summarizes_nested_caught_action_throwable_and_rejects_state_changes() {
        let observation = json!({
            "action_api": {
                "nested_caught_throw": {
                    "events": [
                        {"event":"parent-enter"},
                        {"event":"child-enter"},
                        {"event":"child-throw"},
                        {"event":"parent-catch","same_throwable":true},
                        {"event":"parent-resume"}
                    ],
                    "do_action_return":null,
                    "after": {
                        "current_action":NESTED_CAUGHT_PARENT_HOOK,
                        "doing_parent":true,
                        "doing_child":false,
                        "parent_count":1,
                        "child_count":1,
                        "stack_depth":1
                    }
                }
            }
        });
        assert_eq!(
            nested_caught_throw_summary(&observation).unwrap(),
            json!({
                "trace":["parent-enter","child-enter","child-throw","parent-catch","parent-resume"],
                "same_object":true,
                "parent_do_action_return":null,
                "parent_did_action":1,
                "child_did_action":1,
                "current_action":NESTED_CAUGHT_PARENT_HOOK,
                "doing_parent":true,
                "doing_child":false,
                "stack_depth":1
            })
        );

        let mut changed = observation;
        changed["action_api"]["nested_caught_throw"]["events"][3]["same_throwable"] =
            Value::Bool(false);
        assert_eq!(
            nested_caught_throw_summary(&changed).unwrap_err(),
            "nested_caught_throw_identity_mismatch"
        );
    }

    #[test]
    fn action_counter_accepts_bool_and_byte_string_scalars_without_changing_dispatch_ack() {
        for value in [
            PhpValue::Bool(true),
            PhpValue::Bytes(b"b".to_vec()),
            PhpValue::Bytes(b"c".to_vec()),
        ] {
            assert_eq!(
                validate_action_counter_value(value.clone(), "did_action_count"),
                Ok(value.clone())
            );
            assert_eq!(
                action_counter_increment_ack(value),
                Ok(Completion::Value(PhpValue::Null))
            );
        }
    }

    #[test]
    fn action_counter_still_rejects_unsupported_values_and_nonfinite_floats() {
        assert_eq!(
            validate_action_counter_value(PhpValue::Null, "did_action_count").unwrap_err(),
            "did_action_count_not_number"
        );
        assert_eq!(
            validate_action_counter_value(PhpValue::Float(f64::NAN), "did_action_count")
                .unwrap_err(),
            "did_action_count_not_finite"
        );
        assert_eq!(
            action_counter_increment_ack(PhpValue::Null).unwrap_err(),
            "action_counter_increment_not_number"
        );
        assert_eq!(
            action_counter_increment_ack(PhpValue::Float(f64::INFINITY)).unwrap_err(),
            "action_counter_increment_not_finite"
        );
    }

    #[test]
    fn action_argument_transcript_markers_preserve_order_and_validate_counts() {
        create_peer!(request, wire, continuation, dispatcher, peer);
        let first = handle_descriptor("argument", 30, 1, 301);
        let second = handle_descriptor("argument", 31, 1, 311);
        let registered = peer
            .register_api_arguments(
                &json!({
                    "arguments":{"hook":handle_descriptor("argument", 29, 1, 291)},
                    "args":[
                        {"type":"handle","handle":first},
                        {"type":"handle","handle":second}
                    ]
                }),
                "do_action",
            )
            .unwrap();
        assert_eq!(
            registered
                .iter()
                .map(|(name, _)| name.as_str())
                .collect::<Vec<_>>(),
            vec!["hook", "action_arg_0", "action_arg_1"]
        );
        let frame = registered.iter().cloned().collect::<HashMap<_, _>>();
        peer.api_arguments.push(frame);
        peer.api_action_argument_counts.push(Some(2));
        let translated = peer
            .encode_callback_arguments(
                PhpCallMode::All,
                &[
                    PhpValue::Bytes(b"heap-action".to_vec()),
                    PhpValue::Unsupported,
                    PhpValue::Unsupported,
                ],
            )
            .unwrap();
        assert_eq!(
            translated,
            vec![
                pod(&PhpValue::Bytes(b"heap-action".to_vec()), "prefix").unwrap(),
                json!({"type":"handle","handle":first}),
                json!({"type":"handle","handle":second}),
            ]
        );
        assert_eq!(
            peer.encode_callback_arguments(PhpCallMode::Array, &[PhpValue::Unsupported],)
                .unwrap(),
            vec![json!({"type":"handle","handle":first})]
        );
        assert_eq!(
            peer.encode_callback_arguments(
                PhpCallMode::All,
                &[
                    PhpValue::Bytes(b"heap-action".to_vec()),
                    PhpValue::Unsupported
                ],
            )
            .unwrap_err(),
            "action_all_argument_count"
        );

        peer.api_action_argument_counts.pop();
        peer.api_action_argument_counts.push(Some(1));
        assert_eq!(
            peer.action_argument_handles().unwrap_err(),
            "action_arg_order_or_count"
        );
    }

    #[test]
    fn action_argument_transcript_rejects_bad_markers_and_inactive_handles() {
        create_peer!(request, wire, continuation, dispatcher, peer);
        assert!(
            peer.register_api_arguments(
                &json!({
                    "arguments":{"hook":handle_descriptor("argument", 40, 1, 401)},
                    "args":[{"type":"pod","handle":handle_descriptor("argument", 41, 1, 411)}]
                }),
                "do_action",
            )
            .is_err()
        );

        let registered = peer
            .register_api_arguments(
                &json!({
                    "arguments":{"hook":handle_descriptor("argument", 42, 1, 421)},
                    "args":[{"type":"handle","handle":handle_descriptor("argument", 43, 1, 431)}]
                }),
                "do_action",
            )
            .unwrap();
        let frame = registered.iter().cloned().collect::<HashMap<_, _>>();
        let action_arg = frame["action_arg_0"];
        peer.api_arguments.push(frame);
        peer.api_action_argument_counts.push(Some(1));
        peer.handles.insert(action_arg, HandleState::ReleasePending);
        assert_eq!(
            peer.action_argument_handles().unwrap_err(),
            "action_arg_0_argument_not_active"
        );
    }

    #[test]
    fn empty_action_array_placeholder_is_preserved_as_pod() {
        create_peer!(request, wire, continuation, dispatcher, peer);
        let registered = peer
            .register_api_arguments(
                &json!({
                    "arguments":{"hook":handle_descriptor("argument", 44, 1, 441)},
                    "args":[]
                }),
                "do_action",
            )
            .unwrap();
        peer.api_arguments
            .push(registered.iter().cloned().collect());
        peer.api_action_argument_counts.push(Some(0));

        assert_eq!(
            peer.encode_callback_arguments(PhpCallMode::Array, &[PhpValue::Bytes(Vec::new())],)
                .unwrap(),
            vec![pod(&PhpValue::Bytes(Vec::new()), "expected").unwrap()]
        );
        assert_eq!(
            peer.encode_callback_arguments(PhpCallMode::Array, &[])
                .unwrap(),
            Vec::<Value>::new()
        );
        assert_eq!(
            peer.encode_callback_arguments(
                PhpCallMode::Array,
                &[PhpValue::Bytes(b"unexpected".to_vec())],
            )
            .unwrap_err(),
            "action_empty_argument_placeholder"
        );
        assert_eq!(
            peer.encode_callback_arguments(
                PhpCallMode::Array,
                &[PhpValue::Bytes(Vec::new()), PhpValue::Bytes(Vec::new())],
            )
            .unwrap_err(),
            "action_empty_argument_placeholder"
        );
    }

    #[test]
    fn successful_hook_unbind_preserves_remove_filter_boolean() {
        assert_eq!(
            remove_result_after_unbind(true, Completion::Value(PhpValue::Null)),
            Ok(Completion::Value(PhpValue::Bool(true)))
        );
        assert_eq!(
            remove_result_after_unbind(false, Completion::Value(PhpValue::Null)),
            Ok(Completion::Value(PhpValue::Bool(false)))
        );

        let thrown = PhpHandle::new(
            RequestGeneration::new(71),
            PhpHandleKind::Throwable,
            9,
            1,
            91,
        );
        assert_eq!(
            remove_result_after_unbind(true, Completion::PhpThrowable(thrown)),
            Ok(Completion::PhpThrowable(thrown))
        );
        assert_eq!(
            remove_result_after_unbind(true, Completion::Value(PhpValue::Integer(1))),
            Err("remove_unbind_completion_mismatch")
        );
    }
}
