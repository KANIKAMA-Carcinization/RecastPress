# RecastPress 実PHP-Wasm hook PoC デモ手順

## デモ経路

このPoCの実行環境はUbuntu 24.04のGitHub Actionsです。Windowsの開発環境では`check.py`は実行できません。既存の[Mac Dockerデモ手順](local-demo-macos.ja.md)は静的ページ表示用で、このPHP-Wasm hook PoCとは別のデモです。

最新の成功[workflow run 44280999738](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44280999738)は、PHP-WASIX 8.5.12とWordPress 7.1.3を使い、5シナリオすべてが通過しました。新しい`action-throwable`も、固定WordPress上のnative oracleとThrowable identityおよびdispatch中断後のstateが一致しています。artifactは2026-12-02 10:10 UTCまで保存されます。発表ではこの成功runとartifactを使えます。

## 成功runとartifactを開く

1. [成功run 44280999738](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44280999738)を開きます。run全体と`php-wasix` jobが成功していることを確認します。
2. [artifact 14418135967](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44280999738/artifacts/14418135967)をダウンロードします。artifact名は`v06-wp-hook-dispatch-44280999738-1`です。
3. ZIP内の`latest.json`で5シナリオの終了コードと観測値を見せます。`run-*.log`には実行時のJSON出力が入っています。artifactのSHA-256は`468f0a789247082f8b029316c9a2d2f6ea2d4d532302bb997623e4b9cdd7ca70`です。

このworkflowは現在default branchの`main`にはなく、`workflow_dispatch`によるActions画面からの手動起動はできません。`dev/v0.6`上の対象ファイル変更を含むpushで自動実行されます。発表前に新しいrunが必要になった場合は、実際に検証対象となる変更をpushしてそのrunを使います。runのartifact名は`v06-wp-hook-dispatch-<run-id>-<run-attempt>`です。

## 発表時に見せる観測

`latest.json`の`run.result.action-ref-array`を開き、次を示します。

- `status`: `passed`、`native_oracle_contract_match`: `true`
- callback `first`は`seed`と`left`を受け取り、参照値を書き換える
- callback `second`は`value-mutated-by-first`と`extra-mutated-by-first`を受け取り、2つ目をさらに書き換える
- 呼び出し元の最終値は`value-mutated-by-first`と`extra-mutated-by-second`
- actionの戻り値は`null`。終了後は`current_action`、`doing_action`、`wp_hook_doing_action`、`current_priority`がfalse、`action_count`は1、解放後のlive handle数は0
- `run.exit_codes`の`filter`、`ref-array`、`action`、`action-ref-array`、`action-throwable`はすべて0

直近の成功結果では、`action-ref-array`にPHP↔Rust間8入力/7出力frameが記録されました。`run-*.log`にあるJSON全体を保存し、観測結果と実行commitを一緒に示せます。

## Throwableシナリオ

`latest.json`の`run.result.action-throwable`では、`RuntimeException`の同一PHP objectがRust境界を往復してcallback位置で再throwされ、外側のrequest境界で捕捉されたことを示します。`native_oracle_contract_match`は`true`、`same_throwable`は`true`、callback lease解放数は2、registry close前後のlive handle数は0です。throwでWPのdispatch loopとcleanupが中断されるため、後続callbackは実行されず、`current_action`/`current_filter`と`doing_action`、`$wp_current_filter`、WP_Hookのnesting/iteration/current-priority stateが残ります。これは例外を握りつぶす回復や同一workerの再利用を示すものではありません。

## デモの範囲

これは固定WordPress fixtureに対する境界付き実行証明です。`do_action_ref_array()`では2つのstring参照と2 callbacksを、`action-throwable`では1つの`RuntimeException`のidentity保持・再throw・request境界捕捉を検証します。任意のPHP値グラフ、accepted-argsの切り詰め、0引数callbackのref-array経路、ネスト、throw後のWP state回復やworker再利用、本番bootstrapとの統合はこのデモの対象外です。RecastPress全体がWordPress互換になったことや、本番readyであることを示すものではありません。
