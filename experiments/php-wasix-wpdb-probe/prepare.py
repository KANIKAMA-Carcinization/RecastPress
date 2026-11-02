"""Pin four derived CGI fixtures and stage only five readonly WordPress sources.

No download, native PHP, Wasm, database, or network execution is performed.
Guest mirror labels deliberately retain the previous experiment's names;
compiled new source identities are recorded separately.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tarfile
import unicodedata

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1].resolve()
PREVIOUS = ROOT / 'experiments/wpdb-bridge-probe/fixtures'
WORK = ROOT / 'target/php-wasix-wpdb-probe'
DESTINATION = WORK / 'wp-core'
PREPARED = WORK / 'prepared.json'
ARCHIVE = ROOT / '.reference/wordpress-7.1.3.tar.gz'
ARCHIVE_SHA = 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
ARCHIVE_BYTES = 35368461
OLD_COMMIT = '4fda5212eeafa01a57116d7024001150c8a39dde'
FIXTURES = ['bridge.php', 'helpers.php', 'adapter.php', 'corpus.php']
OLD_PINS = {
    'bridge.php': (122, 'e90449a763e1ff80bae675e56d331864de9ac34b1855abc11b836c3c44accb4f'),
    'helpers.php': (12603, '1220aa52f8a967f7d4981a1b668e07c7343cdff9c10caf42ea99c32a348fccb1'),
    'adapter.php': (15853, 'e153d831fe35a2e40d4ffc34f434b9c4915e4a233ad4ffbb44f2ee90e2442a6d'),
    'corpus.php': (6838, 'b206c4353126cb3416f3935845c43e126f6170837608680f59131c60aa8b34bf'),
}
WP_PINS = {
    'load.php': (56475, '3efd0934bd8a454a8d7076fbbd21504ea097a753125a48e64b8a1dad380d357a'),
    'plugin.php': (37123, 'd70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d'),
    'class-wp-hook.php': (17584, 'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e'),
    'class-wp-filter-sentinel.php': (760, '68c9c675d3805a343835728d98a0efedf0df9833cb904bebf909c7e0fda5c61c'),
    'class-wpdb.php': (121437, 'e15403e90032dd0508811301505e491d4212de5152581a98f51b744a1a3903bd'),
}
BUSINESS_MARKER = b'/** Minimal fixed-corpus drop-in: upstream prepare/get_* remain inherited. */\n'
CGI_INPUT = b'''function rp_entry_input(string $variant): array {
    rp_check($variant === 'bridge', 'bridge_variant_only');
    rp_check(PHP_SAPI === 'cgi-fcgi' && ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET'
        && ($_SERVER['QUERY_STRING'] ?? '') === 'rp_wpdb=1'
        && ($_SERVER['argc'] ?? 0) === 0, 'cgi_entry_guard');
    $nonce = getenv('RP_WPDB_NONCE');
    rp_check(is_string($nonce) && preg_match('/\\A[0-9a-f]{48}\\z/D', $nonce) === 1,
        'input_nonce_shape');
    return ['nonce' => $nonce];
}

'''
FILE_CONNECT = b'''    public function connect(): void {
        rp_check($this->stream === null && !$this->closed, 'rpc_connect_reuse_refused');
        $this->stream = fopen('/rp-wpdb/channel', 'r+b');
        rp_check(is_resource($this->stream), 'rpc_channel_open_failed');
    }

'''


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(wire):
    return hashlib.sha256(wire).hexdigest()


def identity(wire):
    return {'bytes': len(wire), 'sha256': sha(wire)}


def parse_json(wire):
    def pairs(rows):
        value = {}
        for key, item in rows:
            require(key not in value, 'duplicate_prepared_json_key')
            value[key] = item
        return value
    def bad_number(_):
        raise ValueError('nonfinite_prepared_json_number')
    return json.loads(wire.decode('utf-8'), object_pairs_hook=pairs, parse_constant=bad_number)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()


def read(path, maximum):
    require(path.resolve(strict=True) == path and path.is_file(), 'fixed_input_missing_or_linked')
    require(0 < path.stat().st_size <= maximum, 'fixed_input_size')
    wire = path.read_bytes()
    require(len(wire) <= maximum, 'fixed_input_grew')
    return wire


def owned(path):
    require(WORK.resolve() == WORK and path.is_relative_to(WORK) and path.resolve() == path,
        'owned_path_escaped_or_linked')
    return path


def replace_once(wire, old, new):
    require(wire.count(old) == 1, 'fixed_derivation_context_changed')
    return wire.replace(old, new, 1)


def expected_fixtures(original):
    expected = {'corpus.php': original['corpus.php']}
    expected['bridge.php'] = replace_once(original['bridge.php'], b'declare(strict_types=1);\n',
        b"declare(strict_types=1);\nheader('Content-Type: application/json');\n")
    helper = original['helpers.php']
    start, end = b'function rp_entry_input(string $variant): array {\n', b'function rp_load_wordpress(array &$report): void {\n'
    require(helper.count(start) == helper.count(end) == 1, 'fixed_helper_entry_markers')
    first, last = helper.index(start), helper.index(end)
    require(first < last, 'fixed_helper_entry_order')
    helper = helper[:first] + CGI_INPUT + helper[last:]
    helper = replace_once(helper, b"'wasm_executed' => false,", b"'wasm_executed' => true,")
    helper = replace_once(helper,
        b"        rp_check(extension_loaded('mysqli') === ($variant === 'native'), 'entry_mysqli_profile');\n",
        b"        $report['native_driver_extension_present'] = extension_loaded('mysqli');\n"
        b"        rp_check($variant === 'bridge' && $report['native_driver_extension_present'] === true, 'entry_wasix_mysqli_profile');\n")
    helper = replace_once(helper,
        b"        $report['connected'] = true;\n",
        b"        $report['connected'] = true;\n"
        b"        $report['native_mysqli_handle_supplied'] = $db->dbh instanceof mysqli;\n"
        b"        rp_check($db instanceof RpBridgeWpdb && !$report['native_mysqli_handle_supplied'], 'rpc_opaque_driver_handle');\n")
    expected['helpers.php'] = helper
    adapter = original['adapter.php']
    require(adapter.count(BUSINESS_MARKER) == 1, 'fixed_adapter_business_marker')
    client, business = adapter.split(BUSINESS_MARKER)
    first = client.index(b'    public function connect(): void {\n')
    last = client.index(b'    public function request(string $op, string $bytes = \'\'): array {\n')
    require(first < last, 'fixed_transport_connect_markers')
    client = client[:first] + FILE_CONNECT + client[last:]
    client = replace_once(client, b'        $offset = 0;\n',
        b"        rp_check(rewind($this->stream), 'rpc_rewind_before_write_failed');\n        $offset = 0;\n")
    client = replace_once(client, b"        rp_check(fflush($this->stream), 'rpc_flush_failed');\n",
        b"        rp_check(fflush($this->stream), 'rpc_flush_failed');\n        rp_check(rewind($this->stream), 'rpc_rewind_before_read_failed');\n")
    client = replace_once(client,
        b"        $meta = stream_get_meta_data($this->stream);\n        rp_check(!$meta['timed_out'], 'rpc_read_timeout');\n", b'')
    client = replace_once(client,
        b"        $meta = stream_get_meta_data($this->stream);\n        rp_check($extra === '' && !$meta['timed_out'] && feof($this->stream), 'rpc_close_reply_not_eof');\n",
        b"        rp_check($extra === '' && feof($this->stream), 'rpc_close_reply_not_eof');\n")
    client = replace_once(client, b"            'transport_closed' => $this->closed, 'max_requests' => 64];\n",
        b"            'transport_closed' => $this->closed, 'max_requests' => 64,\n            'kind' => 'file-channel', 'channel_guest_path' => '/rp-wpdb/channel'];\n")
    require(all(token not in client for token in [b'stream_socket', b'stream_set_timeout', b'stream_set_blocking']),
        'network_transport_remained')
    expected['adapter.php'] = client + BUSINESS_MARKER + business
    return expected


def derivation_gate():
    original = {}
    for name in FIXTURES:
        wire = read(PREVIOUS / name, 128 * 1024)
        require((len(wire), sha(wire)) == OLD_PINS[name], 'previous_source_pin_changed')
        original[name] = wire
    expected = expected_fixtures(original)
    for name in FIXTURES:
        require(read(HERE / 'fixtures' / name, 128 * 1024) == expected[name], 'derived_fixture_not_exact')
    require({p.name for p in (HERE / 'fixtures').iterdir()} == set(FIXTURES), 'derived_fixture_set_changed')
    old_business = original['adapter.php'].split(BUSINESS_MARKER, 1)[1]
    require(expected['adapter.php'].split(BUSINESS_MARKER, 1)[1] == old_business,
        'adapter_business_logic_changed')
    return {'fixed_old_source_commit': OLD_COMMIT, 'old_git_objects_verified_by_preparer': False,
        'old_sources': {name: identity(wire) for name, wire in original.items()},
        'derived_sources': {name: identity(expected[name]) for name in FIXTURES},
        'corpus_bytes_unchanged': True, 'adapter_business_bytes_unchanged': True,
        'adapter_business_sha256': sha(old_business),
        'allowed_helper_changes': ['CGI-GET-query-argc-and-env-nonce-entry-only', 'wasm-executed-context-flag',
            'fixed-WASIX-built-in-mysqli-profile-and-opaque-RPC-handle'],
        'allowed_transport_changes': ['file-channel-open', 'request-and-reply-rewinds', 'no-socket-timeouts', 'channel-stat-labels']}


def input_hashes():
    paths = [Path(__file__).resolve(), ROOT / 'scripts/wordpress_reference.py']
    paths += [PREVIOUS / name for name in FIXTURES]
    paths += [HERE / 'fixtures' / name for name in FIXTURES]
    return {p.relative_to(ROOT).as_posix(): sha(read(p, 512 * 1024)) for p in paths}


def archive_sources():
    archive = read(ARCHIVE, ARCHIVE_BYTES)
    require(identity(archive) == {'bytes': ARCHIVE_BYTES, 'sha256': ARCHIVE_SHA}, 'wordpress_archive_identity')
    spec = importlib.util.spec_from_file_location('rp_wasm_wpdb_archive_paths', ROOT / 'scripts/wordpress_reference.py')
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    selected, seen, count, expanded = {}, set(), 0, 0
    with tarfile.open(ARCHIVE, 'r|gz') as source:
        for member in source:
            count += 1
            require(count <= 10000 and 0 <= member.size <= 32 * 1024 * 1024, 'wordpress_member_quota')
            expanded += member.size
            require(expanded <= 256 * 1024 * 1024 and (member.isdir() or member.isfile()),
                'wordpress_expansion_or_member_type')
            relative = reference.safe_relative(member.name)
            key = unicodedata.normalize('NFC', relative.as_posix()).casefold()
            require(key not in seen, 'wordpress_duplicate_member')
            seen.add(key)
            name = relative.as_posix().removeprefix('wp-includes/')
            if relative.as_posix() != 'wp-includes/' + name or name not in WP_PINS:
                continue
            require(member.isfile() and member.size == WP_PINS[name][0], 'wordpress_fixed_source_type_or_size')
            with source.extractfile(member) as stream:
                wire = stream.read(WP_PINS[name][0] + 1)
            require((len(wire), sha(wire)) == WP_PINS[name], 'wordpress_fixed_source_identity')
            selected[name] = wire
    require(set(selected) == set(WP_PINS), 'wordpress_five_source_set')
    require(sha(read(ARCHIVE, ARCHIVE_BYTES)) == ARCHIVE_SHA, 'wordpress_archive_changed')
    return selected, {'path': ARCHIVE.relative_to(ROOT).as_posix(), 'bytes': ARCHIVE_BYTES,
        'sha256': ARCHIVE_SHA, 'members_checked': count, 'declared_expanded_bytes_checked': expanded}


def staged_wordpress(selected):
    owned(DESTINATION)
    require(DESTINATION.is_dir() and {p.name for p in DESTINATION.iterdir()} == set(WP_PINS),
        'prepared_wordpress_file_set')
    rows = {}
    for name in WP_PINS:
        path = owned(DESTINATION / name)
        require(read(path, 256 * 1024) == selected[name], 'prepared_wordpress_source_changed')
        require(stat.S_IMODE(path.stat().st_mode) & 0o222 == 0, 'prepared_wordpress_not_readonly')
        rows[name] = {'path': path.relative_to(ROOT).as_posix(), **identity(selected[name])}
    return rows


def metadata(selected, archive, before):
    wp = staged_wordpress(selected)
    files = []
    for name in FIXTURES:
        source = HERE / 'fixtures' / name
        files.append({'source': source.relative_to(ROOT).as_posix(),
            'guest': '/experiments/wpdb-bridge-probe/fixtures/' + name,
            **identity(read(source, 128 * 1024)), 'guest_read_only': True, 'kind': 'derived_fixture'})
    for name, row in wp.items():
        files.append({'source': row['path'], 'guest': '/.reference/wordpress-7.1.3/wp-includes/' + name,
            'bytes': row['bytes'], 'sha256': row['sha256'], 'guest_read_only': True, 'kind': 'wordpress_source'})
    require(len(files) == len({r['guest'] for r in files}) == 9, 'nine_unique_guest_files_required')
    return {'schema': 'recastpress.php-wasix-wpdb-prepared.v0.5', 'outcome': 'fixed_nine_guest_sources_prepared',
        'input_files': before, 'derivation': derivation_gate(), 'source_archive': archive,
        'files': files, 'wordpress_files': wp, 'wordpress_version': '7.1.3',
        'guest_path_labels': 'previous experiment mirror paths; new compiled-source hashes are separate',
        'full_wordpress_bootstrap': False, 'wordpress_executed': False, 'wasm_executed_by_preparer': False,
        'native_php_executed_by_preparer': False, 'network_used_by_preparer': False,
        'guest_network_enabled': False, 'guest_readonly_enforcement_executed': False,
        'wordpress_raw_source_git_objects_verified': False, 'product_abi_selected': False,
        'inputs_unchanged': True}


def verify_prepared():
    before = input_hashes()
    derivation_gate()
    selected, archive = archive_sources()
    expected = metadata(selected, archive, before)
    wire = read(owned(PREPARED), 256 * 1024)
    require(canonical(parse_json(wire)) == canonical(expected), 'prepared_receipt_changed_or_stale')
    require(input_hashes() == before, 'prepared_inputs_changed')
    return expected


def prepare():
    before = input_hashes()
    derivation_gate()
    selected, archive = archive_sources()
    owned(DESTINATION)
    if not DESTINATION.exists():
        DESTINATION.mkdir(parents=True, exist_ok=False)
        for name, wire in selected.items():
            path = owned(DESTINATION / name)
            with path.open('xb') as output:
                output.write(wire)
            path.chmod(0o444)
    report = metadata(selected, archive, before)
    require(input_hashes() == before, 'preparation_inputs_changed')
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    owned(PREPARED)
    if PREPARED.exists():
        require(read(PREPARED, 256 * 1024) == wire, 'existing_prepared_receipt_changed_or_stale')
    else:
        with PREPARED.open('xb') as output:
            output.write(wire)
    require(verify_prepared() == report, 'prepared_after_verification_failed')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    require(not (args.verify_only and args.validate_only), 'choose_one_prepare_mode')
    if args.validate_only:
        derivation_gate()
        print('Wasm wpdb fixture derivation validated; no sources staged or PHP executed')
    else:
        result = verify_prepared() if args.verify_only else prepare()
        print('Wasm wpdb prepare: ' + result['outcome'] + '; nine finite guest sources; no PHP/DB execution')
    return 0


if __name__ == '__main__':
    sys.exit(main())
