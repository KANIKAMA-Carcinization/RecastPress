<?php
declare(strict_types=1);

// A bounded CLI entry for unchanged core functions. No WordPress settings bootstrap.
set_error_handler(static function (): never { throw new RuntimeException('php_warning'); });
try {
    if (PHP_SAPI !== 'cli' || PHP_VERSION_ID !== 80512 || PHP_INT_SIZE !== 8 || $argc !== 1
        || php_ini_loaded_file() !== false || php_ini_scanned_files() !== false) {
        throw new RuntimeException('native_cli_profile');
    }
    $site = realpath(dirname(__DIR__, 3) . '/target/wp-bootstrap-probe/site');
    if ($site === false) {
        throw new RuntimeException('fixed_wordpress_site');
    }
    define('ABSPATH', $site . DIRECTORY_SEPARATOR);
    define('WPINC', 'wp-includes');
    define('MULTISITE', true);
    $blog_id = 1;
    $core = ABSPATH . WPINC . '/';
    require $core . 'version.php';
    require $core . 'load.php';
    require $core . 'functions.php';
    require $core . 'cache.php';
    require __DIR__ . '/corpus.php';
    $expected = [__FILE__, $core . 'version.php', $core . 'load.php', $core . 'functions.php',
        $core . 'option.php', $core . 'cache.php', $core . 'class-wp-object-cache.php', __DIR__ . '/corpus.php'];
    if (get_included_files() !== array_map('realpath', $expected) || $wp_version !== '7.1.3') {
        throw new RuntimeException('fixed_wordpress_sources');
    }
    wp_cache_init();
    if (get_class($GLOBALS['wp_object_cache']) !== 'WP_Object_Cache') {
        throw new RuntimeException('fixed_wordpress_cache_class');
    }
    $observations = rp_cache_contract();
    echo json_encode([
        'schema' => 'recastpress.wp-object-cache-native-observation.v0.5',
        'passed' => true,
        'php_version' => PHP_VERSION,
        'php_integer_bytes' => PHP_INT_SIZE,
        'wordpress_version' => $wp_version,
        'stock_cache_class' => 'WP_Object_Cache',
        'profile' => 'isolated_multisite_cache_prefix',
        'observations' => $observations,
    ], JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES), "\n";
} catch (Throwable $error) {
    $code = $error->getMessage();
    if (!$error instanceof RuntimeException || preg_match('/\A[a-z][a-z0-9_]{0,80}\z/D', $code) !== 1) {
        $code = 'fixture_execution_failed';
    }
    echo json_encode(['passed' => false, 'failure' => $code], JSON_THROW_ON_ERROR), "\n";
    exit(1);
}
