"""Activate one admitted fixture through native WordPress on an owned Windows database."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

import admission


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/wp-plugin-admission-probe'
CONTEXT = ROOT / 'experiments/wp-request-context-probe'
PLUGIN_ID = 'rp-admission-benign'
PLUGIN_FILE = 'rp-admission-benign/rp-admission-benign.php'
PLUGIN_PATH = 'wp-content/plugins/' + PLUGIN_FILE
PLUGIN_SHA = '038309389588dceb42897ea9638c646559dcdc3021e53b9de3bb85aca5abbd83'
MARKER = b'<!-- rp-admission-benign:wpdb-and-request-cache-ok -->'
PINS = {
    'database_check.py': '7dd86f2a2f78323187293c2d6499054b0e98f69539812f14ebd1fa81866e0977',
    'fixtures/bootstrap.php': '3aaa6a9399d48e2f6e4ab362b9f83fadf407dd26614771f731c785b92aa1a10d',
    'fixtures/context.php': '90298bb19119200ab6db8458aef4e6ffabf8fdbc85708ecb5aff54627081c2c4',
    'fixtures/home.php': '7df64fb630fc9b3710f549cba26ebee97b2ace4501bb99d07a23539d24c9ea6b',
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(wire):
    return hashlib.sha256(wire).hexdigest()


def source(name):
    wire = (CONTEXT / name).read_bytes()
    require(sha(wire) == PINS[name], 'accepted_native_source_changed')
    return wire


def native():
    source('database_check.py')
    spec = importlib.util.spec_from_file_location('rp_plugin_fixed_context', CONTEXT / 'database_check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replace(wire, before, after):
    require(wire.count(before) == 1, 'unique_plugin_derivation_marker')
    return wire.replace(before, after, 1)


def bootstrap():
    wire = source('fixtures/bootstrap.php')
    wire = replace(wire, b"        $rp_report['failure'] = 'unexpected_php_shutdown'; rp_boot_emit();",
        b"        $rp_report['failure'] = 'unexpected_php_shutdown';\n"
        b"        $rp_report['shutdown'] = ['status' => http_response_code(),\n"
        b"            'redirect' => count(array_filter(headers_list(), static fn($h) => stripos($h, 'Location:') === 0)) > 0,\n"
        b"            'error_type' => error_get_last()['type'] ?? null];\n"
        b"        rp_boot_emit();")
    wire = replace(wire, b"    $rp_site = $rp_root . DIRECTORY_SEPARATOR . 'target' . DIRECTORY_SEPARATOR . 'wp-bootstrap-probe' . DIRECTORY_SEPARATOR . 'site';",
        b"    rp_boot_check($rp_root === dirname(__DIR__), 'owned_plugin_trial_root');\n"
        b"    rp_boot_check(ini_get('max_execution_time') === '120', 'owned_trial_execution_deadline');\n"
        b"    $rp_site = $rp_root . DIRECTORY_SEPARATOR . 'site';")
    wire = replace(wire, b'    $actual_files = []; $actual_dirs = []; $total = 0;',
        b'    $actual_files = []; $actual_dirs = []; $total = 0; $trial_dir = false; $trial_file = false;')
    wire = replace(wire, b'        if ($entry->isDir()) { $actual_dirs[] = $relative; continue; }',
        b"        if ($relative === 'wp-content/plugins/rp-admission-benign') {\n"
        b"            rp_boot_check(!$trial_dir && $entry->isDir(), 'trial_plugin_directory');\n"
        b"            $trial_dir = true; continue;\n"
        b"        }\n"
        b"        if ($relative === '" + PLUGIN_PATH.encode('ascii') + b"') {\n"
        b"            rp_boot_check(!$trial_file && $entry->isFile() && hash_file('sha256', $path) === '"
        + PLUGIN_SHA.encode('ascii') + b"', 'trial_plugin_bytes');\n"
        b"            $trial_file = true; continue;\n"
        b"        }\n"
        b'        if ($entry->isDir()) { $actual_dirs[] = $relative; continue; }')
    wire = replace(wire, b'&& $total === 110077999', b'&& $trial_dir && $trial_file && $total === 110077999')
    wire = replace(wire, b"    $rp_report['phase'] = 'wp_dispatch';",
        b"    rp_boot_check(did_action('plugins_loaded') === 1, 'normal_plugins_loaded');\n"
        b"    $rp_report['active_before'] = get_option('active_plugins');\n"
        b"    if (RP_PLUGIN_ACTIVATE) {\n"
        b"        rp_boot_check($rp_report['active_before'] === [], 'trial_inactive_before_activation');\n"
        b"        require_once ABSPATH . 'wp-admin/includes/plugin.php';\n"
        b"        $result = activate_plugin('" + PLUGIN_FILE.encode('ascii') + b"');\n"
        b"        rp_boot_check($result === null && is_plugin_active('" + PLUGIN_FILE.encode('ascii')
        + b"'), 'native_activate_plugin_failed');\n"
        b"        $rp_report['activation_succeeded'] = true;\n"
        b"    } else {\n"
        b"    $rp_report['phase'] = 'wp_dispatch';")
    return replace(wire, b"    $rp_report['theme_rendered'] = true;",
        b"    $rp_report['theme_rendered'] = true;\n"
        b"    }\n"
        b"    $rp_report['active_after'] = get_option('active_plugins');")


def checked_path(path):
    require(WORK.resolve() == WORK and path.is_relative_to(WORK) and path != WORK
        and path.resolve() == path, 'owned_plugin_trial_path')
    return path


def stage(ctx, trial, prepared):
    path, wire = admission.approved_trial(PLUGIN_ID)
    require(path == PLUGIN_PATH and sha(wire) == PLUGIN_SHA, 'admitted_plugin_identity')
    original = ctx.home.original_prepare
    source_manifest = original.SOURCE_MANIFEST.read_bytes()
    require(sha(source_manifest) == prepared['source_manifest']['sha256'], 'trial_original_manifest')
    (trial / 'source-manifest.json').write_bytes(source_manifest)
    inventory = json.loads(source_manifest)
    site = checked_path(trial / 'site')
    site.mkdir()
    for name in inventory['directories']:
        checked_path(site / name).mkdir()
    for name, identity in inventory['files'].items():
        source_file = original.WP_SITE / name
        require(source_file.resolve() == source_file and source_file.is_file(), 'trial_original_file')
        with source_file.open('rb') as stream:
            data = stream.read(identity['bytes'] + 1)
        require(len(data) == identity['bytes'] and sha(data) == identity['sha256'], 'trial_original_bytes')
        with checked_path(site / name).open('xb') as stream:
            stream.write(data)
    plugin = checked_path(site / path)
    plugin.parent.mkdir()
    with plugin.open('xb') as stream:
        stream.write(wire)


def sql_active(owner, label, active):
    # Verify persisted WordPress serialization independently without exposing option bytes.
    expected = ('a:1:{i:0;s:' + str(len(PLUGIN_FILE)) + ':"' + PLUGIN_FILE + '";}' if active else 'a:0:{}')
    wire = owner._sql('independent_plugin_option_' + label,
        b"SELECT HEX(option_value) FROM rp_wpdb_probe.rp_options WHERE option_name='active_plugins';\n", 4096)
    require(wire.strip() == expected.encode('ascii').hex().upper().encode('ascii'), 'independent_active_plugins')


def admission_decisions():
    decisions = {}
    for candidate, disposition in ((PLUGIN_ID, 'trial_allowed'), (admission.COLLISION, 'refused'),
            (admission.HELD, 'held')):
        value = admission.assess(candidate)
        require(value['disposition'] == disposition and value['execution_started'] is False
            and value['trial_mount_authorized'] == (candidate == PLUGIN_ID), 'native_trial_admission_gate')
        decisions[candidate] = value
    return decisions


def verify_unmounted(trial, decisions):
    for candidate in (admission.COLLISION, admission.HELD):
        for row in decisions[candidate]['files']:
            path = checked_path(trial / 'site' / row['destination'])
            require(not path.exists() and not path.is_symlink(), 'refused_or_held_trial_mounted')


def request(ctx, trial, owner, capture, validate, mapping, label, report):
    directory = checked_path(trial / label)
    directory.mkdir()
    activating = label == 'activation'
    entry = replace(source('fixtures/home.php'), b"require __DIR__ . '/bootstrap.php';",
        b"define('RP_PLUGIN_ACTIVATE', " + (b'true' if activating else b'false') + b");\n"
        b"require __DIR__ . '/bootstrap.php';")
    private = ctx.canonical({'schema': 'recastpress.wp-request-context-private.v0.5', 'case': 'home',
        'repository_root': str(trial), 'request': owner.request('home')})
    require(0 < len(private) <= 32768, 'trial_bounded_private_context')
    # The copied Windows tree is hashed twice under the same bounded CGI owner.
    # Keep the PHP deadline below the owner's 180-second process deadline.
    files = {'home.php': entry, 'context.php': source('fixtures/context.php'), 'bootstrap.php': bootstrap(),
        'context.json': private,
        'php.ini': ctx.startup.config() + b'display_errors=1\nlog_errors=0\nmax_execution_time=120\n'}
    for name, data in files.items():
        with (directory / name).open('xb') as stream:
            stream.write(data)
    process, stdout, stderr = capture([str(ctx.startup.PHP / 'php-cgi.exe')], ctx.environment(directory, 'home'))
    row = report.setdefault('requests', {})[label] = {'process': process,
        'stdout': ctx.identity(stdout), 'stderr': ctx.identity(stderr)}
    value, outer = ctx.home.parse_cgi(stdout)
    if value.get('passed') is False and value.get('failure') in {'native_activate_plugin_failed',
            'php_warning', 'php_throwable', 'trial_plugin_bytes', 'trial_plugin_directory',
            'closed_native_entry', 'owned_plugin_trial_root', 'full_normal_bootstrap_required',
            'public_secret_guard', 'unexpected_php_shutdown', 'trial_inactive_before_activation'}:
        row['php_failure'] = value['failure']
        if value.get('phase') in {'input', 'source_inventory', 'wp_settings', 'wp_dispatch',
                'template_loader', 'public_observations', 'source_after'}:
            row['php_phase'] = value['phase']
        status = value.get('shutdown')
        if type(status) is dict and set(status) == {'status', 'redirect', 'error_type'} and all(
                type(number) in (bool, int, type(None)) for number in status.values()):
            row['php_shutdown'] = status
    require(process['exit_code'] == 0 and not stderr and value['passed'] is True
        and outer['status'] == 200 and len(outer['headers']) == 1
        and bytes.fromhex(outer['headers'][0]['line_hex']) == b'Content-Type: application/json', 'clean_plugin_cgi_exit')
    require(value['phase'] == 'complete' and value['physical_disconnect_completed'] is True
        and value['core_files_unchanged'] is True and value['wp_version'] == '7.1.3', 'plugin_bootstrap_completion')
    require(value['private_context_before'] == value['private_context_after'] == ctx.identity(private)
        and value['transport_body'] == ctx.identity(b''), 'private_plugin_context_unchanged')
    for name, data in files.items():
        require((directory / name).read_bytes() == data, 'trial_cgi_inputs_changed')
    wanted = [] if label in ('inactive', 'activation') else [PLUGIN_FILE]
    ctx.exact(value['active_before'], wanted, 'plugin_loader_active_before')
    ctx.exact(value['active_after'], [] if label == 'inactive' else [PLUGIN_FILE], 'plugin_loader_active_after')
    row.update(active_before=value['active_before'], active_after=value['active_after'])
    if activating:
        require(value['activation_succeeded'] is True and value['response'] is None
            and value['theme_rendered'] is False, 'native_activation_only')
        row['activation_succeeded'] = True
        return None
    row['validation'] = validate(value, mapping, 'home')
    for phase in ('before_bootstrap', 'after_bootstrap', 'after_dispatch'):
        ctx.exact(value['context_' + phase], ctx.expected_snapshot('home', phase), 'native_plugin_public_context')
    body = bytes.fromhex(value['response']['body_hex'])
    count = body.count(MARKER)
    require(count == (0 if label == 'inactive' else 1), 'plugin_footer_marker_count')
    row['marker_count'] = count
    return value['response']['headers_hex'], body


def run(backend):
    report = {'schema': 'recastpress.wp-plugin-admission-native-check.v0.5', 'backend': backend,
        'passed': False, 'native_only': True, 'wordpress_version': '7.1.3', 'plugin_sha256': PLUGIN_SHA,
        'owned_trial_removed': False}
    ctx = owner = trial = None
    source_before = None
    stage_name = 'source_preflight'
    try:
        require(os.name == 'nt' and backend in ('mysql', 'mariadb'), 'native_plugin_platform')
        ctx = native()
        original = ctx.home.original_prepare
        source_before = original.verify_prepared()
        php_before = ctx.startup.package_gate()
        mapping = {'core_inventory': source_before['source_tree'], 'core_manifest': source_before['source_manifest']}
        validate, report['response_validation_derivation'] = ctx.response_validator()
        capture, report['capture_derivation'] = ctx.capture_function()
        baseline = ctx.home.baseline(backend)
        decisions = report['preactivation_decisions'] = admission_decisions()
        WORK.mkdir(exist_ok=True)
        trial = checked_path(WORK / ('native-' + uuid.uuid4().hex))
        trial.mkdir(mode=0o700)
        stage_name = 'owned_tree_staging'
        stage(ctx, trial, source_before)
        verify_unmounted(trial, decisions)
        owner = ctx.home.owner.OwnedDatabase(backend, report)
        stage_name = 'owned_database'
        with owner:
            for mode in ('install', 'read'):
                stage_name = 'original_native_' + mode
                result = owner.harness.run(stage_name, original.native_command(mode), timeout=180,
                    input_bytes=ctx.canonical(owner.request(mode)) + b'\n', limit=512*1024)
                require(result.returncode == 0 and not result.stderr and result.stdout.endswith(b'\n')
                    and result.stdout.count(b'\n') == 1, 'plugin_native_bootstrap_exit')
                value = ctx.home.db.parse_json(result.stdout)
                ctx.home.original.validate(value, mode)
                for key in ctx.home.BASELINE_FIELDS:
                    ctx.exact(value[key], baseline['observations'][mode][key], 'plugin_native_original_baseline')
            owner.capture_private_password_guard()
            owner.seed_public_dates()
            sql_active(owner, 'before', False)
            report['refused_and_held_unmounted_while_active_plugins_empty'] = True
            outputs = {}
            for label in ('inactive', 'activation', 'active', 'active_repeat'):
                stage_name = 'native_plugin_' + label
                outputs[label] = request(ctx, trial, owner, capture, validate, mapping, label, report)
                sql_active(owner, label, label != 'inactive')
            require(outputs['active'] == outputs['active_repeat'], 'fresh_plugin_response_repeat_exact')
            old_headers, old_body = outputs['inactive']
            headers, body = outputs['active']
            require(headers == old_headers and b''.join(body.split(MARKER)) == old_body, 'only_fixed_plugin_marker_added')
            verify_unmounted(trial, decisions)
            owner.verify_final()
        require(report['normal_admin_shutdown_verified'] and report['database_closed_and_reaped']
            and report['all_command_children_reaped']
            and len(set(ctx.REAPED_CHILDREN)) == 4 and not ctx.OWNED_CHILDREN, 'native_plugin_process_cleanup')
        report.update(activation_via_wordpress=True, fresh_requests=2, fresh_response_repeat_exact=True,
            exact_response_difference='one_fixed_footer_marker', plugin_status_via_independent_sql=True)
        stage_name = 'source_postflight'
        ctx.exact(original.verify_prepared(), source_before, 'original_wordpress_tree_changed')
        ctx.exact(ctx.startup.package_gate(), php_before, 'original_php_package_changed')
        report['original_sources_unchanged'] = True
        report['passed'] = True
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage_name, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        if ctx is not None:
            for pid, child in tuple(ctx.OWNED_CHILDREN.items()):
                try:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)
                    for stream in (child.stdin, child.stdout, child.stderr):
                        stream.close()
                    ctx.OWNED_CHILDREN.pop(pid)
                except (Exception, KeyboardInterrupt):
                    report['passed'] = False
        if trial is not None and trial.exists() and not ctx.OWNED_CHILDREN:
            try:
                shutil.rmtree(checked_path(trial))
                report['owned_trial_removed'] = True
            except (Exception, KeyboardInterrupt):
                report['passed'] = False
    report['passed'] = report['passed'] and report['owned_trial_removed']
    wire = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    for secret in ([] if owner is None else owner.secrets()) + [str(ROOT), ROOT.as_posix()]:
        require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'native_plugin_secret_guard')
    return report, wire


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    report, wire = run(args.backend)
    if args.record:
        path = HERE / 'evidence' / ('windows-' + args.backend + '-native.json')
        path.parent.mkdir(exist_ok=True)
        with path.open('xb') as stream:
            stream.write(wire)
    sys.stdout.buffer.write(wire)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
