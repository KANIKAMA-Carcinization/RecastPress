# WordPress標準autoloadキャッシュを実コミットで失効する

通常のWordPressホームを実行してDBの初回処理を済ませる。その後、所有DBの `rp_options` に表示へ使わない固定autoloadオプションを1件追加し、3つの新規404要求を実行する。最初の404は標準のautoload SELECTを実行し、2回目は同じ結果にヒットする。3回目の前に別接続が固定オプションを更新し、Rustが実binlogのコミットを観測してから再取得する。

試験では行順と列メタデータを含む結果と、標準PHPの404応答を比較する。結果を共有できるのは既存試験で照合した固定SQLとセッション状態だけ。失効の対象は `rp_options` の共有結果全体で、今回の変更検証は同じログ上の固定更新1件に限定する。格納の論理上限は128 KiBで、終了時は予約を解放する。

既存の読み取りロックを使う比較と、固定依存行でのbinlog試験を組み合わせる。WindowsでRustテスト66件と派生ソースの再現性を確認した。Linux CIで両DBの実測を確認する。[autoloadの基準試験](../php-wasix-autoload-cache-probe/README.ja.md)・[固定行のbinlog試験](../php-wasix-binlog-dependency-probe/README.ja.md)
