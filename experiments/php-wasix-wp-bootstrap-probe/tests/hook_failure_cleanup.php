<?php
declare(strict_types=1);

final class RpBootstrapFailure extends RuntimeException
{
}

require __DIR__ . '/../fixtures/hook_adapter.php';
require dirname(__DIR__, 3) . '/target/wp-bootstrap-probe/site/wp-includes/plugin.php';

final class RpBootstrapRecordingHookChannel extends RpBootstrapHookChannel
{
    public array $operations = [];

    public function exchange(string $op, array|stdClass $payload): array
    {
        $this->operations[] = $op;
        throw new RuntimeException('injected_transport_failure');
    }
}

function rp_test_failed_dispatch(
    RpBootstrapHookChannel $channel,
    \RecastPress\V06\Handles\HandleRegistry $registry,
): void {
    remove_all_filters(RP_BOOT_HOOK_NAME);
    add_filter(RP_BOOT_HOOK_NAME, 'rp_boot_hook_first', 10, 1);
    add_filter(RP_BOOT_HOOK_NAME, 'rp_boot_hook_second', 20, 1);
    $hook = $GLOBALS['wp_filter'][RP_BOOT_HOOK_NAME];
    $GLOBALS['rp_boot_hook_object'] = $hook;
    $GLOBALS['rp_boot_hook_registry'] = $registry;
    $GLOBALS['rp_boot_hook_channel'] = $channel;
    $GLOBALS['rp_boot_hook_dispatched'] = false;
    $GLOBALS['rp_boot_hook_exported_handles'] = [];
    $syncAttempted = false;
    try {
        recastpress_wp_dispatch_selected_hook(
            RP_BOOT_HOOK_NAME,
            $hook,
            'RP Bootstrap Fixture',
            ['RP Bootstrap Fixture', 'blogname'],
            0,
            static function () use (&$syncAttempted): bool {
                $syncAttempted = true;
                return false;
            },
        );
        throw new RuntimeException('failure_injection_not_triggered');
    } catch (RpBootstrapFailure $failure) {
        if ($failure->getMessage() !== 'hook_dispatch_begin' || $syncAttempted
            || array_keys($GLOBALS['rp_boot_hook_exported_handles']) !== [
                'rp_boot_hook_first', 'rp_boot_hook_second',
            ]) {
            throw new RuntimeException('failure_stage_changed');
        }
    }
}

$registry = new \RecastPress\V06\Handles\HandleRegistry(RP_BOOT_HOOK_HANDLE_REQUEST);
$channelClass = new ReflectionClass(RpBootstrapHookChannel::class);
$channel = $channelClass->newInstanceWithoutConstructor();
$stream = fopen('php://memory', 'r+');
$channelClass->getProperty('stream')->setValue($channel, $stream);

// The nonce is intentionally uninitialized, so begin fails before writing.
rp_test_failed_dispatch($channel, $registry);

if (!$registry->isClosed() || $registry->liveCount() !== 0
    || $channelClass->getProperty('closed')->getValue($channel) !== true
    || is_resource($stream)) {
    throw new RuntimeException('dispatch_failure_cleanup_incomplete');
}

// A second abort must remain harmless after the adapter's catch already aborted.
$channel->abort();

// With a valid nonce, begin reaches the transport; force its write to fail and
// assert catch cleanup closes locally without attempting a protocol `close`.
$failureRegistry = new \RecastPress\V06\Handles\HandleRegistry(RP_BOOT_HOOK_HANDLE_REQUEST);
$failureChannel = (new ReflectionClass(RpBootstrapRecordingHookChannel::class))
    ->newInstanceWithoutConstructor();
$failureStream = fopen('php://memory', 'r+');
$channelClass->getProperty('nonce')->setValue($failureChannel, str_repeat('a', 48));
$channelClass->getProperty('stream')->setValue($failureChannel, $failureStream);
rp_test_failed_dispatch($failureChannel, $failureRegistry);
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
