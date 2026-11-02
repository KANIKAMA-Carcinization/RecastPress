<?php
declare(strict_types=1);
header('Content-Type: application/json');
require __DIR__ . '/helpers.php';
require __DIR__ . '/corpus.php';
rp_run_entry('bridge');
