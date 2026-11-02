#![forbid(unsafe_code)]

use mysql_async::{Conn, Error, OptsBuilder, Value as Cell, prelude::Queryable};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::{self, Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

type Result<T> = std::result::Result<T, &'static str>;
const FRAME: usize = 16 * 1024;
const REPLY: usize = 64 * 1024;
const REQUEST_TOTAL: usize = 128 * 1024;
const REPLY_TOTAL: usize = 512 * 1024;
const OUTPUT: usize = 65 * 1024;
const CELL: usize = 4096;
const OPS: u64 = 64;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    schema: String,
    host: String,
    port: u16,
    database: String,
    user: String,
    password: String,
    tls: String,
    tls_ca_pem: Value,
    nonce: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    schema: String,
    nonce: String,
    seq: u64,
    op: String,
    data_hex: String,
}

fn check(ok: bool, reason: &'static str) -> Result<()> {
    if ok { Ok(()) } else { Err(reason) }
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        result.push(DIGITS[(byte >> 4) as usize] as char);
        result.push(DIGITS[(byte & 15) as usize] as char);
    }
    result
}

fn sha(bytes: &[u8]) -> String {
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

fn parse_input(wire: &[u8]) -> Result<Input> {
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
        input.schema == "recastpress.wpdb-bridge-input.v0.5"
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

fn parse_request(wire: &[u8], nonce: &str, seq: u64) -> Result<(Request, Vec<u8>)> {
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

fn remaining(deadline: Instant, cap: Duration) -> Result<Duration> {
    let time = deadline
        .checked_duration_since(Instant::now())
        .ok_or("whole_probe_deadline")?;
    Ok(time.min(cap))
}

fn read_frame(stream: &mut TcpStream, deadline: Instant, captures: &Captures) -> Result<Vec<u8>> {
    let mut wire = Vec::new();
    loop {
        remaining(deadline, Duration::from_secs(1))?;
        check(!captures.exceeded(), "php_capture_quota")?;
        let mut byte = [0];
        match stream.read(&mut byte) {
            Ok(0) => return Err("rpc_eof_before_close"),
            Ok(_) => {
                check(wire.len() < FRAME, "request_frame_quota")?;
                wire.push(byte[0]);
                if byte[0] == b'\n' {
                    return Ok(wire);
                }
            }
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                ) =>
            {
                continue;
            }
            Err(_) => return Err("rpc_read_failed"),
        }
    }
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

struct Capture {
    wire: Vec<u8>,
    overflow: bool,
    eof: bool,
    error: bool,
}

type SharedCapture = Arc<Mutex<Capture>>;
struct Captures {
    stdout: SharedCapture,
    stderr: SharedCapture,
}

impl Captures {
    fn new() -> Self {
        let one = || {
            Arc::new(Mutex::new(Capture {
                wire: Vec::new(),
                overflow: false,
                eof: false,
                error: false,
            }))
        };
        Self {
            stdout: one(),
            stderr: one(),
        }
    }
    fn exceeded(&self) -> bool {
        [&self.stdout, &self.stderr].iter().any(|state| {
            state
                .lock()
                .map_or(true, |state| state.overflow || state.error)
        })
    }
}

fn capture(mut input: impl Read + Send + 'static, state: SharedCapture) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut chunk = [0; 4096];
        loop {
            let read = input.read(&mut chunk);
            let Ok(mut state) = state.lock() else {
                return;
            };
            match read {
                Ok(0) => {
                    state.eof = true;
                    return;
                }
                Ok(count) => {
                    let available = OUTPUT.saturating_sub(state.wire.len());
                    state.wire.extend_from_slice(&chunk[..count.min(available)]);
                    if count > available {
                        state.overflow = true;
                    }
                }
                Err(_) => {
                    state.error = true;
                    return;
                }
            }
        }
    })
}

fn child_finished(child: &mut Child) -> Result<bool> {
    Ok(child.try_wait().map_err(|_| "php_wait_failed")?.is_some())
}

fn reap_php(
    child: &mut Child,
    deadline: Instant,
    captures: &Captures,
    force: bool,
    report: &mut Value,
) -> Result<()> {
    let forced = force || captures.exceeded();
    let mut until = if forced {
        deadline.min(Instant::now() + Duration::from_secs(3))
    } else {
        deadline
    };
    let mut execution_expired = false;
    if forced && !child_finished(child)? {
        if child.kill().is_err() {
            report["php_kill_failure_observed"] = json!(true);
        }
        report["php_forced_kill"] = json!(true);
    }
    loop {
        if let Some(status) = child.try_wait().map_err(|_| "php_wait_failed")? {
            report["owned_php_reaped"] = json!(true);
            report["php_exit_success"] = json!(status.success());
            report["php_exit_code"] = json!(status.code());
            return if execution_expired {
                Err("php_exit_deadline")
            } else {
                Ok(())
            };
        }
        if Instant::now() >= until {
            if forced || execution_expired {
                return Err("php_reap_deadline");
            }
            if child.kill().is_err() {
                report["php_kill_failure_observed"] = json!(true);
            }
            report["php_forced_kill"] = json!(true);
            execution_expired = true;
            until = Instant::now() + Duration::from_secs(3);
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn record_capture(captures: &Captures, report: &mut Value) -> Result<Vec<u8>> {
    let mut output = None;
    for (name, shared) in [("stdout", &captures.stdout), ("stderr", &captures.stderr)] {
        let state = shared.lock().map_err(|_| "capture_lock_failed")?;
        report[format!("php_{name}_bytes")] = json!(state.wire.len());
        report[format!("php_{name}_sha256")] = json!(sha(&state.wire));
        report[format!("php_{name}_eof")] = json!(state.eof);
        report[format!("php_{name}_overflow")] = json!(state.overflow);
        check(
            state.eof && !state.error && !state.overflow,
            "php_capture_incomplete",
        )?;
        if name == "stdout" {
            output = Some(state.wire.clone());
        } else {
            check(state.wire.is_empty(), "php_stderr_not_empty")?;
        }
    }
    output.ok_or("stdout_capture_missing")
}

fn normal_fixture_argument() -> Result<PathBuf> {
    // PHP's include/realpath behavior differs for a Windows verbatim script
    // argument. Keep only this CLI argument in Cargo's ordinary absolute form;
    // run() still binds it to the canonical, fixed fixture before spawning.
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let root = manifest
        .parent()
        .and_then(|parent| parent.parent())
        .ok_or("manifest_root_missing")?;
    check(root.is_absolute(), "manifest_root_not_absolute")?;
    #[cfg(windows)]
    check(
        matches!(root.components().next(), Some(std::path::Component::Prefix(prefix))
            if matches!(prefix.kind(), std::path::Prefix::Disk(_))),
        "script_argument_not_normal_windows_path",
    )?;
    Ok(root.join("experiments/wpdb-bridge-probe/fixtures/bridge.php"))
}

fn run(input: &Input, report: &mut Value) -> Result<()> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .map_err(|_| "repo_root_missing")?;
    let php = root.join(".reference/php-8.5.12/php.exe");
    let fixture = root.join("experiments/wpdb-bridge-probe/fixtures/bridge.php");
    check(
        php.canonicalize().map_err(|_| "php_input_missing")? == php
            && fixture
                .canonicalize()
                .map_err(|_| "fixture_input_missing")?
                == fixture,
        "fixed_php_or_fixture_linked",
    )?;
    let script_argument = normal_fixture_argument()?;
    check(
        script_argument
            .canonicalize()
            .map_err(|_| "script_argument_missing")?
            == fixture,
        "script_argument_fixture_mismatch",
    )?;
    let deadline = Instant::now() + Duration::from_secs(60);
    let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|_| "private_listener_failed")?;
    listener
        .set_nonblocking(true)
        .map_err(|_| "listener_nonblocking_failed")?;
    let address = listener
        .local_addr()
        .map_err(|_| "private_listener_address")?;
    check(address.ip().is_loopback(), "private_listener_not_loopback")?;
    let mut command = Command::new(php);
    command
        .args(["-n", "-d", "display_errors=0", "-d", "log_errors=0"])
        .arg(script_argument)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (name, _) in std::env::vars_os() {
        let upper = name.to_string_lossy().to_ascii_uppercase();
        if upper.starts_with("MYSQL_")
            || upper.starts_with("MARIADB_")
            || upper.starts_with("RP_")
            || [
                "PHPRC",
                "PHP_INI_SCAN_DIR",
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "ACTIONS_RUNTIME_TOKEN",
            ]
            .contains(&upper.as_str())
        {
            command.env_remove(name);
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn().map_err(|_| "php_spawn_failed")?;
    report["php_spawned"] = json!(true);
    report["owned_php_pid"] = json!(child.id());
    let captures = Captures::new();
    let mut workers = Vec::new();
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build();
    let mut conn = None;
    let mut stream = None;
    let result = (|| -> Result<()> {
        workers.push(capture(
            child.stdout.take().ok_or("stdout_pipe_missing")?,
            captures.stdout.clone(),
        ));
        workers.push(capture(
            child.stderr.take().ok_or("stderr_pipe_missing")?,
            captures.stderr.clone(),
        ));
        let request =
            serde_json::to_vec(&json!({"schema":"recastpress.wpdb-bridge-php-input.v0.5",
            "host":"127.0.0.1","port":address.port(),"nonce":input.nonce}))
            .map_err(|_| "php_input_encoding")?;
        check(request.len() <= 512, "php_input_quota")?;
        let mut stdin = child.stdin.take().ok_or("stdin_pipe_missing")?;
        stdin
            .write_all(&request)
            .map_err(|_| "php_input_write_failed")?;
        drop(stdin);
        let accept_deadline = deadline.min(Instant::now() + Duration::from_secs(10));
        let socket = loop {
            check(!captures.exceeded(), "php_capture_quota")?;
            match listener.accept() {
                Ok((socket, peer)) => {
                    check(peer.ip().is_loopback(), "rpc_peer_not_loopback")?;
                    break socket;
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    check(!child_finished(&mut child)?, "php_exited_before_rpc")?;
                    check(Instant::now() < accept_deadline, "rpc_accept_deadline")?;
                    thread::sleep(Duration::from_millis(10));
                }
                Err(_) => return Err("rpc_accept_failed"),
            }
        };
        socket
            .set_read_timeout(Some(Duration::from_millis(100)))
            .map_err(|_| "rpc_read_timeout_setup")?;
        socket
            .set_write_timeout(Some(Duration::from_secs(1)))
            .map_err(|_| "rpc_write_timeout_setup")?;
        stream = Some(socket);
        drop(listener);
        let runtime = runtime.as_ref().map_err(|_| "runtime_creation_failed")?;
        let mut status = None;
        let mut requests = 0usize;
        let mut replies = 0usize;
        let mut hello = false;
        for seq in 1..=OPS {
            let socket = stream.as_mut().ok_or("rpc_socket_missing")?;
            let wire = read_frame(socket, deadline, &captures)?;
            requests = requests
                .checked_add(wire.len())
                .ok_or("request_total_quota")?;
            check(requests <= REQUEST_TOTAL, "request_total_quota")?;
            let (request, bytes) = parse_request(&wire, &input.nonce, seq)?;
            let mut close = false;
            let (ok, data) = match request.op.as_str() {
                "hello" => {
                    check(
                        seq == 1 && !hello && bytes.is_empty() && conn.is_none(),
                        "hello_phase",
                    )?;
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
                    let connection = runtime
                        .block_on(async {
                            tokio::time::timeout(
                                remaining(deadline, Duration::from_secs(10))?,
                                Conn::new(options),
                            )
                            .await
                            .map_err(|_| "connect_deadline")
                        })?
                        .map_err(|_| "db_connect_failed")?;
                    conn = Some(connection);
                    report["physical_connection_created"] = json!(true);
                    let connection = conn.as_ref().ok_or("connection_ownership_missing")?;
                    let caps = connection.negotiated_capabilities().bits();
                    check(
                        caps & (2 | 128 | 2048 | 65536) == 0,
                        "forbidden_connection_capability",
                    )?;
                    status = connection
                        .last_ok_packet()
                        .map(|packet| packet.status_flags().bits());
                    let data = json!({"server_version_hex":hex(connection.raw_server_version()),"connection_id":connection.id(),
                        "caps":caps,"charset":"utf8mb4","collation":"utf8mb4_unicode_520_ci"});
                    hello = true;
                    (true, data)
                }
                "query" => {
                    check(hello && !bytes.is_empty(), "query_phase")?;
                    let connection = conn.as_mut().ok_or("query_without_connection")?;
                    runtime.block_on(async {
                        tokio::time::timeout(
                            remaining(deadline, Duration::from_secs(10))?,
                            query(connection, &bytes, &mut status),
                        )
                        .await
                        .map_err(|_| "query_deadline")
                    })??
                }
                "escape" => {
                    check(
                        hello && conn.is_some() && bytes.is_ascii(),
                        "escape_ascii_profile",
                    )?;
                    let last_status = status.ok_or("escape_status_unobserved")?;
                    let quoted = Cell::Bytes(bytes).as_sql(last_status & 0x0200 != 0);
                    let escaped = quoted
                        .strip_prefix('\'')
                        .and_then(|value| value.strip_suffix('\''))
                        .ok_or("escape_quote_shape")?;
                    (true, json!({"escaped_hex":hex(escaped.as_bytes())}))
                }
                "close" => {
                    check(hello && bytes.is_empty(), "close_phase")?;
                    let connection = conn.take().ok_or("close_without_connection")?;
                    report["physical_disconnect_attempted"] = json!(true);
                    runtime
                        .block_on(async {
                            tokio::time::timeout(
                                remaining(deadline, Duration::from_secs(10))?,
                                connection.disconnect(),
                            )
                            .await
                            .map_err(|_| "disconnect_deadline")
                        })?
                        .map_err(|_| "physical_disconnect_failed")?;
                    report["physical_disconnect_completed"] = json!(true);
                    close = true;
                    (true, json!({"closed":true}))
                }
                _ => return Err("unsupported_rpc_operation"),
            };
            let reply =
                json!({"schema":"recastpress.wpdb-rpc-reply.v0.5","seq":seq,"ok":ok,"data":data});
            let mut encoded = serde_json::to_vec(&reply).map_err(|_| "reply_encoding_failed")?;
            encoded.push(b'\n');
            check(encoded.len() <= REPLY, "reply_frame_quota")?;
            replies = replies
                .checked_add(encoded.len())
                .ok_or("reply_total_quota")?;
            check(replies <= REPLY_TOTAL, "reply_total_quota")?;
            report["operations"].as_array_mut().ok_or("report_operations_missing")?.push(json!({"seq":seq,"op":request.op,
                "data_hex":request.data_hex,"request_frame_bytes":wire.len(),"request_frame_sha256":sha(&wire),
                "reply_frame_bytes":encoded.len(),"reply_frame_sha256":sha(&encoded),"ok":ok,"data":data}));
            report["request_bytes"] = json!(requests);
            report["reply_bytes"] = json!(replies);
            socket
                .write_all(&encoded)
                .map_err(|_| "rpc_reply_write_failed")?;
            if close {
                socket
                    .shutdown(Shutdown::Both)
                    .map_err(|_| "rpc_close_failed")?;
                return Ok(());
            }
        }
        Err("operation_quota_without_close")
    })();
    if let Some(socket) = stream.take() {
        let _ = socket.shutdown(Shutdown::Both);
    }
    if let Some(connection) = conn.take() {
        report["cleanup_disconnect_attempted"] = json!(true);
        if let Ok(runtime) = runtime.as_ref() {
            let disconnected = runtime.block_on(async {
                tokio::time::timeout(Duration::from_secs(3), connection.disconnect()).await
            });
            report["cleanup_disconnect_completed"] = json!(matches!(disconnected, Ok(Ok(()))));
        }
    }
    let reap_deadline = if result.is_err() {
        deadline.min(Instant::now() + Duration::from_secs(3))
    } else {
        deadline
    };
    let reaped = reap_php(
        &mut child,
        reap_deadline,
        &captures,
        result.is_err(),
        report,
    );
    let worker_deadline = Instant::now() + Duration::from_secs(3);
    while !workers.iter().all(JoinHandle::is_finished) && Instant::now() < worker_deadline {
        thread::sleep(Duration::from_millis(10));
    }
    let workers_done = workers.iter().all(JoinHandle::is_finished);
    if workers_done {
        for worker in workers {
            let _ = worker.join();
        }
    }
    report["capture_workers_joined"] = json!(workers_done);
    let captured = record_capture(&captures, report);
    if let Ok(wire) = &captured {
        if let Ok(value) = serde_json::from_slice::<Value>(wire) {
            report["php_result"] = value;
        }
    }
    result?;
    reaped?;
    check(workers_done, "capture_worker_deadline")?;
    let wire = captured?;
    check(
        wire.last() == Some(&b'\n') && wire.iter().filter(|&&byte| byte == b'\n').count() == 1,
        "one_php_result_line",
    )?;
    check(
        report["php_exit_success"] == true
            && report["physical_disconnect_completed"] == true
            && report["php_result"]["schema"] == "recastpress.wpdb-bridge-php-observation.v0.5"
            && report["php_result"]["outcome"] == "fixed_shared_wpdb_cases_passed"
            && report["php_result"]["passed"] == true
            && report["php_result"]["close_completed"] == true,
        "php_or_rpc_contract_failed",
    )?;
    check(
        [
            "connected",
            "constructor_connection_deferred",
            "included_source_gate_passed",
        ]
        .iter()
        .all(|name| report["php_result"][name] == true)
            && report["php_result"]["bootstrap_num_queries"] == 0
            && report["php_result"]["final_num_queries"] == 25
            && report["php_result"]["observations"]
                .as_array()
                .is_some_and(|rows| rows.len() == 27),
        "fixed_php_corpus_completion",
    )?;
    Ok(())
}

fn main() {
    let mut report = json!({"schema":"recastpress.wpdb-bridge-observation.v0.5","outcome":"not_run","passed":false,
        "operations":[],"php_result":null,"request_bytes":0,"reply_bytes":0,"php_spawned":false,"owned_php_reaped":false,
        "physical_connection_created":false,"physical_disconnect_attempted":false,"physical_disconnect_completed":false,
        "cleanup_disconnect_attempted":false,"cleanup_disconnect_completed":false,"php_forced_kill":false,"php_kill_failure_observed":false,
        "capture_workers_joined":false,"whole_probe_deadline_seconds":60,
        "execution_deadline_scope":"private-listener/PHP/RPC execution only; stdin parsing and bounded cleanup grace excluded",
        "cleanup_grace_seconds":{"db_disconnect":3,"php_reap":3,"capture_workers":3},
        "total_wall_clock_deadline_verified":false,"query_deadline_seconds":10,"accept_deadline_seconds":10,
        "limits":{"request_frame_bytes":FRAME,"reply_frame_bytes":REPLY,"request_total_bytes":REQUEST_TOTAL,
            "reply_total_bytes":REPLY_TOTAL,"operations":OPS,"rows":16,"columns":16,"cell_bytes":CELL,"php_stream_bytes":OUTPUT},
        "credentials_forwarded_to_php":false,"transport_nonce_recorded":false,"physical_connection_pool_used":false,
        "private_native_loopback_transport":true,"wasm_networking_enabled":false,"native_php_product_fallback":false,
        "hello_charset_fields_scope":"requested fixed profile only; actual session observed by PHP setup queries",
        "native_wpdb_comparison_passed":false,"full_wordpress_bootstrap":false,"wasm_php_bridge_executed":false,
        "raw_non_utf8_err_verified":false,"standard_wpdb_compatibility_verified":false,"product_driver_selected":false,
        "product_abi_selected":false,"database_started_by_probe":false,"ddl_executed_by_probe":false});
    let mut input = None;
    let result = (|| -> Result<()> {
        check(std::env::args_os().len() == 1, "no_command_line_options")?;
        let mut wire = Vec::new();
        io::stdin()
            .take(20 * 1024 + 1)
            .read_to_end(&mut wire)
            .map_err(|_| "stdin_read_failed")?;
        input = Some(parse_input(&wire)?);
        run(input.as_ref().ok_or("input_missing")?, &mut report)
    })();
    match result {
        Ok(()) => {
            report["passed"] = json!(true);
            report["outcome"] = json!("fixed_bridge_cases_passed");
        }
        Err(reason) => {
            report["outcome"] = json!("fixed_bridge_cases_failed");
            report["failure"] = json!(reason);
        }
    }
    let wire = serde_json::to_string(&report).unwrap_or_default();
    let leaked = input.as_ref().is_some_and(|input| {
        [&input.password, &input.nonce]
            .iter()
            .any(|secret| wire.contains(secret.as_str()) || wire.contains(&hex(secret.as_bytes())))
    });
    if leaked {
        report = json!({"schema":"recastpress.wpdb-bridge-observation.v0.5","outcome":"fixed_bridge_cases_failed",
            "passed":false,"failure":"public_secret_guard","owned_php_reaped":report["owned_php_reaped"],
            "physical_disconnect_completed":report["physical_disconnect_completed"],"transport_nonce_recorded":false,
            "credentials_forwarded_to_php":false,"product_driver_selected":false,"product_abi_selected":false});
    }
    println!("{report}");
    if result.is_err() || leaked {
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input() -> Value {
        json!({"schema":"recastpress.wpdb-bridge-input.v0.5","host":"127.0.0.1","port":3307,
            "database":"rp_wpdb_probe","user":"rp_wpdb_probe","password":"1".repeat(48),
            "tls":"disabled","tls_ca_pem":null,"nonce":"2".repeat(48)})
    }

    fn request(seq: u64, op: &str, data: &str) -> Vec<u8> {
        let mut wire = serde_json::to_vec(&json!({"schema":"recastpress.wpdb-rpc-request.v0.5",
            "nonce":"2".repeat(48),"seq":seq,"op":op,"data_hex":data}))
        .unwrap();
        wire.push(b'\n');
        wire
    }

    #[test]
    fn script_argument_is_normal_and_binds_the_canonical_fixture() {
        let argument = normal_fixture_argument().unwrap();
        assert!(argument.is_absolute());
        #[cfg(windows)]
        assert!(!argument.as_os_str().to_string_lossy().starts_with(r"\\?\"));
        let canonical_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .unwrap();
        let canonical_fixture =
            canonical_root.join("experiments/wpdb-bridge-probe/fixtures/bridge.php");
        assert_eq!(argument.canonicalize().unwrap(), canonical_fixture);
    }

    #[test]
    fn input_requires_every_key_and_closed_destination() {
        let mut value = input();
        assert!(parse_input(&serde_json::to_vec(&value).unwrap()).is_ok());
        value.as_object_mut().unwrap().remove("tls_ca_pem");
        assert!(parse_input(&serde_json::to_vec(&value).unwrap()).is_err());
        let mut value = input();
        value["host"] = json!("localhost");
        assert!(parse_input(&serde_json::to_vec(&value).unwrap()).is_err());
        let mut value = input();
        value["tls"] = json!("required");
        assert!(parse_input(&serde_json::to_vec(&value).unwrap()).is_err());
    }

    #[test]
    fn unknown_and_duplicate_fields_are_rejected() {
        let mut value = input();
        value["extra"] = json!(true);
        assert!(parse_input(&serde_json::to_vec(&value).unwrap()).is_err());
        let mut wire = serde_json::to_vec(&input()).unwrap();
        wire.pop();
        wire.extend_from_slice(b",\"host\":\"127.0.0.1\"}");
        assert!(parse_input(&wire).is_err());
        let mut wire = request(1, "hello", "");
        wire.truncate(wire.len() - 2);
        wire.extend_from_slice(b",\"op\":\"hello\"}\n");
        assert!(parse_request(&wire, &"2".repeat(48), 1).is_err());
    }

    #[test]
    fn request_nonce_sequence_and_phase_are_checked_before_dispatch() {
        let wire = request(1, "hello", "");
        assert!(parse_request(&wire, &"2".repeat(48), 1).is_ok());
        assert!(parse_request(&wire, &"3".repeat(48), 1).is_err());
        assert!(parse_request(&wire, &"2".repeat(48), 2).is_err());
        assert!(
            parse_request(&request(1, "query", "53454c4543542031"), &"2".repeat(48), 1).is_err()
        );
        assert!(parse_request(&request(2, "unknown", ""), &"2".repeat(48), 2).is_err());
    }

    #[test]
    fn request_hex_ascii_and_frame_quotas_fail_closed() {
        let nonce = "2".repeat(48);
        assert!(parse_request(&request(2, "query", "53454c4543542031"), &nonce, 2).is_ok());
        for data in ["a".to_owned(), "ff".repeat(CELL + 1), "FF".to_owned()] {
            assert!(parse_request(&request(2, "query", &data), &nonce, 2).is_err());
        }
        assert!(parse_request(&request(2, "escape", "ff"), &nonce, 2).is_err());
        assert!(parse_request(&vec![b'x'; FRAME + 1], &nonce, 2).is_err());
    }
}
