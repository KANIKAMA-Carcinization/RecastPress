<?php
declare(strict_types=1);

if (count($argv) !== 2) {
    fwrite(STDERR, "expected fixed WordPress directory\n");
    exit(2);
}

require $argv[1] . '/wp-includes/plugin.php';

function action_counter_cow_poc_view($value): array
{
    if ($value === null) {
        return ['type' => 'null'];
    }
    if (is_bool($value)) {
        return ['type' => 'bool', 'value' => $value];
    }
    if (is_int($value)) {
        return ['type' => 'int', 'value' => (string) $value];
    }
    if (is_float($value)) {
        return ['type' => 'float', 'bits' => bin2hex(pack('E', $value))];
    }
    if (is_string($value)) {
        return ['type' => 'string', 'hex' => bin2hex($value)];
    }

    return ['type' => get_debug_type($value)];
}

function action_counter_cow_poc_case(bool $keepSnapshot): array
{
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];

    $name = $keepSnapshot ? 'snapshot' : 'live';
    $hook = 'counter-cow-' . $name;
    $GLOBALS['wp_actions'][$hook] = 'a';
    $state = [
        'warnings' => [],
        'events' => [],
        'snapshot' => null,
        'nested_started' => false,
    ];

    $read = static function (?array $snapshot) use (&$state, $hook): array {
        return [
            'live' => action_counter_cow_poc_view($GLOBALS['wp_actions'][$hook] ?? null),
            'snapshot' => $snapshot === null
                ? null
                : action_counter_cow_poc_view($snapshot[$hook] ?? null),
            'did_action' => action_counter_cow_poc_view(did_action($hook)),
        ];
    };
    $capture = static function (string $event, array $args) use (&$state, $hook, $read): void {
        $state['events'][] = [
            'event' => $event,
            'args' => $args,
            'current_action' => current_action(),
            'doing_action' => doing_action($hook),
            ...$read($state['snapshot']),
        ];
    };

    set_error_handler(static function (int $severity, string $message) use (&$state, $hook, $keepSnapshot, $read): bool {
        $index = count($state['warnings']) + 1;
        $warning = [
            'index' => $index,
            'severity' => $severity,
            'message_hex' => bin2hex($message),
            'phase' => $index === 1 ? 'outer_increment' : 'nested_increment',
        ];
        $state['warnings'][] = $warning;

        if ($index === 1) {
            if ($keepSnapshot) {
                // Keep a by-value array snapshot alive while nested do_action mutates the global.
                $state['snapshot'] = $GLOBALS['wp_actions'];
            }
            $state['events'][] = ['event' => 'warning-before-nested', ...$warning, ...$read($state['snapshot'])];
            $state['nested_started'] = true;
            set_error_handler(static function (int $nestedSeverity, string $nestedMessage) use (&$state, $read): bool {
                $nestedWarning = [
                    'index' => count($state['warnings']) + 1,
                    'severity' => $nestedSeverity,
                    'message_hex' => bin2hex($nestedMessage),
                    'phase' => 'nested_increment',
                ];
                $state['warnings'][] = $nestedWarning;
                $state['events'][] = ['event' => 'warning-nested', ...$nestedWarning, ...$read($state['snapshot'])];

                return true;
            });
            try {
                do_action($hook, 'nested');
            } finally {
                restore_error_handler();
            }
            $state['events'][] = ['event' => 'warning-after-nested', ...$warning, ...$read($state['snapshot'])];
        }

        return true;
    });

    add_action('all', static function (...$args) use ($capture, $hook): void {
        if (($args[0] ?? null) === $hook) {
            $capture('all', $args);
        }
    }, 1, 0);
    add_action($hook, static function (...$args) use ($capture): void {
        $capture('named', $args);
    }, 10, 99);

    $before = $read(null);
    do_action($hook, 'outer');
    $after = $read($state['snapshot']);
    $stack = $GLOBALS['wp_current_filter'];
    restore_error_handler();

    return [
        'mode' => $name,
        'snapshot_kept' => $keepSnapshot,
        'before' => $before,
        'events' => $state['events'],
        'warnings' => $state['warnings'],
        'after' => $after,
        'current_filter_stack' => $stack,
    ];
}

echo json_encode([
    'php_version' => PHP_VERSION,
    'php_int_size' => PHP_INT_SIZE,
    'cases' => [
        'live' => action_counter_cow_poc_case(false),
        'snapshot' => action_counter_cow_poc_case(true),
    ],
], JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
