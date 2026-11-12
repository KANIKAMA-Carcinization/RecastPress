<?php
/** Fixed WordPress oracle for nested hook value/Throwable outcomes. */
declare(strict_types=1);
if (count($argv) !== 2) {
    fwrite(STDERR, "expected fixed WordPress directory\n");
    exit(2);
}
require $argv[1] . '/wp-includes/plugin.php';
$results = [];
foreach (['hooks', 'hooks_catch', 'hooks_abort'] as $scenario) {
    $wp_filter = []; $wp_actions = []; $wp_filters = []; $wp_current_filter = [];
    $item = (object) ['name' => 'root-before'];
    $exception = new RuntimeException('hooks child fixture throwable');
    $native = []; $observations = []; $removed = false; $registered = false;
    $same = null;
    $observe = static function (string $where) use (&$observations): void {
        global $wp_current_filter, $wp_filter;
        $observations[] = [
            'where' => $where,
            'current_hook' => current_filter() ?: null,
            'hook_depth' => count($wp_current_filter),
            'child_priority' => isset($wp_filter['child']) ? ($wp_filter['child']->current_priority() ?: null) : null,
        ];
    };
    $rootB = static function (string $value) use ($item, $observe): string {
        $observe('root_b');
        $item->name .= ':b';
        return $value . ':php-b';
    };
    $late = static function (string $value) use ($item): string {
        $item->name .= ':late';
        return $value . ':late';
    };
    add_filter('root', static function (string $value) use (&$native): string {
        $native[] = 'r5';
        return $value . ':r5';
    }, 5);
    add_filter('root', static function (string $value) use (
        $item, $rootB, $late, $scenario, $exception, $observe, &$same, &$removed, &$registered,
    ): string {
        $item->name .= '-child';
        $removed = remove_filter('root', $rootB, 10);
        $registered = add_filter('root', $late, 15);
        try {
            $child = apply_filters('child', $value);
        } catch (Throwable $thrown) {
            $same = $thrown === $exception;
            $observe('root_catch');
            if ($scenario === 'hooks_abort') {
                throw $thrown;
            }
            $child = null;
        }
        $item->name .= ':root-after-child';
        return $child === null ? $value . ':php-root-caught' : $child . ':php-root';
    }, 10);
    add_filter('root', $rootB, 10);
    add_filter('root', static function (string $value) use (&$native, $observe): string {
        $native[] = 'r20';
        $observe('r20');
        return $value . ':r20';
    }, 20);
    add_filter('child', static function (string $value) use (&$native): string {
        $native[] = 'c3';
        return $value . ':c3';
    }, 3);
    add_filter('child', static function (string $value) use ($item, $scenario, $exception, $observe): string {
        $item->name .= ':child';
        $observe('child');
        if ($scenario !== 'hooks') {
            throw $exception;
        }
        return $value . ':php-child';
    }, 5);
    add_filter('child', static function (string $value) use (&$native): string {
        $native[] = 'c7';
        return $value . ':c7';
    }, 7);
    $value = null;
    $uncaught = false;
    try {
        $value = apply_filters('root', 'seed');
    } catch (Throwable $thrown) {
        $same = $same && $thrown === $exception;
        $uncaught = true;
    }
    $results[] = [
        'scenario' => $scenario,
        'final_value_hex' => $value === null ? null : bin2hex($value),
        'final_name' => $item->name,
        'native_trace' => $native,
        'root_b_removed' => $removed,
        'late_registered' => $registered,
        'root_filters' => did_filter('root'),
        'child_filters' => did_filter('child'),
        'hook_depth_after' => count($wp_current_filter),
        'current_hook_after' => current_filter() ?: null,
        'child_priority_after' => $wp_filter['child']->current_priority() ?: null,
        'root_priority_after' => $wp_filter['root']->current_priority() ?: null,
        'uncaught_root' => $uncaught,
        'same_throwable' => $same,
        'observations' => $observations,
    ];
}
echo json_encode(['php_version' => PHP_VERSION, 'cases' => $results], JSON_THROW_ON_ERROR), "\n";
