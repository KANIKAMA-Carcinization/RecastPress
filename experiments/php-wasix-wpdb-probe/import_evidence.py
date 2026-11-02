"""Import JSON-only CI records against API identities and their frozen Git tree.

No network, database, PHP, Wasm, compiler, binary or source archive is run or
acquired. Failed/partial records remain failed. Nonce-dependent request hashes
were checked in CI, but are explicitly not recomputed by this offline importer.
"""
import argparse
import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import SimpleNamespace
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1].resolve()
PREFIX = 'experiments/php-wasix-wpdb-probe/'
MANAGED = 'experiments/php-wasix-managed-access-probe/'
CGI = 'experiments/php-wasix-cgi-build/'
COMBINED = 'experiments/php-wasix-fiber-cgi-probe/'
FIBER = 'experiments/php-wasix-fiber-probe/'
REPOSITORY = 'KANIKAMA-Carcinization/RecastPress'
REPOSITORY_ID = 1432882501
SHA = re.compile('[0-9a-f]{64}')
CONCLUSIONS = {'success', 'failure', 'cancelled', 'skipped', 'timed_out', 'neutral',
    'action_required', 'stale', 'startup_failure'}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(wire):
    return hashlib.sha256(wire).hexdigest()


def identity(wire):
    return {'bytes': len(wire), 'sha256': sha(wire)}


def integer(value, low=0, high=2**63 - 1):
    require(type(value) is int and low <= value <= high, 'invalid_integer_type_or_bound')


def digest(value):
    require(type(value) is str and SHA.fullmatch(value) is not None, 'invalid_sha256')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()


def exact(actual, expected, reason):
    if type(actual) is type(expected) and isinstance(actual, (set, bytes)):
        require(actual == expected, reason)
    else:
        require(canonical(actual) == canonical(expected), reason)


def parse_json(wire):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def bad_number(_):
        raise ValueError('nonfinite_json')
    return json.loads(wire.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=bad_number)


def privacy_gate(value, context=()):
    if type(value) is dict:
        for key, item in value.items():
            require(key.lower() not in {'nonce', 'password', 'admin_password', 'admin_secret', 'db_password',
                'secret', 'token', 'access_token', 'authorization', 'signed_url', 'download_url', 'archive_download_url'},
                'private_field_in_public_json')
            privacy_gate(item, context + (key,))
    elif type(value) is list:
        for index, item in enumerate(value):
            privacy_gate(item, context + (index,))
    elif type(value) is str:
        inspected = value
        if context[-2:] == ('tool_versions', 'clang'):
            # Authorized public CI tool-version exception. Only this exact
            # repository workspace root (or a descendant) is masked during
            # inspection; the original JSON bytes are never changed.
            inspected = re.sub(re.escape('/home/runner/work/RecastPress/RecastPress') + r'(?=/|$)',
                '<public-ci-workspace>', inspected)
        require(re.search(r'https?://api\.github\.com(?:/|$)', inspected, re.I) is None
            and re.search(r'[?&](?:token|sig|signature|x-amz-signature|x-ms-signature)=', inspected, re.I) is None
            and re.search(r'(?:[A-Za-z]:[\\/]+Users[\\/]|/(?:Users|home)/[^/]+/)', inspected, re.I) is None,
            'private_url_or_user_path_in_public_json')
        require(re.fullmatch('[0-9a-f]{48}', value) is None, 'possible_raw_secret_in_public_json')
        if re.fullmatch('[0-9a-f]{96}', value):
            require(re.fullmatch(b'[0-9a-f]{48}', bytes.fromhex(value)) is None,
                'possible_hex_secret_in_public_json')


def relative(name):
    require(type(name) is str and name and len(name) <= 512 and not name.startswith('/')
        and '\\' not in name and all(p not in ('', '.', '..') and ':' not in p for p in name.split('/')),
        'unsafe_relative_path')
    return name


def read(path, maximum):
    require(path.resolve(strict=True) == path and path.is_file(), 'fixed_input_missing_or_linked')
    require(0 < path.stat().st_size <= maximum, 'fixed_input_size')
    wire = path.read_bytes()
    require(len(wire) <= maximum, 'fixed_input_grew')
    return wire


class GitSource:
    def __init__(self, commit):
        require(type(commit) is str and re.fullmatch('[0-9a-f]{40}', commit), 'invalid_source_commit')
        self.commit, self.cache = commit, {}
        require(self.command(['cat-file', '-t', commit], 64) == b'commit\n', 'source_commit_not_available')

    def command(self, args, maximum):
        result = subprocess.run(['git', *args], cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
        require(result.returncode == 0 and not result.stderr and len(result.stdout) <= maximum,
            'bounded_git_read_failed')
        return result.stdout

    def read(self, name):
        relative(name)
        if name not in self.cache:
            spec = self.commit + ':' + name
            size = int(self.command(['cat-file', '-s', spec], 64))
            integer(size, 1, 4 * 1024 * 1024)
            wire = self.command(['show', '--no-textconv', spec], size)
            require(len(wire) == size, 'git_blob_size_changed')
            self.cache[name] = wire
        return self.cache[name]

    def names(self, prefix):
        relative(prefix.rstrip('/'))
        wire = self.command(['ls-tree', '-rz', '--name-only', self.commit, '--', prefix], 2 * 1024 * 1024)
        result = [name.decode('utf-8', errors='strict') for name in wire.split(b'\0') if name]
        require(len(result) <= 20000 and len(result) == len(set(result)), 'git_tree_listing_bound')
        for name in result:
            relative(name)
            require(name.startswith(prefix), 'git_tree_prefix_mismatch')
        return result

    def hashes(self, names):
        return {name: sha(self.read(name)) for name in sorted(set(names))}

    def bindings(self, mapping):
        require(type(mapping) is dict and 0 < len(mapping) <= 512, 'source_binding_map_bound')
        for name, expected in mapping.items():
            digest(expected)
            require(sha(self.read(name)) == expected, 'original_git_source_hash_mismatch')

    def tree(self, name):
        return ast.parse(self.read(name), filename=name)

    def constants(self, name):
        values = {}
        def literal(node):
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Add, ast.Sub)):
                left, right = literal(node.left), literal(node.right)
                require(type(left) is int and type(right) is int, 'frozen_numeric_constant_type')
                value = left * right if isinstance(node.op, ast.Mult) else left + right if isinstance(node.op, ast.Add) else left - right
                require(abs(value) <= 2**63 - 1, 'frozen_numeric_constant_bound')
                return value
            return ast.literal_eval(node)
        for node in self.tree(name).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    values[node.targets[0].id] = literal(node.value)
                except (ValueError, TypeError):
                    pass
        return values

    def functions(self, name, names, namespace=None):
        namespace = {'__builtins__': __builtins__, 'json': json, 're': re, 'Path': Path,
            'ROOT': ROOT, 'HERE': ROOT / str(Path(name).parent), 'require': require, 'sha': sha,
            'exact': exact, 'integer': integer, **(namespace or {})}
        nodes = [node for node in self.tree(name).body if isinstance(node, ast.FunctionDef) and node.name in names]
        require({node.name for node in nodes} == set(names), 'frozen_pure_function_set')
        require(not any(node.decorator_list for node in nodes), 'unexpected_pure_function_decorator')
        # Only named pure function definitions are compiled; imports/top-level
        # calls, main(), build(), prepare(), DB and execution paths are excluded.
        exec(compile(ast.Module(body=nodes, type_ignores=[]), name, 'exec'), namespace)
        return namespace


def member_map(backend):
    return {
        'run.json': PREFIX + 'evidence/linux-' + backend + '.json',
        'ci-stages.json': PREFIX + 'evidence/ci-stages.json',
        'build.json': MANAGED + 'evidence/build.json',
        'toolchain.json': CGI + 'evidence/toolchain.json',
        'prepared.json': 'target/php-wasix-wpdb-probe/prepared.json',
    }


def metadata_gate(api, run, backend):
    exact(set(api), set(['repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha',
        'run_conclusion', 'job', 'artifact']), 'api_metadata_fields')
    require(api['repository_id'] == REPOSITORY_ID and api['repository'] == REPOSITORY, 'fixed_repository')
    integer(api['run_id'], 1)
    integer(api['run_attempt'], 1, 1000)
    require(str(api['run_id']) == run and api['run_conclusion'] in CONCLUSIONS, 'api_run_identity')
    require(type(api['head_sha']) is str and re.fullmatch('[0-9a-f]{40}', api['head_sha']), 'api_head_sha')
    job, artifact = api['job'], api['artifact']
    exact(set(job), {'id', 'name', 'conclusion', 'steps'}, 'api_job_fields')
    integer(job['id'], 1)
    require(job['name'] == 'Cranelift wpdb / ' + backend and job['conclusion'] in CONCLUSIONS, 'api_job_identity')
    require(type(job['steps']) is list and 1 <= len(job['steps']) <= 64, 'api_job_step_bound')
    for row in job['steps']:
        exact(set(row), {'name', 'conclusion'}, 'api_step_fields')
        require(type(row['name']) is str and 0 < len(row['name']) <= 160 and row['conclusion'] in CONCLUSIONS,
            'api_step_value')
    exact(set(artifact), {'id', 'name', 'size_in_bytes', 'digest'}, 'api_artifact_fields')
    integer(artifact['id'], 1)
    integer(artifact['size_in_bytes'], 1, 8 * 1024 * 1024)
    require(artifact['name'] == 'php-wasix-wpdb-' + backend + '-' + str(api['run_attempt'])
        and type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']),
        'api_artifact_identity')


def archive_gate(path, api, backend):
    wire = read(path, 8 * 1024 * 1024)
    exact(identity(wire), {'bytes': api['artifact']['size_in_bytes'],
        'sha256': api['artifact']['digest'][7:]}, 'api_zip_bytes_and_digest')
    mapping = member_map(backend)
    reverse = {path: name for name, path in mapping.items()}
    raw, members, expanded = {}, {}, 0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        rows = archive.infolist()
        require(1 <= len(rows) <= 5 and len({row.filename for row in rows}) == len(rows), 'zip_member_set_bound')
        for row in rows:
            relative(row.filename)
            require(row.filename in reverse and not row.is_dir() and row.flag_bits & 1 == 0
                and stat.S_IFMT(row.external_attr >> 16) != stat.S_IFLNK
                and 0 < row.file_size <= 4 * 1024 * 1024, 'json_only_zip_member')
            expanded += row.file_size
            require(expanded <= 12 * 1024 * 1024, 'json_zip_expansion_bound')
            content = archive.read(row)
            require(len(content) == row.file_size, 'zip_json_size')
            name = reverse[row.filename]
            raw[name], members[name] = content, row.filename
    require('ci-stages.json' in raw, 'ci_stages_record_required')
    return raw, {name: parse_json(content) for name, content in raw.items()}, members


def stages_gate(stages, api, backend, source):
    exact(set(stages), {'schema', 'source_commit', 'run_id', 'run_attempt', 'backend', 'stages'}, 'ci_stage_fields')
    require(stages['schema'] == 'recastpress.php-wasix-wpdb-ci-stages.v0.5'
        and stages['source_commit'] == api['head_sha'] and stages['run_id'] == str(api['run_id'])
        and stages['run_attempt'] == str(api['run_attempt']) and stages['backend'] == backend, 'ci_stage_run_identity')
    text = source.read('.github/workflows/php-wasix-wpdb.yml').decode('utf-8')
    expected = {}
    for block in text.split('      - name: ')[1:]:
        name = block.splitlines()[0].strip()
        match = re.search(r'^        id: ([a-z_]+)$', block, re.M)
        if match:
            expected[match[1]] = name
    exact(set(expected), {'php', 'deps', 'parser', 'packages', 'tools', 'module', 'guest', 'rust', 'contract'},
        'frozen_workflow_stage_inventory')
    exact(set(stages['stages']), set(expected), 'actual_ci_stage_inventory')
    for key, name in expected.items():
        rows = [row for row in api['job']['steps'] if row['name'] == name]
        require(len(rows) == 1, 'api_step_missing_or_duplicate')
        exact(stages['stages'][key], rows[0]['conclusion'], 'api_step_vs_ci_stage')
    return all(value == 'success' for value in stages['stages'].values())


def original_runtime_inputs(source):
    # This source-bound build inventory was committed with the final LF-fixed
    # Windows compile check. It is a separate immutable inventory anchor, not
    # a claim that Windows executed the guest or that its binary was acquired.
    anchor = parse_json(source.read(PREFIX + 'evidence/win32-build.json'))
    require(anchor['schema'] == 'recastpress.php-wasix-wpdb-check.v0.5'
        and anchor['passed'] is True and anchor['build_only'] is True, 'frozen_compile_inventory_anchor')
    mapping = anchor['input_files']
    source.bindings(mapping)
    local = {name for name in source.names(PREFIX) if '/evidence/' not in name and '__pycache__' not in name
        and Path(name).name != 'import_evidence.py'
        and (Path(name).suffix in ('.py', '.rs', '.php', '.toml') or Path(name).name in ('Cargo.lock', 'container-images.json'))}
    require(local <= set(mapping) and '.github/workflows/php-wasix-wpdb.yml' in mapping,
        'complete_local_compile_and_workflow_inventory')
    return mapping


def source_bindings_in_records(source, value):
    count = 0
    if type(value) is dict:
        for key, item in value.items():
            if key == 'input_files':
                if type(item) is dict:
                    source.bindings(item)
                else:
                    require(type(item) is list and 0 < len(item) <= 512, 'compiled_source_binding_list')
                    seen = set()
                    for row in item:
                        require(type(row) is dict and set(row) == {'path', 'bytes', 'sha256'}
                            and row['path'] not in seen, 'compiled_source_binding_fields')
                        seen.add(row['path'])
                        exact({'bytes': row['bytes'], 'sha256': row['sha256']},
                            identity(source.read(row['path'])), 'compiled_git_source_binding')
                count += len(item)
            else:
                count += source_bindings_in_records(source, item)
    elif type(value) is list:
        for item in value:
            count += source_bindings_in_records(source, item)
    return count


def frozen_checker(source):
    name = PREFIX + 'run_check.py'
    constants = source.constants(name)
    reader = lambda path, maximum: source.read(Path(path).relative_to(ROOT).as_posix())
    namespace = source.functions(name, ['canonical', 'exact', 'reference', 'validate_php', 'parse_complete_cgi'],
        {'CORE': constants['CORE'], 'BASELINES': constants['BASELINES'],
         'driver': SimpleNamespace(read_fixed=reader)})
    return namespace


def prepared_gate(mapping, source):
    name = PREFIX + 'prepare.py'
    constants = source.constants(name)
    functions = {node.name: node for node in source.tree(name).body if isinstance(node, ast.FunctionDef)}
    metadata_return = functions['metadata'].body[-1]
    derivation_return = functions['derivation_gate'].body[-1]
    require(isinstance(metadata_return, ast.Return) and isinstance(metadata_return.value, ast.Dict)
        and isinstance(derivation_return, ast.Return) and isinstance(derivation_return.value, ast.Dict),
        'frozen_preparation_return_shapes')
    metadata_fields = {ast.literal_eval(key): value
        for key, value in zip(metadata_return.value.keys, metadata_return.value.values)}
    derivation_fields = {ast.literal_eval(key): value
        for key, value in zip(derivation_return.value.keys, derivation_return.value.values)}
    exact(set(mapping), set(metadata_fields), 'complete_prepared_receipt_fields')
    old_prefix = 'experiments/wpdb-bridge-probe/fixtures/'
    names = constants['FIXTURES']
    original = {n: source.read(old_prefix + n) for n in names}
    for n, wire in original.items():
        exact((len(wire), sha(wire)), constants['OLD_PINS'][n], 'frozen_parent_fixture_pin')
    namespace = source.functions(name, ['replace_once', 'expected_fixtures'], constants)
    derived = namespace['expected_fixtures'](original)
    for n in names:
        exact(source.read(PREFIX + 'fixtures/' + n), derived[n], 'frozen_cgi_derivation')
    inputs = source.hashes([name, 'scripts/wordpress_reference.py']
        + [old_prefix + n for n in names] + [PREFIX + 'fixtures/' + n for n in names])
    exact(mapping['input_files'], inputs, 'complete_prepared_ten_source_inventory')
    wp = {n: {'path': 'target/php-wasix-wpdb-probe/wp-core/' + n, 'bytes': pin[0], 'sha256': pin[1]}
        for n, pin in constants['WP_PINS'].items()}
    files = [{'source': PREFIX + 'fixtures/' + n, 'guest': '/experiments/wpdb-bridge-probe/fixtures/' + n,
        **identity(derived[n]), 'guest_read_only': True, 'kind': 'derived_fixture'} for n in names]
    files += [{'source': row['path'], 'guest': '/.reference/wordpress-7.1.3/wp-includes/' + n,
        'bytes': row['bytes'], 'sha256': row['sha256'], 'guest_read_only': True, 'kind': 'wordpress_source'}
        for n, row in wp.items()]
    exact(mapping['files'], files, 'all_nine_guest_file_mappings')
    exact(mapping['wordpress_files'], wp, 'five_wordpress_mappings_agree')
    require(mapping['schema'] == 'recastpress.php-wasix-wpdb-prepared.v0.5'
        and mapping['outcome'] == 'fixed_nine_guest_sources_prepared' and mapping['inputs_unchanged'] is True,
        'prepared_scope_identity')
    archive = mapping['source_archive']
    exact({key: archive[key] for key in ['path', 'bytes', 'sha256']},
        {'path': '.reference/wordpress-7.1.3.tar.gz', 'bytes': constants['ARCHIVE_BYTES'], 'sha256': constants['ARCHIVE_SHA']},
        'fixed_wordpress_archive_metadata')
    integer(archive['members_checked'], 1, 10000)
    integer(archive['declared_expanded_bytes_checked'], 1, 256 * 1024 * 1024)
    for key in ['full_wordpress_bootstrap', 'wordpress_executed', 'wasm_executed_by_preparer',
            'native_php_executed_by_preparer', 'network_used_by_preparer', 'guest_network_enabled',
            'guest_readonly_enforcement_executed', 'wordpress_raw_source_git_objects_verified', 'product_abi_selected']:
        require(mapping[key] is False, 'unclaimed_preparation_scope')
    business = original['adapter.php'].split(constants['BUSINESS_MARKER'], 1)[1]
    require(derived['adapter.php'].split(constants['BUSINESS_MARKER'], 1)[1] == business, 'unchanged_adapter_business')
    exact(mapping['derivation']['old_sources'], {n: identity(wire) for n, wire in original.items()}, 'old_derivation_sources')
    exact(mapping['derivation']['derived_sources'], {n: identity(derived[n]) for n in names}, 'new_derivation_sources')
    require(mapping['derivation']['corpus_bytes_unchanged'] is True
        and mapping['derivation']['adapter_business_bytes_unchanged'] is True
        and mapping['derivation']['adapter_business_sha256'] == sha(business)
        and mapping['derivation']['fixed_old_source_commit'] == constants['OLD_COMMIT']
        and mapping['derivation']['old_git_objects_verified_by_preparer'] is False,
        'whole_corpus_and_business_unchanged')
    expected_derivation = {
        'fixed_old_source_commit': constants['OLD_COMMIT'], 'old_git_objects_verified_by_preparer': False,
        'old_sources': {n: identity(wire) for n, wire in original.items()},
        'derived_sources': {n: identity(derived[n]) for n in names},
        'corpus_bytes_unchanged': True, 'adapter_business_bytes_unchanged': True,
        'adapter_business_sha256': sha(business),
        'allowed_helper_changes': ast.literal_eval(derivation_fields['allowed_helper_changes']),
        'allowed_transport_changes': ast.literal_eval(derivation_fields['allowed_transport_changes']),
    }
    exact(mapping['derivation'], expected_derivation, 'entire_frozen_preparation_derivation')
    exact(set(archive), {'path', 'bytes', 'sha256', 'members_checked', 'declared_expanded_bytes_checked'},
        'complete_source_archive_metadata_fields')
    exact(mapping['wordpress_version'], ast.literal_eval(metadata_fields['wordpress_version']),
        'fixed_wordpress_version_metadata')
    exact(mapping['guest_path_labels'], ast.literal_eval(metadata_fields['guest_path_labels']),
        'fixed_guest_mirror_label_scope')
    return {'finite_readonly_guest_files': 9, 'wordpress_source_files': 5,
        'source_archive_binary_reverified_offline': False, 'all_fixture_derivations_verified': True}


def offline_host_gate(value, checker, run):
    # No guessed nonce or rewritten host result is passed to validate_host.
    # CI's nonce-sensitive verdict is source-bound; every nonce-independent
    # frame/body/state/accounting condition is independently checked below.
    require(value['schema'] == 'recastpress.php-wasix-wpdb-observation.v0.5'
        and value['outcome'] == 'fixed_wasix_wpdb_cases_passed'
        and value['phase'] == 'wpdb_cgi_contract_verified' and value['error'] is None, 'host_success_identity')
    for key in ['passed', 'physical_connection_created', 'physical_disconnect_completed', 'db_actor_joined',
            'db_actor_exited', 'fresh_runtime', 'source_rechecked_after_execution']:
        require(value[key] is True, 'host_completion_gate')
    for key in ['guest_networking', 'host_environment_forwarded', 'db_credentials_supplied_to_guest',
            'native_php_fallback', 'product_runtime', 'full_wordpress_bootstrap',
            'standard_wpdb_compatibility_proven', 'managed_permission_query_exercised']:
        require(value[key] is False, 'host_unclaimed_scope')
    require(type(value['module_compilations']) is int and value['module_compilations'] == 1
        and value['db_actor_failure'] is None, 'one_compile_and_joined_actor')
    capture = value['cgi_capture']
    for key in ['guest_exit_success', 'runtime_shutdown_returned', 'stdout_eof', 'stderr_eof']:
        require(capture[key] is True, 'complete_cgi_lifecycle')
    require(capture['stdout_overflow'] is False and capture['stderr_overflow'] is False
        and capture['stderr_hex'] == '' and capture['stderr_bytes'] == 0
        and capture['stderr_sha256'] == sha(b''), 'empty_complete_cgi_stderr')
    stdout = bytes.fromhex(capture['stdout_hex'])
    require(len(stdout) == capture['stdout_bytes'] <= 65536 and sha(stdout) == capture['stdout_sha256'], 'actual_cgi_capture')
    exact(checker['parse_complete_cgi'](stdout), capture['response'], 'actual_cgi_response_from_wire')
    exact(parse_json(bytes.fromhex(capture['response']['body_hex'])), value['php_result'], 'actual_cgi_json_body')
    rpc = value['rpc']
    for key in ['finished', 'opened_once', 'close_dispatched']:
        require(rpc[key] is True, 'rpc_finished_gate')
    require(rpc['failure'] is None and rpc['channel_open'] is False and rpc['response_pending'] is False
        and rpc['pending_bytes'] == rpc['unread_response_bytes'] == 0, 'rpc_channel_sealed')
    ops = value['operations']
    require(type(ops) is list and len(ops) == 34 and rpc['operations'] == 34, 'complete_rpc_operations')
    counts = dict.fromkeys(['hello', 'query', 'escape', 'close'], 0)
    for seq, op in enumerate(ops, 1):
        require(type(op['seq']) is int and op['seq'] == seq and op['op'] in counts
            and type(op['ok']) is bool, 'actual_rpc_sequence_and_verdict')
        require(type(op['data_hex']) is str and len(op['data_hex']) <= 8192
            and re.fullmatch('(?:[0-9a-f]{2})*', op['data_hex']), 'bounded_rpc_hex')
        counts[op['op']] += 1
        digest(op['request_frame_sha256'])
        # Only byte length is nonce-independent (the secret is always 48 ASCII).
        size_frame = json.dumps({'schema': 'recastpress.wpdb-rpc-request.v0.5', 'nonce': '0' * 48,
            'seq': seq, 'op': op['op'], 'data_hex': op['data_hex']}, separators=(',', ':')).encode() + b'\n'
        integer(op['request_frame_bytes'], 1, 16384)
        require(op['request_frame_bytes'] == len(size_frame), 'nonce_independent_request_length')
        reply = canonical({'schema': 'recastpress.wpdb-rpc-reply.v0.5', 'seq': seq,
            'ok': op['ok'], 'data': op['data']}) + b'\n'
        require(op['reply_frame_bytes'] == len(reply) <= 65536 and op['reply_frame_sha256'] == sha(reply),
            'actual_reply_wire_identity')
    php = value['php_result']
    transport = php['rpc_stats']['transport']
    exact(counts, {'hello': 1, 'query': 29, 'escape': 3, 'close': 1}, 'fixed_rpc_counts')
    exact(transport['counts'], counts, 'php_actual_rpc_counts')
    require(transport['transport_closed'] is True and transport['requests'] == 34
        and transport['kind'] == 'file-channel' and transport['channel_guest_path'] == '/rp-wpdb/channel',
        'closed_private_file_transport')
    requested, replied = sum(op['request_frame_bytes'] for op in ops), sum(op['reply_frame_bytes'] for op in ops)
    require(requested == rpc['request_bytes'] == transport['request_bytes'] <= 128 * 1024
        and replied == rpc['response_bytes'] == rpc['response_read_bytes'] == transport['reply_bytes'] <= 512 * 1024,
        'actual_rpc_byte_balance')
    setup = php['rpc_stats']['internal_session_setup']
    require(len(setup) == 4, 'four_direct_session_queries')
    queries = [op for op in ops if op['op'] == 'query']
    exact([op['data_hex'] for op in queries], [row['sql_hex'] for row in setup]
        + [row['query_hex'] for row in php['observations'] if row['state']['delta'] == 1], 'all_actual_query_trace')
    for op, row in zip(queries, setup):
        require(op['ok'] is True, 'successful_direct_setup')
        exact(op['data'], row['data'], 'actual_direct_setup_reply')
    require(ops[0]['op'] == 'hello' and ops[0]['ok'] is True and ops[-1]['op'] == 'close'
        and ops[-1]['ok'] is True and ops[-1]['data']['closed'] is True, 'actual_hello_close_sequence')
    exact(ops[0]['data']['server_version_hex'], php['server_info_hex'], 'raw_server_version_preserved')
    integer(ops[0]['data']['caps'], 0, 2**32 - 1)
    require(ops[0]['data']['caps'] & (2 | 128 | 2048 | 65536) == 0, 'forbidden_capabilities_off')
    return {'offline_nonce_recomputed': False, 'request_digest_recomputed_offline': False,
        'nonce_independent_request_lengths_verified': True, 'reply_frames_recomputed': True,
        'actual_cgi_wire_and_all_api_queries_verified': True, 'operations': 34}


def tool_inputs(source):
    names = {name for name in source.names(CGI) if ('/' not in name[len(CGI):] and name.endswith('.py'))
        or (name.startswith(CGI + 'patches/') and name.endswith('.patch'))}
    names.update([CGI + 'manifest.json', CGI + 'README.ja.md', '.github/workflows/php-wasix-cgi-build.yml'])
    return source.hashes(names)


def managed_inputs(source):
    names = [MANAGED + name for name in ['build.py', 'patch_source.py', 'manifest.json', 'source_profile.py',
        'patch-plan.json', 'patches/0001-wasi-managed-access.patch', 'fixtures/recastpress_fs_access.h']]
    names += [COMBINED + name for name in ['build.py', 'patch_source.py', 'manifest.json', 'fixtures/fiber_cgi_contract.php']]
    names += [FIBER + name for name in ['build.py', 'patch_source.py', 'manifest.json',
        'patches/0001-nts-fresh-wasi-zend-fibers.patch', 'fixtures/zend_wasix_fiber.S', 'fixtures/fiber_contract.php']]
    names += [CGI + 'patches/0001-wasi-disable-cgi-prefork.patch']
    return dict(sorted({**tool_inputs(source), **source.hashes(names)}.items()))


def build_configuration(source):
    constants = source.constants(COMBINED + 'build.py')
    for node in source.tree(MANAGED + 'build.py').body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute):
            target = node.targets[0]
            if isinstance(target.value, ast.Name) and target.value.id == 'driver':
                try:
                    constants[target.attr] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
    constants['WORK'] = ROOT / 'target/php-wasix-managed-access-probe'
    constants['LINK_FLAGS'] = ['--export=' + name for name in constants['EXPORTS']]
    constants['MAKE_ENV'] = {'WASIXCC_LINKER_FLAGS': ':'.join(constants['LINK_FLAGS'])}
    pure = source.functions(COMBINED + 'build.py', ['command_recipe', 'build_config'], constants)
    return pure['build_config']()


def managed_gate(data, raw, source, provenance=None):
    build, tools = data['build.json'], data['toolchain.json']
    definition = parse_json(source.read(MANAGED + 'manifest.json'))
    tool_definition = parse_json(source.read(CGI + 'manifest.json'))
    inputs = managed_inputs(source)
    exact(build['input_files'], inputs, 'complete_managed_builder_inputs')
    exact(tools['input_files'], tool_inputs(source), 'complete_toolchain_inputs')
    require(build['schema'] == 'recastpress.php-wasix-managed-access-build.v0.5'
        and build['variant'] == 'nts-fresh-wasi-zend-fibers-managed-access-v1'
        and build['outcome'] == 'managed_access_candidate_built_not_executed'
        and build['phase'] == 'build_complete' and build['tracked_sources_clean_before_build'] is True
        and build['binaryen_validation_enabled'] is True, 'complete_managed_build_identity')
    for key in ['guest_executed', 'php_guest_executed', 'patched_php_executed', 'product_runtime_selected',
            'native_runtime_fallback_used', 'redistributed_binaries', 'php_wasm_fiber_cgi_compatibility_verified',
            'postopt_module_inspected', 'sysroot_source_build_attestation_verified']:
        require(build[key] is False, 'unclaimed_managed_build_scope')
    require(tools['outcome'] == 'verified_host_build_tools_prepared' and tools['guest_executed'] is False
        and tools['product_runtime_selected'] is False and tools['redistributed_binaries'] is False,
        'prepared_tools_scope')
    require(build['toolchain']['receipt_sha256'] == sha(raw['toolchain.json'])
        and build['toolchain']['manifest_sha256'] == sha(source.read(CGI + 'manifest.json')),
        'same_job_build_toolchain_binding')
    exact(build['toolchain']['assets'], [{key: asset[key] for key in ['name', 'asset_id', 'bytes', 'sha256']}
        for asset in tool_definition['assets']], 'fixed_build_assets')
    exact({row['name'] for row in tools['assets']}, {row['name'] for row in tool_definition['assets']},
        'four_fixed_tool_asset_names')
    require(len(tools['assets']) == 4, 'four_fixed_tool_assets')
    for pin in tool_definition['assets']:
        row = next(row for row in tools['assets'] if row['name'] == pin['name'])
        for key in ['name', 'repository', 'tag', 'asset_id', 'url', 'bytes', 'sha256']:
            exact(row[key], pin[key], 'fixed_tool_asset_metadata')
        require(row['verified'] is True and row['observed_bytes'] == pin['bytes']
            and row['observed_sha256'] == pin['sha256'], 'observed_tool_archive_metadata')
        exact(set(row['required_files']), set(pin['required_files']), 'tool_required_file_inventory')
        for item in row['required_files'].values():
            integer(item['bytes'], 1, 512 * 1024 * 1024)
            digest(item['sha256'])
    exact(build['build_config'], build_configuration(source), 'entire_frozen_build_configuration')
    exact(set(build['sources']), set(tool_definition['sources']), 'three_source_checkouts')
    for name, pin in tool_definition['sources'].items():
        row = build['sources'][name]
        exact({key: row[key] for key in ['repository', 'commit', 'directory']},
            {**{key: pin[key] for key in ['repository', 'commit']},
             'directory': 'target/php-wasix-managed-access-probe/' + pin['directory']}, 'fixed_source_checkout')
    require(build['sources']['deps']['rebuilt_here'] is False
        and build['sources']['deps']['all_archives_linked_verified'] is False, 'prebuilt_dependency_scope')
    require(len(build['sources']['deps']['prebuilt_archive_files']) == 28, 'fixed_prebuilt_archive_inventory')
    for path, item in build['sources']['deps']['prebuilt_archive_files'].items():
        relative(path)
        require(path.endswith('.a'), 'dependency_archive_suffix')
        integer(item['bytes'], 8, 512 * 1024 * 1024)
        digest(item['sha256'])
    require(len(build['source_patches']) == len(definition['source_patches']) == 3, 'ordered_three_patch_chain')
    for patch, plan in zip(build['source_patches'], definition['source_patches']):
        for key in ['kind', 'path', 'bytes', 'sha256']:
            exact(patch[key], plan[key], 'fixed_patch_identity')
        require(sha(source.read(plan['path'])) == plan['sha256']
            and patch['source_commit'] == definition['upstream']['commit'] and patch['index_updated'] is False,
            'patch_source_binding')
        for key in ['applied', 'git_apply_check_passed', 'git_apply_passed']:
            require(patch[key] is True, 'patch_application_gate')
        if plan['kind'] == 'managed_access':
            for key in ['fixed_own_targets_verified_before_patch', 'prior_canonical_patch_set_verified',
                    'installed_header_matches_fixed_fixture', 'canonical_patch_targets_disjoint']:
                require(patch[key] is True, 'managed_patch_order_and_header')
            require(patch['tracked_sources_clean_before_patch'] is False, 'managed_patch_follows_two_patches')
        else:
            require(patch['combined_tracked_source_clean_before_first_patch'] is True, 'canonical_patch_clean_base')
        exact(patch['before'], {row['path']: row['before'] for row in plan['files']}, 'whole_patch_source_before')
        exact(set(patch['after']), {row['path'] for row in plan['files']}, 'whole_patch_source_after_set')
        for row in plan['files']:
            after = patch['after'][row['path']]
            exact({key: after[key] for key in row['after']}, row['after'], 'whole_patch_source_after')
            if row['before'] is not None:
                exact(build['tracked_changes_after_build'][row['path']],
                    {key: after[key] for key in ['bytes', 'sha256']}, 'patched_source_unchanged_after_build')
    header = source.read(MANAGED + 'fixtures/recastpress_fs_access.h')
    exact({key: build['source_patches'][2]['after']['main/recastpress_fs_access.h'][key] for key in ['bytes', 'sha256']},
        identity(header), 'installed_header_byte_identity')
    for key, config_key in [('php_wasm', 'wasm_output'), ('raw_wasm', 'raw_output'), ('linked_php_output', 'linked_output')]:
        row = build[key]
        require(row['path'] == build['build_config'][config_key] and row['wasm_header_verified'] is True
            and row['executed'] is False, 'generated_module_identity')
        integer(row['bytes'], 8, 128 * 1024 * 1024)
        digest(row['sha256'])
    exact(build['raw_wasm'], {**build['linked_php_output'], 'path': build['raw_wasm']['path']}, 'raw_and_linked_module_identity')
    require(len(build['build_steps']) == 3, 'three_build_steps')
    for step, recipe in zip(build['build_steps'], build['build_config']['command_recipe']):
        exact({key: step[key] for key in recipe}, recipe, 'actual_build_command_recipe')
        for key in ['completed', 'owned_leader_reaped', 'live_output_quota_enforced', 'output_capture_complete']:
            require(step[key] is True, 'build_command_owned_completion')
        require(type(step['exit_code']) is int and step['exit_code'] == 0 and step['process_group_failure_cleanup'] is False,
            'normal_build_command_exit')
    if provenance is not None:
        require(provenance['build_receipt_sha256'] == sha(raw['build.json']), 'same_job_managed_receipt')
        exact(provenance['module'], build['php_wasm'], 'same_job_module_metadata')
        constants = source.constants(MANAGED + 'managed_check.py')
        namespace = source.functions(MANAGED + 'managed_check.py', ['validate_provenance'], {
            'VARIANT': constants['VARIANT'], 'SOURCES': constants['SOURCES'], 'SHA': SHA,
            'MODULE': ROOT / 'target/php-wasix-managed-access-probe/php-fiber-cgi.wasm',
            'source_bytes': lambda path, read_source=None: source.read(Path(path).relative_to(ROOT).as_posix()),
            'equal': exact})
        namespace['validate_provenance'](provenance, read_source=source.read, build_inputs=inputs)
        require(provenance['builder_provenance']['toolchain_receipt_sha256'] == sha(raw['toolchain.json'])
            and provenance['builder_provenance']['raw_module_sha256'] == build['raw_wasm']['sha256'],
            'all_provenance_module_tool_receipts_bound')
    return {'complete_managed_source_inputs': len(inputs), 'three_patches_and_whole_source_identities_verified': True,
        'same_job_toolchain_and_module_metadata_bound': True, 'source_tool_module_binaries_reverified': False}


def positive_gate(data, raw, source, backend):
    run, mapping = data['run.json'], data['prepared.json']
    require(run['schema'] == 'recastpress.php-wasix-wpdb-check.v0.5' and run['passed'] is True
        and run['outcome'] == 'fixed_wasm_wpdb_database_passed' and run['backend'] == backend
        and run['platform'] == 'linux' and run['build_only'] is False and run['owned_cleanup_completed'] is True,
        'positive_linux_runtime_verdict')
    for key in ['product_runtime_selected', 'product_abi_selected', 'standard_wpdb_compatibility_verified',
            'memory_cpu_limits_verified_for_this_integration', 'all_resources_immediately_reclaimed']:
        require(run[key] is False, 'unclaimed_integrated_runtime_scope')
    exact(run['input_files'], original_runtime_inputs(source), 'complete_frozen_runtime_inputs')
    exact(run['driver_before'], run['driver_after'], 'same_driver_sources_before_and_after')
    driver_plan = parse_json(source.read('experiments/wpdb-rpc-probe/driver-plan.json'))
    staged = run['driver_after']
    require(staged['schema'] == 'recastpress.wpdb-rpc-driver-preparation.v0.5'
        and staged['outcome'] == 'fixed_driver_sources_staged' and staged['patch_applied_in_owned_stage'] is True
        and staged['plan_sha256'] == sha(source.read('experiments/wpdb-rpc-probe/driver-plan.json'))
        and staged['patch_sha256'] == driver_plan['patch']['sha256'], 'driver_source_and_patch_bindings')
    for pin in driver_plan['crates']:
        expected_inventory = driver_plan['patch']['source_tree_inventory_after_sha256'] if pin['name'] == 'mysql_async' else pin['source_file_inventory_sha256']
        exact(staged['source_trees'][pin['name']], {'files': pin['local_regular_files_matching_crate_archive'],
            'inventory_sha256': expected_inventory}, 'all_driver_source_files_before_after')
    for key in ['pinned_registry_sources_and_archives_modified', 'existing_lock_modified', 'network_used_by_preparer',
            'database_started', 'database_connected', 'driver_compiled', 'handshake_wire_verified',
            'standard_wpdb_compatibility_verified']:
        require(staged[key] is False, 'driver_preparation_scope')
    exact(run['prepared'], mapping, 'same_job_prepared_receipt')
    prepared_summary = prepared_gate(mapping, source)
    require(run['owned_build_reaped'] is True and run['owned_host_reaped'] is True
        and type(run['host_exit_code']) is int and run['host_exit_code'] == 0,
        'owned_host_normal_return')
    require(run['host_stdout_sha256'] == sha(canonical(run['observation']) + b'\n')
        and run['host_stderr_sha256'] == sha(b'WPDB_WASIX_PHASE=module_compile\nWPDB_WASIX_PHASE=cgi_execution\n'),
        'actual_host_stdout_and_phase_capture')
    for key in ['container_started', 'container_stopped', 'container_removed', 'container_normal_exit_verified',
            'owned_database_identity_verified_before_fixture_ddl', 'independent_final_database_rows_verified']:
        require(run[key] is True, 'owned_container_completion')
    require(run['production_database_used'] is False and run['publisher_signature_verified'] is False,
        'owned_container_signature_and_production_scope')
    require(run.get('forced_owned_container_cleanup', False) is False
        and run.get('normal_container_stop_failed', False) is False, 'normal_container_shutdown_required')
    for row in run['commands']:
        require(row['owned_reaped'] is True, 'container_command_not_reaped')
    pin = parse_json(source.read(PREFIX + 'container-images.json'))['images'][backend]
    require(run['image'] == pin['repository'] + '@' + pin['platform_digest']
        and run['image_config_digest'] == pin['image_config_digest'], 'fixed_database_image_digest')
    version = run['sql_server_version']
    require(type(version) is str and (version == pin['tag'] or version.startswith(pin['tag'] + '-')),
        'independent_database_version_metadata')
    require(run['units']['passed'] is True and run['units']['owned_reaped'] is True, 'actual_host_units_passed')
    names = run['units']['tests']
    require(type(names) is list and len(names) >= 10 and len(names) == len(set(names)), 'actual_unit_inventory')
    value = run['observation']
    checker = frozen_checker(source)
    api_summary = checker['validate_php'](value['php_result'], backend, mapping)
    expected_loaded = {'wp-includes/' + name: row['sha256'] for name, row in mapping['wordpress_files'].items()}
    exact(value['php_result']['loaded_source_sha256'], expected_loaded, 'actual_loaded_five_wordpress_sources')
    raw_version = bytes.fromhex(value['php_result']['server_info_hex']).decode('ascii', errors='strict')
    require(raw_version == version or backend == 'mariadb' and raw_version == '5.5.5-' + version,
        'unmodified_raw_handshake_vs_server_version')
    rpc_summary = offline_host_gate(value, checker, run)
    function = next(node for node in source.tree(PREFIX + 'run_check.py').body
        if isinstance(node, ast.FunctionDef) and node.name == 'validate_host')
    require(isinstance(function.body[-1], ast.Return), 'frozen_host_validation_summary')
    expected_verdict = ast.literal_eval(function.body[-1].value)
    exact(run['rpc_validation'], expected_verdict, 'ci_nonce_sensitive_verdict_source_binding')
    exact(run['api_validation'], api_summary, 'ci_api_verdict_matches_offline_validation')
    managed_summary = managed_gate(data, raw, source, run['module_provenance'])
    inspector = source.functions(COMBINED + 'fiber_cgi_check.py', ['validate_inspection'], {'equal': exact})
    inspector['validate_inspection'](value['fiber_adapter'], data['build.json']['php_wasm'])
    exact(value['module'], {'path': data['build.json']['php_wasm']['path'], 'bytes': data['build.json']['php_wasm']['bytes'],
        'sha256': data['build.json']['php_wasm']['sha256'], 'verified': True}, 'host_used_same_job_module')
    exact(value['build_receipt'], {'path': MANAGED + 'evidence/build.json', 'sha256': sha(raw['build.json']),
        'verified': True, 'full_chain_verified_by_host': False}, 'host_used_same_job_build_receipt')
    managed_names = source.constants(MANAGED + 'managed_check.py')['SOURCES']
    exact(value['managed_sources'], [{'path': name, 'bytes': len(source.read(name)), 'sha256': sha(source.read(name))}
        for name in managed_names], 'all_fourteen_compiled_managed_sources')
    exact(value['prepared']['files'], mapping['files'], 'host_nine_readonly_file_map')
    require(value['prepared']['sha256'] == sha(raw['prepared.json']) and value['prepared']['verified'] is True
        and value['prepared']['full_archive_verified_by_host'] is False, 'host_prepared_receipt_binding')
    bound = {row['path']: {'bytes': row['bytes'], 'sha256': row['sha256']} for row in value['prepared']['input_files']}
    require(len(bound) == len(value['prepared']['input_files']) == 10, 'ten_compiled_preparation_inputs')
    exact(bound, {name: identity(source.read(name)) for name in mapping['input_files']}, 'compiled_fixture_and_helper_inputs')
    return {'api': api_summary, 'rpc': rpc_summary, 'prepared': prepared_summary, 'managed': managed_summary,
        'ci_nonce_sensitive_validation_bound_to_frozen_source': True}


def verifier_identity():
    return {Path(__file__).resolve().relative_to(ROOT).as_posix(): sha(read(Path(__file__).resolve(), 512 * 1024))}


def failed_observations(data, raw, source):
    summary = {'integration_passed': False, 'raw_failures_preserved': True}
    run = data.get('run.json')
    if run is None:
        return summary
    for key in ['outcome', 'passed', 'failure', 'failure_stage', 'container_started', 'container_stopped',
            'container_removed', 'container_normal_exit_verified', 'independent_final_database_rows_verified']:
        if key in run:
            summary['recorded_' + key] = copy.deepcopy(run[key])
    if type(run.get('input_files')) is dict:
        try:
            exact(run['input_files'], original_runtime_inputs(source), 'failed_runtime_complete_source_inventory')
        except Exception as error:
            summary['complete_runtime_source_inventory_verified'] = False
            summary['runtime_source_inventory_failure_class'] = type(error).__name__
        else:
            summary['complete_runtime_source_inventory_verified'] = True
    # Successful preparation/build components may be verified without turning
    # the failed integration or the overall CI run into a positive checkpoint.
    if 'prepared.json' in data:
        try:
            summary['preparation_metadata'] = prepared_gate(data['prepared.json'], source)
        except Exception as error:
            summary['preparation_metadata_verification_failure_class'] = type(error).__name__
    if 'build.json' in data and 'toolchain.json' in data:
        try:
            summary['build_metadata'] = managed_gate(data, raw, source, run.get('module_provenance'))
        except Exception as error:
            summary['build_metadata_verification_failure_class'] = type(error).__name__
    value = run.get('observation')
    if type(value) is not dict:
        return summary
    for key in ['phase', 'passed', 'outcome', 'failure_reason', 'physical_connection_created',
            'physical_disconnect_completed', 'db_actor_joined', 'db_actor_exited', 'db_actor_failure']:
        if key in value:
            summary['recorded_host_' + key] = copy.deepcopy(value[key])
    ops = value.get('operations')
    if type(ops) is list:
        integer(len(ops), 0, 64)
        summary['recorded_rpc_operations'] = len(ops)
    capture = value.get('cgi_capture')
    if type(capture) is dict:
        for stream in ['stdout', 'stderr']:
            if stream + '_hex' not in capture:
                continue
            wire = bytes.fromhex(capture[stream + '_hex'])
            integer(len(wire), 0, 65536)
            require(capture[stream + '_bytes'] == len(wire)
                and capture[stream + '_sha256'] == sha(wire), 'failed_cgi_raw_capture_identity')
            summary['recorded_' + stream] = {'bytes': len(wire), 'sha256': sha(wire),
                'eof': capture.get(stream + '_eof'), 'overflow': capture.get(stream + '_overflow')}
        summary['recorded_guest_exit_success'] = capture.get('guest_exit_success')
        summary['recorded_runtime_shutdown_returned'] = capture.get('runtime_shutdown_returned')
        try:
            checker = frozen_checker(source)
            parsed = checker['parse_complete_cgi'](bytes.fromhex(capture['stdout_hex']))
            php = parse_json(bytes.fromhex(parsed['body_hex']))
        except Exception:
            summary['positive_profile_cgi_parse_succeeded'] = False
        else:
            summary['positive_profile_cgi_parse_succeeded'] = True
            summary['raw_php_passed'] = php.get('passed')
            summary['raw_php_failure'] = php.get('failure')
            if type(php.get('observations')) is list:
                summary['raw_php_api_observations'] = len(php['observations'])
            summary['raw_php_body_sha256'] = parsed['body_sha256']
            # Parse success is never substituted for failed guest execution.
            summary['failed_guest_execution_promoted_to_success'] = False
    return summary


def immutable_write(path, wire):
    require(path.resolve() == path and path.is_relative_to(HERE / 'evidence'), 'import_destination_escaped')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(read(path, 4 * 1024 * 1024) == wire, 'immutable_import_bytes_changed')
    else:
        with path.open('xb') as stream:
            stream.write(wire)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    require(re.fullmatch('[1-9][0-9]{0,19}', args.run), 'run_id_shape')
    work = ROOT / 'target/php-wasix-wpdb-probe/ci'
    metadata_path = work / ('api-' + args.run + '-' + args.backend + '.json')
    api_wire = read(metadata_path, 128 * 1024)
    api = parse_json(api_wire)
    metadata_gate(api, args.run, args.backend)
    privacy_gate(api)
    source = GitSource(api['head_sha'])
    raw, data, members = archive_gate(work / ('run-' + args.run + '-' + args.backend + '.zip'), api, args.backend)
    for value in data.values():
        privacy_gate(value)
    stages_success = stages_gate(data['ci-stages.json'], api, args.backend, source)
    all_success = api['run_conclusion'] == api['job']['conclusion'] == 'success' and stages_success
    if all_success:
        exact(set(raw), set(member_map(args.backend)), 'five_positive_json_members_required')
    binding_error = None
    try:
        bindings = sum(source_bindings_in_records(source, value) for value in data.values())
    except Exception as error:
        if all_success:
            raise
        # Preserve an API-pinned failure even when its own source verification
        # failed; never manufacture a positive source-binding claim for it.
        bindings = 0
        binding_error = type(error).__name__
    summary = {'overall_positive': False, 'raw_verdicts_rewritten': False,
        'source_binding_entries_checked': bindings, 'source_binding_verification_passed': binding_error is None,
        'source_binding_failure_class': binding_error, 'present_json_members': sorted(raw)}
    if all_success:
        summary.update(positive_gate(data, raw, source, args.backend))
        summary['overall_positive'] = True
    else:
        if 'run.json' in data:
            summary['retained_run_outcome'] = data['run.json'].get('outcome')
            summary['retained_run_passed'] = data['run.json'].get('passed')
        summary['failed_observations'] = failed_observations(data, raw, source)
    proof = {'schema': 'recastpress.php-wasix-wpdb-ci-import.v0.5', 'repository': REPOSITORY,
        'repository_id': REPOSITORY_ID, 'run_id': api['run_id'], 'run_attempt': api['run_attempt'],
        'source_commit': source.commit, 'backend': args.backend, 'run_conclusion': api['run_conclusion'],
        'job': api['job'], 'artifact': api['artifact'], 'api_metadata_sha256': sha(api_wire),
        'archive_member_map': members, 'raw_json': {name: identity(wire) for name, wire in raw.items()},
        'original_git_sources': {name: identity(wire) for name, wire in sorted(source.cache.items())},
        'verifier_input_files': verifier_identity(), 'verification': summary,
        'offline_nonce_recomputed': False, 'source_tool_module_archive_binaries_reverified': False,
        'privacy_exception_scope': 'exact public CI repository workspace allowed only within original clang tool-version JSON; no absolute path copied into this proof',
        'network_used_by_importer': False, 'php_wasm_or_database_executed_by_importer': False,
        'product_runtime_selected': False, 'product_abi_selected': False,
        'standard_wpdb_compatibility_verified': False}
    public = (json.dumps(proof, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    privacy_gate(proof)
    require(len(public) <= 512 * 1024, 'provenance_quota')
    if not args.verify_only:
        destination = HERE / ('evidence/ci-' + args.run + '-' + args.backend)
        for name, wire in raw.items():
            immutable_write(destination / 'raw' / members[name], wire)
        immutable_write(destination / 'api-metadata.json', api_wire)
        immutable_write(destination / 'provenance.json', public)
    print('WPDB CI evidence: ' + ('verified positive' if summary['overall_positive'] else 'preserved partial/failure')
        + ' (' + args.backend + ')')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('WPDB CI import rejected: ' + reason, file=sys.stderr)
        sys.exit(1)
