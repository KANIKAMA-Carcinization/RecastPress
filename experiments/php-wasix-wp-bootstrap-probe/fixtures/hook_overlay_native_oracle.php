<?php
declare(strict_types=1);

const RP_OVERLAY_ORACLE_SCHEMA = 'recastpress.wp-bootstrap-hook-overlay-native-oracle.v0.1';
const RP_OVERLAY_ORACLE_HOOK = 'option_blogname';
const RP_OVERLAY_ORACLE_TARGET = 'rp-option-blogname-1';
const RP_OVERLAY_ORACLE_RECEIPT_SHA = '73a7efccef5e52e4cbf5ff5f7e2544ec654ef3f7faa03f830f5e333a0a4eb360';
const RP_OVERLAY_ORACLE_SENTINEL_SHA = '68c9c675d3805a343835728d98a0efedf0df9833cb904bebf909c7e0fda5c61c';
const RP_OVERLAY_ORACLE_FILES = [
    'wp-includes/plugin.php' => [
        'source_sha256' => 'd70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
        'overlay_sha256' => 'db8dc3d3c61a43ff225e01881187f47ce8eedfb1a65a56c9cd60bf13aea04d5c',
    ],
    'wp-includes/class-wp-hook.php' => [
        'source_sha256' => 'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
        'overlay_sha256' => '76eacf182c0e07c4285be0eff1df5bfd5cf4ce2a08c748a1085b241ffff473e4',
    ],
];

function rp_overlay_oracle_emit(array $value): never
{
    rp_overlay_oracle_cleanup();
    echo json_encode($value, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR), "\n";
    exit;
}

function rp_overlay_oracle_cleanup(): void
{
    $root = $GLOBALS['rp_overlay_oracle_temp_root'] ?? null;
    if (!is_string($root) || !is_dir($root) || is_link($root)) {
        return;
    }
    foreach (['plugin.php', 'class-wp-hook.php', 'class-wp-filter-sentinel.php'] as $name) {
        $path = $root . DIRECTORY_SEPARATOR . 'wp-includes' . DIRECTORY_SEPARATOR . $name;
        if (is_file($path) && !is_link($path)) {
            unlink($path);
        }
    }
    $include = $root . DIRECTORY_SEPARATOR . 'wp-includes';
    if (is_dir($include) && !is_link($include)) {
        rmdir($include);
    }
    rmdir($root);
    unset($GLOBALS['rp_overlay_oracle_temp_root']);
}

function rp_overlay_oracle_state(string $callback, mixed $value): array
{
    $hook = $GLOBALS['wp_filter'][RP_OVERLAY_ORACLE_HOOK] ?? null;
    if (!$hook instanceof WP_Hook) {
        throw new RuntimeException('selected_hook_missing');
    }
    return [
        'callback' => $callback,
        'value' => $value,
        'current_filter' => current_filter(),
        'doing_filter' => doing_filter(RP_OVERLAY_ORACLE_HOOK),
        'did_filter' => did_filter(RP_OVERLAY_ORACLE_HOOK),
        'current_priority' => $hook->current_priority(),
        'stack' => $GLOBALS['wp_current_filter'] ?? [],
    ];
}

function rp_overlay_oracle_first(string $value): string
{
    $GLOBALS['rp_overlay_oracle_events'][] = rp_overlay_oracle_state(__FUNCTION__, $value);
    return $value . '|first';
}

function rp_overlay_oracle_second(string $value): string
{
    $GLOBALS['rp_overlay_oracle_events'][] = rp_overlay_oracle_state(__FUNCTION__, $value);
    return $value . '|second';
}

function recastpress_wp_should_dispatch_selected_hook(string $hookName, object $hook): bool
{
    return $hookName === RP_OVERLAY_ORACLE_HOOK
        && $hook === ($GLOBALS['wp_filter'][RP_OVERLAY_ORACLE_HOOK] ?? null)
        && $hook === ($GLOBALS['rp_overlay_oracle_target'] ?? null);
}

function recastpress_wp_dispatch_selected_hook(
    string $hookName,
    WP_Hook $hook,
    mixed $value,
    array $args,
    int $nestingLevel,
    Closure $sync,
): mixed {
    $expected = ['rp_overlay_oracle_first', 'rp_overlay_oracle_second'];
    $rows = [];
    foreach ($hook->callbacks as $priority => $bucket) {
        foreach ($bucket as $uid => $entry) {
            $rows[] = [
                'priority' => $priority,
                'uid' => $uid,
                'function' => $entry['function'],
                'accepted_args' => $entry['accepted_args'],
            ];
        }
    }
    if ($hookName !== RP_OVERLAY_ORACLE_HOOK || $nestingLevel !== 0
        || $value !== 'RP Bootstrap Fixture' || $args !== [$value, 'blogname']
        || $rows !== [
            ['priority' => 10, 'uid' => $expected[0], 'function' => $expected[0], 'accepted_args' => 1],
            ['priority' => 20, 'uid' => $expected[1], 'function' => $expected[1], 'accepted_args' => 1],
        ]) {
        throw new RuntimeException('callback_table_contract');
    }
    foreach ($rows as $row) {
        if (!$sync('set_priority', $row['priority'])) {
            throw new RuntimeException('priority_sync');
        }
        $value = call_user_func_array($row['function'], [$value]);
    }
    if (!$sync('finish')) {
        throw new RuntimeException('iteration_finish');
    }
    return $value;
}

try {
    if (PHP_SAPI !== 'cli' || $argc !== 1) {
        throw new RuntimeException('cli_entry_guard');
    }
    $root = dirname(__DIR__, 3);
    $site = $root . DIRECTORY_SEPARATOR . 'target' . DIRECTORY_SEPARATOR . 'wp-bootstrap-probe' . DIRECTORY_SEPARATOR . 'site';
    $overlay = $root . DIRECTORY_SEPARATOR . 'experiments' . DIRECTORY_SEPARATOR . 'php-wasix-wp-hook-dispatch-probe'
        . DIRECTORY_SEPARATOR . 'evidence' . DIRECTORY_SEPARATOR . 'overlay-v5';
    foreach (RP_OVERLAY_ORACLE_FILES as $relative => $identity) {
        $source = $site . DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, $relative);
        $patched = $overlay . DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, $relative);
        if (is_link($source) || !is_file($source) || hash_file('sha256', $source) !== $identity['source_sha256']
            || is_link($patched) || !is_file($patched) || hash_file('sha256', $patched) !== $identity['overlay_sha256']) {
            throw new RuntimeException('overlay_identity');
        }
    }
    $sentinel = $site . DIRECTORY_SEPARATOR . 'wp-includes' . DIRECTORY_SEPARATOR . 'class-wp-filter-sentinel.php';
    if (is_link($sentinel) || !is_file($sentinel) || hash_file('sha256', $sentinel) !== RP_OVERLAY_ORACLE_SENTINEL_SHA) {
        throw new RuntimeException('sentinel_source_identity');
    }
    $receipt = $overlay . DIRECTORY_SEPARATOR . 'overlay.json';
    if (is_link($receipt) || !is_file($receipt) || hash_file('sha256', $receipt) !== RP_OVERLAY_ORACLE_RECEIPT_SHA) {
        throw new RuntimeException('overlay_receipt_identity');
    }
    $temp = tempnam(sys_get_temp_dir(), 'rp-hook-overlay-');
    if (!is_string($temp) || !is_file($temp) || is_link($temp) || !unlink($temp)
        || !mkdir($temp, 0700) || realpath($temp) !== $temp
        || !mkdir($temp . DIRECTORY_SEPARATOR . 'wp-includes', 0700)) {
        throw new RuntimeException('overlay_temp_create');
    }
    $GLOBALS['rp_overlay_oracle_temp_root'] = $temp;
    foreach ([
        'plugin.php' => $overlay . DIRECTORY_SEPARATOR . 'wp-includes' . DIRECTORY_SEPARATOR . 'plugin.php',
        'class-wp-hook.php' => $overlay . DIRECTORY_SEPARATOR . 'wp-includes' . DIRECTORY_SEPARATOR . 'class-wp-hook.php',
        'class-wp-filter-sentinel.php' => $sentinel,
    ] as $name => $sourcePath) {
        if (!copy($sourcePath, $temp . DIRECTORY_SEPARATOR . 'wp-includes' . DIRECTORY_SEPARATOR . $name)) {
            throw new RuntimeException('overlay_temp_copy');
        }
    }
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];
    require $temp . DIRECTORY_SEPARATOR . 'wp-includes' . DIRECTORY_SEPARATOR . 'plugin.php';
    add_filter(RP_OVERLAY_ORACLE_HOOK, 'rp_overlay_oracle_first', 10, 1);
    add_filter(RP_OVERLAY_ORACLE_HOOK, 'rp_overlay_oracle_second', 20, 1);
    $hook = $GLOBALS['wp_filter'][RP_OVERLAY_ORACLE_HOOK] ?? null;
    if (!$hook instanceof WP_Hook) {
        throw new RuntimeException('selected_hook_missing');
    }
    $GLOBALS['rp_overlay_oracle_target'] = $hook;
    $GLOBALS['rp_overlay_oracle_events'] = [];
    $result = apply_filters(RP_OVERLAY_ORACLE_HOOK, 'RP Bootstrap Fixture', 'blogname');
    $callbackTable = [];
    foreach ($hook->callbacks as $priority => $bucket) {
        foreach ($bucket as $uid => $entry) {
            $callbackTable[] = [
                'priority' => $priority,
                'uid' => $uid,
                'function' => $entry['function'],
                'accepted_args' => $entry['accepted_args'],
            ];
        }
    }
    $state = [
        'nesting_level' => (new ReflectionProperty(WP_Hook::class, 'nesting_level'))->getValue($hook),
        'iterations' => (new ReflectionProperty(WP_Hook::class, 'iterations'))->getValue($hook),
        'current_priority' => (new ReflectionProperty(WP_Hook::class, 'current_priority'))->getValue($hook),
        'doing_action' => (new ReflectionProperty(WP_Hook::class, 'doing_action'))->getValue($hook),
    ];
    if ($result !== 'RP Bootstrap Fixture|first|second'
        || array_column($GLOBALS['rp_overlay_oracle_events'], 'callback') !== [
            'rp_overlay_oracle_first', 'rp_overlay_oracle_second',
        ]
        || $state !== ['nesting_level' => 0, 'iterations' => [], 'current_priority' => [], 'doing_action' => false]
        || current_filter() !== false || doing_filter() || did_filter(RP_OVERLAY_ORACLE_HOOK) !== 1
        || $GLOBALS['wp_current_filter'] !== []) {
        throw new RuntimeException('native_overlay_dispatch_mismatch');
    }
    rp_overlay_oracle_emit([
        'schema' => RP_OVERLAY_ORACLE_SCHEMA,
        'status' => 'passed',
        'wordpress_version' => '7.1.3',
        'source_sha256' => array_map(static fn(array $row): string => $row['source_sha256'], RP_OVERLAY_ORACLE_FILES),
        'overlay_sha256' => array_map(static fn(array $row): string => $row['overlay_sha256'], RP_OVERLAY_ORACLE_FILES),
        'overlay_receipt_sha256' => RP_OVERLAY_ORACLE_RECEIPT_SHA,
        'scenario' => RP_OVERLAY_ORACLE_HOOK,
        'callback_table' => $callbackTable,
        'event_order' => array_column($GLOBALS['rp_overlay_oracle_events'], 'callback'),
        'events' => $GLOBALS['rp_overlay_oracle_events'],
        'value' => $result,
        'after_dispatch' => [
            'current_filter' => current_filter(),
            'doing_filter' => doing_filter(),
            'did_filter' => did_filter(RP_OVERLAY_ORACLE_HOOK),
            'stack' => $GLOBALS['wp_current_filter'],
            'wp_hook_state' => $state,
        ],
    ]);
} catch (Throwable $error) {
    rp_overlay_oracle_emit([
        'schema' => RP_OVERLAY_ORACLE_SCHEMA,
        'status' => 'failed',
        'failure' => $error->getMessage(),
    ]);
}
