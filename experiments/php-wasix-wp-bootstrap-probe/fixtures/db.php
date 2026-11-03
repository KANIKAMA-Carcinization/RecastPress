<?php
declare(strict_types=1);
// Owned overlay only. require_wp_db() has loaded the untouched parent wpdb.
require_once '/experiments/php-wasix-wp-bootstrap-probe/fixtures/helpers.php';
require_once '/experiments/php-wasix-wp-bootstrap-probe/fixtures/adapter.php';
global $wpdb;
rp_check(!isset($wpdb) && defined('ABSPATH') && ABSPATH === '/wordpress/', 'owned_dropin_context');
rp_check(isset($GLOBALS['rp_input']['nonce']), 'owned_dropin_nonce');
$wpdb = new RpBridgeWpdb(['nonce' => $GLOBALS['rp_input']['nonce']]);
$wpdb->suppress_errors(true);
$GLOBALS['rp_report']['owned_db_dropin_loaded'] = true;
