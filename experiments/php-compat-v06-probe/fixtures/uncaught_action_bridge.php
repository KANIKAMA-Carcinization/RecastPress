<?php
declare(strict_types=1);

if (count($argv) !== 2 || ! in_array($argv[1], ['all', 'named'], true)) {
    fwrite(STDERR, "expected fixed uncaught Action case\n");
    exit(2);
}

$case = $argv[1];
$hook = 'uncaught-throwable-' . $case;
$events = [];
$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];
require __DIR__ . '/wp_plugin.php';

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

register_shutdown_function(static function () use (&$events, $case, $hook, $actionState): void {
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
        'case' => $case,
        'events' => $events,
        'state' => $actionState($hook),
        'last_error' => $lastError === null ? null : [
            'type' => $lastError['type'],
            'uncaught_class' => $uncaught['class'] ?? null,
            'uncaught_message' => $uncaught['message'] ?? null,
        ],
    ];
    fwrite(STDERR, 'V06_UNCAUGHT_ACTION_OBSERVATION=' . json_encode(
        $shutdown,
        JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE
    ) . "\n");
});

$send = static function (string $request, int $sequence, string $kind, array $payload): void {
    $line = json_encode([
        'v' => 1,
        'request' => $request,
        'seq' => $sequence,
        'kind' => $kind,
        'payload' => $payload,
    ], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
    $written = 0;
    while ($written < strlen($line)) {
        $count = fwrite(STDOUT, substr($line, $written));
        if ($count === false || $count === 0) {
            throw new RuntimeException('uncaught_action_transport_write');
        }
        $written += $count;
    }
    fflush(STDOUT);
};

$request = 'hooks-uncaught-' . $case . '-1';
$send($request, 0, 'start_root', ['operation' => 'hooks_uncaught_action', 'case' => $case]);
$line = fgets(STDIN, 65538);
if ($line === false || ! str_ends_with($line, "\n")) {
    throw new RuntimeException('uncaught_action_transport_eof');
}
$frame = json_decode($line, true, 16, JSON_THROW_ON_ERROR);
if (! is_array($frame) || array_is_list($frame)
    || array_keys($frame) !== ['v', 'request', 'seq', 'kind', 'payload']
    || $frame['v'] !== 1 || $frame['request'] !== $request || $frame['seq'] !== 0
    || $frame['kind'] !== 'invoke_php' || ! is_array($frame['payload'])
    || array_keys($frame['payload']) !== ['callback', 'effect_id']
    || $frame['payload']['callback'] !== 'dispatch_uncaught_action'
    || ! is_string($frame['payload']['effect_id'])
    || preg_match('/^e-[0-9]+-[0-9]+$/D', $frame['payload']['effect_id']) !== 1) {
    throw new RuntimeException('uncaught_action_invoke_frame_mismatch');
}
$send($request, 1, 'effect_started', ['effect_id' => $frame['payload']['effect_id']]);

$dispatchUncaughtAction = static function () use ($case, $hook, &$events): void {
    if ($case === 'all') {
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
};
$dispatchUncaughtAction();
