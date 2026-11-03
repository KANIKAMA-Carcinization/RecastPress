"""Install fixed WordPress and run ordinary bootstrap in one owned native DB."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import prepare
from owned_database import OwnedDatabase, db


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()


def exact(actual, expected, reason):
    db.require(canonical(actual) == canonical(expected), reason)


def inputs():
    paths = [p for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts
        and p.suffix in ('.py','.php')]
    paths += [ROOT / name for name in ['experiments/wpdb-bridge-probe/owned_database.py',
        'experiments/wpdb-rpc-probe/database_check.py', 'experiments/wpdb-rpc-probe/wire_check.py',
        'experiments/wpdb-rpc-probe/prepare_driver.py', 'experiments/wpdb-rpc-probe/native_check.py',
        'scripts/owned_probe.py', 'scripts/wordpress_reference.py', 'scripts/setup_mysql.py',
        'scripts/setup_mariadb.py', 'scripts/pinned_zip_tool.py',
        '.reference/mysql-8.4.13/recastpress-tool.json', '.reference/mariadb-11.8.9/recastpress-tool.json',
        '.reference/php-8.5.12/recastpress-tool.json', '.reference/mysql-release-key.asc',
        '.reference/mysql-8.4.13-winx64.zip.asc']]
    result = {}
    for path in sorted(set(paths)):
        db.require(path.resolve(strict=True) == path and path.is_file() and path.stat().st_size <= 2*1024*1024,
            'bootstrap_fixed_input')
        result[path.relative_to(ROOT).as_posix()] = db.sha(path.read_bytes())
    return result


def validate(value, mode):
    db.require(value['schema'] == 'recastpress.wp-bootstrap-native-observation.v0.5'
        and value['mode'] == mode and value['passed'] is True
        and value['outcome'] == 'fixed_native_bootstrap_passed' and value['phase'] == 'complete',
        'bootstrap_actual_observation')
    db.require(type(value['warning_count']) is int and value['warning_count'] == 0
        and value['source_core_theme_plugin_modified'] is False
        and value['outbound_mail_or_http_sent_by_fixture'] is False, 'bootstrap_no_warnings_or_outbound')
    for key in ['bootstrap_completed','shortinit_false','native_mysqli_handle','source_inventory_verified',
            'core_files_unchanged','physical_disconnect_completed']:
        db.require(value[key] is True, 'bootstrap_completed_' + key)
    db.require(value['install_completed'] is (mode == 'install') and value['wp_installing'] is (mode == 'install')
        and value['wp_version'] == '7.1.3' and value['known_wp_table_count'] == 12, 'bootstrap_original_wp_profile')
    exact(value['public_options'], {'blogname':'RP Bootstrap Fixture', 'siteurl':'http://rp-fixture.invalid',
        'home':'http://rp-fixture.invalid'}, 'bootstrap_public_options')
    db.require(type(value['public_posts']) is list and len(value['public_posts']) >= 2
        and type(value['autoload_option_count']) is int and value['autoload_option_count'] > 0
        and type(value['autoload_max_value_bytes']) is int and value['autoload_max_value_bytes'] > 0,
        'bootstrap_real_content_and_options')
    for key in ['blocked_mail_calls','blocked_http_calls']:
        db.require(type(value[key]) is int and value[key] >= 0, 'bootstrap_outbound_observation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql','mariadb'], required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    r = {'schema':'recastpress.wp-bootstrap-native-database.v0.5', 'passed':False, 'outcome':'not_run',
        'observed_at_utc':datetime.now(timezone.utc).isoformat(), 'backend':args.backend,
        'native_comparison_only':True, 'wasm_executed':False, 'rust_db_bridge_executed':False,
        'http_listener_exercised':False, 'standard_wpdb_compatibility_verified':False,
        'product_runtime_selected':False, 'full_wordpress_bootstrap_verified':False,
        'external_messages_authorized_or_sent_by_controller':False}
    passed, owned, stage = False, None, 'preflight'
    try:
        db.require(os.name == 'nt', 'bootstrap_fixed_native_windows')
        before = r['input_files'] = inputs()
        mapping = r['preparation_before'] = prepare.verify_prepared()
        reference = r['native_reference'] = prepare.references()
        stage = 'owned_database'
        owned = OwnedDatabase(args.backend, r)
        with owned:
            r['observations'] = {}
            for mode in ['install','read']:
                stage = 'native_' + mode
                result = owned.harness.run('native_wp_' + mode, prepare.native_command(mode),
                    timeout=180 if mode == 'install' else 120,
                    input_bytes=canonical(owned.request(mode)) + b'\n', limit=512*1024)
                r[mode + '_execution'] = {'exit_code':result.returncode, 'stdout_bytes':len(result.stdout),
                    'stderr_bytes':len(result.stderr), 'stdout_sha256':db.sha(result.stdout),
                    'stderr_sha256':db.sha(result.stderr)}
                db.require(result.stdout.endswith(b'\n') and result.stdout.count(b'\n') == 1,
                    'bootstrap_one_public_observation')
                value = r['observations'][mode] = db.parse_json(result.stdout)
                db.require(result.returncode == 0 and not result.stderr, 'bootstrap_native_execution_failed')
                validate(value, mode)
                exact(inputs(), before, 'bootstrap_inputs_changed')
                exact(prepare.verify_prepared(), mapping, 'bootstrap_source_tree_changed')
            for name in ['public_options','public_posts','known_wp_table_count']:
                exact(r['observations']['read'][name], r['observations']['install'][name],
                    'bootstrap_install_read_' + name + '_differs')
            stage = 'independent_final_verification'
            owned.verify_final()
            exact(prepare.references(), reference, 'bootstrap_native_reference_changed')
        db.require(r['normal_admin_shutdown_verified'] is True and r['database_closed_and_reaped'] is True
            and r['all_command_children_reaped'] is True, 'bootstrap_normal_cleanup_required')
        passed = True
        r.update(outcome='fixed_native_install_and_bootstrap_passed', full_wordpress_bootstrap_verified=True)
    except (Exception, KeyboardInterrupt) as error:
        r.update(outcome='fixed_native_bootstrap_failed', failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            r['failure'] = str(error)
    r['passed'] = passed
    wire = (json.dumps(r, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    if owned is not None:
        for secret in owned.secrets():
            db.require(secret.encode() not in wire and secret.encode().hex().encode() not in wire,
                'bootstrap_public_secret_guard')
    db.require(len(wire) <= 2*1024*1024, 'bootstrap_public_record_quota')
    if args.record:
        path = HERE / ('evidence/windows-' + args.backend + '-native.json')
        db.require(path.resolve() == path and path.parent.resolve() == path.parent, 'bootstrap_record_linked')
        path.parent.mkdir(exist_ok=True)
        with path.open('xb') as stream: stream.write(wire)
    print('WP native bootstrap: ' + r['outcome'] + ' (' + args.backend + ')')
    return 0 if passed else 1


if __name__ == '__main__': sys.exit(main())
