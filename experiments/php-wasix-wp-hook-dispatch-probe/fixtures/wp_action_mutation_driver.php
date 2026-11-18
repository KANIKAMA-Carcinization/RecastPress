<?php
declare(strict_types=1);

require_once __DIR__ . '/bridge_driver.php';
require_once __DIR__ . '/plugin.php';

use RecastPress\V06\Handles\HandleRegistry;

const RP_MUTATION_REQUEST = '81';
const RP_MUTATION_TARGET = 'wp-action-mutation-1';
const RP_MUTATION_HOOK = 'rp-probe-action-mutation';

function recastpress_wp_should_dispatch_selected_action(string $hookName, object $hook): bool
{
    return $hookName === RP_MUTATION_HOOK
        && $hook === ($GLOBALS['rp_mutation_target_hook'] ?? null);
}

function recastpress_wp_should_dispatch_selected_hook(string $hookName, object $hook): bool
{
    return recastpress_wp_should_dispatch_selected_action($hookName, $hook);
}

function recastpress_wp_dispatch_selected_hook(...$args): never
{
    throw new RuntimeException('action_mutation_entered_filter_handoff');
}

function rp_mutation_table_snapshot(WP_Hook $hook): array
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

function rp_mutation_handles_equal(array $left, array $right): bool
{
    ksort($left);
    ksort($right);
    return $left === $right;
}

function rp_mutation_fingerprint(array $rows): string
{
    usort($rows, static fn (array $a, array $b): int => $a['priority'] <=> $b['priority']);
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

function rp_mutation_private_state(WP_Hook $hook): array
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
    $registry = $GLOBALS['rp_mutation_registry'];
    $wire = $GLOBALS['rp_mutation_wire'];
    $rows = rp_mutation_table_snapshot($hook);
    if (count($rows) !== 2) {
        throw new RuntimeException('action_mutation_initial_row_count');
    }
    $handles = [];
    $exported = [];
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
    $GLOBALS['rp_mutation_handles'] = $handles;
    $GLOBALS['rp_mutation_exported_rows'] = $exported;
    $GLOBALS['rp_mutation_released'] = [];
    $initial = $exported;

    $wire->send('action_dispatch_start', [
        'generation' => RP_MUTATION_REQUEST,
        'target' => RP_MUTATION_TARGET,
        'hook' => $hookName,
        'args' => $args,
        'rows' => $exported,
        'table_fingerprint' => rp_mutation_fingerprint($exported),
    ]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'invoke_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, [
                'target', 'uid', 'handle', 'priority', 'call_mode', 'args',
            ], 'action_mutation_invoke');
            $invocation = $frame['payload'];
            $knownHandle = $handles[$invocation['uid']] ?? null;
            if ($invocation['target'] !== RP_MUTATION_TARGET
                || !is_array($knownHandle)
                || !rp_mutation_handles_equal($knownHandle, $invocation['handle'])
                || !is_int($invocation['priority'])
                || !in_array($invocation['call_mode'], ['zero', 'array'], true)
                || !is_array($invocation['args'])
                || ($invocation['call_mode'] === 'zero' && $invocation['args'] !== [])
                || ($invocation['call_mode'] === 'array' && $invocation['args'] === [])
            ) {
                throw new RuntimeException('action_mutation_invoke_identity');
            }
            if (!$sync('set_priority', $invocation['priority'])) {
                throw new RuntimeException('action_mutation_priority_sync');
            }
            $callback = $registry->resolve($invocation['handle'], 'callable');
            call_user_func_array($callback, $invocation['args']);

            if ($invocation['uid'] === 'rp_action_mutation_mutator') {
                $current = rp_mutation_table_snapshot($hook);
                $newRows = array_values(array_filter(
                    $current,
                    static fn (array $row): bool => !array_key_exists($row['uid'], $handles),
                ));
                if (count($newRows) !== 1
                    || $newRows[0]['priority'] !== 15
                    || $newRows[0]['uid'] !== 'rp_action_mutation_added'
                    || $newRows[0]['accepted_args'] !== 0
                    || $newRows[0]['function'] !== 'rp_action_mutation_added'
                ) {
                    throw new RuntimeException('action_mutation_added_row_shape');
                }
                $newHandle = $registry->retainCallable($newRows[0]['function']);
                $row = [
                    'priority' => $newRows[0]['priority'],
                    'uid' => $newRows[0]['uid'],
                    'accepted_args' => $newRows[0]['accepted_args'],
                    'function' => $newRows[0]['function'],
                    'handle' => $newHandle,
                ];
                $handles[$row['uid']] = $newHandle;
                $exported[] = $row;
                $GLOBALS['rp_mutation_handles'] = $handles;
                $GLOBALS['rp_mutation_exported_rows'] = $exported;
                $wire->send('action_callback_added', [
                    'target' => RP_MUTATION_TARGET,
                    'parent_uid' => $invocation['uid'],
                    'row' => $row,
                    'table_fingerprint' => rp_mutation_fingerprint($exported),
                ]);
                $ack = $wire->receive();
                expect_parent_effect($ack, null);
                expect_kind($ack, 'action_mutation_ack');
                expect_payload_keys($ack, ['target', 'uid', 'priority'], 'action_mutation_added_ack');
                if ($ack['payload']['target'] !== RP_MUTATION_TARGET
                    || $ack['payload']['uid'] !== $row['uid']
                    || $ack['payload']['priority'] !== 15
                ) {
                    throw new RuntimeException('action_mutation_added_ack_identity');
                }
            }

            $wire->send('resume_value', [
                'target' => RP_MUTATION_TARGET,
                'uid' => $invocation['uid'],
                'value' => null,
            ]);
            continue;
        }

        if ($frame['kind'] === 'action_complete') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['target', 'invocations'], 'action_mutation_complete');
            if ($frame['payload']['target'] !== RP_MUTATION_TARGET
                || $frame['payload']['invocations'] !== 3
                || rp_mutation_table_snapshot($hook) !== [
                    ['priority' => 5, 'uid' => 'rp_action_mutation_mutator', 'accepted_args' => 0, 'function' => 'rp_action_mutation_mutator'],
                    ['priority' => 15, 'uid' => 'rp_action_mutation_added', 'accepted_args' => 0, 'function' => 'rp_action_mutation_added'],
                    ['priority' => 20, 'uid' => 'rp_action_mutation_tail', 'accepted_args' => 0, 'function' => 'rp_action_mutation_tail'],
                ]
                || !$sync('finish')
            ) {
                throw new RuntimeException('action_mutation_completion_state');
            }
            $GLOBALS['rp_mutation_initial_rows'] = $initial;
            return null;
        }

        if ($frame['kind'] === 'release_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['handle'], 'action_mutation_callback_release');
            $release = $frame['payload']['handle'];
            $known = false;
            foreach ($handles as $uid => $candidate) {
                if (rp_mutation_handles_equal($candidate, $release)
                    && !isset($GLOBALS['rp_mutation_released'][$uid])
                ) {
                    $GLOBALS['rp_mutation_released'][$uid] = true;
                    $known = true;
                    break;
                }
            }
            if (!$known) {
                throw new RuntimeException('action_mutation_release_identity');
            }
            $registry->release($release);
            $wire->send('release_ack', ['handle' => $release]);
            continue;
        }

        throw new RuntimeException('action_mutation_unexpected_frame');
    }
}

function rp_mutation_all(string $hookName, mixed ...$args): void
{
    $GLOBALS['rp_mutation_events'][] = [
        'callback' => 'all',
        'hook' => $hookName,
        'args' => func_get_args(),
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_action' => doing_action(),
        'did_action' => did_action(RP_MUTATION_HOOK),
        'stack' => $GLOBALS['wp_current_filter'],
    ];
}

function rp_action_mutation_mutator(): void
{
    $GLOBALS['rp_mutation_events'][] = [
        'callback' => 'mutator',
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_action' => doing_action(),
        'did_action' => did_action(RP_MUTATION_HOOK),
        'current_priority' => $GLOBALS['rp_mutation_hook']->current_priority(),
        'stack' => $GLOBALS['wp_current_filter'],
    ];
    add_action(RP_MUTATION_HOOK, 'rp_action_mutation_added', 15, 0);
}

function rp_action_mutation_added(): void
{
    $GLOBALS['rp_mutation_events'][] = [
        'callback' => 'added',
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_action' => doing_action(),
        'did_action' => did_action(RP_MUTATION_HOOK),
        'current_priority' => $GLOBALS['rp_mutation_hook']->current_priority(),
        'stack' => $GLOBALS['wp_current_filter'],
    ];
}

function rp_action_mutation_tail(): void
{
    $GLOBALS['rp_mutation_events'][] = [
        'callback' => 'tail',
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_action' => doing_action(),
        'did_action' => did_action(RP_MUTATION_HOOK),
        'current_priority' => $GLOBALS['rp_mutation_hook']->current_priority(),
        'stack' => $GLOBALS['wp_current_filter'],
    ];
}

try {
    $wire = new BridgeProtocol();
    $registry = new HandleRegistry(RP_MUTATION_REQUEST, 64, 64);
    $GLOBALS['rp_mutation_wire'] = $wire;
    $GLOBALS['rp_mutation_registry'] = $registry;
    $GLOBALS['rp_mutation_events'] = [];
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];

    add_action('all', 'rp_mutation_all', 10, 10);
    add_action(RP_MUTATION_HOOK, 'rp_action_mutation_mutator', 5, 0);
    add_action(RP_MUTATION_HOOK, 'rp_action_mutation_tail', 20, 0);
    $hook = $GLOBALS['wp_filter'][RP_MUTATION_HOOK];
    $GLOBALS['rp_mutation_hook'] = $hook;
    $GLOBALS['rp_mutation_target_hook'] = $hook;

    $result = do_action(RP_MUTATION_HOOK);
    $state = [
        'events' => $GLOBALS['rp_mutation_events'],
        'action_count' => did_action(RP_MUTATION_HOOK),
        'current_action' => current_action(),
        'current_filter' => current_filter(),
        'doing_action' => doing_action(),
        'stack' => $GLOBALS['wp_current_filter'],
        'hook_state' => rp_mutation_private_state($hook),
        'registered_rows' => array_map(static fn (array $row): array => [
            'priority' => $row['priority'], 'uid' => $row['uid'],
            'accepted_args' => $row['accepted_args'],
            'function' => is_string($row['function']) ? $row['function'] : null,
        ], rp_mutation_table_snapshot($hook)),
    ];
    if ($result !== null
        || $state['action_count'] !== 1
        || $state['current_action'] !== false
        || $state['current_filter'] !== false
        || $state['doing_action'] !== false
        || $state['stack'] !== []
        || $state['hook_state'] !== [
            'nesting_level' => 0, 'iterations' => [], 'current_priority' => [], 'doing_action' => false,
        ]
        || array_column($state['events'], 'callback') !== ['all', 'mutator', 'added', 'tail']
    ) {
        throw new RuntimeException('action_mutation_final_state');
    }
    $wire->send('action_mutation_observed', [
        'target' => RP_MUTATION_TARGET,
        'observation' => $state,
    ]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'release_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['handle'], 'action_mutation_final_release');
            $registry->release($frame['payload']['handle']);
            $wire->send('release_ack', ['handle' => $frame['payload']['handle']]);
            continue;
        }
        if ($frame['kind'] === 'request_complete') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['target', 'result'], 'action_mutation_request_complete');
            if ($frame['payload']['target'] !== RP_MUTATION_TARGET
                || $frame['payload']['result'] !== null
            ) {
                throw new RuntimeException('action_mutation_request_outcome');
            }
            $liveBeforeClose = $registry->liveCount();
            $registry->close();
            if ($liveBeforeClose !== 0 || $registry->liveCount() !== 0) {
                throw new RuntimeException('action_mutation_live_handles');
            }
            $wire->send('guest_done', [
                'observation' => $state,
                'live_handles' => $registry->liveCount(),
                'live_handles_before_close' => $liveBeforeClose,
                'registry_closed' => $registry->isClosed(),
                'frames' => $wire->stats(),
            ]);
            break;
        }
        throw new RuntimeException('action_mutation_guest_unexpected_frame');
    }
} catch (Throwable $error) {
    fail_bridge($error);
}
