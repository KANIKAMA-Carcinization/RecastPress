<?php
/**
 * Plugin Name: RP Admission Benign
 * Version: 0.1.0
 */

add_action('wp_footer', static function (): void {
    global $wpdb;
    $key = 'footer';
    $group = 'rp_admission_benign';
    $found = true;
    $before = wp_cache_get($key, $group, false, $found);
    if ($before !== false || $found || (string) $wpdb->get_var('SELECT 42') !== '42') {
        return;
    }
    if (!wp_cache_add($key, 'fixture-ok', $group)) {
        return;
    }
    $after = wp_cache_get($key, $group, false, $found);
    if ($found && $after === 'fixture-ok') {
        echo '<!-- rp-admission-benign:wpdb-and-request-cache-ok -->';
    }
});
