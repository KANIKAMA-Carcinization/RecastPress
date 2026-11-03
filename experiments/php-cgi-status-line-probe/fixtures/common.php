<?php
declare(strict_types=1);

try {
    if (PHP_VERSION_ID !== 80512 || PHP_INT_SIZE !== 8 || PHP_SAPI !== 'cgi-fcgi'
        || ($_SERVER['argc'] ?? 0) !== 0 || ($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET'
        || ($_SERVER['QUERY_STRING'] ?? '') !== 'rp_request=1'
        || ($_SERVER['REQUEST_URI'] ?? '') !== '/__rp_framing.php?rp_request=1'
        || ($_SERVER['CONTENT_LENGTH'] ?? '0') !== '0' || ini_get('expose_php') !== '1'
        || !extension_loaded('mysqli') || function_exists('mail') || ini_get('allow_url_fopen') !== '0'
        || ini_get('register_argc_argv') !== '0'
        || !defined('RP_STATUS_CASE') || !defined('RP_STATUS_PRIOR') || !defined('RP_STATUS_SETTER')
        || !in_array(RP_STATUS_PRIOR, [200, 404], true)
        || !in_array(RP_STATUS_SETTER, ['old', 'third_argument'], true)) {
        throw new RuntimeException('fixture_guard');
    }
    header('HTTP/1.1 ' . RP_STATUS_PRIOR . (RP_STATUS_PRIOR === 200 ? ' OK' : ' Not Found'));
    header('Content-Type: text/plain');
    $rp_prior_observed = http_response_code();
    header_remove();
    if (RP_STATUS_SETTER === 'old') {
        http_response_code(200);
        header('Content-Type: application/json');
    } else {
        header('Content-Type: application/json', true, 200);
    }
    $rp_value = ['schema' => 'recastpress.php-cgi-status-line-observation.v0.5', 'passed' => true,
        'case' => RP_STATUS_CASE, 'prior_status' => RP_STATUS_PRIOR, 'setter' => RP_STATUS_SETTER,
        'prior_status_observed' => $rp_prior_observed, 'target_status' => 200,
        'status_after_setter' => http_response_code()];
    echo json_encode($rp_value, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES), "\n";
    exit(0);
} catch (Throwable $rp_failure) {
    header_remove(); header('Content-Type: application/json', true, 200);
    echo '{"schema":"recastpress.php-cgi-status-line-observation.v0.5","passed":false,"failure":"fixture_guard"}', "\n";
    exit(1);
}
