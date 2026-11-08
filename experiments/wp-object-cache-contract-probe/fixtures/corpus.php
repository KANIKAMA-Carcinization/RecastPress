<?php
declare(strict_types=1);

/** Ordered PHP map rows preserve both integer and string keys on the JSON wire. */
function rp_cache_map(array $values): array {
    $rows = [];
    foreach ($values as $key => $value) {
        $rows[] = ['key' => $key, 'value' => $value];
    }
    return $rows;
}

function rp_cache_lookup(int|string $key, string $group = '', bool $force = false): array {
    $found = 'unchanged';
    $value = wp_cache_get($key, $group, $force, $found);
    return [$found, $value];
}

function rp_cache_contract(): array {
    $rows = [];
    $observe = static function (string $name, mixed $actual, mixed $expected) use (&$rows): void {
        if ($actual !== $expected || array_key_exists($name, $rows)) {
            throw new RuntimeException($name);
        }
        $rows[$name] = $actual;
    };
    $miss = [false, false];
    $global = 'rp-cache-global';
    $runtime = 'rp-cache-nonpersistent';

    $observe('multisite_profile', is_multisite(), true);
    $observe('global_groups_registration', wp_cache_add_global_groups([$global]), null);
    $observe('nonpersistent_groups_registration', wp_cache_add_non_persistent_groups([$runtime]), null);
    $observe('fresh_process_start', [
        rp_cache_lookup('freshness', 'rp-cache-local'),
        rp_cache_lookup('freshness', $global),
        rp_cache_lookup('freshness', $runtime),
    ], [$miss, $miss, $miss]);

    $observe('missing', rp_cache_lookup('value'), $miss);
    $observe('add_false', wp_cache_add('value', false), true);
    $observe('add_present_false', wp_cache_add('value', 1), false);
    $observe('present_false', rp_cache_lookup('value', 'default'), [true, false]);
    $observe('force_present_false', rp_cache_lookup('value', '', true), [true, false]);
    $observe('same_key_other_group', rp_cache_lookup('value', 'rp-cache-other'), $miss);
    $observe('replace_present_null', wp_cache_replace('value', null), true);
    $observe('present_null', rp_cache_lookup('value'), [true, null]);
    $observe('add_present_null', wp_cache_add('value', false), false);
    $observe('delete_present_null', wp_cache_delete('value'), true);
    $observe('delete_missing', wp_cache_delete('value'), false);
    $observe('replace_missing', wp_cache_replace('value', 4), false);
    $observe('miss_after_delete', rp_cache_lookup('value'), $miss);
    $observe('set_typed_values', [
        wp_cache_set(0, 0, 'rp-cache-types'),
        wp_cache_set('00', '', 'rp-cache-types'),
        wp_cache_set('list', [], 'rp-cache-types'),
    ], [true, true, true]);
    $observe('lookup_typed_values', [
        rp_cache_lookup('0', 'rp-cache-types'),
        rp_cache_lookup('00', 'rp-cache-types'),
        rp_cache_lookup('list', 'rp-cache-types'),
    ], [[true, 0], [true, ''], [true, []]]);

    $group = 'rp-cache-multiple';
    $observe('set_multiple_ordered_keys', rp_cache_map(wp_cache_set_multiple([7 => false, '07' => null], $group)), [
        ['key' => 7, 'value' => true], ['key' => '07', 'value' => true],
    ]);
    $observe('add_multiple_mixed_result', rp_cache_map(wp_cache_add_multiple([7 => 1, 'new' => 2], $group)), [
        ['key' => 7, 'value' => false], ['key' => 'new', 'value' => true],
    ]);
    $observe('get_multiple_deduplicated_php_keys', rp_cache_map(wp_cache_get_multiple(['07', '7', 7, 'absent', 'new'], $group)), [
        ['key' => '07', 'value' => null], ['key' => 7, 'value' => false],
        ['key' => 'absent', 'value' => false], ['key' => 'new', 'value' => 2],
    ]);
    $observe('delete_multiple_mixed_result', rp_cache_map(wp_cache_delete_multiple(['07', 7, 'absent'], $group)), [
        ['key' => '07', 'value' => true], ['key' => 7, 'value' => true], ['key' => 'absent', 'value' => false],
    ]);
    $observe('delete_multiple_removed_values', [rp_cache_lookup('07', $group), rp_cache_lookup(7, $group)], [$miss, $miss]);
    $observe('multiple_empty_inputs', [wp_cache_add_multiple([]), wp_cache_set_multiple([]),
        wp_cache_get_multiple([]), wp_cache_delete_multiple([])], [[], [], [], []]);

    $group = 'rp-cache-integer';
    $observe('missing_counters', [wp_cache_incr('missing', 1, $group), wp_cache_decr('missing', 1, $group)], [false, false]);
    $observe('integer_counter_seed', wp_cache_set('counter', 2, $group), true);
    $observe('integer_increment_and_zero_offset', [wp_cache_incr('counter', 3, $group), wp_cache_incr('counter', 0, $group)], [5, 5]);
    $observe('integer_floor_and_negative_offsets', [wp_cache_decr('counter', 8, $group),
        wp_cache_decr('counter', -4, $group), wp_cache_incr('counter', -5, $group)], [0, 4, 0]);
    $observe('integer_zero_is_hit', rp_cache_lookup('counter', $group), [true, 0]);
    $observe('integer_string_and_nonnumeric_seed', [wp_cache_set('digits', '10', $group),
        wp_cache_set('text', 'text', $group)], [true, true]);
    $observe('integer_numeric_string_and_nonnumeric_reset', [wp_cache_incr('digits', 2, $group),
        wp_cache_incr('text', 3, $group)], [12, 3]);
    $observe('integer_max_seed', wp_cache_set('max', PHP_INT_MAX - 2, $group), true);
    $observe('integer_max_exact', [wp_cache_incr('max', 2, $group), wp_cache_decr('max', 1, $group)],
        [PHP_INT_MAX, PHP_INT_MAX - 1]);

    $original = (object) ['label' => 'stored', 'nested' => (object) ['label' => 'nested']];
    $observe('object_store', wp_cache_set('object', $original), true);
    $original->label = 'changed-original';
    $original->nested->label = 'changed-original-nested';
    $first = wp_cache_get('object');
    $observe('object_store_shallow_clone', [$first !== $original, $first->label, $first->nested->label],
        [true, 'stored', 'changed-original-nested']);
    $first->label = 'changed-fetch';
    $first->nested->label = 'changed-fetch-nested';
    $second = wp_cache_get('object');
    $observe('object_fetch_shallow_clone', [$second !== $first, $second->label, $second->nested->label,
        $original->nested->label], [true, 'stored', 'changed-fetch-nested', 'changed-fetch-nested']);

    $local = 'rp-cache-local';
    $observe('blog_one_group_seed', [wp_cache_set('site', 'one', $local), wp_cache_set('site', 'shared', $global),
        wp_cache_set('site', 'runtime', $runtime)], [true, true, true]);
    $observe('blog_one_runtime_group_hit', rp_cache_lookup('site', $runtime), [true, 'runtime']);
    $observe('switch_blog_two', wp_cache_switch_to_blog(2), null);
    $observe('blog_two_isolation', [rp_cache_lookup('site', $local), rp_cache_lookup('site', $global),
        rp_cache_lookup('site', $runtime)], [$miss, [true, 'shared'], $miss]);
    $observe('blog_two_local_seed', wp_cache_set('site', 'two', $local), true);
    $observe('switch_blog_one', wp_cache_switch_to_blog(1), null);
    $observe('blog_one_local_retained', rp_cache_lookup('site', $local), [true, 'one']);

    $features = ['add_multiple', 'set_multiple', 'get_multiple', 'delete_multiple', 'flush_runtime', 'flush_group', 'unknown'];
    $observe('supports_exact_booleans', array_combine($features, array_map('wp_cache_supports', $features)),
        array_combine($features, [true, true, true, true, true, true, false]));
    $observe('flush_group', wp_cache_flush_group($local), true);
    $observe('flush_group_isolated', [rp_cache_lookup('site', $local), rp_cache_lookup('site', $global),
        rp_cache_lookup('site', $runtime)], [$miss, [true, 'shared'], [true, 'runtime']]);
    wp_cache_switch_to_blog(2);
    $observe('flush_group_all_blog_prefixes', rp_cache_lookup('site', $local), $miss);
    wp_cache_switch_to_blog(1);
    $observe('flush_runtime', wp_cache_flush_runtime(), true);
    $observe('flush_runtime_cleared', [rp_cache_lookup('site', $global), rp_cache_lookup('site', $runtime)], [$miss, $miss]);
    $observe('flush_seed', wp_cache_set('value', 1), true);
    $observe('flush_all', wp_cache_flush(), true);
    $observe('flush_all_cleared', rp_cache_lookup('value'), $miss);

    $observe('leave_values_for_process_exit', [wp_cache_set('freshness', 'local', $local),
        wp_cache_set('freshness', 'global', $global), wp_cache_set('freshness', 'runtime', $runtime)], [true, true, true]);
    $observe('close', wp_cache_close(), true);
    $observe('close_leaves_request_values', [rp_cache_lookup('freshness', $local), rp_cache_lookup('freshness', $global),
        rp_cache_lookup('freshness', $runtime)], [[true, 'local'], [true, 'global'], [true, 'runtime']]);
    return $rows;
}
