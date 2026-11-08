//! Run only the fixed ERR cases through the patched driver's retained packet accessor.

use mysql_async::{Conn, Error, OptsBuilder, prelude::Queryable};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{io::Read, time::Duration};

const SCHEMA: &str = "recastpress.wpdb-raw-error-wire-driver-input.v0.5";
const REPORT: &str = "recastpress.wpdb-raw-error-wire-driver-observation.v0.5";
const DB_NAME: &str = "rp_wpdb_probe";
const UTF8: &str =
    "SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO = 31001, MESSAGE_TEXT = @rp_err_message";
const LATIN1: &str =
    "SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO = 31002, MESSAGE_TEXT = @rp_err_message";
const NEXT: &str = "SIGNAL SQLSTATE '45001' SET MYSQL_ERRNO = 31003, MESSAGE_TEXT = 'rp followup'";
const RECOVERY: &str = "SELECT 42 AS rp_recovered";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    schema: String,
    host: String,
    port: u16,
    database: String,
    user: String,
    password: String,
}

type Result<T> = std::result::Result<T, &'static str>;

fn check(value: bool, code: &'static str) -> Result<()> {
    if value { Ok(()) } else { Err(code) }
}

fn input() -> Result<Input> {
    let mut wire = Vec::new();
    std::io::stdin()
        .lock()
        .take(4097)
        .read_to_end(&mut wire)
        .map_err(|_| "driver_input")?;
    check(wire.len() <= 4096, "driver_input_size")?;
    let input: Input = serde_json::from_slice(&wire).map_err(|_| "driver_input_schema")?;
    check(
        input.schema == SCHEMA
            && input.host == "127.0.0.1"
            && input.port != 0
            && input.database == DB_NAME
            && input.user == DB_NAME
            && input.password.len() == 48
            && input
                .password
                .bytes()
                .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')),
        "driver_input_profile",
    )?;
    Ok(input)
}

fn hex(wire: &[u8]) -> String {
    wire.iter().map(|byte| format!("{byte:02x}")).collect()
}

async fn signal(conn: &mut Conn, sql: &str, code: u16, state: &[u8; 5]) -> Result<Value> {
    let error = match conn.query_drop(sql).await {
        Err(Error::Server(error)) => error,
        _ => return Err("driver_expected_server_error"),
    };
    let packet = conn
        .last_error_packet()
        .ok_or("driver_missing_retained_error")?;
    let observed_state = packet
        .sql_state_ref()
        .ok_or("driver_missing_sql_state")?
        .as_bytes();
    check(
        error.code == code
            && packet.error_code() == code
            && &observed_state == state
            && error.state.as_bytes() == state,
        "driver_retained_error_identity",
    )?;
    let message = packet.message_ref();
    check(
        !message.is_empty() && message.len() <= 128,
        "driver_retained_message_size",
    )?;
    Ok(
        json!({"code":code,"sqlstate":error.state,"message_hex":hex(message),
        "legacy_string_preserves_bytes":error.message.as_bytes() == message}),
    )
}

async fn corpus(conn: &mut Conn) -> Result<Value> {
    conn.query_drop("SET NAMES utf8mb4")
        .await
        .map_err(|_| "driver_set_names")?;
    conn.query_drop("SET @rp_err_message = CONVERT(0xC3A9 USING utf8mb4)")
        .await
        .map_err(|_| "driver_set_message")?;
    let utf8 = signal(conn, UTF8, 31001, b"45000").await?;
    conn.query_drop("SET SESSION character_set_results = latin1")
        .await
        .map_err(|_| "driver_set_latin1")?;
    let latin1 = signal(conn, LATIN1, 31002, b"45000").await?;
    conn.query_drop("SET SESSION character_set_results = utf8mb4")
        .await
        .map_err(|_| "driver_restore_utf8")?;
    let next = signal(conn, NEXT, 31003, b"45001").await?;
    let recovered: Option<u64> = conn
        .query_first(RECOVERY)
        .await
        .map_err(|_| "driver_recovery_query")?;
    check(
        recovered == Some(42) && conn.last_error_packet().is_none(),
        "driver_recovery_clear",
    )?;
    Ok(
        json!({"utf8_signal":utf8,"latin1_signal":latin1,"next_signal":next,
        "recovery":{"value":42,"retained_error_cleared":true}}),
    )
}

async fn run() -> Result<Value> {
    check(std::env::args_os().len() == 1, "driver_closed_arguments")?;
    let input = input()?;
    let opts = OptsBuilder::default()
        .ip_or_hostname("127.0.0.1")
        .tcp_port(input.port)
        .user(Some(DB_NAME))
        .pass(Some(input.password))
        .db_name(Some(DB_NAME))
        .prefer_socket(false)
        .client_found_rows(false)
        .multi_statements(false)
        .local_files(false);
    let mut conn = Conn::new(opts).await.map_err(|_| "driver_connection")?;
    let observations = corpus(&mut conn).await;
    conn.disconnect().await.map_err(|_| "driver_disconnect")?;
    Ok(
        json!({"schema":REPORT,"passed":true,"observations":observations?,"owned_connection_closed":true}),
    )
}

fn main() {
    let result = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| "driver_runtime")
        .and_then(|runtime| {
            runtime.block_on(async {
                tokio::time::timeout(Duration::from_secs(20), run())
                    .await
                    .map_err(|_| "driver_deadline")?
            })
        });
    match result {
        Ok(value) => println!("{value}"),
        Err(code) => {
            println!("{}", json!({"schema":REPORT,"passed":false,"failure":code}));
            std::process::exit(1);
        }
    }
}
