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

final class RpBootstrapHookChannel
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

    public function exchange(string $op, array $payload): array
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
        $reply = $this->exchange('close', []);
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

    public function __destruct()
    {
        if (is_resource($this->stream)) {
            fclose($this->stream);
        }
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
    if (!$hook instanceof RpBootstrapDispatcherHook) {
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

/** Narrow selected-hook object for the public plugin.php apply_filters path. */
final class RpBootstrapDispatcherHook
{
    public array $callbacks = [];
    private array $priorities = [10, 20];
    private ?int $activePriority = null;
    private RpBootstrapHookChannel $channel;
    private HandleRegistry $registry;
    private array $handles = [];
    private array $invocations = [];
    private bool $dispatched = false;

    public function __construct(string $nonce)
    {
        $this->channel = new RpBootstrapHookChannel($nonce);
        $this->registry = new HandleRegistry(RP_BOOT_HOOK_HANDLE_REQUEST);
        $this->callbacks = [
            10 => ['rp_boot_hook_first' => [
                'function' => 'rp_boot_hook_first',
                'accepted_args' => 1,
            ]],
            20 => ['rp_boot_hook_second' => [
                'function' => 'rp_boot_hook_second',
                'accepted_args' => 1,
            ]],
        ];
        foreach (['rp_boot_hook_first', 'rp_boot_hook_second'] as $uid) {
            $this->handles[$uid] = $this->registry->retainCallable($uid);
        }
    }

    public function current_priority(): int|false
    {
        return $this->activePriority ?? false;
    }

    private static function sameHandle(array $actual, array $expected): bool
    {
        $keys = ['generation', 'kind', 'request', 'slot', 'token'];
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

    public function apply_filters($value, $args)
    {
        if ($this->dispatched) {
            throw new RuntimeException('hook_dispatch_repeated');
        }
        if (!is_string($value) || $value !== 'RP Bootstrap Fixture'
            || !is_array($args) || array_is_list($args) === false
            || ($args[0] ?? null) !== $value || ($args[1] ?? null) !== 'blogname'
            || count($this->callbacks) !== 2 || array_keys($this->callbacks) !== [10, 20]) {
            throw new RuntimeException('hook_filter_context');
        }

        $rows = [];
        foreach ([10 => 'rp_boot_hook_first', 20 => 'rp_boot_hook_second'] as $priority => $uid) {
            $bucket = $this->callbacks[$priority] ?? [];
            if (count($bucket) !== 1) {
                throw new RuntimeException('hook_callback_bucket_shape');
            }
            $row = reset($bucket);
            if (($row['function'] ?? null) !== $uid || ($row['accepted_args'] ?? null) !== 1) {
                throw new RuntimeException('hook_callback_identity');
            }
            $rows[] = [
                'priority' => $priority,
                'uid' => $uid,
                'accepted_args' => 1,
                'function' => $uid,
                'handle' => $this->handles[$uid],
            ];
        }
        $projection = array_map(static fn(array $row): array => [
            $row['priority'], $row['uid'], $row['accepted_args'], $row['function'],
            $row['handle']['request'], $row['handle']['kind'], $row['handle']['slot'],
            $row['handle']['generation'], $row['handle']['token'],
        ], $rows);
        $fingerprint = hash('sha256', json_encode(
            $projection,
            JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
        ));

        // The public plugin.php wrapper owns the global current-filter stack
        // and count. This narrow object mirrors only the selected priority.
        $sync = function (string $operation, ?int $priority = null): bool {
            if ($operation === 'set_priority') {
                if (!in_array($priority, $this->priorities, true)) {
                    return false;
                }
                $this->activePriority = $priority;
                return true;
            }
            if ($operation === 'finish') {
                $this->activePriority = null;
                return true;
            }
            return false;
        };

        $this->dispatched = true;
        $failureStage = 'begin';
        try {
            $reply = $this->channel->exchange('begin', [
                'hook' => RP_BOOT_HOOK_NAME,
                'target' => RP_BOOT_HOOK_TARGET,
                'value' => $value,
                'extra' => array_slice($args, 1),
                'rows' => $rows,
                'fingerprint' => $fingerprint,
            ]);
            while (true) {
            if ($reply['event'] === 'invoke') {
                $failureStage = 'invoke_contract';
                $p = $reply['payload'];
                $uid = $p['uid'] ?? null;
                $handle = $p['handle'] ?? null;
                $priority = $p['priority'] ?? null;
                if (($p['target'] ?? null) !== RP_BOOT_HOOK_TARGET
                    || !is_string($uid) || !isset($this->handles[$uid])
                    || !is_array($handle) || !self::sameHandle($handle, $this->handles[$uid])
                    || !is_int($priority) || !in_array($priority, [10, 20], true)
                    || ($p['value'] ?? null) !== $value
                    || ($p['call_mode'] ?? null) !== 'array'
                    || ($p['args'] ?? null) !== [$value]
                    || !$sync('set_priority', $priority)) {
                    throw new RuntimeException('hook_invoke_identity');
                }
                $failureStage = 'callback';
                $callback = $this->registry->resolve($handle, 'callable');
                $result = call_user_func_array($callback, $p['args']);
                if (!is_string($result)) {
                    throw new RuntimeException('hook_callback_result');
                }
                $value = $result;
                $this->invocations[] = $uid;
                $failureStage = 'resume';
                $reply = $this->channel->exchange('resume', [
                    'target' => RP_BOOT_HOOK_TARGET,
                    'uid' => $uid,
                    'value' => $result,
                ]);
                continue;
            }
            if ($reply['event'] === 'release') {
                $failureStage = 'release';
                $handle = $reply['payload']['handle'] ?? null;
                $uid = null;
                foreach ($this->handles as $candidate => $candidateHandle) {
                    if (is_array($handle) && self::sameHandle($handle, $candidateHandle)) {
                        $uid = $candidate;
                        break;
                    }
                }
                if ($uid === null) {
                    throw new RuntimeException('hook_release_identity');
                }
                $this->registry->release($handle);
                $reply = $this->channel->exchange('release_ack', ['handle' => $handle]);
                continue;
            }
            if ($reply['event'] === 'complete') {
                $failureStage = 'complete';
                if (($reply['payload']['target'] ?? null) !== RP_BOOT_HOOK_TARGET
                    || ($reply['payload']['value'] ?? null) !== $value
                    || ($reply['payload']['invocations'] ?? null) !== 2
                    || $this->invocations !== ['rp_boot_hook_first', 'rp_boot_hook_second']
                    || $this->registry->liveCount() !== 0
                    || !$sync('finish')) {
                    throw new RuntimeException('hook_completion_contract');
                }
                $this->registry->close();
                $this->channel->close();
                $GLOBALS['rp_boot_hook_invocations'] = $this->invocations;
                return $value;
            }
            throw new RuntimeException('hook_reply_op');
            }
        } catch (Throwable $error) {
            throw new RpBootstrapFailure('hook_dispatch_' . $failureStage);
        }
    }
}

/** Call after bootstrap and before the single public get_option('blogname'). */
function rp_bootstrap_install_blogname_dispatcher(): void
{
    if (!class_exists(WP_Hook::class, false)
        || !isset($GLOBALS['rp_input']['nonce'])
        || isset($GLOBALS['wp_filter'][RP_BOOT_HOOK_NAME])) {
        throw new RuntimeException('hook_install_context');
    }
    $GLOBALS['rp_boot_hook_did_filter_before'] = did_filter(RP_BOOT_HOOK_NAME);
    $hook = new RpBootstrapDispatcherHook($GLOBALS['rp_input']['nonce']);
    $GLOBALS['wp_filter'][RP_BOOT_HOOK_NAME] = $hook;
    $GLOBALS['rp_boot_hook_object'] = $hook;
    $GLOBALS['rp_boot_hook_trace'] = [];
}
