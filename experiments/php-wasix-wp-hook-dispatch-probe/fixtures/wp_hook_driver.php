<?php
declare(strict_types=1);

fwrite(STDERR, "RP_HOOK_PHASE=driver_start\n");
require_once __DIR__ . '/bridge_driver.php';
fwrite(STDERR, "RP_HOOK_PHASE=bridge_loaded\n");
$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];
require_once __DIR__ . '/plugin.php';
fwrite(STDERR, "RP_HOOK_PHASE=plugin_loaded\n");

use RecastPress\V06\Handles\HandleRegistry;

const RP_HOOK_REQUEST = '81';
const RP_HOOK_TARGET = 'wp-hook-1';

function recastpress_wp_should_dispatch_selected_hook(string $hookName, object $hook): bool
{
    return $hookName === ($GLOBALS['rp_hook_ref_array_mode'] ? 'rp-probe-ref-array' : 'rp-probe-filter')
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
    $argumentFrame = null;
    if ($GLOBALS['rp_hook_ref_array_mode']) {
        if (!array_is_list($args) || $args === []) {
            throw new RuntimeException('wp_hook_reference_frame_shape');
        }
        $argumentFrame = $registry->retainArgument($args);
        $GLOBALS['rp_hook_argument_frame_handle'] = $argumentFrame;
    }
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
        'argument_frame' => $argumentFrame,
        'rows' => $exported,
        'table_fingerprint' => rp_hook_table_fingerprint($exported),
    ]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'invoke_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, [
                'target', 'uid', 'handle', 'priority', 'call_mode', 'args', 'value', 'argument_frame',
            ], 'wp_hook_invoke');
            $payload = $frame['payload'];
            if ($payload['target'] !== RP_HOOK_TARGET
                || !isset($handles[$payload['uid']])
                || !rp_hook_handles_equal($handles[$payload['uid']], $payload['handle'])
                || !is_int($payload['priority'])
                || !in_array($payload['call_mode'], ['zero', 'array'], true)
                || !is_array($payload['args'])
                || ($GLOBALS['rp_hook_ref_array_mode']
                    ? (!is_array($payload['argument_frame'])
                        || !rp_hook_handles_equal(
                            $payload['argument_frame'],
                            $GLOBALS['rp_hook_argument_frame_handle'],
                        ))
                    : $payload['argument_frame'] !== null)
                || ($payload['call_mode'] === 'zero' && $payload['args'] !== [])
                || ($payload['call_mode'] === 'array' && $payload['args'] === [])
            ) {
                throw new RuntimeException('wp_hook_invoke_identity');
            }
            if (!is_string($payload['value'])
                || ($payload['args'] !== [] && $payload['args'][0] !== $payload['value'])
            ) {
                throw new RuntimeException('wp_hook_invoke_accumulator');
            }
            if (!$sync('set_priority', $payload['priority'])) {
                throw new RuntimeException('wp_hook_priority_sync');
            }
            $callback = $registry->resolve($payload['handle'], 'callable');
            if ($GLOBALS['rp_hook_ref_array_mode']) {
                $arguments = $registry->resolve($payload['argument_frame'], 'argument');
                if (!array_is_list($arguments)) {
                    throw new RuntimeException('wp_hook_reference_frame_not_list');
                }
                $arguments[0] = $payload['value'];
                $callbackArgs = [];
                foreach ($payload['args'] as $index => $argument) {
                    if (!array_key_exists($index, $arguments)) {
                        throw new RuntimeException('wp_hook_reference_argument_missing');
                    }
                    if ($index === 0) {
                        if ($argument !== $payload['value']) {
                            throw new RuntimeException('wp_hook_reference_accumulator_mismatch');
                        }
                    } else {
                        $argumentKeys = is_array($argument) ? array_keys($argument) : [];
                        sort($argumentKeys);
                        if (!is_array($argument)
                            || $argumentKeys !== ['handle', 'index', 'type']
                            || $argument['type'] !== 'argument_frame_slot'
                            || $argument['index'] !== $index
                            || !rp_hook_handles_equal($argument['handle'], $payload['argument_frame'])
                        ) {
                            throw new RuntimeException('wp_hook_reference_slot_descriptor');
                        }
                    }
                    $callbackArgs[] = &$arguments[$index];
                }
                $returned = call_user_func_array($callback, $callbackArgs);
                unset($callbackArgs, $arguments);
            } else {
                foreach ($payload['args'] as $argument) {
                    if (!is_string($argument)) {
                        throw new RuntimeException('wp_hook_invoke_argument_type');
                    }
                }
                $returned = call_user_func_array($callback, $payload['args']);
            }
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

function rp_ref_array_first(string &$value, string &$extra): string
{
    $GLOBALS['rp_hook_events'][] = [
        'callback' => 'first', 'args' => [$value, $extra],
        'current_filter' => current_filter(), 'doing_filter' => doing_filter('rp-probe-ref-array'),
        'did_filter' => did_filter('rp-probe-ref-array'),
        'current_priority' => $GLOBALS['rp_hook_selected_object']->current_priority(),
    ];
    $value = 'value-mutated-by-first';
    $extra = 'extra-mutated-by-first';
    return 'replacement-from-first';
}

function rp_ref_array_truncated(string $value, mixed ...$extra): string
{
    $args = func_get_args();
    $GLOBALS['rp_hook_events'][] = [
        'callback' => 'truncated', 'args' => $args, 'received_arg_count' => count($args),
        'current_filter' => current_filter(), 'doing_filter' => doing_filter('rp-probe-ref-array'),
        'did_filter' => did_filter('rp-probe-ref-array'),
        'current_priority' => $GLOBALS['rp_hook_selected_object']->current_priority(),
    ];
    if ($extra !== []) {
        throw new RuntimeException('wp_hook_ref_array_truncated_callback_received_extra');
    }
    return $value;
}

function rp_ref_array_all(string $hookName, array $arguments): void
{
    $GLOBALS['rp_hook_events'][] = [
        'callback' => 'all', 'hook' => $hookName, 'args' => [$arguments[0], $arguments[1]],
        'current_filter' => current_filter(), 'doing_filter' => doing_filter($hookName),
        'did_filter' => did_filter($hookName),
    ];
}

function rp_ref_array_second(string &$value, string &$extra): string
{
    $GLOBALS['rp_hook_events'][] = [
        'callback' => 'second', 'args' => [$value, $extra],
        'current_filter' => current_filter(), 'doing_filter' => doing_filter('rp-probe-ref-array'),
        'did_filter' => did_filter('rp-probe-ref-array'),
        'current_priority' => $GLOBALS['rp_hook_selected_object']->current_priority(),
    ];
    $extra = 'extra-mutated-by-second';
    return $value . '|' . $extra;
}

try {
    fwrite(STDERR, "RP_HOOK_PHASE=protocol_start\n");
    $refArrayMode = ($argv[1] ?? '') === 'ref-array';
    $hookName = $refArrayMode ? 'rp-probe-ref-array' : 'rp-probe-filter';
    $wire = new BridgeProtocol();
    $registry = new HandleRegistry(RP_HOOK_REQUEST, 64, 64);
    $GLOBALS['rp_hook_wire'] = $wire;
    $GLOBALS['rp_hook_registry'] = $registry;
    $GLOBALS['rp_hook_events'] = [];
    $GLOBALS['rp_hook_invocations'] = [];
    $GLOBALS['rp_hook_ref_array_mode'] = $refArrayMode;
    $GLOBALS['rp_hook_name'] = $hookName;
    $GLOBALS['rp_hook_argument_frame_handle'] = null;
    $GLOBALS['rp_hook_argument_frame_release_acked'] = false;
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];

    if ($refArrayMode) {
        add_filter('all', 'rp_ref_array_all', 10, 2);
        add_filter($hookName, 'rp_ref_array_truncated', 1, 1);
        add_filter($hookName, 'rp_ref_array_first', 5, 2);
        add_filter($hookName, 'rp_ref_array_second', 10, 2);
    } else {
        add_filter('all', 'rp_probe_all', 10, 10);
        add_filter($hookName, 'rp_probe_callback_a', 5, 3);
        $zero = static function (): string {
            $GLOBALS['rp_hook_events'][] = [
                'callback' => 'zero', 'args' => [],
                'current_filter' => current_filter(), 'doing_filter' => doing_filter('rp-probe-filter'),
                'did_filter' => did_filter('rp-probe-filter'),
                'current_priority' => $GLOBALS['rp_hook_selected_object']->current_priority(),
            ];
            return 'Z';
        };
        add_filter($hookName, $zero, 5, 0);
        add_filter($hookName, 'rp_probe_callback_b', 10, 3);
    }
    fwrite(STDERR, "RP_HOOK_PHASE=callbacks_registered\n");

    $selected = $GLOBALS['wp_filter'][$hookName];
    $tokens = new WeakMap();
    $tokens[$selected] = RP_HOOK_TARGET;
    $GLOBALS['rp_hook_token_by_object'] = $tokens;
    $GLOBALS['rp_hook_selected_object'] = $selected;

    fwrite(STDERR, "RP_HOOK_PHASE=apply_filters_start\n");
    if ($refArrayMode) {
        $value = 'seed';
        $extra = 'left';
        $arguments = [&$value, &$extra];
        $result = apply_filters_ref_array($hookName, $arguments);
    } else {
        $result = apply_filters($hookName, 'seed', 'left', 'right');
    }
    fwrite(STDERR, "RP_HOOK_PHASE=apply_filters_done\n");
    if ($result !== ($refArrayMode ? 'replacement-from-first|extra-mutated-by-second' : 'B(Z|left|right)')
        || current_filter() !== false
        || doing_filter()
        || did_filter($hookName) !== 1
        || $selected->current_priority() !== false
    ) {
        throw new RuntimeException('wp_hook_final_observation');
    }

    if ($refArrayMode) {
        $selectedEvents = array_values(array_filter($GLOBALS['rp_hook_events'], static fn (array $event): bool => ($event['callback'] ?? null) !== 'all'));
        if (array_column($selectedEvents, 'callback') !== ['truncated', 'first', 'second']
            || array_column($selectedEvents, 'args') !== [
                ['seed'],
                ['seed', 'left'],
                ['replacement-from-first', 'extra-mutated-by-first'],
            ]
            || ($selectedEvents[0]['received_arg_count'] ?? null) !== 1
            || array_column($selectedEvents, 'current_filter') !== [$hookName, $hookName, $hookName]
            || array_column($selectedEvents, 'doing_filter') !== [true, true, true]
            || array_column($selectedEvents, 'did_filter') !== [1, 1, 1]
            || array_column($selectedEvents, 'current_priority') !== [1, 5, 10]
            || $value !== 'replacement-from-first'
            || $extra !== 'extra-mutated-by-second'
            || ($GLOBALS['rp_hook_events'][0]['args'] ?? null) !== ['seed', 'left']
        ) {
            throw new RuntimeException('wp_hook_ref_array_observation');
        }
    }

    $events = $GLOBALS['rp_hook_events'];
    $selectedEvents = array_values(array_filter($events, static fn (array $event): bool => ($event['callback'] ?? null) !== 'all'));
    if (!$refArrayMode) {
    if (array_column($selectedEvents, 'callback') !== ['a', 'zero', 'b']
            || array_column($selectedEvents, 'args') !== [['seed', 'left', 'right'], [], ['Z', 'left', 'right']]
            || array_column($selectedEvents, 'current_filter') !== ['rp-probe-filter', 'rp-probe-filter', 'rp-probe-filter']
            || array_column($selectedEvents, 'doing_filter') !== [true, true, true]
            || array_column($selectedEvents, 'did_filter') !== [1, 1, 1]
            || array_column($selectedEvents, 'current_priority') !== [5, 5, 10]
        ) {
            throw new RuntimeException('wp_hook_callback_observation');
        }
    }

    $wire->send('request_complete', ['target' => RP_HOOK_TARGET, 'result' => $result]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'release_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['handle'], 'wp_hook_release');
            $released = $registry->release($frame['payload']['handle']);
            if ($GLOBALS['rp_hook_ref_array_mode']
                && $frame['payload']['handle']['kind'] === 'argument'
                && rp_hook_handles_equal($frame['payload']['handle'], $GLOBALS['rp_hook_argument_frame_handle'])
            ) {
                if (!$released) {
                    throw new RuntimeException('wp_hook_argument_frame_release_not_final');
                }
                $GLOBALS['rp_hook_argument_frame_release_acked'] = true;
            }
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
                'filter_count' => did_filter($hookName),
                'current_filter' => current_filter(),
                'doing_filter' => doing_filter(),
                'current_priority' => $selected->current_priority(),
                'table_fingerprint' => rp_hook_table_fingerprint($GLOBALS['rp_hook_imported_rows']),
                'caller_value' => $GLOBALS['rp_hook_ref_array_mode'] ? $value : null,
                'caller_extra' => $GLOBALS['rp_hook_ref_array_mode'] ? $extra : null,
                'live_handles' => $registry->liveCount(),
                'live_handles_before_close' => $liveHandlesBeforeClose,
                'argument_frame_released_before_close' => !$GLOBALS['rp_hook_ref_array_mode']
                    || $GLOBALS['rp_hook_argument_frame_release_acked'],
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
