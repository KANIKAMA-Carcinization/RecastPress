"""Derive one guarded stock autoload-query path from the accepted session actor."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'php-wasix-wp-request-probe/src/db.rs'
SOURCE_SHA = 'cadce4f3f3e9cc803f9d90da636b90a3ddc43caea644590e9c70aae598064b40'
EVIDENCE_ROOT = HERE.parent / 'php-wasix-wp-private-worker-probe/evidence'
EVIDENCE_PINS = {
    'mysql': '75bdeb4b0c06955e5424e43d009d25787a9f43302a002c09a6a3c414e89d010c',
    'mariadb': '5f7d8836bbd9ea57dc2007c942fce2402c4af081d8bb328e434ff3fb03d44c0c',
}
OUTPUT = HERE / 'src/db.rs'
SQL = b"SELECT option_name, option_value FROM rp_options WHERE autoload IN ( 'yes', 'on', 'auto-on', 'auto' )"
PREFIX_KINDS = ('hello', 'query', 'query', 'query', 'query', 'escape', 'escape', 'escape', 'escape')

QUERY_BRANCH = '''        "query" => {
            query(
                conn.as_mut().ok_or("query_without_connection")?,
                bytes,
                status,
            )
            .await
        }'''
QUERY_WITH_CACHE = '''        "query" => {
            let connection = conn.as_mut().ok_or("query_without_connection")?;
            if let Some((cache, before_status)) = cache.zip(eligible_options_status(
                cache.is_some(), sequence, bytes, *status, observation,
            )) {
                if let Some(data) = cache.lookup(before_status) {
                    // The cache requires the returned status to equal this connection's status.
                    return Ok((true, data));
                }
                cache.record_sql();
                let (ok, data) = query(connection, bytes, status).await?;
                if ok && *status == Some(before_status) && data["warnings"].as_u64() == Some(0) {
                    cache.remember(before_status, &data);
                }
                return Ok((ok, data));
            }
            query(connection, bytes, status).await
        }'''
ELIGIBILITY = '''fn eligible_options_status(
    enabled: bool,
    sequence: u64,
    bytes: &[u8],
    status: Option<u16>,
    observation: &Arc<Mutex<Observation>>,
) -> Option<u16> {
    if !enabled || sequence != 10 || bytes != AUTOLOAD_SQL {
        return None;
    }
    // Require autocommit and exclude both the ordinary and read-only transaction flags.
    let status = status.filter(|flags| flags & 0x2003 == 0x0002)?;
    let observation = observation.lock().ok()?;
    let operations = &observation.operations;
    if operations.len() != 9 {
        return None;
    }
    AUTOLOAD_PREFIXES.iter().any(|prefix| {
        operations.iter().zip(prefix).enumerate().all(|(index, (op, (kind, digest)))| {
            op["seq"].as_u64() == Some(index as u64 + 1)
                && op["op"].as_str() == Some(*kind)
                && op["request_data_sha256"].as_str() == Some(*digest)
                && op["ok"].as_bool() == Some(true)
        })
    }).then_some(status)
}

'''
TEST_ANCHOR = '    #[test]\n    fn rpc_nonce_sequence_hex_and_utf8_escape_fail_before_dispatch() {'
ELIGIBILITY_TEST = '''    #[test]
    fn stock_autoload_cache_requires_exact_successful_prefix_and_autocommit() {
        let observation = Arc::new(Mutex::new(Observation::default()));
        for prefix in AUTOLOAD_PREFIXES {
            observation.lock().unwrap().operations = prefix.iter().enumerate()
                .map(|(index, (kind, digest))| json!({"seq":index+1,"op":kind,"request_data_sha256":digest,"ok":true}))
                .collect();
            let eligible = |enabled, sequence, bytes: &[u8], status| {
                eligible_options_status(enabled, sequence, bytes, status, &observation)
            };
            assert_eq!(eligible(true, 10, AUTOLOAD_SQL, Some(2)), Some(2));
            for flags in [None, Some(0), Some(3), Some(0x2002)] {
                assert_eq!(eligible(true, 10, AUTOLOAD_SQL, flags), None);
            }
            assert_eq!(eligible(false, 10, AUTOLOAD_SQL, Some(2)), None);
            assert_eq!(eligible(true, 11, AUTOLOAD_SQL, Some(2)), None);
            assert_eq!(eligible(true, 10, b"SELECT 1", Some(2)), None);
            observation.lock().unwrap().operations[3]["ok"] = json!(false);
            assert_eq!(eligible(true, 10, AUTOLOAD_SQL, Some(2)), None);
            observation.lock().unwrap().operations[3]["ok"] = json!(true);
            observation.lock().unwrap().operations[3]["request_data_sha256"] = json!("");
            assert_eq!(eligible(true, 10, AUTOLOAD_SQL, Some(2)), None);
        }
    }
'''


def pinned(path, digest):
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError('autoload_actor_input_path')
    wire = path.read_bytes()
    if sha256(wire).hexdigest() != digest:
        raise ValueError('autoload_actor_input_changed')
    return wire


def prefixes():
    result = {}
    for backend, digest in EVIDENCE_PINS.items():
        path = (EVIDENCE_ROOT / f'ci-41771428584-{backend}' / 'raw/experiments'
            / f'php-wasix-wp-private-worker-probe/evidence/linux-{backend}.json')
        report = json.loads(pinned(path, digest))
        if report['backend'] != backend or report['passed'] is not True:
            raise ValueError('autoload_actor_baseline_verdict')
        for label in ('missing', 'missing_repeat'):
            operations = report['observations'][label]['operations'][:10]
            expected_kinds = PREFIX_KINDS + ('query',)
            if len(operations) != len(expected_kinds):
                raise ValueError('autoload_actor_baseline_length')
            for index, (operation, kind) in enumerate(zip(operations, expected_kinds), 1):
                if operation['seq'] != index or operation['op'] != kind or operation['ok'] is not True:
                    raise ValueError('autoload_actor_baseline_operation')
            if operations[-1]['request_data_sha256'] != sha256(SQL).hexdigest():
                raise ValueError('autoload_actor_baseline_query')
            prefix = tuple((op['op'], op['request_data_sha256']) for op in operations[:-1])
            if result.setdefault(backend, prefix) != prefix:
                raise ValueError('autoload_actor_baseline_repeat')
    return tuple(result.values())


def derived_bytes():
    source = pinned(SOURCE, SOURCE_SHA).decode('utf-8')
    constants = ['const AUTOLOAD_SQL: &[u8] = b' + json.dumps(SQL.decode('ascii')) + ';',
        'const AUTOLOAD_PREFIXES: [[(&str, &str); 9]; 2] = [']
    for prefix in prefixes():
        constants.append('    [')
        constants.extend(f'        ({json.dumps(kind)}, {json.dumps(digest)}),' for kind, digest in prefix)
        constants.append('    ],')
    constants += ['];', '', '']
    for original, replacement in (
        ('    pub fn start(input: Input) -> Result<Self> {',
         '''    pub fn start(
        input: Input,
        cache: Option<Arc<crate::options_cache::OptionsCache>>,
    ) -> Result<Self> {'''),
        ('dispatch(&input,&job.request,&job.bytes,&mut connection,&mut status,&shared,sequence)',
         'dispatch(&input,&job.request,&job.bytes,&mut connection,&mut status,&shared,sequence,cache.as_deref())'),
        ('async fn dispatch(', '\n'.join(constants) + ELIGIBILITY + 'async fn dispatch('),
        ('''    sequence: u64,
) -> Result<(bool, Value)> {''', '''    sequence: u64,
    cache: Option<&crate::options_cache::OptionsCache>,
) -> Result<(bool, Value)> {'''),
        (QUERY_BRANCH, QUERY_WITH_CACHE),
        (TEST_ANCHOR, ELIGIBILITY_TEST + TEST_ANCHOR),
    ):
        if source.count(original) != 1:
            raise ValueError('autoload_actor_splice')
        source = source.replace(original, replacement, 1)
    return source.encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    wire = derived_bytes()
    if OUTPUT.resolve() != OUTPUT:
        raise ValueError('autoload_actor_output_alias')
    if OUTPUT.exists():
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != wire:
            raise ValueError('autoload_actor_output_changed')
    elif args.verify_only:
        raise ValueError('autoload_actor_output_missing')
    else:
        OUTPUT.parent.mkdir(exist_ok=True)
        with OUTPUT.open('xb') as output:
            output.write(wire)
    print(sha256(wire).hexdigest())


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError):
        print('Autoload cache actor derivation failed', file=sys.stderr)
        sys.exit(1)
