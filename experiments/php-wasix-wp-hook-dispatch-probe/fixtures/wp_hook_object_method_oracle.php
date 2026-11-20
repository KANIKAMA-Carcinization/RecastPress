<?php
declare(strict_types=1);
$root=$argv[1]??throw new RuntimeException('missing pinned WordPress root');
$pinned=[
    $root.'/wp-includes/plugin.php'=>'d70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d',
    $root.'/wp-includes/class-wp-hook.php'=>'b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e',
];
foreach($pinned as $path=>$hash) if(!is_file($path)||hash_file('sha256',$path)!==$hash) throw new RuntimeException('pinned WordPress hook source mismatch');
$GLOBALS['wp_filter']=[];$GLOBALS['wp_actions']=[];$GLOBALS['wp_filters']=[];$GLOBALS['wp_current_filter']=[];
require $root.'/wp-includes/plugin.php';$GLOBALS['events']=[];
final class RpHookObjectMethodOracle {
    public function __construct(private string $label) {}
    public function apply(string $value,string $left,string $right):string {
        $GLOBALS['events'][]=['callback'=>$this->label,'args'=>[$value,$left,$right],
            'current_filter'=>current_filter(),'doing_filter'=>doing_filter('rp-probe-filter'),
            'did_filter'=>did_filter('rp-probe-filter'),
            'current_priority'=>$GLOBALS['wp_filter']['rp-probe-filter']->current_priority()];
        return $value.'|'.$this->label;
    }
}
function rp_hook_object_all(string $hookName,mixed ...$args):void {
    $GLOBALS['events'][]=['callback'=>'all','hook'=>$hookName,'args'=>$args,
        'current_filter'=>current_filter(),'doing_filter'=>doing_filter($hookName),'did_filter'=>did_filter($hookName)];
}
add_filter('all','rp_hook_object_all',10,10);
$first=new RpHookObjectMethodOracle('first');$second=new RpHookObjectMethodOracle('second');$wrong=new RpHookObjectMethodOracle('wrong');
add_filter('rp-probe-filter',[$first,'apply'],5,3);add_filter('rp-probe-filter',[$second,'apply'],10,3);
$presence=[has_filter('rp-probe-filter',[$first,'apply'])];
$outcomes=[remove_filter('rp-probe-filter',[$wrong,'apply'],5)];
$presence[]=has_filter('rp-probe-filter',[$first,'apply']);
$outcomes[]=remove_filter('rp-probe-filter',[$first,'apply'],5);
$outcomes[]=add_filter('rp-probe-filter',[$first,'apply'],5,3);
$result=apply_filters('rp-probe-filter','seed','left','right');$hook=$GLOBALS['wp_filter']['rp-probe-filter'];
$rows=[];foreach($hook->callbacks as $priority=>$bucket)foreach($bucket as $uid=>$row) $rows[]=['priority'=>(int)$priority,'accepted_args'=>(int)$row['accepted_args'],'descriptor'=>get_class($row['function'][0]).'::'.$row['function'][1]];
$selected=array_values(array_filter($GLOBALS['events'],static fn(array $event):bool=>($event['callback']??null)!=='all'));
$observation=['events'=>$GLOBALS['events'],'result'=>$result,'outcomes'=>$outcomes,'presence'=>$presence,'rows'=>$rows,
    'filter_count'=>did_filter('rp-probe-filter'),'current_filter'=>current_filter(),'doing_filter'=>doing_filter()];
if($outcomes!==[false,true,true]||$presence!==[5,5]||$result!=='seed|first|second'||count($rows)!==2||array_column($selected,'callback')!==['first','second']) throw new RuntimeException('object method oracle observation mismatch');
echo json_encode(['status'=>'passed','scenario'=>'wp_hook_object_method_native_oracle','observation'=>$observation],JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);
