#![forbid(unsafe_code)]
#[path = "../../php-wasix-wp-bootstrap-probe/src/db.rs"]
mod db;
#[path = "../../php-wasix-wp-bootstrap-probe/src/rpc_fs.rs"]
mod rpc_fs;
#[path = "../../php-wasix-wp-bootstrap-probe/src/result_budget.rs"]
mod result_budget;
#[path = "../../php-wasix-wp-bootstrap-probe/src/capture.rs"]
mod capture;
#[path = "../../php-wasi-host-probe/src/cgi_response.rs"]
mod cgi_response;
#[path = "../../php-wasix-host-probe/src/fiber_adapter.rs"]
mod fiber_adapter;
#[allow(dead_code)]
#[path = "../../php-wasix-wpdb-probe/src/inputs.rs"]
mod inputs;
use anyhow::{Context,Result,ensure};
use recastpress_wp_source_fs_probe::SourceFs;
use serde_json::{Value,json};
use std::{collections::{BTreeMap,BTreeSet},fs::File,io::{Read,Write},path::{Path,Component},
    sync::{Arc,atomic::{AtomicBool,Ordering}},time::{Duration,Instant}};
use wasmer::{Engine,Module};
use wasmer_wasix::{Pipe,PluggableRuntime,UnsupportedVirtualNetworking,
    runners::wasi::{RuntimeOrEngine,WasiRunner},runtime::{module_cache::HashedModuleData,task_manager::tokio::TokioTaskManager}};
const MODULE:&str="target/php-wasix-managed-access-probe/php-fiber-cgi.wasm";
const RECEIPT:&str="experiments/php-wasix-managed-access-probe/evidence/build.json";
const VARIANT:&str="nts-fresh-wasi-zend-fibers-managed-access-v1";
const PREPARED:&str="target/php-wasix-wpdb-utf8-probe/prepared.json";
const BASE:&str="experiments/php-wasix-wpdb-utf8-probe/";
const PHP_MOUNT:&str="/experiments/php-wasix-wpdb-utf8-probe/fixtures";
const WP_MOUNT:&str="/.reference/wordpress-7.1.3/wp-includes";
const FIXTURES:&[(&str,&[u8])]=&[
    ("bridge.php",include_bytes!("../fixtures/bridge.php")),("entry.php",include_bytes!("../fixtures/entry.php")),
    ("corpus.php",include_bytes!("../fixtures/corpus.php")),("adapter.php",include_bytes!("../fixtures/adapter.php")),
    ("helpers.php",include_bytes!("../fixtures/helpers.php"))];
const ORACLES:&[(&str,&str)]=&[
    ("mysql","c1d7a9e4d55c68b030830e1d8e883d52f42bae031df4bd3062f3bcf4d5370ada"),
    ("mariadb","18c01e8105f7e41e407ebdd36f4e057233b3d808028d06b37168877e2ec2ba90")];
const COMPARE:&[&str]=&["observations","query_trace_hex","session_snapshot","server_info_hex","loaded_source_sha256",
    "suppress_errors_previous","bootstrap_num_queries","final_num_queries","final_row_count","utf8_input_hex"];
const WP_PINS: &[(&str, usize, &str)] = &[
    (
        "load.php",
        56475,
        "3efd0934bd8a454a8d7076fbbd21504ea097a753125a48e64b8a1dad380d357a",
    ),
    (
        "plugin.php",
        37123,
        "d70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d",
    ),
    (
        "class-wp-hook.php",
        17584,
        "b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e",
    ),
    (
        "class-wp-filter-sentinel.php",
        760,
        "68c9c675d3805a343835728d98a0efedf0df9833cb904bebf909c7e0fda5c61c",
    ),
    (
        "class-wpdb.php",
        121437,
        "e15403e90032dd0508811301505e491d4212de5152581a98f51b744a1a3903bd",
    ),
    (
        "functions.php",
        295481,
        "cac8a17e293df99f0683a88debd3ba5e5cce274183a4e9fd3c9f18375747f41c",
    ),
    (
        "option.php",
        105246,
        "fe3f0b7e115b2a50c31162ed6428abe4088c080b0f71b9678d84806168fe90d0",
    ),
    (
        "class-wp-error.php",
        7514,
        "a9e971548bf6a1d93c821f980f530efba985c25d5d0b13d9b63e63b378a81a94",
    ),
];
include!("module_verifier.rs");
fn known_json_cgi_headers(frame: &cgi_response::Response) -> bool {
    frame.status == 200
        && frame.headers.iter().any(|(name, value)| {
            name == "content-type"
                && matches!(
                    value.as_slice(),
                    b"application/json"
                        | b"application/json; charset=UTF-8"
                        | b"application/json; charset=utf-8"
                )
        })
        && frame.headers.iter().all(|(name, value)| {
            name == "content-type" || (name == "x-powered-by" && value.as_slice() == b"PHP/8.5.12")
        })
}
fn fixed_json_cgi_headers(frame: &cgi_response::Response) -> bool {
    known_json_cgi_headers(frame)
        && frame.headers.iter().filter(|(name, _)| name == "content-type").count() == 1
        && frame.headers.iter().filter(|(name, _)| name == "x-powered-by").count() <= 1
}
fn context(input:&db::Input)->Value {json!({"schema":"recastpress.php-wasix-wpdb-utf8-context.v0.5","nonce":input.nonce})}
fn tree(files:BTreeMap<String,Arc<[u8]>>)->Result<SourceFs> {
    SourceFs::new(files,BTreeSet::new()).map_err(|_|anyhow::anyhow!("finite_source_tree"))
}
fn load_sources(root:&Path,report:&mut Value)->Result<(SourceFs,SourceFs,Value)> {
    let wire=fixed(root,PREPARED,2*1024*1024)?;let prepared:Value=serde_json::from_slice(&wire)?;
    ensure!(prepared["schema"]=="recastpress.php-wasix-wpdb-utf8-prepared.v0.5","prepared_profile");
    let hashes=prepared["input_files"].as_object().context("prepared_input_map")?;
    ensure!(!hashes.is_empty() && hashes.len()<=512,"prepared_input_count");
    for (path,hash) in hashes {ensure!(hash==&db::sha(&fixed(root,path,4*1024*1024)?),"prepared_source_changed");}
    let files=prepared["files"].as_array().context("prepared_files")?;
    ensure!(files.len()==13 && prepared["wordpress_files"].as_object().context("wordpress_files")?.len()==8,"thirteen_guest_files");
    let mut php=BTreeMap::new();let mut wp=BTreeMap::new();let mut seen=BTreeSet::new();
    for item in files {
        let guest=item["guest"].as_str().context("guest_path")?;let source=item["source"].as_str().context("source_path")?;
        ensure!(seen.insert(guest) && item["guest_read_only"]==true,"duplicate_or_writable_guest");
        let data=fixed(root,source,512*1024)?;
        ensure!(item["bytes"].as_u64()==Some(data.len() as u64) && item["sha256"]==db::sha(&data),"guest_source_identity");
        if let Some(name)=guest.strip_prefix(&format!("{PHP_MOUNT}/")) {
            let expected=FIXTURES.iter().find(|(n,_)|*n==name).context("closed_php_fixture")?.1;
            ensure!(source==format!("{BASE}fixtures/{name}") && data==expected && item["kind"]=="derived_fixture","compiled_php_fixture");
            if name=="adapter.php" {ensure!(data==include_bytes!("../../php-wasix-wp-bootstrap-probe/fixtures/adapter.php"),"unchanged_full_wp_adapter");}
            php.insert(name.to_owned(),Arc::<[u8]>::from(data));
        } else if let Some(name)=guest.strip_prefix(&format!("{WP_MOUNT}/")) {
            let (_,bytes,hash)=WP_PINS.iter().find(|(file,_,_)|*file==name).context("closed_original_wp_file")?;
            ensure!(data.len()==*bytes && db::sha(&data)==*hash,"compiled_original_wp_pin");
            let mapping=&prepared["wordpress_files"][name];
            ensure!(mapping["path"]==source && mapping["bytes"]==item["bytes"] && mapping["sha256"]==item["sha256"]
                && prepared["wordpress_source_sha256"][format!("wp-includes/{name}")]==item["sha256"]
                && item["kind"]=="wordpress_source","original_wordpress_source");
            wp.insert(name.to_owned(),Arc::<[u8]>::from(data));
        } else {anyhow::bail!("unexpected_guest_mount");}
    }
    ensure!(php.len()==5 && wp.len()==8,"five_fixtures_eight_wp_sources");
    report["prepared"]=json!({"path":PREPARED,"bytes":wire.len(),"sha256":db::sha(&wire),"files":files,"input_files":hashes,"verified":true});
    Ok((tree(php)?,tree(wp)?,prepared))
}
fn environment()->Vec<(String,String)> {
    [("SCRIPT_FILENAME",format!("{PHP_MOUNT}/bridge.php")),("SCRIPT_NAME","/__rp_utf8.php".into()),
        ("DOCUMENT_ROOT","/".into()),("REQUEST_METHOD","GET".into()),("QUERY_STRING","rp_request=1".into()),
        ("REQUEST_URI","/__rp_utf8.php?rp_request=1".into()),("CONTENT_LENGTH","0".into()),("REDIRECT_STATUS","200".into()),
        ("SERVER_PROTOCOL","HTTP/1.1".into()),("GATEWAY_INTERFACE","CGI/1.1".into()),("SERVER_NAME","rp-fixture.invalid".into()),
        ("SERVER_PORT","80".into())].into_iter().map(|(k,v)|(k.to_owned(),v)).collect()
}
struct Native {backend:&'static str,value:Value}
fn references(root:&Path,report:&mut Value)->Result<Vec<Native>> {
    let mut result=vec![];let mut identities=serde_json::Map::new();
    for &(backend,pin) in ORACLES {
        let path=format!("experiments/wpdb-utf8-probe/evidence/windows-{backend}-native.json");
        let wire=fixed(root,&path,2*1024*1024)?;ensure!(db::sha(&wire)==pin,"fixed_native_utf8_oracle");
        let proof:Value=serde_json::from_slice(&wire)?;
        ensure!(proof["passed"]==true && proof["outcome"]=="fixed_native_utf8_corpus_passed"
            && proof["backend"]==backend && proof["source_reverified_after"]==true && proof["normal_admin_shutdown_verified"]==true,"native_utf8_completion");
        let value=proof["observation"].clone();
        ensure!(value["passed"]==true && value["mbstring_loaded"]==true && value["observations"].as_array().is_some_and(|v|v.len()==31)
            && value["query_trace_hex"].as_array().is_some_and(|v|v.len()==22),"native_31_case_22_query_contract");
        result.push(Native{backend,value});identities.insert(backend.to_owned(),json!({"path":path,"bytes":wire.len(),"sha256":pin}));
    }
    report["native_oracles"]=Value::Object(identities);Ok(result)
}
fn matches_native(actual:&Value,expected:&Value)->bool {COMPARE.iter().all(|&key|actual.get(key)==expected.get(key))}
fn diagnostic(actual:&Value,native:&[Native])->Value {
    let rows=actual["observations"].as_array();let mut matched=BTreeSet::new();
    if let Some(rows)=rows {for row in rows.iter().take(64) {for oracle in native {
        if let Some(known)=oracle.value["observations"].as_array().and_then(|v|v.iter().find(|v|*v==row)) {
            if let Some(case)=known["case"].as_str(){matched.insert(case.to_owned());}
        }
    }}}
    json!({"phase":"php_contract","code":"whole_native_corpus_not_verified","passed":actual["passed"].as_bool(),
        "actual_observation_count":rows.map(Vec::len),"actual_query_count":actual["query_trace_hex"].as_array().map(Vec::len),
        "matched_case_ids":matched,"observations_sha256":db::sha(&serde_json::to_vec(&actual["observations"]).unwrap_or_default()),
        "query_trace_sha256":db::sha(&serde_json::to_vec(&actual["query_trace_hex"]).unwrap_or_default())})
}
fn completion(value:&Value,prepared:&Value)->bool {
    let truth=["passed","connected","constructor_connection_deferred","close_completed","included_source_gate_passed",
        "native_driver_extension_present","mbstring_loaded","wasm_executed","rust_driver_executed"];
    let falsity=["native_mysqli_handle_supplied","full_wordpress_bootstrap","database_started_by_fixture","ddl_executed_by_fixture",
        "standard_wpdb_compatibility_verified","product_driver_selected","product_abi_selected","native_tls_verified",
        "raw_non_utf8_err_verified","invalid_utf8_or_overlong_inputs_exercised","normalization_applied"];
    truth.iter().all(|&k|value[k]==true) && falsity.iter().all(|&k|value[k]==false)
        && value["schema"]=="recastpress.php-wasix-wpdb-utf8-php-observation.v0.5" && value["variant"]=="bridge"
        && value["outcome"]=="fixed_utf8_cases_passed" && value["tls_profile"]=="disabled"
        && value["runtime"]==json!({"version":"8.5.12","version_id":80512,"int_size":8,"sapi":"cgi-fcgi"})
        && value["included_source_names"]==prepared["expected_include_names"]
        && value["loaded_source_sha256"]==prepared["wordpress_source_sha256"]
}
fn stats_projection(value:&Value)->Option<Value> {
    let transport=&value["transport"];
    if transport["transport_closed"]!=true || transport["max_requests"]!=4096 || transport["kind"]!="file-channel"
        || transport["channel_guest_path"]!="/rp-wpdb/channel" {return None;}
    let mut result=serde_json::Map::new();
    for (key,maximum) in [("requests",4096),("request_bytes",8*1024*1024),("reply_bytes",16*1024*1024)] {
        result.insert(key.to_owned(),json!(transport[key].as_u64().filter(|n|*n<=maximum)?));
    }
    let mut counts=serde_json::Map::new();
    for key in ["hello","query","escape","close"] {counts.insert(key.to_owned(),json!(transport["counts"][key].as_u64().filter(|n|*n<=4096)?));}
    result.insert("counts".to_owned(),Value::Object(counts));
    result.insert("transport_closed".to_owned(),json!(true));result.insert("max_requests".to_owned(),json!(4096));
    result.insert("kind".to_owned(),json!("file-channel"));result.insert("channel_guest_path".to_owned(),json!("/rp-wpdb/channel"));
    Some(json!({"transport":result,"internal_setup_queries":value["internal_setup_queries"].as_u64().filter(|n|*n<=16)?}))
}
fn public_php(actual:&Value,native:&[Native],prepared:&Value)->Option<(Value,&'static str)> {
    if !completion(actual,prepared){return None;}
    let found=native.iter().find(|n|matches_native(actual,&n.value))?;
    let stats=stats_projection(&actual["rpc_stats"])?;
    let mut value=serde_json::Map::new();
    for &key in COMPARE {value.insert(key.to_owned(),actual[key].clone());}
    for key in ["schema","variant","outcome","runtime","passed","connected","constructor_connection_deferred","close_completed",
        "included_source_gate_passed","native_driver_extension_present","native_mysqli_handle_supplied","mbstring_loaded","included_source_names",
        "full_wordpress_bootstrap","wasm_executed","rust_driver_executed","standard_wpdb_compatibility_verified","normalization_applied"] {
        value.insert(key.to_owned(),actual[key].clone());
    }
    value.insert("rpc_stats".to_owned(),stats);Some((Value::Object(value),found.backend))
}
fn execute(engine:&Engine,module:Module,data:&HashedModuleData,php:SourceFs,wp:SourceFs,context:SourceFs,rpc:&rpc_fs::RpcFs,record:&mut Value)->Result<Value> {
    let runtime=tokio::runtime::Builder::new_multi_thread().enable_all().build()?;let guard=runtime.enter();
    let (stdin_send,stdin_read)=Pipe::channel();drop(stdin_send);
    let (out_send,out_read)=Pipe::channel();let (err_send,err_read)=Pipe::channel();
    let over=Arc::new(AtomicBool::new(false));let err_over=Arc::new(AtomicBool::new(false));let start=Instant::now();
    let outcome=(||->Result<()> {
        let mut implementation=PluggableRuntime::new(Arc::new(TokioTaskManager::new(runtime.handle().clone())));
        implementation.set_engine(engine.clone());implementation.set_networking_implementation(UnsupportedVirtualNetworking::default());implementation.http_client=None;
        let mut runner=WasiRunner::new();
        runner.with_args(["-n","-d","fiber.stack_size=2097152","-d","register_argc_argv=0","-d","allow_url_fopen=0","-d","disable_functions=mail"])
            .with_envs(environment()).with_forward_host_env(false).with_home_mapped(false).with_asynchronous_threading(true).with_current_dir("/")
            .with_mount(PHP_MOUNT.into(),Arc::new(php)).with_mount(WP_MOUNT.into(),Arc::new(wp))
            .with_mount("/rp-utf8".into(),Arc::new(context)).with_mount("/rp-wpdb".into(),Arc::new(rpc.clone()))
            .with_stdin(Box::new(stdin_read)).with_stdout(Box::new(capture::LimitedPipe::new(out_send,over.clone())))
            .with_stderr(Box::new(capture::LimitedPipe::new(err_send,err_over.clone())));
        let caps=runner.capabilities_mut();caps.insecure_allow_all=false;caps.threading.max_threads=Some(1);caps.threading.enable_deep_sleep=false;
        let result=runner.run_wasm(RuntimeOrEngine::Runtime(Arc::new(implementation)),"php-wpdb-utf8",module,*data.hash());
        drop(runner);result.map_err(|_|anyhow::anyhow!("guest_execution_failed"))
    })();
    record["execution_elapsed_ms"]=json!(start.elapsed().as_millis());record["guest_exit_success"]=json!(outcome.is_ok());
    drop(guard);runtime.shutdown_timeout(Duration::from_secs(2));record["runtime_shutdown_returned"]=json!(true);
    let stdout=capture::collect_pipe(out_read);let stderr=capture::collect_pipe(err_read);
    record["stdout_eof"]=json!(stdout.is_ok());record["stderr_eof"]=json!(stderr.is_ok());
    record["stdout_overflow"]=json!(over.load(Ordering::Relaxed));record["stderr_overflow"]=json!(err_over.load(Ordering::Relaxed));
    for (key,bytes) in [("stdout",&stdout),("stderr",&stderr)] {if let Ok(bytes)=bytes {
        record[format!("{key}_bytes")]=json!(bytes.len());record[format!("{key}_sha256")]=json!(db::sha(bytes));
    }}
    let decoded=(||->Result<Value> {
        let response=cgi_response::parse(stdout.as_ref().map_err(|_|anyhow::anyhow!("stdout_incomplete"))?)?;
        record["response"]=json!({"status":response.status,"header_count":response.headers.len(),"body_bytes":response.body.len(),"body_sha256":db::sha(&response.body)});
        ensure!(fixed_json_cgi_headers(&response),"cgi_json_header_profile");
        ensure!(response.body.ends_with(b"\n") && response.body.iter().filter(|&&b|b==b'\n').count()==1,"complete_json_cgi_required");
        let value=serde_json::from_slice(&response.body).map_err(|_|anyhow::anyhow!("php_json_shape"))?;Ok(value)
    })();
    outcome?;ensure!(!over.load(Ordering::Relaxed)&&!err_over.load(Ordering::Relaxed),"capture_overflow");
    ensure!(stderr?.is_empty(),"guest_stderr");decoded
}
fn probe(input:db::Input,report:&mut Value)->Result<()> {
    ensure!(input.mode==db::Mode::Read,"utf8_read_input_only");let root=std::env::current_dir()?.canonicalize()?;
    let bytes=load_module(&root,report)?;let module_before=report["module"].clone();let receipt_before=report["build_receipt"].clone();
    let (php,wp,prepared)=load_sources(&root,report)?;let prepared_before=report["prepared"].clone();let native=references(&root,report)?;
    let native_before=report["native_oracles"].clone();
    let context_wire=serde_json::to_vec(&context(&input))?;
    let context=tree(BTreeMap::from([("context.json".to_owned(),Arc::<[u8]>::from(context_wire))]))?;
    report["phase"]=json!("module_compile");let engine:Engine=wasmer_compiler_cranelift::Cranelift::default().into();
    let data=HashedModuleData::new(bytes);let start=Instant::now();let module=Module::new(&engine,&data.wasm()[..])?;
    report["module_compilations"]=json!(1);report["compile_elapsed_ms"]=json!(start.elapsed().as_millis());
    let nonce=input.nonce.clone();let mut actor=db::Actor::start(input).map_err(|s|anyhow::anyhow!(s))?;
    let rpc=rpc_fs::RpcFs::new(nonce,actor.dispatch.clone());report["phase"]=json!("cgi_execution");report["guest_execution_started"]=json!(true);
    let result=execute(&engine,module,&data,php,wp,context,&rpc,&mut report["cgi_capture"]);
    report["rpc"]=rpc.finish();report["db_actor_joined"]=json!(actor.finish());
    {let state=actor.observation.lock().map_err(|_|anyhow::anyhow!("actor_observation_poisoned"))?;
        report["operations"]=json!(state.operations);report["physical_connection_created"]=json!(state.connection_created);
        report["physical_disconnect_attempted"]=json!(state.disconnect_attempted);report["physical_disconnect_completed"]=json!(state.disconnect_completed);
        report["db_actor_failure"]=json!(state.failure);report["db_actor_exited"]=json!(state.exited);}
    if let Ok(value)=&result {report["php_diagnostic"]=diagnostic(value,&native);}
    let after=(||->Result<()> {
        let _=load_module(&root,report)?;let _=load_sources(&root,report)?;let _=references(&root,report)?;
        ensure!(report["module"]==module_before && report["build_receipt"]==receipt_before
            && report["prepared"]==prepared_before && report["native_oracles"]==native_before,"source_or_module_changed");Ok(())
    })();report["source_rechecked_after_execution"]=json!(after.is_ok());after?;
    let actual=result?;
    ensure!(report["rpc"]["finished"]==true && report["rpc"]["failure"].is_null() && report["rpc"]["channel_open"]==false
        && report["rpc"]["pending_bytes"]==0 && report["rpc"]["unread_response_bytes"]==0 && report["rpc"]["response_pending"]==false
        && report["physical_connection_created"]==true && report["physical_disconnect_completed"]==true
        && report["db_actor_joined"]==true && report["db_actor_exited"]==true && report["db_actor_failure"].is_null(),"owned_rpc_completion");
    let (safe,backend)=public_php(&actual,&native,&prepared).context("whole_utf8_native_contract")?;
    report["php_result"]=safe;report["native_backend_match"]=json!(backend);report["phase"]=json!("full_utf8_verified");Ok(())
}
fn secret_present(wire:&[u8],secrets:&[String])->bool {
    secrets.iter().filter(|s|!s.is_empty()).any(|secret| {
        let encoded=db::hex(secret.as_bytes());
        wire.windows(secret.len()).any(|w|w==secret.as_bytes()) || wire.windows(encoded.len()).any(|w|w==encoded.as_bytes())
    })
}
fn main() {
    let mut report=json!({"schema":"recastpress.php-wasix-wpdb-utf8-observation.v0.5","passed":false,"outcome":"fixed_wasix_wpdb_utf8_failed",
        "mode":"read","profile":"utf8","phase":"input","operations":[],"cgi_capture":{},"php_result":null,
        "physical_connection_created":false,"physical_disconnect_completed":false,"db_actor_joined":false,"full_wordpress_bootstrap":false,
        "guest_execution_started":false,"fresh_runtime":true,"module_compilations":0,"guest_networking":false,"host_environment_forwarded":false,
        "host_home_mapped":false,"db_credentials_supplied_to_guest":false,"host_salts_supplied_to_guest":false,"native_php_fallback":false,
        "product_runtime":false,"standard_wpdb_compatibility_proven":false,"raw_error_bytes_preserved":false,
        "server_error_string_lossy_gap_unresolved":true,"memory_cpu_limits_verified_for_this_integration":false,
        "managed_access_exercised":false,"outer_owned_process_deadline_required":true});
    let mut secrets=vec![];
    let result=(||->Result<()> {
        ensure!(cfg!(target_os="linux"),"actual_php_wasm_linux_only");ensure!(std::env::args_os().count()==1,"closed_arguments");
        let mut wire=vec![];std::io::stdin().take(20*1024+1).read_to_end(&mut wire)?;
        let input=db::parse_input(&wire).map_err(|s|anyhow::anyhow!(s))?;secrets=input.secrets();probe(input,&mut report)
    })();
    if result.is_ok(){report["passed"]=json!(true);report["outcome"]=json!("fixed_wasix_wpdb_utf8_passed");}
    else {report["error"]=json!("fixed_utf8_probe_failed");}
    let mut wire=serde_json::to_vec(&report).expect("finite_public_report");
    let guarded=wire.len()>1024*1024 || secret_present(&wire,&secrets);
    if guarded {
        wire=br#"{"schema":"recastpress.php-wasix-wpdb-utf8-observation.v0.5","passed":false,"error":"public_secret_or_quota_guard"}"#.to_vec();
    }
    let mut stdout=std::io::stdout().lock();
    if stdout.write_all(&wire).and_then(|_|stdout.write_all(b"\n")).and_then(|_|stdout.flush()).is_err(){std::process::exit(2);}
    if result.is_err()||guarded{std::process::exit(1);}
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input()->db::Input {db::parse_input(&serde_json::to_vec(&json!({"schema":"recastpress.php-wasix-wp-bootstrap-input.v0.5","mode":"read",
        "host":"127.0.0.1","port":3306,"database":"rp_wpdb_probe","user":"rp_wpdb_probe","password":"a".repeat(48),"nonce":"b".repeat(48),
        "salts":{"AUTH_KEY":"c".repeat(48),"SECURE_AUTH_KEY":"c".repeat(48),"LOGGED_IN_KEY":"c".repeat(48),"NONCE_KEY":"c".repeat(48),
            "AUTH_SALT":"c".repeat(48),"SECURE_AUTH_SALT":"c".repeat(48),"LOGGED_IN_SALT":"c".repeat(48),"NONCE_SALT":"c".repeat(48)}})).unwrap()).unwrap()}
    #[test]
    fn context_contains_only_schema_nonce_and_environment_has_no_host_secrets() {
        let c=context(&input());assert_eq!(c.as_object().unwrap().len(),2);assert_eq!(c["nonce"],"b".repeat(48));
        let wire=serde_json::to_string(&c).unwrap();assert!(!wire.contains(&"a".repeat(48)));assert!(!wire.contains(&"c".repeat(48)));
        let env:BTreeMap<_,_>=environment().into_iter().collect();assert_eq!(env["CONTENT_LENGTH"],"0");assert_eq!(env["QUERY_STRING"],"rp_request=1");
        assert!(!env.contains_key("CONTENT_TYPE"));assert!(!env.keys().any(|k|k.starts_with("RP_")));
    }
    #[test]
    fn all_case_state_query_and_utf8_bytes_must_match_without_normalization() {
        let mut expected=json!({});for &key in COMPARE {expected[key]=json!("fixed");}
        expected["observations"]=json!([{"case":"composed","value_hex":"c3a9"}]);assert!(matches_native(&expected,&expected));
        let mut different=expected.clone();different["observations"][0]["value_hex"]=json!("65cc81");assert!(!matches_native(&different,&expected));
        different=expected.clone();different["query_trace_hex"]=json!([]);assert!(!matches_native(&different,&expected));
    }
    #[test]
    fn mismatch_diagnostics_expose_only_complete_known_case_ids_counts_and_digests() {
        let known=json!({"case":"prepare_bmp","returned":{"bytes_hex":"e697a5"}});
        let native=vec![Native{backend:"mysql",value:json!({"observations":[known.clone()]})}];
        let report=diagnostic(&json!({"passed":false,"observations":[known,{"case":"PRIVATE_CASE","secret":"PRIVATE_BODY"}],"query_trace_hex":["PRIVATE_SQL"]}),&native);
        assert_eq!(report["matched_case_ids"],json!(["prepare_bmp"]));assert_eq!(report["actual_observation_count"],2);
        let wire=report.to_string();for secret in ["PRIVATE_CASE","PRIVATE_BODY","PRIVATE_SQL"] {assert!(!wire.contains(secret));}
    }
    #[test]
    fn transport_statistics_reject_private_shapes_and_copy_only_finite_counters() {
        let mut value=json!({"transport":{"transport_closed":true,"max_requests":4096,"kind":"file-channel","channel_guest_path":"/rp-wpdb/channel",
            "requests":1,"request_bytes":2,"reply_bytes":3,"counts":{"hello":1,"query":0,"escape":0,"close":1},"private":"PRIVATE"},"internal_setup_queries":4});
        assert!(!stats_projection(&value).unwrap().to_string().contains("PRIVATE"));value["transport"]["requests"]=json!("PRIVATE");assert!(stats_projection(&value).is_none());
    }
    #[test]
    fn outer_json_cgi_allows_only_one_known_content_type_and_optional_powered_by() {
        for header in ["Content-Type: application/json\r\n", "Content-Type: application/json; charset=UTF-8\r\n",
            "X-Powered-By: PHP/8.5.12\r\nContent-Type: application/json\r\n"] {
            let wire=format!("{header}\r\n{{}}\n");assert!(fixed_json_cgi_headers(&cgi_response::parse(wire.as_bytes()).unwrap()));
        }
    }
    #[test]
    fn missing_wrong_status_or_unknown_outer_header_cannot_pass_as_json() {
        for wire in ["\r\n{}\n", "Status: 404 Not Found\r\nContent-Type: application/json\r\n\r\n{}\n",
            "Content-Type: text/plain\r\n\r\n{}\n", "Content-Type: application/json\r\nSet-Cookie: private=1\r\n\r\n{}\n",
            "X-Powered-By: PHP/9.0\r\nContent-Type: application/json\r\n\r\n{}\n"] {
            assert!(!cgi_response::parse(wire.as_bytes()).is_ok_and(|frame|fixed_json_cgi_headers(&frame)));
        }
    }
    #[test]
    fn duplicate_json_or_powered_by_headers_remain_rejected() {
        for header in ["Content-Type: application/json\r\nContent-Type: application/json\r\n",
            "X-Powered-By: PHP/8.5.12\r\nX-Powered-By: PHP/8.5.12\r\nContent-Type: application/json\r\n"] {
            let wire=format!("{header}\r\n{{}}\n");assert!(!cgi_response::parse(wire.as_bytes()).is_ok_and(|frame|fixed_json_cgi_headers(&frame)));
        }
    }
    #[test]
    fn raw_and_hex_host_secrets_are_both_refused_before_output() {
        let secrets=vec!["PRIVATE_VALUE".to_owned()];
        assert!(secret_present(b"prefix_PRIVATE_VALUE_suffix",&secrets));
        assert!(secret_present(db::hex(b"PRIVATE_VALUE").as_bytes(),&secrets));
        assert!(!secret_present(b"known-public-observation",&secrets));
    }
}
