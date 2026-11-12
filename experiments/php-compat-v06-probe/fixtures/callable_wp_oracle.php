<?php

declare(strict_types=1);

if (count($argv) !== 2) {
    fwrite(STDERR, "expected fixed WordPress directory\n");
    exit(2);
}

require $argv[1] . '/wp-includes/plugin.php';
require __DIR__ . '/callable_program.php';

$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];

$result = [
    'php_version' => PHP_VERSION,
    'cases' => callable_program_run(),
];

echo json_encode($result, JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
