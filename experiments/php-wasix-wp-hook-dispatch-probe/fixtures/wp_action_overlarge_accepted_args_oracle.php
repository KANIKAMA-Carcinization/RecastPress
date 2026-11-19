<?php
declare(strict_types=1);

$root = $argv[1] ?? throw new RuntimeException('missing pinned WordPress root');
$pinned = [
    $root . '/wp-includes/plugin.php' => 'd70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
    $root . '/wp-includes/class-wp-hook.php' => 'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
];
foreach ($pinned as $path => $sha256) {
    if (!is_file($path) || hash_file('sha256', $path) !== $sha256) {
        throw new RuntimeException('pinned WordPress overlarge accepted_args source mismatch');
    }
}

const RP_ACTION_OVERLARGE_HOOK = 'rp-probe-action';
$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];
$GLOBALS['rp_action_overlarge_received'] = null;
require $root . '/wp-includes/plugin.php';

function rp_action_overlarge_oracle_callback(mixed ...$args): void
{
    $GLOBALS['rp_action_overlarge_received'] = $args;
}

add_action(RP_ACTION_OVERLARGE_HOOK, 'rp_action_overlarge_oracle_callback', 15, 5);
$result = do_action(RP_ACTION_OVERLARGE_HOOK, 'left', 'right');
$observation = [
    'accepted_args' => 5,
    'supplied_args' => 2,
    'received_args' => $GLOBALS['rp_action_overlarge_received'],
    'action_count' => did_action(RP_ACTION_OVERLARGE_HOOK),
    'current_action' => current_action(),
    'doing_action' => doing_action(),
    'stack' => $GLOBALS['wp_current_filter'],
];
if ($result !== null
    || $observation['received_args'] !== ['left', 'right']
    || $observation['action_count'] !== 1
    || $observation['current_action'] !== false
    || $observation['doing_action'] !== false
    || $observation['stack'] !== []
) {
    fwrite(STDERR, json_encode(['actual' => $observation], JSON_THROW_ON_ERROR) . "\n");
    throw new RuntimeException('wp_action_overlarge_accepted_args_oracle_mismatch');
}
echo json_encode([
    'status' => 'passed',
    'scenario' => 'wp_action_overlarge_accepted_args_native_oracle',
    'wordpress' => '7.1.3',
    'source_sha256' => [
        'wp-includes/plugin.php' => $pinned[$root . '/wp-includes/plugin.php'],
        'wp-includes/class-wp-hook.php' => $pinned[$root . '/wp-includes/class-wp-hook.php'],
    ],
    'observation' => $observation,
], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
