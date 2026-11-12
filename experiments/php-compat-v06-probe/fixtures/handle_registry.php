<?php

declare(strict_types=1);

namespace RecastPress\V06\Handles;

use RuntimeException;
use WeakMap;

final class RegistryError extends RuntimeException
{
}

final class HandleRegistry
{
    private const U32_MAX = 4294967295;
    private const U64_MAX = '18446744073709551615';

    private bool $closed = false;
    private WeakMap $objectSlots;
    private array $slots = [];
    private array $generations = [];
    private array $freeSlots = [];
    private int $nextSlot = 0;
    private int $issued = 0;

    public function __construct(
        private readonly string $request,
        private readonly int $maxLive = 16384,
        private readonly int $maxIssued = 16384,
    ) {
        if ($maxLive <= 0 || $maxIssued <= 0) {
            throw new RegistryError('invalid_limits');
        }

        if (!self::isCanonicalU64($request)) {
            throw new RegistryError('invalid_request');
        }

        $this->objectSlots = new WeakMap();
    }

    public function retainObject(object $value): array
    {
        $this->assertOpen();

        if (isset($this->objectSlots[$value])) {
            $slot = $this->objectSlots[$value];
            if (isset($this->slots[$slot])) {
                if ($this->slots[$slot]['retains'] === PHP_INT_MAX) {
                    throw new RegistryError('retain_limit');
                }

                $this->slots[$slot]['retains']++;
                return $this->handleFor($slot);
            }
        }

        if ($value instanceof \Closure) {
            $kind = 'closure';
        } elseif ($value instanceof \Throwable) {
            $kind = 'throwable';
        } else {
            $kind = 'object';
        }

        $handle = $this->prepareIssue($kind);
        $slot = $handle['slot'];
        $this->storeValue($slot, $handle, $kind, $value);
        $this->objectSlots[$value] = $slot;

        return $handle;
    }

    private function __clone(): void
    {
    }

    public function __serialize(): array
    {
        throw new RegistryError('non_transferable');
    }

    public function __unserialize(array $data): void
    {
        throw new RegistryError('non_transferable');
    }

    public function retainReference(mixed &$value): array
    {
        $this->assertOpen();
        $handle = $this->prepareIssue('reference_cell');
        $slot = $handle['slot'];
        $entry = [
            'generation' => $handle['generation'],
            'token' => $handle['token'],
            'kind' => 'reference_cell',
            'retains' => 1,
            'value' => null,
        ];
        $entry['value'] =& $value;
        $this->slots[$slot] = $entry;
        $this->commitIssue($slot, $handle['generation']);

        return $handle;
    }

    public function retainArray(array $value): array
    {
        $this->assertOpen();
        $handle = $this->prepareIssue('array');
        $slot = $handle['slot'];
        $this->storeValue($slot, $handle, 'array', $value);

        return $handle;
    }

    public function retainCallable(mixed $value): array
    {
        $this->assertOpen();
        $handle = $this->prepareIssue('callable');
        $slot = $handle['slot'];
        $this->storeValue($slot, $handle, 'callable', $value);

        return $handle;
    }

    public function retainArgument(mixed $value): array
    {
        $this->assertOpen();
        $handle = $this->prepareIssue('argument');
        $slot = $handle['slot'];
        $this->storeValue($slot, $handle, 'argument', $value);

        return $handle;
    }

    public function retainResource(mixed $value): array
    {
        $this->assertOpen();
        if (!is_resource($value)) {
            throw new RegistryError('invalid_value');
        }

        $handle = $this->prepareIssue('resource');
        $slot = $handle['slot'];
        $this->storeValue($slot, $handle, 'resource', $value);

        return $handle;
    }

    public function resolve(array $handle, string $kind): mixed
    {
        $slot = $this->validateHandle($handle);
        $entry = $this->slots[$slot];
        if ($entry['kind'] !== $kind) {
            throw new RegistryError('wrong_kind');
        }

        return $entry['value'];
    }

    public function assignReference(array $handle, mixed $value): void
    {
        $slot = $this->validateHandle($handle);
        if ($this->slots[$slot]['kind'] !== 'reference_cell') {
            throw new RegistryError('wrong_kind');
        }

        $this->slots[$slot]['value'] = $value;
    }

    public function release(array $handle): bool
    {
        $slot = $this->validateHandle($handle);
        $entry = $this->slots[$slot];

        if ($entry['retains'] > 1) {
            $this->slots[$slot]['retains']--;
            return false;
        }

        if ($entry['kind'] === 'object' || $entry['kind'] === 'closure' || $entry['kind'] === 'throwable') {
            $object = $entry['value'];
            unset($this->objectSlots[$object]);
        }

        $detached = $entry['value'];
        unset($this->slots[$slot]);

        if ($entry['generation'] === self::U32_MAX) {
            unset($this->generations[$slot], $this->freeSlots[$slot]);
        } else {
            $this->generations[$slot] = $entry['generation'] + 1;
            $this->freeSlots[$slot] = true;
        }

        unset($entry);
        unset($detached);

        return true;
    }

    public function close(): void
    {
        if ($this->closed) {
            return;
        }

        $this->closed = true;
        $detached = $this->slots;
        $this->slots = [];
        $this->generations = [];
        $this->freeSlots = [];
        $this->nextSlot = 0;
        $this->issued = 0;
        $oldObjectSlots = $this->objectSlots;
        $this->objectSlots = new WeakMap();

        unset($oldObjectSlots);
        unset($detached);
    }

    public function isClosed(): bool
    {
        return $this->closed;
    }

    public function liveCount(): int
    {
        return count($this->slots);
    }

    private function assertOpen(): void
    {
        if ($this->closed) {
            throw new RegistryError('request_closed');
        }
    }

    private function prepareIssue(string $kind): array
    {
        if (count($this->slots) >= $this->maxLive) {
            throw new RegistryError('handle_limit');
        }
        if ($this->issued >= $this->maxIssued) {
            throw new RegistryError('issuance_limit');
        }

        $token = bin2hex(random_bytes(8));

        if ($this->freeSlots !== []) {
            $slot = min(array_keys($this->freeSlots));
            $generation = $this->generations[$slot];
        } else {
            $slot = $this->nextSlot;
            if ($slot > self::U32_MAX) {
                throw new RegistryError('handle_limit');
            }
            $generation = 1;
        }

        return [
            'request' => $this->request,
            'kind' => $kind,
            'slot' => $slot,
            'generation' => $generation,
            'token' => $token,
        ];
    }

    private function commitIssue(int $slot, int $generation): void
    {
        if (isset($this->freeSlots[$slot])) {
            unset($this->freeSlots[$slot]);
        } else {
            $this->nextSlot = $slot + 1;
        }

        $this->generations[$slot] = $generation;
        $this->issued++;
    }

    private function storeValue(int $slot, array $handle, string $kind, mixed $value): void
    {
        $this->slots[$slot] = [
            'generation' => $handle['generation'],
            'token' => $handle['token'],
            'kind' => $kind,
            'retains' => 1,
            'value' => $value,
        ];
        $this->commitIssue($slot, $handle['generation']);
    }

    private function handleFor(int $slot): array
    {
        $entry = $this->slots[$slot];

        return [
            'request' => $this->request,
            'kind' => $entry['kind'],
            'slot' => $slot,
            'generation' => $entry['generation'],
            'token' => $entry['token'],
        ];
    }

    private function validateHandle(array $handle): int
    {
        $this->assertOpen();

        $expectedKeys = ['generation', 'kind', 'request', 'slot', 'token'];
        $actualKeys = array_keys($handle);
        sort($actualKeys);
        if ($actualKeys !== $expectedKeys
            || !is_string($handle['request'])
            || !is_string($handle['kind'])
            || !is_int($handle['slot'])
            || !is_int($handle['generation'])
            || !is_string($handle['token'])
            || $handle['slot'] < 0
            || $handle['slot'] > self::U32_MAX
            || $handle['generation'] < 1
            || $handle['generation'] > self::U32_MAX
            || preg_match('/\A[0-9a-f]{16}\z/', $handle['token']) !== 1
        ) {
            throw new RegistryError('invalid_handle');
        }

        if ($handle['request'] !== $this->request) {
            throw new RegistryError('wrong_request');
        }

        $slot = $handle['slot'];
        if (!isset($this->slots[$slot])) {
            throw new RegistryError('invalid_handle');
        }

        $entry = $this->slots[$slot];
        if ($handle['generation'] !== $entry['generation']
            || !hash_equals($entry['token'], $handle['token'])
            || $handle['kind'] !== $entry['kind']
        ) {
            throw new RegistryError('invalid_handle');
        }

        return $slot;
    }

    private static function isCanonicalU64(string $value): bool
    {
        if (preg_match('/\A[1-9][0-9]*\z/', $value) !== 1) {
            return false;
        }

        $length = strlen($value);
        $maxLength = strlen(self::U64_MAX);
        if ($length < $maxLength) {
            return true;
        }
        if ($length > $maxLength) {
            return false;
        }

        return strcmp($value, self::U64_MAX) <= 0;
    }
}
