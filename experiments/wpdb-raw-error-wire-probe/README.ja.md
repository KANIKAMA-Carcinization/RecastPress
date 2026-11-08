# 実MySQL／MariaDBのERRバイト比較

所有DBに対し、UTF-8とLatin-1の `SIGNAL`、別のERR、回復用 `SELECT 42` を順に実行する。ローカルの透過観測器は指定した応答のERRコード、SQLSTATE、本文バイト列だけを記録する。接続はこの比較用にTLSと圧縮を外し、認証データは保存しない。

通常PHPでは、MySQL 8.4.13とMariaDB 11.8.9の両方で、UTF-8時は `c3a9`、Latin-1時は非UTF-8の `e9` が実際のERR本文に現れた。ワイヤー・`mysqli_error()`・標準wpdbの `last_error` が一致し、別ERRへの置換と `SELECT 42` 後の消去も確認した。両DBは正常停止・回収済み。NULを含むERRの扱いは、実サーバーのこの比較とは別に検証する。[MySQL証跡](evidence/windows-mysql-native.json)・[MariaDB証跡](evidence/windows-mariadb-native.json)

`Conn::last_error_packet()` を使うRust試験でも、両DBで3件のcode・SQLSTATE・ERR本文が実ワイヤーおよび通常PHP基準と一致した。従来のStringでは `e9` の元バイトを保てないこと、次のERRで保持内容が置き換わり、`SELECT 42` で消えることも確認した。接続と所有DBを正常終了した。[MySQLのRust証跡](evidence/windows-mysql-driver.json)・[MariaDBのRust証跡](evidence/windows-mariadb-driver.json)

初回MariaDBでは、ドライバーがメタデータ保持をネゴシエートした際の回復SELECT先頭が `01 01` だった。観測器の固定 `01` 検査がこれを拒否していたため、両側が機能を交渉した場合にだけ `01 01` を受け入れた。ドライバーが実際に値42を読む検査はそのまま通した。[初回失敗](evidence/windows-mariadb-driver-first.json)・[切り分け時の記録](evidence/windows-mariadb-driver-diagnostic.json)

Rust実行には、既存のraw accessor実験で準備したドライバーを使う。同じ比較はPHP-Wasm内の所有アダプターとRust DB actorでも両DBで通過した。[ドライバーの準備手順](../wpdb-raw-error-probe/README.ja.md)・[PHP-Wasmの実測](../php-wasix-wpdb-raw-error-probe/README.ja.md)

```powershell
cargo build --locked --offline --manifest-path experiments/wpdb-raw-error-wire-probe/Cargo.toml --target-dir target/wpdb-raw-error-probe/cargo-target
$digest = (Get-FileHash -Algorithm SHA256 target/wpdb-raw-error-probe/cargo-target/debug/recastpress-wpdb-raw-error-wire-probe.exe).Hash.ToLowerInvariant()
python -B experiments/wpdb-raw-error-wire-probe/driver_check.py --backend mysql --sha256 $digest
```
