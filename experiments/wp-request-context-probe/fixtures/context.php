<?php
declare(strict_types=1);

function rp_context_read(string $mode): array {
    global $rp_report;
    rp_boot_check($mode === 'home' && in_array(RP_REQUEST_CASE, ['home', 'missing'], true), 'context_case');
    $body = file_get_contents('php://input');
    rp_boot_check($body === '', 'public_request_body_not_empty');
    $rp_report['transport_body'] = ['bytes' => strlen($body), 'sha256' => hash('sha256', $body)];
    $stream = fopen(__DIR__ . '/context.json', 'rb');
    rp_boot_check(is_resource($stream), 'private_context_open');
    try { $wire = stream_get_contents($stream, 32769); } finally { fclose($stream); }
    rp_boot_check(is_string($wire) && strlen($wire) <= 32768, 'private_context_size');
    $context = json_decode($wire, true, 16, JSON_THROW_ON_ERROR);
    rp_boot_check(is_array($context), 'private_context_object');
    rp_boot_exact($context, ['schema', 'case', 'repository_root', 'request'], 'private_context_keys');
    rp_boot_check($context['schema'] === 'recastpress.wp-request-context-private.v0.5'
        && $context['case'] === RP_REQUEST_CASE && is_string($context['repository_root'])
        && strlen($context['repository_root']) <= 2048
        && realpath($context['repository_root']) === $context['repository_root'], 'private_context_profile');
    rp_boot_check(json_encode($context, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) === trim($wire, " \t\r\n"),
        'private_context_duplicate_or_noncanonical');
    $rp_report['private_context_before'] = ['bytes' => strlen($wire), 'sha256' => hash('sha256', $wire)];
    $loaded = php_ini_loaded_file();
    $scanned = php_ini_scanned_files();
    $rp_report['ini_profile'] = ['owned_file_loaded' => is_string($loaded)
            && realpath($loaded) === realpath(__DIR__ . '/php.ini'),
        'additional_ini_scanned' => $scanned !== false && $scanned !== '',
        'display_errors' => ini_get('display_errors'), 'log_errors' => ini_get('log_errors'),
        'register_argc_argv' => ini_get('register_argc_argv'), 'allow_url_fopen' => ini_get('allow_url_fopen'),
        'mail_function_present' => function_exists('mail'), 'mysqli_loaded' => extension_loaded('mysqli'),
        'expose_php' => ini_get('expose_php')];
    rp_boot_check($rp_report['ini_profile'] === ['owned_file_loaded' => true, 'additional_ini_scanned' => false,
        'display_errors' => '1', 'log_errors' => '0', 'register_argc_argv' => '0', 'allow_url_fopen' => '0',
        'mail_function_present' => false, 'mysqli_loaded' => true, 'expose_php' => '1'], 'sealed_ini_profile');
    return [json_encode($context['request'], JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES), $context];
}

function rp_context_public(): void {
    $missing = RP_REQUEST_CASE === 'missing';
    $query = $missing ? 'p=2147483647' : '';
    $uri = $missing ? '/?p=2147483647' : '/';
    foreach (array_keys(getenv()) as $name) {
        if (preg_match('/\A(?:HTTP_|CONTENT_|REQUEST_|SCRIPT_|SERVER_|REDIRECT_|GATEWAY_|PHP_|RP_)/i', $name)
            || in_array(strtoupper($name), ['PHPRC', 'TEMP', 'TMP', 'DOCUMENT_ROOT'], true)) { putenv($name); }
    }
    foreach (['REQUEST_METHOD' => 'GET', 'REQUEST_URI' => $uri, 'HTTP_HOST' => 'rp-fixture.invalid',
        'SERVER_NAME' => 'rp-fixture.invalid', 'SERVER_PORT' => '80', 'SERVER_PROTOCOL' => 'HTTP/1.1',
        'REQUEST_SCHEME' => 'http', 'HTTPS' => 'off'] as $name => $value) { putenv($name . '=' . $value); }
    if ($query !== '') { putenv('QUERY_STRING=' . $query); }
    $_ENV = []; $_GET = $missing ? ['p' => '2147483647'] : [];
    $_POST = []; $_COOKIE = []; $_REQUEST = $_GET;
    $_SERVER = ['REQUEST_METHOD' => 'GET', 'REQUEST_URI' => $uri, 'QUERY_STRING' => $query,
        'HTTP_HOST' => 'rp-fixture.invalid', 'SERVER_NAME' => 'rp-fixture.invalid', 'SERVER_PORT' => '80',
        'SERVER_PROTOCOL' => 'HTTP/1.1', 'HTTPS' => 'off', 'REQUEST_SCHEME' => 'http',
        'SCRIPT_NAME' => '/index.php', 'PHP_SELF' => '/index.php', 'PATH_INFO' => '',
        'SCRIPT_FILENAME' => str_replace('\\', '/', ABSPATH . 'index.php'),
        'DOCUMENT_ROOT' => str_replace('\\', '/', rtrim(ABSPATH, '/\\')), 'REMOTE_ADDR' => '127.0.0.1'];
}

function rp_context_snapshot(string $stage): array {
    rp_boot_check(in_array($stage, ['before_bootstrap', 'after_bootstrap', 'after_dispatch'], true), 'context_stage');
    $missing = RP_REQUEST_CASE === 'missing';
    $expected_get = $missing ? ['p' => '2147483647'] : [];
    $uri = $missing ? '/?p=2147483647' : '/'; $query = $missing ? 'p=2147483647' : '';
    rp_boot_check(($_SERVER['REQUEST_METHOD'] ?? null) === 'GET' && ($_SERVER['REQUEST_URI'] ?? null) === $uri
        && ($_SERVER['QUERY_STRING'] ?? null) === $query && ($_SERVER['HTTP_HOST'] ?? null) === 'rp-fixture.invalid'
        && $_GET === $expected_get && $_REQUEST === $expected_get && $_POST === [] && $_COOKIE === [], 'public_context_changed');
    $absent = ['CONTENT_TYPE', 'CONTENT_LENGTH', 'HTTP_ACCEPT', 'HTTP_COOKIE', 'HTTP_AUTHORIZATION', 'PHP_AUTH_USER', 'PHP_AUTH_PW'];
    foreach ($absent as $name) { rp_boot_check(!isset($_SERVER[$name]) && getenv($name) === false, 'private_transport_leaked'); }
    rp_boot_check(getenv('PHPRC') === false && getenv('TEMP') === false && getenv('TMP') === false
        && getenv('DOCUMENT_ROOT') === false, 'private_path_environment_leaked');
    $body = file_get_contents('php://input'); rp_boot_check($body === '', 'public_body_changed');
    $available = function_exists('wp_is_json_request');
    $json = $available ? wp_is_json_request() : null;
    rp_boot_check(($stage === 'before_bootstrap' ? !$available : $available && $json === false)
        && ini_get('display_errors') === '1', 'public_json_or_error_display_profile');
    return ['stage' => $stage, 'method' => $_SERVER['REQUEST_METHOD'], 'uri' => $_SERVER['REQUEST_URI'],
        'query' => $_SERVER['QUERY_STRING'], 'get' => $_GET, 'post' => $_POST, 'cookie' => $_COOKIE, 'request' => $_REQUEST,
        'content_type_present' => false, 'content_type' => null, 'content_length_present' => false, 'content_length' => null,
        'http_accept_present' => false, 'http_accept' => null, 'auth_or_cookie_header_present' => false,
        'private_path_environment_present' => false, 'script_filename_matches_public_entry' =>
            realpath($_SERVER['SCRIPT_FILENAME']) === realpath(ABSPATH . 'index.php'),
        'wp_is_json_request_available' => $available, 'wp_is_json_request' => $json,
        'display_errors' => ini_get('display_errors'), 'input' => ['bytes' => strlen($body), 'sha256' => hash('sha256', $body)]];
}

function rp_context_verify(): void {
    global $rp_report;
    $stream = fopen(__DIR__ . '/context.json', 'rb');
    rp_boot_check(is_resource($stream), 'private_context_after_open');
    try { $wire = stream_get_contents($stream, 32769); } finally { fclose($stream); }
    rp_boot_check(is_string($wire) && strlen($wire) <= 32768, 'private_context_after_size');
    $rp_report['private_context_after'] = ['bytes' => strlen($wire), 'sha256' => hash('sha256', $wire)];
    rp_boot_check($rp_report['private_context_after'] === $rp_report['private_context_before'], 'private_context_changed');
}
