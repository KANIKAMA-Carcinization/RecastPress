<?php
declare(strict_types=1);

require_once __DIR__ . '/strict_json.php';

use function RecastPress\V06\StrictJson\rp_decode_unique_json;

function strict_oracle_check(bool $condition, string $name): void
{
    if (!$condition) {
        fwrite(STDERR, "strict JSON oracle failed: {$name}\n");
        exit(1);
    }
}

function strict_oracle_rejects(string $wire): bool
{
    try {
        rp_decode_unique_json($wire);
        return false;
    } catch (Throwable) {
        return true;
    }
}

$checks = [];
$nestedDuplicate = '{"outer":{"name":"first","name":"second"}}';
$escapedDuplicate = '{"x":1,"\\u0078":2}';
$unicodeDuplicate = '{"é":1,"\\u00e9":2}';
$escapedStrings = <<<'JSON'
{"text":"brace { } and quote \" and key-like \"x\":1","nested":{"ok":"\u007b"}}
JSON;
$arrayValues = '[1,1,{"n":1},{"n":2}]';
$numericKeys = '{"1":"one","01":"zero-one","-1":"minus"}';

$checks['nested_duplicate_rejected'] = strict_oracle_rejects($nestedDuplicate);
$checks['unicode_escaped_duplicate_rejected'] = strict_oracle_rejects($escapedDuplicate);
$checks['unicode_codepoint_duplicate_rejected'] = strict_oracle_rejects($unicodeDuplicate);

$escapedValue = rp_decode_unique_json($escapedStrings);
$checks['escaped_strings_match_native_json_decode'] = $escapedValue === json_decode($escapedStrings, true, 64, JSON_THROW_ON_ERROR)
    && $escapedValue['text'] === 'brace { } and quote " and key-like "x":1'
    && $escapedValue['nested']['ok'] === '{';

$arrayValue = rp_decode_unique_json($arrayValues);
$checks['array_duplicate_values_allowed'] = $arrayValue === json_decode($arrayValues, true, 64, JSON_THROW_ON_ERROR)
    && $arrayValue[0] === $arrayValue[1];

$numericValue = rp_decode_unique_json($numericKeys);
$checks['numeric_looking_keys_match_native_json_decode'] = $numericValue === json_decode($numericKeys, true, 64, JSON_THROW_ON_ERROR)
    && $numericValue[1] === 'one'
    && $numericValue['01'] === 'zero-one';

$checks['malformed_json_rejected'] = strict_oracle_rejects('{"valid":true,}');
$checks['invalid_utf8_rejected'] = strict_oracle_rejects("\"" . "\xff" . "\"");
$checks['oversize_rejected_before_decode'] = strict_oracle_rejects(str_repeat(' ', 1_048_576) . '0');

$depth64 = str_repeat('[', 64) . '0' . str_repeat(']', 64);
$depth65 = str_repeat('[', 65) . '0' . str_repeat(']', 65);
$checks['depth_64_accepted'] = rp_decode_unique_json($depth64) === json_decode($depth64, true, 65, JSON_THROW_ON_ERROR);
$checks['depth_65_rejected'] = strict_oracle_rejects($depth65);

foreach ($checks as $name => $passed) {
    strict_oracle_check($passed, $name);
}

echo json_encode(
    ['oracle' => 'strict_json_unique_keys', 'checks' => count($checks), 'passed' => array_keys($checks)],
    JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
), "\n";
