# autoloadの共有結果を使えない場合のDB読み取り

標準WordPressのホームと3件の404を、同じRustホストで新規PHP-Wasm要求として処理する。最初の404で標準autoload SELECTを実行し、次の404で共有結果を返す。最後の404の前に、所有DBの別接続で `rp_options` の固定オプションを更新する。

監視接続が同じソースとログを確認しても、観測した位置が保留中の位置より進んでいれば、共有結果を破棄する。最後の404は通常のDB経路で同じSELECTを実行する。その経路で対象SQLが成功したこと、対象応答の変更、残りのRPCと標準PHPのページ応答の一致、追加ヒットと追加格納が0件、終了時の予約が0バイトであることを検証する。

今回の条件は、所有DBの同一ログで位置が有限の範囲だけ進んだ場合に限る。照合処理の別のエラーや、要求後の再照合に失敗した応答はエラーとして扱う。通信断からの復帰と再同期は別の試験とする。[コミットを再生して失効する試験](../php-wasix-autoload-binlog-probe/README.ja.md)

## 両DBの実測

source `04b4be1` のLinux CIで、同一ログの位置はMySQLで166729から167160、MariaDBで277458から277975へ進んだ。両DBとも初回のキャッシュ対象SQLは1回、共有ヒットは1回、格納した応答は87,682バイトだった。最後の404では予約を0バイトにしてから標準SELECTをDBで直接実行した。対象応答のダイジェストだけが変わり、残りのRPCと標準PHPのページ応答は一致した。Rustテスト66件、固定入力268件と生成ソース6件、所有DBの正常終了を照合した。[MySQL証跡](evidence/ci-42517825080-mysql/provenance.json)・[MariaDB証跡](evidence/ci-42517825080-mariadb/provenance.json)

source `b3bdc7b` のLinux CIでは、監視接続が既存のnonceとともに独立した識別値を読み、3件の404それぞれで実際にSQLを実行する接続から同じ値を取得した。Rustホスト内で、識別値、サーバー版、異なる接続IDを確認してからWordPressのDB要求を受け付けた。監視用nonceの参照権限は監視接続に持たせた。両DBでWordPressから見えるDBの操作列、標準PHPとのページ応答、共有ヒットと更新後のDB直読を照合した。固定入力269件、生成ソース6件、Rustテスト66件を確認した。[MySQL証跡](evidence/ci-42532767293-mysql/provenance.json)・[MariaDB証跡](evidence/ci-42532767293-mariadb/provenance.json)
