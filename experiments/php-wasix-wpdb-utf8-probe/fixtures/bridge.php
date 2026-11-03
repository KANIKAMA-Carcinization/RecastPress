<?php
declare(strict_types=1);
header('Content-Type: application/json', true, 200);
require __DIR__ . '/entry.php';
rp_crud_run('bridge');
