<?php
declare(strict_types=1);

if (count($argv) !== 3 || ! in_array($argv[2], ['all', 'named'], true)) {
    fwrite(STDERR, "expected fixed WordPress directory and case name\n");
    exit(2);
}

require $argv[1] . '/wp-includes/plugin.php';

$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];
$hook = 'uncaught-throwable-' . $argv[2];
$events = [];

$hookState = static function (string $name): array {
    $filter = $GLOBALS['wp_filter'][$name] ?? null;
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
    return $private;
};

$actionState = static function (string $name) use ($hookState): array {
    return [
        'did_action' => did_action($name),
        'current_action' => current_action(),
        'doing_action' => doing_action(),
        'stack' => $GLOBALS['wp_current_filter'],
        'hook_state' => $hookState($name),
        'all_hook_state' => $hookState('all'),
    ];
};

register_shutdown_function(static function () use (&$events, $hook, $argv, $actionState): void {
    $lastError = error_get_last();
    $uncaught = null;
    if (is_array($lastError) && preg_match('/^Uncaught ([^:]+): ([^\r\n]+)/', $lastError['message'], $match)) {
        $message = $match[2];
        $location = strpos($message, ' in ');
        $uncaught = [
            'class' => $match[1],
            'message' => $location === false ? $message : substr($message, 0, $location),
        ];
    }
    $shutdown = [
        'php_version' => PHP_VERSION,
        'case' => $argv[2],
        'events' => $events,
        'state' => $actionState($hook),
        'last_error' => $lastError === null ? null : [
            'type' => $lastError['type'],
            'uncaught_class' => $uncaught['class'] ?? null,
            'uncaught_message' => $uncaught['message'] ?? null,
        ],
    ];
    echo json_encode($shutdown, JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
});

if ($argv[2] === 'all') {
    add_action('all', static function (...$args) use (&$events, $hook): void {
        if ($args[0] !== $hook) {
            return;
        }
        $events[] = ['event' => 'all_first', 'args' => $args];
        throw new RuntimeException('uncaught-all-marker');
    }, 1, 99);
    add_action('all', static function (...$args) use (&$events, $hook): void {
        if ($args[0] === $hook) {
            $events[] = ['event' => 'all_sibling', 'args' => $args];
        }
    }, 2, 99);
    add_action($hook, static function (...$args) use (&$events): void {
        $events[] = ['event' => 'named_listener', 'args' => $args];
    }, 10, 99);
} else {
    add_action('all', static function (...$args) use (&$events, $hook): void {
        if ($args[0] === $hook) {
            $events[] = ['event' => 'all', 'args' => $args];
        }
    }, 1, 99);
    add_action($hook, static function (...$args) use (&$events): void {
        $events[] = ['event' => 'named_first', 'args' => $args];
        throw new RuntimeException('uncaught-named-marker');
    }, 10, 99);
    add_action($hook, static function (...$args) use (&$events): void {
        $events[] = ['event' => 'named_sibling', 'args' => $args];
    }, 20, 99);
}

do_action($hook, 'payload');
