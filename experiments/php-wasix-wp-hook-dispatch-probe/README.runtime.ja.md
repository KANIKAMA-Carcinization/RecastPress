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

`check.py` はLinux専用で、Rust 1.94.0、Wasmer 7.2.0 Cranelift、Wasmer-WASIX 0.702.0を用いる。GitHub Actions workflowはUbuntu 24.04で固定PHP artifact ZIPと展開後Wasm双方のサイズ/hashを確認して実行する。tokenは固定された `api.github.com/repos/wasix-org/php/actions/artifacts/12371010145/zip` だけに送る。

Craneliftは既存の実PHP bridgeと同じく最適化なしで固定する。Rust hostはartifact読込、module compile、guest起動、最初のbridge frame待ちをstderrへ段階表示する。checkerはfilterとactionを別々に実行し、各runnerの上限を300秒とする。タイムアウト時にstdout/stderrそれぞれの保存上限1 MiB内の末尾データについてbyte数とSHA-256を記録し、両方を合わせたcaptureのSHA-256も証跡へ残す。

Windows上で同じrunnerを動かすとCranelift 0.133.0のlowering panicが起きるため、Windowsのcargo checkやPHP lintを実PHP-Wasm実行の証明とは扱わない。WSLはこのworkflowの実行環境ではない。

## 参照配列FilterのWordPress oracle

`check.py` はPHP-WASIX runの前に、固定WordPress 7.1.3のnative `apply_filters_ref_array()` fixtureを実行し、参照されたfilter値と追加引数を二つのcallbackが順に変更する挙動、次callbackへの引継ぎ、呼出し元scalarへの反映、`all`の引数、filter stack cleanupをsource hash付きで証跡JSONへ記録する。このoracleはRust dispatcherを通らず、PHP-WASIXでの参照配列dispatchやproduction integrationの検証ではない。参照cellを保つWasm bridgeは別の未完了単位である。

## 範囲

PHP-WASIXからRustへ渡す対象は通常の `apply_filters()` と `do_action()`、および静的callback tableに限定する。Actionの`all` hookはWordPress内で実行し、Rustへは渡さない。dispatch中の登録変更、nested action、`do_action_ref_array()`、`apply_filters_ref_array()` のWasm bridge、Throwable時の復旧、CGI channel、製品採用は証明しない。native `apply_filters_ref_array()` oracleの成功も、その固定fixtureのWordPress参照意味を記録するだけである。このprobeの成功は対象fixtureでの実行証拠であり、WordPress全体のPHP互換性や製品ランタイム完成を意味しない。

## 実行済み証跡

2026-11-17の[実PHP-WASIX workflow run](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44093716241)は両シナリオ成功。source-bound overlayとPHP lint、3,782個の未変更WordPress source file、filter結果、actionの`all`引数・callback順・終了時cleanupを検証した。固定結果と取得用artifactのID・digestは[証跡JSON](evidence/real-php-wasix-action-run-44093716241.json)に記録した。

2026-11-18の[run](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44250333958)は固定WordPress 7.1.3上のnative `apply_filters_ref_array()` oracleと、既存の実PHP-WASIX filter/action両シナリオに成功した。oracleでは参照された追加引数が2つのcallback間で更新され、filter値のcallback returnが次callbackへ渡り、最終値がcaller側に反映された。`all`の初期引数、`did_filter`、stack cleanupもfixture期待値と一致した。run全体のsource inputは不変。各観測・WordPress source hashとartifact digestは[oracle証跡](evidence/wp-ref-array-oracle-44250333958.json)に記録した。これはnative PHP oracleであり、ref-arrayをRust/PHP-Wasm bridgeへ通した証拠ではない。
