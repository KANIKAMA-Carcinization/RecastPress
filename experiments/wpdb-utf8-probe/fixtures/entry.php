<?php
declare(strict_types=1);

// Reuse pure observers and the exact closed CLI context reader. Neither the
// previous experiment's execution entry nor its include-set gate is called.
require __DIR__ . '/../../wpdb-bridge-probe/fixtures/helpers.php';
require __DIR__ . '/corpus.php';

/** Preserve finite PHP float arguments without JSON numeric conversion. */
function rp_crud_value(mixed $value): mixed {
    if (is_float($value)) {
        rp_check(is_finite($value), 'crud_nonfinite_argument');
        $wire = pack('e', $value);
        rp_check(strlen($wire) === 8, 'crud_float_wire_size');
        return ['type' => 'float', 'ieee754_le_hex' => bin2hex($wire)];
    }
    if (is_array($value)) {
        $entries = [];
        foreach ($value as $key => $item) {
            $entries[] = ['key' => rp_value($key), 'value' => rp_crud_value($item)];
        }
        return ['type' => 'array', 'entries' => $entries];
    }
    if ($value instanceof stdClass) {
        $properties = [];
        foreach (get_object_vars($value) as $key => $item) {
            $properties[] = ['name_hex' => bin2hex((string) $key), 'value' => rp_crud_value($item)];
        }
        return ['type' => 'stdClass', 'properties' => $properties];
    }
    return rp_value($value);
}

function rp_crud_observe(wpdb $db, array &$report, string $case, string $api, array $arguments, Closure $operation): mixed {
    $before = $db->num_queries;
    $trace_start = count($report['query_trace_hex']);
    $encoded = rp_crud_value($arguments);
    $returned = $operation();
    $state = rp_state($db, $before);
    $queries = array_slice($report['query_trace_hex'], $trace_start);
    $report['observations'][] = ['case' => $case, 'api' => $api, 'arguments' => $encoded,
        'return_type' => get_debug_type($returned), 'returned' => rp_crud_value($returned),
        'state' => $state, 'queries_hex' => $queries];
    rp_check($state['delta'] === count($queries), 'crud_actual_query_counter_vs_filter');
    rp_check($db->last_error === '', 'crud_unexpected_database_error');
    return $returned;
}

function rp_crud_wp_files(): array {
    return ['load.php', 'plugin.php', 'class-wp-hook.php', 'class-wp-filter-sentinel.php',
        'class-wpdb.php', 'class-wp-error.php', 'functions.php', 'option.php'];
}

function rp_crud_included_sources(string $variant): array {
    $rows = [
        'experiments/wpdb-utf8-probe/fixtures/' . $variant . '.php' => __DIR__ . '/' . $variant . '.php',
        'experiments/wpdb-utf8-probe/fixtures/entry.php' => __FILE__,
        'experiments/wpdb-utf8-probe/fixtures/corpus.php' => __DIR__ . '/corpus.php',
        'experiments/wpdb-bridge-probe/fixtures/helpers.php' => __DIR__ . '/../../wpdb-bridge-probe/fixtures/helpers.php',
    ];
    if ($variant === 'bridge') {
        $rows['experiments/wpdb-bridge-probe/fixtures/adapter.php'] = __DIR__ . '/../../wpdb-bridge-probe/fixtures/adapter.php';
    }
    foreach (rp_crud_wp_files() as $name) {
        $rows['.reference/wordpress-7.1.3/wp-includes/' . $name] = ABSPATH . WPINC . '/' . $name;
    }
    $expected = array_map('realpath', array_values($rows));
    $actual = array_map('realpath', get_included_files());
    rp_check(!in_array(false, $expected, true), 'crud_include_missing');
    sort($expected, SORT_STRING);
    sort($actual, SORT_STRING);
    rp_check($actual === $expected, 'crud_include_set_mismatch');
    $names = array_keys($rows);
    sort($names, SORT_STRING);
    return $names;
}

function rp_crud_run(string $variant): never {
    ini_set('display_errors', '0');
    ini_set('log_errors', '0');
    error_reporting(E_ALL);
    $report = ['schema' => 'recastpress.wpdb-utf8-php-observation.v0.5', 'variant' => $variant,
        'outcome' => 'not_run', 'passed' => false, 'connected' => false,
        'constructor_connection_deferred' => false, 'suppress_errors_previous' => null,
        'close_completed' => false, 'included_source_gate_passed' => false,
        'observations' => [], 'query_trace_hex' => [], 'full_wordpress_bootstrap' => false,
        'database_started_by_fixture' => false, 'ddl_executed_by_fixture' => false,
        'standard_wpdb_compatibility_verified' => false, 'product_driver_selected' => false,
        'product_abi_selected' => false, 'wasm_executed' => false, 'rust_driver_executed' => false,
        'native_tls_verified' => false, 'raw_non_utf8_err_verified' => false,
        'invalid_utf8_or_overlong_inputs_exercised' => false, 'tls_profile' => 'disabled'];
    $db = null;
    $loaded = false;
    $filter = null;
    set_error_handler(static function (int $severity, string $message, string $file, int $line): never {
        throw new RpNativeFailure('php_warning');
    });
    try {
        rp_check($variant === 'native', 'utf8_native_only');
        $input = rp_entry_input($variant);
        rp_check(PHP_VERSION_ID === 80512 && PHP_INT_SIZE === 8, 'fixed_native_php_version');
        $report['mbstring_loaded'] = extension_loaded('mbstring');
        rp_check($report['mbstring_loaded'], 'utf8_mbstring_profile');
        $report['native_driver_extension_present'] = extension_loaded('mysqli');
        rp_check($report['native_driver_extension_present'] === ($variant === 'native'), 'crud_native_extension_profile');
        $report['runtime'] = ['version' => PHP_VERSION, 'version_id' => PHP_VERSION_ID,
            'int_size' => PHP_INT_SIZE, 'sapi' => PHP_SAPI];
        rp_load_wordpress($report);
        require ABSPATH . WPINC . '/class-wp-error.php';
        require ABSPATH . WPINC . '/functions.php'; // Includes original option.php.
        foreach (rp_crud_wp_files() as $name) {
            $report['loaded_source_sha256']['wp-includes/' . $name] = hash_file('sha256', ABSPATH . WPINC . '/' . $name);
        }
        if ($variant === 'bridge') {
            require __DIR__ . '/../../wpdb-bridge-probe/fixtures/adapter.php';
        }
        $loaded = true;
        $report['included_source_names'] = rp_crud_included_sources($variant);
        $report['included_source_gate_passed'] = true;
        $db = $variant === 'bridge' ? new RpBridgeWpdb($input)
            : new wpdb($input['user'], $input['password'], $input['database'], '127.0.0.1:' . $input['port']);
        $report['constructor_connection_deferred'] = !$db->ready && $db->dbh === null;
        rp_check($report['constructor_connection_deferred'], 'crud_constructor_connected_early');
        $report['suppress_errors_previous'] = $db->suppress_errors(true);
        rp_check($db->db_connect(false) === true && $db->ready, 'crud_connect_failed');
        $report['connected'] = true;
        $report['native_mysqli_handle_supplied'] = $db->dbh instanceof mysqli;
        rp_check($report['native_mysqli_handle_supplied'] === ($variant === 'native'), 'crud_driver_handle_profile');
        $report['rust_driver_executed'] = $variant === 'bridge';
        $report['server_info_hex'] = bin2hex($db->db_server_info());
        $report['session_snapshot'] = $variant === 'bridge' ? $db->rpc_session_snapshot() : rp_native_session_snapshot($db);
        $report['bootstrap_num_queries'] = $db->num_queries;
        rp_check($db->num_queries === 0, 'crud_bootstrap_query_count');
        // Priority zero removes upstream placeholder tokens. Record the exact
        // final query bytes without replacing, rejecting or modifying SQL.
        $filter = static function (string $query) use (&$report): string {
            rp_check(count($report['query_trace_hex']) < 64 && strlen($query) <= 4096, 'crud_query_trace_quota');
            $report['query_trace_hex'][] = bin2hex($query);
            return $query;
        };
        add_filter('query', $filter, PHP_INT_MAX, 1);
        rp_crud_corpus($db, $report);
        $report['final_num_queries'] = $db->num_queries;
        rp_check($db->num_queries === count($report['query_trace_hex']), 'crud_whole_query_counter_vs_filter');
        $report['outcome'] = 'fixed_utf8_cases_passed';
        $report['passed'] = true;
    } catch (Throwable $error) {
        $report['outcome'] = 'fixed_utf8_cases_failed';
        $report['failure'] = $error instanceof RpNativeFailure ? $error->getMessage() : 'php_throwable';
        $report['exception_class'] = get_class($error);
    } finally {
        if ($filter !== null) {
            remove_filter('query', $filter, PHP_INT_MAX);
        }
        if ($db instanceof wpdb && $db->dbh !== null) {
            try {
                $report['close_completed'] = $db->close();
            } catch (Throwable $error) {
                $report['close_completed'] = false;
            }
            if (!$report['close_completed']) {
                $report['passed'] = false;
                $report['outcome'] = 'fixed_utf8_cases_failed';
                $report['cleanup_failure'] = 'crud_close_failed';
            }
        }
        if ($db instanceof RpBridgeWpdb) {
            $report['rpc_stats'] = $db->rpc_stats();
        }
        if ($loaded) {
            try {
                $report['included_source_names'] = rp_crud_included_sources($variant);
                $report['included_source_gate_passed'] = true;
            } catch (Throwable $error) {
                $report['passed'] = false;
                $report['included_source_gate_passed'] = false;
                $report['outcome'] = 'fixed_utf8_cases_failed';
                $report['failure'] ??= 'crud_include_set_mismatch';
            }
        }
        restore_error_handler();
    }
    echo json_encode($report, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES), "\n";
    exit($report['passed'] ? 0 : 1);
}
