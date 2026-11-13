<?php
declare(strict_types=1);

if (count($argv) !== 2) {
    fwrite(STDERR, "expected fixed WordPress directory\n");
    exit(2);
}

require $argv[1] . '/wp-includes/plugin.php';

function action_counter_seed_poc_view($value): array
{
    if ($value === null) {
        return ['type' => 'null'];
    }
    if (is_bool($value)) {
        return ['type' => 'bool', 'value' => $value];
    }
    if (is_int($value)) {
        return ['type' => 'int', 'value' => (string) $value];
    }
    if (is_float($value)) {
        return ['type' => 'float', 'bits' => bin2hex(pack('E', $value))];
    }
    if (is_string($value)) {
        return ['type' => 'string', 'hex' => bin2hex($value)];
    }

    return ['type' => get_debug_type($value)];
}

function action_counter_seed_poc_leaf(string $case, $seed): array
{
    $GLOBALS['wp_filter'] = [];
    $GLOBALS['wp_actions'] = [];
    $GLOBALS['wp_filters'] = [];
    $GLOBALS['wp_current_filter'] = [];

    $hook = 'counter-seed-' . $case;
    $GLOBALS['wp_actions'][$hook] = $seed;
    $state = ['events' => [], 'throw_first' => true, 'warnings' => []];
    set_error_handler(static function (int $severity, string $message) use (&$state): bool {
        $state['warnings'][] = [
            'index' => count($state['warnings']) + 1,
            'severity' => $severity,
            'message_hex' => bin2hex($message),
        ];

        // Every warning/deprecation is preserved in the observation above.
        return true;
    });
    $capture = static function (string $event, array $args) use (&$state, $hook): void {
        $state['events'][] = [
            'event' => $event,
            'args' => $args,
            'current_action' => current_action(),
            'doing_action' => doing_action(),
            'did_action' => action_counter_seed_poc_view(did_action($hook)),
            'wp_actions' => action_counter_seed_poc_view($GLOBALS['wp_actions'][$hook] ?? null),
        ];
    };

    add_action('all', static function (...$args) use ($capture, $hook): void {
        if (($args[0] ?? null) === $hook) {
            $capture('all', $args);
        }
    }, 1, 0);
    $throwing = static function (...$args) use ($capture, &$state): string {
        $capture('named', $args);
        if ($state['throw_first']) {
            $state['throw_first'] = false;
            throw new RuntimeException('counter seed callback boom');
        }

        return 'ignored named return';
    };
    $later = static function (...$args) use ($capture): bool {
        $capture('later', $args);

        return false;
    };
    add_action($hook, $throwing, 10, 99);
    add_action($hook, $later, 20, 99);

    $before = [
        'seed' => action_counter_seed_poc_view($GLOBALS['wp_actions'][$hook]),
        'key_present' => array_key_exists($hook, $GLOBALS['wp_actions']),
        'did_action' => action_counter_seed_poc_view(did_action($hook)),
    ];
    $caught = null;
    try {
        $firstReturn = do_action($hook, 'first');
    } catch (Throwable $throwable) {
        $firstReturn = null;
        $caught = ['class' => $throwable::class, 'message' => $throwable->getMessage()];
    }
    $afterThrow = [
        'did_action' => action_counter_seed_poc_view(did_action($hook)),
        'wp_actions' => action_counter_seed_poc_view($GLOBALS['wp_actions'][$hook] ?? null),
        'current_action' => current_action(),
        'doing_action' => doing_action(),
    ];
    $followupReturn = do_action($hook, 'follow-up');
    $afterFollowup = [
        'did_action' => action_counter_seed_poc_view(did_action($hook)),
        'wp_actions' => action_counter_seed_poc_view($GLOBALS['wp_actions'][$hook] ?? null),
        'current_action' => current_action(),
        'doing_action' => doing_action(),
    ];
    restore_error_handler();

    return [
        'case' => $case,
        'before' => $before,
        'caught' => $caught,
        'first_return' => action_counter_seed_poc_view($firstReturn),
        'callback_order' => array_column($state['events'], 'event'),
        'events' => $state['events'],
        'warnings' => $state['warnings'],
        'after_throw' => $afterThrow,
        'followup_return' => action_counter_seed_poc_view($followupReturn),
        'after_followup' => $afterFollowup,
    ];
}

$seeds = [
    'null' => null,
    'bool_true' => true,
    'int' => 7,
    'float' => 7.5,
    'alpha' => 'a',
    'php_int_max' => PHP_INT_MAX,
];
$cases = [];
foreach ($seeds as $case => $seed) {
    $cases[$case] = action_counter_seed_poc_leaf($case, $seed);
}

echo json_encode([
    'php_version' => PHP_VERSION,
    'php_int_size' => PHP_INT_SIZE,
    'php_int_max' => (string) PHP_INT_MAX,
    'cases' => $cases,
], JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
