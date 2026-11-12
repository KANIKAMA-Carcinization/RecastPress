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

function callable_program_run(): array
{
    $coercions = callable_program_coercion_cases();
    $base = callable_program_run_base();
    $coercions['all_boundary_reentry'] = callable_program_all_boundary_reentry();

    return ['base' => $base, 'coercions' => $coercions];
}
