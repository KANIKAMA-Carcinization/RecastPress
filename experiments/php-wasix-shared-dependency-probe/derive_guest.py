"""Derive two fixed PHP fixtures for a private, read-only dependency trial."""

import argparse
from hashlib import sha256
from pathlib import Path


HERE = Path(__file__).resolve().parent
ACCEPTED = HERE.parent / 'php-wasix-wp-request-context-probe/fixtures'
PINS = {
    'adapter.php': '55f7042bebb6abbdd73104e52688a913dc38f2ae2b59abba292f303200f91b1f',
    'bootstrap.php': 'f1cecedec9428cf26ba247173244315ce355b80fde010f0d042c5dad49ca1dfb',
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def read(path):
    require(path.resolve(strict=True) == path and path.is_file(), 'dependency_guest_source_path')
    with path.open('rb') as source:
        wire = source.read(128 * 1024 + 1)
    require(len(wire) <= 128 * 1024, 'dependency_guest_source_bound')
    return wire


def pinned(name):
    wire = read(ACCEPTED / name)
    require(sha256(wire).hexdigest() == PINS[name], 'dependency_guest_source_changed')
    return wire


def replace(wire, before, after):
    require(wire.count(before) == 1, 'dependency_guest_unique_marker')
    return wire.replace(before, after, 1)


def guest_adapter():
    wire = pinned('adapter.php')
    wire = replace(wire, b"'escape' => 0, 'close' => 0];",
        b"'escape' => 0, 'close' => 0, 'read_fixture_dependency' => 0];")
    before = b'        ++$this->seq;'
    wire = replace(wire, before, b'''        rp_check($op !== 'read_fixture_dependency' || ($bytes === '' && $this->counts[$op] === 0),
            'rpc_request_shape');
''' + before)
    before = b'    public function rpc_stats(): array {'
    return replace(wire, before, b'''    public function read_fixture_dependency(): array {
        rp_check(func_num_args() === 0, 'dependency_trial');
        $reply = $this->rpc->request('read_fixture_dependency');
        rp_check($reply['ok'], 'dependency_trial');
        $data = $reply['data'];
        rp_exact_keys($data, ['value_hex', 'source'], 'dependency_trial');
        $expected = 'recastpress-shared-fixture';
        $bytes = rp_unhex($data['value_hex'], strlen($expected));
        rp_check($bytes === $expected && in_array($data['source'], ['miss_fill', 'hit'], true), 'dependency_trial');
        return ['source' => $data['source'], 'bytes' => strlen($bytes), 'sha256' => hash('sha256', $bytes)];
    }

''' + before)


def guest_bootstrap():
    wire = pinned('bootstrap.php')
    wire = replace(wire, b'const RP_PUBLIC_FAILURE_CODES = [', b'const RP_PUBLIC_FAILURE_CODES = ["dependency_trial",')
    before = b"    $rp_report['context_after_bootstrap'] = rp_context_snapshot('after_bootstrap');\n"
    wire = replace(wire, before, before + b'''    $rp_stock_cache = $GLOBALS['wp_object_cache'];
    $rp_stock_ext_flag = wp_using_ext_object_cache();
    rp_boot_check(get_class($rp_stock_cache) === WP_Object_Cache::class
        && ($rp_stock_ext_flag === null || $rp_stock_ext_flag === false), 'dependency_trial');
    $rp_dependency_trial = $wpdb->read_fixture_dependency();
    rp_boot_check($GLOBALS['wp_object_cache'] === $rp_stock_cache
        && wp_using_ext_object_cache() === $rp_stock_ext_flag, 'dependency_trial');
''')
    before = b"    $rp_report['passed'] = true; $rp_report['outcome'] = 'fixed_wasix_context_passed';"
    return replace(wire, before,
        b"    $rp_report['dependency_trial'] = $rp_dependency_trial + ['stock_cache_unchanged' => true];\n" + before)


def guest_files():
    return {'adapter.php': guest_adapter(), 'bootstrap.php': guest_bootstrap()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    directory = HERE / 'fixtures'
    require(directory.resolve() == directory, 'dependency_guest_output_directory')
    files = guest_files()
    if not args.verify_only:
        directory.mkdir(exist_ok=True)
    for name, wire in files.items():
        path = directory / name
        require(path.resolve() == path, 'dependency_guest_output_path')
        if args.verify_only:
            require(read(path) == wire, 'dependency_guest_generated_source_changed')
        else:
            path.write_bytes(wire)


if __name__ == '__main__':
    main()
