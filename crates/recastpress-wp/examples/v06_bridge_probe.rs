//! Native PHP JSONL peer for the isolated v0.6 continuation fixture.
//!
//! This is a development probe. It does not load WordPress or establish PHP-Wasm
//! re-entry. stdout is reserved for protocol frames; diagnostics go to stderr.

use recastpress_wp::continuation::{
    Completion, Continuation, EffectId, LimitsProfile, OperationId, RequestGeneration,
};
use serde::de::{MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value, json};
use std::fmt;
use std::io::{self, BufRead, Read, Write};

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

fn fail(message: impl fmt::Display) -> ! {
    eprintln!("v06_bridge_probe: {message}");
    std::process::exit(2)
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

fn run<R: BufRead, W: Write>(mut wire: Wire<R, W>) -> Result<(), String> {
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
    cont.resume(nested_effect, Completion::Value(nested_result.clone()))
        .map_err(|e| format!("resume_nested:{e}"))?;
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
    cont.resume(root_effect, Completion::Value(outer_result.clone()))
        .map_err(|e| format!("resume_root:{e}"))?;
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
    eprintln!("V06_BRIDGE_OK native_php=true rust_continuation=true wasm=false");
    Ok(())
}

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let wire = Wire {
        reader: stdin.lock(),
        writer: stdout.lock(),
        in_seq: 0,
        out_seq: 0,
        total: 0,
    };
    if let Err(error) = run(wire) {
        fail(error);
    }
}
