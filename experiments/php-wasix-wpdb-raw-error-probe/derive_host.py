"""Derive the fixed private raw ERR host from the accepted UTF-8 CGI host."""

import argparse
import hashlib
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
UTF8 = HERE.parent / 'php-wasix-wpdb-utf8-probe'
OLD_NAME = b'recastpress-php-wasix-wpdb-utf8-probe'
NAME = b'recastpress-php-wasix-wpdb-raw-error-probe'
PINS = {
    'src/main.rs': '89d8711453d5e37105365a8a6b4ba7360ae8bfbd37a81ee56847eacc7b117541',
    'Cargo.toml': 'e12d00f83144eb910e5caec2b8a3b8a6be36d6c9318f3d62b6a656fa086c1e55',
    'Cargo.lock': '8714ec3aadbbc3be7424a148ce61791cf9bf2e29b813ee9f80109d079bc11696',
}


def require(value, code):
    if not value:
        raise ValueError(code)


def pinned(name):
    path = UTF8 / name
    require(path.resolve(strict=True) == path and path.is_file(), 'raw_host_fixed_source')
    wire = path.read_bytes()
    require(hashlib.sha256(wire).hexdigest() == PINS[name], 'raw_host_source_pin')
    return wire


def once(value, old, new):
    require(value.count(old) == 1, 'raw_host_derivation_marker')
    return value.replace(old, new, 1)


def section(value, start, end):
    require(value.count(start) == value.count(end) == 1, 'raw_host_derivation_section')
    first, last = value.index(start), value.index(end)
    require(first < last, 'raw_host_derivation_order')
    return value[first:last]


CONFIG = '''const BASE: &str = "experiments/php-wasix-wpdb-raw-error-probe/";
const PHP_MOUNT: &str = "/experiments/php-wasix-wpdb-raw-error-probe/fixtures";
const WP_MOUNT: &str = "/.reference/wordpress-7.1.3/wp-includes";
const WP_SITE: &str = "target/wp-bootstrap-probe/site/wp-includes";
const WP_MANIFEST: &str = "target/wp-bootstrap-probe/source-manifest.json";
const WP_MANIFEST_SHA: &str = "734275a54c29e1df269ae5fe34782f567de05a1b3230aad7faed252b37d783b3";
const SCHEMA: &str = "recastpress.php-wasix-wpdb-raw-error-observation.v0.5";
'''


SOURCES = '''fn identity(bytes: &[u8]) -> Value {
    json!({"bytes": bytes.len(), "sha256": db::sha(bytes)})
}

fn load_sources(root: &Path) -> Result<(SourceFs, SourceFs, Value)> {
    let manifest = fixed(root, WP_MANIFEST, 2 * 1024 * 1024)?;
    ensure!(db::sha(&manifest) == WP_MANIFEST_SHA, "raw_wordpress_manifest");
    let catalog = strict_json::parse(&manifest).map_err(|_| anyhow::anyhow!("raw_wordpress_manifest_json"))?;
    let mut php = BTreeMap::new();
    let mut wordpress = BTreeMap::new();
    let mut fixture_ids = serde_json::Map::new();
    let mut wordpress_ids = serde_json::Map::new();
    for (name, compiled) in FIXTURES {
        let bytes = fixed(root, &format!("{BASE}fixtures/{name}"), 512 * 1024)?;
        ensure!(bytes == *compiled, "raw_compiled_fixture_changed");
        fixture_ids.insert((*name).into(), identity(&bytes));
        php.insert((*name).into(), Arc::<[u8]>::from(bytes));
    }
    for (name, size, digest) in WP_PINS {
        let bytes = fixed(root, &format!("{WP_SITE}/{name}"), *size as u64)?;
        let expected = json!({"bytes": size, "sha256": digest});
        let relative = format!("wp-includes/{name}");
        ensure!(identity(&bytes) == expected && catalog["files"][&relative] == expected, "raw_stock_wordpress_source");
        wordpress_ids.insert(relative, expected);
        wordpress.insert((*name).into(), Arc::<[u8]>::from(bytes));
    }
    Ok((tree(php)?, tree(wordpress)?, json!({"source_manifest": identity(&manifest),
        "fixtures": fixture_ids, "wordpress": wordpress_ids})))
}
'''


PROBE = '''fn probe(input: db::Input, report: &mut Value) -> Result<()> {
    ensure!(input.mode == db::Mode::Read, "raw_read_input_only");
    let root = std::env::current_dir()?.canonicalize()?;
    let bytes = load_module(&root, report)?;
    let module_before = report["module"].clone();
    let receipt_before = report["build_receipt"].clone();
    let (php, wordpress, sources) = load_sources(&root)?;
    report["sources_before"] = sources.clone();
    let context_wire = serde_json::to_vec(&context(&input))?;
    let context = tree(BTreeMap::from([("context.json".to_owned(), Arc::<[u8]>::from(context_wire))]))?;

    report["phase"] = json!("module_compile");
    let engine: Engine = wasmer_compiler_cranelift::Cranelift::default().into();
    let data = HashedModuleData::new(bytes);
    let module = Module::new(&engine, &data.wasm()[..]).map_err(|_| anyhow::anyhow!("raw_module_compile"))?;
    report["module_compilations"] = json!(1);
    let nonce = input.nonce.clone();
    let mut actor = db::Actor::start(input).map_err(|code| anyhow::anyhow!(code))?;
    let rpc = rpc_fs::RpcFs::new(nonce, actor.dispatch.clone());
    report["phase"] = json!("cgi_execution");
    report["guest_execution_started"] = json!(true);
    let result = execute(&engine, module, &data, php, wordpress, context, &rpc, &mut report["cgi_capture"]);
    report["rpc"] = rpc.finish();
    report["db_actor_joined"] = json!(actor.finish());
    {
        let state = actor.observation.lock().map_err(|_| anyhow::anyhow!("raw_actor_observation_poisoned"))?;
        report["operations"] = json!(state.operations);
        report["physical_connection_created"] = json!(state.connection_created);
        report["physical_disconnect_attempted"] = json!(state.disconnect_attempted);
        report["physical_disconnect_completed"] = json!(state.disconnect_completed);
        report["db_actor_failure"] = json!(state.failure);
        report["db_actor_exited"] = json!(state.exited);
    }
    if let Ok(value) = &result {
        if value["passed"] == false {
            if let Some(code) = value["failure"].as_str().filter(|code| safe_code(code)) {
                report["guest_failure"] = json!(code);
            }
        }
    }
    let (_, _, sources_after) = load_sources(&root)?;
    let _ = load_module(&root, report)?;
    report["sources_after"] = sources_after.clone();
    ensure!(sources_after == sources && report["module"] == module_before
        && report["build_receipt"] == receipt_before, "raw_source_or_module_changed");
    report["source_rechecked_after_execution"] = json!(true);
    let actual = result?;
    let rpc = &report["rpc"];
    ensure!(rpc["finished"] == true && rpc["opened_once"] == true && rpc["close_dispatched"] == true
        && rpc["failure"].is_null() && rpc["channel_open"] == false && rpc["pending_bytes"] == 0
        && rpc["unread_response_bytes"] == 0 && rpc["response_pending"] == false
        && report["physical_connection_created"] == true && report["physical_disconnect_attempted"] == true
        && report["physical_disconnect_completed"] == true && report["db_actor_joined"] == true
        && report["db_actor_exited"] == true && report["db_actor_failure"].is_null(), "raw_owned_rpc_completion");
    ensure!(actual["schema"] == "recastpress.php-wasix-wpdb-raw-error-php-observation.v0.5"
        && actual["passed"] == true && actual["outcome"] == "fixed_raw_error_cases_passed", "raw_guest_scope");
    report["php_result"] = actual;
    report["phase"] = json!("full_private_raw_error_verified");
    Ok(())
}

fn safe_code(code: &str) -> bool {
    !code.is_empty() && code.len() <= 80 && code.as_bytes()[0].is_ascii_lowercase()
        && code.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}

fn main() {
    let mut report = json!({"schema": SCHEMA, "passed": false, "outcome": "fixed_wasix_wpdb_raw_error_failed",
        "mode": "read", "profile": "fixed_raw_err", "phase": "input", "operations": [], "cgi_capture": {},
        "php_result": null, "private_ipc_only": true, "publication_authorized": false,
        "native_comparison_performed": false, "source_rechecked_after_execution": false,
        "physical_connection_created": false, "physical_disconnect_attempted": false,
        "physical_disconnect_completed": false, "db_actor_joined": false, "db_actor_exited": false,
        "guest_execution_started": false, "fresh_runtime": true, "module_compilations": 0,
        "guest_networking": false, "host_environment_forwarded": false, "host_home_mapped": false,
        "db_credentials_supplied_to_guest": false, "host_salts_supplied_to_guest": false,
        "native_php_fallback": false, "product_runtime": false, "full_wordpress_bootstrap": false,
        "outer_owned_process_deadline_required": true});
    let mut secrets = Vec::new();
    let result = (|| -> Result<()> {
        ensure!(cfg!(target_os = "linux"), "actual_php_wasm_linux_only");
        ensure!(std::env::args_os().count() == 1, "closed_arguments");
        let mut wire = Vec::new();
        std::io::stdin().take(20 * 1024 + 1).read_to_end(&mut wire)?;
        strict_json::parse(&wire).map_err(|_| anyhow::anyhow!("raw_private_input_json"))?;
        let input = db::parse_input(&wire).map_err(|code| anyhow::anyhow!(code))?;
        secrets = input.secrets();
        probe(input, &mut report)
    })();
    if let Err(error) = &result {
        let code = error.to_string();
        report["error"] = json!(if safe_code(&code) { code.as_str() } else { "fixed_raw_error_probe_failed" });
    } else {
        report["passed"] = json!(true);
        report["outcome"] = json!("fixed_wasix_wpdb_raw_error_passed");
    }
    let mut wire = serde_json::to_vec(&report).expect("bounded private report");
    let guarded = wire.len() > 1024 * 1024 || secret_present(&wire, &secrets);
    if guarded {
        wire = serde_json::to_vec(&json!({"schema": SCHEMA, "passed": false,
            "error": "private_secret_or_quota_guard"})).expect("bounded private failure");
    }
    let mut stdout = std::io::stdout().lock();
    if stdout.write_all(&wire).and_then(|_| stdout.write_all(b"\\n")).and_then(|_| stdout.flush()).is_err() {
        std::process::exit(2);
    }
    if result.is_err() || guarded {
        std::process::exit(1);
    }
}
'''


def generated():
    for name in ('Cargo.toml', 'Cargo.lock'):
        value = once(pinned(name), OLD_NAME, NAME)
        if name == 'Cargo.toml':
            old = b'../../target/wpdb-rpc-probe/workspace/source/'
            require(value.count(old) == 2, 'raw_host_fixed_driver_paths')
            value = value.replace(old, b'../../target/wpdb-raw-error-probe/workspace/source/')
        require((HERE / name).read_bytes() == value, 'raw_host_manifest_derivation')
    original = pinned('src/main.rs').decode('utf-8')
    imports = section(original, '#![forbid(unsafe_code)]', 'const PREPARED:')
    imports = once(imports, '#[path = "../../php-wasix-wp-bootstrap-probe/src/db.rs"]\nmod db;',
        'mod db;\n#[path = "../../php-wasix-wp-private-worker-probe/src/strict_json.rs"]\nmod strict_json;')
    fixtures = section(original, 'const FIXTURES:', 'const ORACLES:')
    pins = section(original, 'const WP_PINS:', 'include!("module_verifier.rs");')
    helpers = section(original, 'fn known_json_cgi_headers(', 'fn load_sources(')
    helpers = once(helpers, 'recastpress.php-wasix-wpdb-utf8-context.v0.5',
        'recastpress.php-wasix-wpdb-raw-error-context.v0.5')
    environment = section(original, 'fn environment()', 'struct Native {')
    require(environment.count('/__rp_utf8.php') == 2, 'raw_host_cgi_target_markers')
    environment = environment.replace('/__rp_utf8.php', '/__rp_raw_error.php')
    execute = section(original, 'fn execute(', 'fn probe(')
    execute = once(execute, '"/rp-utf8"', '"/rp-raw-error"')
    execute = once(execute, '"php-wpdb-utf8"', '"php-wpdb-raw-error"')
    execute = once(execute, 'serde_json::from_slice(&response.body)', 'strict_json::parse(&response.body)')
    guard = section(original, 'fn secret_present(', 'fn main()')
    output = imports + CONFIG + fixtures + pins + 'include!("../../php-wasix-wpdb-utf8-probe/src/module_verifier.rs");\n' \
        + helpers + SOURCES + environment + execute + guard + PROBE
    require(not any(marker in output for marker in ('const ORACLES', 'fn references(', 'native_backend_match')),
        'raw_host_native_comparison_leak')
    return output.encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    path = HERE / 'src/main.rs'
    wire = generated()
    require(path.resolve() == path, 'raw_host_generated_alias')
    if path.exists():
        require(path.is_file() and path.read_bytes() == wire, 'raw_host_generated_changed')
    else:
        require(not args.verify_only, 'raw_host_generated_missing')
        path.parent.mkdir(exist_ok=True)
        with path.open('xb') as output:
            output.write(wire)
    print(hashlib.sha256(wire).hexdigest())


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'WordPress raw ERR host derivation failed: {error}', file=sys.stderr)
        sys.exit(1)
