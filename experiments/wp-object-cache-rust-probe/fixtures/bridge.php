<?php
declare(strict_types=1);

set_error_handler(static function (): never { throw new RuntimeException('php_warning'); });
$relay = null;
try {
    if (PHP_SAPI !== 'cli' || PHP_VERSION_ID !== 80512 || PHP_INT_SIZE !== 8 || $argc !== 3
        || php_ini_loaded_file() !== false || php_ini_scanned_files() !== false) {
        throw new RuntimeException('native_cli_profile');
    }
    $root = dirname(__DIR__, 3);
    $binary = realpath($root . '/target/wp-object-cache-rust-probe/cargo-target/debug/recastpress-wp-object-cache-rust-probe.exe');
    if (!is_string($binary) || $binary !== realpath($argv[1]) || preg_match('/\A[a-f0-9]{64}\z/D', $argv[2]) !== 1
        || !hash_equals($argv[2], hash_file('sha256', $binary))) {
        throw new RuntimeException('fixed_rust_child');
    }
    $site = realpath($root . '/target/wp-bootstrap-probe/site');
    if ($site === false) {
        throw new RuntimeException('fixed_wordpress_site');
    }
    define('ABSPATH', $site . DIRECTORY_SEPARATOR);
    define('WPINC', 'wp-includes');
    define('MULTISITE', true);
    $blog_id = 1;
    $core = ABSPATH . WPINC . '/';
    $corpus = $root . '/experiments/wp-object-cache-contract-probe/fixtures/corpus.php';
    require $core . 'version.php';
    require $core . 'load.php';
    require $core . 'functions.php';
    require $core . 'cache.php';
    require $corpus;
    require __DIR__ . '/adapter.php';
    $expected = [__FILE__, $core . 'version.php', $core . 'load.php', $core . 'functions.php',
        $core . 'option.php', $core . 'cache.php', $core . 'class-wp-object-cache.php', $corpus, __DIR__ . '/adapter.php'];
    rp_bridge_check(get_included_files() === array_map('realpath', $expected) && $wp_version === '7.1.3',
        'fixed_wordpress_sources');
    $relay = new RpCacheRelay($binary);
    $GLOBALS['wp_object_cache'] = new RpRustBackedCache($relay);
    $observations = rp_cache_contract();
    $transport = $relay->finish();
    $report = [
        'schema' => 'recastpress.wp-object-cache-rust-native-observation.v0.5',
        'passed' => true,
        'php_version' => PHP_VERSION,
        'php_integer_bytes' => PHP_INT_SIZE,
        'wordpress_version' => $wp_version,
        'adapter_class' => get_class($GLOBALS['wp_object_cache']),
        'stock_parent_class' => get_parent_class($GLOBALS['wp_object_cache']),
        'profile' => 'isolated_multisite_cache_prefix',
        'observations' => $observations,
        'rust_transport' => $transport,
    ];
} catch (Throwable $error) {
    $code = $error->getMessage();
    if (!$error instanceof RuntimeException || preg_match('/\A[a-z][a-z0-9_]{0,80}\z/D', $code) !== 1) {
        $code = 'fixture_execution_failed';
    }
    $report = ['passed' => false, 'failure' => $code];
} finally {
    $relay?->close();
}
echo json_encode($report, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES), "\n";
exit($report['passed'] ? 0 : 1);
