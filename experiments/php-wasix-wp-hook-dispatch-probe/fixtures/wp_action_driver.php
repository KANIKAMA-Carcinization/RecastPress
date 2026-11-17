<?php
declare(strict_types=1);

fwrite(STDERR, "RP_ACTION_PHASE=driver_start\n");
require_once __DIR__ . '/bridge_driver.php';
require_once __DIR__ . '/plugin.php';
fwrite(STDERR, "RP_ACTION_PHASE=plugin_loaded\n");

use RecastPress\V06\Handles\HandleRegistry;

const RP_ACTION_REQUEST = '81';
const RP_ACTION_TARGET = 'wp-action-1';
const RP_ACTION_NAME = 'rp-probe-action';

function recastpress_wp_should_dispatch_selected_action(string $hookName, object $hook): bool
{
    return $hookName === RP_ACTION_NAME
        && isset($GLOBALS['rp_action_token_by_object'][$hook])
        && $GLOBALS['rp_action_token_by_object'][$hook] === RP_ACTION_TARGET
        && $hook === $GLOBALS['rp_action_selected_object'];
}

// Keep both adapter entry points present for the same selected object. The
// filter handoff must still defer to the action handoff while doing_action.
function recastpress_wp_should_dispatch_selected_hook(string $hookName, object $hook): bool
{
    return $hookName === RP_ACTION_NAME
        && isset($GLOBALS['rp_action_token_by_object'][$hook])
        && $GLOBALS['rp_action_token_by_object'][$hook] === RP_ACTION_TARGET
        && $hook === $GLOBALS['rp_action_selected_object'];
}

function recastpress_wp_dispatch_selected_hook(...$args): never
{
    throw new RuntimeException('wp_action_entered_filter_handoff');
}

function rp_action_table_snapshot(WP_Hook $hook): array
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

function rp_action_handles_equal(array $left, array $right): bool
{
    ksort($left);
    ksort($right);
    return $left === $right;
}

function rp_action_wp_hook_is_doing_action(WP_Hook $hook): bool
{
    $property = new ReflectionProperty(WP_Hook::class, 'doing_action');
    return $property->getValue($hook);
}

function rp_action_table_fingerprint(array $rows): string
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

function recastpress_wp_dispatch_selected_action(
    string $hookName,
    WP_Hook $hook,
    array $args,
    int $nestingLevel,
    Closure $sync,
): null {
    $registry = $GLOBALS['rp_action_registry'];
    $wire = $GLOBALS['rp_action_wire'];
    $rows = rp_action_table_snapshot($hook);
    $before = $rows;
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
        'generation' => RP_ACTION_REQUEST,
        'target' => RP_ACTION_TARGET,
        'hook' => $hookName,
        'args' => $args,
        'rows' => $exported,
        'table_fingerprint' => rp_action_table_fingerprint($exported),
    ]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'invoke_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, [
                'target', 'uid', 'handle', 'priority', 'call_mode', 'args',
            ], 'wp_action_invoke');
            $payload = $frame['payload'];
            if ($payload['target'] !== RP_ACTION_TARGET
                || !isset($handles[$payload['uid']])
                || !rp_action_handles_equal($handles[$payload['uid']], $payload['handle'])
                || !is_int($payload['priority'])
                || !in_array($payload['call_mode'], ['zero', 'array'], true)
                || !is_array($payload['args'])
                || ($payload['call_mode'] === 'zero' && $payload['args'] !== [])
                || ($payload['call_mode'] === 'array' && $payload['args'] === [])
            ) {
                throw new RuntimeException('wp_action_invoke_identity');
            }
            if (!$sync('set_priority', $payload['priority'])) {
                throw new RuntimeException('wp_action_priority_sync');
            }
            $callback = $registry->resolve($payload['handle'], 'callable');
            call_user_func_array($callback, $payload['args']);
            $GLOBALS['rp_action_invocations'][] = $payload['uid'];
            // Action return values are deliberately discarded at the PHP boundary.
            $wire->send('resume_value', [
                'target' => RP_ACTION_TARGET,
                'uid' => $payload['uid'],
                'value' => null,
            ]);
            continue;
        }

        if ($frame['kind'] === 'action_complete') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['target', 'invocations'], 'wp_action_complete');
            if ($frame['payload']['target'] !== RP_ACTION_TARGET
                || !is_int($frame['payload']['invocations'])
                || $frame['payload']['invocations'] !== count($rows)
                || count($GLOBALS['rp_action_invocations']) !== count($rows)
                || !$sync('finish')
            ) {
                throw new RuntimeException('wp_action_completion_state');
            }
            if (rp_action_table_snapshot($hook) !== $before) {
                throw new RuntimeException('wp_action_table_mutated_during_dispatch');
            }
            $GLOBALS['rp_action_imported_rows'] = $exported;
            return null;
        }

        throw new RuntimeException('wp_action_unexpected_frame');
    }
}

function rp_action_all(string $hookName, mixed ...$args): void
{
    $GLOBALS['rp_action_events'][] = [
        'callback' => 'all', 'hook' => $hookName, 'args' => $args,
        'current_action' => current_action(), 'doing_action' => doing_action($hookName),
        'did_action' => did_action($hookName),
    ];
}

function rp_action_callback_a(string $left, string $right): string
{
    $GLOBALS['rp_action_events'][] = [
        'callback' => 'a', 'args' => [$left, $right],
        'current_action' => current_action(), 'doing_action' => doing_action(RP_ACTION_NAME),
        'did_action' => did_action(RP_ACTION_NAME),
        'current_priority' => $GLOBALS['rp_action_selected_object']->current_priority(),
        'wp_hook_doing_action' => rp_action_wp_hook_is_doing_action($GLOBALS['rp_action_selected_object']),
    ];
    return 'ignored-a';
}

function rp_action_callback_b(string $left): string
{
    $GLOBALS['rp_action_events'][] = [
        'callback' => 'b', 'args' => [$left],
        'current_action' => current_action(), 'doing_action' => doing_action(RP_ACTION_NAME),
        'did_action' => did_action(RP_ACTION_NAME),
        'current_priority' => $GLOBALS['rp_action_selected_object']->current_priority(),
        'wp_hook_doing_action' => rp_action_wp_hook_is_doing_action($GLOBALS['rp_action_selected_object']),
    ];
    return 'ignored-b';
}

try {
    fwrite(STDERR, "RP_ACTION_PHASE=protocol_start\n");
    $wire = new BridgeProtocol();
    $registry = new HandleRegistry(RP_ACTION_REQUEST, 64, 64);
    $GLOBALS['rp_action_wire'] = $wire;
    $GLOBALS['rp_action_registry'] = $registry;
    $GLOBALS['rp_action_events'] = [];
    $GLOBALS['rp_action_invocations'] = [];
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];

    add_action('all', 'rp_action_all', 10, 10);
    add_action(RP_ACTION_NAME, 'rp_action_callback_a', 5, 2);
    $zero = static function (): string {
        $GLOBALS['rp_action_events'][] = [
            'callback' => 'zero', 'args' => [],
            'current_action' => current_action(), 'doing_action' => doing_action(RP_ACTION_NAME),
            'did_action' => did_action(RP_ACTION_NAME),
            'current_priority' => $GLOBALS['rp_action_selected_object']->current_priority(),
            'wp_hook_doing_action' => rp_action_wp_hook_is_doing_action($GLOBALS['rp_action_selected_object']),
        ];
        return 'ignored-zero';
    };
    add_action(RP_ACTION_NAME, $zero, 5, 0);
    add_action(RP_ACTION_NAME, 'rp_action_callback_b', 10, 1);
    fwrite(STDERR, "RP_ACTION_PHASE=callbacks_registered\n");

    $selected = $GLOBALS['wp_filter'][RP_ACTION_NAME];
    $tokens = new WeakMap();
    $tokens[$selected] = RP_ACTION_TARGET;
    $GLOBALS['rp_action_token_by_object'] = $tokens;
    $GLOBALS['rp_action_selected_object'] = $selected;

    fwrite(STDERR, "RP_ACTION_PHASE=do_action_start\n");
    $result = do_action(RP_ACTION_NAME, 'left', 'right');
    fwrite(STDERR, "RP_ACTION_PHASE=do_action_done\n");
    if ($result !== null
        || current_action() !== false
        || current_filter() !== false
        || doing_action()
        || did_action(RP_ACTION_NAME) !== 1
        || did_filter(RP_ACTION_NAME) !== 0
        || $selected->current_priority() !== false
        || rp_action_wp_hook_is_doing_action($selected) !== false
    ) {
        throw new RuntimeException('wp_action_final_observation');
    }

    $events = $GLOBALS['rp_action_events'];
    if (array_column($events, 'callback') !== ['all', 'a', 'zero', 'b']
        || $events[0] !== [
            'callback' => 'all', 'hook' => RP_ACTION_NAME, 'args' => [RP_ACTION_NAME, 'left', 'right'],
            'current_action' => RP_ACTION_NAME, 'doing_action' => true, 'did_action' => 1,
        ]
        || $events[1]['args'] !== ['left', 'right']
        || $events[1]['current_action'] !== RP_ACTION_NAME
        || $events[1]['doing_action'] !== true
        || $events[1]['did_action'] !== 1
        || $events[1]['current_priority'] !== 5
        || $events[1]['wp_hook_doing_action'] !== true
        || $events[2]['args'] !== []
        || $events[2]['current_action'] !== RP_ACTION_NAME
        || $events[2]['doing_action'] !== true
        || $events[2]['did_action'] !== 1
        || $events[2]['current_priority'] !== 5
        || $events[2]['wp_hook_doing_action'] !== true
        || $events[3]['args'] !== ['left']
        || $events[3]['current_action'] !== RP_ACTION_NAME
        || $events[3]['doing_action'] !== true
        || $events[3]['did_action'] !== 1
        || $events[3]['current_priority'] !== 10
        || $events[3]['wp_hook_doing_action'] !== true
    ) {
        throw new RuntimeException(
            'wp_action_callback_observation:'
            . json_encode($events, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR),
        );
    }

    $wire->send('request_complete', [
        'target' => RP_ACTION_TARGET,
        'result' => null,
        'did_action' => did_action(RP_ACTION_NAME),
    ]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'release_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['handle'], 'wp_action_release');
            $registry->release($frame['payload']['handle']);
            $wire->send('release_ack', ['handle' => $frame['payload']['handle']]);
            continue;
        }
        if ($frame['kind'] === 'request_complete') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['target', 'result'], 'wp_action_request_complete');
            if ($frame['payload']['target'] !== RP_ACTION_TARGET || $frame['payload']['result'] !== null) {
                throw new RuntimeException('wp_action_request_result');
            }
            $liveHandlesBeforeClose = $registry->liveCount();
            $registry->close();
            if ($registry->liveCount() !== 0) {
                throw new RuntimeException('wp_action_live_handles_after_close');
            }
            $wire->send('guest_done', [
                'events' => $GLOBALS['rp_action_events'],
                'action_count' => did_action(RP_ACTION_NAME),
                'current_action' => current_action(),
                'current_filter' => current_filter(),
                'doing_action' => doing_action(),
                'wp_hook_doing_action' => rp_action_wp_hook_is_doing_action($selected),
                'current_priority' => $selected->current_priority(),
                'table_fingerprint' => rp_action_table_fingerprint($GLOBALS['rp_action_imported_rows']),
                'live_handles' => $registry->liveCount(),
                'live_handles_before_close' => $liveHandlesBeforeClose,
                'invocations' => $GLOBALS['rp_action_invocations'],
                'frames' => $wire->stats(),
            ]);
            break;
        }
        throw new RuntimeException('wp_action_guest_unexpected_frame');
    }
} catch (Throwable $error) {
    fail_bridge($error);
}
