//! CGI profile gate around the unchanged native CRUD query-trace validator.
#[path = "../../wpdb-crud-probe/src/completion.rs"]
mod comparison;
use serde_json::Value;

pub fn verify(php: &Value, operations: &Value) -> bool {
    php["schema"] == "recastpress.wpdb-crud-php-observation.v0.5"
        && php["outcome"] == "fixed_crud_cases_passed"
        && php["variant"] == "bridge"
        && [
            "passed",
            "connected",
            "constructor_connection_deferred",
            "close_completed",
            "included_source_gate_passed",
            "native_driver_extension_present",
            "rust_driver_executed",
            "wasm_executed",
        ]
        .iter()
        .all(|key| php[key] == true)
        && [
            "native_mysqli_handle_supplied",
            "full_wordpress_bootstrap",
            "product_driver_selected",
            "product_abi_selected",
            "standard_wpdb_compatibility_verified",
            "raw_non_utf8_err_verified",
            "suppress_errors_previous",
        ]
        .iter()
        .all(|key| php[key] == false)
        && php["runtime"]["sapi"] == "cgi-fcgi"
        && php["runtime"]["version"] == "8.5.12"
        && php["runtime"]["version_id"] == 80512
        && php["runtime"]["int_size"] == 8
        && operation_profile(operations, 40, 20, 18)
        && comparison::verify(php, operations)
}

pub fn operation_profile(operations: &Value, total: usize, queries: usize, escapes: usize) -> bool {
    let Some(rows) = operations.as_array() else {
        return false;
    };
    if rows.len() != total
        || rows
            .first()
            .is_none_or(|op| op["op"] != "hello" || op["ok"] != true)
        || rows
            .last()
            .is_none_or(|op| op["op"] != "close" || op["ok"] != true)
    {
        return false;
    }
    let count = |name: &str| rows.iter().filter(|op| op["op"] == name).count();
    count("hello") == 1
        && count("close") == 1
        && count("query") == queries
        && count("escape") == escapes
        && rows
            .iter()
            .enumerate()
            .all(|(index, op)| op["seq"].as_u64() == Some(index as u64 + 1))
}
