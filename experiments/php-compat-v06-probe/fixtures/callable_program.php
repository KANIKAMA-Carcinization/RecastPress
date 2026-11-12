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
function callable_program_run(): array
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
