//! Only the complete fixed native public response can cross the public boundary.
use crate::{db, fixed};
use anyhow::{Result, ensure};
use serde_json::{Value, json};
use std::{collections::BTreeSet, path::Path};
pub const BODY_LIMIT: usize = 1024 * 1024;
const PINS: &[(&str, &str)] = &[
    (
        "mysql",
        "7cf6c805a7863be81b81fd64177347e2fdea6170bd37594ad446845a90d6bf9a",
    ),
    (
        "mariadb",
        "d79130b07427eab57d54275341b4bbd271bd53c63499b1d47139ab3b4c88831b",
    ),
];
#[derive(Clone)]
pub struct NativeResponse {
    response: Value,
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
    pub fn load(root: &Path, report: &mut Value) -> Result<Self> {
        let mut expected = None;
        let mut identities = serde_json::Map::new();
        for (backend, pin) in PINS {
            let path =
                format!("experiments/wp-request-probe/evidence/windows-{backend}-native.json");
            let wire = fixed(root, &path, 1024 * 1024)?;
            ensure!(db::sha(&wire) == *pin, "fixed_native_home_oracle_identity");
            let v: Value = serde_json::from_slice(&wire)?;
            ensure!(
                v["schema"] == "recastpress.wp-request-native-database.v0.5"
                    && v["passed"] == true
                    && v["outcome"] == "fixed_native_home_request_passed"
                    && v["backend"] == *backend
                    && v["old_native_read_baseline_reproduced"] == true
                    && v["source_reverified_after"] == true
                    && v["normal_admin_shutdown_verified"] == true
                    && v["all_command_children_reaped"] == true
                    && v["database_closed_and_reaped"] == true,
                "fixed_native_home_oracle_completion"
            );
            let response = v["observations"]["home"]["response"].clone();
            let info = shape(&response)
                .ok_or_else(|| anyhow::anyhow!("fixed_native_home_oracle_shape"))?;
            ensure!(
                info.status == 200
                    && info.headers == 3
                    && info.bytes == 69399
                    && info.sha256
                        == "ef43f26984536cae43c4e703edef98dbaaf2bca79cc972a4373bf27f01cabdd1",
                "fixed_native_home_oracle_response"
            );
            if let Some(previous) = &expected {
                ensure!(&response == previous, "native_backends_response_identity");
            }
            identities.insert(
                backend.to_string(),
                json!({"path":path,"bytes":wire.len(),"sha256":pin}),
            );
            expected = Some(response);
        }
        report["native_oracles"] = Value::Object(identities);
        report["fixed_native_response"] = json!({"status":200,"header_count":3,"body_bytes":69399,
            "body_sha256":"ef43f26984536cae43c4e703edef98dbaaf2bca79cc972a4373bf27f01cabdd1","both_backends_exactly_equal":true});
        Ok(Self {
            response: expected.ok_or_else(|| anyhow::anyhow!("native_oracle_missing"))?,
        })
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
            response: original.clone(),
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
            response: fixture(),
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
            response: fixture(),
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
