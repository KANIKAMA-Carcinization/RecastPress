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
    if (is_resource($value)) return ['type' => 'resource', 'resource_type' => get_resource_type($value), 'resource_id' => get_resource_id($value)];
    if (is_array($value)) return ['type' => 'array', 'count' => count($value)];
    if (is_object($value)) return ['type' => 'object', 'class' => get_class($value)];
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
    'boolean_false' => false,
    'boolean_true' => true,
] as $label => $hook) {
    foreach ([false, true] as $named) {
        $key = $label . ($named ? '_named' : '_all_only');
        $cases[$key] = action_hook_key_run($key, $hook, $named);
    }
}

function action_hook_key_alias_case(string $label, $registeredHook, $dispatchedHook): array
{
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];
    $events = [];
    $all = static function (...$args) use (&$events): void {
        $events[] = ['event' => 'all', 'hook_arg' => action_hook_key_view($args[0] ?? null),
            'current_action' => action_hook_key_view(current_action()),
            'did_dispatched' => action_hook_key_view(did_action($args[0] ?? null)),
            'stack' => array_map('action_hook_key_view', $GLOBALS['wp_current_filter'])];
    };
    $named = static function (...$args) use (&$events): void {
        $events[] = ['event' => 'named', 'current_action' => action_hook_key_view(current_action()),
            'stack' => array_map('action_hook_key_view', $GLOBALS['wp_current_filter'])];
    };
    add_action('all', $all, 1, 99);
    add_action($registeredHook, $named, 10, 99);
    $before = [
        'has_dispatched' => has_action($dispatchedHook),
        'did_registered' => did_action($registeredHook),
        'did_dispatched' => did_action($dispatchedHook),
    ];
    do_action($dispatchedHook, 'payload');
    $duringIndependent = [
        'did_registered' => did_action($registeredHook),
        'did_dispatched' => did_action($dispatchedHook),
    ];
    $removed = remove_action($dispatchedHook, $named, 10);
    $after = [
        'has_registered' => has_action($registeredHook),
        'has_dispatched' => has_action($dispatchedHook),
        'did_registered' => did_action($registeredHook),
        'did_dispatched' => did_action($dispatchedHook),
        'current_action' => action_hook_key_view(current_action()),
        'doing_action' => doing_action(),
        'stack' => array_map('action_hook_key_view', $GLOBALS['wp_current_filter']),
    ];
    return ['label' => $label,
        'registered_hook' => action_hook_key_view($registeredHook),
        'dispatched_hook' => action_hook_key_view($dispatchedHook),
        'before' => $before, 'events' => $events,
        'after_dispatch' => $duringIndependent, 'removed_by_alias' => $removed, 'after' => $after];
}

$aliases = [
    'false_register_zero_dispatch' => action_hook_key_alias_case('false_register_zero_dispatch', false, 0),
    'zero_register_false_dispatch' => action_hook_key_alias_case('zero_register_false_dispatch', 0, false),
    'true_register_one_dispatch' => action_hook_key_alias_case('true_register_one_dispatch', true, 1),
    'one_register_true_dispatch' => action_hook_key_alias_case('one_register_true_dispatch', 1, true),
];

class ActionHookKeyStringable
{
    public function __toString(): string { return 'stringable-hook-key'; }
}

function action_hook_key_unusual_case(string $label, $hook, bool $named): array
{
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];
    $events = [];
    $warnings = [];
    $hookView = action_hook_key_view($hook);
    set_error_handler(static function (int $severity, string $message) use (&$events, &$warnings, $hookView): bool {
        $warning = ['index' => count($warnings) + 1, 'severity' => $severity, 'message_hex' => bin2hex($message)];
        $warnings[] = $warning;
        $events[] = ['event' => 'warning', ...$warning,
            'current_action' => action_hook_key_view(current_action()),
            'counter_entries' => count($GLOBALS['wp_actions'] ?? [])];
        return true;
    });
    add_action('all', static function (...$args) use (&$events): void {
        $events[] = ['event' => 'all', 'args' => array_map('action_hook_key_view', $args),
            'current_action' => action_hook_key_view(current_action()),
            'doing_action' => doing_action(),
            'counter_entries' => count($GLOBALS['wp_actions'] ?? [])];
    }, 1, 99);
    if ($named) {
        $registrationThrowable = null;
        try {
            add_action($hook, static function (...$args) use (&$events): void {
                $events[] = ['event' => 'named', 'args' => array_map('action_hook_key_view', $args),
                    'current_action' => action_hook_key_view(current_action()),
                    'doing_action' => doing_action(),
                    'counter_entries' => count($GLOBALS['wp_actions'] ?? [])];
            }, 10, 99);
        } catch (Throwable $throwable) {
            $registrationThrowable = ['class' => $throwable::class, 'message_hex' => bin2hex($throwable->getMessage())];
            $events[] = ['event' => 'throwable', 'stage' => 'named_registration', ...$registrationThrowable];
        }
    } else {
        $registrationThrowable = null;
    }
    $dispatchThrowable = null;
    if ($registrationThrowable === null) {
        try {
            do_action($hook, 'payload');
        } catch (Throwable $throwable) {
            $dispatchThrowable = ['class' => $throwable::class, 'message_hex' => bin2hex($throwable->getMessage())];
            $events[] = ['event' => 'throwable', 'stage' => 'dispatch', ...$dispatchThrowable];
        }
    }
    $didAction = null;
    try {
        $didAction = ['value' => action_hook_key_view(did_action($hook))];
    } catch (Throwable $throwable) {
        $didAction = ['throwable' => ['class' => $throwable::class, 'message_hex' => bin2hex($throwable->getMessage())]];
    }
    $counterEntries = [];
    foreach ($GLOBALS['wp_actions'] as $key => $value) {
        $counterEntries[] = ['key' => action_hook_key_view($key), 'value' => action_hook_key_view($value)];
    }
    $after = ['did_action' => $didAction,
        'counter_entries' => $counterEntries,
        'current_action' => action_hook_key_view(current_action()),
        'doing_action' => doing_action(),
        'stack' => array_map('action_hook_key_view', $GLOBALS['wp_current_filter'])];
    restore_error_handler();
    return ['label' => $label, 'hook' => $hookView, 'named_listener' => $named,
        'events' => $events, 'warnings' => $warnings,
        'registration_throwable' => $registrationThrowable,
        'dispatch_throwable' => $dispatchThrowable, 'after' => $after];
}

$resourceHook = fopen('php://memory', 'r+');
if (!is_resource($resourceHook)) {
    fwrite(STDERR, "could not create resource hook fixture\n");
    exit(2);
}
$unusualHooks = [
    'array' => [],
    'ordinary_object' => new stdClass(),
    'stringable_object' => new ActionHookKeyStringable(),
    'resource' => $resourceHook,
];
$unusualCases = [];
foreach ($unusualHooks as $label => $hook) {
    foreach ([false, true] as $named) {
        $key = $label . ($named ? '_named' : '_all_only');
        $unusualCases[$key] = action_hook_key_unusual_case($key, $hook, $named);
    }
}
fclose($resourceHook);
echo json_encode(['php_version' => PHP_VERSION, 'php_int_size' => PHP_INT_SIZE,
    'cases' => $cases, 'boolean_aliases' => $aliases, 'unusual_cases' => $unusualCases], JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
