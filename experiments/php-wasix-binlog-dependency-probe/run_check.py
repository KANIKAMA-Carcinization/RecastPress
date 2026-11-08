"""Run the existing cross-request oracle with one observed DB commit."""

from hashlib import sha256
from pathlib import Path

from database import Database


HERE = Path(__file__).resolve().parent
UPSTREAM = HERE.parent / 'php-wasix-shared-dependency-probe'
PIN = 'a9116d4656b7ea4af2e834511e2df5c2498d4b6fa6f2f6fcc913f232e3bd47b8'


def controller():
    wire = (UPSTREAM / 'run_check.py').read_bytes()
    if sha256(wire).hexdigest() != PIN:
        raise ValueError('binlog_dependency_controller_changed')
    source = wire.decode()
    start, end = source.index('def fixture('), source.index('def check_request(')
    source = source[:start] + source[end:]
    for before, after in (
        ('Exercise one guarded Rust dependency read across fresh PHP-Wasm requests.',
         'Compare one observed binlog commit across fresh PHP-Wasm requests.'),
        ("NAME = 'recastpress-php-wasix-shared-dependency-probe'",
         "NAME = 'recastpress-php-wasix-binlog-dependency-probe'"),
        ("VALUE = b'recastpress-shared-fixture'",
         "VALUE = b'recastpress-shared-fixture'\nUPDATED = b'recastpress-shared-updated'"),
        ("'.github/workflows/php-wasix-shared-dependency.yml'",
         "'.github/workflows/php-wasix-binlog-dependency.yml'"),
        ("HERE / 'src/dependency.rs']", '''HERE / 'src/dependency.rs', HERE / 'src/monitor.rs',
        UPSTREAM / 'run_check.py', UPSTREAM / 'derive_actor.py', UPSTREAM / 'derive_guest.py',
        UPSTREAM / 'derive_worker.py', UPSTREAM / 'Cargo.toml', UPSTREAM / 'Cargo.lock',
        HERE.parent / 'binlog-probe/src/main.rs', HERE.parent / 'binlog-probe/src/buffer_adapter.rs',
        ROOT / 'crates/recastpress-db/src/change_buffer.rs',
        ROOT / 'crates/recastpress-db/src/budgeted_change_buffer.rs']'''),
        ("('derive_guest.py', 'derive_actor.py', 'derive_worker.py')", "('derive.py', 'derive_binlog.py')"),
        ("expected = {'source': 'miss_fill' if seq == 1 else 'hit', 'bytes': len(VALUE),\n        'sha256': sha(VALUE)",
         """expected_value = UPDATED if seq == 3 else VALUE
    expected = {'source': 'hit' if seq == 2 else 'miss_fill', 'bytes': len(expected_value),
        'sha256': sha(expected_value)"""),
        ("'recastpress.php-wasix-shared-dependency-check.v0.5'", "'recastpress.php-wasix-binlog-dependency-check.v0.5'"),
        ("'src/main.rs', 'src/db.rs', 'fixtures/bootstrap.php', 'fixtures/adapter.php'",
         "'src/main.rs', 'src/db.rs', 'src/binlog_buffer.rs', 'fixtures/bootstrap.php', 'fixtures/adapter.php'"),
        ('database = context.linux.Database(args.backend, owned)', 'database = Database(args.backend, owned)'),
        ("fixture(database, payloads[0]['database']['nonce'])", "database.seed_dependency(payloads[0]['database']['nonce'])"),
        ("report['session_ready'] = ready", """report['session_ready'] = ready
                session.send(database.monitor_config())"""),
        ("""                    if seq == 1:
                        verify_row_lock(database)
                        report['independent_writer_blocked_by_fixture_lock'] = True""",
         """                    if seq == 2:
                        stage = 'source_commit'
                        database.commit_dependency()"""),
        ("{'dependency_sql_reads': 1, 'shared_hits': 2, 'charged_bytes': 0}",
         "{'dependency_sql_reads': 2, 'shared_hits': 1, 'charged_bytes': 0, 'commit_invalidations': 1}"),
        ("if key not in ('dependency', 'fixture_guard_released')}, 3, True)",
         "if key not in ('dependency', 'fixture_guard_released', 'binlog_fence')}, 3, True)"),
        ("report['session_final'] = final", """verify_fence(final['binlog_fence'], equal, require)
                report['session_final'] = final"""),
        ("""            result = database.run('verify_owned_dependency_fixture_value', database.client() + [
                '--execute=SELECT HEX(value) FROM rp_wpdb_probe.rp_dependency_fixture WHERE id=1'])
            require(result.stdout.strip() == VALUE.hex().upper().encode('ascii') and not result.stderr,
                'dependency_fixture_value_changed')
            result = database.run('drop_owned_dependency_fixture', database.client() + [
                '--execute=DROP TABLE rp_wpdb_probe.rp_dependency_fixture'])
            require(result.stdout == result.stderr == b'', 'dependency_fixture_cleanup')""",
         """            database.verify_dependency_value('after_session', UPDATED)"""),
        ("""report.update(passed=True, source_reverified_after=True, stable_fixture_row_guard=True,
            live_binlog_connected=False)""",
         """require(all(owned[key] is True for key in ('dependency_binlog_profile_verified',
            'dependency_fixture_seeded', 'dependency_update_committed_and_visible')), 'dependency_source_not_verified')
        report.update(passed=True, source_reverified_after=True, finite_binlog_replay=True,
            independently_committed_and_visible=True, fixture_row_lock_used=False)"""),
        ("WordPress Wasm shared dependency: ", "WordPress Wasm binlog dependency: "),
    ):
        if source.count(before) != 1:
            raise ValueError('binlog_dependency_controller_derivation')
        source = source.replace(before, after, 1)
    return source


def verify_fence(fence, equal, require):
    import re
    equal(set(fence), {'source_file', 'from_position', 'through_position', 'events', 'checksums',
        'rows', 'commits', 'eof', 'source_reverified'})
    require(re.fullmatch(r'probe-bin\.[0-9]{6}', fence['source_file']) is not None,
        'dependency_binlog_file')
    for key in ('from_position', 'through_position', 'events', 'checksums'):
        require(type(fence[key]) is int, 'dependency_binlog_integer')
    require(4 < fence['from_position'] < fence['through_position'] <= 64 * 1024 * 1024
        and 2 <= fence['checksums'] <= fence['events'] <= 128, 'dependency_binlog_bounds')
    equal(fence['rows'], 1)
    equal(fence['commits'], 1)
    equal(fence['eof'], True)
    equal(fence['source_reverified'], True)


if __name__ == '__main__':
    exec(compile(controller(), '<pinned-binlog-dependency-controller>', 'exec'))
