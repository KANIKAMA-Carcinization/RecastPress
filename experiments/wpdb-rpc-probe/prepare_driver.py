"""Stage two checksum-pinned crate sources and an exact two-file patch offline.

Only the ignored target/wpdb-rpc-probe tree is written. Registry sources,
archives, existing Cargo locks and editing-side SQLx are never modified.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1].resolve()
WORK = ROOT / 'target/wpdb-rpc-probe'
WORKSPACE = WORK / 'workspace'
TARGET = WORK / 'cargo-target'
PLAN = HERE / 'driver-plan.json'
CARGO_HOME = Path(os.environ.get('CARGO_HOME', str(Path.home() / '.cargo'))).resolve()
MANIFEST = b'''[workspace]
members = ["source/mysql_async-0.37.1"]
resolver = "3"

[patch.crates-io]
mysql_common = { path = "source/mysql_common-0.37.3" }
'''


def require(value, message):
    if not value: raise ValueError(message)


def sha(wire):
    return hashlib.sha256(wire).hexdigest()


def canonical(rows):
    return sha(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode())


def identity(wire):
    return {'bytes': len(wire), 'sha256': sha(wire)}


def read_fixed(path, maximum):
    require(path.resolve(strict=True) == path and path.is_file(), 'linked/missing fixed input')
    require(0 <= path.stat().st_size <= maximum, 'fixed input size')
    wire = path.read_bytes(); require(len(wire) <= maximum, 'fixed input grew')
    return wire


def owned(path):
    require(ROOT.resolve(strict=True) == ROOT and WORK.resolve() == WORK, 'owned base linked')
    require(path.is_relative_to(WORK) and path.resolve() == path, 'staging path escaped fixed target')
    return path


def relative(name):
    require(type(name) is str and name and '\\' not in name, 'invalid relative input')
    parts = name.split('/')
    require(all(part not in ('', '.', '..') and ':' not in part for part in parts), 'relative input traversal')
    return parts


def load_plan():
    plan = json.loads(read_fixed(PLAN, 128 * 1024))
    require(plan['schema'] == 'recastpress.wpdb-rpc-driver-plan.v0.5'
        and plan['status'] == 'prepared_not_applied_not_compiled_not_connected', 'fixed prepared plan')
    require([(p['name'], p['version']) for p in plan['crates']] == [('mysql_async', '0.37.1'), ('mysql_common', '0.37.3')], 'fixed crate pair')
    require([r['path'] for r in plan['patch']['files']] == ['src/conn/mod.rs', 'src/opts/mod.rs'], 'exact patched source set')
    for key in ['cargo_manifest', 'cargo_lock']:
        path = ROOT.joinpath(*relative(plan['existing_reference'][key]))
        wire = read_fixed(path, 1024 * 1024)
        expected = plan['existing_reference'][key + '_sha256']
        if key == 'cargo_lock' and plan['schema'] == 'recastpress.wpdb-rpc-driver-plan.v0.5':
            # v0.6 changes only RecastPress's own package versions in the
            # existing binlog lock. Keep the v0.5 plan pinned while allowing
            # this exact, reviewable version-only delta in the v0.6 checkout.
            old = read_fixed(ROOT / 'experiments/wpdb-rpc-probe/fixtures/binlog-Cargo.lock.v0.5', 1024 * 1024)
            old_doc = tomllib.loads(old.decode())
            current_doc = tomllib.loads(wire.decode())
            old_rows = [row for row in old_doc['package'] if row['name'] in ('recastpress-core', 'recastpress-db')]
            current_rows = [row for row in current_doc['package'] if row['name'] in ('recastpress-core', 'recastpress-db')]
            require(len(old_rows) == len(current_rows) == 2
                and {(row['name'], row['version']) for row in old_rows} == {('recastpress-core', '0.5.0'), ('recastpress-db', '0.5.0')}
                and {(row['name'], row['version']) for row in current_rows} == {('recastpress-core', '0.6.0'), ('recastpress-db', '0.6.0')},
                'unexpected internal lock package rows')
            # Change only the version field inside each internal package block,
            # then require byte-for-byte equality with the current lock.
            marker = b'[[package]]\nname = "'
            boundaries = []
            start = 0
            while True:
                index = old.find(marker, start)
                if index < 0: break
                boundaries.append(index); start = index + 1
            blocks = []
            for index, block_start in enumerate(boundaries):
                block_end = boundaries[index + 1] if index + 1 < len(boundaries) else len(old)
                block = old[block_start:block_end]
                if b'name = "recastpress-core"\n' in block or b'name = "recastpress-db"\n' in block:
                    require(block.count(b'version = "0.5.0"\n') == 1, 'fixed internal lock version field')
                    block = block.replace(b'version = "0.5.0"\n', b'version = "0.6.0"\n', 1)
                blocks.append(block)
            expected_v06 = old[:boundaries[0]] + b''.join(blocks)
            require(sha(old) == expected and wire == expected_v06,
                'existing reference changed beyond v0.6 internal package version delta')
        else:
            require(sha(wire) == expected, 'existing reference changed')
    return plan


def registry_inputs(plan):
    lock = tomllib.loads(read_fixed(ROOT / plan['existing_reference']['cargo_lock'], 1024 * 1024).decode())
    result = {}
    for pin in plan['crates']:
        name, version = pin['name'], pin['version']
        rows = [p for p in lock['package'] if p['name'] == name and p['version'] == version]
        require(len(rows) == 1 and rows[0]['checksum'] == pin['archive_sha256'] and rows[0]['source'] == pin['registry_source'], 'existing lock crate pin')
        candidates = list((CARGO_HOME / 'registry/cache').glob('*/' + pin['archive_filename']))
        require(len(candidates) == 1, 'one cached pinned archive required')
        archive = candidates[0]; wire = read_fixed(archive, 4 * 1024 * 1024)
        require(identity(wire) == {'bytes': pin['archive_bytes'], 'sha256': pin['archive_sha256']}, 'cached archive identity')
        source = CARGO_HOME / 'registry/src' / archive.parent.name / (name + '-' + version)
        files = {}
        with tarfile.open(archive, 'r:gz') as tar:
            members = tar.getmembers(); require(len(members) < 4096, 'crate member budget')
            for member in members:
                parts = relative(member.name)
                require(parts[0] == name + '-' + version, 'crate root prefix')
                if member.isdir(): continue
                require(member.isfile() and 0 <= member.size <= 4 * 1024 * 1024 and len(parts) > 1, 'crate regular file budget')
                rel = PurePosixPath(*parts[1:]).as_posix(); require(rel not in files, 'duplicate crate member')
                data = tar.extractfile(member).read(member.size + 1)
                require(len(data) == member.size and data == read_fixed(source.joinpath(*parts[1:]), 4 * 1024 * 1024), 'local source differs from pinned crate')
                files[rel] = data
        require(sum(map(len, files.values())) <= 32 * 1024 * 1024, 'crate source total budget')
        rows = {p: identity(b) for p, b in files.items()}
        require(len(files) == pin['local_regular_files_matching_crate_archive'] and canonical(rows) == pin['source_file_inventory_sha256'], 'full crate source inventory')
        extras = sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file() and p.relative_to(source).as_posix() not in files)
        require(extras == pin['ignored_cargo_cache_metadata_files'], 'unexpected registry source files')
        result[name] = files
    return result


def patched_sources(plan, originals):
    path = ROOT.joinpath(*relative(plan['patch']['path']))
    patch = read_fixed(path, 128 * 1024)
    require(identity(patch) == {'bytes': plan['patch']['bytes'], 'sha256': plan['patch']['sha256']}, 'patch identity')
    lines = patch.decode('utf-8').splitlines(keepends=True); index = 0; changed = {}
    while index < len(lines):
        require(lines[index].startswith('--- a/'), 'fixed unified patch old header')
        name = lines[index][6:].rstrip('\n'); relative(name); index += 1
        require(name not in changed and name in originals and lines[index] == '+++ b/' + name + '\n', 'fixed unified patch new header')
        index += 1; before = originals[name].decode('utf-8').splitlines(keepends=True); output = []; cursor = 0
        while index < len(lines) and lines[index].startswith('@@'):
            match = re.fullmatch(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@\n', lines[index])
            require(match is not None, 'fixed patch hunk header'); index += 1
            start = int(match[1]) - 1; require(cursor <= start <= len(before), 'fixed patch hunk order')
            output += before[cursor:start]; cursor = start; old_count = new_count = 0
            require(len(output) == int(match[3]) - 1, 'fixed patch new hunk position')
            while index < len(lines) and not lines[index].startswith(('@@', '--- a/')):
                kind, line = lines[index][:1], lines[index][1:]
                require(kind in [' ', '+', '-'], 'fixed patch line kind')
                if kind in [' ', '-']:
                    require(cursor < len(before) and before[cursor] == line, 'fixed patch context mismatch')
                    cursor += 1; old_count += 1
                if kind in [' ', '+']: output.append(line); new_count += 1
                index += 1
            require(old_count == int(match[2]) and new_count == int(match[4]), 'fixed patch hunk length')
        output += before[cursor:]; changed[name] = ''.join(output).encode()
    require(sorted(changed) == [row['path'] for row in plan['patch']['files']], 'fixed patch changed files')
    rebuilt = b''.join(''.join(difflib.unified_diff(originals[p].decode().splitlines(keepends=True), changed[p].decode().splitlines(keepends=True), fromfile='a/' + p, tofile='b/' + p, n=3)).encode() for p in sorted(changed))
    require(rebuilt == patch, 'reconstructed exact unified patch differs')
    result = originals.copy()
    for row in plan['patch']['files']:
        name = row['path']; require(identity(originals[name]) == row['before'] and identity(changed[name]) == row['after'], 'whole patched file identity')
        result[name] = changed[name]
    require(canonical({p: identity(b) for p, b in result.items()}) == plan['patch']['source_tree_inventory_after_sha256'], 'complete patched source inventory')
    return result


def verify_tree(directory, files):
    owned(directory); require(directory.is_dir(), 'staged source missing')
    observed = {}
    for path in directory.rglob('*'):
        owned(path); require(not path.is_symlink(), 'staged source linked')
        if path.is_dir(): continue
        require(path.is_file(), 'staged source special file')
        observed[path.relative_to(directory).as_posix()] = identity(read_fixed(path, 4 * 1024 * 1024))
    expected = {p: identity(b) for p, b in files.items()}
    require(observed == expected, 'staged source changed, missing or extra files')
    return {'files': len(observed), 'inventory_sha256': canonical(observed)}


def prepare():
    plan = load_plan(); files = registry_inputs(plan)
    files['mysql_async'] = patched_sources(plan, files['mysql_async'])
    owned(WORKSPACE)
    seed = read_fixed(ROOT / plan['existing_reference']['cargo_lock'], 1024 * 1024)
    if not WORKSPACE.exists():
        WORKSPACE.mkdir(parents=True, exist_ok=False)
        for pin in plan['crates']:
            directory = owned(WORKSPACE / 'source' / (pin['name'] + '-' + pin['version']))
            for name, wire in files[pin['name']].items():
                path = owned(directory.joinpath(*relative(name))); path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream: stream.write(wire)
        for name, wire in [('Cargo.toml', MANIFEST), ('Cargo.lock', seed), ('seed-binlog.Cargo.lock', seed)]:
            with owned(WORKSPACE / name).open('xb') as stream: stream.write(wire)
    require(read_fixed(owned(WORKSPACE / 'Cargo.toml'), 4096) == MANIFEST, 'fixed staged workspace manifest')
    require({p.name for p in WORKSPACE.iterdir()} == {'Cargo.toml', 'Cargo.lock', 'seed-binlog.Cargo.lock', 'source'}, 'unexpected staged workspace entries')
    require({p.name for p in (WORKSPACE / 'source').iterdir()} == {'mysql_async-0.37.1', 'mysql_common-0.37.3'}, 'unexpected staged crate directories')
    require(read_fixed(owned(WORKSPACE / 'seed-binlog.Cargo.lock'), 1024 * 1024) == seed, 'fixed original seed lock')
    trees = {pin['name']: verify_tree(WORKSPACE / 'source' / (pin['name'] + '-' + pin['version']), files[pin['name']]) for pin in plan['crates']}
    lock_state = owned(WORK / 'resolved-lock.json')
    actual_lock = read_fixed(owned(WORKSPACE / 'Cargo.lock'), 1024 * 1024)
    if lock_state.exists():
        state = json.loads(read_fixed(lock_state, 65536)); require(identity(actual_lock) == state['lock'], 'previously resolved staged lock changed')
        require(state['seed_sha256'] == sha(seed) and state['manifest_sha256'] == sha(MANIFEST), 'resolved lock context changed')
    else: require(actual_lock == seed, 'unrecorded staged lock changed')
    return {'schema': 'recastpress.wpdb-rpc-driver-preparation.v0.5', 'outcome': 'fixed_driver_sources_staged',
        'plan_sha256': sha(read_fixed(PLAN, 128 * 1024)), 'patch_sha256': plan['patch']['sha256'],
        'workspace': WORKSPACE.relative_to(ROOT).as_posix(), 'source_trees': trees,
        'seed_lock_sha256': sha(seed), 'workspace_manifest_sha256': sha(MANIFEST),
        'staged_lock': identity(actual_lock), 'patch_applied_in_owned_stage': True,
        'pinned_registry_sources_and_archives_modified': False, 'existing_lock_modified': False, 'network_used_by_preparer': False,
        'database_started': False, 'database_connected': False, 'driver_compiled': False,
        'handshake_wire_verified': False, 'standard_wpdb_compatibility_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.parse_args()
    result = prepare(); print(result['outcome'] + '; source staging only, no Cargo or DB execution')

