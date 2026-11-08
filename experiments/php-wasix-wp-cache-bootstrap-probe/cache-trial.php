<?php
declare(strict_types=1);

function rp_cache_bootstrap_trial(): array {
    $stock = $GLOBALS['wp_object_cache'];
    $external_before = wp_using_ext_object_cache();
    $late_hook = ($GLOBALS['rp_cache_shim_late_observed'] ?? false) === true;
    rp_boot_check($late_hook && did_action('muplugins_loaded') === 1
        && ($external_before === null || $external_before === false)
        && get_class($stock) === WP_Object_Cache::class && get_current_blog_id() === 1, 'cache_trial');

    $get = static function (string $key, string $group): array {
        $found = null;
        $value = wp_cache_get($key, $group, false, $found);
        return [$found, $value];
    };
    $sentinel = $get('alloptions', 'options');
    rp_boot_check($sentinel[0] === true && is_array($sentinel[1]) && $sentinel[1] !== [], 'cache_trial');

    require_once __DIR__ . '/cache-adapter.php';
    $relay = new RpCacheRelay();
    $key = 'rp-bootstrap-trial';
    $multiple = 'rp-cache-multiple';
    $integer = 'rp-cache-integer';
    try {
        $GLOBALS['wp_object_cache'] = new RpRustBackedCache($relay);
        rp_boot_check($get($key, $multiple) === [false, false]
            && wp_cache_set($key, false, $multiple) === true
            && $get($key, $multiple) === [true, false]
            && wp_cache_delete($key, $multiple) === true
            && $get($key, $multiple) === [false, false], 'cache_trial');
        rp_boot_check(wp_cache_set($key, 17, $integer) === true
            && $get($key, $integer) === [true, 17]
            && wp_cache_delete($key, $integer) === true
            && $get($key, $integer) === [false, false], 'cache_trial');
        $transport = $relay->finish();
    } finally {
        $GLOBALS['wp_object_cache'] = $stock;
        $relay->close();
    }

    $external_after = wp_using_ext_object_cache();
    $restored = $GLOBALS['wp_object_cache'] === $stock;
    $sentinel_preserved = $get('alloptions', 'options') === $sentinel;
    $finish = $transport['finish'];
    rp_boot_check($restored && $sentinel_preserved && $external_after === $external_before
        && $transport['groups'] === [$multiple => 5, $integer => 4]
        && $transport['flushes'] === 0 && $transport['channel_closed'] === true
        && $finish['ok'] === true && $finish['operations'] === 9
        && $finish['entries'] === 0 && $finish['charged_bytes'] === 0, 'cache_trial');
    return ['shim_late_hook' => $late_hook, 'external_cache_before' => (bool) $external_before,
        'external_cache_after' => (bool) $external_after, 'stock_restored' => $restored,
        'stock_sentinel_preserved' => $sentinel_preserved, 'groups' => $transport['groups'],
        'finish' => $finish, 'channel_closed' => $transport['channel_closed']];
}
