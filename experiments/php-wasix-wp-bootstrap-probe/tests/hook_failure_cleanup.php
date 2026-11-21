<?php
declare(strict_types=1);

final class RpBootstrapFailure extends RuntimeException
{
}

require __DIR__ . '/../fixtures/hook_adapter.php';

final class RpBootstrapRecordingHookChannel extends RpBootstrapHookChannel
{
    public array $operations = [];

    public function exchange(string $op, array|stdClass $payload): array
    {
        $this->operations[] = $op;
        throw new RuntimeException('injected_transport_failure');
    }
}

$registry = new \RecastPress\V06\Handles\HandleRegistry('1');
$handles = [
    'rp_boot_hook_first' => $registry->retainCallable('rp_boot_hook_first'),
    'rp_boot_hook_second' => $registry->retainCallable('rp_boot_hook_second'),
];

$channelClass = new ReflectionClass(RpBootstrapHookChannel::class);
$channel = $channelClass->newInstanceWithoutConstructor();
$stream = fopen('php://memory', 'r+');
$channelClass->getProperty('stream')->setValue($channel, $stream);

$hookClass = new ReflectionClass(RpBootstrapDispatcherHook::class);
$hook = $hookClass->newInstanceWithoutConstructor();
$hook->callbacks = [
    10 => ['rp_boot_hook_first' => [
        'function' => 'rp_boot_hook_first',
        'accepted_args' => 1,
    ]],
    20 => ['rp_boot_hook_second' => [
        'function' => 'rp_boot_hook_second',
        'accepted_args' => 1,
    ]],
];
$hookClass->getProperty('registry')->setValue($hook, $registry);
$hookClass->getProperty('channel')->setValue($hook, $channel);
$hookClass->getProperty('handles')->setValue($hook, $handles);

try {
    // The channel's nonce is intentionally uninitialized so begin fails inside
    // the adapter's guarded dispatch path before it can write a protocol frame.
    $hook->apply_filters('RP Bootstrap Fixture', ['RP Bootstrap Fixture', 'blogname']);
    throw new RuntimeException('failure_injection_not_triggered');
} catch (RpBootstrapFailure $failure) {
    if ($failure->getMessage() !== 'hook_dispatch_begin') {
        throw new RuntimeException('failure_stage_changed');
    }
}

if (!$registry->isClosed() || $registry->liveCount() !== 0
    || $channelClass->getProperty('closed')->getValue($channel) !== true
    || is_resource($stream)) {
    throw new RuntimeException('dispatch_failure_cleanup_incomplete');
}

// A second abort must remain harmless after the adapter's catch already aborted.
$channel->abort();

// With a valid nonce, begin reaches the transport; force its write to fail and
// assert catch cleanup closes locally without attempting a protocol `close`.
$failureRegistry = new \RecastPress\V06\Handles\HandleRegistry('2');
$failureHandles = [
    'rp_boot_hook_first' => $failureRegistry->retainCallable('rp_boot_hook_first'),
    'rp_boot_hook_second' => $failureRegistry->retainCallable('rp_boot_hook_second'),
];
$failureChannel = (new ReflectionClass(RpBootstrapRecordingHookChannel::class))
    ->newInstanceWithoutConstructor();
$failureStream = fopen('php://memory', 'r+');
$channelClass->getProperty('nonce')->setValue($failureChannel, str_repeat('a', 48));
$channelClass->getProperty('stream')->setValue($failureChannel, $failureStream);
$failureHook = (new ReflectionClass(RpBootstrapDispatcherHook::class))
    ->newInstanceWithoutConstructor();
$failureHook->callbacks = $hook->callbacks;
$hookClass->getProperty('registry')->setValue($failureHook, $failureRegistry);
$hookClass->getProperty('channel')->setValue($failureHook, $failureChannel);
$hookClass->getProperty('handles')->setValue($failureHook, $failureHandles);
try {
    $failureHook->apply_filters('RP Bootstrap Fixture', ['RP Bootstrap Fixture', 'blogname']);
    throw new RuntimeException('transport_failure_not_triggered');
} catch (RpBootstrapFailure $failure) {
    if ($failure->getMessage() !== 'hook_dispatch_begin') {
        throw new RuntimeException('transport_failure_stage_changed');
    }
}
if (!$failureRegistry->isClosed() || $failureRegistry->liveCount() !== 0
    || $channelClass->getProperty('closed')->getValue($failureChannel) !== true
    || is_resource($failureStream) || $failureChannel->operations !== ['begin']) {
    throw new RuntimeException('transport_failure_cleanup_incomplete');
}

echo json_encode([
    'status' => 'passed',
    'failure_stage' => 'hook_dispatch_begin',
    'registry_closed' => $registry->isClosed(),
    'live_handles' => $registry->liveCount(),
    'channel_aborted' => $channelClass->getProperty('closed')->getValue($channel),
    'stream_closed' => !is_resource($stream),
    'transport_failure_stage' => 'hook_dispatch_begin',
    'transport_registry_closed' => $failureRegistry->isClosed(),
    'transport_channel_aborted' => $channelClass->getProperty('closed')->getValue($failureChannel),
    'transport_stream_closed' => !is_resource($failureStream),
    'protocol_operations' => $failureChannel->operations,
    'protocol_close_not_attempted' => !in_array('close', $failureChannel->operations, true),
], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR), "\n";
