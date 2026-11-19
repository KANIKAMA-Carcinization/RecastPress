# RecastPress 実PHP-Wasm hook PoC デモ手順

## デモ本番用の固定成功run

デモで使う固定source-bound runは[44580552353](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44580552353)、source commitは`2df8f4e57e84e34e59c16413a54c724da637adde`です。更新後のruntime READMEを含む全source inputsが不変のまま、固定PHP-WASIX 8.5.12／WordPress 7.1.3で10シナリオすべて成功しました。`README.runtime.ja.md`の検証hashは`0d6c1a13016f21acc1e473d20f837d5bd116b1c821c0023666ad2a7a826f9a91`です。artifact [14560842590](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44580552353/artifacts/14560842590)、SHA-256 `bbd1f368c04e7b4c260e635fb8155d929ac9938dd93ca3d5f49f5cf9a2ee43e7`、期限 2026-12-03 18:14 UTC。

今回の主デモはrun 44580552353の`action-remove-all`です。1回目は`all -> observer -> mutator -> cleared -> reregistered -> peer`、2回目は`all -> readded`。旧hookのactive priority-5 peerが続行し、別の新しい`WP_Hook`へ登録したpriority-7 callbackは2回目だけ動きます。`did_action()`の1→2、native oracle match、終了後のresetとlive handle 0をartifactと[source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-remove-all-reregister-run-44580552353.json)で示せます。

同一PHPリクエスト中の2回`do_action()`と、優先度5で追加したcallbackが2回目に実行される挙動はrun 44429302930で検証済みです。過去の固定成功runは[44354882884](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44354882884)と[source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-deferred-add-run-44354882884.json)に残しています。

このPoCはGitHub ActionsのUbuntu runner上で動作する検証デモです。WindowsやMac上で`check.py`を直接実行する手順ではありません。既存のMac Dockerデモは別のデモ経路です。

## デモ経路

このPoCの実行環境はUbuntu 24.04のGitHub Actionsです。Windowsの開発環境では`check.py`は実行できません。既存の[Mac Dockerデモ手順](local-demo-macos.ja.md)は静的ページ表示用で、このPHP-Wasm hook PoCとは別のデモです。

Pinned current-source confirmation run: [44580552353](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44580552353), source commit `2df8f4e57e84e34e59c16413a54c724da637adde`. The updated runtime README hash is `0d6c1a13016f21acc1e473d20f837d5bd116b1c821c0023666ad2a7a826f9a91`; all ten PHP-WASIX scenarios passed with unchanged source inputs. The two-dispatch `action-remove-all` observation remains equal to its earlier source-bound feature run. See the [confirmation receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-remove-all-reregister-run-44580552353.json) and [artifact 14560842590](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44580552353/artifacts/14560842590), digest `sha256:bbd1f368c04e7b4c260e635fb8155d929ac9938dd93ca3d5f49f5cf9a2ee43e7`, expires 2026-12-03 18:14 UTC.

## 成功runとartifactを開く

1. Open [successful run 44580552353](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44580552353) and confirm the workflow and `php-wasix` job succeeded.
2. Download [artifact 14560842590](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44580552353/artifacts/14560842590), named `v06-wp-hook-dispatch-44580552353-1`.
3. In `latest.json`, show all ten exit codes and `run.result.action-remove-all`. Highlight the first-pass and second-pass event order, `did_action()` 1→2, the new `WP_Hook` identity, native oracle match, final hook state, and zero live handles. The artifact ZIP SHA-256 is `bbd1f368c04e7b4c260e635fb8155d929ac9938dd93ca3d5f49f5cf9a2ee43e7`.

このworkflowは現在default branchの`main`にはなく、`workflow_dispatch`によるActions画面からの手動起動はできません。`dev/v0.6`上の対象ファイル変更を含むpushで自動実行されます。runのartifact名は`v06-wp-hook-dispatch-<run-id>-<run-attempt>`です。

## 最新実行の扱い

run [44425880569](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44425880569)はcheckerのJSON参照階層誤りで失敗しました。実PHP-WASM runner自体は9シナリオすべてexit 0で、`accepted_args=5`のcallbackは`["left", "right"]`を受け取っていました。比較箇所を直した[run 44429302930](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44429302930)は全シナリオ成功し、native oracle一致とsource inputs不変を確認しました。原因と最初のartifact digestは[診断receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-overlarge-accepted-args-run-44425880569.json)、修正版の証跡は[成功receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-overlarge-accepted-args-run-44429302930.json)に記録しています。デモではrun `44580552353` artifact `14560842590`を使います。

The first combined ref-array add/remove run [44503715461](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44503715461) passed fixture syntax and the overlay but stopped in `action-ref-array`: PHP received an unhandled `release_php` frame after the pending priority-8 callback was removed. The driver now ACKs dispatch-time callable releases once. Code run [44506652301](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44506652301) passed all nine scenarios. The repository revision at source commit `4a2020c278d40bbbb12cdef2763b49e33cc04b25` includes the documentation and evidence committed at that point; [run 44541723826](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44541723826) tested the implementation at that revision, with all nine scenarios passing. The initial failure is retained in [diagnostic receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-ref-array-add-remove-diagnostic-run-44503715461.json).

## 発表時に見せる観測

デモではartifactの`run.result.action-remove-all`を中心に、旧・新hook objectと2回のdispatchを示します。

- 1回目のイベント順は`all -> observer -> mutator -> cleared -> reregistered -> peer`。`remove_all_filters()`で旧hookは空になりますが、開始済みのpriority-5 bucket peerは続行します。
- 登録し直したpriority-7 callbackは別の`WP_Hook`に属するため1回目には走らず、2回目は`all -> readded`だけが実行されます。
- `did_action()`は1から2へ進み、旧・新hookのstateとstackは終了後にreset。native oracleと一致し、registry close時のlive handlesは0です。
- 10シナリオすべての`run.exit_codes`が0であることを確認します。

副デモとして同じartifactの`action-ref-array`を開き、reference cellを通した追加・削除後のcallback順と値伝播を示せます。
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
