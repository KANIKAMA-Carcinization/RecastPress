<?php
declare(strict_types=1);

const RP_ADAPTER_ORACLE_SCHEMA = 'recastpress.wp-bootstrap-hook-adapter-native-oracle.v0.1';
const RP_ADAPTER_ORACLE_NONCE = '0123456789abcdef0123456789abcdef0123456789abcdef';
const RP_ADAPTER_ORACLE_RECEIPT = '73a7efccef5e52e4cbf5ff5f7e2544ec654ef3f7faa03f830f5e333a0a4eb360';
const RP_ADAPTER_ORACLE_OVERLAY = [
    'wp-includes/plugin.php' => [
        'source_sha256' => 'd70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
        'overlay_sha256' => 'db8dc3d3c61a43ff225e01881187f47ce8eedfb1a65a56c9cd60bf13aea04d5c',
    ],
    'wp-includes/class-wp-hook.php' => [
        'source_sha256' => 'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
        'overlay_sha256' => '76eacf182c0e07c4285be0eff1df5bfd5cf4ce2a08c748a1085b241ffff473e4',
    ],
];
const RP_ADAPTER_ORACLE_SENTINEL = '68c9c675d3805a343835728d98a0efedf0df9833cb904bebf909c7e0fda5c61c';

function rp_adapter_oracle_emit(array $value): never
{
    $value['temporary_cleanup'] = rp_adapter_oracle_cleanup();
    echo json_encode($value, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR), "\n";
    exit;
}

function rp_adapter_oracle_cleanup(): bool
{
    $root = $GLOBALS['rp_adapter_oracle_temp'] ?? null;
    if (!is_string($root) || !str_starts_with($root, rtrim(sys_get_temp_dir(), DIRECTORY_SEPARATOR)
        . DIRECTORY_SEPARATOR . 'rp-hook-adapter-')) {
        return true;
    }
    if (!is_dir($root) || is_link($root)) { return false; }
    $clean = true;
    foreach (['hook_adapter.php', 'hook_handle_registry.php'] as $name) {
        $path = $root . DIRECTORY_SEPARATOR . $name;
        if (is_file($path) && !is_link($path) && !unlink($path)) { $clean = false; }
    }
    $include = $root . DIRECTORY_SEPARATOR . 'wp-includes';
    if (is_link($include)) { return false; }
    foreach (['plugin.php', 'class-wp-hook.php', 'class-wp-filter-sentinel.php'] as $name) {
        $path = $include . DIRECTORY_SEPARATOR . $name;
        if (is_file($path) && !is_link($path) && !unlink($path)) { $clean = false; }
    }
    if (is_dir($include) && !is_link($include) && !rmdir($include)) { $clean = false; }
    if (!rmdir($root)) { $clean = false; }
    unset($GLOBALS['rp_adapter_oracle_temp']);
    return $clean;
}

final class RpAdapterOracleStream
{
    public $context;
    private string $buffer = '';
    private int $position = 0;
    private bool $eof = false;
    private int $sequence = 0;
    private int $expectedRequestSequence = 1;
    private int $phase = 0;
    private string $nonce = RP_ADAPTER_ORACLE_NONCE;
    private array $handles = [];
    private array $observed = [];

    public static array $last = [];

    public function stream_open(string $path, string $mode, int $options, ?string &$openedPath): bool
    {
        return $path === 'recastpress-rp-hook-test://channel' && $mode === 'r+b';
    }

    public function stream_write(string $data): int
    {
        if ($this->position === 0) { $this->buffer = ''; $this->eof = false; }
        $this->buffer .= $data;
        $this->position += strlen($data);
        if (!str_ends_with($this->buffer, "\n")) {
            return strlen($data);
        }
        $wire = $this->buffer;
        $request = json_decode(substr($wire, 0, -1), true, 32, JSON_THROW_ON_ERROR);
        if (!is_array($request) || array_is_list($request)) { throw new RuntimeException('mock_request_object'); }
        $keys = array_keys($request);
        sort($keys, SORT_STRING);
        if ($keys !== ['nonce', 'op', 'payload', 'schema', 'seq']
            || ($request['schema'] ?? null) !== 'recastpress.wp-hooks-request.v0.1'
            || ($request['nonce'] ?? null) !== $this->nonce
            || !is_int($request['seq'] ?? null) || $request['seq'] !== $this->expectedRequestSequence
            || !is_string($request['op'] ?? null) || !is_array($request['payload'] ?? null)) {
            throw new RuntimeException('mock_request_envelope');
        }
        $this->expectedRequestSequence++;
        $this->observed[] = ['op' => $request['op'], 'seq' => $request['seq']];
        $payload = $request['payload'];
        $eventPayload = [];
        $event = '';
        switch ($request['op']) {
            case 'begin':
                $payloadKeys = array_keys($payload);
                sort($payloadKeys, SORT_STRING);
                if ($payloadKeys !== ['extra', 'fingerprint', 'hook', 'rows', 'target', 'value']
                    || $this->phase !== 0 || $request['seq'] !== 1
                    || $payload['hook'] !== 'option_blogname'
                    || $payload['target'] !== 'rp-option-blogname-1'
                    || $payload['value'] !== 'RP Bootstrap Fixture'
                    || $payload['extra'] !== ['blogname'] || count($payload['rows']) !== 2) {
                    throw new RuntimeException('mock_begin_contract');
                }
                $this->handles = array_column($payload['rows'], 'handle', 'uid');
                foreach ([['rp_boot_hook_first', 10], ['rp_boot_hook_second', 20]] as $i => [$uid, $priority]) {
                    $row = $payload['rows'][$i];
                    $rowKeys = array_keys($row);
                    sort($rowKeys, SORT_STRING);
                    $handle = $row['handle'] ?? null;
                    $handleKeys = is_array($handle) ? array_keys($handle) : [];
                    sort($handleKeys, SORT_STRING);
                    if ($rowKeys !== ['accepted_args', 'function', 'handle', 'priority', 'uid']
                        || !is_array($handle) || array_is_list($handle)
                        || $handleKeys !== ['generation', 'kind', 'request', 'slot', 'token']
                        || $row['uid'] !== $uid || $row['function'] !== $uid || $row['priority'] !== $priority
                        || $row['accepted_args'] !== 1 || $handle['request'] !== '1'
                        || $handle['kind'] !== 'callable' || !is_int($handle['slot'])
                        || !is_int($handle['generation']) || !is_string($handle['token'])
                        || preg_match('/\A[0-9a-f]{16}\z/', $handle['token']) !== 1) {
                        throw new RuntimeException('mock_callback_table');
                    }
                }
                $projection = array_map(static fn(array $row): array => [
                    $row['priority'], $row['uid'], $row['accepted_args'], $row['function'],
                    $row['handle']['request'], $row['handle']['kind'], $row['handle']['slot'],
                    $row['handle']['generation'], $row['handle']['token'],
                ], $payload['rows']);
                $fingerprint = hash('sha256', json_encode($projection, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR));
                if (!is_string($payload['fingerprint']) || !hash_equals($fingerprint, $payload['fingerprint'])) {
                    throw new RuntimeException('mock_begin_fingerprint');
                }
                $event = 'invoke';
                $eventPayload = ['target' => 'rp-option-blogname-1', 'uid' => 'rp_boot_hook_first',
                    'priority' => 10, 'handle' => $this->handles['rp_boot_hook_first'], 'value' => 'RP Bootstrap Fixture',
                    'call_mode' => 'array', 'args' => ['RP Bootstrap Fixture']];
                $this->phase = 1;
                break;
            case 'resume':
                $payloadKeys = array_keys($payload);
                sort($payloadKeys, SORT_STRING);
                if ($payloadKeys !== ['target', 'uid', 'value'] || $payload['target'] !== 'rp-option-blogname-1') {
                    throw new RuntimeException('mock_resume_payload');
                }
                if ($request['seq'] === 2 && $this->phase === 1
                    && $payload['uid'] === 'rp_boot_hook_first' && $payload['value'] === 'RP Bootstrap Fixture|first') {
                    $event = 'invoke';
                    $eventPayload = ['target' => 'rp-option-blogname-1', 'uid' => 'rp_boot_hook_second',
                        'priority' => 20, 'handle' => $this->handles['rp_boot_hook_second'],
                        'value' => 'RP Bootstrap Fixture|first', 'call_mode' => 'array',
                        'args' => ['RP Bootstrap Fixture|first']];
                    $this->phase = 2;
                } elseif ($request['seq'] === 3 && $this->phase === 2
                    && $payload['uid'] === 'rp_boot_hook_second'
                    && $payload['value'] === 'RP Bootstrap Fixture|first|second') {
                    $event = 'release';
                    $eventPayload = ['handle' => $this->handles['rp_boot_hook_first']];
                    $this->phase = 3;
                } else {
                    throw new RuntimeException('mock_resume_contract');
                }
                break;
            case 'release_ack':
                $payloadKeys = array_keys($payload);
                sort($payloadKeys, SORT_STRING);
                if ($payloadKeys !== ['handle']) { throw new RuntimeException('mock_release_payload'); }
                if ($request['seq'] === 4 && $this->phase === 3
                    && $payload['handle'] === $this->handles['rp_boot_hook_first']) {
                    $event = 'release';
                    $eventPayload = ['handle' => $this->handles['rp_boot_hook_second']];
                    $this->phase = 4;
                } elseif ($request['seq'] === 5 && $this->phase === 4
                    && $payload['handle'] === $this->handles['rp_boot_hook_second']) {
                    $event = 'complete';
                    $eventPayload = ['target' => 'rp-option-blogname-1',
                        'value' => 'RP Bootstrap Fixture|first|second', 'invocations' => 2];
                    $this->phase = 5;
                } else {
                    throw new RuntimeException('mock_release_contract');
                }
                break;
            case 'close':
                if ($payload !== [] || $request['seq'] !== 6 || $this->phase !== 5) {
                    throw new RuntimeException('mock_close_contract');
                }
                $event = 'close';
                $eventPayload = ['closed' => true];
                $this->phase = 6;
                break;
            default:
                throw new RuntimeException('mock_unknown_operation');
        }
        $this->sequence++;
        $this->buffer = json_encode(['schema' => 'recastpress.wp-hooks-reply.v0.1',
            'nonce' => $this->nonce, 'seq' => $this->sequence, 'event' => $event,
            'payload' => (object)$eventPayload], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
        $this->position = 0;
        $this->eof = false;
        self::$last = ['phase' => $this->phase, 'operations' => $this->observed];
        return strlen($data);
    }

    public function stream_read(int $count): string
    {
        $value = substr($this->buffer, $this->position, $count);
        $this->position += strlen($value);
        if ($this->position >= strlen($this->buffer)) {
            $this->eof = true;
        }
        return $value;
    }

    public function stream_eof(): bool { return $this->eof; }
    public function stream_tell(): int { return $this->position; }
    public function stream_flush(): bool { return true; }
    public function stream_close(): void { self::$last['closed'] = true; }

    public function stream_seek(int $offset, int $whence = SEEK_SET): bool
    {
        $base = match ($whence) {
            SEEK_SET => 0,
            SEEK_CUR => $this->position,
            SEEK_END => strlen($this->buffer),
            default => -1,
        };
        $next = $base + $offset;
        if ($next < 0 || $next > strlen($this->buffer)) { return false; }
        $this->position = $next;
        $this->eof = false;
        return true;
    }

    public function stream_stat(): array
    {
        return ['mode' => 0100666, 'size' => strlen($this->buffer), 2 => 0100666, 7 => strlen($this->buffer)];
    }
}

try {
    if (PHP_SAPI !== 'cli' || $argc !== 1 || !stream_wrapper_register('recastpress-rp-hook-test', RpAdapterOracleStream::class)) {
        throw new RuntimeException('entry_guard');
    }
    $root = dirname(__DIR__, 2);
    $site = $root . DIRECTORY_SEPARATOR . 'target' . DIRECTORY_SEPARATOR . 'wp-bootstrap-probe' . DIRECTORY_SEPARATOR . 'site';
    $overlay = $root . DIRECTORY_SEPARATOR . 'experiments' . DIRECTORY_SEPARATOR . 'php-wasix-wp-hook-dispatch-probe'
        . DIRECTORY_SEPARATOR . 'evidence' . DIRECTORY_SEPARATOR . 'overlay-v5';
    $sourceAdapter = $root . DIRECTORY_SEPARATOR . 'experiments' . DIRECTORY_SEPARATOR . 'php-wasix-wp-bootstrap-probe'
        . DIRECTORY_SEPARATOR . 'fixtures' . DIRECTORY_SEPARATOR . 'hook_adapter.php';
    $sourceRegistry = $root . DIRECTORY_SEPARATOR . 'experiments' . DIRECTORY_SEPARATOR . 'php-wasix-wp-bootstrap-probe'
        . DIRECTORY_SEPARATOR . 'fixtures' . DIRECTORY_SEPARATOR . 'hook_handle_registry.php';
    foreach (RP_ADAPTER_ORACLE_OVERLAY as $relative => $identity) {
        $source = $site . DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, $relative);
        $patched = $overlay . DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, $relative);
        if (!is_file($source) || hash_file('sha256', $source) !== $identity['source_sha256']
            || !is_file($patched) || hash_file('sha256', $patched) !== $identity['overlay_sha256']) {
            throw new RuntimeException('overlay_identity');
        }
    }
    $sentinel = $site . DIRECTORY_SEPARATOR . 'wp-includes' . DIRECTORY_SEPARATOR . 'class-wp-filter-sentinel.php';
    $overlayReceipt = $overlay . DIRECTORY_SEPARATOR . 'overlay.json';
    if (!is_file($sentinel) || hash_file('sha256', $sentinel) !== RP_ADAPTER_ORACLE_SENTINEL
        || !is_file($overlayReceipt) || hash_file('sha256', $overlayReceipt) !== RP_ADAPTER_ORACLE_RECEIPT) {
        throw new RuntimeException('overlay_support_identity');
    }
    $adapterBytes = file_get_contents($sourceAdapter);
    $registryBytes = file_get_contents($sourceRegistry);
    if (!is_string($adapterBytes) || !is_string($registryBytes)
        || substr_count($adapterBytes, "'/rp-wp-hooks/channel'") !== 1) {
        throw new RuntimeException('adapter_fixture_input');
    }
    $temporary = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'rp-hook-adapter-' . bin2hex(random_bytes(8));
    if (!mkdir($temporary, 0700)) {
        throw new RuntimeException('temporary_setup');
    }
    $GLOBALS['rp_adapter_oracle_temp'] = $temporary;
    if (!mkdir($temporary . DIRECTORY_SEPARATOR . 'wp-includes', 0700)) {
        throw new RuntimeException('temporary_include_setup');
    }
    foreach (['plugin.php' => $overlay . DIRECTORY_SEPARATOR . 'wp-includes' . DIRECTORY_SEPARATOR . 'plugin.php',
        'class-wp-hook.php' => $overlay . DIRECTORY_SEPARATOR . 'wp-includes' . DIRECTORY_SEPARATOR . 'class-wp-hook.php',
        'class-wp-filter-sentinel.php' => $sentinel] as $name => $path) {
        if (!copy($path, $temporary . DIRECTORY_SEPARATOR . 'wp-includes' . DIRECTORY_SEPARATOR . $name)) {
            throw new RuntimeException('overlay_copy');
        }
    }
    $transformed = str_replace("'/rp-wp-hooks/channel'", "'recastpress-rp-hook-test://channel'", $adapterBytes, $replacementCount);
    if ($replacementCount !== 1
        || file_put_contents($temporary . DIRECTORY_SEPARATOR . 'hook_adapter.php', $transformed, LOCK_EX) !== strlen($transformed)
        || file_put_contents($temporary . DIRECTORY_SEPARATOR . 'hook_handle_registry.php', $registryBytes, LOCK_EX) !== strlen($registryBytes)) {
        throw new RuntimeException('adapter_test_seam');
    }
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];
    $GLOBALS['rp_input'] = ['nonce' => RP_ADAPTER_ORACLE_NONCE];
    require $temporary . DIRECTORY_SEPARATOR . 'wp-includes' . DIRECTORY_SEPARATOR . 'plugin.php';
    require $temporary . DIRECTORY_SEPARATOR . 'hook_adapter.php';
    rp_bootstrap_install_blogname_dispatcher();
    $hook = $GLOBALS['wp_filter']['option_blogname'] ?? null;
    if (!$hook instanceof WP_Hook) { throw new RuntimeException('wp_hook_missing'); }
    $GLOBALS['rp_boot_hook_object'] = $hook;
    $value = apply_filters('option_blogname', 'RP Bootstrap Fixture', 'blogname');
    $events = $GLOBALS['rp_boot_hook_trace'] ?? [];
    $state = [
        'nesting_level' => (new ReflectionProperty(WP_Hook::class, 'nesting_level'))->getValue($hook),
        'iterations' => (new ReflectionProperty(WP_Hook::class, 'iterations'))->getValue($hook),
        'current_priority' => (new ReflectionProperty(WP_Hook::class, 'current_priority'))->getValue($hook),
        'doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    if ($value !== 'RP Bootstrap Fixture|first|second'
        || array_column($events, 'callback') !== ['rp_boot_hook_first', 'rp_boot_hook_second']
        || array_column($events, 'current_priority') !== [10, 20]
        || array_column($events, 'did_filter') !== [1, 1]
        || array_column($events, 'current_filter') !== ['option_blogname', 'option_blogname']
        || array_column($events, 'doing_filter') !== [true, true]
        || did_filter('option_blogname') !== 1 || current_filter() !== false || doing_filter()
        || $GLOBALS['wp_current_filter'] !== []
        || $state !== ['nesting_level' => 0, 'iterations' => [], 'current_priority' => [], 'doing_action' => false]
        || ($GLOBALS['rp_boot_hook_registry']->liveCount() ?? -1) !== 0
        || ($GLOBALS['rp_boot_hook_invocations'] ?? null) !== ['rp_boot_hook_first', 'rp_boot_hook_second']
        || RpAdapterOracleStream::$last['phase'] !== 6
        || count(RpAdapterOracleStream::$last['operations']) !== 6) {
        throw new RuntimeException('adapter_dispatch_observation_mismatch');
    }
    rp_adapter_oracle_emit([
        'schema' => RP_ADAPTER_ORACLE_SCHEMA,
        'status' => 'passed',
        'scope' => 'pinned PHP 8.5.12 native WP_Hook overlay plus exact hook_adapter.php with only the fixed channel path redirected to a deterministic PHP stream wrapper; Rust HookRpcFs is simulated, no WordPress bootstrap or Wasm guest',
        'wordpress_version' => '7.1.3',
        'adapter_path_transform_count' => $replacementCount,
        'callback_order' => array_column($events, 'callback'),
        'events' => $events,
        'filtered_value' => $value,
        'did_filter' => did_filter('option_blogname'),
        'hook_state_after' => $state,
        'registry_live_handles_after' => $GLOBALS['rp_boot_hook_registry']->liveCount(),
        'mock_peer_operations' => RpAdapterOracleStream::$last['operations'],
        'mock_peer_closed' => RpAdapterOracleStream::$last['closed'] ?? false,
        'source_sha256' => ['adapter' => hash('sha256', $adapterBytes),
            'transformed_adapter' => hash('sha256', $transformed),
            'handle_registry' => hash('sha256', $registryBytes),
            'overlay_receipt' => RP_ADAPTER_ORACLE_RECEIPT,
            'overlay_files' => array_map(static fn(array $row): string => $row['overlay_sha256'], RP_ADAPTER_ORACLE_OVERLAY)],
    ]);
} catch (Throwable $error) {
    rp_adapter_oracle_emit(['schema' => RP_ADAPTER_ORACLE_SCHEMA, 'status' => 'failed', 'failure' => $error->getMessage()]);
}
