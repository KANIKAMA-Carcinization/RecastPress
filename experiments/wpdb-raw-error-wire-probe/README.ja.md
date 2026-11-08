# 実MySQL／MariaDBのERRバイト比較

所有DBに対し、UTF-8とLatin-1の `SIGNAL`、別のERR、回復用 `SELECT 42` を順に実行する。ローカルの透過観測器は指定した応答のERRコード、SQLSTATE、本文バイト列だけを記録する。接続はこの比較用にTLSと圧縮を外し、認証データは保存しない。

通常PHPでは、MySQL 8.4.13とMariaDB 11.8.9の両方で、UTF-8時は `c3a9`、Latin-1時は非UTF-8の `e9` が実際のERR本文に現れた。ワイヤー・`mysqli_error()`・標準wpdbの `last_error` が一致し、別ERRへの置換と `SELECT 42` 後の消去も確認した。両DBは正常停止・回収済み。NULを含むERRの扱いは、実サーバーのこの比較とは別に検証する。[MySQL証跡](evidence/windows-mysql-native.json)・[MariaDB証跡](evidence/windows-mariadb-native.json)
