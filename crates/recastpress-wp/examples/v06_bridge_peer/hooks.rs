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
    ContextCallback::new(
        CallbackId {
            module: "native".into(),
            callback: label.into(),
        },
        1,
        move |dispatcher: &mut Dispatcher, args| {
            if args.len() != 1 || dispatcher.current_hook() != Some(hook) {
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
        "continuation_depth_after":continuation.depth(),
        "state_after":format!("{:?}", continuation.state()),
        "php_observation":observation,
    });
    eprintln!("V06_HOOKS_RESULT {summary}");
    eprintln!("V06_HOOKS_OK continuation_peer=true");
    Ok(summary)
}
