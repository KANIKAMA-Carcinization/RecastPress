# WordPress標準autoloadキャッシュを実コミットで失効する

通常のWordPressホームを実行してDBの初回処理を済ませる。その後、所有DBの `rp_options` に表示へ使わない固定autoloadオプションを1件追加し、3つの新規404要求を実行する。最初の404は標準のautoload SELECTを実行し、2回目は同じ結果にヒットする。3回目の前に別接続が固定オプションを更新し、Rustが実binlogのコミットを観測してから再取得する。

試験では行順と列メタデータを含む結果と、標準PHPの404応答を比較する。結果を共有できるのは既存試験で照合した固定SQLとセッション状態だけ。失効の対象は `rp_options` の共有結果全体で、今回の変更検証は同じログ上の固定更新1件に限定する。格納の論理上限は128 KiBで、終了時は予約を解放する。

既存の読み取りロックを使う比較と、固定依存行でのbinlog試験を組み合わせた。[autoloadの基準試験](../php-wasix-autoload-cache-probe/README.ja.md)・[固定行のbinlog試験](../php-wasix-binlog-dependency-probe/README.ja.md)

## 両DBの実測

source `2b18812` のLinux CIでは、PHP-Wasmを1回コンパイルし、ホーム1件と404の3件を新規要求として処理した。両DBとも、計測した404のautoload結果は118行・2列だった。対象SELECTのDB実行2回、共有ヒット1回、失効1回を確認した。格納した応答は87,682バイトで、終了時の予約は0バイトだった。コミット後に変わったのは対象SELECTの応答ダイジェストだけで、残りのRPCと標準PHPのページ応答は一致した。

| DB | 同じログ上の再生範囲 | イベント数 | CRC照合数 |
| --- | --- | ---: | ---: |
| MySQL 8.4.13 | `probe-bin.000003` の166732～167163 | 7 | 6 |
| MariaDB 11.8.9 | `probe-bin.000002` の277458～277975 | 7 | 7 |

両DBで `rp_options` の変更前後の1行、コミット1件、EOF、ソースの再照合、所有DBの正常終了を確認した。Rustテスト66件、固定入力262件と生成ソース5件を照合した。監視は要求間の有限再生で、連続監視や監視喪失時の通常DB経路は別の受入とする。[MySQL証跡](evidence/ci-42515445130-mysql/provenance.json)・[MariaDB証跡](evidence/ci-42515445130-mariadb/provenance.json)
