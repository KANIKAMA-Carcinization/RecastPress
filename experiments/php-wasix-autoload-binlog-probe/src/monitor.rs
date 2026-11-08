//! The fixed autoload option's SQL identity and full binlog row shape.

#[path = "monitor_impl.rs"]
mod implementation;
pub(crate) use implementation::Monitor;

use anyhow::{Context, Result, anyhow, ensure};
use mysql_async::binlog::{row::BinlogRow, value::BinlogValue};
use mysql_async::{Conn, Value, prelude::Queryable};

const OPTION_NAME: &[u8] = b"rp_autoload_probe_marker";
const AUTOLOAD: &[u8] = b"yes";

async fn autoload_id(connection: &mut Conn, expected: &[u8]) -> Result<u64> {
    let (id, name, value, autoload): (u64, Vec<u8>, Vec<u8>, Vec<u8>) = connection
        .query_first("SELECT option_id, option_name, option_value, autoload FROM rp_wpdb_probe.rp_options WHERE option_name='rp_autoload_probe_marker'")
        .await
        .map_err(|_| anyhow!("dependency_monitor_value"))?
        .context("dependency_monitor_option_missing")?;
    ensure!(
        id != 0 && name == OPTION_NAME && value == expected && autoload == AUTOLOAD,
        "dependency_monitor_value_mismatch"
    );
    Ok(id)
}

fn check_row(row: &BinlogRow, id: u64, expected: &[u8]) -> Result<()> {
    let row_id = match row.as_ref(0) {
        Some(BinlogValue::Value(Value::UInt(value))) => Some(*value),
        // Without signedness metadata the driver can decode unsigned BIGINT as
        // signed. The pinned WordPress schema declares option_id unsigned.
        Some(BinlogValue::Value(Value::Int(value))) => {
            Some(u64::from_ne_bytes(value.to_ne_bytes()))
        }
        _ => None,
    };
    let bytes_match = |column, expected: &[u8]| matches!(row.as_ref(column), Some(BinlogValue::Value(Value::Bytes(bytes))) if bytes == expected);
    ensure!(
        row.len() == 4
            && row_id == Some(id)
            && bytes_match(1, OPTION_NAME)
            && bytes_match(2, expected)
            && bytes_match(3, AUTOLOAD),
        "dependency_monitor_option_image"
    );
    Ok(())
}
