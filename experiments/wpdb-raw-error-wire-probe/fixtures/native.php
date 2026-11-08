<?php
declare(strict_types=1);

require __DIR__ . '/../../wpdb-bridge-probe/fixtures/helpers.php';

function rp_err_source_gate(): void {
    $core = ABSPATH . WPINC . '/';
    $expected = [__FILE__, __DIR__ . '/../../wpdb-bridge-probe/fixtures/helpers.php'];
    foreach (['load.php', 'plugin.php', 'class-wp-hook.php', 'class-wp-filter-sentinel.php',
        'class-wpdb.php', 'class-wp-error.php', 'functions.php', 'option.php'] as $name) {
        $expected[] = $core . $name;
    }
    $expected = array_map('realpath', $expected);
    $actual = array_map('realpath', get_included_files());
    sort($expected, SORT_STRING);
    sort($actual, SORT_STRING);
    rp_check(!in_array(false, $expected, true) && $actual === $expected, 'raw_err_native_source_set');
}

function rp_err_rows(wpdb $db): array {
    $queries = [
        'message' => 'SET @rp_err_message = CONVERT(0xC3A9 USING utf8mb4)',
        'utf8_signal' => "SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO = 31001, MESSAGE_TEXT = @rp_err_message",
        'latin1_results' => 'SET SESSION character_set_results = latin1',
        'latin1_signal' => "SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO = 31002, MESSAGE_TEXT = @rp_err_message",
        'restore_utf8_results' => 'SET SESSION character_set_results = utf8mb4',
        'next_signal' => "SIGNAL SQLSTATE '45001' SET MYSQL_ERRNO = 31003, MESSAGE_TEXT = 'rp followup'",
        'recovery' => 'SELECT 42 AS rp_recovered',
    ];
    $observations = [];
    foreach ($queries as $case => $sql) {
        $before = $db->num_queries;
        $returned = $db->query($sql);
        $state = rp_state($db, $before);
        $message = mysqli_error($db->dbh);
        $sqlstate = mysqli_sqlstate($db->dbh);
        rp_check(strlen($message) <= 512 && strlen($db->last_error) <= 512 && strlen($sqlstate) === 5,
            'raw_err_native_message_bound');
        $observations[$case] = ['query_hex' => bin2hex($sql), 'returned' => $returned, 'state' => $state,
            'mysqli_errno' => mysqli_errno($db->dbh), 'mysqli_sqlstate_hex' => bin2hex($sqlstate),
            'mysqli_error_hex' => bin2hex($message), 'last_result' => rp_value($db->last_result)];
    }
    return $observations;
}

ini_set('display_errors', '0');
ini_set('log_errors', '0');
error_reporting(E_ALL);
$db = null;
$report = ['schema' => 'recastpress.wpdb-raw-error-wire-native-php.v0.5', 'passed' => false,
    'close_completed' => false];
set_error_handler(static function (): never { throw new RpNativeFailure('php_warning'); });
try {
    $input = rp_entry_input('native');
    rp_check(PHP_VERSION_ID === 80512 && PHP_INT_SIZE === 8 && extension_loaded('mysqli'), 'raw_err_native_runtime');
    rp_load_wordpress($report);
    require ABSPATH . WPINC . '/class-wp-error.php';
    require ABSPATH . WPINC . '/functions.php';
    foreach (['class-wp-error.php', 'functions.php', 'option.php'] as $name) {
        $report['loaded_source_sha256']['wp-includes/' . $name] = hash_file('sha256', ABSPATH . WPINC . '/' . $name);
    }
    rp_err_source_gate();
    $report['runtime'] = ['version' => PHP_VERSION, 'version_id' => PHP_VERSION_ID,
        'int_size' => PHP_INT_SIZE, 'sapi' => PHP_SAPI];
    $db = new wpdb($input['user'], $input['password'], $input['database'], $input['host'] . ':' . $input['port']);
    rp_check(!$db->ready && $db->dbh === null, 'raw_err_deferred_connect');
    rp_check($db->suppress_errors(true) === false && $db->db_connect(false) === true && $db->ready
        && $db->dbh instanceof mysqli && $db->num_queries === 0, 'raw_err_native_connect');
    $report['server_info_hex'] = bin2hex($db->db_server_info());
    $report['session_snapshot'] = rp_native_session_snapshot($db);
    $charset = mysqli_query($db->dbh, 'SELECT @@character_set_results');
    rp_check($charset instanceof mysqli_result, 'raw_err_initial_results_charset');
    try {
        rp_check(mysqli_fetch_row($charset) === ['utf8mb4'] && mysqli_fetch_row($charset) === null,
            'raw_err_initial_results_utf8');
    } finally {
        mysqli_free_result($charset);
    }
    $report['initial_results_charset'] = 'utf8mb4';
    $report['observations'] = rp_err_rows($db);
    rp_err_source_gate();
    $report['included_source_gate_passed'] = true;
    $report['passed'] = true;
} catch (Throwable $error) {
    unset($report['observations']);
    $report['failure'] = $error instanceof RpNativeFailure ? $error->getMessage() : 'php_throwable';
} finally {
    if ($db instanceof wpdb && $db->dbh instanceof mysqli) {
        try {
            $report['close_completed'] = $db->close();
        } catch (Throwable $ignored) {
            $report['close_completed'] = false;
        }
    }
    $report['passed'] = $report['passed'] && $report['close_completed'];
    restore_error_handler();
}
echo json_encode($report, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES), "\n";
exit($report['passed'] ? 0 : 1);
