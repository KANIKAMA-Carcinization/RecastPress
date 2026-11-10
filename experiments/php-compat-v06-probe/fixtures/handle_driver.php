<?php
declare(strict_types=1);

// The mode is deliberately checked before either bridge helper is loaded. The
// ordinary bridge's default mode starts a different exchange.
if ($argc !== 2 || !in_array($argv[1], ['handles', 'handles_abort'], true)) {
    fwrite(STDERR, "usage: handle_driver.php handles|handles_abort\n");
    exit(64);
}
$abortMode = $argv[1] === 'handles_abort';

require_once __DIR__ . '/bridge_driver.php';
require_once __DIR__ . '/handle_registry.php';

use RecastPress\V06\Handles\HandleRegistry;
use RecastPress\V06\Handles\RegistryError;

final class HandleLifetimeFixture
{
    /** @var Closure():void */
    private Closure $onDestroy;

    public function __construct(Closure $onDestroy)
    {
        $this->onDestroy = $onDestroy;
    }

    public function __destruct()
    {
        ($this->onDestroy)();
    }
}

final class HandleCycleFixture
{
    public self $self;
    /** @var Closure():void */
    private Closure $onDestroy;

    public function __construct(Closure $onDestroy)
    {
        $this->onDestroy = $onDestroy;
        $this->self = $this;
    }

    public function __destruct()
    {
        ($this->onDestroy)();
    }
}

function handle_expect(bool $condition, string $reason): void
{
    if (!$condition) {
        throw new RuntimeException($reason);
    }
}

function handle_expect_keys(array $value, array $keys, string $label): void
{
    $actual = array_keys($value);
    sort($actual);
    sort($keys);
    if ($actual !== $keys) {
        throw new RuntimeException($label . '_schema');
    }
}

function handle_expect_registry_error(Closure $operation, string $reason): void
{
    try {
        $operation();
    } catch (RegistryError $error) {
        if ($error->getMessage() === $reason) {
            return;
        }
        throw new RuntimeException('registry_error_' . $error->getMessage() . '_expected_' . $reason);
    }
    throw new RuntimeException('registry_error_missing_' . $reason);
}

function handle_expect_frame(BridgeProtocol $wire, string $kind, ?string $parent): array
{
    $frame = $wire->receive();
    expect_kind($frame, $kind);
    expect_parent_effect($frame, $parent);
    return $frame;
}

function run_handles_probe(bool $abortMode): void
{
    $wire = new BridgeProtocol();
    $request = $abortMode ? '42' : '41';
    $registry = new HandleRegistry($request);
    $lifetimeDestroyed = false;
    $lifetimeReentrantSafe = false;
    $cycleDestroyed = false;
    $closeReentryRejected = false;

    $original = (object)['name' => 'original'];
    $externalAlias = $original;
    $closure = static function () use ($original): string { return $original->name; };
    $exception = new RuntimeException('registry throwable identity');
    $arrayInput = [2, 4];
    $resource = fopen('php://memory', 'r+');
    if (!is_resource($resource)) {
        throw new RuntimeException('resource_fixture_failed');
    }
    $lifetime = new HandleLifetimeFixture(static function () use ($registry, &$lifetimeDestroyed, &$lifetimeReentrantSafe): void {
        $lifetimeDestroyed = true;
        $sentinel = (object)['name' => 'reentrant-sentinel'];
        $sentinelHandle = $registry->retainObject($sentinel);
        $resolved = $registry->resolve($sentinelHandle, 'object');
        $lifetimeReentrantSafe = $resolved === $sentinel && $registry->release($sentinelHandle);
    });
    $lifetimeWeak = WeakReference::create($lifetime);
    $cycle = new HandleCycleFixture(static function () use ($registry, &$cycleDestroyed, &$closeReentryRejected): void {
        $cycleDestroyed = true;
        handle_expect_registry_error(static function () use ($registry): void {
            $registry->retainObject((object)['name' => 'closed-probe']);
        }, 'request_closed');
        $closeReentryRejected = true;
    });
    $cycleWeak = WeakReference::create($cycle);

    $handles = [
        'original' => $registry->retainObject($original),
        'cell' => null,
        'closure' => $registry->retainObject($closure),
        'throwable' => $registry->retainObject($exception),
        'array' => $registry->retainArray($arrayInput),
        'resource' => $registry->retainResource($resource),
        'lifetime' => $registry->retainObject($lifetime),
        'cycle' => $registry->retainObject($cycle),
    ];
    // The object handle is deliberately deduplicated and reference-counted.
    $sameOriginalHandle = $registry->retainObject($original);
    handle_expect($sameOriginalHandle === $handles['original'], 'original_handle_not_deduplicated');
    unset($sameOriginalHandle);
    $cell = $original;
    $handles['cell'] = $registry->retainReference($cell);
    $arrayInput[0] = 9;
    unset($lifetime, $cycle);
    $heldClosure = $registry->resolve($handles['closure'], 'closure');
    handle_expect($heldClosure === $closure, 'registry_closure_identity');

    $view = [
        'original_name' => $externalAlias->name,
        'cell_name' => $cell->name,
        'closure_name' => $closure(),
        'array_value' => $registry->resolve($handles['array'], 'array'),
        'resource_type' => get_resource_type($resource),
        'live' => $registry->liveCount(),
        'alias_same' => $externalAlias === $registry->resolve($handles['original'], 'object'),
    ];
    handle_expect($view === [
        'original_name' => 'original', 'cell_name' => 'original', 'closure_name' => 'original',
        'array_value' => [2, 4], 'resource_type' => 'stream', 'live' => 8, 'alias_same' => true,
    ], 'initial_registry_view');
    $wire->send('start_root', [
        'operation' => 'handles_probe',
        'request_generation' => $request,
        'handles' => $handles,
        'view' => $view,
    ]);

    $rootInvoke = handle_expect_frame($wire, 'invoke_php', null);
    expect_payload_keys($rootInvoke, ['callback', 'effect_id'], 'handles_root_invoke');
    handle_expect($rootInvoke['payload'] === ['callback' => 'registry_root_callback', 'effect_id' => 'e-root'], 'handles_root_callback');

    // Root callback: a non-final release, a real reference-cell rebind, and
    // three independently checked forged capabilities.
    handle_expect(!$registry->release($handles['original']), 'original_first_release_was_final');
    handle_expect($lifetimeWeak->get() instanceof HandleLifetimeFixture, 'lifetime_not_retained');
    $replacement = (object)['name' => 'replacement'];
    $registry->assignReference($handles['cell'], $replacement);
    $replacementHandle = $registry->retainObject($replacement);
    $wrongRequest = $handles['original'];
    $wrongKind = $handles['original'];
    $wrongToken = $handles['original'];
    handle_expect(is_array($wrongRequest) && is_array($wrongKind) && is_array($wrongToken), 'handle_not_structured');
    $wrongRequest['request'] = '99';
    // The valid descriptor remains intact; only its requested kind differs.
    handle_expect_registry_error(static function () use ($registry, $wrongRequest): void { $registry->resolve($wrongRequest, 'object'); }, 'wrong_request');
    handle_expect_registry_error(static function () use ($registry, $wrongKind): void { $registry->resolve($wrongKind, 'throwable'); }, 'wrong_kind');
    $firstHex = $wrongToken['token'][0];
    $wrongToken['token'][0] = $firstHex === '0' ? '1' : '0';
    handle_expect_registry_error(static function () use ($registry, $wrongToken): void { $registry->resolve($wrongToken, 'object'); }, 'invalid_handle');
    $rootView = [
        'alias_name' => $externalAlias->name,
        'cell_name' => $cell->name,
        'closure_name' => $closure(),
        'retained_original_live' => $lifetimeWeak->get() instanceof HandleLifetimeFixture,
        'reference_rebound' => $cell === $replacement,
        'forged_rejections' => 3,
        'live' => $registry->liveCount(),
    ];
    handle_expect($rootView === [
        'alias_name' => 'original', 'cell_name' => 'replacement', 'closure_name' => 'original',
        'retained_original_live' => true, 'reference_rebound' => true, 'forged_rejections' => 3, 'live' => 9,
    ], 'root_registry_view');
    $wire->send('call_core', [
        'operation' => 'handles_child', 'replacement' => $replacementHandle,
        'view' => $rootView,
    ], 'e-root');

    $nestedInvoke = handle_expect_frame($wire, 'invoke_php', null);
    expect_payload_keys($nestedInvoke, ['callback', 'effect_id'], 'handles_nested_invoke');
    handle_expect($nestedInvoke['payload'] === ['callback' => 'registry_nested_callback', 'effect_id' => 'e-child'], 'handles_nested_callback');

    // Final release invalidates the old slot before the lifetime destructor
    // re-enters and allocates/releases a separate sentinel handle.
    handle_expect($registry->release($handles['original']), 'original_final_release_not_final');
    handle_expect($externalAlias === $original && $closure() === 'original', 'external_original_alias_lost');
    handle_expect($registry->release($handles['lifetime']), 'lifetime_release_not_final');
    handle_expect($lifetimeDestroyed && $lifetimeReentrantSafe && $lifetimeWeak->get() === null, 'lifetime_reentry_or_drop_failed');
    $reusedObject = (object)['name' => 'reused'];
    $reusedHandle = $registry->retainObject($reusedObject);
    $slotReused = $reusedHandle['slot'] === $handles['original']['slot']
        && $reusedHandle['generation'] > $handles['original']['generation'];
    handle_expect($slotReused, 'minimum_slot_reuse_or_generation');
    handle_expect_registry_error(static function () use ($registry, $handles): void { $registry->resolve($handles['original'], 'object'); }, 'invalid_handle');
    $wrongGeneration = $reusedHandle;
    $wrongGeneration['generation'] = $handles['original']['generation'];
    handle_expect_registry_error(static function () use ($registry, $wrongGeneration): void { $registry->resolve($wrongGeneration, 'object'); }, 'invalid_handle');
    $wrongOldToken = $reusedHandle;
    $firstReusedHex = $wrongOldToken['token'][0];
    $wrongOldToken['token'][0] = $firstReusedHex === '0' ? '1' : '0';
    handle_expect_registry_error(static function () use ($registry, $wrongOldToken): void { $registry->resolve($wrongOldToken, 'object'); }, 'invalid_handle');
    $nestedView = [
        'alias_name' => $externalAlias->name,
        'cell_name' => $cell->name,
        'closure_name' => $closure(),
        'slot_reused' => $slotReused,
        'old_generation_rejected' => true,
        'lifetime_destroyed' => $lifetimeDestroyed,
        'reentrant_release_safe' => $lifetimeReentrantSafe,
        'stale_rejections' => 3,
        'live' => $registry->liveCount(),
    ];
    handle_expect($nestedView === [
        'alias_name' => 'original', 'cell_name' => 'replacement', 'closure_name' => 'original',
        'slot_reused' => true, 'old_generation_rejected' => true, 'lifetime_destroyed' => true,
        'reentrant_release_safe' => true, 'stale_rejections' => 3, 'live' => 8,
    ], 'nested_registry_view');
    $wire->send('resume', ['effect_id' => 'e-child', 'result' => [
        'released' => ['original' => $handles['original'], 'lifetime' => $handles['lifetime']],
        'reused' => $reusedHandle,
        'view' => $nestedView,
    ]], 'e-root');

    $childReturn = handle_expect_frame($wire, 'return', 'e-root');
    expect_payload_keys($childReturn, ['operation', 'expired_rejected', 'slot_reused'], 'handles_child_return');
    handle_expect($childReturn['payload']['operation'] === 'handles_child', 'handles_child_return_operation');
    $childReturnPayload = $childReturn['payload'];
    handle_expect(($childReturnPayload['expired_rejected'] ?? null) === true
        && ($childReturnPayload['slot_reused'] ?? null) === true, 'handles_child_return_values');

    $arrayValue = $registry->resolve($handles['array'], 'array');
    $sameThrowable = $registry->resolve($handles['throwable'], 'throwable') === $exception;
    $resourceAlive = is_resource($registry->resolve($handles['resource'], 'resource'));
    handle_expect($arrayValue === [2, 4] && $sameThrowable && $resourceAlive, 'registry_payload_identity');
    $wire->send('resume', ['effect_id' => 'e-root', 'result' => ['view' => [
        'alias_name' => $externalAlias->name,
        'cell_name' => $cell->name,
        'closure_name' => $closure(),
        'throwable_same' => $sameThrowable,
        'array_value' => $arrayValue,
        'resource_alive' => $resourceAlive,
        'live' => $registry->liveCount(),
    ]]]);

    $finalFrame = $wire->receive();
    expect_kind($finalFrame, $abortMode ? 'abort' : 'return');
    expect_parent_effect($finalFrame, null);
    expect_payload_keys($finalFrame, ['operation', 'status'], 'handles_final_return');
    handle_expect($finalFrame['payload'] === [
        'operation' => 'handles_probe', 'status' => $abortMode ? 'aborted' : 'finished',
    ], 'handles_final_return_values');

    // A tiny registry independently exercises live and issuance bounds, plus
    // registry/request binding and post-close API rejection.
    $limits = new HandleRegistry($request, 1, 2);
    $limitFirst = $limits->retainObject((object)['name' => 'first']);
    handle_expect_registry_error(static function () use ($limits): void { $limits->retainObject((object)['name' => 'overflow']); }, 'handle_limit');
    handle_expect($limits->resolve($limitFirst, 'object')->name === 'first', 'live_limit_corrupted_existing');
    handle_expect($limits->release($limitFirst), 'limit_first_release');
    $limitSecond = $limits->retainObject((object)['name' => 'second']);
    handle_expect($limits->release($limitSecond), 'limit_second_release');
    handle_expect_registry_error(static function () use ($limits): void { $limits->retainObject((object)['name' => 'third']); }, 'issuance_limit');
    handle_expect_registry_error(static function () use ($registry, $handles): void { $registry->resolve($handles['original'], 'object'); }, 'invalid_handle');
    $otherRegistry = new HandleRegistry($request);
    for ($slot = 0; $slot < $handles['closure']['slot']; $slot++) {
        $otherRegistry->retainObject((object)['name' => 'filler-' . $slot]);
    }
    $otherClosureHandle = $otherRegistry->retainObject(static function (): void {});
    handle_expect($otherClosureHandle['slot'] === $handles['closure']['slot']
        && $otherClosureHandle['generation'] === $handles['closure']['generation'], 'cross_registry_slot_generation_mismatch');
    handle_expect_registry_error(static function () use ($otherRegistry, $handles): void { $otherRegistry->resolve($handles['closure'], 'closure'); }, 'invalid_handle');
    $otherRegistry->close();
    $limits->close();
    handle_expect_registry_error(static function () use ($limits): void { $limits->retainObject((object)[]); }, 'request_closed');
    try {
        clone $registry;
        throw new RuntimeException('registry_clone_was_accepted');
    } catch (Error $error) {
        handle_expect(str_contains($error->getMessage(), '__clone'), 'unexpected_clone_error');
    }
    handle_expect_registry_error(static function () use ($registry): void {
        serialize($registry);
    }, 'non_transferable');

    // Destructor exceptions propagate after registry metadata is invalidated.
    $releaseFailure = new HandleRegistry('91');
    $failingHandle = $releaseFailure->retainObject(new HandleLifetimeFixture(static function (): void {
        throw new RuntimeException('release_destructor');
    }));
    try {
        $releaseFailure->release($failingHandle);
        throw new RuntimeException('release_destructor_did_not_throw');
    } catch (RuntimeException $error) {
        handle_expect($error->getMessage() === 'release_destructor', 'unexpected_release_destructor_error');
    }
    handle_expect($releaseFailure->liveCount() === 0, 'failed_release_left_live_slot');
    handle_expect_registry_error(static function () use ($releaseFailure, $failingHandle): void {
        $releaseFailure->resolve($failingHandle, 'object');
    }, 'invalid_handle');
    $afterFailure = $releaseFailure->retainObject((object)['name' => 'after-failure']);
    handle_expect($afterFailure['slot'] === $failingHandle['slot']
        && $afterFailure['generation'] > $failingHandle['generation'], 'failed_release_slot_reuse');
    $releaseFailure->close();

    $closeFailure = new HandleRegistry('92');
    $closingHandle = $closeFailure->retainObject(new HandleLifetimeFixture(static function (): void {
        throw new RuntimeException('close_destructor');
    }));
    try {
        $closeFailure->close();
        throw new RuntimeException('close_destructor_did_not_throw');
    } catch (RuntimeException $error) {
        handle_expect($error->getMessage() === 'close_destructor', 'unexpected_close_destructor_error');
    }
    handle_expect($closeFailure->isClosed() && $closeFailure->liveCount() === 0, 'failed_close_state');
    handle_expect_registry_error(static function () use ($closeFailure, $closingHandle): void {
        $closeFailure->resolve($closingHandle, 'object');
    }, 'request_closed');
    $closeFailure->close();

    $registry->close();
    handle_expect($registry->isClosed() && $registry->liveCount() === 0, 'registry_close_state');
    foreach ($handles as $name => $handle) {
        handle_expect_registry_error(static function () use ($registry, $handle): void { $registry->resolve($handle, match ($handle['kind']) {
            'object' => 'object', 'reference_cell' => 'reference_cell', 'closure' => 'closure',
            'throwable' => 'throwable', 'array' => 'array', 'resource' => 'resource', default => 'object',
        }); }, 'request_closed');
    }
    handle_expect_registry_error(static function () use ($registry, $replacementHandle): void { $registry->resolve($replacementHandle, 'object'); }, 'request_closed');
    handle_expect_registry_error(static function () use ($registry, $reusedHandle): void { $registry->resolve($reusedHandle, 'object'); }, 'request_closed');
    $arrayAfterClose = $arrayValue === [2, 4];
    $externalAfterClose = $externalAlias->name === 'original';
    $cellAfterClose = $cell->name === 'replacement';
    $closureAfterClose = $heldClosure === $closure && $heldClosure() === 'original';
    $resourceAfterClose = is_resource($resource);
    unset($original, $replacement);
    gc_collect_cycles();
    $cycleCollected = $cycleWeak->get() === null && $cycleDestroyed;
    if (is_resource($resource)) {
        fclose($resource);
    }

    $observation = [
        'registry_closed' => $registry->isClosed(),
        'all_closed_rejected' => true,
        'external_alias_preserved' => $externalAfterClose,
        'reference_rebound' => $cellAfterClose,
        'closure_same' => $closureAfterClose,
        'throwable_same' => $sameThrowable,
        'array_copy_preserved' => $arrayAfterClose,
        'resource_external_alive' => $resourceAfterClose,
        'slot_reused' => $slotReused,
        'stale_rejected' => true,
        'lifetime_destroyed' => $lifetimeDestroyed,
        'reentrant_release_safe' => $lifetimeReentrantSafe,
        'cycle_collected' => $cycleCollected,
        'close_reentry_rejected' => $closeReentryRejected,
        'limits_preserved' => true,
        'destructor_failure_safe' => true,
        'abort_cleanup' => $abortMode,
        'live_after_close' => $registry->liveCount(),
    ];
    $expectedObservation = [
        'registry_closed' => true, 'all_closed_rejected' => true, 'external_alias_preserved' => true,
        'reference_rebound' => true, 'closure_same' => true, 'throwable_same' => true,
        'array_copy_preserved' => true, 'resource_external_alive' => true, 'slot_reused' => true,
        'stale_rejected' => true, 'lifetime_destroyed' => true, 'reentrant_release_safe' => true,
        'cycle_collected' => true, 'close_reentry_rejected' => true, 'limits_preserved' => true,
        'destructor_failure_safe' => true,
        'abort_cleanup' => $abortMode, 'live_after_close' => 0,
    ];
    handle_expect($observation === $expectedObservation, 'final_registry_observation');
    $wire->send('request_finished', [
        'status' => $abortMode ? 'aborted' : 'ok',
        'php_observation' => $observation,
        'transport' => $wire->stats(),
    ]);
}

try {
    run_handles_probe($abortMode);
} catch (Throwable $error) {
    fail_bridge($error);
}
