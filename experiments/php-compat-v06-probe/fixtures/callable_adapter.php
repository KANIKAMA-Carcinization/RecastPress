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
    private array $argumentFrames = [];
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
        [$arguments, $handles] = $this->retainArguments([
            'hook' => $hookName,
            'callback' => $callback,
            'priority' => $priority,
            'accepted_args' => $acceptedArgs,
        ]);

        return $this->api('add_filter', ['arguments' => $arguments], $handles);
    }

    public function addAction($hookName, $callback, $priority = 10, $acceptedArgs = 1)
    {
        [$arguments, $handles] = $this->retainArguments([
            'hook' => $hookName,
            'callback' => $callback,
            'priority' => $priority,
            'accepted_args' => $acceptedArgs,
        ]);

        return $this->api('add_action', ['arguments' => $arguments], $handles);
    }

    public function removeFilter($hookName, $callback, $priority = 10)
    {
        [$arguments, $handles] = $this->retainArguments([
            'hook' => $hookName,
            'callback' => $callback,
            'priority' => $priority,
        ]);

        return $this->api('remove_filter', ['arguments' => $arguments], $handles);
    }

    public function removeAction($hookName, $callback, $priority = 10)
    {
        [$arguments, $handles] = $this->retainArguments([
            'hook' => $hookName,
            'callback' => $callback,
            'priority' => $priority,
        ]);

        return $this->api('remove_action', ['arguments' => $arguments], $handles);
    }

    public function hasFilter($hookName, $callback = false, $priority = false)
    {
        $any = $callback === false;
        [$arguments, $handles] = $this->retainArguments([
            'hook' => $hookName,
            'callback' => $callback,
        ]);

        return $this->api('has_filter', [
            'arguments' => $arguments,
            'any' => $any,
            'priority' => is_int($priority) ? self::encodePod($priority) : null,
        ], $handles);
    }

    public function hasAction($hookName, $callback = false, $priority = false)
    {
        $any = $callback === false;
        [$arguments, $handles] = $this->retainArguments([
            'hook' => $hookName,
            'callback' => $callback,
        ]);

        return $this->api('has_action', [
            'arguments' => $arguments,
            'any' => $any,
            'priority' => is_int($priority) ? self::encodePod($priority) : null,
        ], $handles);
    }

    public function applyFilters($hookName, $value, ...$args)
    {
        $valueNode = self::encodePod($value);
        $argumentNodes = array_map(self::encodePod(...), $args);
        [$arguments, $handles] = $this->retainArguments(['hook' => $hookName]);

        return $this->api('apply_filters', [
            'arguments' => $arguments,
            'value' => $valueNode,
            'args' => $argumentNodes,
        ], $handles);
    }

    public function doAction($hookName, ...$args)
    {
        [$arguments, $handles] = $this->retainArguments(['hook' => $hookName]);
        try {
            foreach ($args as $index => $value) {
                $handle = $this->registry->retainArgument($value);
                $arguments['args'][$index] = $handle;
                $handles[] = $handle;
            }
        } catch (Throwable $error) {
            foreach ($handles as $handle) {
                try {
                    $this->registry->release($handle);
                } catch (Throwable) {
                    // Keep the original retention failure.
                }
            }
            throw $error;
        }

        $argumentNodes = array_map(
            static fn (array $handle): array => ['type' => 'handle', 'handle' => $handle],
            $arguments['args'] ?? [],
        );

        $result = $this->api('do_action', [
            'arguments' => ['hook' => $arguments['hook']],
            'args' => $argumentNodes,
        ], $handles);
        array_pop($GLOBALS['wp_current_filter']);

        return $result;
    }

    public function currentFilter()
    {
        return $this->api('current_filter', []);
    }

    public function currentAction()
    {
        return $this->api('current_action', []);
    }

    public function doingFilter($hookName = null)
    {
        [$arguments, $handles] = $this->retainArguments(['hook' => $hookName]);

        return $this->api('doing_filter', [
            'arguments' => $arguments,
            'any' => $hookName === null,
        ], $handles);
    }

    public function doingAction($hookName = null)
    {
        [$arguments, $handles] = $this->retainArguments(['hook' => $hookName]);

        return $this->api('doing_action', [
            'arguments' => $arguments,
            'any' => $hookName === null,
        ], $handles);
    }

    public function didFilter($hookName)
    {
        [$arguments, $handles] = $this->retainArguments(['hook' => $hookName]);

        return $this->api('did_filter', ['arguments' => $arguments], $handles);
    }

    public function didAction($hookName)
    {
        [$arguments, $handles] = $this->retainArguments(['hook' => $hookName]);

        return $this->api('did_action', ['arguments' => $arguments], $handles);
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

        if ($operation === 'release_callable_unwind') {
            $this->handleCallableUnwindRelease($frame, null);
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

    private function retainArguments(array $values): array
    {
        $arguments = [];
        $handles = [];
        try {
            foreach ($values as $name => $value) {
                $handle = $this->registry->retainArgument($value);
                $arguments[$name] = $handle;
                $handles[] = $handle;
            }
        } catch (Throwable $error) {
            foreach ($handles as $handle) {
                try {
                    $this->registry->release($handle);
                } catch (Throwable) {
                    // Keep the original registration failure.
                }
            }
            throw $error;
        }

        return [$arguments, $handles];
    }

    private function api(string $api, array $fields, array $argumentHandles = [])
    {
        $parentEffect = $this->currentEffect();
        if ($argumentHandles !== []) {
            $this->argumentFrames[] = $argumentHandles;
        }

        try {
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
        } finally {
            if ($argumentHandles !== []) {
                $releasedFrame = array_pop($this->argumentFrames);
                if (!self::sameHandleList($releasedFrame, $argumentHandles)) {
                    throw new RuntimeException('callable_argument_frame_stack');
                }
            }
        }
    }

    private function performBoundary(string $action, array $arguments): array
    {
        if ($action === 'action_counter_increment' || $action === 'action_counter_read') {
            expect_payload_keys(
                ['payload' => $arguments],
                ['handle'],
                'callable_boundary_action_counter',
            );
            if (!is_array($arguments['handle'])) {
                throw new RuntimeException('callable_boundary_action_counter_handle');
            }

            $hookName = $this->registry->resolve($arguments['handle'], 'argument');
            if ($action === 'action_counter_increment') {
                if (!isset($GLOBALS['wp_actions'][$hookName])) {
                    $GLOBALS['wp_actions'][$hookName] = 1;
                } else {
                    ++$GLOBALS['wp_actions'][$hookName];
                }
                // WordPress increments before pushing the current hook. Keep the
                // public PHP stack aligned even when the counter boundary throws.
                $GLOBALS['wp_current_filter'][] = $hookName;
                return self::encodePod($GLOBALS['wp_actions'][$hookName]);
            }

            global $wp_actions;
            $count = isset($wp_actions[$hookName]) ? $wp_actions[$hookName] : 0;
            return self::encodePod($count);
        }

        if ($action === 'array_key') {
            expect_payload_keys(
                ['payload' => $arguments],
                ['handle', 'access', 'null_to_zero'],
                'callable_boundary_array_key',
            );
            if (!is_array($arguments['handle'])
                || !is_string($arguments['access'])
                || !in_array($arguments['access'], ['isset', 'nested_isset', 'read', 'write', 'unset', 'increment'], true)
                || !is_bool($arguments['null_to_zero'])
            ) {
                throw new RuntimeException('callable_boundary_array_key_shape');
            }

            $original = $this->registry->resolve($arguments['handle'], 'argument');
            if ($arguments['null_to_zero'] && $original === null) {
                $original = 0;
            }

            if (is_array($original) || is_object($original)) {
                $target = [];
                switch ($arguments['access']) {
                    case 'isset':
                        isset($target[$original]);
                        break;
                    case 'nested_isset':
                        isset($target[$original]['callback']);
                        break;
                    case 'read':
                        $readValue = $target[$original];
                        unset($readValue);
                        break;
                    case 'write':
                        $target[$original] = true;
                        break;
                    case 'unset':
                        unset($target[$original]);
                        break;
                    case 'increment':
                        $target[$original]++;
                        break;
                }
                throw new RuntimeException('callable_invalid_array_offset_unreachable');
            }

            try {
                $key = self::normalizePhpArrayKey($original);
            } catch (Throwable $normalizationError) {
                $target = [];
                switch ($arguments['access']) {
                    case 'isset':
                        isset($target[$original]);
                        break;
                    case 'nested_isset':
                        isset($target[$original]['callback']);
                        break;
                    case 'read':
                        $readValue = $target[$original];
                        unset($readValue);
                        break;
                    case 'write':
                        $target[$original] = true;
                        break;
                    case 'unset':
                        unset($target[$original]);
                        break;
                    case 'increment':
                        $target[$original]++;
                        break;
                }
                throw $normalizationError;
            }
            $target = [];
            if ($arguments['access'] === 'increment') {
                $target[self::decodePhpKey($key)] = 0;
            } elseif ($arguments['access'] === 'nested_isset') {
                $target[self::decodePhpKey($key)] = ['callback' => true];
            } elseif (in_array($arguments['access'], ['read', 'unset'], true)) {
                $target[self::decodePhpKey($key)] = true;
            }

            switch ($arguments['access']) {
                case 'isset':
                    isset($target[$original]);
                    break;
                case 'nested_isset':
                    isset($target[$original]['callback']);
                    break;
                case 'read':
                    $readValue = $target[$original];
                    unset($readValue);
                    break;
                case 'write':
                    $target[$original] = true;
                    break;
                case 'unset':
                    unset($target[$original]);
                    break;
                case 'increment':
                    $target[$original]++;
                    break;
            }

            return [
                'original' => self::encodePod($original),
                'key' => $key,
            ];
        }

        if ($action === 'callback_id') {
            expect_payload_keys(['payload' => $arguments], ['handle'], 'callable_boundary_callback_id');
            if (!is_array($arguments['handle'])) {
                throw new RuntimeException('callable_boundary_callback_handle');
            }

            $callback = $this->registry->resolve($arguments['handle'], 'argument');
            $id = _wp_filter_build_unique_id(null, $callback, false);
            return ['key' => CallableIdentity::arrayKey($id)];
        }

        if ($action === 'accepted_args') {
            expect_payload_keys(['payload' => $arguments], ['handle'], 'callable_boundary_accepted_args');
            if (!is_array($arguments['handle'])) {
                throw new RuntimeException('callable_boundary_accepted_args_handle');
            }

            $value = $this->registry->resolve($arguments['handle'], 'argument');
            return self::encodePod((int) $value);
        }

        if ($action === 'retain_callable') {
            expect_payload_keys(['payload' => $arguments], ['handle'], 'callable_boundary_retain_callable');
            if (!is_array($arguments['handle'])) {
                throw new RuntimeException('callable_boundary_retain_callable_handle');
            }

            $value = $this->registry->resolve($arguments['handle'], 'argument');
            $handle = $this->registry->retainCallable($value);
            $this->issuedHandles[] = $handle;
            return ['handle' => $handle];
        }

        if ($action === 'hook_scalar') {
            expect_payload_keys(['payload' => $arguments], ['handle'], 'callable_boundary_hook_scalar');
            if (!is_array($arguments['handle'])) {
                throw new RuntimeException('callable_boundary_hook_scalar_handle');
            }

            $value = $this->registry->resolve($arguments['handle'], 'argument');
            if (!self::isHookScalar($value)) {
                return ['supported' => false, 'value' => null];
            }

            return ['supported' => true, 'value' => self::encodePod($value)];
        }

        if ($action === 'missing_offset') {
            expect_payload_keys(['payload' => $arguments], ['key'], 'callable_boundary_missing_offset');
            $key = self::decodePhpKey($arguments['key']);
            $absent = [];
            $value = $absent[$key];
            unset($value);
            return ['missing' => true];
        }

        if ($action === 'null_property') {
            expect_payload_keys(['payload' => $arguments], ['property'], 'callable_boundary_null_property');
            if ($arguments['property'] !== 'callbacks') {
                throw new RuntimeException('callable_boundary_null_property_name');
            }

            $null = null;
            $value = $null->{$arguments['property']};
            return ['missing' => $value === null];
        }

        if ($action === 'null_foreach') {
            expect_payload_keys(['payload' => $arguments], [], 'callable_boundary_null_foreach');
            $null = null;
            foreach ($null as $value) {
                unset($value);
            }
            return ['missing' => true];
        }

        if ($action === 'null_method') {
            expect_payload_keys(['payload' => $arguments], ['method'], 'callable_boundary_null_method');
            if (!in_array($arguments['method'], ['add_filter', 'remove_filter', 'has_filter', 'apply_filters'], true)) {
                throw new RuntimeException('callable_boundary_null_method_name');
            }

            $null = null;
            $method = $arguments['method'];
            $null->{$method}();
            return ['called' => true];
        }

        if ($action === 'release_arguments') {
            expect_payload_keys(['payload' => $arguments], ['handles'], 'callable_boundary_release_arguments');
            if (!is_array($arguments['handles']) || !array_is_list($arguments['handles'])) {
                throw new RuntimeException('callable_boundary_release_arguments_shape');
            }

            $activeFrame = $this->argumentFrames === []
                ? null
                : $this->argumentFrames[array_key_last($this->argumentFrames)];
            if ($activeFrame === null || !self::sameHandleList($arguments['handles'], $activeFrame)) {
                throw new RuntimeException('callable_boundary_argument_frame_mismatch');
            }

            foreach ($arguments['handles'] as $handle) {
                if (!is_array($handle)) {
                    throw new RuntimeException('callable_boundary_release_argument_handle');
                }
                $this->registry->resolve($handle, 'argument');
            }

            $firstError = null;
            foreach ($arguments['handles'] as $handle) {
                try {
                    if (!$this->registry->release($handle)) {
                        throw new RuntimeException('callable_argument_release_not_final');
                    }
                } catch (Throwable $error) {
                    $firstError ??= $error;
                }
            }
            if ($firstError !== null) {
                throw $firstError;
            }

            return ['released' => true];
        }

        throw new RuntimeException('callable_boundary_action');
    }

    private static function normalizePhpArrayKey(mixed $value): array
    {
        $before = error_get_last();
        $probe = [];
        set_error_handler(static fn (): bool => true);
        try {
            $probe[$value] = true;
        } finally {
            restore_error_handler();
        }

        if (error_get_last() !== $before) {
            error_clear_last();
            throw new RuntimeException('callable_internal_key_error_leak');
        }

        $key = array_key_first($probe);
        if (is_int($key)) {
            return ['type' => 'int', 'hex' => bin2hex((string) $key)];
        }
        if (is_string($key)) {
            return ['type' => 'string', 'hex' => bin2hex($key)];
        }

        throw new RuntimeException('callable_normalized_key_missing');
    }

    private static function sameHandleList(array $left, array $right): bool
    {
        if (count($left) !== count($right)) {
            return false;
        }

        foreach ($left as $index => $handle) {
            if (!is_array($handle) || !is_array($right[$index] ?? null)) {
                return false;
            }
            foreach (['request', 'kind', 'slot', 'generation', 'token'] as $field) {
                if (!array_key_exists($field, $handle)
                    || !array_key_exists($field, $right[$index])
                    || $handle[$field] !== $right[$index][$field]
                ) {
                    return false;
                }
            }
        }

        return true;
    }

    private function handleNestedInvoke(array $frame, string $expectedParent): void
    {
        expect_kind($frame, 'invoke_php');
        expect_parent_effect($frame, $expectedParent);
        $payload = $frame['payload'];
        $operation = $payload['operation'] ?? null;

        if ($operation === 'boundary') {
            expect_payload_keys($frame, ['operation', 'effect_id', 'action', 'arguments'], 'callable_boundary');
            $effectId = $payload['effect_id'];
            if (!is_string($effectId) || !is_string($payload['action']) || !is_array($payload['arguments'])) {
                throw new RuntimeException('callable_boundary_metadata');
            }

            $completion = null;
            $this->withEffect($effectId, function () use ($payload, &$completion): void {
                try {
                    $value = $this->performBoundary($payload['action'], $payload['arguments']);
                    $completion = ['kind' => 'value', 'value' => $value];
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

        if ($operation === 'callable') {
            expect_payload_keys(
                $frame,
                ['operation', 'effect_id', 'handle', 'hook', 'priority', 'call_mode', 'action_dispatch', 'args'],
                'callable_invoke',
            );
            $effectId = $payload['effect_id'];
            if (!is_string($effectId)) {
                throw new RuntimeException('callable_invoke_effect_id');
            }

            $completion = null;
            $this->withEffect($effectId, function () use ($payload, &$completion): void {
                if (!is_array($payload['hook'])
                    || !is_array($payload['priority'])
                    || !is_string($payload['call_mode'])
                    || !is_bool($payload['action_dispatch'])
                ) {
                    throw new RuntimeException('callable_invoke_metadata');
                }
                $hookName = self::decodePod($payload['hook']);
                if (!self::isHookScalar($hookName)) {
                    throw new RuntimeException('callable_invoke_hook_scalar');
                }
                $priority = self::decodePhpKey($payload['priority']);
                $callable = $this->registry->resolve($payload['handle'], 'callable');
                try {
                    $arguments = $this->decodeCallableArguments($payload['args']);
                    if ($payload['call_mode'] === 'zero') {
                        if ($arguments !== []) {
                            throw new RuntimeException('callable_zero_args_not_empty');
                        }
                        $value = call_user_func($callable);
                    } elseif ($payload['call_mode'] === 'array' || $payload['call_mode'] === 'all') {
                        // WordPress do_action() exposes the original variadic arguments
                        // to `all`, then unwraps the PHP4-style array(&$this) form for
                        // named listeners. Keep this at the PHP call boundary so the
                        // Argument handle and underlying object identity stay intact.
                        if ($payload['action_dispatch']
                            && $payload['call_mode'] === 'array'
                            && isset($arguments[0])
                            && is_array($arguments[0])
                            && count($arguments[0]) === 1
                            && isset($arguments[0][0])
                            && is_object($arguments[0][0])
                        ) {
                            $arguments[0] = $arguments[0][0];
                        }
                        if ($payload['call_mode'] === 'all'
                            && ($arguments === [] || self::encodePod($arguments[0]) !== self::encodePod($hookName))
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
                    unset($callable, $arguments, $hookName, $priority, $value);
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

        if ($operation === 'release_callable_unwind') {
            $this->handleCallableUnwindRelease($frame, $expectedParent);
            return;
        }

        throw new RuntimeException('callable_unknown_invoke_operation');
    }

    private function handleCallableUnwindRelease(array $frame, ?string $parentEffect): void
    {
        expect_payload_keys(
            $frame,
            ['operation', 'effect_id', 'handle', 'throwable'],
            $parentEffect === null ? 'callable_root_unwind_release' : 'callable_unwind_release',
        );
        $payload = $frame['payload'];
        $effectId = $payload['effect_id'];
        if (!is_string($effectId) || $effectId === '') {
            throw new RuntimeException('callable_unwind_release_effect_id');
        }
        if (!is_array($payload['handle']) || !is_array($payload['throwable'])) {
            throw new RuntimeException('callable_unwind_release_handles');
        }

        // Resolve both descriptors before releasing anything. Registry::resolve
        // checks the complete descriptor shape, token, generation, and kind.
        $this->registry->resolve($payload['handle'], 'callable');
        $original = $this->registry->resolve($payload['throwable'], 'throwable');
        if (!$original instanceof Throwable) {
            throw new RuntimeException('callable_unwind_release_throwable');
        }

        $completion = null;
        $this->withEffect($effectId, function () use ($payload, $original, &$completion): void {
            $releaseCompletedNormally = false;
            try {
                try {
                    throw $original;
                } finally {
                    // This is the actual final lease release. PHP may run the
                    // callback destructor here and replace $original with its
                    // Throwable, attaching $original as getPrevious().
                    if (!$this->registry->release($payload['handle'])) {
                        throw new RuntimeException('callable_unwind_release_not_final');
                    }
                    $releaseCompletedNormally = true;
                }
            } catch (Throwable $error) {
                if ($releaseCompletedNormally && $error === $original) {
                    $completion = ['kind' => 'value', 'value' => ['released' => true]];
                } else {
                    $completion = $this->throwableCompletion($error);
                }
            }
        });

        if (!is_array($completion)) {
            throw new RuntimeException('callable_unwind_release_completion');
        }
        $this->wire->send('resume', [
            'effect_id' => $effectId,
            'completion' => $completion,
        ], $parentEffect);
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

    private function decodeCallableArguments($nodes): array
    {
        if (!is_array($nodes) || !array_is_list($nodes)) {
            throw new RuntimeException('callable_args_not_list');
        }

        $arguments = [];
        foreach ($nodes as $node) {
            if (is_array($node) && ($node['type'] ?? null) === 'handle') {
                expect_payload_keys(
                    ['payload' => $node],
                    ['type', 'handle'],
                    'callable_argument_handle_marker',
                );
                if (!is_array($node['handle'])) {
                    throw new RuntimeException('callable_argument_handle_marker');
                }
                $arguments[] = $this->registry->resolve($node['handle'], 'argument');
                continue;
            }
            $arguments[] = self::decodePod($node);
        }

        return $arguments;
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

    private static function decodePhpKey($node): int|string
    {
        if (!is_array($node) || !isset($node['type'])) {
            throw new RuntimeException('callable_php_key_shape');
        }
        expect_payload_keys(['payload' => $node], ['type', 'hex'], 'callable_php_key');
        $encoded = self::decodeHexString($node['hex']);
        if ($node['type'] === 'int') {
            return self::decodeInt($encoded);
        }
        if ($node['type'] !== 'string') {
            throw new RuntimeException('callable_php_key_type');
        }
        $key = $encoded;
        if (is_string($key)) {
            $probe = [];
            $probe[$key] = true;
            if (is_int(array_key_first($probe))) {
                throw new RuntimeException('callable_php_key_not_normalized');
            }
        }

        return $key;
    }

    private static function isHookScalar(mixed $value): bool
    {
        return $value === null || is_bool($value) || is_int($value) || is_float($value) || is_string($value);
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

function add_action($hook_name, $callback, $priority = 10, $accepted_args = 1)
{
    return callable_adapter()->addAction($hook_name, $callback, $priority, $accepted_args);
}

function remove_filter($hook_name, $callback, $priority = 10)
{
    return callable_adapter()->removeFilter($hook_name, $callback, $priority);
}

function remove_action($hook_name, $callback, $priority = 10)
{
    return callable_adapter()->removeAction($hook_name, $callback, $priority);
}

function has_filter($hook_name, $callback = false, $priority = false)
{
    return callable_adapter()->hasFilter($hook_name, $callback, $priority);
}

function has_action($hook_name, $callback = false, $priority = false)
{
    return callable_adapter()->hasAction($hook_name, $callback, $priority);
}

function apply_filters($hook_name, $value, ...$args)
{
    return callable_adapter()->applyFilters($hook_name, $value, ...$args);
}

function do_action($hook_name, ...$args)
{
    return callable_adapter()->doAction($hook_name, ...$args);
}

function current_filter()
{
    return callable_adapter()->currentFilter();
}

function current_action()
{
    return callable_adapter()->currentAction();
}

function doing_filter($hook_name = null)
{
    return callable_adapter()->doingFilter($hook_name);
}

function doing_action($hook_name = null)
{
    return callable_adapter()->doingAction($hook_name);
}

function did_filter($hook_name)
{
    return callable_adapter()->didFilter($hook_name);
}

function did_action($hook_name)
{
    return callable_adapter()->didAction($hook_name);
}
