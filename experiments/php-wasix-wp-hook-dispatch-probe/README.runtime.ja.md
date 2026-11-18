# 実PHP-WASIX hook dispatch PoC

この専用workspaceは、固定PHP-WASIX 8.5.12とWordPress 7.1.3のsource-bound overlayを使い、実際の `apply_filters()` と `do_action()` から選択された `WP_Hook` callback tableをRust `Dispatcher` にimportして実行する。既存の7 profile PHP compatibility bridgeやそのreceiptは変更しない。

PHPはcallable自体をtransportへ出さず、request-scoped `HandleRegistry` のdescriptorだけを渡す。Rust側は元のcallback UID、priority、insertion order、accepted args、handle identityを検証する。各PHP callback直前にWP_Hookのprivate current priorityをmethod内closureから同期する。filterの戻り値はchainへ戻し、action callbackの戻り値は捨てる。`all` callback、`$wp_actions` counter、`$wp_current_filter` stackはWordPressの元 `plugin.php` が先に処理し、Rustには選択された名前付きhookだけを渡す。

完了時はWP_Hook iterationを正常終了させ、callback tableを前後比較する。Rust dispatcherからregistration leaseを解放し、PHP registryのhandle release ACKを受け取ってからrequest registryをcloseする。filter fixtureはcallback順、引数、priority、`current_filter()`、`doing_filter()`、`did_filter()`、filter結果を検証する。action fixtureは`all`の実行順、accepted args、`current_action()`、`doing_action()`、`did_action()`、WP_Hookのaction state、無視される戻り値と最終cleanupを検証する。引数なしactionではWordPress標準loopの空文字placeholderと`all`側の引数形も確認する。

## 実行

```sh
python3 scripts/wordpress_reference.py fetch
# 固定Upstream Actions artifactをworkflow内で取得・照合する。
python3 experiments/php-wasix-wp-hook-dispatch-probe/check_overlay.py
python3 experiments/php-wasix-wp-hook-dispatch-probe/check.py
```

`check.py` is Linux-only and uses Rust 1.94.0, Wasmer 7.2.0 Cranelift, and Wasmer-WASIX 0.702.0. GitHub Actions runs on Ubuntu 24.04, verifies the fixed PHP artifact ZIP and extracted Wasm size/hash, and sends its token only to the pinned `api.github.com/repos/wasix-org/php/actions/artifacts/12371010145/zip` endpoint.

Craneliftは既存の実PHP bridgeと同じく最適化なしで固定する。Rust hostはartifact読込、module compile、guest起動、最初のbridge frame待ちをstderrへ段階表示する。checkerは`filter`、`ref-array`、`action`、`action-ref-array`、`action-throwable`、`action-nested`、`action-mutation`の7シナリオを別々に実行し、各runnerの上限を300秒とする。タイムアウト時にstdout/stderrそれぞれの保存上限1 MiB内の末尾データについてbyte数とSHA-256を記録し、両方を合わせたcaptureのSHA-256も証跡へ残す。

Windows上で同じrunnerを動かすとCranelift 0.133.0のlowering panicが起きるため、Windowsのcargo checkやPHP lintを実PHP-Wasm実行の証明とは扱わない。WSLはこのworkflowの実行環境ではない。

## 実行中アクションへのcallback追加

`action-mutation` は優先度5のcallback内で優先度15のcallbackを追加し、WordPress/PHP hook iterationとRust Dispatcherの両方が `mutator → added → tail` の順に進むかを検証する。追加rowのhandleをフレームで交換し、active iterationを並べ替え、完了後は3 callbackのleaseを解放する。CIは固定WordPress native oracleとゲストの全観測を比較する。このfixtureは現在のactionに対する将来優先度への追加のみを対象にし、削除、現在優先度への追加、再入actionは対象外。

## 参照配列FilterのWordPress oracle

`check.py` runs the native reference-array oracle before PHP-WASIX. That oracle validates WordPress reference behavior only; later, run 44257900392 verified the bounded successful reference-array path through Rust/PHP-Wasm (see implementation status below).

## 2026-11-18 implementation status: reference-array filter bridge

The `ref-array` scenario now passes through the real PHP-WASIX driver and Rust dispatcher. Its bounded fixture keeps the PHP argument list in a request-scoped `Argument` handle, sends reference-slot descriptors for additional callback arguments, threads callback return values through the filter accumulator, and acknowledges frame release before registry close. The successful [workflow run 44257900392](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44257900392), commit `6a2d9b779a21de19c051520d97170a4cccd25b8e`, completed all `filter`, `ref-array`, and `action` scenarios with exit code 0. The ref-array result was `replacement-from-first|extra-mutated-by-second`; the caller observed `replacement-from-first` and `extra-mutated-by-second`; `native_oracle_contract_match` was true; both live-handle counts were zero and the argument frame was released before registry close. Build took 135,941 ms and the three scenario runners 376,371 ms. All source inputs were unchanged. The source-bound receipt is [`real-php-wasix-ref-array-run-44257900392.json`](evidence/real-php-wasix-ref-array-run-44257900392.json), from [artifact 14407446829](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44257900392/artifacts/14407446829).

The first Wasm run ([44255639047](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44255639047)) failed before ref-array because the regular-filter argument serializer rejected later strings. Run [44256856248](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44256856248) then passed the existing filter scenario but failed ref-array because the guest invoke identity check required a null frame handle. Commit `6a2d9b7` fixed that identity check; run 44257900392 verifies the fix. These failed runs remain useful diagnostic history and are not success evidence.

The fixture covers only the successful `apply_filters_ref_array()` path with two callbacks and a two-element list containing the filter value plus one by-reference extra. It does not cover accepted-argument truncation or zero-argument callbacks in this mode, throwable cleanup, arbitrary PHP argument graphs, or production adapter/bootstrap integration. The overlay remains fixture-only and is not product-ready.
## 範囲

The demonstrated PHP-WASIX-to-Rust paths include `apply_filters()`, `do_action()`, bounded successful `apply_filters_ref_array()` and `do_action_ref_array()` fixtures, a terminal Throwable roundtrip, one-level nested `do_action()`, and adding a future-priority callback while an action is active. Callback removal, same-priority additions, deeper nesting, CGI channel, production adoption, and general PHP compatibility remain unproven. Reference-array fixtures do not cover accepted-argument truncation or zero-argument callbacks. The Throwable case does not prove recovery or worker reuse.

## 実行済み証跡

2026-11-17の[実PHP-WASIX workflow run](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44093716241)は両シナリオ成功。source-bound overlayとPHP lint、3,782個の未変更WordPress source file、filter結果、actionの`all`引数・callback順・終了時cleanupを検証した。固定結果と取得用artifactのID・digestは[証跡JSON](evidence/real-php-wasix-action-run-44093716241.json)に記録した。

2026-11-18の[run](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44250333958)は固定WordPress 7.1.3上のnative `apply_filters_ref_array()` oracleと、既存の実PHP-WASIX filter/action両シナリオに成功した。oracleでは参照された追加引数が2つのcallback間で更新され、filter値のcallback returnが次callbackへ渡り、最終値がcaller側に反映された。`all`の初期引数、`did_filter`、stack cleanupもfixture期待値と一致した。run全体のsource inputは不変。各観測・WordPress source hashとartifact digestは[oracle証跡](evidence/wp-ref-array-oracle-44250333958.json)に記録した。これはnative PHP oracleであり、ref-arrayをRust/PHP-Wasm bridgeへ通した証拠ではない。


## Bounded `do_action_ref_array()` PHP-WASIX proof

The real PHP 8.5.12-WASIX / WordPress 7.1.3 fixture passed `do_action_ref_array()` through the Rust Dispatcher. Both callbacks received the same two reference cells; the first changed both values and the second observed those mutations and changed the extra value again. The caller observed `value-mutated-by-first` and `extra-mutated-by-second`. WordPress's `all` callback observed the call-time snapshot, action callback return values were ignored, counters and action state matched the pinned native PHP oracle, and callback plus argument handles were acknowledged and released before registry close. `native_oracle_contract_match` was true. All four scenarios (`filter`, `ref-array`, `action`, `action-ref-array`) passed with exit code 0 and all source inputs unchanged. See [workflow run 44266907529](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44266907529), [source-bound receipt](evidence/real-php-wasix-action-ref-array-run-44266907529.json), and [artifact 14411022906](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44266907529/artifacts/14411022906). Build took 154,806 ms; scenario execution took 687,275 ms.

This ref-array fixture proves only the successful two-string-reference/two-callback path. It does not prove accepted-argument truncation, zero-argument callbacks, arbitrary nested PHP values, or callback-table mutation. Nested action behavior is covered separately below and only to one level. Throwable recovery, production adapter/bootstrap integration, and general PHP compatibility remain unproven. The overlay remains fixture-only and is not product-ready.

## Bounded action Throwable roundtrip

On [workflow run 44275172417](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44275172417), fixed PHP-WASIX 8.5.12 and WordPress 7.1.3 passed all five scenarios. The additional `action-throwable` scenario passes the callback's `RuntimeException` as a retained PHP object through Rust `Completion::PhpThrowable`, then asks PHP to throw that same object at the callback call site. The outer request boundary catches it; the native WordPress oracle and Wasm observation match with `same_throwable=true`, action count 1, only `all` and `throwing` events, no later callback, and zero live handles before and after registry close. WordPress state remains at the interrupted point (`current_action`/`current_filter` set, `doing_action=true`, `$wp_current_filter` still contains the hook, nesting level 1 and current priority 5); WP's normal iteration cleanup does not run after the exception. This is terminal request-boundary handling, not exception recovery, dispatch continuation, or proof that the worker can be reused. The run took 132,647 ms to build and 487,253 ms for all five scenarios; all source inputs were unchanged. See the [source-bound receipt](evidence/real-php-wasix-action-throwable-run-44275172417.json), [workflow artifact 14414108780](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44275172417/artifacts/14414108780), digest `sha256:959ea3f9b7e53d84c19f97549c4c2ba77c5a06224ac427268e7f7b33dc871707`.

## Bounded one-level nested `do_action()` proof

On [workflow run 44313968335](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44313968335), all six real PHP-WASIX scenarios passed. In `action-nested`, the outer `do_action()` invokes a PHP callback that enters an inner `do_action()`. The same Rust Dispatcher completes the child ticket, returns to the waiting outer callback, and then invokes the outer tail callback. The complete guest observation matches the pinned WordPress 7.1.3 native oracle. Both action counters equal 1; the event order is `all`, `outer_parent_enter`, inner `all`, `inner_callback`, `outer_parent_after`, `outer_tail`; the inner hook is fully reset before the outer callback resumes; the final stack is empty; and the handle registry closes with zero live handles. The [source-bound receipt](evidence/real-php-wasix-action-nested-run-44313968335.json) records the CI evidence and the [artifact 14432982150](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44313968335/artifacts/14432982150), digest `sha256:292992f7f213133d649a70e9b183fc556157b934b8cc348afb6b6f1d01447bbe`. Build took 163,020 ms and all six scenario runners took 1,051,164 ms; all source inputs were unchanged.

This proves only one nested action level with the fixed callbacks and arguments in this fixture. The separate mutation scenario covers one future-priority addition only. Deeper nesting, callback removal, same-priority additions, arbitrary PHP values, Throwable recovery, production adapter/bootstrap integration, and general PHP compatibility remain unproven. The overlay remains fixture-only and is not product-ready.
