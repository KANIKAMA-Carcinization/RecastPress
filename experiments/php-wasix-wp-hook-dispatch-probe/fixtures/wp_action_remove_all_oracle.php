<?php
declare(strict_types=1);

$root=$argv[1]??throw new RuntimeException('missing pinned WordPress root');
$pinned=[
    $root.'/wp-includes/plugin.php'=>'d70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
    $root.'/wp-includes/class-wp-hook.php'=>'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
];
foreach($pinned as $path=>$sha256) if(!is_file($path)||hash_file('sha256',$path)!==$sha256) {
    throw new RuntimeException('pinned WordPress action-removal source mismatch');
}
const RP_REMOVE_ALL_HOOK='rp-probe-action-remove-all';
$GLOBALS['wp_filter']=[];$GLOBALS['wp_actions']=[];$GLOBALS['wp_filters']=[];
$GLOBALS['wp_current_filter']=[];$GLOBALS['rp_remove_all_oracle_events']=[];
require $root.'/wp-includes/plugin.php';

function rp_remove_all_oracle_state(WP_Hook $hook):array
{
    $read=static fn(string $key):mixed=>(new ReflectionProperty(WP_Hook::class,$key))->getValue($hook);
    return ['nesting_level'=>$read('nesting_level'),'iterations'=>$read('iterations'),
        'current_priority'=>$read('current_priority'),'doing_action'=>$read('doing_action')];
}
function rp_remove_all_oracle_rows(WP_Hook $hook):array
{
    $rows=[];foreach($hook->callbacks as $priority=>$bucket)foreach($bucket as $uid=>$row) {
        $rows[]=['priority'=>(int)$priority,'uid'=>(string)$uid,
            'accepted_args'=>(int)$row['accepted_args'],'function'=>$row['function']];
    }return $rows;
}
function rp_remove_all_oracle_wp_hook_doing_action(WP_Hook $hook):bool
{
    return (new ReflectionProperty(WP_Hook::class,'doing_action'))->getValue($hook);
}
function rp_remove_all_oracle_all(string $hookName,mixed ...$args):void
{
    $GLOBALS['rp_remove_all_oracle_events'][]=['callback'=>'all','hook'=>$hookName,'args'=>func_get_args(),
        'current_action'=>current_action(),'current_filter'=>current_filter(),'doing_action'=>doing_action(),
        'did_action'=>did_action(RP_REMOVE_ALL_HOOK),
        'wp_hook_doing_action'=>rp_remove_all_oracle_wp_hook_doing_action($GLOBALS['wp_filter']['all']),
        'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_action_remove_all_observer():void
{
    $GLOBALS['rp_remove_all_oracle_events'][]=['callback'=>'observer','current_action'=>current_action(),
        'current_filter'=>current_filter(),'doing_action'=>doing_action(),'did_action'=>did_action(RP_REMOVE_ALL_HOOK),
        'current_priority'=>$GLOBALS['rp_remove_all_oracle_hook']->current_priority(),
        'wp_hook_doing_action'=>rp_remove_all_oracle_wp_hook_doing_action($GLOBALS['rp_remove_all_oracle_hook']),
        'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_action_remove_all_mutator():void
{
    $hook=$GLOBALS['rp_remove_all_oracle_hook'];
    $GLOBALS['rp_remove_all_oracle_events'][]=['callback'=>'mutator','current_action'=>current_action(),
        'current_filter'=>current_filter(),'doing_action'=>doing_action(),'did_action'=>did_action(RP_REMOVE_ALL_HOOK),
        'current_priority'=>$hook->current_priority(),'wp_hook_doing_action'=>rp_remove_all_oracle_wp_hook_doing_action($hook),
        'stack'=>$GLOBALS['wp_current_filter']];
    if(!remove_all_filters(RP_REMOVE_ALL_HOOK)) throw new RuntimeException('native remove_all_filters returned false');
    $GLOBALS['rp_remove_all_oracle_events'][]=['callback'=>'cleared','current_action'=>current_action(),
        'current_filter'=>current_filter(),'doing_action'=>doing_action(),'did_action'=>did_action(RP_REMOVE_ALL_HOOK),
        'current_priority'=>$hook->current_priority(),'global_hook_present'=>isset($GLOBALS['wp_filter'][RP_REMOVE_ALL_HOOK]),
        'wp_hook_doing_action'=>rp_remove_all_oracle_wp_hook_doing_action($hook),
        'rows'=>rp_remove_all_oracle_rows($hook),'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_action_remove_all_peer():void
{
    $GLOBALS['rp_remove_all_oracle_events'][]=['callback'=>'peer','current_action'=>current_action(),
        'current_filter'=>current_filter(),'doing_action'=>doing_action(),'did_action'=>did_action(RP_REMOVE_ALL_HOOK),
        'current_priority'=>$GLOBALS['rp_remove_all_oracle_hook']->current_priority(),
        'wp_hook_doing_action'=>rp_remove_all_oracle_wp_hook_doing_action($GLOBALS['rp_remove_all_oracle_hook']),
        'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_action_remove_all_pending():void { throw new RuntimeException('native removed future callback was invoked'); }
function rp_action_remove_all_tail():void { throw new RuntimeException('native removed tail callback was invoked'); }

add_action('all','rp_remove_all_oracle_all',10,10);
add_action(RP_REMOVE_ALL_HOOK,'rp_action_remove_all_observer',1,0);
add_action(RP_REMOVE_ALL_HOOK,'rp_action_remove_all_mutator',5,0);
add_action(RP_REMOVE_ALL_HOOK,'rp_action_remove_all_peer',5,0);
add_action(RP_REMOVE_ALL_HOOK,'rp_action_remove_all_pending',8,0);
add_action(RP_REMOVE_ALL_HOOK,'rp_action_remove_all_tail',10,0);
$hook=$GLOBALS['wp_filter'][RP_REMOVE_ALL_HOOK];$GLOBALS['rp_remove_all_oracle_hook']=$hook;
$result=do_action(RP_REMOVE_ALL_HOOK);
$observation=['events'=>$GLOBALS['rp_remove_all_oracle_events'],'action_count'=>did_action(RP_REMOVE_ALL_HOOK),
    'current_action'=>current_action(),'current_filter'=>current_filter(),'doing_action'=>doing_action(),
    'stack'=>$GLOBALS['wp_current_filter'],'hook_state'=>rp_remove_all_oracle_state($hook),
    'registered_rows'=>rp_remove_all_oracle_rows($hook)];
if($result!==null || array_column($observation['events'],'callback')!==['all','observer','mutator','cleared','peer']
    || $observation['action_count']!==1 || $observation['current_action']!==false
    || $observation['current_filter']!==false || $observation['doing_action']!==false
    || $observation['stack']!==[]
    || $observation['hook_state']!==['nesting_level'=>0,'iterations'=>[],'current_priority'=>[],'doing_action'=>false]
    || $observation['registered_rows']!==[] || isset($GLOBALS['wp_filter'][RP_REMOVE_ALL_HOOK])) {
    fwrite(STDERR,json_encode(['actual'=>$observation],JSON_THROW_ON_ERROR)."\n");
    throw new RuntimeException('wp_action_remove_all_oracle_mismatch');
}
echo json_encode(['status'=>'passed','scenario'=>'wp_action_remove_all_native_oracle','wordpress'=>'7.1.3',
    'source_sha256'=>['wp-includes/plugin.php'=>$pinned[$root.'/wp-includes/plugin.php'],
        'wp-includes/class-wp-hook.php'=>$pinned[$root.'/wp-includes/class-wp-hook.php']],
    'observation'=>$observation],JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR)."\n";
