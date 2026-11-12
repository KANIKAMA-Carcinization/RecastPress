<?php
declare(strict_types=1);

require_once __DIR__ . '/strict_json.php';
require_once __DIR__ . '/handle_registry.php';
use RecastPress\V06\Handles\HandleRegistry;
use RecastPress\V06\Handles\RegistryError;
use function RecastPress\V06\StrictJson\rp_decode_unique_json;

/**
 * Small PHP-runtime fixture for the v0.6 compatibility boundary.
 * This is deliberately not a WordPress bootstrap or an implementation of WP logic.
 */

const RP_MAX_FRAME = 1_048_576;
const RP_MAX_TOTAL = 33_554_432;

function rp_fail(string $message): never {
    fwrite(STDERR, "fixture error: {$message}\n");
    exit(2);
}

function rp_emit(array $value): never {
    $json = json_encode($value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
    if (strlen($json) > RP_MAX_FRAME) rp_fail('response_frame_limit');
    fwrite(STDOUT, $json . "\n");
    exit(0);
}

final class PublicState {
    public object $item;
    /** @var array<string,mixed> */
    public array $settings;

    public function __construct() {
        $this->item = (object)['name' => 'before', 'count' => 1];
        $this->settings = ['flag' => false, 'bytes' => "A\0\xFFZ"];
    }
}

class ParentProbe {
    public function label(): string { return 'parent'; }
    public function dispatch(): string { return 'parent:' . $this->label(); }
}

final class ChildProbe extends ParentProbe {
    public function label(): string { return 'child'; }
    public function inherited(): string { return parent::dispatch(); }
}

function rp_surface(): array {
    $state = new PublicState();
    $alias = $state->item;
    $GLOBALS['rp_public'] = $state->item;
    $initial = $state->item;
    $refCell = ['value' => 'old'];
    $registry = new HandleRegistry('1');
    $objectHandle = $registry->retainObject($state->item);
    $aliasHandle = $registry->retainObject($alias);
    $refHandle = $registry->retainReference($refCell['value']);

    // The callback changes PHP public state and synchronously calls a child core API.
    $trace = ['root:before'];
    $childValue = null;
    $callback = static function (PublicState $public, callable $callCore) use (&$refCell, &$trace, &$childValue): void {
        $public->item->name = 'changed-in-callback';
        $public->settings['flag'] = true;
        $refCell['value'] = 'changed-through-reference';
        $GLOBALS['rp_public'] = (object)['name' => 'replacement'];
        $trace[] = 'callback:before-child';
        $childValue = $callCore('child_get_option');
        $trace[] = 'callback:after-child';
    };

    $callback($state, static function (string $operation) use (&$trace): string {
        if ($operation !== 'child_get_option') throw new LogicException('unknown_child_operation');
        $trace[] = 'child:complete';
        return 'option-from-child';
    });
    $trace[] = 'root:after-callback';

    $sameObject = $alias === $initial && $state->item === $initial;
    $sameHandle = $objectHandle === $aliasHandle;
    $oldAliasName = $alias->name;
    $globalReplaced = $GLOBALS['rp_public'] !== $initial && $GLOBALS['rp_public']->name === 'replacement';
    $fieldObservedAfterChild = $state->item->name;
    $arrayReferenceObserved = $registry->resolve($refHandle, 'reference_cell');
    $objectTypeCheck = false;
    try { $registry->resolve($objectHandle, 'reference_cell'); } catch (RegistryError) { $objectTypeCheck = true; }
    $bytes = $state->settings['bytes'];
    $parentOverride = (new ChildProbe())->inherited();

    $registry->close();
    $expiredHandleRejected = false;
    try { $registry->resolve($objectHandle, 'object'); } catch (RegistryError) { $expiredHandleRejected = true; }

    // User-level Throwable is catchable; it is a result, not a process crash.
    $throwableCaught = false;
    try { throw new RuntimeException('fixture throwable'); }
    catch (Throwable $error) { $throwableCaught = $error->getMessage() === 'fixture throwable'; }

    return [
        'same_object_alias' => $sameObject,
        'same_object_handle' => $sameHandle,
        'alias_public_property' => $oldAliasName,
        'public_property_after_callback' => $fieldObservedAfterChild,
        'global_replaced_without_changing_alias' => $globalReplaced && $alias === $initial,
        'array_reference_cell_after_callback' => $arrayReferenceObserved,
        'type_mismatch_rejected' => $objectTypeCheck,
        'expired_handle_rejected' => $expiredHandleRejected,
        'child_api_result' => $childValue,
        'callback_observed_child_completion_before_return' => $trace === ['root:before', 'callback:before-child', 'child:complete', 'callback:after-child', 'root:after-callback'],
        'callback_trace' => $trace,
        'nested_callback' => (static function () use (&$trace): bool {
            $outer = static function (callable $inner) use (&$trace): void { $trace[] = 'outer:enter'; $inner(); $trace[] = 'outer:exit'; };
            $outer(static function () use (&$trace): void { $trace[] = 'inner:enter'; $trace[] = 'inner:exit'; });
            return array_slice($trace, -4) === ['outer:enter', 'inner:enter', 'inner:exit', 'outer:exit'];
        })(),
        'catchable_throwable' => $throwableCaught,
        'parent_method_override' => $parentOverride,
        'byte_value_hex' => bin2hex($bytes),
        'byte_value_length' => strlen($bytes),
    ];
}

if (realpath($_SERVER['SCRIPT_FILENAME'] ?? '') === __FILE__) {
    $input = fgets(STDIN, RP_MAX_FRAME + 2);
    if ($input === false || !str_ends_with($input, "\n") || strlen($input) > RP_MAX_FRAME) rp_fail('invalid_or_oversize_input_frame');
    if (fgets(STDIN, 2) !== false) rp_fail('more_than_one_input_frame');
    $request = rp_decode_unique_json($input);
    if (!is_array($request) || array_is_list($request)) rp_fail('frame_must_be_object');
    $allowed = ['v', 'request', 'seq', 'kind', 'case'];
    foreach (array_keys($request) as $key) if (!in_array($key, $allowed, true)) rp_fail('unknown_frame_field');
    if (($request['v'] ?? null) !== 1 || !is_string($request['request'] ?? null) || !is_int($request['seq'] ?? null) || $request['seq'] !== 0 || ($request['kind'] ?? null) !== 'probe' || ($request['case'] ?? null) !== 'surface') rp_fail('invalid_probe_request');
    rp_emit(['v' => 1, 'request' => $request['request'], 'seq' => 0, 'kind' => 'probe_result', 'ok' => true, 'result' => rp_surface()]);
}
