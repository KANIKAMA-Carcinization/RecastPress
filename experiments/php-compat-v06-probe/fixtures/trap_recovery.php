<?php
declare(strict_types=1);
$start = "{\"v\":1,\"request\":\"trap-probe-1\",\"seq\":0,\"kind\":\"start_root\",\"payload\":{\"operation\":\"metered_callback_loop\"}}\n";
if (fwrite(STDOUT, $start) !== strlen($start) || !fflush(STDOUT)) exit(91);
$invoke = fgets(STDIN);
$expected = "{\"v\":1,\"request\":\"trap-probe-1\",\"seq\":0,\"kind\":\"invoke_php\",\"payload\":{\"callback\":\"metered_callback_loop\",\"effect_id\":\"pending-trap\"}}\n";
if ($invoke !== $expected) exit(95);
$metered_callback_loop = static function (): never {
    $checkpoint = "{\"v\":1,\"request\":\"trap-probe-1\",\"seq\":1,\"kind\":\"effect_started\",\"payload\":{\"effect_id\":\"pending-trap\"}}\n";
    if (fwrite(STDOUT, $checkpoint) !== strlen($checkpoint) || !fflush(STDOUT)) exit(91);
    while (true) {}
};
$metered_callback_loop();
