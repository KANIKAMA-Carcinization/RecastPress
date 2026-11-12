<?php

declare(strict_types=1);

use RecastPress\V06\Handles\HandleRegistry;
use RecastPress\V06\Handles\RegistryError;

final class CallableIdentity
{
    public static function build($hookName, $callback, $priority): ?string
    {
        if (is_string($callback)) {
            return $callback;
        }

        if (is_object($callback)) {
            return 'spl_object_id:' . spl_object_id($callback);
        }

        if (!isset($callback[1]) || !is_string($callback[1])) {
            return null;
        }

        if (is_object($callback[0])) {
            return (string) spl_object_id($callback[0]) . $callback[1];
        }

        if (is_string($callback[0])) {
            return $callback[0] . '::' . $callback[1];
        }

        return null;
    }

    public static function arrayKey(?string $id): ?array
    {
        if ($id === null) {
            return null;
        }

        $probe = [];
        $probe[$id] = true;
        $key = array_key_first($probe);

        if (is_int($key)) {
            return ['type' => 'int', 'hex' => bin2hex((string) $key)];
        }

        return ['type' => 'string', 'hex' => bin2hex($key)];
    }
}

function _wp_filter_build_unique_id($hook_name, $callback, $priority): ?string
{
    return CallableIdentity::build($hook_name, $callback, $priority);
}

final class CallableApiAdapter
{
    private static ?self $active = null;
    private array $effects = [];
    private array $issuedHandles = [];
    private array $registryObservation = [];

    public function __construct(
        private readonly BridgeProtocol $wire,
        private readonly HandleRegistry $registry,
    ) {
    }

    public static function bind(self $adapter): void
    {
        self::$active = $adapter;
    }

    public static function active(): self
    {
        if (self::$active === null) {
            throw new RuntimeException('callable_adapter_unbound');
        }

        return self::$active;
    }

    public function setRootEffect(string $effectId): void
    {
        if ($this->effects !== []) {
            throw new RuntimeException('callable_root_effect_already_set');
        }
        $this->effects[] = $effectId;
    }

    public function addFilter($hookName, $callback, $priority = 10, $acceptedArgs = 1)
    {
        $hookHex = $this->hookHex($hookName);
        $id = _wp_filter_build_unique_id($hookName, $callback, $priority);
        $key = CallableIdentity::arrayKey($id);
        $handle = null;
        $accepted = null;

        if ($key !== null) {
            $priority = $this->normalizePriority($priority);
            $accepted = (int) $acceptedArgs;
            $handle = $this->registry->retainCallable($callback);
            $this->issuedHandles[] = $handle;
        }

        $this->api('add_filter', [
            'hook_hex' => $hookHex,
            'key' => $key,
            'priority' => $key === null ? $this->normalizePriority($priority) : $priority,
            'accepted_args' => $accepted,
            'handle' => $handle,
        ]);

        return true;
    }

    public function removeFilter($hookName, $callback, $priority = 10)
    {
        $hookHex = $this->hookHex($hookName);
        $key = CallableIdentity::arrayKey(_wp_filter_build_unique_id($hookName, $callback, $priority));
        $priority = $this->normalizePriority($priority);

        return $this->api('remove_filter', [
            'hook_hex' => $hookHex,
            'key' => $key,
            'priority' => $priority,
        ]);
    }

    public function hasFilter($hookName, $callback = false, $priority = false)
    {
        $hookHex = $this->hookHex($hookName);
        $any = $callback === false;
        $key = $any ? null : CallableIdentity::arrayKey(
            _wp_filter_build_unique_id($hookName, $callback, $priority),
        );

        return $this->api('has_filter', [
            'hook_hex' => $hookHex,
            'key' => $key,
            'any' => $any,
            'priority' => is_int($priority) ? $priority : null,
        ]);
    }

    public function applyFilters($hookName, $value, ...$args)
    {
        return $this->api('apply_filters', [
            'hook_hex' => $this->hookHex($hookName),
            'value' => self::encodePod($value),
            'args' => array_map(self::encodePod(...), $args),
        ]);
    }

    public function currentFilter()
    {
        return $this->api('current_filter', []);
    }

    public function doingFilter($hookName = null)
    {
        return $this->api('doing_filter', [
            'hook_hex' => $hookName === null ? null : $this->hookHex($hookName),
        ]);
    }

    public function didFilter($hookName)
    {
        return $this->api('did_filter', [
            'hook_hex' => $this->hookHex($hookName),
        ]);
    }

    public function handleTerminalInvoke(array $frame): void
    {
        expect_kind($frame, 'invoke_php');
        expect_parent_effect($frame, null);
        $payload = $frame['payload'];
        $operation = $payload['operation'] ?? null;
        if ($operation === 'release_callable') {
            expect_payload_keys($frame, ['operation', 'effect_id', 'handle'], 'callable_root_release');
            $effectId = $payload['effect_id'];
            if (!is_string($effectId)) {
                throw new RuntimeException('callable_release_effect_id');
            }

            $completion = null;
            $this->withEffect($effectId, function () use ($payload, &$completion): void {
                try {
                    if (!$this->registry->release($payload['handle'])) {
                        throw new RuntimeException('callable_release_not_final');
                    }
                    $completion = ['kind' => 'value', 'value' => ['released' => true]];
                } catch (Throwable $error) {
                    $completion = $this->throwableCompletion($error);
                }
            });
            $this->wire->send('resume', [
                'effect_id' => $effectId,
                'completion' => $completion,
            ]);
            return;
        }

        if ($operation === 'close_registry') {
            expect_payload_keys($frame, ['effect_id', 'operation'], 'callable_close_registry');
            $effectId = $payload['effect_id'];
            if (!is_string($effectId)) {
                throw new RuntimeException('callable_close_effect_id');
            }

            $this->withEffect($effectId, function () use ($effectId): void {
                $this->registry->close();
                $closedReadRejected = false;
                if ($this->issuedHandles !== []) {
                    try {
                        $this->registry->resolve($this->issuedHandles[0], 'callable');
                    } catch (RegistryError $error) {
                        $closedReadRejected = $error->getMessage() === 'request_closed';
                    }
                }

                $this->registryObservation = [
                    'closed' => $this->registry->isClosed(),
                    'live_after_close' => $this->registry->liveCount(),
                    'closed_read_rejected' => $closedReadRejected,
                ];
                if ($this->registryObservation !== [
                    'closed' => true,
                    'live_after_close' => 0,
                    'closed_read_rejected' => true,
                ]) {
                    throw new RuntimeException('callable_close_observation_mismatch');
                }

                $this->wire->send('resume', [
                    'effect_id' => $effectId,
                    'completion' => [
                        'kind' => 'value',
                        'value' => $this->registryObservation,
                    ],
                ]);
            });
            return;
        }

        throw new RuntimeException('callable_unexpected_terminal_invoke');
    }

    public function registryObservation(): array
    {
        return $this->registryObservation;
    }

    private function api(string $api, array $fields)
    {
        $parentEffect = $this->currentEffect();
        $this->wire->send('call_core', ['api' => $api, ...$fields], $parentEffect);

        while (true) {
            $frame = $this->wire->receive();
            if ($frame['kind'] === 'invoke_php') {
                $this->handleNestedInvoke($frame, $parentEffect);
                continue;
            }

            expect_kind($frame, 'return');
            expect_parent_effect($frame, $parentEffect);
            expect_payload_keys($frame, ['api', 'completion'], 'callable_api_return');
            if ($frame['payload']['api'] !== $api) {
                throw new RuntimeException('callable_api_return_name');
            }

            return $this->decodeCompletion($frame['payload']['completion']);
        }
    }

    private function handleNestedInvoke(array $frame, string $expectedParent): void
    {
        expect_kind($frame, 'invoke_php');
        expect_parent_effect($frame, $expectedParent);
        $payload = $frame['payload'];
        $operation = $payload['operation'] ?? null;

        if ($operation === 'callable') {
            expect_payload_keys(
                $frame,
                ['operation', 'effect_id', 'handle', 'hook_hex', 'priority', 'call_mode', 'args'],
                'callable_invoke',
            );
            $effectId = $payload['effect_id'];
            if (!is_string($effectId)) {
                throw new RuntimeException('callable_invoke_effect_id');
            }

            $completion = null;
            $this->withEffect($effectId, function () use ($payload, &$completion): void {
                if (!is_string($payload['hook_hex'])
                    || !is_int($payload['priority'])
                    || !is_string($payload['call_mode'])
                ) {
                    throw new RuntimeException('callable_invoke_metadata');
                }
                $hookName = self::decodeHexString($payload['hook_hex']);
                $callable = $this->registry->resolve($payload['handle'], 'callable');
                $arguments = self::decodePodList($payload['args']);
                try {
                    if ($payload['call_mode'] === 'zero') {
                        if ($arguments !== []) {
                            throw new RuntimeException('callable_zero_args_not_empty');
                        }
                        $value = call_user_func($callable);
                    } elseif ($payload['call_mode'] === 'array' || $payload['call_mode'] === 'all') {
                        if ($payload['call_mode'] === 'all'
                            && ($arguments === [] || $arguments[0] !== $hookName)
                        ) {
                            throw new RuntimeException('callable_all_hook_name_mismatch');
                        }
                        $value = call_user_func_array($callable, $arguments);
                    } else {
                        throw new RuntimeException('callable_call_mode');
                    }
                    $completion = [
                        'kind' => 'value',
                        'value' => self::encodePod($value),
                    ];
                } catch (Throwable $error) {
                    $completion = $this->throwableCompletion($error);
                } finally {
                    unset($callable, $arguments, $hookName, $value);
                }
            });

            $this->wire->send('resume', [
                'effect_id' => $effectId,
                'completion' => $completion,
            ], $expectedParent);
            return;
        }

        if ($operation === 'release_callable') {
            expect_payload_keys($frame, ['operation', 'effect_id', 'handle'], 'callable_release');
            $effectId = $payload['effect_id'];
            if (!is_string($effectId)) {
                throw new RuntimeException('callable_release_effect_id');
            }

            $completion = null;
            $this->withEffect($effectId, function () use ($payload, &$completion): void {
                try {
                    if (!$this->registry->release($payload['handle'])) {
                        throw new RuntimeException('callable_release_not_final');
                    }
                    $completion = ['kind' => 'value', 'value' => ['released' => true]];
                } catch (Throwable $error) {
                    $completion = $this->throwableCompletion($error);
                }
            });

            $this->wire->send('resume', [
                'effect_id' => $effectId,
                'completion' => $completion,
            ], $expectedParent);
            return;
        }

        throw new RuntimeException('callable_unknown_invoke_operation');
    }

    private function decodeCompletion($completion)
    {
        if (!is_array($completion) || !isset($completion['kind']) || !is_string($completion['kind'])) {
            throw new RuntimeException('callable_completion_shape');
        }

        if ($completion['kind'] === 'value') {
            expect_payload_keys(['payload' => $completion], ['kind', 'value'], 'callable_value_completion');
            return self::decodePod($completion['value']);
        }

        if ($completion['kind'] === 'php_throwable') {
            expect_payload_keys(
                ['payload' => $completion],
                ['kind', 'handle', 'class_hex', 'message_hex'],
                'callable_throwable_completion',
            );
            $throwable = $this->registry->resolve($completion['handle'], 'throwable');
            if (!$throwable instanceof Throwable
                || bin2hex($throwable::class) !== $completion['class_hex']
                || bin2hex($throwable->getMessage()) !== $completion['message_hex']
            ) {
                throw new RuntimeException('callable_throwable_completion_mismatch');
            }

            throw $throwable;
        }

        throw new RuntimeException('callable_completion_kind');
    }

    private function throwableCompletion(Throwable $error): array
    {
        return [
            'kind' => 'php_throwable',
            'handle' => $this->registry->retainObject($error),
            'class_hex' => bin2hex($error::class),
            'message_hex' => bin2hex($error->getMessage()),
        ];
    }

    private function withEffect(string $effectId, callable $callback): void
    {
        $this->effects[] = $effectId;
        try {
            $callback();
        } finally {
            array_pop($this->effects);
        }
    }

    private function currentEffect(): string
    {
        if ($this->effects === []) {
            throw new RuntimeException('callable_api_without_effect');
        }

        return $this->effects[array_key_last($this->effects)];
    }

    private function hookHex($hookName): string
    {
        if (!is_string($hookName)) {
            throw new RuntimeException('callable_hook_name_requires_string');
        }

        return bin2hex($hookName);
    }

    private function normalizePriority($priority): int
    {
        if ($priority === null) {
            return 0;
        }
        if (!is_int($priority)) {
            throw new RuntimeException('callable_priority_out_of_profile');
        }

        return $priority;
    }

    private static function encodePod($value): array
    {
        if ($value === null) {
            return ['type' => 'null'];
        }
        if (is_bool($value)) {
            return ['type' => 'bool', 'value' => $value];
        }
        if (is_int($value)) {
            return ['type' => 'int', 'value' => (string) $value];
        }
        if (is_float($value)) {
            return ['type' => 'float', 'bits' => bin2hex(pack('E', $value))];
        }
        if (is_string($value)) {
            return ['type' => 'string', 'hex' => bin2hex($value)];
        }
        if (is_array($value)) {
            $entries = [];
            foreach ($value as $key => $entry) {
                if (ReflectionReference::fromArrayElement($value, $key) !== null) {
                    throw new RuntimeException('callable_reference_value_out_of_profile');
                }
                $keyNode = is_int($key)
                    ? ['type' => 'int', 'value' => (string) $key]
                    : ['type' => 'string', 'hex' => bin2hex($key)];
                $entries[] = ['key' => $keyNode, 'value' => self::encodePod($entry)];
            }

            return ['type' => 'array', 'entries' => $entries];
        }

        throw new RuntimeException('callable_value_out_of_profile');
    }

    private static function decodePod($node)
    {
        if (!is_array($node) || !isset($node['type']) || !is_string($node['type'])) {
            throw new RuntimeException('callable_pod_shape');
        }

        switch ($node['type']) {
            case 'null':
                expect_payload_keys(['payload' => $node], ['type'], 'callable_pod_null');
                return null;
            case 'bool':
                expect_payload_keys(['payload' => $node], ['type', 'value'], 'callable_pod_bool');
                if (!is_bool($node['value'])) {
                    throw new RuntimeException('callable_pod_bool_value');
                }
                return $node['value'];
            case 'int':
                expect_payload_keys(['payload' => $node], ['type', 'value'], 'callable_pod_int');
                return self::decodeInt($node['value']);
            case 'float':
                expect_payload_keys(['payload' => $node], ['type', 'bits'], 'callable_pod_float');
                if (!is_string($node['bits']) || preg_match('/\A[0-9a-f]{16}\z/D', $node['bits']) !== 1) {
                    throw new RuntimeException('callable_pod_float_bits');
                }
                return unpack('Evalue', hex2bin($node['bits']))['value'];
            case 'string':
                expect_payload_keys(['payload' => $node], ['type', 'hex'], 'callable_pod_string');
                return self::decodeHexString($node['hex']);
            case 'array':
                expect_payload_keys(['payload' => $node], ['type', 'entries'], 'callable_pod_array');
                if (!is_array($node['entries']) || !array_is_list($node['entries'])) {
                    throw new RuntimeException('callable_pod_entries_list');
                }
                $result = [];
                $seen = [];
                foreach ($node['entries'] as $entry) {
                    expect_payload_keys(['payload' => $entry], ['key', 'value'], 'callable_pod_entry');
                    $key = self::decodeArrayKey($entry['key']);
                    $identity = is_int($key) ? 'i:' . $key : 's:' . bin2hex($key);
                    if (isset($seen[$identity])) {
                        throw new RuntimeException('callable_pod_duplicate_key');
                    }
                    $seen[$identity] = true;
                    if (is_string($key)) {
                        $probe = [];
                        $probe[$key] = true;
                        if (is_int(array_key_first($probe))) {
                            throw new RuntimeException('callable_pod_unnormalized_string_key');
                        }
                    }
                    $result[$key] = self::decodePod($entry['value']);
                }
                return $result;
        }

        throw new RuntimeException('callable_pod_type');
    }

    private static function decodePodList($nodes): array
    {
        if (!is_array($nodes) || !array_is_list($nodes)) {
            throw new RuntimeException('callable_pod_list');
        }

        return array_map(self::decodePod(...), $nodes);
    }

    private static function decodeArrayKey($node): int|string
    {
        if (!is_array($node) || !isset($node['type'])) {
            throw new RuntimeException('callable_pod_key_shape');
        }
        if ($node['type'] === 'int') {
            expect_payload_keys(['payload' => $node], ['type', 'value'], 'callable_pod_int_key');
            return self::decodeInt($node['value']);
        }
        if ($node['type'] === 'string') {
            expect_payload_keys(['payload' => $node], ['type', 'hex'], 'callable_pod_string_key');
            return self::decodeHexString($node['hex']);
        }

        throw new RuntimeException('callable_pod_key_type');
    }

    private static function decodeInt($value): int
    {
        if (!is_string($value) || preg_match('/\A(?:0|-?[1-9][0-9]*)\z/D', $value) !== 1) {
            throw new RuntimeException('callable_pod_int_canonical');
        }
        $parsed = filter_var($value, FILTER_VALIDATE_INT);
        if (!is_int($parsed) || (string) $parsed !== $value) {
            throw new RuntimeException('callable_pod_int_range');
        }

        return $parsed;
    }

    private static function decodeHexString($hex): string
    {
        if (!is_string($hex) || preg_match('/\A(?:[0-9a-f]{2})*\z/D', $hex) !== 1) {
            throw new RuntimeException('callable_pod_hex');
        }

        $bytes = hex2bin($hex);
        if (!is_string($bytes) || bin2hex($bytes) !== $hex) {
            throw new RuntimeException('callable_pod_hex');
        }

        return $bytes;
    }
}

function callable_adapter(): CallableApiAdapter
{
    return CallableApiAdapter::active();
}

function add_filter($hook_name, $callback, $priority = 10, $accepted_args = 1)
{
    return callable_adapter()->addFilter($hook_name, $callback, $priority, $accepted_args);
}

function remove_filter($hook_name, $callback, $priority = 10)
{
    return callable_adapter()->removeFilter($hook_name, $callback, $priority);
}

function has_filter($hook_name, $callback = false, $priority = false)
{
    return callable_adapter()->hasFilter($hook_name, $callback, $priority);
}

function apply_filters($hook_name, $value, ...$args)
{
    return callable_adapter()->applyFilters($hook_name, $value, ...$args);
}

function current_filter()
{
    return callable_adapter()->currentFilter();
}

function doing_filter($hook_name = null)
{
    return callable_adapter()->doingFilter($hook_name);
}

function did_filter($hook_name)
{
    return callable_adapter()->didFilter($hook_name);
}
