<?php
declare(strict_types=1);

if (($argv[1] ?? null) !== 'publish' || count($argv) !== 2) {
    fwrite(STDERR, "publish fixture accepts only argv[1]=publish\n");
    exit(2);
}

require_once __DIR__ . '/bridge_driver.php';

/** Observe only the fixed public cells used by this publish fixture. */
function publish_live(object $item, array $settings, array $items, array $copy, object $global): array {
    return [
        'item' => ['count' => $item->count, 'unknown' => $item->unknown, 'final_marker' => $item->final_marker],
        'settings' => ['enabled' => $settings['enabled']],
        'items' => array_values($items),
        'alias_same' => $GLOBALS['rp_publish_alias'] === $item,
        'property_reference_value' => $GLOBALS['rp_publish_count'],
        'array_reference_value' => $GLOBALS['rp_publish_entry'],
        'copy_before' => array_values($copy),
        'global_name' => $global->name,
    ];
}

/** JSON object key order is irrelevant; scalar types and list order are exact. */
function publish_view_matches(array $actual, array $expected): bool {
    if (array_is_list($expected)) return $actual === $expected;
    $actualKeys = array_keys($actual); sort($actualKeys);
    $expectedKeys = array_keys($expected); sort($expectedKeys);
    if ($actualKeys !== $expectedKeys) return false;
    foreach ($expected as $key => $value) {
        if (is_array($value)) {
            if (!is_array($actual[$key]) || !publish_view_matches($actual[$key], $value)) return false;
        } elseif ($actual[$key] !== $value) {
            return false;
        }
    }
    return true;
}

/** One shared closed-world validator and applier for every incoming publish frame. */
function attempt_publish(array $payload, int $observation, array &$usedIds, object $item, array &$items): ?string {
    expect_payload_keys(['payload' => $payload], ['publish_id', 'expected_observation', 'writes'], 'publish_input');
    if (!is_string($payload['publish_id']) || !is_int($payload['expected_observation'])) return 'invalid_envelope';
    if ($payload['expected_observation'] !== $observation) return 'stale_observation';
    if (isset($usedIds[$payload['publish_id']])) return 'replayed_publish';
    $writes = $payload['writes'];
    if (!is_array($writes) || !array_is_list($writes) || count($writes) < 1 || count($writes) > 2) return 'invalid_batch';

    // Validate every record and target before the first write. JSON object key order is irrelevant.
    $normalized = [];
    foreach ($writes as $write) {
        if (!is_array($write) || array_is_list($write)) return 'invalid_write_schema';
        $keys = array_keys($write); sort($keys);
        if ($keys !== ['field', 'target', 'value']) return 'invalid_write_schema';
        if (!is_string($write['target']) || !is_string($write['field'])) return 'invalid_target';
        if ($write['target'] === 'item' && in_array($write['field'], ['count', 'final_marker'], true)) {
            if (!is_int($write['value'])) return 'invalid_value_type';
        } elseif ($write['target'] === 'items' && $write['field'] === '1') {
            if (!is_int($write['value'])) return 'invalid_value_type';
        } else {
            return 'invalid_target';
        }
        $normalized[] = [$write['target'], $write['field'], $write['value']];
    }

    foreach ($normalized as [$target, $field, $value]) {
        if ($target === 'item') $item->{$field} = $value;
        else $items[1] = $value;
    }
    $usedIds[$payload['publish_id']] = true;
    return null;
}

function reject_publish(BridgeProtocol $wire, array $frame, string $reason, int $observation, array $view): void {
    expect_kind($frame, 'publish');
    expect_parent_effect($frame, 'e-root');
    $wire->send('publish_reject', ['publish_id' => $frame['payload']['publish_id'], 'reason' => $reason, 'observation' => $observation, 'view' => $view], 'e-root');
}

function run_publish_probe(): void {
    $wire = new BridgeProtocol();
    $item = (object)['count' => 3, 'unknown' => 'keep', 'final_marker' => null];
    $item->name = 'original';
    $alias = $item;
    $GLOBALS['rp_publish_alias'] = $alias;
    $GLOBALS['rp_publish_count'] =& $item->count;
    $item->settings = ['enabled' => false];
    $settings =& $item->settings;
    $item->items = [2, 4];
    $copy = $item->items;
    // Extending the live array separates its COW copy; the retained copy stays [2,4].
    $item->items[2] = 0;
    $items =& $item->items;
    $GLOBALS['rp_publish_entry'] =& $items[1];
    $GLOBALS['rp_publish_global'] = $item;
    $observation = 1;
    $trace = [];
    $usedPublishIds = [];

    $initial = publish_live($item, $settings, $items, $copy, $GLOBALS['rp_publish_global']);
    $wire->send('start_root', ['operation' => 'publish_probe', 'observation' => $observation, 'view' => $initial]);

    // Root publish happens before the first PHP callback. It is fully preflighted before mutation.
    $rootPublish = $wire->receive();
    expect_kind($rootPublish, 'publish');
    expect_parent_effect($rootPublish, null);
    $rootCount = $item->count + 1;
    $rootIndex = $items[1] + $item->count;
    $rootReject = attempt_publish($rootPublish['payload'], $observation, $usedPublishIds, $item, $items);
    if ($rootReject !== null || $item->count !== $rootCount || $items[1] !== $rootIndex) throw new RuntimeException('root_publish_apply_or_derived_values');
    $observation = 2;
    $rootView = publish_live($item, $settings, $items, $copy, $GLOBALS['rp_publish_global']);
    $wire->send('publish_ack', ['publish_id' => 'p-root', 'applied' => true, 'observation' => $observation, 'view' => $rootView]);

    $rootInvoke = $wire->receive();
    expect_kind($rootInvoke, 'invoke_php');
    expect_parent_effect($rootInvoke, null);
    expect_payload_keys($rootInvoke, ['callback', 'effect_id'], 'publish_root_invoke');
    if ($rootInvoke['payload']['callback'] !== 'publish_root_callback' || $rootInvoke['payload']['effect_id'] !== 'e-root') throw new RuntimeException('publish_root_not_allowlisted');
    if ($item->count !== $rootCount || $GLOBALS['rp_publish_count'] !== $rootCount || $GLOBALS['rp_publish_entry'] !== $rootIndex || $items[1] !== $rootIndex) throw new RuntimeException('root_callback_did_not_see_initial_publish');

    // Fixed callback mutates live PHP state, rebinds the global, then calls Rust.
    $item->unknown = 'php-root';
    $settings['enabled'] = true;
    $GLOBALS['rp_publish_global'] = (object)['name' => 'replacement'];
    $observation = 3;
    $trace[] = 'root:before-child';
    $childView = publish_live($item, $settings, $items, $copy, $GLOBALS['rp_publish_global']);
    $wire->send('call_core', ['operation' => 'publish_child', 'observation' => $observation, 'view' => $childView], 'e-root');

    $childPublish = $wire->receive();
    expect_kind($childPublish, 'publish');
    expect_parent_effect($childPublish, 'e-root');
    $childCount = $item->count + 1;
    $childIndex = $items[1] + $item->count;
    $childReject = attempt_publish($childPublish['payload'], $observation, $usedPublishIds, $item, $items);
    if ($childReject !== null || $item->count !== $childCount || $items[1] !== $childIndex) throw new RuntimeException('child_publish_apply_or_derived_values');
    $observation = 4;
    $publishedView = publish_live($item, $settings, $items, $copy, $GLOBALS['rp_publish_global']);
    $wire->send('publish_ack', ['publish_id' => 'p-child', 'applied' => true, 'observation' => $observation, 'view' => $publishedView], 'e-root');

    // Probe stale, replay and all-batch preflight rejection against independent live snapshots.
    foreach ([['p-stale', 3, 'stale_observation'], ['p-child', 5, 'replayed_publish']] as [$id, $expected, $reason]) {
        $negative = $wire->receive();
        expect_kind($negative, 'publish');
        $before = publish_live($item, $settings, $items, $copy, $GLOBALS['rp_publish_global']);
        if (($negative['payload']['publish_id'] ?? null) !== $id || ($negative['payload']['expected_observation'] ?? null) !== $expected) throw new RuntimeException('negative_publish_identity');
        $actualReason = attempt_publish($negative['payload'], $observation, $usedPublishIds, $item, $items);
        if ($actualReason !== $reason) throw new RuntimeException('negative_publish_guard_reason');
        $after = publish_live($item, $settings, $items, $copy, $GLOBALS['rp_publish_global']);
        if ($before !== $after) throw new RuntimeException('negative_publish_changed_live_state');
        $observation++;
        reject_publish($wire, $negative, $reason, $observation, $after);
    }
    $malformed = $wire->receive();
    expect_kind($malformed, 'publish');
    $before = publish_live($item, $settings, $items, $copy, $GLOBALS['rp_publish_global']);
    $writes = $malformed['payload']['writes'] ?? null;
    if (($malformed['payload']['publish_id'] ?? null) !== 'p-atomic' || ($malformed['payload']['expected_observation'] ?? null) !== 6
        || !is_array($writes) || count($writes) !== 2
        || ($writes[0]['target'] ?? null) !== 'item' || ($writes[0]['field'] ?? null) !== 'count' || ($writes[0]['value'] ?? null) !== 999
        || ($writes[1]['target'] ?? null) !== 'unknown' || ($writes[1]['field'] ?? null) !== 'oops' || ($writes[1]['value'] ?? null) !== 1) throw new RuntimeException('atomic_negative_batch_shape');
    $actualReason = attempt_publish($malformed['payload'], $observation, $usedPublishIds, $item, $items);
    if ($actualReason !== 'invalid_target') throw new RuntimeException('atomic_preflight_did_not_reject_invalid_later_target');
    // The shared validator saw a valid first write and rejected the later target before assigning either.
    $after = publish_live($item, $settings, $items, $copy, $GLOBALS['rp_publish_global']);
    if ($before !== $after) throw new RuntimeException('atomic_negative_batch_mutated');
    $observation++;
    reject_publish($wire, $malformed, 'invalid_target', $observation, $after);

    $nested = $wire->receive();
    expect_kind($nested, 'invoke_php');
    expect_parent_effect($nested, null);
    expect_payload_keys($nested, ['callback', 'effect_id'], 'publish_nested_invoke');
    if ($nested['payload']['callback'] !== 'nested_publish_callback' || $nested['payload']['effect_id'] !== 'e-child') throw new RuntimeException('publish_nested_not_allowlisted');
    if ($item->count !== $childCount || $items[1] !== $childIndex || $GLOBALS['rp_publish_count'] !== $childCount || $GLOBALS['rp_publish_entry'] !== $childIndex) throw new RuntimeException('nested_callback_did_not_see_child_publish');
    $item->unknown = 'php-nested';
    $items[2] = 77;
    $observation = 8;
    $trace[] = 'nested:mutated';
    $nestedView = publish_live($item, $settings, $items, $copy, $GLOBALS['rp_publish_global']);
    $wire->send('resume', ['effect_id' => 'e-child', 'result' => ['observation' => $observation, 'view' => $nestedView, 'published_seen' => true]], 'e-root');

    $childReturn = $wire->receive();
    expect_kind($childReturn, 'return');
    expect_parent_effect($childReturn, 'e-root');
    expect_payload_keys($childReturn, ['operation', 'observation'], 'publish_child_return');
    if ($childReturn['payload']['operation'] !== 'publish_child') throw new RuntimeException('publish_child_return_operation');

    // The outer callback resumes against current PHP memory; it never writes its old snapshot back.
    $item->unknown = 'php-outer';
    $observation = 9;
    $trace[] = 'root:after-child';
    $outerView = publish_live($item, $settings, $items, $copy, $GLOBALS['rp_publish_global']);
    $wire->send('resume', ['effect_id' => 'e-root', 'result' => ['observation' => $observation, 'view' => $outerView, 'global_name' => 'replacement']]);

    $finalPublish = $wire->receive();
    expect_kind($finalPublish, 'publish');
    expect_parent_effect($finalPublish, null);
    $marker = $item->count + $items[1] + $items[2] + strlen($GLOBALS['rp_publish_global']->name);
    if (($finalPublish['payload']['expected_observation'] ?? null) !== 9 || $marker !== 104) throw new RuntimeException('final_publish_observation_or_live_derived_value');
    $finalReject = attempt_publish($finalPublish['payload'], $observation, $usedPublishIds, $item, $items);
    if ($finalReject !== null || $item->final_marker !== $marker) throw new RuntimeException('final_publish_apply_or_derived_value');
    if ($marker !== 104) throw new RuntimeException('final_marker_not_derived_from_latest_heap');
    $observation = 10;
    $finalView = publish_live($item, $settings, $items, $copy, $GLOBALS['rp_publish_global']);
    $wire->send('publish_ack', ['publish_id' => 'p-final', 'applied' => true, 'observation' => $observation, 'view' => $finalView]);

    $rootReturn = $wire->receive();
    expect_kind($rootReturn, 'return');
    expect_parent_effect($rootReturn, null);
    expect_payload_keys($rootReturn, ['operation', 'observation'], 'publish_root_return');
    $returnedView = $rootReturn['payload']['observation']['view'] ?? null;
    if ($rootReturn['payload']['operation'] !== 'publish_probe' || !is_array($returnedView)
        || !publish_view_matches($returnedView, $finalView)) throw new RuntimeException('publish_root_return_live_view');
    if ($finalView['item']['count'] !== $childCount || $finalView['item']['unknown'] !== 'php-outer' || $finalView['item']['final_marker'] !== $marker
        || $finalView['items'] !== [2, $childIndex, 77] || $finalView['copy_before'] !== [2, 4] || !$finalView['alias_same'] || $finalView['property_reference_value'] !== $childCount || $finalView['array_reference_value'] !== $childIndex) throw new RuntimeException('publish_final_live_heap_mismatch');

    $wire->send('request_finished', ['status' => 'ok', 'php_observation' => [
        'view' => $finalView, 'trace' => $trace, 'negative_publish_rejections' => 3,
        'alias_same_object' => $alias === $item, 'reference_cell_same_value' => $GLOBALS['rp_publish_count'] === $item->count && $GLOBALS['rp_publish_entry'] === $items[1],
        'copy_on_write_preserved' => $copy === [2, 4], 'global_rebound_without_alias_rebind' => $GLOBALS['rp_publish_global']->name === 'replacement' && $alias->unknown === 'php-outer',
    ], 'transport' => $wire->stats()]);
}

try {
    run_publish_probe();
} catch (Throwable $error) {
    fail_bridge($error);
}
