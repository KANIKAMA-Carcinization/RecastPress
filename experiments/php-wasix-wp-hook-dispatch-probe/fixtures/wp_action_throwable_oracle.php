<?php
declare(strict_types=1);

$root = $argv[1] ?? throw new RuntimeException('missing pinned WordPress root');
$pinned = [
    $root . '/wp-includes/plugin.php' => 'd70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
    $root . '/wp-includes/class-wp-hook.php' => 'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
];
foreach ($pinned as $path => $sha256) {
    if (!is_file($path) || hash_file('sha256', $path) !== $sha256) {
        throw new RuntimeException('pinned WordPress action Throwable source mismatch');
    }
}

$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];
require $root . '/wp-includes/plugin.php';
$GLOBALS['rp_action_throwable_events'] = [];

function rp_action_throwable_oracle_private_state(WP_Hook $hook): array
{
    $read = static fn (string $name): mixed => (new ReflectionProperty(WP_Hook::class, $name))->getValue($hook);
    return [
        'nesting_level' => $read('nesting_level'),
        'iterations' => $read('iterations'),
        'current_priority' => $read('current_priority'),
        'doing_action' => $read('doing_action'),
    ];
}

function rp_action_throwable_all(string $hookName, mixed ...$args): void
{
    $GLOBALS['rp_action_throwable_events'][] = [
        'callback' => 'all',
        'hook' => $hookName,
        'args' => func_get_args(),
        'current_action' => current_action(),
        'doing_action' => doing_action($hookName),
        'did_action' => did_action($hookName),
    ];
}

function rp_action_throwing_oracle_callback(string $value, string $extra): never
{
    $hook = $GLOBALS['rp_action_throwable_selected'];
    $GLOBALS['rp_action_throwable_events'][] = [
        'callback' => 'throwing',
        'args' => [$value, $extra],
        'current_action' => current_action(),
        'doing_action' => doing_action('rp-probe-action-throwable'),
        'did_action' => did_action('rp-probe-action-throwable'),
        'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    $throwable = new RuntimeException('action-throwable-fixture');
    $GLOBALS['rp_action_throwable_original'] = $throwable;
    throw $throwable;
}

function rp_action_never_oracle_callback(string $value, string $extra): void
{
    $GLOBALS['rp_action_throwable_events'][] = ['callback' => 'must-not-run', 'args' => [$value, $extra]];
}

$hookName = 'rp-probe-action-throwable';
add_action('all', 'rp_action_throwable_all', 10, 10);
add_action($hookName, 'rp_action_throwing_oracle_callback', 5, 2);
add_action($hookName, 'rp_action_never_oracle_callback', 10, 2);
$selected = $GLOBALS['wp_filter'][$hookName];
$GLOBALS['rp_action_throwable_selected'] = $selected;
$caught = null;
try {
    do_action($hookName, 'seed', 'left');
    throw new RuntimeException('native oracle did_action return unexpectedly');
} catch (Throwable $error) {
    $caught = $error;
}
$observation = [
    'events' => $GLOBALS['rp_action_throwable_events'],
    'same_throwable' => $caught === $GLOBALS['rp_action_throwable_original'],
    'throwable_class' => $caught::class,
    'throwable_message' => $caught->getMessage(),
    'action_count' => did_action($hookName),
    'current_action' => current_action(),
    'current_filter' => current_filter(),
    'doing_action' => doing_action($hookName),
    'wp_current_filter' => $GLOBALS['wp_current_filter'],
    'current_priority' => $selected->current_priority(),
    'wp_hook_state' => rp_action_throwable_oracle_private_state($selected),
];
$expected = [
    'events' => [
        [
            'callback' => 'all',
            'hook' => $hookName,
            'args' => [$hookName, 'seed', 'left'],
            'current_action' => $hookName,
            'doing_action' => true,
            'did_action' => 1,
        ],
        [
            'callback' => 'throwing',
            'args' => ['seed', 'left'],
            'current_action' => $hookName,
            'doing_action' => true,
            'did_action' => 1,
            'current_priority' => 5,
            'wp_hook_doing_action' => true,
        ],
    ],
    'same_throwable' => true,
    'throwable_class' => RuntimeException::class,
    'throwable_message' => 'action-throwable-fixture',
    'action_count' => 1,
    'current_action' => $hookName,
    'current_filter' => $hookName,
    'doing_action' => true,
    'wp_current_filter' => [$hookName],
    'current_priority' => 5,
    'wp_hook_state' => [
        'nesting_level' => 1,
        'iterations' => [[5, 10]],
        'current_priority' => [5],
        'doing_action' => true,
    ],
];
if ($observation !== $expected) {
    fwrite(STDERR, json_encode(['expected' => $expected, 'actual' => $observation], JSON_THROW_ON_ERROR) . "\n");
    throw new RuntimeException('wp_action_throwable_oracle_mismatch');
}

echo json_encode([
    'status' => 'passed',
    'scenario' => 'wp_action_throwable_native_oracle',
    'wordpress' => '7.1.3',
    'source_sha256' => [
        'wp-includes/plugin.php' => $pinned[$root . '/wp-includes/plugin.php'],
        'wp-includes/class-wp-hook.php' => $pinned[$root . '/wp-includes/class-wp-hook.php'],
    ],
    'observation' => $observation,
], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
