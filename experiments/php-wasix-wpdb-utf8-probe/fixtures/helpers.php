<?php
declare(strict_types=1);

final class RpNativeFailure extends RuntimeException {}

function rp_check(bool $condition, string $code): void {
    if (!$condition) {
        throw new RpNativeFailure($code);
    }
}

/** Strings remain bytes; PHP scalar and collection types remain distinguishable. */
function rp_value(mixed $value): mixed {
    if (is_string($value)) {
        return ['bytes_hex' => bin2hex($value)];
    }
    if ($value === null || is_bool($value) || is_int($value)) {
        return $value;
    }
    if (is_array($value)) {
        $entries = [];
        foreach ($value as $key => $item) {
            $entries[] = ['key' => rp_value($key), 'value' => rp_value($item)];
        }
        return ['type' => 'array', 'entries' => $entries];
    }
    if ($value instanceof stdClass) {
        $properties = [];
        foreach (get_object_vars($value) as $key => $item) {
            $properties[] = ['name_hex' => bin2hex((string) $key), 'value' => rp_value($item)];
        }
        return ['type' => 'stdClass', 'properties' => $properties];
    }
    throw new RpNativeFailure('unexpected_php_value_type');
}

function rp_state(wpdb $db, int $before): array {
    return [
        'num_queries' => $db->num_queries,
        'delta' => $db->num_queries - $before,
        'num_rows' => $db->num_rows,
        'rows_affected' => $db->rows_affected,
        'insert_id' => $db->insert_id,
        'last_error_hex' => bin2hex($db->last_error),
        'last_query_hex' => bin2hex((string) $db->last_query),
    ];
}

function rp_observe(wpdb $db, array &$report, string $case, string $api, ?string $query, Closure $operation, int $delta = 1): mixed {
    $before = $db->num_queries;
    $returned = $operation();
    $state = rp_state($db, $before);
    $report['observations'][] = [
        'case' => $case,
        'api' => $api,
        'query_hex' => $query === null ? null : bin2hex($query),
        'return_type' => get_debug_type($returned),
        'returned' => rp_value($returned),
        'state' => $state,
    ];
    rp_check($state['delta'] === $delta, 'num_queries_delta_mismatch');
    return $returned;
}

function rp_query(wpdb $db, array &$report, string $case, string $query): int|bool {
    return rp_observe($db, $report, $case, 'query', $query, fn() => $db->query($query));
}

function rp_exact_keys(array $value, array $expected, string $failure): void {
    $keys = array_keys($value);
    sort($keys);
    sort($expected);
    rp_check($keys === $expected, $failure);
}

function rp_unhex(mixed $value, int $maximum = 8192): string {
    rp_check(is_string($value) && strlen($value) <= $maximum * 2
        && strlen($value) % 2 === 0 && preg_match('/\A[0-9a-f]*\z/D', $value) === 1, 'rpc_invalid_hex');
    $bytes = hex2bin($value);
    rp_check(is_string($bytes), 'rpc_hex_decode_failed');
    return $bytes;
}

function rp_decimal_int(mixed $value): int {
    rp_check(is_string($value) && preg_match('/\A(?:0|[1-9][0-9]*)\z/D', $value) === 1,
        'rpc_invalid_unsigned_decimal');
    $limit = (string) PHP_INT_MAX;
    rp_check(strlen($value) < strlen($limit) || (strlen($value) === strlen($limit) && strcmp($value, $limit) <= 0),
        'rpc_integer_out_of_native_range');
    return (int) $value;
}

function rp_error_identity(wpdb $db, array &$report): void {
    if ($db instanceof RpBridgeWpdb) {
        $identity = $db->rpc_error_identity();
        $report['syntax_error_code'] = $identity['code'];
        $report['syntax_error_state'] = $identity['state'];
    } else {
        $report['syntax_error_code'] = mysqli_errno($db->dbh);
        $report['syntax_error_state'] = mysqli_sqlstate($db->dbh);
    }
}

function rp_entry_input(string $variant): array {
    rp_check($variant === 'bridge' && PHP_SAPI === 'cgi-fcgi'
        && ($_SERVER['argc'] ?? 0) === 0 && ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET'
        && ($_SERVER['QUERY_STRING'] ?? '') === 'rp_request=1'
        && ($_SERVER['REQUEST_URI'] ?? '') === '/__rp_utf8.php?rp_request=1'
        && ($_SERVER['CONTENT_LENGTH'] ?? '') === '0'
        && !isset($_SERVER['CONTENT_TYPE']), 'utf8_closed_cgi_entry');
    $stream = fopen('/rp-utf8/context.json', 'rb');
    rp_check(is_resource($stream), 'utf8_context_open');
    try { $wire = stream_get_contents($stream, 4097); } finally { fclose($stream); }
    rp_check(is_string($wire) && strlen($wire) <= 4096, 'utf8_context_quota');
    $value = json_decode($wire, true, 8, JSON_THROW_ON_ERROR);
    rp_check(is_array($value), 'utf8_context_object');
    rp_exact_keys($value, ['schema', 'nonce'], 'utf8_context_exact_keys');
    rp_check($value['schema'] === 'recastpress.php-wasix-wpdb-utf8-context.v0.5'
        && is_string($value['nonce']) && preg_match('/\A[0-9a-f]{48}\z/D', $value['nonce']) === 1,
        'utf8_context_profile');
    rp_check(json_encode($value, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) === trim($wire, " \t\r\n"),
        'utf8_context_duplicate_or_noncanonical');
    return ['nonce' => $value['nonce']];
}

function rp_load_wordpress(array &$report): void {
    define('ABSPATH', __DIR__ . '/../../../.reference/wordpress-7.1.3/');
    define('WPINC', 'wp-includes');
    define('WP_SETUP_CONFIG', true);
    define('WP_DEBUG', false);
    define('WP_DEBUG_DISPLAY', false);
    define('DB_CHARSET', 'utf8mb4');
    define('DB_COLLATE', '');
    define('MYSQL_CLIENT_FLAGS', 0);
    require ABSPATH . WPINC . '/load.php';
    require ABSPATH . WPINC . '/plugin.php';
    require ABSPATH . WPINC . '/class-wpdb.php';
    $report['loaded_source_sha256'] = [];
    foreach (['load.php', 'plugin.php', 'class-wp-hook.php', 'class-wp-filter-sentinel.php', 'class-wpdb.php'] as $name) {
        $report['loaded_source_sha256']['wp-includes/' . $name] = hash_file('sha256', ABSPATH . WPINC . '/' . $name);
    }
}

function rp_included_sources(string $variant): array {
    $fixtures = [$variant . '.php', 'helpers.php', 'corpus.php'];
    if ($variant === 'bridge') {
        $fixtures[] = 'adapter.php';
    }
    $relative = [];
    $expected = [];
    foreach ($fixtures as $name) {
        $relative[] = 'experiments/wpdb-bridge-probe/fixtures/' . $name;
        $expected[] = realpath(__DIR__ . '/' . $name);
    }
    foreach (['load.php', 'plugin.php', 'class-wp-hook.php', 'class-wp-filter-sentinel.php', 'class-wpdb.php'] as $name) {
        $relative[] = '.reference/wordpress-7.1.3/wp-includes/' . $name;
        $expected[] = realpath(ABSPATH . WPINC . '/' . $name);
    }
    rp_check(!in_array(false, $expected, true), 'included_source_missing');
    $actual = array_map('realpath', get_included_files());
    sort($expected, SORT_STRING);
    sort($actual, SORT_STRING);
    rp_check($actual === $expected, 'included_source_set_mismatch');
    sort($relative, SORT_STRING);
    return $relative;
}

function rp_native_session_snapshot(wpdb $db): array {
    $result = mysqli_query($db->dbh, 'SELECT @@character_set_connection,@@collation_connection,@@SESSION.sql_mode');
    rp_check($result instanceof mysqli_result, 'native_session_snapshot_failed');
    try {
        $row = mysqli_fetch_row($result);
        rp_check(is_array($row) && count($row) === 3 && mysqli_fetch_row($result) === null
            && is_string($row[0]) && is_string($row[1]) && is_string($row[2]), 'native_session_snapshot_shape');
        return ['character_set_connection_hex' => bin2hex($row[0]), 'collation_connection_hex' => bin2hex($row[1]),
            'sql_mode_hex' => bin2hex($row[2])];
    } finally {
        mysqli_free_result($result);
    }
}

function rp_run_entry(string $variant): never {
    ini_set('display_errors', '0');
    ini_set('log_errors', '0');
    error_reporting(E_ALL);
    $report = ['schema' => 'recastpress.wpdb-bridge-php-observation.v0.5', 'variant' => $variant,
        'outcome' => 'not_run', 'passed' => false, 'connected' => false,
        'constructor_connection_deferred' => false, 'suppress_errors_previous' => null,
        'close_completed' => false, 'included_source_gate_passed' => false, 'observations' => [],
        'full_wordpress_bootstrap' => false, 'database_started_by_fixture' => false,
        'ddl_executed_by_fixture' => false, 'standard_wpdb_compatibility_verified' => false,
        'product_driver_selected' => false, 'product_abi_selected' => false, 'wasm_executed' => false,
        'rust_driver_executed' => false, 'native_tls_verified' => false,
        'raw_non_utf8_err_verified' => false, 'tls_profile' => 'disabled'];
    $db = null;
    $loaded = false;
    set_error_handler(static function (int $severity, string $message, string $file, int $line): never {
        throw new RpNativeFailure('php_warning');
    });
    try {
        $input = rp_entry_input($variant);
        rp_check(PHP_VERSION_ID === 80512 && PHP_INT_SIZE === 8, 'fixed_native_php_version');
        rp_check(extension_loaded('mysqli') === ($variant === 'native'), 'entry_mysqli_profile');
        $report['runtime'] = ['version' => PHP_VERSION, 'version_id' => PHP_VERSION_ID,
            'int_size' => PHP_INT_SIZE, 'sapi' => PHP_SAPI];
        rp_load_wordpress($report);
        if ($variant === 'bridge') {
            require __DIR__ . '/adapter.php';
        }
        $loaded = true;
        $report['included_source_names'] = rp_included_sources($variant);
        $report['included_source_gate_passed'] = true;
        if ($variant === 'bridge') {
            $db = new RpBridgeWpdb($input);
        } else {
            $db = new wpdb($input['user'], $input['password'], $input['database'], '127.0.0.1:' . $input['port']);
        }
        $report['constructor_connection_deferred'] = !$db->ready && $db->dbh === null;
        rp_check($report['constructor_connection_deferred'], 'constructor_connected_early');
        $report['suppress_errors_previous'] = $db->suppress_errors(true);
        rp_check($db->db_connect(false) === true && $db->ready, 'db_connect_failed');
        $report['connected'] = true;
        $report['rust_driver_executed'] = $variant === 'bridge';
        $report['server_info_hex'] = bin2hex($db->db_server_info());
        $report['capabilities'] = [];
        foreach (['collation', 'set_charset', 'utf8mb4', 'identifier_placeholders'] as $capability) {
            $report['capabilities'][$capability] = $db->has_cap($capability);
        }
        $report['session_snapshot'] = $variant === 'bridge' ? $db->rpc_session_snapshot() : rp_native_session_snapshot($db);
        $report['bootstrap_num_queries'] = $db->num_queries;
        rp_check($db->num_queries === 0, 'unexpected_bootstrap_query_count');
        rp_corpus($db, $report);
        $report['outcome'] = 'fixed_shared_wpdb_cases_passed';
        $report['passed'] = true;
    } catch (Throwable $error) {
        $report['outcome'] = 'fixed_shared_wpdb_cases_failed';
        $report['failure'] = $error instanceof RpNativeFailure ? $error->getMessage() : 'php_throwable';
        $report['exception_class'] = get_class($error);
    } finally {
        if ($db instanceof wpdb && $db->dbh !== null) {
            try {
                $report['close_completed'] = $db->close();
            } catch (Throwable $error) {
                $report['close_completed'] = false;
            }
            if (!$report['close_completed']) {
                $report['passed'] = false;
                $report['outcome'] = 'fixed_shared_wpdb_cases_failed';
                $report['cleanup_failure'] = 'db_close_failed';
            }
        }
        if ($db instanceof RpBridgeWpdb) {
            $report['rpc_stats'] = $db->rpc_stats();
        }
        if ($loaded) {
            try {
                $report['included_source_names'] = rp_included_sources($variant);
                $report['included_source_gate_passed'] = true;
            } catch (Throwable $error) {
                $report['included_source_gate_passed'] = false;
                $report['passed'] = false;
                $report['outcome'] = 'fixed_shared_wpdb_cases_failed';
                $report['failure'] ??= 'included_source_set_mismatch';
            }
        }
        restore_error_handler();
    }
    echo json_encode($report, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES), "\n";
    exit($report['passed'] ? 0 : 1);
}
