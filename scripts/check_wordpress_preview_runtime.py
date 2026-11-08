"""Serve the fixed WordPress preview from a release moved outside the checkout."""

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile

from check_wordpress_preview_release import check, run
from stage_wordpress_preview_release import stage_release


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'experiments/php-wasix-wp-session-http-probe'
PREFIX = 'recastpress.php-wasix-wp-release-http-'
ORIGINAL_PREFIX = 'recastpress.php-wasix-wp-session-http-'


def require(value, code):
    if not value:
        raise ValueError(code)


def replace_once(source, before, after):
    require(source.count(before) == 1, 'preview_controller_derivation')
    return source.replace(before, after, 1)


def frozen(name, digest):
    path = BASE / name
    wire = path.read_bytes()
    require(hashlib.sha256(wire).hexdigest() == digest, 'preview_controller_source')
    return path, wire.decode('utf-8')


def baseline_controller():
    path, source = frozen('run_check.py', 'cf750dff9a77d9cbac1d64bdf6c568bbfba0e5aab3087107329eb81b6ed5e8e6')
    spec = importlib.util.spec_from_file_location('rp_preview_baseline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    node = next(node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == 'validate_service')
    validator = ast.get_source_segment(source, node)
    require(validator.count("'worker_build'") == 2, 'preview_worker_identity_derivation')
    validator = validator.replace("'worker_build'", "'release_worker'")
    require(validator.count("'source_files_rehashed_by_service',") == 2, 'preview_service_scope_derivation')
    validator = validator.replace("'source_files_rehashed_by_service',", '')
    validator = replace_once(validator, "'startup_private_input_used'):",
        "'startup_private_input_used', 'release_files_verified_by_service'):")
    validator = replace_once(validator, "'release_worker',", "'release_worker', 'release_files_verified_by_service',")
    namespace = {**vars(module), 'PREFIX': PREFIX}
    exec(compile(validator, '<fixed-release-http-validator>', 'exec'), namespace)
    return module, namespace['validate_service']


def supervisor(captures):
    _, source = frozen('server_process.py', 'bba7f9f9716f218c0b22c616bcdb0cc961c306efbb1fad0618ca1335ad418d41')
    for before, after in (
        ("ROOT = Path(__file__).resolve().parents[2]\nWORK = ROOT / 'target/php-wasix-wp-session-http-probe/server-runs'",
         'WORK = OWNED_CAPTURES'),
        ('payload, report, canonical, identity, parse_json):',
         'payload, report, canonical, identity, parse_json, release, manifest_sha, capture_parent, cwd):\n'
         '        self.release_sha = manifest_sha'),
        ('subprocess.Popen([str(binary)], cwd=ROOT,',
         "subprocess.Popen([binary, 'preview-wordpress', release, manifest_sha, capture_parent], cwd=cwd,"),
        ("{'schema', 'port', 'session_ready'}", "{'schema', 'port', 'session_ready', 'release_manifest_sha256'}"),
        ("or type(value['port']) is not int", "or value['release_manifest_sha256'] != self.release_sha\n"
         "                            or type(value['port']) is not int"),
        (ORIGINAL_PREFIX + 'ready.v0.5', PREFIX + 'ready.v0.5'),
        (ORIGINAL_PREFIX + 'observation.v0.5', PREFIX + 'observation.v0.5'),
    ):
        source = replace_once(source, before, after)
    namespace = {'OWNED_CAPTURES': captures}
    exec(compile(source, '<fixed-release-http-supervisor>', 'exec'), namespace)
    return namespace['Server'], namespace['reap_owned_servers']


def compare_backend(backend, checker, release, digest, roles, outside):
    base, validate_service = baseline_controller()
    captures = outside / ('adapter-' + backend)
    guest_captures = outside / ('worker-' + backend)
    guest_captures.mkdir(mode=0o700)
    server_class, reap_servers = supervisor(captures)
    context = base.context
    native = context.linux.reference(backend)
    native_bootstrap = context.original.reference(backend)
    wire_references = base.observer.native_references()[backend]
    environment = base.session.build.environment()
    bootstrap = context.oldbuild.verify_build()
    report = {'observations': {}, 'validations': {}}
    database = context.linux.Database(backend, report)
    expected_ready = base.expected_ready(roles['php/php.wasm']['sha256'])
    worker_identity = {'manifest_sha256': digest, 'executable': 'bin/worker',
        'executable_identity': roles['bin/worker']}
    result = []
    try:
        with database:
            for mode in ('install', 'read'):
                value = context.execute(ROOT / bootstrap['executable'], environment, database.request(mode), report, mode)
                context.original.validate(value, mode, native_bootstrap[mode])
            database.seed_public_dates()
            database.verify_context_state('before_requests')
            payload = {'schema': PREFIX + 'input.v0.5', 'database': database.home_request()}
            with server_class(checker, {}, payload, report, base.canonical, base.identity,
                    base.http.process.parse_json, release, digest, guest_captures, outside) as server:
                base.equal(server.ready, expected_ready)
                for label in base.observer.REJECTIONS:
                    base.observe(server, label)
                for label in base.LABELS:
                    case = 'home' if label == 'home' else 'missing'
                    observation = base.observe(server, case, wire_references[case])
                    database.verify_context_state(label)
                    result.append({key: observation[key] for key in ('status', 'body_bytes', 'body_sha256')})
                service = server.finish()
                validate_service(service, report['http_server_execution'], worker_identity,
                    roles['php/php.wasm']['sha256'], native, report)
        require(all(report.get(key) is True for key in ('container_stopped', 'container_removed',
            'container_normal_exit_verified')), 'preview_database_normal_cleanup')
        return {'responses': result, 'rejected_requests': len(base.observer.REJECTIONS),
            'worker_processes': 1, 'module_compilations_total': service['module_compilations_total'],
            'request_module_compilations_total': service['request_module_compilations_total'],
            'owned_processes_reaped': True, 'owned_database_normal_cleanup': True}
    finally:
        reap_servers()
        context.owned_probe.reap_owned_probes()


def main():
    from build_wordpress_preview_runtime import build_runtime

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    args = parser.parse_args()
    checker = args.binary.resolve(strict=True)
    runtime = build_runtime()
    with tempfile.TemporaryDirectory(prefix='rp-wordpress-runtime-') as temporary:
        outside = Path(temporary).resolve(strict=True)
        require(not outside.is_relative_to(ROOT), 'preview_runtime_outside_checkout')
        original = outside / 'original'
        digest = stage_release(original, runtime=runtime)
        release = outside / 'relocated'
        original.rename(release)
        before = check(checker, release, digest, outside, True)
        capture = outside / 'rejected-capture'
        capture.mkdir(mode=0o700)
        wrong = ('0' if digest[0] != '0' else '1') + digest[1:]
        invalid = run([checker, 'preview-wordpress', release, wrong, capture], outside)
        require(invalid.returncode == 2 and not invalid.stdout
            and invalid.stderr == b'WordPress preview launch failed\n'
            and not any(capture.iterdir()), 'preview_launcher_rejects_wrong_manifest')
        roles = json.loads((release / 'release.json').read_bytes())['files']
        backends = {name: compare_backend(name, checker, release, digest, roles, outside) for name in ('mysql', 'mariadb')}
        require(check(checker, release, digest, outside, True) == before, 'preview_release_changed')
    print(json.dumps({'schema': 'recastpress.wordpress-preview-runtime-check.v0.5', 'passed': True,
        'verified_release': before, 'outside_checkout': True, 'relocated_directory': True,
        'product_cli_launch_verified': True, 'product_cli_wrong_manifest_refused': True,
        'build': runtime, 'backends': backends}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        code = str(error) if type(error) is ValueError and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('WordPress preview runtime check failed: ' + code, file=sys.stderr)
        sys.exit(1)
