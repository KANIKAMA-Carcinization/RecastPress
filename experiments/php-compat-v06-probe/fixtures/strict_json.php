<?php
declare(strict_types=1);

namespace RecastPress\V06\StrictJson;

const MAX_WIRE_BYTES = 1_048_576;
const MAX_DEPTH = 64;

/**
 * Decode strict JSON while rejecting duplicate decoded keys in every object.
 * This is a bounded language utility for the v0.6 fixture, not WP behavior.
 */
function rp_decode_unique_json(string $wire): mixed
{
    if (strlen($wire) > MAX_WIRE_BYTES) {
        throw new \LengthException('JSON input exceeds the 1 MiB limit');
    }

    // json_validate performs strict UTF-8 and complete JSON grammar validation,
    // and applies the nesting cap before json_decode allocates decoded values.
    // Native max_depth counts the root JSON value as one level; add one to
    // admit a protocol payload with exactly 64 nested containers.
    if (!\json_validate($wire, MAX_DEPTH + 1)) {
        throw new \JsonException('Invalid JSON: ' . \json_last_error_msg(), \json_last_error());
    }

    $offset = 0;
    scan_value($wire, $offset, 0);
    skip_whitespace($wire, $offset);
    if ($offset !== strlen($wire)) {
        // Defensive: json_validate already guarantees this cannot be reached.
        throw new \JsonException('Trailing data after JSON value');
    }

    // PHP's native depth parameter counts the root value in addition to
    // container nesting, while the protocol profile counts nested containers.
    // The pre-validation above enforces the protocol's 64-container ceiling.
    return \json_decode($wire, true, MAX_DEPTH + 1, JSON_THROW_ON_ERROR);
}

function scan_value(string $wire, int &$offset, int $parentDepth): void
{
    skip_whitespace($wire, $offset);
    $length = strlen($wire);
    if ($offset >= $length) {
        throw new \JsonException('Unexpected end of JSON input');
    }

    $next = $wire[$offset];
    if ($next === '{') {
        $depth = $parentDepth + 1;
        if ($depth > MAX_DEPTH) {
            throw new \JsonException('JSON nesting exceeds the 64 level limit');
        }
        scan_object($wire, $offset, $depth);
        return;
    }
    if ($next === '[') {
        $depth = $parentDepth + 1;
        if ($depth > MAX_DEPTH) {
            throw new \JsonException('JSON nesting exceeds the 64 level limit');
        }
        scan_array($wire, $offset, $depth);
        return;
    }
    if ($next === '"') {
        scan_string($wire, $offset);
        return;
    }

    // Input is already grammar-validated. Scalars end at a structural or
    // whitespace delimiter; no decoded token is needed for duplicate checks.
    while ($offset < $length) {
        $byte = $wire[$offset];
        if ($byte === ',' || $byte === ']' || $byte === '}' || is_json_whitespace($byte)) {
            return;
        }
        $offset++;
    }
}

function scan_object(string $wire, int &$offset, int $depth): void
{
    $offset++; // {
    skip_whitespace($wire, $offset);
    if ($wire[$offset] === '}') {
        $offset++;
        return;
    }

    $seen = [];
    while (true) {
        $start = $offset;
        scan_string($wire, $offset);
        $keyToken = substr($wire, $start, $offset - $start);
        $key = \json_decode($keyToken, false, MAX_DEPTH, JSON_THROW_ON_ERROR);
        // Prefix prevents PHP from converting numeric-looking string keys to
        // integer array keys, so "1" and "01" remain distinct JSON names.
        $lookup = "\0" . $key;
        if (isset($seen[$lookup])) {
            throw new \JsonException('Duplicate object key');
        }
        $seen[$lookup] = true;

        skip_whitespace($wire, $offset);
        $offset++; // : (validated above)
        scan_value($wire, $offset, $depth);
        skip_whitespace($wire, $offset);
        if ($wire[$offset] === '}') {
            $offset++;
            return;
        }
        $offset++; // , (validated above)
        skip_whitespace($wire, $offset);
    }
}

function scan_array(string $wire, int &$offset, int $depth): void
{
    $offset++; // [
    skip_whitespace($wire, $offset);
    if ($wire[$offset] === ']') {
        $offset++;
        return;
    }
    while (true) {
        scan_value($wire, $offset, $depth);
        skip_whitespace($wire, $offset);
        if ($wire[$offset] === ']') {
            $offset++;
            return;
        }
        $offset++; // , (validated above)
    }
}

function scan_string(string $wire, int &$offset): void
{
    $offset++; // opening quote
    $length = strlen($wire);
    while ($offset < $length) {
        $byte = $wire[$offset++];
        if ($byte === '\\') {
            $offset++; // escaped quote/backslash/unicode sequence; validated
        } elseif ($byte === '"') {
            return;
        }
    }
}

function skip_whitespace(string $wire, int &$offset): void
{
    $length = strlen($wire);
    while ($offset < $length && is_json_whitespace($wire[$offset])) {
        $offset++;
    }
}

function is_json_whitespace(string $byte): bool
{
    return $byte === ' ' || $byte === "\t" || $byte === "\n" || $byte === "\r";
}
