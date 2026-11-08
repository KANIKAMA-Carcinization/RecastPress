<?php
/**
 * Plugin Name: RP Admission Driver Provider Fixture
 * Version: 0.1.0
 */

add_action('init', static function (): void {
    global $wpdb;
    $provider = get_option('rp_admission_driver_provider');
    if (is_string($provider) && is_callable($provider)) {
        $provider($wpdb);
    }
});
