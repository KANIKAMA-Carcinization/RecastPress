//! Fixed native-comparison corpus completion, checked against actual DB ops.
use serde_json::Value;

const CASES: [&str; 15] = [
    "fixture_empty",
    "insert_explicit_formats",
    "update_same",
    "update_changed",
    "update_null",
    "update_where_null_empty",
    "replace_existing",
    "insert_default_formats",
    "insert_exhausted_formats",
    "delete_missing",
    "delete_where_null",
    "delete_existing",
    "remaining_row",
    "delete_remaining",
    "final_empty",
];

pub fn verify(php: &Value, operations: &Value) -> bool {
    let Some(observations) = php["observations"].as_array() else {
        return false;
    };
    let Some(trace) = php["query_trace_hex"].as_array() else {
        return false;
    };
    if observations.len() != CASES.len()
        || php["bootstrap_num_queries"] != 0
        || php["final_num_queries"].as_u64() != Some(trace.len() as u64)
        || trace.iter().any(|sql| !valid_hex(sql))
    {
        return false;
    }
    let mut observed_queries = 0usize;
    for (observation, case) in observations.iter().zip(CASES) {
        let Some(queries) = observation["queries_hex"].as_array() else {
            return false;
        };
        if observation["case"] != case
            || observation["state"]["delta"].as_u64() != Some(queries.len() as u64)
            || trace.get(observed_queries..observed_queries + queries.len())
                != Some(queries.as_slice())
        {
            return false;
        }
        observed_queries += queries.len();
        if observation["state"]["num_queries"].as_u64() != Some(observed_queries as u64) {
            return false;
        }
    }
    if observed_queries != trace.len() {
        return false;
    }
    let Some(setup) = php["rpc_stats"]["internal_session_setup"].as_array() else {
        return false;
    };
    let Some(operations) = operations.as_array() else {
        return false;
    };
    // Session setup is real, separately observed SQL outside wpdb::num_queries.
    // Its length depends on the server's initial sql_mode; never pad a counter.
    if !(3..=4).contains(&setup.len()) {
        return false;
    }
    let query_ops: Vec<_> = operations.iter().filter(|op| op["op"] == "query").collect();
    if query_ops.len() != setup.len() + trace.len() {
        return false;
    }
    for (actual, expected) in query_ops.iter().zip(setup) {
        if actual["data_hex"] != expected["sql_hex"]
            || actual["ok"] != true
            || actual["data"] != expected["data"]
        {
            return false;
        }
    }
    query_ops[setup.len()..]
        .iter()
        .zip(trace)
        .all(|(actual, expected)| actual["data_hex"] == *expected)
}

fn valid_hex(value: &Value) -> bool {
    value.as_str().is_some_and(|text| {
        !text.is_empty()
            && text.len() <= 8192
            && text.len() % 2 == 0
            && text
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}
