# RecastPress 実PHP-Wasm hook PoC デモ手順

## デモ本番用の固定成功run

最新の成功runは[44429302930](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44429302930)、対象commitは`ffdc2b72051c933fede6b8ba3ffb23a48e9bcb42`です。固定PHP-WASIX 8.5.12とWordPress 7.1.3を使い、9シナリオがすべて成功し、source inputs不変も確認しました。accepted_args=5で2引数を渡した通常action callbackも、`["left", "right"]`だけを受け取り、固定native oracleと一致しました。

発表ではrunの`php-wasix` jobとartifact `v06-wp-hook-dispatch-44429302930-1`を開き、`latest.json`の`action-deferred-add`を表示します。1回目の観測順は`all -> mutator -> peer -> tail`、2回目は`all -> mutator -> peer -> added -> tail`です。native oracle一致、`did_action()`の1から2への増加、最終callback table、hook state reset、live handles 0を確認できます。証跡は[成功receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-overlarge-accepted-args-run-44429302930.json)に記録しています。artifactのSHA-256は`53214c508659f583276ee4a68f91fdacf11e50bec08cd9b3af2d8ef8ead4d55f`で、2026-12-03 00:43 UTCまで取得できます。

同一PHPリクエスト中の2回`do_action()`と、優先度5で追加したcallbackが2回目に実行される挙動はrun 44429302930で検証済みです。過去の固定成功runは[44354882884](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44354882884)と[source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-deferred-add-run-44354882884.json)に残しています。

このPoCはGitHub ActionsのUbuntu runner上で動作する検証デモです。WindowsやMac上で`check.py`を直接実行する手順ではありません。既存のMac Dockerデモは別のデモ経路です。

## デモ経路

このPoCの実行環境はUbuntu 24.04のGitHub Actionsです。Windowsの開発環境では`check.py`は実行できません。既存の[Mac Dockerデモ手順](local-demo-macos.ja.md)は静的ページ表示用で、このPHP-Wasm hook PoCとは別のデモです。

Latest validated run: [44429302930](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44429302930), source commit `ffdc2b72051c933fede6b8ba3ffb23a48e9bcb42`. The pinned PHP-WASIX 8.5.12 and WordPress 7.1.3 workflow passed all nine scenarios with unchanged source inputs. `action-deferred-add` proved that a priority-5 callback added during pass one is skipped there and invoked on the second `do_action()` in the same PHP request. The full observation matched the native oracle, including `did_action()` values 1 and 2. The ordinary `action` case also confirmed that a callback registered with `accepted_args=5` receives the two supplied values, matching the pinned WordPress oracle. Receipt: [real-php-wasix-overlarge-accepted-args-run-44429302930.json](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-overlarge-accepted-args-run-44429302930.json). The earlier [callback removal run 44334354645](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44334354645) also passed.

## 成功runとartifactを開く

1. Open [successful run 44429302930](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44429302930) and confirm the workflow and `php-wasix` job succeeded.
2. Download [artifact 14486072410](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44429302930/artifacts/14486072410), named `v06-wp-hook-dispatch-44429302930-1`.
3. In `latest.json`, show all nine exit codes and the `action-deferred-add` observation. Highlight both pass orders, `action_count: 2`, native oracle match, reset hook state, and zero live handles. The artifact ZIP SHA-256 is `53214c508659f583276ee4a68f91fdacf11e50bec08cd9b3af2d8ef8ead4d55f`.

このworkflowは現在default branchの`main`にはなく、`workflow_dispatch`によるActions画面からの手動起動はできません。`dev/v0.6`上の対象ファイル変更を含むpushで自動実行されます。runのartifact名は`v06-wp-hook-dispatch-<run-id>-<run-attempt>`です。

## 最新実行の扱い

run [44425880569](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44425880569)はcheckerのJSON参照階層誤りで失敗しました。実PHP-WASM runner自体は9シナリオすべてexit 0で、`accepted_args=5`のcallbackは`["left", "right"]`を受け取っていました。比較箇所を直した[run 44429302930](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44429302930)は全シナリオ成功し、native oracle一致とsource inputs不変を確認しました。原因と最初のartifact digestは[診断receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-overlarge-accepted-args-run-44425880569.json)、修正版の証跡は[成功receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-overlarge-accepted-args-run-44429302930.json)に記録しています。二日後の発表ではrun `44429302930`の成功artifactを使います。

## 発表時に見せる観測

二日後のデモは、`latest.json`の`run.result.action-deferred-add`を開き、次を示します。

- 1回目のcallback順は`all -> mutator -> peer -> tail`で、優先度5で追加したcallbackはまだ実行されない
- 2回目は`all -> mutator -> peer -> added -> tail`。同一PHP request中に遅延callbackが実行される
- `did_action()`が1から2へ進み、native WordPress oracleが一致し、callback tableが4行、hook state reset、解放後live handle数が0
- `run.exit_codes`の9シナリオがすべて0

予備として同じartifactの`run.result.action`を表示し、WordPressの`all` callback、通常callback順、accepted args、action counterとcleanupを説明できます。`native_oracle_accepted_args_match: true`と`accepted_args_overlarge: ["left", "right"]`がnative parityの確認箇所です。

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

This is bounded execution evidence for fixed WordPress fixtures. It covers ordinary action `accepted_args=5` truncation to the two supplied arguments, reference-array callbacks, a terminal Throwable, one nested action, future-priority addition, removal of a pending callback, and same-priority addition deferred to the next action pass in one request. Accepted-argument truncation and zero-argument callbacks remain unproven for reference-array dispatch; arbitrary PHP values, deeper nesting, Throwable recovery or worker reuse, and production bootstrap integration also remain outside this demo. It does not establish full WordPress compatibility or production readiness.
