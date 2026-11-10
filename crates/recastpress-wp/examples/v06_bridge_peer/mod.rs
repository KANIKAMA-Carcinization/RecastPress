//! Reusable JSONL peer for the isolated v0.6 continuation fixture.
//!
//! The caller supplies streams and owns their deadlines/lifecycle. This module
//! owns closed-protocol validation and the Rust continuation state transitions.

use recastpress_wp::continuation::{
    Completion, Continuation, ContinuationError, EffectId, LimitsProfile, OperationId, PhpHandle,
    PhpHandleKind, RequestGeneration, RequestState,
};
use serde::de::{MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value, json};
use std::fmt;
use std::io::{BufRead, Read, Write};

mod handles;
mod publish;

const REQUEST: &str = "surface-probe-1";
const MAX_FRAME: usize = 1_048_576;
const MAX_TOTAL: usize = 33_554_432;
const MAX_DEPTH: usize = 64;
const MAX_NODES: usize = 65_536;

#[derive(Debug)]
struct StrictValue(Value);

impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = StrictValue;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a JSON value without duplicate object keys")
            }
            fn visit_bool<E: serde::de::Error>(self, v: bool) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Bool(v)))
            }
            fn visit_i64<E: serde::de::Error>(self, v: i64) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Number(v.into())))
            }
            fn visit_u64<E: serde::de::Error>(self, v: u64) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Number(v.into())))
            }
            fn visit_f64<E: serde::de::Error>(self, v: f64) -> Result<Self::Value, E> {
                serde_json::Number::from_f64(v)
                    .map(Value::Number)
                    .map(StrictValue)
                    .ok_or_else(|| E::custom("non-finite JSON number"))
            }
            fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::String(v.to_owned())))
            }
            fn visit_string<E: serde::de::Error>(self, v: String) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::String(v)))
            }
            fn visit_none<E: serde::de::Error>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }
            fn visit_unit<E: serde::de::Error>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
                let mut values = Vec::new();
                while let Some(value) = seq.next_element::<StrictValue>()? {
                    values.push(value.0);
                }
                Ok(StrictValue(Value::Array(values)))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
                let mut values = Map::new();
                while let Some((key, value)) = map.next_entry::<String, StrictValue>()? {
                    if values.contains_key(&key) {
                        return Err(serde::de::Error::custom(format!(
                            "duplicate JSON key: {key}"
                        )));
                    }
                    values.insert(key, value.0);
                }
                Ok(StrictValue(Value::Object(values)))
            }
        }
        deserializer.deserialize_any(V)
    }
}

fn exact_keys(value: &Value, expected: &[&str], label: &str) -> Result<(), String> {
    let obj = value
        .as_object()
        .ok_or_else(|| format!("{label}_must_be_object"))?;
    let mut actual: Vec<_> = obj.keys().map(String::as_str).collect();
    actual.sort_unstable();
    let mut expected = expected.to_vec();
    expected.sort_unstable();
    if actual != expected {
        return Err(format!("{label}_schema"));
    }
    Ok(())
}

fn check_decoded_limits(value: &Value) -> Result<(), String> {
    fn walk(value: &Value, depth: usize, nodes: &mut usize) -> Result<(), String> {
        if depth > MAX_DEPTH {
            return Err("decoded_depth_limit".into());
        }
        *nodes = (*nodes).checked_add(1).ok_or("decoded_element_limit")?;
        if *nodes > MAX_NODES {
            return Err("decoded_element_limit".into());
        }
        match value {
            Value::Array(values) => {
                for value in values {
                    walk(value, depth + 1, nodes)?;
                }
            }
            Value::Object(values) => {
                for value in values.values() {
                    walk(value, depth + 1, nodes)?;
                }
            }
            _ => {}
        }
        Ok(())
    }
    let mut nodes = 0;
    walk(value, 0, &mut nodes)
}

struct Wire<R, W> {
    reader: R,
    writer: W,
    in_seq: u64,
    out_seq: u64,
    total: usize,
}

impl<R: BufRead, W: Write> Wire<R, W> {
    fn receive(
        &mut self,
        expected_kind: &str,
        parent_effect: Option<&str>,
    ) -> Result<Value, String> {
        let mut line = Vec::new();
        let n = (&mut self.reader)
            .take((MAX_FRAME + 1) as u64)
            .read_until(b'\n', &mut line)
            .map_err(|e| format!("read:{e}"))?;
        if n == 0 || line.last() != Some(&b'\n') {
            return Err("eof_or_partial_frame".into());
        }
        if line.len() > MAX_FRAME {
            return Err("frame_limit".into());
        }
        self.total = self.total.checked_add(n).ok_or("total_limit")?;
        if self.total > MAX_TOTAL {
            return Err("total_limit".into());
        }
        let mut de = serde_json::Deserializer::from_slice(&line[..line.len() - 1]);
        let frame = StrictValue::deserialize(&mut de)
            .map_err(|e| format!("json:{e}"))?
            .0;
        de.end().map_err(|e| format!("trailing_json:{e}"))?;
        check_decoded_limits(&frame)?;
        let mut keys = vec!["v", "request", "seq", "kind", "payload"];
        if parent_effect.is_some() {
            keys.push("parent_effect");
        }
        exact_keys(&frame, &keys, "frame")?;
        let obj = frame.as_object().ok_or("frame_must_be_object")?;
        if obj.get("v") != Some(&json!(1)) || obj.get("request") != Some(&json!(REQUEST)) {
            return Err("frame_request_or_version".into());
        }
        if obj.get("seq").and_then(Value::as_u64) != Some(self.in_seq) {
            return Err("frame_sequence".into());
        }
        self.in_seq += 1;
        if obj.get("kind").and_then(Value::as_str) != Some(expected_kind) {
            return Err("frame_kind".into());
        }
        if let Some(parent) = parent_effect
            && obj.get("parent_effect").and_then(Value::as_str) != Some(parent)
        {
            return Err("parent_effect_correlation".into());
        }
        let payload = obj.get("payload").cloned().ok_or("missing_payload")?;
        if !payload.is_object() {
            return Err("payload_must_be_object".into());
        }
        Ok(payload)
    }

    fn send(
        &mut self,
        kind: &str,
        parent_effect: Option<&str>,
        payload: Value,
    ) -> Result<(), String> {
        let mut frame = serde_json::Map::new();
        frame.insert("v".into(), json!(1));
        frame.insert("request".into(), json!(REQUEST));
        frame.insert("seq".into(), json!(self.out_seq));
        frame.insert("kind".into(), json!(kind));
        if let Some(parent) = parent_effect {
            frame.insert("parent_effect".into(), json!(parent));
        }
        frame.insert("payload".into(), payload);
        let mut line =
            serde_json::to_vec(&Value::Object(frame)).map_err(|e| format!("encode:{e}"))?;
        line.push(b'\n');
        if line.len() > MAX_FRAME {
            return Err("outgoing_frame_limit".into());
        }
        self.total = self.total.checked_add(line.len()).ok_or("total_limit")?;
        if self.total > MAX_TOTAL {
            return Err("total_limit".into());
        }
        self.writer
            .write_all(&line)
            .map_err(|e| format!("write:{e}"))?;
        self.writer.flush().map_err(|e| format!("flush:{e}"))?;
        self.out_seq += 1;
        Ok(())
    }
}

fn string<'a>(value: &'a Value, key: &str) -> Result<&'a str, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{key}_must_be_string"))
}

fn run_surface<R: BufRead, W: Write>(mut wire: Wire<R, W>) -> Result<Value, String> {
    let mut cont = Continuation::<Value>::new(RequestGeneration::new(1), LimitsProfile::default())
        .map_err(|e| format!("continuation:{e}"))?;
    let start = wire.receive("start_root", None)?;
    exact_keys(&start, &["operation"], "start_root_payload")?;
    if string(&start, "operation")? != "surface_probe" {
        return Err("root_operation".into());
    }
    let root: OperationId = cont.start_root().map_err(|e| format!("start_root:{e}"))?;
    let root_effect: EffectId = cont
        .invoke_php(root)
        .map_err(|e| format!("invoke_root:{e}"))?;
    let root_effect_wire = "e-root";
    wire.send(
        "invoke_php",
        None,
        json!({"effect_id":root_effect_wire,"callback":"probe_root_callback"}),
    )?;

    let call = wire.receive("call_core", Some(root_effect_wire))?;
    exact_keys(&call, &["operation", "view"], "call_core_payload")?;
    if string(&call, "operation")? != "observe_public" {
        return Err("child_operation".into());
    }
    let view = call.get("view").ok_or("missing_public_view")?;
    exact_keys(
        view,
        &[
            "public_name",
            "setting_flag",
            "alias_same_object",
            "global_replaced",
        ],
        "public_view",
    )?;
    // These values came from PHP's live heap after the callback performed its mutation.
    if view.get("public_name") != Some(&json!("changed-before-child"))
        || view.get("setting_flag") != Some(&json!(true))
        || view.get("alias_same_object") != Some(&json!(true))
        || view.get("global_replaced") != Some(&json!(true))
    {
        return Err("public_snapshot_mismatch".into());
    }
    let before_name = string(view, "public_name")?.to_owned();
    let child = cont
        .begin_child(root_effect)
        .map_err(|e| format!("begin_child:{e}"))?;
    let nested_effect = cont
        .invoke_php(child)
        .map_err(|e| format!("invoke_nested:{e}"))?;
    let nested_wire_id = "e-nested";
    wire.send(
        "invoke_php",
        None,
        json!({"effect_id":nested_wire_id,"callback":"nested_probe_callback"}),
    )?;

    let nested_resume = wire.receive("resume", Some(root_effect_wire))?;
    exact_keys(
        &nested_resume,
        &["effect_id", "result"],
        "nested_resume_payload",
    )?;
    if string(&nested_resume, "effect_id")? != nested_wire_id {
        return Err("nested_effect_correlation".into());
    }
    let nested_result = nested_resume.get("result").ok_or("missing_nested_result")?;
    exact_keys(
        nested_result,
        &["public_name", "alias_same_object"],
        "nested_result",
    )?;
    if nested_result.get("public_name") != Some(&json!("changed-before-child:nested"))
        || nested_result.get("alias_same_object") != Some(&json!(true))
    {
        return Err("nested_snapshot_mismatch".into());
    }
    let after_name = string(nested_result, "public_name")?.to_owned();
    let name_growth_bytes = after_name
        .len()
        .checked_sub(before_name.len())
        .ok_or("nested_name_shrank")?;
    let nested_completion = cont
        .resume(nested_effect, Completion::Value(nested_result.clone()))
        .map_err(|e| format!("resume_nested:{e}"))?;
    if nested_completion != Completion::Value(nested_result.clone()) {
        return Err("nested_resume_completion_was_not_preserved".into());
    }
    let parent_effect = cont
        .finish_operation(child)
        .map_err(|e| format!("finish_child:{e}"))?
        .ok_or("child_return_missing_parent_effect")?;
    if parent_effect != root_effect {
        return Err("child_parent_effect_mismatch".into());
    }
    let child_observation = json!({
        "public_name_before": before_name,
        "public_name_after": after_name,
        "name_growth_bytes": name_growth_bytes,
        "setting_flag": view["setting_flag"],
        "alias_same_object": nested_result["alias_same_object"],
    });
    let child_return = json!({"operation":"observe_public","observation":child_observation});
    wire.send("return", Some(root_effect_wire), child_return.clone())?;

    let outer_resume = wire.receive("resume", None)?;
    exact_keys(
        &outer_resume,
        &["effect_id", "result"],
        "outer_resume_payload",
    )?;
    if string(&outer_resume, "effect_id")? != root_effect_wire {
        return Err("root_effect_correlation".into());
    }
    let outer_result = outer_resume.get("result").ok_or("missing_outer_result")?;
    exact_keys(
        outer_result,
        &["child_return", "after_child", "global_name"],
        "outer_result",
    )?;
    if outer_result.get("child_return") != Some(&child_return) {
        return Err("child_return_mismatch".into());
    }
    let after = outer_result
        .get("after_child")
        .ok_or("missing_after_child_snapshot")?;
    exact_keys(
        after,
        &["public_name", "setting_flag", "alias_same_object"],
        "after_child_snapshot",
    )?;
    if after.get("public_name") != Some(&json!("changed-before-child:nested"))
        || after.get("setting_flag") != Some(&json!(true))
        || after.get("alias_same_object") != Some(&json!(true))
        || outer_result.get("global_name") != Some(&json!("global-replacement"))
    {
        return Err("resumed_snapshot_mismatch".into());
    }
    let resumed_name = string(after, "public_name")?.to_owned();
    let root_observation = json!({
        "public_name_before": before_name,
        "public_name_after": resumed_name,
        "name_growth_bytes": string(after, "public_name")?.len() - before_name.len(),
        "setting_flag": after["setting_flag"],
        "alias_same_object": after["alias_same_object"],
        "global_name": outer_result["global_name"],
    });
    let root_completion = cont
        .resume(root_effect, Completion::Value(outer_result.clone()))
        .map_err(|e| format!("resume_root:{e}"))?;
    if root_completion != Completion::Value(outer_result.clone()) {
        return Err("root_resume_completion_was_not_preserved".into());
    }
    cont.finish_operation(root)
        .map_err(|e| format!("finish_root:{e}"))?;
    wire.send(
        "return",
        None,
        json!({"operation":"surface_probe","observation":root_observation}),
    )?;

    let finished = wire.receive("request_finished", None)?;
    exact_keys(
        &finished,
        &["status", "php_observation", "transport"],
        "request_finished_payload",
    )?;
    if string(&finished, "status")? != "ok" {
        return Err("request_finished_status".into());
    }
    let observation = finished
        .get("php_observation")
        .ok_or("missing_php_observation")?;
    exact_keys(
        observation,
        &[
            "same_object_alias",
            "public_name_after_resume",
            "global_replacement",
            "rust_child_observation",
            "rust_root_observation",
        ],
        "php_observation",
    )?;
    if observation.get("same_object_alias") != Some(&json!(true))
        || observation.get("public_name_after_resume")
            != Some(&json!("changed-before-child:nested"))
        || observation.get("global_replacement") != Some(&json!("global-replacement"))
        || observation.get("rust_child_observation") != Some(&child_observation)
        || observation.get("rust_root_observation") != Some(&root_observation)
    {
        return Err("php_final_observation_mismatch".into());
    }
    let transport = finished.get("transport").ok_or("missing_transport_stats")?;
    exact_keys(
        transport,
        &["frames_sent", "frames_received", "bytes_total"],
        "transport_stats",
    )?;
    // PHP snapshots stats while assembling RequestFinished, before counting that frame.
    if transport.get("frames_sent").and_then(Value::as_u64) != Some(4)
        || transport.get("frames_received").and_then(Value::as_u64) != Some(4)
    {
        return Err("transport_frame_count".into());
    }
    cont.finish_request(root)
        .map_err(|e| format!("finish_request:{e}"))?;
    let mut extra = Vec::new();
    if (&mut wire.reader)
        .take((MAX_FRAME + 1) as u64)
        .read_until(b'\n', &mut extra)
        .map_err(|e| format!("eof_check:{e}"))?
        != 0
    {
        return Err("extra_php_bytes_after_request_finished".into());
    }
    let summary = json!({"child_observation": child_observation, "root_observation": root_observation, "php_observation": observation, "frames_from_php": 5, "frames_from_rust": 4});
    eprintln!("V06_BRIDGE_RESULT {summary}");
    eprintln!("V06_BRIDGE_OK continuation_peer=true");
    Ok(summary)
}

fn run_throwable<R: BufRead, W: Write>(mut wire: Wire<R, W>) -> Result<Value, String> {
    const THROWABLE_ID: &str = "php-throwable-1";
    let request = RequestGeneration::new(2);
    let mut cont = Continuation::<Value>::new(request, LimitsProfile::default())
        .map_err(|e| format!("throwable_continuation:{e}"))?;
    let start = wire.receive("start_root", None)?;
    exact_keys(&start, &["operation"], "throwable_start_payload")?;
    if string(&start, "operation")? != "throwable_probe" {
        return Err("throwable_root_operation".into());
    }
    let root = cont
        .start_root()
        .map_err(|e| format!("throwable_start:{e}"))?;
    let root_effect = cont
        .invoke_php(root)
        .map_err(|e| format!("throwable_invoke_root:{e}"))?;
    wire.send(
        "invoke_php",
        None,
        json!({"effect_id":"e-root","callback":"probe_throwable_root"}),
    )?;

    let call = wire.receive("call_core", Some("e-root"))?;
    exact_keys(&call, &["operation"], "throwable_call_core_payload")?;
    if string(&call, "operation")? != "raise_nested" {
        return Err("throwable_child_operation".into());
    }
    let child = cont
        .begin_child(root_effect)
        .map_err(|e| format!("throwable_begin_child:{e}"))?;
    let nested_effect = cont
        .invoke_php(child)
        .map_err(|e| format!("throwable_invoke_nested:{e}"))?;
    wire.send(
        "invoke_php",
        None,
        json!({"effect_id":"e-nested","callback":"throwing_nested_callback"}),
    )?;

    let nested_resume = wire.receive("resume", Some("e-root"))?;
    exact_keys(
        &nested_resume,
        &["effect_id", "completion"],
        "throwable_nested_resume_payload",
    )?;
    if string(&nested_resume, "effect_id")? != "e-nested" {
        return Err("throwable_nested_effect_correlation".into());
    }
    let completion = nested_resume
        .get("completion")
        .ok_or("throwable_completion_missing")?;
    exact_keys(
        completion,
        &["kind", "handle", "class", "message"],
        "throwable_completion",
    )?;
    if string(completion, "kind")? != "php_throwable"
        || string(completion, "handle")? != THROWABLE_ID
        || string(completion, "class")? != "RuntimeException"
        || string(completion, "message")? != "nested fixture throwable"
    {
        return Err("throwable_completion_mismatch".into());
    }
    // This typed Rust handle is only a local mapping for PHP's request-local table
    // entry. The PHP object itself remains in the real PHP process and is never
    // serialized as bytes or reconstructed by Rust.
    let throwable = PhpHandle::new(request, PhpHandleKind::Throwable, 1, 7, 1);
    cont.register_handle(throwable)
        .map_err(|e| format!("throwable_register_handle:{e}"))?;
    let resumed_throwable = cont
        .resume(nested_effect, Completion::PhpThrowable(throwable))
        .map_err(|e| format!("throwable_resume_nested:{e}"))?;
    let returned_throwable = match resumed_throwable {
        Completion::PhpThrowable(handle) if handle == throwable => handle,
        _ => return Err("php_throwable_completion_was_lost_or_changed".into()),
    };
    let parent_effect = cont
        .finish_operation(child)
        .map_err(|e| format!("throwable_finish_child:{e}"))?
        .ok_or("throwable_child_missing_parent")?;
    if parent_effect != root_effect || cont.depth() != 1 {
        return Err("throwable_child_was_not_recovered_before_abort".into());
    }
    let child_return = json!({
        "operation":"raise_nested",
        "completion":{"kind":"php_throwable","handle":if returned_throwable == throwable { THROWABLE_ID } else { "invalid-handle" },"class":"RuntimeException","message":"nested fixture throwable"}
    });
    wire.send("return", Some("e-root"), child_return.clone())?;

    let double_resume_rejected = matches!(
        cont.resume(nested_effect, Completion::PhpThrowable(throwable)),
        Err(ContinuationError::EffectAlreadyResumed)
    );
    if !double_resume_rejected {
        return Err("throwable_double_resume_was_accepted".into());
    }
    let child_depth_before_abort = cont.depth();
    cont.abort();
    let depth_after_abort = cont.depth();
    let aborted = cont.state() == &RequestState::Aborted;
    let resume_after_abort_rejected = matches!(
        cont.resume(root_effect, Completion::Value(json!("must-not-resume"))),
        Err(ContinuationError::RequestClosed)
    );
    if !aborted
        || child_depth_before_abort != 1
        || depth_after_abort != 0
        || !resume_after_abort_rejected
    {
        return Err("throwable_abort_did_not_clear_and_close_request".into());
    }
    let finish_request_after_abort_rejected = matches!(
        cont.finish_request(root),
        Err(ContinuationError::RequestClosed)
    );
    if !finish_request_after_abort_rejected {
        return Err("finish_request_after_abort_was_accepted".into());
    }
    let abort_observation = json!({
        "reason":"php_throwable",
        "handle":THROWABLE_ID,
        "child_recovered":true,
        "child_depth_before_abort":child_depth_before_abort,
        "depth_after_abort":depth_after_abort,
        "double_resume_rejected":double_resume_rejected,
        "resume_after_abort_rejected":resume_after_abort_rejected,
        "finish_request_after_abort_rejected":finish_request_after_abort_rejected,
    });
    wire.send("abort", None, abort_observation.clone())?;

    let finished = wire.receive("request_finished", None)?;
    exact_keys(
        &finished,
        &["status", "php_observation", "transport"],
        "throwable_request_finished_payload",
    )?;
    if string(&finished, "status")? != "throwable" {
        return Err("throwable_request_finished_status".into());
    }
    let observed = finished
        .get("php_observation")
        .ok_or("throwable_php_observation_missing")?;
    exact_keys(
        observed,
        &[
            "caught_same_object",
            "throwable_class",
            "throwable_message",
            "trace",
            "nested_callback_calls",
            "abort_reason",
            "child_recovered",
            "child_depth_before_abort",
            "depth_after_abort",
            "double_resume_rejected",
            "resume_after_abort_rejected",
            "finish_request_after_abort_rejected",
        ],
        "throwable_php_observation",
    )?;
    let expected_trace = json!([
        "root:enter",
        "nested:enter",
        "nested:transport_catch",
        "nested:finally",
        "child:rethrow",
        "root:catch",
        "root:finally"
    ]);
    if observed.get("caught_same_object") != Some(&json!(true))
        || observed.get("throwable_class") != Some(&json!("RuntimeException"))
        || observed.get("throwable_message") != Some(&json!("nested fixture throwable"))
        || observed.get("trace") != Some(&expected_trace)
        || observed.get("nested_callback_calls") != Some(&json!(1))
        || observed.get("abort_reason") != Some(&json!("php_throwable"))
        || observed.get("child_recovered") != Some(&json!(true))
        || observed.get("child_depth_before_abort") != Some(&json!(1))
        || observed.get("depth_after_abort") != Some(&json!(0))
        || observed.get("double_resume_rejected") != Some(&json!(true))
        || observed.get("resume_after_abort_rejected") != Some(&json!(true))
        || observed.get("finish_request_after_abort_rejected") != Some(&json!(true))
    {
        return Err("php_throwable_did_not_propagate_as_expected".into());
    }
    if let Some(transport) = finished.get("transport") {
        exact_keys(
            transport,
            &["frames_sent", "frames_received", "bytes_total"],
            "throwable_transport_stats",
        )?;
        if transport.get("frames_sent").and_then(Value::as_u64) != Some(3)
            || transport.get("frames_received").and_then(Value::as_u64) != Some(4)
            || transport
                .get("bytes_total")
                .and_then(Value::as_u64)
                .is_none_or(|n| n > MAX_TOTAL as u64)
        {
            return Err("throwable_transport_frame_count_or_limit".into());
        }
    } else {
        return Err("throwable_transport_missing".into());
    }
    let mut extra = Vec::new();
    if (&mut wire.reader)
        .take((MAX_FRAME + 1) as u64)
        .read_until(b'\n', &mut extra)
        .map_err(|e| format!("throwable_eof_check:{e}"))?
        != 0
    {
        return Err("extra_php_bytes_after_throwable_finish".into());
    }
    let summary = json!({
        "child_return":child_return,
        "abort":abort_observation,
        "php_observation":observed,
        "frames_from_php":4,
        "frames_from_rust":4,
        "child_recovered_before_abort":true,
        "resume_after_abort_rejected":true,
        "double_resume_rejected":true
    });
    eprintln!("V06_THROWABLE_RESULT {summary}");
    eprintln!("V06_THROWABLE_OK continuation_peer=true");
    Ok(summary)
}

/// Drive one v0.6 peer request over caller-provided bounded or native streams.
///
/// `scenario` is `surface` or `throwable`. The caller owns stream deadlines and
/// process/thread lifecycle; this function owns protocol validation and state transitions.
pub fn run_peer<R: BufRead, W: Write>(
    reader: R,
    writer: W,
    scenario: &str,
) -> Result<Value, String> {
    let wire = Wire {
        reader,
        writer,
        in_seq: 0,
        out_seq: 0,
        total: 0,
    };
    match scenario {
        "surface" => run_surface(wire),
        "throwable" => run_throwable(wire),
        "publish" => publish::run_publish(wire),
        "handles" => handles::run_handles_peer(wire, false),
        "handles_abort" => handles::run_handles_peer(wire, true),
        _ => Err("unknown_bridge_scenario".into()),
    }
}
