<?php
declare(strict_types=1);

require_once __DIR__ . '/bridge_driver.php';
require_once __DIR__ . '/plugin.php';

use RecastPress\V06\Handles\HandleRegistry;

const RP_REMOVE_ALL_REQUEST = '81';
const RP_REMOVE_ALL_TARGET = 'wp-action-remove-all-1';
const RP_REMOVE_ALL_HOOK = 'rp-probe-action-remove-all';

function recastpress_wp_should_dispatch_selected_action(string $hookName, object $hook): bool
{
    return $hookName === RP_REMOVE_ALL_HOOK && $hook === ($GLOBALS['rp_remove_all_hook'] ?? null);
}
function recastpress_wp_should_dispatch_selected_hook(string $hookName, object $hook): bool
{
    return recastpress_wp_should_dispatch_selected_action($hookName, $hook);
}
function recastpress_wp_dispatch_selected_hook(...$args): never
{
    throw new RuntimeException('action_remove_all_entered_filter_handoff');
}
function rp_remove_all_rows(WP_Hook $hook): array
{
    $rows = [];
    foreach ($hook->callbacks as $priority => $bucket) {
        foreach ($bucket as $uid => $entry) {
            $rows[] = ['priority'=>(int)$priority,'uid'=>(string)$uid,
                'accepted_args'=>(int)$entry['accepted_args'],'function'=>$entry['function']];
        }
    }
    return $rows;
}
function rp_remove_all_same_handle(array $a, array $b): bool
{
    ksort($a); ksort($b); return $a === $b;
}
function rp_remove_all_fingerprint(array $rows): string
{
    usort($rows, static fn(array $a,array $b):int=>$a['priority']<=>$b['priority']);
    $projection=array_map(static fn(array $row):array=>[
        $row['priority'],$row['uid'],$row['accepted_args'],$row['function'],
        $row['handle']['request'],$row['handle']['kind'],$row['handle']['slot'],
        $row['handle']['generation'],$row['handle']['token'],
    ],$rows);
    return hash('sha256',json_encode($projection,JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR));
}
function rp_remove_all_state(WP_Hook $hook): array
{
    $read=static fn(string $key):mixed=>(new ReflectionProperty(WP_Hook::class,$key))->getValue($hook);
    return ['nesting_level'=>$read('nesting_level'),'iterations'=>$read('iterations'),
        'current_priority'=>$read('current_priority'),'doing_action'=>$read('doing_action')];
}
function rp_remove_all_wp_hook_doing_action(WP_Hook $hook): bool
{
    return (new ReflectionProperty(WP_Hook::class,'doing_action'))->getValue($hook);
}
function recastpress_wp_dispatch_selected_action(
    string $hookName, WP_Hook $hook, array $args, int $nestingLevel, Closure $sync,
): null {
    $wire=$GLOBALS['rp_remove_all_wire'];
    $registry=$GLOBALS['rp_remove_all_registry'];
    $rows=rp_remove_all_rows($hook);
    if(count($rows)!==5) throw new RuntimeException('action_remove_all_initial_rows');
    $handles=[]; $exported=[];
    foreach($rows as $row) {
        $handle=$registry->retainCallable($row['function']);
        $handles[$row['uid']]=$handle;
        $exported[]=['priority'=>$row['priority'],'uid'=>$row['uid'],
            'accepted_args'=>$row['accepted_args'],'function'=>$row['function'],'handle'=>$handle];
    }
    $GLOBALS['rp_remove_all_handles']=$handles;
    $GLOBALS['rp_remove_all_rows']=$exported;
    $GLOBALS['rp_remove_all_released']=[];
    $wire->send('action_dispatch_start',['generation'=>RP_REMOVE_ALL_REQUEST,'target'=>RP_REMOVE_ALL_TARGET,
        'hook'=>$hookName,'args'=>$args,'rows'=>$exported,'table_fingerprint'=>rp_remove_all_fingerprint($exported)]);
    while(true) {
        $frame=$wire->receive();
        if($frame['kind']==='invoke_php') {
            expect_parent_effect($frame,null);
            expect_payload_keys($frame,['target','uid','handle','priority','call_mode','args'],'action_remove_all_invoke');
            $call=$frame['payload']; $known=$handles[$call['uid']]??null;
            if($call['target']!==RP_REMOVE_ALL_TARGET || !is_array($known)
                || !rp_remove_all_same_handle($known,$call['handle'])
                || !is_int($call['priority']) || $call['call_mode']!=='zero' || $call['args']!==[]) {
                throw new RuntimeException('action_remove_all_invoke_identity');
            }
            $nextPriority = !empty($GLOBALS['rp_remove_all_cleared']) ? false : $call['priority'];
            if(!$sync('set_priority',$nextPriority)) throw new RuntimeException('action_remove_all_priority_sync');
            call_user_func_array($registry->resolve($call['handle'],'callable'),$call['args']);
            if($call['uid']==='rp_action_remove_all_mutator') {
                if (rp_remove_all_rows($hook) !== [] || isset($GLOBALS['wp_filter'][RP_REMOVE_ALL_HOOK])) {
                    throw new RuntimeException('action_remove_all_table_not_cleared');
                }
                $wire->send('action_callbacks_removed', [
                    'target'=>RP_REMOVE_ALL_TARGET,
                    'parent_uid'=>$call['uid'],
                    'rows'=>$exported,
                    'table_fingerprint'=>rp_remove_all_fingerprint([]),
                ]);
                $ack=$wire->receive();
                expect_parent_effect($ack,null);
                expect_kind($ack,'action_callbacks_removed_ack');
                expect_payload_keys($ack,['target','removed_count'],'action_remove_all_ack');
                if ($ack['payload']['target']!==RP_REMOVE_ALL_TARGET
                    || $ack['payload']['removed_count']!==count($exported)) {
                    throw new RuntimeException('action_remove_all_ack_identity');
                }
                $GLOBALS['rp_remove_all_cleared']=true;
            }
            $wire->send('resume_value',['target'=>RP_REMOVE_ALL_TARGET,'uid'=>$call['uid'],'value'=>null]);
            continue;
        }
        if($frame['kind']==='release_php') {
            expect_parent_effect($frame,null); expect_payload_keys($frame,['handle'],'action_remove_all_release');
            $handle=$frame['payload']['handle']; $found=false;
            foreach($handles as $uid=>$known) if(rp_remove_all_same_handle($known,$handle) && !isset($GLOBALS['rp_remove_all_released'][$uid])) {
                $GLOBALS['rp_remove_all_released'][$uid]=true; $found=true; break;
            }
            if(!$found) throw new RuntimeException('action_remove_all_release_identity');
            $registry->release($handle); $wire->send('release_ack',['handle'=>$handle]); continue;
        }
        if($frame['kind']==='action_complete') {
            expect_parent_effect($frame,null); expect_payload_keys($frame,['target','invocations'],'action_remove_all_complete');
            $actual=rp_remove_all_rows($hook);
            if($frame['payload']['target']!==RP_REMOVE_ALL_TARGET || $frame['payload']['invocations']!==3
                || $actual!==[] || isset($GLOBALS['wp_filter'][RP_REMOVE_ALL_HOOK]) || !$sync('finish')) throw new RuntimeException('action_remove_all_completion_state');
            return null;
        }
        throw new RuntimeException('action_remove_all_unexpected_frame');
    }
}
function rp_action_remove_all_all(string $hookName,mixed ...$args):void
{
    $GLOBALS['rp_remove_all_events'][]=['callback'=>'all','hook'=>$hookName,'args'=>func_get_args(),
        'current_action'=>current_action(),'current_filter'=>current_filter(),'doing_action'=>doing_action(),
        'did_action'=>did_action(RP_REMOVE_ALL_HOOK),'wp_hook_doing_action'=>rp_remove_all_wp_hook_doing_action($GLOBALS['wp_filter']['all']),
        'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_action_remove_all_observer():void
{
    $GLOBALS['rp_remove_all_events'][]=['callback'=>'observer','current_action'=>current_action(),
        'current_filter'=>current_filter(),'doing_action'=>doing_action(),'did_action'=>did_action(RP_REMOVE_ALL_HOOK),
        'current_priority'=>$GLOBALS['rp_remove_all_hook']->current_priority(),
        'wp_hook_doing_action'=>rp_remove_all_wp_hook_doing_action($GLOBALS['rp_remove_all_hook']),
        'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_action_remove_all_mutator():void
{
    $hook=$GLOBALS['rp_remove_all_hook'];
    $GLOBALS['rp_remove_all_events'][]=['callback'=>'mutator','current_action'=>current_action(),
        'current_filter'=>current_filter(),'doing_action'=>doing_action(),'did_action'=>did_action(RP_REMOVE_ALL_HOOK),
        'current_priority'=>$hook->current_priority(),'wp_hook_doing_action'=>rp_remove_all_wp_hook_doing_action($hook),
        'stack'=>$GLOBALS['wp_current_filter']];
    if (!remove_all_filters(RP_REMOVE_ALL_HOOK)) throw new RuntimeException('native remove_all_filters returned false');
    $GLOBALS['rp_remove_all_events'][]=['callback'=>'cleared','current_action'=>current_action(),
        'current_filter'=>current_filter(),'doing_action'=>doing_action(),'did_action'=>did_action(RP_REMOVE_ALL_HOOK),
        'current_priority'=>$hook->current_priority(),'global_hook_present'=>isset($GLOBALS['wp_filter'][RP_REMOVE_ALL_HOOK]),
        'wp_hook_doing_action'=>rp_remove_all_wp_hook_doing_action($hook),
        'rows'=>rp_remove_all_rows($hook),'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_action_remove_all_peer():void
{
    $GLOBALS['rp_remove_all_events'][]=['callback'=>'peer','current_action'=>current_action(),
        'current_filter'=>current_filter(),'doing_action'=>doing_action(),'did_action'=>did_action(RP_REMOVE_ALL_HOOK),
        'current_priority'=>$GLOBALS['rp_remove_all_hook']->current_priority(),
        'wp_hook_doing_action'=>rp_remove_all_wp_hook_doing_action($GLOBALS['rp_remove_all_hook']),
        'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_action_remove_all_pending():void { throw new RuntimeException('removed_future_callback_was_invoked'); }
function rp_action_remove_all_tail():void { throw new RuntimeException('removed_tail_callback_was_invoked'); }

try {
    $wire=new BridgeProtocol(); $registry=new HandleRegistry(RP_REMOVE_ALL_REQUEST,64,64);
    $GLOBALS['rp_remove_all_wire']=$wire; $GLOBALS['rp_remove_all_registry']=$registry;
    $GLOBALS['rp_remove_all_events']=[]; $GLOBALS['rp_remove_all_cleared']=false; $GLOBALS['wp_filter']=[]; $GLOBALS['wp_actions']=[];
    $GLOBALS['wp_filters']=[]; $GLOBALS['wp_current_filter']=[];
    add_action('all','rp_action_remove_all_all',10,10);
    add_action(RP_REMOVE_ALL_HOOK,'rp_action_remove_all_observer',1,0);
    add_action(RP_REMOVE_ALL_HOOK,'rp_action_remove_all_mutator',5,0);
    add_action(RP_REMOVE_ALL_HOOK,'rp_action_remove_all_peer',5,0);
    add_action(RP_REMOVE_ALL_HOOK,'rp_action_remove_all_pending',8,0);
    add_action(RP_REMOVE_ALL_HOOK,'rp_action_remove_all_tail',10,0);
    $hook=$GLOBALS['wp_filter'][RP_REMOVE_ALL_HOOK]; $GLOBALS['rp_remove_all_hook']=$hook;
    $result=do_action(RP_REMOVE_ALL_HOOK);
    $state=['events'=>$GLOBALS['rp_remove_all_events'],'action_count'=>did_action(RP_REMOVE_ALL_HOOK),
        'current_action'=>current_action(),'current_filter'=>current_filter(),'doing_action'=>doing_action(),
        'stack'=>$GLOBALS['wp_current_filter'],'hook_state'=>rp_remove_all_state($hook),
        'registered_rows'=>array_map(static fn(array $row):array=>[
            'priority'=>$row['priority'],'uid'=>$row['uid'],'accepted_args'=>$row['accepted_args'],
            'function'=>is_string($row['function'])?$row['function']:null,
        ],rp_remove_all_rows($hook))];
    if($result!==null || $state['action_count']!==1
        || array_column($state['events'],'callback')!==['all','observer','mutator','cleared','peer']
        || $state['current_action']!==false || $state['current_filter']!==false
        || $state['doing_action']!==false || $state['stack']!==[] || $state['registered_rows']!==[]
        || $state['hook_state']!==['nesting_level'=>0,'iterations'=>[],'current_priority'=>[],'doing_action'=>false]) {
        throw new RuntimeException('action_remove_all_final_state');
    }
    $wire->send('action_remove_all_observed',['target'=>RP_REMOVE_ALL_TARGET,'observation'=>$state]);
    while(true) {
        $frame=$wire->receive();
        if($frame['kind']==='release_php') {
            expect_parent_effect($frame,null); expect_payload_keys($frame,['handle'],'action_remove_all_final_release');
            $registry->release($frame['payload']['handle']); $wire->send('release_ack',['handle'=>$frame['payload']['handle']]); continue;
        }
        if($frame['kind']==='request_complete') {
            expect_parent_effect($frame,null); expect_payload_keys($frame,['target','result'],'action_remove_all_request_complete');
            if($frame['payload']['target']!==RP_REMOVE_ALL_TARGET || $frame['payload']['result']!==null) {
                throw new RuntimeException('action_remove_all_request_outcome');
            }
            $live=$registry->liveCount(); $registry->close();
            if($live!==0 || $registry->liveCount()!==0) throw new RuntimeException('action_remove_all_live_handles');
            $wire->send('guest_done',['observation'=>$state,'live_handles'=>0,
                'live_handles_before_close'=>$live,'registry_closed'=>$registry->isClosed(),'frames'=>$wire->stats()]);
            break;
        }
        throw new RuntimeException('action_remove_all_guest_unexpected_frame');
    }
} catch(Throwable $error) { fail_bridge($error); }
