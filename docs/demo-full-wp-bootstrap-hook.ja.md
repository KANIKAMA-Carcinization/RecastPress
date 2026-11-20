# Full WordPress bootstrap + PHP-Wasm hook dispatch demo

## このデモで確認すること

固定したWordPress 7.1.3のcore・theme・pluginを変更せず、PHP-WASIX CGIで通常の`wp-settings.php` bootstrapを通します。専用のDB drop-inからRust DB actorへ接続し、installとreadをMySQL／MariaDBで実行します。

bootstrap完了後の公開観測で`get_option('blogname')`を1回呼び、WordPress本体の`plugin.php::apply_filters()`経路から選定フィルター`option_blogname`を実行します。priority 10と20、`accepted_args=1`の2 callbackをRust `recastpress_wp::Dispatcher`が順番に要求し、guest PHPがhandle registry経由で呼び出します。結果は`RP Bootstrap Fixture|first|second`です。DB RPCとは独立した`/rp-wp-hooks/channel`を使います。

`did_filter()`の事前カウント、各callback内のcurrent filter・priority・doing state、終了後のglobal filter stackを観測します。別プロセスのnative oracleは、同じ固定WordPressソースの実`WP_Hook`で直接`apply_filters()`した結果と比較します。install/readの両方で比較し、baselineを差し引いたdispatch回数とcleanupを確認します。

## 意図した範囲

WP 7.1.3の`WP_Hook`はfinal classです。全core sourceを保つため、bootstrap内の選定1 hookには2 callbackだけを持つ専用オブジェクトを置き、WordPress本体のpublic `apply_filters()` wrapperから呼び出します。このPoCは`WP_Hook::apply_filters()`の実装互換を証明しません。native oracleがWP_Hook側の期待値を示し、Wasm側はpublic wrapper・固定guest adapter・Rust Dispatcher間の限定した経路を検証します。

callbackはこのfixture専用です。すべてのWordPress hook、一般plugin、製品runtime、全wpdb APIへの互換性は主張しません。エラー時は要求を失敗させ、未解放handleや未close channelを成功として記録しません。例外後のrequest再利用・回復は未検証です。

## 実行と確認

GitHub Actionsの`PHP WASIX full WordPress bootstrap` workflowは、`dev/v0.6`への対象source pushでMySQLとMariaDBのjobを実行します。成功後はworkflow artifactの`linux-mysql.json`／`linux-mariadb.json`で、`hook_dispatch`、`native_hook_oracle_match`、`hook_rpc`を確認します。PHP lint、native oracle、Wasm install/read、DB最終状態の確認が全て通ることをデモ受入条件にします。

### 最新の成功run

[run 44649976739](https://github.com/KANIKAMA-Carcinization/ReCastPress/actions/runs/44649976739)はsource commit `0b0d68d032adff6dc2b80d1254e04a323c18b83a`で実行し、MySQL job `136002149219`とMariaDB job `136002149544`が両方successでした。各artifactの`linux-<backend>.json`ではinstall/readの両方で固定WordPress 7.1.3のnative `WP_Hook` oracleと一致し、callback順は`rp_boot_hook_first -> rp_boot_hook_second`、結果は`RP Bootstrap Fixture|first|second`です。native/Wasm traceとreset stateが一致し、hook RPCはclose済み、failureなし、live handles・pending bytes・unread response bytesがすべて0でした。

| backend | artifact | SHA-256 | 取得期限 |
| --- | --- | --- | --- |
| MySQL | [14590399453](https://github.com/KANIKAMA-Carcinization/ReCastPress/actions/runs/44649976739/artifacts/14590399453) | `7c5bb54e1caf25345819c835c202af047b482abe7544d18d594b6883e80ba98e` | 2026-12-20 01:17 UTC |
| MariaDB | [14591199141](https://github.com/KANIKAMA-Carcinization/ReCastPress/actions/runs/44649976739/artifacts/14591199141) | `ec4dff7acba1eae64364aea6ee45dadec6e5f22ec1d4c3a438570ee54ed8b4ea` | 2026-12-20 01:17 UTC |

source-bound import receiptは[MySQL](../experiments/php-wasix-wp-bootstrap-probe/evidence/ci-44649976739-mysql/provenance.json)と[MariaDB](../experiments/php-wasix-wp-bootstrap-probe/evidence/ci-44649976739-mariadb/provenance.json)にあります。offline importerはartifact digest、Windows/Linuxのsource inventory、fixture derivation、native/Wasm比較を検証します。importer自身はPHP・Wasm・DBを再実行せず、CIの非公開SQL等のbytesも再構成しません。
