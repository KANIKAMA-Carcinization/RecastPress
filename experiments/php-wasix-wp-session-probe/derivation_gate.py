"""Source boundary for one compiled module and three fresh private requests."""
import hashlib
from pathlib import Path
import re
import tomllib

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
WORKER=ROOT/'experiments/php-wasix-wp-private-worker-probe'
SHARED=ROOT/'experiments/php-wasix-wp-request-probe'
CODEC=ROOT/'experiments/wp-private-response-probe'
REUSED=['access_fs','capture','catalog','db','result_budget','rpc_fs','warning_diagnostic']


def require(value,code):
    if not value:raise ValueError(code)


def sha(wire):return hashlib.sha256(wire).hexdigest()
def inputs():return [WORKER/'Cargo.toml',WORKER/'Cargo.lock',WORKER/'src/main.rs',WORKER/'src/strict_json.rs',
    *[SHARED/('src/'+name+'.rs') for name in REUSED],
    ROOT/'experiments/php-wasi-host-probe/src/cgi_response.rs',
    ROOT/'experiments/php-wasix-host-probe/src/fiber_adapter.rs',
    ROOT/'experiments/php-wasix-wpdb-probe/src/inputs.rs',
    CODEC/'Cargo.toml',CODEC/'Cargo.lock',CODEC/'src/lib.rs']


def verify():
    old_name=b'recastpress-php-wasix-wp-private-worker-probe'
    new_name=b'recastpress-php-wasix-wp-session-probe'
    for name in ('Cargo.toml','Cargo.lock'):
        original=(WORKER/name).read_bytes()
        require(original.count(old_name)==1 and original.replace(old_name,new_name)==(HERE/name).read_bytes(),
            'session_manifest_lock_package_name_only')
    packages=tomllib.loads((HERE/'Cargo.lock').read_text())['package']
    require(len(packages)==446,'session_original_446_packages')
    source=(HERE/'src/main.rs').read_bytes();production=source.split(b'#[cfg(test)]',1)[0]
    require(not any(token in production for token in (b'NativeResponse',b'response_contract',
        b'experiments/wp-request-context-probe/evidence',b'experiments/wp-request-probe/evidence')),
        'session_no_production_wordpress_response_oracle')
    for name in REUSED:
        declaration=('#[path = "../../php-wasix-wp-request-probe/src/'+name+'.rs"]\nmod '+name+';').encode()
        require(production.count(declaration)==1 and not (HERE/('src/'+name+'.rs')).exists(),
            'session_common_runtime_readonly_import')
    for name,path in [('cgi_response','php-wasi-host-probe/src/cgi_response.rs'),
        ('fiber_adapter','php-wasix-host-probe/src/fiber_adapter.rs'),
        ('inputs','php-wasix-wpdb-probe/src/inputs.rs'),
        ('strict_json','php-wasix-wp-private-worker-probe/src/strict_json.rs')]:
        declaration=('#[path = "../../'+path+'"]\nmod '+name+';').encode()
        require(production.count(declaration)==1 and not (HERE/('src/'+name+'.rs')).exists(),
            'session_other_runtime_readonly_import')
    old=(WORKER/'src/main.rs').read_bytes()
    def section(wire,start,end):return wire[wire.index(start):wire.index(end,wire.index(start))]
    require(section(old,b'fn load_module(',b'fn finite(')==section(source,b'fn load_module(',b'fn finite('),
        'session_managed_module_verifier_unchanged')
    require(production.count(b'Module::new(')==1,'session_one_module_compile_callsite')
    startup=section(production,b'fn startup(',b'fn probe(')
    probe=section(production,b'fn probe(',b'fn private_contexts_valid(')
    parameter=re.search(rb'\b([a-z_]+)\s*:\s*&CompiledCandidate',probe)
    require(b'Module::new(' in startup and b'Module::new(' not in probe
        and parameter is not None and parameter.group(1)+b'.module.clone()' in probe,
        'session_startup_compile_and_per_request_module_clone')
    require(b'db::sha(&load_module(root,report)?) == self.module_sha256' in startup
        and probe.count(parameter.group(1)+b'.verify(')>=2,'session_compiled_module_hash_rechecked_per_request')
    candidate=re.search(rb'struct CompiledCandidate\s*\{([^}]+)\}',production)
    require(candidate is not None,'session_owned_compiled_candidate')
    fields=re.sub(rb'\s+',b'',candidate.group(1)).strip(b',').split(b',')
    require(set(fields)=={b'engine:Engine',b'data:HashedModuleData',b'module:Module',b'module_sha256:String'},
        'session_candidate_has_no_guest_or_database_state')
    require(all(token in probe for token in (b'db::Actor::start(',b'rpc_fs::RpcFs::new(',
        b'access_fs::ManagedAccessFs::new(',b'load_sources(',b'guest_context(')),
        'session_fresh_per_request_actor_channels_sources_and_context')
    require(b'struct CaptureDir' in production and b'create_new(true)' in production
        and b'struct SessionProtocol' in production,'session_unique_capture_and_finite_protocol')
    return {'original_sources':{p.relative_to(ROOT).as_posix():sha(p.read_bytes()) for p in inputs()},
        'manifest_and_lock_package_name_only':True,'dependency_packages':446,'new_dependency_packages':0,
        'shared_runtime_sources_modified':False,'managed_module_verifier_unchanged':True,
        'native_wordpress_response_oracle_loaded_by_worker':False,
        'compiled_candidate_fields':['engine','data','module','module_sha256'],
        'module_compiled_once_in_startup':True,'compiled_module_cloned_for_each_request':True,
        'wasi_db_actor_channels_sources_and_context_rebuilt_per_request':True,
        'one_owned_process_three_serial_requests':True,'response_cache_or_worker_pool':False,
        'old_artifact_gate_scope':'exact_receipt_source_maps_and_artifact_byte_identities',
        'full_recursive_old_build_verifiers_invoked':False,'product_runtime_selected':False}
