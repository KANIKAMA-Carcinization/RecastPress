<?php
declare(strict_types=1);

$root = $argv[1] ?? throw new RuntimeException('missing pinned WordPress root');
$pinned = [
    $root . '/wp-includes/plugin.php' => 'd70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
    $root . '/wp-includes/class-wp-hook.php' => 'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
];
foreach ($pinned as $path => $sha256) {
    if (!is_file($path) || hash_file('sha256', $path) !== $sha256) {
        throw new RuntimeException('pinned WordPress hook source mismatch');
    }
}

$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];
require $root . '/wp-includes/plugin.php';
$GLOBALS['rp_action_ref_array_events'] = [];

function rp_action_ref_array_all(string $hookName, array $args): void
{
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'all',
        'hook' => $hookName,
        'args' => [$hookName, array_map(static fn ($argument) => $argument, $args)],
        'current_action' => current_action(),
        'doing_action' => doing_action($hookName),
        'did_action' => did_action($hookName),
    ];
}

function rp_action_ref_array_truncated(string $value, mixed ...$extra): void
{
    $args = func_get_args();
    $hook = $GLOBALS['rp_action_ref_array_selected'];
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'truncated', 'args' => $args, 'received_arg_count' => count($args),
        'current_action' => current_action(), 'doing_action' => doing_action('rp-probe-action-ref-array'),
        'did_action' => did_action('rp-probe-action-ref-array'), 'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    if ($extra !== []) {
        throw new RuntimeException('wp_action_ref_array_truncated_received_extra');
    }
}

function rp_action_ref_array_zero(): string
{
    $args = func_get_args();
    $hook = $GLOBALS['rp_action_ref_array_selected'];
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'zero', 'args' => $args, 'received_arg_count' => count($args),
        'current_action' => current_action(), 'doing_action' => doing_action('rp-probe-action-ref-array'),
        'did_action' => did_action('rp-probe-action-ref-array'), 'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    if ($args !== []) {
        throw new RuntimeException('wp_action_ref_array_zero_received_arguments');
    }
    return 'ignored-zero-action-result';
}

function rp_action_ref_array_first(string &$value, string &$extra): string
{
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'first',
        'args' => [$value, $extra],
        'current_action' => current_action(),
        'doing_action' => doing_action('rp-probe-action-ref-array'),
        'did_action' => did_action('rp-probe-action-ref-array'),
        'current_priority' => $GLOBALS['rp_action_ref_array_selected']->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($GLOBALS['rp_action_ref_array_selected']),
    ];
    $value = 'value-mutated-by-first';
    $extra = 'extra-mutated-by-first';
    add_action('rp-probe-action-ref-array', 'rp_action_ref_array_added', 7, 2);
    return 'ignored-first-action-result';
}

function rp_action_ref_array_added(string &$value, string &$extra): void
{
    $hook = $GLOBALS['rp_action_ref_array_selected'];
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'added', 'args' => [$value, $extra],
        'current_action' => current_action(), 'doing_action' => doing_action('rp-probe-action-ref-array'),
        'did_action' => did_action('rp-probe-action-ref-array'), 'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    $value = 'value-mutated-by-added';
    $extra = 'extra-mutated-by-added';
}

function rp_action_ref_array_second(string &$value, string &$extra): string
{
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'second',
        'args' => [$value, $extra],
        'current_action' => current_action(),
        'doing_action' => doing_action('rp-probe-action-ref-array'),
        'did_action' => did_action('rp-probe-action-ref-array'),
        'current_priority' => $GLOBALS['rp_action_ref_array_selected']->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($GLOBALS['rp_action_ref_array_selected']),
    ];
    $extra = 'extra-mutated-by-second';
    return 'ignored-second-action-result';
}

$hookName = 'rp-probe-action-ref-array';
add_action('all', 'rp_action_ref_array_all', 10, 2);
add_action($hookName, 'rp_action_ref_array_truncated', 1, 1);
add_action($hookName, 'rp_action_ref_array_zero', 2, 0);
add_action($hookName, 'rp_action_ref_array_first', 5, 2);
add_action($hookName, 'rp_action_ref_array_second', 10, 2);
$selected = $GLOBALS['wp_filter'][$hookName];
$GLOBALS['rp_action_ref_array_selected'] = $selected;
$value = 'seed';
$extra = 'left';
$args = [&$value, &$extra];
$result = do_action_ref_array($hookName, $args);
$observation = [
    'events' => $GLOBALS['rp_action_ref_array_events'],
    'result' => $result,
    'caller_value' => $value,
    'caller_extra' => $extra,
    'did_action' => did_action($hookName),
    'current_action' => current_action(),
    'doing_action' => doing_action(),
    'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($selected),
    'current_priority' => $selected->current_priority(),
];
$expected = [
    'events' => [
        [
            'callback' => 'all', 'hook' => $hookName,
            'args' => [$hookName, ['seed', 'left']],
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 1,
        ],
        [
            'callback' => 'truncated', 'args' => ['seed'], 'received_arg_count' => 1,
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 1, 'wp_hook_doing_action' => true,
        ],
        [
            'callback' => 'zero', 'args' => [], 'received_arg_count' => 0,
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 2, 'wp_hook_doing_action' => true,
        ],
        [
            'callback' => 'first', 'args' => ['seed', 'left'],
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 5, 'wp_hook_doing_action' => true,
        ],
        [
            'callback' => 'added', 'args' => ['value-mutated-by-first', 'extra-mutated-by-first'],
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 7, 'wp_hook_doing_action' => true,
        ],
        [
            'callback' => 'second', 'args' => ['value-mutated-by-added', 'extra-mutated-by-added'],
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 10, 'wp_hook_doing_action' => true,
        ],
    ],
    'result' => null,
    'caller_value' => 'value-mutated-by-added',
    'caller_extra' => 'extra-mutated-by-second',
    'did_action' => 1,
    'current_action' => false,
    'doing_action' => false,
    'wp_hook_doing_action' => false,
    'current_priority' => false,
];
if ($observation !== $expected) {
    fwrite(STDERR, json_encode(['expected' => $expected, 'actual' => $observation], JSON_THROW_ON_ERROR) . "\n");
    throw new RuntimeException('wp_action_ref_array_oracle_mismatch');
}

echo json_encode([
    'status' => 'passed',
    'scenario' => 'wp_action_ref_array_native_oracle',
    'wordpress' => '7.1.3',
    'source_sha256' => [
        'wp-includes/plugin.php' => $pinned[$root . '/wp-includes/plugin.php'],
        'wp-includes/class-wp-hook.php' => $pinned[$root . '/wp-includes/class-wp-hook.php'],
    ],
    'observation' => $observation,
], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
