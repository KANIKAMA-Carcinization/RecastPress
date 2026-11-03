"""Fresh pinned DB ownership for the fixed public home and missing-post cases."""
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('rp_context_frozen_theme_owner',
    ROOT / 'experiments/php-wasix-wp-request-probe/linux_database.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
require = base.require
ORACLES = {
    'mysql': '7c2a0b01fdd5e90a120b8e278368245c18618936a268baefb09ff1adba01e00b',
    'mariadb': 'a2d54133bc368f9945b5f178aafcc5c1edf60778c90247491f1ad60aeb0fe1df',
}


def reference(backend):
    require(backend in ORACLES, 'closed_context_backend')
    path = ROOT / ('experiments/wp-request-context-probe/evidence/windows-' + backend + '-native.json')
    require(path.resolve(strict=True) == path and path.is_file() and 0 < path.stat().st_size <= 2*1024*1024,
        'fixed_context_native_reference')
    wire = path.read_bytes()
    require(hashlib.sha256(wire).hexdigest() == ORACLES[backend], 'fixed_context_native_reference_changed')
    value = json.loads(wire)
    for key in ('passed', 'source_reverified_after', 'normal_admin_shutdown_verified',
        'all_command_children_reaped', 'all_owned_cgi_reaped', 'owned_database_cleanup_completed',
        'fresh_repeat_404_response_exact', 'original_install_read_baseline_reproduced'):
        require(value[key] is True, 'accepted_context_reference_' + key)
    require(value['schema'] == 'recastpress.wp-request-context-native-check.v0.5'
        and value['backend'] == backend and value['owned_cgi_count'] == value['owned_cgi_reaped'] == 3,
        'accepted_context_native_profile')
    return value


class Database(base.Database):
    def __init__(self, backend, report):
        self.context_native = reference(backend)
        super().__init__(backend, report)

    def context_request(self, case):
        require(case in ('home', 'missing'), 'closed_public_context_case')
        return {'schema': 'recastpress.php-wasix-wp-request-context-input.v0.5',
            'case': case, 'database': self.home_request()}

    def seed_public_dates(self):
        require(self.context_native['fixed_public_date_seed']['sql_sha256'] == hashlib.sha256(base.SEED).hexdigest(),
            'same_clean_native_public_date_seed')
        super().seed_public_dates()

    def verify_context_state(self, label, php=None):
        require(label in ('before_requests', 'home', 'missing', 'missing_repeat'), 'closed_context_state_phase')
        self.inspect()
        result = self.run('independent_missing_post_' + label, self.client() + [
            '--execute=SELECT COUNT(*) FROM rp_wpdb_probe.rp_posts WHERE ID=2147483647'])
        require(result.stdout.strip() == b'0' and not result.stderr, 'fixed_context_missing_post_exists')
        super().final_rows()
        result = self.run('independent_context_autoload_' + label, self.client() + [
            "--execute=SELECT COUNT(*),COALESCE(MAX(OCTET_LENGTH(option_value)),0) FROM rp_wpdb_probe.rp_options "
            "WHERE autoload IN ('yes','on','auto-on','auto')"])
        fields = result.stdout.strip().split(b'\t')
        require(not result.stderr and len(fields) == 2 and all(x.isdigit() and len(x) <= 12 for x in fields),
            'independent_context_autoload_shape')
        count, maximum = map(int, fields)
        require(count > 0 and maximum > 0, 'independent_context_autoload_empty')
        if php is not None:
            require(count == php['autoload_option_count'] and maximum == php['autoload_max_value_bytes'],
                'independent_context_autoload_differs')
        self.report.setdefault('independent_context_checks', {})[label] = {
            'missing_post_count': 0, 'original_public_state_verified': True,
            'fixed_public_dates_verified': True, 'autoload_option_count': count, 'autoload_max_value_bytes': maximum}
