<?php
/** Fixed upstream API corpus; only the two driver-error accessors are injected. */
function rp_corpus(wpdb $db, array &$report): void {
    $old_prefix = $db->set_prefix('rp_');
    $report['prefix'] = ['previous' => rp_value($old_prefix), 'prefix_hex' => bin2hex($db->prefix),
        'base_prefix_hex' => bin2hex($db->base_prefix), 'posts_hex' => bin2hex($db->posts), 'users_hex' => bin2hex($db->users)];
    rp_check($db->prefix === 'rp_' && $db->base_prefix === 'rp_' && $db->posts === 'rp_posts' && $db->users === 'rp_users', 'prefix_contract');

    $sql = "SHOW SESSION STATUS LIKE 'Ssl_cipher'";
    $tls = rp_observe($db, $report, 'native_tls', 'get_row/ARRAY_N', $sql, fn() => $db->get_row($sql, ARRAY_N));
    rp_check($tls === ['Ssl_cipher', ''], 'native_expected_non_tls');
    $sql = 'SELECT COUNT(*) FROM rp_wire_fixture';
    $empty = rp_observe($db, $report, 'fixture_empty', 'get_var', $sql, fn() => $db->get_var($sql));
    rp_check($empty === '0', 'fixture_not_empty');
    $sql = "INSERT INTO rp_wire_fixture(marker,payload,dec_value,big_value) VALUES('rp-wire',X'00ff41',12345678901234567890.1234567890,18446744073709551615)";
    rp_check(rp_query($db, $report, 'insert', $sql) === 1 && is_int($db->insert_id) && $db->insert_id > 0, 'insert_contract');

    $sql = "SELECT payload,NULL AS null_value,X'' AS empty_value,dec_value,big_value FROM rp_wire_fixture WHERE marker='rp-wire'";
    $expected = ['payload' => "\x00\xffA", 'null_value' => null, 'empty_value' => '',
        'dec_value' => '12345678901234567890.1234567890', 'big_value' => '18446744073709551615'];
    $row = rp_observe($db, $report, 'get_row_object', 'get_row/OBJECT', $sql, fn() => $db->get_row($sql, OBJECT));
    rp_check($row instanceof stdClass && get_object_vars($row) === $expected, 'object_row_contract');
    $row = rp_observe($db, $report, 'get_row_array_a', 'get_row/ARRAY_A', $sql, fn() => $db->get_row($sql, ARRAY_A));
    rp_check($row === $expected, 'associative_row_contract');
    $row = rp_observe($db, $report, 'get_row_array_n', 'get_row/ARRAY_N', $sql, fn() => $db->get_row($sql, ARRAY_N));
    rp_check($row === array_values($expected), 'numeric_row_contract');
    foreach ([OBJECT, ARRAY_A, ARRAY_N] as $mode) {
        $rows = rp_observe($db, $report, 'get_results_' . strtolower($mode), 'get_results/' . $mode, $sql, fn() => $db->get_results($sql, $mode));
        $actual = $mode === OBJECT ? array_map('get_object_vars', $rows) : $rows;
        rp_check($actual === [$mode === ARRAY_N ? array_values($expected) : $expected], 'results_mode_contract');
    }
    $sql = "SELECT marker,payload FROM rp_wire_fixture WHERE marker='rp-wire'";
    $keyed = rp_observe($db, $report, 'get_results_object_k', 'get_results/OBJECT_K', $sql, fn() => $db->get_results($sql, OBJECT_K));
    rp_check(array_keys($keyed) === ['rp-wire'] && get_object_vars($keyed['rp-wire']) === ['marker' => 'rp-wire', 'payload' => "\x00\xffA"], 'keyed_results_contract');
    $sql = "SELECT payload FROM rp_wire_fixture WHERE marker='rp-wire'";
    $col = rp_observe($db, $report, 'get_col_binary', 'get_col', $sql, fn() => $db->get_col($sql));
    rp_check($col === ["\x00\xffA"], 'binary_col_contract');
    $col = rp_observe($db, $report, 'get_col_cached', 'get_col/null', null, fn() => $db->get_col(null), 0);
    rp_check($col === ["\x00\xffA"], 'cached_col_contract');
    foreach (['SELECT NULL' => null, "SELECT X''" => null, 'SELECT 17' => '17'] as $sql => $expected_var) {
        $value = rp_observe($db, $report, 'get_var_' . ($expected_var === null ? (str_contains($sql, 'NULL') ? 'null' : 'empty') : 'integer_string'), 'get_var', $sql, fn() => $db->get_var($sql));
        rp_check($value === $expected_var, 'get_var_contract');
    }
    $sql = 'SELECT payload,NULL AS null_value FROM rp_wire_fixture WHERE 1=0';
    $rows = rp_observe($db, $report, 'zero_rows', 'get_results/ARRAY_N', $sql, fn() => $db->get_results($sql, ARRAY_N));
    rp_check($rows === [] && $db->num_rows === 0, 'zero_rows_contract');
    $columns = rp_observe($db, $report, 'zero_row_column_names', 'get_col_info/name', null, fn() => $db->get_col_info('name'), 0);
    rp_check($columns === ['payload', 'null_value'], 'zero_row_columns_lost');

    // SQL remains ASCII. Binary input is passed as HEX text through real prepare().
    $prepared = $db->prepare('SELECT UNHEX(%s) AS escaped_value,%d AS integer_value,%i AS payload_column FROM %i WHERE marker=%s',
        bin2hex("quote'\\\x00\xff"), 7, 'payload', 'rp_wire_fixture', 'rp-wire');
    rp_check(is_string($prepared) && preg_match('/\A[\x00-\x7f]*\z/D', $prepared) === 1, 'prepared_ascii_required');
    $report['prepare'] = ['returned_sql_hex' => bin2hex($prepared), 'placeholder_removed_sql_hex' => bin2hex($db->remove_placeholder_escape($prepared))];
    $prepared_row = rp_observe($db, $report, 'prepare_identifiers_and_values', 'get_row/ARRAY_N', $prepared, fn() => $db->get_row($prepared, ARRAY_N));
    rp_check($prepared_row === ["quote'\\\x00\xff", '7', "\x00\xffA"], 'prepare_hex_round_trip');
    $escape_input = "quote'\\_";
    $escaped_sql = $db->prepare('SELECT %s AS escaped_value', $escape_input);
    $escaped = rp_observe($db, $report, 'prepare_escape', 'get_var', $escaped_sql, fn() => $db->get_var($escaped_sql));
    rp_check($escaped === $escape_input, 'prepare_escape_round_trip');

    rp_check(rp_query($db, $report, 'same_value_update', "UPDATE rp_wire_fixture SET payload=X'00ff41' WHERE marker='rp-wire'") === 0 && $db->rows_affected === 0, 'same_update_contract');
    rp_check(rp_query($db, $report, 'changed_update', "UPDATE rp_wire_fixture SET payload=X'00ff42' WHERE marker='rp-wire'") === 1 && $db->rows_affected === 1, 'changed_update_contract');
    rp_check(rp_query($db, $report, 'ascii_syntax_error', 'SELEC rp_fixed_ascii_error') === false && $db->last_error !== '', 'syntax_error_contract');
    rp_error_identity($db, $report);
    rp_check($report['syntax_error_code'] === 1064 && $report['syntax_error_state'] === '42000', 'syntax_error_identity');
    $sql = 'SELECT 23';
    rp_check(rp_observe($db, $report, 'error_recovery', 'get_var', $sql, fn() => $db->get_var($sql)) === '23' && $db->last_error === '', 'error_recovery_contract');
    rp_check(rp_query($db, $report, 'begin', 'BEGIN') === 0, 'begin_contract');
    rp_check(rp_query($db, $report, 'transaction_update', "UPDATE rp_wire_fixture SET payload=X'00ff43' WHERE marker='rp-wire'") === 1, 'transaction_update_contract');
    rp_check(rp_query($db, $report, 'rollback', 'ROLLBACK') === 0, 'rollback_contract');
    $sql = "SELECT payload FROM rp_wire_fixture WHERE marker='rp-wire'";
    rp_check(rp_observe($db, $report, 'rollback_read', 'get_var', $sql, fn() => $db->get_var($sql)) === "\x00\xffB", 'rollback_read_contract');
    $report['final_num_queries'] = $db->num_queries;
}
