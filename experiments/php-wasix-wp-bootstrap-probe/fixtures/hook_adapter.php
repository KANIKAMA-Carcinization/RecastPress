<?php
declare(strict_types=1);

use RecastPress\V06\Handles\HandleRegistry;
use RecastPress\V06\Handles\RegistryError;

require_once __DIR__ . '/hook_handle_registry.php';

const RP_BOOT_HOOK_NAME = 'option_blogname';
const RP_BOOT_HOOK_TARGET = 'rp-option-blogname-1';
const RP_BOOT_HOOK_HANDLE_REQUEST = '1';
const RP_BOOT_HOOK_MAX_FRAME = 1048576;
const RP_BOOT_HOOK_MAX_TOTAL = 4194304;

class RpBootstrapHookChannel
{
    private mixed $stream;
    private int $sendSeq = 1;
    private int $receiveSeq = 1;
    private int $totalBytes = 0;
    private bool $closed = false;

    public function __construct(private readonly string $nonce)
    {
        if (preg_match('/\A[0-9a-f]{48}\z/D', $nonce) !== 1) {
            throw new RuntimeException('hook_nonce_shape');
        }
        $stream = fopen('/rp-wp-hooks/channel', 'r+b');
        if (!is_resource($stream)) {
            throw new RuntimeException('hook_channel_open');
        }
        $this->stream = $stream;
    }

    public function exchange(string $op, array|\stdClass $payload): array
    {
        if ($this->closed || !is_resource($this->stream)) {
            throw new RuntimeException('hook_channel_closed');
        }
        $wire = json_encode([
            'schema' => 'recastpress.wp-hooks-request.v0.1',
            'nonce' => $this->nonce,
            'seq' => $this->sendSeq++,
            'op' => $op,
            'payload' => $payload,
        ], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
        $size = strlen($wire);
        if ($size > RP_BOOT_HOOK_MAX_FRAME || $this->totalBytes + $size > RP_BOOT_HOOK_MAX_TOTAL) {
            throw new RuntimeException('hook_request_quota');
        }
        if (!rewind($this->stream)) {
            throw new RuntimeException('hook_rewind_before_write');
        }
        $offset = 0;
        while ($offset < $size) {
            $n = fwrite($this->stream, substr($wire, $offset));
            if (!is_int($n) || $n <= 0) {
                throw new RuntimeException('hook_write_failed');
            }
            $offset += $n;
        }
        if (!fflush($this->stream)) {
            throw new RuntimeException('hook_flush_failed');
        }
        if (!rewind($this->stream)) {
            throw new RuntimeException('hook_rewind_before_read');
        }
        $this->totalBytes += $size;

        $line = fgets($this->stream, RP_BOOT_HOOK_MAX_FRAME + 2);
        if (!is_string($line) || !str_ends_with($line, "\n")
            || strlen($line) > RP_BOOT_HOOK_MAX_FRAME) {
            throw new RuntimeException('hook_reply_frame');
        }
        $this->totalBytes += strlen($line);
        if ($this->totalBytes > RP_BOOT_HOOK_MAX_TOTAL) {
            throw new RuntimeException('hook_total_quota');
        }
        try {
            $reply = json_decode($line, true, 32, JSON_THROW_ON_ERROR);
        } catch (JsonException $error) {
            throw new RuntimeException('hook_reply_json', 0, $error);
        }
        if (!is_array($reply) || array_is_list($reply)
            || ($reply['schema'] ?? null) !== 'recastpress.wp-hooks-reply.v0.1'
            || ($reply['nonce'] ?? null) !== $this->nonce
            || ($reply['seq'] ?? null) !== $this->receiveSeq++
            || !is_string($reply['event'] ?? null)
            || !is_array($reply['payload'] ?? null)) {
            throw new RuntimeException('hook_reply_identity');
        }
        return $reply;
    }

    public function close(): void
    {
        if ($this->closed) {
            throw new RuntimeException('hook_close_twice');
        }
        $reply = $this->exchange('close', new \stdClass());
        if ($reply['event'] !== 'close' || $reply['payload'] !== ['closed' => true]) {
            throw new RuntimeException('hook_close_ack');
        }
        $extra = fread($this->stream, 1);
        if ($extra !== '' || !feof($this->stream)) {
            throw new RuntimeException('hook_close_not_eof');
        }
        $ok = fclose($this->stream);
        $this->closed = $ok;
        if (!$ok) {
            throw new RuntimeException('hook_close_failed');
        }
    }

    /** Close the local channel after a failed dispatch without sending `close`. */
    public function abort(): void
    {
        if ($this->closed) {
            return;
        }
        $stream = $this->stream;
        $this->stream = null;
        $this->closed = true;
        if (is_resource($stream)) {
            try {
                fclose($stream);
            } catch (Throwable) {
                // Preserve the dispatch-stage failure during best-effort cleanup.
            }
        }
    }

    public function __destruct()
    {
        $this->abort();
    }
}

function rp_boot_hook_first(string $value): string
{
    rp_boot_hook_record('rp_boot_hook_first', $value);
    return $value . '|first';
}

function rp_boot_hook_second(string $value): string
{
    rp_boot_hook_record('rp_boot_hook_second', $value);
    return $value . '|second';
}

function rp_boot_hook_record(string $callback, string $value): void
{
    $hook = $GLOBALS['rp_boot_hook_object'] ?? null;
    if (!$hook instanceof WP_Hook) {
        throw new RuntimeException('hook_object_missing');
    }
    $GLOBALS['rp_boot_hook_trace'][] = [
        'callback' => $callback,
        'value' => $value,
        'did_filter' => did_filter(RP_BOOT_HOOK_NAME),
        'current_filter' => current_filter(),
        'current_priority' => $hook->current_priority(),
        'doing_filter' => doing_filter(RP_BOOT_HOOK_NAME),
    ];
}

function recastpress_wp_should_dispatch_selected_hook(string $hookName, object $hook): bool
{
    return $hook instanceof WP_Hook
        && $hook === ($GLOBALS['wp_filter'][RP_BOOT_HOOK_NAME] ?? null)
        && $hook === ($GLOBALS['rp_boot_hook_object'] ?? null)
        && $hookName === RP_BOOT_HOOK_NAME
        && ($GLOBALS['rp_boot_hook_dispatched'] ?? false) === false;
}

function rp_boot_hook_same_handle(array $actual, array $expected): bool
{
    $keys = ['generation', 'kind', 'request', 'slot', 'token'];
    sort($keys, SORT_STRING);
    if (count($actual) !== count($keys) || count($expected) !== count($keys)) {
        return false;
    }
    $actualKeys = array_keys($actual);
    $expectedKeys = array_keys($expected);
    sort($actualKeys, SORT_STRING);
    sort($expectedKeys, SORT_STRING);
    if ($actualKeys !== $keys || $expectedKeys !== $keys) {
        return false;
    }
    foreach (['generation', 'kind', 'request', 'slot'] as $key) {
        if ($actual[$key] !== $expected[$key]) {
            return false;
        }
    }
    return is_string($actual['token']) && is_string($expected['token'])
        && hash_equals($expected['token'], $actual['token']);
}

/** Called only from the fixed WP_Hook overlay for the selected real hook. */
function recastpress_wp_dispatch_selected_hook(
    string $hookName,
    WP_Hook $hook,
    mixed $value,
    array $args,
    int $nestingLevel,
    Closure $sync,
): mixed {
    $registry = $GLOBALS['rp_boot_hook_registry'] ?? null;
    $channel = $GLOBALS['rp_boot_hook_channel'] ?? null;
    $expectedCallbacks = ['rp_boot_hook_first', 'rp_boot_hook_second'];
    $GLOBALS['rp_boot_hook_dispatched'] = true;
    $invocations = [];
    $failureStage = 'begin';
    try {
        if (!$registry instanceof HandleRegistry || !$channel instanceof RpBootstrapHookChannel
            || $hook !== ($GLOBALS['wp_filter'][RP_BOOT_HOOK_NAME] ?? null)
            || $hook !== ($GLOBALS['rp_boot_hook_object'] ?? null)
            || ($GLOBALS['rp_boot_hook_dispatched'] ?? false) !== true
            || $hookName !== RP_BOOT_HOOK_NAME || !is_string($value)
            || $value !== 'RP Bootstrap Fixture' || !array_is_list($args)
            || $args !== [$value, 'blogname'] || $nestingLevel < 0) {
            throw new RuntimeException('hook_filter_context');
        }

        $rows = [];
        foreach ($hook->callbacks as $priority => $bucket) {
            if (!is_int($priority) || !is_array($bucket)) {
                throw new RuntimeException('hook_callback_bucket_shape');
            }
            foreach ($bucket as $uid => $entry) {
                $function = $entry['function'] ?? null;
                $acceptedArgs = $entry['accepted_args'] ?? null;
                if (!is_string($uid) || !is_string($function) || $uid !== $function
                    || !in_array($uid, $expectedCallbacks, true) || $acceptedArgs !== 1) {
                    throw new RuntimeException('hook_callback_identity');
                }
                $rows[] = [
                    'priority' => $priority,
                    'uid' => $uid,
                    'accepted_args' => $acceptedArgs,
                    'function' => $function,
                ];
            }
        }
        if (count($rows) !== 2 || array_column($rows, 'uid') !== $expectedCallbacks
            || array_column($rows, 'priority') !== [10, 20]) {
            throw new RuntimeException('hook_callback_table_contract');
        }
        foreach ($rows as &$row) {
            $row['handle'] = $registry->retainCallable($row['function']);
        }
        unset($row);
        $GLOBALS['rp_boot_hook_exported_handles'] = array_column($rows, 'handle', 'uid');
        $projection = array_map(static fn(array $row): array => [
            $row['priority'], $row['uid'], $row['accepted_args'], $row['function'],
            $row['handle']['request'], $row['handle']['kind'], $row['handle']['slot'],
            $row['handle']['generation'], $row['handle']['token'],
        ], $rows);
        $fingerprint = hash('sha256', json_encode($projection, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR));
        $failureStage = 'begin';
        $reply = $channel->exchange('begin', [
            'hook' => $hookName,
            'target' => RP_BOOT_HOOK_TARGET,
            'value' => $value,
            'extra' => array_slice($args, 1),
            'rows' => $rows,
            'fingerprint' => $fingerprint,
        ]);
        while (true) {
            if ($reply['event'] === 'invoke') {
                $failureStage = 'invoke_contract';
                $payload = $reply['payload'];
                $uid = $payload['uid'] ?? null;
                $handle = $payload['handle'] ?? null;
                $priority = $payload['priority'] ?? null;
                $expectedHandle = is_string($uid) ? ($GLOBALS['rp_boot_hook_exported_handles'][$uid] ?? null) : null;
                if (($payload['target'] ?? null) !== RP_BOOT_HOOK_TARGET
                    || !is_string($uid) || !is_array($expectedHandle) || !is_array($handle)
                    || !rp_boot_hook_same_handle($handle, $expectedHandle)
                    || !is_int($priority) || !in_array($priority, [10, 20], true)
                    || ($payload['value'] ?? null) !== $value
                    || ($payload['call_mode'] ?? null) !== 'array'
                    || ($payload['args'] ?? null) !== [$value]
                    || !$sync('set_priority', $priority)) {
                    throw new RuntimeException('hook_invoke_identity');
                }
                $failureStage = 'callback';
                $callback = $registry->resolve($handle, 'callable');
                $returned = call_user_func_array($callback, $payload['args']);
                if (!is_string($returned)) {
                    throw new RuntimeException('hook_callback_result');
                }
                $value = $returned;
                $invocations[] = $uid;
                $failureStage = 'resume';
                $reply = $channel->exchange('resume', [
                    'target' => RP_BOOT_HOOK_TARGET,
                    'uid' => $uid,
                    'value' => $returned,
                ]);
                continue;
            }
            if ($reply['event'] === 'release') {
                $failureStage = 'release';
                $handle = $reply['payload']['handle'] ?? null;
                $uid = null;
                foreach ($GLOBALS['rp_boot_hook_exported_handles'] as $candidate => $candidateHandle) {
                    if (is_array($handle) && rp_boot_hook_same_handle($handle, $candidateHandle)) {
                        $uid = $candidate;
                        break;
                    }
                }
                if ($uid === null) {
                    throw new RuntimeException('hook_release_identity');
                }
                $registry->release($handle);
                $reply = $channel->exchange('release_ack', ['handle' => $handle]);
                continue;
            }
            if ($reply['event'] === 'complete') {
                $failureStage = 'complete';
                if (($reply['payload']['target'] ?? null) !== RP_BOOT_HOOK_TARGET
                    || ($reply['payload']['value'] ?? null) !== $value
                    || ($reply['payload']['invocations'] ?? null) !== 2
                    || $invocations !== $expectedCallbacks || $registry->liveCount() !== 0
                    || !$sync('finish')) {
                    throw new RuntimeException('hook_completion_contract');
                }
                $registry->close();
                $channel->close();
                $GLOBALS['rp_boot_hook_invocations'] = $invocations;
                return $value;
            }
            throw new RuntimeException('hook_reply_op');
        }
    } catch (Throwable $error) {
        if ($registry instanceof HandleRegistry) {
            try { $registry->close(); } catch (Throwable) { /* preserve dispatch stage */ }
        }
        if ($channel instanceof RpBootstrapHookChannel) {
            try { $channel->abort(); } catch (Throwable) { /* close is success-only */ }
        }
        throw new RpBootstrapFailure('hook_dispatch_' . $failureStage);
    }
}

/** Call after bootstrap and before the single public get_option('blogname'). */
function rp_bootstrap_install_blogname_dispatcher(): void
{
    if (!class_exists(WP_Hook::class, false)
        || !isset($GLOBALS['rp_input']['nonce'])
        || isset($GLOBALS['rp_boot_hook_object'])) {
        throw new RuntimeException('hook_install_context');
    }
    $GLOBALS['rp_boot_hook_did_filter_before'] = did_filter(RP_BOOT_HOOK_NAME);
    $GLOBALS['rp_boot_hook_channel'] = new RpBootstrapHookChannel($GLOBALS['rp_input']['nonce']);
    $GLOBALS['rp_boot_hook_registry'] = new HandleRegistry(RP_BOOT_HOOK_HANDLE_REQUEST);
    $GLOBALS['rp_boot_hook_dispatched'] = false;
    add_filter(RP_BOOT_HOOK_NAME, 'rp_boot_hook_first', 10, 1);
    add_filter(RP_BOOT_HOOK_NAME, 'rp_boot_hook_second', 20, 1);
    $hook = $GLOBALS['wp_filter'][RP_BOOT_HOOK_NAME] ?? null;
    if (!$hook instanceof WP_Hook) {
        throw new RuntimeException('hook_install_wp_hook_missing');
    }
    $GLOBALS['rp_boot_hook_object'] = $hook;
    $GLOBALS['rp_boot_hook_trace'] = [];
}
