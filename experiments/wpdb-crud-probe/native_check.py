"""Verify a fixed native comparison runtime and eight untouched WP sources."""
import importlib.util
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('rp_crud_original_native',
    ROOT / 'experiments/wpdb-rpc-probe/native_check.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
require, read, sha = base.require, base.read, base.sha
WP_FILES = ['load.php', 'plugin.php', 'class-wp-hook.php', 'class-wp-filter-sentinel.php',
    'class-wpdb.php', 'functions.php', 'option.php', 'class-wp-error.php']


def references():
    result = base.references()
    archive = ROOT / '.reference/wordpress-7.1.3.tar.gz'
    wire = read(archive, 40 * 1024 * 1024)
    require(sha(wire) == base.WP_SHA, 'crud_original_wp_archive')
    selected = {}
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        require(len(members) < 5000, 'crud_archive_member_quota')
        for name in WP_FILES:
            matches = [item for item in members if item.name == 'wordpress/wp-includes/' + name]
            require(len(matches) == 1 and matches[0].isfile() and matches[0].size <= 512 * 1024,
                'crud_original_wp_source_member')
            with source.extractfile(matches[0]) as stream:
                content = stream.read(512 * 1024 + 1)
            require(len(content) == matches[0].size
                and read(base.WP / 'wp-includes' / name, 512 * 1024) == content,
                'crud_original_wp_source_bytes')
            selected['wp-includes/' + name] = sha(content)
    result['wordpress_loaded_source_sha256'] = selected
    result['wordpress_verified_source_count'] = 8
    return result


def command(variant):
    require(variant in ('native', 'bridge'), 'crud_closed_variant')
    command = [str(base.PHP / 'php.exe'), '-n', '-d', 'display_errors=0', '-d', 'log_errors=0']
    if variant == 'native':
        command += ['-d', 'extension_dir=' + str(base.PHP / 'ext'), '-d', 'extension=mysqli']
    return command + [str(HERE / 'fixtures' / (variant + '.php'))]


def included_sources(variant):
    names = ['experiments/wpdb-crud-probe/fixtures/' + name
        for name in [variant + '.php', 'entry.php', 'corpus.php']]
    names += ['experiments/wpdb-bridge-probe/fixtures/helpers.php']
    if variant == 'bridge':
        names += ['experiments/wpdb-bridge-probe/fixtures/adapter.php']
    names += ['.reference/wordpress-7.1.3/wp-includes/' + name for name in WP_FILES]
    return sorted(names)
