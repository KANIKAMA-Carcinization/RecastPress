"""Prepare a byte-identical complete pinned WordPress tree for native fixtures.

This never starts PHP or a DB. No wp-config.php or modified core is produced.
All writes are bounded to a new ignored target/wp-bootstrap-probe tree.
"""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/wp-bootstrap-probe'
WP_SITE = WORK / 'site'
SOURCE_MANIFEST = WORK / 'source-manifest.json'
PREPARED = WORK / 'prepared.json'
ARCHIVE = ROOT / '.reference/wordpress-7.1.3.tar.gz'
ARCHIVE_SHA256 = 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
ARCHIVE_BYTES = 35368461
MAX_MEMBERS = 5000
MAX_FILE = 16 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
spec = importlib.util.spec_from_file_location('rp_bootstrap_original_native_gate',
    ROOT / 'experiments/wpdb-rpc-probe/native_check.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(wire):
    return hashlib.sha256(wire).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def read(path, maximum):
    require(path.resolve(strict=True) == path and path.is_file() and path.stat().st_size <= maximum,
        'bootstrap_fixed_file')
    wire = path.read_bytes()
    require(len(wire) <= maximum, 'bootstrap_fixed_file_grew')
    return wire


def owned(path):
    require(WORK.resolve() == WORK and path.resolve() == path and path.is_relative_to(WORK),
        'bootstrap_owned_path')
    return path


def relative(name):
    require(type(name) is str and name and '\\' not in name, 'bootstrap_archive_path')
    parts = name.split('/')
    reserved = {'con', 'prn', 'aux', 'nul'} | {'com' + str(n) for n in range(1, 10)} | {'lpt' + str(n) for n in range(1, 10)}
    require(all(part not in ('', '.', '..') and ':' not in part and not part.endswith((' ', '.'))
        and all(ord(c) >= 32 and ord(c) != 127 for c in part)
        and part.split('.')[0].lower() not in reserved for part in parts), 'bootstrap_archive_traversal')
    return parts


def archive_sources():
    wire = read(ARCHIVE, ARCHIVE_BYTES)
    require(len(wire) == ARCHIVE_BYTES and sha(wire) == ARCHIVE_SHA256, 'bootstrap_archive_identity')
    files, directories, seen = {}, [], set()
    expanded = 0
    with tarfile.open(fileobj=io.BytesIO(wire), mode='r:gz') as source:
        members = source.getmembers()
        require(len(members) == 4250 and len(members) < MAX_MEMBERS, 'bootstrap_archive_member_count')
        for member in members:
            name = member.name.rstrip('/')
            parts = relative(name)
            require(parts[0] == 'wordpress' and name.casefold() not in seen,
                'bootstrap_archive_root_or_duplicate')
            seen.add(name.casefold())
            require(member.isdir() or member.isfile(), 'bootstrap_archive_link_or_special')
            if len(parts) == 1:
                require(member.isdir(), 'bootstrap_archive_root_directory')
                continue
            name = '/'.join(parts[1:])
            if member.isdir():
                directories.append(name)
                continue
            require(0 <= member.size <= MAX_FILE, 'bootstrap_archive_file_budget')
            expanded += member.size
            require(expanded <= MAX_EXPANDED, 'bootstrap_archive_expansion_budget')
            with source.extractfile(member) as stream:
                data = stream.read(MAX_FILE + 1)
            require(len(data) == member.size, 'bootstrap_archive_member_size')
            files[name] = {'bytes': len(data), 'sha256': sha(data)}
    require(len(files) == 3782 and len(directories) == 467 and expanded == 110077999,
        'bootstrap_archive_full_inventory')
    return wire, files, sorted(directories)


def source_manifest():
    wire, files, directories = archive_sources()
    return wire, {'schema': 'recastpress.wp-bootstrap-source-manifest.v0.5',
        'wordpress_version': '7.1.3', 'archive': {'path': ARCHIVE.relative_to(ROOT).as_posix(),
            'bytes': len(wire), 'sha256': sha(wire)}, 'member_count': 4250,
        'file_count': len(files), 'directory_count': len(directories),
        'expanded_bytes': sum(row['bytes'] for row in files.values()),
        'file_inventory_sha256': sha(canonical(files)),
        'directory_inventory_sha256': sha(canonical(directories)),
        'files': files, 'directories': directories, 'core_theme_plugin_bytes_modified': False,
        'wp_config_created': False}


def verify_site(expected):
    owned(WP_SITE)
    require(WP_SITE.is_dir(), 'bootstrap_site_missing')
    files, directories = {}, []
    for path in WP_SITE.rglob('*'):
        owned(path)
        require(not path.is_symlink(), 'bootstrap_site_link')
        name = path.relative_to(WP_SITE).as_posix()
        if path.is_dir():
            directories.append(name)
            continue
        require(path.is_file(), 'bootstrap_site_special_file')
        data = read(path, MAX_FILE)
        files[name] = {'bytes': len(data), 'sha256': sha(data)}
    require(files == expected['files'] and sorted(directories) == expected['directories'],
        'bootstrap_site_changed_missing_or_extra')
    require(not (WP_SITE / 'wp-config.php').exists(), 'bootstrap_config_must_be_absent')
    return {'files': len(files), 'directories': len(directories),
        'expanded_bytes': sum(row['bytes'] for row in files.values()),
        'file_inventory_sha256': sha(canonical(files)), 'directory_inventory_sha256': sha(canonical(sorted(directories)))}


def inputs():
    paths = [HERE / 'prepare.py', *(HERE / 'fixtures' / name for name in ['install.php', 'read.php', 'bootstrap.php']),
        ROOT / 'experiments/wpdb-rpc-probe/native_check.py']
    return {path.relative_to(ROOT).as_posix(): sha(read(path, 256 * 1024)) for path in paths}


def references():
    return native.references()


def native_command(mode):
    require(mode in ('install', 'read'), 'bootstrap_closed_mode')
    return [str(native.PHP / 'php.exe'), '-n', '-d', 'extension_dir=' + str(native.PHP / 'ext'),
        '-d', 'extension=mysqli', '-d', 'disable_functions=mail', '-d', 'allow_url_fopen=0',
        '-d', 'display_errors=0', '-d', 'log_errors=0', str(HERE / 'fixtures' / (mode + '.php'))]


def prepare():
    before = inputs()
    wire, expected = source_manifest()
    owned(WORK).mkdir(parents=True, exist_ok=True)
    if not WP_SITE.exists():
        owned(WP_SITE).mkdir(exist_ok=False)
        for name in expected['directories']:
            owned(WP_SITE.joinpath(*relative(name))).mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(wire), mode='r:gz') as source:
            for member in source.getmembers():
                if not member.isfile():
                    continue
                name = '/'.join(relative(member.name)[1:])
                path = owned(WP_SITE.joinpath(*relative(name)))
                require(path.parent.is_dir(), 'bootstrap_expected_parent_directory')
                with source.extractfile(member) as incoming, path.open('xb') as output:
                    data = incoming.read(MAX_FILE + 1)
                    require({'bytes': len(data), 'sha256': sha(data)} == expected['files'][name],
                        'bootstrap_copy_exact_original')
                    output.write(data)
    tree = verify_site(expected)
    manifest_wire = (json.dumps(expected, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()
    if SOURCE_MANIFEST.exists():
        require(read(SOURCE_MANIFEST, 2 * 1024 * 1024) == manifest_wire, 'bootstrap_fixed_manifest_changed')
    else:
        with owned(SOURCE_MANIFEST).open('xb') as output:
            output.write(manifest_wire)
    require(inputs() == before, 'bootstrap_prepare_source_changed')
    value = {'schema': 'recastpress.wp-bootstrap-prepared.v0.5', 'outcome': 'fixed_full_wordpress_site_prepared',
        'input_files': before, 'site': WP_SITE.relative_to(ROOT).as_posix(), 'source_tree': tree,
        'source_manifest': {'path': SOURCE_MANIFEST.relative_to(ROOT).as_posix(), 'bytes': len(manifest_wire),
            'sha256': sha(manifest_wire)}, 'archive_sha256': ARCHIVE_SHA256, 'archive_bytes': ARCHIVE_BYTES,
        'database_started': False, 'php_executed': False, 'native_bootstrap_executed': False,
        'rust_or_wasm_executed': False, 'product_runtime_selected': False}
    prepared_wire = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()
    if PREPARED.exists() and read(PREPARED, 128 * 1024) != prepared_wire:
        old = read(PREPARED, 128 * 1024)
        saved = owned(WORK / ('prepared-before-' + sha(old)[:16] + '.json'))
        if saved.exists():
            require(read(saved, 128 * 1024) == old, 'bootstrap_preserved_preparation_changed')
        else:
            with saved.open('xb') as output:
                output.write(old)
    owned(PREPARED).write_bytes(prepared_wire)
    return verify_prepared()


def verify_prepared():
    _, expected = source_manifest()
    manifest_wire = read(SOURCE_MANIFEST, 2 * 1024 * 1024)
    require(json.loads(manifest_wire) == expected, 'bootstrap_manifest_vs_original_archive')
    value = json.loads(read(PREPARED, 128 * 1024))
    require(value['schema'] == 'recastpress.wp-bootstrap-prepared.v0.5'
        and value['outcome'] == 'fixed_full_wordpress_site_prepared'
        and value['input_files'] == inputs() and value['site'] == WP_SITE.relative_to(ROOT).as_posix()
        and value['archive_sha256'] == ARCHIVE_SHA256 and value['archive_bytes'] == ARCHIVE_BYTES,
        'bootstrap_prepared_identity')
    require(value['source_manifest'] == {'path': SOURCE_MANIFEST.relative_to(ROOT).as_posix(),
        'bytes': len(manifest_wire), 'sha256': sha(manifest_wire)}
        and verify_site(expected) == value['source_tree'], 'bootstrap_prepared_tree')
    return value


if __name__ == '__main__':
    print(prepare()['outcome'] + '; complete original source only, no PHP or DB execution')
