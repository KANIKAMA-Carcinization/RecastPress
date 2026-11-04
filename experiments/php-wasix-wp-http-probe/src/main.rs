//! Fixed loopback HTTP experiment. Every valid request executes a fresh worker.
#[path = "../../php-wasix-wp-request-context-probe/src/response_contract.rs"]
mod response_contract;
#[path = "../../php-wasi-host-probe/src/cgi_response.rs"]
mod cgi_response;
use anyhow::{Result, ensure};
use axum::{Router, body::{Body, to_bytes}, extract::{Request, State}, http::{HeaderMap, HeaderName, HeaderValue, Method, StatusCode, Version}, response::Response};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{collections::BTreeMap, io::{Read, Write}, path::{Component, Path, PathBuf}, process::Stdio, sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}}, time::Duration};
use tokio::{io::{AsyncRead, AsyncReadExt, AsyncWriteExt}, process::Command, sync::{Semaphore, Notify}};
const WORKER_RECEIPT: &str = "experiments/php-wasix-wp-request-context-probe/evidence/linux-build.json";
const WORKER: &str = "target/php-wasix-wp-bootstrap-probe/cargo-target/release/recastpress-php-wasix-wp-request-context-probe";
const WORKER_ANCHOR: &[u8] = include_bytes!("../../php-wasix-wp-request-context-probe/evidence/windows-build.json");
pub(crate) mod db {
    use sha2::{Digest, Sha256};
    pub fn sha(bytes: &[u8]) -> String { format!("{:x}",Sha256::digest(bytes)) }
    pub fn hex(bytes: &[u8]) -> String { bytes.iter().map(|b|format!("{b:02x}")).collect() }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Case { Home, Missing }
impl Case {
    fn as_str(self) -> &'static str { if self==Self::Home {"home"} else {"missing"} }
    fn status(self) -> u16 { if self==Self::Home {200} else {404} }
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Salts {
    #[serde(rename="AUTH_KEY")] auth_key:String,
    #[serde(rename="SECURE_AUTH_KEY")] secure_auth_key:String,
    #[serde(rename="LOGGED_IN_KEY")] logged_in_key:String,
    #[serde(rename="NONCE_KEY")] nonce_key:String,
    #[serde(rename="AUTH_SALT")] auth_salt:String,
    #[serde(rename="SECURE_AUTH_SALT")] secure_auth_salt:String,
    #[serde(rename="LOGGED_IN_SALT")] logged_in_salt:String,
    #[serde(rename="NONCE_SALT")] nonce_salt:String,
}
impl Salts { fn values(&self)->[&str;8] { [&self.auth_key,&self.secure_auth_key,&self.logged_in_key,&self.nonce_key,&self.auth_salt,&self.secure_auth_salt,&self.logged_in_salt,&self.nonce_salt] } }
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Database { schema:String, mode:String, host:String, port:u16, database:String, user:String, password:String, nonce:String, salts:Salts }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input { schema:String, database:Database }
fn secret(s:&str)->bool { s.len()==48 && s.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b)) }
fn parse_input(wire:&[u8])->Result<Database> {
    ensure!(wire.len()<=24*1024,"input_quota");
    let input:Input=serde_json::from_slice(wire).map_err(|_|anyhow::anyhow!("invalid_input"))?;
    let d=input.database;
    ensure!(input.schema=="recastpress.php-wasix-wp-http-input.v0.5"
        && d.schema=="recastpress.php-wasix-wp-request-input.v0.5" && d.mode=="home"
        && d.host=="127.0.0.1" && d.port>0 && d.database=="rp_wpdb_probe" && d.user=="rp_wpdb_probe"
        && secret(&d.password) && secret(&d.nonce) && d.salts.values().iter().all(|s|secret(s)),"input_profile");
    ensure!(serde_json::to_vec(&d)?.len()<=20*1024,"database_input_quota"); Ok(d)
}
fn fixed(root:&Path,name:&str,limit:u64)->Result<Vec<u8>> {
    ensure!(!Path::new(name).is_absolute() && Path::new(name).components().all(|c|matches!(c,Component::Normal(_))),"fixed_path");
    let p=root.join(name); ensure!(p.canonicalize()?==p,"fixed_alias");
    let file=std::fs::File::open(p)?; let m=file.metadata()?;
    ensure!(m.is_file() && m.len()<=limit,"fixed_quota"); let mut bytes=vec![];
    file.take(limit+1).read_to_end(&mut bytes)?; ensure!(bytes.len() as u64==m.len(),"fixed_changed"); Ok(bytes)
}
fn worker_identity(root:&Path)->Result<Value> {
    ensure!(cfg!(target_os="linux"),"worker_linux_only");
    let wire=fixed(root,WORKER_RECEIPT,2*1024*1024)?;
    let receipt:Value=serde_json::from_slice(&wire)?;
    let anchor:Value=serde_json::from_slice(WORKER_ANCHOR)?;
    ensure!(receipt["schema"]=="recastpress.php-wasix-wp-request-context-build.v0.5" && receipt["passed"]==true
        && receipt["outcome"]=="locked_offline_context_host_build_passed" && receipt["locked_offline"]==true
        && receipt["owned_cleanup_completed"]==true && receipt["source_reverified_after"]==true
        && receipt["input_files"]==anchor["input_files"] && receipt["input_files"].as_object().is_some_and(|m|m.len()==195)
        && receipt["units"]["passed"]==true && receipt["units"]["tests"]==anchor["units"]["tests"]
        && receipt["units"]["tests"].as_array().is_some_and(|a|a.len()==58)
        && receipt["executable"]==WORKER && receipt["build_profile"]=="release","worker_receipt_profile");
    for key in ["derivation","driver","source_fs_registry","old_build"] {
        ensure!(receipt[format!("{key}_before")]==receipt[format!("{key}_after")],"worker_receipt_changed");
    }
    for key in ["php_executed","wasm_executed","database_connected","database_started","product_runtime_selected"] {
        ensure!(receipt[key]==false,"worker_build_scope");
    }
    for (name,digest) in receipt["input_files"].as_object().ok_or_else(||anyhow::anyhow!("worker_source_map"))? {
        ensure!(*digest==db::sha(&fixed(root,name,4*1024*1024)?),"worker_source_changed");
    }
    let binary=fixed(root,WORKER,256*1024*1024)?;
    ensure!(receipt["executable_identity"]==json!({"bytes":binary.len(),"sha256":db::sha(&binary)}),"worker_binary_changed");
    Ok(json!({"receipt_path":WORKER_RECEIPT,"receipt_sha256":db::sha(&wire),"executable":WORKER,
        "executable_identity":receipt["executable_identity"],"source_inputs":195,"units":58}))
}
#[derive(Clone, Copy)]
enum Rejection { Method, Target, Host, Headers, Body, Busy, Budget }
impl Rejection { fn name(self)->&'static str { match self {Self::Method=>"method",Self::Target=>"target",Self::Host=>"host",Self::Headers=>"headers",Self::Body=>"body",Self::Busy=>"busy",Self::Budget=>"budget"} } }
fn request_case(request:&Request)->std::result::Result<Case,Rejection> {
    if request.method()!=Method::GET {return Err(Rejection::Method)}
    if request.version()!=Version::HTTP_11 {return Err(Rejection::Headers)}
    let case=match request.uri().path_and_query().map(|s|s.as_str()) {Some("/")=>Case::Home,Some("/?p=2147483647")=>Case::Missing,_=>return Err(Rejection::Target)};
    if request.uri().scheme().is_some() || request.uri().authority().is_some() {return Err(Rejection::Target)}
    let headers=request.headers();
    let host:Vec<_>=headers.get_all("host").iter().collect();
    if host.len()!=1 || !host[0].as_bytes().eq_ignore_ascii_case(b"rp-fixture.invalid") {return Err(Rejection::Host)}
    for name in headers.keys() {
        match name.as_str() {
            "host"=>{},
            "connection"=>{let v:Vec<_>=headers.get_all(name).iter().collect();if v.len()!=1 || !v[0].as_bytes().eq_ignore_ascii_case(b"close") {return Err(Rejection::Headers)}},
            "content-length"=>{let v:Vec<_>=headers.get_all(name).iter().collect();if v.len()!=1 || v[0].as_bytes()!=b"0" {return Err(Rejection::Body)}},
            _=>return Err(Rejection::Headers),
        }
    }
    Ok(case)
}
fn negative(status:u16,body:&'static str)->Response {
    Response::builder().status(status).header("connection","close").header("content-length",body.len()).body(Body::from(body)).expect("fixed response")
}
fn response_from_inner(value:&Value)->Result<Response> {
    let status=value["status"].as_u64().ok_or_else(||anyhow::anyhow!("inner_status"))?;
    ensure!((100..=599).contains(&status),"inner_status");
    let body=unhex(value["body_hex"].as_str().ok_or_else(||anyhow::anyhow!("inner_body"))?,1024*1024)?;
    let mut cgi=format!("Status: {status}\r\n").into_bytes();
    for h in value["headers_hex"].as_array().ok_or_else(||anyhow::anyhow!("inner_headers"))? {
        cgi.extend(unhex(h.as_str().ok_or_else(||anyhow::anyhow!("inner_header"))?,8192)?);cgi.extend(b"\r\n");
    }
    cgi.extend(b"\r\n");cgi.extend(&body);let parsed=cgi_response::parse(&cgi)?;
    ensure!(parsed.status as u64==status && parsed.body==body,"inner_frame");
    let mut headers=HeaderMap::new();
    for (name,value) in parsed.headers {
        headers.append(HeaderName::from_bytes(name.as_bytes())?,HeaderValue::from_bytes(&value)?);
    }
    headers.insert("content-length",HeaderValue::from_str(&body.len().to_string())?);
    headers.insert("connection",HeaderValue::from_static("close"));
    let mut response=Response::new(Body::from(body));*response.status_mut()=StatusCode::from_u16(parsed.status)?;*response.headers_mut()=headers;Ok(response)
}
fn unhex(s:&str,max:usize)->Result<Vec<u8>> {
    ensure!(s.len()<=max*2 && s.len()%2==0 && s.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b)),"hex_profile");
    (0..s.len()).step_by(2).map(|i|u8::from_str_radix(&s[i..i+2],16).map_err(|_|anyhow::anyhow!("hex_profile"))).collect()
}
async fn bounded<R:AsyncRead+Unpin>(mut read:R,limit:usize)->Result<Vec<u8>> {
    let mut bytes=Vec::new();let mut chunk=[0u8;4096];
    loop {let n=read.read(&mut chunk).await?;if n==0 {return Ok(bytes)}ensure!(bytes.len()+n<=limit,"capture_overflow");bytes.extend_from_slice(&chunk[..n]);}
}
fn worker_contract(value:&Value,case:Case,native:&response_contract::NativeResponse)->bool {
    value["schema"]=="recastpress.php-wasix-wp-request-context-observation.v0.5" && value["passed"]==true
        && value["outcome"]=="fixed_wasix_wp_request_context_passed" && value["phase"]=="full_context_verified"
        && value["case"]==case.as_str() && value["mode"]=="home" && value["module_compilations"]==1
        && ["physical_connection_created","physical_disconnect_completed","db_actor_joined","db_actor_exited","fresh_runtime","source_rechecked_after_execution","full_wordpress_bootstrap"].iter().all(|k|value[*k]==true)
        && value["db_actor_failure"].is_null()
        && ["guest_networking","host_environment_forwarded","host_home_mapped","db_credentials_supplied_to_guest","native_php_fallback","product_runtime"].iter().all(|k|value[*k]==false)
        && ["guest_exit_success","runtime_shutdown_returned","stdout_eof","stderr_eof"].iter().all(|k|value["cgi_capture"][*k]==true)
        && value["cgi_capture"]["stderr_bytes"]==0 && value["cgi_capture"]["stderr_sha256"]==db::sha(b"")
        && value["cgi_capture"]["stdout_overflow"]==false && value["cgi_capture"]["stderr_overflow"]==false
        && value["cgi_capture"]["response"]["status"]==200
        && value["rpc"]["finished"]==true && value["rpc"]["failure"].is_null() && value["rpc"]["channel_open"]==false
        && value["rpc"]["pending_bytes"]==0 && value["rpc"]["unread_response_bytes"]==0 && value["rpc"]["response_pending"]==false
        && value["managed_access"]["finished"]==true && value["managed_access"]["protocol_error"].is_null()
        && value["managed_access"]["invalid"]==0 && value["managed_access"]["io_errors"]==0
        && value["managed_access"]["active_handles"]==0 && value["managed_access"]["pending_bytes"]==0 && value["managed_access"]["unread_response_bytes"]==0
        && value["php_result"]["passed"]==true && value["php_result"]["phase"]=="complete"
        && value["php_result"]["schema"]=="recastpress.php-wasix-wp-request-context-php-observation.v0.5"
        && value["php_result"]["outcome"]=="fixed_wasix_context_passed"
        && native.accepts(&value["php_result"]["response"]) && native.contexts_accept(&value["php_result"])
}
async fn capture_owned(command:&mut Command,payload:&[u8],deadline:Duration,out_limit:usize,err_limit:usize,record:&mut Value)->Result<Vec<u8>> {
    let mut child=command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true).spawn().map_err(|_|anyhow::anyhow!("worker_spawn"))?;
    record["owned_pid"]=json!(child.id());record["reaped"]=json!(false);
    let execution=tokio::time::timeout(deadline,async {
        let mut stdin=child.stdin.take().ok_or_else(||anyhow::anyhow!("worker_stdin"))?;
        let stdout=child.stdout.take().ok_or_else(||anyhow::anyhow!("worker_stdout"))?;
        let stderr=child.stderr.take().ok_or_else(||anyhow::anyhow!("worker_stderr"))?;
        let (_,out,err,status)=tokio::try_join!(async {stdin.write_all(&payload).await?;stdin.shutdown().await?;drop(stdin);Ok::<_,anyhow::Error>(())},
            bounded(stdout,out_limit),bounded(stderr,err_limit),async {Ok::<_,anyhow::Error>(child.wait().await?)} )?;
        Ok::<_,anyhow::Error>((out,err,status))
    }).await;
    let execution=match execution {Ok(Ok(value))=>Ok(value),_=>{
        record["cleanup_kill_succeeded"]=json!(child.start_kill().is_ok());
        let reaped=matches!(tokio::time::timeout(Duration::from_secs(5),child.wait()).await,Ok(Ok(_)));
        record["reaped"]=json!(reaped);record["cleanup_failed"]=json!(!reaped);
        Err(anyhow::anyhow!("worker_capture_failed"))}};
    let (out,err,status)=execution?;record["reaped"]=json!(true);record["exit_code"]=json!(status.code());
    record["stdout_eof"]=json!(true);record["stderr_eof"]=json!(true);
    record["stdout"]=json!({"bytes":out.len(),"sha256":db::sha(&out)});record["stderr"]=json!({"bytes":err.len(),"sha256":db::sha(&err)});
    ensure!(status.success() && err.is_empty(),"worker_completion");Ok(out)
}
async fn run_worker(root:&Path,database:Database,case:Case,record:&mut Value)->Result<(Value,Response)> {
    let before=worker_identity(root)?;record["identity_before"]=before.clone();
    let payload=serde_json::to_vec(&json!({"schema":"recastpress.php-wasix-wp-request-context-input.v0.5","case":case.as_str(),"database":database}))?;
    let execution=capture_owned(Command::new(root.join(WORKER)).env_clear(),&payload,Duration::from_secs(300),4*1024*1024,64*1024,record).await;
    let after=worker_identity(root);record["identity_after_verified"]=json!(after.as_ref().is_ok_and(|a|*a==before));
    let out=execution?;ensure!(after?==before,"worker_identity_changed");
    ensure!(out.len()>0 && out.last()==Some(&b'\n') && out.iter().filter(|&&b|b==b'\n').count()==1,"worker_json_line");
    let value:Value=serde_json::from_slice(&out).map_err(|_|anyhow::anyhow!("worker_json"))?;
    let native=response_contract::NativeResponse::load(root,case,&mut json!({}))?;
    ensure!(worker_contract(&value,case,&native),"worker_contract");
    let response=response_from_inner(&value["php_result"]["response"])?;Ok((value,response))
}
struct Observations { executions:usize, rejected:BTreeMap<&'static str,usize>, requests:Vec<Value>, secrets:Vec<String> }
#[derive(Clone)]
struct App { root:PathBuf, database:Database, observations:Arc<Mutex<Observations>>, semaphore:Arc<Semaphore>, stop:Arc<Notify> }
fn rejected(app:&App,reason:Rejection)->Response {
    if let Ok(mut state)=app.observations.lock() {*state.rejected.entry(reason.name()).or_default()+=1;}
    match reason {Rejection::Busy|Rejection::Budget=>negative(503,"fixed request unavailable\n"),_=>negative(400,"fixed request rejected\n")}
}
async fn handle(State(app):State<App>,request:Request)->Response {
    let case=match request_case(&request) {Ok(c)=>c,Err(reason)=>return rejected(&app,reason)};
    if to_bytes(request.into_body(),0).await.is_err() {return rejected(&app,Rejection::Body)}
    let permit=match app.semaphore.clone().try_acquire_owned() {Ok(p)=>p,Err(_)=>return rejected(&app,Rejection::Busy)};
    let mut nonce=[0u8;24];if getrandom::getrandom(&mut nonce).is_err() {return negative(502,"fixed worker failed\n")}
    let mut database=app.database.clone();database.nonce=db::hex(&nonce);
    {
        let Ok(mut state)=app.observations.lock() else{return negative(502,"fixed worker failed\n")};
        if state.executions>=3 {drop(state);return rejected(&app,Rejection::Budget)}
        if state.secrets.contains(&database.nonce) {return negative(502,"fixed worker failed\n")}
        state.secrets.push(database.nonce.clone());state.executions+=1;
    }
    // Dropping a disconnected client's handler must not drop an owned child.
    // The detached supervisor retains the permit until capture/kill/reap and
    // observation commit finish; server shutdown waits for that same permit.
    match tokio::spawn(supervised(app,database,case,permit)).await {
        Ok(response)=>response,Err(_)=>negative(502,"fixed worker failed\n")
    }
}
async fn supervised(app:App,database:Database,case:Case,_permit:tokio::sync::OwnedSemaphorePermit)->Response {
    let mut worker=json!({"reaped":false,"stdout_eof":false,"stderr_eof":false});
    let result=run_worker(&app.root,database,case,&mut worker).await;
    let (row,response)=match result {Ok((observation,response))=>(json!({"case":case.as_str(),"worker":worker,"observation":observation,"response_constructed":true}),response),
        Err(_)=>(json!({"case":case.as_str(),"worker":worker,"failure":"fixed_worker_failed","response_constructed":false}),negative(502,"fixed worker failed\n"))};
    if let Ok(mut state)=app.observations.lock() {state.requests.push(row);if state.executions==3 {app.stop.notify_one();}}
    response
}
async fn serve(database:Database)->Result<Value> {
    ensure!(cfg!(target_os="linux"),"server_linux_only");
    let root=Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize()?;
    let worker=worker_identity(&root)?;
    let mut secrets=vec![database.password.clone(),database.nonce.clone()];secrets.extend(database.salts.values().iter().map(|s|s.to_string()));
    let observations=Arc::new(Mutex::new(Observations {executions:0,rejected:["method","target","host","headers","body","busy","budget"].into_iter().map(|k|(k,0)).collect(),requests:vec![],secrets}));
    let stop=Arc::new(Notify::new());let shutdown=stop.clone();
    let stdin_stop=stop.clone();let stdin_shutdown=Arc::new(AtomicBool::new(false));let stdin_protocol=Arc::new(AtomicBool::new(false));
    let observed=stdin_shutdown.clone();let invalid=stdin_protocol.clone();
    // This dedicated OS thread is not a Tokio blocking task. Normal three-run
    // shutdown does not wait on an open parent control pipe.
    std::thread::spawn(move || {
        let mut byte=[0u8;1];let result=std::io::stdin().read(&mut byte);
        if !matches!(result,Ok(0)) {invalid.store(true,Ordering::SeqCst);}
        observed.store(true,Ordering::SeqCst);stdin_stop.notify_one();
    });
    let app=App {root,database,observations:observations.clone(),semaphore:Arc::new(Semaphore::new(1)),stop};
    let listener=tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST,0)).await?;
    let port=listener.local_addr()?.port();
    println!("{}",json!({"schema":"recastpress.php-wasix-wp-http-ready.v0.5","port":port}));std::io::stdout().flush()?;
    let completed=app.semaphore.clone();
    axum::serve(listener,Router::new().fallback(handle).with_state(app)).with_graceful_shutdown(async move {shutdown.notified().await}).await?;
    let _completed=completed.acquire_owned().await.map_err(|_|anyhow::anyhow!("supervisor_shutdown"))?;
    let state=observations.lock().map_err(|_|anyhow::anyhow!("observation_lock"))?;
    let passed=!stdin_protocol.load(Ordering::SeqCst) && state.executions==3 && state.requests.len()==3 && state.requests.iter().all(|r|r["response_constructed"]==true && r["worker"]["reaped"]==true);
    let report=json!({"schema":"recastpress.php-wasix-wp-http-observation.v0.5","passed":passed,
        "outcome":if passed {"fixed_http_context_passed"}else{"fixed_http_context_failed"},"requests":state.requests,
        "worker_executions":state.executions,"worker_build":worker,"rejected_counts":state.rejected,
        "concurrency":1,"valid_execution_limit":3,"bound_loopback_only":true,"graceful_shutdown_completed":true,
        "stdin_shutdown_observed":stdin_shutdown.load(Ordering::SeqCst),"stdin_protocol_error":stdin_protocol.load(Ordering::SeqCst),
        "startup_private_input_used":true,"private_config_on_http":false,"transport_delivery_verified":false,
        "native_php_fallback":false,"product_runtime":false});
    let encoded=serde_json::to_string(&report)?;
    ensure!(encoded.len()<=12*1024*1024,"public_report_quota");
    ensure!(state.secrets.iter().all(|s|!encoded.contains(s)&&!encoded.contains(&db::hex(s.as_bytes()))),"public_secret_guard");Ok(report)
}
#[tokio::main(flavor="multi_thread",worker_threads=2)]
async fn main() {
    let result=async {ensure!(std::env::args_os().count()==1,"closed_arguments");serve(parse_input(&private_line(&mut std::io::stdin())?)?).await}.await;
    match result {Ok(report)=>{println!("{report}");if report["passed"]!=true {std::process::exit(1)}},Err(_)=>{println!("{}",json!({"schema":"recastpress.php-wasix-wp-http-observation.v0.5","passed":false,"failure":"fixed_http_probe_failed"}));std::process::exit(1)}}
}
fn private_line(read:&mut impl Read)->Result<Vec<u8>> {
    let mut wire=Vec::new();let mut byte=[0u8;1];
    loop {ensure!(wire.len()<24*1024,"input_line_quota");ensure!(read.read(&mut byte)?==1,"input_line_eof");
        if byte[0]==b'\n' {return Ok(wire)}wire.push(byte[0]);}
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input()->Value {
        let salt="a".repeat(48);
        json!({"schema":"recastpress.php-wasix-wp-http-input.v0.5","database":{
            "schema":"recastpress.php-wasix-wp-request-input.v0.5","mode":"home","host":"127.0.0.1","port":3306,
            "database":"rp_wpdb_probe","user":"rp_wpdb_probe","password":salt,"nonce":"b".repeat(48),"salts":{
                "AUTH_KEY":salt,"SECURE_AUTH_KEY":salt,"LOGGED_IN_KEY":salt,"NONCE_KEY":salt,
                "AUTH_SALT":salt,"SECURE_AUTH_SALT":salt,"LOGGED_IN_SALT":salt,"NONCE_SALT":salt}}})
    }
    fn root()->PathBuf {Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().unwrap()}
    #[test]
    fn private_line_preserves_control_byte_and_rejects_missing_or_large_line() {
        let mut read=std::io::Cursor::new(b"{}\nx");assert_eq!(private_line(&mut read).unwrap(),b"{}");
        let mut tail=vec![];Read::read_to_end(&mut read,&mut tail).unwrap();assert_eq!(tail,b"x");
        assert!(private_line(&mut std::io::Cursor::new(b"{}")).is_err());
        assert!(private_line(&mut std::io::Cursor::new(vec![b' ';24*1024])).is_err());
    }
    #[test]
    fn strict_private_input_rejects_unknown_duplicate_and_destination_changes() {
        let value=input();assert!(parse_input(&serde_json::to_vec(&value).unwrap()).is_ok());
        for key in ["host","schema","mode","nonce"] {let mut v=value.clone();v["database"][key]=json!("bad");assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());}
        let mut v=value.clone();v["database"]["salts"]["extra"]=json!("a".repeat(48));assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
        let wire=serde_json::to_string(&value).unwrap().replacen("\"port\":3306","\"port\":3306,\"port\":3307",1);
        assert!(parse_input(wire.as_bytes()).is_err());let mut v=value;v["worker"]=json!("other");assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
    }
    fn req(method:&str,target:&str)->Request {Request::builder().method(method).uri(target).header("host","RP-FIXTURE.INVALID").header("connection","close").body(Body::empty()).unwrap()}
    #[test]
    fn finite_http_profile_rejects_before_any_worker_is_selected() {
        assert!(matches!(request_case(&req("GET","/")),Ok(Case::Home)));
        assert!(matches!(request_case(&req("GET","/?p=2147483647")),Ok(Case::Missing)));
        for r in [req("HEAD","/"),req("POST","/"),req("GET","/?p=1"),req("GET","/missing"),req("GET","http://rp-fixture.invalid/")] {assert!(request_case(&r).is_err());}
        for (key,value) in [("cookie","x=1"),("authorization","Bearer fixture"),("x-extra","fixture"),("content-length","1"),("connection","keep-alive"),("host","other.invalid")] {
            let mut r=req("GET","/");r.headers_mut().insert(HeaderName::from_bytes(key.as_bytes()).unwrap(),HeaderValue::from_str(value).unwrap());assert!(request_case(&r).is_err());
        }
        let mut r=req("GET","/");r.headers_mut().append("host",HeaderValue::from_static("rp-fixture.invalid"));assert!(request_case(&r).is_err());
        let mut r=req("GET","/");*r.version_mut()=Version::HTTP_10;assert!(request_case(&r).is_err());
    }
    #[tokio::test]
    async fn complete_inner_response_keeps_binary_body_and_per_name_cookie_order() {
        let body=b"a\0b\xff";
        let value=json!({"status":404,"headers_hex":[db::hex(b"Content-Type: application/octet-stream"),db::hex(b"Set-Cookie: one=1"),db::hex(b"Set-Cookie: two=2")],"body_hex":db::hex(body)});
        let response=response_from_inner(&value).unwrap();assert_eq!(response.status(),404);
        assert_eq!(response.headers().get_all("set-cookie").iter().map(|v|v.as_bytes()).collect::<Vec<_>>(),vec![b"one=1".as_slice(),b"two=2".as_slice()]);
        assert_eq!(response.headers()["content-length"],"4");assert_eq!(response.headers()["connection"],"close");
        assert!(!response.headers().contains_key("transfer-encoding"));assert_eq!(to_bytes(response.into_body(),100).await.unwrap().as_ref(),body);
    }
    #[test]
    fn fixed_worker_contract_rejects_incomplete_cleanup_and_unknown_context() {
        let native:Value=serde_json::from_slice(&fixed(&root(),"experiments/wp-request-context-probe/evidence/windows-mysql-native.json",1024*1024).unwrap()).unwrap();
        let oracle=response_contract::NativeResponse::load(&root(),Case::Home,&mut json!({})).unwrap();
        let mut v=json!({"schema":"recastpress.php-wasix-wp-request-context-observation.v0.5","passed":true,"outcome":"fixed_wasix_wp_request_context_passed","phase":"full_context_verified","case":"home","mode":"home","module_compilations":1,
            "db_actor_failure":null,"php_result":native["observations"]["home"],"cgi_capture":{"stderr_bytes":0,"stderr_sha256":db::sha(b""),"stdout_overflow":false,"stderr_overflow":false,"response":{"status":200}},
            "rpc":{"finished":true,"failure":null,"channel_open":false,"pending_bytes":0,"unread_response_bytes":0,"response_pending":false},
            "managed_access":{"finished":true,"protocol_error":null,"invalid":0,"io_errors":0,"active_handles":0,"pending_bytes":0,"unread_response_bytes":0}});
        v["php_result"]["schema"]=json!("recastpress.php-wasix-wp-request-context-php-observation.v0.5");v["php_result"]["outcome"]=json!("fixed_wasix_context_passed");
        for k in ["physical_connection_created","physical_disconnect_completed","db_actor_joined","db_actor_exited","fresh_runtime","source_rechecked_after_execution","full_wordpress_bootstrap"] {v[k]=json!(true);}
        for k in ["guest_networking","host_environment_forwarded","host_home_mapped","db_credentials_supplied_to_guest","native_php_fallback","product_runtime"] {v[k]=json!(false);}
        for k in ["guest_exit_success","runtime_shutdown_returned","stdout_eof","stderr_eof"] {v["cgi_capture"][k]=json!(true);}
        assert!(worker_contract(&v,Case::Home,&oracle));
        for k in ["db_actor_joined","source_rechecked_after_execution","physical_disconnect_completed"] {let mut bad=v.clone();bad[k]=json!(false);assert!(!worker_contract(&bad,Case::Home,&oracle));}
        let mut bad=v.clone();bad["php_result"]["context_after_dispatch"]["environment_query"]=json!("private");assert!(!worker_contract(&bad,Case::Home,&oracle));
        let mut bad=v;bad["php_result"]["response"]["headers_hex"]=json!([]);assert!(!worker_contract(&bad,Case::Home,&oracle));
    }
    #[test]
    fn mock_capture_child() {
        match std::env::var("RP_HTTP_CAPTURE_CHILD").as_deref() {
            Ok("normal")=>{std::io::stdout().write_all(b"fixed mock output").unwrap();std::process::exit(0)},
            Ok("overflow")=>{let _=std::io::stdout().write_all(&vec![b'x';16384]);std::thread::sleep(Duration::from_secs(30));},
            Ok("timeout")=>std::thread::sleep(Duration::from_secs(30)),
            Ok("panic")=>panic!("fixed mock child failure"),
            _=>{},
        }
    }
    #[tokio::test]
    async fn owned_capture_reaps_normal_overflow_timeout_and_child_panic() {
        for mode in ["normal","overflow","timeout","panic"] {
            let mut command=Command::new(std::env::current_exe().unwrap());
            command.env_clear().env("RP_HTTP_CAPTURE_CHILD",mode).args(["--exact","tests::mock_capture_child","--nocapture"]);
            let mut record=json!({});let deadline=if mode=="timeout" {Duration::from_millis(150)} else {Duration::from_secs(5)};
            let result=capture_owned(&mut command,b"",deadline,1024,4096,&mut record).await;
            assert_eq!(record["reaped"],true);assert!(record["owned_pid"].as_u64().is_some_and(|n|n>0));
            if mode=="normal" {assert!(result.unwrap().windows(17).any(|w|w==b"fixed mock output"));assert_eq!(record["stdout_eof"],true);assert_eq!(record["stderr_eof"],true);} else {assert!(result.is_err());}
        }
        let semaphore=Arc::new(Semaphore::new(1));let permit=semaphore.clone().acquire_owned().await.unwrap();
        let committed=Arc::new(Mutex::new(json!({})));let output=committed.clone();
        let waiter=tokio::spawn(async move {
            let _permit=permit;
            let mut command=Command::new(std::env::current_exe().unwrap());
            command.env_clear().env("RP_HTTP_CAPTURE_CHILD","normal").args(["--exact","tests::mock_capture_child","--nocapture"]);
            let mut record=json!({});capture_owned(&mut command,b"",Duration::from_secs(5),1024,4096,&mut record).await.unwrap();
            *output.lock().unwrap()=record;
        });
        drop(waiter); // Model a disconnected handler dropping its await handle.
        let _finished=tokio::time::timeout(Duration::from_secs(6),semaphore.acquire_owned()).await.unwrap().unwrap();
        assert_eq!(committed.lock().unwrap()["reaped"],true);
    }
}
