# RecastPress 実PHP-Wasm hook PoC デモ手順

## デモ経路

このPoCの実行環境はUbuntu 24.04のGitHub Actionsです。Windowsの開発環境では`check.py`は実行できません。既存の[Mac Dockerデモ手順](local-demo-macos.ja.md)は静的ページ表示用で、このPHP-Wasm hook PoCとは別のデモです。

最新の成功[workflow run 44318637943](https://github.com/KANIKAMA-Carcinization/ReCastPress/actions/runs/44318637943)は、PHP-WASIX 8.5.12とWordPress 7.1.3を使い、6シナリオすべてが通過しました。`action-nested`では親actionのcallbackから子`do_action()`を実行し、親callbackへ戻って後続callbackまで続くことをnative oracleと全観測値で照合しています。次のデモ更新では、実行中のactionへ将来優先度callbackを追加するシナリオも含めます。現在のartifactは2026-12-02 14:08 UTCまで保存されます。

## 成功runとartifactを開く

1. [成功run 44318637943](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44318637943)を開きます。run全体と`php-wasix` jobが成功していることを確認します。
2. [artifact 14435575180](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44318637943/artifacts/14435575180)をダウンロードします。artifact名は`v06-wp-hook-dispatch-44318637943-1`です。
3. ZIP内の`latest.json`で6シナリオの終了コードと観測値を見せます。`run-*.log`には実行時のJSON出力が入っています。artifact ZIPのSHA-256は`6b5c5c440218752a8724871827e543f8d3d874f739c31effb2a8bec07608ddce`です。

このworkflowは現在default branchの`main`にはなく、`workflow_dispatch`によるActions画面からの手動起動はできません。`dev/v0.6`上の対象ファイル変更を含むpushで自動実行されます。発表前に新しいrunが必要になった場合は、実際に検証対象となる変更をpushしてそのrunを使います。runのartifact名は`v06-wp-hook-dispatch-<run-id>-<run-attempt>`です。

## 発表時に見せる観測

`latest.json`の`run.result.action-ref-array`を開き、次を示します。

- `status`: `passed`、`native_oracle_contract_match`: `true`
- callback `first`は`seed`と`left`を受け取り、参照値を書き換える
- callback `second`は`value-mutated-by-first`と`extra-mutated-by-first`を受け取り、2つ目をさらに書き換える
- 呼び出し元の最終値は`value-mutated-by-first`と`extra-mutated-by-second`
- actionの戻り値は`null`。終了後は`current_action`、`doing_action`、`wp_hook_doing_action`、`current_priority`がfalse、`action_count`は1、解放後のlive handle数は0
- `run.exit_codes`の`filter`、`ref-array`、`action`、`action-ref-array`、`action-throwable`、`action-nested`はすべて0

直近の成功結果では、`action-ref-array`にPHP↔Rust間8入力/7出力frame、`action-nested`に11入力/9出力frameが記録されました。`run-*.log`にあるJSON全体を保存し、観測結果と実行commitを一緒に示せます。

## Throwableシナリオ

`latest.json`の`run.result.action-throwable`では、`RuntimeException`の同一PHP objectがRust境界を往復してcallback位置で再throwされ、外側のrequest境界で捕捉されたことを示します。`native_oracle_contract_match`は`true`、`same_throwable`は`true`、callback lease解放数は2、registry close前後のlive handle数は0です。throwでWPのdispatch loopとcleanupが中断されるため、後続callbackは実行されず、`current_action`/`current_filter`と`doing_action`、`$wp_current_filter`、WP_Hookのnesting/iteration/current-priority stateが残ります。これは例外を握りつぶす回復や同一workerの再利用を示すものではありません。

## 入れ子actionシナリオ

`latest.json`の`run.result.action-nested`では、`outer_parent_enter`が内側の`do_action()`を呼び、`inner_callback`完了後に親callbackが再開し、`outer_tail`へ進む順序を見せます。`native_oracle_contract_match`は`true`で、完全なイベント・hook stack・priority・WordPress内部stateを固定WordPress 7.1.3のnative oracleと照合しています。外側・内側のaction counterは各1、子action終了後は親stackとpriority 5へ戻り、最終stackは空、registry close時のlive handle数は0です。これは1段の入れ子だけを示し、さらに深い再入れやcallback tableの変更は対象外です。

## デモの範囲

これは固定WordPress fixtureに対する境界付き実行証明です。`do_action_ref_array()`では2つのstring参照と2 callbacksを、`action-throwable`では1つの`RuntimeException`のidentity保持・再throw・request境界捕捉を、`action-nested`では1段のnested `do_action()`から親callbackへの復帰を検証します。開発中のaction mutation scenarioは優先度5のcallback内で優先度15のcallbackを追加し、実行順序を `mutator → added → tail` と照合します。任意のPHP値グラフ、accepted-argsの切り詰め、0引数callbackのref-array経路、callback削除、同一優先度への追加、2段以上の入れ子、throw後のWP state回復やworker再利用、本番bootstrapとの統合はこのデモの対象外です。RecastPress全体がWordPress互換になったことや、本番readyであることを示すものではありません。
