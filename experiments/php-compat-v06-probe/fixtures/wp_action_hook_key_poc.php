<?php
declare(strict_types=1);

if (count($argv) !== 2) {
    fwrite(STDERR, "expected fixed WordPress directory\n");
    exit(2);
}
require $argv[1] . '/wp-includes/plugin.php';

function action_hook_key_view($value): array
{
    if ($value === null) return ['type' => 'null'];
    if (is_bool($value)) return ['type' => 'bool', 'value' => $value];
    if (is_int($value)) return ['type' => 'int', 'value' => (string) $value];
    if (is_float($value)) return ['type' => 'float', 'bits' => bin2hex(pack('E', $value))];
    if (is_string($value)) return ['type' => 'string', 'hex' => bin2hex($value)];
    return ['type' => get_debug_type($value)];
}

function action_hook_key_run(string $label, $hook, bool $named): array
{
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];
    $events = [];
    $warnings = [];
    $hookView = action_hook_key_view($hook);
    $observe = static function (string $kind, array $args) use (&$events, &$warnings, $hook, $hookView): void {
        $events[] = [
            'event' => $kind,
            'args' => array_map('action_hook_key_view', $args),
            'hook' => $hookView,
            'current_action' => action_hook_key_view(current_action()),
            'doing_action' => doing_action(),
            'did_action' => action_hook_key_view(did_action($hook)),
            'counter' => action_hook_key_view(array_values($GLOBALS['wp_actions'])[0] ?? null),
            'warning_count' => count($warnings),
        ];
    };
    set_error_handler(static function (int $severity, string $message) use (&$events, &$warnings, $hookView): bool {
        $warning = ['index' => count($warnings) + 1, 'severity' => $severity, 'message_hex' => bin2hex($message)];
        $warnings[] = $warning;
        $events[] = [
            'event' => 'warning', ...$warning,
            'hook' => $hookView,
            'current_action' => action_hook_key_view(current_action()),
            'doing_action' => doing_action(),
            'counter' => is_array($GLOBALS['wp_actions'] ?? null)
                ? action_hook_key_view(array_values($GLOBALS['wp_actions'])[0] ?? null)
                : null,
            'warning_count' => count($warnings),
        ];
        return true;
    });
    add_action('all', static function (...$args) use ($observe): void { $observe('all', $args); }, 1, 99);
    if ($named) {
        add_action($hook, static function (...$args) use ($observe): void { $observe('named', $args); }, 10, 99);
    }
    $thrown = null;
    $after = null;
    try {
        do_action($hook, 'payload');
    } catch (Throwable $throwable) {
        $thrown = ['class' => $throwable::class, 'message_hex' => bin2hex($throwable->getMessage())];
        $events[] = ['event' => 'throwable', ...$thrown];
    } finally {
        $after = [
            'did_action' => action_hook_key_view(did_action($hook)),
            'current_action' => action_hook_key_view(current_action()),
            'doing_action' => doing_action(),
            'stack' => array_map('action_hook_key_view', $GLOBALS['wp_current_filter']),
        ];
        restore_error_handler();
    }
    $result = [
        'label' => $label,
        'hook' => $hookView,
        'named_listener' => $named,
        'events' => $events,
        'warnings' => $warnings,
        'throwable' => $thrown,
        'after' => $after,
    ];
    return $result;
}

$cases = [];
foreach ([
    'float_5_7' => 5.7,
    'null' => null,
    'numeric_string_5' => '5',
    'integer_5' => 5,
] as $label => $hook) {
    foreach ([false, true] as $named) {
        $key = $label . ($named ? '_named' : '_all_only');
        $cases[$key] = action_hook_key_run($key, $hook, $named);
    }
}
echo json_encode(['php_version' => PHP_VERSION, 'php_int_size' => PHP_INT_SIZE, 'cases' => $cases], JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
