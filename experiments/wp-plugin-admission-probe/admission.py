"""Pre-activation decisions for three exact WordPress plugin fixture plans."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys


FIXTURES = Path(__file__).resolve().parent / 'fixtures'
VERSION = '0.1.0'
POLICY = 'recastpress.wp-plugin-admission-fixture.v0.5'
BENIGN = 'rp-admission-benign'
COLLISION = 'rp-admission-collision'
HELD = 'rp-admission-driver-provider'
LIMIT = 4096

# (fixture basename, intended path within the WordPress root, bytes, SHA-256)
CANDIDATES = {
    BENIGN: (
        ('rp-admission-benign.php', 'wp-content/plugins/rp-admission-benign/rp-admission-benign.php', 638,
         '038309389588dceb42897ea9638c646559dcdc3021e53b9de3bb85aca5abbd83'),
    ),
    COLLISION: (
        ('rp-admission-collision.php', 'wp-content/plugins/rp-admission-collision/rp-admission-collision.php', 79,
         'c8fa9132776cc54c892a3ce5cb5de4ff964f91abd9c654e68326ba9bc6b00117'),
        ('collision-db.php', 'wp-content/db.php', 137,
         'f46a595072a0a88aac5d9cec1e789a00eaf207c41f994dc954dec252fa9619eb'),
        ('collision-object-cache.php', 'wp-content/object-cache.php', 143,
         'f1f189e41b1618977567a3673f1041f93ce891e0686c8d43419c867044269fee'),
    ),
    HELD: (
        ('rp-admission-driver-provider.php',
         'wp-content/plugins/rp-admission-driver-provider/rp-admission-driver-provider.php', 304,
         'c79c49f3d830e1ca6278fedbff65765fd204be8e0339e7f3f7f434935b466a2e'),
    ),
}
RESERVED = {'wp-content/db.php': 'database_connection', 'wp-content/object-cache.php': 'object_cache'}

# This witness describes one byte-pinned fixture. It is not a PHP classifier.
DRIVER_WITNESS = b'''    global $wpdb;
    $provider = get_option('rp_admission_driver_provider');
    if (is_string($provider) && is_callable($provider)) {
        $provider($wpdb);
    }
'''


def require(condition, code):
    if not condition:
        raise ValueError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def normalized_destination(path):
    match = re.fullmatch(r'wp-content/plugins/([a-z0-9]+(?:-[a-z0-9]+)*)/\1\.php', path)
    require(path in RESERVED or match is not None, 'admission_destination_shape')
    return path


def _read_fixture(name, length, digest):
    path = FIXTURES / name
    require(path.parent == FIXTURES and path.resolve(strict=True) == path and path.is_file(), 'admission_fixture_path')
    with path.open('rb') as stream:
        wire = stream.read(LIMIT + 1)
    require(len(wire) <= LIMIT and len(wire) == length and sha256(wire).hexdigest() == digest,
        'admission_fixture_identity')
    return wire


def _evaluate(candidate):
    require(candidate in CANDIDATES, 'admission_unknown_fixture')
    contents = {}
    files = []
    for name, target, length, digest in CANDIDATES[candidate]:
        destination = normalized_destination(target)
        require(destination not in contents, 'admission_duplicate_destination')
        contents[destination] = _read_fixture(name, length, digest)
        files.append({'destination': destination, 'bytes': length, 'sha256': digest})
    files.sort(key=lambda file: file['destination'])
    identity = {'candidate': candidate, 'version': VERSION, 'files': files}
    result = {'schema': 'recastpress.wp-plugin-admission-observation.v0.5', 'policy': POLICY,
        **identity, 'candidate_sha256': sha256(canonical(identity)).hexdigest(), 'execution_started': False,
        'trial_mount_authorized': False, 'rp_report': None}
    collisions = [{'check': 'reserved_dropin_destination', 'destination': file['destination'],
        'area': RESERVED[file['destination']]} for file in files if file['destination'] in RESERVED]
    if collisions:
        result.update(disposition='refused', diagnostics=collisions)
    elif candidate == HELD:
        require(len(contents) == 1 and next(iter(contents.values())).count(DRIVER_WITNESS) == 1,
            'admission_driver_witness_changed')
        check = {'check': 'option_selected_driver_provider', 'area': 'database_connection',
            'confirmed': 'wp_option_selects_callable_receiving_wpdb',
            'unresolved': 'provider_implementation_and_connection_effect'}
        result.update(disposition='held', diagnostics=[check], rp_report={
            'schema': 'recastpress.wp-plugin-admission-rp-report.v0.5', 'policy': POLICY,
            'candidate': candidate, 'version': VERSION, 'candidate_sha256': result['candidate_sha256'],
            'profile': 'wordpress_7_1_3_private_fixture', 'stage': 'pre_activation', 'execution_started': False,
            'diagnostic': dict(check), 'reproduce': 'inspect_pinned_fixture_before_activation',
            'instruction': 'RP開発者へ報告してください',
        })
    else:
        require(candidate == BENIGN and len(contents) == 1, 'admission_trial_scope')
        result.update(disposition='trial_allowed', diagnostics=[{'check': 'exact_benign_fixture'}],
            trial_mount_authorized=True)
    return result, contents if result['trial_mount_authorized'] else {}


def assess(candidate):
    """Return the public pre-activation decision and, for a hold, a local report."""
    return _evaluate(candidate)[0]


def approved_trial(candidate):
    """Return the single destination and bytes only for the approved trial fixture."""
    result, contents = _evaluate(candidate)
    require(result['trial_mount_authorized'] is True, 'admission_trial_not_authorized')
    return next(iter(contents.items()))


def self_test():
    benign = assess(BENIGN)
    require(benign['disposition'] == 'trial_allowed' and benign['rp_report'] is None, 'admission_test_benign')
    destination, wire = approved_trial(BENIGN)
    require(benign['files'] == [{'destination': destination, 'bytes': len(wire),
        'sha256': sha256(wire).hexdigest()}], 'admission_test_trial_identity')
    refused = assess(COLLISION)
    require(refused['disposition'] == 'refused' and refused['rp_report'] is None
        and {row['destination'] for row in refused['diagnostics']} == set(RESERVED)
        and len(refused['files']) == 3, 'admission_test_whole_fixture_refused')
    held = assess(HELD)
    report = held['rp_report']
    require(held['disposition'] == 'held' and report['candidate_sha256'] == held['candidate_sha256']
        and report['execution_started'] is False and report['instruction'] == 'RP開発者へ報告してください'
        and report['diagnostic']['confirmed'] != report['diagnostic']['unresolved'], 'admission_test_held_report')
    for candidate in (COLLISION, HELD):
        expect_failure(lambda: approved_trial(candidate), 'admission_trial_not_authorized')
    expect_failure(lambda: assess('unknown'), 'admission_unknown_fixture')
    for path in ('/wp-content/db.php', 'wp-content/./db.php', 'wp-content/../db.php',
                 'wp-content//db.php', 'wp-content\\db.php', 'wp-content/DB.php'):
        expect_failure(lambda: normalized_destination(path), 'admission_destination_shape')
    expect_failure(lambda: _read_fixture(CANDIDATES[BENIGN][0][0], len(wire), '0' * 64), 'admission_fixture_identity')
    return {'passed': True, 'decisions': [benign, refused, held]}


def expect_failure(check, code):
    try:
        check()
    except ValueError as error:
        require(str(error) == code, 'admission_test_wrong_failure')
    else:
        raise ValueError('admission_test_unexpected_success')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--candidate', choices=CANDIDATES)
    action.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    result = self_test() if args.self_test else assess(args.candidate)
    sys.stdout.buffer.write(canonical(result) + b'\n')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError):
        print('WordPress plugin admission check failed', file=sys.stderr)
        sys.exit(1)
