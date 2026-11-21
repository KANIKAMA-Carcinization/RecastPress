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
$GLOBALS['rp_events'] = [];
$hookName = 'rp-probe-action-ref-array-unset-bucket';

function rp_unset_bucket_all(string $name, array $args): void
{
    $GLOBALS['rp_events'][] = [
        'callback' => 'all', 'hook' => $name,
        'args' => [$name, array_map(static fn ($argument) => $argument, $args)],
        'current_action' => current_action(), 'doing_action' => doing_action($name),
        'did_action' => did_action($name),
    ];
}

function rp_unset_bucket_first(string &$value, string &$extra): void
{
    $hook = $GLOBALS['rp_selected_hook'];
    $GLOBALS['rp_events'][] = [
        'callback' => 'first', 'args' => [$value, $extra],
        'current_action' => current_action(), 'doing_action' => doing_action('rp-probe-action-ref-array-unset-bucket'),
        'did_action' => did_action('rp-probe-action-ref-array-unset-bucket'),
        'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    $value = 'value-mutated-by-first';
    $priorities = (new ReflectionProperty(WP_Hook::class, 'priorities'))->getValue($hook);
    if (!isset($hook->callbacks[8]['rp_unset_bucket_pending']) || !in_array(8, $priorities, true)) {
        throw new RuntimeException('priority-8 bucket missing before direct unset');
    }
    unset($hook->callbacks[8]);
    $priorities = (new ReflectionProperty(WP_Hook::class, 'priorities'))->getValue($hook);
    $bucketExists = array_key_exists(8, $hook->callbacks);
    $priorityStillScheduled = in_array(8, $priorities, true);
    $GLOBALS['rp_events'][array_key_last($GLOBALS['rp_events'])]['direct_table_edit'] = [
        'operation' => 'unset_priority_bucket_directly',
        'removed_uid' => 'rp_unset_bucket_pending',
        'priority' => 8,
        'bucket_exists' => $bucketExists,
        'priority_still_scheduled' => $priorityStillScheduled,
    ];
    if ($bucketExists || !$priorityStillScheduled) {
        throw new RuntimeException('direct priority-bucket unset state mismatch');
    }
}

function rp_unset_bucket_pending(string &$value, string &$extra): void
{
    throw new RuntimeException('removed callback in unset priority bucket was invoked');
}

function rp_unset_bucket_tail(string &$value, string &$extra): void
{
    $hook = $GLOBALS['rp_selected_hook'];
    $GLOBALS['rp_events'][] = [
        'callback' => 'tail', 'args' => [$value, $extra],
        'current_action' => current_action(), 'doing_action' => doing_action('rp-probe-action-ref-array-unset-bucket'),
        'did_action' => did_action('rp-probe-action-ref-array-unset-bucket'),
        'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    $extra = 'extra-mutated-by-tail';
}

add_action('all', 'rp_unset_bucket_all', 10, 2);
add_action($hookName, 'rp_unset_bucket_first', 5, 2);
add_action($hookName, 'rp_unset_bucket_pending', 8, 2);
add_action($hookName, 'rp_unset_bucket_tail', 10, 2);
$hook = $GLOBALS['wp_filter'][$hookName];
$GLOBALS['rp_selected_hook'] = $hook;
$value = 'seed';
$extra = 'left';
$args = [&$value, &$extra];
$warnings = [];
set_error_handler(static function (int $severity, string $message) use (&$warnings): bool {
    $warnings[] = ['severity' => $severity, 'message' => $message];
    return true;
});
$result = do_action_ref_array($hookName, $args);
restore_error_handler();
$observation = [
    'events' => $GLOBALS['rp_events'],
    'result' => $result,
    'caller_value' => $value,
    'caller_extra' => $extra,
    'did_action' => did_action($hookName),
    'current_action' => current_action(),
    'doing_action' => doing_action(),
    'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    'current_priority' => $hook->current_priority(),
    'warnings' => $warnings,
];
$expected = [
    'events' => [
        [
            'callback' => 'all', 'hook' => $hookName, 'args' => [$hookName, ['seed', 'left']],
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 1,
        ],
        [
            'callback' => 'first', 'args' => ['seed', 'left'],
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 5, 'wp_hook_doing_action' => true,
            'direct_table_edit' => [
                'operation' => 'unset_priority_bucket_directly',
                'removed_uid' => 'rp_unset_bucket_pending', 'priority' => 8,
                'bucket_exists' => false, 'priority_still_scheduled' => true,
            ],
        ],
        [
            'callback' => 'tail', 'args' => ['value-mutated-by-first', 'left'],
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 10, 'wp_hook_doing_action' => true,
        ],
    ],
    'result' => null,
    'caller_value' => 'value-mutated-by-first',
    'caller_extra' => 'extra-mutated-by-tail',
    'did_action' => 1,
    'current_action' => false,
    'doing_action' => false,
    'wp_hook_doing_action' => false,
    'current_priority' => false,
    'warnings' => [
        [
            'severity' => E_WARNING,
            'message' => 'Undefined array key 8',
        ],
        [
            'severity' => E_WARNING,
            'message' => 'foreach() argument must be of type array|object, null given',
        ],
    ],
];
if ($observation !== $expected) {
    fwrite(STDERR, json_encode(['expected' => $expected, 'actual' => $observation], JSON_THROW_ON_ERROR) . "\n");
    throw new RuntimeException('direct priority-bucket unset oracle mismatch');
}

echo json_encode([
    'status' => 'passed',
    'scenario' => 'wp_action_ref_array_unset_bucket_native_oracle',
    'wordpress' => '7.1.3',
    'source_sha256' => [
        'wp-includes/plugin.php' => $pinned[$root . '/wp-includes/plugin.php'],
        'wp-includes/class-wp-hook.php' => $pinned[$root . '/wp-includes/class-wp-hook.php'],
    ],
    'observation' => $observation,
], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
