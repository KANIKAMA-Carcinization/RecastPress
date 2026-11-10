//! Request-local typed handle registry continuation scenario.

use super::*;
use std::collections::HashSet;

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
    let parsed_request = request_text
        .parse::<u64>()
        .map_err(|_| format!("{label}_request_schema"))?;
    if parsed_request.to_string() != request_text || parsed_request != request.get() {
        return Err(format!("{label}_request_mismatch"));
    }
    let kind = match string(value, "kind")? {
        "object" => PhpHandleKind::Object,
        "reference_cell" => PhpHandleKind::ReferenceCell,
        "closure" => PhpHandleKind::Closure,
        "throwable" => PhpHandleKind::Throwable,
        "array" => PhpHandleKind::Array,
        "resource" => PhpHandleKind::Resource,
        _ => return Err(format!("{label}_kind")),
    };
    let slot = value
        .get("slot")
        .and_then(Value::as_u64)
        .and_then(|n| u32::try_from(n).ok())
        .ok_or_else(|| format!("{label}_slot"))?;
    let generation = value
        .get("generation")
        .and_then(Value::as_u64)
        .and_then(|n| u32::try_from(n).ok())
        .filter(|n| *n > 0)
        .ok_or_else(|| format!("{label}_generation"))?;
    let token_text = string(value, "token")?;
    if token_text.len() != 16
        || !token_text
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(format!("{label}_token"));
    }
    let token = u64::from_str_radix(token_text, 16).map_err(|_| format!("{label}_token"))?;
    Ok(PhpHandle::new(request, kind, slot, generation, token))
}

fn expect_handle(value: &Value, handle: PhpHandle, label: &str) -> Result<(), String> {
    let observed = parse_handle(value, handle.request(), label)?;
    if observed != handle {
        return Err(format!("{label}_changed"));
    }
    Ok(())
}

fn get_handle(value: &Value, key: &str, request: RequestGeneration) -> Result<PhpHandle, String> {
    parse_handle(
        value.get(key).ok_or_else(|| format!("{key}_missing"))?,
        request,
        key,
    )
}

fn expect_kind(handle: PhpHandle, kind: PhpHandleKind, label: &str) -> Result<(), String> {
    if handle.kind() != kind {
        return Err(format!("{label}_kind_mismatch"));
    }
    Ok(())
}

fn expect_view(view: &Value, expected: &Value, label: &str) -> Result<(), String> {
    exact_keys(
        view,
        &[
            "alias_name",
            "cell_name",
            "closure_name",
            "throwable_same",
            "array_value",
            "resource_alive",
            "live",
        ],
        label,
    )?;
    if view != expected {
        return Err(format!("{label}_mismatch"));
    }
    Ok(())
}

fn expect_transport(
    value: &Value,
    wire: &Wire<impl BufRead, impl Write>,
    bytes_before_finish: usize,
) -> Result<(), String> {
    exact_keys(
        value,
        &["frames_sent", "frames_received", "bytes_total"],
        "handles_transport",
    )?;
    // PHP snapshots the counters before its RequestFinished frame is sent.
    if value.get("frames_sent").and_then(Value::as_u64) != wire.in_seq.checked_sub(1)
        || value.get("frames_received").and_then(Value::as_u64) != Some(wire.out_seq)
        || value.get("bytes_total").and_then(Value::as_u64) != Some(bytes_before_finish as u64)
    {
        return Err("handles_transport_counts_or_limit".into());
    }
    Ok(())
}

pub(super) fn run_handles_peer<R: BufRead, W: Write>(
    mut wire: Wire<R, W>,
    abort_mode: bool,
) -> Result<Value, String> {
    let scenario = if abort_mode {
        "handles_abort"
    } else {
        "handles"
    };
    let request = RequestGeneration::new(if abort_mode { 42 } else { 41 });
    let mut cont = Continuation::<Value>::new(request, LimitsProfile::default())
        .map_err(|e| format!("handles_continuation:{e}"))?;
    let start = wire.receive("start_root", None)?;
    exact_keys(
        &start,
        &["operation", "request_generation", "handles", "view"],
        "handles_start",
    )?;
    if string(&start, "operation")? != "handles_probe"
        || string(&start, "request_generation")? != if abort_mode { "42" } else { "41" }
    {
        return Err("handles_start_operation_or_generation".into());
    }
    let handles_value = start
        .get("handles")
        .ok_or("handles_start_handles_missing")?;
    exact_keys(
        handles_value,
        &[
            "original",
            "cell",
            "closure",
            "throwable",
            "array",
            "resource",
            "lifetime",
            "cycle",
        ],
        "handles_start_handles",
    )?;
    let keys = [
        "original",
        "cell",
        "closure",
        "throwable",
        "array",
        "resource",
        "lifetime",
        "cycle",
    ];
    let kinds = [
        PhpHandleKind::Object,
        PhpHandleKind::ReferenceCell,
        PhpHandleKind::Closure,
        PhpHandleKind::Throwable,
        PhpHandleKind::Array,
        PhpHandleKind::Resource,
        PhpHandleKind::Object,
        PhpHandleKind::Object,
    ];
    let mut handles = Vec::with_capacity(keys.len());
    let mut identities = HashSet::new();
    for (key, kind) in keys.iter().zip(kinds) {
        let handle = get_handle(handles_value, key, request)?;
        expect_kind(handle, kind, key)?;
        if !identities.insert(handle.slot()) {
            return Err("handles_duplicate_identity".into());
        }
        cont.register_handle(handle)
            .map_err(|e| format!("register_{key}:{e}"))?;
        handles.push(handle);
    }
    let [
        original,
        cell,
        closure,
        throwable,
        array,
        resource,
        lifetime,
        cycle,
    ]: [PhpHandle; 8] = handles
        .clone()
        .try_into()
        .map_err(|_| "handles_initial_count")?;
    let start_view = start.get("view").ok_or("handles_start_view_missing")?;
    exact_keys(
        start_view,
        &[
            "original_name",
            "cell_name",
            "closure_name",
            "array_value",
            "resource_type",
            "live",
            "alias_same",
        ],
        "handles_start_view",
    )?;
    if start_view
        != &json!({
            "original_name":"original", "cell_name":"original", "closure_name":"original",
            "array_value":[2,4], "resource_type":"stream", "live":8, "alias_same":true
        })
    {
        return Err("handles_start_view_mismatch".into());
    }

    let root = cont
        .start_root()
        .map_err(|e| format!("handles_start_root:{e}"))?;
    let root_effect = cont
        .invoke_php(root)
        .map_err(|e| format!("handles_invoke_root:{e}"))?;
    wire.send(
        "invoke_php",
        None,
        json!({"callback":"registry_root_callback","effect_id":"e-root"}),
    )?;

    let call = wire.receive("call_core", Some("e-root"))?;
    exact_keys(
        &call,
        &["operation", "replacement", "view"],
        "handles_call_core",
    )?;
    if string(&call, "operation")? != "handles_child" {
        return Err("handles_child_operation".into());
    }
    for (key, handle) in keys.iter().zip(handles.iter()) {
        cont.validate_handle(*handle)
            .map_err(|e| format!("initial_{key}_invalid_before_child:{e}"))?;
    }
    let replacement = get_handle(&call, "replacement", request)?;
    expect_kind(replacement, PhpHandleKind::Object, "replacement")?;
    cont.register_handle(replacement)
        .map_err(|e| format!("handles_register_replacement:{e}"))?;
    let child_view = call.get("view").ok_or("handles_child_view_missing")?;
    exact_keys(
        child_view,
        &[
            "alias_name",
            "cell_name",
            "closure_name",
            "retained_original_live",
            "reference_rebound",
            "forged_rejections",
            "live",
        ],
        "handles_child_view",
    )?;
    if child_view
        != &json!({"alias_name":"original","cell_name":"replacement","closure_name":"original","retained_original_live":true,"reference_rebound":true,"forged_rejections":3,"live":9})
    {
        return Err("handles_child_view_mismatch".into());
    }
    let child = cont
        .begin_child(root_effect)
        .map_err(|e| format!("handles_begin_child:{e}"))?;
    let child_effect = cont
        .invoke_php(child)
        .map_err(|e| format!("handles_invoke_child:{e}"))?;
    wire.send(
        "invoke_php",
        None,
        json!({"callback":"registry_nested_callback","effect_id":"e-child"}),
    )?;

    let child_resume = wire.receive("resume", Some("e-root"))?;
    exact_keys(
        &child_resume,
        &["effect_id", "result"],
        "handles_child_resume",
    )?;
    if string(&child_resume, "effect_id")? != "e-child" {
        return Err("handles_child_effect".into());
    }
    let result = child_resume
        .get("result")
        .ok_or("handles_child_result_missing")?;
    exact_keys(
        result,
        &["released", "reused", "view"],
        "handles_child_result",
    )?;
    let released = result.get("released").ok_or("handles_released_missing")?;
    exact_keys(released, &["original", "lifetime"], "handles_released")?;
    expect_handle(
        released
            .get("original")
            .ok_or("released_original_missing")?,
        original,
        "released_original",
    )?;
    expect_handle(
        released
            .get("lifetime")
            .ok_or("released_lifetime_missing")?,
        lifetime,
        "released_lifetime",
    )?;
    cont.unregister_handle(original)
        .map_err(|e| format!("unregister_original:{e}"))?;
    cont.unregister_handle(lifetime)
        .map_err(|e| format!("unregister_lifetime:{e}"))?;
    let expired_rejections = [original, lifetime]
        .into_iter()
        .filter(|h| {
            matches!(
                cont.validate_handle(*h),
                Err(ContinuationError::InvalidHandle)
            )
        })
        .count();
    if expired_rejections != 2 {
        return Err("handles_expired_not_rejected".into());
    }
    let reused = get_handle(result, "reused", request)?;
    expect_kind(reused, PhpHandleKind::Object, "reused")?;
    if reused.slot() != original.slot() || reused.generation() <= original.generation() {
        return Err("handles_slot_generation_not_reused".into());
    }
    cont.register_handle(reused)
        .map_err(|e| format!("register_reused:{e}"))?;
    if !matches!(
        cont.validate_handle(original),
        Err(ContinuationError::InvalidHandle)
    ) || !matches!(
        cont.validate_handle(lifetime),
        Err(ContinuationError::InvalidHandle)
    ) {
        return Err("handles_old_generation_accepted".into());
    }
    for h in [
        cell,
        closure,
        throwable,
        array,
        resource,
        cycle,
        replacement,
        reused,
    ] {
        cont.validate_handle(h)
            .map_err(|e| format!("handles_live_metadata:{e}"))?;
    }
    let child_view_after = result
        .get("view")
        .ok_or("handles_child_result_view_missing")?;
    exact_keys(
        child_view_after,
        &[
            "alias_name",
            "cell_name",
            "closure_name",
            "slot_reused",
            "old_generation_rejected",
            "lifetime_destroyed",
            "reentrant_release_safe",
            "stale_rejections",
            "live",
        ],
        "handles_child_result_view",
    )?;
    if child_view_after
        != &json!({"alias_name":"original","cell_name":"replacement","closure_name":"original","slot_reused":true,"old_generation_rejected":true,"lifetime_destroyed":true,"reentrant_release_safe":true,"stale_rejections":3,"live":8})
    {
        return Err("handles_child_result_view_mismatch".into());
    }
    let completion = cont
        .resume(child_effect, Completion::Value(result.clone()))
        .map_err(|e| format!("handles_resume_child:{e}"))?;
    if completion != Completion::Value(result.clone()) {
        return Err("handles_child_completion_lost".into());
    }
    let parent_effect = cont
        .finish_operation(child)
        .map_err(|e| format!("handles_finish_child:{e}"))?
        .ok_or("handles_parent_effect_missing")?;
    if parent_effect != root_effect || cont.depth() != 1 {
        return Err("handles_child_return_state".into());
    }
    wire.send(
        "return",
        Some("e-root"),
        json!({"operation":"handles_child","expired_rejected":true,"slot_reused":true}),
    )?;

    let root_resume = wire.receive("resume", None)?;
    exact_keys(
        &root_resume,
        &["effect_id", "result"],
        "handles_root_resume",
    )?;
    if string(&root_resume, "effect_id")? != "e-root" {
        return Err("handles_root_effect".into());
    }
    let root_result = root_resume
        .get("result")
        .ok_or("handles_root_result_missing")?;
    exact_keys(root_result, &["view"], "handles_root_result")?;
    let root_view = root_result
        .get("view")
        .ok_or("handles_root_result_view_missing")?;
    expect_view(
        root_view,
        &json!({"alias_name":"original","cell_name":"replacement","closure_name":"original","throwable_same":true,"array_value":[2,4],"resource_alive":true,"live":8}),
        "handles_root_result_view",
    )?;
    for h in [
        cell,
        closure,
        throwable,
        array,
        resource,
        cycle,
        replacement,
        reused,
    ] {
        cont.validate_handle(h)
            .map_err(|e| format!("handles_final_live_metadata:{e}"))?;
    }
    if !matches!(
        cont.validate_handle(original),
        Err(ContinuationError::InvalidHandle)
    ) || !matches!(
        cont.validate_handle(lifetime),
        Err(ContinuationError::InvalidHandle)
    ) {
        return Err("handles_final_old_handle_accepted".into());
    }
    let root_completion = cont
        .resume(root_effect, Completion::Value(root_result.clone()))
        .map_err(|e| format!("handles_resume_root:{e}"))?;
    if root_completion != Completion::Value(root_result.clone()) {
        return Err("handles_root_completion_lost".into());
    }
    cont.finish_operation(root)
        .map_err(|e| format!("handles_finish_root:{e}"))?;
    if cont.state() != &RequestState::RootReturned {
        return Err("handles_root_not_returned".into());
    }
    if abort_mode {
        cont.abort();
    }
    let terminal_payload =
        json!({"operation":"handles_probe","status":if abort_mode {"aborted"} else {"finished"}});
    if abort_mode {
        wire.send("abort", None, terminal_payload)?;
    } else {
        wire.send("return", None, terminal_payload)?;
    }

    let bytes_before_finish = wire.total;
    let finished = wire.receive("request_finished", None)?;
    exact_keys(
        &finished,
        &["status", "php_observation", "transport"],
        "handles_request_finished",
    )?;
    let expected_status = if abort_mode { "aborted" } else { "ok" };
    if string(&finished, "status")? != expected_status {
        return Err("handles_finished_status".into());
    }
    let observation = finished
        .get("php_observation")
        .ok_or("handles_php_observation_missing")?;
    exact_keys(
        observation,
        &[
            "registry_closed",
            "all_closed_rejected",
            "external_alias_preserved",
            "reference_rebound",
            "closure_same",
            "throwable_same",
            "array_copy_preserved",
            "resource_external_alive",
            "slot_reused",
            "stale_rejected",
            "lifetime_destroyed",
            "reentrant_release_safe",
            "cycle_collected",
            "close_reentry_rejected",
            "limits_preserved",
            "destructor_failure_safe",
            "abort_cleanup",
            "live_after_close",
        ],
        "handles_php_observation",
    )?;
    let expected_observation = json!({
        "registry_closed":true,"all_closed_rejected":true,"external_alias_preserved":true,
        "reference_rebound":true,"closure_same":true,"throwable_same":true,
        "array_copy_preserved":true,"resource_external_alive":true,"slot_reused":true,
        "stale_rejected":true,"lifetime_destroyed":true,"reentrant_release_safe":true,
        "cycle_collected":true,"close_reentry_rejected":true,"limits_preserved":true,
        "destructor_failure_safe":true,
        "abort_cleanup":abort_mode,"live_after_close":0
    });
    if observation != &expected_observation {
        return Err("handles_php_observation_mismatch".into());
    }
    expect_transport(
        finished
            .get("transport")
            .ok_or("handles_transport_missing")?,
        &wire,
        bytes_before_finish,
    )?;
    if abort_mode {
        if cont.state() != &RequestState::Aborted || cont.depth() != 0 {
            return Err("handles_abort_state".into());
        }
    } else {
        cont.finish_request(root)
            .map_err(|e| format!("handles_finish_request:{e}"))?;
        if cont.state() != &RequestState::Finished || cont.depth() != 0 {
            return Err("handles_finished_state".into());
        }
    }
    for h in handles.iter().copied().chain([replacement, reused]) {
        if !matches!(
            cont.validate_handle(h),
            Err(ContinuationError::RequestClosed)
        ) {
            return Err("handles_not_closed".into());
        }
    }
    let mut extra = Vec::new();
    if (&mut wire.reader)
        .take((MAX_FRAME + 1) as u64)
        .read_until(b'\n', &mut extra)
        .map_err(|e| format!("handles_eof_check:{e}"))?
        != 0
    {
        return Err("extra_php_bytes_after_handles_finished".into());
    }
    let summary = json!({
        "scenario":scenario,"continuation_peer":true,"frames_from_php":wire.in_seq,
        "frames_from_rust":wire.out_seq,"registered_initial":8,"expired_handles_rejected":expired_rejections,
        "slot_reused":true,"old_generation_rejected":true,
        "state_after":if abort_mode {"Aborted"} else {"Finished"},"php_observation":observation
    });
    eprintln!("V06_HANDLES_RESULT {summary}");
    eprintln!("V06_HANDLES_OK continuation_peer=true");
    Ok(summary)
}
