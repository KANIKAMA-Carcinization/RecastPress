<?php
declare(strict_types=1);
require_once __DIR__ . '/dispatcher.php';

function oracle_check(bool $ok, string $name): void {
    if (!$ok) { fwrite(STDERR, "oracle failed: {$name}\n"); exit(1); }
}

$observed = rp_surface();
$expected = [
    'same_object_alias' => true,
    'same_object_handle' => true,
    'alias_public_property' => 'changed-in-callback',
    'public_property_after_callback' => 'changed-in-callback',
    'global_replaced_without_changing_alias' => true,
    'array_reference_cell_after_callback' => 'changed-through-reference',
    'type_mismatch_rejected' => true,
    'expired_handle_rejected' => true,
    'child_api_result' => 'option-from-child',
    'callback_observed_child_completion_before_return' => true,
    'nested_callback' => true,
    'catchable_throwable' => true,
    'parent_method_override' => 'parent:child',
    'byte_value_hex' => '4100ff5a',
    'byte_value_length' => 4,
];
foreach ($expected as $key => $value) oracle_check(($observed[$key] ?? null) === $value, $key);
echo json_encode(['oracle' => 'passed', 'checks' => count($expected)], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR), "\n";
