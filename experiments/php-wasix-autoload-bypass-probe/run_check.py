"""Run the pinned four-request autoload trial with a rejected pre-request position."""

import inspect
from pathlib import Path
import runpy
import secrets
import sys

import derive


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NAME = derive.NAME.decode('ascii')
DERIVATIONS = ('derive.py',)
GENERATED = ('src/main.rs', 'src/db.rs', 'src/options_cache.rs', 'src/binlog_buffer.rs',
    'src/monitor.rs', 'src/monitor_impl.rs')


def positive_controller():
    derive.pinned('run_check.py')
    derive.pinned('database.py')
    sys.path.insert(0, str(derive.BASE))
    try:
        return runpy.run_path(str(derive.BASE / 'run_check.py'))
    finally:
        sys.path.pop(0)


positive = positive_controller()
require, equal, sha = (positive[name] for name in ('require', 'equal', 'sha'))
baseline, process = (positive[name] for name in ('baseline', 'process'))


class Database(positive['Database']):
    def seed_dependency(self, nonce):
        super().seed_dependency(nonce)
        binding = secrets.token_hex(24)
        require(binding != nonce, 'autoload_binding_distinct')
        sql = (
            'SET SESSION sql_log_bin=0;\n'
            'ALTER TABLE `rp_options_guard_fixture`.`identity` ADD binding VARBINARY(48) NOT NULL;\n'
            f"UPDATE `rp_options_guard_fixture`.`identity` SET binding=X'{binding.encode('ascii').hex()}' WHERE id=1;\n"
            "GRANT SELECT (id,binding) ON `rp_options_guard_fixture`.`identity` TO 'rp_wpdb_probe'@'%';\n"
        ).encode('ascii')
        result = self.run('seed_owned_autoload_binding', self.client(), timeout=15, input_bytes=sql)
        require(not result.stdout and not result.stderr, 'autoload_binding_seed')


def sources():
    inventory = positive['sources']()
    paths = [HERE / name for name in ('derive.py', 'run_check.py', 'Cargo.toml', 'Cargo.lock', 'src/guard.rs',
        'src/source_identity.rs', 'src/live_watch.rs')]
    paths.append(derive.FRONTIER)
    paths.extend(derive.BASE / name for name in derive.PINS)
    paths.append(ROOT / '.github/workflows/php-wasix-autoload-bypass.yml')
    inventory.update({path.relative_to(ROOT).as_posix(): sha(path.read_bytes()) for path in paths})
    return dict(sorted(inventory.items()))


def generated():
    return {name: process.identity((HERE / name).read_bytes()) for name in GENERATED}


def build(report):
    autoload = positive['autoload']
    namespace = dict(vars(autoload), HERE=HERE, NAME=NAME, DERIVATIONS=DERIVATIONS)
    exec(compile(inspect.getsource(autoload.build), '<stock-autoload-bypass-build>', 'exec'), namespace)
    return namespace['build'](report)


def check_final(final):
    cache = final['options_cache']
    retained = cache['retained_reply_bytes']
    require(type(retained) is int and 0 < retained <= positive['autoload'].MAX_RETAINED_BYTES,
        'autoload_bypass_retained_reply_bound')
    equal(cache, {'sql_reads': 1, 'hits': 1, 'charged_bytes': 0,
        'retained_reply_bytes': retained, 'commit_invalidations': 0})
    equal(final['options_guard_released'], True)
    mismatch = final['position_mismatch']
    equal(set(mismatch), {'from_position', 'observed_position'})
    require(all(type(position) is int for position in mismatch.values())
        and 4 < mismatch['from_position'] < mismatch['observed_position'] <= 64 * 1024 * 1024
        and mismatch['observed_position'] - mismatch['from_position'] <= 64 * 1024,
        'autoload_bypass_position_bounds')
    baseline.validate_final({key: value for key, value in final.items()
        if key not in ('options_cache', 'options_guard_released', 'position_mismatch', 'live_commit')}, 4, True)
    commit = final['live_commit']
    equal(set(commit), {'events', 'checksums', 'commits'})
    equal(commit['commits'], 1)
    require(type(commit['events']) is int and type(commit['checksums']) is int
        and 1 <= commit['checksums'] <= commit['events'] <= 16, 'autoload_live_commit_counts')


def controller():
    source = inspect.getsource(positive['main'])
    for old, new in (
        ('php-wasix-autoload-binlog-check.v0.5', 'php-wasix-autoload-bypass-check.v0.5'),
        ("Session(binary, env, report, 'autoload_binlog')", "Session(binary, env, report, 'autoload_bypass')"),
        ('                    traces.append(autoload.comparable_operations(value))',
         '''                    equal(value['source_identity_verified'], seq > 1)
                    if seq == 4:
                        equal(value['autoload_direct_sql'], True)
                        report['position_mismatch_direct_sql_verified'] = True
                    traces.append(autoload.comparable_operations(value))'''),
        ('only_autoload_reply_digest_changed_after_commit=True, finite_binlog_replay=True',
         'only_autoload_reply_digest_changed_after_commit=True, finite_binlog_replay=False, verified_actor_connections=3'),
        ('WordPress Wasm autoload binlog: ', 'WordPress Wasm autoload bypass: '),
    ):
        source = derive.once(source, old, new)
    namespace = dict(positive, HERE=HERE, Database=Database, sources=sources, generated=generated,
        build=build, check_final=check_final)
    exec(compile(source, '<pinned-autoload-bypass-controller>', 'exec'), namespace)
    return namespace['main']


if __name__ == '__main__':
    sys.exit(controller()())
