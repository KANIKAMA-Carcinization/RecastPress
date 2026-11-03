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
pub const FRAME: usize = 256 * 1024;
pub const REPLY: usize = 512 * 1024;
pub const REQUEST_TOTAL: usize = 8 * 1024 * 1024;
pub const REPLY_TOTAL: usize = 16 * 1024 * 1024;
pub const CELL: usize = 65536;
pub const OPS: u64 = 4096;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Home,
}
impl Mode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Home => "home",
        }
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Salts {
    #[serde(rename = "AUTH_KEY")]
    pub auth_key: String,
    #[serde(rename = "SECURE_AUTH_KEY")]
    pub secure_auth_key: String,
    #[serde(rename = "LOGGED_IN_KEY")]
    pub logged_in_key: String,
    #[serde(rename = "NONCE_KEY")]
    pub nonce_key: String,
    #[serde(rename = "AUTH_SALT")]
    pub auth_salt: String,
    #[serde(rename = "SECURE_AUTH_SALT")]
    pub secure_auth_salt: String,
    #[serde(rename = "LOGGED_IN_SALT")]
    pub logged_in_salt: String,
    #[serde(rename = "NONCE_SALT")]
    pub nonce_salt: String,
}
impl Salts {
    pub fn values(&self) -> [&str; 8] {
        [
            &self.auth_key,
            &self.secure_auth_key,
            &self.logged_in_key,
            &self.nonce_key,
            &self.auth_salt,
            &self.secure_auth_salt,
            &self.logged_in_salt,
            &self.nonce_salt,
        ]
    }
    fn value(&self) -> Value {
        json!({"AUTH_KEY":self.auth_key,"SECURE_AUTH_KEY":self.secure_auth_key,"LOGGED_IN_KEY":self.logged_in_key,"NONCE_KEY":self.nonce_key,"AUTH_SALT":self.auth_salt,"SECURE_AUTH_SALT":self.secure_auth_salt,"LOGGED_IN_SALT":self.logged_in_salt,"NONCE_SALT":self.nonce_salt})
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub schema: String,
    pub mode: Mode,
    pub host: String,
    pub port: u16,
    pub database: String,
    pub user: String,
    pub password: String,
    pub nonce: String,
    pub salts: Salts,
}
impl Input {
    pub fn context(&self) -> Value {
        json!({"schema":"recastpress.php-wasix-wp-request-context.v0.5","mode":self.mode.as_str(),"nonce":self.nonce,"salts":self.salts.value()})
    }
    pub fn secrets(&self) -> Vec<String> {
        let mut values = vec![self.password.clone(), self.nonce.clone()];
        values.extend(self.salts.values().into_iter().map(str::to_owned));
        values
    }
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
    let value: Value = serde_json::from_slice(wire).map_err(|_| "invalid_input")?;
    let mut expected = vec![
        "schema", "mode", "host", "port", "database", "user", "password", "nonce", "salts",
    ];
    expected.sort_unstable();
    let keys: Vec<_> = value
        .as_object()
        .ok_or("input_object_required")?
        .keys()
        .map(String::as_str)
        .collect();
    check(keys == expected, "input_exact_keys")?;
    check(
        input.schema == "recastpress.php-wasix-wp-request-input.v0.5"
            && input.mode == Mode::Home
            && input.host == "127.0.0.1"
            && input.port > 0
            && input.database == "rp_wpdb_probe"
            && input.user == "rp_wpdb_probe",
        "closed_input_profile",
    )?;
    check(
        input.secrets().iter().all(|s| secret_shape(s)),
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
        "escape" => check(
            seq > 1 && std::str::from_utf8(&bytes).is_ok(),
            "escape_utf8_profile",
        )?,
        "close" => check(seq > 1 && bytes.is_empty(), "close_request_shape")?,
        _ => return Err("unsupported_rpc_operation"),
    }
    Ok((request, bytes))
}

async fn query(
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
    };
    let columns = result
        .columns_ref()
        .iter()
        .map(|c| Column {
            name: c.name_ref().to_vec(),
            column_type: c.column_type() as u8,
            flags: c.flags().bits(),
            character_set: c.character_set(),
            declared_length: c.column_length(),
            decimals: c.decimals(),
            metadata_bytes: c.schema_ref().len()
                + c.table_ref().len()
                + c.org_table_ref().len()
                + c.name_ref().len()
                + c.org_name_ref().len(),
        })
        .collect();
    let mut collector =
        Collector::new(Limits::default(), columns).map_err(|_| "result_metadata_budget")?;
    while let Some(row) = result.next().await.map_err(|_| "row_transport_failed")? {
        check(row.len() <= 32, "result_column_budget")?;
        let mut borrowed = Vec::with_capacity(row.len());
        for i in 0..row.len() {
            borrowed.push(match row.as_ref(i).ok_or("taken_row_slot")? {
                Cell::NULL => None,
                Cell::Bytes(b) => Some(b.as_slice()),
                _ => return Err("non_text_protocol_cell"),
            });
        }
        collector
            .push(&borrowed)
            .map_err(|_| "result_row_cell_raw_budget")?;
    }
    check(result.is_empty(), "unexpected_additional_result")?;
    let affected = result.affected_rows().to_string();
    let insert = result.last_insert_id().map(|v| v.to_string());
    let warnings = result.warnings();
    // EOF is observed. Overflow above returns immediately; caller disconnects
    // this owned non-pooled Conn without implicit result draining/reuse.
    drop(result);
    let encoded = collector.finish().map_err(|_| "result_encoded_budget")?;
    let mut data: Value =
        serde_json::from_slice(&encoded).map_err(|_| "result_collector_encoding")?;
    let object = data.as_object_mut().ok_or("result_collector_object")?;
    object.remove("row_count");
    object.remove("raw_cell_bytes");
    object.remove("metadata_bytes");
    let status = conn
        .last_ok_packet()
        .ok_or("successful_query_status_unobserved")?
        .status_flags()
        .bits();
    *last_status = Some(status);
    object.insert("affected_rows".into(), json!(affected));
    object.insert("insert_id".into(), json!(insert));
    object.insert("warnings".into(), json!(warnings));
    object.insert("status".into(), json!(status));
    Ok((true, data))
}
fn bounded_reply(sequence: u64, ok: bool, data: &Value) -> Result<Vec<u8>> {
    use std::io::Write;
    let mut writer =
        crate::result_budget::BoundedWriter::new(REPLY).map_err(|_| "reply_allocation")?;
    serde_json::to_writer(
        &mut writer,
        &json!({"schema":"recastpress.wpdb-rpc-reply.v0.5","seq":sequence,"ok":ok,"data":data}),
    )
    .map_err(|_| "reply_encoded_budget")?;
    writer
        .write_all(b"\n")
        .map_err(|_| "reply_encoded_budget")?;
    Ok(writer.into_bytes())
}
fn safe_operation(job: &Job, sequence: u64, ok: bool, data: &Value, encoded: &[u8]) -> Value {
    json!({"seq":sequence,"op":job.request.op,"request_data_bytes":job.bytes.len(),"request_data_sha256":sha(&job.bytes),"request_frame_bytes":job.frame_bytes,"request_frame_sha256":job.frame_sha256,"reply_frame_bytes":encoded.len(),"reply_frame_sha256":sha(encoded),"ok":ok,"row_count":data["rows"].as_array().map(Vec::len),"column_count":data["columns"].as_array().map(Vec::len),"server_error_code":if !ok{data["code"].clone()}else{Value::Null},"server_error_state_hex":if !ok{data["state_hex"].clone()}else{Value::Null}})
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
            let deadline = Instant::now()+Duration::from_secs(120);
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
                    let encoded=bounded_reply(sequence,ok,&data)?;
                    check(encoded.len()<=REPLY_TOTAL.saturating_sub(reply_total),"reply_total_quota")?;
                    reply_total+=encoded.len();
                    shared.lock().map_err(|_|"actor_lock_failed")?.operations.push(safe_operation(&job,sequence,ok,&data,&encoded));
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
                .user(Some(input.user.clone()))
                .pass(Some(input.password.clone()))
                .db_name(Some("rp_wpdb_probe"))
                .prefer_socket(false)
                .client_found_rows(false)
                .multi_statements(false)
                .local_files(false)
                .max_allowed_packet(Some(1024 * 1024));
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
            check(conn.is_some(), "escape_without_connection")?;
            let text = std::str::from_utf8(bytes).map_err(|_| "escape_utf8_profile")?;
            let quoted = Cell::Bytes(text.as_bytes().to_vec())
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
    fn input(mode: &str) -> Value {
        json!({"schema":"recastpress.php-wasix-wp-request-input.v0.5","mode":mode,"host":"127.0.0.1","port":3306,"database":"rp_wpdb_probe","user":"rp_wpdb_probe","password":"a".repeat(48),"nonce":"b".repeat(48),"salts":{"AUTH_KEY":"c".repeat(48),"SECURE_AUTH_KEY":"d".repeat(48),"LOGGED_IN_KEY":"e".repeat(48),"NONCE_KEY":"f".repeat(48),"AUTH_SALT":"1".repeat(48),"SECURE_AUTH_SALT":"2".repeat(48),"LOGGED_IN_SALT":"3".repeat(48),"NONCE_SALT":"4".repeat(48)}})
    }
    fn request(seq: u64, op: &str, bytes: &[u8]) -> Vec<u8> {
        format!("{}\n",json!({"schema":"recastpress.wpdb-rpc-request.v0.5","nonce":"b".repeat(48),"seq":seq,"op":op,"data_hex":hex(bytes)})).into_bytes()
    }
    #[test]
    fn exact_mode_keys_salts_and_credentials_are_closed() {
        assert!(parse_input(&serde_json::to_vec(&input("home")).unwrap()).is_ok());
        for mode in ["install", "read", "HOME", ""] {
            assert!(parse_input(&serde_json::to_vec(&input(mode)).unwrap()).is_err());
        }
        let mut v = input("home");
        v["admin_password"] = Value::Null;
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
        v = input("home");
        v["user"] = json!("rp_wp_setup");
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
        v = input("home");
        v["salts"].as_object_mut().unwrap().remove("NONCE_SALT");
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
        v = input("home");
        v["host"] = json!("localhost");
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
    }
    #[test]
    fn unknown_duplicate_and_guest_context_secret_separation() {
        let v = input("home");
        let wire = serde_json::to_vec(&v).unwrap();
        let input = parse_input(&wire).unwrap();
        let context = input.context();
        assert_eq!(context.as_object().unwrap().len(), 4);
        for name in [
            "admin_password",
            "password",
            "user",
            "host",
            "port",
            "database",
        ] {
            assert!(context.get(name).is_none());
        }
        let duplicate = String::from_utf8(wire)
            .unwrap()
            .replacen("{", "{\"nonce\":\"bad\",", 1);
        assert!(parse_input(duplicate.as_bytes()).is_err());
        let mut v = v;
        v["extra"] = json!(true);
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
    }
    #[test]
    fn rpc_nonce_sequence_hex_and_utf8_escape_fail_before_dispatch() {
        let n = "b".repeat(48);
        assert!(parse_request(&request(1, "hello", b""), &n, 1).is_ok());
        assert!(parse_request(&request(2, "escape", "日本語".as_bytes()), &n, 2).is_ok());
        assert!(parse_request(&request(2, "escape", &[255]), &n, 2).is_err());
        assert!(parse_request(&request(2, "query", &[255]), &n, 2).is_ok());
        assert!(parse_request(&request(2, "query", b"select 1"), &n, 3).is_err());
        assert!(parse_request(&request(2, "query", b"select 1"), &"a".repeat(48), 2).is_err());
        assert!(parse_request(&request(2, "query", &vec![1; CELL + 1]), &n, 2).is_err());
        assert!(parse_request(&request(OPS + 1, "close", b""), &n, OPS + 1).is_err());
    }
    #[test]
    fn safe_operation_never_publishes_sql_rows_or_error_messages() {
        let secret = "sensitive-row-password-hash";
        let w = request(2, "query", secret.as_bytes());
        let (request, bytes) = parse_request(&w, &"b".repeat(48), 2).unwrap();
        let (reply, _) = oneshot::channel();
        let job = Job {
            request,
            bytes,
            frame_bytes: w.len(),
            frame_sha256: sha(&w),
            reply,
        };
        let data = json!({"columns":[],"rows":[[hex(secret.as_bytes())]],"message_hex":hex(secret.as_bytes()),"code":1064,"state_hex":"3432303030"});
        let raw = bounded_reply(2, false, &data).unwrap();
        let summary = safe_operation(&job, 2, false, &data, &raw);
        let text = summary.to_string();
        assert!(
            !text.contains(secret)
                && !text.contains(&hex(secret.as_bytes()))
                && summary.get("data").is_none()
                && summary.get("data_hex").is_none()
        );
        assert_eq!(summary["server_error_code"], 1064);
        assert!(bounded_reply(2, true, &json!({"rows": "x".repeat(REPLY)})).is_err());
    }
}
