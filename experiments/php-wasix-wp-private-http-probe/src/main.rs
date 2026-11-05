//! Fixed loopback HTTP experiment. Every valid request executes a fresh worker.
#[path = "../../php-wasix-wp-private-worker-probe/src/strict_json.rs"]
mod strict_json;
use recastpress_wp_private_response_probe as private_response;
use anyhow::{Result, ensure};
use axum::{Router, body::{Body, to_bytes}, extract::{Request, State}, http::{HeaderMap, HeaderName, HeaderValue, Method, StatusCode, Version}, response::Response};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{collections::{BTreeMap, BTreeSet}, io::{Read, Write}, path::{Component, Path, PathBuf}, process::Stdio, sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}}, time::Duration};
use tokio::{io::{AsyncRead, AsyncReadExt, AsyncWriteExt}, process::Command, sync::{Semaphore, Notify}};
const WORKER_RECEIPT: &str = "experiments/php-wasix-wp-private-worker-probe/evidence/linux-build.json";
const WORKER: &str = "target/php-wasix-wp-bootstrap-probe/cargo-target/release/recastpress-php-wasix-wp-private-worker-probe";
const WORKER_ANCHOR: &[u8] = include_bytes!("../../php-wasix-wp-private-worker-probe/evidence/windows-build.json");
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
    ensure!(input.schema=="recastpress.php-wasix-wp-private-http-input.v0.5"
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
    ensure!(receipt["schema"]=="recastpress.php-wasix-wp-private-worker-build.v0.5" && receipt["passed"]==true
        && receipt["outcome"]=="locked_offline_private_worker_build_passed" && receipt["locked_offline"]==true
        && receipt["owned_cleanup_completed"]==true && receipt["source_reverified_after"]==true
        && receipt["input_files"]==anchor["input_files"] && receipt["input_files"].as_object().is_some_and(|m|m.len()==219)
        && receipt["units"]["passed"]==true && receipt["units"]["tests"]==anchor["units"]["tests"]
        && receipt["units"]["tests"].as_array().is_some_and(|a|a.len()==59)
        && receipt["executable"]==WORKER && receipt["build_profile"]=="release","worker_receipt_profile");
    for key in ["derivation","driver","source_fs_registry","old_build"] {
        ensure!(receipt[format!("{key}_before")]==receipt[format!("{key}_after")],"worker_receipt_changed");
    }
    for key in ["php_executed","wasm_executed","database_connected","database_started","product_runtime_selected"] {
        ensure!(receipt[key]==false,"worker_build_scope");
    }
    // The source map contains historical response evidence. Compare only
    // the compiled anchor map here; build/controller own all source rehashing.
    let binary=fixed(root,WORKER,256*1024*1024)?;
    ensure!(receipt["executable_identity"]==json!({"bytes":binary.len(),"sha256":db::sha(&binary)}),"worker_binary_changed");
    Ok(json!({"receipt_path":WORKER_RECEIPT,"receipt_sha256":db::sha(&wire),"executable":WORKER,
        "executable_identity":receipt["executable_identity"],"source_inputs":219,"units":59,"source_files_rehashed_by_service":false}))
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
// A private token exists only after the complete anonymous request profile
// has been checked. It never changes the private worker's publication flag.
struct Admission { case: Case }
async fn admit(request:Request)->std::result::Result<Admission,Rejection> {
    let case=request_case(&request)?;
    if to_bytes(request.into_body(),0).await.is_err() {return Err(Rejection::Body)}
    Ok(Admission {case})
}
fn response_from_decoded(value:&private_response::PrivateWpResponse)->Result<Response> {
    let mut headers=HeaderMap::new();
    for field in value.headers() {
        headers.append(HeaderName::from_bytes(field.name().as_bytes())?,HeaderValue::from_bytes(field.value())?);
    }
    headers.insert("content-length",HeaderValue::from_str(&value.body().len().to_string())?);
    headers.insert("connection",HeaderValue::from_static("close"));
    let mut response=Response::new(Body::from(value.body().to_vec()));
    *response.status_mut()=StatusCode::from_u16(value.status())?;*response.headers_mut()=headers;Ok(response)
}
async fn bounded<R:AsyncRead+Unpin>(mut read:R,limit:usize)->Result<Vec<u8>> {
    let mut bytes=Vec::new();let mut chunk=[0u8;4096];
    loop {let n=read.read(&mut chunk).await?;if n==0 {return Ok(bytes)}ensure!(bytes.len()+n<=limit,"capture_overflow");bytes.extend_from_slice(&chunk[..n]);}
}
fn private_contexts_valid(value: &Value, case: Case) -> bool {
    let missing = case == Case::Missing;
    let query = if missing { "p=2147483647" } else { "" };
    let uri = if missing { "/?p=2147483647" } else { "/" };
    let get = if missing { json!({"p":"2147483647"}) } else { json!([]) };
    ["before_bootstrap", "after_bootstrap", "after_dispatch"].iter().all(|stage| {
        let available = *stage != "before_bootstrap";
        value[format!("context_{stage}")] == json!({"stage":stage,"method":"GET","uri":uri,"query":query,
            "get":get,"post":[],"cookie":[],"request":get,"environment_query_present":missing,
            "environment_query":if missing { json!(query) } else { Value::Null },
            "content_type_present":false,"content_type":null,"content_length_present":false,"content_length":null,
            "http_accept_present":false,"http_accept":null,"auth_or_cookie_header_present":false,
            "private_path_environment_present":false,"script_filename_matches_public_entry":true,
            "wp_is_json_request_available":available,"wp_is_json_request":if available { json!(false) } else { Value::Null },
            "display_errors":"1","input":{"bytes":0,"sha256":db::sha(b"")}})
    })
}
fn number(value:&Value,maximum:u64)->Result<u64> {
    value.as_u64().filter(|n|*n<=maximum).ok_or_else(||anyhow::anyhow!("worker_numeric_metric"))
}
fn digest(value:&Value)->bool {
    value.as_str().is_some_and(|s|s.len()==64 && s.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b)))
}
fn pick(value:&Value,keys:&[&str])->Value {
    Value::Object(keys.iter().map(|key|((*key).into(),value[*key].clone())).collect())
}
fn operations_summary(value:&Value)->Result<Value> {
    let rows=value["operations"].as_array().ok_or_else(||anyhow::anyhow!("worker_operations"))?;
    ensure!(!rows.is_empty() && rows.len()<=4096,"worker_operation_budget");
    let keys=BTreeSet::from(["seq","op","request_data_bytes","request_data_sha256","request_frame_bytes",
        "request_frame_sha256","reply_frame_bytes","reply_frame_sha256","ok","row_count","column_count",
        "server_error_code","server_error_state_hex"]);
    let mut counts=BTreeMap::from([("hello",0u64),("query",0),("escape",0),("close",0)]);
    let(mut requested,mut replied,mut largest_row,mut largest_column,mut largest_reply)=(0,0,0,0,0);
    for (index,row) in rows.iter().enumerate() {
        ensure!(row.as_object().is_some_and(|m|m.keys().map(String::as_str).collect::<BTreeSet<_>>()==keys),"worker_operation_shape");
        ensure!(number(&row["seq"],4096)?==index as u64+1 && row["ok"].is_boolean(),"worker_operation_order");
        let op=row["op"].as_str().filter(|s|counts.contains_key(*s)).ok_or_else(||anyhow::anyhow!("worker_operation_kind"))?;
        *counts.get_mut(op).expect("validated operation")+=1;
        number(&row["request_data_bytes"],65536)?;
        let request=number(&row["request_frame_bytes"],262144)?;let reply=number(&row["reply_frame_bytes"],524288)?;
        ensure!(request>0 && reply>0,"worker_operation_frame");requested+=request;replied+=reply;largest_reply=largest_reply.max(reply);
        for key in ["request_data_sha256","request_frame_sha256","reply_frame_sha256"] {ensure!(digest(&row[key]),"worker_operation_digest");}
        if !row["row_count"].is_null() {largest_row=largest_row.max(number(&row["row_count"],256)?);}
        if !row["column_count"].is_null() {largest_column=largest_column.max(number(&row["column_count"],32)?);}
        if !row["server_error_code"].is_null() {number(&row["server_error_code"],65535)?;}
        ensure!(row["server_error_state_hex"].is_null() || row["server_error_state_hex"].as_str().is_some_and(|s|
            s.len()==10 && s.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b))),"worker_operation_state");
    }
    let rpc=&value["rpc"];
    ensure!(rows[0]["op"]=="hello" && rows[rows.len()-1]["op"]=="close" && counts["hello"]==1 && counts["close"]==1
        && counts["query"]>0 && number(&rpc["operations"],4096)?==rows.len() as u64,"worker_operation_session");
    ensure!(requested==number(&rpc["request_bytes"],8388608)? && replied==number(&rpc["response_bytes"],16777216)?
        && replied==number(&rpc["response_read_bytes"],16777216)?,"worker_rpc_byte_balance");
    Ok(json!({"operations":rows.len(),"hello":counts["hello"],"query":counts["query"],"escape":counts["escape"],"close":counts["close"],
        "request_bytes":requested,"reply_bytes":replied,"largest_row_count":largest_row,
        "largest_column_count":largest_column,"largest_reply_frame_bytes":largest_reply}))
}
fn authorize_response(value:&Value,admission:&Admission,owned:&Value)->Result<(Value,Response)> {
    let case=admission.case;
    ensure!(owned["owned_pid"].as_u64().is_some_and(|n|n>0) && number(&owned["stdout"]["bytes"],4*1024*1024)?>0
        && digest(&owned["stdout"]["sha256"]),"worker_owned_metrics");
    ensure!(owned["reaped"]==true && owned["exit_code"]==0 && owned["stdout_eof"]==true && owned["stderr_eof"]==true
        && owned["identity_after_verified"]==true && owned["stderr"]==json!({"bytes":0,"sha256":db::sha(b"")}),"worker_owned_completion");
    ensure!(value["schema"]=="recastpress.php-wasix-wp-private-worker-observation.v0.5" && value["passed"]==true
        && value["outcome"]=="fixed_private_wp_worker_passed" && value["phase"]=="full_private_response_verified"
        && value["case"]==case.as_str() && value["mode"]=="home" && value["module_compilations"]==1
        && value["private_ipc_only"]==true && value["publication_authorized"]==false && value["native_comparison_performed"]==false,"private_worker_profile");
    let positive=["physical_connection_created","physical_disconnect_completed","db_actor_joined","db_actor_exited",
        "fresh_runtime","source_rechecked_after_execution","full_wordpress_bootstrap"];
    let negative=["guest_networking","host_environment_forwarded","host_home_mapped","db_credentials_supplied_to_guest","native_php_fallback","product_runtime"];
    ensure!(positive.iter().all(|key|value[*key]==true) && negative.iter().all(|key|value[*key]==false)
        && value.get("db_actor_failure")==Some(&Value::Null),"worker_runtime_completion");
    let capture=&value["cgi_capture"];
    ensure!(["guest_exit_success","runtime_shutdown_returned","stdout_eof","stderr_eof"].iter().all(|k|capture[*k]==true)
        && capture["stdout_overflow"]==false && capture["stderr_overflow"]==false && capture["stderr_bytes"]==0
        && capture["stderr_sha256"]==db::sha(b""),"worker_cgi_completion");
    ensure!(number(&capture["stdout_bytes"],3*1024*1024)?>0 && digest(&capture["stdout_sha256"]),"worker_cgi_stdout");
    let outer=&capture["response"];
    ensure!(outer["status"]==200 && number(&outer["header_count"],2)?>0 && number(&outer["body_bytes"],3*1024*1024)?>0
        && digest(&outer["body_sha256"]) && outer["representation_length"].is_null(),"worker_cgi_outer_frame");
    let rpc=&value["rpc"];
    ensure!(["finished","opened_once","close_dispatched"].iter().all(|k|rpc[*k]==true)
        && rpc.get("failure")==Some(&Value::Null) && rpc["channel_open"]==false && rpc["response_pending"]==false
        && rpc["pending_bytes"]==0 && rpc["unread_response_bytes"]==0,"worker_rpc_sealed");
    let access=&value["managed_access"];
    let access_numbers=["pending_bytes","unread_response_bytes","active_handles","requests","generated_bytes","read_bytes",
        "open_events","closed_handles","invalid","io_errors","allowed","denied","missing","unmanaged"];
    for key in access_numbers {number(&access[key],16777216)?;}
    let requests=number(&access["requests"],16384)?;
    ensure!(requests>0 && access["finished"]==true && access.get("protocol_error")==Some(&Value::Null)
        && ["pending_bytes","unread_response_bytes","active_handles","invalid","io_errors"].iter().all(|k|access[*k]==0)
        && access["generated_bytes"]==requests*5 && access["read_bytes"]==requests*5 && access["open_events"]==access["closed_handles"]
        && ["allowed","denied","missing","unmanaged"].iter().map(|k|access[*k].as_u64().unwrap_or(u64::MAX/8)).sum::<u64>()==requests,
        "worker_managed_access_sealed");
    let php=&value["php_result"];
    let php_true=["passed","rust_executed","wasm_executed","owned_db_dropin_loaded","source_inventory_verified","core_files_unchanged",
        "bootstrap_completed","shortinit_false","physical_disconnect_completed","request_dispatch_completed","template_loader_completed","anonymous_request","theme_rendered"];
    let php_false=["native_mysqli_handle","wp_installing","install_completed","html_normalized","http_listener_exercised",
        "site_config_file_created","source_core_theme_plugin_modified","outbound_mail_or_http_sent_by_fixture"];
    ensure!(php["schema"]=="recastpress.php-wasix-wp-request-context-php-observation.v0.5" && php["outcome"]=="fixed_wasix_context_passed"
        && php["phase"]=="complete" && php["mode"]=="home" && php["wp_version"]=="7.1.3" && php_true.iter().all(|k|php[*k]==true)
        && php_false.iter().all(|k|php[*k]==false) && php["known_wp_table_count"]==12 && php["warning_count"]==0
        && php["blocked_mail_calls"]==0 && php["blocked_http_calls"]==0,"worker_php_anonymous_completion");
    let contexts=&value["private_contexts"];
    ensure!(contexts.as_object().is_some_and(|m|m.len()==7) && contexts["case"]==case.as_str()
        && contexts["is_home"]==(case==Case::Home) && contexts["is_404"]==(case==Case::Missing)
        && contexts["transport_body"]==json!({"bytes":0,"sha256":db::sha(b"")}) && private_contexts_valid(contexts,case),"worker_public_context");
    ensure!(value["prepared"]["verification_scope"]=="compiled_fixtures_and_core_only" && value["prepared"]["source_verified"]==true
        && value["prepared"]["transitive_inputs_verified_by_worker"]==false && value["prepared"]["core_files"]==3782
        && value["prepared"]["owned_dropin_files"]==1,"worker_source_scope");
    let response=private_response::decode(&serde_json::to_vec(&value["private_response"])?)
        .map_err(|_|anyhow::anyhow!("private_response_shape"))?;
    ensure!(response.status()==case.status() && php["response_summary"]==json!(response.summary()),"worker_response_summary");
    let operations=operations_summary(value)?;
    let autoload=number(&php["autoload_option_count"],u32::MAX as u64)?;
    let autoload_max=number(&php["autoload_max_value_bytes"],u32::MAX as u64)?;
    ensure!(autoload>0 && autoload_max>0,"worker_autoload_observations");
    let context_bytes=serde_json::to_vec(contexts)?;
    let mut summary=json!({"schema":"recastpress.php-wasix-wp-private-http-worker-summary.v0.5",
        "worker_schema":"recastpress.php-wasix-wp-private-worker-observation.v0.5","outcome":"fixed_private_wp_worker_passed",
        "phase":"full_private_response_verified","case":case.as_str(),"mode":"home","module_compilations":1,
        "completion_verified":true,"public_context_verified":true,"private_ipc_only":true,"worker_publication_authorized":false,
        "native_comparison_performed":false,"db_actor_failure":null,"response_summary":response.summary(),
        "private_contexts_identity":{"bytes":context_bytes.len(),"sha256":db::sha(&context_bytes)},
        "autoload_option_count":autoload,"autoload_max_value_bytes":autoload_max,"operations_summary":operations});
    for key in positive.into_iter().chain(negative) {summary[key]=value[key].clone();}
    summary["cgi_capture"]=pick(capture,&["guest_exit_success","runtime_shutdown_returned","stdout_eof","stderr_eof","stdout_overflow",
        "stderr_overflow","stdout_bytes","stderr_bytes","stdout_sha256","stderr_sha256"]);
    summary["cgi_capture"]["response"]=pick(outer,&["status","header_count","body_bytes","body_sha256","representation_length"]);
    summary["rpc"]=pick(rpc,&["finished","opened_once","close_dispatched","failure","channel_open","response_pending","pending_bytes",
        "unread_response_bytes","operations","request_bytes","response_bytes","response_read_bytes"]);
    summary["managed_access"]=pick(access,&["finished","protocol_error","pending_bytes","unread_response_bytes","active_handles","requests",
        "generated_bytes","read_bytes","open_events","closed_handles","invalid","io_errors","allowed","denied","missing","unmanaged"]);
    Ok((summary,response_from_decoded(&response)?))
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
async fn run_worker(root:&Path,database:Database,admission:Admission,record:&mut Value)->Result<(Value,Response)> {
    let case=admission.case;
    let mut secrets=vec![database.password.clone(),database.nonce.clone()];
    secrets.extend(database.salts.values().iter().map(|s|s.to_string()));
    let before=worker_identity(root)?;record["identity_before"]=before.clone();
    let payload=serde_json::to_vec(&json!({"schema":"recastpress.php-wasix-wp-private-worker-input.v0.5","case":case.as_str(),"database":database}))?;
    let execution=capture_owned(Command::new(root.join(WORKER)).env_clear(),&payload,Duration::from_secs(300),4*1024*1024,64*1024,record).await;
    let after=worker_identity(root);record["identity_after_verified"]=json!(after.as_ref().is_ok_and(|a|*a==before));
    let out=execution?;ensure!(after?==before,"worker_identity_changed");
    ensure!(secrets.iter().all(|s| {
        let encoded=db::hex(s.as_bytes());
        !out.windows(s.len()).any(|b|b==s.as_bytes()) && !out.windows(encoded.len()).any(|b|b==encoded.as_bytes())
    }),"worker_private_secret_guard");
    ensure!(out.len()>0 && out.last()==Some(&b'\n') && out.iter().filter(|&&b|b==b'\n').count()==1,"worker_json_line");
    let value=strict_json::parse(&out).map_err(|_|anyhow::anyhow!("worker_json"))?;
    authorize_response(&value,&admission,record)
}

struct Observations { executions:usize, rejected:BTreeMap<&'static str,usize>, requests:Vec<Value>, secrets:Vec<String> }
#[derive(Clone)]
struct App { root:PathBuf, database:Database, observations:Arc<Mutex<Observations>>, semaphore:Arc<Semaphore>, stop:Arc<Notify> }
fn rejected(app:&App,reason:Rejection)->Response {
    if let Ok(mut state)=app.observations.lock() {*state.rejected.entry(reason.name()).or_default()+=1;}
    match reason {Rejection::Busy|Rejection::Budget=>negative(503,"fixed request unavailable\n"),_=>negative(400,"fixed request rejected\n")}
}
async fn handle(State(app):State<App>,request:Request)->Response {
    let admission=match admit(request).await {Ok(value)=>value,Err(reason)=>return rejected(&app,reason)};
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
    match tokio::spawn(supervised(app,database,admission,permit)).await {
        Ok(response)=>response,Err(_)=>negative(502,"fixed worker failed\n")
    }
}
async fn supervised(app:App,database:Database,admission:Admission,_permit:tokio::sync::OwnedSemaphorePermit)->Response {
    let case=admission.case;
    let mut worker=json!({"reaped":false,"stdout_eof":false,"stderr_eof":false});
    let result=run_worker(&app.root,database,admission,&mut worker).await;
    let (row,response)=match result {Ok((summary,response))=>(json!({"case":case.as_str(),"worker":worker,"worker_summary":summary,"response_constructed":true,"anonymous_fixture_request_authorized":true}),response),
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
    println!("{}",json!({"schema":"recastpress.php-wasix-wp-private-http-ready.v0.5","port":port}));std::io::stdout().flush()?;
    let completed=app.semaphore.clone();
    axum::serve(listener,Router::new().fallback(handle).with_state(app)).with_graceful_shutdown(async move {shutdown.notified().await}).await?;
    let _completed=completed.acquire_owned().await.map_err(|_|anyhow::anyhow!("supervisor_shutdown"))?;
    let state=observations.lock().map_err(|_|anyhow::anyhow!("observation_lock"))?;
    let passed=!stdin_protocol.load(Ordering::SeqCst) && state.executions==3 && state.requests.len()==3 && state.requests.iter().all(|r|r["response_constructed"]==true && r["worker"]["reaped"]==true);
    let report=json!({"schema":"recastpress.php-wasix-wp-private-http-observation.v0.5","passed":passed,
        "outcome":if passed {"fixed_private_http_passed"}else{"fixed_private_http_failed"},"requests":state.requests,
        "worker_executions":state.executions,"worker_build":worker,"rejected_counts":state.rejected,
        "concurrency":1,"valid_execution_limit":3,"bound_loopback_only":true,"graceful_shutdown_completed":true,
        "stdin_shutdown_observed":stdin_shutdown.load(Ordering::SeqCst),"stdin_protocol_error":stdin_protocol.load(Ordering::SeqCst),
        "startup_private_input_used":true,"private_config_on_http":false,"transport_delivery_verified":false,"source_files_rehashed_by_service":false,"native_comparison_performed":false,
        "native_php_fallback":false,"product_runtime":false});
    let encoded=serde_json::to_string(&report)?;
    ensure!(encoded.len()<=12*1024*1024,"public_report_quota");
    ensure!(state.secrets.iter().all(|s|!encoded.contains(s)&&!encoded.contains(&db::hex(s.as_bytes()))),"public_secret_guard");Ok(report)
}
#[tokio::main(flavor="multi_thread",worker_threads=2)]
async fn main() {
    let result=async {ensure!(std::env::args_os().count()==1,"closed_arguments");serve(parse_input(&private_line(&mut std::io::stdin())?)?).await}.await;
    match result {Ok(report)=>{println!("{report}");if report["passed"]!=true {std::process::exit(1)}},Err(_)=>{println!("{}",json!({"schema":"recastpress.php-wasix-wp-private-http-observation.v0.5","passed":false,"failure":"fixed_http_probe_failed"}));std::process::exit(1)}}
}
fn private_line(read:&mut impl Read)->Result<Vec<u8>> {
    let mut wire=Vec::new();let mut byte=[0u8;1];
    loop {ensure!(wire.len()<24*1024,"input_line_quota");ensure!(read.read(&mut byte)?==1,"input_line_eof");
        if byte[0]==b'\n' {return Ok(wire)}wire.push(byte[0]);}
}

#[cfg(test)]
mod tests {
    use super::*;
    fn worker_fixture(case:Case)->(Value,Value) {
        let (mut value,mut php)=private_fixture();
        let missing=case==Case::Missing;
        php["case"]=json!(case.as_str());php["is_home"]=json!(!missing);php["is_404"]=json!(missing);
        if missing {
            php["response"]["status"]=json!(404);
            for stage in ["before_bootstrap","after_bootstrap","after_dispatch"] {
                let context=&mut php[format!("context_{stage}")];
                context["uri"]=json!("/?p=2147483647");context["query"]=json!("p=2147483647");
                context["get"]=json!({"p":"2147483647"});context["request"]=json!({"p":"2147483647"});
                context["environment_query_present"]=json!(true);context["environment_query"]=json!("p=2147483647");
            }
        }
        value["private_response"]=php["response"].clone();
        value["private_contexts"]=pick(&php,&["case","is_home","is_404","transport_body",
            "context_before_bootstrap","context_after_bootstrap","context_after_dispatch"]);
        value["schema"]=json!("recastpress.php-wasix-wp-private-worker-observation.v0.5");value["passed"]=json!(true);
        value["outcome"]=json!("fixed_private_wp_worker_passed");value["phase"]=json!("full_private_response_verified");
        value["case"]=json!(case.as_str());value["mode"]=json!("home");value["module_compilations"]=json!(1);
        value["private_ipc_only"]=json!(true);value["publication_authorized"]=json!(false);value["native_comparison_performed"]=json!(false);
        value["fresh_runtime"]=json!(true);
        for k in ["guest_networking","host_environment_forwarded","host_home_mapped","db_credentials_supplied_to_guest","native_php_fallback","product_runtime"] {value[k]=json!(false);}
        value["cgi_capture"]["stdout_bytes"]=json!(100);value["cgi_capture"]["stdout_sha256"]=json!(db::sha(b"fixed outer CGI"));
        value["cgi_capture"]["response"]=json!({"status":200,"header_count":1,"body_bytes":80,"body_sha256":db::sha(b"fixed outer JSON"),"representation_length":null});
        let mut diagnostic=json!({"schema":"recastpress.php-wasix-wp-request-context-php-observation.v0.5",
            "outcome":"fixed_wasix_context_passed","phase":"complete","mode":"home","warning_count":0,
            "known_wp_table_count":12,"wp_version":"7.1.3","blocked_mail_calls":0,"blocked_http_calls":0,"autoload_option_count":117,"autoload_max_value_bytes":1024});
        for k in ["passed","rust_executed","wasm_executed","owned_db_dropin_loaded","source_inventory_verified","core_files_unchanged",
            "bootstrap_completed","shortinit_false","physical_disconnect_completed","request_dispatch_completed","template_loader_completed","anonymous_request","theme_rendered"] {diagnostic[k]=json!(true);}
        for k in ["native_mysqli_handle","wp_installing","install_completed","html_normalized","http_listener_exercised",
            "site_config_file_created","source_core_theme_plugin_modified","outbound_mail_or_http_sent_by_fixture"] {diagnostic[k]=json!(false);}
        let decoded=private_response::decode(&serde_json::to_vec(&value["private_response"]).unwrap()).unwrap();
        diagnostic["response_summary"]=json!(decoded.summary());value["php_result"]=diagnostic;
        value["prepared"]=json!({"verification_scope":"compiled_fixtures_and_core_only","source_verified":true,
            "transitive_inputs_verified_by_worker":false,"core_files":3782,"owned_dropin_files":1});
        value["rpc"]["opened_once"]=json!(true);value["rpc"]["close_dispatched"]=json!(true);
        value["rpc"]["operations"]=json!(3);value["rpc"]["request_bytes"]=json!(3);value["rpc"]["response_bytes"]=json!(3);value["rpc"]["response_read_bytes"]=json!(3);
        value["operations"]=json!(["hello","query","close"].into_iter().enumerate().map(|(i,op)|json!({
            "seq":i+1,"op":op,"request_data_bytes":0,"request_data_sha256":db::sha(b""),"request_frame_bytes":1,
            "request_frame_sha256":db::sha(b"x"),"reply_frame_bytes":1,"reply_frame_sha256":db::sha(b"x"),
            "ok":true,"row_count":null,"column_count":null,"server_error_code":null,"server_error_state_hex":null})).collect::<Vec<_>>());
        value["managed_access"]["generated_bytes"]=json!(5);value["managed_access"]["read_bytes"]=json!(5);
        value["managed_access"]["open_events"]=json!(1);value["managed_access"]["closed_handles"]=json!(1);
        value["managed_access"]["allowed"]=json!(1);
        for k in ["denied","missing","unmanaged"] {value["managed_access"][k]=json!(0);}
        let owned=json!({"owned_pid":123,"reaped":true,"exit_code":0,"stdout_eof":true,"stderr_eof":true,
            "identity_after_verified":true,"stdout":{"bytes":100,"sha256":db::sha(b"fixed private IPC")},"stderr":{"bytes":0,"sha256":db::sha(b"")}});
        (value,owned)
    }
    #[tokio::test]
    async fn unknown_private_body_requires_admission_and_clean_worker_then_is_served_exactly() {
        for (case,target) in [(Case::Home,"/"),(Case::Missing,"/?p=2147483647")] {
            let admission=admit(req("GET",target)).await.unwrap_or_else(|_|panic!("fixed admission"));
            let (value,owned)=worker_fixture(case);let expected=value["private_response"]["body_hex"].as_str().unwrap();
            let (summary,response)=authorize_response(&value,&admission,&owned).unwrap();
            assert_eq!(summary["worker_publication_authorized"],false);assert_eq!(summary["completion_verified"],true);
            assert_eq!(summary["schema"],"recastpress.php-wasix-wp-private-http-worker-summary.v0.5");
            let body=to_bytes(response.into_body(),1024).await.unwrap();assert_eq!(db::hex(&body),expected);
            assert!(summary.get("private_response").is_none() && summary.get("private_contexts").is_none() && summary.get("operations").is_none());
        }
        assert!(admit(req("POST","/")).await.is_err());
        let mut nonempty=req("GET","/");*nonempty.body_mut()=Body::from("x");assert!(admit(nonempty).await.is_err());
    }
    #[tokio::test]
    async fn codec_keeps_binary_body_and_duplicate_cookie_value_order() {
        let admission=admit(req("GET","/")).await.unwrap_or_else(|_|panic!("fixed admission"));
        let (mut value,owned)=worker_fixture(Case::Home);let body=b"a\0b\xff";
        value["private_response"]=json!({"status":200,"headers_hex":[db::hex(b"Set-Cookie: one=1"),db::hex(b"Set-Cookie: two=2")],
            "body_bytes":body.len(),"body_hex":db::hex(body),"body_sha256":db::sha(body)});
        value["php_result"]["response_summary"]=json!(private_response::decode(&serde_json::to_vec(&value["private_response"]).unwrap()).unwrap().summary());
        let (_,response)=authorize_response(&value,&admission,&owned).unwrap();
        assert_eq!(response.headers().get_all("set-cookie").iter().map(|v|v.as_bytes()).collect::<Vec<_>>(),vec![b"one=1".as_slice(),b"two=2".as_slice()]);
        assert_eq!(response.headers()["content-length"],"4");assert_eq!(response.headers()["connection"],"close");
        assert_eq!(to_bytes(response.into_body(),100).await.unwrap().as_ref(),body);
    }
    #[test]
    fn incomplete_reap_lifecycle_context_or_protocol_never_constructs_response() {
        let admission=Admission {case:Case::Home};let (value,owned)=worker_fixture(Case::Home);
        for field in ["reaped","stdout_eof","stderr_eof","identity_after_verified"] {
            let mut bad=owned.clone();bad[field]=json!(false);assert!(authorize_response(&value,&admission,&bad).is_err());
        }
        for (parent,key,bad) in [(None,"db_actor_joined",json!(false)),(None,"publication_authorized",json!(true)),
            (Some("private_contexts"),"case",json!("missing")),(Some("rpc"),"pending_bytes",json!(1)),
            (Some("managed_access"),"active_handles",json!(1)),(Some("php_result"),"anonymous_request",json!(false)),
            (Some("php_result"),"response_summary",json!({}))] {
            let mut changed=value.clone();if let Some(p)=parent {changed[p][key]=bad;} else {changed[key]=bad;}
            assert!(authorize_response(&changed,&admission,&owned).is_err());
        }
        let mut changed=value;changed["private_contexts"]["context_after_dispatch"]["environment_query"]=json!("private");
        assert!(authorize_response(&changed,&admission,&owned).is_err());
    }
    #[test]
    fn safe_summary_excludes_all_unknown_private_payload_and_raw_operation_rows() {
        let admission=Admission {case:Case::Home};let (mut value,owned)=worker_fixture(Case::Home);
        let marker="private-unknown-extra-placeholder";
        for parent in ["php_result","cgi_capture","rpc","managed_access","prepared"] {value[parent]["extra_private_payload"]=json!(marker);}
        value["extra_private_payload"]=json!(marker);
        let (summary,_)=authorize_response(&value,&admission,&owned).unwrap();let text=summary.to_string();
        for forbidden in [marker,"opaque unknown header",value["private_response"]["body_hex"].as_str().unwrap()] {assert!(!text.contains(forbidden));}
        assert!(!text.contains("headers_hex") && !text.contains("body_hex") && !text.contains("context_after_dispatch") && !text.contains("request_frame_sha256"));
        assert_eq!(summary["operations_summary"]["operations"],3);
        assert_eq!(summary["private_contexts_identity"]["sha256"],db::sha(&serde_json::to_vec(&value["private_contexts"]).unwrap()));
    }
    #[test]
    fn recursive_worker_json_duplicates_cannot_collapse_before_codec() {
        assert!(strict_json::parse(br#"{"private_response":{"status":200,"status":404}}"#).is_err());
    }
    fn private_fixture() -> (Value, Value) {
        let report = json!({"php_result":{},"full_wordpress_bootstrap":true,"source_rechecked_after_execution":true,
            "physical_connection_created":true,"physical_disconnect_completed":true,"db_actor_joined":true,
            "db_actor_exited":true,"db_actor_failure":null,
            "cgi_capture":{"guest_exit_success":true,"runtime_shutdown_returned":true,"stdout_eof":true,"stderr_eof":true,
                "stdout_overflow":false,"stderr_overflow":false,"stderr_bytes":0,"stderr_sha256":db::sha(b""),"response":{"status":200}},
            "managed_access":{"finished":true,"protocol_error":null,"requests":1,"pending_bytes":0,
                "unread_response_bytes":0,"active_handles":0,"invalid":0,"io_errors":0},
            "rpc":{"finished":true,"failure":null,"channel_open":false,"response_pending":false,"pending_bytes":0,"unread_response_bytes":0}});
        let body = b"unknown private response\0\xff";
        let mut php = json!({"case":"home","is_home":true,"is_404":false,"transport_body":{"bytes":0,"sha256":db::sha(b"")},
            "response":{"status":200,"headers_hex":[db::hex(b"X-Private: opaque unknown header")],"body_bytes":body.len(),
                "body_hex":db::hex(body),"body_sha256":db::sha(body)}});
        for stage in ["before_bootstrap", "after_bootstrap", "after_dispatch"] {
            let available = stage != "before_bootstrap";
            php[format!("context_{stage}")] = json!({"stage":stage,"method":"GET","uri":"/","query":"",
                "get":[],"post":[],"cookie":[],"request":[],"environment_query_present":false,"environment_query":null,
                "content_type_present":false,"content_type":null,"content_length_present":false,"content_length":null,
                "http_accept_present":false,"http_accept":null,"auth_or_cookie_header_present":false,
                "private_path_environment_present":false,"script_filename_matches_public_entry":true,
                "wp_is_json_request_available":available,"wp_is_json_request":if available { json!(false) } else { Value::Null },
                "display_errors":"1","input":{"bytes":0,"sha256":db::sha(b"")}});
        }
        (report, php)
    }
    fn input()->Value {
        let salt="a".repeat(48);
        json!({"schema":"recastpress.php-wasix-wp-private-http-input.v0.5","database":{
            "schema":"recastpress.php-wasix-wp-request-input.v0.5","mode":"home","host":"127.0.0.1","port":3306,
            "database":"rp_wpdb_probe","user":"rp_wpdb_probe","password":salt,"nonce":"b".repeat(48),"salts":{
                "AUTH_KEY":salt,"SECURE_AUTH_KEY":salt,"LOGGED_IN_KEY":salt,"NONCE_KEY":salt,
                "AUTH_SALT":salt,"SECURE_AUTH_SALT":salt,"LOGGED_IN_SALT":salt,"NONCE_SALT":salt}}})
    }
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
