"""Closed dependency and runtime reuse boundary for the private response worker."""
import hashlib
from pathlib import Path
import tomllib

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
CONTEXT=ROOT/'experiments/php-wasix-wp-request-context-probe'
SHARED=ROOT/'experiments/php-wasix-wp-request-probe'
CODEC=ROOT/'experiments/wp-private-response-probe'
REUSED=['db','rpc_fs','result_budget','catalog','access_fs','capture','warning_diagnostic']


def require(value,code):
    if not value:raise ValueError(code)


def sha(wire):return hashlib.sha256(wire).hexdigest()
def inputs():
    return [CONTEXT/'Cargo.toml',CONTEXT/'Cargo.lock',CONTEXT/'src/main.rs',
        CODEC/'Cargo.toml',CODEC/'Cargo.lock',CODEC/'src/lib.rs',
        *[SHARED/('src/'+name+'.rs') for name in REUSED],
        ROOT/'experiments/php-wasi-host-probe/src/cgi_response.rs',
        ROOT/'experiments/php-wasix-wpdb-probe/src/inputs.rs',
        ROOT/'experiments/php-wasix-host-probe/src/fiber_adapter.rs']


def dependency_gate():
    old_name='recastpress-php-wasix-wp-request-context-probe'
    new_name='recastpress-php-wasix-wp-private-worker-probe'
    codec_name='recastpress-wp-private-response-probe'
    manifest=(CONTEXT/'Cargo.toml').read_text().replace(old_name,new_name)
    marker='recastpress-wp-source-fs-probe = { path = "../wp-source-fs-probe" }'
    require(manifest.count(marker)==1,'private_worker_parent_manifest')
    manifest=manifest.replace(marker,marker+'\n'+codec_name+' = { path = "../wp-private-response-probe" }')
    require((HERE/'Cargo.toml').read_text()==manifest,'private_worker_manifest_codec_dependency_only')
    def packages(path):
        rows=tomllib.loads(path.read_text())['package']
        result={(p['name'],p['version']):p for p in rows}
        require(len(result)==len(rows),'private_worker_unique_lock_packages');return result
    original=packages(CONTEXT/'Cargo.lock');codec=packages(CODEC/'Cargo.lock');actual=packages(HERE/'Cargo.lock')
    expected={k:dict(v) for k,v in original.items()}
    root=expected.pop((old_name,'0.0.0'));root['name']=new_name
    root['dependencies']=sorted([*root['dependencies'],codec_name])
    expected[(new_name,'0.0.0')]=root
    private=dict(codec[(codec_name,'0.0.0')]);private['dependencies']=[
        'sha2 0.10.9' if name=='sha2' else name for name in private['dependencies']]
    expected[(codec_name,'0.0.0')]=private
    require(actual==expected,'private_worker_lock_exact_parent_plus_codec')
    for key,package in codec.items():
        if key!=(codec_name,'0.0.0'):
            require(key in original and all(package.get(field)==original[key].get(field)
                for field in ('name','version','source','checksum')),'private_worker_codec_dependency_drift')
    return {'packages':len(actual),'new_registry_packages':0,
        'context_lock_plus_one_local_codec_package_only':True,
        'registry_versions_sources_and_checksums_unchanged':True}


def verify():
    dependencies=dependency_gate()
    source=(HERE/'src/main.rs').read_bytes()
    production=source.split(b'#[cfg(test)]',1)[0]
    require(not any(token in production for token in (b'NativeResponse',b'response_contract',
        b'experiments/wp-request-context-probe/evidence',b'experiments/wp-request-probe/evidence')),
        'private_worker_production_response_oracle_reference')
    require(b'recastpress_wp_private_response_probe' in production,'private_worker_structural_codec_used')
    for name in REUSED:
        declaration=('#[path = "../../php-wasix-wp-request-probe/src/'+name+'.rs"]\nmod '+name+';').encode()
        require(production.count(declaration)==1 and not (HERE/('src/'+name+'.rs')).exists(),
            'private_worker_shared_module_readonly_import')
    for name,path in [('cgi_response','php-wasi-host-probe/src/cgi_response.rs'),
        ('fiber_adapter','php-wasix-host-probe/src/fiber_adapter.rs'),
        ('inputs','php-wasix-wpdb-probe/src/inputs.rs')]:
        declaration=('#[path = "../../'+path+'"]\nmod '+name+';').encode()
        require(production.count(declaration)==1 and not (HERE/('src/'+name+'.rs')).exists(),
            'private_worker_other_readonly_runtime_import')
    old=(CONTEXT/'src/main.rs').read_bytes()
    def section(wire,start,end):return wire[wire.index(start):wire.index(end,wire.index(start))]
    require(section(old,b'fn load_module(',b'fn finite(')==section(source,b'fn load_module(',b'fn finite('),
        'private_worker_module_verifier_exact')
    loading=section(production,b'fn load_sources(',b'fn environment(')
    require(b'p["input_files"]' not in loading and b'compiled_fixtures_and_core_only' in loading
        and b'transitive_inputs_verified_by_worker' in loading,
        'private_worker_no_transitive_wordpress_oracle_scan')
    require(b'const GUEST_BASE: &str = "experiments/php-wasix-wp-request-probe/";' in production
        and b'.with_mount(format!("/{GUEST_BASE}fixtures").into(), Arc::new(php))' in production,
        'private_worker_guest_mount_unchanged')
    return {'original_sources':{p.relative_to(ROOT).as_posix():sha(p.read_bytes()) for p in inputs()},
        'dependencies':dependencies,'shared_runtime_sources_modified':False,
        'managed_module_verifier_body_exact':True,'old_guest_mount_namespace_preserved':True,
        'native_response_oracle_loaded_by_production_worker':False,
        'transitive_old_prepared_inputs_verified_by_worker':False,
        'runtime_source_scope':'compiled_fixtures_and_core_only',
        'full_source_inputs_verified_by_build_and_controller':True,
        'private_response_decode_is_structural_only':True,'publication_authorized':False}
