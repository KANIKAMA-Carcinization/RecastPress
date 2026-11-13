<?php

declare(strict_types=1);

if (count($argv) < 2 || count($argv) > 3 || (isset($argv[2]) && $argv[2] !== 'action_api')) {
    fwrite(STDERR, "expected fixed WordPress directory and optional action_api mode\n");
    exit(2);
}

require $argv[1] . '/wp-includes/plugin.php';
require __DIR__ . '/callable_program.php';

$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];

$cases = isset($argv[2])
    ? callable_program_run_action_api()
    : callable_program_run();

$result = [
    'php_version' => PHP_VERSION,
    'cases' => $cases,
];

echo json_encode($result, JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
