"""Derive the private raw ERR actor from the pinned bootstrap actor."""

import argparse
from hashlib import sha256
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'php-wasix-wp-bootstrap-probe/src/db.rs'
SOURCE_SHA = '368cbf4736cec5c24f7f8c213d756dbb52955f2df3a9878b55f141e13d0d299f'
GENERATED = HERE / 'src/db.rs'


def require(condition, code):
    if not condition:
        raise ValueError(code)


def replace(source, old, new):
    require(source.count(old) == 1, 'raw_err_actor_edit_boundary')
    return source.replace(old, new, 1)


QUERY_HEADER = '''async fn query(
    conn: &mut Conn,
    sql: &[u8],
    last_status: &mut Option<u16>,
) -> Result<(bool, Value)> {
    use crate::result_budget::{Collector, Column, Limits};
    let mut result = match conn.query_iter(sql).await {
        Ok(r) => r,
        Err(Error::Server(e)) => {
            check(
                e.state.is_ascii() && e.state.len() == 5 && e.message.len() <= CELL,
                "server_error_string_quota",
            )?;
            return Ok((
                false,
                json!({"kind":"server","code":e.code,"state_hex":hex(e.state.as_bytes()),"message_hex":hex(e.message.as_bytes()),"raw_err_preserved":false}),
            ));
        }
        Err(_) => return Err("query_transport_failed"),
    };'''

QUERY_OWNED = '''enum QueryOutcome {
    Server { code: u16, state: String },
    Success { data: Value, affected: String, insert: Option<String>, warnings: u16 },
}

// Return only owned data so a QueryResult can no longer borrow Conn when its
// retained packet is inspected. Never issue a second command in between.
async fn query_owned(conn: &mut Conn, sql: &[u8]) -> Result<QueryOutcome> {
    use crate::result_budget::{Collector, Column, Limits};
    let mut result = match conn.query_iter(sql).await {
        Ok(r) => r,
        Err(Error::Server(e)) => return Ok(QueryOutcome::Server { code: e.code, state: e.state }),
        Err(_) => return Err("query_transport_failed"),
    };'''

QUERY_TAIL = '''    object.remove("metadata_bytes");
    let status = conn'''

QUERY_RETAINED = '''    object.remove("metadata_bytes");
    Ok(QueryOutcome::Success { data, affected, insert, warnings })
}

async fn query(
    conn: &mut Conn,
    sql: &[u8],
    last_status: &mut Option<u16>,
) -> Result<(bool, Value)> {
    let (mut data, affected, insert, warnings) = match query_owned(conn, sql).await? {
        QueryOutcome::Server { code, state } => {
            let packet = conn.last_error_packet().ok_or("server_error_packet_missing")?;
            let raw_state = packet.sql_state_ref().ok_or("server_error_sqlstate_missing")?.as_bytes();
            check(packet.error_code() == code && raw_state.is_ascii()
                && state.as_bytes() == raw_state.as_slice(), "server_error_packet_identity")?;
            let raw_message = packet.message_ref();
            check(!raw_message.is_empty() && raw_message.len() <= CELL && !raw_message.contains(&0),
                "server_error_message_profile")?;
            return Ok((false, json!({"kind":"server","code":code,"state_hex":hex(&raw_state),
                "message_hex":hex(raw_message),"raw_err_preserved":true})));
        }
        QueryOutcome::Success { data, affected, insert, warnings } => (data, affected, insert, warnings),
    };
    check(conn.last_error_packet().is_none(), "successful_query_retained_error")?;
    let object = data.as_object_mut().ok_or("result_collector_object")?;
    let status = conn'''


def derived_bytes():
    require(SOURCE.resolve(strict=True) == SOURCE and SOURCE.is_file(), 'raw_err_actor_input_path')
    wire = SOURCE.read_bytes()
    require(sha256(wire).hexdigest() == SOURCE_SHA, 'raw_err_actor_input_changed')
    source = wire.decode('utf-8')
    return replace(replace(source, QUERY_HEADER, QUERY_OWNED), QUERY_TAIL, QUERY_RETAINED).encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    wire = derived_bytes()
    require(GENERATED.resolve() == GENERATED, 'raw_err_actor_generated_alias')
    if GENERATED.exists():
        require(GENERATED.is_file() and GENERATED.read_bytes() == wire, 'raw_err_actor_generated_changed')
    else:
        require(not args.verify_only, 'raw_err_actor_generated_missing')
        GENERATED.parent.mkdir(exist_ok=True)
        with GENERATED.open('xb') as output:
            output.write(wire)
    print(sha256(wire).hexdigest())


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError):
        print('Raw ERR actor derivation failed', file=sys.stderr)
        sys.exit(1)
