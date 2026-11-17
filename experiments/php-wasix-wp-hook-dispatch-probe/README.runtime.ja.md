# 実PHP-WASIX hook dispatch PoC

この専用workspaceは、固定PHP-WASIX 8.5.12とWordPress 7.1.3のsource-bound overlayを使い、実際の `apply_filters()` から選択された `WP_Hook` callback tableをRust `Dispatcher` にimportして実行する。既存の7 profile PHP compatibility bridgeやそのreceiptは変更しない。

PHPはcallable自体をtransportへ出さず、request-scoped `HandleRegistry` のdescriptorだけを渡す。Rust側は元のcallback UID、priority、insertion order、accepted args、handle identityを検証する。各PHP callback直前にWP_Hookのprivate current priorityをmethod内closureから同期し、戻り値をfilter chainへ戻す。`all` callback、filter counter、current filter stackはWordPressの元実装が担当する。

完了時はWP_Hook iterationを正常終了させ、callback tableを前後比較する。Rust dispatcherからregistration leaseを解放し、PHP registryのhandle release ACKを受け取ってからrequest registryをcloseする。fixtureはcallback順、引数、priority、`current_filter()`、`doing_filter()`、`did_filter()`、filter結果、final cleanup stateを検証する。

## 実行

```sh
python3 scripts/wordpress_reference.py fetch
# 固定Upstream Actions artifactをworkflow内で取得・照合する。
python3 experiments/php-wasix-wp-hook-dispatch-probe/check_overlay.py
python3 experiments/php-wasix-wp-hook-dispatch-probe/check.py
```

`check.py` はLinux専用で、Rust 1.94.0、Wasmer 7.2.0 Cranelift、Wasmer-WASIX 0.702.0を用いる。GitHub Actions workflowはUbuntu 24.04で固定PHP artifact ZIPと展開後Wasm双方のサイズ/hashを確認して実行する。tokenは固定された `api.github.com/repos/wasix-org/php/actions/artifacts/12371010145/zip` だけに送る。

Craneliftは既存の実PHP bridgeと同じく最適化なしで固定する。Rust hostはartifact読込、module compile、guest起動、最初のbridge frame待ちをstderrへ段階表示する。checkerはタイムアウト時にstdout/stderrそれぞれの保存上限1 MiB内の末尾データについてbyte数とSHA-256を記録し、両方を合わせたcaptureのSHA-256も証跡へ残す。これによりPHP fixture開始前の停止もCIログと保存証跡から切り分けられる。

Windows上で同じrunnerを動かすとCranelift 0.133.0のlowering panicが起きるため、Windowsのcargo checkやPHP lintを実PHP-Wasm実行の証明とは扱わない。WSLはこのworkflowの実行環境ではない。

## 範囲

対象は通常の `apply_filters()` と静的callback tableに限定する。dispatch中の登録変更、`apply_filters_ref_array()`、actions、Throwable時の復旧、CGI channel、製品採用は証明しない。このprobeの成功は対象fixtureでの実行証拠であり、WordPress全体のPHP互換性や製品ランタイム完成を意味しない。
