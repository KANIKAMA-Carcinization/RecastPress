# RecastPress 実PHP-Wasm hook PoC デモ手順

## 最新の実測デモ

最新の成功runは[44340406987](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44340406987)、対象commitは`42f85b7b77657a8e5f0d7fc4d513eab34d5e6d22`です。固定PHP-WASIX 8.5.12とWordPress 7.1.3を使い、9シナリオがすべて成功し、source inputs不変も確認しました。

発表ではrunの`php-wasix` jobとartifact `v06-wp-hook-dispatch-44340406987-1`を開き、`latest.json`の`action-same-priority`を表示します。観測順は`all -> mutator -> peer -> tail`です。実行中の優先度5に追加したcallbackはcallback tableに残りますが、そのaction passでは呼び出されず、終了時のlive handlesは0になります。`action-removal`の実測は[run 44334354645](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44334354645)と[source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-removal-run-44334354645.json)、同優先度追加は[source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-same-priority-run-44340406987.json)に記録しています。artifactは2026-12-02 16:08 UTCまで取得できます。

現在、同一リクエストで2回 `do_action()` を実行する`action-deferred-add`シナリオを追加しています。期待値は1回目`all -> mutator -> peer -> tail`、2回目`all -> mutator -> peer -> added -> tail`で、native oracle・`did_action()`カウンター・request-close時のhandle解放を照合します。次回CIが成功するまでは、この挙動をデモ済みとして説明しません。

このPoCはGitHub ActionsのUbuntu runner上で動作する検証デモです。WindowsやMac上で`check.py`を直接実行する手順ではありません。既存のMac Dockerデモは別のデモ経路です。

## デモ経路

このPoCの実行環境はUbuntu 24.04のGitHub Actionsです。Windowsの開発環境では`check.py`は実行できません。既存の[Mac Dockerデモ手順](local-demo-macos.ja.md)は静的ページ表示用で、このPHP-Wasm hook PoCとは別のデモです。

Latest validated run remains [44340406987](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44340406987), source commit `42f85b7b77657a8e5f0d7fc4d513eab34d5e6d22`. It passed all nine then-current scenarios and proves only the single-pass same-priority behavior. A new `action-deferred-add` two-pass scenario is under validation; do not use run 44340406987 as evidence for second-pass invocation. The earlier [callback removal run 44334354645](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44334354645) also passed.

## 成功runとartifactを開く

1. Open the [successful run 44340406987](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44340406987) and confirm the workflow and `php-wasix` job succeeded.
2. Download [artifact 14444472666](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44340406987/artifacts/14444472666), named `v06-wp-hook-dispatch-44340406987-1`.
3. In `latest.json`, show the nine exit codes and observations, especially the native oracle match, registered row, reset hook state, and zero live handles in the historical `action-same-priority`. The artifact ZIP SHA-256 is `be65eda36eda1b30d611c5166c730800f179eeeb91bea1a4040aa08c191fb88d`.

このworkflowは現在default branchの`main`にはなく、`workflow_dispatch`によるActions画面からの手動起動はできません。`dev/v0.6`上の対象ファイル変更を含むpushで自動実行されます。発表前に新しいrunが必要になった場合は、実際に検証対象となる変更をpushしてそのrunを使います。runのartifact名は`v06-wp-hook-dispatch-<run-id>-<run-attempt>`です。

## 発表時に見せる観測

`latest.json`の`run.result.action-ref-array`を開き、次を示します。

- `status`: `passed`、`native_oracle_contract_match`: `true`
- callback `first`は`seed`と`left`を受け取り、参照値を書き換える
- callback `second`は`value-mutated-by-first`と`extra-mutated-by-first`を受け取り、2つ目をさらに書き換える
- 呼び出し元の最終値は`value-mutated-by-first`と`extra-mutated-by-second`
- actionの戻り値は`null`。終了後は`current_action`、`doing_action`、`wp_hook_doing_action`、`current_priority`がfalse、`action_count`は1、解放後のlive handle数は0
- All nine `run.exit_codes` entries (`filter`, `ref-array`, `action`, `action-ref-array`, `action-throwable`, `action-nested`, `action-mutation`, `action-removal`, `action-same-priority`) are 0.

The latest successful run records the ref-array PHP/Rust frame exchange, future-priority addition, active callback removal, and same-priority addition. `run-*.log` contains the full JSON observations, bridge frames, and source commit.

## Throwableシナリオ

`latest.json`の`run.result.action-throwable`では、`RuntimeException`の同一PHP objectがRust境界を往復してcallback位置で再throwされ、外側のrequest境界で捕捉されたことを示します。`native_oracle_contract_match`は`true`、`same_throwable`は`true`、callback lease解放数は2、registry close前後のlive handle数は0です。throwでWPのdispatch loopとcleanupが中断されるため、後続callbackは実行されず、`current_action`/`current_filter`と`doing_action`、`$wp_current_filter`、WP_Hookのnesting/iteration/current-priority stateが残ります。これは例外を握りつぶす回復や同一workerの再利用を示すものではありません。

## 入れ子actionシナリオ

`latest.json`の`run.result.action-nested`では、`outer_parent_enter`が内側の`do_action()`を呼び、`inner_callback`完了後に親callbackが再開し、`outer_tail`へ進む順序を見せます。`native_oracle_contract_match`は`true`で、完全なイベント・hook stack・priority・WordPress内部stateを固定WordPress 7.1.3のnative oracleと照合しています。外側・内側のaction counterは各1、子action終了後は親stackとpriority 5へ戻り、最終stackは空、registry close時のlive handle数は0です。これは1段の入れ子だけを示し、さらに深い再入れやcallback tableの変更は対象外です。

## デモの範囲

This is bounded execution evidence for fixed WordPress fixtures. It covers reference-array callbacks, a terminal Throwable, one nested action, future-priority addition, removal of a pending callback, and same-priority addition whose row remains registered but does not run during the active pass. Arbitrary PHP values, accepted-argument truncation, zero-argument ref-array callbacks, deeper nesting, Throwable recovery or worker reuse, and production bootstrap integration remain outside this demo. It does not establish full WordPress compatibility or production readiness.
