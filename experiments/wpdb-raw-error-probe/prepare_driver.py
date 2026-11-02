"""Verify original archives + original two-file patch, then stage one accessor.

All writes are confined to the new ignored target/wpdb-raw-error-probe tree.
The original preparer is imported only for its read-only verification functions.
"""
import difflib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / 'target/wpdb-raw-error-probe'
WORKSPACE = WORK / 'workspace'
TARGET = WORK / 'cargo-target'
spec = importlib.util.spec_from_file_location('rp_raw_error_original_preparer',
    ROOT / 'experiments/wpdb-rpc-probe/prepare_driver.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
require, sha, identity, canonical = base.require, base.sha, base.identity, base.canonical

ANCHOR = b'''    /// Returns a reference to the last OK packet.
    pub fn last_ok_packet(&self) -> Option<&OkPacket<'static>> {
        self.inner.last_ok_packet.as_ref()
    }
'''
ACCESSOR = b'''
    /// Borrows the last parsed ERR packet, retaining its message bytes.
    /// An OK packet clears this value; the existing lossy ServerError API is unchanged.
    pub fn last_error_packet(&self) -> Option<&mysql_common::packets::ServerError<'static>> {
        self.inner.last_err_packet.as_ref()
    }
'''


def owned(path):
    require(ROOT.resolve(strict=True) == ROOT and WORK.resolve() == WORK, 'raw_stage_base_linked')
    require(path.is_relative_to(WORK) and path.resolve() == path, 'raw_stage_path_escape')
    return path


def verify_tree(directory, files):
    owned(directory)
    require(directory.is_dir(), 'raw_staged_source_missing')
    observed = {}
    for path in directory.rglob('*'):
        owned(path)
        require(not path.is_symlink(), 'raw_stage_linked')
        if path.is_dir():
            continue
        require(path.is_file(), 'raw_stage_special_file')
        observed[path.relative_to(directory).as_posix()] = identity(base.read_fixed(path, 4 * 1024 * 1024))
    require(observed == {name: identity(data) for name, data in files.items()}, 'raw_stage_inventory_differs')
    return {'files': len(observed), 'inventory_sha256': canonical(observed)}


def prepare():
    plan_wire = base.read_fixed(HERE / 'patch-plan.json', 128 * 1024)
    plan = json.loads(plan_wire)
    require(plan['schema'] == 'recastpress.wpdb-raw-error-plan.v0.5', 'raw_plan_schema')
    require(sha(base.read_fixed(HERE / 'driver.Cargo.lock', 1024 * 1024)) == plan['driver_lock_seed']['sha256'],
        'raw_driver_lock_seed_changed')
    for name, expected in plan['base_inputs'].items():
        require(sha(base.read_fixed(ROOT.joinpath(*base.relative(name)), 2 * 1024 * 1024)) == expected,
            'raw_original_input_changed')
    original_plan = base.load_plan()
    files = base.registry_inputs(original_plan)
    files['mysql_async'] = base.patched_sources(original_plan, files['mysql_async'])
    before = files['mysql_async']['src/conn/mod.rs']
    require(before.count(ANCHOR) == 1, 'raw_accessor_anchor')
    tests = base.read_fixed(HERE / 'fixtures/raw_error_tests.rs', 32 * 1024)
    require(identity(tests) == {key: plan['test_fixture'][key] for key in ['bytes', 'sha256']},
        'raw_unit_fixture_identity')
    after = before.replace(ANCHOR, ANCHOR + ACCESSOR) + b'\n' + tests
    patch = ''.join(difflib.unified_diff(before.decode().splitlines(keepends=True),
        after.decode().splitlines(keepends=True), fromfile='a/src/conn/mod.rs',
        tofile='b/src/conn/mod.rs', n=0)).encode()
    require(base.read_fixed(ROOT / plan['patch']['path'], 128 * 1024) == patch
        and identity(patch) == {key: plan['patch'][key] for key in ['bytes', 'sha256']},
        'raw_exact_accessor_and_test_patch')
    require(plan['patch']['files'] == [{'path': 'src/conn/mod.rs',
        'before': identity(before), 'after': identity(after)}], 'raw_changed_file_set')
    files['mysql_async']['src/conn/mod.rs'] = after
    expected = {name: {'files': len(tree), 'inventory_sha256': canonical(
        {name: identity(data) for name, data in tree.items()})} for name, tree in files.items()}
    require(expected == plan['source_trees_after'], 'raw_expected_complete_trees')
    source = owned(WORKSPACE / 'source')
    if not WORKSPACE.exists():
        WORKSPACE.mkdir(parents=True, exist_ok=False)
        for pin in original_plan['crates']:
            directory = owned(source / (pin['name'] + '-' + pin['version']))
            for name, data in files[pin['name']].items():
                path = owned(directory.joinpath(*base.relative(name)))
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(data)
    for target, source_name in [('Cargo.toml', 'driver-workspace.toml'), ('Cargo.lock', 'driver.Cargo.lock')]:
        wire = base.read_fixed(HERE / source_name, 1024 * 1024)
        path = owned(WORKSPACE / target)
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(wire)
        require(base.read_fixed(path, 1024 * 1024) == wire, 'raw_staged_workspace_input_changed')
    require({p.name for p in WORKSPACE.iterdir()} == {'source', 'Cargo.toml', 'Cargo.lock'}, 'raw_workspace_extra_entries')
    require({p.name for p in source.iterdir()} == {'mysql_async-0.37.1', 'mysql_common-0.37.3'},
        'raw_stage_extra_crate')
    trees = {pin['name']: verify_tree(source / (pin['name'] + '-' + pin['version']), files[pin['name']])
        for pin in original_plan['crates']}
    return {'schema': 'recastpress.wpdb-raw-error-preparation.v0.5',
        'outcome': 'fixed_raw_error_driver_staged', 'plan_sha256': sha(plan_wire),
        'base_plan_sha256': sha(base.read_fixed(base.PLAN, 128 * 1024)),
        'base_patch_sha256': original_plan['patch']['sha256'], 'raw_patch_sha256': sha(patch),
        'source_trees': trees, 'new_stage': WORKSPACE.relative_to(ROOT).as_posix(),
        'workspace_manifest': identity(base.read_fixed(WORKSPACE / 'Cargo.toml', 1024 * 1024)),
        'workspace_lock': identity(base.read_fixed(WORKSPACE / 'Cargo.lock', 1024 * 1024)),
        'registry_and_old_stage_modified': False, 'network_used': False,
        'database_started': False, 'database_connected': False, 'product_driver_selected': False}


if __name__ == '__main__':
    print(json.dumps(prepare(), indent=2))
