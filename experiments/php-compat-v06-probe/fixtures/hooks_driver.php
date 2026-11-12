<?php
declare(strict_types=1);

use RecastPress\V06\Handles\HandleRegistry;
use RecastPress\V06\Handles\RegistryError;

if (count($argv) !== 2 || ($argv[1] ?? null) !== 'hooks') {
    fwrite(STDERR, "hooks fixture requires exactly the hooks mode argument\n");
    exit(2);
}

// In hooks mode bridge_driver.php contributes only the shared protocol helpers.
require_once __DIR__ . '/bridge_driver.php';

function hooks_fail(string $code): never
{
    throw new RuntimeException($code);
}

function hooks_exact_keys(mixed $value, array $expected, string $label): array
{
    if (!is_array($value) || array_is_list($value)) {
        hooks_fail($label . '_object_required');
    }

    $actual = array_keys($value);
    sort($actual);
    sort($expected);
    if ($actual !== $expected) {
        hooks_fail($label . '_schema');
    }

    return $value;
}

function hooks_hex_bytes(mixed $hex, string $label): string
{
    if (!is_string($hex)
        || preg_match('/\A(?:[0-9a-f]{2})*\z/D', $hex) !== 1
    ) {
        hooks_fail($label . '_hex');
    }

    $bytes = hex2bin($hex);
    if (!is_string($bytes) || bin2hex($bytes) !== $hex) {
        hooks_fail($label . '_hex_decode');
    }

    return $bytes;
}

function hooks_same_descriptor(mixed $received, array $expected): bool
{
    if (!is_array($received) || array_is_list($received)) {
        return false;
    }

    $receivedKeys = array_keys($received);
    $expectedKeys = array_keys($expected);
    sort($receivedKeys, SORT_STRING);
    sort($expectedKeys, SORT_STRING);
    if ($receivedKeys !== $expectedKeys) {
        return false;
    }

    ksort($received, SORT_STRING);
    ksort($expected, SORT_STRING);
    return $received === $expected;
}

function hooks_id_string(Closure $callback): string
{
    $id = (string) spl_object_id($callback);
    if (preg_match('/\A[1-9][0-9]*\z/D', $id) !== 1) {
        hooks_fail('callback_id_not_canonical');
    }

    return $id;
}


function hooks_expect_invoke(
    BridgeProtocol $wire,
    HandleRegistry $registry,
    array $frame,
    string $callback,
    string $effect,
    array $descriptor,
    string $hook,
    int $priority,
    string $valueHex,
): Closure {
    expect_kind($frame, 'invoke_php');
    expect_parent_effect($frame, null);
    expect_payload_keys(
        $frame,
        ['callback', 'effect_id', 'handle', 'hook', 'priority', 'value_hex'],
        'hooks_' . $callback . '_invoke',
    );

    $payload = $frame['payload'];
    if ($payload['callback'] !== $callback
        || $payload['effect_id'] !== $effect
        || !hooks_same_descriptor($payload['handle'], $descriptor)
        || $payload['hook'] !== $hook
        || $payload['priority'] !== $priority
        || $payload['value_hex'] !== $valueHex
    ) {
        hooks_fail('hooks_' . $callback . '_invoke_mismatch');
    }

    $resolved = $registry->resolve($payload['handle'], 'closure');
    if (!$resolved instanceof Closure) {
        hooks_fail('hooks_' . $callback . '_not_closure');
    }

    return $resolved;
}

function hooks_assert_closed(
    HandleRegistry $registry,
    array $descriptors,
): bool {
    foreach ($descriptors as $label => $descriptor) {
        try {
            $registry->resolve($descriptor, $descriptor['kind']);
        } catch (RegistryError $error) {
            if ($error->getMessage() !== 'request_closed') {
                hooks_fail('hooks_' . $label . '_wrong_close_error');
            }
            continue;
        }

        hooks_fail('hooks_' . $label . '_resolved_after_close');
    }

    return true;
}

function run_hooks_probe(): void
{
    $wire = new BridgeProtocol();
    $registry = new HandleRegistry('51');
    $item = (object) ['name' => 'root-before'];
    $alias = $item;
    $trace = [];
    $childDescriptor = null;

    // This cell has its own variable. Rebinding it must not rebind the item alias.
    $referenceCell = $item;
    $referenceDescriptor = $registry->retainReference($referenceCell);
    $itemDescriptor = $registry->retainObject($item);

    $rootA = static function (string $value) use (
        $wire,
        $registry,
        $item,
        $alias,
        &$childDescriptor,
        &$trace,
    ): string {
        if ($value !== 'seed:r5') {
            hooks_fail('hooks_root_a_input');
        }
        $trace[] = 'root_a:enter';

        $item->name .= '-child';
        if ($alias !== $item || $item->name !== 'root-before-child') {
            hooks_fail('hooks_root_a_parent_mutation');
        }
        $wire->send('call_core', [
            'operation' => 'child_hook',
            'value_hex' => bin2hex($value),
            'mutate_parent' => true,
            'view' => [
                'name' => $item->name,
                'alias_same' => $alias === $item,
            ],
        ], 'e-root-a');

        while (true) {
            $frame = $wire->receive();
            if ($frame['kind'] === 'invoke_php') {
                $child = hooks_expect_invoke(
                    $wire,
                    $registry,
                    $frame,
                    'child',
                    'e-child',
                    $childDescriptor,
                    'child',
                    5,
                    bin2hex('seed:r5:c3'),
                );
                $childInput = hooks_hex_bytes($frame['payload']['value_hex'], 'hooks_child_input');
                $childValue = $child($childInput);
                $wire->send('resume', [
                    'effect_id' => 'e-child',
                    'result' => [
                        'value_hex' => bin2hex($childValue),
                        'view' => [
                            'name' => $item->name,
                            'alias_same' => $alias === $item,
                        ],
                    ],
                ], 'e-root-a');
                continue;
            }

            if ($frame['kind'] !== 'return') {
                hooks_fail('hooks_child_exchange_frame');
            }
            expect_parent_effect($frame, 'e-root-a');
            expect_payload_keys(
                $frame,
                ['operation', 'value_hex', 'current_hook', 'root_b_removed', 'late_registered'],
                'hooks_child_return',
            );
            $payload = $frame['payload'];
            if ($payload['operation'] !== 'child_hook'
                || $payload['value_hex'] !== bin2hex('seed:r5:c3:php-child:c7')
                || $payload['current_hook'] !== 'root'
                || $payload['root_b_removed'] !== true
                || $payload['late_registered'] !== true
            ) {
                hooks_fail('hooks_child_return_mismatch');
            }

            $childBytes = hooks_hex_bytes($payload['value_hex'], 'hooks_child_return');
            $item->name .= ':root-after-child';
            if ($alias !== $item) {
                hooks_fail('hooks_root_alias_changed_after_child');
            }
            $trace[] = 'root_a:after-child';
            $result = $childBytes . ':php-root';
            $trace[] = 'root_a:exit';
            $wire->send('resume', [
                'effect_id' => 'e-root-a',
                'result' => [
                    'value_hex' => bin2hex($result),
                    'view' => [
                        'name' => $item->name,
                        'alias_same' => $alias === $item,
                    ],
                ],
            ]);

            return $result;
        }
    };

    $rootB = static function (string $value) use ($item, $alias, &$trace): string {
        $trace[] = 'root_b';
        if ($alias !== $item || $item->name !== 'root-before-child:child:root-after-child') {
            hooks_fail('hooks_root_b_snapshot');
        }
        $item->name .= ':b';
        return $value . ':php-b';
    };

    $childCallback = static function (string $value) use ($item, $alias, &$trace): string {
        $trace[] = 'child:enter';
        if ($alias !== $item || $item->name !== 'root-before-child') {
            hooks_fail('hooks_child_parent_state');
        }
        $item->name .= ':child';
        $result = $value . ':php-child';
        $trace[] = 'child:exit';
        return $result;
    };

    $late = static function (string $value) use ($item, $alias, &$trace): string {
        $trace[] = 'late';
        if ($alias !== $item || $item->name !== 'root-before-child:child:root-after-child:b') {
            hooks_fail('hooks_late_current_bucket');
        }
        $item->name .= ':late';
        return $value . ':late';
    };

    $rootADescriptor = $registry->retainObject($rootA);
    $rootARetainedAgain = $registry->retainObject($rootA);
    $rootBDescriptor = $registry->retainObject($rootB);
    $childCallbackDescriptor = $registry->retainObject($childCallback);
    $childDescriptor = $childCallbackDescriptor;
    $lateDescriptor = $registry->retainObject($late);

    if ($rootADescriptor !== $rootARetainedAgain
        || $registry->release($rootADescriptor) !== false
        || $rootA !== $registry->resolve($rootARetainedAgain, 'closure')
        || $rootB !== $registry->resolve($rootBDescriptor, 'closure')
        || $childCallback !== $registry->resolve($childCallbackDescriptor, 'closure')
        || $late !== $registry->resolve($lateDescriptor, 'closure')
        || $item !== $registry->resolve($itemDescriptor, 'object')
    ) {
        hooks_fail('hooks_initial_registry_identity');
    }

    $ids = [
        'root_a' => hooks_id_string($rootA),
        'root_b' => hooks_id_string($rootB),
        'child' => hooks_id_string($childCallback),
        'late' => hooks_id_string($late),
    ];
    if (count(array_unique(array_values($ids), SORT_STRING)) !== count($ids)
        || $rootA === $rootB
        || $rootA === $childCallback
        || $rootA === $late
        || $rootB === $childCallback
        || $rootB === $late
        || $childCallback === $late
        || $rootARetainedAgain !== $rootADescriptor
        || $rootBDescriptor === $childCallbackDescriptor
        || $rootBDescriptor === $lateDescriptor
        || $childCallbackDescriptor === $lateDescriptor
    ) {
        hooks_fail('hooks_distinct_closures');
    }

    $referenceCell = (object) ['name' => 'separate-reference-cell'];
    if ($registry->resolve($referenceDescriptor, 'reference_cell') !== $referenceCell
        || $item !== $alias
        || $item !== $registry->resolve($itemDescriptor, 'object')
    ) {
        hooks_fail('hooks_reference_rebind_changed_item_alias');
    }

    $handles = hooks_exact_keys([
        'root_a' => $rootADescriptor,
        'root_b' => $rootBDescriptor,
        'child' => $childCallbackDescriptor,
        'late' => $lateDescriptor,
        'item' => $itemDescriptor,
    ], ['root_a', 'root_b', 'child', 'late', 'item'], 'hooks_handles');
    $ids = hooks_exact_keys($ids, ['root_a', 'root_b', 'child', 'late'], 'hooks_ids');
    $wire->send('start_root', [
        'operation' => 'hooks_probe',
        'request_generation' => '51',
        'handles' => $handles,
        'ids' => $ids,
        'value_hex' => bin2hex('seed'),
    ]);

    $rootInvoke = $wire->receive();
    $rootCallback = hooks_expect_invoke(
        $wire,
        $registry,
        $rootInvoke,
        'root_a',
        'e-root-a',
        $rootADescriptor,
        'root',
        10,
        bin2hex('seed:r5'),
    );
    $rootResult = $rootCallback(hooks_hex_bytes($rootInvoke['payload']['value_hex'], 'hooks_root_input'));
    if ($rootResult !== 'seed:r5:c3:php-child:c7:php-root') {
        hooks_fail('hooks_root_a_result');
    }

    $rootBInvoke = $wire->receive();
    $rootBResolved = hooks_expect_invoke(
        $wire,
        $registry,
        $rootBInvoke,
        'root_b',
        'e-root-b',
        $rootBDescriptor,
        'root',
        10,
        bin2hex($rootResult),
    );
    $rootBResult = $rootBResolved(
        hooks_hex_bytes($rootBInvoke['payload']['value_hex'], 'hooks_root_b_input'),
    );
    if ($rootBResult !== 'seed:r5:c3:php-child:c7:php-root:php-b') {
        hooks_fail('hooks_root_b_result');
    }
    $wire->send('resume', [
        'effect_id' => 'e-root-b',
        'result' => [
            'value_hex' => bin2hex($rootBResult),
            'view' => [
                'name' => $item->name,
                'alias_same' => $alias === $item,
            ],
        ],
    ]);

    $lateInvoke = $wire->receive();
    $lateResolved = hooks_expect_invoke(
        $wire,
        $registry,
        $lateInvoke,
        'late',
        'e-late',
        $lateDescriptor,
        'root',
        15,
        bin2hex($rootBResult),
    );
    $lateResult = $lateResolved(
        hooks_hex_bytes($lateInvoke['payload']['value_hex'], 'hooks_late_input'),
    );
    $finalValue = 'seed:r5:c3:php-child:c7:php-root:php-b:late:r20';
    if ($lateResult !== 'seed:r5:c3:php-child:c7:php-root:php-b:late'
        || $item->name !== 'root-before-child:child:root-after-child:b:late'
        || $alias !== $item
    ) {
        hooks_fail('hooks_late_result');
    }
    $wire->send('resume', [
        'effect_id' => 'e-late',
        'result' => [
            'value_hex' => bin2hex($lateResult),
            'view' => [
                'name' => $item->name,
                'alias_same' => $alias === $item,
            ],
        ],
    ]);

    $rootReturn = $wire->receive();
    expect_kind($rootReturn, 'return');
    expect_parent_effect($rootReturn, null);
    expect_payload_keys(
        $rootReturn,
        ['operation', 'value_hex', 'root_b_removed', 'late_registered', 'hook_depth', 'current_hook'],
        'hooks_root_return',
    );
    $rootPayload = $rootReturn['payload'];
    if ($rootPayload['operation'] !== 'hooks_probe'
        || $rootPayload['value_hex'] !== bin2hex($finalValue)
        || $rootPayload['root_b_removed'] !== true
        || $rootPayload['late_registered'] !== true
        || $rootPayload['hook_depth'] !== 0
        || $rootPayload['current_hook'] !== null
        || hooks_hex_bytes($rootPayload['value_hex'], 'hooks_final_value') !== $finalValue
        || $item->name !== 'root-before-child:child:root-after-child:b:late'
        || $alias !== $item
    ) {
        hooks_fail('hooks_root_return_mismatch');
    }

    $expectedTrace = [
        'root_a:enter',
        'child:enter',
        'child:exit',
        'root_a:after-child',
        'root_a:exit',
        'root_b',
        'late',
    ];
    if ($trace !== $expectedTrace) {
        hooks_fail('hooks_trace_mismatch');
    }

    $registry->close();
    $descriptors = [
        'root_a' => $rootADescriptor,
        'root_b' => $rootBDescriptor,
        'child' => $childCallbackDescriptor,
        'late' => $lateDescriptor,
        'item' => $itemDescriptor,
        'reference_cell' => $referenceDescriptor,
    ];
    $closedReadRejected = hooks_assert_closed($registry, $descriptors);
    if (!$registry->isClosed()
        || $registry->liveCount() !== 0
        || $alias !== $item
        || $ids !== [
            'root_a' => hooks_id_string($rootA),
            'root_b' => hooks_id_string($rootB),
            'child' => hooks_id_string($childCallback),
            'late' => hooks_id_string($late),
        ]
    ) {
        hooks_fail('hooks_registry_close_state');
    }

    $observation = [
        'alias_same' => $alias === $item,
        'same_closure_handle' => $rootADescriptor === $rootARetainedAgain,
        'distinct_closures' => true,
        'parent_hook_restored' => $rootPayload['hook_depth'] === 0 && $rootPayload['current_hook'] === null,
        'closed_read_rejected' => $closedReadRejected,
        'registry_live_after_close' => $registry->liveCount(),
        'final_name' => $item->name,
        'returned_value_hex' => bin2hex($finalValue),
        'trace' => $trace,
    ];
    $wire->send('request_finished', [
        'status' => 'ok',
        'php_observation' => $observation,
        'transport' => $wire->stats(),
    ]);
}

try {
    run_hooks_probe();
} catch (Throwable $error) {
    fail_bridge($error);
}
