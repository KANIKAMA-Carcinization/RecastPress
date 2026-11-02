<?php
declare(strict_types=1);

/** Closed, bounded experimental channel. Stdout is never a dispatch channel. */
final class RpRpcClient {
    private mixed $stream = null;
    private int $seq = 0;
    private array $counts = ['hello' => 0, 'query' => 0, 'escape' => 0, 'close' => 0];
    private int $written = 0;
    private int $read = 0;
    private bool $closed = false;

    public function __construct(private readonly array $context) {}

    public function connect(): void {
        rp_check($this->stream === null && !$this->closed, 'rpc_connect_reuse_refused');
        $error_code = 0;
        $error_text = '';
        $this->stream = stream_socket_client('tcp://127.0.0.1:' . $this->context['port'],
            $error_code, $error_text, 5, STREAM_CLIENT_CONNECT);
        rp_check(is_resource($this->stream), 'rpc_connect_failed');
        // The host owns a 10-second DB operation deadline. Allow its bounded
        // response/error path to finish before this comparison-client timeout.
        rp_check(stream_set_timeout($this->stream, 15) && stream_set_blocking($this->stream, true),
            'rpc_stream_configuration_failed');
    }

    public function request(string $op, string $bytes = ''): array {
        rp_check(is_resource($this->stream) && !$this->closed && $this->seq < 64,
            'rpc_channel_closed_or_budget');
        rp_check(isset($this->counts[$op]) && strlen($bytes) <= 4096
            && (($op !== 'hello' && $op !== 'close') || $bytes === ''), 'rpc_request_shape');
        ++$this->seq;
        ++$this->counts[$op];
        $wire = json_encode(['schema' => 'recastpress.wpdb-rpc-request.v0.5',
            'nonce' => $this->context['nonce'], 'seq' => $this->seq, 'op' => $op,
            'data_hex' => bin2hex($bytes)], JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . "\n";
        rp_check(strlen($wire) <= 16384 && $this->written + strlen($wire) <= 131072,
            'rpc_request_frame_or_total_quota');
        $offset = 0;
        while ($offset < strlen($wire)) {
            $written = fwrite($this->stream, substr($wire, $offset));
            rp_check(is_int($written) && $written > 0, 'rpc_write_failed');
            $offset += $written;
            $this->written += $written;
        }
        rp_check(fflush($this->stream), 'rpc_flush_failed');
        $line = fgets($this->stream, 65538);
        rp_check(is_string($line) && strlen($line) <= 65536 && str_ends_with($line, "\n")
            && !str_contains(substr($line, 0, -1), "\n"), 'rpc_reply_incomplete_or_quota');
        rp_check($this->read + strlen($line) <= 524288, 'rpc_reply_total_quota');
        $this->read += strlen($line);
        $meta = stream_get_meta_data($this->stream);
        rp_check(!$meta['timed_out'], 'rpc_read_timeout');
        try {
            $reply = json_decode($line, true, 32, JSON_THROW_ON_ERROR);
        } catch (JsonException $error) {
            throw new RpNativeFailure('rpc_invalid_json');
        }
        rp_check(is_array($reply), 'rpc_reply_object_required');
        rp_exact_keys($reply, ['schema', 'seq', 'ok', 'data'], 'rpc_reply_exact_keys');
        rp_check($reply['schema'] === 'recastpress.wpdb-rpc-reply.v0.5'
            && $reply['seq'] === $this->seq && is_bool($reply['ok']) && is_array($reply['data']),
            'rpc_reply_identity');
        return $reply;
    }

    public function close(): bool {
        rp_check(is_resource($this->stream) && !$this->closed, 'rpc_close_without_channel');
        $reply = $this->request('close');
        rp_check($reply['ok'], 'rpc_host_close_failed');
        rp_exact_keys($reply['data'], ['closed'], 'rpc_close_reply_keys');
        rp_check($reply['data']['closed'] === true, 'rpc_host_close_not_completed');
        $extra = fread($this->stream, 1);
        $meta = stream_get_meta_data($this->stream);
        rp_check($extra === '' && !$meta['timed_out'] && feof($this->stream), 'rpc_close_reply_not_eof');
        $closed = fclose($this->stream);
        $this->stream = null;
        $this->closed = $closed;
        return $closed;
    }

    public function stats(): array {
        return ['requests' => $this->seq, 'counts' => $this->counts,
            'request_bytes' => $this->written, 'reply_bytes' => $this->read,
            'transport_closed' => $this->closed, 'max_requests' => 64];
    }

    public function __destruct() {
        // Local descriptor cleanup is not a successful host/session close.
        if (is_resource($this->stream)) {
            fclose($this->stream);
        }
    }
}

/** Minimal fixed-corpus drop-in: upstream prepare/get_* remain inherited. */
final class RpBridgeWpdb extends wpdb {
    private RpRpcClient $rpc;
    private string $serverVersion = '';
    private array $columns = [];
    private array $errorIdentity = ['code' => 0, 'state' => '00000'];
    private array $setup = [];
    private array $sessionSnapshot = [];
    private bool $connectAttempted = false;

    public function __construct(array $context) {
        $this->rpc = new RpRpcClient($context);
        parent::__construct('rp_wpdb_probe', '', 'rp_wpdb_probe', '127.0.0.1');
    }

    private function queryData(array $data): array {
        rp_exact_keys($data, ['columns', 'rows', 'affected_rows', 'insert_id', 'warnings', 'status'],
            'rpc_query_data_keys');
        rp_check(is_array($data['columns']) && array_is_list($data['columns']) && count($data['columns']) <= 16
            && is_array($data['rows']) && array_is_list($data['rows']) && count($data['rows']) <= 16,
            'rpc_query_collection_bounds');
        foreach ($data['columns'] as $column) {
            rp_check(is_array($column), 'rpc_column_object');
            rp_exact_keys($column, ['name_hex', 'type', 'flags', 'character_set', 'declared_length', 'decimals'],
                'rpc_column_keys');
            rp_unhex($column['name_hex'], 256);
            foreach (['type' => 255, 'flags' => 65535, 'character_set' => 65535,
                'declared_length' => 4294967295, 'decimals' => 255] as $field => $maximum) {
                rp_check(is_int($column[$field]) && $column[$field] >= 0 && $column[$field] <= $maximum,
                    'rpc_column_integer');
            }
        }
        foreach ($data['rows'] as $row) {
            rp_check(is_array($row) && array_is_list($row) && count($row) === count($data['columns']),
                'rpc_row_width');
            foreach ($row as $cell) {
                if ($cell !== null) {
                    rp_unhex($cell, 4096);
                }
            }
        }
        rp_decimal_int($data['affected_rows']);
        if ($data['insert_id'] !== null) {
            rp_decimal_int($data['insert_id']);
        }
        foreach (['warnings', 'status'] as $field) {
            rp_check(is_int($data[$field]) && $data[$field] >= 0 && $data[$field] <= 65535,
                'rpc_query_u16');
        }
        return $data;
    }

    private function rawQuery(string $sql): array {
        $reply = $this->rpc->request('query', $sql);
        rp_check($reply['ok'], 'rpc_internal_query_failed');
        $data = $this->queryData($reply['data']);
        $this->setup[] = ['sql_hex' => bin2hex($sql), 'data' => $data];
        return $data;
    }

    public function db_connect($allow_bail = true) {
        rp_check(!$this->connectAttempted && !$this->ready, 'rpc_reconnect_not_supported');
        $this->connectAttempted = true;
        $this->rpc->connect();
        $hello = $this->rpc->request('hello');
        rp_check($hello['ok'], 'rpc_hello_failed');
        $data = $hello['data'];
        rp_exact_keys($data, ['server_version_hex', 'connection_id', 'caps', 'charset', 'collation'],
            'rpc_hello_keys');
        $this->serverVersion = rp_unhex($data['server_version_hex'], 256);
        rp_check($this->serverVersion !== '' && is_int($data['connection_id']) && $data['connection_id'] > 0
            && $data['connection_id'] <= 4294967295 && is_int($data['caps']) && $data['caps'] >= 0
            && $data['caps'] <= 4294967295 && ($data['caps'] & (2 | 128 | 65536)) === 0
            && $data['charset'] === 'utf8mb4' && $data['collation'] === 'utf8mb4_unicode_520_ci',
            'rpc_hello_contract');
        // Opaque non-mysqli handle; parent prepare/get_* never see a fake mysqli.
        $this->dbh = new stdClass();
        $this->is_mysql = true;
        $this->charset = 'utf8mb4';
        $this->collate = 'utf8mb4_unicode_520_ci';
        // Native wpdb sends these through mysqli directly, outside num_queries.
        $this->rawQuery("SET NAMES 'utf8mb4' COLLATE 'utf8mb4_unicode_520_ci'");
        $this->set_sql_mode();
        $snapshot = $this->rawQuery('SELECT @@character_set_connection,@@collation_connection,@@SESSION.sql_mode');
        rp_check(count($snapshot['rows']) === 1 && count($snapshot['columns']) === 3
            && count($snapshot['rows'][0]) === 3 && !in_array(null, $snapshot['rows'][0], true),
            'rpc_session_snapshot_shape');
        $values = array_map('rp_unhex', $snapshot['rows'][0]);
        rp_check($values[0] === $this->charset && $values[1] === $this->collate,
            'rpc_actual_session_charset_mismatch');
        $this->sessionSnapshot = ['character_set_connection_hex' => bin2hex($values[0]),
            'collation_connection_hex' => bin2hex($values[1]), 'sql_mode_hex' => bin2hex($values[2])];
        $this->ready = true;
        return true;
    }

    public function set_sql_mode($modes = []) {
        if (empty($modes)) {
            $result = $this->rawQuery('SELECT @@SESSION.sql_mode');
            rp_check(count($result['rows']) === 1 && count($result['columns']) === 1
                && $result['rows'][0][0] !== null, 'rpc_sql_mode_shape');
            $raw = rp_unhex($result['rows'][0][0], 4096);
            if ($raw === '') {
                return;
            }
            $modes = explode(',', $raw);
        }
        $modes = array_change_key_case($modes, CASE_UPPER);
        $incompatible = (array) apply_filters('incompatible_sql_modes', $this->incompatible_modes);
        foreach ($modes as $index => $mode) {
            if (in_array($mode, $incompatible, true)) {
                unset($modes[$index]);
            }
        }
        $raw = implode(',', $modes);
        rp_check(preg_match('/\A[A-Z0-9_,]*\z/D', $raw) === 1, 'rpc_sql_mode_out_of_fixed_scope');
        $this->rawQuery("SET SESSION sql_mode='$raw'");
    }

    public function db_server_info() {
        rp_check($this->serverVersion !== '', 'rpc_server_info_before_connect');
        return $this->serverVersion;
    }

    public function query($query) {
        if (!$this->ready) {
            $this->check_current_query = true;
            return false;
        }
        $query = apply_filters('query', $query);
        if (!$query) {
            $this->insert_id = 0;
            return false;
        }
        rp_check(is_string($query) && preg_match('/\A[\x00-\x7f]*\z/D', $query) === 1,
            'rpc_non_ascii_query_out_of_fixed_scope');
        $this->flush();
        $this->func_call = "\$db->query(\"$query\")";
        $this->check_current_query = true;
        $this->last_query = $query;
        $reply = $this->rpc->request('query', $query);
        ++$this->num_queries;
        if (!$reply['ok']) {
            $error = $reply['data'];
            rp_exact_keys($error, ['kind', 'code', 'state_hex', 'message_hex', 'raw_err_preserved'],
                'rpc_server_error_keys');
            $state = rp_unhex($error['state_hex'], 5);
            $message = rp_unhex($error['message_hex'], 4096);
            rp_check($error['kind'] === 'server' && $error['code'] === 1064 && $state === '42000'
                && $error['raw_err_preserved'] === false && $message !== ''
                && preg_match('/\A[\x00-\x7f]*\z/D', $message) === 1, 'rpc_server_error_out_of_fixed_scope');
            $this->errorIdentity = ['code' => $error['code'], 'state' => $state];
            $this->last_error = $message;
            if ($this->insert_id && preg_match('/^\s*(insert|replace)\s/i', $query)) {
                $this->insert_id = 0;
            }
            $this->print_error($this->last_error);
            return false;
        }
        $data = $this->queryData($reply['data']);
        $this->errorIdentity = ['code' => 0, 'state' => '00000'];
        $this->columns = $data['columns'];
        foreach ($data['rows'] as $row) {
            $object = new stdClass();
            foreach ($this->columns as $index => $column) {
                $name = rp_unhex($column['name_hex'], 256);
                $object->{$name} = $row[$index] === null ? null : rp_unhex($row[$index], 4096);
            }
            $this->last_result[] = $object;
        }
        // These are the upstream return branches, not SQL rejection or result
        // correction. The server alone determines errors and affected counts.
        if (preg_match('/^\s*(create|alter|truncate|drop)\s/i', $query)) {
            $this->result = true;
            return $this->result;
        } elseif (preg_match('/^\s*(insert|delete|update|replace)\s/i', $query)) {
            $this->rows_affected = rp_decimal_int($data['affected_rows']);
            if (preg_match('/^\s*(insert|replace)\s/i', $query)) {
                $this->insert_id = $data['insert_id'] === null ? 0 : rp_decimal_int($data['insert_id']);
            }
            return $this->rows_affected;
        }
        $this->num_rows = count($this->last_result);
        return $this->num_rows;
    }

    public function flush() {
        parent::flush();
        $this->columns = [];
    }

    public function get_col_info($info_type = 'name', $col_offset = -1) {
        rp_check($info_type === 'name', 'rpc_mysqli_column_metadata_out_of_fixed_scope');
        if (!$this->columns) {
            return null;
        }
        $names = array_map(static fn(array $column): string => rp_unhex($column['name_hex'], 256), $this->columns);
        if ($col_offset === -1) {
            return $names;
        }
        rp_check(is_int($col_offset) && array_key_exists($col_offset, $names), 'rpc_column_offset_out_of_range');
        return $names[$col_offset];
    }

    protected function load_col_info() {
        throw new RpNativeFailure('rpc_mysqli_column_metadata_out_of_fixed_scope');
    }

    public function _real_escape($data) {
        if (!is_scalar($data)) {
            return '';
        }
        $bytes = (string) $data;
        rp_check($this->ready && preg_match('/\A[\x00-\x7f]*\z/D', $bytes) === 1,
            'rpc_escape_out_of_fixed_scope');
        $reply = $this->rpc->request('escape', $bytes);
        rp_check($reply['ok'], 'rpc_escape_failed');
        rp_exact_keys($reply['data'], ['escaped_hex'], 'rpc_escape_reply_keys');
        return $this->add_placeholder_escape(rp_unhex($reply['data']['escaped_hex'], 8192));
    }

    public function check_connection($allow_bail = true) {
        throw new RpNativeFailure('rpc_reconnect_not_supported');
    }

    public function close() {
        if ($this->dbh === null) {
            return false;
        }
        $closed = $this->rpc->close();
        if ($closed) {
            $this->dbh = null;
            $this->ready = false;
        }
        return $closed;
    }

    public function rpc_error_identity(): array {
        return $this->errorIdentity;
    }

    public function rpc_session_snapshot(): array {
        return $this->sessionSnapshot;
    }

    public function rpc_stats(): array {
        return ['transport' => $this->rpc->stats(), 'internal_session_setup' => $this->setup,
            'escape_scope' => 'ASCII-only-fixed-utf8mb4-and-cached-last-OK-NO_BACKSLASH_ESCAPES',
            'mysqli_column_metadata_scope' => 'get_col_info-name-only'];
    }
}
