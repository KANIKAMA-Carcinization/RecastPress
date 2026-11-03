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
