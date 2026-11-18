<?php
declare(strict_types=1);

require_once __DIR__ . '/bridge_driver.php';
require_once __DIR__ . '/plugin.php';

use RecastPress\V06\Handles\HandleRegistry;

const RP_NESTED_REQUEST = '81';
const RP_NESTED_OUTER_TARGET = 'wp-action-nested-outer-1';
const RP_NESTED_INNER_TARGET = 'wp-action-nested-inner-1';
const RP_NESTED_OUTER_HOOK = 'rp-probe-action-nested-outer';
const RP_NESTED_INNER_HOOK = 'rp-probe-action-nested-inner';

function rp_nested_targets(): WeakMap
{
    return $GLOBALS['rp_nested_target_by_hook'];
}

function recastpress_wp_should_dispatch_selected_action(string $hookName, object $hook): bool
{
    $targets = rp_nested_targets();
    if (!isset($targets[$hook])) {
        return false;
    }
    return ($hookName === RP_NESTED_OUTER_HOOK && $targets[$hook] === RP_NESTED_OUTER_TARGET)
        || ($hookName === RP_NESTED_INNER_HOOK && $targets[$hook] === RP_NESTED_INNER_TARGET);
}

function recastpress_wp_should_dispatch_selected_hook(string $hookName, object $hook): bool
{
    return recastpress_wp_should_dispatch_selected_action($hookName, $hook);
}

function recastpress_wp_dispatch_selected_hook(...$args): never
{
    throw new RuntimeException('nested_action_entered_filter_handoff');
}

function rp_nested_table_snapshot(WP_Hook $hook): array
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

function rp_nested_handles_equal(array $left, array $right): bool
{
    ksort($left);
    ksort($right);
    return $left === $right;
}

function rp_nested_table_fingerprint(array $rows): string
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

function rp_nested_private_state(WP_Hook $hook): array
{
    $read = static fn (string $name): mixed => (new ReflectionProperty(WP_Hook::class, $name))->getValue($hook);
    return [
        'nesting_level' => $read('nesting_level'),
        'iterations' => $read('iterations'),
        'current_priority' => $read('current_priority'),
        'doing_action' => $read('doing_action'),
    ];
}

function recastpress_wp_dispatch_selected_action(
    string $hookName,
    WP_Hook $hook,
    array $args,
    int $nestingLevel,
    Closure $sync,
): null {
    $target = rp_nested_targets()[$hook] ?? throw new RuntimeException('nested_action_unknown_target');
    $inner = $target === RP_NESTED_INNER_TARGET;
    $rows = rp_nested_table_snapshot($hook);
    $before = $rows;
    $exported = [];
    $handles = [];
    foreach ($rows as $row) {
        $handle = $GLOBALS['rp_nested_registry']->retainCallable($row['function']);
        $handles[$row['uid']] = $handle;
        $exported[] = [
            'priority' => $row['priority'],
            'uid' => $row['uid'],
            'accepted_args' => $row['accepted_args'],
            'function' => is_string($row['function']) ? $row['function'] : null,
            'handle' => $handle,
        ];
    }

    $payload = [
        'generation' => RP_NESTED_REQUEST,
        'target' => $target,
        'hook' => $hookName,
        'args' => $args,
        'rows' => $exported,
        'table_fingerprint' => rp_nested_table_fingerprint($exported),
    ];
    if ($inner) {
        $payload['parent_target'] = RP_NESTED_OUTER_TARGET;
        $payload['parent_uid'] = 'rp_nested_outer_parent';
    }
    $wire = $GLOBALS['rp_nested_wire'];
    $wire->send('action_dispatch_start', $payload);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'invoke_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, [
                'target', 'uid', 'handle', 'priority', 'call_mode', 'args',
            ], 'nested_action_invoke');
            $invocation = $frame['payload'];
            if ($invocation['target'] !== $target
                || !isset($handles[$invocation['uid']])
                || !rp_nested_handles_equal($handles[$invocation['uid']], $invocation['handle'])
                || !is_int($invocation['priority'])
                || !in_array($invocation['call_mode'], ['zero', 'array'], true)
                || !is_array($invocation['args'])
                || ($invocation['call_mode'] === 'zero' && $invocation['args'] !== [])
                || ($invocation['call_mode'] === 'array' && $invocation['args'] === [])
            ) {
                throw new RuntimeException('nested_action_invoke_identity');
            }
            if (!$sync('set_priority', $invocation['priority'])) {
                throw new RuntimeException('nested_action_priority_sync');
            }
            $callback = $GLOBALS['rp_nested_registry']->resolve($invocation['handle'], 'callable');
            call_user_func_array($callback, $invocation['args']);
            $wire->send('resume_value', [
                'target' => $target,
                'uid' => $invocation['uid'],
                'value' => null,
            ]);
            continue;
        }

        if ($frame['kind'] === 'action_complete') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['target', 'invocations'], 'nested_action_complete');
            $expectedCount = $inner ? 1 : 2;
            if ($frame['payload']['target'] !== $target
                || $frame['payload']['invocations'] !== $expectedCount
                || rp_nested_table_snapshot($hook) !== $before
                || !$sync('finish')
            ) {
                throw new RuntimeException('nested_action_completion_state');
            }
            if ($inner) {
                $wire->send('nested_action_return_ack', [
                    'target' => RP_NESTED_INNER_TARGET,
                    'parent_target' => RP_NESTED_OUTER_TARGET,
                    'parent_uid' => 'rp_nested_outer_parent',
                ]);
                return null;
            }
            $GLOBALS['rp_nested_imported_rows'][$target] = $exported;
            return null;
        }

        if ($frame['kind'] === 'release_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['handle'], 'nested_action_callback_release');
            $release = $frame['payload']['handle'];
            $known = false;
            foreach ($handles as $uid => $candidate) {
                if (rp_nested_handles_equal($candidate, $release)
                    && !isset($GLOBALS['rp_nested_released'][$target][$uid])
                ) {
                    $GLOBALS['rp_nested_released'][$target][$uid] = true;
                    $known = true;
                    break;
                }
            }
            if (!$known) {
                throw new RuntimeException('nested_action_release_identity');
            }
            $GLOBALS['rp_nested_registry']->release($release);
            $wire->send('release_ack', ['handle' => $release]);
            continue;
        }

        throw new RuntimeException('nested_action_unexpected_frame');
    }
}

function rp_nested_all(string $hookName, mixed ...$args): void
{
    $GLOBALS['rp_nested_events'][] = [
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

function rp_nested_outer_parent(): void
{
    $outer = $GLOBALS['rp_nested_outer_hook'];
    $inner = $GLOBALS['rp_nested_inner_hook'];
    $GLOBALS['rp_nested_events'][] = [
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

    $GLOBALS['rp_nested_events'][] = [
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
        'inner_hook_state' => rp_nested_private_state($inner),
    ];
}

function rp_nested_inner_callback(string $value): void
{
    $GLOBALS['rp_nested_events'][] = [
        'callback' => 'inner_callback',
        'args' => [$value],
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_outer' => doing_action(RP_NESTED_OUTER_HOOK),
        'doing_inner' => doing_action(RP_NESTED_INNER_HOOK),
        'did_outer' => did_action(RP_NESTED_OUTER_HOOK),
        'did_inner' => did_action(RP_NESTED_INNER_HOOK),
        'stack' => $GLOBALS['wp_current_filter'],
        'current_priority' => $GLOBALS['rp_nested_inner_hook']->current_priority(),
    ];
}

function rp_nested_outer_tail(): void
{
    $GLOBALS['rp_nested_events'][] = [
        'callback' => 'outer_tail',
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_outer' => doing_action(RP_NESTED_OUTER_HOOK),
        'doing_inner' => doing_action(RP_NESTED_INNER_HOOK),
        'did_outer' => did_action(RP_NESTED_OUTER_HOOK),
        'did_inner' => did_action(RP_NESTED_INNER_HOOK),
        'stack' => $GLOBALS['wp_current_filter'],
        'current_priority' => $GLOBALS['rp_nested_outer_hook']->current_priority(),
    ];
}

try {
    $wire = new BridgeProtocol();
    $registry = new HandleRegistry(RP_NESTED_REQUEST, 64, 64);
    $GLOBALS['rp_nested_wire'] = $wire;
    $GLOBALS['rp_nested_registry'] = $registry;
    $GLOBALS['rp_nested_events'] = [];
    $GLOBALS['rp_nested_released'] = [];
    $GLOBALS['rp_nested_imported_rows'] = [];
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];

    add_action('all', 'rp_nested_all', 10, 10);
    add_action(RP_NESTED_OUTER_HOOK, 'rp_nested_outer_parent', 5, 0);
    add_action(RP_NESTED_OUTER_HOOK, 'rp_nested_outer_tail', 10, 0);
    add_action(RP_NESTED_INNER_HOOK, 'rp_nested_inner_callback', 7, 1);
    $outer = $GLOBALS['wp_filter'][RP_NESTED_OUTER_HOOK];
    $inner = $GLOBALS['wp_filter'][RP_NESTED_INNER_HOOK];
    $GLOBALS['rp_nested_outer_hook'] = $outer;
    $GLOBALS['rp_nested_inner_hook'] = $inner;
    $targets = new WeakMap();
    $targets[$outer] = RP_NESTED_OUTER_TARGET;
    $targets[$inner] = RP_NESTED_INNER_TARGET;
    $GLOBALS['rp_nested_target_by_hook'] = $targets;

    $result = do_action(RP_NESTED_OUTER_HOOK);
    if ($result !== null
        || current_action() !== false
        || current_filter() !== false
        || doing_action()
        || did_action(RP_NESTED_OUTER_HOOK) !== 1
        || did_action(RP_NESTED_INNER_HOOK) !== 1
        || $outer->current_priority() !== false
        || $inner->current_priority() !== false
        || rp_nested_private_state($outer) !== [
            'nesting_level' => 0, 'iterations' => [], 'current_priority' => [], 'doing_action' => false,
        ]
        || rp_nested_private_state($inner) !== [
            'nesting_level' => 0, 'iterations' => [], 'current_priority' => [], 'doing_action' => false,
        ]
    ) {
        throw new RuntimeException('nested_action_final_state');
    }

    $observation = [
        'events' => $GLOBALS['rp_nested_events'],
        'outer_count' => did_action(RP_NESTED_OUTER_HOOK),
        'inner_count' => did_action(RP_NESTED_INNER_HOOK),
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_action' => doing_action(),
        'stack' => $GLOBALS['wp_current_filter'],
        'outer_hook_state' => rp_nested_private_state($outer),
        'inner_hook_state' => rp_nested_private_state($inner),
    ];
    $wire->send('nested_action_observed', [
        'target' => RP_NESTED_OUTER_TARGET,
        'observation' => $observation,
    ]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'release_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['handle'], 'nested_action_final_release');
            $registry->release($frame['payload']['handle']);
            $wire->send('release_ack', ['handle' => $frame['payload']['handle']]);
            continue;
        }
        if ($frame['kind'] === 'request_complete') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['target', 'outcome'], 'nested_action_request_complete');
            if ($frame['payload']['target'] !== RP_NESTED_OUTER_TARGET
                || $frame['payload']['outcome'] !== 'normal_nested_dispatch'
            ) {
                throw new RuntimeException('nested_action_request_outcome');
            }
            $liveBeforeClose = $registry->liveCount();
            $registry->close();
            if ($liveBeforeClose !== 0 || $registry->liveCount() !== 0) {
                throw new RuntimeException('nested_action_live_handles');
            }
            $wire->send('guest_done', [
                'observation' => $observation,
                'live_handles' => $registry->liveCount(),
                'live_handles_before_close' => $liveBeforeClose,
                'registry_closed' => $registry->isClosed(),
                'frames' => $wire->stats(),
            ]);
            break;
        }
        throw new RuntimeException('nested_action_guest_unexpected_frame');
    }
} catch (Throwable $error) {
    fail_bridge($error);
}
