<?php
declare(strict_types=1);
$mode = $argv[1] ?? '';
$emit = static function (string $kind, int $seq, array $payload): void {
    $line = json_encode(['v'=>1,'request'=>'recovery-probe-1','seq'=>$seq,'kind'=>$kind,'payload'=>$payload], JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR)."\n";
    if (fwrite(STDOUT, $line) !== strlen($line) || !fflush(STDOUT)) exit(91);
};
$pending_root_callback = static function (string $mode) use ($emit): never {
    $emit('effect_started', 1, ['effect_id'=>'pending-root']);
    if ($mode === 'recovery_host_cancel') {
        $response = fgets(STDIN);
        if ($response === false) exit(26);
        exit(92);
    }
    if ($mode === 'recovery_root_exit_23') exit(23);
    if ($mode === 'recovery_partial_frame_exit_24') {
        $partial = '{"v":1,"request":"recovery-probe-1","seq":2,"kind":"call_core","payload":{"operation":"pending"';
        if (fwrite(STDOUT, $partial) !== strlen($partial) || !fflush(STDOUT)) exit(96);
        exit(24);
    }
    exit(94);
};
$start = json_encode(['v'=>1,'request'=>'recovery-probe-1','seq'=>0,'kind'=>'start_root','payload'=>['operation'=>'pending_root_effect']], JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR)."\n";
if (fwrite(STDOUT, $start) !== strlen($start) || !fflush(STDOUT)) exit(91);
$invoke = fgets(STDIN);
if ($invoke === false) exit(93);
$expected = "{\"v\":1,\"request\":\"recovery-probe-1\",\"seq\":0,\"kind\":\"invoke_php\",\"payload\":{\"callback\":\"pending_root_callback\",\"effect_id\":\"pending-root\"}}\n";
if ($invoke !== $expected) exit(95);
$pending_root_callback($mode);