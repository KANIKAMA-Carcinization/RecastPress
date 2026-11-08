"""Derive the seven-call raw ERR guest from accepted PHP bridge and native fixtures."""

import argparse
from hashlib import sha256
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UTF8 = ROOT / 'experiments/php-wasix-wpdb-utf8-probe/fixtures'
NATIVE = ROOT / 'experiments/wpdb-raw-error-wire-probe/fixtures/native.php'
PINS = {
    UTF8 / 'bridge.php': 'f116ecde591e7912418128a9bd30f1455b92de911fbbc6dcf6f1fc5ba31eaf10',
    UTF8 / 'entry.php': 'dd8be48c44edc30a9122c5822a3057a415f035d33b73c9ddb2be3f662f55d080',
    UTF8 / 'adapter.php': '55f7042bebb6abbdd73104e52688a913dc38f2ae2b59abba292f303200f91b1f',
    UTF8 / 'helpers.php': 'f3a2278be589835cc869809216cab142f050d636da199697996fd2e90e12ee1b',
    NATIVE: '1e3c84880444bde5a68e62126945af342819e8a3074fd5a1d7ceba3ac00a49d8',
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def pinned(path):
    require(path.resolve(strict=True) == path and path.is_file(), 'raw_err_guest_source_path')
    with path.open('rb') as stream:
        wire = stream.read(128 * 1024 + 1)
    require(len(wire) <= 128 * 1024 and sha256(wire).hexdigest() == PINS[path], 'raw_err_guest_source_changed')
    return wire


def replace(wire, before, after):
    require(wire.count(before) == 1, 'raw_err_guest_unique_derivation_marker')
    return wire.replace(before, after, 1)


def region(wire, first, last):
    require(wire.count(first) == wire.count(last) == 1, 'raw_err_guest_region_markers')
    start, end = wire.index(first), wire.index(last)
    require(start < end, 'raw_err_guest_region_order')
    return wire[start:end]


def adapter():
    wire = pinned(UTF8 / 'adapter.php')
    wire = replace(wire, b"$message = rp_unhex($error['message_hex'], 65536);",
        b"$message = rp_unhex($error['message_hex'], 512);")
    wire = replace(wire, b"&& $error['raw_err_preserved'] === false && $message !== ''\n"
        b"                && preg_match('//u', $message) === 1,",
        b"&& $error['raw_err_preserved'] === true && $message !== ''\n"
        br'''                && !str_contains($message, "\0"),''')
    initial = b"private array $errorIdentity = ['code' => 0, 'state' => '00000'];"
    wire = replace(wire, initial, initial +
        b"\n    private array $rawErrorIdentity = ['code' => 0, 'state_hex' => '3030303030', 'message_hex' => ''];")
    error = b"$this->errorIdentity = ['code' => $error['code'], 'state' => $state];"
    wire = replace(wire, error, error +
        b"\n            $this->rawErrorIdentity = ['code' => $error['code'], 'state_hex' => $error['state_hex'],\n"
        b"                'message_hex' => $error['message_hex']];")
    reset = b"$this->errorIdentity = ['code' => 0, 'state' => '00000'];"
    wire = replace(wire, reset, reset +
        b"\n        $this->rawErrorIdentity = ['code' => 0, 'state_hex' => '3030303030', 'message_hex' => ''];")
    signature = b'    public function rpc_session_snapshot(): array {'
    return replace(wire, signature, b'    public function rpc_raw_error_identity(): array {\n'
        b'        return $this->rawErrorIdentity;\n'
        b'    }\n\n' + signature)


def corpus():
    body = region(pinned(NATIVE), b'function rp_err_rows(', b"ini_set('display_errors', '0');")
    body = replace(body, b'function rp_err_rows(wpdb $db): array {', b'function rp_err_rows(RpBridgeWpdb $db): array {')
    native_observation = region(body, b'        $message = mysqli_error($db->dbh);', b'    }\n    return $observations;')
    body = replace(body, native_observation,
        b"        $raw = $db->rpc_raw_error_identity();\n"
        b"        rp_exact_keys($raw, ['code', 'state_hex', 'message_hex'], 'raw_err_rpc_identity');\n"
        b"        rp_check($raw['message_hex'] === $state['last_error_hex'], 'raw_err_rpc_visible_bytes');\n"
        b"        $observations[$case] = ['query_hex' => bin2hex($sql), 'returned' => $returned, 'state' => $state,\n"
        b"            'last_result' => rp_value($db->last_result), 'rpc_error' => $raw];\n")
    return b'<?php\ndeclare(strict_types=1);\n\n' + body


def entry():
    wire = pinned(UTF8 / 'entry.php')
    unused = region(wire, b'/** Preserve finite PHP float arguments', b'function rp_crud_wp_files()')
    wire = replace(wire, unused, b'')
    for before, after in (
        (b'experiments/php-wasix-wpdb-utf8-probe/fixtures/', b'experiments/php-wasix-wpdb-raw-error-probe/fixtures/'),
        (b'recastpress.php-wasix-wpdb-utf8-php-observation.v0.5', b'recastpress.php-wasix-wpdb-raw-error-php-observation.v0.5'),
        (b'fixed_utf8_cases_passed', b'fixed_raw_error_cases_passed'),
        (b"    $report['outcome'] = 'fixed_utf8_cases_failed';", b"    $report['outcome'] = 'fixed_raw_error_cases_failed';"),
        (b"        'invalid_utf8_or_overlong_inputs_exercised' => false,", b"        'embedded_nul_exercised' => false,"),
        (b'        rp_crud_corpus($db, $report);',
         b"        $report['observations'] = rp_err_rows($db);\n"
         b"        $latin1 = rp_unhex($report['observations']['latin1_signal']['rpc_error']['message_hex'], 512);\n"
         b"        $report['raw_non_utf8_err_verified'] = preg_match('//u', $latin1) === false;\n"
         b"        rp_check($report['raw_non_utf8_err_verified'], 'raw_err_latin1_invalid_utf8_required');"),
        (b"        rp_check($db->num_queries === count($report['query_trace_hex']), 'crud_whole_query_counter_vs_filter');",
         b"        rp_check($db->num_queries === 7 && $db->num_queries === count($report['query_trace_hex']),\n"
         b"            'raw_err_exact_public_query_count');"),
    ):
        if before == b"    $report['outcome'] = 'fixed_utf8_cases_failed';":
            require(wire.count(before) == 3, 'raw_err_guest_failure_outcomes')
            wire = wire.replace(before, after)
        else:
            wire = replace(wire, before, after)
    return wire


def helpers():
    wire = pinned(UTF8 / 'helpers.php')
    wire = replace(wire, b'/__rp_utf8.php?rp_request=1', b'/__rp_raw_error.php?rp_request=1')
    wire = replace(wire, b'/rp-utf8/context.json', b'/rp-raw-error/context.json')
    return replace(wire, b'recastpress.php-wasix-wpdb-utf8-context.v0.5',
        b'recastpress.php-wasix-wpdb-raw-error-context.v0.5')


def guest_files():
    return {'bridge.php': pinned(UTF8 / 'bridge.php'), 'entry.php': entry(),
        'corpus.php': corpus(), 'adapter.php': adapter(), 'helpers.php': helpers()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    directory = HERE / 'fixtures'
    require(directory.resolve() == directory, 'raw_err_guest_output_path')
    files = guest_files()
    if not args.verify_only:
        directory.mkdir(exist_ok=True)
    for name, wire in files.items():
        path = directory / name
        require(path.resolve() == path, 'raw_err_guest_output_alias')
        if args.verify_only:
            require(path.read_bytes() == wire, 'raw_err_guest_generated_source_changed')
        else:
            path.write_bytes(wire)


if __name__ == '__main__':
    main()
