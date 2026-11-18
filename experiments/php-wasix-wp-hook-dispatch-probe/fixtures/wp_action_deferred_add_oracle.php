<?php
declare(strict_types=1);

$root = $argv[1] ?? throw new RuntimeException('missing pinned WordPress root');
$pinned = [
    $root . '/wp-includes/plugin.php' => 'd70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
    $root . '/wp-includes/class-wp-hook.php' => 'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
];
foreach ($pinned as $path => $sha256) {
    if (!is_file($path) || hash_file('sha256', $path) !== $sha256) {
        throw new RuntimeException('pinned WordPress deferred-add source mismatch');
    }
}

const RP_DEFERRED_ADD_HOOK = 'rp-probe-action-same-priority';
$GLOBALS['wp_filter'] = [];
$GLOBALS['wp_actions'] = [];
$GLOBALS['wp_filters'] = [];
$GLOBALS['wp_current_filter'] = [];
$GLOBALS['rp_deferred_add_events'] = [];
require $root . '/wp-includes/plugin.php';

function rp_deferred_add_state(WP_Hook $hook): array
{
    $read = static fn (string $name): mixed => (new ReflectionProperty(WP_Hook::class, $name))->getValue($hook);
    return ['nesting_level'=>$read('nesting_level'),'iterations'=>$read('iterations'),
        'current_priority'=>$read('current_priority'),'doing_action'=>$read('doing_action')];
}
function rp_deferred_add_all(string $hookName, mixed ...$args): void
{
    $GLOBALS['rp_deferred_add_events'][] = ['callback'=>'all','hook'=>$hookName,
        'args'=>func_get_args(),
        'current_action'=>current_action(),'current_filter'=>current_filter(),
        'doing_action'=>doing_action(),'did_action'=>did_action(RP_DEFERRED_ADD_HOOK),
        'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_deferred_add_record(string $callback): void
{
    $hook = $GLOBALS['rp_deferred_add_hook'];
    $GLOBALS['rp_deferred_add_events'][] = ['callback'=>$callback,
        'current_action'=>current_action(),'current_filter'=>current_filter(),
        'doing_action'=>doing_action(),'did_action'=>did_action(RP_DEFERRED_ADD_HOOK),
        'current_priority'=>$hook->current_priority(),'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_action_same_priority_mutator(): void
{
    rp_deferred_add_record('mutator');
    if (empty($GLOBALS['rp_deferred_add_registered'])) {
        $GLOBALS['rp_deferred_add_registered'] = true;
        add_action(RP_DEFERRED_ADD_HOOK, 'rp_action_same_priority_added', 5, 0);
    }
}
function rp_action_same_priority_peer(): void { rp_deferred_add_record('peer'); }
function rp_action_same_priority_added(): void { rp_deferred_add_record('added'); }
function rp_action_same_priority_tail(): void { rp_deferred_add_record('tail'); }

add_action('all', 'rp_deferred_add_all', 10, 10);
add_action(RP_DEFERRED_ADD_HOOK, 'rp_action_same_priority_mutator', 5, 0);
add_action(RP_DEFERRED_ADD_HOOK, 'rp_action_same_priority_peer', 5, 0);
add_action(RP_DEFERRED_ADD_HOOK, 'rp_action_same_priority_tail', 20, 0);
$hook = $GLOBALS['wp_filter'][RP_DEFERRED_ADD_HOOK];
$GLOBALS['rp_deferred_add_hook'] = $hook;
$results = [do_action(RP_DEFERRED_ADD_HOOK), do_action(RP_DEFERRED_ADD_HOOK)];
$registeredRows = static function () use ($hook): array {
    $rows = [];
    foreach ($hook->callbacks as $priority => $bucket) {
        foreach ($bucket as $uid => $row) {
            $rows[] = ['priority'=>(int)$priority,'uid'=>(string)$uid,
                'accepted_args'=>(int)$row['accepted_args'],'function'=>$row['function']];
        }
    }
    return $rows;
};
$observation = ['events'=>$GLOBALS['rp_deferred_add_events'],
    'pass_orders'=>[
        array_column(array_slice($GLOBALS['rp_deferred_add_events'], 0, 4), 'callback'),
        array_column(array_slice($GLOBALS['rp_deferred_add_events'], 4), 'callback'),
    ],
    'action_count'=>did_action(RP_DEFERRED_ADD_HOOK),
    'current_action'=>current_action(),'current_filter'=>current_filter(),
    'doing_action'=>doing_action(),'stack'=>$GLOBALS['wp_current_filter'],
    'hook_state'=>rp_deferred_add_state($hook),'registered_rows'=>$registeredRows()];
$eventCounters = array_column($observation['events'], 'did_action');
$expectedCounters = [1,1,1,1,2,2,2,2,2];
$expectedRows = [
    [5,'rp_action_same_priority_mutator'],[5,'rp_action_same_priority_peer'],
    [5,'rp_action_same_priority_added'],[20,'rp_action_same_priority_tail'],
];
$actualRows = array_map(static fn(array $row): array => [$row['priority'],$row['uid']], $observation['registered_rows']);
if ($results !== [null, null]
    || $observation['action_count'] !== 2
    || $observation['pass_orders'] !== [
        ['all','mutator','peer','tail'], ['all','mutator','peer','added','tail'],
    ]
    || $eventCounters !== $expectedCounters
    || $observation['current_action'] !== false || $observation['current_filter'] !== false
    || $observation['doing_action'] !== false || $observation['stack'] !== []
    || $observation['hook_state'] !== ['nesting_level'=>0,'iterations'=>[],
        'current_priority'=>[],'doing_action'=>false]
    || $actualRows !== $expectedRows
) {
    fwrite(STDERR, json_encode(['actual'=>$observation], JSON_THROW_ON_ERROR)."\n");
    throw new RuntimeException('wp_action_deferred_add_oracle_mismatch');
}
echo json_encode(['status'=>'passed','scenario'=>'wp_action_deferred_add_native_oracle',
    'wordpress'=>'7.1.3','source_sha256'=>[
        'wp-includes/plugin.php'=>$pinned[$root.'/wp-includes/plugin.php'],
        'wp-includes/class-wp-hook.php'=>$pinned[$root.'/wp-includes/class-wp-hook.php'],
    ],'observation'=>$observation], JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR)."\n";
