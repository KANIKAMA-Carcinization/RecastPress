"""Import eleven public CI JSON records without executing PHP, Wasm, DB or network.

Only a complete response identical to the pinned synthetic native home and missing-post responses may
contain public HTML bytes. Private SQL, results, CGI and nonce bytes remain
unavailable: their digests are source-bound CI observations, not recomputed.
"""
import argparse
import ast
import importlib.util
import io
import json
from pathlib import Path
import re
import stat
import sys
import tomllib
from types import SimpleNamespace
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREFIX = 'experiments/php-wasix-wp-http-probe/'
CONTEXT = 'experiments/php-wasix-wp-request-context-probe/'
THEME = 'experiments/php-wasix-wp-request-probe/'
BOOT = 'experiments/php-wasix-wp-bootstrap-probe/'
SHARED = CONTEXT + 'import_evidence.py'
spec = importlib.util.spec_from_file_location('rp_home_offline_bootstrap_verifier', ROOT / SHARED)
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)
theme = context.theme
base = context.base
old = base.old
require, sha, read, identity, exact, canonical, parse_json = (
    base.require, base.sha, base.read, base.identity, base.exact, base.canonical, base.parse_json)


def member_map(backend):
    return {'run.json': PREFIX + 'evidence/linux-' + backend + '.json',
        'host-build.json': PREFIX + 'evidence/linux-build.json',
        'context-build.json': CONTEXT + 'evidence/linux-build.json',
        'theme-build.json': THEME + 'evidence/linux-build.json',
        'bootstrap-build.json': BOOT + 'evidence/linux-build.json',
        'ci-stages.json': PREFIX + 'evidence/ci-stages.json',
        'build.json': old.MANAGED + 'evidence/build.json',
        'toolchain.json': old.CGI + 'evidence/toolchain.json',
        'prepared.json': 'target/php-wasix-wp-request-context-probe/prepared.json',
        'theme-prepared.json': 'target/php-wasix-wp-request-probe/prepared.json',
        'bootstrap-prepared.json': 'target/php-wasix-wp-bootstrap-probe/prepared.json'}


native_references = context.native_references
php_diagnostic = context.php_diagnostic
operation_types = context.operation_types


def observer(source):
    name = PREFIX + 'http_observation.py'; constants = source.constants(name)
    pattern = next(n.value.args[0] for n in source.tree(name).body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'TOKEN' for t in n.targets))
    names = ['_field', '_native', '_headers', '_parse', 'observe_wire', 'observe_rejection',
        '_fixed_request', 'self_test']
    pure = source.functions(name, names, {**constants, 'email': __import__('email.utils'),
        'TOTAL_LIMIT': constants['HEADER_LIMIT'] + constants['BODY_LIMIT'],
        'TOKEN': re.compile(ast.literal_eval(pattern)), 'FORBIDDEN_ORIGIN': constants['TRANSPORT'] |
            {'status', 'transfer-encoding', 'trailer', 'te', 'keep-alive', 'proxy-connection', 'upgrade'}})
    request_node = next(n for n in source.tree(name).body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'REQUESTS' for t in n.targets))
    require(isinstance(request_node.value, ast.Dict), 'finite_source_request_dictionary')
    expression = ast.Expression(body=request_node.value)
    pure['REQUESTS'] = eval(compile(expression, name, 'eval'), pure)
    rejection_node = next(n for n in source.tree(name).body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'REJECTIONS' for t in n.targets))
    require(isinstance(rejection_node.value, ast.DictComp), 'finite_source_rejection_dictionary')
    pure['REJECTIONS'] = eval(compile(ast.Expression(body=rejection_node.value), name, 'eval'), pure)
    require(pure['REJECTIONS'] == {label: {'status': 400, 'body': b'fixed request rejected\n'}
        for label in ('wrong_host', 'post', 'cookie', 'authorization', 'nonzero_length', 'unknown_query', 'unknown_header')}
        , 'source_fixed_rejection_profile')
    require(len(pure['REQUESTS']) == 9 and set(pure['REJECTIONS']) < set(pure['REQUESTS']), 'finite_http_request_sets')
    inputs = {constants['CGI_SOURCE']: constants['CGI_SOURCE_SHA256'], name: sha(source.read(name))}
    exact(sha(source.read(constants['CGI_SOURCE'])), constants['CGI_SOURCE_SHA256'], 'fixed_http_cgi_parser_source')
    for pin in constants['ORACLES'].values():
        exact(identity(source.read(pin['path'])), {k: pin[k] for k in ('bytes', 'sha256')}, 'http_native_oracle_whole_pin')
        inputs[pin['path']] = pin['sha256']
    references = native_references(source)
    pure['native_references'] = lambda: {backend: {case: native['observations'][case]['response']
        for case in ('home', 'missing')} for backend, native in references.items()}
    pure['input_hashes'] = lambda: dict(sorted(inputs.items()))
    return pure


def public_gate(value, references=None, fields=None, rejection_contract=None):
    if fields is None: fields = []
    def inspect(v):
        if type(v) is dict:
            if 'http_rejections' in v:
                cases = v['http_rejections']
                require(type(cases) is dict and set(cases) <= {'wrong_host', 'post', 'cookie',
                    'authorization', 'nonzero_length', 'unknown_query', 'unknown_header'},
                    'closed_public_http_rejection_labels')
                for label, observation in cases.items():
                    require(type(observation) is dict and rejection_contract is not None,
                        'fixed_http_rejection_metrics_object_required')
                    http_metrics_gate(observation, label, None, rejection_contract)
                # This is a fixed test-case label, never an HTTP field/value.
                # Preserve its entire value for recursive privacy inspection.
                v = {**v, 'http_rejections': {('fixed_authorization_rejection' if k == 'authorization' else k): item
                    for k, item in cases.items()}}
            if 'origin_header_fields' in v:
                require(any(canonical(v['origin_header_fields']) == canonical(f) for f in fields),
                    'only_exact_known_http_origin_fields')
                v = {k: item for k, item in v.items() if k != 'origin_header_fields'}
            require(not any(k in v for k in ('http_wire', 'http_wire_hex', 'wire_hex', 'raw_http',
                'raw_http_wire', 'raw_wire', 'request_hex', 'request_wire_hex', 'response_wire_hex')),
                'raw_http_wire_in_public_record')
            if 'request_wire' in v:
                item = v['request_wire']
                require(type(item) is dict and set(item) == {'bytes', 'sha256'}, 'only_http_request_wire_identity_public')
                old.integer(item['bytes'], 1, 1024); old.digest(item['sha256'])
            return {k: inspect(item) for k, item in v.items()}
        if type(v) is list: return [inspect(item) for item in v]
        return v
    context.public_gate(inspect(value), references)


def origin_maps(source, references):
    pure = observer(source)
    return [{name: [v.hex() for v in values] for name, values in sorted(
        pure['_native'](references['mysql']['observations'][case]['response'])[1].items())}
        for case in ('home', 'missing')]


def api_gate(api, run, backend):
    # Reuse the strict compact metadata shape, changing only the explicit job
    # and artifact identities; never rewrite metadata to satisfy an old gate.
    exact(set(api), {'repository_id', 'repository', 'run_id', 'run_attempt', 'head_sha',
        'run_conclusion', 'job', 'artifact'}, 'api_fields')
    exact(api['repository_id'], old.REPOSITORY_ID, 'api_repository_id')
    exact(api['repository'], old.REPOSITORY, 'api_repository')
    old.integer(api['run_id'], 1); old.integer(api['run_attempt'], 1, 1000)
    require(str(api['run_id']) == run and api['run_conclusion'] in old.CONCLUSIONS, 'api_run')
    require(type(api['head_sha']) is str and re.fullmatch('[0-9a-f]{40}', api['head_sha']), 'api_commit')
    job, artifact = api['job'], api['artifact']
    exact(set(job), {'id', 'name', 'conclusion', 'steps'}, 'api_job_fields'); old.integer(job['id'], 1)
    require(job['name'] == 'Cranelift HTTP WP / ' + backend and job['conclusion'] in old.CONCLUSIONS, 'api_job')
    require(type(job['steps']) is list and 1 <= len(job['steps']) <= 64, 'api_steps')
    for step in job['steps']:
        exact(set(step), {'name', 'conclusion'}, 'api_step_fields')
        require(type(step['name']) is str and 0 < len(step['name']) <= 160
            and step['conclusion'] in old.CONCLUSIONS, 'api_step')
    exact(set(artifact), {'id', 'name', 'size_in_bytes', 'digest'}, 'api_artifact_fields')
    old.integer(artifact['id'], 1); old.integer(artifact['size_in_bytes'], 1, 24 * 1024 * 1024)
    require(artifact['name'] == 'php-wasix-wp-http-' + backend + '-' + str(api['run_attempt'])
        and type(artifact['digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']), 'api_artifact')


def archive_gate(path, api, backend, source):
    wire = read(path, 24 * 1024 * 1024)
    exact(identity(wire), {'bytes': api['artifact']['size_in_bytes'],
        'sha256': api['artifact']['digest'][7:]}, 'api_archive_identity')
    reverse = {p: n for n, p in member_map(backend).items()}
    raw, members, expanded = {}, {}, 0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        entries = archive.infolist()
        require(1 <= len(entries) <= 11 and len({r.filename for r in entries}) == len(entries), 'zip_member_count')
        for entry in entries:
            old.relative(entry.filename)
            require(entry.filename in reverse and not entry.is_dir() and entry.flag_bits & 1 == 0
                and stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFLNK
                and 0 < entry.file_size <= 8 * 1024 * 1024, 'public_json_member')
            expanded += entry.file_size; require(expanded <= 24 * 1024 * 1024, 'zip_expansion_limit')
            value = archive.read(entry); require(len(value) == entry.file_size, 'zip_member_size')
            name = reverse[entry.filename]; raw[name] = value; members[name] = entry.filename
    require('ci-stages.json' in raw, 'stage_record_required')
    data = {n: parse_json(b) for n, b in raw.items()}
    response = native_references(source)
    fields = origin_maps(source, response)
    rejection_contract = observer(source)
    for value in data.values(): public_gate(value, response, fields, rejection_contract)
    return raw, data, members


def stages_gate(value, api, backend, source):
    exact(set(value), {'schema', 'source_commit', 'run_id', 'run_attempt', 'backend', 'stages'}, 'stage_fields')
    require(value['schema'] == 'recastpress.php-wasix-wp-http-ci-stages.v0.5'
        and value['source_commit'] == api['head_sha'] and value['run_id'] == str(api['run_id'])
        and value['run_attempt'] == str(api['run_attempt']) and value['backend'] == backend, 'stage_identity')
    names = {}
    for block in source.read('.github/workflows/php-wasix-wp-http.yml').decode().split('      - name: ')[1:]:
        match = re.search(r'^        id: ([a-z_]+)$', block, re.M)
        if match: names[match[1]] = block.splitlines()[0].strip()
    exact(set(names), {'php', 'deps', 'parser', 'packages', 'tools', 'module', 'guest', 'rust', 'bootstrap', 'theme', 'context', 'host', 'contract'},
        'workflow_stage_inventory')
    exact(set(value['stages']), set(names), 'actual_stage_inventory')
    for key, name in names.items():
        rows = [r for r in api['job']['steps'] if r['name'] == name]
        require(len(rows) == 1, 'api_stage_step')
        exact(value['stages'][key], rows[0]['conclusion'], 'api_vs_stage_conclusion')
    return all(v == 'success' for v in value['stages'].values())


def return_fields(source, name, function):
    node = next(n for n in source.tree(name).body if isinstance(n, ast.FunctionDef) and n.name == function)
    require(isinstance(node.body[-1], ast.Return) and isinstance(node.body[-1].value, ast.Dict), 'source_return_dictionary')
    value = node.body[-1].value
    return {ast.literal_eval(k): v for k, v in zip(value.keys, value.values)}


def host_build_gate(host, source):
    text = source.read(context.SHARED).decode()
    node = next(n for n in source.tree(context.SHARED).body if isinstance(n, ast.FunctionDef) and n.name == 'host_build_gate')
    function = ast.get_source_segment(text, node)
    changes = [
        ("prefix = PREFIX if home else BOOT", "prefix = PREFIX"),
        ("'recastpress.php-wasix-wp-' + ('request' if home else 'bootstrap') + '-build.v0.5'", "'recastpress.php-wasix-wp-http-build.v0.5'"),
        ("('locked_offline_home_host_build_passed' if home else 'locked_offline_bootstrap_host_build_passed')", "'locked_offline_http_host_build_passed'"),
        ("len(host['units']['tests']) >= 31", "len(host['units']['tests']) == 21"),
    ]
    for before, after in changes:
        require(function.count(before) == 1, 'http_build_gate_derivation'); function = function.replace(before, after, 1)
    namespace = dict(theme.__dict__, PREFIX=PREFIX)
    exec(compile(function, '<frozen-http-build-gate>', 'exec'), namespace)
    anchor = namespace['host_build_gate'](host, source, True)
    exact(len(anchor['input_files']), 206, 'http_host_206_inputs')
    exact(len(host['units']['tests']), 21, 'http_host_21_units')
    return anchor


def frozen_server_validator(source, raw):
    checker = context.frozen_checker(source)
    receipt = ROOT / (CONTEXT + 'evidence/linux-build.json')
    def reader(path, maximum):
        require(Path(path) == receipt, 'only_same_job_context_receipt_read')
        require(len(raw['context-build.json']) <= maximum, 'bounded_worker_receipt')
        return raw['context-build.json']
    return source.functions(PREFIX + 'run_check.py', ['validate_server'], {
        'canonical': canonical, 'context': SimpleNamespace(validate_context=checker['validate_context'],
            build=SimpleNamespace(read=reader, RECEIPT=receipt))})['validate_server']


def http_metrics_gate(value, label, response, pure):
    from datetime import datetime, timezone
    import email.utils
    rejected = label in pure['REJECTIONS']
    fields = {'status', 'transport_headers', 'body_bytes', 'body_sha256', 'wire_bytes', 'wire_sha256',
        'eof', 'request_wire', 'request_time_window'}
    fields |= {'header_count', 'fixed_rejection_matches'} if rejected else {
        'origin_header_fields', 'body_matches_native', 'origin_headers_match_native'}
    exact(set(value), fields, 'closed_http_observation_fields')
    expected_body = pure['REJECTIONS'][label]['body'] if rejected else bytes.fromhex(response['body_hex'])
    exact(value['status'], 400 if rejected else response['status'], 'actual_http_status')
    exact(value['body_bytes'], len(expected_body), 'actual_http_body_bytes')
    exact(value['body_sha256'], sha(expected_body), 'actual_http_body_sha')
    require(value['eof'] is True, 'actual_http_client_eof')
    if rejected:
        require(value['fixed_rejection_matches'] is True, 'fixed_http_rejection')
    else:
        expected_fields = {k: [v.hex() for v in vals] for k, vals in sorted(pure['_native'](response)[1].items())}
        exact(value['origin_header_fields'], expected_fields, 'exact_native_origin_multimap')
        require(value['body_matches_native'] is True and value['origin_headers_match_native'] is True, 'http_exact_native_flags')
    transport = value['transport_headers']
    exact(set(transport), {'content_length', 'date_gmt', 'connection_close'}, 'closed_http_transport_fields')
    exact(transport['content_length'], len(expected_body), 'http_completed_content_length')
    require(transport['connection_close'] is True, 'http_explicit_connection_close')
    window = value['request_time_window']
    exact(set(window), {'started_utc', 'finished_utc', 'date_within_window_if_present'}, 'closed_http_request_window')
    require(window['date_within_window_if_present'] is True, 'http_actual_date_window_checked')
    times = []
    for key in ('started_utc', 'finished_utc'):
        require(type(window[key]) is str and len(window[key]) <= 40, 'http_time_string')
        stamp = datetime.fromisoformat(window[key])
        require(stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0, 'http_utc_time')
        times.append(stamp.timestamp())
    require(times[0] <= times[1] and times[1] - times[0] <= (10 if rejected else 310), 'http_request_time_bounds')
    date = transport['date_gmt']
    if date is not None:
        require(type(date) is str and len(date) == 29, 'http_date_shape')
        parsed = email.utils.parsedate_to_datetime(date)
        require(parsed.utcoffset().total_seconds() == 0 and email.utils.format_datetime(parsed, usegmt=True) == date,
            'http_canonical_date')
        require(times[0] - 2 <= parsed.timestamp() <= times[1] + 2, 'http_date_actual_window')
    if rejected: exact(value['header_count'], 2 + int(date is not None), 'fixed_rejection_transport_header_count')
    case = 'missing' if label == 'missing_repeat' else label
    exact(value['request_wire'], identity(pure['REQUESTS'][case]), 'source_fixed_http_request_wire')
    old.integer(value['wire_bytes'], len(expected_body) + 4, len(expected_body) + 16384)
    old.digest(value['wire_sha256'])


def positive_gate(data, raw, source, backend):
    run, host, worker, boot, oldhost = (data[k] for k in ('run.json', 'host-build.json', 'context-build.json', 'bootstrap-build.json', 'theme-build.json'))
    require(run['schema'] == 'recastpress.php-wasix-wp-http-check.v0.5' and run['passed'] is True
        and run['outcome'] == 'fixed_real_http_php_wasm_passed' and run['platform'] == 'linux'
        and run['backend'] == backend and run['source_reverified_after'] is True
        and run['owned_cleanup_completed'] is True, 'positive_http_run')
    anchor = host_build_gate(host, source)
    context.host_build_gate(worker, source); theme.host_build_gate(boot, source, False); theme.host_build_gate(oldhost, source, True)
    for key, value in [('build_receipt', host), ('context_build_receipt', worker), ('bootstrap_build_receipt', boot), ('theme_build_receipt', oldhost)]:
        exact(run[key], value, 'same_job_' + key)
    nested = {}
    for label, prefix, receipt, member in [('bootstrap', BOOT, boot, 'bootstrap-build.json'), ('theme', THEME, oldhost, 'theme-build.json'), ('context', CONTEXT, worker, 'context-build.json')]:
        nested[label] = {'receipt_path': prefix + 'evidence/linux-build.json', 'receipt_identity': identity(raw[member]),
            'executable': receipt['executable'], 'executable_identity': receipt['executable_identity'],
            'unit_executable': receipt['units']['executable'], 'unit_executable_identity': receipt['units']['executable_identity'],
            'source_inputs': len(receipt['input_files']), 'units': len(receipt['units']['tests']), 'original_build_fully_reverified': True}
    for suffix in ('before', 'after'):
        exact(host['old_build_' + suffix], nested, 'same_job_three_previous_builds_' + suffix)
        exact(worker['old_build_' + suffix], {k: nested[k] for k in ('bootstrap', 'theme')}, 'same_job_context_old_builds_' + suffix)
        exact(oldhost['old_build_' + suffix], nested['bootstrap'], 'same_job_theme_old_build_' + suffix)
    preparation = context.prepared_gate(data['prepared.json'], data['theme-prepared.json'], data['bootstrap-prepared.json'], source)
    context_sources = {'input_files': worker['input_files'], 'prepared': data['prepared.json'],
        'bootstrap_prepared': data['bootstrap-prepared.json'], 'theme_prepared': data['theme-prepared.json']}
    pure = observer(source)
    expected_sources = {'input_files': anchor['input_files'], 'context': context_sources, 'http_observer_inputs': pure['input_hashes']()}
    exact(run['sources_before'], expected_sources, 'complete_http_sources_before')
    exact(run['sources_after'], expected_sources, 'complete_http_sources_after')
    exact(run['http_observer_pure_checks'], pure['self_test'](), 'same_source_47_pure_http_checks')
    exact(run['module_provenance'], run['module_provenance_after'], 'module_unchanged')
    managed = old.managed_gate(data, raw, source, run['module_provenance'])
    previous = base.frozen_checker(source)
    native_boot, native = previous['reference'](backend), native_references(source)[backend]
    for prefix in (PREFIX, CONTEXT, THEME, BOOT):
        lock = tomllib.loads(source.read(prefix + 'Cargo.lock').decode())
        crates = [p for p in lock['package'] if p['name'] == 'serde_json']
        require(len(crates) == 1 and not any(d.startswith('indexmap') for d in crates[0].get('dependencies', [])), 'default_sorted_json_map_required')
    exact(set(run['observations']), {'install', 'read'}, 'two_direct_bootstrap_observations')
    exact(set(run['executions']), {'install', 'read'}, 'two_direct_bootstrap_executions')
    for label in ('install', 'read'):
        value = run['observations'][label]
        exact(run['validations'][label], previous['validate'](value, label, native_boot[label]), 'source_bound_bootstrap_comparison')
        operation_types(value, previous['source_operation_limit'])
        execution = run['executions'][label]; old.integer(execution['owned_pid'], 1)
        require(execution['owned_reaped'] is True, 'owned_bootstrap_reaped')
        exact(execution['exit_code'], 0, 'owned_bootstrap_exit')
        exact(execution['stderr_bytes'], 0, 'owned_bootstrap_stderr')
        exact(execution['stderr_sha256'], sha(b''), 'owned_bootstrap_empty_stderr_sha')
        exact({'bytes': execution['stdout_bytes'], 'sha256': execution['stdout_sha256']}, identity(canonical(value) + b'\n'), 'bootstrap_stdout_identity')
        php_diagnostic(value['php_result'], source, False)
    server, execution = run['http_server_observation'], run['http_server_execution']
    exact(set(server), {'schema', 'passed', 'outcome', 'requests', 'worker_executions', 'worker_build',
        'rejected_counts', 'concurrency', 'valid_execution_limit', 'bound_loopback_only',
        'graceful_shutdown_completed', 'stdin_shutdown_observed', 'stdin_protocol_error',
        'startup_private_input_used', 'private_config_on_http', 'transport_delivery_verified',
        'native_php_fallback', 'product_runtime'}, 'closed_http_server_report')
    require(server['stdin_protocol_error'] is False and type(server['stdin_shutdown_observed']) is bool,
        'closed_server_private_input_protocol')
    for key, expected in [('worker_executions', 3), ('valid_execution_limit', 3), ('concurrency', 1)]:
        exact(server[key], expected, 'typed_server_' + key)
    old.integer(execution['exit_code'], 0, 0)
    exact(execution['stderr'], identity(b''), 'typed_server_empty_stderr')
    derived_report = {'validations': {}, 'independent_context_checks': run['independent_context_checks']}
    frozen_server_validator(source, raw)(server, execution, worker, native, derived_report)
    for key in ('fresh_repeat_404_response_exact', 'all_three_owned_workers_reaped', 'request_module_compilations'):
        exact(run[key], derived_report[key], 'source_bound_server_' + key)
    exact(run['validations'], {**{k: run['validations'][k] for k in ('install', 'read')}, **derived_report['validations']}, 'all_five_source_validations')
    requests = {label: row['observation'] for label, row in zip(('home', 'missing', 'missing_repeat'), server['requests'])}
    for row in server['requests']:
        old.integer(row['worker']['exit_code'], 0, 0)
        exact(row['worker']['stderr'], identity(b''), 'typed_worker_empty_stderr')
    for value in requests.values(): operation_types(value, 4096); php_diagnostic(value['php_result'], source, True)
    for value in [*run['observations'].values(), *requests.values()]:
        module = data['build.json']['php_wasm']
        exact(value['module'], {'path': module['path'], 'bytes': module['bytes'], 'sha256': module['sha256'], 'verified': True}, 'same_job_worker_module')
        exact(value['build_receipt'], {'path': old.MANAGED + 'evidence/build.json', 'sha256': sha(raw['build.json']), 'verified': True, 'full_chain_verified_by_host': False}, 'same_job_worker_module_receipt')
    old.integer(execution['owned_pid'], 1); old.integer(execution['bound_port'], 1, 65535)
    require(execution['private_input_on_http'] is False and execution['parent_shutdown_input_closed'] is True
        and execution['capture_kind'] == 'owned_regular_files' and execution['run_retained'] is True, 'owned_server_capture_scope')
    require(re.fullmatch(r'target/php-wasix-wp-http-probe/server-runs/server-[0-9a-f]{32}', execution['run']) is not None, 'owned_server_capture_path')
    old.integer(execution['private_startup_input']['bytes'], 1, 4096); old.digest(execution['private_startup_input']['sha256'])
    ready = canonical({'schema': 'recastpress.php-wasix-wp-http-ready.v0.5', 'port': execution['bound_port']}) + b'\n'
    final = canonical(server) + b'\n'
    exact(execution['ready_line'], identity(ready), 'canonical_server_ready_stdout')
    exact(execution['final_line'], identity(final), 'canonical_server_final_stdout')
    exact(execution['stdout'], identity(ready + final), 'canonical_whole_server_stdout')
    exact(set(run['http_observations']), {'home', 'missing', 'missing_repeat'}, 'three_real_http_observations')
    exact(set(run['http_rejections']), set(pure['REJECTIONS']), 'seven_real_http_rejections')
    for label, value in run['http_observations'].items():
        http_metrics_gate(value, label, native['observations']['home' if label == 'home' else 'missing']['response'], pure)
    for label, value in run['http_rejections'].items(): http_metrics_gate(value, label, None, pure)
    require(run['real_http_listener_exercised'] is True, 'actual_loopback_http_exercised')
    for key in ('request_or_body_normalized', 'module_cache_exercised'):
        require(run[key] is False, 'unclaimed_http_' + key)
    for key in ['container_started', 'container_stopped', 'container_removed', 'container_normal_exit_verified',
        'owned_database_identity_verified_before_fixture_ddl', 'independent_wordpress_final_verified',
        'installer_schema_ddl_and_crud', 'reader_schema_crud']:
        require(run[key] is True, 'normal_container_' + key)
    exact(run['wordpress_table_count'], 12, 'twelve_original_wordpress_tables')
    for key in ['forced_owned_container_cleanup', 'normal_container_stop_failed', 'production_database_used',
        'management_credentials_supplied_to_php', 'response_normalized', 'product_runtime_selected',
        'http_listener_exercised', 'standard_wpdb_compatibility_verified', 'native_php_executed', 'native_php_fallback',
        'module_shared_across_owned_host_processes', 'memory_cpu_limits_verified_for_this_integration', 'all_resources_immediately_reclaimed']:
        require(run.get(key, False) is False, 'unclaimed_or_failure_scope_' + key)
    require(type(run['commands']) is list and len(run['commands']) > 0, 'owned_database_commands')
    for command in run['commands']: require(command['owned_reaped'] is True, 'owned_database_command_reaped')
    stamp = source.constants(THEME + 'linux_database.py')['STAMP']; seed = run['fixed_public_date_seed']
    require(seed['timestamp'] == stamp and seed['actual_database_updated'] is True
        and seed['response_body_normalized'] is False, 'fixed_public_date_seed')
    exact(seed['sql_sha256'], native['fixed_public_date_seed']['sql_sha256'], 'same_original_date_seed_sql')
    exact(run['public_date_verification'], {phase: {'post_ids': [1, 2], 'comment_ids': [1],
        'timestamp': stamp, 'verified': True} for phase in ('before_home', 'after_home')},
        'independent_exact_public_dates')
    exact(set(run['independent_context_checks']), {'before_requests', 'home', 'missing', 'missing_repeat'}, 'independent_four_context_checks')
    for label, check in run['independent_context_checks'].items():
        expected = {'missing_post_count': 0, 'original_public_state_verified': True,
            'fixed_public_dates_verified': True, 'autoload_option_count': check['autoload_option_count'],
            'autoload_max_value_bytes': check['autoload_max_value_bytes']}
        exact(check, expected, 'independent_public_state_check')
        old.integer(check['autoload_option_count'], 1, 2**31); old.integer(check['autoload_max_value_bytes'], 1, 2**31)
        php = (run['observations']['read'] if label == 'before_requests' else requests[label])['php_result']
        for k in ('autoload_option_count', 'autoload_max_value_bytes'): exact(check[k], php[k], 'independent_php_' + k)
    pin = parse_json(source.read('experiments/php-wasix-wpdb-probe/container-images.json'))['images'][backend]
    exact(run['image'], pin['repository'] + '@' + pin['platform_digest'], 'fixed_database_image')
    exact(run['image_config_digest'], pin['image_config_digest'], 'database_config_digest')
    # The actual platform string is retained exactly, with no suffix normalization.
    version = '8.4.13' if backend == 'mysql' else '11.8.9-MariaDB-ubu2404'
    exact(run['sql_server_version'], version, 'exact_linux_sql_version')
    return {'all_five_guest_executions_verified': True, 'actual_three_http_native_responses_verified': True,
        'all_seven_http_rejections_verified': True, 'prepared': preparation, 'managed': managed,
        'runtime_source_inputs': len(anchor['input_files']), 'http_host_units': len(host['units']['tests']),
        'context_host_units': len(worker['units']['tests']), 'all_three_context_snapshots_exact': True,
        'fresh_repeat_404_exact': True, 'worker_and_server_public_json_reserialization_verified': True,
        'http_wire_digests_recomputed_offline': False, 'private_config_nonce_or_rpc_digests_recomputed_offline': False}


def immutable(path, wire):
    require(path.resolve() == path and path.is_relative_to(HERE / 'evidence'), 'owned_import_path')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists(): require(read(path, 8 * 1024 * 1024) == wire, 'immutable_import_changed')
    else:
        with path.open('xb') as stream: stream.write(wire)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True); parser.add_argument('--backend', choices=['mysql', 'mariadb'], required=True)
    parser.add_argument('--verify-only', action='store_true'); args = parser.parse_args()
    require(re.fullmatch('[1-9][0-9]{0,19}', args.run), 'run_id')
    work = ROOT / 'target/php-wasix-wp-http-probe/ci'
    api_wire = read(work / ('api-' + args.run + '-' + args.backend + '.json'), 128 * 1024)
    api = parse_json(api_wire); api_gate(api, args.run, args.backend); old.privacy_gate(api)
    source = old.GitSource(api['head_sha'])
    for name in [SHARED, context.SHARED, theme.SHARED, base.LEGACY]:
        exact(source.read(name), read(ROOT / name, 512 * 1024), 'frozen_shared_verifier_helpers_changed')
    raw, data, members = archive_gate(work / ('run-' + args.run + '-' + args.backend + '.zip'), api, args.backend, source)
    stages = stages_gate(data['ci-stages.json'], api, args.backend, source)
    positive = api['run_conclusion'] == api['job']['conclusion'] == 'success' and stages
    if positive: exact(set(raw), set(member_map(args.backend)), 'eleven_positive_public_records')
    binding_error = None
    try: bindings = sum(old.source_bindings_in_records(source, v) for v in data.values())
    except Exception as error:
        if positive: raise
        bindings = 0; binding_error = type(error).__name__
    summary = {'overall_positive': False, 'raw_verdicts_rewritten': False, 'source_binding_entries_checked': bindings,
        'source_binding_verification_passed': binding_error is None, 'source_binding_failure_class': binding_error}
    if positive:
        summary.update(positive_gate(data, raw, source, args.backend)); summary['overall_positive'] = True
    else:
        run = data.get('run.json', {})
        summary.update(recorded_outcome=run.get('outcome'), recorded_passed=run.get('passed'),
            recorded_failure_stage=run.get('failure_stage'), public_guest_failures={})
        for mode, value in run.get('observations', {}).items():
            require(mode in ('install', 'read', 'home', 'missing', 'missing_repeat'), 'closed_failure_mode')
            if type(value) is not dict: continue
            public = {k: value[k] for k in ['passed', 'phase', 'failure', 'failure_reason', 'php_diagnostic'] if k in value}
            php = value.get('php_result')
            if type(php) is not dict: php = value.get('cgi_capture', {}).get('safe_php_observation')
            public['php'] = php_diagnostic(php, source, mode in ('home', 'missing', 'missing_repeat'))
            summary['public_guest_failures'][mode] = public
    proof = {'schema': 'recastpress.php-wasix-wp-http-ci-import.v0.5', 'repository': old.REPOSITORY,
        'repository_id': old.REPOSITORY_ID, 'run_id': api['run_id'], 'run_attempt': api['run_attempt'],
        'source_commit': source.commit, 'backend': args.backend, 'run_conclusion': api['run_conclusion'],
        'job': api['job'], 'artifact': api['artifact'], 'api_metadata_sha256': sha(api_wire),
        'archive_member_map': members, 'raw_json': {n: identity(b) for n, b in raw.items()},
        'original_git_sources': {n: identity(b) for n, b in sorted(source.cache.items())},
        'verifier_input_files': {p.relative_to(ROOT).as_posix(): sha(read(p, 512 * 1024))
            for p in [Path(__file__).resolve(), ROOT / SHARED, ROOT / context.SHARED, ROOT / theme.SHARED, ROOT / base.LEGACY]},
        'verification': summary, 'offline_nonce_recomputed': False, 'private_source_bytes_reconstructed': False,
        'source_tool_module_archive_binaries_reverified': False, 'network_used_by_importer': False,
        'php_wasm_or_database_executed_by_importer': False, 'product_runtime_selected': False,
        'privacy_exception_scope': 'exact public CI clang workspace; whole pinned synthetic native responses; exact normalized origin maps; authorization fixed rejection case label only; raw bytes unchanged'}
    public_gate(proof)
    wire = (json.dumps(proof, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
    require(len(wire) <= 2 * 1024 * 1024, 'import_proof_quota')
    if not args.verify_only:
        destination = HERE / ('evidence/ci-' + args.run + '-' + args.backend)
        for name, content in raw.items(): immutable(destination / 'raw' / members[name], content)
        immutable(destination / 'api-metadata.json', api_wire); immutable(destination / 'provenance.json', wire)
    print('WP HTTP CI evidence: ' + ('verified positive' if summary['overall_positive'] else 'preserved failure/partial')
        + ' (' + args.backend + ')')
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z][a-z0-9_]{0,127}', str(error)) else type(error).__name__
        print('WP HTTP import rejected: ' + reason, file=sys.stderr); sys.exit(1)
