"""Exercise release staging and the CLI from a working directory outside the checkout."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'recastpress.wordpress-preview-release-verification.v0.5'


def require(value, code):
    if not value:
        raise ValueError(code)


def run(command, cwd, timeout=60):
    return subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
        timeout=timeout, check=False)


def check(binary, release, digest, cwd, accept):
    result = run([binary, 'check-wordpress-release', release, digest], cwd)
    if accept:
        require(result.returncode == 0 and not result.stderr and len(result.stdout) <= 1024,
            'release_check_success')
        observed = json.loads(result.stdout)
        require(observed == {'schema': SCHEMA, 'target': 'linux-x86_64', 'manifest_sha256': digest,
            'release_files': 11, 'wordpress_files': 3782, 'wordpress_directories': 467,
            'wordpress_bytes': 110077999}, 'release_check_report')
        return observed
    require(result.returncode == 2 and not result.stdout
        and result.stderr == b'WordPress release check failed\n', 'release_check_closed_failure')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='rp-wordpress-release-') as temporary:
        root = Path(temporary).resolve(strict=True)
        require(not root.is_relative_to(ROOT), 'release_test_outside_checkout')
        original = root / 'original'
        staging = [sys.executable, '-B', ROOT / 'scripts/stage_wordpress_preview_release.py', '--output', original]
        staged = run(staging, ROOT, timeout=600)
        require(staged.returncode == 0 and not staged.stderr and re.fullmatch(rb'[0-9a-f]{64}\n', staged.stdout),
            'preview_release_staging')
        digest = staged.stdout.decode('ascii').strip()
        again = run(staging, ROOT, timeout=600)
        require(again.returncode != 0 and not again.stdout, 'stage_existing_release_refused')
        original_report = check(binary, original, digest, root, True)
        release = root / 'relocated'
        original.rename(release)
        require(check(binary, release, digest, root, True) == original_report, 'relocated_identity_changed')

        wrong_digest = ('0' if digest[0] != '0' else '1') + digest[1:]
        check(binary, release, wrong_digest, root, False)
        manifest = release / 'release.json'
        canonical = manifest.read_bytes()
        duplicate = canonical.replace(b'"files":', b'"files":{},"files":', 1)
        require(duplicate != canonical, 'fixture_manifest_files_field')
        manifest.chmod(0o600)
        manifest.write_bytes(duplicate)
        check(binary, release, hashlib.sha256(duplicate).hexdigest(), root, False)
        manifest.write_bytes(canonical)
        owned_file = release / 'php/db.php'
        expected = owned_file.read_bytes()
        owned_file.chmod(0o600)
        owned_file.write_bytes(bytes([expected[0] ^ 1]) + expected[1:])
        check(binary, release, digest, root, False)
        owned_file.write_bytes(expected)
        extra = release / 'wordpress/site/wp-content/db.php'
        with extra.open('xb') as stream:
            stream.write(b'<?php')
        check(binary, release, digest, root, False)
        extra.unlink()
        external = root / 'external.php'
        owned_file.rename(external)
        owned_file.symlink_to(external)
        check(binary, release, digest, root, False)
        owned_file.unlink()
        check(binary, release, digest, root, False)
        external.rename(owned_file)
        require(check(binary, release, digest, root, True) == original_report, 'restored_identity_changed')

    print(json.dumps({'schema': 'recastpress.wordpress-preview-release-check.v0.5', 'passed': True,
        'verified_release': original_report, 'outside_checkout': True, 'relocated_directory': True,
        'rejected': ['existing_stage', 'wrong_manifest_digest', 'duplicate_manifest_field', 'changed_owned_php', 'extra_wordpress_dropin',
            'linked_owned_php', 'missing_owned_php'], 'staged_worker_executed': False}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        code = str(error) if type(error) is ValueError and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else 'io'
        print('WordPress preview release check failed: ' + code, file=sys.stderr)
        sys.exit(1)
