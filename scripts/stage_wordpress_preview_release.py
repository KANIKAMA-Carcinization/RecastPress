"""Stage the verified Linux WordPress preview inputs for release preflight.

The standalone command stages the original repository-bound binary specimens.
The release runtime controller can supply the two separately built relocated
executables. Stdout from the standalone command is the SHA-256 of release.json.
"""

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / 'experiments'
SCHEMA = 'recastpress.wordpress-preview-release.v0.5'
TARGET = 'linux-x86_64'
FIXTURES = ('home.php', 'missing.php', 'context.php', 'bootstrap.php',
            'helpers.php', 'adapter.php', 'db.php')
CORE_MANIFEST = 'target/wp-bootstrap-probe/source-manifest.json'
CORE_SITE = ROOT / 'target/wp-bootstrap-probe/site'
MAX_MANIFEST = 2 * 1024 * 1024
MAX_BINARY = 256 * 1024 * 1024
MAX_WASM = 128 * 1024 * 1024
MAX_FIXTURE = 256 * 1024
MAX_CORE_FILE = 16 * 1024 * 1024
COPY_CHUNK = 128 * 1024
RENAME_NOREPLACE = 1
RUNTIME_BUILD_SCHEMA = 'recastpress.wordpress-preview-runtime-build.v0.5'
RELOCATED_BINARIES = {
    'http': 'target/php-wasix-wp-http-probe/cargo-target/release/recastpress-php-wasix-wp-release-http-probe',
    'worker': 'target/php-wasix-wp-bootstrap-probe/cargo-target/release/recastpress-php-wasix-wp-release-worker-probe',
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def load(experiment, filename):
    path = EXPERIMENTS / experiment / filename
    spec = importlib.util.spec_from_file_location('rp_release_stage_' + experiment.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def relative(name):
    path = PurePosixPath(name)
    require(path.parts and not path.is_absolute() and path.as_posix() == name
        and '..' not in path.parts and not any(c in name for c in ('\\', ':', '\0')),
        'release_input_relative_path')
    return Path(*path.parts)


def identity(record, limit):
    size, digest = record['bytes'], record['sha256']
    require(type(size) is int and 0 <= size <= limit and type(digest) is str
        and re.fullmatch('[a-f0-9]{64}', digest) is not None, 'release_input_identity')
    return {'bytes': size, 'sha256': digest}


def copy_checked(source, target, record, limit, executable=False):
    expected = identity(record, limit)
    require(source.resolve(strict=True) == source, 'release_input_alias')
    digest = hashlib.sha256()
    remaining = expected['bytes']
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as origin:
        metadata = os.fstat(origin.fileno())
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_size == remaining,
            'release_input_type_or_size')
        with target.open('xb') as destination:
            while remaining:
                block = origin.read(min(COPY_CHUNK, remaining))
                require(block, 'release_input_truncated')
                remaining -= len(block)
                digest.update(block)
                destination.write(block)
            require(not origin.read(1) and digest.hexdigest() == expected['sha256'],
                'release_input_content_changed')
            os.fchmod(destination.fileno(), 0o500 if executable else 0o400)
    return expected


def runtime_artifacts(runtime):
    if runtime is None:
        adapter = load('php-wasix-wp-session-http-probe', 'build_check.py')
        return {'http': adapter.verify_build(), 'worker': adapter.sessionbuild.verify_build()}
    require(runtime['schema'] == RUNTIME_BUILD_SCHEMA and runtime['platform'] == TARGET
        and runtime['source_reverified_after'] is True, 'release_runtime_build_profile')
    result = {}
    for role, expected in RELOCATED_BINARIES.items():
        record = runtime[role]
        require(set(record) == {'executable', 'executable_identity'} and record['executable'] == expected,
            'release_runtime_fixed_binary')
        result[role] = record
    return result


def copy_release(stage, runtime=None):
    executables = runtime_artifacts(runtime)
    module = load('php-wasix-managed-access-probe', 'managed_check.py').verify_build()['module']
    prepared = load('php-wasix-wp-request-context-probe', 'prepare.py').verify_prepared()
    context_base = 'experiments/php-wasix-wp-request-context-probe/fixtures/'
    fixtures = {row['source']: row for row in prepared['files']}
    require(set(fixtures) == {context_base + name for name in FIXTURES}
        and len(prepared['files']) == len(FIXTURES)
        and prepared['core_manifest']['path'] == CORE_MANIFEST
        and prepared['core_site'] == CORE_SITE.relative_to(ROOT).as_posix(), 'release_fixed_preparation')

    for name in ('bin', 'php', 'wordpress', 'wordpress/site'):
        (stage / name).mkdir(mode=0o700)
    files = {}
    for name, record in executables.items():
        member = 'bin/' + name
        files[member] = copy_checked(ROOT / relative(record['executable']), stage / member,
            record['executable_identity'], MAX_BINARY, executable=True)
    files['php/php.wasm'] = copy_checked(ROOT / relative(module['path']), stage / 'php/php.wasm', module, MAX_WASM)
    for name in FIXTURES:
        source = context_base + name
        member = 'php/' + name
        files[member] = copy_checked(ROOT / source, stage / member, fixtures[source], MAX_FIXTURE)

    manifest = stage / 'wordpress/source-manifest.json'
    files['wordpress/source-manifest.json'] = copy_checked(ROOT / CORE_MANIFEST, manifest,
        prepared['core_manifest'], MAX_MANIFEST)
    core = json.loads(manifest.read_bytes())
    site = stage / 'wordpress/site'
    for name in sorted(core['directories']):
        part = relative(name)
        original = CORE_SITE / part
        require(original.resolve(strict=True) == original and original.is_dir(), 'release_core_directory')
        (site / part).mkdir(mode=0o700)
    for name, record in core['files'].items():
        part = relative(name)
        copy_checked(CORE_SITE / part, site / part, record, MAX_CORE_FILE)

    wire = (json.dumps({'schema': SCHEMA, 'target': TARGET, 'files': files}, sort_keys=True,
        separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    with (stage / 'release.json').open('xb') as output:
        output.write(wire)
        os.fchmod(output.fileno(), 0o400)
    return hashlib.sha256(wire).hexdigest()


def publish(parent_fd, staged_name, output_name):
    # os.rename can replace an existing empty directory. Use the Linux
    # no-replace primitive so a racing caller cannot lose its directory.
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent_fd, os.fsencode(staged_name), parent_fd, os.fsencode(output_name), RENAME_NOREPLACE):
        raise OSError(ctypes.get_errno(), 'release_publish_failed')


def stage_release(output, runtime=None):
    require(sys.platform == 'linux' and platform.machine() == 'x86_64', 'release_requires_linux_x86_64')
    output = Path(os.path.abspath(output))
    parent = output.parent
    require(output.name and parent.resolve(strict=True) == parent and not os.path.lexists(output),
        'release_output_must_be_fresh')
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    staged_name = None
    try:
        name = '.rp-preview-' + uuid.uuid4().hex
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        staged_name = name
        stage = Path('/proc/self/fd') / str(parent_fd) / staged_name
        digest = copy_release(stage, runtime)
        require(os.path.samestat(os.stat(parent, follow_symlinks=False), os.fstat(parent_fd)),
            'release_output_parent_changed')
        publish(parent_fd, staged_name, output.name)
        staged_name = None
        return digest
    finally:
        try:
            if staged_name is not None:
                # Resolve the owned name under the original open directory,
                # even when its external path was renamed during staging.
                shutil.rmtree(staged_name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        digest = stage_release(args.output)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        print('WordPress preview release staging failed', file=sys.stderr)
        return 2
    print(digest)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
