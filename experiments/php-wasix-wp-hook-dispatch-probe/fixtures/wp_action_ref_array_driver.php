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
    $rows = rp_action_ref_array_table_snapshot($hook);
    $expectedRows = $rows;
    $argumentFrame = $registry->retainArgument($args);
    $GLOBALS['rp_action_ref_array_argument_frame'] = $argumentFrame;
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
    $GLOBALS['rp_action_ref_array_handles'] = $handles;

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
            foreach ($handles as $uid => $knownHandle) {
                if (rp_action_ref_array_handles_equal($knownHandle, $releasedHandle)) {
                    $releasedUid = $uid;
                    break;
                }
            }
            if ($releasedUid === null || isset($GLOBALS['rp_action_ref_array_released'][$releasedUid])) {
                throw new RuntimeException('wp_action_ref_array_dispatch_release_identity');
            }
            $registry->release($releasedHandle);
            $GLOBALS['rp_action_ref_array_released'][$releasedUid] = true;
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
                if (count($newRows) !== 1
                    || $newRows[0]['priority'] !== 7
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
                usort(
                    $exported,
                    static fn (array $left, array $right): int => [$left['priority'], $left['uid']] <=> [$right['priority'], $right['uid']],
                );
                $expectedRows = $current;
                $GLOBALS['rp_action_ref_array_handles'] = $handles;
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
                    || $ack['payload']['priority'] !== 7
                ) {
                    throw new RuntimeException('wp_action_ref_array_mutation_ack_identity');
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
                || $frame['payload']['invocations'] !== count($exported)
                || count($GLOBALS['rp_action_ref_array_invocations']) !== count($exported)
                || !$sync('finish')
            ) {
                throw new RuntimeException('wp_action_ref_array_completion_state');
            }
            if (rp_action_ref_array_table_snapshot($hook) !== $expectedRows) {
                throw new RuntimeException('wp_action_ref_array_table_mutated');
            }
            $GLOBALS['rp_action_ref_array_imported_rows'] = $exported;
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
    add_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_added', 7, 2);
    if (!remove_action(RP_ACTION_REF_ARRAY_NAME, 'rp_action_ref_array_pending', 8)) {
        throw new RuntimeException('wp_action_ref_array_remove_return');
    }
    return 'ignored-first-action-result';
}

function rp_action_ref_array_pending(string &$value, string &$extra): void
{
    throw new RuntimeException('wp_action_ref_array_removed_callback_invoked');
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
    $GLOBALS['rp_action_ref_array_argument_frame_released'] = false;
    $GLOBALS['rp_action_ref_array_released'] = [];
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
    if ($result !== null
        || $value !== 'value-mutated-by-added'
        || $extra !== 'extra-mutated-by-second'
        || current_action() !== false
        || current_filter() !== false
        || doing_action()
        || did_action(RP_ACTION_REF_ARRAY_NAME) !== 1
        || did_filter(RP_ACTION_REF_ARRAY_NAME) !== 0
        || $selected->current_priority() !== false
        || (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($selected) !== false
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
        ],
        [
            'callback' => 'added', 'args' => ['value-mutated-by-first', 'extra-mutated-by-first'],
            'current_action' => RP_ACTION_REF_ARRAY_NAME, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 7, 'wp_hook_doing_action' => true,
        ],
        [
            'callback' => 'second', 'args' => ['value-mutated-by-added', 'extra-mutated-by-added'],
            'current_action' => RP_ACTION_REF_ARRAY_NAME, 'doing_action' => true, 'did_action' => 1,
            'current_priority' => 10, 'wp_hook_doing_action' => true,
        ],
    ];
    if ($events !== $expected || $GLOBALS['rp_action_ref_array_invocations'] !== array_column($GLOBALS['rp_action_ref_array_imported_rows'], 'uid')) {
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
                if (!rp_action_ref_array_handles_equal($handle, $GLOBALS['rp_action_ref_array_argument_frame'])) {
                    throw new RuntimeException('wp_action_ref_array_release_identity');
                }
                $GLOBALS['rp_action_ref_array_argument_frame_released'] = true;
            } elseif (($handle['kind'] ?? null) === 'callable') {
                $found = false;
                foreach ($GLOBALS['rp_action_ref_array_handles'] as $uid => $candidate) {
                    if (rp_action_ref_array_handles_equal($candidate, $handle)) {
                        if (isset($GLOBALS['rp_action_ref_array_released'][$uid])) {
                            throw new RuntimeException('wp_action_ref_array_release_duplicate');
                        }
                        $found = true;
                        $GLOBALS['rp_action_ref_array_released'][$uid] = true;
                        break;
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
                'caller_value' => $value,
                'caller_extra' => $extra,
                'action_count' => did_action(RP_ACTION_REF_ARRAY_NAME),
                'current_action' => current_action(),
                'current_filter' => current_filter(),
                'doing_action' => doing_action(),
                'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($selected),
                'current_priority' => $selected->current_priority(),
                'table_fingerprint' => rp_action_ref_array_fingerprint($GLOBALS['rp_action_ref_array_imported_rows']),
                'live_handles' => $registry->liveCount(),
                'live_handles_before_close' => $liveHandlesBeforeClose,
                'argument_frame_released_before_close' => $GLOBALS['rp_action_ref_array_argument_frame_released'],
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
