<?php
declare(strict_types=1);

if (count($argv) !== 2) {
    fwrite(STDERR, "expected fixed WordPress directory\n");
    exit(2);
}

require $argv[1] . '/wp-includes/plugin.php';

function global_state_poc_view($value): array
{
    if ($value === null) return ['type' => 'null'];
    if (is_bool($value)) return ['type' => 'bool', 'value' => $value];
    if (is_int($value)) return ['type' => 'int', 'value' => (string) $value];
    if (is_float($value)) return ['type' => 'float', 'bits' => bin2hex(pack('E', $value))];
    if (is_string($value)) return ['type' => 'string', 'hex' => bin2hex($value)];
    return ['type' => get_debug_type($value)];
}

function global_state_poc_state(string $state, string $hook): array
{
    unset($GLOBALS['wp_actions']);
    switch ($state) {
        case 'unset': break;
        case 'null': $GLOBALS['wp_actions'] = null; break;
        case 'false': $GLOBALS['wp_actions'] = false; break;
        case 'true': $GLOBALS['wp_actions'] = true; break;
        case 'int': $GLOBALS['wp_actions'] = 7; break;
        case 'float': $GLOBALS['wp_actions'] = 7.5; break;
        case 'string': $GLOBALS['wp_actions'] = 'seed'; break;
        case 'array': $GLOBALS['wp_actions'] = []; break;
        case 'object': $GLOBALS['wp_actions'] = new stdClass(); break;
        default: throw new LogicException('unknown state');
    }

    return global_state_poc_snapshot($hook);
}

function global_state_poc_snapshot(string $hook): array
{
    $present = array_key_exists('wp_actions', $GLOBALS);
    $value = $present ? $GLOBALS['wp_actions'] : null;
    $snapshot = ['present' => $present, 'type' => $present ? get_debug_type($value) : 'unset'];
    if (is_array($value)) {
        $snapshot['entries'] = [];
        foreach ($value as $key => $entry) {
            $snapshot['entries'][] = ['key_hex' => bin2hex((string) $key), 'value' => global_state_poc_view($entry)];
        }
    } else {
        $snapshot['value'] = $present ? global_state_poc_view($value) : ['type' => 'unset'];
    }
    $snapshot['hook_key_present'] = is_array($value) && array_key_exists($hook, $value);

    return $snapshot;
}

function global_state_poc_run(string $stateName, string $operation): array
{
    $hook = 'global-state-' . $stateName . ($operation === 'did_action' ? '-did' : '');
    global_state_poc_state($stateName, $hook);
    $log = [];
    $callbackCount = 0;
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_current_filter'] = [];
    if ($operation === 'do_action') {
        add_action($hook, static function () use (&$log, &$callbackCount): void {
            ++$callbackCount;
            $log[] = ['kind' => 'callback', 'current_action' => current_action(), 'doing_action' => doing_action()];
        });
    }
    $initial = global_state_poc_snapshot($hook);
    set_error_handler(static function (int $severity, string $message) use (&$log): bool {
        $log[] = ['kind' => 'warning', 'severity' => $severity, 'message_hex' => bin2hex($message)];
        return true;
    });

    $log[] = ['kind' => 'call', 'operation' => $operation, 'initial' => $initial];
    try {
        $result = $operation === 'do_action' ? do_action($hook) : did_action($hook);
        $log[] = ['kind' => 'result', 'value' => global_state_poc_view($result)];
    } catch (Throwable $throwable) {
        $log[] = ['kind' => 'throwable', 'class' => $throwable::class,
            'message_hex' => bin2hex($throwable->getMessage())];
    }

    restore_error_handler();
    $log[] = ['kind' => 'final', 'state' => global_state_poc_snapshot($hook),
        'callback_count' => $callbackCount,
        'current_filter_stack' => array_values($GLOBALS['wp_current_filter'] ?? [])];

    return $log;
}

$observations = [];
foreach (['unset', 'null', 'false', 'true', 'int', 'float', 'string', 'array', 'object'] as $state) {
    foreach (['do_action', 'did_action'] as $operation) {
        $observations[$state][$operation] = global_state_poc_run($state, $operation);
    }
}

echo json_encode(['php_version' => PHP_VERSION, 'php_int_size' => PHP_INT_SIZE,
    'wordpress_version' => '7.1.3', 'observations' => $observations],
    JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
