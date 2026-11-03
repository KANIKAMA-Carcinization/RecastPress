#![forbid(unsafe_code)]
use mysql_async::{Conn, OptsBuilder, Value as Cell, prelude::Queryable};
use recastpress_wpdb_result_budget_probe::{
    BoundedWriter, BudgetError, Collector, Column, Input, Limits, parse_input,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    io::{self, Read, Write},
    time::{Duration, Instant},
};

const POSITIVE: &[u8] = b"SELECT id,payload,label FROM rp_result_fixture WHERE id<=115 ORDER BY id";
const ZERO: &[u8] = b"SELECT id,payload,label FROM rp_result_fixture WHERE id<0 ORDER BY id";
const ALL: &[u8] = b"SELECT id,payload,label FROM rp_result_fixture ORDER BY id";
const CELL: &[u8] = b"SELECT id,payload,label FROM rp_result_fixture WHERE id=1 ORDER BY id";
const RAW_115: usize = 39048;
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn check(ok: bool, code: &'static str) -> Result<(), &'static str> {
    if ok { Ok(()) } else { Err(code) }
}
fn remaining(end: Instant, cap: u64) -> Result<Duration, &'static str> {
    let n = end
        .checked_duration_since(Instant::now())
        .ok_or("whole_probe_deadline")?;
    Ok(n.min(Duration::from_secs(cap)))
}
#[derive(Debug)]
enum Failure {
    Budget(BudgetError),
    Driver(&'static str),
}
impl From<BudgetError> for Failure {
    fn from(e: BudgetError) -> Self {
        Self::Budget(e)
    }
}

async fn collect(
    conn: &mut Conn,
    sql: &[u8],
    limits: Limits,
    seen: &mut usize,
) -> Result<Vec<u8>, Failure> {
    let mut result = conn
        .query_iter(sql)
        .await
        .map_err(|_| Failure::Driver("query_failed"))?;
    let source = result.columns_ref();
    if source.len() > limits.columns {
        return Err(BudgetError::ColumnLimit.into());
    }
    let mut columns = Vec::new();
    let mut metadata = 0usize;
    for c in source {
        let names = [
            c.schema_ref(),
            c.table_ref(),
            c.org_table_ref(),
            c.name_ref(),
            c.org_name_ref(),
        ];
        let mut size = 0usize;
        for name in names {
            if name.len() > limits.identifier_bytes {
                return Err(BudgetError::MetadataLimit.into());
            }
            size = size
                .checked_add(name.len())
                .ok_or(BudgetError::MetadataLimit)?;
        }
        metadata = metadata
            .checked_add(size)
            .ok_or(BudgetError::MetadataLimit)?;
        if metadata > limits.metadata_bytes {
            return Err(BudgetError::MetadataLimit.into());
        }
        columns.push(Column {
            name: c.name_ref().to_vec(),
            column_type: c.column_type() as u8,
            flags: c.flags().bits(),
            character_set: c.character_set(),
            declared_length: c.column_length(),
            decimals: c.decimals(),
            metadata_bytes: size,
        });
    }
    let mut collector = Collector::new(limits, columns)?;
    while let Some(row) = result
        .next()
        .await
        .map_err(|_| Failure::Driver("row_failed"))?
    {
        *seen = seen
            .checked_add(1)
            .ok_or(Failure::Driver("row_counter_overflow"))?;
        if row.len() > limits.columns {
            return Err(BudgetError::ColumnLimit.into());
        }
        let mut borrowed = Vec::with_capacity(row.len());
        for i in 0..row.len() {
            borrowed.push(match row.as_ref(i).ok_or(Failure::Driver("taken_cell"))? {
                Cell::NULL => None,
                Cell::Bytes(b) => Some(b.as_slice()),
                _ => return Err(Failure::Driver("non_text_protocol_cell")),
            });
        }
        collector.push(&borrowed)?;
    }
    if !result.is_empty() {
        return Err(Failure::Driver("additional_result"));
    }
    // At EOF there is nothing left to drain. On overflow return immediately;
    // the owner uses direct Conn::disconnect, not cleanup_for_pool/drop_result.
    drop(result);
    collector.finish().map_err(Failure::Budget)
}

async fn case(
    input: &Input,
    name: &str,
    sql: &[u8],
    limits: Limits,
    expected: Option<BudgetError>,
    work_end: Instant,
    hard_end: Instant,
) -> Result<Value, &'static str> {
    let started = Instant::now();
    let opts = OptsBuilder::default()
        .ip_or_hostname("127.0.0.1")
        .tcp_port(input.port)
        .user(Some("rp_wpdb_probe"))
        .pass(Some(input.password.clone()))
        .db_name(Some("rp_wpdb_probe"))
        .prefer_socket(false)
        .client_found_rows(false)
        .multi_statements(false)
        .local_files(false)
        .max_allowed_packet(Some(1024 * 1024));
    let mut conn = tokio::time::timeout(remaining(work_end, 10)?, Conn::new(opts))
        .await
        .map_err(|_| "connect_timeout")?
        .map_err(|_| "connect_failed")?;
    let caps = conn.negotiated_capabilities().bits();
    let connection_id = conn.id();
    let server = hex(conn.raw_server_version());
    let mut seen = 0;
    let collected = match remaining(work_end, 10) {
        Ok(t) => tokio::time::timeout(t, collect(&mut conn, sql, limits, &mut seen))
            .await
            .unwrap_or(Err(Failure::Driver("query_timeout"))),
        Err(e) => Err(Failure::Driver(e)),
    };
    // A pending/error result is never implicitly drained for reuse. This owns
    // one physical non-pooled session and requests COM_QUIT + stream close.
    let closed = match remaining(hard_end, 5) {
        Ok(t) => matches!(tokio::time::timeout(t, conn.disconnect()).await, Ok(Ok(()))),
        Err(_) => {
            drop(conn);
            false
        }
    };
    check(closed, "physical_disconnect_failed")?;
    check(
        caps & (2 | 128 | 2048 | 65536) == 0,
        "forbidden_handshake_capability",
    )?;
    let mut row = json!({"case":name,"query_hex":hex(sql),"limits":limits,
        "physical_connection_created":true,"physical_disconnect_completed":closed,
        "connection_id":connection_id,"raw_server_version_hex":server,"negotiated_capabilities":caps,
        "rows_observed":seen,"elapsed_ms":started.elapsed().as_millis() as u64,
        "partial_result_returned":false,"result":null,"encoded_result_bytes":0,
        "encoded_result_sha256":null,"budget_error":null,"passed":false});
    match collected {
        Ok(wire) => {
            check(expected.is_none(), "expected_budget_failure_not_observed")?;
            let value: Value =
                serde_json::from_slice(&wire).map_err(|_| "encoded_result_invalid")?;
            row["result"] = value;
            row["encoded_result_bytes"] = json!(wire.len());
            row["encoded_result_sha256"] = json!(sha(&wire));
        }
        Err(Failure::Budget(error)) => {
            check(expected == Some(error), "unexpected_budget_failure")?;
            row["budget_error"] = serde_json::to_value(error).map_err(|_| "budget_error_encode")?;
        }
        Err(Failure::Driver(code)) => return Err(code),
    }
    row["passed"] = json!(true);
    Ok(row)
}
fn validate_positive(value: &Value) -> Result<(), &'static str> {
    let result = &value["result"];
    let rows = result["rows"].as_array().ok_or("positive_rows")?;
    check(
        rows.len() == 115 && result["raw_cell_bytes"] == RAW_115,
        "positive_shape",
    )?;
    let columns = result["columns"].as_array().ok_or("positive_columns")?;
    check(
        columns.len() == 3
            && columns[0]["name_hex"] == hex(b"id")
            && columns[1]["name_hex"] == hex(b"payload")
            && columns[2]["name_hex"] == hex(b"label"),
        "column_order",
    )?;
    for (i, row) in rows.iter().enumerate() {
        let id = i + 1;
        let payload = match id {
            1 => Some([0u8, 255, 65].repeat(7926)),
            2 => None,
            3 => Some(vec![]),
            _ => Some(vec![66; 128]),
        };
        let expected = json!([
            hex(id.to_string().as_bytes()),
            payload.map(|b| hex(&b)),
            hex(format!("row-{id}").as_bytes())
        ]);
        check(*row == expected, "actual_fixture_value_differs")?;
    }
    check(
        value["encoded_result_bytes"]
            .as_u64()
            .is_some_and(|n| n > 65536),
        "old_reply_budget_not_exceeded",
    )
}
async fn run(input: &Input, report: &mut Value) -> Result<(), &'static str> {
    let started = Instant::now();
    let work_end = started + Duration::from_secs(55);
    let hard_end = started + Duration::from_secs(60);
    let limits = Limits::default();
    let mut rows = Vec::new();
    let first = case(
        input,
        "positive_115",
        POSITIVE,
        limits,
        None,
        work_end,
        hard_end,
    )
    .await?;
    validate_positive(&first)?;
    let encoded = first["encoded_result_bytes"]
        .as_u64()
        .ok_or("encoded_size")? as usize;
    let columns = first["result"]["columns"].clone();
    rows.push(first);
    report["observations"] = json!(rows);
    let cases = [
        ("zero_rows", ZERO, limits, None),
        ("row_limit_257", ALL, limits, Some(BudgetError::RowLimit)),
        (
            "cell_limit_4096",
            CELL,
            Limits {
                cell_bytes: 4096,
                ..limits
            },
            Some(BudgetError::CellLimit),
        ),
        (
            "raw_limit_exact_115",
            POSITIVE,
            Limits {
                total_raw_cell_bytes: RAW_115,
                ..limits
            },
            None,
        ),
        (
            "raw_limit_below_115",
            POSITIVE,
            Limits {
                total_raw_cell_bytes: RAW_115 - 1,
                ..limits
            },
            Some(BudgetError::RawBytesLimit),
        ),
        (
            "encoded_limit_exact_115",
            POSITIVE,
            Limits {
                encoded_reply_bytes: encoded,
                ..limits
            },
            None,
        ),
        (
            "encoded_limit_below_115",
            POSITIVE,
            Limits {
                encoded_reply_bytes: encoded - 1,
                ..limits
            },
            Some(BudgetError::EncodedBytesLimit),
        ),
        ("fresh_recovery_115", POSITIVE, limits, None),
    ];
    for (name, sql, l, expected) in cases {
        let value = case(input, name, sql, l, expected, work_end, hard_end).await?;
        if expected.is_none() {
            if name == "zero_rows" {
                check(
                    value["result"]["columns"] == columns && value["result"]["rows"] == json!([]),
                    "zero_row_metadata_differs",
                )?;
            } else {
                validate_positive(&value)?;
            }
        }
        report["observations"]
            .as_array_mut()
            .ok_or("observation_array")?
            .push(value);
    }
    let ids: std::collections::BTreeSet<_> = report["observations"]
        .as_array()
        .ok_or("observations")?
        .iter()
        .filter_map(|r| r["connection_id"].as_u64())
        .collect();
    check(ids.len() == 9, "fresh_physical_sessions_required")?;
    report["elapsed_ms"] = json!(started.elapsed().as_millis() as u64);
    report["all_cases_disconnected"] = json!(true);
    Ok(())
}
fn main() {
    let mut report = json!({"schema":"recastpress.wpdb-result-budget-observation.v0.5","outcome":"not_run","passed":false,
        "observations":[],"physical_connection_pool_used":false,"database_started_by_probe":false,"ddl_executed_by_probe":false,
        "wordpress_api_executed":false,"wasm_executed":false,"rpc_frames_exercised":false,"product_budget_accepted":false,
        "driver_packet_allocation_budget_verified":false,"all_resources_reclaimed":false,"unbounded_result_drain_used":false,
        "raw_non_utf8_err_preserved":false,"all_cases_disconnected":false,"whole_probe_timeout_seconds":60,
        "operation_timeout_seconds":10,"disconnect_timeout_seconds":5,"input_sql_modified":false,
        "candidate_limits":Limits::default()});
    let result = (|| -> Result<(), &'static str> {
        let mut wire = Vec::new();
        io::stdin()
            .take(4097)
            .read_to_end(&mut wire)
            .map_err(|_| "input_read")?;
        let input = parse_input(&wire)?;
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|_| "runtime_create")?;
        let result = runtime.block_on(run(&input, &mut report));
        runtime.shutdown_timeout(Duration::from_secs(1));
        result
    })();
    match result {
        Ok(()) => {
            report["passed"] = json!(true);
            report["outcome"] = json!("fixed_result_budget_cases_passed");
        }
        Err(code) => {
            report["outcome"] = json!("fixed_result_budget_failed");
            report["failure"] = json!(code);
        }
    }
    let mut out = BoundedWriter::new(2 * 1024 * 1024).expect("fixed public buffer");
    let ok = serde_json::to_writer(&mut out, &report).is_ok() && out.write_all(b"\n").is_ok();
    if ok {
        let _ = io::stdout().write_all(&out.into_bytes());
    } else {
        let _=io::stdout().write_all(b"{\"schema\":\"recastpress.wpdb-result-budget-observation.v0.5\",\"passed\":false,\"failure\":\"public_output_quota\"}\n");
    }
    std::process::exit(if ok && report["passed"] == true { 0 } else { 1 });
}
