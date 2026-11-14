<?php
declare(strict_types=1);

if (count($argv) !== 2) {
    fwrite(STDERR, "expected fixed WordPress directory\n");
    exit(2);
}
require $argv[1] . '/wp-includes/plugin.php';
$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];
$GLOBALS['shutdown_probe_mutation'] = 'initial';
$hook = 'recastpress_shutdown_throw_probe';
$observe = static function () use ($hook): void {
    $filter = $GLOBALS['wp_filter'][$hook] ?? null;
    $private = [];
    if ($filter instanceof WP_Hook) {
        $reflection = new ReflectionObject($filter);
        foreach (['nesting_level', 'iterations', 'current_priority', 'doing_action'] as $field) {
            if ($reflection->hasProperty($field)) {
                $property = $reflection->getProperty($field);
                $private[$field] = $property->getValue($filter);
            }
        }
    }
    echo json_encode([
        'php_version' => PHP_VERSION,
        'event' => 'first_shutdown_wp_observer',
        'wp_state' => [
            'did_action' => did_action($hook),
            'current_action' => current_action(),
            'doing_action' => doing_action(),
            'stack' => $GLOBALS['wp_current_filter'],
            'hook_state' => $private,
            'mutation_before_throw_callback' => $GLOBALS['shutdown_probe_mutation'],
        ],
    ], JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
};
add_action($hook, $observe, 5);
add_action($hook, static function (): void {
    $GLOBALS['shutdown_probe_mutation'] = 'mutated_before_throw';
    throw new RuntimeException('shutdown-do-action-throw-marker');
}, 10);
add_action($hook, static function (): void {
    echo "WP_ACTION_SIBLING_REACHED\n";
}, 20);
register_shutdown_function(static function () use ($hook): void {
    do_action($hook);
});
register_shutdown_function(static function (): void {
    echo "SECOND_SHUTDOWN_CALLBACK_REACHED\n";
});
