#![forbid(unsafe_code)]
#[allow(dead_code)]
#[path = "../../php-wasix-wpdb-probe/src/capture.rs"]
mod bounded_pipe;
mod catalog;
mod trace_fs;
use anyhow::{Result, ensure};
use recastpress_wp_source_fs_probe::SourceFs;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::io::AsyncReadExt;
use wasmer::{Engine, Module};
use wasmer_wasix::{
    Pipe, PluggableRuntime, UnsupportedVirtualNetworking,
    runners::wasi::{RuntimeOrEngine, WasiRunner},
    runtime::{module_cache::HashedModuleData, task_manager::tokio::TokioTaskManager},
    virtual_fs::FileSystem,
};
const WAT: &str = include_str!("../fixtures/source.wat");
const SMALL: &[u8] = b"<?php\n// small immutable WordPress-like source\n";
const OUTPUT: usize = 65 * 1024;
const WORDS: [&str; 24] = [
    "prestat_errno",
    "preopen_name_bytes",
    "directory_open_errno",
    "directory_fd",
    "readdir_errno",
    "readdir_bytes",
    "file_open_errno",
    "file_fd",
    "prefix_read_errno",
    "prefix_bytes",
    "total_read_bytes",
    "drain_errno",
    "eof_observed",
    "readonly_fd_write_errno",
    "file_close_errno",
    "closed_fd_read_errno",
    "write_open_errno",
    "mkdir_errno",
    "unlink_errno",
    "rename_errno",
    "directory_close_errno",
    "drain_iterations",
    "prestat_name_errno",
    "unexpected_write_fd_close_errno",
];
fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn small_tree() -> SourceFs {
    SourceFs::new(
        BTreeMap::from([
            (
                "wp-includes/class-wpdb.php".into(),
                Arc::<[u8]>::from(SMALL),
            ),
            (
                "wp-includes/nested/child.php".into(),
                Arc::<[u8]>::from(&b"<?php\n"[..]),
            ),
        ]),
        ["wp-includes", "wp-includes/empty", "wp-includes/nested"]
            .into_iter()
            .map(str::to_owned)
            .collect(),
    )
    .unwrap()
}
fn collect(mut pipe: Pipe) -> (Vec<u8>, bool) {
    let end = Instant::now() + Duration::from_secs(2);
    let mut data = Vec::new();
    let mut block = [0; 4096];
    loop {
        match pipe.try_read(&mut block) {
            Some(0) => return (data, true),
            Some(n) => {
                let available = OUTPUT.saturating_sub(data.len());
                data.extend_from_slice(&block[..n.min(available)]);
                if n > available {
                    return (data, false);
                }
            }
            None if Instant::now() < end => std::thread::sleep(Duration::from_millis(1)),
            None => return (data, false),
        }
    }
}
#[derive(Debug, PartialEq, Eq)]
struct Observation {
    words: [u32; 24],
    preopen: Vec<u8>,
    entries: Vec<(Vec<u8>, u8, u64)>,
    prefix: Vec<u8>,
}
fn parse(raw: &[u8]) -> Result<Observation> {
    ensure!(raw.len() >= 104 && &raw[..8] == b"RPFSW01\n", "wire_header");
    let mut words = [0; 24];
    for (index, slot) in words.iter_mut().enumerate() {
        *slot = u32::from_le_bytes(raw[8 + 4 * index..12 + 4 * index].try_into().unwrap());
    }
    ensure!(
        words[1] <= 128 && words[5] <= 32768 && words[9] <= 32,
        "wire_lengths"
    );
    ensure!(
        raw.len() == 104 + words[1] as usize + words[5] as usize + words[9] as usize,
        "wire_complete_frame"
    );
    let preopen = raw[104..104 + words[1] as usize].to_vec();
    let start = 104 + words[1] as usize;
    let end = start + words[5] as usize;
    let mut offset = start;
    let mut entries = vec![];
    while offset < end {
        ensure!(end - offset >= 24, "dirent_header");
        let n = u32::from_le_bytes(raw[offset + 16..offset + 20].try_into().unwrap()) as usize;
        ensure!(n > 0 && n <= 4096 && n <= end - offset - 24, "dirent_name");
        let cookie = u64::from_le_bytes(raw[offset..offset + 8].try_into().unwrap());
        entries.push((
            raw[offset + 24..offset + 24 + n].to_vec(),
            raw[offset + 20],
            cookie,
        ));
        offset += 24 + n;
    }
    Ok(Observation {
        words,
        preopen,
        entries,
        prefix: raw[end..].to_vec(),
    })
}
fn validate(observation: &Observation, expected: &[(Vec<u8>, u8)], data: &[u8]) -> Result<()> {
    let w = &observation.words;
    for index in [0, 2, 4, 6, 8, 11, 14, 20, 22] {
        ensure!(w[index] == 0, "successful_syscall_errno");
    }
    ensure!(
        observation.preopen == b"/" || observation.preopen == b".",
        "fixed_root_preopen_name"
    );
    ensure!(
        w[3] > 3 && w[7] > 3 && w[3] != w[7],
        "distinct_owned_directory_and_file_fds"
    );
    ensure!(
        w[5] < 32768
            && w[9] as usize == data.len().min(32)
            && w[10] as usize == data.len()
            && w[12] == 1
            && (1..=64).contains(&w[21]),
        "read_prefix_complete_eof"
    );
    ensure!(
        observation.prefix == data[..data.len().min(32)],
        "actual_prefix_bytes"
    );
    ensure!(w[15] == 8, "closed_fd_must_be_badf");
    for index in [13, 16, 17, 18, 19] {
        ensure!(
            w[index] != 0 && w[index] != u32::MAX,
            "mutation_must_be_rejected"
        );
    }
    ensure!(w[23] == u32::MAX, "write_open_must_not_create_handle");
    ensure!(
        observation
            .entries
            .iter()
            .map(|(name, kind, _)| (name.clone(), *kind))
            .collect::<Vec<_>>()
            == expected,
        "actual_sorted_directory_names_and_types"
    );
    ensure!(
        observation
            .entries
            .iter()
            .enumerate()
            .all(|(index, (_, _, cookie))| *cookie == index as u64 + 1),
        "actual_directory_cookies"
    );
    Ok(())
}
fn expected(fs: &SourceFs) -> Result<Vec<(Vec<u8>, u8)>> {
    let mut entries = vec![(b".".to_vec(), 3), (b"..".to_vec(), 3)];
    for item in fs.read_dir(Path::new("/wp-includes"))? {
        let item = item?;
        let name = item
            .file_name()
            .into_string()
            .map_err(|_| anyhow::anyhow!("expected_name_utf8"))?;
        entries.push((
            name.into_bytes(),
            if item.metadata()?.is_dir() { 3 } else { 4 },
        ));
    }
    entries.sort_by(|left, right| left.0.cmp(&right.0));
    Ok(entries)
}
fn execute(
    engine: &Engine,
    module: Module,
    data: &HashedModuleData,
    fs: SourceFs,
    record: &mut Value,
) -> Result<()> {
    let expected = expected(&fs)?;
    let oracle_runtime = tokio::runtime::Builder::new_current_thread().build()?;
    let expected_body = oracle_runtime.block_on(async {
        let mut f = fs
            .new_open_options()
            .read(true)
            .open("/wp-includes/class-wpdb.php")?;
        let mut bytes = vec![];
        f.read_to_end(&mut bytes).await?;
        Ok::<_, anyhow::Error>(bytes)
    })?;
    drop(oracle_runtime);
    let before = (fs.file_count(), fs.directory_count(), fs.total_bytes());
    let traced = trace_fs::TraceFs::new(fs.clone());
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let guard = runtime.enter();
    let (stdin_send, stdin_read) = Pipe::channel();
    drop(stdin_send);
    let (out_send, out_read) = Pipe::channel();
    let (err_send, err_read) = Pipe::channel();
    let out_over = Arc::new(AtomicBool::new(false));
    let err_over = Arc::new(AtomicBool::new(false));
    let outcome = (|| -> Result<()> {
        let tasks = TokioTaskManager::new(runtime.handle().clone());
        let mut implementation = PluggableRuntime::new(Arc::new(tasks));
        implementation.set_engine(engine.clone());
        implementation.set_networking_implementation(UnsupportedVirtualNetworking::default());
        implementation.http_client = None;
        let mut runner = WasiRunner::new();
        runner
            .with_forward_host_env(false)
            .with_home_mapped(false)
            .with_asynchronous_threading(true)
            .with_current_dir("/")
            .with_mount("/source".into(), Arc::new(traced.clone()))
            .with_stdin(Box::new(stdin_read))
            .with_stdout(Box::new(bounded_pipe::LimitedPipe::new(
                out_send,
                out_over.clone(),
            )))
            .with_stderr(Box::new(bounded_pipe::LimitedPipe::new(
                err_send,
                err_over.clone(),
            )));
        let caps = runner.capabilities_mut();
        caps.insecure_allow_all = false;
        caps.threading.max_threads = Some(1);
        caps.threading.enable_deep_sleep = false;
        record["invocation_attempted"] = json!(true);
        let start = Instant::now();
        let result = runner.run_wasm(
            RuntimeOrEngine::Runtime(Arc::new(implementation)),
            "source-fs-wat",
            module,
            *data.hash(),
        );
        record["execution_elapsed_ms"] = json!(start.elapsed().as_millis());
        drop(runner);
        result.map_err(|_| anyhow::anyhow!("wasi_run_failed"))
    })();
    record["runtime_run_returned_success"] = json!(outcome.is_ok());
    drop(guard);
    runtime.shutdown_timeout(Duration::from_secs(2));
    record["runtime_shutdown_returned"] = json!(true);
    record["runtime_shutdown_wait_ms"] = json!(2000);
    let (stdout, out_eof) = collect(out_read);
    let (stderr, err_eof) = collect(err_read);
    record["stdout_hex"] = json!(hex(&stdout));
    record["stdout_sha256"] = json!(sha(&stdout));
    record["stdout_bytes"] = json!(stdout.len());
    record["stdout_eof"] = json!(out_eof);
    record["stderr_hex"] = json!(hex(&stderr));
    record["stderr_bytes"] = json!(stderr.len());
    record["stderr_eof"] = json!(err_eof);
    record["stdout_overflow"] = json!(out_over.load(Ordering::Relaxed));
    record["stderr_overflow"] = json!(err_over.load(Ordering::Relaxed));
    if let Ok(observation) = parse(&stdout) {
        record["wasi_syscall_observation_emitted"] = json!(true);
        record["syscalls"] = Value::Object(
            WORDS
                .iter()
                .zip(observation.words)
                .map(|(key, value)| (key.to_string(), json!(value)))
                .collect(),
        );
        record["preopen_name_hex"] = json!(hex(&observation.preopen));
        record["prefix_hex"] = json!(hex(&observation.prefix));
        record["directory_entries"]=json!(observation.entries.iter().map(|(name,kind,cookie)|json!({"name_hex":hex(name),"file_type":kind,"next_cookie":cookie})).collect::<Vec<_>>());
    }
    record["source_catalog_counts_unchanged"] =
        json!(before == (fs.file_count(), fs.directory_count(), fs.total_bytes()));
    record["backing_calls"] = json!(traced.snapshot());
    outcome?;
    ensure!(
        out_eof
            && err_eof
            && !out_over.load(Ordering::Relaxed)
            && !err_over.load(Ordering::Relaxed)
            && stderr.is_empty(),
        "bounded_complete_capture"
    );
    let observation = parse(&stdout)?;
    validate(&observation, &expected, &expected_body)?;
    record["passed"] = json!(true);
    Ok(())
}
fn probe(record: &mut Value) -> Result<()> {
    ensure!(std::env::args_os().len() == 1, "closed_arguments");
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()?;
    let bytes = wat::parse_str(WAT)?;
    record["module"] = json!({"bytes":bytes.len(),"sha256":sha(&bytes),"source_wat_sha256":sha(WAT.as_bytes()),"exception_handling_used":false,"max_memory_pages":2});
    let engine: Engine = wasmer_compiler_cranelift::Cranelift::default().into();
    let data = HashedModuleData::new(bytes);
    record["module_compilations"] = json!(1);
    let module = Module::new(&engine, &data.wasm()[..])?;
    record["small"] = json!({"passed":false,"wasi_syscall_observation_emitted":false});
    execute(
        &engine,
        module.clone(),
        &data,
        small_tree(),
        &mut record["small"],
    )?;
    let full = catalog::load(&root, &mut record["catalog_source"])
        .map_err(|reason| anyhow::anyhow!(reason))?;
    record["full"] = json!({"passed":false,"wasi_syscall_observation_emitted":false});
    execute(&engine, module, &data, full, &mut record["full"])?;
    Ok(())
}
fn main() {
    let mut record = json!({"schema":"recastpress.wp-source-wasi-observation.v0.5","passed":false,"outcome":"fixed_source_wasi_failed",
        "backing_path_adapter":"windows-separators-to-posix-then-frozen-source-validation",
        "windows_backing_separator_conversion_enabled":cfg!(windows),"guest_backslash_rejection_verified":false,
        "module_compilations":0,"catalog_source":{},"wasi_executed":false,"php_executed":false,"database_connected":false,
        "networking_enabled":false,"host_environment_forwarded":false,"host_home_mapped":false,"product_runtime_selected":false,
        "native_metadata_compatibility_verified":false,"all_runtime_tasks_independently_enumerated":false});
    let result = probe(&mut record);
    record["wasi_executed"] = json!(
        ["small", "full"]
            .iter()
            .any(|name| record[name]["wasi_syscall_observation_emitted"] == true)
    );
    if result.is_ok() {
        record["passed"] = json!(true);
        record["outcome"] = json!("fixed_source_wasi_small_and_complete_catalog_passed");
    } else {
        record["failure"] = json!("source_wasi_contract_failed");
        if let Err(error) = &result {
            let message = error.to_string();
            if message.len() < 100 && message.bytes().all(|b| b.is_ascii_lowercase() || b == b'_') {
                record["failure_reason"] = json!(message);
            }
        }
    }
    println!("{record}");
    if result.is_err() {
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixed_wat_parses_without_eh_and_has_small_bounded_memory() {
        let bytes = wat::parse_str(WAT).unwrap();
        assert!(bytes.starts_with(b"\0asm\x01\0\0\0"));
        assert!(WAT.contains("(memory (export \"memory\") 2 2)"));
        assert!(!WAT.contains("throw"));
        assert!(!WAT.contains("exception"));
    }
    #[test]
    fn binary_observer_rejects_partial_and_oversized_frames() {
        assert!(parse(b"RPFSW01\n").is_err());
        let mut wire = vec![0; 104];
        wire[..8].copy_from_slice(b"RPFSW01\n");
        wire[12..16].copy_from_slice(&129u32.to_le_bytes());
        assert!(parse(&wire).is_err());
        wire[12..16].copy_from_slice(&0u32.to_le_bytes());
        wire.push(0);
        assert!(parse(&wire).is_err());
    }
}
