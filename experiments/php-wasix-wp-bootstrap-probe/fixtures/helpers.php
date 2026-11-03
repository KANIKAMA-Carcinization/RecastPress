<?php
declare(strict_types=1);

final class RpNativeFailure extends RuntimeException {}
function rp_check(bool $condition, string $code): void {
    if (!$condition) {
        throw new RpNativeFailure($code);
    }
}

function rp_exact_keys(array $value, array $expected, string $failure): void {
    $keys = array_keys($value);
    sort($keys);
    sort($expected);
    rp_check($keys === $expected, $failure);
}

function rp_unhex(mixed $value, int $maximum = 8192): string {
    rp_check(is_string($value) && strlen($value) <= $maximum * 2
        && strlen($value) % 2 === 0 && preg_match('/\A[0-9a-f]*\z/D', $value) === 1, 'rpc_invalid_hex');
    $bytes = hex2bin($value);
    rp_check(is_string($bytes), 'rpc_hex_decode_failed');
    return $bytes;
}

function rp_decimal_int(mixed $value): int {
    rp_check(is_string($value) && preg_match('/\A(?:0|[1-9][0-9]*)\z/D', $value) === 1,
        'rpc_invalid_unsigned_decimal');
    $limit = (string) PHP_INT_MAX;
    rp_check(strlen($value) < strlen($limit) || (strlen($value) === strlen($limit) && strcmp($value, $limit) <= 0),
        'rpc_integer_out_of_native_range');
    return (int) $value;
}

function rp_context_json_keys(string $wire): array {
    // The input has only a root object and a salts object, with disjoint fixed
    // keys. Count their string tokens to reject duplicate keys after decoding.
    $keys = []; $length = strlen($wire);
    for ($i = 0; $i < $length; ++$i) {
        if ($wire[$i] !== '"') { continue; }
        $start = $i++;
        while ($i < $length) {
            if ($wire[$i] === '\\') { $i += 2; continue; }
            if ($wire[$i] === '"') { break; }
            ++$i;
        }
        rp_check($i < $length, 'input_string_token');
        $end = $i + 1;
        while ($end < $length && str_contains(" \t\r\n", $wire[$end])) { ++$end; }
        if ($end < $length && $wire[$end] === ':') {
            $keys[] = json_decode(substr($wire, $start, $i - $start + 1), true, 4, JSON_THROW_ON_ERROR);
        }
    }
    sort($keys); return $keys;
}
function rp_boot_context(string $wire, string $mode): array {
    rp_check(strlen($wire) <= 4096, 'context_size');
    $value = json_decode($wire, true, 16, JSON_THROW_ON_ERROR);
    rp_check(is_array($value), 'context_object');
    $keys = ['schema', 'mode', 'nonce', 'salts'];
    if ($mode === 'install') { $keys[] = 'admin_password'; }
    rp_exact_keys($value, $keys, 'context_exact_keys');
    rp_check($value['schema'] === 'recastpress.php-wasix-wp-bootstrap-context.v0.5'
        && $value['mode'] === $mode && in_array($mode, ['install', 'read'], true), 'context_profile');
    $secret = static fn($v): bool => is_string($v) && preg_match('/\A[0-9a-f]{48}\z/D', $v) === 1;
    rp_check($secret($value['nonce']) && is_array($value['salts']), 'context_nonce_or_salts');
    $salts = ['AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY',
        'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT'];
    rp_exact_keys($value['salts'], $salts, 'context_salt_keys');
    foreach ($value['salts'] as $salt) { rp_check($secret($salt), 'context_salt_shape'); }
    if ($mode === 'install') { rp_check($secret($value['admin_password']), 'context_admin_shape'); }
    $all = array_merge($keys, $salts); sort($all);
    rp_check(rp_context_json_keys($wire) === $all, 'context_duplicate_keys');
    return $value;
}
