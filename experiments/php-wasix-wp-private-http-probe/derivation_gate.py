"""Dependency, private worker and no-response-oracle service boundaries."""
import hashlib
from pathlib import Path
import re
import tomllib

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
HTTP=ROOT/'experiments/php-wasix-wp-http-probe'
WORKER=ROOT/'experiments/php-wasix-wp-private-worker-probe'
CODEC=ROOT/'experiments/wp-private-response-probe'


def require(value,code):
    if not value:raise ValueError(code)


def sha(wire):return hashlib.sha256(wire).hexdigest()
def inputs():return [HTTP/'Cargo.toml',HTTP/'Cargo.lock',HTTP/'src/main.rs',
    WORKER/'src/strict_json.rs',WORKER/'evidence/windows-build.json',
    CODEC/'Cargo.toml',CODEC/'Cargo.lock',CODEC/'src/lib.rs']


def dependency_gate():
    old_name='recastpress-php-wasix-wp-http-probe'
    new_name='recastpress-php-wasix-wp-private-http-probe'
    codec_name='recastpress-wp-private-response-probe'
    manifest=(HTTP/'Cargo.toml').read_text().replace(old_name,new_name)
    marker='getrandom = "=0.2.17"'
    require(manifest.count(marker)==1,'private_http_parent_manifest')
    manifest=manifest.replace(marker,marker+'\n'+codec_name+' = { path = "../wp-private-response-probe" }')
    require((HERE/'Cargo.toml').read_text()==manifest,'private_http_manifest_local_codec_only')
    def packages(path):
        rows=tomllib.loads(path.read_text())['package']
        result={(p['name'],p['version']):p for p in rows}
        require(len(result)==len(rows),'private_http_unique_lock_packages');return result
    original=packages(HTTP/'Cargo.lock');codec=packages(CODEC/'Cargo.lock');actual=packages(HERE/'Cargo.lock')
    expected={k:dict(v) for k,v in original.items()}
    root=expected.pop((old_name,'0.0.0'));root['name']=new_name
    root['dependencies']=sorted([*root['dependencies'],codec_name]);expected[(new_name,'0.0.0')]=root
    expected[(codec_name,'0.0.0')]=codec[(codec_name,'0.0.0')]
    require(actual==expected,'private_http_lock_parent_plus_local_codec_only')
    for key,package in codec.items():
        if key!=(codec_name,'0.0.0'):
            require(key in original and all(package.get(k)==original[key].get(k)
                for k in ('name','version','source','checksum')),'private_http_codec_dependency_drift')
    require(not any(p['name'].startswith(('wasmer','mysql_')) for p in actual.values()),
        'private_http_service_has_no_wasm_or_database_library')
    return {'packages':len(actual),'new_registry_packages':0,
        'accepted_http_lock_plus_one_local_codec_only':True,'registry_versions_sources_checksums_unchanged':True,
        'wasmer_or_mysql_linked_into_service':False}


def verify():
    dependencies=dependency_gate()
    source=(HERE/'src/main.rs').read_bytes();production=source.split(b'#[cfg(test)]',1)[0]
    require(not any(token in production for token in [b'NativeResponse',b'response_contract',
        b'experiments/wp-request-context-probe/evidence',b'experiments/wp-request-probe/evidence',
        b'ef43f26984536cae43c4e703edef98dbaaf2bca79cc972a4373bf27f01cabdd1',
        b'd2bda2fdf6bf12e075689fd3af5e87eba2ede1ea953d9f0d7085a63fc11f60bc']),
        'private_http_production_response_oracle_reference')
    declaration=b'#[path = "../../php-wasix-wp-private-worker-probe/src/strict_json.rs"]\nmod strict_json;'
    require(production.count(declaration)==1 and not (HERE/'src/strict_json.rs').exists(),
        'private_http_strict_json_readonly_import')
    require(b'recastpress_wp_private_response_probe' in production,'private_http_structural_codec_used')
    start=production.index(b'fn worker_identity(');end=production.index(b'enum Rejection',start)
    identity=production[start:end]
    files=re.findall(rb'fixed\(\s*root\s*,\s*([A-Z_]+)\s*,',identity)
    require(files==[b'WORKER_RECEIPT',b'WORKER'],'private_http_worker_identity_only_receipt_and_binary_io')
    require(not any(token in identity for token in [b'File::open',b'fs::read',b'read_to_string']),
        'private_http_no_other_worker_identity_file_io')
    compact=re.sub(rb'\s+',b'',identity)
    require(b'receipt["input_files"]==anchor["input_files"]' in compact
        and b'm.len()==219' in compact and b'a.len()==59' in compact,
        'private_http_worker_receipt_matches_compiled_anchor')
    require(b'Command::new(root.join(WORKER)).env_clear()' in production
        and b'tokio::time::timeout(Duration::from_secs(5),child.wait())' in production
        and b'try_acquire_owned()' in production and b'Ipv4Addr::LOCALHOST,0' in production,
        'private_http_fixed_child_and_loopback_lifecycle')
    return {'original_inputs':{p.relative_to(ROOT).as_posix():sha(p.read_bytes()) for p in inputs()},
        'dependencies':dependencies,'accepted_runtime_sources_modified':False,
        'private_worker_json_decoder_imported_unchanged':True,'structural_codec_dependency':True,
        'service_native_wordpress_response_oracle_loaded':False,
        'service_worker_receipt_anchor_sources':219,'service_worker_receipt_anchor_units':59,
        'service_rechecks_transitive_worker_source_files':False,
        'service_worker_identity_io_scope':'same_job_receipt_and_fixed_binary_only',
        'full_source_bindings_checked_by_build_and_controller':True,
        'worker_execution_limit':3,'worker_concurrency':1,'loopback_only':True,
        'worker_deadline_seconds':300,'failed_worker_cleanup_deadline_seconds':5,
        'product_runtime_selected':False}
