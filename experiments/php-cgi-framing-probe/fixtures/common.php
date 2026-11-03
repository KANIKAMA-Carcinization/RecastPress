<?php
declare(strict_types=1);

$rp_initial_buffer = ob_get_level();
set_error_handler(static function (int $severity): bool {
    if (!(error_reporting() & $severity)) { return false; }
    throw new RuntimeException('fixture_guard');
});
try {
    if (PHP_VERSION_ID !== 80512 || PHP_INT_SIZE !== 8 || PHP_SAPI !== 'cgi-fcgi'
        || ($_SERVER['argc'] ?? 0) !== 0 || ($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET'
        || ($_SERVER['QUERY_STRING'] ?? '') !== 'rp_request=1'
        || ($_SERVER['REQUEST_URI'] ?? '') !== '/__rp_framing.php?rp_request=1'
        || ($_SERVER['CONTENT_LENGTH'] ?? '0') !== '0' || ini_get('expose_php') !== '1'
        || !defined('RP_FRAMING_CASE') || !defined('RP_FRAMING_BYTES')
        || !defined('RP_FRAMING_BUFFERED') || !defined('RP_FRAMING_REMOVE')) {
        throw new RuntimeException('fixture_guard');
    }
    $rp_size = RP_FRAMING_BYTES;
    $rp_buffered = RP_FRAMING_BUFFERED;
    $rp_remove = RP_FRAMING_REMOVE;
    if (!in_array($rp_size, [64, 70000], true) || !is_bool($rp_buffered) || !is_bool($rp_remove)
        || RP_FRAMING_CASE !== ($rp_size === 64 ? 'small' : 'large') . '-'
            . ($rp_buffered ? 'buffered' : 'plain') . '-' . ($rp_remove ? 'remove' : 'keep')) {
        throw new RuntimeException('fixture_guard');
    }
    http_response_code(200);
    header('Content-Type: text/plain');
    if ($rp_buffered) {
        $rp_captured = '';
        ob_start();
        ob_start(static function (string $bytes) use (&$rp_captured): string {
            $rp_captured .= $bytes; return '';
        }, 4096);
        echo str_repeat('b', 8192);
        if (ob_get_level() !== $rp_initial_buffer + 2) { throw new RuntimeException('fixture_guard'); }
        ob_flush();
        if ($rp_captured !== str_repeat('b', 8192) || headers_sent()) { throw new RuntimeException('fixture_guard'); }
    }
    $rp_payload = str_repeat('0123456789abcdef', intdiv($rp_size, 16));
    $rp_result = ['schema' => 'recastpress.php-cgi-framing-php-observation.v0.5', 'passed' => true,
        'case' => RP_FRAMING_CASE, 'buffered' => $rp_buffered, 'header_remove' => $rp_remove,
        'payload_bytes' => strlen($rp_payload), 'payload_hex' => bin2hex($rp_payload),
        'payload_sha256' => hash('sha256', $rp_payload)];
    while (ob_get_level() > $rp_initial_buffer) { ob_end_clean(); }
    $rp_wire = json_encode($rp_result, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES);
    if ($rp_remove) { header_remove(); }
    http_response_code(200); header('Content-Type: application/json');
    echo $rp_wire, "\n";
    restore_error_handler();
    exit(0);
} catch (Throwable $rp_failure) {
    restore_error_handler();
    while (ob_get_level() > $rp_initial_buffer) { ob_end_clean(); }
    header_remove(); http_response_code(200); header('Content-Type: application/json');
    echo '{"schema":"recastpress.php-cgi-framing-php-observation.v0.5","passed":false,"failure":"fixture_guard"}', "\n";
    exit(1);
}
