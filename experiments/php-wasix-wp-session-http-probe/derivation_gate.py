"""Bind the standalone HTTP build to its accepted service and session inputs."""
import hashlib
from pathlib import Path
import re
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIVATE_HTTP = ROOT / 'experiments/php-wasix-wp-private-http-probe'
SESSION = ROOT / 'experiments/php-wasix-wp-session-probe'
WORKER = ROOT / 'experiments/php-wasix-wp-private-worker-probe'
CODEC = ROOT / 'experiments/wp-private-response-probe'


def require(value, code):
    if not value:
        raise ValueError(code)


def inputs():
    return [PRIVATE_HTTP / 'Cargo.toml', PRIVATE_HTTP / 'Cargo.lock', PRIVATE_HTTP / 'src/main.rs',
        SESSION / 'src/main.rs', SESSION / 'evidence/windows-build.json',
        WORKER / 'src/strict_json.rs', CODEC / 'Cargo.toml', CODEC / 'Cargo.lock', CODEC / 'src/lib.rs']


def dependency_gate():
    original = b'recastpress-php-wasix-wp-private-http-probe'
    candidate = b'recastpress-php-wasix-wp-session-http-probe'
    for name in ('Cargo.toml', 'Cargo.lock'):
        previous = (PRIVATE_HTTP / name).read_bytes()
        require(previous.count(original) == 1
            and (HERE / name).read_bytes() == previous.replace(original, candidate, 1),
            'session_http_dependencies_match_private_http')
    packages = tomllib.loads((HERE / 'Cargo.lock').read_text())['package']
    require(len(packages) == 58 and not any(p['name'].startswith(('wasmer', 'mysql_')) for p in packages),
        'session_http_service_lock_scope')
    return {'packages': len(packages), 'new_registry_packages': 0,
        'private_http_manifest_and_lock_package_name_only': True,
        'wasmer_or_mysql_linked_into_service': False}


def verify():
    dependencies = dependency_gate()
    source = (HERE / 'src/main.rs').read_bytes()
    production = source.split(b'#[cfg(test)]', 1)[0]
    require(not any(token in production for token in (b'NativeResponse', b'response_contract',
        b'experiments/wp-request-context-probe/evidence', b'experiments/wp-request-probe/evidence')),
        'session_http_production_response_oracle_reference')
    declaration = b'#[path = "../../php-wasix-wp-private-worker-probe/src/strict_json.rs"]\nmod strict_json;'
    require(production.count(declaration) == 1 and not (HERE / 'src/strict_json.rs').exists(),
        'session_http_strict_json_readonly_import')
    require(b'recastpress_wp_private_response_probe' in production, 'session_http_structural_codec_used')

    for literal in (
        b'"experiments/php-wasix-wp-session-probe/evidence/linux-build.json"',
        b'"target/php-wasix-wp-bootstrap-probe/cargo-target/release/recastpress-php-wasix-wp-session-probe"',
        b'include_bytes!("../../php-wasix-wp-session-probe/evidence/windows-build.json")',
    ):
        require(production.count(literal) == 1, 'session_http_fixed_session_artifact_paths')
    start = production.index(b'fn worker_identity(')
    end = production.index(b'\n}', start) + 2
    identity = production[start:end]
    require(re.findall(rb'fixed\(\s*root\s*,\s*([A-Z_]+)\s*,', identity) == [b'WORKER_RECEIPT', b'WORKER']
        and not any(token in identity for token in (b'File::open', b'fs::read', b'read_to_string')),
        'session_http_worker_identity_only_receipt_and_binary_io')
    compact = re.sub(rb'\s+', b'', identity)
    require(b'receipt["input_files"]==anchor["input_files"]' in compact
        and b'm.len()==241' in compact and b'a.len()==64' in compact,
        'session_http_worker_receipt_matches_compiled_anchor')

    previous = (PRIVATE_HTTP / 'src/main.rs').read_bytes()
    for marker in (b'fn request_case(', b'fn response_from_decoded('):
        original_start = previous.index(marker)
        original_end = previous.index(b'\n}', original_start) + 2
        candidate_start = production.index(marker)
        candidate_end = production.index(b'\n}', candidate_start) + 2
        require(production[candidate_start:candidate_end] == previous[original_start:original_end],
            'session_http_existing_http_admission_and_rendering_retained')
    compact = re.sub(rb'\s+', b'', production)
    require(compact.count(b'Command::new(root.join(WORKER)).env_clear()') == 1
        and b'TcpListener::bind((std::net::Ipv4Addr::LOCALHOST,0))' in compact
        and b'try_acquire_owned()' in compact
        and b'"module_compilations_total":1' in compact
        and b'"request_module_compilations_total":0' in compact,
        'session_http_fixed_worker_loopback_and_compile_count_scope')
    return {'original_inputs': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs()},
        'dependencies': dependencies, 'private_worker_json_decoder_imported_unchanged': True,
        'structural_codec_dependency': True, 'service_native_wordpress_response_oracle_loaded': False,
        'service_worker_receipt_anchor_sources': 241, 'service_worker_receipt_anchor_units': 64,
        'service_worker_identity_io_scope': 'same_job_session_receipt_and_fixed_binary_only',
        'full_source_bindings_checked_by_build_and_controller': True,
        'fixed_session_worker_spawn_sites': 1, 'loopback_only': True,
        'startup_compilation_count_expected': 1, 'per_request_compilation_count_expected': 0,
        'accepted_http_request_admission_and_response_rendering_unchanged': True,
        'product_runtime_selected': False}
