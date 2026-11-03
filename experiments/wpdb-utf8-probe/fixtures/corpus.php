<?php
declare(strict_types=1);

/** Fixed valid UTF-8 bytes; inherited wpdb APIs perform every SQL operation. */
function rp_crud_corpus(wpdb $db, array &$report): void {
    $hex = ['bmp' => 'e697a5e69cace8aa9ee383bbe6bca2e5ad97', 'emoji' => 'f09f9880f09f9a80f0a0aeb7',
        'quote' => 'e5bc95e794a8275ce7b58ce8b7af255f', 'composed' => 'c3a9', 'combining' => '65cc81'];
    $values = array_map('hex2bin', $hex);
    foreach ($values as $value) {
        rp_check(is_string($value) && strlen($value) <= 64 && preg_match('//u', $value) === 1, 'utf8_fixed_valid_input');
    }
    rp_check($values['composed'] !== $values['combining'], 'utf8_distinct_normalization_bytes');
    $report['utf8_input_hex'] = $hex;
    $report['normalization_applied'] = false;
    $table = 'rp_crud_fixture';
    $count = 'SELECT COUNT(*) FROM rp_crud_fixture';
    $empty = rp_crud_observe($db, $report, 'fixture_empty', 'get_var', [$count], fn() => $db->get_var($count));
    rp_check($empty === '0', 'utf8_fixture_not_empty');
    $expected = [];
    $id = 0;
    foreach ($values as $label => $value) {
        ++$id;
        $sql = rp_crud_observe($db, $report, 'prepare_' . $label, 'prepare/remove_placeholder_escape',
            ['SELECT %s AS utf8_value', $value],
            fn() => $db->remove_placeholder_escape($db->prepare('SELECT %s AS utf8_value', $value)));
        rp_check(is_string($sql), 'utf8_prepare_string');
        $like = rp_crud_observe($db, $report, 'esc_like_' . $label, 'esc_like', [$value], fn() => $db->esc_like($value));
        rp_check(is_string($like), 'utf8_like_string');
        $returned = rp_crud_observe($db, $report, 'select_' . $label, 'get_var', [$sql], fn() => $db->get_var($sql));
        rp_check($returned === $value, 'utf8_select_bytes_changed');
        $data = ['label' => $label, 'note' => $value, 'payload' => $value];
        $formats = ['%s', '%s', '%s'];
        $inserted = rp_crud_observe($db, $report, 'insert_' . $label, 'insert', [$table, $data, $formats],
            fn() => $db->insert($table, $data, $formats));
        rp_check($inserted === 1 && $db->insert_id === $id, 'utf8_insert_result');
        $expected[] = [(string) $id, $label, $value, $value];
    }
    $sql = 'SELECT id,label,note,payload FROM rp_crud_fixture ORDER BY id';
    $rows = rp_crud_observe($db, $report, 'rows_initial', 'get_results/ARRAY_N', [$sql, ARRAY_N],
        fn() => $db->get_results($sql, ARRAY_N));
    rp_check($rows === $expected, 'utf8_initial_rows_changed');
    $updated = hex2bin('e69bb4e696b0f09f9880c3a9');
    $data = ['note' => $updated, 'payload' => $updated]; $where = ['id' => 3];
    $changed = rp_crud_observe($db, $report, 'update_unicode', 'update',
        [$table, $data, $where, ['%s', '%s'], ['%d']], fn() => $db->update($table, $data, $where, ['%s', '%s'], ['%d']));
    rp_check($changed === 1, 'utf8_update_result');
    $same = rp_crud_observe($db, $report, 'update_same', 'update',
        [$table, $data, $where, ['%s', '%s'], ['%d']], fn() => $db->update($table, $data, $where, ['%s', '%s'], ['%d']));
    rp_check($same === 0, 'utf8_update_same_result');
    $expected[2][2] = $updated; $expected[2][3] = $updated;
    $rows = rp_crud_observe($db, $report, 'rows_updated', 'get_results/ARRAY_N', [$sql, ARRAY_N],
        fn() => $db->get_results($sql, ARRAY_N));
    rp_check($rows === $expected, 'utf8_updated_rows_changed');
    for ($id = 1; $id <= 5; ++$id) {
        $where = ['id' => $id];
        $deleted = rp_crud_observe($db, $report, 'delete_' . $id, 'delete', [$table, $where, '%d'],
            fn() => $db->delete($table, $where, '%d'));
        rp_check($deleted === 1, 'utf8_delete_result');
    }
    $empty = rp_crud_observe($db, $report, 'final_empty', 'get_var', [$count], fn() => $db->get_var($count));
    rp_check($empty === '0', 'utf8_final_rows_not_empty');
    $report['final_row_count'] = $empty;
    rp_check(count($report['observations']) === 31 && count($report['query_trace_hex']) === 22, 'utf8_complete_corpus');
}
