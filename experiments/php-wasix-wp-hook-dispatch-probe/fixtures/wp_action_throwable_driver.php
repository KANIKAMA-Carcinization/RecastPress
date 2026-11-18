<?php
declare(strict_types=1);

fwrite(STDERR, "RP_ACTION_THROWABLE_PHASE=driver_start\n");
require_once __DIR__ . '/bridge_driver.php';
require_once __DIR__ . '/plugin.php';

use RecastPress\V06\Handles\HandleRegistry;

const RP_ACTION_THROWABLE_REQUEST = '81';
const RP_ACTION_THROWABLE_TARGET = 'wp-action-throwable-1';
const RP_ACTION_THROWABLE_NAME = 'rp-probe-action-throwable';

function rp_action_throwable_handles_equal(array $left, array $right): bool
{
    ksort($left);
    ksort($right);
    return $left === $right;
}

function rp_action_throwable_table_snapshot(WP_Hook $hook): array
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

function rp_action_throwable_fingerprint(array $rows): string
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
    return $hookName === RP_ACTION_THROWABLE_NAME
        && isset($GLOBALS['rp_action_throwable_token_by_object'][$hook])
        && $GLOBALS['rp_action_throwable_token_by_object'][$hook] === RP_ACTION_THROWABLE_TARGET
        && $hook === $GLOBALS['rp_action_throwable_selected_object'];
}

function recastpress_wp_dispatch_selected_hook(...$args): never
{
    throw new RuntimeException('wp_action_throwable_entered_filter_handoff');
}

function recastpress_wp_should_dispatch_selected_hook(string $hookName, object $hook): bool
{
    return $hookName === RP_ACTION_THROWABLE_NAME
        && isset($GLOBALS['rp_action_throwable_token_by_object'][$hook])
        && $GLOBALS['rp_action_throwable_token_by_object'][$hook] === RP_ACTION_THROWABLE_TARGET
        && $hook === $GLOBALS['rp_action_throwable_selected_object'];
}

function recastpress_wp_dispatch_selected_action(
    string $hookName,
    WP_Hook $hook,
    array $args,
    int $nestingLevel,
    Closure $sync,
): null {
    $registry = $GLOBALS['rp_action_throwable_registry'];
    $wire = $GLOBALS['rp_action_throwable_wire'];
    $rows = rp_action_throwable_table_snapshot($hook);
    $exported = [];
    $handles = [];
    foreach ($rows as $row) {
        $handle = $registry->retainCallable($row['function']);
        $handles[$row['uid']] = $handle;
        $exported[] = [
            'priority' => $row['priority'],
            'uid' => $row['uid'],
            'accepted_args' => $row['accepted_args'],
            'function' => is_string($row['function']) ? $row['function'] : null,
            'handle' => $handle,
        ];
    }

    $wire->send('action_dispatch_start', [
        'generation' => RP_ACTION_THROWABLE_REQUEST,
        'target' => RP_ACTION_THROWABLE_TARGET,
        'hook' => $hookName,
        'args' => $args,
        'rows' => $exported,
        'table_fingerprint' => rp_action_throwable_fingerprint($exported),
    ]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'invoke_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, [
                'target', 'uid', 'handle', 'priority', 'call_mode', 'args',
            ], 'wp_action_throwable_invoke');
            $payload = $frame['payload'];
            if ($payload['target'] !== RP_ACTION_THROWABLE_TARGET
                || !isset($handles[$payload['uid']])
                || !rp_action_throwable_handles_equal($handles[$payload['uid']], $payload['handle'])
                || $payload['uid'] !== 'rp_action_throwing_callback'
                || $payload['priority'] !== 5
                || $payload['call_mode'] !== 'array'
                || $payload['args'] !== ['seed', 'left']
            ) {
                throw new RuntimeException('wp_action_throwable_invoke_identity');
            }
            if (!$sync('set_priority', $payload['priority'])) {
                throw new RuntimeException('wp_action_throwable_priority_sync');
            }
            $callback = $registry->resolve($payload['handle'], 'callable');
            try {
                call_user_func_array($callback, $payload['args']);
            } catch (Throwable $error) {
                $throwable = $registry->retainObject($error);
                $GLOBALS['rp_action_throwable_original'] = $error;
                $GLOBALS['rp_action_throwable_handle'] = $throwable;
                $wire->send('resume_throwable', [
                    'target' => RP_ACTION_THROWABLE_TARGET,
                    'uid' => $payload['uid'],
                    'throwable' => $throwable,
                    'class' => $error::class,
                    'message' => $error->getMessage(),
                ]);

                $releasedCallables = [];
                while (true) {
                    $reply = $wire->receive();
                    if ($reply['kind'] === 'release_php') {
                        expect_parent_effect($reply, null);
                        expect_payload_keys($reply, ['handle'], 'wp_action_throwable_release_callback');
                        $releaseHandle = $reply['payload']['handle'];
                        $releaseUid = null;
                        foreach ($handles as $uid => $candidate) {
                            if (rp_action_throwable_handles_equal($candidate, $releaseHandle)) {
                                $releaseUid = $uid;
                                break;
                            }
                        }
                        if ($releaseUid === null || isset($releasedCallables[$releaseUid])) {
                            throw new RuntimeException('wp_action_throwable_release_callback_identity');
                        }
                        $registry->release($releaseHandle);
                        $releasedCallables[$releaseUid] = true;
                        $wire->send('release_ack', ['handle' => $releaseHandle]);
                        continue;
                    }
                    if ($reply['kind'] === 'throw_php') {
                        expect_parent_effect($reply, null);
                        expect_payload_keys($reply, ['target', 'throwable'], 'wp_action_throwable_rethrow');
                        if ($reply['payload']['target'] !== RP_ACTION_THROWABLE_TARGET
                            || !rp_action_throwable_handles_equal($throwable, $reply['payload']['throwable'])
                            || count($releasedCallables) !== count($handles)
                        ) {
                            throw new RuntimeException('wp_action_throwable_rethrow_identity');
                        }
                        $resolved = $registry->resolve($throwable, 'throwable');
                        if ($resolved !== $error) {
                            throw new RuntimeException('wp_action_throwable_identity_changed');
                        }
                        throw $resolved;
                    }
                    throw new RuntimeException('wp_action_throwable_unexpected_rethrow_frame');
                }
            }
            throw new RuntimeException('wp_action_throwable_callback_returned');
        }

        throw new RuntimeException('wp_action_throwable_unexpected_frame');
    }
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

function rp_action_throwing_callback(string $value, string $extra): never
{
    $hook = $GLOBALS['rp_action_throwable_selected_object'];
    $GLOBALS['rp_action_throwable_events'][] = [
        'callback' => 'throwing',
        'args' => [$value, $extra],
        'current_action' => current_action(),
        'doing_action' => doing_action(RP_ACTION_THROWABLE_NAME),
        'did_action' => did_action(RP_ACTION_THROWABLE_NAME),
        'current_priority' => $hook->current_priority(),
        'wp_hook_doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    $throwable = new RuntimeException('action-throwable-fixture');
    $GLOBALS['rp_action_throwable_expected_original'] = $throwable;
    throw $throwable;
}

function rp_action_never_callback(string $value, string $extra): void
{
    $GLOBALS['rp_action_throwable_events'][] = ['callback' => 'must-not-run', 'args' => [$value, $extra]];
}

function rp_action_throwable_private_state(WP_Hook $hook): array
{
    $read = static function (string $name) use ($hook): mixed {
        return (new ReflectionProperty(WP_Hook::class, $name))->getValue($hook);
    };
    return [
        'nesting_level' => $read('nesting_level'),
        'iterations' => $read('iterations'),
        'current_priority' => $read('current_priority'),
        'doing_action' => $read('doing_action'),
    ];
}

try {
    fwrite(STDERR, "RP_ACTION_THROWABLE_PHASE=protocol_start\n");
    $wire = new BridgeProtocol();
    $registry = new HandleRegistry(RP_ACTION_THROWABLE_REQUEST, 32, 32);
    $GLOBALS['rp_action_throwable_wire'] = $wire;
    $GLOBALS['rp_action_throwable_registry'] = $registry;
    $GLOBALS['rp_action_throwable_events'] = [];
    $GLOBALS['rp_action_throwable_original'] = null;
    $GLOBALS['rp_action_throwable_expected_original'] = null;
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];

    add_action('all', 'rp_action_throwable_all', 10, 10);
    add_action(RP_ACTION_THROWABLE_NAME, 'rp_action_throwing_callback', 5, 2);
    add_action(RP_ACTION_THROWABLE_NAME, 'rp_action_never_callback', 10, 2);
    $selected = $GLOBALS['wp_filter'][RP_ACTION_THROWABLE_NAME];
    $tokens = new WeakMap();
    $tokens[$selected] = RP_ACTION_THROWABLE_TARGET;
    $GLOBALS['rp_action_throwable_token_by_object'] = $tokens;
    $GLOBALS['rp_action_throwable_selected_object'] = $selected;

    $caught = null;
    try {
        do_action(RP_ACTION_THROWABLE_NAME, 'seed', 'left');
        throw new RuntimeException('wp_action_throwable_returned_normally');
    } catch (Throwable $error) {
        $caught = $error;
    }

    $original = $GLOBALS['rp_action_throwable_original'];
    $expectedOriginal = $GLOBALS['rp_action_throwable_expected_original'];
    if (!$original instanceof Throwable
        || $original !== $expectedOriginal
        || $caught !== $original
        || $caught::class !== RuntimeException::class
        || $caught->getMessage() !== 'action-throwable-fixture'
        || array_column($GLOBALS['rp_action_throwable_events'], 'callback') !== ['all', 'throwing']
        || current_action() !== RP_ACTION_THROWABLE_NAME
        || current_filter() !== RP_ACTION_THROWABLE_NAME
        || !doing_action(RP_ACTION_THROWABLE_NAME)
        || did_action(RP_ACTION_THROWABLE_NAME) !== 1
        || did_filter(RP_ACTION_THROWABLE_NAME) !== 0
        || $GLOBALS['wp_current_filter'] !== [RP_ACTION_THROWABLE_NAME]
        || $selected->current_priority() !== 5
        || rp_action_throwable_private_state($selected) !== [
            'nesting_level' => 1,
            'iterations' => [[5, 10]],
            'current_priority' => [5],
            'doing_action' => true,
        ]
    ) {
        throw new RuntimeException('wp_action_throwable_outer_observation:' . json_encode([
            'events' => $GLOBALS['rp_action_throwable_events'],
            'current_action' => current_action(),
            'current_filter' => current_filter(),
            'doing_action' => doing_action(RP_ACTION_THROWABLE_NAME),
            'did_action' => did_action(RP_ACTION_THROWABLE_NAME),
            'stack' => $GLOBALS['wp_current_filter'],
            'current_priority' => $selected->current_priority(),
            'hook_state' => rp_action_throwable_private_state($selected),
        ], JSON_THROW_ON_ERROR));
    }

    $observation = [
        'events' => $GLOBALS['rp_action_throwable_events'],
        'same_throwable' => $caught === $original,
        'throwable_class' => $caught::class,
        'throwable_message' => $caught->getMessage(),
        'action_count' => did_action(RP_ACTION_THROWABLE_NAME),
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_action' => doing_action(RP_ACTION_THROWABLE_NAME),
        'wp_current_filter' => $GLOBALS['wp_current_filter'],
        'current_priority' => $selected->current_priority(),
        'wp_hook_state' => rp_action_throwable_private_state($selected),
    ];
    $wire->send('action_throwable_caught', [
        'target' => RP_ACTION_THROWABLE_TARGET,
        'observation' => $observation,
        'throwable' => $GLOBALS['rp_action_throwable_handle'],
    ]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'release_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['handle'], 'wp_action_throwable_release_object');
            if (!rp_action_throwable_handles_equal($GLOBALS['rp_action_throwable_handle'], $frame['payload']['handle'])) {
                throw new RuntimeException('wp_action_throwable_release_identity');
            }
            $registry->release($frame['payload']['handle']);
            $wire->send('release_ack', ['handle' => $frame['payload']['handle']]);
            continue;
        }
        if ($frame['kind'] === 'request_complete') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['target', 'outcome'], 'wp_action_throwable_request_complete');
            if ($frame['payload']['target'] !== RP_ACTION_THROWABLE_TARGET
                || $frame['payload']['outcome'] !== 'request_aborted_as_expected'
            ) {
                throw new RuntimeException('wp_action_throwable_request_outcome');
            }
            $liveHandlesBeforeClose = $registry->liveCount();
            $registry->close();
            if ($registry->liveCount() !== 0 || $liveHandlesBeforeClose !== 0) {
                throw new RuntimeException('wp_action_throwable_live_handles');
            }
            $wire->send('guest_done', [
                'observation' => $observation,
                'live_handles' => $registry->liveCount(),
                'live_handles_before_close' => $liveHandlesBeforeClose,
                'registry_closed' => $registry->isClosed(),
                'frames' => $wire->stats(),
            ]);
            break;
        }
        throw new RuntimeException('wp_action_throwable_guest_unexpected_frame');
    }
} catch (Throwable $error) {
    fail_bridge($error);
}
