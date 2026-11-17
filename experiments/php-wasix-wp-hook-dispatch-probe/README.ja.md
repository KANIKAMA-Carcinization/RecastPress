# 実WordPress hook dispatch PoC

この専用probeは固定WordPress 7.1.3の `plugin.php` から対象hook名を `WP_Hook::apply_filters()` へ渡し、method内部から選択済み実objectをDispatcherへ渡すPHP-Wasm経路を検証する。既存の互換性profileやbootstrap probeのsource-bound receiptには変更を加えない。

## 現在の実装

`prepare_overlay.py` は公式archive identityと展開済みsource treeを検証し、`plugin.php` でhook名をmethodへ渡す。`class-wp-hook.php` はside-effect-free `recastpress_wp_should_dispatch_selected_hook()` predicateが選択済み実objectを識別した場合だけhandoffする。非対象のcore hookは元のWP_Hookループへ進む。handoffはmethod内に置き、同期closureからprivate iteration pointer/current priorityを更新できる構造にする。正常終了時はadapterがclosureへ `finish` を送りiterations/current_priorityをunsetしてnestingを一度減らし、Throwable時はその呼出しを飛ばしてWPのstale stateを保つ設計である。これはsource shapeの証拠で、runtime挙動は未検証。対象source二つは固定SHA-256で別途pinし、receiptに変更前後のhashを記録する。

```powershell
python -B experiments/php-wasix-wp-hook-dispatch-probe/prepare_overlay.py .reference/wordpress-7.1.3 target/wp-hook-dispatch-overlay
python -B experiments/php-wasix-wp-hook-dispatch-probe/check_overlay.py
```

このbounded sliceではdispatch中のcallback table mutationをfixtureの前提として発生させず、runtimeでdispatch前後のtable fingerprintを照合して発生有無を検出する。`WP_Hook::apply_filters()` の直接呼出しへ第三引数を渡す経路は対象外で、その呼出しがbridge predicateを満たした場合の挙動は保証しない。対象とするのはWordPress `apply_filters()` が選択したobjectを渡す経路である。runtimeはpredicate/adapterを `plugin.php` 読込前に定義する。`.github/workflows/v06-wp-hook-overlay.yml` は固定WP sourceを取得・検証し、overlayの再生成結果とcommitted evidenceを比較する。これはsource overlay準備の証跡であり、callback table import、PHP-Wasm実行、native比較、closure駆動のpriority更新実測、Throwable cleanup実測、CGI channel、製品採用を証明しない。`apply_filters_ref_array()` とactionsもこの段階では未接続である。runtimeを実装し同じoverlayでnativeとPHP-Wasmを比較するまでprobeは未完了である。

## Validation record

- `python -B experiments/php-wasix-wp-hook-dispatch-probe/check_overlay.py` passed: 3,782 pinned WordPress source files verified and the v2 receipt plus both overlay files reproduced byte-for-byte.
- [GitHub Actions run 44066575473](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44066575473) succeeded on 2026-11-17. It verifies the pinned source overlay only; it does not execute PHP-Wasm or the Dispatcher adapter.
