"""Derive the finite private Wasm plugin guest from the accepted context fixture."""

import argparse
from hashlib import sha256
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONTEXT = ROOT / 'experiments/php-wasix-wp-request-context-probe/fixtures'
PLUGIN_NAME = 'rp-admission-benign.php'
PLUGIN_FILE = 'rp-admission-benign/' + PLUGIN_NAME
PLUGIN_PATH = 'wp-content/plugins/' + PLUGIN_FILE
PLUGIN_SHA = '038309389588dceb42897ea9638c646559dcdc3021e53b9de3bb85aca5abbd83'
PLUGIN = ROOT / 'experiments/wp-plugin-admission-probe/fixtures' / PLUGIN_NAME
PINS = {
    'home.php': '7df64fb630fc9b3710f549cba26ebee97b2ace4501bb99d07a23539d24c9ea6b',
    'bootstrap.php': 'f1cecedec9428cf26ba247173244315ce355b80fde010f0d042c5dad49ca1dfb',
    'context.php': '9fe91c097c17354b8163b3334c9b9b52b8aa18a2765c59c35ee84c14c97a8a4c',
    'helpers.php': 'e854d9b6c4b8ac3d27381086f8a8c9b984de949782ca6f24350c6e0294ef421d',
    'adapter.php': '55f7042bebb6abbdd73104e52688a913dc38f2ae2b59abba292f303200f91b1f',
    'db.php': '5e48035bdc93b159237e9742eb90b296748e5a20f96b83c2ef2e1148f225df15',
}
FAILURES = {
    'trial_plugin_directory', 'trial_plugin_bytes', 'trial_plugins_loaded',
    'trial_activation_requires_inactive', 'trial_plugin_activation',
    'trial_active_option', 'trial_render_option_changed',
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def digest(wire):
    return sha256(wire).hexdigest()


def pinned(path, expected):
    require(path.resolve(strict=True) == path and path.is_file(), 'plugin_guest_source_path')
    with path.open('rb') as stream:
        wire = stream.read(256 * 1024 + 1)
    require(len(wire) <= 256 * 1024 and digest(wire) == expected, 'plugin_guest_source_changed')
    return wire


def replace(wire, before, after):
    require(wire.count(before) == 1, 'plugin_guest_unique_derivation_marker')
    return wire.replace(before, after, 1)


def failures(wire):
    prefix = b'const RP_PUBLIC_FAILURE_CODES = '
    rows = [line for line in wire.splitlines() if line.startswith(prefix)]
    require(len(rows) == 1 and rows[0].endswith(b';'), 'plugin_guest_failure_allowlist')
    values = json.loads(rows[0][len(prefix):-1])
    require(type(values) is list and len(set(values)) == len(values) and not set(values) & FAILURES,
        'plugin_guest_distinct_failure_codes')
    return replace(wire, rows[0], prefix + json.dumps(sorted(set(values) | FAILURES), separators=(',', ':')).encode('ascii') + b';')


def plugin_inventory(wire):
    wire = replace(wire, b'    $actual_files = []; $actual_dirs = []; $total = 0; $overlay_seen = false;',
        b'    $actual_files = []; $actual_dirs = []; $total = 0; $overlay_seen = false;\n'
        b'    $trial_dir_seen = false; $trial_file_seen = false;')
    needle = b'        if ($entry->isDir()) { $actual_dirs[] = $relative; continue; }'
    wire = replace(wire, needle,
        b"        if ($relative === 'wp-content/plugins/rp-admission-benign') {\n"
        b"            rp_boot_check(!$trial_dir_seen && $entry->isDir(), 'trial_plugin_directory');\n"
        b"            $trial_dir_seen = true; continue;\n"
        b"        }\n"
        b"        if ($relative === '" + PLUGIN_PATH.encode('ascii') + b"') {\n"
        b"            rp_boot_check(!$trial_file_seen && $entry->isFile()\n"
        b"                && hash_file('sha256', $path) === '" + PLUGIN_SHA.encode('ascii') + b"', 'trial_plugin_bytes');\n"
        b"            $trial_file_seen = true; continue;\n"
        b"        }\n" + needle)
    return replace(wire, b'&& $overlay_seen && $total === 110077999',
        b'&& $overlay_seen && $trial_dir_seen && $trial_file_seen && $total === 110077999')


def activation_or_render(wire):
    wire = replace(wire, b"$rp_mode = defined('RP_BOOTSTRAP_MODE') ? RP_BOOTSTRAP_MODE : null;",
        b"const RP_TRIAL_PLUGIN = '" + PLUGIN_FILE.encode('ascii') + b"';\n"
        b"function rp_trial_active_plugins(): array {\n"
        b"    $plugins = get_option('active_plugins');\n"
        b"    rp_boot_check($plugins === [] || $plugins === [RP_TRIAL_PLUGIN], 'trial_active_option');\n"
        b"    return $plugins;\n"
        b"}\n\n"
        b"$rp_mode = defined('RP_BOOTSTRAP_MODE') ? RP_BOOTSTRAP_MODE : null;")
    wire = replace(wire, b"    'anonymous_request' => false, 'theme_rendered' => false, 'response' => null,",
        b"    'anonymous_request' => false, 'theme_rendered' => false, 'response' => null,\n"
        b"    'active_before' => null, 'active_after' => null, 'activation_succeeded' => false,")
    wire = replace(wire, b"    rp_boot_check(PHP_SAPI === 'cgi-fcgi' && ($_SERVER['argc'] ?? 0) === 0 && $rp_mode === 'home'",
        b"    rp_boot_check(PHP_SAPI === 'cgi-fcgi' && ($_SERVER['argc'] ?? 0) === 0 && $rp_mode === 'home'\n"
        b"        && RP_REQUEST_CASE === 'home' && defined('RP_PLUGIN_ACTIVATE') && is_bool(RP_PLUGIN_ACTIVATE)")
    begin, end = b"    $rp_report['phase'] = 'wp_dispatch';\n", b"    $rp_report['phase'] = 'public_observations';\n"
    require(wire.count(begin) == wire.count(end) == 1, 'plugin_guest_dispatch_markers')
    first, last = wire.index(begin), wire.index(end)
    require(first < last, 'plugin_guest_dispatch_order')
    dispatch = b''.join(b'    ' + line if line.strip() else line for line in wire[first:last].splitlines(keepends=True))
    return wire[:first] + (
        b"    rp_boot_check(did_action('plugins_loaded') === 1, 'trial_plugins_loaded');\n"
        b"    $rp_report['active_before'] = rp_trial_active_plugins();\n"
        b"    if (RP_PLUGIN_ACTIVATE) {\n"
        b"        rp_boot_check($rp_report['active_before'] === [], 'trial_activation_requires_inactive');\n"
        b"        require_once ABSPATH . 'wp-admin/includes/plugin.php';\n"
        b"        $activation = activate_plugin(RP_TRIAL_PLUGIN);\n"
        b"        rp_boot_check($activation === null && is_plugin_active(RP_TRIAL_PLUGIN)\n"
        b"            && did_action('wp') === 0 && did_action('wp_footer') === 0, 'trial_plugin_activation');\n"
        b"        $rp_report['active_after'] = rp_trial_active_plugins();\n"
        b"        rp_boot_check($rp_report['active_after'] === [RP_TRIAL_PLUGIN], 'trial_plugin_activation');\n"
        b"        $rp_report['activation_succeeded'] = true;\n"
        b"    } else {\n" + dispatch +
        b"        $rp_report['active_after'] = rp_trial_active_plugins();\n"
        b"        rp_boot_check($rp_report['active_after'] === $rp_report['active_before'], 'trial_render_option_changed');\n"
        b"    }\n"
    ) + wire[last:]


def guest_files():
    files = {name: pinned(CONTEXT / name, expected) for name, expected in PINS.items()}
    entry = files.pop('home.php')
    include = b"require __DIR__ . '/bootstrap.php';"
    for stage, setting in (('activate', b'true'), ('render', b'false')):
        files[f'home-{stage}.php'] = replace(entry, include,
            b"define('RP_PLUGIN_ACTIVATE', " + setting + b");\n" + include)
    files['bootstrap.php'] = activation_or_render(plugin_inventory(failures(files['bootstrap.php'])))
    files[PLUGIN_NAME] = pinned(PLUGIN, PLUGIN_SHA)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    directory = HERE / 'fixtures'
    require(directory.resolve() == directory, 'plugin_guest_output_path')
    files = guest_files()
    if not args.verify_only:
        directory.mkdir(exist_ok=True)
    for name, wire in files.items():
        path = directory / name
        require(path.resolve() == path, 'plugin_guest_output_alias')
        if args.verify_only:
            require(path.read_bytes() == wire, 'plugin_guest_generated_bytes_changed')
        else:
            path.write_bytes(wire)


if __name__ == '__main__':
    main()
