<?php
declare(strict_types=1);

function rp_bridge_check(bool $ok, string $code): void {
    if (!$ok) {
        throw new RuntimeException($code);
    }
}

final class RpCacheRelay {
    private const CAP = 65536;
    private $process = null;
    private $input = null;
    private $output = null;
    private int $cursor = 0;
    private int $deadline;
    private int $pid;
    private array $groups = ['rp-cache-multiple' => 0, 'rp-cache-integer' => 0];
    private int $flushes = 0;

    public function __construct(string $binary) {
        $this->deadline = hrtime(true) + 6_000_000_000;
        $system = getenv('SystemRoot');
        rp_bridge_check(is_string($system) && $system !== '', 'relay_environment');
        $this->process = proc_open([$binary], [
            0 => ['pipe', 'r'],
            1 => ['file', __DIR__ . '/rust.stdout', 'wb'],
            2 => ['file', __DIR__ . '/rust.stderr', 'wb'],
        ], $pipes, __DIR__, ['SystemRoot' => $system, 'WINDIR' => $system], ['bypass_shell' => true]);
        rp_bridge_check(is_resource($this->process), 'relay_launch');
        $this->input = $pipes[0];
        try {
            $this->pid = proc_get_status($this->process)['pid'];
            $this->output = fopen(__DIR__ . '/rust.stdout', 'rb');
            rp_bridge_check(is_resource($this->output), 'relay_output_open');
        } catch (Throwable $error) {
            $this->close();
            throw $error;
        }
    }

    private function sizes(): array {
        clearstatcache();
        $stdout = filesize(__DIR__ . '/rust.stdout');
        $stderr = filesize(__DIR__ . '/rust.stderr');
        rp_bridge_check(is_int($stdout) && $stdout <= self::CAP && $stderr === 0, 'relay_capture_limit');
        return [$stdout, $stderr];
    }

    private function line(): string {
        while (hrtime(true) < $this->deadline) {
            [$size] = $this->sizes();
            if ($size > $this->cursor) {
                rp_bridge_check(fseek($this->output, $this->cursor) === 0, 'relay_output_seek');
                $line = fgets($this->output, 1025);
                rp_bridge_check(is_string($line) && strlen($line) < 1024, 'relay_reply_limit');
                if (str_ends_with($line, "\n")) {
                    $this->cursor += strlen($line);
                    return $line;
                }
            }
            usleep(1000);
        }
        throw new RuntimeException('relay_reply_deadline');
    }

    private static function fields(array $reply, array $expected): bool {
        $keys = array_keys($reply);
        sort($keys, SORT_STRING);
        sort($expected, SORT_STRING);
        return $keys === $expected;
    }

    private function exchange(array $request, array $fields): array {
        rp_bridge_check(hrtime(true) < $this->deadline, 'relay_request_deadline');
        $wire = json_encode($request, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . "\n";
        // This finite PHP corpus sends only small frames and has one outstanding request.
        rp_bridge_check(strlen($wire) < 1024 && fwrite($this->input, $wire) === strlen($wire)
            && fflush($this->input), 'relay_request_write');
        if ($request['op'] === 'finish') {
            fclose($this->input);
            $this->input = null;
        }
        $reply = json_decode($this->line(), true, 8, JSON_THROW_ON_ERROR);
        rp_bridge_check(is_array($reply) && ($reply['ok'] ?? false) === true
            && self::fields($reply, ['ok', ...$fields]), 'relay_reply_shape');
        if (isset($request['group'])) {
            ++$this->groups[$request['group']];
        } elseif ($request['op'] === 'flush') {
            ++$this->flushes;
        }
        return $reply;
    }

    private static function encode_key(int|string $key): string {
        rp_bridge_check(is_int($key) || trim($key) !== '', 'relay_key_profile');
        // Match PHP array key coercion, including canonical numeric strings.
        $key = array_key_first([$key => true]);
        return is_int($key) ? 'i:' . $key : 's:' . bin2hex($key);
    }

    private static function encode_value(mixed $value): array {
        return match (gettype($value)) {
            'NULL' => ['kind' => 'null'],
            'boolean' => ['kind' => 'bool', 'value' => $value],
            'integer' => ['kind' => 'int', 'value' => $value],
            'string' => ['kind' => 'bytes', 'value' => bin2hex($value)],
            default => throw new RuntimeException('relay_value_profile'),
        };
    }

    private static function decode_value(mixed $value): mixed {
        rp_bridge_check(is_array($value) && is_string($value['kind'] ?? null), 'relay_value_shape');
        if ($value['kind'] === 'null') {
            rp_bridge_check(self::fields($value, ['kind']), 'relay_null_shape');
            return null;
        }
        rp_bridge_check(self::fields($value, ['kind', 'value']), 'relay_value_fields');
        $data = $value['value'];
        return match ($value['kind']) {
            'bool' => is_bool($data) ? $data : throw new RuntimeException('relay_bool_shape'),
            'int' => is_int($data) ? $data : throw new RuntimeException('relay_integer_shape'),
            'bytes' => is_string($data) && preg_match('/\A(?:[a-f0-9]{2})*\z/D', $data) === 1
                ? hex2bin($data) : throw new RuntimeException('relay_hex_shape'),
            default => throw new RuntimeException('relay_value_kind'),
        };
    }

    public function get(string $group, int|string $key, &$found): mixed {
        $reply = $this->exchange(['op' => 'get', 'group' => $group, 'key' => self::encode_key($key)], ['found', 'value']);
        $found = $reply['found'];
        rp_bridge_check(is_bool($found) && ($found || $reply['value'] === null), 'relay_found_shape');
        return $found ? self::decode_value($reply['value']) : false;
    }

    public function mutate(string $op, string $group, int|string $key, mixed $data = null): bool {
        $request = ['op' => $op, 'group' => $group, 'key' => self::encode_key($key)];
        if ($op !== 'delete') {
            $request['value'] = self::encode_value($data);
        }
        $reply = $this->exchange($request, ['changed']);
        rp_bridge_check(is_bool($reply['changed']), 'relay_mutation_shape');
        return $reply['changed'];
    }

    public function adjust(string $op, string $group, int|string $key, int $offset): int|false {
        $reply = $this->exchange(['op' => $op, 'group' => $group, 'key' => self::encode_key($key),
            'offset' => $offset], ['value']);
        rp_bridge_check($reply['value'] === null || is_int($reply['value']), 'relay_counter_shape');
        return $reply['value'] ?? false;
    }

    public function flush(?string $group = null): void {
        $request = $group === null ? ['op' => 'flush'] : ['op' => 'flush_group', 'group' => $group];
        $reply = $this->exchange($request, ['changed']);
        rp_bridge_check(is_bool($reply['changed']), 'relay_flush_shape');
    }

    public function finish(): array {
        $reply = $this->exchange(['op' => 'finish'], ['operations', 'entries', 'charged_bytes']);
        rp_bridge_check($reply['operations'] === array_sum($this->groups) + $this->flushes
            && $reply['entries'] === 0 && $reply['charged_bytes'] === 0, 'relay_finish_state');
        $status = $this->wait_exit($this->deadline);
        rp_bridge_check(!$status['running'] && $status['exitcode'] === 0, 'relay_child_exit');
        [$size] = $this->sizes();
        rp_bridge_check($size === $this->cursor, 'relay_unexpected_trailer');
        fclose($this->output);
        $this->output = null;
        $code = proc_close($this->process);
        $this->process = null;
        rp_bridge_check($code === 0 || $code === -1, 'relay_child_reap');
        return ['owned_pid' => $this->pid, 'exit_code' => $status['exitcode'], 'owned_reaped' => true,
            'stdout_bytes' => $size, 'stderr_bytes' => 0, 'groups' => $this->groups, 'flushes' => $this->flushes,
            'finish' => $reply];
    }

    private function wait_exit(int $deadline): array {
        do {
            $status = proc_get_status($this->process);
            if (!$status['running']) {
                break;
            }
            usleep(1000);
        } while (hrtime(true) < $deadline);
        return $status;
    }

    public function close(): void {
        if (is_resource($this->input)) {
            fclose($this->input);
        }
        if (is_resource($this->output)) {
            fclose($this->output);
        }
        if (is_resource($this->process)) {
            if (proc_get_status($this->process)['running']) {
                proc_terminate($this->process);
            }
            rp_bridge_check(!$this->wait_exit(hrtime(true) + 2_000_000_000)['running'], 'relay_owned_cleanup');
            proc_close($this->process);
            $this->process = null;
        }
    }
}

/** Inherit stock multiple-key APIs and dispatch their individual operations. */
final class RpRustBackedCache extends WP_Object_Cache {
    private int $blog = 1;

    public function __construct(private readonly RpCacheRelay $relay) {
        parent::__construct();
    }

    private static function selected($group): bool {
        return $group === 'rp-cache-multiple' || $group === 'rp-cache-integer';
    }

    private function delegated($group): bool {
        return $this->blog === 1 && self::selected($group);
    }

    public function get($key, $group = 'default', $force = false, &$found = null) {
        return $this->delegated($group) ? $this->relay->get($group, $key, $found) : parent::get($key, $group, $force, $found);
    }

    public function set($key, $data, $group = 'default', $expire = 0) {
        return $this->delegated($group) ? $this->relay->mutate('set', $group, $key, $data) : parent::set($key, $data, $group, $expire);
    }

    public function add($key, $data, $group = 'default', $expire = 0) {
        if ($this->delegated($group)) {
            return !wp_suspend_cache_addition() && $this->relay->mutate('add', $group, $key, $data);
        }
        return parent::add($key, $data, $group, $expire);
    }

    public function replace($key, $data, $group = 'default', $expire = 0) {
        return $this->delegated($group) ? $this->relay->mutate('replace', $group, $key, $data) : parent::replace($key, $data, $group, $expire);
    }

    public function delete($key, $group = 'default', $deprecated = false) {
        return $this->delegated($group) ? $this->relay->mutate('delete', $group, $key) : parent::delete($key, $group, $deprecated);
    }

    public function incr($key, $offset = 1, $group = 'default') {
        return $this->delegated($group) ? $this->relay->adjust('incr', $group, $key, (int) $offset) : parent::incr($key, $offset, $group);
    }

    public function decr($key, $offset = 1, $group = 'default') {
        return $this->delegated($group) ? $this->relay->adjust('decr', $group, $key, (int) $offset) : parent::decr($key, $offset, $group);
    }

    public function flush() {
        $this->relay->flush();
        return parent::flush();
    }

    public function flush_group($group) {
        if (self::selected($group)) {
            $this->relay->flush($group);
        }
        return parent::flush_group($group);
    }

    public function switch_to_blog($blog_id) {
        $this->blog = (int) $blog_id;
        parent::switch_to_blog($blog_id);
    }
}
