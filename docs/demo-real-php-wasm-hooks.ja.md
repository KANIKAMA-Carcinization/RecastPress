# RecastPress 実PHP-Wasm hook PoC デモ手順

## デモ本番用の固定成功run

デモ用の最新runは[44569371084](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44569371084)、対象commitは`0ad84fbe9eca54e14c08a00d4fef7db44eec86b1`です。固定PHP-WASIX 8.5.12とWordPress 7.1.3で10シナリオがすべて成功し、source inputs不変を確認しました。artifactは[14555361955](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44569371084/artifacts/14555361955)、SHA-256は`6f488ab9689de9aef421e0671beb533f7c85218a9cda5ffa8c167cfb0a07bd40`で、2026-12-03 17:07 UTCまで取得できます。

今回の主デモはrun 44569371084の`action-remove-all`です。priority 5 callbackが`remove_all_filters()`を実行し、旧hookのactive bucket peerが続行した後、新しい`WP_Hook`へpriority 7 callbackを登録します。1回目は`all -> observer -> mutator -> cleared -> reregistered -> peer`、2回目は`all -> readded`。旧・新hook object、`did_action()`の1→2、state reset、native oracle match、live handle 0を`latest.json`とreceiptで示せます。`action-ref-array`のadd/removeと参照セル伝播も同じartifactの別シナリオとして利用できます。

同一PHPリクエスト中の2回`do_action()`と、優先度5で追加したcallbackが2回目に実行される挙動はrun 44429302930で検証済みです。過去の固定成功runは[44354882884](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44354882884)と[source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-deferred-add-run-44354882884.json)に残しています。

このPoCはGitHub ActionsのUbuntu runner上で動作する検証デモです。WindowsやMac上で`check.py`を直接実行する手順ではありません。既存のMac Dockerデモは別のデモ経路です。

## デモ経路

このPoCの実行環境はUbuntu 24.04のGitHub Actionsです。Windowsの開発環境では`check.py`は実行できません。既存の[Mac Dockerデモ手順](local-demo-macos.ja.md)は静的ページ表示用で、このPHP-Wasm hook PoCとは別のデモです。

Pinned demo validation run: [44569371084](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44569371084), source commit `0ad84fbe9eca54e14c08a00d4fef7db44eec86b1`. The pinned PHP-WASIX 8.5.12 and WordPress 7.1.3 workflow passed all ten scenarios with unchanged source inputs. In `action-remove-all`, the first dispatch removes every callback from the old hook, adds a priority-7 callback to a distinct new `WP_Hook`, and still completes the old active priority-5 peer. The second dispatch runs only `all -> readded`; `did_action()` advances from 1 to 2. Old/new object reset, final table, native oracle equality, and zero live handles are recorded in the [source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-remove-all-reregister-run-44569371084.json). The [artifact 14555361955](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44569371084/artifacts/14555361955), digest `sha256:6f488ab9689de9aef421e0671beb533f7c85218a9cda5ffa8c167cfb0a07bd40`, expires 2026-12-03 17:07 UTC.

## 成功runとartifactを開く

1. Open [successful run 44569371084](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44569371084) and confirm the workflow and `php-wasix` job succeeded.
2. Download [artifact 14555361955](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44569371084/artifacts/14555361955), named `v06-wp-hook-dispatch-44569371084-1`.
3. In `latest.json`, show all ten exit codes and `run.result.action-remove-all`. Highlight the first-pass and second-pass event order, `did_action()` 1→2, the new `WP_Hook` identity, native oracle match, final hook state, and zero live handles. The artifact ZIP SHA-256 is `6f488ab9689de9aef421e0671beb533f7c85218a9cda5ffa8c167cfb0a07bd40`.

このworkflowは現在default branchの`main`にはなく、`workflow_dispatch`によるActions画面からの手動起動はできません。`dev/v0.6`上の対象ファイル変更を含むpushで自動実行されます。runのartifact名は`v06-wp-hook-dispatch-<run-id>-<run-attempt>`です。

## 最新実行の扱い

run [44425880569](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44425880569)はcheckerのJSON参照階層誤りで失敗しました。実PHP-WASM runner自体は9シナリオすべてexit 0で、`accepted_args=5`のcallbackは`["left", "right"]`を受け取っていました。比較箇所を直した[run 44429302930](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44429302930)は全シナリオ成功し、native oracle一致とsource inputs不変を確認しました。原因と最初のartifact digestは[診断receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-overlarge-accepted-args-run-44425880569.json)、修正版の証跡は[成功receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-overlarge-accepted-args-run-44429302930.json)に記録しています。次のデモではrun `44569371084` artifact `14555361955`を使います。

The first combined ref-array add/remove run [44503715461](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44503715461) passed fixture syntax and the overlay but stopped in `action-ref-array`: PHP received an unhandled `release_php` frame after the pending priority-8 callback was removed. The driver now ACKs dispatch-time callable releases once. Code run [44506652301](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44506652301) passed all nine scenarios. The repository revision at source commit `4a2020c278d40bbbb12cdef2763b49e33cc04b25` includes the documentation and evidence committed at that point; [run 44541723826](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44541723826) tested the implementation at that revision, with all nine scenarios passing. The initial failure is retained in [diagnostic receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-ref-array-add-remove-diagnostic-run-44503715461.json).

## 発表時に見せる観測

デモではrun 44569371084のartifactを開き、`latest.json`の`run.result.action-remove-all`を中心に旧・新hook objectと2回のdispatchを示します。

- 優先度5のcallbackがdispatch中に優先度7のcallbackを追加し、唯一の未実行優先度8 callbackを削除する
- 観測順は`all -> truncated -> zero -> first -> added -> second`。削除したcallbackは呼ばれず、native WordPress oracleと一致する
- 優先度7 callbackが`value-mutated-by-first` / `extra-mutated-by-first`を受け取り、両方を変更する。優先度10 callbackがその変更を受け取る
- priority 2の`accepted_args=0` callbackには引数が渡らず、action resultは`null`
- action終了後にhook stateがresetされ、argument frameが解放済みでlive handle数は0
- 9シナリオすべての`run.exit_codes`が0

予備として同じartifactの`run.result.action-deferred-add`を表示し、同一request内の2回目の`do_action()`で同一優先度の追加callbackが動く挙動と、`did_action()`の1から2への増加を示せます。

## Throwableシナリオ

`latest.json`の`run.result.action-throwable`では、`RuntimeException`の同一PHP objectがRust境界を往復してcallback位置で再throwされ、外側のrequest境界で捕捉されたことを示します。`native_oracle_contract_match`は`true`、`same_throwable`は`true`、callback lease解放数は2、registry close前後のlive handle数は0です。throwでWPのdispatch loopとcleanupが中断されるため、後続callbackは実行されず、`current_action`/`current_filter`と`doing_action`、`$wp_current_filter`、WP_Hookのnesting/iteration/current-priority stateが残ります。これは例外を握りつぶす回復や同一workerの再利用を示すものではありません。

## 入れ子actionシナリオ

`latest.json`の`run.result.action-nested`では、`outer_parent_enter`が内側の`do_action()`を呼び、`inner_callback`完了後に親callbackが再開し、`outer_tail`へ進む順序を見せます。`native_oracle_contract_match`は`true`で、完全なイベント・hook stack・priority・WordPress内部stateを固定WordPress 7.1.3のnative oracleと照合しています。外側・内側のaction counterは各1、子action終了後は親stackとpriority 5へ戻り、最終stackは空、registry close時のlive handle数は0です。これは1段の入れ子だけを示し、さらに深い再入れやcallback tableの変更は対象外です。

## デモの範囲

This is bounded execution evidence for fixed WordPress fixtures. It covers ordinary action `accepted_args=5` truncation to the two supplied arguments, reference-array filter and action `accepted_args=1` truncation to one argument while preserving later reference mutations, a reference-array filter callback with `accepted_args=0` whose return becomes the next callback's accumulator, and a reference-array action callback with `accepted_args=0` whose declared return does not replace the observed null action result while later reference mutations still occur. Arbitrary PHP values, deeper nesting, Throwable recovery or worker reuse, and production bootstrap integration also remain outside this demo. It does not establish full WordPress compatibility or production readiness.

The reference-array truncation case passed in [real PHP-WASIX run 44459199659](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44459199659). Use its [artifact 14499947334](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44459199659/artifacts/14499947334); the source-bound [receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-ref-array-accepted-args-run-44459199659.json) records the native oracle match and observed arguments.

The corresponding `do_action_ref_array()` truncation case passed in [real PHP-WASIX run 44466526546](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44466526546). Its [artifact 14503086916](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44466526546/artifacts/14503086916) and [source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-ref-array-accepted-args-run-44466526546.json) record the native oracle match, one received argument, and preserved reference mutations.

The `accepted_args=0` `do_action_ref_array()` case passed in [real PHP-WASIX run 44476556483](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44476556483). Its [artifact 14508021951](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44476556483/artifacts/14508021951) and [source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-ref-array-zero-accepted-args-run-44476556483.json) record the empty callback argument list, null action result, native oracle match, and preserved later reference mutations.

The `accepted_args=0` filter case passed in [real PHP-WASIX run 44473413660](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44473413660). Its [artifact 14506291461](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44473413660/artifacts/14506291461) and [source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-ref-array-zero-accepted-args-run-44473413660.json) record the empty argument list and the returned `zero-result` observed by the next callback.
