<?php
declare(strict_types=1);

$root = $argv[1] ?? throw new RuntimeException('missing pinned WordPress root');
$oracleMode = $argv[2] ?? '';
$emptyBucketMode = $oracleMode === 'action-ref-array-empty-bucket';
$emptyBucketReinsertMode = $oracleMode === 'action-ref-array-empty-bucket-reinsert';
$emptyBucketAddActionMode = $oracleMode === 'action-ref-array-empty-bucket-add-action';
$globalReplaceSecondDispatchMode = $oracleMode === 'action-ref-array-global-replace-second-dispatch';
$GLOBALS['rp_action_ref_array_scenario'] = $oracleMode !== '' ? $oracleMode : 'action-ref-array';
$GLOBALS['rp_action_ref_array_global_replace_second_dispatch_mode'] = $globalReplaceSecondDispatchMode;
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

function rp_action_ref_array_table_snapshot(WP_Hook $hook): array
{
    $rows = [];
    foreach ($hook->callbacks as $priority => $bucket) {
        foreach ($bucket as $uid => $entry) {
            $rows[] = [
                'priority' => (int) $priority,
                'uid' => (string) $uid,
                'accepted_args' => (int) $entry['accepted_args'],
                'function' => $entry['function'],
            ];
        }
    }
    return $rows;
}

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
    $hook = $GLOBALS['rp_action_ref_array_selected'];
    if ($GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-empty-bucket') {
        if (!isset($hook->callbacks[8]['rp_action_ref_array_pending'])) {
            throw new RuntimeException('wp_action_ref_array_oracle_empty_bucket_missing_pending');
        }
        $hook->callbacks[8] = [];
        $priorities = (new ReflectionProperty(WP_Hook::class, 'priorities'))->getValue($hook);
        $bucketExists = array_key_exists(8, $hook->callbacks);
        $bucketUids = array_keys($hook->callbacks[8] ?? []);
        $priorityStillScheduled = in_array(8, $priorities, true);
        $GLOBALS['rp_action_ref_array_events'][array_key_last($GLOBALS['rp_action_ref_array_events'])]
            ['direct_table_edit'] = [
                'operation' => 'replace_future_bucket_with_empty_array',
                'removed_uid' => 'rp_action_ref_array_pending',
                'priority' => 8,
                'bucket_exists' => $bucketExists,
                'bucket_uids' => $bucketUids,
                'priority_still_scheduled' => $priorityStillScheduled,
            ];
        if (!$bucketExists || $bucketUids !== [] || !$priorityStillScheduled) {
            throw new RuntimeException('wp_action_ref_array_oracle_empty_bucket_state');
        }
        return 'ignored-first-action-result';
    }
    if ($GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-empty-bucket-reinsert') {
        if (!isset($hook->callbacks[8]['rp_action_ref_array_pending'])) {
            throw new RuntimeException('wp_action_ref_array_oracle_reinsert_missing_pending');
        }
        $hook->callbacks[8] = [];
        $hook->callbacks[8]['rp_action_ref_array_added'] = [
            'function' => 'rp_action_ref_array_added',
            'accepted_args' => 2,
        ];
        $priorities = (new ReflectionProperty(WP_Hook::class, 'priorities'))->getValue($hook);
        $GLOBALS['rp_action_ref_array_events'][array_key_last($GLOBALS['rp_action_ref_array_events'])]
            ['direct_table_edit'] = [
                'operation' => 'empty_bucket_then_insert_callback_directly',
                'removed_uid' => 'rp_action_ref_array_pending',
                'inserted_uid' => 'rp_action_ref_array_added',
                'priority' => 8,
                'bucket_uids' => array_keys($hook->callbacks[8]),
                'priority_still_scheduled' => in_array(8, $priorities, true),
            ];
        if (array_keys($hook->callbacks[8]) !== ['rp_action_ref_array_added']
            || !in_array(8, $priorities, true)
        ) {
            throw new RuntimeException('wp_action_ref_array_oracle_reinsert_state');
        }
        return 'ignored-first-action-result';
    }
    if ($GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-empty-bucket-add-action') {
        if (!isset($hook->callbacks[8]['rp_action_ref_array_pending'])) {
            throw new RuntimeException('wp_action_ref_array_oracle_add_action_missing_pending');
        }
        $hook->callbacks[8] = [];
        add_action('rp-probe-action-ref-array', 'rp_action_ref_array_added', 8, 2);
        $priorities = (new ReflectionProperty(WP_Hook::class, 'priorities'))->getValue($hook);
        $bucketUids = array_keys($hook->callbacks[8] ?? []);
        $GLOBALS['rp_action_ref_array_events'][array_key_last($GLOBALS['rp_action_ref_array_events'])]
            ['direct_table_edit'] = [
                'operation' => 'empty_bucket_then_add_action',
                'removed_uid' => 'rp_action_ref_array_pending',
                'inserted_uid' => 'rp_action_ref_array_added',
                'priority' => 8,
                'bucket_uids' => $bucketUids,
                'priority_still_scheduled' => in_array(8, $priorities, true),
                'priority_occurrences' => count(array_keys($priorities, 8, true)),
            ];
        if ($bucketUids !== ['rp_action_ref_array_added']
            || !in_array(8, $priorities, true)
            || count(array_keys($priorities, 8, true)) !== 1
        ) {
            throw new RuntimeException('wp_action_ref_array_oracle_add_action_state');
        }
        return 'ignored-first-action-result';
    }
    if (in_array($GLOBALS['rp_action_ref_array_scenario'], [
        'action-ref-array-global-replace',
        'action-ref-array-global-replace-second-dispatch',
    ], true)) {
        if (!isset($hook->callbacks[8]['rp_action_ref_array_pending'])) {
            throw new RuntimeException('wp_action_ref_array_oracle_global_replace_missing_pending');
        }
        $hook->callbacks[8] = [];
        $replacement = new WP_Hook();
        $GLOBALS['wp_filter']['rp-probe-action-ref-array'] = $replacement;
        add_action('rp-probe-action-ref-array', 'rp_action_ref_array_global_marker', 3, 2);
        $hook->callbacks[8]['rp_action_ref_array_added'] = [
            'function' => 'rp_action_ref_array_added',
            'accepted_args' => 2,
        ];
        unset($hook->callbacks[8]['rp_action_ref_array_pending']);
        $priorities = (new ReflectionProperty(WP_Hook::class, 'priorities'))->getValue($hook);
        $replacementPriorities = (new ReflectionProperty(WP_Hook::class, 'priorities'))->getValue($replacement);
        $GLOBALS['rp_action_ref_array_events'][array_key_last($GLOBALS['rp_action_ref_array_events'])]
            ['direct_table_edit'] = [
                'operation' => 'replace_global_wp_hook_while_old_receiver_runs',
                'active_hook_is_global' => $hook === $GLOBALS['wp_filter']['rp-probe-action-ref-array'],
                'old_priorities' => $priorities,
                'global_priorities' => $replacementPriorities,
                'old_added_priority' => $hook->has_filter('rp-probe-action-ref-array', 'rp_action_ref_array_added'),
                'global_marker_priority' => has_action('rp-probe-action-ref-array', 'rp_action_ref_array_global_marker'),
                'global_added_priority' => has_action('rp-probe-action-ref-array', 'rp_action_ref_array_added'),
                'doing_action_from_stack' => doing_action('rp-probe-action-ref-array'),
                'old_hook_private_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
                'global_hook_private_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($replacement),
            ];
        if ($hook === $replacement
            || $priorities !== [1, 2, 5, 8, 10]
            || $replacementPriorities !== [3]
            || $hook->has_filter('rp-probe-action-ref-array', 'rp_action_ref_array_added') !== 8
            || has_action('rp-probe-action-ref-array', 'rp_action_ref_array_global_marker') !== 3
            || has_action('rp-probe-action-ref-array', 'rp_action_ref_array_added') !== false
            || !doing_action('rp-probe-action-ref-array')
            || !(new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook)
            || (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($replacement)
        ) {
            throw new RuntimeException('wp_action_ref_array_oracle_global_replace_state');
        }
        return 'ignored-first-action-result';
    }
    if (isset($hook->callbacks[8]['rp_action_ref_array_added'])) {
        throw new RuntimeException('wp_action_ref_array_oracle_direct_add_duplicate_entry');
    }
    $hook->callbacks[8]['rp_action_ref_array_added'] = [
        'function' => 'rp_action_ref_array_added',
        'accepted_args' => 2,
    ];
    if (!isset($hook->callbacks[8]['rp_action_ref_array_pending'])) {
        throw new RuntimeException('wp_action_ref_array_oracle_direct_unset_missing_entry');
    }
    unset($hook->callbacks[8]['rp_action_ref_array_pending']);
    $priorities = (new ReflectionProperty(WP_Hook::class, 'priorities'))->getValue($hook);
    $bucketUids = array_keys($hook->callbacks[8] ?? []);
    $priorityStillScheduled = in_array(8, $priorities, true);
    $GLOBALS['rp_action_ref_array_events'][array_key_last($GLOBALS['rp_action_ref_array_events'])]
        ['direct_table_edit'] = [
            'operation' => 'replace_bucket_entries_directly',
            'inserted_uid' => 'rp_action_ref_array_added',
            'removed_uid' => 'rp_action_ref_array_pending',
            'priority' => 8,
            'bucket_uids' => $bucketUids,
            'priority_still_scheduled' => $priorityStillScheduled,
        ];
    if ($bucketUids !== ['rp_action_ref_array_added'] || !$priorityStillScheduled) {
        throw new RuntimeException('wp_action_ref_array_oracle_direct_unset_state');
    }
    return 'ignored-first-action-result';
}

function rp_action_ref_array_pending(string &$value, string &$extra): void
{
    throw new RuntimeException('wp_action_ref_array_oracle_removed_callback_invoked');
}

function rp_action_ref_array_global_marker(string &$value, string &$extra): void
{
    if (!$GLOBALS['rp_action_ref_array_global_replace_second_dispatch_mode']
        || did_action('rp-probe-action-ref-array') !== 2
        || $GLOBALS['rp_action_ref_array_selected'] !== $GLOBALS['wp_filter']['rp-probe-action-ref-array']
    ) {
        throw new RuntimeException('wp_action_ref_array_oracle_global_marker_unexpected_dispatch');
    }
    $hook = $GLOBALS['rp_action_ref_array_selected'];
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'global_marker', 'args' => [$value, $extra],
        'current_action' => current_action(), 'doing_action' => doing_action('rp-probe-action-ref-array'),
        'did_action' => did_action('rp-probe-action-ref-array'), 'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    $value = 'value-mutated-by-global-marker';
    $extra = 'extra-mutated-by-global-marker';
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
add_action($hookName, 'rp_action_ref_array_pending', 8, 2);
add_action($hookName, 'rp_action_ref_array_second', 10, 2);
$selected = $GLOBALS['wp_filter'][$hookName];
$GLOBALS['rp_action_ref_array_selected'] = $selected;
$originalSelected = $selected;
$value = 'seed';
$extra = 'left';
$args = [&$value, &$extra];
$result = do_action_ref_array($hookName, $args);
$firstValue = $value;
$firstExtra = $extra;
$replacement = null;
$secondResult = null;
if ($globalReplaceSecondDispatchMode) {
    $replacement = $GLOBALS['wp_filter'][$hookName] ?? null;
    if (!($replacement instanceof WP_Hook) || $replacement === $selected
        || (new ReflectionProperty(WP_Hook::class, 'priorities'))->getValue($replacement) !== [3]
    ) {
        throw new RuntimeException('wp_action_ref_array_oracle_global_replace_second_hook');
    }
    $GLOBALS['rp_action_ref_array_selected'] = $replacement;
    $secondValue = 'second-seed';
    $secondExtra = 'second-extra';
    $secondArgs = [&$secondValue, &$secondExtra];
    $secondResult = do_action_ref_array($hookName, $secondArgs);
    if ($secondResult !== null || $secondValue !== 'value-mutated-by-global-marker'
        || $secondExtra !== 'extra-mutated-by-global-marker'
    ) {
        throw new RuntimeException('wp_action_ref_array_oracle_global_replace_second_result');
    }
}
$activeSelected = $replacement ?? $selected;
$finalValue = $globalReplaceSecondDispatchMode ? $secondValue : $value;
$finalExtra = $globalReplaceSecondDispatchMode ? $secondExtra : $extra;
$observation = [
    'events' => $GLOBALS['rp_action_ref_array_events'],
    'result' => $result,
    'caller_value' => $finalValue,
    'caller_extra' => $finalExtra,
    'did_action' => did_action($hookName),
    'current_action' => current_action(),
    'doing_action' => doing_action(),
    'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($activeSelected),
    'current_priority' => $activeSelected->current_priority(),
];
$resetHookState = ['nesting_level' => 0, 'iterations' => [], 'current_priority' => [], 'doing_action' => false];
if ($globalReplaceSecondDispatchMode) {
    $observation['second_result'] = $secondResult;
    $observation['first_dispatch'] = [
        'caller_value' => $firstValue,
        'caller_extra' => $firstExtra,
        'did_action' => 1,
        'old_hook_state' => [
            'nesting_level' => (new ReflectionProperty(WP_Hook::class, 'nesting_level'))->getValue($originalSelected),
            'iterations' => (new ReflectionProperty(WP_Hook::class, 'iterations'))->getValue($originalSelected),
            'current_priority' => (new ReflectionProperty(WP_Hook::class, 'current_priority'))->getValue($originalSelected),
            'doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($originalSelected),
        ],
    ];
    $observation['replacement_state'] = [
        'priorities' => (new ReflectionProperty(WP_Hook::class, 'priorities'))->getValue($replacement),
        'rows' => array_map(static fn (array $row): array => [
            'priority' => $row['priority'], 'uid' => $row['uid'],
            'accepted_args' => $row['accepted_args'], 'function' => is_string($row['function']) ? $row['function'] : null,
        ], rp_action_ref_array_table_snapshot($replacement)),
        'hook_state' => [
            'nesting_level' => (new ReflectionProperty(WP_Hook::class, 'nesting_level'))->getValue($replacement),
            'iterations' => (new ReflectionProperty(WP_Hook::class, 'iterations'))->getValue($replacement),
            'current_priority' => (new ReflectionProperty(WP_Hook::class, 'current_priority'))->getValue($replacement),
            'doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($replacement),
        ],
    ];
}
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
            'direct_table_edit' => [
                'operation' => 'replace_bucket_entries_directly',
                'inserted_uid' => 'rp_action_ref_array_added',
                'removed_uid' => 'rp_action_ref_array_pending',
                'priority' => 8,
                'bucket_uids' => ['rp_action_ref_array_added'],
                'priority_still_scheduled' => true,
            ],
        ],
        [
            'callback' => 'added', 'args' => ['value-mutated-by-first', 'extra-mutated-by-first'],
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 8, 'wp_hook_doing_action' => true,
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
if (in_array($GLOBALS['rp_action_ref_array_scenario'], [
    'action-ref-array-global-replace',
    'action-ref-array-global-replace-second-dispatch',
], true)) {
    $expected['events'][3]['direct_table_edit'] = [
        'operation' => 'replace_global_wp_hook_while_old_receiver_runs',
        'active_hook_is_global' => false,
        'old_priorities' => [1, 2, 5, 8, 10],
        'global_priorities' => [3],
        'old_added_priority' => 8,
        'global_marker_priority' => 3,
        'global_added_priority' => false,
        'doing_action_from_stack' => true,
        'old_hook_private_doing_action' => true,
        'global_hook_private_doing_action' => false,
    ];
    if ($globalReplaceSecondDispatchMode) {
        $expected['events'][] = [
            'callback' => 'all', 'hook' => $hookName,
            'args' => [$hookName, ['second-seed', 'second-extra']],
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 2,
        ];
        $expected['events'][] = [
            'callback' => 'global_marker', 'args' => ['second-seed', 'second-extra'],
            'current_action' => $hookName, 'doing_action' => true, 'did_action' => 2,
            'current_priority' => 3, 'wp_hook_doing_action' => true,
        ];
        $expected['caller_value'] = 'value-mutated-by-global-marker';
        $expected['caller_extra'] = 'extra-mutated-by-global-marker';
        $expected['did_action'] = 2;
        $expected['second_result'] = null;
        $expected['first_dispatch'] = [
            'caller_value' => 'value-mutated-by-added',
            'caller_extra' => 'extra-mutated-by-second',
            'did_action' => 1,
            'old_hook_state' => $resetHookState,
        ];
        $expected['replacement_state'] = [
            'priorities' => [3],
            'rows' => [[
                'priority' => 3, 'uid' => 'rp_action_ref_array_global_marker',
                'accepted_args' => 2, 'function' => 'rp_action_ref_array_global_marker',
            ]],
            'hook_state' => $resetHookState,
        ];
    }
} elseif ($emptyBucketAddActionMode) {
    $expected['events'][3]['direct_table_edit'] = [
        'operation' => 'empty_bucket_then_add_action',
        'removed_uid' => 'rp_action_ref_array_pending',
        'inserted_uid' => 'rp_action_ref_array_added',
        'priority' => 8,
        'bucket_uids' => ['rp_action_ref_array_added'],
        'priority_still_scheduled' => true,
        'priority_occurrences' => 1,
    ];
} elseif ($emptyBucketReinsertMode) {
    $expected['events'][3]['direct_table_edit'] = [
        'operation' => 'empty_bucket_then_insert_callback_directly',
        'removed_uid' => 'rp_action_ref_array_pending',
        'inserted_uid' => 'rp_action_ref_array_added',
        'priority' => 8,
        'bucket_uids' => ['rp_action_ref_array_added'],
        'priority_still_scheduled' => true,
    ];
} elseif ($emptyBucketMode) {
    $expected['events'] = array_slice($expected['events'], 0, 4);
    $expected['events'][3]['direct_table_edit'] = [
        'operation' => 'replace_future_bucket_with_empty_array',
        'removed_uid' => 'rp_action_ref_array_pending',
        'priority' => 8,
        'bucket_exists' => true,
        'bucket_uids' => [],
        'priority_still_scheduled' => true,
    ];
    $expected['events'][] = [
        'callback' => 'second',
        'args' => ['value-mutated-by-first', 'extra-mutated-by-first'],
        'current_action' => $hookName,
        'doing_action' => true,
        'did_action' => 1,
        'current_priority' => 10,
        'wp_hook_doing_action' => true,
    ];
    $expected['caller_value'] = 'value-mutated-by-first';
}
if ($observation !== $expected) {
    fwrite(STDERR, json_encode(['expected' => $expected, 'actual' => $observation], JSON_THROW_ON_ERROR) . "\n");
    throw new RuntimeException('wp_action_ref_array_oracle_mismatch');
}

echo json_encode([
    'status' => 'passed',
    'scenario' => $globalReplaceSecondDispatchMode
        ? 'wp_action_ref_array_global_replace_second_dispatch_native_oracle'
        : ($GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-global-replace'
        ? 'wp_action_ref_array_global_replace_native_oracle'
        : ($emptyBucketAddActionMode
        ? 'wp_action_ref_array_empty_bucket_add_action_native_oracle'
        : ($emptyBucketReinsertMode
        ? 'wp_action_ref_array_empty_bucket_reinsert_native_oracle'
        : ($emptyBucketMode ? 'wp_action_ref_array_empty_bucket_native_oracle' : 'wp_action_ref_array_native_oracle')))),
    'wordpress' => '7.1.3',
    'source_sha256' => [
        'wp-includes/plugin.php' => $pinned[$root . '/wp-includes/plugin.php'],
        'wp-includes/class-wp-hook.php' => $pinned[$root . '/wp-includes/class-wp-hook.php'],
    ],
    'observation' => $observation,
], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
