<?php
declare(strict_types=1);

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
    $wire = stream_get_contents(STDIN, 16385);
    rp_boot_check(is_string($wire) && strlen($wire) <= 16384, 'input_size');
    $value = json_decode($wire, true, 16, JSON_THROW_ON_ERROR);
    rp_boot_check(is_array($value), 'input_object');
    $keys = ['schema', 'mode', 'host', 'port', 'database', 'user', 'password', 'salts'];
    if ($mode === 'install') { $keys[] = 'admin_password'; }
    rp_boot_exact($value, $keys, 'input_exact_keys');
    rp_boot_check($value['schema'] === 'recastpress.wp-bootstrap-native-input.v0.5'
        && $value['mode'] === $mode && $value['host'] === '127.0.0.1'
        && is_int($value['port']) && $value['port'] >= 1 && $value['port'] <= 65535
        && $value['database'] === 'rp_wpdb_probe'
        && $value['user'] === ($mode === 'install' ? 'rp_wp_setup' : 'rp_wpdb_probe'), 'input_closed_destination');
    rp_boot_check(rp_boot_secret($value['password']) && is_array($value['salts']), 'input_secret_shape');
    $salt_keys = ['AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY',
        'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT'];
    rp_boot_exact($value['salts'], $salt_keys, 'input_salt_keys');
    foreach ($value['salts'] as $salt) { rp_boot_check(rp_boot_secret($salt), 'input_salt_shape'); }
    if ($mode === 'install') { rp_boot_check(rp_boot_secret($value['admin_password']), 'input_admin_password'); }
    $all_keys = array_merge($keys, $salt_keys); sort($all_keys);
    rp_boot_check(rp_boot_json_keys($wire) === $all_keys, 'input_duplicate_keys');
    return $value;
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
    $actual_files = []; $actual_dirs = []; $total = 0;
    $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($site, FilesystemIterator::SKIP_DOTS),
        RecursiveIteratorIterator::SELF_FIRST);
    foreach ($iterator as $entry) {
        rp_boot_check(!$entry->isLink(), 'source_link');
        $path = $entry->getPathname();
        rp_boot_check(realpath($path) === $path, 'source_noncanonical_path');
        $relative = str_replace('\\', '/', substr($path, strlen($site) + 1));
        if ($entry->isDir()) { $actual_dirs[] = $relative; continue; }
        rp_boot_check($entry->isFile() && isset($files[$relative]) && $entry->getSize() <= 16777216,
            'source_extra_or_special_file');
        $row = $files[$relative];
        rp_boot_check($entry->getSize() === $row['bytes'] && hash_file('sha256', $path) === $row['sha256'],
            'source_file_bytes_changed');
        $actual_files[] = $relative; $total += $entry->getSize();
    }
    sort($actual_files, SORT_STRING); sort($actual_dirs, SORT_STRING);
    rp_boot_check($actual_files === array_keys($files) && $actual_dirs === $directories
        && $total === 110077999 && !file_exists($site . '/wp-config.php'), 'source_complete_tree');
    return ['files' => count($actual_files), 'directories' => count($actual_dirs), 'bytes' => $total,
        'file_inventory_sha256' => $manifest['file_inventory_sha256'], 'manifest_sha256' => hash('sha256', $wire)];
}

$rp_mode = defined('RP_BOOTSTRAP_MODE') ? RP_BOOTSTRAP_MODE : null;
$rp_report = ['schema' => 'recastpress.wp-bootstrap-native-observation.v0.5', 'mode' => $rp_mode,
    'passed' => false, 'outcome' => 'fixed_native_bootstrap_failed', 'phase' => 'input',
    'bootstrap_completed' => false, 'install_completed' => false, 'physical_disconnect_completed' => false,
    'source_inventory_verified' => false, 'core_files_unchanged' => false,
    'blocked_mail_calls' => 0, 'blocked_http_calls' => 0, 'warning_count' => 0,
    'rust_executed' => false, 'wasm_executed' => false, 'product_runtime_selected' => false,
    'native_php_product_fallback' => false, 'site_config_file_created' => false,
    'source_core_theme_plugin_modified' => false, 'outbound_mail_or_http_sent_by_fixture' => false];
$rp_secrets = []; $rp_emitted = false; $rp_buffer = ob_get_level(); ob_start();
function rp_boot_emit(): void {
    global $rp_report, $rp_secrets, $rp_emitted, $rp_buffer;
    while (ob_get_level() > $rp_buffer) { ob_end_clean(); }
    $wire = json_encode($rp_report, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES);
    foreach ($rp_secrets as $secret) {
        if (str_contains($wire, $secret) || str_contains($wire, bin2hex($secret))) {
            $wire = '{"schema":"recastpress.wp-bootstrap-native-observation.v0.5","passed":false,"outcome":"fixed_native_bootstrap_failed","failure":"public_secret_guard"}';
            $rp_report['passed'] = false; break;
        }
    }
    $rp_emitted = true; echo $wire, "\n";
}
register_shutdown_function(static function (): void {
    global $rp_emitted, $rp_report;
    if (!$rp_emitted) {
        $rp_report['passed'] = false; $rp_report['outcome'] = 'fixed_native_bootstrap_failed';
        $rp_report['failure'] = 'unexpected_php_shutdown'; rp_boot_emit();
    }
});
set_error_handler(static function (int $severity): bool {
    if (!(error_reporting() & $severity)) { return false; }
    ++$GLOBALS['rp_report']['warning_count']; throw new RpBootstrapFailure('php_warning');
});
try {
    rp_boot_check(PHP_SAPI === 'cli' && ($argc ?? 0) === 1 && in_array($rp_mode, ['install', 'read'], true), 'closed_native_entry');
    $rp_input = rp_boot_input($rp_mode);
    $rp_secrets = array_merge([$rp_input['password']], array_values($rp_input['salts']));
    if ($rp_mode === 'install') { $rp_secrets[] = $rp_input['admin_password']; }
    rp_boot_check(PHP_VERSION_ID === 80512 && PHP_INT_SIZE === 8 && extension_loaded('mysqli'), 'native_php_profile');
    rp_boot_check(!function_exists('mail') && ini_get('allow_url_fopen') === '0', 'native_outbound_guard_configuration');
    $rp_root = realpath(__DIR__ . '/../../..');
    rp_boot_check(is_string($rp_root), 'fixed_root');
    $rp_site = $rp_root . DIRECTORY_SEPARATOR . 'target' . DIRECTORY_SEPARATOR . 'wp-bootstrap-probe' . DIRECTORY_SEPARATOR . 'site';
    rp_boot_check(realpath($rp_site) === $rp_site, 'fixed_site');
    $rp_manifest = dirname($rp_site) . DIRECTORY_SEPARATOR . 'source-manifest.json';
    $rp_report['phase'] = 'source_inventory';
    $rp_report['source_before'] = rp_boot_inventory($rp_site, $rp_manifest);
    $rp_report['source_inventory_verified'] = true;
    define('ABSPATH', $rp_site . DIRECTORY_SEPARATOR);
    define('DB_NAME', $rp_input['database']); define('DB_USER', $rp_input['user']); define('DB_PASSWORD', $rp_input['password']);
    define('DB_HOST', '127.0.0.1:' . $rp_input['port']); define('DB_CHARSET', 'utf8mb4'); define('DB_COLLATE', 'utf8mb4_unicode_520_ci');
    foreach ($rp_input['salts'] as $rp_name => $rp_value) { define($rp_name, $rp_value); }
    define('WP_HOME', 'http://rp-fixture.invalid'); define('WP_SITEURL', 'http://rp-fixture.invalid');
    define('WP_INSTALLING', $rp_mode === 'install'); define('WP_DEBUG', false); define('WP_DEBUG_DISPLAY', false);
    define('WP_DEBUG_LOG', false); define('WP_DISABLE_FATAL_ERROR_HANDLER', true); define('WP_CACHE', false);
    define('WP_ENVIRONMENT_TYPE', 'local'); define('DISABLE_WP_CRON', true); define('WP_HTTP_BLOCK_EXTERNAL', true);
    define('AUTOMATIC_UPDATER_DISABLED', true); define('WP_AUTO_UPDATE_CORE', false);
    define('DISALLOW_FILE_MODS', true); define('DISALLOW_FILE_EDIT', true);
    $table_prefix = 'rp_';
    $_SERVER['HTTP_HOST'] = 'rp-fixture.invalid'; $_SERVER['SERVER_NAME'] = 'rp-fixture.invalid';
    $_SERVER['SERVER_PORT'] = '80'; $_SERVER['SERVER_PROTOCOL'] = 'HTTP/1.1'; $_SERVER['REQUEST_METHOD'] = 'GET';
    $_SERVER['SCRIPT_NAME'] = $rp_mode === 'install' ? '/wp-admin/install.php' : '/index.php';
    $_SERVER['PHP_SELF'] = $_SERVER['SCRIPT_NAME']; $_SERVER['REQUEST_URI'] = $_SERVER['SCRIPT_NAME'];
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
        && $rp_report['native_mysqli_handle'], 'full_normal_bootstrap_required');
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
    $rp_report['passed'] = true; $rp_report['outcome'] = 'fixed_native_bootstrap_passed'; $rp_report['phase'] = 'complete';
} catch (Throwable $rp_error) {
    $rp_report['failure'] = $rp_error instanceof RpBootstrapFailure ? $rp_error->getMessage() : 'php_throwable';
    $rp_report['failure_class'] = get_class($rp_error);
} finally {
    if (isset($wpdb) && $wpdb instanceof wpdb && $wpdb->dbh instanceof mysqli) {
        try { $rp_report['physical_disconnect_completed'] = $wpdb->close(); } catch (Throwable $rp_cleanup) { $rp_report['physical_disconnect_completed'] = false; }
    }
    restore_error_handler(); rp_boot_emit();
}
exit($rp_report['passed'] ? 0 : 1);
