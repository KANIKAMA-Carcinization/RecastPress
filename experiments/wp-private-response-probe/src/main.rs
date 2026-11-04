//! Diagnostic codec harness: it never returns input headers or body on stdout.
use recastpress_wp_private_response_probe::{decode, WIRE_LIMIT};
use serde_json::json;
use std::io::{Read, Write};

fn main() {
    let mut input = Vec::new();
    let result = std::io::stdin().lock().take((WIRE_LIMIT + 1) as u64).read_to_end(&mut input);
    let (success, value) = if result.is_err() {
        (false, json!({"error":"private_input_io"}))
    } else {
        match decode(&input) {
            Ok(response) => (true, json!({"summary":response.summary()})),
            Err(error) => (false, json!({"error":error.to_string()})),
        }
    };
    let mut output = json!({
        "schema":"recastpress.wp-private-response-summary.v0.5",
        "passed":success,
        "publication_authorized":false,
        "execution_completion_verified":false,
        "native_comparison_performed":false,
        "php_wasm_database_or_http_executed":false,
    });
    output.as_object_mut().unwrap().extend(value.as_object().unwrap().clone());
    let wire = serde_json::to_vec(&output).expect("fixed summary JSON");
    let mut stdout = std::io::stdout().lock();
    if stdout.write_all(&wire).and_then(|_|stdout.write_all(b"\n")).is_err() {
        std::process::exit(2);
    }
    if !success { std::process::exit(1); }
}
