"""Verify the frozen bootstrap host's reused bodies without changing them."""
import hashlib
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
OLD=ROOT/'experiments/php-wasix-wp-bootstrap-probe'


def require(value,reason):
    if not value:raise ValueError(reason)


def sha(wire):return hashlib.sha256(wire).hexdigest()


def inputs():
    return [OLD/p for p in ['Cargo.toml','Cargo.lock','src/main.rs','src/db.rs','src/catalog.rs',
        'src/result_budget.rs','src/rpc_fs.rs','src/access_fs.rs','src/capture.rs','build_check.py','derivation_gate.py']]


def between(wire,start,end):
    return wire[wire.index(start):wire.index(end,wire.index(start))]


def verify():
    identities={}
    oldname=b'recastpress-php-wasix-wp-bootstrap-probe'
    newname=b'recastpress-php-wasix-wp-request-probe'
    for name in ['Cargo.toml','Cargo.lock']:
        wire=(OLD/name).read_bytes()
        require(wire.count(oldname)==1 and wire.replace(oldname,newname)==(HERE/name).read_bytes(),
            'home_workspace_name_only_derivation')
        identities[name]=sha(wire)
    for name in ['catalog.rs','result_budget.rs','rpc_fs.rs']:
        original=(OLD/'src'/name).read_bytes()
        require(original==(HERE/'src'/name).read_bytes(),'home_unchanged_runtime_body')
        identities['src/'+name]=sha(original)
    original=(OLD/'src/access_fs.rs').read_bytes()
    require(original.replace(b'php-wasix-wp-bootstrap-probe',b'php-wasix-wp-request-probe')
        .replace(b'/rp-bootstrap',b'/rp-request')==(HERE/'src/access_fs.rs').read_bytes(),
        'home_access_mount_prefix_only_derivation')
    identities['src/access_fs.rs']=sha(original)
    original=(OLD/'src/capture.rs').read_bytes()
    require(original.count(b'const OUTPUT_LIMIT: usize = 65 * 1024;')==1,'home_original_capture_limit')
    expected=original.replace(b'const OUTPUT_LIMIT: usize = 65 * 1024;',
        b'const OUTPUT_LIMIT: usize = 3 * 1024 * 1024;')
    actual=(HERE/'src/capture.rs').read_bytes().split(b'#[cfg(test)]',1)[0]
    require(expected.rstrip()==actual.rstrip(),'home_capture_limit_only_derivation')
    identities['src/capture.rs']=sha(original)
    olddb=(OLD/'src/db.rs').read_bytes();newdb=(HERE/'src/db.rs').read_bytes()
    start,end=b'pub fn parse_request(',b'#[cfg(test)]'
    require(between(olddb,start,end)==between(newdb,start,end),'home_db_actor_query_escape_body_changed')
    start,end=b'pub type Result<T>',b'#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]'
    require(between(olddb,start,end)==between(newdb,start,end),'home_db_wire_budgets_changed')
    oldmain=(OLD/'src/main.rs').read_bytes();newmain=(HERE/'src/main.rs').read_bytes()
    require(between(oldmain,b'fn load_module(',b'fn finite(')==between(newmain,b'fn load_module(',b'fn finite('),
        'home_managed_module_gate_changed')
    return {'original_sources':identities,'original_sources_modified':False,
        'workspace_and_lock_package_name_only':True,'db_query_escape_actor_wire_budgets_unchanged':True,
        'async_rpc_catalog_and_result_budgets_unchanged':True,
        'access_mount_prefix_only':True,'capture_limit_bytes':[66560,3145728],
        'managed_module_verification_body_unchanged':True,
        'new_input_context_home_routing_and_public_response_policy_explicit':True}
