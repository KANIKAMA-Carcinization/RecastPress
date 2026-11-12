//! Fixed PHP-closure to Rust dispatcher continuation scenario.

use super::*;
use recastpress_wp::{
    dispatcher::{DispatchStep, Dispatcher, DispatcherCallback},
    hooks::{CallbackId, ContextCallback, HookError, PhpInvocation},
    value::PhpValue,
};
use std::{
    collections::HashSet,
    sync::{Arc, Mutex},
};

const REQUEST_GENERATION: u64 = 51;
const ROOT_EFFECT_A: &str = "e-root-a";
const CHILD_EFFECT: &str = "e-child";
const ROOT_EFFECT_B: &str = "e-root-b";
const LATE_EFFECT: &str = "e-late";

fn parse_handle(
    value: &Value,
    request: RequestGeneration,
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
        "object" => PhpHandleKind::Object,
        "closure" => PhpHandleKind::Closure,
        "throwable" => PhpHandleKind::Throwable,
        _ => return Err(format!("{label}_kind")),
    };
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

fn bytes_value(value: &PhpValue, label: &str) -> Result<Vec<u8>, String> {
    match value {
        PhpValue::Bytes(bytes) => Ok(bytes.clone()),
        _ => Err(format!("{label}_must_be_bytes")),
    }
}

fn append_bytes(value: &PhpValue, suffix: &[u8], label: &str) -> Result<PhpValue, HookError> {
    let PhpValue::Bytes(bytes) = value else {
        return Err(HookError::CallbackFailed(format!("{label}_must_be_bytes")));
    };
    let mut output = bytes.clone();
    output.extend_from_slice(suffix);
    Ok(PhpValue::Bytes(output))
}

fn native_callback(
    label: &'static str,
    hook: &'static str,
    suffix: &'static [u8],
    trace: Arc<Mutex<Vec<String>>>,
) -> DispatcherCallback {
    native_callback_with_current(label, hook, suffix, trace)
}

fn native_callback_with_current(
    label: &'static str,
    expected_current: &'static str,
    suffix: &'static [u8],
    trace: Arc<Mutex<Vec<String>>>,
) -> DispatcherCallback {
    ContextCallback::new(
        CallbackId {
            module: "native".into(),
            callback: label.into(),
        },
        1,
        move |dispatcher: &mut Dispatcher, args| {
            if args.len() != 1 || dispatcher.current_hook() != Some(expected_current) {
                return Err(HookError::CallbackFailed(format!("{label}_context")));
            }
            let mut events = trace
                .lock()
                .map_err(|_| HookError::CallbackFailed("native_trace_poisoned".into()))?;
            events.push(label.into());
            drop(events);
            append_bytes(&args[0], suffix, label)
        },
    )
}

struct ExpectedInvocation<'a> {
    callback_id: &'a CallbackId,
    callback_label: &'a str,
    handle: PhpHandle,
    handle_descriptor: &'a Value,
    hook: &'a str,
    priority: i64,
    expected_value: &'a [u8],
    effect_wire: &'a str,
    label: &'a str,
}

fn expect_invocation(
    step: DispatchStep,
    expected: &ExpectedInvocation<'_>,
) -> Result<Vec<u8>, String> {
    let DispatchStep::InvokePhp(PhpInvocation {
        callback,
        handle: observed_handle,
        hook: observed_hook,
        priority: observed_priority,
        args,
        ..
    }) = step
    else {
        return Err(format!("{}_expected_php_invocation", expected.label));
    };
    let actual_value = if args.len() == 1 {
        bytes_value(&args[0], expected.label)?
    } else {
        return Err(format!("{}_argument_count", expected.label));
    };
    if &callback != expected.callback_id
        || observed_handle != expected.handle
        || observed_hook != expected.hook
        || observed_priority != expected.priority
        || actual_value != expected.expected_value
    {
        return Err(format!("{}_invocation_mismatch", expected.label));
    }
    Ok(actual_value)
}

fn php_callback_id(ids: &Value, key: &str) -> Result<CallbackId, String> {
    let value = string(ids, key)?;
    let number = value
        .parse::<u64>()
        .map_err(|_| format!("{key}_id_schema"))?;
    if number == 0 || number.to_string() != value {
        return Err(format!("{key}_id_not_canonical_positive_decimal"));
    }
    Ok(CallbackId {
        module: "php".into(),
        callback: value.into(),
    })
}

fn expect_view(view: &Value, name: &str, label: &str) -> Result<(), String> {
    exact_keys(view, &["name", "alias_same"], label)?;
    if string(view, "name")? != name || view.get("alias_same") != Some(&json!(true)) {
        return Err(format!("{label}_mismatch"));
    }
    Ok(())
}

fn resume_value<'a>(
    frame: &'a Value,
    effect: &str,
    label: &str,
) -> Result<(Vec<u8>, &'a Value), String> {
    exact_keys(frame, &["effect_id", "result"], label)?;
    if string(frame, "effect_id")? != effect {
        return Err(format!("{label}_effect_mismatch"));
    }
    let result = frame
        .get("result")
        .ok_or_else(|| format!("{label}_result_missing"))?;
    exact_keys(result, &["value_hex", "view"], label)?;
    let value = decode_hex(string(result, "value_hex")?, label)?;
    Ok((value, result))
}

fn wire_effect(
    wire: &mut Wire<impl BufRead, impl Write>,
    continuation: &mut Continuation<PhpValue>,
    operation: OperationId,
    step: DispatchStep,
    expected: ExpectedInvocation<'_>,
) -> Result<EffectId, String> {
    let actual_value = expect_invocation(step, &expected)?;
    continuation
        .validate_handle(expected.handle)
        .map_err(|error| format!("{}_handle_mirror:{error:?}", expected.label))?;
    let effect = continuation
        .invoke_php(operation)
        .map_err(|error| format!("{}_invoke_php:{error:?}", expected.label))?;
    // PHP's request-local registry owns resolution; these echoed fields only
    // identify the already-registered closure and grant no Rust-side authority.
    wire.send(
        "invoke_php",
        None,
        json!({
            "callback":expected.callback_label,
            "effect_id":expected.effect_wire,
            "handle":expected.handle_descriptor,
            "hook":expected.hook,
            "priority":expected.priority,
            "value_hex":encode_hex(&actual_value),
        }),
    )?;
    Ok(effect)
}

pub(super) fn run_hooks_peer<R: BufRead, W: Write>(mut wire: Wire<R, W>) -> Result<Value, String> {
    let request = RequestGeneration::new(REQUEST_GENERATION);
    let mut continuation = Continuation::<PhpValue>::new(request, LimitsProfile::default())
        .map_err(|error| format!("hooks_continuation:{error:?}"))?;

    let start = wire.receive("start_root", None)?;
    exact_keys(
        &start,
        &[
            "operation",
            "request_generation",
            "handles",
            "ids",
            "value_hex",
        ],
        "hooks_start",
    )?;
    if string(&start, "operation")? != "hooks_probe"
        || string(&start, "request_generation")? != "51"
    {
        return Err("hooks_start_operation_or_generation".into());
    }
    let input = decode_hex(string(&start, "value_hex")?, "hooks_start_value")?;
    if input != b"seed" {
        return Err("hooks_start_value_mismatch".into());
    }

    let handles_value = start.get("handles").ok_or("hooks_handles_missing")?;
    exact_keys(
        handles_value,
        &["root_a", "root_b", "child", "late", "item"],
        "hooks_handles",
    )?;
    let ids = start.get("ids").ok_or("hooks_ids_missing")?;
    exact_keys(ids, &["root_a", "root_b", "child", "late"], "hooks_ids")?;
    let id_keys = ["root_a", "root_b", "child", "late"];
    let mut callback_ids = Vec::with_capacity(id_keys.len());
    let mut unique_ids = HashSet::new();
    for key in id_keys {
        let id = php_callback_id(ids, key)?;
        if !unique_ids.insert(id.callback.clone()) {
            return Err("hooks_closure_ids_not_distinct".into());
        }
        callback_ids.push(id);
    }
    let [root_a_id, root_b_id, child_id, late_id]: [CallbackId; 4] = callback_ids
        .try_into()
        .map_err(|_| "hooks_callback_id_count")?;

    let handle_keys = ["root_a", "root_b", "child", "late", "item"];
    let mut handles = Vec::with_capacity(handle_keys.len());
    let mut descriptors = Vec::with_capacity(handle_keys.len());
    let mut unique_slots = HashSet::new();
    for (index, key) in handle_keys.into_iter().enumerate() {
        let descriptor = handles_value
            .get(key)
            .ok_or_else(|| format!("{key}_handle_missing"))?;
        let handle = parse_handle(descriptor, request, key)?;
        let expected_kind = if index == 4 {
            PhpHandleKind::Object
        } else {
            PhpHandleKind::Closure
        };
        if handle.kind() != expected_kind || !unique_slots.insert(handle.slot()) {
            return Err(format!("{key}_handle_identity_or_kind"));
        }
        continuation
            .register_handle(handle)
            .map_err(|error| format!("register_{key}:{error:?}"))?;
        handles.push(handle);
        descriptors.push(descriptor.clone());
    }
    let [
        root_a_handle,
        root_b_handle,
        child_handle,
        late_handle,
        item_handle,
    ]: [PhpHandle; 5] = handles.try_into().map_err(|_| "hooks_handle_count")?;
    let [
        root_a_descriptor,
        root_b_descriptor,
        child_descriptor,
        late_descriptor,
        _item_descriptor,
    ]: [Value; 5] = descriptors
        .try_into()
        .map_err(|_| "hooks_descriptor_count")?;
    continuation
        .validate_handle(item_handle)
        .map_err(|error| format!("hooks_item_handle_mirror:{error:?}"))?;

    let trace = Arc::new(Mutex::new(Vec::new()));
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "root",
        5,
        native_callback("r5", "root", b":r5", Arc::clone(&trace)),
    );
    dispatcher.add(
        "root",
        10,
        ContextCallback::php(root_a_id.clone(), 1, root_a_handle)
            .map_err(|error| format!("hooks_root_a_callback:{error:?}"))?,
    );
    dispatcher.add(
        "root",
        10,
        ContextCallback::php(root_b_id.clone(), 1, root_b_handle)
            .map_err(|error| format!("hooks_root_b_callback:{error:?}"))?,
    );
    dispatcher.add(
        "root",
        20,
        native_callback("r20", "root", b":r20", Arc::clone(&trace)),
    );
    dispatcher.add(
        "child",
        3,
        native_callback("c3", "child", b":c3", Arc::clone(&trace)),
    );
    dispatcher.add(
        "child",
        5,
        ContextCallback::php(child_id.clone(), 1, child_handle)
            .map_err(|error| format!("hooks_child_callback:{error:?}"))?,
    );
    dispatcher.add(
        "child",
        7,
        native_callback("c7", "child", b":c7", Arc::clone(&trace)),
    );

    let root = continuation
        .start_root()
        .map_err(|error| format!("hooks_start_root:{error:?}"))?;
    let root_ticket = dispatcher
        .begin_filter("root", PhpValue::Bytes(input), &[])
        .map_err(|error| format!("hooks_begin_root:{error:?}"))?;
    let first_root_step = dispatcher
        .advance(root_ticket)
        .map_err(|error| format!("hooks_advance_root_a:{error:?}"))?;
    let expected_root_value = b"seed:r5";
    let root_effect_a = wire_effect(
        &mut wire,
        &mut continuation,
        root,
        first_root_step,
        ExpectedInvocation {
            callback_id: &root_a_id,
            callback_label: "root_a",
            handle: root_a_handle,
            handle_descriptor: &root_a_descriptor,
            hook: "root",
            priority: 10,
            expected_value: expected_root_value,
            effect_wire: ROOT_EFFECT_A,
            label: "hooks_root_a",
        },
    )?;

    let call = wire.receive("call_core", Some(ROOT_EFFECT_A))?;
    exact_keys(
        &call,
        &["operation", "value_hex", "mutate_parent", "view"],
        "hooks_call_core",
    )?;
    let child_input = decode_hex(string(&call, "value_hex")?, "hooks_call_core_value")?;
    if string(&call, "operation")? != "child_hook"
        || call.get("mutate_parent") != Some(&json!(true))
        || child_input != expected_root_value
    {
        return Err("hooks_call_core_values".into());
    }
    expect_view(
        call.get("view").ok_or("hooks_call_core_view_missing")?,
        "root-before-child",
        "hooks_call_core_view",
    )?;

    let root_b_removed = dispatcher.remove("root", 10, &root_b_id);
    if !root_b_removed || dispatcher.has_at("root", 10, &root_b_id) {
        return Err("hooks_root_b_remove_failed".into());
    }
    dispatcher.add(
        "root",
        15,
        ContextCallback::php(late_id.clone(), 1, late_handle)
            .map_err(|error| format!("hooks_late_callback:{error:?}"))?,
    );
    let late_registered = dispatcher.has_at("root", 15, &late_id);
    if !late_registered {
        return Err("hooks_late_registration_failed".into());
    }

    let child = continuation
        .begin_child(root_effect_a)
        .map_err(|error| format!("hooks_begin_child:{error:?}"))?;
    let child_ticket = dispatcher
        .begin_filter("child", PhpValue::Bytes(child_input), &[])
        .map_err(|error| format!("hooks_begin_child_filter:{error:?}"))?;
    let child_step = dispatcher
        .advance(child_ticket)
        .map_err(|error| format!("hooks_advance_child:{error:?}"))?;
    let child_native_value = b"seed:r5:c3";
    let child_effect = wire_effect(
        &mut wire,
        &mut continuation,
        child,
        child_step,
        ExpectedInvocation {
            callback_id: &child_id,
            callback_label: "child",
            handle: child_handle,
            handle_descriptor: &child_descriptor,
            hook: "child",
            priority: 5,
            expected_value: child_native_value,
            effect_wire: CHILD_EFFECT,
            label: "hooks_child",
        },
    )?;

    let child_resume_frame = wire.receive("resume", Some(ROOT_EFFECT_A))?;
    let (child_php_value, child_resume) =
        resume_value(&child_resume_frame, CHILD_EFFECT, "hooks_child_resume")?;
    expect_view(
        child_resume.get("view").ok_or("hooks_child_view_missing")?,
        "root-before-child:child",
        "hooks_child_view",
    )?;
    let expected_child_php = b"seed:r5:c3:php-child";
    if child_php_value != expected_child_php {
        return Err("hooks_child_php_value_mismatch".into());
    }
    let child_completion = continuation
        .resume(
            child_effect,
            Completion::Value(PhpValue::Bytes(child_php_value.clone())),
        )
        .map_err(|error| format!("hooks_resume_child:{error:?}"))?;
    if child_completion != Completion::Value(PhpValue::Bytes(child_php_value.clone())) {
        return Err("hooks_child_completion_changed".into());
    }
    dispatcher
        .resume_php(child_ticket, PhpValue::Bytes(child_php_value))
        .map_err(|error| format!("hooks_dispatcher_resume_child:{error:?}"))?;
    let child_complete = dispatcher
        .advance(child_ticket)
        .map_err(|error| format!("hooks_advance_child_complete:{error:?}"))?;
    let DispatchStep::Complete(child_output) = child_complete else {
        return Err("hooks_child_expected_complete".into());
    };
    let child_output_bytes = bytes_value(&child_output, "hooks_child_output")?;
    if child_output_bytes != b"seed:r5:c3:php-child:c7"
        || dispatcher.current_hook() != Some("root")
        || dispatcher.hook_stack_depth() != 1
    {
        return Err("hooks_child_result_or_parent_restore".into());
    }
    let parent_effect = continuation
        .finish_operation(child)
        .map_err(|error| format!("hooks_finish_child:{error:?}"))?
        .ok_or("hooks_child_parent_effect_missing")?;
    if parent_effect != root_effect_a {
        return Err("hooks_child_parent_effect_mismatch".into());
    }
    wire.send(
        "return",
        Some(ROOT_EFFECT_A),
        json!({
            "operation":"child_hook",
            "value_hex":encode_hex(&child_output_bytes),
            "current_hook":"root",
            "root_b_removed":root_b_removed,
            "late_registered":late_registered,
        }),
    )?;

    let root_a_resume_frame = wire.receive("resume", None)?;
    let (root_a_value, root_a_resume) =
        resume_value(&root_a_resume_frame, ROOT_EFFECT_A, "hooks_root_a_resume")?;
    let expected_root_a_value = b"seed:r5:c3:php-child:c7:php-root";
    expect_view(
        root_a_resume
            .get("view")
            .ok_or("hooks_root_a_view_missing")?,
        "root-before-child:child:root-after-child",
        "hooks_root_a_view",
    )?;
    if root_a_value != expected_root_a_value {
        return Err("hooks_root_a_php_value_mismatch".into());
    }
    let root_a_completion = continuation
        .resume(
            root_effect_a,
            Completion::Value(PhpValue::Bytes(root_a_value.clone())),
        )
        .map_err(|error| format!("hooks_resume_root_a:{error:?}"))?;
    if root_a_completion != Completion::Value(PhpValue::Bytes(root_a_value.clone())) {
        return Err("hooks_root_a_completion_changed".into());
    }
    dispatcher
        .resume_php(root_ticket, PhpValue::Bytes(root_a_value))
        .map_err(|error| format!("hooks_dispatcher_resume_root_a:{error:?}"))?;
    let root_b_step = dispatcher
        .advance(root_ticket)
        .map_err(|error| format!("hooks_advance_root_b:{error:?}"))?;
    let root_b_input = b"seed:r5:c3:php-child:c7:php-root";
    let root_effect_b = wire_effect(
        &mut wire,
        &mut continuation,
        root,
        root_b_step,
        ExpectedInvocation {
            callback_id: &root_b_id,
            callback_label: "root_b",
            handle: root_b_handle,
            handle_descriptor: &root_b_descriptor,
            hook: "root",
            priority: 10,
            expected_value: root_b_input,
            effect_wire: ROOT_EFFECT_B,
            label: "hooks_root_b",
        },
    )?;
    if dispatcher.has_at("root", 10, &root_b_id) {
        return Err("hooks_root_b_removal_not_visible".into());
    }

    let root_b_resume_frame = wire.receive("resume", None)?;
    let (root_b_value, root_b_resume) =
        resume_value(&root_b_resume_frame, ROOT_EFFECT_B, "hooks_root_b_resume")?;
    expect_view(
        root_b_resume
            .get("view")
            .ok_or("hooks_root_b_view_missing")?,
        "root-before-child:child:root-after-child:b",
        "hooks_root_b_view",
    )?;
    let expected_root_b_value = b"seed:r5:c3:php-child:c7:php-root:php-b";
    if root_b_value != expected_root_b_value {
        return Err("hooks_root_b_php_value_mismatch".into());
    }
    let root_b_completion = continuation
        .resume(
            root_effect_b,
            Completion::Value(PhpValue::Bytes(root_b_value.clone())),
        )
        .map_err(|error| format!("hooks_resume_root_b:{error:?}"))?;
    if root_b_completion != Completion::Value(PhpValue::Bytes(root_b_value.clone())) {
        return Err("hooks_root_b_completion_changed".into());
    }
    dispatcher
        .resume_php(root_ticket, PhpValue::Bytes(root_b_value))
        .map_err(|error| format!("hooks_dispatcher_resume_root_b:{error:?}"))?;
    let late_step = dispatcher
        .advance(root_ticket)
        .map_err(|error| format!("hooks_advance_late:{error:?}"))?;
    let late_input = b"seed:r5:c3:php-child:c7:php-root:php-b";
    let late_effect = wire_effect(
        &mut wire,
        &mut continuation,
        root,
        late_step,
        ExpectedInvocation {
            callback_id: &late_id,
            callback_label: "late",
            handle: late_handle,
            handle_descriptor: &late_descriptor,
            hook: "root",
            priority: 15,
            expected_value: late_input,
            effect_wire: LATE_EFFECT,
            label: "hooks_late",
        },
    )?;

    let late_resume_frame = wire.receive("resume", None)?;
    let (late_value, late_resume) =
        resume_value(&late_resume_frame, LATE_EFFECT, "hooks_late_resume")?;
    expect_view(
        late_resume.get("view").ok_or("hooks_late_view_missing")?,
        "root-before-child:child:root-after-child:b:late",
        "hooks_late_view",
    )?;
    let expected_late_value = b"seed:r5:c3:php-child:c7:php-root:php-b:late";
    if late_value != expected_late_value {
        return Err("hooks_late_php_value_mismatch".into());
    }
    let late_completion = continuation
        .resume(
            late_effect,
            Completion::Value(PhpValue::Bytes(late_value.clone())),
        )
        .map_err(|error| format!("hooks_resume_late:{error:?}"))?;
    if late_completion != Completion::Value(PhpValue::Bytes(late_value.clone())) {
        return Err("hooks_late_completion_changed".into());
    }
    dispatcher
        .resume_php(root_ticket, PhpValue::Bytes(late_value))
        .map_err(|error| format!("hooks_dispatcher_resume_late:{error:?}"))?;
    let root_complete = dispatcher
        .advance(root_ticket)
        .map_err(|error| format!("hooks_advance_root_complete:{error:?}"))?;
    let DispatchStep::Complete(final_value) = root_complete else {
        return Err("hooks_root_expected_complete".into());
    };
    let final_bytes = bytes_value(&final_value, "hooks_final_value")?;
    if final_bytes != b"seed:r5:c3:php-child:c7:php-root:php-b:late:r20"
        || dispatcher.current_hook().is_some()
        || dispatcher.hook_stack_depth() != 0
        || dispatcher.did_filter("root") != 1
        || dispatcher.did_filter("child") != 1
    {
        return Err("hooks_root_final_state".into());
    }
    let root_b_removed = !dispatcher.has_at("root", 10, &root_b_id);
    let late_registered = dispatcher.has_at("root", 15, &late_id);
    let native_trace = trace
        .lock()
        .map_err(|_| "hooks_native_trace_poisoned")?
        .clone();
    if !root_b_removed || !late_registered || native_trace != ["r5", "c3", "c7", "r20"] {
        return Err("hooks_dispatch_summary_mismatch".into());
    }

    continuation
        .finish_operation(root)
        .map_err(|error| format!("hooks_finish_root:{error:?}"))?;
    wire.send(
        "return",
        None,
        json!({
            "operation":"hooks_probe",
            "value_hex":encode_hex(&final_bytes),
            "root_b_removed":root_b_removed,
            "late_registered":late_registered,
            "hook_depth":dispatcher.hook_stack_depth(),
            "current_hook":dispatcher.current_hook(),
        }),
    )?;

    let bytes_before_finish = wire.total;
    let finished = wire.receive("request_finished", None)?;
    exact_keys(
        &finished,
        &["status", "php_observation", "transport"],
        "hooks_request_finished",
    )?;
    if string(&finished, "status")? != "ok" {
        return Err("hooks_request_finished_status".into());
    }
    let observation = finished
        .get("php_observation")
        .ok_or("hooks_php_observation_missing")?;
    exact_keys(
        observation,
        &[
            "alias_same",
            "same_closure_handle",
            "distinct_closures",
            "parent_hook_restored",
            "closed_read_rejected",
            "registry_live_after_close",
            "final_name",
            "returned_value_hex",
            "trace",
        ],
        "hooks_php_observation",
    )?;
    if observation.get("alias_same") != Some(&json!(true))
        || observation.get("same_closure_handle") != Some(&json!(true))
        || observation.get("distinct_closures") != Some(&json!(true))
        || observation.get("parent_hook_restored") != Some(&json!(true))
        || observation.get("closed_read_rejected") != Some(&json!(true))
        || observation.get("registry_live_after_close") != Some(&json!(0))
        || string(observation, "final_name")? != "root-before-child:child:root-after-child:b:late"
        || string(observation, "returned_value_hex")? != encode_hex(&final_bytes)
        || observation.get("trace")
            != Some(&json!([
                "root_a:enter",
                "child:enter",
                "child:exit",
                "root_a:after-child",
                "root_a:exit",
                "root_b",
                "late"
            ]))
    {
        return Err("hooks_php_observation_mismatch".into());
    }
    exact_keys(
        finished.get("transport").ok_or("hooks_transport_missing")?,
        &["frames_sent", "frames_received", "bytes_total"],
        "hooks_transport",
    )?;
    let transport = finished.get("transport").ok_or("hooks_transport_missing")?;
    if transport.get("frames_sent").and_then(Value::as_u64) != wire.in_seq.checked_sub(1)
        || transport.get("frames_received").and_then(Value::as_u64) != Some(wire.out_seq)
        || transport.get("bytes_total").and_then(Value::as_u64) != Some(bytes_before_finish as u64)
    {
        return Err("hooks_transport_counts_or_bytes_mismatch".into());
    }
    let mut extra = Vec::new();
    if (&mut wire.reader)
        .take((MAX_FRAME + 1) as u64)
        .read_until(b'\n', &mut extra)
        .map_err(|error| format!("hooks_eof_check:{error:?}"))?
        != 0
    {
        return Err("extra_php_bytes_after_hooks_finish".into());
    }

    continuation
        .finish_request(root)
        .map_err(|error| format!("hooks_finish_request:{error:?}"))?;
    if continuation.state() != &RequestState::Finished
        || continuation.depth() != 0
        || continuation.validate_handle(root_a_handle) != Err(ContinuationError::RequestClosed)
        || continuation.validate_handle(root_b_handle) != Err(ContinuationError::RequestClosed)
        || continuation.validate_handle(child_handle) != Err(ContinuationError::RequestClosed)
        || continuation.validate_handle(late_handle) != Err(ContinuationError::RequestClosed)
        || continuation.validate_handle(item_handle) != Err(ContinuationError::RequestClosed)
    {
        return Err("hooks_request_close_mirror_mismatch".into());
    }
    let summary = json!({
        "scenario":"hooks",
        "continuation_peer":true,
        "frames_from_php":wire.in_seq,
        "frames_from_rust":wire.out_seq,
        "final_value_hex":encode_hex(&final_bytes),
        "native_trace":native_trace,
        "root_b_removed":root_b_removed,
        "late_registered":late_registered,
        "root_filters":dispatcher.did_filter("root"),
        "child_filters":dispatcher.did_filter("child"),
        "hook_depth_after":dispatcher.hook_stack_depth(),
        "current_hook_after":dispatcher.current_hook(),
        "child_priority_after":dispatcher.current_priority("child"),
        "root_priority_after":dispatcher.current_priority("root"),
        "continuation_depth_after":continuation.depth(),
        "state_after":format!("{:?}", continuation.state()),
        "php_observation":observation,
    });
    eprintln!("V06_HOOKS_RESULT {summary}");
    eprintln!("V06_HOOKS_OK continuation_peer=true");
    Ok(summary)
}

const HOOK_THROW_CLASS: &str = "RuntimeException";
const HOOK_THROW_MESSAGE: &str = "hooks child fixture throwable";
const ROOT_CAUGHT_VALUE: &[u8] = b"seed:r5:php-root-caught";
const ROOT_CAUGHT_FINAL: &[u8] = b"seed:r5:php-root-caught:php-b:late:r20";

fn parse_throwable_completion(
    value: &Value,
    request: RequestGeneration,
    label: &str,
) -> Result<(PhpHandle, Value), String> {
    exact_keys(value, &["kind", "handle", "class", "message"], label)?;
    if string(value, "kind")? != "php_throwable"
        || string(value, "class")? != HOOK_THROW_CLASS
        || string(value, "message")? != HOOK_THROW_MESSAGE
    {
        return Err(format!("{label}_identity_mismatch"));
    }
    let descriptor = value
        .get("handle")
        .ok_or_else(|| format!("{label}_handle_missing"))?;
    let handle = parse_handle(descriptor, request, label)?;
    if handle.kind() != PhpHandleKind::Throwable {
        return Err(format!("{label}_handle_kind"));
    }
    Ok((handle, descriptor.clone()))
}

struct ThrowPhpExpectation<'a> {
    catch_mode: bool,
    final_name: &'a str,
    returned_value_hex: Option<&'a str>,
    trace: &'a [&'a str],
    current_hook: &'a str,
    hook_depth: u64,
}

fn validate_throw_php_observation(
    observation: &Value,
    expected: ThrowPhpExpectation<'_>,
) -> Result<(), String> {
    let ThrowPhpExpectation {
        catch_mode,
        final_name,
        returned_value_hex,
        trace,
        current_hook,
        hook_depth,
    } = expected;
    exact_keys(
        observation,
        &[
            "alias_same",
            "same_closure_handle",
            "distinct_closures",
            "parent_ticket_preserved",
            "closed_read_rejected",
            "registry_live_after_close",
            "final_name",
            "returned_value_hex",
            "trace",
            "throwable_class",
            "throwable_message",
            "child_throw_same_object",
            "root_rethrow_same_object",
            "root_caught_same_object",
            "root_transport_catch_same_object",
            "child_throw_count",
            "root_rethrow_count",
            "root_catch_count",
            "root_transport_catch_count",
            "external_throwable_alive_after_close",
            "current_hook_after",
            "hook_depth_after",
        ],
        "hooks_throw_php_observation",
    )?;
    let root_catch_count = u64::from(catch_mode);
    let root_transport_catch_count = u64::from(!catch_mode);
    if observation.get("alias_same") != Some(&json!(true))
        || observation.get("same_closure_handle") != Some(&json!(true))
        || observation.get("distinct_closures") != Some(&json!(true))
        || observation.get("parent_ticket_preserved") != Some(&json!(true))
        || observation.get("closed_read_rejected") != Some(&json!(true))
        || observation.get("registry_live_after_close") != Some(&json!(0))
        || string(observation, "final_name")? != final_name
        || observation.get("returned_value_hex") != Some(&json!(returned_value_hex))
        || string(observation, "throwable_class")? != HOOK_THROW_CLASS
        || string(observation, "throwable_message")? != HOOK_THROW_MESSAGE
        || observation.get("child_throw_same_object") != Some(&json!(true))
        || observation.get("root_rethrow_same_object") != Some(&json!(true))
        || observation.get("root_caught_same_object") != Some(&json!(catch_mode))
        || observation.get("root_transport_catch_same_object") != Some(&json!(!catch_mode))
        || observation.get("child_throw_count") != Some(&json!(1))
        || observation.get("root_rethrow_count") != Some(&json!(1))
        || observation.get("root_catch_count") != Some(&json!(root_catch_count))
        || observation.get("root_transport_catch_count") != Some(&json!(root_transport_catch_count))
        || observation.get("external_throwable_alive_after_close") != Some(&json!(true))
        || string(observation, "current_hook_after")? != current_hook
        || observation.get("hook_depth_after") != Some(&json!(hook_depth))
        || observation.get("trace") != Some(&json!(trace))
    {
        return Err("hooks_throw_php_observation_mismatch".into());
    }
    Ok(())
}

pub(super) fn run_hooks_throw_peer<R: BufRead, W: Write>(
    mut wire: Wire<R, W>,
    scenario: &str,
) -> Result<Value, String> {
    let catch_mode = match scenario {
        "hooks_catch" => true,
        "hooks_abort" => false,
        _ => return Err("unknown_hooks_throw_scenario".into()),
    };
    let request = RequestGeneration::new(REQUEST_GENERATION);
    let mut continuation = Continuation::<PhpValue>::new(request, LimitsProfile::default())
        .map_err(|error| format!("hooks_throw_continuation:{error:?}"))?;

    let start = wire.receive("start_root", None)?;
    exact_keys(
        &start,
        &[
            "operation",
            "request_generation",
            "handles",
            "ids",
            "value_hex",
        ],
        "hooks_throw_start",
    )?;
    if string(&start, "operation")? != scenario
        || string(&start, "request_generation")? != "51"
        || decode_hex(string(&start, "value_hex")?, "hooks_throw_start_value")? != b"seed"
    {
        return Err("hooks_throw_start_values".into());
    }

    let handles_value = start.get("handles").ok_or("hooks_throw_handles_missing")?;
    exact_keys(
        handles_value,
        &["root_a", "root_b", "child", "late", "item"],
        "hooks_throw_handles",
    )?;
    let ids = start.get("ids").ok_or("hooks_throw_ids_missing")?;
    exact_keys(
        ids,
        &["root_a", "root_b", "child", "late"],
        "hooks_throw_ids",
    )?;
    let mut callback_ids = Vec::with_capacity(4);
    let mut unique_ids = HashSet::new();
    for key in ["root_a", "root_b", "child", "late"] {
        let id = php_callback_id(ids, key)?;
        if !unique_ids.insert(id.callback.clone()) {
            return Err("hooks_throw_closure_ids_not_distinct".into());
        }
        callback_ids.push(id);
    }
    let [root_a_id, root_b_id, child_id, late_id]: [CallbackId; 4] = callback_ids
        .try_into()
        .map_err(|_| "hooks_throw_callback_id_count")?;

    let mut handles = Vec::with_capacity(5);
    let mut descriptors = Vec::with_capacity(5);
    let mut unique_slots = HashSet::new();
    for (index, key) in ["root_a", "root_b", "child", "late", "item"]
        .into_iter()
        .enumerate()
    {
        let descriptor = handles_value
            .get(key)
            .ok_or_else(|| format!("hooks_throw_{key}_handle_missing"))?;
        let handle = parse_handle(descriptor, request, key)?;
        let expected_kind = if index == 4 {
            PhpHandleKind::Object
        } else {
            PhpHandleKind::Closure
        };
        if handle.kind() != expected_kind || !unique_slots.insert(handle.slot()) {
            return Err(format!("hooks_throw_{key}_handle_identity_or_kind"));
        }
        continuation
            .register_handle(handle)
            .map_err(|error| format!("hooks_throw_register_{key}:{error:?}"))?;
        handles.push(handle);
        descriptors.push(descriptor.clone());
    }
    let [
        root_a_handle,
        root_b_handle,
        child_handle,
        late_handle,
        item_handle,
    ]: [PhpHandle; 5] = handles.try_into().map_err(|_| "hooks_throw_handle_count")?;
    let [
        root_a_descriptor,
        root_b_descriptor,
        child_descriptor,
        late_descriptor,
        _item_descriptor,
    ]: [Value; 5] = descriptors
        .try_into()
        .map_err(|_| "hooks_throw_descriptor_count")?;
    continuation
        .validate_handle(item_handle)
        .map_err(|error| format!("hooks_throw_item_handle:{error:?}"))?;

    let trace = Arc::new(Mutex::new(Vec::new()));
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "root",
        5,
        native_callback("r5", "root", b":r5", Arc::clone(&trace)),
    );
    dispatcher.add(
        "root",
        10,
        ContextCallback::php(root_a_id.clone(), 1, root_a_handle)
            .map_err(|error| format!("hooks_throw_root_a_callback:{error:?}"))?,
    );
    dispatcher.add(
        "root",
        10,
        ContextCallback::php(root_b_id.clone(), 1, root_b_handle)
            .map_err(|error| format!("hooks_throw_root_b_callback:{error:?}"))?,
    );
    dispatcher.add(
        "root",
        20,
        native_callback_with_current("r20", "child", b":r20", Arc::clone(&trace)),
    );
    dispatcher.add(
        "child",
        3,
        native_callback("c3", "child", b":c3", Arc::clone(&trace)),
    );
    dispatcher.add(
        "child",
        5,
        ContextCallback::php(child_id.clone(), 1, child_handle)
            .map_err(|error| format!("hooks_throw_child_callback:{error:?}"))?,
    );
    dispatcher.add(
        "child",
        7,
        native_callback("c7", "child", b":c7", Arc::clone(&trace)),
    );

    let root = continuation
        .start_root()
        .map_err(|error| format!("hooks_throw_start_root:{error:?}"))?;
    let root_ticket = dispatcher
        .begin_filter("root", PhpValue::Bytes(b"seed".to_vec()), &[])
        .map_err(|error| format!("hooks_throw_begin_root:{error:?}"))?;
    let root_step = dispatcher
        .advance(root_ticket)
        .map_err(|error| format!("hooks_throw_advance_root_a:{error:?}"))?;
    let root_effect = wire_effect(
        &mut wire,
        &mut continuation,
        root,
        root_step,
        ExpectedInvocation {
            callback_id: &root_a_id,
            callback_label: "root_a",
            handle: root_a_handle,
            handle_descriptor: &root_a_descriptor,
            hook: "root",
            priority: 10,
            expected_value: b"seed:r5",
            effect_wire: ROOT_EFFECT_A,
            label: "hooks_throw_root_a",
        },
    )?;

    let call = wire.receive("call_core", Some(ROOT_EFFECT_A))?;
    exact_keys(
        &call,
        &["operation", "value_hex", "mutate_parent", "view"],
        "hooks_throw_call_core",
    )?;
    let child_input = decode_hex(string(&call, "value_hex")?, "hooks_throw_child_input")?;
    if string(&call, "operation")? != "child_hook"
        || call.get("mutate_parent") != Some(&json!(true))
        || child_input != b"seed:r5"
    {
        return Err("hooks_throw_call_core_values".into());
    }
    expect_view(
        call.get("view")
            .ok_or("hooks_throw_call_core_view_missing")?,
        "root-before-child",
        "hooks_throw_call_core_view",
    )?;

    let root_b_removed = dispatcher.remove("root", 10, &root_b_id);
    if !root_b_removed || dispatcher.has_at("root", 10, &root_b_id) {
        return Err("hooks_throw_root_b_remove_failed".into());
    }
    dispatcher.add(
        "root",
        15,
        ContextCallback::php(late_id.clone(), 1, late_handle)
            .map_err(|error| format!("hooks_throw_late_callback:{error:?}"))?,
    );
    let late_registered = dispatcher.has_at("root", 15, &late_id);
    if !late_registered {
        return Err("hooks_throw_late_registration_failed".into());
    }

    let child = continuation
        .begin_child(root_effect)
        .map_err(|error| format!("hooks_throw_begin_child:{error:?}"))?;
    let child_ticket = dispatcher
        .begin_filter("child", PhpValue::Bytes(child_input), &[])
        .map_err(|error| format!("hooks_throw_begin_child_filter:{error:?}"))?;
    let child_step = dispatcher
        .advance(child_ticket)
        .map_err(|error| format!("hooks_throw_advance_child:{error:?}"))?;
    let child_effect = wire_effect(
        &mut wire,
        &mut continuation,
        child,
        child_step,
        ExpectedInvocation {
            callback_id: &child_id,
            callback_label: "child",
            handle: child_handle,
            handle_descriptor: &child_descriptor,
            hook: "child",
            priority: 5,
            expected_value: b"seed:r5:c3",
            effect_wire: CHILD_EFFECT,
            label: "hooks_throw_child",
        },
    )?;

    let child_resume = wire.receive("resume", Some(ROOT_EFFECT_A))?;
    exact_keys(
        &child_resume,
        &["effect_id", "completion"],
        "hooks_throw_child_resume",
    )?;
    if string(&child_resume, "effect_id")? != CHILD_EFFECT {
        return Err("hooks_throw_child_effect_mismatch".into());
    }
    let (throwable, throwable_descriptor) = parse_throwable_completion(
        child_resume
            .get("completion")
            .ok_or("hooks_throw_child_completion_missing")?,
        request,
        "hooks_throw_child_completion",
    )?;
    if !unique_slots.insert(throwable.slot()) {
        return Err("hooks_throwable_slot_collision".into());
    }
    continuation
        .register_handle(throwable)
        .map_err(|error| format!("hooks_throw_register_throwable:{error:?}"))?;
    continuation
        .validate_handle(throwable)
        .map_err(|error| format!("hooks_throw_validate_throwable:{error:?}"))?;
    dispatcher
        .resume_php_completion(child_ticket, Completion::PhpThrowable(throwable))
        .map_err(|error| format!("hooks_throw_dispatcher_resume_child:{error:?}"))?;
    match dispatcher
        .advance(child_ticket)
        .map_err(|error| format!("hooks_throw_advance_child_throw:{error:?}"))?
    {
        DispatchStep::PhpThrowable(handle) if handle == throwable => {}
        _ => return Err("hooks_throw_child_dispatch_completion_mismatch".into()),
    }
    let child_ticket_retired = matches!(
        dispatcher.advance(child_ticket),
        Err(HookError::InvalidDispatchTicket)
    );
    let parent_ticket_preserved = matches!(
        dispatcher.advance(root_ticket),
        Err(HookError::PhpCallbackPending)
    );
    if !child_ticket_retired
        || !parent_ticket_preserved
        || dispatcher.current_hook() != Some("child")
        || dispatcher.hook_stack_depth() != 2
        || dispatcher.current_priority("child") != Some(5)
        || dispatcher.current_priority("root") != Some(10)
    {
        return Err("hooks_throw_child_parent_ticket_or_public_stack_mismatch".into());
    }
    let child_completion = continuation
        .resume(child_effect, Completion::PhpThrowable(throwable))
        .map_err(|error| format!("hooks_throw_resume_child:{error:?}"))?;
    if child_completion != Completion::PhpThrowable(throwable) {
        return Err("hooks_throw_child_completion_changed".into());
    }
    let parent_effect = continuation
        .finish_operation(child)
        .map_err(|error| format!("hooks_throw_finish_child:{error:?}"))?
        .ok_or("hooks_throw_child_parent_effect_missing")?;
    if parent_effect != root_effect {
        return Err("hooks_throw_child_parent_effect_mismatch".into());
    }
    let child_return = json!({
        "operation":"child_hook",
        "completion":{
            "kind":"php_throwable",
            "handle":throwable_descriptor,
            "class":HOOK_THROW_CLASS,
            "message":HOOK_THROW_MESSAGE,
        },
        "current_hook":dispatcher.current_hook(),
        "hook_depth":dispatcher.hook_stack_depth(),
        "child_ticket_retired":child_ticket_retired,
        "parent_ticket_preserved":parent_ticket_preserved,
    });
    wire.send("return", Some(ROOT_EFFECT_A), child_return)?;

    let root_resume = wire.receive("resume", None)?;
    let final_bytes = if catch_mode {
        let (root_value, root_result) =
            resume_value(&root_resume, ROOT_EFFECT_A, "hooks_catch_root_resume")?;
        expect_view(
            root_result
                .get("view")
                .ok_or("hooks_catch_root_view_missing")?,
            "root-before-child:child:root-after-child",
            "hooks_catch_root_view",
        )?;
        if root_value != ROOT_CAUGHT_VALUE {
            return Err("hooks_catch_root_value_mismatch".into());
        }
        let root_completion = continuation
            .resume(
                root_effect,
                Completion::Value(PhpValue::Bytes(root_value.clone())),
            )
            .map_err(|error| format!("hooks_catch_resume_root:{error:?}"))?;
        if root_completion != Completion::Value(PhpValue::Bytes(root_value.clone())) {
            return Err("hooks_catch_root_completion_changed".into());
        }
        dispatcher
            .resume_php(root_ticket, PhpValue::Bytes(root_value))
            .map_err(|error| format!("hooks_catch_dispatcher_resume_root:{error:?}"))?;

        let root_b_step = dispatcher
            .advance(root_ticket)
            .map_err(|error| format!("hooks_catch_advance_root_b:{error:?}"))?;
        let root_b_effect = wire_effect(
            &mut wire,
            &mut continuation,
            root,
            root_b_step,
            ExpectedInvocation {
                callback_id: &root_b_id,
                callback_label: "root_b",
                handle: root_b_handle,
                handle_descriptor: &root_b_descriptor,
                hook: "root",
                priority: 10,
                expected_value: ROOT_CAUGHT_VALUE,
                effect_wire: ROOT_EFFECT_B,
                label: "hooks_catch_root_b",
            },
        )?;
        if dispatcher.current_hook() != Some("child")
            || dispatcher.hook_stack_depth() != 2
            || dispatcher.current_priority("child") != Some(5)
        {
            return Err("hooks_catch_root_b_public_stack_mismatch".into());
        }
        let root_b_resume = wire.receive("resume", None)?;
        let (root_b_value, root_b_result) =
            resume_value(&root_b_resume, ROOT_EFFECT_B, "hooks_catch_root_b_resume")?;
        expect_view(
            root_b_result
                .get("view")
                .ok_or("hooks_catch_root_b_view_missing")?,
            "root-before-child:child:root-after-child:b",
            "hooks_catch_root_b_view",
        )?;
        let expected_root_b = b"seed:r5:php-root-caught:php-b";
        if root_b_value != expected_root_b {
            return Err("hooks_catch_root_b_value_mismatch".into());
        }
        let root_b_completion = continuation
            .resume(
                root_b_effect,
                Completion::Value(PhpValue::Bytes(root_b_value.clone())),
            )
            .map_err(|error| format!("hooks_catch_resume_root_b:{error:?}"))?;
        if root_b_completion != Completion::Value(PhpValue::Bytes(root_b_value.clone())) {
            return Err("hooks_catch_root_b_completion_changed".into());
        }
        dispatcher
            .resume_php(root_ticket, PhpValue::Bytes(root_b_value))
            .map_err(|error| format!("hooks_catch_dispatcher_resume_root_b:{error:?}"))?;

        let late_step = dispatcher
            .advance(root_ticket)
            .map_err(|error| format!("hooks_catch_advance_late:{error:?}"))?;
        let late_input = b"seed:r5:php-root-caught:php-b";
        let late_effect = wire_effect(
            &mut wire,
            &mut continuation,
            root,
            late_step,
            ExpectedInvocation {
                callback_id: &late_id,
                callback_label: "late",
                handle: late_handle,
                handle_descriptor: &late_descriptor,
                hook: "root",
                priority: 15,
                expected_value: late_input,
                effect_wire: LATE_EFFECT,
                label: "hooks_catch_late",
            },
        )?;
        let late_resume = wire.receive("resume", None)?;
        let (late_value, late_result) =
            resume_value(&late_resume, LATE_EFFECT, "hooks_catch_late_resume")?;
        expect_view(
            late_result
                .get("view")
                .ok_or("hooks_catch_late_view_missing")?,
            "root-before-child:child:root-after-child:b:late",
            "hooks_catch_late_view",
        )?;
        let expected_late = b"seed:r5:php-root-caught:php-b:late";
        if late_value != expected_late {
            return Err("hooks_catch_late_value_mismatch".into());
        }
        let late_completion = continuation
            .resume(
                late_effect,
                Completion::Value(PhpValue::Bytes(late_value.clone())),
            )
            .map_err(|error| format!("hooks_catch_resume_late:{error:?}"))?;
        if late_completion != Completion::Value(PhpValue::Bytes(late_value.clone())) {
            return Err("hooks_catch_late_completion_changed".into());
        }
        dispatcher
            .resume_php(root_ticket, PhpValue::Bytes(late_value))
            .map_err(|error| format!("hooks_catch_dispatcher_resume_late:{error:?}"))?;
        Some(
            match dispatcher
                .advance(root_ticket)
                .map_err(|error| format!("hooks_catch_advance_root_complete:{error:?}"))?
            {
                DispatchStep::Complete(value) => bytes_value(&value, "hooks_catch_final_value")?,
                _ => return Err("hooks_catch_root_expected_complete".into()),
            },
        )
    } else {
        exact_keys(
            &root_resume,
            &["effect_id", "completion"],
            "hooks_abort_root_resume",
        )?;
        if string(&root_resume, "effect_id")? != ROOT_EFFECT_A {
            return Err("hooks_abort_root_effect_mismatch".into());
        }
        let (root_throwable, root_descriptor) = parse_throwable_completion(
            root_resume
                .get("completion")
                .ok_or("hooks_abort_root_completion_missing")?,
            request,
            "hooks_abort_root_completion",
        )?;
        if root_throwable != throwable || root_descriptor != throwable_descriptor {
            return Err("hooks_abort_throwable_identity_changed".into());
        }
        let root_completion = continuation
            .resume(root_effect, Completion::PhpThrowable(root_throwable))
            .map_err(|error| format!("hooks_abort_resume_root:{error:?}"))?;
        if root_completion != Completion::PhpThrowable(throwable) {
            return Err("hooks_abort_root_completion_changed".into());
        }
        dispatcher
            .resume_php_completion(root_ticket, Completion::PhpThrowable(root_throwable))
            .map_err(|error| format!("hooks_abort_dispatcher_resume_root:{error:?}"))?;
        match dispatcher
            .advance(root_ticket)
            .map_err(|error| format!("hooks_abort_advance_root_throw:{error:?}"))?
        {
            DispatchStep::PhpThrowable(handle) if handle == throwable => {}
            _ => return Err("hooks_abort_root_dispatch_completion_mismatch".into()),
        }
        None
    };

    let native_trace = trace
        .lock()
        .map_err(|_| "hooks_throw_native_trace_poisoned")?
        .clone();
    let expected_native: &[&str] = if catch_mode {
        &["r5", "c3", "r20"]
    } else {
        &["r5", "c3"]
    };
    if native_trace != expected_native
        || !root_b_removed
        || !late_registered
        || dispatcher.did_filter("root") != 1
        || dispatcher.did_filter("child") != 1
    {
        return Err("hooks_throw_dispatch_summary_mismatch".into());
    }

    let parent_ticket_preserved = true;
    let root_ticket_retired_by_throwable = !catch_mode;
    let root_b_removed_after = !dispatcher.has_at("root", 10, &root_b_id);
    let late_registered_after = dispatcher.has_at("root", 15, &late_id);
    if !root_b_removed_after || !late_registered_after {
        return Err("hooks_throw_registration_state_changed".into());
    }

    let (
        hook_depth_after,
        current_hook_after,
        child_priority_after,
        root_priority_after,
        mut continuation_depth_after,
        mut state_after,
        abort_observation,
        request_status,
        final_value,
        final_name,
        php_trace,
        returned_value_hex,
    ) = if catch_mode {
        if final_bytes.as_deref() != Some(ROOT_CAUGHT_FINAL)
            || dispatcher.hook_stack_depth() != 1
            || dispatcher.current_hook() != Some("root")
            || dispatcher.current_priority("child") != Some(5)
            || dispatcher.current_priority("root").is_some()
        {
            return Err("hooks_catch_final_public_stack_or_value_mismatch".into());
        }
        continuation
            .finish_operation(root)
            .map_err(|error| format!("hooks_catch_finish_root:{error:?}"))?;
        wire.send(
            "return",
            None,
            json!({
                "operation":"hooks_catch",
                "value_hex":encode_hex(final_bytes.as_deref().ok_or("hooks_catch_final_value_missing")?),
                "root_b_removed":!dispatcher.has_at("root", 10, &root_b_id),
                "late_registered":dispatcher.has_at("root", 15, &late_id),
                "hook_depth":dispatcher.hook_stack_depth(),
                "current_hook":dispatcher.current_hook(),
            }),
        )?;
        (
            dispatcher.hook_stack_depth(),
            dispatcher.current_hook().map(str::to_owned),
            dispatcher.current_priority("child"),
            dispatcher.current_priority("root"),
            continuation.depth(),
            format!("{:?}", continuation.state()),
            Value::Null,
            "ok",
            final_bytes,
            "root-before-child:child:root-after-child:b:late",
            vec![
                "root_a:enter",
                "child:enter",
                "child:throw",
                "child:transport_catch",
                "child:finally",
                "root_a:child_rethrow",
                "root_a:caught_same_object",
                "root_a:resume",
                "root_a:exit",
                "root_b",
                "late",
            ],
            Some(encode_hex(ROOT_CAUGHT_FINAL)),
        )
    } else {
        let root_ticket_retired = matches!(
            dispatcher.advance(root_ticket),
            Err(HookError::InvalidDispatchTicket)
        );
        continuation.abort();
        let late_resume_rejected = matches!(
            continuation.resume(
                root_effect,
                Completion::Value(PhpValue::Bytes(b"late".to_vec()))
            ),
            Err(ContinuationError::RequestClosed)
        );
        let finish_request_after_abort_rejected = matches!(
            continuation.finish_request(root),
            Err(ContinuationError::RequestClosed)
        );
        let hook_depth = dispatcher.hook_stack_depth();
        let current_hook = dispatcher.current_hook().map(str::to_owned);
        let cont_depth = continuation.depth();
        let state = format!("{:?}", continuation.state());
        let child_priority = dispatcher.current_priority("child");
        let root_priority = dispatcher.current_priority("root");
        if !root_ticket_retired
            || !late_resume_rejected
            || !finish_request_after_abort_rejected
            || hook_depth != 2
            || current_hook.as_deref() != Some("child")
            || child_priority != Some(5)
            || root_priority != Some(10)
            || cont_depth != 0
            || state != "Aborted"
        {
            return Err("hooks_abort_cleanup_or_public_stack_mismatch".into());
        }
        let abort_payload = json!({
            "reason":"php_throwable",
            "completion":{
                "kind":"php_throwable",
                "handle":throwable_descriptor,
                "class":HOOK_THROW_CLASS,
                "message":HOOK_THROW_MESSAGE,
            },
            "root_ticket_retired":root_ticket_retired,
            "root_b_removed":root_b_removed_after,
            "late_registered":late_registered_after,
            "current_hook":current_hook,
            "hook_depth":hook_depth,
            "continuation_depth":cont_depth,
            "state":state,
            "late_resume_rejected":late_resume_rejected,
            "finish_request_after_abort_rejected":finish_request_after_abort_rejected,
        });
        wire.send("abort", None, abort_payload.clone())?;
        (
            hook_depth,
            current_hook,
            child_priority,
            root_priority,
            cont_depth,
            state,
            abort_payload,
            "throwable",
            None,
            "root-before-child:child",
            vec![
                "root_a:enter",
                "child:enter",
                "child:throw",
                "child:transport_catch",
                "child:finally",
                "root_a:child_rethrow",
                "root_a:transport_catch",
                "root_a:finally",
            ],
            None,
        )
    };

    let bytes_before_finish = wire.total;
    let finished = wire.receive("request_finished", None)?;
    exact_keys(
        &finished,
        &["status", "php_observation", "transport"],
        "hooks_throw_request_finished",
    )?;
    if string(&finished, "status")? != request_status {
        return Err("hooks_throw_request_finished_status".into());
    }
    let observation = finished
        .get("php_observation")
        .ok_or("hooks_throw_php_observation_missing")?;
    validate_throw_php_observation(
        observation,
        ThrowPhpExpectation {
            catch_mode,
            final_name,
            returned_value_hex: returned_value_hex.as_deref(),
            trace: &php_trace,
            current_hook: current_hook_after
                .as_deref()
                .ok_or("hooks_throw_current_hook_missing")?,
            hook_depth: hook_depth_after as u64,
        },
    )?;
    let transport = finished
        .get("transport")
        .ok_or("hooks_throw_transport_missing")?;
    exact_keys(
        transport,
        &["frames_sent", "frames_received", "bytes_total"],
        "hooks_throw_transport",
    )?;
    let expected_frames_from_php = if catch_mode { 7 } else { 5 };
    let expected_frames_from_rust = if catch_mode { 6 } else { 4 };
    if transport.get("frames_sent").and_then(Value::as_u64) != Some(expected_frames_from_php - 1)
        || transport.get("frames_received").and_then(Value::as_u64)
            != Some(expected_frames_from_rust)
        || transport.get("bytes_total").and_then(Value::as_u64) != Some(bytes_before_finish as u64)
    {
        return Err("hooks_throw_transport_counts_or_bytes_mismatch".into());
    }
    let mut extra = Vec::new();
    if (&mut wire.reader)
        .take((MAX_FRAME + 1) as u64)
        .read_until(b'\n', &mut extra)
        .map_err(|error| format!("hooks_throw_eof_check:{error:?}"))?
        != 0
    {
        return Err("extra_php_bytes_after_hooks_throw_finish".into());
    }

    if catch_mode {
        continuation
            .finish_request(root)
            .map_err(|error| format!("hooks_catch_finish_request:{error:?}"))?;
        if continuation.state() != &RequestState::Finished || continuation.depth() != 0 {
            return Err("hooks_catch_request_close_mismatch".into());
        }
        continuation_depth_after = continuation.depth();
        state_after = format!("{:?}", continuation.state());
        for handle in [
            root_a_handle,
            root_b_handle,
            child_handle,
            late_handle,
            item_handle,
            throwable,
        ] {
            if continuation.validate_handle(handle) != Err(ContinuationError::RequestClosed) {
                return Err("hooks_catch_handle_open_after_finish".into());
            }
        }
    }

    let summary = json!({
        "scenario":scenario,
        "continuation_peer":true,
        "frames_from_php":wire.in_seq,
        "frames_from_rust":wire.out_seq,
        "final_value_hex":final_value.as_ref().map(|value| encode_hex(value)),
        "native_trace":native_trace,
        "root_b_removed":root_b_removed_after,
        "late_registered":late_registered_after,
        "root_filters":dispatcher.did_filter("root"),
        "child_filters":dispatcher.did_filter("child"),
        "hook_depth_after":hook_depth_after,
        "continuation_depth_after":continuation_depth_after,
        "state_after":state_after,
        "php_observation":observation,
        "current_hook_after":current_hook_after,
        "child_priority_after":child_priority_after,
        "root_priority_after":root_priority_after,
        "parent_ticket_preserved":parent_ticket_preserved,
        "child_ticket_retired":child_ticket_retired,
        "root_ticket_retired_by_throwable":root_ticket_retired_by_throwable,
        "abort_observation":abort_observation,
    });
    eprintln!("V06_HOOKS_RESULT {summary}");
    eprintln!("V06_HOOKS_OK continuation_peer=true");
    Ok(summary)
}
