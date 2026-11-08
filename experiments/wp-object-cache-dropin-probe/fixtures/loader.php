<?php
declare(strict_types=1);

function rp_loader_require(bool $ok, string $code): void {
    if (!$ok) {
        throw new RuntimeException($code);
    }
}

set_error_handler(static function (): never { throw new RuntimeException('php_warning'); });
$rp_cache_relay = null;
try {
    rp_loader_require(PHP_SAPI === 'cli' && PHP_VERSION_ID === 80512 && PHP_INT_SIZE === 8 && $argc === 4
        && php_ini_loaded_file() === false && php_ini_scanned_files() === false, 'native_cli_profile');
    [, $mode, $expected_sha, $run] = $argv;
    rp_loader_require(in_array($mode, ['stock', 'shim', 'dropin', 'direct-before', 'direct-after', 'explicit-init'], true), 'fixture_mode');
    $root = dirname(__DIR__, 3);
    $site = realpath($root . '/target/wp-bootstrap-probe/site');
    $rp_cache_probe_binary = realpath($root . '/target/wp-object-cache-rust-probe/cargo-target/debug/recastpress-wp-object-cache-rust-probe.exe');
    rp_loader_require($site !== false && $run === realpath($run) && is_dir($run)
        && is_string($rp_cache_probe_binary) && preg_match('/\A[a-f0-9]{64}\z/D', $expected_sha) === 1
        && hash_equals($expected_sha, hash_file('sha256', $rp_cache_probe_binary)), 'fixed_loader_inputs');
    define('ABSPATH', $site . DIRECTORY_SEPARATOR);
    define('WPINC', 'wp-includes');
    define('WP_CONTENT_DIR', $run);
    define('MULTISITE', true);
    $blog_id = 1;
    $core = ABSPATH . WPINC . '/';
    $dropin = WP_CONTENT_DIR . '/object-cache.php';
    $corpus = $root . '/experiments/wp-object-cache-contract-probe/fixtures/corpus.php';
    require $core . 'version.php';
    require $core . 'load.php';
    require $core . 'plugin.php';
    require $core . 'functions.php';
    require $corpus;
    wp_using_ext_object_cache(false);
    $before = wp_using_ext_object_cache();
    if ($mode === 'direct-before') {
        require_once $core . 'cache.php';
    }
    wp_start_object_cache();
    $active = wp_using_ext_object_cache();
    $class = get_class($GLOBALS['wp_object_cache']);
    $from_dropin = !in_array($mode, ['stock', 'shim', 'direct-before'], true);
    $wrappers = $from_dropin ? $dropin : $core . 'cache.php';
    rp_loader_require($wp_version === '7.1.3' && $before === false && $active === !in_array($mode, ['stock', 'shim'], true)
        && $class === ($from_dropin ? 'RpRustBackedCache' : 'WP_Object_Cache'), 'fixed_loader_selection');
    foreach (['wp_cache_init', 'wp_cache_get', 'wp_cache_set', 'wp_cache_flush', 'wp_cache_get_multiple'] as $name) {
        rp_loader_require(realpath((new ReflectionFunction($name))->getFileName()) === realpath($wrappers), 'loader_wrapper_provenance');
    }
    $expected = [__FILE__, $core . 'version.php', $core . 'load.php', $core . 'plugin.php',
        $core . 'class-wp-hook.php', $core . 'class-wp-filter-sentinel.php', $core . 'functions.php',
        $core . 'option.php', $corpus];
    if ($mode === 'shim') {
        $expected[] = $dropin;
    }
    array_push($expected, $wrappers, $core . 'class-wp-object-cache.php', $core . 'cache-compat.php');
    if ($from_dropin) {
        $expected[] = $run . '/adapter.php';
    }
    rp_loader_require(get_included_files() === array_map('realpath', $expected), 'fixed_loader_includes');
    $shim_late_hook = null;
    if ($mode === 'shim') {
        rp_loader_require(!isset($GLOBALS['rp_cache_shim_late_observed']), 'shim_hook_ran_early');
        do_action('muplugins_loaded');
        $shim_late_hook = ($GLOBALS['rp_cache_shim_late_observed'] ?? null) === true;
        rp_loader_require($shim_late_hook, 'shim_hook_not_run');
    }

    $initial = $GLOBALS['wp_object_cache'];
    wp_cache_set('seed', 'stock', 'rp-loader-repeat');
    $blog_id = 2;
    wp_start_object_cache();
    $second_blog = rp_cache_lookup('seed', 'rp-loader-repeat');
    $blog_id = 1;
    wp_start_object_cache();
    $first_blog = rp_cache_lookup('seed', 'rp-loader-repeat');
    $repeat = $initial === $GLOBALS['wp_object_cache'] && $second_blog === [false, false] && $first_blog === [true, 'stock'];
    wp_cache_delete('seed', 'rp-loader-repeat');
    rp_loader_require($repeat && wp_using_ext_object_cache() === $active, 'repeated_loader_state');
    $observations = null;
    $explicit = null;
    if ($mode === 'explicit-init') {
        wp_cache_set('seed', 'stock', 'rp-loader-repeat');
        wp_cache_set('seed', 'rust', 'rp-cache-multiple');
        wp_cache_init();
        $explicit = ['new_object' => $initial !== $GLOBALS['wp_object_cache'],
            'stock' => rp_cache_lookup('seed', 'rp-loader-repeat'), 'routed' => rp_cache_lookup('seed', 'rp-cache-multiple')];
    } elseif (in_array($mode, ['stock', 'shim', 'dropin'], true)) {
        $observations = rp_cache_contract();
    }
    $report = ['passed' => true, 'mode' => $mode, 'ext_before' => $before, 'ext_after' => $active,
        'cache_class' => $class, 'stock_parent' => get_parent_class($initial), 'wrapper_source' => $from_dropin ? 'dropin' : 'core',
        'repeat_preserved_object_and_blog_scoping' => $repeat, 'observations' => $observations, 'explicit_init' => $explicit,
        'shim_late_hook' => $shim_late_hook,
        'rust_transport' => $rp_cache_relay?->finish()];
} catch (Throwable $error) {
    $code = $error->getMessage();
    $report = ['passed' => false, 'failure' => $error instanceof RuntimeException && preg_match('/\A[a-z][a-z0-9_]{0,80}\z/D', $code) === 1
        ? $code : 'fixture_execution_failed'];
} finally {
    $rp_cache_relay?->close();
}
if ($report['passed'] && $mode === 'direct-after') {
    // An actual PHP redeclaration fatal in this isolated process is the observation.
    ini_set('display_errors', '0');
    ini_set('log_errors', '0');
    register_shutdown_function(static function () use ($report): void {
        $error = error_get_last();
        $report['direct_include_redeclared_init'] = $error !== null && $error['type'] === E_COMPILE_ERROR
            && str_contains($error['message'], 'Cannot redeclare function wp_cache_init()');
        echo json_encode($report, JSON_THROW_ON_ERROR), "\n";
    });
    require_once $core . 'cache.php';
    exit(1);
}
echo json_encode($report, JSON_THROW_ON_ERROR), "\n";
exit($report['passed'] ? 0 : 1);
