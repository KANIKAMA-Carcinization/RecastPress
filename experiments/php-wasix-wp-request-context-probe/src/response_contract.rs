//! Only the complete fixed native public response can cross the public boundary.
use crate::{db, fixed, Case};
use anyhow::{Result, ensure};
use serde_json::{Value, json};
use std::{collections::BTreeSet, path::Path};
pub const BODY_LIMIT: usize = 1024 * 1024;
const PINS: &[(&str, &str)] = &[
    (
        "mysql",
        "7c2a0b01fdd5e90a120b8e278368245c18618936a268baefb09ff1adba01e00b",
    ),
    (
        "mariadb",
        "a2d54133bc368f9945b5f178aafcc5c1edf60778c90247491f1ad60aeb0fe1df",
    ),
];
#[derive(Clone)]
pub struct NativeResponse {
    response: Value,
    contexts: Value,
    case: Case,
}

fn unhex(value: &str, limit: usize) -> Option<Vec<u8>> {
    if value.len() > limit.checked_mul(2)? || value.len() % 2 != 0 {
        return None;
    }
    let nibble = |b| match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        _ => None,
    };
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|v| Some((nibble(v[0])? << 4) | nibble(v[1])?))
        .collect()
}
struct Shape {
    status: u16,
    headers: usize,
    bytes: usize,
    sha256: String,
}
fn shape(response: &Value) -> Option<Shape> {
    let keys: BTreeSet<_> = response.as_object()?.keys().map(String::as_str).collect();
    if keys
        != BTreeSet::from([
            "status",
            "headers_hex",
            "body_bytes",
            "body_hex",
            "body_sha256",
        ])
    {
        return None;
    }
    let status = response["status"]
        .as_u64()
        .filter(|n| (100..=599).contains(n))? as u16;
    let body = unhex(response["body_hex"].as_str()?, BODY_LIMIT)?;
    if body.is_empty() || response["body_bytes"].as_u64() != Some(body.len() as u64) {
        return None;
    }
    let digest = db::sha(&body);
    if response["body_sha256"] != digest {
        return None;
    }
    let headers = response["headers_hex"]
        .as_array()
        .filter(|h| h.len() <= 64)?;
    let mut total = 0usize;
    for header in headers {
        let raw = unhex(header.as_str()?, 8192)?;
        total = total.checked_add(raw.len() + 2)?;
        if raw.is_empty()
            || !raw.contains(&b':')
            || raw.iter().any(|b| (*b < 32 && *b != 9) || *b == 127)
            || total > 16384
        {
            return None;
        }
    }
    Some(Shape {
        status,
        headers: headers.len(),
        bytes: body.len(),
        sha256: digest,
    })
}
impl NativeResponse {
    pub fn load(root: &Path, case: Case, report: &mut Value) -> Result<Self> {
        let mut expected = None;
        let mut contexts = None;
        let mut identities = serde_json::Map::new();
        for (backend, pin) in PINS {
            let path = format!("experiments/wp-request-context-probe/evidence/windows-{backend}-native.json");
            let wire = fixed(root, &path, 1024 * 1024)?;
            ensure!(db::sha(&wire) == *pin, "fixed_native_context_oracle_identity");
            let v: Value = serde_json::from_slice(&wire)?;
            ensure!(v["schema"] == "recastpress.wp-request-context-native-check.v0.5"
                && v["passed"] == true && v["backend"] == *backend
                && v["outcome"] == "clean_native_home_and_repeat_404_passed"
                && v["original_install_read_baseline_reproduced"] == true
                && v["fresh_repeat_404_response_exact"] == true
                && v["source_reverified_after"] == true && v["source_before"] == v["source_after"]
                && v["normal_admin_shutdown_verified"] == true
                && v["all_command_children_reaped"] == true && v["database_closed_and_reaped"] == true
                && v["owned_database_cleanup_completed"] == true && v["all_owned_cgi_reaped"] == true
                && v["owned_cgi_count"] == 3 && v["owned_cgi_reaped"] == 3
                && v["preparation"]["input_files"].as_object().is_some_and(|m|m.len()==40),
                "fixed_native_context_oracle_completion");
            let observation = &v["observations"][case.as_str()];
            let response = observation["response"].clone();
            let info = shape(&response).ok_or_else(|| anyhow::anyhow!("fixed_native_context_oracle_shape"))?;
            let (bytes, digest) = match case {
                Case::Home => (69399, "ef43f26984536cae43c4e703edef98dbaaf2bca79cc972a4373bf27f01cabdd1"),
                Case::Missing => (58177, "d2bda2fdf6bf12e075689fd3af5e87eba2ede1ea953d9f0d7085a63fc11f60bc"),
            };
            ensure!(info.status == case.status() && info.bytes == bytes && info.sha256 == digest,
                "fixed_native_context_oracle_response");
            ensure!(v["observations"]["missing"]["response"] == v["requests"]["missing_repeat"]["observation"]["response"],
                "fixed_native_repeat_response");
            let mut snapshots = serde_json::Map::new();
            for stage in ["before_bootstrap", "after_bootstrap", "after_dispatch"] {
                let key = format!("context_{stage}");
                let snapshot = &observation[&key];
                ensure!(snapshot["stage"] == stage && snapshot["query"] == if case == Case::Home {""} else {"p=2147483647"}
                    && snapshot["environment_query_present"] == (case == Case::Missing)
                    && snapshot["environment_query"] == if case == Case::Home {Value::Null} else {json!("p=2147483647")}
                    && snapshot["display_errors"] == "1" && snapshot["input"]["bytes"] == 0
                    && snapshot["input"]["sha256"] == db::sha(b""), "fixed_native_context_snapshot");
                snapshots.insert(key, snapshot.clone());
            }
            let snapshots = Value::Object(snapshots);
            if let Some(previous) = &expected { ensure!(&response == previous, "native_backends_response_identity"); }
            if let Some(previous) = &contexts { ensure!(&snapshots == previous, "native_backends_context_identity"); }
            identities.insert(backend.to_string(), json!({"path":path,"bytes":wire.len(),"sha256":pin}));
            expected = Some(response); contexts = Some(snapshots);
        }
        let response = expected.ok_or_else(|| anyhow::anyhow!("native_oracle_missing"))?;
        let contexts = contexts.ok_or_else(|| anyhow::anyhow!("native_context_missing"))?;
        report["native_oracles"] = Value::Object(identities);
        report["fixed_native_response"] = json!({"case":case.as_str(),"status":case.status(),
            "header_count":response["headers_hex"].as_array().map(Vec::len),"body_bytes":response["body_bytes"],
            "body_sha256":response["body_sha256"],"both_backends_exactly_equal":true,"three_context_stages_exactly_equal":true});
        Ok(Self { response, contexts, case })
    }
    pub fn contexts_accept(&self, value: &Value) -> bool {
        value["case"] == self.case.as_str()
            && value["is_home"] == (self.case == Case::Home)
            && value["is_404"] == (self.case == Case::Missing)
            && self.contexts.as_object().is_some_and(|m|m.len()==3 && m.iter().all(|(k,v)|value[k] == *v))
    }
    pub fn context_projection(&self, value: &Value) -> serde_json::Map<String,Value> {
        let mut safe = serde_json::Map::new();
        safe.insert("request_context_exact_native_match".into(), json!(self.contexts_accept(value)));
        if self.contexts_accept(value) {
            for (key, value) in self.contexts.as_object().expect("validated snapshot object") { safe.insert(key.clone(), value.clone()); }
            safe.insert("case".into(), json!(self.case.as_str()));
            safe.insert("is_home".into(), json!(self.case == Case::Home));
            safe.insert("is_404".into(), json!(self.case == Case::Missing));
        }
        safe
    }
    pub fn accepts(&self, response: &Value) -> bool {
        shape(response).is_some() && *response == self.response
    }
    pub fn projection(&self, response: &Value) -> Option<Value> {
        self.accepts(response).then(|| response.clone())
    }
    pub fn observe(&self, response: &Value) -> Value {
        if let Some(s) = shape(response) {
            json!({"status":s.status,"header_count":s.headers,"body_bytes":s.bytes,"body_sha256":s.sha256,
                "exact_native_match":self.accepts(response),"framing_valid":true})
        } else {
            // Never return unknown strings, headers, HTML or claimed digests.
            let body = response["body_hex"]
                .as_str()
                .and_then(|s| unhex(s, BODY_LIMIT));
            json!({"status":response["status"].as_u64().filter(|n|(100..=599).contains(n)),
                "header_count":response["headers_hex"].as_array().map(Vec::len).filter(|n|*n<=64),
                "body_bytes":body.as_ref().map(Vec::len),"body_sha256":body.as_ref().map(|b|db::sha(b)),
                "exact_native_match":false,"framing_valid":false})
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn native(case: Case) -> NativeResponse {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().unwrap();
        NativeResponse::load(&root, case, &mut json!({})).unwrap()
    }
    fn context_value(native: &NativeResponse) -> Value {
        let mut value = native.contexts.clone();
        value["case"] = json!(native.case.as_str());
        value["is_home"] = json!(native.case == Case::Home);
        value["is_404"] = json!(native.case == Case::Missing);
        value
    }
    #[test]
    fn context_snapshot_mismatch_cannot_publish_unknown_values() {
        let oracle = native(Case::Missing);
        for stage in ["before_bootstrap", "after_bootstrap", "after_dispatch"] {
            let mut value = context_value(&oracle);
            value[format!("context_{stage}")]["environment_query"] = json!("private-secret-query");
            assert!(!oracle.contexts_accept(&value));
            let safe = oracle.context_projection(&value);
            assert_eq!(safe.len(),1);
            assert!(!serde_json::to_string(&safe).unwrap().contains("private-secret-query"));
        }
    }
    #[test]
    fn fixed_native_context_routes_are_distinct_and_require_all_stages() {
        let home = native(Case::Home); let missing = native(Case::Missing);
        assert_ne!(home.response,missing.response);
        for oracle in [&home,&missing] {
            let value = context_value(oracle);
            assert!(oracle.contexts_accept(&value));
            assert_eq!(oracle.context_projection(&value)["case"],oracle.case.as_str());
            let mut incomplete=value.clone(); incomplete.as_object_mut().unwrap().remove("context_after_dispatch");
            assert!(!oracle.contexts_accept(&incomplete));
            let mut wrong=value; wrong["case"]=json!("other"); assert!(!oracle.contexts_accept(&wrong));
        }
        assert!(!missing.contexts_accept(&context_value(&home)));
    }
    fn fixture() -> Value {
        let body = b"<html>fixed public fixture</html>";
        json!({"status":200,
        "headers_hex":[db::hex(b"Content-Type: text/html"),db::hex(b"X-Fixed: original")],
        "body_bytes":body.len(),"body_hex":db::hex(body),"body_sha256":db::sha(body)})
    }
    #[test]
    fn exact_response_keeps_all_original_bytes_and_header_order() {
        let original = fixture();
        let oracle = NativeResponse {
            response: original.clone(), contexts:json!({}), case:Case::Home,
        };
        assert_eq!(oracle.projection(&original), Some(original.clone()));
        let mut changed = original;
        changed["headers_hex"].as_array_mut().unwrap().reverse();
        assert!(oracle.projection(&changed).is_none());
        assert_eq!(oracle.observe(&changed)["exact_native_match"], false);
    }
    #[test]
    fn mismatched_private_html_and_headers_only_yield_safe_actual_metrics() {
        let oracle = NativeResponse {
            response: fixture(), contexts:json!({}), case:Case::Home,
        };
        let secret = "SQL-row-password-hash-must-stay-private";
        let changed = json!({"status":200,"headers_hex":[db::hex(secret.as_bytes())],"body_bytes":secret.len(),"body_hex":db::hex(secret.as_bytes()),"body_sha256":db::sha(secret.as_bytes())});
        assert!(oracle.projection(&changed).is_none());
        let observed = oracle.observe(&changed).to_string();
        assert!(!observed.contains(secret) && !observed.contains(&db::hex(secret.as_bytes())));
        assert_eq!(oracle.observe(&changed)["body_bytes"], secret.len());
    }
    #[test]
    fn oversized_incomplete_and_forged_response_frames_are_never_public() {
        let oracle = NativeResponse {
            response: fixture(), contexts:json!({}), case:Case::Home,
        };
        let mut bad = fixture();
        bad["body_bytes"] = json!(1);
        assert!(oracle.projection(&bad).is_none());
        bad = fixture();
        bad["body_sha256"] = json!("0".repeat(64));
        assert!(oracle.projection(&bad).is_none());
        bad = fixture();
        bad["body_hex"] = json!("00".repeat(BODY_LIMIT + 1));
        assert!(oracle.projection(&bad).is_none());
        bad = fixture();
        bad["headers_hex"] = json!(["0"]);
        assert!(oracle.projection(&bad).is_none());
        bad = fixture();
        bad["extra_private_field"] = json!("private");
        assert!(oracle.projection(&bad).is_none());
    }
}
