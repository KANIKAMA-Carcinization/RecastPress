<?php
declare(strict_types=1);
require __DIR__ . '/helpers.php';
const RP_PUBLIC_PHASES = ["input","source_inventory","wp_settings","wp_install","wp_dispatch","template_loader","public_observations","source_after","complete"];
const RP_PUBLIC_FAILURE_CODES = ["anonymous_home_required","autoload_stats","cgi_entry_guard","closed_native_entry","complete_bounded_home_html_required","complete_home_dispatch_required","context_duplicate_keys","context_exact_keys","context_nonce_or_salts","context_object","context_open","context_profile","context_read","context_salt_keys","context_salt_shape","context_size","core_source_changed_during_bootstrap","fixed_guest_site","full_rpc_bootstrap_required","html_capture_buffer_lost","input_string_token","installer_not_complete","installer_return","local","native_disconnect_failed","native_outbound_guard_configuration","native_php_profile","owned_dropin_bytes","owned_dropin_context","owned_dropin_nonce","php_throwable","php_warning","public_observation_contract","public_response_quota","public_secret_guard","rb","rp_unused_guest_db","rp_unused_guest_user","rpc_actual_session_charset_mismatch","rpc_channel_closed_or_budget","rpc_channel_open_failed","rpc_close_reply_keys","rpc_close_reply_not_eof","rpc_close_without_channel","rpc_column_integer","rpc_column_keys","rpc_column_object","rpc_column_offset_out_of_range","rpc_connect_reuse_refused","rpc_escape_failed","rpc_escape_out_of_fixed_scope","rpc_escape_reply_keys","rpc_flush_failed","rpc_hello_contract","rpc_hello_failed","rpc_hello_keys","rpc_hex_decode_failed","rpc_host_close_failed","rpc_host_close_not_completed","rpc_integer_out_of_native_range","rpc_internal_query_failed","rpc_invalid_hex","rpc_invalid_json","rpc_invalid_unsigned_decimal","rpc_mysqli_column_metadata_out_of_fixed_scope","rpc_query_collection_bounds","rpc_query_data_keys","rpc_query_string_required","rpc_query_u16","rpc_reconnect_not_supported","rpc_reply_exact_keys","rpc_reply_identity","rpc_reply_incomplete_or_quota","rpc_reply_object_required","rpc_reply_total_quota","rpc_request_frame_or_total_quota","rpc_request_shape","rpc_result_raw_byte_quota","rpc_rewind_before_read_failed","rpc_rewind_before_write_failed","rpc_row_width","rpc_server_error_keys","rpc_server_error_out_of_fixed_scope","rpc_server_info_before_connect","rpc_session_snapshot_shape","rpc_sql_mode_out_of_fixed_scope","rpc_sql_mode_shape","rpc_write_failed","single_site_table_set","source_complete_tree","source_extra_or_special_file","source_file_bytes_changed","source_link","source_manifest_fixed_inventory","source_manifest_profile","source_manifest_size","source_noncanonical_path","unclassified_failure","unexpected_php_shutdown","utf8mb4","utf8mb4_unicode_520_ci"];
const RP_PUBLIC_FAILURE_CLASSES = ["RpBootstrapFailure","RpNativeFailure","JsonException","Error","TypeError","ValueError","ArgumentCountError","RuntimeException","Exception","ParseError","ErrorException","unclassified_exception"];
const RP_DROPIN_SHA = '5e48035bdc93b159237e9742eb90b296748e5a20f96b83c2ef2e1148f225df15';

final class RpBootstrapFailure extends RuntimeException {}
function rp_boot_check(bool $value, string $code): void {
    if (!$value) { throw new RpBootstrapFailure($code); }
}
function rp_boot_exact(array $value, array $names, string $code): void {
    $keys = array_keys($value); sort($keys); sort($names);
    rp_boot_check($keys === $names, $code);
}
function rp_boot_secret(mixed $value): bool {
    return is_string($value) && preg_match('/\A[0-9a-f]{48}\z/D', $value) === 1;
}
function rp_boot_json_keys(string $wire): array {
    // The input has only a root object and a salts object, with disjoint fixed
    // keys. Count their string tokens to reject duplicate keys after decoding.
    $keys = []; $length = strlen($wire);
    for ($i = 0; $i < $length; ++$i) {
        if ($wire[$i] !== '"') { continue; }
        $start = $i++;
        while ($i < $length) {
            if ($wire[$i] === '\\') { $i += 2; continue; }
            if ($wire[$i] === '"') { break; }
            ++$i;
        }
        rp_boot_check($i < $length, 'input_string_token');
        $end = $i + 1;
        while ($end < $length && str_contains(" \t\r\n", $wire[$end])) { ++$end; }
        if ($end < $length && $wire[$end] === ':') {
            $keys[] = json_decode(substr($wire, $start, $i - $start + 1), true, 4, JSON_THROW_ON_ERROR);
        }
    }
    sort($keys); return $keys;
}
function rp_boot_input(string $mode): array {
    rp_boot_check(PHP_SAPI === 'cgi-fcgi' && ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET'
        && ($_SERVER['QUERY_STRING'] ?? '') === 'rp_request=1'
        && ($_SERVER['REQUEST_URI'] ?? '') === '/__rp_request.php?rp_request=1'
        && ($_SERVER['argc'] ?? 0) === 0 && $mode === 'home', 'cgi_entry_guard');
    $stream = fopen('/rp-request/context.json', 'rb');
    rp_boot_check(is_resource($stream), 'context_open');
    try { $wire = stream_get_contents($stream, 4097); } finally { fclose($stream); }
    rp_boot_check(is_string($wire) && strlen($wire) <= 4096, 'context_read');
    return rp_boot_context($wire, $mode);
}

function rp_boot_inventory(string $site, string $manifest_path): array {
    $wire = file_get_contents($manifest_path);
    rp_boot_check(is_string($wire) && strlen($wire) <= 2097152, 'source_manifest_size');
    $manifest = json_decode($wire, true, 16, JSON_THROW_ON_ERROR);
    rp_boot_check(is_array($manifest)
        && $manifest['schema'] === 'recastpress.wp-bootstrap-source-manifest.v0.5'
        && $manifest['wordpress_version'] === '7.1.3'
        && $manifest['archive']['bytes'] === 35368461
        && $manifest['archive']['sha256'] === 'd2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5'
        && count($manifest['files']) === 3782 && count($manifest['directories']) === 467,
        'source_manifest_profile');
    $files = $manifest['files']; ksort($files, SORT_STRING);
    $directories = $manifest['directories']; sort($directories, SORT_STRING);
    rp_boot_check(hash('sha256', json_encode($files, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES))
        === 'afacbaee85821a793af5964b0b797d2aa2d0e6b27bf8cb76763dc3c0cb5d911c'
        && hash('sha256', json_encode($directories, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES))
        === '7d8e0d997ccd9c7e565c8804a2d8c00a5502eec10c578338fbbd347dc37e57a5', 'source_manifest_fixed_inventory');
    $actual_files = []; $actual_dirs = []; $total = 0; $overlay_seen = false;
    $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($site, FilesystemIterator::SKIP_DOTS),
        RecursiveIteratorIterator::SELF_FIRST);
    foreach ($iterator as $entry) {
        rp_boot_check(!$entry->isLink(), 'source_link');
        $path = $entry->getPathname();
        rp_boot_check(realpath($path) === $path, 'source_noncanonical_path');
        $relative = str_replace('\\', '/', substr($path, strlen($site) + 1));
        if ($entry->isDir()) { $actual_dirs[] = $relative; continue; }
        if ($relative === 'wp-content/db.php') {
            rp_boot_check(!$overlay_seen && $entry->isFile() && hash_file('sha256', $path) === RP_DROPIN_SHA, 'owned_dropin_bytes');
            $overlay_seen = true; continue;
        }
        rp_boot_check($entry->isFile() && isset($files[$relative]) && $entry->getSize() <= 16777216,
            'source_extra_or_special_file');
        $row = $files[$relative];
        rp_boot_check($entry->getSize() === $row['bytes'] && hash_file('sha256', $path) === $row['sha256'],
            'source_file_bytes_changed');
        $actual_files[] = $relative; $total += $entry->getSize();
    }
    sort($actual_files, SORT_STRING); sort($actual_dirs, SORT_STRING);
    rp_boot_check($actual_files === array_keys($files) && $actual_dirs === $directories
        && $overlay_seen && $total === 110077999 && !file_exists($site . '/wp-config.php'), 'source_complete_tree');
    return ['files' => count($actual_files), 'directories' => count($actual_dirs), 'bytes' => $total,
        'file_inventory_sha256' => $manifest['file_inventory_sha256'], 'manifest_sha256' => hash('sha256', $wire)];
}

$rp_mode = defined('RP_BOOTSTRAP_MODE') ? RP_BOOTSTRAP_MODE : null;
$rp_report = ['schema' => 'recastpress.php-wasix-wp-request-php-observation.v0.5', 'mode' => $rp_mode,
    'passed' => false, 'outcome' => 'fixed_wasix_home_failed', 'phase' => 'input',
    'bootstrap_completed' => false, 'install_completed' => false, 'physical_disconnect_completed' => false,
    'source_inventory_verified' => false, 'core_files_unchanged' => false,
    'request_dispatch_completed' => false, 'template_loader_completed' => false,
    'anonymous_request' => false, 'theme_rendered' => false, 'response' => null,
    'http_listener_exercised' => false, 'html_normalized' => false,
    'blocked_mail_calls' => 0, 'blocked_http_calls' => 0, 'warning_count' => 0,
    'rust_executed' => true, 'wasm_executed' => true, 'product_runtime_selected' => false,
    'native_php_product_fallback' => false, 'site_config_file_created' => false,
    'source_core_theme_plugin_modified' => false, 'outbound_mail_or_http_sent_by_fixture' => false];
$rp_secrets = []; $rp_emitted = false; $rp_buffer = ob_get_level();
$rp_html = ''; $rp_html_seen = 0; $rp_html_overflow = false;
// Outer safety buffer stays empty: the chunk callback never releases HTML.
ob_start();
ob_start(static function (string $bytes): string {
    global $rp_html, $rp_html_seen, $rp_html_overflow;
    $rp_html_seen += strlen($bytes);
    if (!$rp_html_overflow && strlen($bytes) <= 1048576 - strlen($rp_html)) {
        $rp_html .= $bytes;
    } else { $rp_html_overflow = true; }
    return '';
}, 4096);
function rp_boot_emit(): void {
    global $rp_report, $rp_secrets, $rp_emitted, $rp_buffer;
    while (ob_get_level() > $rp_buffer) { ob_end_clean(); }
    $wire = json_encode($rp_report, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES);
    foreach ($rp_secrets as $secret) {
        if (str_contains($wire, $secret) || str_contains($wire, bin2hex($secret))) {
            $wire = '{"schema":"recastpress.php-wasix-wp-request-php-observation.v0.5","passed":false,"outcome":"fixed_wasix_home_failed","failure":"public_secret_guard"}';
            $rp_report['passed'] = false; break;
        }
    }
    if (strlen($wire) > 3145728) {
        $wire = '{"schema":"recastpress.php-wasix-wp-request-php-observation.v0.5","passed":false,"failure":"public_response_quota"}';
        $rp_report['passed'] = false;
    }
    header_remove(); header('Content-Type: application/json', true, 200);
    $rp_emitted = true; echo $wire, "\n";
}
register_shutdown_function(static function (): void {
    global $rp_emitted, $rp_report;
    if (!$rp_emitted) {
        $rp_report['passed'] = false; $rp_report['outcome'] = 'fixed_wasix_home_failed';
        $rp_report['failure'] = 'unexpected_php_shutdown'; rp_boot_emit();
    }
});
set_error_handler(static function (int $severity): bool {
    if (!(error_reporting() & $severity)) { return false; }
    ++$GLOBALS['rp_report']['warning_count']; throw new RpBootstrapFailure('php_warning');
});
try {
    rp_boot_check(PHP_SAPI === 'cgi-fcgi' && ($_SERVER['argc'] ?? 0) === 0 && $rp_mode === 'home'
        && ($_SERVER['REQUEST_METHOD'] ?? '') === 'GET'
        && ($_SERVER['REQUEST_URI'] ?? '') === '/__rp_request.php?rp_request=1'
        && ($_SERVER['QUERY_STRING'] ?? '') === 'rp_request=1', 'closed_native_entry');
    $rp_input = rp_boot_input($rp_mode);
    $rp_secrets = array_merge([$rp_input['nonce']], array_values($rp_input['salts']));
    if ($rp_mode === 'install') { $rp_secrets[] = $rp_input['admin_password']; }
    rp_boot_check(PHP_VERSION_ID === 80512 && PHP_INT_SIZE === 8 && extension_loaded('mysqli'), 'native_php_profile');
    rp_boot_check(!function_exists('mail') && ini_get('allow_url_fopen') === '0', 'native_outbound_guard_configuration');
    $rp_site = '/wordpress';
    rp_boot_check(realpath($rp_site) === $rp_site, 'fixed_guest_site');
    $rp_manifest = '/rp-request/source-manifest.json';
    $rp_report['phase'] = 'source_inventory';
    $rp_report['source_before'] = rp_boot_inventory($rp_site, $rp_manifest);
    $rp_report['source_inventory_verified'] = true;
    define('ABSPATH', $rp_site . DIRECTORY_SEPARATOR);
    define('DB_NAME', 'rp_unused_guest_db'); define('DB_USER', 'rp_unused_guest_user'); define('DB_PASSWORD', '');
    define('DB_HOST', 'rp-unused.invalid'); define('DB_CHARSET', 'utf8mb4'); define('DB_COLLATE', 'utf8mb4_unicode_520_ci');
    foreach ($rp_input['salts'] as $rp_name => $rp_value) { define($rp_name, $rp_value); }
    define('WP_HOME', 'http://rp-fixture.invalid'); define('WP_SITEURL', 'http://rp-fixture.invalid');
    define('WP_INSTALLING', $rp_mode === 'install'); define('WP_DEBUG', false); define('WP_DEBUG_DISPLAY', false);
    define('WP_DEBUG_LOG', false); define('WP_DISABLE_FATAL_ERROR_HANDLER', true); define('WP_CACHE', false);
    define('WP_ENVIRONMENT_TYPE', 'local'); define('DISABLE_WP_CRON', true); define('WP_HTTP_BLOCK_EXTERNAL', true);
    define('AUTOMATIC_UPDATER_DISABLED', true); define('WP_AUTO_UPDATE_CORE', false);
    define('DISALLOW_FILE_MODS', true); define('DISALLOW_FILE_EDIT', true);
    define('WP_USE_THEMES', true);
    $_GET = []; $_POST = []; $_COOKIE = [];
    $table_prefix = 'rp_';
    $_SERVER['HTTP_HOST'] = 'rp-fixture.invalid'; $_SERVER['SERVER_NAME'] = 'rp-fixture.invalid';
    $_SERVER['SERVER_PORT'] = '80'; $_SERVER['SERVER_PROTOCOL'] = 'HTTP/1.1'; $_SERVER['REQUEST_METHOD'] = 'GET';
    $_SERVER['SCRIPT_NAME'] = $rp_mode === 'install' ? '/wp-admin/install.php' : '/index.php';
    $_SERVER['PHP_SELF'] = $_SERVER['SCRIPT_NAME']; $_SERVER['REQUEST_URI'] = '/';
    $_SERVER['QUERY_STRING'] = ''; $_SERVER['PATH_INFO'] = '';
    $_SERVER['HTTPS'] = 'off'; $_SERVER['REQUEST_SCHEME'] = 'http';
    unset($_SERVER['PHP_AUTH_USER'], $_SERVER['PHP_AUTH_PW'], $_SERVER['HTTP_AUTHORIZATION']);
    $_SERVER['SCRIPT_FILENAME'] = ABSPATH . ltrim($_SERVER['SCRIPT_NAME'], '/');
    // No WPINC definition here: unchanged wp-settings.php defines it itself.
    require_once ABSPATH . 'wp-includes/plugin.php';
    add_filter('pre_wp_mail', static function (): bool { ++$GLOBALS['rp_report']['blocked_mail_calls']; return false; }, PHP_INT_MAX);
    add_filter('pre_http_request', static function () { ++$GLOBALS['rp_report']['blocked_http_calls']; return new WP_Error('rp_fixture_external_http_blocked', 'Fixture HTTP disabled.'); }, PHP_INT_MAX);
    add_filter('flush_rewrite_rules_hard', static fn(): bool => false, PHP_INT_MAX);
    $rp_report['phase'] = 'wp_settings';
    require ABSPATH . 'wp-settings.php';
    $rp_report['wp_version'] = $wp_version;
    $rp_report['shortinit_false'] = defined('SHORTINIT') && SHORTINIT === false;
    $rp_report['wp_installing'] = WP_INSTALLING;
    $rp_report['native_mysqli_handle'] = $wpdb->dbh instanceof mysqli;
    $rp_report['bootstrap_completed'] = did_action('init') >= 1 && did_action('wp_loaded') >= 1
        && class_exists('WP_Query', false) && class_exists('WP_User', false) && class_exists('WP_Rewrite', false);
    rp_boot_check($wp_version === '7.1.3' && $rp_report['bootstrap_completed'] && $rp_report['shortinit_false']
        && !$rp_report['native_mysqli_handle'] && $wpdb instanceof RpBridgeWpdb
        && ($rp_report['owned_db_dropin_loaded'] ?? false) === true, 'full_rpc_bootstrap_required');
    $wpdb->suppress_errors(true);
    if ($rp_mode === 'install') {
        $rp_report['phase'] = 'wp_install';
        require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        $rp_installed = wp_install('RP Bootstrap Fixture', 'rp_fixture_admin', 'email@example.invalid', 1, '', $rp_input['admin_password']);
        rp_boot_check(is_array($rp_installed) && isset($rp_installed['user_id']) && is_int($rp_installed['user_id'])
            && $rp_installed['user_id'] > 0, 'installer_return');
        unset($rp_installed);
        $rp_report['install_completed'] = is_blog_installed();
        rp_boot_check($rp_report['install_completed'], 'installer_not_complete');
    }
    $rp_report['phase'] = 'wp_dispatch';
    rp_boot_check(!is_user_logged_in(), 'anonymous_home_required');
    $rp_report['anonymous_request'] = true;
    wp();
    $rp_report['request_dispatch_completed'] = did_action('wp') >= 1;
    $rp_report['phase'] = 'template_loader';
    require ABSPATH . WPINC . '/template-loader.php';
    $rp_report['template_loader_completed'] = true;
    rp_boot_check(ob_get_level() === $rp_buffer + 2, 'html_capture_buffer_lost');
    ob_flush();
    rp_boot_check(!$rp_html_overflow && strlen($rp_html) === $rp_html_seen
        && $rp_html_seen > 0 && $rp_html_seen <= 1048576, 'complete_bounded_home_html_required');
    $rp_status = http_response_code();
    $rp_headers = headers_list();
    rp_boot_check(is_int($rp_status) && $rp_status === 200 && !headers_sent()
        && $rp_report['request_dispatch_completed'] && did_action('template_redirect') >= 1
        && did_action('wp_head') >= 1 && did_action('wp_footer') >= 1, 'complete_home_dispatch_required');
    $rp_report['response'] = ['status' => $rp_status, 'headers_hex' => array_map('bin2hex', $rp_headers),
        'body_bytes' => strlen($rp_html), 'body_hex' => bin2hex($rp_html), 'body_sha256' => hash('sha256', $rp_html)];
    $rp_report['theme_rendered'] = true;
    $rp_report['phase'] = 'public_observations';
    $rp_report['public_options'] = ['blogname' => get_option('blogname'), 'siteurl' => get_option('siteurl'), 'home' => get_option('home')];
    $rp_report['public_posts'] = [];
    foreach (get_posts(['post_type' => ['post', 'page'], 'post_status' => 'publish', 'numberposts' => 10, 'orderby' => 'ID', 'order' => 'ASC']) as $rp_post) {
        $rp_report['public_posts'][] = ['ID' => $rp_post->ID, 'type' => $rp_post->post_type, 'status' => $rp_post->post_status,
            'title' => $rp_post->post_title, 'slug' => $rp_post->post_name];
    }
    $rp_tables = array_values($wpdb->tables('all', true)); sort($rp_tables, SORT_STRING);
    rp_boot_check(count($rp_tables) === 12, 'single_site_table_set');
    $rp_present = $wpdb->get_col('SHOW TABLES');
    $rp_report['known_wp_table_count'] = count(array_intersect($rp_tables, $rp_present));
    $rp_autoload = wp_autoload_values_to_autoload();
    $rp_sql = 'SELECT COUNT(*) AS option_count, COALESCE(MAX(OCTET_LENGTH(option_value)),0) AS max_value_bytes FROM ' . $wpdb->options
        . ' WHERE autoload IN (' . implode(',', array_fill(0, count($rp_autoload), '%s')) . ')';
    $rp_stats = $wpdb->get_row($wpdb->prepare($rp_sql, $rp_autoload), ARRAY_A);
    rp_boot_check(is_array($rp_stats) && is_string($rp_stats['option_count']) && is_string($rp_stats['max_value_bytes'])
        && preg_match('/\A[0-9]+\z/D', $rp_stats['option_count']) === 1 && preg_match('/\A[0-9]+\z/D', $rp_stats['max_value_bytes']) === 1, 'autoload_stats');
    $rp_report['autoload_option_count'] = (int) $rp_stats['option_count'];
    $rp_report['autoload_max_value_bytes'] = (int) $rp_stats['max_value_bytes'];
    rp_boot_check($rp_report['known_wp_table_count'] === 12 && count($rp_report['public_posts']) >= 2
        && $rp_report['public_options'] === ['blogname' => 'RP Bootstrap Fixture', 'siteurl' => 'http://rp-fixture.invalid', 'home' => 'http://rp-fixture.invalid']
        && $wpdb->last_error === '', 'public_observation_contract');
    $rp_report['phase'] = 'source_after';
    $rp_report['source_after'] = rp_boot_inventory($rp_site, $rp_manifest);
    rp_boot_check($rp_report['source_before'] === $rp_report['source_after'], 'core_source_changed_during_bootstrap');
    $rp_report['core_files_unchanged'] = true;
    $rp_report['physical_disconnect_completed'] = $wpdb->close();
    rp_boot_check($rp_report['physical_disconnect_completed'], 'native_disconnect_failed');
    $rp_report['passed'] = true; $rp_report['outcome'] = 'fixed_wasix_home_passed'; $rp_report['phase'] = 'complete';
} catch (Throwable $rp_error) {
    $rp_report['response'] = null;
    $rp_failure = ($rp_error instanceof RpBootstrapFailure || $rp_error instanceof RpNativeFailure) ? $rp_error->getMessage() : 'php_throwable';
    $rp_report['failure'] = in_array($rp_failure, RP_PUBLIC_FAILURE_CODES, true) ? $rp_failure : 'unclassified_failure';
    $rp_class = get_class($rp_error);
    $rp_report['failure_class'] = in_array($rp_class, RP_PUBLIC_FAILURE_CLASSES, true) ? $rp_class : 'unclassified_exception';
} finally {
    if (isset($wpdb) && $wpdb instanceof RpBridgeWpdb && $wpdb->dbh !== null) {
        try { $rp_report['physical_disconnect_completed'] = $wpdb->close(); } catch (Throwable $rp_cleanup) { $rp_report['physical_disconnect_completed'] = false; }
    }
    if (isset($wpdb) && $wpdb instanceof RpBridgeWpdb) { $rp_report['rpc_stats'] = $wpdb->rpc_stats(); }
    restore_error_handler(); rp_boot_emit();
}
exit($rp_report['passed'] ? 0 : 1);
