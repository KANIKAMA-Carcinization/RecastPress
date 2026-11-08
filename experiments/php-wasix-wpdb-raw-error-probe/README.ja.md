# 実DBのERR本文をPHP-Wasm上のwpdbまで保持する

Linux CIで、MySQL 8.4.13とMariaDB 11.8.9の実ERRを、PHP-Wasm上の標準 `wpdb` からRustのDB actorへ問い合わせる固定7 SQLで検証した。接続は所有するDBとループバック観測器に限定した。Rustはドライバーが保持したERRパケットから本文バイトを読み、private RPCでPHPに渡す。通常PHPと比較し、UTF-8本文 `c3a9`、Latin-1の非UTF-8本文 `e9`、後続ERRのコード・SQLSTATE・本文が、wireと `wpdb::last_error` でそれぞれ一致した。最後のSELECTではエラーが消え、返り値と結果も一致した。

修正版のsourceは `4ce8bee`。各DBで13 RPC、Rustテスト26件、入力72件と生成7件を照合した。DB actorの終了とRPCの読み切り、ゲストと所有DBの正常終了も確認した。[MySQL証跡](evidence/ci-42498452725-mysql/provenance.json)・[MariaDB証跡](evidence/ci-42498452725-mariadb/provenance.json)

初回CIは、生成したRustソースがない状態で `cargo fetch` を実行したため、Wasmの実行前に終了した。初回の判定を保存し、修正版ではソース生成を取得前に移した。[初回MySQL](evidence/ci-42497285887-mysql/provenance.json)・[初回MariaDB](evidence/ci-42497285887-mariadb/provenance.json)

対象は固定WordPress 7.1.3の `wpdb` と有限のエラー比較。WordPress全体の起動、実DBからのNUL入りERR、任意クエリや製品側の公開応答は別の検証範囲とする。[実DBの通常PHPとRustドライバーの基準](../wpdb-raw-error-wire-probe/README.ja.md)
