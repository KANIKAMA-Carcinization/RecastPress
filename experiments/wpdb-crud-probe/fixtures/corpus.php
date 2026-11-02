<?php
declare(strict_types=1);

/** Shared native/bridge corpus; all CRUD helpers and schema processing are upstream. */
function rp_crud_corpus(wpdb $db, array &$report): void {
    $table = 'rp_crud_fixture';
    $sql = 'SELECT COUNT(*) FROM rp_crud_fixture';
    $empty = rp_crud_observe($db, $report, 'fixture_empty', 'get_var', [$sql], fn() => $db->get_var($sql));
    rp_check($empty === '0', 'crud_fixture_not_empty');

    $data = ['label' => 'keep', 'note' => 'first', 'count_value' => 7, 'ratio' => 1.125, 'payload' => 'ASCII'];
    $format = ['%s', '%s', '%d', '%f', '%s'];
    $result = rp_crud_observe($db, $report, 'insert_explicit_formats', 'insert', [$table, $data, $format],
        fn() => $db->insert($table, $data, $format));
    rp_check($result === 1 && $db->insert_id === 1, 'crud_explicit_insert');

    $data = ['count_value' => 7];
    $where = ['id' => 1];
    $result = rp_crud_observe($db, $report, 'update_same', 'update', [$table, $data, $where, ['%d'], ['%d']],
        fn() => $db->update($table, $data, $where, ['%d'], ['%d']));
    rp_check($result === 0 && $db->rows_affected === 0, 'crud_same_update');
    $data = ['count_value' => 9];
    $result = rp_crud_observe($db, $report, 'update_changed', 'update', [$table, $data, $where, ['%d'], ['%d']],
        fn() => $db->update($table, $data, $where, ['%d'], ['%d']));
    rp_check($result === 1 && $db->rows_affected === 1, 'crud_changed_update');

    $data = ['note' => null, 'ratio' => null];
    $result = rp_crud_observe($db, $report, 'update_null', 'update', [$table, $data, $where, ['%s', '%f'], ['%d']],
        fn() => $db->update($table, $data, $where, ['%s', '%f'], ['%d']));
    rp_check($result === 1, 'crud_null_update');
    $data = ['note' => ''];
    $where = ['ratio' => null, 'label' => 'keep'];
    $result = rp_crud_observe($db, $report, 'update_where_null_empty', 'update', [$table, $data, $where, '%s', ['%f', '%s']],
        fn() => $db->update($table, $data, $where, '%s', ['%f', '%s']));
    rp_check($result === 1, 'crud_where_null_and_empty');

    $data = ['id' => 1, 'label' => 'keep', 'note' => 'replaced', 'count_value' => 11, 'ratio' => 2.5, 'payload' => 'ASCII'];
    $format = ['%d', '%s', '%s', '%d', '%f', '%s'];
    $result = rp_crud_observe($db, $report, 'replace_existing', 'replace', [$table, $data, $format],
        fn() => $db->replace($table, $data, $format));
    rp_check($result === 2 && $db->insert_id === 1, 'crud_replace_existing');

    $data = ['label' => 'default', 'count_value' => '12', 'ratio' => '3.125', 'payload' => ''];
    $result = rp_crud_observe($db, $report, 'insert_default_formats', 'insert', [$table, $data, null],
        fn() => $db->insert($table, $data));
    rp_check($result === 1 && $db->insert_id === 2, 'crud_default_formats');
    // Upstream falls back to the first supplied format after the list ends.
    $data = ['label' => 'cyclic', 'count_value' => 13, 'note' => 'cycled', 'payload' => 'tail'];
    $format = ['%s', '%d'];
    $result = rp_crud_observe($db, $report, 'insert_exhausted_formats', 'insert', [$table, $data, $format],
        fn() => $db->insert($table, $data, $format));
    rp_check($result === 1 && $db->insert_id === 3, 'crud_exhausted_formats');

    $where = ['label' => 'missing'];
    $result = rp_crud_observe($db, $report, 'delete_missing', 'delete', [$table, $where, '%s'],
        fn() => $db->delete($table, $where, '%s'));
    rp_check($result === 0, 'crud_delete_missing');
    $where = ['label' => 'default', 'note' => null];
    $result = rp_crud_observe($db, $report, 'delete_where_null', 'delete', [$table, $where, ['%s', '%d']],
        fn() => $db->delete($table, $where, ['%s', '%d']));
    rp_check($result === 1, 'crud_delete_where_null');
    $where = ['label' => 'cyclic'];
    $result = rp_crud_observe($db, $report, 'delete_existing', 'delete', [$table, $where, '%s'],
        fn() => $db->delete($table, $where, '%s'));
    rp_check($result === 1, 'crud_delete_existing');

    $sql = 'SELECT id,label,note,count_value,ratio,payload FROM rp_crud_fixture ORDER BY id';
    $row = rp_crud_observe($db, $report, 'remaining_row', 'get_row/ARRAY_N', [$sql, ARRAY_N],
        fn() => $db->get_row($sql, ARRAY_N));
    rp_check($row === ['1', 'keep', 'replaced', '11', '2.500', 'ASCII'] && $db->num_rows === 1,
        'crud_final_values_before_delete');
    $where = ['id' => 1];
    $result = rp_crud_observe($db, $report, 'delete_remaining', 'delete', [$table, $where, '%d'],
        fn() => $db->delete($table, $where, '%d'));
    rp_check($result === 1, 'crud_final_helper_delete');
    $sql = 'SELECT COUNT(*) FROM rp_crud_fixture';
    $empty = rp_crud_observe($db, $report, 'final_empty', 'get_var', [$sql], fn() => $db->get_var($sql));
    rp_check($empty === '0', 'crud_final_table_not_empty');
    $report['final_row_count'] = $empty;
    $schema_query = bin2hex('SHOW FULL COLUMNS FROM `rp_crud_fixture`');
    rp_check(count(array_filter($report['query_trace_hex'], static fn(string $query): bool => $query === $schema_query)) === 1,
        'crud_schema_query_not_observed_once');
    rp_check(count($report['observations']) === 15, 'crud_complete_case_count');
}
