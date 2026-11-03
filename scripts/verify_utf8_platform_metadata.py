"""Offline, fixed-evidence UTF8 platform-metadata audit; never runs PHP or a DB."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'experiments/php-wasix-wpdb-utf8-probe'
RUN = '41632550317'
SOURCE = '5da4ef5c30eac90996f4ec7720a1541fa061679b'
INITIAL = {
    'mysql': {'run': (251360, 'c9861b7d5582b2661b7c65eb14281b37105d3e59bd9768911e5d887dd87cd607'),
        'provenance': (37709, '42c717b751d655905afe287405f7b4e7639a72d072e6b3c82ea4c5024dc1dc6a')},
    'mariadb': {'run': (142227, '29eafa35bc1f9a2269daba47fd3fde47bf315131088896f272bb59e9079654df'),
        'provenance': (37724, '1ff2051fc27cbd360f2b6d00dd653990782ec2a1380ba9aa60d0db35bd0bab0d')},
}
PREPARED_PIN = (15500, '35ebac7830860eb9a399034eca6135ab72cfa451bc49871a836e2a9eec62175d')
INPUTS = {}


def require(value, code):
    if not value: raise ValueError(code)


def sha(wire): return hashlib.sha256(wire).hexdigest()
def identity(wire): return {'bytes': len(wire), 'sha256': sha(wire)}
def encoded(value, sorted_keys=False):
    return (json.dumps(value, ensure_ascii=True, sort_keys=sorted_keys, allow_nan=False, separators=(',', ':')) + '\n').encode('ascii')


def read(path, pin=None):
    require(path.resolve(strict=True) == path and path.is_relative_to(ROOT) and path.is_file()
        and 0 < path.stat().st_size <= 4*1024*1024, 'fixed_audit_input')
    wire = path.read_bytes(); require(len(wire) <= 4*1024*1024, 'audit_input_quota')
    if pin is not None: require((len(wire), sha(wire)) == pin, 'immutable_audit_input_changed')
    INPUTS[path.relative_to(ROOT).as_posix()] = identity(wire); return wire


def json_record(path, pin=None): return json.loads(read(path, pin))


def load_prepare():
    path = PROFILE / 'prepare.py'; read(path)
    spec = importlib.util.spec_from_file_location('rp_utf8_platform_metadata_source_only', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def audit_backend(backend, metadata, preparer):
    directory = PROFILE / ('evidence/ci-' + RUN + '-' + backend)
    provenance = json_record(directory / 'provenance.json', INITIAL[backend]['provenance'])
    require(provenance['source_commit'] == SOURCE and provenance['run_id'] == int(RUN)
        and provenance['backend'] == backend and provenance['verification']['source_binding_verification_passed'] is True
        and provenance['verification']['raw_verdicts_rewritten'] is False, 'original_import_provenance')
    run = json_record(directory / 'raw/experiments/php-wasix-wpdb-utf8-probe/evidence' / ('linux-' + backend + '.json'), INITIAL[backend]['run'])
    prepared = json_record(directory / 'raw/target/php-wasix-wpdb-utf8-probe/prepared.json', PREPARED_PIN)
    host = run['observation']; capture = host['cgi_capture']; rpc = host['rpc']; operations = host['operations']
    require(run['backend'] == backend and host['source_rechecked_after_execution'] is True
        and capture['guest_exit_success'] is True and capture['stderr_bytes'] == 0
        and run['container_normal_exit_verified'] is True and run['container_removed'] is True, 'original_clean_execution')
    pin = preparer.ORACLES[backend]
    native = json_record(ROOT / pin['path'], (pin['bytes'], pin['sha256']))['observation']
    contract = metadata['backends'][backend]
    require(run['sql_server_version'] == contract['linux_version']
        and native['server_info_hex'] == contract['native_version_hex']
        and run['image_config_digest'] == contract['image']['image_config_digest']
        and run['image'].endswith('@' + contract['image']['platform_digest']), 'actual_platform_identity_binding')
    prior_pin = contract['linux_reference']
    prior = json_record(ROOT / prior_pin['path'], (prior_pin['bytes'], prior_pin['sha256']))
    require(prior['passed'] is True and prior['observation']['php_result']['server_info_hex'] == contract['linux_version_hex'], 'accepted_original_linux_version')
    for item in prepared['files']:
        if item['kind'] == 'derived_fixture':
            read(ROOT / item['source'], (item['bytes'], item['sha256']))
    counts = {name: sum(op['op'] == name for op in operations) for name in ['hello', 'query', 'escape', 'close']}
    require(counts == {'hello': 1, 'query': 26, 'escape': 24, 'close': 1}
        and len(operations) == rpc['operations'] == 52 and rpc['finished'] is True
        and rpc['failure'] is None and rpc['response_read_bytes'] == rpc['response_bytes'], 'original_rpc_balance')
    require(sum(op['request_frame_bytes'] for op in operations) == rpc['request_bytes']
        and sum(op['reply_frame_bytes'] for op in operations) == rpc['response_bytes'], 'original_rpc_bytes')
    # Recreate only public, source-fixed data. Never extract a raw guest body.
    known = copy.deepcopy(native)
    known.update(schema='recastpress.php-wasix-wpdb-utf8-php-observation.v0.5', variant='bridge',
        wasm_executed=True, rust_driver_executed=True, native_mysqli_handle_supplied=False,
        included_source_names=prepared['expected_include_names'], server_info_hex=contract['linux_version_hex'])
    known['runtime']['sapi'] = 'cgi-fcgi'
    known['rpc_stats'] = {'transport': {'requests': 52, 'counts': counts,
        'request_bytes': rpc['request_bytes'], 'reply_bytes': rpc['response_bytes'],
        'transport_closed': True, 'max_requests': 4096, 'kind': 'file-channel', 'channel_guest_path': '/rp-wpdb/channel'},
        'internal_setup_queries': 4,
        'escape_scope': 'valid-UTF8-only-fixed-utf8mb4-and-cached-last-OK-NO_BACKSLASH_ESCAPES',
        'mysqli_column_metadata_scope': 'get_col_info-name-only'}
    body = encoded(known)
    require(identity(body) == {key: capture['response']['body_' + key] for key in ['bytes', 'sha256']}, 'whole_known_php_body_not_equal')
    wire = b'X-Powered-By: PHP/8.5.12\r\nContent-Type: application/json\r\n\r\n' + body
    require(identity(wire) == {key: capture['stdout_' + key] for key in ['bytes', 'sha256']}, 'whole_known_cgi_wire_not_equal')
    # Reply metadata has no nonce. Connection identifiers stay isolated and are
    # never emitted; the bounded candidate audit claims only the exact version
    # profile consistent with the already-public HELLO reply size/SHA.
    hello = operations[0]; old_hello = prior['observation']['operations'][0]
    require(hello['seq'] == old_hello['seq'] == 1 and hello['op'] == old_hello['op'] == 'hello', 'first_hello_reply')
    old_frame = {'schema': 'recastpress.wpdb-rpc-reply.v0.5', 'seq': 1, 'ok': True, 'data': old_hello['data']}
    require(identity(encoded(old_frame, True)) == {key: old_hello['reply_frame_' + key] for key in ['bytes', 'sha256']}, 'known_hello_serializer')
    profiles = [('native_windows', contract['native_version_hex']), ('pinned_linux', contract['linux_version_hex'])]
    candidate_count = 0; matches = []
    for profile, version_hex in profiles:
        for identifier in range(1, 1025):
            data = copy.deepcopy(old_hello['data']); data['connection_id'] = identifier; data['server_version_hex'] = version_hex
            candidate = encoded({'schema': 'recastpress.wpdb-rpc-reply.v0.5', 'seq': 1, 'ok': True, 'data': data}, True)
            candidate_count += 1
            if len(candidate) == hello['reply_frame_bytes'] and sha(candidate) == hello['reply_frame_sha256']:
                matches.append(profile)
    require('pinned_linux' in matches and all(dict(profiles)[profile] == contract['linux_version_hex'] for profile in matches), 'isolated_hello_exact_version_not_verified')
    # Setup replies also bind the complete session snapshot independently.
    require(all(operations[i]['reply_frame_sha256'] == prior['observation']['operations'][i]['reply_frame_sha256']
        for i in range(1, 5)), 'known_session_setup_reply_changed')
    require(prior['observation']['php_result']['session_snapshot'] == native['session_snapshot'], 'exact_native_session_snapshot')
    return {'backend': backend, 'initial_recorded_passed': run['passed'], 'initial_host_recorded_passed': host['passed'],
        'raw_verdicts_rewritten': False, 'whole_public_php_body_exact': True, 'known_php_body_identity': identity(body),
        'whole_public_cgi_wire_exact': True, 'known_cgi_wire_identity': identity(wire),
        'all_completion_and_stats_fields_verified_by_whole_body_identity': True,
        'all_31_case_states_and_22_sql_bytes_verified_by_whole_body_identity': True,
        'session_setup_reply_identity_equal_to_accepted_linux_reference': True,
        'isolated_hello_reply_identity': {'bytes': hello['reply_frame_bytes'], 'sha256': hello['reply_frame_sha256']},
        'hello_candidate_count': candidate_count, 'hello_matching_candidate_count': len(matches),
        'hello_version_profiles': sorted(set(matches)), 'connection_identifier_disclosed': False,
        'metadata_derivation': metadata['reference_derivation'], 'actual_guest_result_modified': False,
        'private_request_frames_or_nonce_digests_recomputed': False}


def audit(record=False):
    read(Path(__file__).resolve()); preparer = load_prepare(); metadata, metadata_identity = preparer.server_metadata()
    read(PROFILE / 'server-metadata.json', (metadata_identity['bytes'], metadata_identity['sha256']))
    pin = metadata['image_manifest']; read(ROOT / pin['path'], (pin['bytes'], pin['sha256']))
    backends = {backend: audit_backend(backend, metadata, preparer) for backend in ['mysql', 'mariadb']}
    proof = {'schema': 'recastpress.wpdb-utf8-platform-metadata-audit.v0.5', 'run_id': RUN, 'source_commit': SOURCE,
        'passed': True, 'backends': backends, 'input_files': INPUTS, 'runtime_acceptance_changed_by_audit': False,
        'raw_guest_body_or_wire_published': False, 'network_used': False, 'php_wasm_or_database_executed': False,
        'source_archive_module_tool_binaries_reverified': False, 'product_runtime_selected': False}
    wire = (json.dumps(proof, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    require(len(wire) <= 128*1024, 'audit_public_quota')
    if record:
        path = PROFILE / ('evidence/platform-metadata-audit-' + RUN + '.json')
        require(path.resolve() == path and path.parent.resolve() == path.parent, 'owned_audit_record_path')
        if path.exists(): require(path.read_bytes() == wire, 'immutable_audit_record_changed')
        else:
            with path.open('xb') as output: output.write(wire)
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--record', action='store_true'); args = parser.parse_args()
    try:
        result = audit(args.record)
        print('UTF8 platform metadata: two exact public bodies and isolated HELLO version profiles verified offline')
    except (Exception, KeyboardInterrupt) as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('UTF8 platform audit rejected: ' + code, file=sys.stderr); sys.exit(1)
