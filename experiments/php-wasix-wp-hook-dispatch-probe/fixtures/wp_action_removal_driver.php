<?php
declare(strict_types=1);

require_once __DIR__ . '/bridge_driver.php';
require_once __DIR__ . '/plugin.php';

use RecastPress\V06\Handles\HandleRegistry;

const RP_REMOVAL_REQUEST = '81';
const RP_REMOVAL_TARGET = 'wp-action-removal-1';
const RP_REMOVAL_HOOK = 'rp-probe-action-removal';

function recastpress_wp_should_dispatch_selected_action(string $hookName, object $hook): bool
{
    return $hookName === RP_REMOVAL_HOOK && $hook === ($GLOBALS['rp_removal_hook'] ?? null);
}
function recastpress_wp_should_dispatch_selected_hook(string $hookName, object $hook): bool
{
    return recastpress_wp_should_dispatch_selected_action($hookName, $hook);
}
function recastpress_wp_dispatch_selected_hook(...$args): never
{
    throw new RuntimeException('action_removal_entered_filter_handoff');
}
function rp_removal_rows(WP_Hook $hook): array
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
function rp_removal_same_handle(array $a, array $b): bool
{
    ksort($a); ksort($b); return $a === $b;
}
function rp_removal_fingerprint(array $rows): string
{
    usort($rows, static fn(array $a,array $b):int=>$a['priority']<=>$b['priority']);
    $projection=array_map(static fn(array $row):array=>[
        $row['priority'],$row['uid'],$row['accepted_args'],$row['function'],
        $row['handle']['request'],$row['handle']['kind'],$row['handle']['slot'],
        $row['handle']['generation'],$row['handle']['token'],
    ],$rows);
    return hash('sha256',json_encode($projection,JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR));
}
function rp_removal_state(WP_Hook $hook): array
{
    $read=static fn(string $key):mixed=>(new ReflectionProperty(WP_Hook::class,$key))->getValue($hook);
    return ['nesting_level'=>$read('nesting_level'),'iterations'=>$read('iterations'),
        'current_priority'=>$read('current_priority'),'doing_action'=>$read('doing_action')];
}
function recastpress_wp_dispatch_selected_action(
    string $hookName, WP_Hook $hook, array $args, int $nestingLevel, Closure $sync,
): null {
    $wire=$GLOBALS['rp_removal_wire'];
    $registry=$GLOBALS['rp_removal_registry'];
    $rows=rp_removal_rows($hook);
    if(count($rows)!==3) throw new RuntimeException('action_removal_initial_rows');
    $handles=[]; $exported=[];
    foreach($rows as $row) {
        $handle=$registry->retainCallable($row['function']);
        $handles[$row['uid']]=$handle;
        $exported[]=['priority'=>$row['priority'],'uid'=>$row['uid'],
            'accepted_args'=>$row['accepted_args'],'function'=>$row['function'],'handle'=>$handle];
    }
    $GLOBALS['rp_removal_handles']=$handles;
    $GLOBALS['rp_removal_rows']=$exported;
    $GLOBALS['rp_removal_released']=[];
    $wire->send('action_dispatch_start',['generation'=>RP_REMOVAL_REQUEST,'target'=>RP_REMOVAL_TARGET,
        'hook'=>$hookName,'args'=>$args,'rows'=>$exported,'table_fingerprint'=>rp_removal_fingerprint($exported)]);
    while(true) {
        $frame=$wire->receive();
        if($frame['kind']==='invoke_php') {
            expect_parent_effect($frame,null);
            expect_payload_keys($frame,['target','uid','handle','priority','call_mode','args'],'action_removal_invoke');
            $call=$frame['payload']; $known=$handles[$call['uid']]??null;
            if($call['target']!==RP_REMOVAL_TARGET || !is_array($known)
                || !rp_removal_same_handle($known,$call['handle'])
                || !is_int($call['priority']) || $call['call_mode']!=='zero' || $call['args']!==[]) {
                throw new RuntimeException('action_removal_invoke_identity');
            }
            if(!$sync('set_priority',$call['priority'])) throw new RuntimeException('action_removal_priority_sync');
            call_user_func_array($registry->resolve($call['handle'],'callable'),$call['args']);
            if($call['uid']==='rp_action_removal_mutator') {
                $before=$handles['rp_action_removal_pending'];
                if(!remove_action(RP_REMOVAL_HOOK,'rp_action_removal_pending',15)) {
                    throw new RuntimeException('action_removal_remove_return');
                }
                $removedRow=null;
                foreach($exported as $row) if($row['uid']==='rp_action_removal_pending') $removedRow=$row;
                $remaining=array_values(array_filter($exported,static fn(array $row):bool=>$row['uid']!=='rp_action_removal_pending'));
                $wire->send('action_callback_removed',['target'=>RP_REMOVAL_TARGET,'parent_uid'=>$call['uid'],
                    'row'=>$removedRow,'removed_handle'=>$before,
                    'table_fingerprint'=>rp_removal_fingerprint($remaining)]);
                $ack=$wire->receive();
                expect_parent_effect($ack,null);
                expect_kind($ack,'action_removal_ack');
                expect_payload_keys($ack,['target','uid','priority'],'action_removal_ack');
                if($ack['payload']['target']!==RP_REMOVAL_TARGET
                    || $ack['payload']['uid']!=='rp_action_removal_pending'
                    || $ack['payload']['priority']!==15) {
                    throw new RuntimeException('action_removal_ack_identity');
                }
            }
            $wire->send('resume_value',['target'=>RP_REMOVAL_TARGET,'uid'=>$call['uid'],'value'=>null]);
            continue;
        }
        if($frame['kind']==='release_php') {
            expect_parent_effect($frame,null); expect_payload_keys($frame,['handle'],'action_removal_release');
            $handle=$frame['payload']['handle']; $found=false;
            foreach($handles as $uid=>$known) if(rp_removal_same_handle($known,$handle) && !isset($GLOBALS['rp_removal_released'][$uid])) {
                $GLOBALS['rp_removal_released'][$uid]=true; $found=true; break;
            }
            if(!$found) throw new RuntimeException('action_removal_release_identity');
            $registry->release($handle); $wire->send('release_ack',['handle'=>$handle]); continue;
        }
        if($frame['kind']==='action_complete') {
            expect_parent_effect($frame,null); expect_payload_keys($frame,['target','invocations'],'action_removal_complete');
            $actual=rp_removal_rows($hook);
            if($frame['payload']['target']!==RP_REMOVAL_TARGET || $frame['payload']['invocations']!==2
                || $actual!==[
                    ['priority'=>5,'uid'=>'rp_action_removal_mutator','accepted_args'=>0,'function'=>'rp_action_removal_mutator'],
                    ['priority'=>20,'uid'=>'rp_action_removal_tail','accepted_args'=>0,'function'=>'rp_action_removal_tail'],
                ] || !$sync('finish')) throw new RuntimeException('action_removal_completion_state');
            return null;
        }
        throw new RuntimeException('action_removal_unexpected_frame');
    }
}
function rp_action_removal_all(string $hookName,mixed ...$args):void
{
    $GLOBALS['rp_removal_events'][]=['callback'=>'all','hook'=>$hookName,'args'=>func_get_args(),
        'current_action'=>current_action(),'current_filter'=>current_filter(),'doing_action'=>doing_action(),
        'did_action'=>did_action(RP_REMOVAL_HOOK),'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_action_removal_mutator():void
{
    $GLOBALS['rp_removal_events'][]=['callback'=>'mutator','current_action'=>current_action(),
        'current_filter'=>current_filter(),'doing_action'=>doing_action(),'did_action'=>did_action(RP_REMOVAL_HOOK),
        'current_priority'=>$GLOBALS['rp_removal_hook']->current_priority(),'stack'=>$GLOBALS['wp_current_filter']];
}
function rp_action_removal_pending():void { throw new RuntimeException('removed_callback_was_invoked'); }
function rp_action_removal_tail():void
{
    $GLOBALS['rp_removal_events'][]=['callback'=>'tail','current_action'=>current_action(),
        'current_filter'=>current_filter(),'doing_action'=>doing_action(),'did_action'=>did_action(RP_REMOVAL_HOOK),
        'current_priority'=>$GLOBALS['rp_removal_hook']->current_priority(),'stack'=>$GLOBALS['wp_current_filter']];
}

try {
    $wire=new BridgeProtocol(); $registry=new HandleRegistry(RP_REMOVAL_REQUEST,64,64);
    $GLOBALS['rp_removal_wire']=$wire; $GLOBALS['rp_removal_registry']=$registry;
    $GLOBALS['rp_removal_events']=[]; $GLOBALS['wp_filter']=[]; $GLOBALS['wp_actions']=[];
    $GLOBALS['wp_filters']=[]; $GLOBALS['wp_current_filter']=[];
    add_action('all','rp_action_removal_all',10,10);
    add_action(RP_REMOVAL_HOOK,'rp_action_removal_mutator',5,0);
    add_action(RP_REMOVAL_HOOK,'rp_action_removal_pending',15,0);
    add_action(RP_REMOVAL_HOOK,'rp_action_removal_tail',20,0);
    $hook=$GLOBALS['wp_filter'][RP_REMOVAL_HOOK]; $GLOBALS['rp_removal_hook']=$hook;
    $result=do_action(RP_REMOVAL_HOOK);
    $state=['events'=>$GLOBALS['rp_removal_events'],'action_count'=>did_action(RP_REMOVAL_HOOK),
        'current_action'=>current_action(),'current_filter'=>current_filter(),'doing_action'=>doing_action(),
        'stack'=>$GLOBALS['wp_current_filter'],'hook_state'=>rp_removal_state($hook),
        'registered_rows'=>array_map(static fn(array $row):array=>[
            'priority'=>$row['priority'],'uid'=>$row['uid'],'accepted_args'=>$row['accepted_args'],
            'function'=>is_string($row['function'])?$row['function']:null,
        ],rp_removal_rows($hook))];
    if($result!==null || $state['action_count']!==1
        || array_column($state['events'],'callback')!==['all','mutator','tail']
        || $state['current_action']!==false || $state['current_filter']!==false
        || $state['doing_action']!==false || $state['stack']!==[]
        || $state['hook_state']!==['nesting_level'=>0,'iterations'=>[],'current_priority'=>[],'doing_action'=>false]) {
        throw new RuntimeException('action_removal_final_state');
    }
    $wire->send('action_removal_observed',['target'=>RP_REMOVAL_TARGET,'observation'=>$state]);
    while(true) {
        $frame=$wire->receive();
        if($frame['kind']==='release_php') {
            expect_parent_effect($frame,null); expect_payload_keys($frame,['handle'],'action_removal_final_release');
            $registry->release($frame['payload']['handle']); $wire->send('release_ack',['handle'=>$frame['payload']['handle']]); continue;
        }
        if($frame['kind']==='request_complete') {
            expect_parent_effect($frame,null); expect_payload_keys($frame,['target','result'],'action_removal_request_complete');
            if($frame['payload']['target']!==RP_REMOVAL_TARGET || $frame['payload']['result']!==null) {
                throw new RuntimeException('action_removal_request_outcome');
            }
            $live=$registry->liveCount(); $registry->close();
            if($live!==0 || $registry->liveCount()!==0) throw new RuntimeException('action_removal_live_handles');
            $wire->send('guest_done',['observation'=>$state,'live_handles'=>0,
                'live_handles_before_close'=>$live,'registry_closed'=>$registry->isClosed(),'frames'=>$wire->stats()]);
            break;
        }
        throw new RuntimeException('action_removal_guest_unexpected_frame');
    }
} catch(Throwable $error) { fail_bridge($error); }
