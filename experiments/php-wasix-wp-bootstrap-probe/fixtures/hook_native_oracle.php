<?php
declare(strict_types=1);

/**
 * Native oracle for a narrow option_blogname filter contract. This loads only
 * the pinned native plugin API and WP_Hook classes; it does not run
 * wp-settings.php, connect to a database, or start WordPress.
 */

const RP_HOOK_ORACLE_SCHEMA = 'recastpress.wp-bootstrap-hook-native-oracle.v0.1';
const RP_HOOK_ORACLE_NAME = 'option_blogname';
const RP_HOOK_ORACLE_INPUT = 'RP Bootstrap Fixture';
const RP_HOOK_ORACLE_SOURCE = [
    'wp-includes/plugin.php' => 'd70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
    'wp-includes/class-wp-hook.php' => 'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
];

function rp_hook_oracle_reflection_value(WP_Hook $hook, string $name): mixed {
    $property = new ReflectionProperty(WP_Hook::class, $name);
    return $property->getValue($hook);
}

function rp_hook_oracle_state(string $callback, mixed $value, int $argument_count): array {
    $hook = $GLOBALS['wp_filter'][RP_HOOK_ORACLE_NAME] ?? null;
    if (!$hook instanceof WP_Hook) {
        throw new RuntimeException('selected_hook_missing');
    }

    return [
        'callback' => $callback,
        'current_filter' => current_filter(),
        'doing_filter' => doing_filter(),
        'doing_selected_filter' => doing_filter(RP_HOOK_ORACLE_NAME),
        'did_filter' => did_filter(RP_HOOK_ORACLE_NAME),
        'current_priority' => $hook->current_priority(),
        'stack' => $GLOBALS['wp_current_filter'] ?? [],
        'value' => $value,
        'argument_count' => $argument_count,
    ];
}

function rp_boot_hook_first(mixed $value): string {
    $GLOBALS['rp_hook_oracle_events'][] = rp_hook_oracle_state(__FUNCTION__, $value, func_num_args());
    return $value . '|first';
}

function rp_boot_hook_second(mixed $value): string {
    $GLOBALS['rp_hook_oracle_events'][] = rp_hook_oracle_state(__FUNCTION__, $value, func_num_args());
    return $value . '|second';
}

function rp_hook_oracle_hook_state(WP_Hook $hook): array {
    return [
        'nesting_level' => rp_hook_oracle_reflection_value($hook, 'nesting_level'),
        'iterations' => rp_hook_oracle_reflection_value($hook, 'iterations'),
        'current_priority' => rp_hook_oracle_reflection_value($hook, 'current_priority'),
        'doing_action' => rp_hook_oracle_reflection_value($hook, 'doing_action'),
    ];
}

function rp_hook_oracle_emit(array $value): never {
    $wire = json_encode($value, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    if (strlen($wire) > 8192) {
        $wire = json_encode([
            'schema' => RP_HOOK_ORACLE_SCHEMA,
            'status' => 'failed',
            'failure' => 'result_size_limit',
        ], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    }
    echo $wire, "\n";
    exit;
}

if (PHP_SAPI !== 'cli' || $argc !== 1) {
    rp_hook_oracle_emit([
        'schema' => RP_HOOK_ORACLE_SCHEMA,
        'status' => 'failed',
        'failure' => 'cli_entry_guard',
    ]);
}

ini_set('display_errors', '0');
error_reporting(E_ALL);
set_error_handler(static function (int $severity): never {
    throw new ErrorException('native_oracle_warning', 0, $severity);
});
ob_start();

try {
    $repo_root = dirname(__DIR__, 3);
    $wordpress_root = $repo_root . DIRECTORY_SEPARATOR . 'target'
        . DIRECTORY_SEPARATOR . 'wp-bootstrap-probe' . DIRECTORY_SEPARATOR . 'site';
    if (realpath($repo_root) !== $repo_root || realpath($wordpress_root) !== $wordpress_root) {
        throw new RuntimeException('fixed_wordpress_tree_missing');
    }

    foreach (RP_HOOK_ORACLE_SOURCE as $relative => $expected_sha256) {
        $path = $wordpress_root . DIRECTORY_SEPARATOR
            . str_replace('/', DIRECTORY_SEPARATOR, $relative);
        if (is_link($path) || !is_file($path)
            || hash_file('sha256', $path) !== $expected_sha256) {
            throw new RuntimeException('pinned_wordpress_source_mismatch');
        }
    }

    $version_path = $wordpress_root . DIRECTORY_SEPARATOR . 'wp-includes'
        . DIRECTORY_SEPARATOR . 'version.php';
    if (is_link($version_path) || !is_file($version_path)) {
        throw new RuntimeException('pinned_wordpress_version_missing');
    }
    require $version_path;
    if (($wp_version ?? null) !== '7.1.3') {
        throw new RuntimeException('pinned_wordpress_version_mismatch');
    }

    define('ABSPATH', rtrim($wordpress_root, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR);
    require ABSPATH . 'wp-includes' . DIRECTORY_SEPARATOR . 'plugin.php';

    $GLOBALS['rp_hook_oracle_events'] = [];
    add_filter(RP_HOOK_ORACLE_NAME, 'rp_boot_hook_first', 10, 1);
    add_filter(RP_HOOK_ORACLE_NAME, 'rp_boot_hook_second', 20, 1);

    $hook = $GLOBALS['wp_filter'][RP_HOOK_ORACLE_NAME] ?? null;
    if (!$hook instanceof WP_Hook
        || count($hook->callbacks) !== 2
        || ($hook->callbacks[10][ 'rp_boot_hook_first' ]['accepted_args'] ?? null) !== 1
        || ($hook->callbacks[20][ 'rp_boot_hook_second' ]['accepted_args'] ?? null) !== 1) {
        throw new RuntimeException('native_callback_table_mismatch');
    }

    $filtered_value = apply_filters(RP_HOOK_ORACLE_NAME, RP_HOOK_ORACLE_INPUT, 'blogname');

    $final_hook = $GLOBALS['wp_filter'][RP_HOOK_ORACLE_NAME] ?? null;
    if (!$final_hook instanceof WP_Hook || count($GLOBALS['rp_hook_oracle_events']) !== 2) {
        throw new RuntimeException('native_dispatch_incomplete');
    }
    $events = $GLOBALS['rp_hook_oracle_events'];
    $names = array_column($events, 'callback');
    if ($names !== ['rp_boot_hook_first', 'rp_boot_hook_second']) {
        throw new RuntimeException('native_callback_order_mismatch');
    }
    $expected_values = [RP_HOOK_ORACLE_INPUT, RP_HOOK_ORACLE_INPUT . '|first'];
    foreach ($events as $index => $event) {
        if ($event['value'] !== $expected_values[$index]
            || $event['argument_count'] !== 1
            || $event['current_filter'] !== RP_HOOK_ORACLE_NAME
            || $event['doing_filter'] !== true
            || $event['doing_selected_filter'] !== true
            || $event['did_filter'] !== 1
            || $event['current_priority'] !== ($index === 0 ? 10 : 20)
            || $event['stack'] !== [RP_HOOK_ORACLE_NAME]) {
            throw new RuntimeException('native_filter_state_mismatch');
        }
    }
    if ($filtered_value !== RP_HOOK_ORACLE_INPUT . '|first|second') {
        throw new RuntimeException('native_filter_value_mismatch');
    }

    $after_dispatch = [
        'current_filter' => current_filter(),
        'doing_filter' => doing_filter(),
        'doing_selected_filter' => doing_filter(RP_HOOK_ORACLE_NAME),
        'did_filter' => did_filter(RP_HOOK_ORACLE_NAME),
        'stack' => $GLOBALS['wp_current_filter'] ?? [],
        'hook_state' => rp_hook_oracle_hook_state($final_hook),
    ];
    if ($after_dispatch['current_filter'] !== false
        || $after_dispatch['doing_filter'] !== false
        || $after_dispatch['doing_selected_filter'] !== false
        || $after_dispatch['did_filter'] !== 1
        || $after_dispatch['stack'] !== []
        || $after_dispatch['hook_state'] !== [
            'nesting_level' => 0,
            'iterations' => [],
            'current_priority' => [],
            'doing_action' => false,
        ]) {
        throw new RuntimeException('native_filter_reset_mismatch');
    }

    $GLOBALS['rp_hook_oracle_output'] = [
        'schema' => RP_HOOK_ORACLE_SCHEMA,
        'status' => 'passed',
        'scope' => 'Pinned native WordPress 7.1.3 plugin API and WP_Hook only; no wp-settings.php bootstrap, database, plugins, themes, or network.',
        'wordpress_version' => $wp_version,
        'wordpress_source_sha256' => RP_HOOK_ORACLE_SOURCE,
        'scenario' => RP_HOOK_ORACLE_NAME,
        'input_value' => RP_HOOK_ORACLE_INPUT,
        'extra_args' => ['blogname'],
        'registered_callbacks' => [
            ['callback' => 'rp_boot_hook_first', 'priority' => 10, 'accepted_args' => 1],
            ['callback' => 'rp_boot_hook_second', 'priority' => 20, 'accepted_args' => 1],
        ],
        'event_order' => $names,
        'events' => $events,
        'filtered_value' => $filtered_value,
        'after_dispatch' => $after_dispatch,
    ];
    ob_end_clean();
    rp_hook_oracle_emit($GLOBALS['rp_hook_oracle_output']);
} catch (Throwable $error) {
    if (ob_get_level() > 0) {
        ob_end_clean();
    }
    rp_hook_oracle_emit([
        'schema' => RP_HOOK_ORACLE_SCHEMA,
        'status' => 'failed',
        'failure' => match (true) {
            str_contains($error->getMessage(), 'fixed_wordpress_tree_missing') => 'fixed_wordpress_tree_missing',
            str_contains($error->getMessage(), 'pinned_wordpress_source_mismatch') => 'pinned_wordpress_source_mismatch',
            str_contains($error->getMessage(), 'pinned_wordpress_version_missing') => 'pinned_wordpress_version_missing',
            str_contains($error->getMessage(), 'pinned_wordpress_version_mismatch') => 'pinned_wordpress_version_mismatch',
            str_contains($error->getMessage(), 'native_callback_table_mismatch') => 'native_callback_table_mismatch',
            str_contains($error->getMessage(), 'native_dispatch_incomplete') => 'native_dispatch_incomplete',
            str_contains($error->getMessage(), 'native_callback_order_mismatch') => 'native_callback_order_mismatch',
            str_contains($error->getMessage(), 'native_filter_state_mismatch') => 'native_filter_state_mismatch',
            str_contains($error->getMessage(), 'native_filter_value_mismatch') => 'native_filter_value_mismatch',
            str_contains($error->getMessage(), 'native_filter_reset_mismatch') => 'native_filter_reset_mismatch',
            str_contains($error->getMessage(), 'selected_hook_missing') => 'selected_hook_missing',
            default => 'native_oracle_execution_failed',
        },
    ]);
}
