<?php
declare(strict_types=1);

fwrite(STDERR, "RP_ACTION_REF_ARRAY_PHASE=driver_start\n");
require_once __DIR__ . '/bridge_driver.php';
require_once __DIR__ . '/plugin.php';
fwrite(STDERR, "RP_ACTION_REF_ARRAY_PHASE=plugin_loaded\n");

use RecastPress\V06\Handles\HandleRegistry;

const RP_ACTION_REF_ARRAY_REQUEST = '81';
const RP_ACTION_REF_ARRAY_TARGET = 'wp-action-ref-array-1';
const RP_ACTION_REF_ARRAY_NAME = 'rp-probe-action-ref-array';
$GLOBALS['rp_action_ref_array_scenario'] = $argv[1] ?? 'action-ref-array';

function rp_action_ref_array_handles_equal(array $left, array $right): bool
{
    ksort($left);
    ksort($right);
    return $left === $right;
}

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

function rp_action_ref_array_fingerprint(array $rows): string
{
    $projection = array_map(
        static fn (array $row): array => [
            $row['priority'], $row['uid'], $row['accepted_args'], $row['function'],
            $row['handle']['request'], $row['handle']['kind'], $row['handle']['slot'],
            $row['handle']['generation'], $row['handle']['token'],
        ],
        $rows,
    );
    return hash('sha256', json_encode($projection, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR));
}

function recastpress_wp_should_dispatch_selected_action(string $hookName, object $hook): bool
{
    return $hookName === RP_ACTION_REF_ARRAY_NAME
        && isset($GLOBALS['rp_action_ref_array_token_by_object'][$hook])
        && $GLOBALS['rp_action_ref_array_token_by_object'][$hook] === RP_ACTION_REF_ARRAY_TARGET
        && $hook === $GLOBALS['rp_action_ref_array_selected_object'];
}

function recastpress_wp_dispatch_selected_action(
    string $hookName,
    WP_Hook $hook,
    array $args,
    int $nestingLevel,
    Closure $sync,
): null {
    $registry = $GLOBALS['rp_action_ref_array_registry'];
    $wire = $GLOBALS['rp_action_ref_array_wire'];
    if (!array_is_list($args) || count($args) !== 2) {
        throw new RuntimeException('wp_action_ref_array_frame_shape');
    }
    $pass = did_action($hookName);
    if (!in_array($pass, [1, 2], true)
        || ($pass === 2 && $GLOBALS['rp_action_ref_array_scenario'] !== 'action-ref-array-global-replace-second-dispatch')
    ) {
        throw new RuntimeException('wp_action_ref_array_dispatch_pass');
    }
    $GLOBALS['rp_action_ref_array_selected_object'] = $hook;
    $rows = rp_action_ref_array_table_snapshot($hook);
    $expectedRows = $rows;
    $argumentFrame = $registry->retainArgument($args);
    $GLOBALS['rp_action_ref_array_argument_frames'][$pass] = $argumentFrame;
    $exported = [];
    $handles = [];
    $acceptedArgsByUid = [];
    $priorityByUid = [];
    foreach ($rows as $row) {
        $handle = $registry->retainCallable($row['function']);
        $handles[$row['uid']] = $handle;
        $acceptedArgsByUid[$row['uid']] = $row['accepted_args'];
        $priorityByUid[$row['uid']] = $row['priority'];
        $exported[] = [
            'priority' => $row['priority'],
            'uid' => $row['uid'],
            'accepted_args' => $row['accepted_args'],
            'function' => is_string($row['function']) ? $row['function'] : null,
            'handle' => $handle,
        ];
    }
    $GLOBALS['rp_action_ref_array_handles_by_pass'][$pass] = $handles;
    $GLOBALS['rp_action_ref_array_released_by_pass'][$pass] = [];
    $invocationCount = 0;

    $wire->send('action_dispatch_start', [
        'generation' => RP_ACTION_REF_ARRAY_REQUEST,
        'target' => RP_ACTION_REF_ARRAY_TARGET,
        'hook' => $hookName,
        'argument_frame' => $argumentFrame,
        'argument_count' => count($args),
        'rows' => $exported,
        'table_fingerprint' => rp_action_ref_array_fingerprint($exported),
    ]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'release_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['handle'], 'wp_action_ref_array_dispatch_release');
            $releasedHandle = $frame['payload']['handle'];
            if (($releasedHandle['kind'] ?? null) !== 'callable') {
                throw new RuntimeException('wp_action_ref_array_dispatch_release_kind');
            }
            $releasedUid = null;
            foreach ($GLOBALS['rp_action_ref_array_handles_by_pass'] as $handlePass => $passHandles) {
                foreach ($passHandles as $uid => $knownHandle) {
                    if (rp_action_ref_array_handles_equal($knownHandle, $releasedHandle)) {
                        $releasedUid = [$handlePass, $uid];
                        break 2;
                    }
                }
            }
            if ($releasedUid === null
                || isset($GLOBALS['rp_action_ref_array_released_by_pass'][$releasedUid[0]][$releasedUid[1]])
            ) {
                throw new RuntimeException('wp_action_ref_array_dispatch_release_identity');
            }
            $registry->release($releasedHandle);
            $GLOBALS['rp_action_ref_array_released_by_pass'][$releasedUid[0]][$releasedUid[1]] = true;
            $wire->send('release_ack', ['handle' => $releasedHandle]);
            continue;
        }
        if ($frame['kind'] === 'invoke_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, [
                'target', 'uid', 'handle', 'priority', 'call_mode', 'args', 'argument_frame',
            ], 'wp_action_ref_array_invoke');
            $payload = $frame['payload'];
            if ($payload['target'] !== RP_ACTION_REF_ARRAY_TARGET
                || !isset($handles[$payload['uid']])
                || !rp_action_ref_array_handles_equal($handles[$payload['uid']], $payload['handle'])
                || !rp_action_ref_array_handles_equal($argumentFrame, $payload['argument_frame'])
                || !is_int($payload['priority'])
                || $payload['priority'] !== $priorityByUid[$payload['uid']]
                || !in_array($payload['call_mode'], ['zero', 'array'], true)
                || !is_array($payload['args'])
                || count($payload['args']) !== $acceptedArgsByUid[$payload['uid']]
                || ($payload['call_mode'] === 'zero' && $acceptedArgsByUid[$payload['uid']] !== 0)
                || ($payload['call_mode'] === 'array' && $acceptedArgsByUid[$payload['uid']] === 0)
            ) {
                throw new RuntimeException('wp_action_ref_array_invoke_identity');
            }
            if (!$sync('set_priority', $payload['priority'])) {
                throw new RuntimeException('wp_action_ref_array_priority_sync');
            }
            $callback = $registry->resolve($payload['handle'], 'callable');
            $arguments = $registry->resolve($argumentFrame, 'argument');
            if (!array_is_list($arguments)) {
                throw new RuntimeException('wp_action_ref_array_frame_not_list');
            }
            $callbackArgs = [];
            foreach ($payload['args'] as $position => $argument) {
                $keys = is_array($argument) ? array_keys($argument) : [];
                sort($keys);
                if (!is_array($argument)
                    || $keys !== ['handle', 'index', 'type']
                    || $argument['type'] !== 'argument_frame_slot'
                    || !is_int($argument['index'])
                    || !array_key_exists($argument['index'], $arguments)
                    || !rp_action_ref_array_handles_equal($argument['handle'], $argumentFrame)
                ) {
                    throw new RuntimeException('wp_action_ref_array_slot_descriptor');
                }
                if ($argument['index'] !== $position) {
                    throw new RuntimeException('wp_action_ref_array_slot_order');
                }
                $callbackArgs[] = &$arguments[$argument['index']];
            }
            if ($payload['call_mode'] === 'zero') {
                call_user_func($callback);
            } else {
                call_user_func_array($callback, $callbackArgs);
            }
            unset($callbackArgs, $arguments);
            if ($payload['uid'] === 'rp_action_ref_array_first') {
                $current = rp_action_ref_array_table_snapshot($hook);
                $newRows = array_values(array_filter(
                    $current,
                    static fn (array $row): bool => !array_key_exists($row['uid'], $handles),
                ));
                if ($GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-empty-bucket') {
                    if ($newRows !== []
                        || !isset($hook->callbacks[8])
                        || !is_array($hook->callbacks[8])
                        || array_keys($hook->callbacks[8]) !== []
                        || !in_array(8, (new ReflectionProperty(WP_Hook::class, 'priorities'))->getValue($hook), true)
                    ) {
                        throw new RuntimeException('wp_action_ref_array_empty_bucket_state');
                    }
                    $expectedRows = $current;
                } else {
                    if (count($newRows) !== 1
                        || $newRows[0]['priority'] !== 8
                        || $newRows[0]['uid'] !== 'rp_action_ref_array_added'
                        || $newRows[0]['accepted_args'] !== 2
                        || $newRows[0]['function'] !== 'rp_action_ref_array_added'
                    ) {
                        throw new RuntimeException('wp_action_ref_array_added_callback_identity');
                    }
                    $addedHandle = $registry->retainCallable($newRows[0]['function']);
                    $handles[$newRows[0]['uid']] = $addedHandle;
                    $acceptedArgsByUid[$newRows[0]['uid']] = $newRows[0]['accepted_args'];
                    $priorityByUid[$newRows[0]['uid']] = $newRows[0]['priority'];
                    $added = [
                        'priority' => $newRows[0]['priority'],
                        'uid' => $newRows[0]['uid'],
                        'accepted_args' => $newRows[0]['accepted_args'],
                        'function' => $newRows[0]['function'],
                        'handle' => $addedHandle,
                    ];
                    $exported[] = $added;
                    // Preserve insertion order within a priority in the wire fingerprint.
                    usort(
                        $exported,
                        static fn (array $left, array $right): int => $left['priority'] <=> $right['priority'],
                    );
                    $expectedRows = $current;
                    $GLOBALS['rp_action_ref_array_handles_by_pass'][$pass] = $handles;
                    $wire->send('action_callback_added', [
                        'target' => RP_ACTION_REF_ARRAY_TARGET,
                        'parent_uid' => $payload['uid'],
                        'row' => $added,
                        'table_fingerprint' => rp_action_ref_array_fingerprint($exported),
                    ]);
                    $ack = $wire->receive();
                    expect_parent_effect($ack, null);
                    expect_payload_keys($ack, ['target', 'uid', 'priority'], 'wp_action_ref_array_mutation_ack');
                    if ($ack['kind'] !== 'action_mutation_ack'
                        || $ack['payload']['target'] !== RP_ACTION_REF_ARRAY_TARGET
                        || $ack['payload']['uid'] !== $newRows[0]['uid']
                        || $ack['payload']['priority'] !== 8
                    ) {
                        throw new RuntimeException('wp_action_ref_array_mutation_ack_identity');
                    }
                }
                $removedRow = null;
                foreach ($exported as $candidate) {
                    if ($candidate['uid'] === 'rp_action_ref_array_pending') {
                        $removedRow = $candidate;
                        break;
                    }
                }
                $pendingStillRegistered = array_filter(
                    rp_action_ref_array_table_snapshot($hook),
                    static fn (array $candidate): bool => $candidate['uid'] === 'rp_action_ref_array_pending',
                );
                if (!is_array($removedRow) || $pendingStillRegistered !== []) {
                    throw new RuntimeException('wp_action_ref_array_remove_pending');
                }
                $exported = array_values(array_filter(
                    $exported,
                    static fn (array $candidate): bool => $candidate['uid'] !== 'rp_action_ref_array_pending',
                ));
                $wire->send('action_callback_removed', [
                    'target' => RP_ACTION_REF_ARRAY_TARGET,
                    'parent_uid' => $payload['uid'],
                    'row' => $removedRow,
                    'removed_handle' => $removedRow['handle'],
                    'table_fingerprint' => rp_action_ref_array_fingerprint($exported),
                ]);
                $removeAck = $wire->receive();
                expect_parent_effect($removeAck, null);
                expect_payload_keys($removeAck, ['target', 'uid', 'priority'], 'wp_action_ref_array_remove_ack');
                if ($removeAck['kind'] !== 'action_removal_ack'
                    || $removeAck['payload']['target'] !== RP_ACTION_REF_ARRAY_TARGET
                    || $removeAck['payload']['uid'] !== 'rp_action_ref_array_pending'
                    || $removeAck['payload']['priority'] !== 8
                ) {
                    throw new RuntimeException('wp_action_ref_array_remove_ack_identity');
                }
                $expectedRows = rp_action_ref_array_table_snapshot($hook);
            }
            $GLOBALS['rp_action_ref_array_invocations'][] = $payload['uid'];
            $invocationCount++;
            $wire->send('resume_value', [
                'target' => RP_ACTION_REF_ARRAY_TARGET,
                'uid' => $payload['uid'],
                'value' => null,
            ]);
            continue;
        }

        if ($frame['kind'] === 'action_complete') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['target', 'invocations'], 'wp_action_ref_array_complete');
            if ($frame['payload']['target'] !== RP_ACTION_REF_ARRAY_TARGET
                || $frame['payload']['invocations'] !== $invocationCount
                || !$sync('finish')
            ) {
                throw new RuntimeException('wp_action_ref_array_completion_state');
            }
            if (rp_action_ref_array_table_snapshot($hook) !== $expectedRows) {
                throw new RuntimeException('wp_action_ref_array_table_mutated');
            }
            $GLOBALS['rp_action_ref_array_imported_rows_by_pass'][$pass] = $exported;
            return null;
        }
        throw new RuntimeException('wp_action_ref_array_unexpected_frame');
    }
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
    $hook = $GLOBALS['rp_action_ref_array_selected_object'];
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'truncated', 'args' => $args, 'received_arg_count' => count($args),
        'current_action' => current_action(), 'doing_action' => doing_action(RP_ACTION_REF_ARRAY_NAME),
        'did_action' => did_action(RP_ACTION_REF_ARRAY_NAME), 'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    if ($extra !== []) {
        throw new RuntimeException('wp_action_ref_array_truncated_received_extra');
    }
}

function rp_action_ref_array_zero(): string
{
    $args = func_get_args();
    $hook = $GLOBALS['rp_action_ref_array_selected_object'];
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'zero', 'args' => $args, 'received_arg_count' => count($args),
        'current_action' => current_action(), 'doing_action' => doing_action(RP_ACTION_REF_ARRAY_NAME),
        'did_action' => did_action(RP_ACTION_REF_ARRAY_NAME), 'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    if ($args !== []) {
        throw new RuntimeException('wp_action_ref_array_zero_received_arguments');
    }
    return 'ignored-zero-action-result';
}

function rp_action_ref_array_first(string &$value, string &$extra): string
{
    $hook = $GLOBALS['rp_action_ref_array_selected_object'];
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'first', 'args' => [$value, $extra],
        'current_action' => current_action(), 'doing_action' => doing_action(RP_ACTION_REF_ARRAY_NAME),
        'did_action' => did_action(RP_ACTION_REF_ARRAY_NAME), 'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    $value = 'value-mutated-by-first';
    $extra = 'extra-mutated-by-first';
    if (isset($hook->callbacks[8]['rp_action_ref_array_added'])) {
        throw new RuntimeException('wp_action_ref_array_direct_add_duplicate_entry');
    }
    if ($GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-empty-bucket') {
        if (!isset($hook->callbacks[8]['rp_action_ref_array_pending'])) {
            throw new RuntimeException('wp_action_ref_array_empty_bucket_missing_pending');
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
            throw new RuntimeException('wp_action_ref_array_empty_bucket_state');
        }
        return 'ignored-first-action-result';
    }
    if ($GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-empty-bucket-reinsert') {
        if (!isset($hook->callbacks[8]['rp_action_ref_array_pending'])) {
            throw new RuntimeException('wp_action_ref_array_reinsert_missing_pending');
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
            throw new RuntimeException('wp_action_ref_array_reinsert_state');
        }
        return 'ignored-first-action-result';
    }
    if ($GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-empty-bucket-add-action') {
        if (!isset($hook->callbacks[8]['rp_action_ref_array_pending'])) {
            throw new RuntimeException('wp_action_ref_array_add_action_missing_pending');
        }
        $hook->callbacks[8] = [];
        add_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_added', 8, 2);
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
            throw new RuntimeException('wp_action_ref_array_add_action_state');
        }
        return 'ignored-first-action-result';
    }
    if (in_array($GLOBALS['rp_action_ref_array_scenario'], [
        'action-ref-array-global-replace',
        'action-ref-array-global-replace-second-dispatch',
    ], true)) {
        if (!isset($hook->callbacks[8]['rp_action_ref_array_pending'])) {
            throw new RuntimeException('wp_action_ref_array_global_replace_missing_pending');
        }
        $hook->callbacks[8] = [];
        $replacement = new WP_Hook();
        $GLOBALS['wp_filter'][RP_ACTION_REF_ARRAY_NAME] = $replacement;
        add_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_global_marker', 3, 2);
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
                'active_hook_is_global' => $hook === $GLOBALS['wp_filter'][RP_ACTION_REF_ARRAY_NAME],
                'old_priorities' => $priorities,
                'global_priorities' => $replacementPriorities,
                'old_added_priority' => $hook->has_filter(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_added'),
                'global_marker_priority' => has_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_global_marker'),
                'global_added_priority' => has_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_added'),
                'doing_action_from_stack' => doing_action(RP_ACTION_REF_ARRAY_NAME),
                'old_hook_private_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
                'global_hook_private_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($replacement),
            ];
        if ($hook === $replacement
            || $priorities !== [1, 2, 5, 8, 10]
            || $replacementPriorities !== [3]
            || $hook->has_filter(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_added') !== 8
            || has_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_global_marker') !== 3
            || has_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_added') !== false
            || !doing_action(RP_ACTION_REF_ARRAY_NAME)
            || !(new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook)
            || (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($replacement)
        ) {
            throw new RuntimeException('wp_action_ref_array_global_replace_state');
        }
        return 'ignored-first-action-result';
    }
    $hook->callbacks[8]['rp_action_ref_array_added'] = [
        'function' => 'rp_action_ref_array_added',
        'accepted_args' => 2,
    ];
    if (!isset($hook->callbacks[8]['rp_action_ref_array_pending'])) {
        throw new RuntimeException('wp_action_ref_array_direct_unset_missing_entry');
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
        throw new RuntimeException('wp_action_ref_array_direct_unset_state');
    }
    return 'ignored-first-action-result';
}

function rp_action_ref_array_pending(string &$value, string &$extra): void
{
    throw new RuntimeException('wp_action_ref_array_removed_callback_invoked');
}

function rp_action_ref_array_global_marker(string &$value, string &$extra): void
{
    if ($GLOBALS['rp_action_ref_array_scenario'] !== 'action-ref-array-global-replace-second-dispatch'
        || did_action(RP_ACTION_REF_ARRAY_NAME) !== 2
        || $GLOBALS['rp_action_ref_array_selected_object'] !== $GLOBALS['wp_filter'][RP_ACTION_REF_ARRAY_NAME]
    ) {
        throw new RuntimeException('wp_action_ref_array_global_marker_unexpected_dispatch');
    }
    $hook = $GLOBALS['rp_action_ref_array_selected_object'];
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'global_marker', 'args' => [$value, $extra],
        'current_action' => current_action(), 'doing_action' => doing_action(RP_ACTION_REF_ARRAY_NAME),
        'did_action' => did_action(RP_ACTION_REF_ARRAY_NAME), 'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    $value = 'value-mutated-by-global-marker';
    $extra = 'extra-mutated-by-global-marker';
}

function rp_action_ref_array_added(string &$value, string &$extra): void
{
    $hook = $GLOBALS['rp_action_ref_array_selected_object'];
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'added', 'args' => [$value, $extra],
        'current_action' => current_action(), 'doing_action' => doing_action(RP_ACTION_REF_ARRAY_NAME),
        'did_action' => did_action(RP_ACTION_REF_ARRAY_NAME), 'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    $value = 'value-mutated-by-added';
    $extra = 'extra-mutated-by-added';
}

function rp_action_ref_array_second(string &$value, string &$extra): string
{
    $hook = $GLOBALS['rp_action_ref_array_selected_object'];
    $GLOBALS['rp_action_ref_array_events'][] = [
        'callback' => 'second', 'args' => [$value, $extra],
        'current_action' => current_action(), 'doing_action' => doing_action(RP_ACTION_REF_ARRAY_NAME),
        'did_action' => did_action(RP_ACTION_REF_ARRAY_NAME), 'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    $extra = 'extra-mutated-by-second';
    return 'ignored-second-action-result';
}

try {
    fwrite(STDERR, "RP_ACTION_REF_ARRAY_PHASE=protocol_start\n");
    $wire = new BridgeProtocol();
    $registry = new HandleRegistry(RP_ACTION_REF_ARRAY_REQUEST, 32, 32);
    $GLOBALS['rp_action_ref_array_wire'] = $wire;
    $GLOBALS['rp_action_ref_array_registry'] = $registry;
    $GLOBALS['rp_action_ref_array_events'] = [];
    $GLOBALS['rp_action_ref_array_invocations'] = [];
    $GLOBALS['rp_action_ref_array_argument_frames'] = [];
    $GLOBALS['rp_action_ref_array_argument_frames_released'] = [];
    $GLOBALS['rp_action_ref_array_handles_by_pass'] = [];
    $GLOBALS['rp_action_ref_array_released_by_pass'] = [];
    $GLOBALS['rp_action_ref_array_imported_rows_by_pass'] = [];
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];

    add_action('all', 'rp_action_ref_array_all', 10, 2);
    add_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_truncated', 1, 1);
    add_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_zero', 2, 0);
    add_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_first', 5, 2);
    add_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_pending', 8, 2);
    add_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_second', 10, 2);
    $selected = $GLOBALS['wp_filter'][RP_ACTION_REF_ARRAY_NAME];
    $tokens = new WeakMap();
    $tokens[$selected] = RP_ACTION_REF_ARRAY_TARGET;
    $GLOBALS['rp_action_ref_array_token_by_object'] = $tokens;
    $GLOBALS['rp_action_ref_array_selected_object'] = $selected;

    $value = 'seed';
    $extra = 'left';
    $args = [&$value, &$extra];
    fwrite(STDERR, "RP_ACTION_REF_ARRAY_PHASE=do_action_ref_array_start\n");
    $result = do_action_ref_array(RP_ACTION_REF_ARRAY_NAME, $args);
    fwrite(STDERR, "RP_ACTION_REF_ARRAY_PHASE=do_action_ref_array_done\n");
    $globalReplaceSecondDispatchMode = $GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-global-replace-second-dispatch';
    $firstValue = $value;
    $firstExtra = $extra;
    $replacement = null;
    if ($globalReplaceSecondDispatchMode) {
        if ($firstValue !== 'value-mutated-by-added' || $firstExtra !== 'extra-mutated-by-second') {
            throw new RuntimeException('wp_action_ref_array_global_replace_first_refs');
        }
        $replacement = $GLOBALS['wp_filter'][RP_ACTION_REF_ARRAY_NAME] ?? null;
        if (!($replacement instanceof WP_Hook) || $replacement === $selected
            || rp_action_ref_array_table_snapshot($replacement) === []
        ) {
            throw new RuntimeException('wp_action_ref_array_global_replace_second_hook');
        }
        $tokens[$replacement] = RP_ACTION_REF_ARRAY_TARGET;
        $GLOBALS['rp_action_ref_array_selected_object'] = $replacement;
        $secondValue = 'second-seed';
        $secondExtra = 'second-extra';
        $secondArgs = [&$secondValue, &$secondExtra];
        $secondResult = do_action_ref_array(RP_ACTION_REF_ARRAY_NAME, $secondArgs);
        if ($secondResult !== null
            || $secondValue !== 'value-mutated-by-global-marker'
            || $secondExtra !== 'extra-mutated-by-global-marker'
        ) {
            throw new RuntimeException('wp_action_ref_array_global_replace_second_result');
        }
    }
    $finalValue = $globalReplaceSecondDispatchMode ? $secondValue : $value;
    $finalExtra = $globalReplaceSecondDispatchMode ? $secondExtra : $extra;
    $emptyBucketMode = $GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-empty-bucket';
    $emptyBucketAddActionMode = $GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-empty-bucket-add-action';
    if ($result !== null
        || $finalValue !== ($globalReplaceSecondDispatchMode ? 'value-mutated-by-global-marker' : ($emptyBucketMode ? 'value-mutated-by-first' : 'value-mutated-by-added'))
        || $finalExtra !== ($globalReplaceSecondDispatchMode ? 'extra-mutated-by-global-marker' : 'extra-mutated-by-second')
        || current_action() !== false
        || current_filter() !== false
        || doing_action()
        || did_action(RP_ACTION_REF_ARRAY_NAME) !== ($globalReplaceSecondDispatchMode ? 2 : 1)
        || did_filter(RP_ACTION_REF_ARRAY_NAME) !== 0
        || $selected->current_priority() !== false
        || (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($selected) !== false
        || ($globalReplaceSecondDispatchMode && ($replacement->current_priority() !== false
            || (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($replacement) !== false
            || (new ReflectionProperty(WP_Hook::class, 'nesting_level'))->getValue($selected) !== 0
            || (new ReflectionProperty(WP_Hook::class, 'iterations'))->getValue($selected) !== []
            || (new ReflectionProperty(WP_Hook::class, 'nesting_level'))->getValue($replacement) !== 0
            || (new ReflectionProperty(WP_Hook::class, 'iterations'))->getValue($replacement) !== []))
    ) {
        throw new RuntimeException('wp_action_ref_array_final_observation');
    }
    $events = $GLOBALS['rp_action_ref_array_events'];
    $expected = [
        [
            'callback' => 'all', 'hook' => RP_ACTION_REF_ARRAY_NAME,
            'args' => [RP_ACTION_REF_ARRAY_NAME, ['seed', 'left']],
            'current_action' => RP_ACTION_REF_ARRAY_NAME, 'doing_action' => true, 'did_action' => 1,
        ],
        [
            'callback' => 'truncated', 'args' => ['seed'], 'received_arg_count' => 1,
            'current_action' => RP_ACTION_REF_ARRAY_NAME, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 1, 'wp_hook_doing_action' => true,
        ],
        [
            'callback' => 'zero', 'args' => [], 'received_arg_count' => 0,
            'current_action' => RP_ACTION_REF_ARRAY_NAME, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 2, 'wp_hook_doing_action' => true,
        ],
        [
            'callback' => 'first', 'args' => ['seed', 'left'],
            'current_action' => RP_ACTION_REF_ARRAY_NAME, 'doing_action' => true, 'did_action' => 1,
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
            'current_action' => RP_ACTION_REF_ARRAY_NAME, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 8, 'wp_hook_doing_action' => true,
        ],
        [
            'callback' => 'second', 'args' => ['value-mutated-by-added', 'extra-mutated-by-added'],
            'current_action' => RP_ACTION_REF_ARRAY_NAME, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 10, 'wp_hook_doing_action' => true,
        ],
    ];
    if (in_array($GLOBALS['rp_action_ref_array_scenario'], [
        'action-ref-array-global-replace',
        'action-ref-array-global-replace-second-dispatch',
    ], true)) {
        $expected[3]['direct_table_edit'] = [
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
        if ($GLOBALS['wp_filter'][RP_ACTION_REF_ARRAY_NAME] === $selected) {
            throw new RuntimeException('wp_action_ref_array_global_object_not_replaced');
        }
        if ($globalReplaceSecondDispatchMode) {
            $expected[] = [
                'callback' => 'all', 'hook' => RP_ACTION_REF_ARRAY_NAME,
                'args' => [RP_ACTION_REF_ARRAY_NAME, ['second-seed', 'second-extra']],
                'current_action' => RP_ACTION_REF_ARRAY_NAME, 'doing_action' => true, 'did_action' => 2,
            ];
            $expected[] = [
                'callback' => 'global_marker', 'args' => ['second-seed', 'second-extra'],
                'current_action' => RP_ACTION_REF_ARRAY_NAME, 'doing_action' => true, 'did_action' => 2,
                'current_priority' => 3, 'wp_hook_doing_action' => true,
            ];
        }
    } elseif ($GLOBALS['rp_action_ref_array_scenario'] === 'action-ref-array-empty-bucket-reinsert') {
        $expected[3]['direct_table_edit'] = [
            'operation' => 'empty_bucket_then_insert_callback_directly',
            'removed_uid' => 'rp_action_ref_array_pending',
            'inserted_uid' => 'rp_action_ref_array_added',
            'priority' => 8,
            'bucket_uids' => ['rp_action_ref_array_added'],
            'priority_still_scheduled' => true,
        ];
    } elseif ($emptyBucketAddActionMode) {
        $expected[3]['direct_table_edit'] = [
            'operation' => 'empty_bucket_then_add_action',
            'removed_uid' => 'rp_action_ref_array_pending',
            'inserted_uid' => 'rp_action_ref_array_added',
            'priority' => 8,
            'bucket_uids' => ['rp_action_ref_array_added'],
            'priority_still_scheduled' => true,
            'priority_occurrences' => 1,
        ];
    } elseif ($emptyBucketMode) {
        $expected = array_slice($expected, 0, 4);
        $expected[3]['direct_table_edit'] = [
            'operation' => 'replace_future_bucket_with_empty_array',
            'removed_uid' => 'rp_action_ref_array_pending',
            'priority' => 8,
            'bucket_exists' => true,
            'bucket_uids' => [],
            'priority_still_scheduled' => true,
        ];
        $expected[] = [
            'callback' => 'second',
            'args' => ['value-mutated-by-first', 'extra-mutated-by-first'],
            'current_action' => RP_ACTION_REF_ARRAY_NAME,
            'doing_action' => true,
            'did_action' => 1,
            'current_priority' => 10,
            'wp_hook_doing_action' => true,
        ];
    }
    $importedRows = [];
    foreach ($GLOBALS['rp_action_ref_array_imported_rows_by_pass'] as $passRows) {
        foreach ($passRows as $row) {
            $importedRows[] = $row;
        }
    }
    if ($events !== $expected || $GLOBALS['rp_action_ref_array_invocations'] !== array_column($importedRows, 'uid')) {
        throw new RuntimeException('wp_action_ref_array_observation:' . json_encode($events, JSON_THROW_ON_ERROR));
    }

    $wire->send('request_complete', [
        'target' => RP_ACTION_REF_ARRAY_TARGET,
        'result' => null,
        'did_action' => did_action(RP_ACTION_REF_ARRAY_NAME),
    ]);
    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'release_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['handle'], 'wp_action_ref_array_release');
            $handle = $frame['payload']['handle'];
            if (($handle['kind'] ?? null) === 'argument') {
                $matchedPass = null;
                foreach ($GLOBALS['rp_action_ref_array_argument_frames'] as $pass => $frameHandle) {
                    if (rp_action_ref_array_handles_equal($handle, $frameHandle)) {
                        $matchedPass = $pass;
                        break;
                    }
                }
                if ($matchedPass === null || isset($GLOBALS['rp_action_ref_array_argument_frames_released'][$matchedPass])) {
                    throw new RuntimeException('wp_action_ref_array_release_identity');
                }
                $GLOBALS['rp_action_ref_array_argument_frames_released'][$matchedPass] = true;
            } elseif (($handle['kind'] ?? null) === 'callable') {
                $found = false;
                foreach ($GLOBALS['rp_action_ref_array_handles_by_pass'] as $pass => $passHandles) {
                    foreach ($passHandles as $uid => $candidate) {
                        if (rp_action_ref_array_handles_equal($candidate, $handle)) {
                            if (isset($GLOBALS['rp_action_ref_array_released_by_pass'][$pass][$uid])) {
                                throw new RuntimeException('wp_action_ref_array_release_duplicate');
                            }
                            $found = true;
                            $GLOBALS['rp_action_ref_array_released_by_pass'][$pass][$uid] = true;
                            break 2;
                        }
                    }
                }
                if (!$found) {
                    throw new RuntimeException('wp_action_ref_array_callable_release_identity');
                }
            } else {
                throw new RuntimeException('wp_action_ref_array_release_kind');
            }
            $registry->release($handle);
            $wire->send('release_ack', ['handle' => $handle]);
            continue;
        }
        if ($frame['kind'] === 'request_complete') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['target', 'result'], 'wp_action_ref_array_request_complete');
            if ($frame['payload']['target'] !== RP_ACTION_REF_ARRAY_TARGET || $frame['payload']['result'] !== null) {
                throw new RuntimeException('wp_action_ref_array_request_result');
            }
            $liveHandlesBeforeClose = $registry->liveCount();
            $registry->close();
            if ($registry->liveCount() !== 0) {
                throw new RuntimeException('wp_action_ref_array_live_handles_after_close');
            }
            $wire->send('guest_done', [
                'events' => $events,
                'result' => null,
                'caller_value' => $finalValue,
                'caller_extra' => $finalExtra,
                'action_count' => did_action(RP_ACTION_REF_ARRAY_NAME),
                'current_action' => current_action(),
                'current_filter' => current_filter(),
                'doing_action' => doing_action(),
                'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($replacement ?? $selected),
                'current_priority' => ($replacement ?? $selected)->current_priority(),
                'table_fingerprint' => rp_action_ref_array_fingerprint($GLOBALS['rp_action_ref_array_imported_rows_by_pass'][did_action(RP_ACTION_REF_ARRAY_NAME)]),
                'live_handles' => $registry->liveCount(),
                'live_handles_before_close' => $liveHandlesBeforeClose,
                'argument_frame_released_before_close' => count($GLOBALS['rp_action_ref_array_argument_frames_released']) === count($GLOBALS['rp_action_ref_array_argument_frames']),
                'invocations' => $GLOBALS['rp_action_ref_array_invocations'],
                'frames' => $wire->stats(),
            ]);
            break;
        }
        throw new RuntimeException('wp_action_ref_array_guest_unexpected_frame');
    }
} catch (Throwable $error) {
    fail_bridge($error);
}
