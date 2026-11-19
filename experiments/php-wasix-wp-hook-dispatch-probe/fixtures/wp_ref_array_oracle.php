<?php
declare(strict_types=1);

$root = $argv[1] ?? throw new RuntimeException('missing pinned WordPress root');
$pinned = [
    $root . '/wp-includes/plugin.php' => 'd70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
    $root . '/wp-includes/class-wp-hook.php' => 'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
];
foreach ($pinned as $path => $sha256) {
    if (!is_file($path) || hash_file('sha256', $path) !== $sha256) {
        throw new RuntimeException('pinned WordPress hook source mismatch');
    }
}

$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];
require $root . '/wp-includes/plugin.php';
$GLOBALS['rp_ref_array_events'] = [];

function rp_ref_array_all(string $hookName, array $arguments): void
{
    $GLOBALS['rp_ref_array_events'][] = [
        'callback' => 'all',
        'hook' => $hookName,
        'args' => [$arguments[0], $arguments[1]],
        'current_filter' => current_filter(),
        'doing_filter' => doing_filter($hookName),
        'did_filter' => did_filter($hookName),
    ];
}

function rp_ref_array_first(string &$value, string &$extra): string
{
    $GLOBALS['rp_ref_array_events'][] = [
        'callback' => 'first',
        'args' => [$value, $extra],
        'current_filter' => current_filter(),
        'doing_filter' => doing_filter('rp-probe-ref-array'),
        'did_filter' => did_filter('rp-probe-ref-array'),
        'current_priority' => $GLOBALS['wp_filter']['rp-probe-ref-array']->current_priority(),
    ];
    $value = 'value-mutated-by-first';
    $extra = 'extra-mutated-by-first';
    return 'replacement-from-first';
}

function rp_ref_array_truncated(string $value, mixed ...$extra): string
{
    $args = func_get_args();
    $GLOBALS['rp_ref_array_events'][] = [
        'callback' => 'truncated', 'args' => $args, 'received_arg_count' => count($args),
        'current_filter' => current_filter(), 'doing_filter' => doing_filter('rp-probe-ref-array'),
        'did_filter' => did_filter('rp-probe-ref-array'),
        'current_priority' => $GLOBALS['wp_filter']['rp-probe-ref-array']->current_priority(),
    ];
    if ($extra !== []) {
        throw new RuntimeException('wp_ref_array_truncated_callback_received_extra');
    }
    return $value;
}

function rp_ref_array_second(string &$value, string &$extra): string
{
    $GLOBALS['rp_ref_array_events'][] = [
        'callback' => 'second',
        'args' => [$value, $extra],
        'current_filter' => current_filter(),
        'doing_filter' => doing_filter('rp-probe-ref-array'),
        'did_filter' => did_filter('rp-probe-ref-array'),
        'current_priority' => $GLOBALS['wp_filter']['rp-probe-ref-array']->current_priority(),
    ];
    $extra = 'extra-mutated-by-second';
    return $value . '|' . $extra;
}

add_filter('all', 'rp_ref_array_all', 10, 2);
add_filter('rp-probe-ref-array', 'rp_ref_array_truncated', 1, 1);
add_filter('rp-probe-ref-array', 'rp_ref_array_first', 5, 2);
add_filter('rp-probe-ref-array', 'rp_ref_array_second', 10, 2);

$value = 'seed';
$extra = 'left';
$arguments = [&$value, &$extra];
$result = apply_filters_ref_array('rp-probe-ref-array', $arguments);
$observation = [
    'events' => $GLOBALS['rp_ref_array_events'],
    'result' => $result,
    'caller_value' => $value,
    'caller_extra' => $extra,
    'did_filter' => did_filter('rp-probe-ref-array'),
    'current_filter' => current_filter(),
    'doing_filter' => doing_filter(),
];
$expected = [
    'events' => [
        [
            'callback' => 'all', 'hook' => 'rp-probe-ref-array', 'args' => ['seed', 'left'],
            'current_filter' => 'rp-probe-ref-array', 'doing_filter' => true, 'did_filter' => 1,
        ],
        [
            'callback' => 'truncated', 'args' => ['seed'], 'received_arg_count' => 1,
            'current_filter' => 'rp-probe-ref-array', 'doing_filter' => true, 'did_filter' => 1,
            'current_priority' => 1,
        ],
        [
            'callback' => 'first', 'args' => ['seed', 'left'],
            'current_filter' => 'rp-probe-ref-array', 'doing_filter' => true, 'did_filter' => 1,
            'current_priority' => 5,
        ],
        [
            'callback' => 'second', 'args' => ['replacement-from-first', 'extra-mutated-by-first'],
            'current_filter' => 'rp-probe-ref-array', 'doing_filter' => true, 'did_filter' => 1,
            'current_priority' => 10,
        ],
    ],
    'result' => 'replacement-from-first|extra-mutated-by-second',
    'caller_value' => 'replacement-from-first',
    'caller_extra' => 'extra-mutated-by-second',
    'did_filter' => 1,
    'current_filter' => false,
    'doing_filter' => false,
];
if ($observation !== $expected) {
    fwrite(STDERR, json_encode(['expected' => $expected, 'actual' => $observation], JSON_THROW_ON_ERROR) . "\n");
    throw new RuntimeException('wp_ref_array_oracle_mismatch');
}

echo json_encode([
    'status' => 'passed',
    'scenario' => 'wp_ref_array_native_oracle',
    'wordpress' => '7.1.3',
    'source_sha256' => [
        'wp-includes/plugin.php' => $pinned[$root . '/wp-includes/plugin.php'],
        'wp-includes/class-wp-hook.php' => $pinned[$root . '/wp-includes/class-wp-hook.php'],
    ],
    'observation' => $observation,
], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
