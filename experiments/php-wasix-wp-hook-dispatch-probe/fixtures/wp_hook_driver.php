<?php
declare(strict_types=1);

require_once __DIR__ . '/bridge_driver.php';
require_once __DIR__ . '/class-wp-hook.php';
require_once __DIR__ . '/plugin.php';

use RecastPress\V06\Handles\HandleRegistry;

const RP_HOOK_REQUEST = '81';
const RP_HOOK_TARGET = 'wp-hook-1';

function recastpress_wp_should_dispatch_selected_hook(string $hookName, object $hook): bool
{
    return $hookName === 'rp-probe-filter'
        && isset($GLOBALS['rp_hook_token_by_object'][$hook])
        && $GLOBALS['rp_hook_token_by_object'][$hook] === RP_HOOK_TARGET
        && $hook === $GLOBALS['rp_hook_selected_object'];
}

function rp_hook_table_snapshot(WP_Hook $hook): array
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

function rp_hook_handles_equal(array $left, array $right): bool
{
    ksort($left);
    ksort($right);
    return $left === $right;
}

function rp_hook_table_fingerprint(array $rows): string
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

function recastpress_wp_dispatch_selected_hook(
    string $hookName,
    WP_Hook $hook,
    mixed $value,
    array $args,
    int $nestingLevel,
    Closure $sync,
): mixed {
    $registry = $GLOBALS['rp_hook_registry'];
    $wire = $GLOBALS['rp_hook_wire'];
    $rows = rp_hook_table_snapshot($hook);
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

    $wire->send('dispatch_start', [
        'generation' => RP_HOOK_REQUEST,
        'target' => RP_HOOK_TARGET,
        'hook' => $hookName,
        'value' => $value,
        'extra' => array_slice($args, 1),
        'rows' => $exported,
        'table_fingerprint' => rp_hook_table_fingerprint($exported),
    ]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'invoke_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, [
                'target', 'uid', 'handle', 'priority', 'call_mode', 'args',
            ], 'wp_hook_invoke');
            $payload = $frame['payload'];
            if ($payload['target'] !== RP_HOOK_TARGET
                || !isset($handles[$payload['uid']])
                || !rp_hook_handles_equal($handles[$payload['uid']], $payload['handle'])
                || !is_int($payload['priority'])
                || !in_array($payload['call_mode'], ['zero', 'array'], true)
                || !is_array($payload['args'])
                || ($payload['call_mode'] === 'zero' && $payload['args'] !== [])
                || ($payload['call_mode'] === 'array' && $payload['args'] === [])
            ) {
                throw new RuntimeException('wp_hook_invoke_identity');
            }
            if (!$sync('set_priority', $payload['priority'])) {
                throw new RuntimeException('wp_hook_priority_sync');
            }
            $callback = $registry->resolve($payload['handle'], 'callable');
            $returned = call_user_func_array($callback, $payload['args']);
            $GLOBALS['rp_hook_invocations'][] = $payload['uid'];
            $wire->send('resume_value', [
                'target' => RP_HOOK_TARGET,
                'uid' => $payload['uid'],
                'value' => $returned,
            ]);
            continue;
        }

        if ($frame['kind'] === 'complete') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['target', 'value', 'invocations'], 'wp_hook_complete');
            if ($frame['payload']['target'] !== RP_HOOK_TARGET
                || !is_int($frame['payload']['invocations'])
                || $frame['payload']['invocations'] !== count($rows)
                || count($GLOBALS['rp_hook_invocations']) !== count($rows)
                || !$sync('finish')
            ) {
                throw new RuntimeException('wp_hook_completion_state');
            }
            if (rp_hook_table_snapshot($hook) !== $before) {
                throw new RuntimeException('wp_hook_table_mutated_during_dispatch');
            }
            $GLOBALS['rp_hook_final'] = $frame['payload']['value'];
            $GLOBALS['rp_hook_imported_rows'] = $exported;
            return $frame['payload']['value'];
        }

        throw new RuntimeException('wp_hook_unexpected_frame');
    }
}

function rp_probe_callback_a(string $value, string $left, string $right): string
{
    $GLOBALS['rp_hook_events'][] = [
        'callback' => 'a', 'args' => [$value, $left, $right],
        'current_filter' => current_filter(), 'doing_filter' => doing_filter('rp-probe-filter'),
        'did_filter' => did_filter('rp-probe-filter'),
        'current_priority' => $GLOBALS['rp_hook_selected_object']->current_priority(),
    ];
    return "A({$value}|{$left}|{$right})";
}

function rp_probe_callback_b(string $value, string $left, string $right): string
{
    $GLOBALS['rp_hook_events'][] = [
        'callback' => 'b', 'args' => [$value, $left, $right],
        'current_filter' => current_filter(), 'doing_filter' => doing_filter('rp-probe-filter'),
        'did_filter' => did_filter('rp-probe-filter'),
        'current_priority' => $GLOBALS['rp_hook_selected_object']->current_priority(),
    ];
    return "B({$value}|{$left}|{$right})";
}

function rp_probe_all(string $hookName, mixed ...$args): void
{
    $GLOBALS['rp_hook_events'][] = [
        'callback' => 'all', 'hook' => $hookName, 'args' => $args,
        'current_filter' => current_filter(), 'doing_filter' => doing_filter($hookName),
        'did_filter' => did_filter($hookName),
    ];
}

try {
    $wire = new BridgeProtocol();
    $registry = new HandleRegistry(RP_HOOK_REQUEST, 64, 64);
    $GLOBALS['rp_hook_wire'] = $wire;
    $GLOBALS['rp_hook_registry'] = $registry;
    $GLOBALS['rp_hook_events'] = [];
    $GLOBALS['rp_hook_invocations'] = [];
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];

    add_filter('all', 'rp_probe_all', 10, 10);
    add_filter('rp-probe-filter', 'rp_probe_callback_a', 5, 3);
    $zero = static function (): string {
        $GLOBALS['rp_hook_events'][] = [
            'callback' => 'zero', 'args' => [],
            'current_filter' => current_filter(), 'doing_filter' => doing_filter('rp-probe-filter'),
            'did_filter' => did_filter('rp-probe-filter'),
            'current_priority' => $GLOBALS['rp_hook_selected_object']->current_priority(),
        ];
        return 'Z';
    };
    add_filter('rp-probe-filter', $zero, 5, 0);
    add_filter('rp-probe-filter', 'rp_probe_callback_b', 10, 3);

    $selected = $GLOBALS['wp_filter']['rp-probe-filter'];
    $tokens = new WeakMap();
    $tokens[$selected] = RP_HOOK_TARGET;
    $GLOBALS['rp_hook_token_by_object'] = $tokens;
    $GLOBALS['rp_hook_selected_object'] = $selected;

    $result = apply_filters('rp-probe-filter', 'seed', 'left', 'right');
    if ($result !== 'B(Z|left|right)'
        || current_filter() !== false
        || doing_filter()
        || did_filter('rp-probe-filter') !== 1
        || $selected->current_priority() !== false
    ) {
        throw new RuntimeException('wp_hook_final_observation');
    }

    $events = $GLOBALS['rp_hook_events'];
    $selectedEvents = array_values(array_filter($events, static fn (array $event): bool => ($event['callback'] ?? null) !== 'all'));
    if (array_column($selectedEvents, 'callback') !== ['a', 'zero', 'b']
        || array_column($selectedEvents, 'args') !== [['seed', 'left', 'right'], [], ['Z', 'left', 'right']]
        || array_column($selectedEvents, 'current_filter') !== ['rp-probe-filter', 'rp-probe-filter', 'rp-probe-filter']
        || array_column($selectedEvents, 'doing_filter') !== [true, true, true]
        || array_column($selectedEvents, 'did_filter') !== [1, 1, 1]
        || array_column($selectedEvents, 'current_priority') !== [5, 5, 10]
    ) {
        throw new RuntimeException('wp_hook_callback_observation');
    }

    $wire->send('request_complete', ['target' => RP_HOOK_TARGET, 'result' => $result]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'release_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['handle'], 'wp_hook_release');
            $registry->release($frame['payload']['handle']);
            $wire->send('release_ack', ['handle' => $frame['payload']['handle']]);
            continue;
        }
        if ($frame['kind'] === 'request_complete') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['target', 'result'], 'wp_hook_request_complete');
            if ($frame['payload']['target'] !== RP_HOOK_TARGET || $frame['payload']['result'] !== $result) {
                throw new RuntimeException('wp_hook_request_result');
            }
            $liveHandlesBeforeClose = $registry->liveCount();
            $registry->close();
            if ($registry->liveCount() !== 0) {
                throw new RuntimeException('wp_hook_live_handles_after_close');
            }
            $wire->send('guest_done', [
                'result' => $result,
                'events' => $GLOBALS['rp_hook_events'],
                'filter_count' => did_filter('rp-probe-filter'),
                'current_filter' => current_filter(),
                'doing_filter' => doing_filter(),
                'current_priority' => $selected->current_priority(),
                'table_fingerprint' => rp_hook_table_fingerprint($GLOBALS['rp_hook_imported_rows']),
                'live_handles' => $registry->liveCount(),
                'live_handles_before_close' => $liveHandlesBeforeClose,
                'invocations' => $GLOBALS['rp_hook_invocations'],
                'frames' => $wire->stats(),
            ]);
            break;
        }
        throw new RuntimeException('wp_hook_guest_unexpected_frame');
    }
} catch (Throwable $error) {
    fail_bridge($error);
}
