"""Prepare a source-bound WordPress hook dispatch overlay.

The overlay passes the hook name from plugin.php, then places the selected
dispatcher handoff inside WP_Hook::apply_filters so a method-scoped closure can
update its private iteration and priority state. The adapter itself is supplied
by a later runtime fixture and is deliberately not included here.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / ".reference" / "wordpress-7.1.3"
PINNED_ARCHIVE_SHA1 = "491a78c7676b15dffcc69130c6054b8f49228a91"
PLUGIN = Path("wp-includes/plugin.php")
HOOK = Path("wp-includes/class-wp-hook.php")
PINNED_FILES = {
    PLUGIN: "d70b9e18d34ab3fe46e15548a5e8e089ea5920832f06a4d89a9d64b2f7938b0d",
    HOOK: "b839c0e5672246bca8db1ab781ec8835f7732f253c375a237cbf6ec536e8d12e",
}
PLUGIN_FILTER_OLD = b"$filtered = $wp_filter[ $hook_name ]->apply_filters( $value, $args );"
PLUGIN_FILTER_NEW = b"$filtered = $wp_filter[ $hook_name ]->apply_filters( $value, $args, $hook_name );"
PLUGIN_REF_FILTER_OLD = b"$filtered = $wp_filter[ $hook_name ]->apply_filters( $args[0], $args );"
PLUGIN_REF_FILTER_NEW = b"$filtered = $wp_filter[ $hook_name ]->apply_filters( $args[0], $args, $hook_name );"
PLUGIN_ACTION_OLD = b"$wp_filter[ $hook_name ]->do_action( $arg );"
PLUGIN_ACTION_NEW = b"$wp_filter[ $hook_name ]->do_action( $arg, $hook_name );"
HOOK_DO_ACTION_SIGNATURE_OLD = b"public function do_action( $args ) {"
HOOK_DO_ACTION_SIGNATURE_NEW = b"public function do_action( $args, $recastpress_hook_name = null ) {"
HOOK_DO_ACTION_CALL_OLD = b"$this->apply_filters( '', $args );"
HOOK_DO_ACTION_CALL_NEW = b"$this->apply_filters( '', $args, $recastpress_hook_name );"
HOOK_SIGNATURE_OLD = b"public function apply_filters( $value, $args ) {"
HOOK_SIGNATURE_NEW = b"public function apply_filters( $value, $args, $recastpress_hook_name = null ) {"
HOOK_BODY_OLD = (
    b"\t\tif ( ! $this->callbacks ) {\n"
    b"\t\t\treturn $value;\n"
    b"\t\t}\n\n"
    b"\t\t$nesting_level = $this->nesting_level++;"
)
HOOK_BODY_NEW = (
    b"\t\tif ( ! $this->callbacks ) {\n"
    b"\t\t\treturn $value;\n"
    b"\t\t}\n\n"
    b"\t\tif ( ! $this->doing_action && null !== $recastpress_hook_name\n"
    b"\t\t\t&& function_exists( 'recastpress_wp_should_dispatch_selected_hook' )\n"
    b"\t\t\t&& function_exists( 'recastpress_wp_dispatch_selected_hook' )\n"
    b"\t\t\t&& recastpress_wp_should_dispatch_selected_hook( $recastpress_hook_name, $this )\n"
    b"\t\t) {\n"
    b"\t\t\t$nesting_level = $this->nesting_level++;\n"
    b"\t\t\t$this->iterations[ $nesting_level ] = $this->priorities;\n"
    b"\t\t\t$recastpress_sync = function ( $operation, $priority = null ) use ( $nesting_level ) {\n"
    b"\t\t\t\tif ( 'set_priority' === $operation ) {\n"
    b"\t\t\t\t\treset( $this->iterations[ $nesting_level ] );\n"
    b"\t\t\t\t\twhile ( current( $this->iterations[ $nesting_level ] ) !== $priority ) {\n"
    b"\t\t\t\t\t\tif ( false === next( $this->iterations[ $nesting_level ] ) ) {\n"
    b"\t\t\t\t\t\t\treturn false;\n"
    b"\t\t\t\t\t\t}\n"
    b"\t\t\t\t\t}\n"
    b"\t\t\t\t\t$this->current_priority[ $nesting_level ] = $priority;\n"
    b"\t\t\t\t\treturn true;\n"
    b"\t\t\t\t}\n"
    b"\t\t\t\tif ( 'finish' === $operation ) {\n"
    b"\t\t\t\t\tunset( $this->iterations[ $nesting_level ] );\n"
    b"\t\t\t\t\tunset( $this->current_priority[ $nesting_level ] );\n"
    b"\t\t\t\t\t--$this->nesting_level;\n"
    b"\t\t\t\t\treturn true;\n"
    b"\t\t\t\t}\n"
    b"\t\t\t\treturn false;\n"
    b"\t\t\t};\n"
    b"\t\t\treturn recastpress_wp_dispatch_selected_hook( $recastpress_hook_name, $this, $value, $args, $nesting_level, $recastpress_sync );\n"
    b"\t\t}\n\n"
    b"\t\t$nesting_level = $this->nesting_level++;"
)
HOOK_ACTION_BRANCH_OLD = (
    b"\t\t$nesting_level = $this->nesting_level++;\n\n"
    b"\t\t$this->iterations[ $nesting_level ] = $this->priorities;\n\n"
    b"\t\t$num_args = count( $args );"
)
HOOK_ACTION_BRANCH_NEW = (
    b"\t\tif ( $this->doing_action && null !== $recastpress_hook_name\n"
    b"\t\t\t&& function_exists( 'recastpress_wp_should_dispatch_selected_action' )\n"
    b"\t\t\t&& function_exists( 'recastpress_wp_dispatch_selected_action' )\n"
    b"\t\t\t&& recastpress_wp_should_dispatch_selected_action( $recastpress_hook_name, $this )\n"
    b"\t\t) {\n"
    b"\t\t\t$nesting_level = $this->nesting_level++;\n"
    b"\t\t\t$this->iterations[ $nesting_level ] = $this->priorities;\n"
    b"\t\t\t$recastpress_sync = function ( $operation, $priority = null ) use ( $nesting_level ) {\n"
    b"\t\t\t\tif ( 'set_priority' === $operation ) {\n"
    b"\t\t\t\t\treset( $this->iterations[ $nesting_level ] );\n"
    b"\t\t\t\t\twhile ( current( $this->iterations[ $nesting_level ] ) !== $priority ) {\n"
    b"\t\t\t\t\t\tif ( false === next( $this->iterations[ $nesting_level ] ) ) {\n"
    b"\t\t\t\t\t\t\treturn false;\n"
    b"\t\t\t\t\t\t}\n"
    b"\t\t\t\t\t}\n"
    b"\t\t\t\t\t$this->current_priority[ $nesting_level ] = $priority;\n"
    b"\t\t\t\t\treturn true;\n"
    b"\t\t\t\t}\n"
    b"\t\t\t\tif ( 'finish' === $operation ) {\n"
    b"\t\t\t\t\tunset( $this->iterations[ $nesting_level ] );\n"
    b"\t\t\t\t\tunset( $this->current_priority[ $nesting_level ] );\n"
    b"\t\t\t\t\t--$this->nesting_level;\n"
    b"\t\t\t\t\treturn true;\n"
    b"\t\t\t\t}\n"
    b"\t\t\t\treturn false;\n"
    b"\t\t\t};\n"
    b"\t\t\treturn recastpress_wp_dispatch_selected_action( $recastpress_hook_name, $this, $args, $nesting_level, $recastpress_sync );\n"
    b"\t\t}\n\n"
    b"\t\t$nesting_level = $this->nesting_level++;\n\n"
    b"\t\t$this->iterations[ $nesting_level ] = $this->priorities;\n\n"
    b"\t\t$num_args = count( $args );"
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def prepare(source_root, output_root, allow_temporary=False):
    source_root = Path(source_root).resolve()
    output_root = Path(output_root).resolve()
    owned_root = ROOT.resolve()
    target_root = ROOT / "target"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "wordpress_reference.py"), "verify"],
        cwd=ROOT,
        check=True,
    )
    if source_root != REFERENCE.resolve():
        raise ValueError("source root must be the verified WordPress 7.1.3 tree")
    if not allow_temporary:
        if target_root.is_symlink():
            raise ValueError("repository target directory must not be a symlink")
        resolved_target = target_root.resolve()
        if not resolved_target.is_relative_to(owned_root):
            raise ValueError("repository target directory resolves outside the workspace")
        if not output_root.is_relative_to(resolved_target):
            raise ValueError("overlay output must be inside the repository target directory")
    if output_root.exists():
        raise ValueError("overlay output already exists")

    originals = {path: (source_root / path).read_bytes() for path in PINNED_FILES}
    for path, original in originals.items():
        if sha256(original) != PINNED_FILES[path]:
            raise ValueError(f"pinned WordPress source file hash differs: {path.as_posix()}")
    replacements = {
        PLUGIN: [
            (PLUGIN_FILTER_OLD, PLUGIN_FILTER_NEW),
            (PLUGIN_REF_FILTER_OLD, PLUGIN_REF_FILTER_NEW),
            (PLUGIN_ACTION_OLD, PLUGIN_ACTION_NEW),
        ],
        HOOK: [
            (HOOK_DO_ACTION_SIGNATURE_OLD, HOOK_DO_ACTION_SIGNATURE_NEW),
            (HOOK_DO_ACTION_CALL_OLD, HOOK_DO_ACTION_CALL_NEW),
            (HOOK_SIGNATURE_OLD, HOOK_SIGNATURE_NEW),
            (HOOK_BODY_OLD, HOOK_BODY_NEW),
            (HOOK_ACTION_BRANCH_OLD, HOOK_ACTION_BRANCH_NEW),
        ],
    }
    patched_files = {}
    for path, hunks in replacements.items():
        patched = originals[path]
        for old, new in hunks:
            if patched.count(old) != 1:
                raise ValueError(f"expected one pinned overlay hunk in {path.as_posix()}")
            patched = patched.replace(old, new, 1)
        patched_files[path] = patched

    output_root.mkdir(parents=True)
    for path, patched in patched_files.items():
        target = output_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(patched)
    receipt = {
        "schema": "recastpress.wp-hook-dispatch-overlay.v4",
        "wordpress_version": "7.1.3",
        "wordpress_archive_sha1": PINNED_ARCHIVE_SHA1,
        "source_sha256": {path.as_posix(): sha256(originals[path]) for path in PINNED_FILES},
        "overlay_sha256": {path.as_posix(): sha256(patched_files[path]) for path in PINNED_FILES},
        "changed_files": [path.as_posix() for path in PINNED_FILES],
        "preserved_plugin_php_scope": [
            "wp_filters counter increment",
            "wp_actions preincrement remains in plugin.php",
            "all hook dispatch",
            "wp_current_filter push remains; normal-return pop remains and Throwable leaves it stale",
            "empty named hook return",
            "do_action empty-argument placeholder remains in plugin.php",
        ],
        "wp_hook_method_has_sync_handoff": True,
        "selected_object_predicate_guards_handoff": True,
        "selected_action_object_predicate_guards_handoff": True,
        "plugin_do_action_counter_all_and_stack_remain_wordpress_owned": True,
        "wp_hook_sync_operations": ["set_priority", "finish"],
        "callback_table_mutation_during_dispatch_supported": False,
        "direct_method_call_with_third_argument_in_scope": False,
        "apply_filters_ref_array_bridged": True,
        "apply_filters_ref_array_bridge_scope": "fixture-only request-scoped PHP argument-frame handle; success path",
        "action_callback_table_mutation_during_dispatch_supported": False,
        "action_throwable_recovery_proven": False,
        "action_all_hook_dispatched_by_rust": False,
        "adapter_runtime_included": False,
        "product_ready": False,
    }
    (output_root / "overlay.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?", default=REFERENCE)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        prepare(args.source, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Overlay preparation failed: {error}\n")
