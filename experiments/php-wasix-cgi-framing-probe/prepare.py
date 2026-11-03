"""Prepare the eight fixed native CGI frames without native binaries or execution."""
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/php-wasix-cgi-framing-probe'
NATIVE = ROOT / 'experiments/php-cgi-framing-probe'
THEME = ROOT / 'experiments/php-wasix-wp-request-probe'
PROOF = NATIVE / 'evidence/windows-native.json'
PROOF_SHA = 'cb14bab903650d7ab41dac3f65055851cc19c8a22d8ca096e8c83d0bc4b17ab9'


def require(value, code):
    if not value: raise ValueError(code)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


native = load('rp_wasix_framing_native_preparer', NATIVE / 'prepare.py')
home = load('rp_wasix_framing_readonly_theme_build', THEME / 'build_check.py')
sha = native.sha


def read(path, limit=4*1024*1024):
    require(path.resolve(strict=True) == path and path.is_file() and path.stat().st_size <= limit, 'fixed_source_path_or_quota')
    wire = path.read_bytes(); require(len(wire) <= limit, 'fixed_source_quota'); return wire


def derivation():
    old = read(THEME / 'src/main.rs'); new = read(HERE / 'src/main.rs')
    start = old.index(b'fn fixed('); end = old.index(b'fn finite(', start)
    block = old[start:end]
    require(new.count(block) == 1, 'exact_fixed_and_module_verifier_bodies')
    old_name = b'recastpress-php-wasix-wp-request-probe'
    new_name = b'recastpress-php-wasix-cgi-framing-probe'
    for name in ['Cargo.toml', 'Cargo.lock']:
        wire = read(THEME / name)
        require(wire.count(old_name) == 1 and wire.replace(old_name, new_name) == read(HERE / name), 'package_name_only_workspace_derivation')
    return {'fixed_and_module_verifier_sha256': sha(block), 'fixed_and_module_verifier_bodies_unchanged': True,
        'workspace_package_name_only': True, 'old_sources_modified': False}


def inputs():
    values = home.inputs()
    for path in [HERE / x for x in ['prepare.py', 'build_check.py', 'run_check.py', 'Cargo.toml', 'Cargo.lock', 'src/main.rs']] + [
            ROOT / '.github/workflows/php-wasix-cgi-framing.yml']:
        values[path.relative_to(ROOT).as_posix()] = sha(read(path))
    for name, identity in native.sources().items(): values[name] = identity['sha256']
    values[PROOF.relative_to(ROOT).as_posix()] = sha(read(PROOF))
    return dict(sorted(values.items()))


def native_gate():
    wire = read(PROOF); require(sha(wire) == PROOF_SHA, 'frozen_native_framing_proof')
    value = json.loads(wire)
    require(value['schema'] == 'recastpress.php-cgi-framing-check.v0.5' and value['passed'] is True
        and value['outcome'] == 'native_all_eight_cgi_frames_exact'
        and value['input_files'] == native.sources() and value['source_reverified_after'] is True
        and value['owned_cleanup_completed'] is True and value['owned_scratch_reclaimed'] is True
        and value['owned_child_count'] == value['owned_reaped_count'] == 8, 'native_framing_completion')
    require(len(value['observations']) == 8, 'eight_native_observations')
    for case, actual in zip(native.cases(), value['observations']):
        expected, headers, body = native.expected(case)
        complete = b'\r\n'.join(headers) + b'\r\n\r\n' + body
        require(actual['case'] == case['case'] and actual['complete_cgi_wire_matches'] is True
            and actual['stdout'] == native.identity(complete) and actual['stderr'] == native.identity(b'')
            and actual['process']['exit_code'] == 0 and actual['process']['stdout_eof'] is True
            and actual['process']['stderr_eof'] is True and actual['process']['owned_reaped'] is True
            and actual['response']['body'] == native.identity(body)
            and actual['response']['header_lines_hex'] == [h.hex() for h in headers], 'native_case_identity')
    return {'path': PROOF.relative_to(ROOT).as_posix(), **native.identity(wire), 'all_eight_exact': True}


def prepare(record=True):
    before = inputs(); proof = native_gate(); rows = []
    for case in native.cases():
        _, headers, body = native.expected(case)
        complete = b'\r\n'.join(headers) + b'\r\n\r\n' + body
        rows.append({**case, 'expected_body': native.identity(body), 'expected_status': 200,
            'expected_header_lines_hex': [h.hex() for h in headers],
            'expected_cgi_wire': native.identity(complete), 'expected_cgi_wire_hex': complete.hex()})
    result = {'schema': 'recastpress.php-wasix-cgi-framing-prepared.v0.5', 'source_inputs': before,
        'fixture_files': {name: identity for name, identity in native.sources().items() if '/fixtures/' in name},
        'native_proof': proof, 'derivation': derivation(), 'cases': rows,
        'php_executed': False, 'wasm_executed': False, 'database_connected': False,
        'native_binaries_required': False, 'network_calls_made': False, 'product_runtime_selected': False}
    require(before == inputs(), 'framing_sources_changed')
    if record:
        require(WORK.resolve() == WORK and WORK.is_relative_to(ROOT / 'target'), 'owned_prepare_path')
        WORK.mkdir(parents=True, exist_ok=True)
        wire = (json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
        require(len(wire) <= 3*1024*1024, 'prepared_quota')
        (WORK / 'prepared.json').write_bytes(wire)
    return result


if __name__ == '__main__':
    prepare(); print('Prepared eight fixed WASIX CGI framing cases; no execution')
