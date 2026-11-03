"""Compare clean public request contexts and complete WP responses on owned DBs."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import owned_probe


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


FROZEN_THEME_CONTROLLER = 'c929e15f5e3ecdd9e5c45e33ad36e66a972928a7a3fbffbcdc3dcfe6460cd3c3'
THEME_CONTROLLER = ROOT / 'experiments/php-wasix-wp-request-probe/run_check.py'
if hashlib.sha256(THEME_CONTROLLER.read_bytes()).hexdigest() != FROZEN_THEME_CONTROLLER:
    raise ValueError('fixed_theme_controller_changed')
theme = load('rp_public_context_frozen_theme_controller', THEME_CONTROLLER)
original = theme.original
oldbuild = theme.oldbuild
themebuild = theme.build
managed = theme.managed
build = load('rp_public_context_owned_build', HERE / 'build_check.py')
prepared = load('rp_public_context_owned_preparation', HERE / 'prepare.py')
linux = load('rp_public_context_owned_database', HERE / 'linux_database.py')
require, sha = build.require, build.sha
CONTEXT_FIELDS = ['case', 'is_home', 'is_404', 'transport_body',
    'context_before_bootstrap', 'context_after_bootstrap', 'context_after_dispatch']
COMPARE = theme.COMPARE + CONTEXT_FIELDS


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def response_validator():
    wire = THEME_CONTROLLER.read_bytes()
    require(sha(wire) == FROZEN_THEME_CONTROLLER, 'fixed_theme_validator_changed')
    source = wire.decode('utf-8')
    matches = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == 'validate_home']
    require(len(matches) == 1, 'one_fixed_theme_validator')
    text = ast.get_source_segment(source, matches[0])
    changes = [
        ('recastpress.php-wasix-wp-request-observation.v0.5', 'recastpress.php-wasix-wp-request-context-observation.v0.5'),
        ('fixed_wasix_wp_request_passed', 'fixed_wasix_wp_request_context_passed'),
        ('full_home_verified', 'full_context_verified'),
    ]
    derived = text
    for before, after in changes:
        require(derived.count(before) == 1, 'exact_context_validator_derivation')
        derived = derived.replace(before, after, 1)
    namespace = dict(theme.__dict__)
    exec(compile(derived, '<fixed-theme-context-validator>', 'exec'), namespace)
    return namespace['validate_home'], {'original_function_sha256': sha(text.encode()),
        'derived_function_sha256': sha(derived.encode()), 'changed_literals': len(changes),
        'original_semantic_response_rpc_and_cleanup_checks_retained': True}


def validate_context(value, case, native):
    require(case in ('home', 'missing') and value['case'] == case, 'actual_host_context_case')
    validate, derivation = response_validator()
    result = validate(value, 'home', native)
    php = value['php_result']
    require(php['schema'] == 'recastpress.php-wasix-wp-request-context-php-observation.v0.5'
        and php['outcome'] == 'fixed_wasix_context_passed', 'actual_php_context_profile')
    for key in CONTEXT_FIELDS:
        require(canonical(php[key]) == canonical(native[key]), 'native_public_context_' + key + '_differs')
    require(php['is_home'] is (case == 'home') and php['is_404'] is (case == 'missing'), 'actual_wp_context_route')
    require(php['response']['status'] == (200 if case == 'home' else 404), 'actual_inner_wp_status')
    require(not any(bytes.fromhex(h).lower().startswith(b'location:') for h in php['response']['headers_hex']),
        'original_wp_unexpected_redirect')
    result.update(compared_fields=COMPARE, context_snapshots_exact=True,
        actual_public_case=case, validator_derivation=derivation, response_normalized=False)
    return result


def execute(binary, environment, payload, report, label):
    pid, result = owned_probe.run_captured_probe([str(binary)], environment, timeout=300, input_bytes=canonical(payload))
    report.setdefault('executions', {})[label] = {'owned_pid': pid, 'owned_reaped': True,
        'exit_code': result.returncode, 'stdout_bytes': len(result.stdout), 'stderr_bytes': len(result.stderr),
        'stdout_sha256': sha(result.stdout), 'stderr_sha256': sha(result.stderr)}
    require(0 < len(result.stdout) <= 4*1024*1024 and result.stdout.endswith(b'\n')
        and result.stdout.count(b'\n') == 1 and len(result.stderr) <= 65536, 'context_host_output_shape')
    value = json.loads(result.stdout)
    report['observations'][label] = value
    require(result.returncode == 0 and value['passed'] is True and not result.stderr, 'context_host_execution_failed')
    return value


def sources():
    return {'input_files': build.inputs(), 'prepared': prepared.verify_prepared(),
        'bootstrap_prepared': original.prepared.verify_prepared(), 'theme_prepared': theme.prepared.verify_prepared()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--record', action='store_true'); args = parser.parse_args()
    report = {'schema': 'recastpress.php-wasix-wp-request-context-check.v0.5', 'passed': False,
        'outcome': 'not_run', 'backend': args.backend, 'platform': sys.platform,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'observations': {}, 'validations': {},
        'owned_cleanup_completed': False, 'response_normalized': False, 'http_listener_exercised': False,
        'native_php_executed': False, 'native_php_fallback': False, 'product_runtime_selected': False,
        'standard_wpdb_compatibility_verified': False, 'memory_cpu_limits_verified_for_this_integration': False,
        'all_resources_immediately_reclaimed': False, 'module_shared_across_owned_host_processes': False}
    passed = False; database = None; before = None; stage = 'source_preparation'
    try:
        require(sys.platform == 'linux', 'actual_php_wasm_linux_only')
        before = report['sources_before'] = sources()
        host = report['build_receipt'] = build.verify_build()
        bootstrap_host = report['bootstrap_build_receipt'] = oldbuild.verify_build()
        theme_host = report['theme_build_receipt'] = themebuild.verify_build()
        report['module_provenance'] = managed.verify_build()
        native = linux.reference(args.backend)
        report['native_reference'] = {'path': 'experiments/wp-request-context-probe/evidence/windows-' + args.backend + '-native.json',
            'sha256': linux.ORACLES[args.backend]}
        native_boot = original.reference(args.backend)
        environment = build.environment()
        database = linux.Database(args.backend, report)
        with database:
            for mode in ('install', 'read'):
                stage = 'php_wasm_' + mode
                value = execute(ROOT / bootstrap_host['executable'], environment, database.request(mode), report, mode)
                report['validations'][mode] = original.validate(value, mode, native_boot[mode])
            stage = 'fixed_public_dates'
            database.seed_public_dates()
            database.verify_context_state('before_requests')
            for case, label in (('home', 'home'), ('missing', 'missing'), ('missing', 'missing_repeat')):
                stage = 'php_wasm_' + label
                value = execute(ROOT / host['executable'], environment, database.context_request(case), report, label)
                expected = native['requests'][label]['observation']
                report['validations'][label] = validate_context(value, case, expected)
                database.verify_context_state(label, value['php_result'])
                require(sources() == before, 'sources_after_public_context_request_changed')
            require(report['observations']['missing']['php_result']['response']
                == report['observations']['missing_repeat']['php_result']['response'], 'fresh_wasm_repeat_404_differs')
            report['fresh_repeat_404_response_exact'] = True
        require(report['container_stopped'] is True and report['container_removed'] is True
            and report['container_normal_exit_verified'] is True, 'normal_context_container_cleanup')
        stage = 'after_verification'
        report['sources_after'] = sources()
        require(report['sources_after'] == before, 'context_source_after_cleanup_changed')
        require(build.verify_build() == host and oldbuild.verify_build() == bootstrap_host
            and themebuild.verify_build() == theme_host, 'context_binaries_or_receipts_changed')
        report['module_provenance_after'] = managed.verify_build()
        require(report['module_provenance_after'] == report['module_provenance'], 'context_module_changed')
        report['request_module_compilations'] = sum(report['observations'][label]['module_compilations']
            for label in ('home', 'missing', 'missing_repeat'))
        require(report['request_module_compilations'] == 3, 'one_module_compilation_per_fresh_request_host')
        report['source_reverified_after'] = True; passed = True
    except (Exception, KeyboardInterrupt) as error:
        report.update(failure_stage=stage, failure_class=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)):
            report['failure'] = str(error)
    finally:
        try:
            owned_probe.reap_owned_probes(); report['owned_cleanup_completed'] = True
        except (Exception, KeyboardInterrupt):
            passed = False; report['cleanup_failed'] = True
    report['passed'] = passed
    report['outcome'] = 'fixed_wasix_public_request_context_passed' if passed else 'wasix_public_request_context_failed'
    wire = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    if database is not None:
        for secret in database.private_values():
            require(secret.encode() not in wire and secret.encode().hex().encode() not in wire, 'context_public_secret_guard')
    require(len(wire) <= 12*1024*1024, 'public_context_report_quota')
    if args.record: build.preserve(HERE / ('evidence/linux-' + args.backend + '.json'), wire)
    print(report['outcome']); return 0 if passed else 1


if __name__ == '__main__': sys.exit(main())
