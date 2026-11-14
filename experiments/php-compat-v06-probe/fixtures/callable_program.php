<?php

declare(strict_types=1);

final class CallableStaticFixture
{
    public static function convert(string $value, string $extra = ''): string
    {
        return $value . ':static-string:' . $extra;
    }

    public static function replacement(string $value): string
    {
        return $value . ':static-array';
    }
}

final class CallableZeroFixture
{
    public static function run(): string
    {
        $GLOBALS['callable_zero_calls']++;

        return 'zero-only';
    }
}

final class CallableDuplicateFixture
{
    public static function run(string $value, string $extra = ''): string
    {
        $GLOBALS['callable_duplicate_order'][] = 'duplicate:' . $extra;

        return $value . ':duplicate:' . $extra;
    }
}

final class CallableObjectFixture
{
    public function convert(string $value): string
    {
        return $value . ':object-method';
    }
}

final class CallableInvokableFixture
{
    public function __invoke(string $value): string
    {
        return $value . ':invokable';
    }
}

final class CallableCollisionFixture
{
    public function invokeTarget(string $value): string
    {
        return $value . ':collision-object';
    }
}

final class CallableReentrantDestructor
{
    public string $rawId;

    public function __construct(
        private readonly string $hookName,
    ) {
        $this->rawId = '';
    }

    public function __invoke(string $value): string
    {
        return $value . ':reentrant';
    }

    public function __destruct()
    {
        $GLOBALS['callable_reentry_trace'][] = 'destructor-enter';
        $GLOBALS['callable_reentry_observation'] = [
            'old_priority' => has_filter($this->hookName, $this->rawId),
            'current_filter' => current_filter(),
            'added' => add_filter($this->hookName, 'callable_program_destructor_added', 10),
            'new_priority' => has_filter($this->hookName, 'callable_program_destructor_added'),
        ];
        $GLOBALS['callable_reentry_trace'][] = 'destructor-exit';
    }
}

final class CallableLifetimeFixture
{
    public function __invoke(string $value): string
    {
        $GLOBALS['callable_lifetime_trace'][] = 'callback-enter';
        remove_filter('callable-lifetime', $this, 10);
        unset($GLOBALS['callable_lifetime_alias']);
        $GLOBALS['callable_lifetime_trace'][] = 'alias-unset';
        throw new RuntimeException('callable lifetime fixture');
    }

    public function __destruct()
    {
        $GLOBALS['callable_lifetime_trace'][] = 'destructor';
    }
}

final class CallablePriorityCowListener
{
    private $state;

    public function __construct(
        private string $label,
        array &$state,
        private ?Closure $onCall = null,
    ) {
        $this->state =& $state;
    }

    public function __invoke(mixed $value): mixed
    {
        $this->state['trace'][] = $this->label;
        $this->state['events'][] = ['kind' => 'invoke', 'label' => $this->label, 'phase' => $this->state['phase']];
        if ($this->onCall !== null) {
            ($this->onCall)();
        }

        return $value . ':' . $this->label;
    }

    public function __destruct()
    {
        $event = [
            'kind' => 'destruct',
            'label' => $this->label,
            'phase' => $this->state['phase'],
            'warnings_seen' => count($this->state['warnings']),
            'trace_count' => count($this->state['trace']),
        ];
        $this->state['events'][] = $event;
        $this->state['destructors'][] = $event;
    }
}

final class CallablePriorityThrowableSeed
{
    private $state;

    public function __construct(array &$state, private bool $throwDestructor)
    {
        $this->state =& $state;
    }

    public function __invoke(mixed $value): mixed
    {
        return $value . ':seed';
    }

    public function __destruct()
    {
        $this->state['events'][] = ['kind' => 'seed_destructor_enter', 'phase' => $this->state['phase']];
        if ($this->throwDestructor) {
            $throwable = new RuntimeException('B from seed destructor');
            $this->state['throwable_b'] = WeakReference::create($throwable);
            $this->state['events'][] = ['kind' => 'seed_destructor_throw_b', 'phase' => $this->state['phase']];
            throw $throwable;
        }
        $this->state['events'][] = ['kind' => 'seed_destructor_exit', 'phase' => $this->state['phase']];
    }
}

final class CallablePriorityThrowableAnchor
{
    public function __invoke(mixed $value): mixed
    {
        return $value . ':anchor';
    }
}

function callable_program_function(string $value): string
{
    $current = current_filter();
    $GLOBALS['callable_function_state'][] = [
        'current_filter_hex' => is_string($current) ? bin2hex($current) : null,
        'doing_any' => doing_filter(),
        'doing_named' => doing_filter('callable-function'),
        'did_filter' => did_filter('callable-function'),
    ];

    return $value . ':function';
}

function callable_program_pod_filter($value, ...$args): array
{
    return ['value' => $value, 'args' => $args];
}

function callable_program_all_listener(string $hookName, ...$args): void
{
    if ($hookName === $GLOBALS['callable_raw_hook_name']) {
        $GLOBALS['callable_all_state'][] = array_map(
            static fn (string $value): string => bin2hex($value),
            func_get_args(),
        );
    }
}

function callable_program_destructor_added(string $value): string
{
    return $value . ':destructor-added';
}

function callable_program_case_name(string $value): string
{
    $GLOBALS['callable_case_trace'][] = 'called';
    return $value . ':case';
}

function callable_program_case_capture(array &$result): void
{
    $result = [
        'current_filter' => current_filter(),
        'doing_filter' => doing_filter(),
        'did_filter' => did_filter('callable-function'),
        'callback_state' => $GLOBALS['callable_function_state'],
        'current_filter_after' => current_filter(),
        'doing_filter_after' => doing_filter(),
        'doing_named_filter_after' => doing_filter('callable-function'),
    ];
}

function callable_program_identity($callback, ?string $expectedId = null): array
{
    $id = _wp_filter_build_unique_id('identity-probe', $callback, 999);
    if ($id === null) {
        return ['id_hex' => null, 'key_type' => null, 'key_hex' => null];
    }

    $probe = [];
    $probe[$id] = true;
    $key = array_key_first($probe);

    if ($expectedId !== null) {
        return [
            'formula_matches' => $id === $expectedId,
            'key_type' => is_int($key) ? 'int' : 'string',
        ];
    }

    return [
        'id_hex' => bin2hex($id),
        'key_type' => is_int($key) ? 'int' : 'string',
        'key_hex' => bin2hex((string) $key),
    ];
}
function callable_program_run_base(): array
{
    $result = [];
    $GLOBALS['callable_function_state'] = [];
    $GLOBALS['callable_all_state'] = [];
    $identityObject = new CallableObjectFixture();
    $identityInvokable = new CallableInvokableFixture();
    $identityByte = "\xff\0id";
    $result['identity_matrix'] = [
        'function' => callable_program_identity('callable_program_function'),
        'static_string' => callable_program_identity('CallableStaticFixture::convert'),
        'static_array' => callable_program_identity([CallableStaticFixture::class, 'convert']),
        'object' => callable_program_identity($identityObject, 'spl_object_id:' . spl_object_id($identityObject)),
        'object_method' => callable_program_identity([$identityObject, 'convert'], (string) spl_object_id($identityObject) . 'convert'),
        'invokable_class' => callable_program_identity($identityInvokable, 'spl_object_id:' . spl_object_id($identityInvokable)),
        'byte_string' => callable_program_identity($identityByte),
        'numeric_string' => callable_program_identity('123'),
        'zero_string' => callable_program_identity('0'),
        'null_id' => callable_program_identity(123),
    ];

    $functionAdded = add_filter('callable-function', 'callable_program_function', 10, 1);
    $functionPriority = has_filter('callable-function', 'callable_program_function');
    $functionAnyBeforeRemove = has_filter('callable-function');
    $functionAtPriority = has_filter('callable-function', 'callable_program_function', 10);
    $functionNonIntPriority = has_filter('callable-function', 'callable_program_function', '10');
    $functionValue = apply_filters('callable-function', 'seed');
    $functionRemoved = remove_filter('callable-function', 'callable_program_function');
    $functionAnyAfterRemove = has_filter('callable-function');
    $result['function_string'] = [
        'added' => $functionAdded,
        'priority' => $functionPriority,
        'any_before_remove' => $functionAnyBeforeRemove,
        'at_priority' => $functionAtPriority,
        'non_int_priority' => $functionNonIntPriority,
        'value' => $functionValue,
        'removed' => $functionRemoved,
        'any_after_remove' => $functionAnyAfterRemove,
        'did_filter' => did_filter('callable-function'),
        'callback_state' => $GLOBALS['callable_function_state'],
        'current_filter_after' => current_filter(),
        'doing_filter_after' => doing_filter(),
        'doing_named_filter_after' => doing_filter('callable-function'),
    ];

    add_filter('callable-pod', 'callable_program_pod_filter', 30, 99);
    $podValue = [
        'int_min' => PHP_INT_MIN,
        'int_max' => PHP_INT_MAX,
        'float' => 1.25,
        'negative_zero' => -0.0,
        'bool' => false,
        'null' => null,
        'bytes' => "\xff\0pod",
        'array' => ['01' => 'leading', 1 => 'integer-key'],
    ];
    $podArgs = [true, 23, -0.5, null, ['x' => [4 => 'nested']]];
    $podOutput = apply_filters('callable-pod', $podValue, ...$podArgs);
    $result['pod_filter_values'] = [
        'strict_roundtrip' => $podOutput === ['value' => $podValue, 'args' => $podArgs],
    ];

    $GLOBALS['callable_zero_calls'] = 0;
    add_filter('callable-zero-args', 'CallableZeroFixture::run', 31, 0);
    $zeroValue = apply_filters('callable-zero-args', 'ignored', 'also-ignored');
    $result['zero_accepted_args'] = [
        'value' => $zeroValue,
        'calls' => $GLOBALS['callable_zero_calls'],
    ];

    $staticString = 'CallableStaticFixture::convert';
    $staticArray = [CallableStaticFixture::class, 'convert'];
    $staticFirstAdded = add_filter('callable-static', $staticString, 10, 2);
    $staticSameIdPriority = has_filter('callable-static', $staticArray);
    $staticReplacementAdded = add_filter('callable-static', [CallableStaticFixture::class, 'convert'], 10, 1);
    $staticSecondIdPriority = has_filter('callable-static', $staticString);
    $staticValue = apply_filters('callable-static', 'seed', 'extra');
    $result['static_replacement'] = [
        'first_added' => $staticFirstAdded,
        'same_id_priority' => $staticSameIdPriority,
        'replacement_added' => $staticReplacementAdded,
        'second_id_priority' => $staticSecondIdPriority,
        'value' => $staticValue,
    ];

    $methodCaseFirst = add_filter(
        'callable-method-case',
        'CallableStaticFixture::convert',
        16,
        1,
    );
    $methodCaseSecond = add_filter(
        'callable-method-case',
        'callablestaticfixture::CONVERT',
        16,
        1,
    );
    $methodCaseFirstPriority = has_filter(
        'callable-method-case',
        'CallableStaticFixture::convert',
    );
    $methodCaseSecondPriority = has_filter(
        'callable-method-case',
        'callablestaticfixture::CONVERT',
    );
    $methodCaseValue = apply_filters('callable-method-case', 'seed');
    $result['static_case_ids'] = [
        'first_added' => $methodCaseFirst,
        'second_added' => $methodCaseSecond,
        'first_priority' => $methodCaseFirstPriority,
        'second_priority' => $methodCaseSecondPriority,
        'value' => $methodCaseValue,
    ];

    $objectCallback = new CallableObjectFixture();
    add_filter('callable-object-method', [$objectCallback, 'convert'], 12);
    $objectMethodPriority = has_filter('callable-object-method', [$objectCallback, 'convert']);
    $objectMethodRemoved = remove_filter('callable-object-method', [$objectCallback, 'convert'], 12);
    $objectMethodAfterRemove = has_filter('callable-object-method', [$objectCallback, 'convert']);
    $result['object_method'] = [
        'priority' => $objectMethodPriority,
        'removed_by_fresh_array' => $objectMethodRemoved,
        'after_remove' => $objectMethodAfterRemove,
    ];

    $invokable = new CallableInvokableFixture();
    $invokableAdded = add_filter('callable-invokable', $invokable, 14);
    $invokableValue = apply_filters('callable-invokable', 'seed');
    $result['invokable_object'] = [
        'added' => $invokableAdded,
        'value' => $invokableValue,
        'priority' => has_filter('callable-invokable', $invokable),
    ];

    $GLOBALS['callable_case_trace'] = [];
    $caseUpper = add_filter('callable-case', 'callable_program_case_name', 15);
    $caseLower = add_filter('callable-case', 'CALLABLE_PROGRAM_CASE_NAME', 15);
    $caseUpperPriority = has_filter('callable-case', 'callable_program_case_name');
    $caseLowerPriority = has_filter('callable-case', 'CALLABLE_PROGRAM_CASE_NAME');
    $caseValue = apply_filters('callable-case', 'seed');
    $result['case_sensitive_ids'] = [
        'upper_added' => $caseUpper,
        'lower_added' => $caseLower,
        'upper_priority' => $caseUpperPriority,
        'lower_priority' => $caseLowerPriority,
        'value' => $caseValue,
        'calls' => $GLOBALS['callable_case_trace'],
    ];

    $GLOBALS['callable_duplicate_order'] = [];
    $other = static function (string $value): string {
        $GLOBALS['callable_duplicate_order'][] = 'other';
        return $value . ':other';
    };
    add_filter('callable-duplicate', 'CallableDuplicateFixture::run', 17, 1);
    add_filter('callable-duplicate', $other, 17, 1);
    $duplicateReplacementAdded = add_filter('callable-duplicate', [CallableDuplicateFixture::class, 'run'], 17, '2');
    $duplicateValue = apply_filters('callable-duplicate', 'seed', 'extra');
    $result['duplicate_replacement_order'] = [
        'replacement_added' => $duplicateReplacementAdded,
        'value' => $duplicateValue,
        'trace' => $GLOBALS['callable_duplicate_order'],
    ];

    $nullPriorityAdded = add_filter('callable-null-priority', 'callable_program_function', null, 1);
    $nullPriority = has_filter('callable-null-priority', 'callable_program_function');
    $nullDefaultRemove = remove_filter('callable-null-priority', 'callable_program_function');
    $nullExplicitRemove = remove_filter('callable-null-priority', 'callable_program_function', 0);
    $result['null_priority'] = [
        'added' => $nullPriorityAdded,
        'priority' => $nullPriority,
        'default_remove' => $nullDefaultRemove,
        'explicit_zero_remove' => $nullExplicitRemove,
    ];

    $GLOBALS['callable_reentry_trace'] = [];
    $GLOBALS['callable_reentry_observation'] = [];
    $reentrantObject = new CallableReentrantDestructor('callable-destructor-reentry');
    $reentrantRawId = _wp_filter_build_unique_id(
        'callable-destructor-reentry',
        $reentrantObject,
        10,
    );
    $reentrantObject->rawId = $reentrantRawId;
    $reentrantWeak = WeakReference::create($reentrantObject);
    $reentrantAdded = add_filter('callable-destructor-reentry', $reentrantObject, 10);
    unset($reentrantObject);
    $reentrantAliasAliveBeforeRemove = $reentrantWeak->get() !== null;
    $reentrantRemovedByRawId = remove_filter(
        'callable-destructor-reentry',
        $reentrantRawId,
        10,
    );
    $result['destructor_reentry'] = [
        'added' => $reentrantAdded,
        'alias_alive_before_remove' => $reentrantAliasAliveBeforeRemove,
        'removed_by_raw_id' => $reentrantRemovedByRawId,
        'weak_alive_after_remove' => $reentrantWeak->get() !== null,
        'observation_during_remove' => $GLOBALS['callable_reentry_observation'],
        'trace' => $GLOBALS['callable_reentry_trace'],
        'old_priority_after' => has_filter('callable-destructor-reentry', $reentrantRawId),
        'new_priority_after' => has_filter('callable-destructor-reentry', 'callable_program_destructor_added'),
        'value_after' => apply_filters('callable-destructor-reentry', 'seed'),
    ];

    $GLOBALS['callable_lifetime_trace'] = [];
    $GLOBALS['callable_lifetime_alias'] = new CallableLifetimeFixture();
    $lifetimeWeak = WeakReference::create($GLOBALS['callable_lifetime_alias']);
    add_filter('callable-lifetime', $GLOBALS['callable_lifetime_alias'], 10);
    unset($GLOBALS['callable_lifetime_alias']);
    $lifetimeError = null;
    try {
        apply_filters('callable-lifetime', 'seed');
    } catch (Throwable $error) {
        $GLOBALS['callable_lifetime_trace'][] = 'parent-catch';
        $lifetimeError = ['class_hex' => bin2hex($error::class), 'message_hex' => bin2hex($error->getMessage())];
    }
    $result['self_remove_throw_lifetime'] = [
        'error' => $lifetimeError,
        'weak_alive_in_catch' => $lifetimeWeak->get() !== null,
        'trace' => $GLOBALS['callable_lifetime_trace'],
        'current_filter_after' => current_filter(),
    ];

    $invalidFunction = 'callable_missing_function_7a21';
    $invalidFunctionAdded = add_filter('callable-invalid-function', $invalidFunction, 19);
    $invalidFunctionPriority = has_filter('callable-invalid-function', $invalidFunction);
    $invalidFunctionError = null;
    try {
        apply_filters('callable-invalid-function', 'seed');
    } catch (Throwable $error) {
        $invalidFunctionError = ['class_hex' => bin2hex($error::class), 'message_hex' => bin2hex($error->getMessage())];
    }
    $result['invalid_function_delayed_error'] = [
        'added' => $invalidFunctionAdded,
        'priority' => $invalidFunctionPriority,
        'error' => $invalidFunctionError,
    ];

    $invalidObject = new stdClass();
    $invalidObjectAdded = add_filter('callable-invalid-object', $invalidObject, 20);
    $invalidObjectPriority = has_filter('callable-invalid-object', $invalidObject);
    $invalidObjectError = null;
    try {
        apply_filters('callable-invalid-object', 'seed');
    } catch (Throwable $error) {
        $invalidObjectError = ['class_hex' => bin2hex($error::class), 'message_hex' => bin2hex($error->getMessage())];
    }
    $result['invalid_object_delayed_error'] = [
        'added' => $invalidObjectAdded,
        'priority' => $invalidObjectPriority,
        'error' => $invalidObjectError,
    ];

    $nullIdAdded = add_filter('callable-null-id', 23, 21);
    $nullIdAny = has_filter('callable-null-id');
    $result['null_id_noop'] = [
        'added' => $nullIdAdded,
        'has_any' => $nullIdAny,
    ];

    $byteId = "\xff\0callable";
    $byteIdAdded = add_filter('callable-byte-id', $byteId, 22);
    $byteIdPriority = has_filter('callable-byte-id', $byteId);
    $byteIdRemoved = remove_filter('callable-byte-id', $byteId, 22);
    $GLOBALS['callable_raw_hook_name'] = "\xff\0raw-hook";
    $allAdded = add_filter('all', 'callable_program_all_listener', 1, 1);
    $rawHookAdded = add_filter($GLOBALS['callable_raw_hook_name'], 'callable_program_function', 27, 1);
    $rawHookValue = apply_filters($GLOBALS['callable_raw_hook_name'], 'seed', 'extra');
    $result['all_listener_raw_hook'] = [
        'all_added' => $allAdded,
        'hook_added' => $rawHookAdded,
        'value_hex' => bin2hex($rawHookValue),
        'all_arguments_hex' => $GLOBALS['callable_all_state'],
    ];

    $result['arbitrary_byte_id'] = [
        'added' => $byteIdAdded,
        'priority' => $byteIdPriority,
        'removed' => $byteIdRemoved,
    ];

    $numericIdAdded = add_filter('callable-numeric-id', '123', 23);
    $numericIdPriority = has_filter('callable-numeric-id', '123');
    $zeroIdAdded = add_filter('callable-zero-id', '0', 24);
    $zeroIdPriority = has_filter('callable-zero-id', '0');
    $zeroIdRemoved = remove_filter('callable-zero-id', '0', 24);
    $result['array_key_coercion'] = [
        'numeric_added' => $numericIdAdded,
        'numeric_priority' => $numericIdPriority,
        'zero_added' => $zeroIdAdded,
        'zero_has_priority' => $zeroIdPriority,
        'zero_removed' => $zeroIdRemoved,
    ];

    $collisionObject = new CallableCollisionFixture();
    $collisionId = (string) spl_object_id($collisionObject) . 'invokeTarget';
    $collisionObjectAdded = add_filter('callable-id-collision', [$collisionObject, 'invokeTarget'], 25);
    $collisionRawAdded = add_filter('callable-id-collision', $collisionId, 25);
    $collisionArrayPriority = has_filter('callable-id-collision', [$collisionObject, 'invokeTarget']);
    $collisionStringPriority = has_filter('callable-id-collision', $collisionId);
    $collisionRemovedByArray = remove_filter('callable-id-collision', [$collisionObject, 'invokeTarget'], 25);
    $result['cross_form_id_collision'] = [
        'object_added' => $collisionObjectAdded,
        'raw_string_added' => $collisionRawAdded,
        'array_priority' => $collisionArrayPriority,
        'string_priority' => $collisionStringPriority,
        'removed_by_array' => $collisionRemovedByArray,
    ];

    return $result;
}

function callable_program_scalar_view($value): array
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

    return ['type' => get_debug_type($value)];
}

function callable_program_action_global_state_snapshot(string $hook): array
{
    $present = array_key_exists('wp_actions', $GLOBALS);
    $value = $present ? $GLOBALS['wp_actions'] : null;
    $snapshot = [
        'present' => $present,
        'type' => $present ? get_debug_type($value) : 'unset',
    ];
    if (is_array($value)) {
        $snapshot['entries'] = [];
        foreach ($value as $key => $entry) {
            $snapshot['entries'][] = [
                'key_hex' => bin2hex((string) $key),
                'value' => callable_program_scalar_view($entry),
            ];
        }
    } else {
        $snapshot['value'] = $present
            ? callable_program_scalar_view($value)
            : ['type' => 'unset'];
    }
    $snapshot['hook_key_present'] = is_array($value) && array_key_exists($hook, $value);

    return $snapshot;
}

function callable_program_action_global_state_set(string $state): void
{
    unset($GLOBALS['wp_actions']);
    switch ($state) {
        case 'unset':
            break;
        case 'null':
            $GLOBALS['wp_actions'] = null;
            break;
        case 'false':
            $GLOBALS['wp_actions'] = false;
            break;
        case 'true':
            $GLOBALS['wp_actions'] = true;
            break;
        case 'int':
            $GLOBALS['wp_actions'] = 7;
            break;
        case 'float':
            $GLOBALS['wp_actions'] = 7.5;
            break;
        case 'string':
            $GLOBALS['wp_actions'] = 'seed';
            break;
        case 'array':
            $GLOBALS['wp_actions'] = [];
            break;
        case 'object':
            $GLOBALS['wp_actions'] = new stdClass();
            break;
        default:
            throw new LogicException('unknown action global state');
    }
}

function callable_program_action_global_state_leaf(string $state, string $operation): array
{
    $hook = 'global-state-' . $state . ($operation === 'did_action' ? '-did' : '');
    unset($GLOBALS['wp_actions']);
    $GLOBALS['wp_current_filter'] = [];
    callable_program_action_global_state_set($state);

    $log = [];
    $callbackCount = 0;
    if ($operation === 'do_action') {
        add_action($hook, static function () use (&$log, &$callbackCount): void {
            ++$callbackCount;
            $log[] = [
                'kind' => 'callback',
                'current_action' => current_action(),
                'doing_action' => doing_action(),
            ];
        });
    }
    $initial = callable_program_action_global_state_snapshot($hook);
    set_error_handler(static function (int $severity, string $message) use (&$log): bool {
        $log[] = [
            'kind' => 'warning',
            'severity' => $severity,
            'message_hex' => bin2hex($message),
        ];

        return true;
    });

    $log[] = ['kind' => 'call', 'operation' => $operation, 'initial' => $initial];
    try {
        $result = $operation === 'do_action' ? do_action($hook) : did_action($hook);
        $log[] = ['kind' => 'result', 'value' => callable_program_scalar_view($result)];
    } catch (Throwable $throwable) {
        $log[] = [
            'kind' => 'throwable',
            'class' => $throwable::class,
            'message_hex' => bin2hex($throwable->getMessage()),
        ];
    } finally {
        restore_error_handler();
    }

    $log[] = [
        'kind' => 'final',
        'state' => callable_program_action_global_state_snapshot($hook),
        'callback_count' => $callbackCount,
        'current_filter_stack' => array_values($GLOBALS['wp_current_filter'] ?? []),
    ];

    return $log;
}

function callable_program_action_global_state_cases(): array
{
    $observations = [];
    foreach (['unset', 'null', 'false', 'true', 'int', 'float', 'string', 'array', 'object'] as $state) {
        foreach (['do_action', 'did_action'] as $operation) {
            $observations[$state][$operation] = callable_program_action_global_state_leaf($state, $operation);
        }
    }

    return $observations;
}

function callable_program_action_counter_cow_leaf(bool $keepSnapshot): array
{
    $mode = $keepSnapshot ? 'snapshot' : 'live';
    $hook = 'callable-action-counter-cow-' . $mode;
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_current_filter'] = [];
    $noopAll = static function (...$args): void {
        if (!array_key_exists(0, $args)) {
            return;
        }
    };
    add_action('all', $noopAll, 0, 0);
    $GLOBALS['wp_actions'][$hook] = 'a';
    $state = [
        'warnings' => [],
        'events' => [],
        'snapshot' => null,
        'nested_started' => false,
    ];

    $read = static function (?array $snapshot) use (&$state, $hook): array {
        return [
            'live' => callable_program_scalar_view($GLOBALS['wp_actions'][$hook] ?? null),
            'snapshot' => $snapshot === null
                ? null
                : callable_program_scalar_view($snapshot[$hook] ?? null),
            'did_action' => callable_program_scalar_view(did_action($hook)),
        ];
    };
    $capture = static function (string $event, array $args) use (&$state, $hook, $read): void {
        $state['events'][] = [
            'event' => $event,
            'args' => $args,
            'current_action' => current_action(),
            'doing_action' => doing_action($hook),
            ...$read($state['snapshot']),
        ];
    };

    set_error_handler(static function (int $severity, string $message) use (
        &$state,
        $hook,
        $keepSnapshot,
        $read
    ): bool {
        $index = count($state['warnings']) + 1;
        $warning = [
            'index' => $index,
            'severity' => $severity,
            'message_hex' => bin2hex($message),
            'phase' => $index === 1 ? 'outer_increment' : 'nested_increment',
        ];
        $state['warnings'][] = $warning;

        if ($index === 1) {
            if ($keepSnapshot) {
                $state['snapshot'] = $GLOBALS['wp_actions'];
            }
            $state['events'][] = [
                'event' => 'warning-before-nested',
                ...$warning,
                ...$read($state['snapshot']),
            ];
            $state['nested_started'] = true;
            set_error_handler(static function (int $nestedSeverity, string $nestedMessage) use (
                &$state,
                $read
            ): bool {
                $nestedWarning = [
                    'index' => count($state['warnings']) + 1,
                    'severity' => $nestedSeverity,
                    'message_hex' => bin2hex($nestedMessage),
                    'phase' => 'nested_increment',
                ];
                $state['warnings'][] = $nestedWarning;
                $state['events'][] = [
                    'event' => 'warning-nested',
                    ...$nestedWarning,
                    ...$read($state['snapshot']),
                ];

                return true;
            });
            try {
                do_action($hook, 'nested');
            } finally {
                restore_error_handler();
            }
            $state['events'][] = [
                'event' => 'warning-after-nested',
                ...$warning,
                ...$read($state['snapshot']),
            ];
        }

        return true;
    });

    $allObserver = static function (...$args) use ($capture, $hook): void {
        if (($args[0] ?? null) === $hook) {
            $capture('all', $args);
        }
    };
    $namedObserver = static function (...$args) use ($capture): void {
        $capture('named', $args);
    };
    add_action('all', $allObserver, 3, 0);
    add_action($hook, $namedObserver, 10, 99);

    $before = [
        'live' => callable_program_scalar_view($GLOBALS['wp_actions'][$hook] ?? null),
        'snapshot' => null,
        'did_action' => callable_program_scalar_view(did_action($hook)),
    ];
    do_action($hook, 'outer');
    $after = [
        'live' => callable_program_scalar_view($GLOBALS['wp_actions'][$hook] ?? null),
        'snapshot' => $state['snapshot'] === null
            ? null
            : callable_program_scalar_view($state['snapshot'][$hook] ?? null),
        'did_action' => callable_program_scalar_view(did_action($hook)),
    ];
    $stack = array_values($GLOBALS['wp_current_filter'] ?? []);
    remove_action($hook, $namedObserver, 10);
    remove_action('all', $allObserver, 3);
    remove_action('all', $noopAll, 0);
    restore_error_handler();

    return [
        'mode' => $mode,
        'snapshot_kept' => $keepSnapshot,
        'before' => $before,
        'events' => $state['events'],
        'warnings' => $state['warnings'],
        'after' => $after,
        'current_filter_stack' => $stack,
    ];
}

function callable_program_action_counter_cow_cases(): array
{
    return [
        'live' => callable_program_action_counter_cow_leaf(false),
        'snapshot' => callable_program_action_counter_cow_leaf(true),
    ];
}

function callable_program_hook_scalar_view($value): array
{
    if (is_float($value) && is_nan($value)) {
        return ['type' => 'float', 'special' => 'nan'];
    }

    return callable_program_scalar_view($value);
}

function callable_program_hook_strict_match($left, $right): bool
{
    if (is_float($left) && is_nan($left) && is_float($right) && is_nan($right)) {
        return true;
    }

    return $left === $right;
}

function callable_program_priority_callback(string $label): Closure
{
    return static function ($value) use ($label) {
        $GLOBALS['callable_coercion_priority_trace'][] = $label;
        return $value . ':' . $label;
    };
}

function callable_program_priority_pair_case(string $name, array $entries): array
{
    $hook = 'callable-coercion-priority-pair-' . $name;
    $trace = [];
    $callbacks = [];
    foreach ($entries as $entry) {
        $label = $entry['label'];
        $callback = static function ($value) use (&$trace, $label) {
            $trace[] = $label;
            return $value;
        };
        $callbacks[$label] = $callback;
        add_filter($hook, $callback, $entry['priority'], 1);
    }
    $value = apply_filters($hook, 'seed');
    $priorities = [];
    foreach ($entries as $entry) {
        $priorities[] = [
            'label' => $entry['label'],
            'priority' => callable_program_scalar_view($entry['priority']),
            'found_at' => callable_program_scalar_view(has_filter($hook, $callbacks[$entry['label']])),
        ];
    }

    return ['value' => $value, 'dispatch' => $trace, 'callback_priorities' => $priorities];
}

function callable_program_priority_active_pair(string $name, $currentPriority, $newPriority): array
{
    $hook = 'callable-coercion-priority-active-' . $name;
    $trace = [];
    $mutated = false;
    $late = static function ($value) use (&$trace) {
        $trace[] = 'late';
        return $value;
    };
    $trigger = static function ($value) use (&$trace, &$mutated, $hook, $newPriority, $late) {
        $trace[] = 'trigger';
        if (!$mutated) {
            $mutated = true;
            add_filter($hook, $late, $newPriority, 1);
        }
        return $value;
    };
    $tail = static function ($value) use (&$trace) {
        $trace[] = 'tail';
        return $value;
    };
    add_filter($hook, $trigger, $currentPriority, 1);
    add_filter($hook, $tail, 9007199254740999, 1);
    $value = apply_filters($hook, 'seed');

    return [
        'value' => $value,
        'trace' => $trace,
        'trigger_priority' => callable_program_scalar_view(has_filter($hook, $trigger)),
        'late_priority' => callable_program_scalar_view(has_filter($hook, $late)),
        'tail_priority' => callable_program_scalar_view(has_filter($hook, $tail)),
    ];
}

function callable_program_priority_numeric_matrix(): array
{
    $pairs = [
        'int_2p53_reverse' => [
            ['label' => 'plus_one', 'priority' => 9007199254740993],
            ['label' => 'base', 'priority' => 9007199254740992],
        ],
        'int_2p53_forward' => [
            ['label' => 'base', 'priority' => 9007199254740992],
            ['label' => 'plus_one', 'priority' => 9007199254740993],
        ],
        'string_plus_large_reverse' => [
            ['label' => 'int_plus_one', 'priority' => 9007199254740993],
            ['label' => 'plus_string', 'priority' => '+9007199254740993'],
        ],
        'string_plus_large_forward' => [
            ['label' => 'plus_string', 'priority' => '+9007199254740993'],
            ['label' => 'int_plus_one', 'priority' => 9007199254740993],
        ],
        'string_zero_large_reverse' => [
            ['label' => 'int_plus_one', 'priority' => 9007199254740993],
            ['label' => 'zero_string', 'priority' => '09007199254740993'],
        ],
        'string_zero_large_forward' => [
            ['label' => 'zero_string', 'priority' => '09007199254740993'],
            ['label' => 'int_plus_one', 'priority' => 9007199254740993],
        ],
        'string_plus_base_reverse' => [
            ['label' => 'int_plus_one', 'priority' => 9007199254740993],
            ['label' => 'plus_base', 'priority' => '+9007199254740992'],
        ],
        'string_plus_base_forward' => [
            ['label' => 'plus_base', 'priority' => '+9007199254740992'],
            ['label' => 'int_plus_one', 'priority' => 9007199254740993],
        ],
        'string_fraction_large_reverse' => [
            ['label' => 'int_plus_one', 'priority' => 9007199254740993],
            ['label' => 'fraction_string', 'priority' => '9007199254740992.0'],
        ],
        'string_fraction_large_forward' => [
            ['label' => 'fraction_string', 'priority' => '9007199254740992.0'],
            ['label' => 'int_plus_one', 'priority' => 9007199254740993],
        ],
        'string_prefix_10_reverse' => [
            ['label' => 'int10', 'priority' => 10],
            ['label' => 'prefix12', 'priority' => '12foo'],
        ],
        'string_prefix_10_forward' => [
            ['label' => 'prefix12', 'priority' => '12foo'],
            ['label' => 'int10', 'priority' => 10],
        ],
        'string_prefix_20_reverse' => [
            ['label' => 'int20', 'priority' => 20],
            ['label' => 'prefix12', 'priority' => '12foo'],
        ],
        'string_prefix_20_forward' => [
            ['label' => 'prefix12', 'priority' => '12foo'],
            ['label' => 'int20', 'priority' => 20],
        ],
        'string_inf_zero_reverse' => [
            ['label' => 'int0', 'priority' => 0],
            ['label' => 'string_inf', 'priority' => 'INF'],
        ],
        'string_inf_zero_forward' => [
            ['label' => 'string_inf', 'priority' => 'INF'],
            ['label' => 'int0', 'priority' => 0],
        ],
        'string_nan_zero_reverse' => [
            ['label' => 'int0', 'priority' => 0],
            ['label' => 'string_nan', 'priority' => 'NAN'],
        ],
        'string_nan_zero_forward' => [
            ['label' => 'string_nan', 'priority' => 'NAN'],
            ['label' => 'int0', 'priority' => 0],
        ],
        'abc_zero_reverse' => [
            ['label' => 'zero', 'priority' => 0],
            ['label' => 'abc', 'priority' => 'abc'],
        ],
        'abc_zero_forward' => [
            ['label' => 'abc', 'priority' => 'abc'],
            ['label' => 'zero', 'priority' => 0],
        ],
        '07_7_reverse' => [
            ['label' => 'int7', 'priority' => 7],
            ['label' => 'string07', 'priority' => '07'],
        ],
        '07_7_forward' => [
            ['label' => 'string07', 'priority' => '07'],
            ['label' => 'int7', 'priority' => 7],
        ],
        'exp100_100_reverse' => [
            ['label' => 'int100', 'priority' => 100],
            ['label' => 'exp100', 'priority' => '1e2'],
        ],
        'exp100_100_forward' => [
            ['label' => 'exp100', 'priority' => '1e2'],
            ['label' => 'int100', 'priority' => 100],
        ],
    ];
    $result = ['pairs' => [], 'active_add' => []];
    foreach ($pairs as $name => $entries) {
        $result['pairs'][$name] = callable_program_priority_pair_case($name, $entries);
    }
    foreach ([
        'int_2p53_to_plus1' => [9007199254740992, 9007199254740993],
        'plus1_to_int_2p53' => [9007199254740993, 9007199254740992],
        'int_plus1_to_string_plus_large' => [9007199254740992, '+9007199254740993'],
        'string_plus_large_to_int_base' => ['+9007199254740993', 9007199254740992],
        'int_plus1_to_string_zero_large' => [9007199254740992, '09007199254740993'],
        'string_zero_large_to_int_base' => ['09007199254740993', 9007199254740992],
        'int_plus1_to_string_plus_base' => [9007199254740993, '+9007199254740992'],
        'string_plus_base_to_int_plus1' => ['+9007199254740992', 9007199254740993],
        'current_string_prefix_add_int12' => ['12foo', 12],
        'current_int12_add_string_prefix' => [12, '12foo'],
        'current_string_inf_add_int0' => ['INF', 0],
        'current_int0_add_string_inf' => [0, 'INF'],
        'current_abc_add_zero' => ['abc', 0],
        'current_zero_add_abc' => [0, 'abc'],
        'current_07_add_7' => ['07', 7],
        'current_7_add_07' => [7, '07'],
        'current_exp_add_100' => ['1e2', 100],
        'current_100_add_exp' => [100, '1e2'],
    ] as $name => [$current, $new]) {
        $result['active_add'][$name] = callable_program_priority_active_pair($name, $current, $new);
    }

    return $result;
}

function callable_program_capture_warnings(callable $call): array
{
    $warnings = [];
    set_error_handler(static function (int $severity, string $message) use (&$warnings): bool {
        $warnings[] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
        return true;
    });
    try {
        $value = $call();
    } finally {
        restore_error_handler();
    }

    return ['value' => $value, 'warnings' => $warnings];
}

function callable_program_active_mutation_callback(string $label, string $hook, array &$trace, ?Closure $mutator = null): Closure
{
    return static function ($value) use ($label, $hook, &$trace, $mutator) {
        $trace[] = $label;
        if ($mutator !== null) {
            $mutator();
        }
        return $value . ':' . $label;
    };
}

function callable_program_current_bucket_readd(string $name, int $basePriority, $rawPriority): array
{
    $hook = 'callable-coercion-readd-' . $name;
    $trace = [];
    $trigger = null;
    $replacement = callable_program_active_mutation_callback('replacement', $hook, $trace);
    $trigger = static function ($value) use (&$trace, $hook, &$trigger, $replacement, $rawPriority) {
        $trace[] = 'trigger';
        $removed = remove_filter($hook, $trigger, $rawPriority);
        $added = add_filter($hook, $replacement, $rawPriority, 1);
        $trace[] = ['removed' => $removed, 'readded' => $added];
        return $value . ':trigger';
    };
    $lower = callable_program_active_mutation_callback('lower', $hook, $trace);
    $tail = callable_program_active_mutation_callback('tail', $hook, $trace);
    add_filter($hook, $lower, 0, 1);
    add_filter($hook, $trigger, $basePriority, 1);
    add_filter($hook, $tail, 10, 1);
    $value = apply_filters($hook, 'seed');

    return ['value' => $value, 'trace' => $trace];
}

function callable_program_non_numeric_active_add(): array
{
    $hook = 'callable-coercion-active-abc';
    $trace = [];
    $late = callable_program_active_mutation_callback('late_abc', $hook, $trace);
    $trigger = callable_program_active_mutation_callback(
        'trigger',
        $hook,
        $trace,
        static function () use ($hook, &$trace, $late): void {
            add_filter($hook, $late, 'abc', 1);
        },
    );
    $tail = callable_program_active_mutation_callback('tail', $hook, $trace);
    add_filter($hook, $trigger, 0, 1);
    add_filter($hook, $tail, 10, 1);
    $value = apply_filters($hook, 'seed');

    return ['value' => $value, 'trace' => $trace];
}

function callable_program_coercion_reentry_added($value)
{
    $GLOBALS['callable_coercion_pin_trace'][] = 'rebound';
    return $value . ':rebound';
}

function callable_program_coercion_reentry_outer($value)
{
    $GLOBALS['callable_coercion_pin_trace'][] = 'outer';
    return $value . ':outer';
}

function callable_program_coercion_reentry_sentinel($value)
{
    $GLOBALS['callable_coercion_pin_trace'][] = 'sentinel';
    return $value . ':sentinel';
}

function callable_program_coercion_throw_added($value)
{
    $GLOBALS['callable_coercion_throw_trace'][] = 'nested';
    return $value . ':nested';
}

function callable_program_coercion_throw_outer($value)
{
    $GLOBALS['callable_coercion_throw_trace'][] = 'outer';
    return $value . ':outer';
}

function callable_program_all_boundary_reentry(): array
{
    $baseAllRemoved = remove_filter('all', 'callable_program_all_listener', 1);
    $emptyAllHook = 101000.5;
    $emptyAllAdded = add_filter('all', null);
    $emptyApply = callable_program_capture_warnings(static fn () => apply_filters($emptyAllHook, 'seed'));
    $emptyCurrentAfter = callable_program_scalar_view(current_filter());
    $emptyDoingAfter = doing_filter();
    $emptyAllRemoved = remove_filter('all', null);

    $hook = 101001.5;
    $callbackObservation = [];
    $callback = static function ($value) use (&$callbackObservation, $hook) {
        $callbackObservation = [
            'current_filter' => callable_program_hook_scalar_view(current_filter()),
            'doing_any' => doing_filter(),
            'doing_exact' => doing_filter($hook),
        ];

        return $value . ':all-boundary-reentry';
    };
    $namedAdded = callable_program_capture_warnings(
        static fn () => add_filter($hook, $callback, 10, 1),
    );
    $warnings = [];
    $injection = ['warning_count_at_injection' => null];
    $injected = false;
    set_error_handler(static function (int $severity, string $message) use (&$warnings, &$injection, &$injected, $hook): bool {
        $warnings[] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
        if (!$injected && count($warnings) === 4) {
            $injected = true;
            $injection = [
                'warning_count_at_injection' => count($warnings),
                'current_filter_before_add' => callable_program_hook_scalar_view(current_filter()),
                'doing_before_add' => doing_filter(),
                'doing_exact_before_add' => doing_filter($hook),
                'all_added' => add_filter('all', null),
                'current_filter_after_add' => callable_program_hook_scalar_view(current_filter()),
                'doing_after_add' => doing_filter(),
                'doing_exact_after_add' => doing_filter($hook),
            ];
        }

        return true;
    });
    $applyThrowable = null;
    try {
        $applyValue = apply_filters($hook, 'seed');
    } catch (Throwable $error) {
        $applyValue = null;
        $applyThrowable = [
            'class_hex' => bin2hex($error::class),
            'message_hex' => bin2hex($error->getMessage()),
        ];
    } finally {
        restore_error_handler();
    }
    $currentAfter = callable_program_scalar_view(current_filter());
    $doingAfter = doing_filter();
    $allRemoved = remove_filter('all', null);
    $namedRemoved = callable_program_capture_warnings(
        static fn () => remove_filter($hook, $callback, 10),
    );
    $currentAfterCleanup = callable_program_hook_scalar_view(current_filter());
    $doingAfterCleanup = doing_filter();

    return [
        'base_all_listener_removed' => $baseAllRemoved,
        'empty_all_without_named_callback' => [
            'all_added' => $emptyAllAdded,
            'apply' => $emptyApply,
            'current_filter_after' => $emptyCurrentAfter,
            'doing_filter_after' => $emptyDoingAfter,
            'all_remove_result' => $emptyAllRemoved,
        ],
        'all_added_during_named_read_warning' => [
            'named_add' => $namedAdded,
            'apply_value' => $applyValue,
            'apply_throwable' => $applyThrowable,
            'warnings' => $warnings,
            'injected' => $injection,
            'callback' => $callbackObservation,
            'current_filter_after' => $currentAfter,
            'doing_filter_after' => $doingAfter,
            'all_remove_result' => $allRemoved,
            'named_remove' => $namedRemoved,
            'current_filter_after_cleanup' => $currentAfterCleanup,
            'doing_filter_after_cleanup' => $doingAfterCleanup,
        ],
    ];
}

function callable_program_counter_cow_leaf(string $group, string $phase, string $mode, float $hook, int $warningIndex): array
{
    $otherHook = 'callable-counter-cow-other-' . $group . '-' . $phase . '-' . $mode;
    $setup = ['value' => null, 'warnings' => []];
    if ($group !== 'missing_counter') {
        $setup = callable_program_capture_warnings(static fn () => apply_filters($hook, 'seed:first'));
    }

    $warnings = [];
    $injectedAt = null;
    $readOnlyOtherCount = null;
    $nestedApplyValue = null;
    $nestedDidFilter = null;
    $operationValue = null;
    set_error_handler(static function (int $severity, string $message) use (
        &$warnings,
        &$injectedAt,
        &$readOnlyOtherCount,
        &$nestedApplyValue,
        &$nestedDidFilter,
        $group,
        $phase,
        $mode,
        $warningIndex,
        $otherHook,
    ): bool {
        $warnings[] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
        if ($injectedAt === null && count($warnings) === $warningIndex) {
            $injectedAt = ['warning_index' => count($warnings)];
            if ($mode === 'nested_write') {
                $nestedApplyValue = apply_filters($otherHook, 'child');
                $nestedDidFilter = did_filter($otherHook);
            } else {
                $readOnlyOtherCount = did_filter($otherHook);
            }
        }

        return true;
    });
    try {
        if ($group === 'did_filter') {
            $operationValue = did_filter($hook);
        } else {
            $operationInput = $group === 'existing_counter' ? 'seed:second' : 'seed:missing';
            $operationValue = apply_filters($hook, $operationInput);
        }
    } finally {
        restore_error_handler();
    }

    $after = callable_program_capture_warnings(static fn () => did_filter($hook));
    $otherAfter = did_filter($otherHook);

    return [
        'hook_bits' => bin2hex(pack('E', $hook)),
        'setup' => $setup,
        'operation' => [
            'value' => $operationValue,
            'warnings' => $warnings,
            'injected_at' => $injectedAt,
            'read_only_other_did_filter' => $readOnlyOtherCount,
            'nested_apply_value' => $nestedApplyValue,
            'nested_did_filter' => $nestedDidFilter,
        ],
        'after' => [
            'did_filter' => $after['value'],
            'warnings' => $after['warnings'],
            'other_did_filter' => $otherAfter,
        ],
    ];
}

function callable_program_counter_cow_nested_read_leaf(string $mode, float $hook): array
{
    $otherHook = 'callable-counter-cow-owner-other-' . $mode;
    $setup = callable_program_capture_warnings(static fn () => apply_filters($hook, 'seed:first'));

    $outerWarnings = [];
    $middleWarnings = [];
    $innerWarnings = [];
    $outerInjectedAt = null;
    $middleInjectedAt = null;
    $innerInjectedAt = null;
    $outerInjected = false;
    $middleInjected = false;
    $innerInjected = false;
    $middleDidFilter = null;
    $middleApplyValue = null;
    $readOnlyOtherCount = null;
    $innerApplyValue = null;
    $innerDidFilter = null;
    $outerValue = null;
    set_error_handler(static function (int $severity, string $message) use (
        &$outerWarnings,
        &$middleWarnings,
        &$innerWarnings,
        &$outerInjectedAt,
        &$middleInjectedAt,
        &$innerInjectedAt,
        &$outerInjected,
        &$middleInjected,
        &$innerInjected,
        &$middleDidFilter,
        &$middleApplyValue,
        &$readOnlyOtherCount,
        &$innerApplyValue,
        &$innerDidFilter,
        $hook,
        $otherHook,
        $mode,
    ): bool {
        $outerWarnings[] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
        if (!$outerInjected && count($outerWarnings) === 2) {
            $outerInjected = true;
            $outerInjectedAt = ['warning_index' => count($outerWarnings)];
            set_error_handler(static function (int $middleSeverity, string $middleMessage) use (
                &$middleWarnings,
                &$middleInjectedAt,
                &$middleInjected,
                &$middleDidFilter,
                &$middleApplyValue,
                &$innerWarnings,
                &$innerInjectedAt,
                &$innerInjected,
                &$readOnlyOtherCount,
                &$innerApplyValue,
                &$innerDidFilter,
                $hook,
                $otherHook,
                $mode,
            ): bool {
                $middleWarnings[] = ['severity' => $middleSeverity, 'message_hex' => bin2hex($middleMessage)];
                if (!$middleInjected && count($middleWarnings) === 2) {
                    $middleInjected = true;
                    $middleInjectedAt = ['warning_index' => count($middleWarnings)];
                    set_error_handler(static function (int $innerSeverity, string $innerMessage) use (
                        &$innerWarnings,
                        &$innerInjectedAt,
                        &$innerInjected,
                        &$readOnlyOtherCount,
                        &$innerApplyValue,
                        &$innerDidFilter,
                        $otherHook,
                        $mode,
                    ): bool {
                        $innerWarnings[] = ['severity' => $innerSeverity, 'message_hex' => bin2hex($innerMessage)];
                        if (!$innerInjected && count($innerWarnings) === 2) {
                            $innerInjected = true;
                            $innerInjectedAt = ['warning_index' => count($innerWarnings)];
                            if ($mode === 'nested_write') {
                                $innerApplyValue = apply_filters($otherHook, 'inner');
                                $innerDidFilter = did_filter($otherHook);
                            } else {
                                $readOnlyOtherCount = did_filter($otherHook);
                            }
                        }

                        return true;
                    });
                    try {
                        $middleApplyValue = apply_filters($hook, 'seed:middle');
                    } finally {
                        restore_error_handler();
                    }
                }

                return true;
            });
            try {
                $middleDidFilter = did_filter($hook);
            } finally {
                restore_error_handler();
            }
        }

        return true;
    });
    try {
        $outerValue = did_filter($hook);
    } finally {
        restore_error_handler();
    }

    $after = callable_program_capture_warnings(static fn () => did_filter($hook));
    $otherAfter = did_filter($otherHook);

    return [
        'hook_bits' => bin2hex(pack('E', $hook)),
        'setup' => $setup,
        'operation' => [
            'value' => $outerValue,
            'warnings' => $outerWarnings,
            'injected_at' => $outerInjectedAt,
            'middle_did_filter' => $middleDidFilter,
            'float_apply_value' => $middleApplyValue,
            'middle_warnings' => $middleWarnings,
            'middle_injected_at' => $middleInjectedAt,
            'nested_string_apply_value' => $innerApplyValue,
            'inner_did_filter' => $innerDidFilter,
            'inner_warnings' => $innerWarnings,
            'inner_injected_at' => $innerInjectedAt,
            'read_only_other_did_filter' => $readOnlyOtherCount,
        ],
        'after' => [
            'did_filter' => $after['value'],
            'warnings' => $after['warnings'],
            'other_did_filter' => $otherAfter,
        ],
    ];
}

function callable_program_counter_cow_cases(): array
{
    $phases = [
        'existing_counter' => [
            'counter_isset' => 1,
            'increment' => 2,
            'later_hook_lookup' => 3,
        ],
        'missing_counter' => [
            'counter_isset' => 1,
            'counter_write' => 2,
            'later_hook_lookup' => 3,
        ],
        'did_filter' => [
            'counter_isset' => 1,
            'counter_read' => 2,
        ],
    ];
    $result = [];
    $caseIndex = 0;
    foreach ($phases as $group => $groupPhases) {
        foreach ($groupPhases as $phase => $warningIndex) {
            foreach (['control', 'nested_write'] as $mode) {
                $hook = 102000.5 + $caseIndex;
                $caseIndex++;
                $result[$group][$phase][$mode] = callable_program_counter_cow_leaf(
                    $group,
                    $phase,
                    $mode,
                    $hook,
                    $warningIndex,
                );
            }
        }
    }

    return $result;
}
function callable_program_coercion_cases(): array
{
    $result = [];
    $runtimeHook = 'callable-coercion-runtime-width';
    add_filter($runtimeHook, static fn ($value) => $value, 10, 1);
    $runtimeMin = apply_filters($runtimeHook, PHP_INT_MIN);
    $runtimeMax = apply_filters($runtimeHook, PHP_INT_MAX);
    $result['runtime_integer_width'] = [
        'php_version' => PHP_VERSION,
        'php_int_size' => PHP_INT_SIZE,
        'php_int_min' => (string) PHP_INT_MIN,
        'php_int_max' => (string) PHP_INT_MAX,
        'apply_filter_min_same_integer' => is_int($runtimeMin) && $runtimeMin === PHP_INT_MIN,
        'apply_filter_max_same_integer' => is_int($runtimeMax) && $runtimeMax === PHP_INT_MAX,
        'apply_filter_min_type' => get_debug_type($runtimeMin),
        'apply_filter_max_type' => get_debug_type($runtimeMax),
    ];

    $GLOBALS['callable_coercion_all_names'] = ['5', 5, '05', true, 1, null, '', 5.7, false, 0, 0.0, -0.0, NAN, INF];
    $GLOBALS['callable_coercion_all_trace'] = [];
    $all = static function (...$arguments): void {
        $hook = $arguments[0] ?? null;
        foreach ($GLOBALS['callable_coercion_all_names'] as $expected) {
            if (callable_program_hook_strict_match($hook, $expected)) {
                $GLOBALS['callable_coercion_all_trace'][] = callable_program_hook_scalar_view($hook);
                break;
            }
        }
    };
    add_filter('all', $all, 10, 99);

    $hookTrace = [];
    $hooks = [
        ['label' => 'numeric_string', 'value' => '5'],
        ['label' => 'integer_alias', 'value' => 5],
        ['label' => 'leading_zero_string', 'value' => '05'],
        ['label' => 'boolean_key', 'value' => true],
        ['label' => 'integer_boolean_alias', 'value' => 1],
        ['label' => 'null_key', 'value' => null],
        ['label' => 'empty_string_alias', 'value' => ''],
        ['label' => 'float_key', 'value' => 5.7],
    ];
    $hookCallbacks = [];
    foreach ($hooks as $entry) {
        $label = $entry['label'];
        $hookValue = $entry['value'];
        $callback = static function ($value) use ($label, $hookValue, &$hookTrace) {
            $current = current_filter();
            $hookTrace[] = [
                'callback' => $label,
                'current_filter' => callable_program_hook_scalar_view($current),
                'doing_any' => doing_filter(),
                'doing_exact' => doing_filter($hookValue),
                'did_filter' => did_filter($hookValue),
            ];
            return $value . ':' . $label;
        };
        $hookCallbacks[$label] = $callback;
        $warnings = [];
        set_error_handler(static function (int $severity, string $message) use (&$warnings): bool {
            $warnings[] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
            return true;
        });
        try {
            $added = add_filter($hookValue, $callback, 10, 1);
        } finally {
            restore_error_handler();
        }
        $result['hook_add_' . $label] = ['added' => $added, 'warnings' => $warnings];
    }

    $hookRuns = [];
    foreach ($hooks as $entry) {
        $hookValue = $entry['value'];
        $warnings = [];
        set_error_handler(static function (int $severity, string $message) use (&$warnings): bool {
            $warnings[] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
            return true;
        });
        try {
            $value = apply_filters($hookValue, 'seed');
        } finally {
            restore_error_handler();
        }
        $didFilter = callable_program_capture_warnings(static fn () => did_filter($hookValue));
        $hookRuns[] = [
            'label' => $entry['label'],
            'value' => $value,
            'did_filter' => $didFilter['value'],
            'did_filter_warnings' => $didFilter['warnings'],
            'warnings' => $warnings,
        ];
    }
    $result['hook_key_coercions'] = [
        'runs' => $hookRuns,
        'callback_trace' => $hookTrace,
        'all_raw_names' => $GLOBALS['callable_coercion_all_trace'],
        'aliases' => [
            'numeric_string_to_int' => has_filter('5', $hookCallbacks['numeric_string']) === 10,
            'leading_zero_distinct' => has_filter('05', $hookCallbacks['numeric_string']) === false,
            'bool_to_int' => has_filter(true, $hookCallbacks['boolean_key']) === 10,
            'null_to_empty_string' => has_filter('', $hookCallbacks['null_key']) === 10,
        ],
        'current_filter_after' => callable_program_scalar_view(current_filter()),
        'doing_filter_after' => doing_filter(),
    ];

    $specialHookTrace = [];
    $specialHookCallbacks = [];
    $specialHooks = [
        ['label' => 'false', 'value' => false],
        ['label' => 'int_zero', 'value' => 0],
        ['label' => 'positive_float_zero', 'value' => 0.0],
        ['label' => 'negative_float_zero', 'value' => -0.0],
        ['label' => 'nan', 'value' => NAN],
        ['label' => 'infinity', 'value' => INF],
    ];
    foreach ($specialHooks as $entry) {
        $label = $entry['label'];
        $hookValue = $entry['value'];
        $callback = static function ($value) use ($label, $hookValue, &$specialHookTrace) {
            $current = current_filter();
            $specialHookTrace[] = [
                'callback' => $label,
                'current_filter' => callable_program_hook_scalar_view($current),
                'doing_any' => doing_filter(),
                'doing_exact_original' => doing_filter($hookValue),
                'doing_false' => doing_filter(false),
                'doing_int_zero' => doing_filter(0),
                'doing_positive_float_zero' => doing_filter(0.0),
                'doing_negative_float_zero' => doing_filter(-0.0),
                'doing_nan' => doing_filter(NAN),
                'doing_infinity' => doing_filter(INF),
            ];
            return $value;
        };
        $specialHookCallbacks[$label] = $callback;
        $warnings = [];
        set_error_handler(static function (int $severity, string $message) use (&$warnings): bool {
            $warnings[] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
            return true;
        });
        try {
            $added = add_filter($hookValue, $callback, 10, 1);
        } finally {
            restore_error_handler();
        }
        $result['special_float_hooks']['add_' . $label] = ['added' => $added, 'warnings' => $warnings];
    }
    $specialHookRuns = [];
    foreach ($specialHooks as $entry) {
        $hookValue = $entry['value'];
        $run = callable_program_capture_warnings(static fn () => apply_filters($hookValue, 'seed'));
        $specialHookRuns[] = [
            'label' => $entry['label'],
            'hook' => callable_program_hook_scalar_view($hookValue),
            'value' => $run['value'],
            'warnings' => $run['warnings'],
        ];
    }
    $result['special_float_hooks']['runs'] = $specialHookRuns;
    $result['special_float_hooks']['callback_trace'] = $specialHookTrace;
    $result['special_float_hooks']['all_raw_names'] = array_slice($GLOBALS['callable_coercion_all_trace'], count($hooks));
    foreach ($specialHooks as $entry) {
        $result['special_float_hooks']['remove_' . $entry['label']] = callable_program_capture_warnings(
            static fn () => remove_filter($entry['value'], $specialHookCallbacks[$entry['label']], 10),
        );
    }
    remove_filter('all', $all, 10);

    $priorityTrace = [];
    $GLOBALS['callable_coercion_priority_trace'] = [];
    $priorityHook = 'callable-coercion-priority-order';
    $priorityCallbacks = [
        'string_07' => callable_program_priority_callback('string_07'),
        'int_7' => callable_program_priority_callback('int_7'),
        'exponent_100' => callable_program_priority_callback('exponent_100'),
        'int_100' => callable_program_priority_callback('int_100'),
        'big_2p53' => callable_program_priority_callback('big_2p53'),
        'big_2p53_plus1' => callable_program_priority_callback('big_2p53_plus1'),
        'overflow_decimal' => callable_program_priority_callback('overflow_decimal'),
        'overflow_exponent' => callable_program_priority_callback('overflow_exponent'),
        'ordinary_10' => callable_program_priority_callback('ordinary_10'),
    ];
    foreach ([
        ['string_07', '07'],
        ['int_7', 7],
        ['exponent_100', '1e2'],
        ['int_100', 100],
        ['big_2p53', 9007199254740992],
        ['big_2p53_plus1', 9007199254740993],
        ['overflow_exponent', '1e309'],
        ['overflow_decimal', '9223372036854775808'],
        ['ordinary_10', 10],
    ] as [$name, $priority]) {
        add_filter($priorityHook, $priorityCallbacks[$name], $priority, 1);
    }
    $priorityValue = apply_filters($priorityHook, 'seed');
    $priorityHasString = has_filter($priorityHook, $priorityCallbacks['string_07'], '07');
    $priorityHasInt = has_filter($priorityHook, $priorityCallbacks['int_7'], 7);
    $priorityRemoved = remove_filter($priorityHook, $priorityCallbacks['int_7'], '7');
    $result['priority_key_coercions'] = [
        'value' => $priorityValue,
        'has_string_priority' => callable_program_scalar_view($priorityHasString),
        'has_int_priority' => $priorityHasInt,
        'removed_by_numeric_alias' => $priorityRemoved,
        'int_priority_after_remove' => has_filter($priorityHook, $priorityCallbacks['int_7']),
        'trace' => $GLOBALS['callable_coercion_priority_trace'] ?? $priorityTrace,
    ];
    $result['active_priority_mutations'] = [
        'current_int7_readd_int' => callable_program_capture_warnings(static fn () => callable_program_current_bucket_readd('int7', 7, 7)),
        'current_int7_readd_numeric_string' => callable_program_capture_warnings(static fn () => callable_program_current_bucket_readd('string7', 7, '7')),
        'current_int7_readd_float' => callable_program_capture_warnings(static fn () => callable_program_current_bucket_readd('float79', 7, 7.9)),
        'current_int1_readd_bool' => callable_program_capture_warnings(static fn () => callable_program_current_bucket_readd('booltrue', 1, true)),
        'current_int1_readd_int' => callable_program_capture_warnings(static fn () => callable_program_current_bucket_readd('int1', 1, 1)),
        'non_numeric_string_added_at_int_zero' => callable_program_capture_warnings(callable_program_non_numeric_active_add(...)),
    ];
    $result['priority_numeric_sort_matrix'] = callable_program_priority_numeric_matrix();

    $nullHook = 'callable-coercion-null-id';
    $nullIdResult = add_filter($nullHook, null, new stdClass(), new stdClass());
    $result['null_id_short_circuit'] = [
        'added' => $nullIdResult,
        'any_after_add' => has_filter($nullHook),
        'remove_missing' => remove_filter($nullHook, null),
    ];

    $invalid = [];
    foreach ([
        'invalid_array_hook' => static fn () => add_filter([], null, [], []),
        'invalid_object_hook' => static fn () => add_filter(new stdClass(), null, [], []),
        'invalid_array_has_hook' => static fn () => has_filter([], false),
        'invalid_object_remove_hook' => static fn () => remove_filter(new stdClass(), null, []),
        'invalid_array_apply_hook' => static fn () => apply_filters([], 'seed'),
        'invalid_object_did_hook' => static fn () => did_filter(new stdClass()),
        'invalid_array_priority' => static fn () => add_filter('callable-coercion-invalid-priority-array', 'callable_missing_coercion_array', [], []),
        'invalid_object_priority' => static fn () => add_filter('callable-coercion-invalid-priority-object', 'callable_missing_coercion_object', new stdClass(), new stdClass()),
    ] as $name => $call) {
        try {
            $invalid[$name] = ['returned' => callable_program_scalar_view($call())];
        } catch (Throwable $error) {
            $invalid[$name] = ['throwable' => ['class_hex' => bin2hex($error::class), 'message_hex' => bin2hex($error->getMessage())]];
        }
    }
    $doingArray = doing_filter([]);
    $priorityProbe = 'callable-coercion-nonint-priority-search';
    add_filter($priorityProbe, 'callable_program_function', 7, 1);
    $hasArrayPriority = has_filter($priorityProbe, 'callable_program_function', []);
    try {
        $invalid['remove_existing_array_priority'] = [
            'returned' => callable_program_scalar_view(remove_filter($priorityProbe, 'callable_program_function', [])),
        ];
    } catch (Throwable $error) {
        $invalid['remove_existing_array_priority'] = [
            'throwable' => ['class_hex' => bin2hex($error::class), 'message_hex' => bin2hex($error->getMessage())],
        ];
    }
    $result['invalid_hook_and_priority_types'] = $invalid;
    $result['ignored_nonscalar_priority_and_hook'] = [
        'has_filter_array_priority' => callable_program_scalar_view($hasArrayPriority),
        'doing_filter_array_hook' => $doingArray,
    ];

    $GLOBALS['callable_coercion_pin_trace'] = [];
    $pinHook = 'callable-coercion-receiver-pin';
    add_filter($pinHook, 'callable_program_coercion_reentry_sentinel', 10, 1);
    $pinWarnings = [];
    $pinReentry = [];
    $pinTriggered = false;
    set_error_handler(static function (int $severity, string $message) use (&$pinWarnings, &$pinReentry, &$pinTriggered, $pinHook): bool {
        $pinWarnings[] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
        if (!$pinTriggered) {
            $pinTriggered = true;
            $pinReentry['sentinel_removed'] = remove_filter($pinHook, 'callable_program_coercion_reentry_sentinel', 10);
            $pinReentry['replacement_added'] = add_filter($pinHook, 'callable_program_coercion_reentry_added', 5, 1);
            $pinReentry['replacement_priority'] = has_filter($pinHook, 'callable_program_coercion_reentry_added');
        }
        return true;
    });
    try {
        $pinOuterAdded = add_filter($pinHook, 'callable_program_coercion_reentry_outer', 5.7, 1);
    } finally {
        restore_error_handler();
    }
    $pinValue = apply_filters($pinHook, 'seed');
    $result['priority_warning_rebind'] = [
        'outer_added' => $pinOuterAdded,
        'warnings' => $pinWarnings,
        'reentry' => $pinReentry,
        'outer_visible_after' => has_filter($pinHook, 'callable_program_coercion_reentry_outer'),
        'replacement_priority_after' => has_filter($pinHook, 'callable_program_coercion_reentry_added'),
        'value' => $pinValue,
        'trace' => $GLOBALS['callable_coercion_pin_trace'],
    ];

    $GLOBALS['callable_coercion_throw_trace'] = [];
    $throwHook = 'callable-coercion-accepted-args-throw';
    $throwWarnings = [];
    $throwReentry = [];
    set_error_handler(static function (int $severity, string $message) use (&$throwWarnings, &$throwReentry, $throwHook): bool {
        $throwWarnings[] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
        $throwReentry['outer_visible_before'] = has_filter($throwHook, 'callable_program_coercion_throw_outer');
        $throwReentry['nested_added'] = add_filter($throwHook, 'callable_program_coercion_throw_added', 5, 1);
        throw new RuntimeException('callable accepted_args warning handler threw');
    });
    try {
        $throwAdd = add_filter($throwHook, 'callable_program_coercion_throw_outer', 5, new stdClass());
        $throwOutcome = ['returned' => callable_program_scalar_view($throwAdd)];
    } catch (Throwable $error) {
        $throwOutcome = ['throwable' => ['class_hex' => bin2hex($error::class), 'message_hex' => bin2hex($error->getMessage())]];
    } finally {
        restore_error_handler();
    }
    $throwValue = apply_filters($throwHook, 'seed');
    $result['accepted_args_warning_throw'] = [
        'warnings' => $throwWarnings,
        'reentry' => $throwReentry,
        'outcome' => $throwOutcome,
        'outer_visible_after' => has_filter($throwHook, 'callable_program_coercion_throw_outer'),
        'nested_priority_after' => has_filter($throwHook, 'callable_program_coercion_throw_added'),
        'value' => $throwValue,
        'trace' => $GLOBALS['callable_coercion_throw_trace'],
    ];

    return $result;
}

function callable_program_priority_float_leaf(string $case): array
{
    $hook = 'callable-priority-cow-float-' . $case;
    $state = [
        'trace' => [],
        'warnings' => [],
        'injection' => null,
        'outer_attempted' => false,
        'outer_add_result' => null,
        'outer_throwable' => null,
    ];
    $nested = static function (string $value) use (&$state): string {
        $state['trace'][] = 'nested';
        return $value . ':nested';
    };
    $victim = static function (string $value) use (&$state): string {
        $state['trace'][] = 'victim';
        return $value . ':victim';
    };
    $outer = static function (string $value) use (&$state): string {
        $state['trace'][] = 'outer';
        return $value . ':outer';
    };
    $anchor = static function (string $value) use (&$state): string {
        $state['trace'][] = 'anchor';
        return $value . ':anchor';
    };
    $warningTarget = match ($case) {
        'isset_add', 'isset_remove_bucket' => 1,
        'write_add', 'write_remove_bucket' => 2,
        default => 0,
    };
    $mutation = str_ends_with($case, '_add') ? 'add' :
        (str_ends_with($case, '_remove_bucket') ? 'remove_bucket' : 'control');
    $seed = null;
    $seed = static function (string $value) use (
        &$state,
        $hook,
        $nested,
        $victim,
        $outer,
        &$seed,
        $warningTarget,
        $mutation,
    ): string {
        $state['trace'][] = 'seed';
        if (!$state['outer_attempted']) {
            $state['outer_attempted'] = true;
            $warningIndex = 0;
            set_error_handler(static function (int $severity, string $message) use (
                &$state,
                &$warningIndex,
                $hook,
                $nested,
                $victim,
                &$seed,
                $warningTarget,
                $mutation,
            ): bool {
                $warningIndex++;
                $state['warnings'][] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
                if ($warningTarget > 0 && $warningIndex === $warningTarget && $state['injection'] === null) {
                    $state['injection'] = ['warning_index' => $warningIndex];
                    if ($mutation === 'add') {
                        $state['injection']['add_result'] = add_filter($hook, $nested, 7, 1);
                        $state['injection']['nested_priority_after_add'] = has_filter($hook, $nested);
                    } elseif ($mutation === 'remove_bucket') {
                        $state['injection']['remove_seed_result'] = remove_filter($hook, $seed, 7);
                        $state['injection']['remove_victim_result'] = remove_filter($hook, $victim, 7);
                        $state['injection']['seed_priority_after_remove'] = has_filter($hook, $seed);
                        $state['injection']['victim_priority_after_remove'] = has_filter($hook, $victim);
                    }
                }

                return true;
            });
            try {
                $state['outer_add_result'] = add_filter($hook, $outer, 7.5, 1);
            } catch (Throwable $throwable) {
                $state['outer_throwable'] = [
                    'class_hex' => bin2hex($throwable::class),
                    'message_hex' => bin2hex($throwable->getMessage()),
                ];
            } finally {
                restore_error_handler();
            }
        }

        return $value . ':seed';
    };
    add_filter($hook, $seed, 7, 1);
    add_filter($hook, $victim, 7, 1);
    add_filter($hook, $anchor, 10, 1);
    $status = static fn (): array => [
        'seed' => has_filter($hook, $seed),
        'victim' => has_filter($hook, $victim),
        'nested' => has_filter($hook, $nested),
        'outer' => has_filter($hook, $outer),
        'any' => has_filter($hook),
    ];
    $before = $status();
    $firstValue = apply_filters($hook, 'seed');
    $firstTrace = $state['trace'];
    $afterFirst = $status();
    $state['trace'] = [];
    $secondValue = apply_filters($hook, 'seed');
    $secondTrace = $state['trace'];
    $afterSecond = $status();
    foreach ([$nested, $victim, $outer, $seed, $anchor] as $callback) {
        $priority = has_filter($hook, $callback);
        if ($priority !== false) {
            remove_filter($hook, $callback, $priority);
        }
    }

    return [
        'warning_target' => $warningTarget,
        'mutation' => $mutation,
        'outer_add_result' => $state['outer_add_result'],
        'outer_throwable' => $state['outer_throwable'],
        'warnings' => $state['warnings'],
        'injection' => $state['injection'],
        'before' => $before,
        'after_first' => $afterFirst,
        'first_apply' => ['value' => $firstValue, 'trace' => $firstTrace],
        'after_second' => $afterSecond,
        'second_apply' => ['value' => $secondValue, 'trace' => $secondTrace],
    ];
}

function callable_program_priority_accepted_args_partial_leaf(string $case): array
{
    $hook = 'callable-priority-cow-accepted-partial-' . $case;
    $state = [
        'trace' => [],
        'warnings' => [],
        'injection' => null,
        'outer_attempted' => false,
        'outer_add_result' => null,
        'outer_throwable' => null,
    ];
    $nested = static function (string $value) use (&$state): string {
        $state['trace'][] = 'nested';
        return $value . ':nested';
    };
    $victim = static function (string $value) use (&$state): string {
        $state['trace'][] = 'victim';
        return $value . ':victim';
    };
    $outer = static function (string $value) use (&$state): string {
        $state['trace'][] = 'outer';
        return $value . ':outer';
    };
    $anchor = static function (string $value) use (&$state): string {
        $state['trace'][] = 'anchor';
        return $value . ':anchor';
    };
    $mutate = $case === 'add_remove_victim';
    $seed = null;
    $seed = static function (string $value) use (&$state, $hook, $nested, $victim, $outer, &$seed, $mutate): string {
        $state['trace'][] = 'seed';
        if (!$state['outer_attempted']) {
            $state['outer_attempted'] = true;
            $warningIndex = 0;
            set_error_handler(static function (int $severity, string $message) use (
                &$state,
                &$warningIndex,
                $hook,
                $nested,
                $victim,
                $mutate,
            ): bool {
                $warningIndex++;
                $state['warnings'][] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
                if ($mutate && $warningIndex === 1 && $state['injection'] === null) {
                    $state['injection'] = ['warning_index' => $warningIndex];
                    $state['injection']['add_result'] = add_filter($hook, $nested, 7, 1);
                    $state['injection']['nested_priority_after_add'] = has_filter($hook, $nested);
                    $state['injection']['remove_result'] = remove_filter($hook, $victim, 7);
                    $state['injection']['victim_priority_after_remove'] = has_filter($hook, $victim);
                }

                return true;
            });
            try {
                $state['outer_add_result'] = add_filter($hook, $outer, 7, new stdClass());
            } catch (Throwable $throwable) {
                $state['outer_throwable'] = [
                    'class_hex' => bin2hex($throwable::class),
                    'message_hex' => bin2hex($throwable->getMessage()),
                ];
            } finally {
                restore_error_handler();
            }
        }

        return $value . ':seed';
    };
    add_filter($hook, $seed, 7, 1);
    add_filter($hook, $victim, 7, 1);
    add_filter($hook, $anchor, 10, 1);
    $status = static fn (): array => [
        'seed' => has_filter($hook, $seed),
        'victim' => has_filter($hook, $victim),
        'nested' => has_filter($hook, $nested),
        'outer' => has_filter($hook, $outer),
        'any' => has_filter($hook),
    ];
    $before = $status();
    $firstValue = apply_filters($hook, 'seed');
    $firstTrace = $state['trace'];
    $afterFirst = $status();
    $state['trace'] = [];
    $secondValue = apply_filters($hook, 'seed');
    $secondTrace = $state['trace'];
    $afterSecond = $status();
    foreach ([$nested, $victim, $outer, $seed, $anchor] as $callback) {
        $priority = has_filter($hook, $callback);
        if ($priority !== false) {
            remove_filter($hook, $callback, $priority);
        }
    }

    return [
        'mutation' => $case,
        'outer_add_result' => $state['outer_add_result'],
        'outer_throwable' => $state['outer_throwable'],
        'warnings' => $state['warnings'],
        'injection' => $state['injection'],
        'before' => $before,
        'after_first' => $afterFirst,
        'first_apply' => ['value' => $firstValue, 'trace' => $firstTrace],
        'after_second' => $afterSecond,
        'second_apply' => ['value' => $secondValue, 'trace' => $secondTrace],
    ];
}

function callable_program_priority_accepted_args_reentry_leaf(string $case): array
{
    $hook = 'callable-priority-cow-accepted-reentry-' . $case;
    $state = [
        'phase' => 'setup',
        'trace' => [],
        'events' => [],
        'warnings' => [],
        'destructors' => [],
        'handler' => null,
        'outer_add_result' => null,
        'outer_throwable' => null,
    ];
    $seedWeak = null;
    $victimWeak = null;
    $nestedWeak = null;
    $outerWeak = null;
    $anchorWeak = null;

    $operation = static function () use (&$state, $hook, $case, &$seedWeak, &$victimWeak, &$nestedWeak, &$outerWeak): void {
        $warningIndex = 0;
        set_error_handler(static function (int $severity, string $message) use (
            &$state,
            &$warningIndex,
            $hook,
            $case,
            &$seedWeak,
            &$victimWeak,
            &$nestedWeak,
        ): bool {
            $warningIndex++;
            $state['warnings'][] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
            $state['phase'] = 'warning_handler';
            if ($case !== 'readonly' && $warningIndex === 1) {
                $seedObject = $seedWeak->get();
                $victimObject = $victimWeak->get();
                $state['handler']['before'] = [
                    'seed_priority' => $seedObject === null ? false : has_filter($hook, [$seedObject, '__invoke']),
                    'victim_priority' => $victimObject === null ? false : has_filter($hook, [$victimObject, '__invoke']),
                ];
                $state['handler']['remove_seed'] = $seedObject === null
                    ? false
                    : remove_filter($hook, [$seedObject, '__invoke'], 7);
                unset($seedObject);
                $state['handler']['remove_victim'] = $victimObject === null
                    ? false
                    : remove_filter($hook, [$victimObject, '__invoke'], 7);
                unset($victimObject);
                $state['handler']['after_remove'] = [
                    'seed_alive' => $seedWeak->get() !== null,
                    'victim_alive' => $victimWeak->get() !== null,
                    'seed_priority' => $seedWeak->get() === null ? false : has_filter($hook, [$seedWeak->get(), '__invoke']),
                    'victim_priority' => $victimWeak->get() === null ? false : has_filter($hook, [$victimWeak->get(), '__invoke']),
                ];
                if ($case === 'remove_recreate') {
                    $nested = new CallablePriorityCowListener('nested', $state);
                    $nestedWeak = WeakReference::create($nested);
                    $state['handler']['recreate_result'] = add_filter($hook, [$nested, '__invoke'], 7, 1);
                    unset($nested);
                    $state['handler']['after_recreate'] = [
                        'nested_alive' => $nestedWeak->get() !== null,
                        'nested_priority' => $nestedWeak->get() === null ? false : has_filter($hook, [$nestedWeak->get(), '__invoke']),
                    ];
                }
            }

            return true;
        });
        $outer = new CallablePriorityCowListener('outer', $state);
        $outerWeak = WeakReference::create($outer);
        try {
            $state['outer_add_result'] = add_filter($hook, [$outer, '__invoke'], 7, new stdClass());
        } catch (Throwable $throwable) {
            $state['outer_throwable'] = [
                'class_hex' => bin2hex($throwable::class),
                'message_hex' => bin2hex($throwable->getMessage()),
            ];
        } finally {
            restore_error_handler();
        }
        unset($outer);
        $state['phase'] = 'after_add';
    };
    $seedOnCall = static function () use (&$state, $operation): void {
        if ($state['handler'] === null && $state['outer_add_result'] === null && $state['outer_throwable'] === null) {
            $state['handler'] = [];
            $operation();
        }
    };
    $seed = new CallablePriorityCowListener('seed', $state, $seedOnCall);
    $victim = new CallablePriorityCowListener('victim', $state);
    $anchor = new CallablePriorityCowListener('anchor', $state);
    $seedWeak = WeakReference::create($seed);
    $victimWeak = WeakReference::create($victim);
    $anchorWeak = WeakReference::create($anchor);
    add_filter($hook, [$seed, '__invoke'], 7, 1);
    add_filter($hook, [$victim, '__invoke'], 7, 1);
    add_filter($hook, [$anchor, '__invoke'], 10, 1);
    unset($seed, $victim, $anchor);

    $status = static function () use ($hook, &$seedWeak, &$victimWeak, &$nestedWeak, &$outerWeak, &$anchorWeak): array {
        $seedObject = $seedWeak->get();
        $victimObject = $victimWeak->get();
        $nestedObject = $nestedWeak?->get();
        $outerObject = $outerWeak?->get();
        $anchorObject = $anchorWeak->get();
        return [
            'seed' => $seedObject === null ? false : has_filter($hook, [$seedObject, '__invoke']),
            'victim' => $victimObject === null ? false : has_filter($hook, [$victimObject, '__invoke']),
            'nested' => $nestedObject === null ? false : has_filter($hook, [$nestedObject, '__invoke']),
            'outer' => $outerObject === null ? false : has_filter($hook, [$outerObject, '__invoke']),
            'anchor' => $anchorObject === null ? false : has_filter($hook, [$anchorObject, '__invoke']),
            'any' => has_filter($hook),
            'weak_alive' => [
                'seed' => $seedObject !== null,
                'victim' => $victimObject !== null,
                'nested' => $nestedObject !== null,
                'outer' => $outerObject !== null,
                'anchor' => $anchorObject !== null,
            ],
        ];
    };
    $before = $status();
    $state['phase'] = 'first_apply';
    $firstValue = apply_filters($hook, 'seed');
    $firstTrace = $state['trace'];
    $firstEvents = $state['events'];
    $afterFirst = $status();
    $state['phase'] = 'second_apply';
    $state['trace'] = [];
    $secondValue = apply_filters($hook, 'seed');
    $secondTrace = $state['trace'];
    $secondEvents = array_slice($state['events'], count($firstEvents));
    $afterSecond = $status();
    $state['phase'] = 'cleanup';
    foreach ([$seedWeak, $victimWeak, $nestedWeak, $outerWeak, $anchorWeak] as $reference) {
        $object = $reference?->get();
        if ($object !== null) {
            $priority = has_filter($hook, [$object, '__invoke']);
            if ($priority !== false) {
                remove_filter($hook, [$object, '__invoke'], $priority);
            }
            unset($object);
        }
    }
    unset($status, $operation, $seedOnCall);
    $state['phase'] = 'request_end';

    return [
        'warnings' => $state['warnings'],
        'handler' => $state['handler'],
        'outer_add_result' => $state['outer_add_result'],
        'outer_throwable' => $state['outer_throwable'],
        'before' => $before,
        'after_first' => $afterFirst,
        'first_apply' => ['value' => $firstValue, 'trace' => $firstTrace],
        'events_after_first' => $firstEvents,
        'destructors_after_first' => array_values(array_filter(
            $state['destructors'],
            static fn (array $item): bool => $item['phase'] !== 'cleanup',
        )),
        'after_second' => $afterSecond,
        'second_apply' => ['value' => $secondValue, 'trace' => $secondTrace],
        'events_after_second' => $secondEvents,
        'destructors_final' => $state['destructors'],
    ];
}

function callable_program_priority_float_accepted_args_lifetime_leaf(string $case): array
{
    $hook = 'callable-priority-cow-float-accepted-lifetime-' . $case;
    $trigger = match ($case) {
        'remove_at_isset' => 1,
        'remove_at_cast' => 2,
        'remove_at_write' => 3,
        default => 0,
    };
    $state = [
        'phase' => 'setup',
        'trace' => [],
        'events' => [],
        'warnings' => [],
        'destructors' => [],
        'handler_observation' => null,
        'outer_add_result' => null,
        'outer_throwable' => null,
    ];
    $seedObject = new CallablePriorityCowListener('seed', $state);
    $seedWeak = WeakReference::create($seedObject);
    $anchorObject = new CallablePriorityCowListener('anchor', $state);
    $anchorWeak = WeakReference::create($anchorObject);
    $outerObject = new CallablePriorityCowListener('outer', $state);
    $outerWeak = WeakReference::create($outerObject);
    add_filter($hook, [$seedObject, '__invoke'], 7, 1);
    add_filter($hook, [$anchorObject, '__invoke'], 10, 1);
    $state['phase'] = 'before_outer_add';

    $warningIndex = 0;
    set_error_handler(static function (int $severity, string $message) use (
        &$state,
        &$warningIndex,
        &$seedObject,
        $seedWeak,
        $outerWeak,
        $hook,
        $trigger,
    ): bool {
        $warningIndex++;
        $state['warnings'][] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
        $state['events'][] = ['kind' => 'warning', 'index' => $warningIndex];
        if ($trigger > 0 && $warningIndex === $trigger) {
            $state['phase'] = 'handler_enter';
            $target = $seedWeak->get();
            $outerTarget = $outerWeak->get();
            $state['handler_observation'] = [
                'entry_seed_alive' => $target !== null,
                'entry_seed_priority' => $target === null ? false : has_filter($hook, [$target, '__invoke']),
                'entry_outer_priority' => $outerTarget === null ? false : has_filter($hook, [$outerTarget, '__invoke']),
            ];
            $state['events'][] = [
                'kind' => 'handler_enter',
                'seed_alive' => $target !== null,
                'outer_priority' => $outerTarget === null ? false : has_filter($hook, [$outerTarget, '__invoke']),
            ];
            $state['handler_observation']['remove_seed'] = $target === null
                ? false
                : remove_filter($hook, [$target, '__invoke'], 7);
            unset($target);
            $seedObject = null;
            $state['phase'] = 'handler_after_remove';
            $state['handler_observation']['after_remove_seed_alive'] = $seedWeak->get() !== null;
            $state['handler_observation']['after_remove_seed_priority'] = $seedWeak->get() === null
                ? false
                : has_filter($hook, [$seedWeak->get(), '__invoke']);
            $state['events'][] = [
                'kind' => 'handler_exit',
                'seed_alive' => $seedWeak->get() !== null,
                'outer_priority' => $outerTarget === null ? false : has_filter($hook, [$outerTarget, '__invoke']),
            ];
            unset($outerTarget);
        }

        return true;
    });
    try {
        $state['outer_add_result'] = add_filter($hook, [$outerObject, '__invoke'], 7.5, new stdClass());
    } catch (Throwable $throwable) {
        $state['outer_throwable'] = [
            'class_hex' => bin2hex($throwable::class),
            'message_hex' => bin2hex($throwable->getMessage()),
        ];
    } finally {
        restore_error_handler();
    }
    $state['phase'] = 'after_outer_add';
    $status = [
        'seed_alive' => $seedWeak->get() !== null,
        'seed_priority' => $seedWeak->get() === null ? false : has_filter($hook, [$seedWeak->get(), '__invoke']),
        'outer_alive' => $outerWeak->get() !== null,
        'outer_priority' => $outerWeak->get() === null ? false : has_filter($hook, [$outerWeak->get(), '__invoke']),
        'anchor_alive' => $anchorWeak->get() !== null,
        'anchor_priority' => $anchorWeak->get() === null ? false : has_filter($hook, [$anchorWeak->get(), '__invoke']),
        'any' => has_filter($hook),
    ];
    foreach ([[$seedWeak, 7], [$outerWeak, 7], [$anchorWeak, 10]] as [$reference, $priority]) {
        $object = $reference->get();
        if ($object !== null && has_filter($hook, [$object, '__invoke']) !== false) {
            remove_filter($hook, [$object, '__invoke'], $priority);
        }
    }
    $state['phase'] = 'cleanup';
    unset($seedObject, $outerObject, $anchorObject);

    return [
        'warning_target' => $trigger,
        'warnings' => $state['warnings'],
        'handler' => $state['handler_observation'],
        'outer_add_result' => $state['outer_add_result'],
        'outer_throwable' => $state['outer_throwable'],
        'after_outer_add' => $status,
        'events' => $state['events'],
        'destructors' => $state['destructors'],
    ];
}

function callable_program_priority_throwable_cleanup_leaf(string $case): array
{
    $hook = 'callable-priority-throwable-cleanup-' . $case;
    $trigger = str_starts_with($case, 'isset_') ? 1 : 2;
    $throwDestructor = str_ends_with($case, 'throwing_destructor');
    $state = [
        'phase' => 'setup',
        'warnings' => [],
        'events' => [],
        'throwable_a' => null,
        'throwable_b' => null,
        'handler' => null,
        'outer_add_result' => null,
        'caught' => null,
    ];
    $seed = new CallablePriorityThrowableSeed($state, $throwDestructor);
    $seedWeak = WeakReference::create($seed);
    $anchor = new CallablePriorityThrowableAnchor();
    $anchorWeak = WeakReference::create($anchor);
    $outer = static fn (mixed $value): mixed => $value . ':outer';
    $outerWeak = WeakReference::create($outer);
    add_filter($hook, [$seed, '__invoke'], 7, 1);
    add_filter($hook, [$anchor, '__invoke'], 10, 1);
    $state['phase'] = 'before_outer_add';

    $warningIndex = 0;
    set_error_handler(static function (int $severity, string $message) use (
        &$state,
        &$warningIndex,
        &$seed,
        $seedWeak,
        $outerWeak,
        $hook,
        $trigger,
    ): bool {
        $warningIndex++;
        $state['warnings'][] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
        $state['events'][] = ['kind' => 'warning', 'index' => $warningIndex];
        if ($warningIndex === $trigger) {
            $state['phase'] = 'handler_enter';
            $seedObject = $seedWeak->get();
            $outerObject = $outerWeak->get();
            $state['handler'] = [
                'warning_index' => $warningIndex,
                'seed_alive_at_entry' => $seedObject !== null,
                'seed_priority_at_entry' => $seedObject === null ? false : has_filter($hook, [$seedObject, '__invoke']),
                'outer_priority_at_entry' => $outerObject === null ? false : has_filter($hook, $outerObject),
                'remove_seed' => $seedObject === null ? false : remove_filter($hook, [$seedObject, '__invoke'], 7),
            ];
            $state['events'][] = ['kind' => 'handler_enter', 'index' => $warningIndex];
            unset($seedObject);
            $seed = null;
            $state['phase'] = 'handler_before_throw_a';
            $state['handler']['seed_alive_after_alias_clear'] = $seedWeak->get() !== null;
            $state['handler']['seed_priority_after_alias_clear'] = $seedWeak->get() === null
                ? false
                : has_filter($hook, [$seedWeak->get(), '__invoke']);
            $state['events'][] = [
                'kind' => 'handler_before_throw_a',
                'seed_alive' => $seedWeak->get() !== null,
            ];
            $throwable = new RuntimeException('A from warning handler');
            $state['throwable_a'] = WeakReference::create($throwable);
            $state['handler']['outer_priority_before_throw_a'] = $outerObject === null
                ? false
                : has_filter($hook, $outerObject);
            $state['events'][] = ['kind' => 'handler_throw_a'];
            unset($outerObject);
            throw $throwable;
        }
        return true;
    });

    try {
        $state['outer_add_result'] = add_filter($hook, $outer, 7.5, 1);
        $state['phase'] = 'after_outer_add';
    } catch (Throwable $throwable) {
        $state['caught'] = $throwable;
        $state['phase'] = 'caught';
        $state['events'][] = ['kind' => 'outer_caught', 'class_hex' => bin2hex($throwable::class)];
    } finally {
        restore_error_handler();
    }

    $caught = $state['caught'];
    $chain = [];
    for ($current = $caught, $depth = 0; $current !== null && $depth < 8; $current = $current->getPrevious(), $depth++) {
        $chain[] = [
            'class_hex' => bin2hex($current::class),
            'message_hex' => bin2hex($current->getMessage()),
            'same_as_a' => $state['throwable_a']?->get() === $current,
            'same_as_b' => $state['throwable_b']?->get() === $current,
        ];
    }
    $after = [
        'seed_alive' => $seedWeak->get() !== null,
        'seed_priority' => $seedWeak->get() === null ? false : has_filter($hook, [$seedWeak->get(), '__invoke']),
        'outer_alive' => $outerWeak->get() !== null,
        'outer_priority' => $outerWeak->get() === null ? false : has_filter($hook, $outerWeak->get()),
        'anchor_alive' => $anchorWeak->get() !== null,
        'anchor_priority' => $anchorWeak->get() === null ? false : has_filter($hook, [$anchorWeak->get(), '__invoke']),
        'any' => has_filter($hook),
    ];

    foreach ([[$seedWeak, '__invoke', 7], [$outerWeak, null, 7], [$anchorWeak, '__invoke', 10]] as [$reference, $method, $priority]) {
        $object = $reference->get();
        if ($object !== null) {
            $callback = $method === null ? $object : [$object, $method];
            if (has_filter($hook, $callback) !== false) {
                remove_filter($hook, $callback, $priority);
            }
        }
    }
    unset($seed, $outer, $anchor, $object, $callback);

    return [
        'warning_target' => $trigger,
        'warnings' => $state['warnings'],
        'handler' => $state['handler'],
        'outer_add_result' => $state['outer_add_result'],
        'caught' => $caught === null ? null : [
            'class_hex' => bin2hex($caught::class),
            'message_hex' => bin2hex($caught->getMessage()),
            'same_as_a' => $state['throwable_a']?->get() === $caught,
            'same_as_b' => $state['throwable_b']?->get() === $caught,
            'previous_chain' => $chain,
        ],
        'after_outer_add' => $after,
        'events' => $state['events'],
    ];
}

function callable_program_remove_cow_leaf(string $case, bool $priorityFirst): array
{
    $trigger = match ($case) {
        'warning1_remove_target', 'warning1_add_missing_target' => 1,
        'warning2_empty_priority7', 'warning2_recreate_priority7' => 2,
        'read_warning_adds_replacement' => 3,
        'unset_warning_adds_replacement' => 4,
        default => 0,
    };
    $group = $priorityFirst ? 'first' : 'read-unset';
    $hook = 'callable-remove-cow-' . $group . '-' . $case;
    $state = [
        'trace' => [],
        'events' => [],
        'warnings' => [],
        'warning_index' => 0,
    ];
    $targetCalls = 0;
    $keeperCalls = 0;
    $replacementCalls = 0;
    $target = static function (string $value) use (&$state, &$targetCalls): string {
        $targetCalls++;
        $state['trace'][] = 'target';
        return $value . ':target';
    };
    $keeper = static function (string $value) use (&$state, &$keeperCalls): string {
        $keeperCalls++;
        $state['trace'][] = 'keeper';
        return $value . ':keeper';
    };
    $replacement = static function (string $value) use (&$state, &$replacementCalls): string {
        $replacementCalls++;
        $state['trace'][] = 'replacement';
        return $value . ':replacement';
    };
    $anchor = static function (string $value) use (&$state): string {
        $state['trace'][] = 'anchor';
        return $value . ':anchor';
    };

    $targetExistsInitially = $case !== 'warning1_add_missing_target';
    if ($targetExistsInitially) {
        add_filter($hook, $target, 7, 1);
    } else {
        add_filter($hook, $keeper, 7, 1);
    }
    add_filter($hook, $anchor, 10, 1);

    set_error_handler(static function (int $severity, string $message) use (
        &$state,
        $hook,
        $case,
        $trigger,
        $target,
        $replacement,
        $priorityFirst,
    ): bool {
        $index = ++$state['warning_index'];
        $state['warnings'][] = [
            'index' => $index,
            'severity' => $severity,
            'message_hex' => bin2hex($message),
        ];
        $state['events'][] = ['kind' => 'warning', 'index' => $index];
        if ($index !== $trigger) {
            return true;
        }

        $state['events'][] = ['kind' => 'handler_enter', 'index' => $index];
        if ($priorityFirst) {
            if ($case === 'warning1_remove_target' || $case === 'warning2_empty_priority7') {
                $state['events'][] = [
                    'kind' => 'handler_remove_target',
                    'result' => remove_filter($hook, $target, 7),
                ];
            } elseif ($case === 'warning1_add_missing_target') {
                $state['events'][] = [
                    'kind' => 'handler_add_target',
                    'result' => add_filter($hook, $target, 7, 1),
                ];
            } elseif ($case === 'warning2_recreate_priority7') {
                $state['events'][] = [
                    'kind' => 'handler_remove_target',
                    'result' => remove_filter($hook, $target, 7),
                ];
                $state['events'][] = [
                    'kind' => 'handler_add_replacement',
                    'result' => add_filter($hook, $replacement, 7, 1),
                ];
            }
        } else {
            $state['events'][] = [
                'kind' => 'handler_add_replacement',
                'result' => add_filter($hook, $replacement, 7, 1),
            ];
        }
        $state['events'][] = ['kind' => 'handler_exit', 'index' => $index];
        return true;
    });

    $removeResult = null;
    $caught = null;
    try {
        $removeResult = remove_filter($hook, $target, 7.5);
    } catch (Throwable $throwable) {
        $caught = [
            'class_hex' => bin2hex($throwable::class),
            'message_hex' => bin2hex($throwable->getMessage()),
        ];
    } finally {
        restore_error_handler();
    }

    $afterRemove = [
        'target' => has_filter($hook, $target),
        'keeper' => has_filter($hook, $keeper),
        'replacement' => has_filter($hook, $replacement),
        'anchor' => has_filter($hook, $anchor),
        'any' => has_filter($hook),
    ];
    $state['events'][] = ['kind' => 'after_remove', 'state' => $afterRemove];
    $state['trace'] = [];
    $applyValue = apply_filters($hook, 'seed');
    return [
        'case' => $case,
        'trigger_warning' => $trigger,
        'warnings' => $state['warnings'],
        'events' => $state['events'],
        'remove_result' => $removeResult,
        'caught' => $caught,
        'after_remove' => $afterRemove,
        'apply_value' => $applyValue,
        'apply_trace' => $state['trace'],
        'callback_calls' => [
            'target' => $targetCalls,
            'keeper' => $keeperCalls,
            'replacement' => $replacementCalls,
        ],
    ];
}

function callable_program_remove_cow_cases(): array
{
    return [
        'priority_first' => [
            'control' => callable_program_remove_cow_leaf('control', true),
            'warning1_remove_target' => callable_program_remove_cow_leaf('warning1_remove_target', true),
            'warning1_add_missing_target' => callable_program_remove_cow_leaf('warning1_add_missing_target', true),
            'warning2_empty_priority7' => callable_program_remove_cow_leaf('warning2_empty_priority7', true),
            'warning2_recreate_priority7' => callable_program_remove_cow_leaf('warning2_recreate_priority7', true),
        ],
        'priority_read_unset' => [
            'control' => callable_program_remove_cow_leaf('control', false),
            'read_warning_adds_replacement' => callable_program_remove_cow_leaf('read_warning_adds_replacement', false),
            'unset_warning_adds_replacement' => callable_program_remove_cow_leaf('unset_warning_adds_replacement', false),
        ],
    ];
}

final class CallableProgramHookUnsetDestructor
{
    public static ?Throwable $lastThrown = null;

    public function __construct(
        private array &$events,
        private Throwable $failure,
    ) {}

    public function __invoke(string $value): string
    {
        $this->events[] = 'replacement_invoked';
        return $value . ':replacement';
    }

    public function __destruct()
    {
        $this->events[] = 'replacement_destructor';
        self::$lastThrown = $this->failure;
        throw $this->failure;
    }
}

function callable_program_hook_unset_leaf(string $case): array
{
    if (!in_array($case, ['control', 'inject_on_hook_unset', 'throw_during_finally'], true)) {
        throw new RuntimeException('unknown callable hook-unset case');
    }

    $hook = 7.5;
    $state = [
        'events' => [],
        'warnings' => [],
        'trace' => [],
        'phase' => 'setup',
        'injecting' => false,
        'injection_result' => null,
    ];
    $primary = $case === 'throw_during_finally' ? new RuntimeException('primary A') : null;
    $secondary = $case === 'throw_during_finally' ? new RuntimeException('destructor B') : null;
    $caught = null;
    CallableProgramHookUnsetDestructor::$lastThrown = null;
    $target = static function (string $value) use (&$state): string {
        $state['trace'][] = 'target';
        return $value . ':target';
    };
    $replacement = static function (string $value) use (&$state): string {
        $state['trace'][] = 'replacement';
        return $value . ':replacement';
    };

    set_error_handler(static function (int $severity, string $message) use (
        &$state,
        $case,
        $hook,
        $replacement,
        $secondary,
    ): bool {
        $index = count($state['warnings']) + 1;
        $state['warnings'][] = [
            'index' => $index,
            'phase' => $state['phase'],
            'severity' => $severity,
            'message_hex' => bin2hex($message),
        ];
        $state['events'][] = [
            'kind' => 'warning',
            'index' => $index,
            'phase' => $state['phase'],
        ];
        if ($case !== 'control'
            && $state['phase'] === 'remove'
            && $index === 7
            && !$state['injecting']
        ) {
            $state['injecting'] = true;
            $state['events'][] = ['kind' => 'injection_enter', 'warning_index' => $index];
            $state['phase'] = 'nested_add';
            if ($case === 'throw_during_finally') {
                $object = new CallableProgramHookUnsetDestructor($state['events'], $secondary);
                $state['injection_result'] = add_filter(7, $object, 7, 1);
                unset($object);
            } else {
                $state['injection_result'] = add_filter(7, $replacement, 7, 1);
            }
            $state['phase'] = 'remove';
            $state['events'][] = [
                'kind' => 'injection_return',
                'warning_index' => $index,
                'result' => $state['injection_result'],
            ];
            $state['injecting'] = false;
        }
        return true;
    });

    $setupResult = add_filter($hook, $target, 7, 1);
    $removeResult = null;
    if ($case === 'throw_during_finally') {
        try {
            try {
                $state['events'][] = ['kind' => 'throw_primary_A'];
                throw $primary;
            } finally {
                $state['phase'] = 'remove';
                $removeResult = remove_filter($hook, $target, 7);
            }
        } catch (Throwable $throwable) {
            $caught = [
                'class_hex' => bin2hex($throwable::class),
                'message_hex' => bin2hex($throwable->getMessage()),
                'is_primary_A' => $throwable === $primary,
                'is_destructor_B' => $throwable === CallableProgramHookUnsetDestructor::$lastThrown,
                'previous_is_primary_A' => $throwable->getPrevious() === $primary,
            ];
            $state['events'][] = [
                'kind' => 'caught',
                'is_primary_A' => $throwable === $primary,
                'is_destructor_B' => $throwable === CallableProgramHookUnsetDestructor::$lastThrown,
            ];
        }
    } else {
        $state['phase'] = 'remove';
        $removeResult = remove_filter($hook, $target, 7);
    }

    $state['phase'] = 'observe';
    $afterRemove = [
        'target' => has_filter($hook, $target),
        'replacement' => has_filter($hook, $replacement),
        'any' => has_filter($hook),
    ];
    $state['trace'] = [];
    $applyValue = apply_filters($hook, 'seed');
    restore_error_handler();

    return [
        'case' => $case,
        'hook_value' => $hook,
        'setup_result' => $setupResult,
        'remove_result' => $removeResult,
        'injection_result' => $state['injection_result'],
        'caught' => $caught,
        'warnings' => $state['warnings'],
        'events' => $state['events'],
        'after_remove' => $afterRemove,
        'apply_value' => $applyValue,
        'apply_trace' => $state['trace'],
    ];
}

function callable_program_hook_unset_cases(): array
{
    return [
        'control' => callable_program_hook_unset_leaf('control'),
        'inject_on_hook_unset' => callable_program_hook_unset_leaf('inject_on_hook_unset'),
        'throw_during_finally' => callable_program_hook_unset_leaf('throw_during_finally'),
    ];
}
function callable_program_priority_cow_cases(): array
{
    return [
        'float_priority' => [
            'control' => callable_program_priority_float_leaf('control'),
            'isset_add' => callable_program_priority_float_leaf('isset_add'),
            'isset_remove_bucket' => callable_program_priority_float_leaf('isset_remove_bucket'),
            'write_add' => callable_program_priority_float_leaf('write_add'),
            'write_remove_bucket' => callable_program_priority_float_leaf('write_remove_bucket'),
        ],
        'accepted_args_partial_bucket' => [
            'control' => callable_program_priority_accepted_args_partial_leaf('control'),
            'add_remove_victim' => callable_program_priority_accepted_args_partial_leaf('add_remove_victim'),
        ],
        'accepted_args_reentry' => [
            'readonly' => callable_program_priority_accepted_args_reentry_leaf('readonly'),
            'remove_bucket' => callable_program_priority_accepted_args_reentry_leaf('remove_bucket'),
            'remove_recreate' => callable_program_priority_accepted_args_reentry_leaf('remove_recreate'),
        ],
        'float_accepted_args_lifetime' => [
            'control' => callable_program_priority_float_accepted_args_lifetime_leaf('control'),
            'remove_at_isset' => callable_program_priority_float_accepted_args_lifetime_leaf('remove_at_isset'),
            'remove_at_cast' => callable_program_priority_float_accepted_args_lifetime_leaf('remove_at_cast'),
            'remove_at_write' => callable_program_priority_float_accepted_args_lifetime_leaf('remove_at_write'),
        ],
        'throwable_cleanup' => [
            'isset_control' => callable_program_priority_throwable_cleanup_leaf('isset_control'),
            'isset_throwing_destructor' => callable_program_priority_throwable_cleanup_leaf('isset_throwing_destructor'),
            'write_control' => callable_program_priority_throwable_cleanup_leaf('write_control'),
            'write_throwing_destructor' => callable_program_priority_throwable_cleanup_leaf('write_throwing_destructor'),
        ],
    ];
}

function callable_program_action_heap_argument_continuation(): array
{
    $hook = 'callable-action-heap-arguments';
    $nestedHook = 'callable-action-heap-arguments-nested';
    $events = [];
    $object = (object) ['marker' => 'seed'];
    $cell = 'seed';
    $payload = ['cell' => &$cell];

    $observe = static function (string $event, string $name, $receivedObject, $receivedPayload) use (
        &$events,
        $object,
    ): void {
        $events[] = [
            'event' => $event,
            'hook' => $name,
            'object_class' => is_object($receivedObject) ? $receivedObject::class : null,
            'same_object' => $receivedObject === $object,
            'object_marker' => is_object($receivedObject) ? ($receivedObject->marker ?? null) : null,
            'cell' => is_array($receivedPayload) ? ($receivedPayload['cell'] ?? null) : null,
        ];
    };

    $all = static function (...$args) use ($hook, $nestedHook, $observe): void {
        $name = $args[0] ?? null;
        if ($name !== $hook && $name !== $nestedHook) {
            return;
        }
        $observe('all', $name, $args[1] ?? null, $args[2] ?? null);
    };
    add_action('all', $all, 1, 0);

    $nested = static function (...$args) use ($nestedHook, $observe): void {
        $objectArg = $args[0] ?? null;
        $payloadArg = $args[1] ?? null;
        $observe('nested-enter', $nestedHook, $objectArg, $payloadArg);
        if (is_object($objectArg)) {
            $objectArg->marker = 'nested';
        }
        if (is_array($payloadArg)) {
            $payloadArg['cell'] = 'nested';
        }
        $observe('nested-exit', $nestedHook, $objectArg, $payloadArg);
    };
    add_action($nestedHook, $nested, 10, 2);

    $outer = static function (...$args) use (
        $hook,
        $nestedHook,
        $object,
        &$cell,
        &$events,
        $observe,
    ): void {
        $objectArg = $args[0] ?? null;
        $payloadArg = $args[1] ?? null;
        $observe('named-enter', $hook, $objectArg, $payloadArg);
        if (is_object($objectArg)) {
            $objectArg->marker = 'outer';
        }
        if (is_array($payloadArg)) {
            $payloadArg['cell'] = 'outer';
        }
        $nestedReturn = do_action($nestedHook, $objectArg, $payloadArg);
        $events[] = [
            'event' => 'outer-resume',
            'object_class' => is_object($objectArg) ? $objectArg::class : null,
            'same_object' => $objectArg === $object,
            'object_marker' => is_object($objectArg) ? ($objectArg->marker ?? null) : null,
            'cell' => is_array($payloadArg) ? ($payloadArg['cell'] ?? null) : null,
            'scalar_cell' => $cell,
            'nested_return' => $nestedReturn,
        ];
    };
    add_action($hook, $outer, 10, 2);

    $outerReturn = do_action($hook, $object, $payload);
    $after = [
        'object_class' => $object::class,
        'object_marker' => $object->marker,
        'cell' => $payload['cell'],
        'scalar_cell' => $cell,
        'outer_count' => did_action($hook),
        'nested_count' => did_action($nestedHook),
    ];

    remove_action($hook, $outer, 10);
    remove_action($nestedHook, $nested, 10);
    remove_action('all', $all, 1);

    return [
        'events' => $events,
        'returns' => ['outer' => $outerReturn],
        'after' => $after,
    ];
}

function callable_program_action_singleton_object_cases(): array
{
    $view = static function ($value, object $expected) use (&$view): array {
        if (is_object($value)) {
            return ['type' => 'object', 'class' => get_class($value), 'marker' => $value->marker,
                'same_object' => $value === $expected];
        }
        if (is_array($value)) {
            $items = [];
            foreach ($value as $key => $item) {
                $items[] = ['key' => (string) $key, 'value' => $view($item, $expected)];
            }
            return ['type' => 'array', 'count' => count($value), 'items' => $items];
        }
        if (is_string($value)) {
            return ['type' => 'string', 'hex' => bin2hex($value)];
        }
        return ['type' => get_debug_type($value)];
    };
    $definitions = [
        'zero_key_singleton' => ['zero-key-singleton', [0 => 'object']],
        'nonzero_key_singleton' => ['nonzero-key-singleton', [1 => 'object']],
        'multi_element' => ['multi-element', [0 => 'object', 1 => 'tail']],
        'direct_object' => ['direct-object', 'object'],
        'extra_args' => ['extra-args', [[0 => 'object'], 'tail']],
        'missing_named_listener' => ['missing-named-listener', [0 => 'object']],
    ];
    $cases = [];
    foreach ($definitions as $case => [$label, $shape]) {
        $GLOBALS['wp_filter'] = [];
        $GLOBALS['wp_actions'] = [];
        $GLOBALS['wp_filters'] = [];
        $GLOBALS['wp_current_filter'] = [];
        $hook = 'singleton-object-' . $label;
        $object = (object) ['marker' => 'object-' . $label];
        $events = [];
        add_action('all', static function (...$args) use (&$events, $hook, $object, $view): void {
            if (($args[0] ?? null) === $hook) {
                $events[] = ['event' => 'all', 'args' => array_map(
                    static fn ($value): array => $view($value, $object), $args,
                )];
            }
        }, 10, 99);
        if ($case !== 'missing_named_listener') {
            add_action($hook, static function (...$args) use (&$events, $object, $view): void {
                $events[] = ['event' => 'named', 'args' => array_map(
                    static fn ($value): array => $view($value, $object), $args,
                )];
            }, 10, 99);
        }
        $convert = null;
        $convert = static function ($value) use (&$convert, $object) {
            if ($value === 'object') { return $object; }
            if (is_array($value)) { foreach ($value as $key => $entry) { $value[$key] = $convert($entry); } }
            return $value;
        };
        $args = $convert($shape);
        if ($case === 'direct_object') {
            do_action($hook, $object);
        } elseif ($case === 'extra_args') {
            do_action($hook, $args[0], $args[1]);
        } else {
            do_action($hook, $args);
        }
        $cases[$case] = ['events' => $events, 'did_action' => did_action($hook),
            'current_filter_stack' => $GLOBALS['wp_current_filter']];
    }
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];
    return $cases;
}

function callable_program_action_hook_alias_cases(): array
{
    $view = static function ($value): array {
        if ($value === null) return ['type' => 'null'];
        if (is_bool($value)) return ['type' => 'bool', 'value' => $value];
        if (is_int($value)) return ['type' => 'int', 'value' => (string) $value];
        if (is_string($value)) return ['type' => 'string', 'hex' => bin2hex($value)];
        return ['type' => get_debug_type($value)];
    };
    $definitions = [
        'string_registration_integer_dispatch' => ['5', 5],
        'integer_registration_string_dispatch' => [5, '5'],
    ];
    $cases = [];
    foreach ($definitions as $name => [$registered, $dispatched]) {
        $GLOBALS['wp_filter'] = [];
        if ($name === 'string_registration_integer_dispatch') {
            $GLOBALS['wp_actions'] = [];
        }
        $GLOBALS['wp_filters'] = [];
        $GLOBALS['wp_current_filter'] = [];
        $events = [];
        $before = ['string' => $view(did_action('5')), 'integer' => $view(did_action(5))];
        $all = static function (...$args) use (&$events, $view): void {
            if (($args[0] ?? null) !== '5' && ($args[0] ?? null) !== 5) return;
            $events[] = ['event' => 'all', 'args' => array_map($view, $args),
                'current_action' => $view(current_action()),
                'did_action_string' => $view(did_action('5')),
                'did_action_integer' => $view(did_action(5))];
        };
        add_action('all', $all, 1, 99);
        $named = static function (...$args) use (&$events, $view): void {
            $events[] = ['event' => 'named', 'args' => array_map($view, $args),
                'current_action' => $view(current_action()),
                'did_action_string' => $view(did_action('5')),
                'did_action_integer' => $view(did_action(5))];
        };
        add_action($registered, $named, 10, 99);
        $hasBefore = ['registered' => $view(has_action($registered, $named)),
            'dispatch' => $view(has_action($dispatched, $named))];
        do_action($dispatched, 'payload');
        $secondDispatch = is_int($dispatched) ? '5' : 5;
        do_action($secondDispatch, 'payload');
        $removed = remove_action($dispatched, $named, 10);
        remove_action('all', $all, 1);
        $hasAfterRemove = ['registered' => $view(has_action($registered, $named)),
            'dispatch' => $view(has_action($dispatched, $named))];
        $after = ['did_action_string' => $view(did_action('5')),
            'did_action_integer' => $view(did_action(5)), 'removed_by_dispatch_alias' => $removed,
            'has_after_remove' => $hasAfterRemove, 'current_action' => $view(current_action()),
            'doing_action' => doing_action(),
            'stack' => array_map($view, $GLOBALS['wp_current_filter'])];
        $cases[$name] = ['label' => $name, 'registered_hook' => $view($registered),
            'dispatch_hook' => $view($dispatched), 'second_dispatch_hook' => $view($secondDispatch),
            'before' => $before, 'has_before_dispatch' => $hasBefore,
            'events' => $events, 'after' => $after];
    }

    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];
    $events = [];
    $all = static function (...$args) use (&$events, $view): void {
        $hook = $args[0] ?? null;
        if ($hook !== '05' && $hook !== 5) return;
        $events[] = ['event' => 'all', 'args' => array_map($view, $args),
            'current_action' => $view(current_action()),
            'did_action_five_string' => $view(did_action('5')),
            'did_action_five_integer' => $view(did_action(5)),
            'did_action_leading_zero' => $view(did_action('05'))];
    };
    $named = static function (...$args) use (&$events, $view): void {
        $events[] = ['event' => 'named', 'args' => array_map($view, $args),
            'current_action' => $view(current_action()),
            'did_action_five_string' => $view(did_action('5')),
            'did_action_five_integer' => $view(did_action(5)),
            'did_action_leading_zero' => $view(did_action('05'))];
    };
    add_action('all', $all, 1, 99);
    add_action('05', $named, 10, 99);
    $before = ['five_string' => $view(did_action('5')), 'five_integer' => $view(did_action(5)),
        'leading_zero' => $view(did_action('05'))];
    $hasBefore = ['leading_zero' => $view(has_action('05', $named)),
        'integer_five' => $view(has_action(5, $named))];
    do_action(5, 'integer-dispatch');
    do_action('05', 'leading-zero-dispatch');
    $after = ['did_action_five_string' => $view(did_action('5')),
        'did_action_five_integer' => $view(did_action(5)),
        'did_action_leading_zero' => $view(did_action('05')),
        'current_action' => $view(current_action()), 'doing_action' => doing_action(),
        'stack' => array_map($view, $GLOBALS['wp_current_filter'])];
    remove_action('05', $named, 10);
    remove_action('all', $all, 1);
    $cases['leading_zero_non_alias'] = ['label' => 'leading_zero_non_alias',
        'registered_hook' => $view('05'), 'integer_hook' => $view(5), 'before' => $before,
        'has_before' => $hasBefore, 'events' => $events, 'after' => $after];

    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];
    $events = [];
    $all = static function (...$args) use (&$events, $view): void {
        $hook = $args[0] ?? null;
        if ($hook !== '05' && $hook !== 5) return;
        $events[] = ['event' => 'all', 'args' => array_map($view, $args),
            'current_action' => $view(current_action()), 'did_action_five' => $view(did_action(5)),
            'did_action_leading_zero' => $view(did_action('05'))];
    };
    $named = static function (...$args) use (&$events, $view): void {
        $events[] = ['event' => 'named', 'args' => array_map($view, $args),
            'current_action' => $view(current_action()), 'did_action_five' => $view(did_action(5)),
            'did_action_leading_zero' => $view(did_action('05'))];
    };
    add_action('all', $all, 1, 99);
    add_action(5, $named, 10, 99);
    $before = ['five' => $view(did_action(5)), 'leading_zero' => $view(did_action('05'))];
    $hasBefore = ['integer_five' => $view(has_action(5, $named)),
        'leading_zero' => $view(has_action('05', $named))];
    do_action('05', 'leading-zero-dispatch');
    do_action(5, 'integer-dispatch');
    $after = ['did_action_five' => $view(did_action(5)),
        'did_action_leading_zero' => $view(did_action('05')),
        'current_action' => $view(current_action()), 'doing_action' => doing_action(),
        'stack' => array_map($view, $GLOBALS['wp_current_filter'])];
    remove_action(5, $named, 10);
    remove_action('all', $all, 1);
    $cases['leading_zero_registration_integer_dispatch'] = [
        'label' => 'leading_zero_registration_integer_dispatch', 'registered_hook' => $view(5),
        'leading_zero_hook' => $view('05'), 'before' => $before, 'has_before' => $hasBefore,
        'events' => $events, 'after' => $after];

    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];
    return $cases;
}

function callable_program_action_api_cases(): array
{
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];

    $heapArgumentContinuation = callable_program_action_heap_argument_continuation();
    $singletonObjectCases = callable_program_action_singleton_object_cases();
    $hookKeyAliasCases = callable_program_action_hook_alias_cases();

    $hook = 'callable-action-api';
    $events = [];
    $observe = static function (string $label, array $args) use (&$events, $hook): void {
        $events[] = ['event' => $label, 'args' => $args, 'current_action' => current_action(),
            'doing_action' => doing_action(), 'doing_named' => doing_action($hook),
            'did_action' => did_action($hook), 'did_filter' => did_filter($hook),
            'wp_actions' => $GLOBALS['wp_actions'][$hook] ?? 0];
    };
    $first = static function (...$args) use ($observe): string { $observe('first', $args); return 'ignored-first-return'; };
    $second = static function (...$args) use ($observe): bool { $observe('second', $args); return false; };
    $added = add_action($hook, $first, 0, 99);
    $addedSecond = add_action($hook, $second, 10, 99);
    $aliases = ['add_action' => $added, 'add_action_second' => $addedSecond,
        'has_action_any' => has_action($hook), 'has_action_zero_priority' => has_action($hook, $first),
        'has_action_second_priority' => has_action($hook, $second),
        'has_action_missing' => has_action($hook, static function (): void {}),
        'has_action_wrong_priority' => has_action($hook, $first, 1),
        'remove_action_wrong_priority' => remove_action($hook, $first, 1),
        'remove_action_first' => remove_action($hook, $first, 0),
        'has_action_after_remove' => has_action($hook)];
    add_action($hook, $first, 0, 99);
    $doReturn = do_action($hook, 'outer', 'tail');
    $afterOuter = ['did_action' => did_action($hook), 'did_filter' => did_filter($hook),
        'current_action' => current_action(), 'doing_action' => doing_action()];

    $placeholderEvents = [];
    add_action('callable-action-empty-args', static function (...$args) use (&$placeholderEvents): void { $placeholderEvents[] = ['accepted_args' => 1, 'args' => $args]; }, 10, 1);
    add_action('callable-action-empty-args', static function (...$args) use (&$placeholderEvents): void { $placeholderEvents[] = ['accepted_args' => 0, 'args' => $args]; }, 20, 0);
    $emptyReturn = do_action('callable-action-empty-args');

    $allEvents = [];
    $allObserve = static function (int $acceptedArgs, array $args) use (&$allEvents): void {
        $hookName = $args[0] ?? '';
        $allEvents[] = ['accepted_args' => $acceptedArgs, 'args' => $args,
            'current_action' => current_action(), 'doing_action' => doing_action(),
            'did_action' => did_action($hookName), 'wp_actions' => $GLOBALS['wp_actions'][$hookName] ?? 0];
    };
    $allObserverZero = static function (...$args) use ($allObserve): void { $allObserve(0, $args); };
    $allObserverOne = static function (...$args) use ($allObserve): void { $allObserve(1, $args); };
    add_action('all', $allObserverZero, 1, 0);
    add_action('all', $allObserverOne, 2, 1);
    add_action('callable-action-all-args', static function (...$args) use (&$allEvents): void { $allEvents[] = ['named' => true, 'args' => $args]; }, 10, 1);
    $allReturn = do_action('callable-action-all-args', 'alpha', 'beta', 'gamma');
    $allNoListenerReturn = do_action('callable-action-all-without-named-listener', 'solo', 'extra');

    $sameNameBeforeFilter = ['did_action' => did_action($hook), 'did_filter' => did_filter($hook)];
    apply_filters($hook, 'filter-seed');
    $sameNameAfterFilter = ['did_action' => did_action($hook), 'did_filter' => did_filter($hook)];

    $nestedEvents = [];
    $nestedHook = 'callable-action-nested';
    $nestedOnce = false;
    $nested = static function (...$args) use (&$nestedEvents, &$nestedOnce, $nestedHook): void {
        $nestedEvents[] = ['event' => 'outer-enter', 'args' => $args, 'count' => did_action($nestedHook), 'current_action' => current_action()];
        if (!$nestedOnce) { $nestedOnce = true; do_action($nestedHook, 'inner'); do_action('callable-action-nested-child', 'child'); }
        $nestedEvents[] = ['event' => 'outer-exit', 'count' => did_action($nestedHook), 'current_action' => current_action()];
    };
    add_action($nestedHook, $nested, 10, 9);
    add_action('callable-action-nested-child', static function (...$args) use (&$nestedEvents, $nestedHook): void {
        $nestedEvents[] = ['event' => 'child', 'args' => $args, 'current_action' => current_action(),
            'doing_parent' => doing_action($nestedHook), 'doing_child' => doing_action('callable-action-nested-child')];
    }, 10, 9);
    $nestedReturn = do_action($nestedHook, 'outer');
    $nestedCaughtParentHook = 'callable-action-nested-caught-parent';
    $nestedCaughtChildHook = 'callable-action-nested-caught-child';
    $nestedCaughtEvents = [];
    $nestedCaughtThrowable = null;
    $nestedCaughtParent = static function (...$args) use (
        &$nestedCaughtEvents,
        &$nestedCaughtThrowable,
        $nestedCaughtParentHook,
        $nestedCaughtChildHook
    ): void {
        $nestedCaughtEvents[] = [
            'event' => 'parent-enter',
            'args' => $args,
            'current_action' => current_action(),
            'doing_parent' => doing_action($nestedCaughtParentHook),
            'doing_child' => doing_action($nestedCaughtChildHook),
            'parent_count' => did_action($nestedCaughtParentHook),
            'child_count' => did_action($nestedCaughtChildHook),
        ];
        try {
            do_action($nestedCaughtChildHook, 'child-argument');
        } catch (RuntimeException $throwable) {
            if ($throwable::class !== RuntimeException::class
                || $throwable->getMessage() !== 'nested action child boom') {
                throw $throwable;
            }
            $nestedCaughtEvents[] = [
                'event' => 'parent-catch',
                'same_throwable' => $throwable === $nestedCaughtThrowable,
                'class' => $throwable::class,
                'message' => $throwable->getMessage(),
                'current_action' => current_action(),
                'doing_parent' => doing_action($nestedCaughtParentHook),
                'doing_child' => doing_action($nestedCaughtChildHook),
                'parent_count' => did_action($nestedCaughtParentHook),
                'child_count' => did_action($nestedCaughtChildHook),
            ];
        }
        $nestedCaughtEvents[] = [
            'event' => 'parent-resume',
            'current_action' => current_action(),
            'doing_parent' => doing_action($nestedCaughtParentHook),
            'doing_child' => doing_action($nestedCaughtChildHook),
            'parent_count' => did_action($nestedCaughtParentHook),
            'child_count' => did_action($nestedCaughtChildHook),
        ];
    };
    add_action($nestedCaughtParentHook, $nestedCaughtParent, 10, 9);
    add_action($nestedCaughtChildHook, static function (...$args) use (
        &$nestedCaughtEvents,
        &$nestedCaughtThrowable,
        $nestedCaughtParentHook,
        $nestedCaughtChildHook
    ): void {
        $nestedCaughtEvents[] = [
            'event' => 'child-enter',
            'args' => $args,
            'current_action' => current_action(),
            'doing_parent' => doing_action($nestedCaughtParentHook),
            'doing_child' => doing_action($nestedCaughtChildHook),
            'parent_count' => did_action($nestedCaughtParentHook),
            'child_count' => did_action($nestedCaughtChildHook),
        ];
        $nestedCaughtThrowable = new RuntimeException('nested action child boom');
        $nestedCaughtEvents[] = [
            'event' => 'child-throw',
            'class' => $nestedCaughtThrowable::class,
            'message' => $nestedCaughtThrowable->getMessage(),
            'current_action' => current_action(),
            'doing_parent' => doing_action($nestedCaughtParentHook),
            'doing_child' => doing_action($nestedCaughtChildHook),
            'parent_count' => did_action($nestedCaughtParentHook),
            'child_count' => did_action($nestedCaughtChildHook),
        ];
        throw $nestedCaughtThrowable;
    }, 10, 9);
    $nestedCaughtReturn = do_action($nestedCaughtParentHook, 'parent-argument');
    $nestedCaughtAfter = [
        'current_action' => current_action(),
        'doing_parent' => doing_action($nestedCaughtParentHook),
        'doing_child' => doing_action($nestedCaughtChildHook),
        'parent_count' => did_action($nestedCaughtParentHook),
        'child_count' => did_action($nestedCaughtChildHook),
        'stack_depth' => count($GLOBALS['wp_current_filter']),
    ];
    $noListenerHook = 'callable-action-no-listener';
    $noListenerBefore = did_action($noListenerHook);
    $noListenerReturn = do_action($noListenerHook);
    $noListenerAfter = did_action($noListenerHook);

    $counterSeedDiagnostics = [];
    foreach ([
        'bool_true' => [
            'hook' => 'callable-action-counter-seed-diagnostic-bool-true',
            'seed' => true,
        ],
        'alpha' => [
            'hook' => 'callable-action-counter-seed-diagnostic-alpha',
            'seed' => 'a',
        ],
    ] as $label => $case) {
        $diagnosticHook = $case['hook'];
        $GLOBALS['wp_actions'][$diagnosticHook] = $case['seed'];
        $diagnosticTrace = [];
        $diagnostics = [];
        $dispatchLabel = null;
        add_action('all', static function (...$args) use (
            &$diagnosticTrace,
            &$dispatchLabel,
            $diagnosticHook
        ): void {
            if (($args[0] ?? null) !== $diagnosticHook) {
                return;
            }
            $diagnosticTrace[] = [
                'event' => 'all',
                'dispatch' => $dispatchLabel,
                'args' => $args,
            ];
        }, 0, 0);
        add_action($diagnosticHook, static function (...$args) use (
            &$diagnosticTrace,
            &$dispatchLabel,
            $diagnosticHook
        ): void {
            $diagnosticTrace[] = [
                'event' => 'named',
                'dispatch' => $dispatchLabel,
                'args' => $args,
                'did_action' => callable_program_scalar_view(did_action($diagnosticHook)),
                'wp_actions' => callable_program_scalar_view($GLOBALS['wp_actions'][$diagnosticHook] ?? null),
            ];
        }, 10, 99);

        set_error_handler(static function (int $severity, string $message) use (
            &$diagnosticTrace,
            &$diagnostics,
            &$dispatchLabel
        ): bool {
            $diagnostic = [
                'severity' => $severity,
                'message_hex' => bin2hex($message),
            ];
            $diagnostics[] = $diagnostic;
            $diagnosticTrace[] = [
                'event' => 'diagnostic',
                'dispatch' => $dispatchLabel,
                ...$diagnostic,
            ];
            return true;
        });
        try {
            $dispatchLabel = 'first';
            $firstReturn = do_action($diagnosticHook, 'first');
            $dispatchLabel = 'second';
            $secondReturn = do_action($diagnosticHook, 'second');
        } finally {
            restore_error_handler();
        }

        $counterSeedDiagnostics[$label] = [
            'seed' => callable_program_scalar_view($case['seed']),
            'returns' => [
                callable_program_scalar_view($firstReturn),
                callable_program_scalar_view($secondReturn),
            ],
            'diagnostics' => $diagnostics,
            'trace' => $diagnosticTrace,
            'after' => callable_program_scalar_view($GLOBALS['wp_actions'][$diagnosticHook] ?? null),
        ];
    }

    $counterSeedValues = [];
    foreach ([
        'int_7' => ['hook' => 'callable-action-counter-seed-int-7', 'seed' => 7],
        'float_7_5' => ['hook' => 'callable-action-counter-seed-float-7-5', 'seed' => 7.5],
        'php_int_max' => ['hook' => 'callable-action-counter-seed-php-int-max', 'seed' => PHP_INT_MAX],
    ] as $label => $case) {
        $counterSeedHook = $case['hook'];
        $GLOBALS['wp_actions'][$counterSeedHook] = $case['seed'];
        $counterSeedBefore = callable_program_scalar_view(did_action($counterSeedHook));
        $counterSeedEvents = [];
        $counterSeedCallback = static function () use (&$counterSeedEvents, $counterSeedHook): void {
            $counterSeedEvents[] = [
                'did_action' => callable_program_scalar_view(did_action($counterSeedHook)),
                'wp_actions' => callable_program_scalar_view($GLOBALS['wp_actions'][$counterSeedHook] ?? null),
            ];
        };
        add_action($counterSeedHook, $counterSeedCallback, 10, 0);
        $counterSeedReturns = [do_action($counterSeedHook), do_action($counterSeedHook)];
        $counterSeedAfter = callable_program_scalar_view(did_action($counterSeedHook));
        remove_action($counterSeedHook, $counterSeedCallback, 10);
        $counterSeedValues[$label] = [
            'before' => $counterSeedBefore,
            'returns' => $counterSeedReturns,
            'events' => $counterSeedEvents,
            'after' => $counterSeedAfter,
        ];
    }

    $throwAllEvents = []; $throwAllHook = 'callable-action-throw-all'; $throwAllOnce = false;
    add_action('all', static function (...$args) use (&$throwAllEvents, &$throwAllOnce, $throwAllHook): void {
        if (($args[0] ?? null) !== $throwAllHook) { return; }
        $throwAllEvents[] = ['event' => 'all-enter', 'args' => $args, 'count' => did_action($throwAllHook), 'current_action' => current_action(), 'doing' => doing_action()];
        if (!$throwAllOnce) { $throwAllOnce = true; throw new RuntimeException('action all boom'); }
        $throwAllEvents[] = ['event' => 'all-exit'];
    }, 3, 0);
    add_action($throwAllHook, static function (...$args) use (&$throwAllEvents): void { $throwAllEvents[] = ['event' => 'named-after-all', 'args' => $args]; }, 10, 9);
    $throwAllCaught = null;
    try { do_action($throwAllHook, 'first'); } catch (Throwable $throwable) { $throwAllCaught = ['class' => $throwable::class, 'message' => $throwable->getMessage()]; }
    $throwAllAfterCatch = ['did_action' => did_action($throwAllHook), 'current_action' => current_action(), 'doing_action' => doing_action(), 'doing_named' => doing_action($throwAllHook)];
    $throwAllFollowupReturn = do_action($throwAllHook, 'follow-up');
    $throwAllAfterFollowup = ['did_action' => did_action($throwAllHook), 'current_action' => current_action(), 'doing_action' => doing_action(), 'doing_named' => doing_action($throwAllHook)];

    $throwNamedEvents = []; $throwNamedHook = 'callable-action-throw-named'; $throwNamedOnce = false;
    add_action($throwNamedHook, static function (...$args) use (&$throwNamedEvents, &$throwNamedOnce, $throwNamedHook): void {
        $throwNamedEvents[] = ['event' => 'named-thrower', 'args' => $args, 'count' => did_action($throwNamedHook), 'current_action' => current_action(), 'doing' => doing_action()];
        if (!$throwNamedOnce) { $throwNamedOnce = true; throw new RuntimeException('action named boom'); }
    }, 10, 9);
    add_action($throwNamedHook, static function (...$args) use (&$throwNamedEvents): void { $throwNamedEvents[] = ['event' => 'named-later-sibling', 'args' => $args]; }, 20, 9);
    $throwNamedCaught = null;
    try { do_action($throwNamedHook, 'first'); } catch (Throwable $throwable) { $throwNamedCaught = ['class' => $throwable::class, 'message' => $throwable->getMessage()]; }
    $throwNamedAfterCatch = ['did_action' => did_action($throwNamedHook), 'current_action' => current_action(), 'doing_action' => doing_action(), 'doing_named' => doing_action($throwNamedHook)];
    $throwNamedFollowupReturn = do_action($throwNamedHook, 'follow-up');
    $throwNamedAfterFollowup = ['did_action' => did_action($throwNamedHook), 'current_action' => current_action(), 'doing_action' => doing_action(), 'doing_named' => doing_action($throwNamedHook)];

    $savedActionGlobals = [];
    foreach (['wp_filter', 'wp_actions', 'wp_filters', 'wp_current_filter'] as $globalName) {
        $savedActionGlobals[$globalName] = [
            'present' => array_key_exists($globalName, $GLOBALS),
            'value' => $GLOBALS[$globalName] ?? null,
        ];
    }
    $globalStateCases = callable_program_action_global_state_cases();
    foreach ($savedActionGlobals as $globalName => $saved) {
        if ($saved['present']) {
            $GLOBALS[$globalName] = $saved['value'];
        } else {
            unset($GLOBALS[$globalName]);
        }
    }
    return ['aliases' => $aliases,
        'do_action_returns' => ['populated' => $doReturn, 'empty_args' => $emptyReturn, 'all_args' => $allReturn, 'all_without_named' => $allNoListenerReturn, 'nested' => $nestedReturn, 'no_listener' => $noListenerReturn],
        'callbacks' => $events, 'after_outer' => $afterOuter, 'empty_args' => $placeholderEvents,
        'all_hook' => ['events' => $allEvents, 'did_action_without_named' => did_action('callable-action-all-without-named-listener')],
        'counter_independence' => ['before_filter' => $sameNameBeforeFilter, 'after_filter' => $sameNameAfterFilter],
        'nested' => ['events' => $nestedEvents, 'did_action' => did_action($nestedHook), 'current_action' => current_action(), 'doing_action' => doing_action()],
        'nested_caught_throw' => ['events' => $nestedCaughtEvents, 'do_action_return' => $nestedCaughtReturn,
            'after' => $nestedCaughtAfter],
        'heap_argument_continuation' => $heapArgumentContinuation,
        'singleton_object_unwrap' => $singletonObjectCases,
        'hook_key_alias' => $hookKeyAliasCases,
        'no_listener_count' => ['before' => $noListenerBefore, 'after' => $noListenerAfter],
        'counter_seed_diagnostics' => $counterSeedDiagnostics,
        'counter_seed_values' => $counterSeedValues,
        'global_state' => $globalStateCases,
        'throw_all' => ['caught' => $throwAllCaught, 'events' => $throwAllEvents, 'after_catch' => $throwAllAfterCatch, 'followup_return' => $throwAllFollowupReturn, 'after_followup' => $throwAllAfterFollowup],
        'throw_named' => ['caught' => $throwNamedCaught, 'events' => $throwNamedEvents, 'after_catch' => $throwNamedAfterCatch, 'followup_return' => $throwNamedFollowupReturn, 'after_followup' => $throwNamedAfterFollowup]];
}

function callable_program_run(): array
{
    $coercions = callable_program_coercion_cases();
    $base = callable_program_run_base();
    $coercions['all_boundary_reentry'] = callable_program_all_boundary_reentry();

    $counterCow = callable_program_counter_cow_cases();
    $counterCow['nested_read_owners']['inner_increment']['control'] = callable_program_counter_cow_nested_read_leaf('control', 102201.5);
    $counterCow['nested_read_owners']['inner_increment']['nested_write'] = callable_program_counter_cow_nested_read_leaf('nested_write', 102202.5);

    $priorityCow = callable_program_priority_cow_cases();

    return [
        'base' => $base,
        'coercions' => $coercions,
        'counter_cow' => $counterCow,
        'priority_cow' => $priorityCow,
        'remove_cow' => callable_program_remove_cow_cases(),
        'hook_unset_reentry' => callable_program_hook_unset_cases(),
    ];
}

function callable_program_run_action_api(): array
{
    $processWarnings = [];
    set_error_handler(static function (int $severity, string $message) use (&$processWarnings): bool {
        $processWarnings[] = ['severity' => $severity, 'message_hex' => bin2hex($message)];
        return true;
    });
    $counterCowCases = callable_program_action_counter_cow_cases();
    $actionApi = callable_program_action_api_cases();
    restore_error_handler();
    $actionApi['counter_cow'] = $counterCowCases;
    $actionApi['process_warnings'] = $processWarnings;
    return ['action_api' => $actionApi];
}
