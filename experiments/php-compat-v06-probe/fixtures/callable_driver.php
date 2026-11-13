<?php

declare(strict_types=1);

require_once __DIR__ . '/bridge_driver.php';
require_once __DIR__ . '/callable_adapter.php';
require_once __DIR__ . '/callable_program.php';

use RecastPress\V06\Handles\HandleRegistry;

if (count($argv) !== 2 || !in_array($argv[1], ['callable_api', 'action_api'], true)) {
    fwrite(STDERR, "callable fixture requires callable_api or action_api mode\n");
    exit(2);
}

try {
    $wire = new BridgeProtocol();
    $registry = new HandleRegistry('71');
    $adapter = new CallableApiAdapter($wire, $registry);
    CallableApiAdapter::bind($adapter);

    $wire->send('start_root', [
        'operation' => $argv[1],
        'protocol' => 2,
        'request_generation' => '71',
    ]);

    $rootInvoke = $wire->receive();
    expect_kind($rootInvoke, 'invoke_php');
    expect_parent_effect($rootInvoke, null);
    expect_payload_keys($rootInvoke, ['operation', 'effect_id'], 'callable_root_script');
    if ($rootInvoke['payload']['operation'] !== 'run_script'
        || !is_string($rootInvoke['payload']['effect_id'])
    ) {
        throw new RuntimeException('callable_root_script_mismatch');
    }

    $rootEffect = $rootInvoke['payload']['effect_id'];
    $adapter->setRootEffect($rootEffect);
    $observation = $argv[1] === 'action_api'
        ? callable_program_run_action_api()
        : callable_program_run();
    $wire->send('resume', [
        'effect_id' => $rootEffect,
        'completion' => [
            'kind' => 'value',
            'value' => $observation,
        ],
    ]);

    while (true) {
        $frame = $wire->receive();
        if ($frame['kind'] === 'invoke_php') {
            $adapter->handleTerminalInvoke($frame);
            continue;
        }

        expect_kind($frame, 'return');
        expect_parent_effect($frame, null);
        expect_payload_keys($frame, ['operation', 'status'], 'callable_root_return');
        if ($frame['payload']['operation'] !== $argv[1]
            || $frame['payload']['status'] !== 'ok'
        ) {
            throw new RuntimeException('callable_root_return_mismatch');
        }
        break;
    }

    $wire->send('request_finished', [
        'status' => 'ok',
        'php_observation' => $observation,
        'registry_observation' => $adapter->registryObservation(),
        'transport' => $wire->stats(),
    ]);
} catch (Throwable $error) {
    fail_bridge($error);
}
