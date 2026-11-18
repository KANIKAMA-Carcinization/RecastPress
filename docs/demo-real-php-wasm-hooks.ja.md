# RecastPress 実PHP-Wasm hook PoC デモ手順

## デモ経路

このPoCの実行環境はUbuntu 24.04のGitHub Actionsです。Windowsの開発環境では`check.py`は実行できません。既存の[Mac Dockerデモ手順](local-demo-macos.ja.md)は静的ページ表示用で、このPHP-Wasm hook PoCとは別のデモです。

手元で成功を確認した[workflow run 44269393623](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44269393623)は、PHP-WASIX 8.5.12とWordPress 7.1.3を使い、4シナリオすべてが通過しました。実行後のartifactは2026-12-02まで保存されます。発表ではこの成功runとartifactを使えます。

## 成功runとartifactを開く

1. [成功run 44269393623](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44269393623)を開きます。run全体と`php-wasix` jobが成功していることを確認します。
2. [artifact 14411269527](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44269393623/artifacts/14411269527)をダウンロードします。artifact名は`v06-wp-hook-dispatch-44269393623-1`です。
3. ZIP内の`latest.json`で4シナリオの終了コードと観測値を見せます。`run-*.log`には実行時のJSON出力が入っています。

このworkflowは現在default branchの`main`にはなく、`workflow_dispatch`によるActions画面からの手動起動はできません。`dev/v0.6`上の対象ファイル変更を含むpushで自動実行されます。発表前に新しいrunが必要になった場合は、実際に検証対象となる変更をpushしてそのrunを使います。runのartifact名は`v06-wp-hook-dispatch-<run-id>-<run-attempt>`です。

## 発表時に見せる観測

`latest.json`の`run.result.action-ref-array`を開き、次を示します。

- `status`: `passed`、`native_oracle_contract_match`: `true`
- callback `first`は`seed`と`left`を受け取り、参照値を書き換える
- callback `second`は`value-mutated-by-first`と`extra-mutated-by-first`を受け取り、2つ目をさらに書き換える
- 呼び出し元の最終値は`value-mutated-by-first`と`extra-mutated-by-second`
- actionの戻り値は`null`。終了後は`current_action`、`doing_action`、`wp_hook_doing_action`、`current_priority`がfalse、`action_count`は1、解放後のlive handle数は0
- `run.exit_codes`の`filter`、`ref-array`、`action`、`action-ref-array`はすべて0

直近の成功結果では、`action-ref-array`にPHP↔Rust間8入力/7出力frameが記録されました。`run-*.log`にあるJSON全体を保存し、観測結果と実行commitを一緒に示せます。

## デモの範囲

これは固定WordPress fixtureに対する成功経路の実行証明です。`do_action_ref_array()`では2つのstring参照と2 callbacksを検証します。任意のPHP値グラフ、accepted-argsの切り詰め、0引数callback、ネスト、Throwable recovery、本番bootstrapとの統合はこのデモの対象外です。RecastPress全体がWordPress互換になったことや、本番readyであることを示すものではありません。
