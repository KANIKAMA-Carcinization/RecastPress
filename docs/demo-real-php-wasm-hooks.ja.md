# RecastPress 実PHP-Wasm hook PoC デモ手順

## デモ本番用の固定成功run

最新の成功runは[44354882884](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44354882884)、対象commitは`4fb5821c9dfaf35b7332ec8108d1b7a34ffa559f`です。固定PHP-WASIX 8.5.12とWordPress 7.1.3を使い、9シナリオがすべて成功し、source inputs不変も確認しました。

発表ではrunの`php-wasix` jobとartifact `v06-wp-hook-dispatch-44354882884-1`を開き、`latest.json`の`action-deferred-add`を表示します。1回目の観測順は`all -> mutator -> peer -> tail`、2回目は`all -> mutator -> peer -> added -> tail`です。native oracle一致、`did_action()`の1から2への増加、最終callback table、hook state reset、live handles 0を確認できます。証跡は[source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-deferred-add-run-44354882884.json)に記録しています。artifactのSHA-256は`c9b2d1333e9ea462c8fb1f01c2ef89f410b2e42a84b3ee4695519076b7e75105`で、2026-12-02 17:24 UTCまで取得できます。

同一PHPリクエスト中の2回`do_action()`と、優先度5で追加したcallbackが2回目に実行される挙動はrun 44354882884で検証済みです。歴史的な単発証跡は[run 44340406987](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44340406987)と[source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-same-priority-run-44340406987.json)に残しています。

このPoCはGitHub ActionsのUbuntu runner上で動作する検証デモです。WindowsやMac上で`check.py`を直接実行する手順ではありません。既存のMac Dockerデモは別のデモ経路です。

## デモ経路

このPoCの実行環境はUbuntu 24.04のGitHub Actionsです。Windowsの開発環境では`check.py`は実行できません。既存の[Mac Dockerデモ手順](local-demo-macos.ja.md)は静的ページ表示用で、このPHP-Wasm hook PoCとは別のデモです。

Latest validated run: [44354882884](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44354882884), source commit `4fb5821c9dfaf35b7332ec8108d1b7a34ffa559f`. The pinned PHP-WASIX 8.5.12 and WordPress 7.1.3 workflow passed all nine scenarios. `action-deferred-add` proved that a priority-5 callback added during pass one is skipped there and invoked on the second `do_action()` in the same PHP request. The full observation matched the native oracle, including `did_action()` values 1 and 2. The earlier [callback removal run 44334354645](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44334354645) also passed.

## 成功runとartifactを開く

1. Open [successful run 44354882884](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44354882884) and confirm the workflow and `php-wasix` job succeeded.
2. Download [artifact 14452513411](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44354882884/artifacts/14452513411), named `v06-wp-hook-dispatch-44354882884-1`.
3. In `latest.json`, show all nine exit codes and the `action-deferred-add` observation. Highlight both pass orders, `action_count: 2`, native oracle match, reset hook state, and zero live handles. The artifact ZIP SHA-256 is `c9b2d1333e9ea462c8fb1f01c2ef89f410b2e42a84b3ee4695519076b7e75105`.

このworkflowは現在default branchの`main`にはなく、`workflow_dispatch`によるActions画面からの手動起動はできません。`dev/v0.6`上の対象ファイル変更を含むpushで自動実行されます。runのartifact名は`v06-wp-hook-dispatch-<run-id>-<run-attempt>`です。

## 最新実行の扱い

run [44425880569](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44425880569)はcommit `42d6b008353c90a9865524fd64d6b745d6304838`を対象に、新しいordinary action accepted-args fixtureを含む10シナリオを実行中です。完了して成功するまでは、デモ証跡として扱いません。所要は過去のrunで約18分でした。二日前の発表では、上記の固定成功run `44354882884`とその取得済みartifactを使い、新runはActions画面で結果だけ確認します。

## 発表時に見せる観測

二日後のデモは、`latest.json`の`run.result.action-deferred-add`を開き、次を示します。

- 1回目のcallback順は`all -> mutator -> peer -> tail`で、優先度5で追加したcallbackはまだ実行されない
- 2回目は`all -> mutator -> peer -> added -> tail`。同一PHP request中に遅延callbackが実行される
- `did_action()`が1から2へ進み、native WordPress oracleが一致し、callback tableが4行、hook state reset、解放後live handle数が0
- `run.exit_codes`の9シナリオがすべて0

予備として同じartifactの`run.result.action`を表示し、WordPressの`all` callback、通常callback順、accepted args、action counterとcleanupを説明できます。新runが成功してreceipt/artifactを確認できたら、この資料と発表用runを更新します。

追加の技術説明が必要な場合だけ、`latest.json`の`run.result.action-ref-array`を開き、次を示します。

- `status`: `passed`、`native_oracle_contract_match`: `true`
- callback `first`は`seed`と`left`を受け取り、参照値を書き換える
- callback `second`は`value-mutated-by-first`と`extra-mutated-by-first`を受け取り、2つ目をさらに書き換える
- 呼び出し元の最終値は`value-mutated-by-first`と`extra-mutated-by-second`
- actionの戻り値は`null`。終了後は`current_action`、`doing_action`、`wp_hook_doing_action`、`current_priority`がfalse、`action_count`は1、解放後のlive handle数は0
- All nine `run.exit_codes` entries (`filter`, `ref-array`, `action`, `action-ref-array`, `action-throwable`, `action-nested`, `action-mutation`, `action-removal`, `action-deferred-add`) are 0.

The latest successful run records the ref-array PHP/Rust frame exchange, future-priority addition, active callback removal, and the same-priority callback's invocation on the second pass. `run-*.log` contains the full JSON observations, bridge frames, and source commit.

## Throwableシナリオ

`latest.json`の`run.result.action-throwable`では、`RuntimeException`の同一PHP objectがRust境界を往復してcallback位置で再throwされ、外側のrequest境界で捕捉されたことを示します。`native_oracle_contract_match`は`true`、`same_throwable`は`true`、callback lease解放数は2、registry close前後のlive handle数は0です。throwでWPのdispatch loopとcleanupが中断されるため、後続callbackは実行されず、`current_action`/`current_filter`と`doing_action`、`$wp_current_filter`、WP_Hookのnesting/iteration/current-priority stateが残ります。これは例外を握りつぶす回復や同一workerの再利用を示すものではありません。

## 入れ子actionシナリオ

`latest.json`の`run.result.action-nested`では、`outer_parent_enter`が内側の`do_action()`を呼び、`inner_callback`完了後に親callbackが再開し、`outer_tail`へ進む順序を見せます。`native_oracle_contract_match`は`true`で、完全なイベント・hook stack・priority・WordPress内部stateを固定WordPress 7.1.3のnative oracleと照合しています。外側・内側のaction counterは各1、子action終了後は親stackとpriority 5へ戻り、最終stackは空、registry close時のlive handle数は0です。これは1段の入れ子だけを示し、さらに深い再入れやcallback tableの変更は対象外です。

## デモの範囲

This is bounded execution evidence for fixed WordPress fixtures. It covers reference-array callbacks, a terminal Throwable, one nested action, future-priority addition, removal of a pending callback, and same-priority addition deferred to the next action pass in one request. Arbitrary PHP values, accepted-argument truncation, zero-argument ref-array callbacks, deeper nesting, Throwable recovery or worker reuse, and production bootstrap integration remain outside this demo. It does not establish full WordPress compatibility or production readiness.
