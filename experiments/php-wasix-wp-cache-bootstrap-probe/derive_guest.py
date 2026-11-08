"""Derive the stock-cache shim and private post-bootstrap cache trial guest files."""

import argparse
from hashlib import sha256
from pathlib import Path
import runpy


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BOOTSTRAP = ROOT / 'experiments/php-wasix-wp-request-context-probe/fixtures/bootstrap.php'
SHIM = ROOT / 'experiments/wp-object-cache-dropin-probe/fixtures/stock-transparent.php'
ADAPTER_DERIVE = ROOT / 'experiments/php-wasix-wp-object-cache-probe/derive_guest.py'
PINS = {
    BOOTSTRAP: 'f1cecedec9428cf26ba247173244315ce355b80fde010f0d042c5dad49ca1dfb',
    SHIM: 'c3ad7334e940852b74726a15ef9346d45b6d15ac9d23d726eb3fcb46dd92c5f4',
    ADAPTER_DERIVE: '486375a887dbd72418637c19853cae63539db7e758fc8544324b78775a4715b2',
}
ADAPTER_SHA = '3e64b755e0f101fbe9b11fa5ea6e286f4f1b68a4aa6c0b3460afe2ac2463636b'


def require(condition, code):
    if not condition:
        raise ValueError(code)


def read(path):
    require(path.resolve(strict=True) == path and path.is_file(), 'cache_bootstrap_fixed_source')
    with path.open('rb') as stream:
        wire = stream.read(128 * 1024 + 1)
    require(len(wire) <= 128 * 1024, 'cache_bootstrap_source_bound')
    if path in PINS:
        require(sha256(wire).hexdigest() == PINS[path], 'cache_bootstrap_source_changed')
    return wire


def replace(wire, before, after):
    require(wire.count(before) == 1, 'cache_bootstrap_unique_derivation_marker')
    return wire.replace(before, after, 1)


def guest_bootstrap():
    wire = read(BOOTSTRAP)
    shim_hash = PINS[SHIM].encode('ascii')
    wire = replace(wire, b'const RP_PUBLIC_FAILURE_CODES = [', b'const RP_PUBLIC_FAILURE_CODES = ["cache_trial",')
    wire = replace(wire, b'$overlay_seen = false;', b'$overlay_seen = false; $cache_shim_seen = false;')
    inventory = b"        if ($relative === 'wp-content/db.php') {"
    wire = replace(wire, inventory, b'''        if ($relative === 'wp-content/object-cache.php') {
            rp_boot_check(!$cache_shim_seen && $entry->isFile() && hash_file('sha256', $path) === 'HASH', 'cache_trial');
            $cache_shim_seen = true; continue;
        }
'''.replace(b'HASH', shim_hash) + inventory)
    wire = replace(wire, b'&& $overlay_seen && $total', b'&& $overlay_seen && $cache_shim_seen && $total')
    after_bootstrap = b"    $rp_report['context_after_bootstrap'] = rp_context_snapshot('after_bootstrap');\n"
    wire = replace(wire, after_bootstrap, after_bootstrap + b'''    require_once __DIR__ . '/cache-trial.php';
    try {
        $rp_cache_trial = rp_cache_bootstrap_trial();
    } catch (RuntimeException $rp_cache_error) {
        throw new RpBootstrapFailure('cache_trial');
    }
''')
    success = b"    $rp_report['passed'] = true; $rp_report['outcome'] = 'fixed_wasix_context_passed';"
    return replace(wire, success, b"    $rp_report['cache_trial'] = $rp_cache_trial;\n" + success)


def guest_files():
    read(ADAPTER_DERIVE)
    adapter = runpy.run_path(str(ADAPTER_DERIVE))['guest_adapter']()
    require(sha256(adapter).hexdigest() == ADAPTER_SHA, 'cache_bootstrap_generated_adapter_changed')
    return {'bootstrap.php': guest_bootstrap(), 'cache-adapter.php': adapter,
        'cache-trial.php': read(HERE / 'cache-trial.php'), 'object-cache.php': read(SHIM)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    directory = HERE / 'fixtures'
    require(directory.resolve() == directory, 'cache_bootstrap_output_directory')
    files = guest_files()
    if not args.verify_only:
        directory.mkdir(exist_ok=True)
    for name, wire in files.items():
        path = directory / name
        require(path.resolve() == path, 'cache_bootstrap_output_alias')
        if args.verify_only:
            require(read(path) == wire, 'cache_bootstrap_generated_file_changed')
        else:
            path.write_bytes(wire)


if __name__ == '__main__':
    main()
