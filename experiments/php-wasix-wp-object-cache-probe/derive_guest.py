"""Derive the private Wasm object-cache CGI fixture from the accepted native bridge."""

import argparse
from hashlib import sha256
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ADAPTER = ROOT / 'experiments/wp-object-cache-rust-probe/fixtures/adapter.php'
BRIDGE = ROOT / 'experiments/wp-object-cache-rust-probe/fixtures/bridge.php'
CORPUS = ROOT / 'experiments/wp-object-cache-contract-probe/fixtures/corpus.php'
PINS = {
    ADAPTER: 'c85f12b4d8e827995a820db4b661106d6ffa8c373b8edb9a39415dd24d1a7235',
    BRIDGE: 'fb325dc672aae25ff97923abb3073244e4b0d3f2be83f50c4e8396ba2c7703d2',
    CORPUS: 'be08a2bf3a782dccd8bd929d9fdbb8f685d66693a1b5f25c0599c2118dd4937d',
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def pinned(path):
    require(path.resolve(strict=True) == path and path.is_file(), 'cache_guest_source_path')
    with path.open('rb') as stream:
        wire = stream.read(128 * 1024 + 1)
    require(len(wire) <= 128 * 1024 and sha256(wire).hexdigest() == PINS[path], 'cache_guest_source_changed')
    return wire


def replace(wire, before, after):
    require(wire.count(before) == 1, 'cache_guest_unique_derivation_marker')
    return wire.replace(before, after, 1)


def section(wire, first, last, replacement):
    require(wire.count(first) == wire.count(last) == 1, 'cache_guest_section_markers')
    start, end = wire.index(first), wire.index(last)
    require(start < end, 'cache_guest_section_order')
    return wire[:start] + replacement + wire[end:]


RELAY_FIELDS = b'''    private $channel = null;
    private array $groups = ['rp-cache-multiple' => 0, 'rp-cache-integer' => 0];
    private int $flushes = 0;

    public function __construct() {
        $this->channel = fopen('/rp-object-cache/channel', 'r+b');
        rp_bridge_check(is_resource($this->channel), 'relay_channel_open');
    }

'''

EXCHANGE = br'''        $wire = json_encode($request, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . "\n";
        // One complete small frame is sent at a time. The private filesystem
        // seals the write at fflush and exposes only its corresponding reply.
        rp_bridge_check(strlen($wire) < 1024 && rewind($this->channel)
            && fwrite($this->channel, $wire) === strlen($wire) && fflush($this->channel)
            && rewind($this->channel), 'relay_request_write');
        $line = fgets($this->channel, 1025);
        rp_bridge_check(is_string($line) && strlen($line) < 1024 && str_ends_with($line, "\n")
            && fread($this->channel, 1) === '', 'relay_reply_frame');
        $reply = json_decode($line, true, 8, JSON_THROW_ON_ERROR);
'''

RELAY_FINISH = b'''        $closed = fclose($this->channel);
        $this->channel = null;
        rp_bridge_check($closed, 'relay_channel_close');
        return ['groups' => $this->groups, 'flushes' => $this->flushes,
            'finish' => $reply, 'channel_closed' => true];
    }

    public function close(): void {
        if (is_resource($this->channel)) {
            fclose($this->channel);
            $this->channel = null;
        }
    }
}

'''

GUEST_PROFILE = b'''    if (PHP_SAPI !== 'cgi-fcgi' || PHP_VERSION_ID !== 80512 || PHP_INT_SIZE !== 8
        || ($_SERVER['argc'] ?? 0) !== 0 || ($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET'
        || ($_SERVER['SCRIPT_FILENAME'] ?? '') !== '/fixture/entry.php' || __FILE__ !== '/fixture/entry.php'
        || php_ini_loaded_file() !== false || !in_array(php_ini_scanned_files(), [false, ''], true)
        || file_get_contents('php://input') !== '') {
        throw new RuntimeException('wasm_cgi_profile');
    }
    define('ABSPATH', '/wordpress/');
'''


def guest_adapter():
    wire = pinned(ADAPTER)
    wire = section(wire, b'    private const CAP', b'    private static function fields(', RELAY_FIELDS)
    wire = section(wire, b"        rp_bridge_check(hrtime(true) < $this->deadline, 'relay_request_deadline');",
        b'        rp_bridge_check(is_array($reply)', EXCHANGE)
    return section(wire, b'        $status = $this->wait_exit($this->deadline);',
        b'/** Inherit stock multiple-key APIs', RELAY_FINISH)


def guest_entry():
    wire = section(pinned(BRIDGE), b"    if (PHP_SAPI !== 'cli'", b"    define('WPINC', 'wp-includes');", GUEST_PROFILE)
    wire = replace(wire, b"$root . '/experiments/wp-object-cache-contract-probe/fixtures/corpus.php'",
        b"__DIR__ . '/corpus.php'")
    wire = replace(wire, b'new RpCacheRelay($binary)', b'new RpCacheRelay()')
    wire = replace(wire, b'recastpress.wp-object-cache-rust-native-observation.v0.5',
        b'recastpress.php-wasix-wp-object-cache-guest-observation.v0.5')
    return replace(wire, b'echo json_encode($report, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES)',
        b"header('Content-Type: application/json', true, 200);\n"
        b'echo json_encode($report, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES)')


def guest_files():
    files = {'entry.php': guest_entry(), 'adapter.php': guest_adapter(), 'corpus.php': pinned(CORPUS)}
    forbidden = (b'proc_open', b'proc_get_status', b'proc_close', b'proc_terminate', b'rust.stdout',
        b'rust.stderr', b'cargo-target', b'$binary', b'$argv')
    require(not any(name in wire for wire in files.values() for name in forbidden), 'cache_guest_native_transport_retained')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    directory = HERE / 'fixtures'
    require(directory.resolve() == directory, 'cache_guest_output_path')
    files = guest_files()
    if not args.verify_only:
        directory.mkdir(exist_ok=True)
    for name, wire in files.items():
        path = directory / name
        require(path.resolve() == path, 'cache_guest_output_alias')
        if args.verify_only:
            require(path.read_bytes() == wire, 'cache_guest_generated_source_changed')
        else:
            path.write_bytes(wire)


if __name__ == '__main__':
    main()
