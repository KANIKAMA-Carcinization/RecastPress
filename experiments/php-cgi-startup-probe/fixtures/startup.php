<?php
declare(strict_types=1);

// No mail, URL, socket, database, WordPress or process API is called.
if (PHP_SAPI !== 'cgi-fcgi' || PHP_VERSION_ID !== 80512
    || ($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET'
    || ($_SERVER['SCRIPT_NAME'] ?? '') !== '/__rp_startup.php') {
    exit(2);
}
$input = fopen('php://stdin', 'rb');
if (!is_resource($input)) { exit(3); }
try { $wire = stream_get_contents($input, 4097); } finally { fclose($input); }
if (!is_string($wire) || strlen($wire) > 4096) { exit(4); }
$query = getenv('QUERY_STRING');
$expected = getenv('RP_EXPECTED_INI');
$loaded = php_ini_loaded_file();
$scanned = php_ini_scanned_files();
header('Content-Type: application/json');
echo json_encode([
    'schema' => 'recastpress.php-cgi-startup-observation.v0.5',
    'runtime' => ['version' => PHP_VERSION, 'version_id' => PHP_VERSION_ID,
        'int_size' => PHP_INT_SIZE, 'sapi' => PHP_SAPI],
    'profile' => ['mysqli_loaded' => extension_loaded('mysqli'),
        'mail_function_present' => function_exists('mail'),
        'allow_url_fopen' => ini_get('allow_url_fopen'),
        'register_argc_argv' => ini_get('register_argc_argv'),
        'expose_php' => ini_get('expose_php')],
    'ini_loaded' => is_string($loaded),
    'ini_loaded_expected' => is_string($expected) && is_string($loaded)
        && realpath($expected) === realpath($loaded),
    'additional_ini_scanned' => is_string($scanned) && $scanned !== '',
    'query_environment_present' => is_string($query),
    'query_string' => is_string($query) ? $query : null,
    'get_values' => $_GET,
    'argc' => $_SERVER['argc'] ?? null,
    'stdin' => ['bytes' => strlen($wire), 'sha256' => hash('sha256', $wire)],
], JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . "\n";
