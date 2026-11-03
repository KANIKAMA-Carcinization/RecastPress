//! Fixed private wpdb bridge protocol and one physically owned DB actor.
use mysql_async::{Conn, Error, OptsBuilder, Value as Cell, prelude::Queryable};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    sync::{Arc, Mutex, mpsc},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};
use tokio::sync::{oneshot, watch};
pub type Result<T> = std::result::Result<T, &'static str>;
pub const FRAME: usize = 16 * 1024;
pub const REPLY: usize = 64 * 1024;
pub const REQUEST_TOTAL: usize = 128 * 1024;
pub const REPLY_TOTAL: usize = 512 * 1024;
pub const CELL: usize = 4096;
pub const OPS: u64 = 64;

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Corpus {
    #[default]
    Standard,
    Crud,
}
impl Corpus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Standard => "standard",
            Self::Crud => "crud",
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub schema: String,
    pub host: String,
    pub port: u16,
    pub database: String,
    pub user: String,
    pub password: String,
    pub tls: String,
    pub tls_ca_pem: Value,
    pub nonce: String,
    #[serde(default)]
    pub corpus: Corpus,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub schema: String,
    pub nonce: String,
    pub seq: u64,
    pub op: String,
    pub data_hex: String,
}

pub fn check(ok: bool, reason: &'static str) -> Result<()> {
    if ok { Ok(()) } else { Err(reason) }
}

pub fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        result.push(DIGITS[(byte >> 4) as usize] as char);
        result.push(DIGITS[(byte & 15) as usize] as char);
    }
    result
}

pub fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn secret_shape(value: &str) -> bool {
    value.len() == 48
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn unhex(value: &str) -> Result<Vec<u8>> {
    check(
        value.len() <= CELL * 2 && value.len() % 2 == 0,
        "data_hex_quota",
    )?;
    let nibble = |byte| match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        _ => Err("data_hex_syntax"),
    };
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| Ok((nibble(pair[0])? << 4) | nibble(pair[1])?))
        .collect()
}

pub fn parse_input(wire: &[u8]) -> Result<Input> {
    check(wire.len() <= 20 * 1024, "input_quota")?;
    let input: Input = serde_json::from_slice(wire).map_err(|_| "invalid_input")?;
    let object: Value = serde_json::from_slice(wire).map_err(|_| "invalid_input")?;
    let mut keys: Vec<&str> = object
        .as_object()
        .ok_or("input_object_required")?
        .keys()
        .map(String::as_str)
        .collect();
    keys.sort_unstable();
    let has_corpus = keys.contains(&"corpus");
    check(
        keys.len() == 9 + usize::from(has_corpus),
        "input_exact_key_count",
    )?;
    keys.retain(|key| *key != "corpus");
    check(
        keys == [
            "database",
            "host",
            "nonce",
            "password",
            "port",
            "schema",
            "tls",
            "tls_ca_pem",
            "user",
        ],
        "input_exact_keys",
    )?;
    check(
        input.schema == "recastpress.php-wasix-wpdb-input.v0.5"
            && input.host == "127.0.0.1"
            && input.port > 0
            && input.database == "rp_wpdb_probe"
            && input.user == "rp_wpdb_probe"
            && input.tls == "disabled"
            && input.tls_ca_pem.is_null(),
        "closed_input_profile",
    )?;
    check(
        secret_shape(&input.password) && secret_shape(&input.nonce),
        "input_secret_shape",
    )?;
    Ok(input)
}

pub fn parse_request(wire: &[u8], nonce: &str, seq: u64) -> Result<(Request, Vec<u8>)> {
    check(
        wire.len() <= FRAME
            && wire.last() == Some(&b'\n')
            && wire.iter().filter(|&&byte| byte == b'\n').count() == 1,
        "request_frame_shape",
    )?;
    let request: Request = serde_json::from_slice(wire).map_err(|_| "invalid_rpc_request")?;
    check(
        request.schema == "recastpress.wpdb-rpc-request.v0.5"
            && request.nonce == nonce
            && request.seq == seq
            && (1..=OPS).contains(&seq),
        "rpc_identity_or_sequence",
    )?;
    let bytes = unhex(&request.data_hex)?;
    match request.op.as_str() {
        "hello" => check(seq == 1 && bytes.is_empty(), "hello_request_shape")?,
        "query" => check(seq > 1 && !bytes.is_empty(), "query_request_shape")?,
        "escape" => check(seq > 1 && bytes.is_ascii(), "escape_ascii_profile")?,
        "close" => check(seq > 1 && bytes.is_empty(), "close_request_shape")?,
        _ => return Err("unsupported_rpc_operation"),
    }
    Ok((request, bytes))
}

fn column_value(column: &mysql_async::Column) -> Value {
    json!({"name_hex":hex(column.name_ref()),"type":column.column_type() as u8,
        "flags":column.flags().bits(),"character_set":column.character_set(),
        "declared_length":column.column_length(),"decimals":column.decimals()})
}

async fn query(
    conn: &mut Conn,
    sql: &[u8],
    last_status: &mut Option<u16>,
) -> Result<(bool, Value)> {
    let mut result = match conn.query_iter(sql).await {
        Ok(result) => result,
        Err(Error::Server(error)) => {
            check(
                error.state.is_ascii()
                    && error.state.len() == 5
                    && error.message.is_ascii()
                    && error.message.len() <= CELL,
                "server_error_ascii_profile",
            )?;
            return Ok((
                false,
                json!({"kind":"server","code":error.code,"state_hex":hex(error.state.as_bytes()),
                "message_hex":hex(error.message.as_bytes()),"raw_err_preserved":false}),
            ));
        }
        Err(_) => return Err("query_transport_failed"),
    };
    let columns: Vec<_> = result.columns_ref().iter().map(column_value).collect();
    check(columns.len() <= 16, "column_quota")?;
    let mut rows = Vec::new();
    while let Some(row) = result.next().await.map_err(|_| "row_transport_failed")? {
        check(
            rows.len() < 16 && row.len() == columns.len(),
            "row_quota_or_width",
        )?;
        let mut cells = Vec::new();
        for index in 0..row.len() {
            cells.push(match row.as_ref(index).ok_or("taken_row_slot")? {
                Cell::NULL => Value::Null,
                Cell::Bytes(bytes) => {
                    check(bytes.len() <= CELL, "cell_quota")?;
                    json!(hex(bytes))
                }
                _ => return Err("non_text_protocol_cell"),
            });
        }
        rows.push(json!(cells));
    }
    check(result.is_empty(), "unexpected_additional_result")?;
    let affected = result.affected_rows().to_string();
    let insert = result.last_insert_id().map(|id| id.to_string());
    let warnings = result.warnings();
    result
        .drop_result()
        .await
        .map_err(|_| "result_drain_failed")?;
    let status = conn
        .last_ok_packet()
        .ok_or("successful_query_status_unobserved")?
        .status_flags()
        .bits();
    *last_status = Some(status);
    Ok((
        true,
        json!({"columns":columns,"rows":rows,"affected_rows":affected,"insert_id":insert,
        "warnings":warnings,"status":status}),
    ))
}

#[derive(Default)]
pub struct Observation {
    pub operations: Vec<Value>,
    pub connection_created: bool,
    pub disconnect_attempted: bool,
    pub disconnect_completed: bool,
    pub failure: Option<&'static str>,
    pub exited: bool,
}
pub struct Job {
    pub request: Request,
    pub bytes: Vec<u8>,
    pub frame_bytes: usize,
    pub frame_sha256: String,
    pub reply: oneshot::Sender<Result<Vec<u8>>>,
}
#[derive(Clone)]
pub struct Dispatch(pub mpsc::SyncSender<Job>);
pub struct Actor {
    pub dispatch: Dispatch,
    pub observation: Arc<Mutex<Observation>>,
    stop: watch::Sender<bool>,
    worker: Option<JoinHandle<()>>,
    finish_attempted: bool,
}
impl Actor {
    pub fn start(input: Input) -> Result<Self> {
        let (send, receive) = mpsc::sync_channel::<Job>(1);
        let (stop, mut stopping) = watch::channel(false);
        let observation = Arc::new(Mutex::new(Observation::default()));
        let shared = observation.clone();
        let worker = thread::Builder::new().name("wpdb-owned-db".into()).spawn(move || {
            let runtime = match tokio::runtime::Builder::new_current_thread().enable_all().build() {
                Ok(runtime) => runtime,
                Err(_) => { if let Ok(mut s)=shared.lock(){s.failure=Some("actor_runtime_failed");s.exited=true;} return; }
            };
            let deadline = Instant::now()+Duration::from_secs(60);
            let mut connection: Option<Conn> = None;
            let mut status = None;
            let mut sequence=1;
            let mut reply_total=0usize;
            loop {
                if *stopping.borrow() || Instant::now()>=deadline { break; }
                let job = match receive.recv_timeout(Duration::from_millis(20)) {
                    Ok(job)=>job,
                    Err(mpsc::RecvTimeoutError::Timeout)=>continue,
                    Err(_)=>break,
                };
                let close=job.request.op=="close";
                let result=runtime.block_on(async {
                    let limit=deadline.saturating_duration_since(Instant::now()).min(Duration::from_secs(10));
                    tokio::select! {
                        _=stopping.changed()=>Err("actor_cancelled"),
                        value=tokio::time::timeout(limit, dispatch(&input,&job.request,&job.bytes,&mut connection,&mut status,&shared,sequence))=>value.map_err(|_|"db_operation_deadline")?,
                    }
                }).and_then(|(ok,data)| {
                    let mut encoded=serde_json::to_vec(&json!({"schema":"recastpress.wpdb-rpc-reply.v0.5","seq":sequence,"ok":ok,"data":data})).map_err(|_|"reply_encoding_failed")?;
                    encoded.push(b'\n');
                    check(encoded.len()<=REPLY && encoded.len()<=REPLY_TOTAL.saturating_sub(reply_total),"reply_quota")?;
                    reply_total+=encoded.len();
                    shared.lock().map_err(|_|"actor_lock_failed")?.operations.push(json!({"seq":sequence,"op":job.request.op,"data_hex":job.request.data_hex,"request_frame_bytes":job.frame_bytes,"request_frame_sha256":job.frame_sha256,"reply_frame_bytes":encoded.len(),"reply_frame_sha256":sha(&encoded),"ok":ok,"data":data}));
                    Ok(encoded)
                });
                let failed=result.as_ref().err().copied();
                if let Some(reason)=failed { if let Ok(mut s)=shared.lock(){s.failure=Some(reason);} }
                let delivered=job.reply.send(result).is_ok();
                if failed.is_some() || !delivered || close { break; }
                sequence+=1;
            }
            if let Some(conn)=connection.take() {
                if let Ok(mut s)=shared.lock(){s.disconnect_attempted=true;}
                let disconnected=runtime.block_on(async {tokio::time::timeout(Duration::from_secs(2),conn.disconnect()).await.is_ok_and(|r|r.is_ok())});
                if let Ok(mut s)=shared.lock(){s.disconnect_completed=disconnected;if !disconnected{s.failure.get_or_insert("cleanup_disconnect_failed");}}
            }
            if let Ok(mut s)=shared.lock(){s.exited=true;}
        }).map_err(|_|"actor_spawn_failed")?;
        Ok(Self {
            dispatch: Dispatch(send),
            observation,
            stop,
            worker: Some(worker),
            finish_attempted: false,
        })
    }
    pub fn finish(&mut self) -> bool {
        let _ = self.stop.send(true);
        let end = Instant::now() + Duration::from_secs(3);
        if let Some(worker) = self.worker.as_ref().filter(|_| !self.finish_attempted) {
            while !worker.is_finished() && Instant::now() < end {
                thread::sleep(Duration::from_millis(5));
            }
        }
        self.finish_attempted = true;
        if self
            .worker
            .as_ref()
            .is_some_and(|worker| worker.is_finished())
        {
            return self
                .worker
                .take()
                .is_some_and(|worker| worker.join().is_ok());
        }
        false
    }
}
impl Drop for Actor {
    fn drop(&mut self) {
        if self.worker.is_some() && !self.finish_attempted {
            let _ = self.finish();
        }
    }
}
async fn dispatch(
    input: &Input,
    request: &Request,
    bytes: &[u8],
    conn: &mut Option<Conn>,
    status: &mut Option<u16>,
    observation: &Arc<Mutex<Observation>>,
    sequence: u64,
) -> Result<(bool, Value)> {
    check(request.seq == sequence, "actor_sequence")?;
    match request.op.as_str() {
        "hello" => {
            check(sequence == 1 && conn.is_none(), "hello_phase")?;
            let options = OptsBuilder::default()
                .ip_or_hostname("127.0.0.1")
                .tcp_port(input.port)
                .user(Some("rp_wpdb_probe"))
                .pass(Some(input.password.clone()))
                .db_name(Some("rp_wpdb_probe"))
                .prefer_socket(false)
                .client_found_rows(false)
                .multi_statements(false)
                .local_files(false);
            *conn = Some(Conn::new(options).await.map_err(|_| "db_connect_failed")?);
            observation
                .lock()
                .map_err(|_| "actor_lock_failed")?
                .connection_created = true;
            let c = conn.as_ref().ok_or("connection_missing")?;
            let caps = c.negotiated_capabilities().bits();
            check(
                caps & (2 | 128 | 2048 | 65536) == 0,
                "forbidden_connection_capability",
            )?;
            *status = c
                .last_ok_packet()
                .map(|packet| packet.status_flags().bits());
            Ok((
                true,
                json!({"server_version_hex":hex(c.raw_server_version()),"connection_id":c.id(),"caps":caps,"charset":"utf8mb4","collation":"utf8mb4_unicode_520_ci"}),
            ))
        }
        "query" => {
            query(
                conn.as_mut().ok_or("query_without_connection")?,
                bytes,
                status,
            )
            .await
        }
        "escape" => {
            check(conn.is_some() && bytes.is_ascii(), "escape_ascii_profile")?;
            let quoted = Cell::Bytes(bytes.to_vec())
                .as_sql(status.ok_or("escape_status_unobserved")? & 0x0200 != 0);
            let escaped = quoted
                .strip_prefix('\'')
                .and_then(|s| s.strip_suffix('\''))
                .ok_or("escape_quote_shape")?;
            Ok((true, json!({"escaped_hex":hex(escaped.as_bytes())})))
        }
        "close" => {
            let c = conn.take().ok_or("close_without_connection")?;
            observation
                .lock()
                .map_err(|_| "actor_lock_failed")?
                .disconnect_attempted = true;
            c.disconnect()
                .await
                .map_err(|_| "physical_disconnect_failed")?;
            observation
                .lock()
                .map_err(|_| "actor_lock_failed")?
                .disconnect_completed = true;
            Ok((true, json!({"closed":true})))
        }
        _ => Err("unsupported_rpc_operation"),
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn input() -> Value {
        json!({"schema":"recastpress.php-wasix-wpdb-input.v0.5","host":"127.0.0.1","port":3306,"database":"rp_wpdb_probe","user":"rp_wpdb_probe","password":"a".repeat(48),"tls":"disabled","tls_ca_pem":null,"nonce":"b".repeat(48)})
    }
    fn frame(nonce: &str, seq: u64, data: &str) -> Vec<u8> {
        format!("{}\n",json!({"schema":"recastpress.wpdb-rpc-request.v0.5","nonce":nonce,"seq":seq,"op":"hello","data_hex":data})).into_bytes()
    }
    #[test]
    fn input_keys_and_closed_profile() {
        let mut v = input();
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_ok());
        v.as_object_mut().unwrap().remove("tls_ca_pem");
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
        v = input();
        v["tls"] = json!("required");
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
    }
    #[test]
    fn request_identity_bounds_and_duplicate() {
        let n = "b".repeat(48);
        assert!(parse_request(&frame(&n, 1, ""), &n, 1).is_ok());
        assert!(parse_request(&frame(&n, 2, ""), &n, 1).is_err());
        assert!(parse_request(&frame(&"a".repeat(48), 1, ""), &n, 1).is_err());
        assert!(parse_request(&frame(&n, 1, "0"), &n, 1).is_err());
        let duplicate =
            String::from_utf8(frame(&n, 1, ""))
                .unwrap()
                .replacen("{", "{\"seq\":1,", 1);
        assert!(parse_request(duplicate.as_bytes(), &n, 1).is_err());
    }
}

#[cfg(test)]
mod corpus_tests {
    use super::*;
    fn value() -> Value {
        json!({"schema":"recastpress.php-wasix-wpdb-input.v0.5","host":"127.0.0.1","port":3306,"database":"rp_wpdb_probe","user":"rp_wpdb_probe","password":"a".repeat(48),"tls":"disabled","tls_ca_pem":null,"nonce":"b".repeat(48)})
    }
    #[test]
    fn corpus_absence_defaults_standard_and_only_closed_strings_are_accepted() {
        let mut v = value();
        assert_eq!(
            parse_input(&serde_json::to_vec(&v).unwrap())
                .unwrap()
                .corpus,
            Corpus::Standard
        );
        v["corpus"] = json!("standard");
        assert_eq!(
            parse_input(&serde_json::to_vec(&v).unwrap())
                .unwrap()
                .corpus,
            Corpus::Standard
        );
        v["corpus"] = json!("crud");
        assert_eq!(
            parse_input(&serde_json::to_vec(&v).unwrap())
                .unwrap()
                .corpus,
            Corpus::Crud
        );
        for invalid in [
            Value::Null,
            json!(true),
            json!(3),
            json!("STANDARD"),
            json!("unknown"),
            json!(["crud"]),
        ] {
            v["corpus"] = invalid;
            assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
        }
    }
    #[test]
    fn optional_corpus_does_not_allow_missing_unknown_or_duplicate_input_keys() {
        let mut v = value();
        v["corpus"] = json!("crud");
        v.as_object_mut().unwrap().remove("tls_ca_pem");
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
        v = value();
        v["corpus"] = json!("crud");
        v["extra"] = json!(0);
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
        v = value();
        v["corpus"] = json!("crud");
        let mut wire = serde_json::to_vec(&v).unwrap();
        wire.pop();
        wire.extend_from_slice(b",\"corpus\":\"standard\"}");
        assert!(parse_input(&wire).is_err());
    }
}
