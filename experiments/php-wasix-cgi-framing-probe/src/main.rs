#![forbid(unsafe_code)]
#[path = "../../php-wasix-wp-request-probe/src/access_fs.rs"]
mod access_fs;
#[path = "../../php-wasix-wp-request-probe/src/capture.rs"]
mod capture;
#[path = "../../php-wasix-wp-request-probe/src/warning_diagnostic.rs"]
mod warning_diagnostic;
#[path = "../../php-wasi-host-probe/src/cgi_response.rs"]
mod cgi_response;
#[path = "../../php-wasix-host-probe/src/fiber_adapter.rs"]
mod fiber_adapter;
#[allow(dead_code)]
#[path = "../../php-wasix-wpdb-probe/src/inputs.rs"]
mod inputs;
mod db { pub fn sha(bytes:&[u8])->String { use sha2::{Digest,Sha256};format!("{:x}",Sha256::digest(bytes)) } }
use anyhow::{Context,Result,ensure};
use recastpress_wp_source_fs_probe::SourceFs;
use serde_json::{Value,json};
use std::{collections::{BTreeMap,BTreeSet},fs::File,io::{Read,Write},path::{Path,Component},sync::{Arc,atomic::{AtomicBool,Ordering}},time::{Duration,Instant}};
use wasmer::{Engine,Module};
use wasmer_wasix::{Pipe,PluggableRuntime,UnsupportedVirtualNetworking,runners::wasi::{RuntimeOrEngine,WasiRunner},runtime::{module_cache::HashedModuleData,task_manager::tokio::TokioTaskManager}};
const MODULE:&str="target/php-wasix-managed-access-probe/php-fiber-cgi.wasm";
const RECEIPT:&str="experiments/php-wasix-managed-access-probe/evidence/build.json";
const VARIANT:&str="nts-fresh-wasi-zend-fibers-managed-access-v1";
const PREPARED:&str="target/php-wasix-cgi-framing-probe/prepared.json";
const MOUNT:&str="/experiments/php-wasix-wp-request-probe/fixtures";
const NATIVE_PROOF:&str="experiments/php-cgi-framing-probe/evidence/windows-native.json";
const NATIVE_SHA:&str="cb14bab903650d7ab41dac3f65055851cc19c8a22d8ca096e8c83d0bc4b17ab9";
const FIXTURES:&[(&str,&[u8])]=&[
    ("common.php",include_bytes!("../../php-cgi-framing-probe/fixtures/common.php")),
    ("case-large-buffered-keep.php",include_bytes!("../../php-cgi-framing-probe/fixtures/case-large-buffered-keep.php")),
    ("case-large-buffered-remove.php",include_bytes!("../../php-cgi-framing-probe/fixtures/case-large-buffered-remove.php")),
    ("case-large-plain-keep.php",include_bytes!("../../php-cgi-framing-probe/fixtures/case-large-plain-keep.php")),
    ("case-large-plain-remove.php",include_bytes!("../../php-cgi-framing-probe/fixtures/case-large-plain-remove.php")),
    ("case-small-buffered-keep.php",include_bytes!("../../php-cgi-framing-probe/fixtures/case-small-buffered-keep.php")),
    ("case-small-buffered-remove.php",include_bytes!("../../php-cgi-framing-probe/fixtures/case-small-buffered-remove.php")),
    ("case-small-plain-keep.php",include_bytes!("../../php-cgi-framing-probe/fixtures/case-small-plain-keep.php")),
    ("case-small-plain-remove.php",include_bytes!("../../php-cgi-framing-probe/fixtures/case-small-plain-remove.php")),
];
fn fixed(root: &Path, name: &str, limit: u64) -> Result<Vec<u8>> {
    ensure!(
        !Path::new(name).is_absolute()
            && Path::new(name)
                .components()
                .all(|c| matches!(c, Component::Normal(_))),
        "fixed_relative_path"
    );
    let path = root.join(name);
    ensure!(path.canonicalize()? == path, "fixed_file_alias");
    let f = File::open(path)?;
    let m = f.metadata()?;
    ensure!(m.is_file() && m.len() <= limit, "fixed_file_quota");
    let mut b = vec![];
    f.take(limit + 1).read_to_end(&mut b)?;
    ensure!(b.len() as u64 == m.len(), "fixed_file_changed");
    Ok(b)
}
fn load_module(root: &Path, report: &mut Value) -> Result<Vec<u8>> {
    let wire = fixed(root, RECEIPT, 2 * 1024 * 1024)?;
    let v: Value = serde_json::from_slice(&wire)?;
    ensure!(
        v["schema"] == "recastpress.php-wasix-managed-access-build.v0.5"
            && v["variant"] == VARIANT
            && v["phase"] == "build_complete"
            && v["outcome"] == "managed_access_candidate_built_not_executed"
            && v["guest_executed"] == false,
        "managed_build_profile"
    );
    for (p, expected) in inputs::MANAGED_SOURCES {
        let b = fixed(root, p, 256 * 1024)?;
        ensure!(b == expected, "compiled_managed_source_changed");
        if !p.contains("/evidence/native-") {
            ensure!(v["input_files"][p] == db::sha(&b), "managed_input_binding");
        }
    }
    let patches = v["source_patches"].as_array().context("source_patches")?;
    ensure!(
        patches.len() == 3 && patches.iter().all(|p| p["applied"] == true),
        "three_patch_chain"
    );
    let b = fixed(root, MODULE, 128 * 1024 * 1024)?;
    ensure!(
        v["php_wasm"]["path"] == MODULE
            && v["php_wasm"]["wasm_header_verified"] == true
            && v["php_wasm"]["executed"] == false
            && b.starts_with(b"\0asm\x01\0\0\0")
            && v["php_wasm"]["bytes"].as_u64() == Some(b.len() as u64)
            && v["php_wasm"]["sha256"] == db::sha(&b),
        "managed_module_identity"
    );
    report["module"] = json!({"path":MODULE,"bytes":b.len(),"sha256":db::sha(&b),"verified":true});
    report["build_receipt"] = json!({"path":RECEIPT,"sha256":db::sha(&wire),"verified":true,"full_chain_verified_by_host":false});
    report["fiber_adapter"] = fiber_adapter::inspect(&b)?;
    Ok(b)
}
fn unhex(value:&str)->Result<Vec<u8>> {
    ensure!(value.len()<=6*1024*1024 && value.len()%2==0,"expected_wire_hex_size");
    let digit=|b:u8|->Result<u8>{match b {b'0'..=b'9'=>Ok(b-b'0'),b'a'..=b'f'=>Ok(b-b'a'+10),_=>anyhow::bail!("expected_wire_hex")}};
    value.as_bytes().chunks_exact(2).map(|p|Ok(digit(p[0])?*16+digit(p[1])?)).collect()
}
fn identity(bytes:&[u8])->Value {json!({"bytes":bytes.len(),"sha256":db::sha(bytes)})}
fn sources(root:&Path,prepared:&Value)->Result<()> {
    let values=prepared["source_inputs"].as_object().context("source_input_map")?;
    ensure!(!values.is_empty() && values.len()<=512,"source_input_count");
    for (path,hash) in values {ensure!(hash==&db::sha(&fixed(root,path,4*1024*1024)?),"source_input_changed");}
    let original=fixed(root,NATIVE_PROOF,1024*1024)?;
    ensure!(db::sha(&original)==NATIVE_SHA && prepared["native_proof"]["sha256"]==NATIVE_SHA,"native_proof_identity");
    Ok(())
}
fn fixtures(root:&Path,prepared:&Value)->Result<SourceFs> {
    let rows=prepared["fixture_files"].as_object().context("fixture_inventory")?;
    ensure!(rows.len()==9,"nine_php_fixtures");
    let mut files=BTreeMap::new();
    for (name,compiled) in FIXTURES {
        let path=format!("experiments/php-cgi-framing-probe/fixtures/{name}");
        let actual=fixed(root,&path,1024*1024)?;
        ensure!(actual==*compiled && rows[&path]==identity(&actual),"original_php_fixture_identity");
        files.insert((*name).to_owned(),Arc::<[u8]>::from(actual));
    }
    SourceFs::new(files,BTreeSet::new()).map_err(|_|anyhow::anyhow!("finite_fixture_tree"))
}
const CASES:[&str;8]=["small-plain-keep","small-plain-remove","small-buffered-keep","small-buffered-remove",
    "large-plain-keep","large-plain-remove","large-buffered-keep","large-buffered-remove"];
fn environment(case:&str)->Result<Vec<(String,String)>> {
    ensure!(CASES.contains(&case),"closed_case");
    Ok([("SCRIPT_FILENAME",format!("{MOUNT}/case-{case}.php")),("SCRIPT_NAME","/__rp_framing.php".into()),
        ("DOCUMENT_ROOT",MOUNT.into()),("REQUEST_METHOD","GET".into()),("QUERY_STRING","rp_request=1".into()),
        ("REQUEST_URI","/__rp_framing.php?rp_request=1".into()),("CONTENT_LENGTH","0".into()),
        ("REDIRECT_STATUS","200".into()),("GATEWAY_INTERFACE","CGI/1.1".into()),("SERVER_PROTOCOL","HTTP/1.1".into()),
        ("SERVER_SOFTWARE","recastpress-cgi-framing-probe".into()),("SERVER_NAME","rp-fixture.invalid".into()),
        ("SERVER_PORT","80".into()),("HTTP_HOST","rp-fixture.invalid".into()),("REMOTE_ADDR","127.0.0.1".into()),
        ("HTTPS","off".into()),("REQUEST_SCHEME","http".into())].into_iter().map(|(k,v)|(k.to_owned(),v)).collect())
}
fn observation(actual:&[u8],expected:&[u8])->Value {
    let exact=actual==expected;
    let prefix=actual.iter().zip(expected).take_while(|(a,b)|a==b).count();
    let suffix=actual.iter().rev().zip(expected.iter().rev()).take_while(|(a,b)|a==b).count();
    let classification=if exact {"exact_native_frame"}else if actual.is_empty(){"empty"}
        else if actual.starts_with(b"Content-Type: application/json")||actual.starts_with(b"X-Powered-By: PHP/8.5.12"){"known_cgi_header_prefix"}
        else if actual.starts_with(b"{\"schema\":\"recastpress.php-cgi-framing-php-observation.v0.5\""){"known_json_prefix"}else{"other"};
    // Only an entire byte-identical fixed native JSON body may delimit this
    // diagnostic prefix. Never search for arbitrary JSON or publish its bytes.
    let positions=cgi_response::parse(expected).ok().map(|frame|frame.body)
        .filter(|body|body.starts_with(b"{\"schema\":\"recastpress.php-cgi-framing-php-observation.v0.5\""))
        .map(|body|actual.windows(body.len()).enumerate().filter_map(|(i,part)|(part==body.as_slice()).then_some(i)).take(2).collect::<Vec<_>>())
        .unwrap_or_default();
    let offset=(positions.len()==1).then(||positions[0]);
    let prefix_end=offset.unwrap_or(actual.len().min(4096));
    let known=[warning_diagnostic::KnownSource{
        path:"/experiments/php-wasix-wp-request-probe/fixtures/common.php",
        label:warning_diagnostic::SourceLabel::FramingCommon,
        line_count:include_str!("../../php-cgi-framing-probe/fixtures/common.php").lines().count(),
    }];
    let warnings=warning_diagnostic::classify(&actual[..prefix_end],&known);
    json!({"actual":identity(actual),"complete_cgi_wire_matches":exact,"classification":classification,
        "expected_prefix_match_bytes":prefix,"expected_suffix_match_bytes":suffix,
        "lf_bytes":actual.iter().filter(|&&b|b==b'\n').count(),"cr_bytes":actual.iter().filter(|&&b|b==b'\r').count(),
        "exact_expected_json_occurrences_capped_two":positions.len(),"exact_expected_json_offset":offset,
        "warning_diagnostic":warnings,
        "unknown_output_recorded":false})
}
fn run_case(engine:&Engine,module:Module,data:&HashedModuleData,php:SourceFs,case:&str,expected:&[u8],row:&mut Value)->Result<()> {
    let empty=||SourceFs::new(BTreeMap::new(),BTreeSet::new()).map_err(|_|anyhow::anyhow!("empty_finite_tree"));
    let access=access_fs::ManagedAccessFs::new(empty()?,php.clone(),empty()?)?;
    let runtime=tokio::runtime::Builder::new_multi_thread().enable_all().build()?;
    let guard=runtime.enter();
    let (stdin_send,stdin_read)=Pipe::channel();drop(stdin_send);
    let (out_send,out_read)=Pipe::channel();let (err_send,err_read)=Pipe::channel();
    let overflow=Arc::new(AtomicBool::new(false));let err_overflow=Arc::new(AtomicBool::new(false));
    let start=Instant::now();
    let result=(||->Result<()> {
        let mut implementation=PluggableRuntime::new(Arc::new(TokioTaskManager::new(runtime.handle().clone())));
        implementation.set_engine(engine.clone());
        implementation.set_networking_implementation(UnsupportedVirtualNetworking::default());implementation.http_client=None;
        let mut runner=WasiRunner::new();
        runner.with_args(["-n","-d","fiber.stack_size=2097152","-d","register_argc_argv=0","-d","allow_url_fopen=0","-d","disable_functions=mail"])
            .with_envs(environment(case)?).with_forward_host_env(false).with_home_mapped(false).with_asynchronous_threading(true)
            .with_current_dir("/").with_mount(MOUNT.into(),Arc::new(php))
            .with_mount("/rp-p0-fs".into(),Arc::new(access.clone()))
            .with_stdin(Box::new(stdin_read)).with_stdout(Box::new(capture::LimitedPipe::new(out_send,overflow.clone())))
            .with_stderr(Box::new(capture::LimitedPipe::new(err_send,err_overflow.clone())));
        let caps=runner.capabilities_mut();caps.insecure_allow_all=false;caps.threading.max_threads=Some(1);caps.threading.enable_deep_sleep=false;
        row["guest_execution_started"]=json!(true);
        let outcome=runner.run_wasm(RuntimeOrEngine::Runtime(Arc::new(implementation)),"php-framing",module,*data.hash());
        drop(runner);outcome.map_err(|_|anyhow::anyhow!("guest_execution_failed"))
    })();
    row["guest_exit_success"]=json!(result.is_ok());row["execution_elapsed_ms"]=json!(start.elapsed().as_millis());
    drop(guard);runtime.shutdown_timeout(Duration::from_secs(2));row["runtime_shutdown_returned"]=json!(true);
    let stdout=capture::collect_pipe(out_read);let stderr=capture::collect_pipe(err_read);
    row["stdout_eof"]=json!(stdout.is_ok());row["stderr_eof"]=json!(stderr.is_ok());
    row["stdout_overflow"]=json!(overflow.load(Ordering::Relaxed));row["stderr_overflow"]=json!(err_overflow.load(Ordering::Relaxed));
    if let Ok(actual)=&stdout {row["framing"]=observation(actual,expected);}
    if let Ok(actual)=&stderr {row["stderr"]=identity(actual);}
    let access_result=access.finish();
    if let Ok(snapshot)=access.snapshot(){row["managed_access"]=snapshot.report_value();}
    if let Ok(reasons)=access.invalid_reason_counts(){row["managed_access_invalid_reasons"]=reasons;}
    row["access_channel_finished"]=json!(access_result.is_ok());
    result?;access_result?;
    ensure!(!overflow.load(Ordering::Relaxed)&&!err_overflow.load(Ordering::Relaxed),"capture_overflow");
    ensure!(stderr?.is_empty(),"guest_stderr");
    ensure!(stdout?==expected,"native_cgi_wire_mismatch");
    row["passed"]=json!(true);Ok(())
}
fn probe(root:&Path,report:&mut Value)->Result<()> {
    let wire=fixed(root,PREPARED,3*1024*1024)?;
    let prepared:Value=serde_json::from_slice(&wire)?;
    ensure!(prepared["schema"]=="recastpress.php-wasix-cgi-framing-prepared.v0.5","prepared_schema");
    sources(root,&prepared)?;let php=fixtures(root,&prepared)?;
    let proof:Value=serde_json::from_slice(&fixed(root,NATIVE_PROOF,1024*1024)?)?;
    let rows=prepared["cases"].as_array().context("prepared_cases")?;ensure!(rows.len()==8,"eight_cases");
    report["prepared"]=json!({"path":PREPARED,"identity":identity(&wire)});
    report["source_inputs"]=prepared["source_inputs"].clone();report["native_proof"]=prepared["native_proof"].clone();
    let bytes=load_module(root,report)?;
    let module_identity=report["module"].clone();let receipt_identity=report["build_receipt"].clone();
    report["module_before"]=module_identity.clone();report["module_receipt_before"]=receipt_identity.clone();
    let engine:Engine=wasmer_compiler_cranelift::Cranelift::default().into();
    let data=HashedModuleData::new(bytes);let start=Instant::now();
    let module=Module::new(&engine,&data.wasm()[..])?;
    report["module_compilations"]=json!(1);report["compile_elapsed_ms"]=json!(start.elapsed().as_millis());
    let mut all=true;
    for (index,(case,source)) in CASES.iter().zip(rows).enumerate() {
        ensure!(source["case"]==*case && source["script"]==format!("case-{case}.php"),"closed_case_order");
        let expected=unhex(source["expected_cgi_wire_hex"].as_str().context("expected_wire")?)?;
        ensure!(identity(&expected)==source["expected_cgi_wire"] && identity(&expected)==proof["observations"][index]["stdout"],"native_expected_wire_binding");
        let mut row=json!({"case":case,"passed":false,"fresh_runtime":true,"stdin_bytes":0,"guest_networking":false,
            "host_environment_forwarded":false,"host_home_mapped":false,"native_php_fallback":false});
        let result=run_case(&engine,module.clone(),&data,php.clone(),case,&expected,&mut row);
        if result.is_err(){all=false;row["failure"]=json!("case_failed");}
        report["observations"].as_array_mut().context("observation_array")?.push(row);
        let after=(||->Result<()>{
            sources(root,&prepared)?;let _=fixtures(root,&prepared)?;let _=load_module(root,report)?;
            report["module_after"]=report["module"].clone();report["module_receipt_after"]=report["build_receipt"].clone();
            ensure!(report["module"]==module_identity && report["build_receipt"]==receipt_identity,"module_or_receipt_changed");Ok(())
        })();
        report["source_and_module_reverified_after_each"]=json!(after.is_ok());after?;
    }
    ensure!(fixed(root,PREPARED,3*1024*1024)?==wire,"prepared_changed");
    report["source_reverified_after"]=json!(true);
    ensure!(all,"eight_native_frames_required");Ok(())
}
fn main() {
    let mut report=json!({"schema":"recastpress.php-wasix-cgi-framing-check.v0.5","passed":false,"outcome":"wasix_cgi_framing_failed",
        "observations":[],"module_compilations":0,"source_reverified_after":false,"unknown_output_recorded":false,
        "wordpress_executed":false,"database_connected":false,"native_php_fallback":false,"network_calls_made":false,
        "product_runtime_selected":false,"actual_php_wasm_executed":false,"fresh_runtime_per_case":true,
        "all_physical_resources_reclaimed":false,"outer_owned_process_deadline_required":true});
    let result=(||->Result<()> {
        ensure!(cfg!(target_os="linux"),"actual_php_wasm_linux_only");
        ensure!(std::env::args_os().count()==1,"closed_arguments");
        let root=std::env::current_dir()?.canonicalize()?;
        probe(&root,&mut report)
    })();
    report["actual_php_wasm_executed"]=json!(report["observations"].as_array().is_some_and(|rows|
        rows.iter().any(|row|row["guest_execution_started"]==true)));
    if result.is_ok(){report["passed"]=json!(true);report["outcome"]=json!("wasix_all_eight_cgi_frames_exact");}
    else {report["failure"]=json!("fixed_probe_failed");}
    let wire=serde_json::to_vec(&report).expect("finite_report");
    if wire.len()>1024*1024 {std::process::exit(2);}
    let mut stdout=std::io::stdout().lock();
    if stdout.write_all(&wire).and_then(|_|stdout.write_all(b"\n")).and_then(|_|stdout.flush()).is_err(){std::process::exit(2);}
    if result.is_err(){std::process::exit(1);}
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn closed_cases_keep_native_query_and_no_private_environment() {
        for case in CASES {let values:BTreeMap<_,_>=environment(case).unwrap().into_iter().collect();
            assert_eq!(values["QUERY_STRING"],"rp_request=1");assert_eq!(values["CONTENT_LENGTH"],"0");
            assert_eq!(values["REQUEST_URI"],"/__rp_framing.php?rp_request=1");
            assert_eq!(values["SCRIPT_FILENAME"],format!("{MOUNT}/case-{case}.php"));
            assert!(!values.contains_key("PHPRC"));assert!(!values.contains_key("HTTP_COOKIE"));}
        assert!(environment("../private.php").is_err());assert!(environment("home").is_err());
    }
    #[test]
    fn unknown_bytes_are_only_counts_digests_and_fixed_labels() {
        let private=b"PRIVATE_BODY_AND_HEADERS";let value=observation(private,b"expected");
        assert_eq!(value["complete_cgi_wire_matches"],false);assert_eq!(value["classification"],"other");
        assert!(!value.to_string().contains("PRIVATE_BODY_AND_HEADERS"));
        assert_eq!(value["unknown_output_recorded"],false);
        let equal=observation(b"complete",b"complete");assert_eq!(equal["expected_prefix_match_bytes"],8);
        assert_eq!(equal["expected_suffix_match_bytes"],8);assert_eq!(equal["complete_cgi_wire_matches"],true);
    }
    #[test]
    fn exact_native_json_delimits_warning_prefix_and_duplicates_do_not() {
        let body=b"{\"schema\":\"recastpress.php-cgi-framing-php-observation.v0.5\",\"passed\":true}\n";
        let mut expected=b"Content-Type: application/json\r\n\r\n".to_vec();expected.extend_from_slice(body);
        let prefix=b"UNKNOWN_PRIVATE_PREFIX\n";let mut actual=prefix.to_vec();actual.extend_from_slice(body);
        let value=observation(&actual,&expected);
        assert_eq!(value["exact_expected_json_occurrences_capped_two"],1);
        assert_eq!(value["exact_expected_json_offset"],prefix.len());
        assert!(!value.to_string().contains("UNKNOWN_PRIVATE_PREFIX"));
        actual.extend_from_slice(body);let repeated=observation(&actual,&expected);
        assert_eq!(repeated["exact_expected_json_occurrences_capped_two"],2);
        assert!(repeated["exact_expected_json_offset"].is_null());
        let other=observation(b"{\"schema\":\"private\"}",&expected);
        assert_eq!(other["exact_expected_json_occurrences_capped_two"],0);
        assert!(other["exact_expected_json_offset"].is_null());
    }
    #[test]
    fn expected_wire_hex_is_bounded_and_lowercase_only() {
        assert_eq!(unhex("000aff").unwrap(),[0,10,255]);
        for value in ["A0","0","zz"] {assert!(unhex(value).is_err());}
        assert!(unhex(&"0".repeat(6*1024*1024+2)).is_err());
    }
}
