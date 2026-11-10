//! Closed live-public-state publication scenario for the v0.6 bridge fixture.

use super::*;
use recastpress_wp::public_state::{PublicStateError, PublicStateTracker};

fn view_number(view: &Value, path: &[&str]) -> Result<i64, String> {
    let mut value = view;
    for key in path {
        value = value
            .get(*key)
            .ok_or_else(|| format!("missing_view_{key}"))?;
    }
    value
        .as_i64()
        .ok_or_else(|| format!("view_{path:?}_must_be_integer"))
}

fn array_number(view: &Value, index: usize) -> Result<i64, String> {
    view.get("items")
        .and_then(Value::as_array)
        .and_then(|items| items.get(index))
        .and_then(Value::as_i64)
        .ok_or_else(|| format!("items_{index}_must_be_integer"))
}

fn validate_view(view: &Value, label: &str) -> Result<(), String> {
    exact_keys(
        view,
        &[
            "item",
            "settings",
            "items",
            "alias_same",
            "property_reference_value",
            "array_reference_value",
            "copy_before",
            "global_name",
        ],
        label,
    )?;
    exact_keys(
        view.get("item").ok_or("view_item_missing")?,
        &["count", "unknown", "final_marker"],
        "view_item",
    )?;
    exact_keys(
        view.get("settings").ok_or("view_settings_missing")?,
        &["enabled"],
        "view_settings",
    )?;
    if view.get("alias_same") != Some(&json!(true))
        || view.get("property_reference_value").and_then(Value::as_i64)
            != Some(view_number(view, &["item", "count"])?)
        || view.get("array_reference_value").and_then(Value::as_i64) != Some(array_number(view, 1)?)
        || view.get("copy_before") != Some(&json!([2, 4]))
    {
        return Err(format!("{label}_alias_or_copy_mismatch"));
    }
    if !view
        .get("item")
        .and_then(|v| v.get("unknown"))
        .is_some_and(Value::is_string)
        || !view
            .get("settings")
            .and_then(|v| v.get("enabled"))
            .is_some_and(Value::is_boolean)
        || !view.get("global_name").is_some_and(Value::is_string)
    {
        return Err(format!("{label}_value_schema"));
    }
    let items = view
        .get("items")
        .and_then(Value::as_array)
        .ok_or("view_items_must_be_array")?;
    if items.len() != 3 || items.iter().any(|v| !v.is_i64()) {
        return Err(format!("{label}_items_schema"));
    }
    if !view
        .get("item")
        .and_then(|v| v.get("final_marker"))
        .is_some_and(|v| v.is_null() || v.is_i64())
    {
        return Err(format!("{label}_final_marker_schema"));
    }
    Ok(())
}

struct ExpectedView<'a> {
    count: i64,
    array_entry: i64,
    unknown: &'a str,
    enabled: bool,
    tail: i64,
    final_marker: Option<i64>,
    global_name: &'a str,
}

fn expect_view_values(view: &Value, expected: ExpectedView<'_>, label: &str) -> Result<(), String> {
    validate_view(view, label)?;
    if view_number(view, &["item", "count"])? != expected.count
        || array_number(view, 1)? != expected.array_entry
        || view.get("item").and_then(|v| v.get("unknown")) != Some(&json!(expected.unknown))
        || view.get("settings").and_then(|v| v.get("enabled")) != Some(&json!(expected.enabled))
        || array_number(view, 2)? != expected.tail
        || view.get("global_name") != Some(&json!(expected.global_name))
        || view
            .get("item")
            .and_then(|v| v.get("final_marker"))
            .and_then(Value::as_i64)
            != expected.final_marker
    {
        return Err(format!("{label}_values_mismatch"));
    }
    Ok(())
}

fn send_publish<R: BufRead, W: Write>(
    wire: &mut Wire<R, W>,
    parent_effect: Option<&str>,
    publish_id: &str,
    expected_observation: u64,
    writes: Value,
) -> Result<(), String> {
    wire.send(
        "publish",
        parent_effect,
        json!({"publish_id":publish_id,"expected_observation":expected_observation,"writes":writes}),
    )
}

fn receive_publish_ack<R: BufRead, W: Write>(
    wire: &mut Wire<R, W>,
    parent_effect: Option<&str>,
    publish_id: &str,
    expected_observation: u64,
    tracker: &mut PublicStateTracker,
    permit: &recastpress_wp::public_state::PublishPermit,
) -> Result<(Value, u64), String> {
    let ack = wire.receive("publish_ack", parent_effect)?;
    exact_keys(
        &ack,
        &["publish_id", "applied", "observation", "view"],
        "publish_ack_payload",
    )?;
    if string(&ack, "publish_id")? != publish_id || ack.get("applied") != Some(&json!(true)) {
        return Err("publish_ack_id_or_status".into());
    }
    let number = ack
        .get("observation")
        .and_then(Value::as_u64)
        .ok_or("publish_ack_observation")?;
    if number <= expected_observation {
        return Err("publish_ack_not_fresh".into());
    }
    let view = ack.get("view").cloned().ok_or("publish_ack_view_missing")?;
    validate_view(&view, "publish_ack_view")?;
    tracker
        .acknowledge_publish(permit)
        .map_err(|e| format!("publish_ack_tracker:{e}"))?;
    tracker
        .observe(number)
        .map_err(|e| format!("publish_ack_observe:{e}"))?;
    Ok((view, number))
}

fn receive_reject<R: BufRead, W: Write>(
    wire: &mut Wire<R, W>,
    publish_id: &str,
    reason: &str,
    expected_observation: u64,
    tracker: &mut PublicStateTracker,
) -> Result<(Value, u64), String> {
    let rejected = wire.receive("publish_reject", Some("e-root"))?;
    exact_keys(
        &rejected,
        &["publish_id", "reason", "observation", "view"],
        "publish_reject_payload",
    )?;
    if string(&rejected, "publish_id")? != publish_id || string(&rejected, "reason")? != reason {
        return Err(format!("{publish_id}_rejection_mismatch"));
    }
    let number = rejected
        .get("observation")
        .and_then(Value::as_u64)
        .ok_or("publish_reject_observation")?;
    if number <= expected_observation {
        return Err(format!("{publish_id}_rejection_observation_not_fresh"));
    }
    let view = rejected
        .get("view")
        .cloned()
        .ok_or("publish_reject_view_missing")?;
    validate_view(&view, "publish_reject_view")?;
    tracker
        .observe(number)
        .map_err(|e| format!("publish_reject_observe:{e}"))?;
    Ok((view, number))
}

pub(super) fn run_publish<R: BufRead, W: Write>(mut wire: Wire<R, W>) -> Result<Value, String> {
    let request = RequestGeneration::new(3);
    let mut tracker = PublicStateTracker::new(request);
    let mut cont = Continuation::<Value>::new(request, LimitsProfile::default())
        .map_err(|e| format!("publish_continuation:{e}"))?;
    let start = wire.receive("start_root", None)?;
    exact_keys(
        &start,
        &["operation", "observation", "view"],
        "publish_start_payload",
    )?;
    if string(&start, "operation")? != "publish_probe"
        || start.get("observation").and_then(Value::as_u64) != Some(1)
    {
        return Err("publish_start_operation_or_observation".into());
    }
    let initial = start
        .get("view")
        .cloned()
        .ok_or("publish_start_view_missing")?;
    expect_view_values(
        &initial,
        ExpectedView {
            count: 3,
            array_entry: 4,
            unknown: "keep",
            enabled: false,
            tail: 0,
            final_marker: None,
            global_name: "original",
        },
        "publish_initial_view",
    )?;
    let obs1 = tracker
        .observe(1)
        .map_err(|e| format!("publish_observe_initial:{e}"))?;
    let root = cont
        .start_root()
        .map_err(|e| format!("publish_start_root:{e}"))?;
    let root_effect = cont
        .invoke_php(root)
        .map_err(|e| format!("publish_invoke_root:{e}"))?;

    let root_permit = tracker
        .begin_publish(&obs1)
        .map_err(|e| format!("publish_begin_root:{e}"))?;
    send_publish(
        &mut wire,
        None,
        "p-root",
        1,
        json!([
            {"target":"item","field":"count","value":view_number(&initial, &["item", "count"])? + 1},
            {"target":"items","field":"1","value":array_number(&initial, 1)? + view_number(&initial, &["item", "count"])?}
        ]),
    )?;
    let (root_view, root_observation) =
        receive_publish_ack(&mut wire, None, "p-root", 1, &mut tracker, &root_permit)?;
    if root_observation != 2 {
        return Err("publish_root_observation_sequence".into());
    }
    expect_view_values(
        &root_view,
        ExpectedView {
            count: 4,
            array_entry: 7,
            unknown: "keep",
            enabled: false,
            tail: 0,
            final_marker: None,
            global_name: "original",
        },
        "publish_root_readback",
    )?;
    wire.send(
        "invoke_php",
        None,
        json!({"effect_id":"e-root","callback":"publish_root_callback"}),
    )?;

    let call = wire.receive("call_core", Some("e-root"))?;
    exact_keys(
        &call,
        &["operation", "observation", "view"],
        "publish_call_core_payload",
    )?;
    if string(&call, "operation")? != "publish_child"
        || call.get("observation").and_then(Value::as_u64) != Some(3)
    {
        return Err("publish_child_call_identity".into());
    }
    let child_view = call
        .get("view")
        .cloned()
        .ok_or("publish_child_view_missing")?;
    expect_view_values(
        &child_view,
        ExpectedView {
            count: 4,
            array_entry: 7,
            unknown: "php-root",
            enabled: true,
            tail: 0,
            final_marker: None,
            global_name: "replacement",
        },
        "publish_child_entry_view",
    )?;
    let child_observation = tracker
        .observe(3)
        .map_err(|e| format!("publish_observe_child:{e}"))?;
    let child = cont
        .begin_child(root_effect)
        .map_err(|e| format!("publish_begin_child:{e}"))?;

    let child_permit = tracker
        .begin_publish(&child_observation)
        .map_err(|e| format!("publish_begin_child_write:{e}"))?;
    send_publish(
        &mut wire,
        Some("e-root"),
        "p-child",
        3,
        json!([
            {"target":"item","field":"count","value":view_number(&child_view, &["item", "count"])? + 1},
            {"target":"items","field":"1","value":array_number(&child_view, 1)? + view_number(&child_view, &["item", "count"])?}
        ]),
    )?;
    let (after_child_publish, child_publish_observation) = receive_publish_ack(
        &mut wire,
        Some("e-root"),
        "p-child",
        3,
        &mut tracker,
        &child_permit,
    )?;
    if child_publish_observation != 4 {
        return Err("publish_child_observation_sequence".into());
    }
    expect_view_values(
        &after_child_publish,
        ExpectedView {
            count: 5,
            array_entry: 11,
            unknown: "php-root",
            enabled: true,
            tail: 0,
            final_marker: None,
            global_name: "replacement",
        },
        "publish_child_readback",
    )?;

    // PHP independently rejects stale, replayed, and non-atomic batches before writing.
    let stale_replay_rejected = matches!(
        tracker.begin_publish(&child_observation),
        Err(PublicStateError::StaleObservation)
    );
    if !stale_replay_rejected {
        return Err("publish_tracker_stale_observation_accepted".into());
    }
    send_publish(
        &mut wire,
        Some("e-root"),
        "p-stale",
        3,
        json!([{"target":"item","field":"count","value":999}] ),
    )?;
    let (after_stale, stale_observation) =
        receive_reject(&mut wire, "p-stale", "stale_observation", 4, &mut tracker)?;
    if stale_observation != 5 {
        return Err("publish_stale_rejection_observation_sequence".into());
    }
    expect_view_values(
        &after_stale,
        ExpectedView {
            count: 5,
            array_entry: 11,
            unknown: "php-root",
            enabled: true,
            tail: 0,
            final_marker: None,
            global_name: "replacement",
        },
        "stale_unchanged_view",
    )?;

    let replay_rejected = matches!(
        tracker.begin_publish(&child_observation),
        Err(PublicStateError::StaleObservation)
    );
    if !replay_rejected {
        return Err("publish_tracker_replay_accepted".into());
    }
    send_publish(
        &mut wire,
        Some("e-root"),
        "p-child",
        stale_observation,
        json!([{"target":"item","field":"count","value":999}] ),
    )?;
    let (after_replay, replay_observation) = receive_reject(
        &mut wire,
        "p-child",
        "replayed_publish",
        stale_observation,
        &mut tracker,
    )?;
    if replay_observation != 6 {
        return Err("publish_replay_rejection_observation_sequence".into());
    }
    expect_view_values(
        &after_replay,
        ExpectedView {
            count: 5,
            array_entry: 11,
            unknown: "php-root",
            enabled: true,
            tail: 0,
            final_marker: None,
            global_name: "replacement",
        },
        "replay_unchanged_view",
    )?;
    let replay_permit_rejected = matches!(
        tracker.acknowledge_publish(&child_permit),
        Err(PublicStateError::InvalidPermit)
    );
    if !replay_permit_rejected {
        return Err("publish_tracker_permit_replay_accepted".into());
    }

    send_publish(
        &mut wire,
        Some("e-root"),
        "p-atomic",
        replay_observation,
        json!([
            {"target":"item","field":"count","value":999},
            {"target":"unknown","field":"oops","value":1}
        ]),
    )?;
    let (after_atomic, atomic_observation) = receive_reject(
        &mut wire,
        "p-atomic",
        "invalid_target",
        replay_observation,
        &mut tracker,
    )?;
    if atomic_observation != 7 {
        return Err("publish_atomic_rejection_observation_sequence".into());
    }
    expect_view_values(
        &after_atomic,
        ExpectedView {
            count: 5,
            array_entry: 11,
            unknown: "php-root",
            enabled: true,
            tail: 0,
            final_marker: None,
            global_name: "replacement",
        },
        "atomic_unchanged_view",
    )?;

    let nested_effect = cont
        .invoke_php(child)
        .map_err(|e| format!("publish_invoke_child:{e}"))?;
    wire.send(
        "invoke_php",
        None,
        json!({"effect_id":"e-child","callback":"nested_publish_callback"}),
    )?;
    let nested_resume = wire.receive("resume", Some("e-root"))?;
    exact_keys(
        &nested_resume,
        &["effect_id", "result"],
        "publish_nested_resume_payload",
    )?;
    if string(&nested_resume, "effect_id")? != "e-child" {
        return Err("publish_nested_effect_correlation".into());
    }
    let nested_result = nested_resume
        .get("result")
        .cloned()
        .ok_or("publish_nested_result_missing")?;
    exact_keys(
        &nested_result,
        &["observation", "view", "published_seen"],
        "publish_nested_result",
    )?;
    if nested_result.get("observation").and_then(Value::as_u64) != Some(8)
        || nested_result.get("published_seen") != Some(&json!(true))
    {
        return Err("publish_nested_result_marker".into());
    }
    let nested_view = nested_result
        .get("view")
        .cloned()
        .ok_or("publish_nested_view_missing")?;
    expect_view_values(
        &nested_view,
        ExpectedView {
            count: 5,
            array_entry: 11,
            unknown: "php-nested",
            enabled: true,
            tail: 77,
            final_marker: None,
            global_name: "replacement",
        },
        "publish_nested_view",
    )?;
    tracker
        .observe(8)
        .map_err(|e| format!("publish_observe_nested:{e}"))?;
    let nested_completion = cont
        .resume(nested_effect, Completion::Value(nested_result.clone()))
        .map_err(|e| format!("publish_resume_nested:{e}"))?;
    if nested_completion != Completion::Value(nested_result.clone()) {
        return Err("publish_nested_completion_changed".into());
    }
    let parent_effect = cont
        .finish_operation(child)
        .map_err(|e| format!("publish_finish_child:{e}"))?
        .ok_or("publish_parent_effect_missing")?;
    if parent_effect != root_effect {
        return Err("publish_child_parent_effect_mismatch".into());
    }
    let child_return = json!({"operation":"publish_child","observation":{"count":view_number(&nested_view, &["item","count"])?,"array_entry":array_number(&nested_view,1)?,"nested_unknown":nested_view["item"]["unknown"],"copy_before":nested_view["copy_before"],"stale_rejected":true,"replay_rejected":true,"atomic_rejected":true}});
    wire.send("return", Some("e-root"), child_return.clone())?;

    let outer_resume = wire.receive("resume", None)?;
    exact_keys(
        &outer_resume,
        &["effect_id", "result"],
        "publish_outer_resume_payload",
    )?;
    if string(&outer_resume, "effect_id")? != "e-root" {
        return Err("publish_outer_effect_correlation".into());
    }
    let outer_result = outer_resume
        .get("result")
        .cloned()
        .ok_or("publish_outer_result_missing")?;
    exact_keys(
        &outer_result,
        &["observation", "view", "global_name"],
        "publish_outer_result",
    )?;
    if outer_result.get("observation").and_then(Value::as_u64) != Some(9) {
        return Err("publish_outer_observation".into());
    }
    let outer_view = outer_result
        .get("view")
        .cloned()
        .ok_or("publish_outer_view_missing")?;
    expect_view_values(
        &outer_view,
        ExpectedView {
            count: 5,
            array_entry: 11,
            unknown: "php-outer",
            enabled: true,
            tail: 77,
            final_marker: None,
            global_name: "replacement",
        },
        "publish_outer_view",
    )?;
    if outer_result.get("global_name") != Some(&json!("replacement")) {
        return Err("publish_global_rebind_mismatch".into());
    }
    let outer_observation = tracker
        .observe(9)
        .map_err(|e| format!("publish_observe_outer:{e}"))?;
    let fresh_final_value = view_number(&outer_view, &["item", "count"])?
        + array_number(&outer_view, 1)?
        + array_number(&outer_view, 2)?
        + string(&outer_view, "global_name")?.len() as i64;
    let final_permit = tracker
        .begin_publish(&outer_observation)
        .map_err(|e| format!("publish_begin_final:{e}"))?;
    send_publish(
        &mut wire,
        None,
        "p-final",
        9,
        json!([{"target":"item","field":"final_marker","value":fresh_final_value}]),
    )?;
    let (final_view, final_observation) =
        receive_publish_ack(&mut wire, None, "p-final", 9, &mut tracker, &final_permit)?;
    if final_observation != 10 {
        return Err("publish_final_observation_sequence".into());
    }
    expect_view_values(
        &final_view,
        ExpectedView {
            count: 5,
            array_entry: 11,
            unknown: "php-outer",
            enabled: true,
            tail: 77,
            final_marker: Some(fresh_final_value),
            global_name: "replacement",
        },
        "publish_final_readback",
    )?;

    let final_result = json!({"child_return":child_return,"view":final_view,"global_name":"replacement","final_value":fresh_final_value});
    let root_completion = cont
        .resume(root_effect, Completion::Value(final_result.clone()))
        .map_err(|e| format!("publish_resume_root:{e}"))?;
    if root_completion != Completion::Value(final_result.clone()) {
        return Err("publish_root_completion_changed".into());
    }
    cont.finish_operation(root)
        .map_err(|e| format!("publish_finish_root:{e}"))?;
    let observation = json!({
        "view":final_view,
        "initial_observation":1,
        "root_publish_observation":root_observation,
        "child_entry_observation":3,
        "child_publish_observation":child_publish_observation,
        "stale_rejection_observation":stale_observation,
        "replay_rejection_observation":replay_observation,
        "atomic_rejection_observation":atomic_observation,
        "nested_observation":8,
        "outer_observation":9,
        "final_publish_observation":final_observation,
        "published_values":{"root_count":4,"root_array_entry":7,"child_count":5,"child_array_entry":11,"final_marker":fresh_final_value},
        "stale_rejected_before_write":stale_replay_rejected,
        "replay_rejected_before_write":replay_rejected && replay_permit_rejected,
        "atomic_batch_no_partial_write":true,
        "alias_and_reference_values_preserved":true,
        "copy_on_write_preserved":true,
        "child_and_php_edits_preserved":true,
        "global_rebind_preserved":"replacement"
    });
    wire.send(
        "return",
        None,
        json!({"operation":"publish_probe","observation":observation}),
    )?;

    let finished = wire.receive("request_finished", None)?;
    exact_keys(
        &finished,
        &["status", "php_observation", "transport"],
        "publish_finished_payload",
    )?;
    if string(&finished, "status")? != "ok" {
        return Err("publish_finished_status".into());
    }
    let php_observation = finished
        .get("php_observation")
        .ok_or("publish_php_observation_missing")?;
    exact_keys(
        php_observation,
        &[
            "view",
            "trace",
            "negative_publish_rejections",
            "alias_same_object",
            "reference_cell_same_value",
            "copy_on_write_preserved",
            "global_rebound_without_alias_rebind",
        ],
        "publish_php_observation",
    )?;
    let final_php_view = php_observation
        .get("view")
        .ok_or("publish_final_php_view_missing")?;
    expect_view_values(
        final_php_view,
        ExpectedView {
            count: 5,
            array_entry: 11,
            unknown: "php-outer",
            enabled: true,
            tail: 77,
            final_marker: Some(fresh_final_value),
            global_name: "replacement",
        },
        "publish_php_final_view",
    )?;
    if php_observation.get("negative_publish_rejections") != Some(&json!(3))
        || php_observation.get("alias_same_object") != Some(&json!(true))
        || php_observation.get("reference_cell_same_value") != Some(&json!(true))
        || php_observation.get("copy_on_write_preserved") != Some(&json!(true))
        || php_observation.get("global_rebound_without_alias_rebind") != Some(&json!(true))
    {
        return Err("publish_php_observation_mismatch".into());
    }
    if !php_observation.get("trace").is_some_and(Value::is_array)
        || php_observation.get("trace")
            != Some(&json!([
                "root:before-child",
                "nested:mutated",
                "root:after-child"
            ]))
    {
        return Err("publish_trace_mismatch".into());
    }
    let transport = finished
        .get("transport")
        .ok_or("publish_transport_missing")?;
    exact_keys(
        transport,
        &["frames_sent", "frames_received", "bytes_total"],
        "publish_transport",
    )?;
    if transport
        .get("frames_sent")
        .and_then(Value::as_u64)
        .is_none()
        || transport
            .get("frames_received")
            .and_then(Value::as_u64)
            .is_none()
        || transport
            .get("bytes_total")
            .and_then(Value::as_u64)
            .is_none_or(|n| n > MAX_TOTAL as u64)
    {
        return Err("publish_transport_stats_invalid".into());
    }

    tracker.close();
    if !matches!(tracker.observe(11), Err(PublicStateError::RequestClosed)) {
        return Err("publish_observe_after_close_accepted".into());
    }
    cont.finish_request(root)
        .map_err(|e| format!("publish_finish_request:{e}"))?;
    let mut extra = Vec::new();
    if (&mut wire.reader)
        .take((MAX_FRAME + 1) as u64)
        .read_until(b'\n', &mut extra)
        .map_err(|e| format!("publish_eof_check:{e}"))?
        != 0
    {
        return Err("extra_php_bytes_after_publish_finish".into());
    }
    let summary = json!({"observation":observation,"php_observation":php_observation,"frames_from_php":wire.in_seq,"frames_from_rust":wire.out_seq,"continuation_peer":true});
    eprintln!("V06_PUBLISH_RESULT {summary}");
    eprintln!("V06_PUBLISH_OK continuation_peer=true");
    Ok(summary)
}
