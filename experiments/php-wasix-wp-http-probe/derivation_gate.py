"""Read-only worker boundaries for the independent loopback HTTP experiment."""
import hashlib
from pathlib import Path
import tomllib

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
OLD=ROOT/'experiments/php-wasix-wp-request-context-probe'


def require(value,code):
    if not value:raise ValueError(code)


def inputs():return [OLD/'src/response_contract.rs',OLD/'evidence/windows-build.json',
    ROOT/'experiments/php-wasi-host-probe/src/cgi_response.rs',ROOT/'Cargo.lock']


def verify():
    source=(HERE/'src/main.rs').read_text()
    require('#[path = "../../php-wasix-wp-request-context-probe/src/response_contract.rs"]\nmod response_contract;' in source,
        'http_readonly_native_contract')
    require('#[path = "../../php-wasi-host-probe/src/cgi_response.rs"]\nmod cgi_response;' in source,
        'http_readonly_cgi_parser')
    require(not (HERE/'src/response_contract.rs').exists() and not (HERE/'src/cgi_response.rs').exists(),
        'http_no_copied_shared_runtime')
    require('Command::new(root.join(WORKER)).env_clear()' in source
        and 'tokio::time::timeout(Duration::from_secs(5),child.wait())' in source
        and 'try_acquire_owned()' in source and 'Ipv4Addr::LOCALHOST,0' in source,
        'http_fixed_worker_and_lifecycle_boundaries')
    known=set()
    for path in [ROOT/'Cargo.lock',OLD/'Cargo.lock']:
        known.update((p['name'],p['version']) for p in tomllib.loads(path.read_text())['package'])
    lock=tomllib.loads((HERE/'Cargo.lock').read_text())
    for package in lock['package']:
        require(package['name']=='recastpress-php-wasix-wp-http-probe'
            or (package['name'],package['version']) in known,'http_dependency_version_outside_cached_profiles')
    return {'readonly_imports':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs()},
        'old_worker_actor_sources_modified':False,'old_native_response_contract_imported_unchanged':True,
        'old_cgi_parser_imported_unchanged':True,'worker_path_from_http_or_config':False,
        'dependency_versions_from_existing_root_or_context_lock':True,
        'live_worker_capture_deadline_seconds':300,'failed_worker_cleanup_deadline_seconds':5,
        'worker_execution_limit':3,'worker_concurrency':1,'loopback_only':True}
