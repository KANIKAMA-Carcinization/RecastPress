<?php
declare(strict_types=1);

require_once __DIR__ . '/strict_json.php';
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

/** Request-local registry; object and PHP reference-cell entries are distinct kinds. */
final class RequestRegistry {
    private string $requestId;
    private int $generation = 7;
    private int $next = 1;
    /** @var array<int,array{kind:string,generation:int,value:mixed}> */
    private array $slots = [];

    public function __construct() { $this->requestId = bin2hex(random_bytes(16)); }

    public function object(object $value): string {
        foreach ($this->slots as $slot => $entry) {
            if ($entry['kind'] === 'object' && $entry['value'] === $value) return $this->token($slot, 'object');
        }
        $slot = $this->next++;
        $this->slots[$slot] = ['kind' => 'object', 'generation' => $this->generation, 'value' => $value];
        return $this->token($slot, 'object');
    }

    public function reference(mixed &$value): string {
        $slot = $this->next++;
        $this->slots[$slot] = ['kind' => 'reference', 'generation' => $this->generation, 'value' => &$value];
        return $this->token($slot, 'reference');
    }

    public function read(string $token, string $kind): mixed {
        [$slot, $actualKind] = $this->parse($token);
        if ($actualKind !== $kind || !isset($this->slots[$slot]) || $this->slots[$slot]['generation'] !== $this->generation) {
            throw new LogicException('invalid_or_expired_handle');
        }
        return $this->slots[$slot]['value'];
    }

    public function endRequest(): void { $this->slots = []; $this->generation++; }

    private function token(int $slot, string $kind): string {
        return 'rp1.' . $this->requestId . '.' . $this->generation . '.' . $slot . '.' . $kind . '.' . hash('sha256', "{$this->requestId}|{$this->generation}|{$slot}|{$kind}");
    }

    private function parse(string $token): array {
        if (!preg_match('/^rp1\.([a-f0-9]{32})\.(\d+)\.(\d+)\.(object|reference)\.([a-f0-9]{64})$/D', $token, $m)) throw new LogicException('malformed_handle');
        $slot = (int)$m[3]; $kind = $m[4];
        if (!hash_equals($this->requestId, $m[1]) || (int)$m[2] !== $this->generation || !hash_equals($this->token($slot, $kind), $token)) throw new LogicException('forged_or_expired_handle');
        return [$slot, $kind];
    }
}

function rp_surface(): array {
    $state = new PublicState();
    $alias = $state->item;
    $GLOBALS['rp_public'] = $state->item;
    $initial = $state->item;
    $refCell = ['value' => 'old'];
    $registry = new RequestRegistry();
    $objectHandle = $registry->object($state->item);
    $aliasHandle = $registry->object($alias);
    $refHandle = $registry->reference($refCell['value']);

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
    $sameHandle = hash_equals($objectHandle, $aliasHandle);
    $oldAliasName = $alias->name;
    $globalReplaced = $GLOBALS['rp_public'] !== $initial && $GLOBALS['rp_public']->name === 'replacement';
    $fieldObservedAfterChild = $state->item->name;
    $arrayReferenceObserved = $registry->read($refHandle, 'reference');
    $objectTypeCheck = false;
    try { $registry->read($objectHandle, 'reference'); } catch (LogicException) { $objectTypeCheck = true; }
    $bytes = $state->settings['bytes'];
    $parentOverride = (new ChildProbe())->inherited();

    $registry->endRequest();
    $expiredHandleRejected = false;
    try { $registry->read($objectHandle, 'object'); } catch (LogicException) { $expiredHandleRejected = true; }

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
