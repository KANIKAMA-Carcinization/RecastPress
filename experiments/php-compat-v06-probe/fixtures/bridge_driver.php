<?php
declare(strict_types=1);

require_once __DIR__ . '/strict_json.php';
use function RecastPress\V06\StrictJson\rp_decode_unique_json;

/** Interactive JSONL PHP side of the v0.6 continuation probe. */
const RP_MAX_FRAME = 1_048_576;
const RP_MAX_TOTAL = 33_554_432;

final class BridgeState {
    public object $item;
    public array $settings;
    public function __construct() { $this->item = (object)['name' => 'root-before']; $this->settings = ['flag' => false]; }
}

final class BridgeProtocol {
    private int $seq = 0;
    private int $receiveSeq = 0;
    private int $total = 0;
    private int $received = 0;
    private int $sent = 0;

    public function send(string $kind, array $payload, ?string $parentEffect = null): void {
        $frame = ['v' => 1, 'request' => 'surface-probe-1', 'seq' => $this->seq++, 'kind' => $kind];
        if ($parentEffect !== null) $frame['parent_effect'] = $parentEffect;
        $frame['payload'] = $payload;
        $line = json_encode($frame, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR) . "\n";
        $size = strlen($line);
        if ($size > RP_MAX_FRAME || $this->total + $size > RP_MAX_TOTAL) throw new RuntimeException('transport_limit');
        $written = 0;
        while ($written < $size) {
            $n = fwrite(STDOUT, substr($line, $written));
            if ($n === false || $n === 0) throw new RuntimeException('transport_write_failed');
            $written += $n;
        }
        fflush(STDOUT);
        $this->total += $size; $this->sent++;
    }

    public function receive(): array {
        $line = fgets(STDIN, RP_MAX_FRAME + 2);
        if ($line === false || !str_ends_with($line, "\n")) throw new RuntimeException('transport_eof_or_oversize');
        $size = strlen($line);
        if ($size > RP_MAX_FRAME || $this->total + $size > RP_MAX_TOTAL) throw new RuntimeException('transport_limit');
        $this->total += $size; $this->received++;
        $frame = rp_decode_unique_json($line);
        if (!is_array($frame) || array_is_list($frame)) throw new RuntimeException('frame_not_object');
        $keys = ['v', 'request', 'seq', 'kind', 'payload'];
        if (isset($frame['parent_effect'])) $keys[] = 'parent_effect';
        if (array_diff(array_keys($frame), $keys) || array_diff($keys, array_keys($frame))) throw new RuntimeException('frame_schema');
        if ($frame['v'] !== 1 || $frame['request'] !== 'surface-probe-1' || $frame['seq'] !== $this->receiveSeq++) throw new RuntimeException('frame_correlation');
        if (!is_string($frame['kind']) || !is_array($frame['payload'])) throw new RuntimeException('frame_types');
        return $frame;
    }

    public function stats(): array { return ['frames_sent' => $this->sent, 'frames_received' => $this->received, 'bytes_total' => $this->total]; }
}

function fail_bridge(Throwable $e): never {
    fwrite(STDERR, 'bridge fixture failed: ' . $e::class . ': ' . $e->getMessage() . "\n");
    exit(2);
}

function expect_kind(array $frame, string $kind): void {
    if ($frame['kind'] !== $kind) throw new RuntimeException('expected_' . $kind . '_got_' . $frame['kind']);
}

function expect_payload_keys(array $frame, array $keys, string $label): void {
    if (!isset($frame['payload']) || !is_array($frame['payload'])) throw new RuntimeException($label . '_payload_not_object');
    $actual = array_keys($frame['payload']); sort($actual);
    sort($keys);
    if ($actual !== $keys) throw new RuntimeException($label . '_payload_schema');
}

function expect_parent_effect(array $frame, ?string $expected): void {
    if ($expected === null) {
        if (array_key_exists('parent_effect', $frame)) throw new RuntimeException('unexpected_parent_effect');
    } elseif (($frame['parent_effect'] ?? null) !== $expected) {
        throw new RuntimeException('parent_effect_correlation');
    }
}

function run_throwable_probe(): void {
    $wire = new BridgeProtocol();
    $throwableId = 'php-throwable-1';
    $throwables = [];
    $originalThrowable = null;
    $caughtThrowable = null;
    $nestedCalls = 0;
    $trace = [];

    $wire->send('start_root', ['operation' => 'throwable_probe']);
    $rootInvoke = $wire->receive();
    expect_kind($rootInvoke, 'invoke_php');
    expect_parent_effect($rootInvoke, null);
    expect_payload_keys($rootInvoke, ['callback', 'effect_id'], 'throwable_root_invoke');
    if (($rootInvoke['payload']['callback'] ?? null) !== 'probe_throwable_root' || ($rootInvoke['payload']['effect_id'] ?? null) !== 'e-root') {
        throw new RuntimeException('throwable_root_callback_not_allowlisted');
    }

    $rootCallback = static function () use ($wire, $throwableId, &$throwables, &$originalThrowable, &$nestedCalls, &$trace): never {
        $trace[] = 'root:enter';
        $wire->send('call_core', ['operation' => 'raise_nested'], 'e-root');
        while (true) {
            $frame = $wire->receive();
            if ($frame['kind'] === 'invoke_php') {
                expect_parent_effect($frame, null);
                expect_payload_keys($frame, ['callback', 'effect_id'], 'throwable_nested_invoke');
                if (($frame['payload']['callback'] ?? null) !== 'throwing_nested_callback' || ($frame['payload']['effect_id'] ?? null) !== 'e-nested') {
                    throw new RuntimeException('throwable_nested_callback_not_allowlisted');
                }
                $nestedCalls++;
                try {
                    $trace[] = 'nested:enter';
                    $originalThrowable = new RuntimeException('nested fixture throwable');
                    throw $originalThrowable;
                } catch (Throwable $error) {
                    if ($error !== $originalThrowable) throw new RuntimeException('throwable_identity_changed_in_nested_callback');
                    $throwables[$throwableId] = $error;
                    $trace[] = 'nested:transport_catch';
                    $wire->send('resume', ['effect_id' => 'e-nested', 'completion' => [
                        'kind' => 'php_throwable', 'handle' => $throwableId,
                        'class' => $error::class, 'message' => $error->getMessage(),
                    ]], 'e-root');
                } finally {
                    $trace[] = 'nested:finally';
                }
                continue;
            }
            if ($frame['kind'] === 'return') {
                expect_parent_effect($frame, 'e-root');
                expect_payload_keys($frame, ['operation', 'completion'], 'throwable_child_return');
                if (($frame['payload']['operation'] ?? null) !== 'raise_nested' || !is_array($frame['payload']['completion'] ?? null)) {
                    throw new RuntimeException('throwable_child_return_shape');
                }
                $completion = $frame['payload']['completion'];
                $completionKeys = array_keys($completion); sort($completionKeys);
                $expectedKeys = ['kind', 'handle', 'class', 'message']; sort($expectedKeys);
                if ($completionKeys !== $expectedKeys || ($completion['kind'] ?? null) !== 'php_throwable'
                    || ($completion['handle'] ?? null) !== $throwableId
                    || ($completion['class'] ?? null) !== RuntimeException::class
                    || ($completion['message'] ?? null) !== 'nested fixture throwable'
                    || !isset($throwables[$throwableId])) {
                    throw new RuntimeException('throwable_child_return_handle_mismatch');
                }
                $trace[] = 'child:rethrow';
                // Re-throw the original PHP object held in this request-local table.
                // Rust returned only its opaque fixture handle; it did not construct it.
                throw $throwables[$throwableId];
            }
            throw new RuntimeException('unexpected_throwable_child_frame');
        }
    };

    try {
        $rootCallback();
        throw new RuntimeException('throwable_probe_returned_normally');
    } catch (Throwable $error) {
        $caughtThrowable = $error;
        $trace[] = 'root:catch';
    } finally {
        $trace[] = 'root:finally';
    }

    if ($caughtThrowable !== $originalThrowable) throw new RuntimeException('throwable_identity_changed_at_root');
    $abort = $wire->receive();
    expect_kind($abort, 'abort');
    expect_parent_effect($abort, null);
    expect_payload_keys($abort, [
        'reason', 'handle', 'child_recovered', 'child_depth_before_abort', 'depth_after_abort',
        'double_resume_rejected', 'resume_after_abort_rejected', 'finish_request_after_abort_rejected',
    ], 'throwable_abort');
    $abortPayload = $abort['payload'];
    if ($abortPayload['reason'] !== 'php_throwable' || $abortPayload['handle'] !== $throwableId
        || $abortPayload['child_recovered'] !== true || $abortPayload['child_depth_before_abort'] !== 1
        || $abortPayload['depth_after_abort'] !== 0 || $abortPayload['double_resume_rejected'] !== true
        || $abortPayload['resume_after_abort_rejected'] !== true
        || $abortPayload['finish_request_after_abort_rejected'] !== true) {
        throw new RuntimeException('throwable_abort_state_mismatch');
    }
    $expectedTrace = ['root:enter', 'nested:enter', 'nested:transport_catch', 'nested:finally', 'child:rethrow', 'root:catch', 'root:finally'];
    if ($trace !== $expectedTrace || $nestedCalls !== 1) throw new RuntimeException('throwable_replay_or_order_mismatch');

    $wire->send('request_finished', ['status' => 'throwable', 'php_observation' => [
        'caught_same_object' => $caughtThrowable === $originalThrowable,
        'throwable_class' => $caughtThrowable::class,
        'throwable_message' => $caughtThrowable->getMessage(),
        'trace' => $trace,
        'nested_callback_calls' => $nestedCalls,
        'abort_reason' => $abortPayload['reason'],
        'child_recovered' => $abortPayload['child_recovered'],
        'child_depth_before_abort' => $abortPayload['child_depth_before_abort'],
        'depth_after_abort' => $abortPayload['depth_after_abort'],
        'double_resume_rejected' => $abortPayload['double_resume_rejected'],
        'resume_after_abort_rejected' => $abortPayload['resume_after_abort_rejected'],
        'finish_request_after_abort_rejected' => $abortPayload['finish_request_after_abort_rejected'],
    ], 'transport' => $wire->stats()]);
}

if (($argv[1] ?? 'surface') === 'throwable') {
    try { run_throwable_probe(); } catch (Throwable $e) { fail_bridge($e); }
} elseif (($argv[1] ?? 'surface') === 'surface') try {
    $wire = new BridgeProtocol();
    $state = new BridgeState();
    $alias = $state->item;
    $GLOBALS['rp_bridge_item'] = $state->item;
    $wire->send('start_root', ['operation' => 'surface_probe']);
    $rootEffect = $wire->receive();
    expect_kind($rootEffect, 'invoke_php');
    expect_parent_effect($rootEffect, null);
    expect_payload_keys($rootEffect, ['callback', 'effect_id'], 'root_invoke');
    if (($rootEffect['payload']['callback'] ?? null) !== 'probe_root_callback' || ($rootEffect['payload']['effect_id'] ?? null) !== 'e-root') throw new RuntimeException('root_callback_not_allowlisted');
    $effect = $rootEffect['payload']['effect_id'];

    // This is an ordinary registered PHP callback. It mutates the live object before calling Rust.
    $state->item->name = 'changed-before-child';
    $beforeChildName = $state->item->name;
    $state->settings['flag'] = true;
    $GLOBALS['rp_bridge_item'] = (object)['name' => 'global-replacement'];

    // Nested synchronous exchange. Rust must observe the current PHP heap at this exact point.
    $wire->send('call_core', ['operation' => 'observe_public', 'view' => [
        'public_name' => $state->item->name,
        'setting_flag' => $state->settings['flag'],
        'alias_same_object' => $alias === $state->item,
        'global_replaced' => $GLOBALS['rp_bridge_item'] !== $alias,
    ]], $effect);

    $childReturn = null;
    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'invoke_php') {
            expect_parent_effect($frame, null);
            expect_payload_keys($frame, ['callback', 'effect_id'], 'nested_invoke');
            $nestedEffect = $frame['payload']['effect_id'] ?? null;
            if (($frame['payload']['callback'] ?? null) !== 'nested_probe_callback' || $nestedEffect !== 'e-nested') throw new RuntimeException('nested_callback_not_allowlisted');
            // Model a nested PHP hook with its own live read; no arbitrary source execution is supported.
            $state->item->name .= ':nested';
            $wire->send('resume', ['effect_id' => $nestedEffect, 'result' => [
                'public_name' => $state->item->name,
                'alias_same_object' => $alias === $state->item,
            ]], $effect);
            continue;
        }
        if ($frame['kind'] === 'return') {
            expect_parent_effect($frame, $effect);
            expect_payload_keys($frame, ['operation', 'observation'], 'child_return');
            if (($frame['payload']['operation'] ?? null) !== 'observe_public') throw new RuntimeException('child_return_operation');
            $childKeys = ['public_name_before', 'public_name_after', 'name_growth_bytes', 'setting_flag', 'alias_same_object'];
            $actualChildKeys = array_keys($frame['payload']['observation']); sort($actualChildKeys); sort($childKeys);
            if ($actualChildKeys !== $childKeys) throw new RuntimeException('child_return_observation_schema');
            $childReturn = $frame['payload']; break;
        }
        throw new RuntimeException('unexpected_child_exchange_frame');
    }

    // Re-read after the child finished; outer callback resumes with the same PHP heap.
    $afterChild = ['public_name' => $state->item->name, 'setting_flag' => $state->settings['flag'], 'alias_same_object' => $alias === $state->item];
    $nameGrowthBytes = strlen($afterChild['public_name']) - strlen($beforeChildName);
    $childObservation = $childReturn['observation'] ?? null;
    if (!is_array($childObservation)
        || ($childObservation['public_name_before'] ?? null) !== $beforeChildName
        || ($childObservation['public_name_after'] ?? null) !== $afterChild['public_name']
        || ($childObservation['name_growth_bytes'] ?? null) !== $nameGrowthBytes
        || ($childObservation['setting_flag'] ?? null) !== $afterChild['setting_flag']
        || ($childObservation['alias_same_object'] ?? null) !== $afterChild['alias_same_object']) {
        throw new RuntimeException('rust_child_observation_did_not_match_live_php_heap');
    }
    $phpRootObservation = [
        'public_name_before' => $beforeChildName,
        'public_name_after' => $afterChild['public_name'],
        'name_growth_bytes' => $nameGrowthBytes,
        'setting_flag' => $afterChild['setting_flag'],
        'alias_same_object' => $afterChild['alias_same_object'],
        'global_name' => $GLOBALS['rp_bridge_item']->name,
    ];
    $wire->send('resume', ['effect_id' => $effect, 'result' => [
        'child_return' => $childReturn,
        'after_child' => $afterChild,
        'global_name' => $GLOBALS['rp_bridge_item']->name,
    ]]);

    $rootReturn = $wire->receive();
    expect_kind($rootReturn, 'return');
    expect_parent_effect($rootReturn, null);
    expect_payload_keys($rootReturn, ['operation', 'observation'], 'root_return');
    if (($rootReturn['payload']['operation'] ?? null) !== 'surface_probe') throw new RuntimeException('wrong_root_return');
    $rootObservation = $rootReturn['payload']['observation'] ?? null;
    if (!is_array($rootObservation)) throw new RuntimeException('root_return_observation_not_object');
    $rootKeys = array_keys($rootObservation); sort($rootKeys);
    $expectedRootKeys = ['public_name_before', 'public_name_after', 'name_growth_bytes', 'setting_flag', 'alias_same_object', 'global_name']; sort($expectedRootKeys);
    if ($rootKeys !== $expectedRootKeys) throw new RuntimeException('root_return_observation_schema');
    if (!is_array($rootObservation)
        || ($rootObservation['public_name_before'] ?? null) !== $phpRootObservation['public_name_before']
        || ($rootObservation['public_name_after'] ?? null) !== $phpRootObservation['public_name_after']
        || ($rootObservation['name_growth_bytes'] ?? null) !== $phpRootObservation['name_growth_bytes']
        || ($rootObservation['setting_flag'] ?? null) !== $phpRootObservation['setting_flag']
        || ($rootObservation['alias_same_object'] ?? null) !== $phpRootObservation['alias_same_object']
        || ($rootObservation['global_name'] ?? null) !== $phpRootObservation['global_name']) {
        throw new RuntimeException('rust_root_observation_did_not_match_live_php_heap');
    }
    $wire->send('request_finished', ['status' => 'ok', 'php_observation' => [
        'same_object_alias' => $alias === $state->item,
        'public_name_after_resume' => $state->item->name,
        'global_replacement' => $GLOBALS['rp_bridge_item']->name,
        'rust_child_observation' => $childObservation,
        'rust_root_observation' => $rootObservation,
    ], 'transport' => $wire->stats()]);
} catch (Throwable $e) { fail_bridge($e); }
