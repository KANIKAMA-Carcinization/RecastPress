<?php
declare(strict_types=1);

$root = $argv[1] ?? throw new RuntimeException('missing pinned WordPress root');
$pinned = [
    $root . '/wp-includes/plugin.php' => 'd70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
    $root . '/wp-includes/class-wp-hook.php' => 'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
];
foreach ($pinned as $path => $sha256) {
    if (!is_file($path) || hash_file('sha256', $path) !== $sha256) {
        throw new RuntimeException('pinned WordPress nested-action source mismatch');
    }
}

const RP_NESTED_OUTER_HOOK = 'rp-probe-action-nested-outer';
const RP_NESTED_INNER_HOOK = 'rp-probe-action-nested-inner';

$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];
$GLOBALS['rp_nested_oracle_events'] = [];
require $root . '/wp-includes/plugin.php';

function rp_nested_oracle_private_state(WP_Hook $hook): array
{
    $read = static fn (string $name): mixed => (new ReflectionProperty(WP_Hook::class, $name))->getValue($hook);
    return [
        'nesting_level' => $read('nesting_level'),
        'iterations' => $read('iterations'),
        'current_priority' => $read('current_priority'),
        'doing_action' => $read('doing_action'),
    ];
}

function rp_nested_oracle_all(string $hookName, mixed ...$args): void
{
    $GLOBALS['rp_nested_oracle_events'][] = [
        'callback' => 'all',
        'hook' => $hookName,
        'args' => func_get_args(),
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_outer' => doing_action(RP_NESTED_OUTER_HOOK),
        'doing_inner' => doing_action(RP_NESTED_INNER_HOOK),
        'did_outer' => did_action(RP_NESTED_OUTER_HOOK),
        'did_inner' => did_action(RP_NESTED_INNER_HOOK),
        'stack' => $GLOBALS['wp_current_filter'],
    ];
}

function rp_nested_oracle_outer_parent(): void
{
    $outer = $GLOBALS['rp_nested_oracle_outer_hook'];
    $inner = $GLOBALS['rp_nested_oracle_inner_hook'];
    $GLOBALS['rp_nested_oracle_events'][] = [
        'callback' => 'outer_parent_enter',
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_outer' => doing_action(RP_NESTED_OUTER_HOOK),
        'doing_inner' => doing_action(RP_NESTED_INNER_HOOK),
        'did_outer' => did_action(RP_NESTED_OUTER_HOOK),
        'did_inner' => did_action(RP_NESTED_INNER_HOOK),
        'stack' => $GLOBALS['wp_current_filter'],
        'outer_priority' => $outer->current_priority(),
    ];
    do_action(RP_NESTED_INNER_HOOK, 'inner-value');
    $GLOBALS['rp_nested_oracle_events'][] = [
        'callback' => 'outer_parent_after',
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_outer' => doing_action(RP_NESTED_OUTER_HOOK),
        'doing_inner' => doing_action(RP_NESTED_INNER_HOOK),
        'did_outer' => did_action(RP_NESTED_OUTER_HOOK),
        'did_inner' => did_action(RP_NESTED_INNER_HOOK),
        'stack' => $GLOBALS['wp_current_filter'],
        'outer_priority' => $outer->current_priority(),
        'inner_priority' => $inner->current_priority(),
        'inner_hook_state' => rp_nested_oracle_private_state($inner),
    ];
}

function rp_nested_oracle_inner_callback(string $value): void
{
    $GLOBALS['rp_nested_oracle_events'][] = [
        'callback' => 'inner_callback',
        'args' => [$value],
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_outer' => doing_action(RP_NESTED_OUTER_HOOK),
        'doing_inner' => doing_action(RP_NESTED_INNER_HOOK),
        'did_outer' => did_action(RP_NESTED_OUTER_HOOK),
        'did_inner' => did_action(RP_NESTED_INNER_HOOK),
        'stack' => $GLOBALS['wp_current_filter'],
        'current_priority' => $GLOBALS['rp_nested_oracle_inner_hook']->current_priority(),
    ];
}

function rp_nested_oracle_outer_tail(): void
{
    $GLOBALS['rp_nested_oracle_events'][] = [
        'callback' => 'outer_tail',
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_outer' => doing_action(RP_NESTED_OUTER_HOOK),
        'doing_inner' => doing_action(RP_NESTED_INNER_HOOK),
        'did_outer' => did_action(RP_NESTED_OUTER_HOOK),
        'did_inner' => did_action(RP_NESTED_INNER_HOOK),
        'stack' => $GLOBALS['wp_current_filter'],
        'current_priority' => $GLOBALS['rp_nested_oracle_outer_hook']->current_priority(),
    ];
}

add_action('all', 'rp_nested_oracle_all', 10, 10);
add_action(RP_NESTED_OUTER_HOOK, 'rp_nested_oracle_outer_parent', 5, 0);
add_action(RP_NESTED_OUTER_HOOK, 'rp_nested_oracle_outer_tail', 10, 0);
add_action(RP_NESTED_INNER_HOOK, 'rp_nested_oracle_inner_callback', 7, 1);
$outer = $GLOBALS['wp_filter'][RP_NESTED_OUTER_HOOK];
$inner = $GLOBALS['wp_filter'][RP_NESTED_INNER_HOOK];
$GLOBALS['rp_nested_oracle_outer_hook'] = $outer;
$GLOBALS['rp_nested_oracle_inner_hook'] = $inner;
$result = do_action(RP_NESTED_OUTER_HOOK);

$observation = [
    'events' => $GLOBALS['rp_nested_oracle_events'],
    'outer_count' => did_action(RP_NESTED_OUTER_HOOK),
    'inner_count' => did_action(RP_NESTED_INNER_HOOK),
    'current_action' => current_action(),
    'current_filter' => current_filter(),
    'doing_action' => doing_action(),
    'stack' => $GLOBALS['wp_current_filter'],
    'outer_hook_state' => rp_nested_oracle_private_state($outer),
    'inner_hook_state' => rp_nested_oracle_private_state($inner),
];
if ($result !== null
    || array_column($observation['events'], 'callback') !== [
        'all', 'outer_parent_enter', 'all', 'inner_callback', 'outer_parent_after', 'outer_tail',
    ]
    || $observation['outer_count'] !== 1
    || $observation['inner_count'] !== 1
    || $observation['current_action'] !== false
    || $observation['current_filter'] !== false
    || $observation['doing_action'] !== false
    || $observation['stack'] !== []
    || $observation['outer_hook_state'] !== [
        'nesting_level' => 0, 'iterations' => [], 'current_priority' => [], 'doing_action' => false,
    ]
    || $observation['inner_hook_state'] !== [
        'nesting_level' => 0, 'iterations' => [], 'current_priority' => [], 'doing_action' => false,
    ]
) {
    fwrite(STDERR, json_encode(['actual' => $observation], JSON_THROW_ON_ERROR) . "\n");
    throw new RuntimeException('wp_action_nested_oracle_mismatch');
}

echo json_encode([
    'status' => 'passed',
    'scenario' => 'wp_action_nested_native_oracle',
    'wordpress' => '7.1.3',
    'source_sha256' => [
        'wp-includes/plugin.php' => $pinned[$root . '/wp-includes/plugin.php'],
        'wp-includes/class-wp-hook.php' => $pinned[$root . '/wp-includes/class-wp-hook.php'],
    ],
    'observation' => $observation,
], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
