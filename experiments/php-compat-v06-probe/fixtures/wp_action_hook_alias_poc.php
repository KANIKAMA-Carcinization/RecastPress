<?php
declare(strict_types=1);

if (count($argv) !== 2) {
    fwrite(STDERR, "expected fixed WordPress directory\\n");
    exit(2);
}
require $argv[1] . '/wp-includes/plugin.php';

function action_hook_alias_view($value): array
{
    if ($value === null) return ['type' => 'null'];
    if (is_bool($value)) return ['type' => 'bool', 'value' => $value];
    if (is_int($value)) return ['type' => 'int', 'value' => (string) $value];
    if (is_string($value)) return ['type' => 'string', 'hex' => bin2hex($value)];
    return ['type' => get_debug_type($value)];
}

function action_hook_alias_case(string $name, string|int $registered, string|int $dispatched): array
{
    $GLOBALS['wp_filter'] = [];
    if ($name === 'string_registration_integer_dispatch') {
        $GLOBALS['wp_actions'] = [];
    }
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];
    $events = [];
    $before = ['string' => action_hook_alias_view(did_action('5')),
        'integer' => action_hook_alias_view(did_action(5))];
    $all = static function (...$args) use (&$events): void {
        if (($args[0] ?? null) !== '5' && ($args[0] ?? null) !== 5) return;
        $events[] = ['event' => 'all', 'args' => array_map('action_hook_alias_view', $args),
            'current_action' => action_hook_alias_view(current_action()),
            'did_action_string' => action_hook_alias_view(did_action('5')),
            'did_action_integer' => action_hook_alias_view(did_action(5))];
    };
    add_action('all', $all, 1, 99);
    $named = static function (...$args) use (&$events): void {
        $events[] = ['event' => 'named', 'args' => array_map('action_hook_alias_view', $args),
            'current_action' => action_hook_alias_view(current_action()),
            'did_action_string' => action_hook_alias_view(did_action('5')),
            'did_action_integer' => action_hook_alias_view(did_action(5))];
    };
    add_action($registered, $named, 10, 99);
    $hasBefore = ['registered' => action_hook_alias_view(has_action($registered, $named)),
        'dispatch' => action_hook_alias_view(has_action($dispatched, $named))];
    $registration = action_hook_alias_view($registered);
    $dispatch = action_hook_alias_view($dispatched);
    do_action($dispatched, 'payload');
    $secondDispatch = is_int($dispatched) ? '5' : 5;
    do_action($secondDispatch, 'payload');
    $removed = remove_action($dispatched, $named, 10);
    remove_action('all', $all, 1);
    $hasAfterRemove = ['registered' => action_hook_alias_view(has_action($registered, $named)),
        'dispatch' => action_hook_alias_view(has_action($dispatched, $named))];
    $after = ['did_action_string' => action_hook_alias_view(did_action('5')),
        'did_action_integer' => action_hook_alias_view(did_action(5)),
        'removed_by_dispatch_alias' => $removed,
        'has_after_remove' => $hasAfterRemove,
        'current_action' => action_hook_alias_view(current_action()),
        'doing_action' => doing_action(),
        'stack' => array_map('action_hook_alias_view', $GLOBALS['wp_current_filter'])];
    return ['label' => $name, 'registered_hook' => $registration, 'dispatch_hook' => $dispatch, 'second_dispatch_hook' => action_hook_alias_view($secondDispatch),
        'before' => $before, 'has_before_dispatch' => $hasBefore, 'events' => $events, 'after' => $after];
}

$cases = [
    'string_registration_integer_dispatch' => action_hook_alias_case('string_registration_integer_dispatch', '5', 5),
    'integer_registration_string_dispatch' => action_hook_alias_case('integer_registration_string_dispatch', 5, '5'),
];
echo json_encode(['php_version' => PHP_VERSION, 'php_int_size' => PHP_INT_SIZE, 'cases' => $cases],
    JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
