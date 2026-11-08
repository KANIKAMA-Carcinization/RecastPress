<?php
// The normal loader must still select stock cache.php when no wp_cache_init is defined here.
add_action('muplugins_loaded', static function (): void {
    $GLOBALS['rp_cache_shim_late_observed'] = true;
});
