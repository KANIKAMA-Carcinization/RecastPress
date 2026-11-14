<?php
declare(strict_types=1);

if (count($argv) !== 2) {
    fwrite(STDERR, "expected fixed WordPress directory\n");
    exit(2);
}

require $argv[1] . '/wp-includes/plugin.php';

function action_singleton_poc_view($value, object $expected): array
{
    if (is_object($value)) {
        return ['type' => 'object', 'class' => get_class($value), 'marker' => $value->marker,
            'same_object' => $value === $expected];
    }
    if (is_array($value)) {
        $items = [];
        foreach ($value as $key => $item) {
            $items[] = ['key' => (string) $key, 'value' => action_singleton_poc_view($item, $expected)];
        }
        return ['type' => 'array', 'count' => count($value), 'items' => $items];
    }
    if (is_string($value)) {
        return ['type' => 'string', 'hex' => bin2hex($value)];
    }
    return ['type' => get_debug_type($value)];
}

function action_singleton_poc_case(string $case, bool $namedListener, callable $invoke): array
{
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];
    $hook = 'singleton-object-' . $case;
    $object = (object) ['marker' => 'object-' . $case];
    $events = [];

    add_action('all', static function (...$args) use (&$events, $hook, $object): void {
        if (($args[0] ?? null) === $hook) {
            $events[] = ['event' => 'all', 'args' => array_map(
                static fn ($value): array => action_singleton_poc_view($value, $object), $args,
            )];
        }
    }, 10, 99);
    if ($namedListener) {
        add_action($hook, static function (...$args) use (&$events, $object): void {
            $events[] = ['event' => 'named', 'args' => array_map(
                static fn ($value): array => action_singleton_poc_view($value, $object), $args,
            )];
        }, 10, 99);
    }

    $invoke($hook, $object);
    return ['events' => $events, 'did_action' => did_action($hook),
        'current_filter_stack' => $GLOBALS['wp_current_filter']];
}

$cases = [];
$cases['zero_key_singleton'] = action_singleton_poc_case('zero-key-singleton', true,
    static fn ($hook, $object) => do_action($hook, [0 => $object]));
$cases['nonzero_key_singleton'] = action_singleton_poc_case('nonzero-key-singleton', true,
    static fn ($hook, $object) => do_action($hook, [1 => $object]));
$cases['multi_element'] = action_singleton_poc_case('multi-element', true,
    static fn ($hook, $object) => do_action($hook, [0 => $object, 1 => 'tail']));
$cases['direct_object'] = action_singleton_poc_case('direct-object', true,
    static fn ($hook, $object) => do_action($hook, $object));
$cases['extra_args'] = action_singleton_poc_case('extra-args', true,
    static fn ($hook, $object) => do_action($hook, [0 => $object], 'tail'));
$cases['missing_named_listener'] = action_singleton_poc_case('missing-named-listener', false,
    static fn ($hook, $object) => do_action($hook, [0 => $object]));
$cases['do_action_ref_array_control'] = action_singleton_poc_case('ref-array-control', true,
    static fn ($hook, $object) => do_action_ref_array($hook, [[0 => $object]]));

echo json_encode(['php_version' => PHP_VERSION, 'cases' => $cases],
    JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
