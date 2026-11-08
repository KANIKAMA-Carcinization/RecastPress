# 固定プラグインのPHP-Wasm／Rust DB試験

WordPress 7.1.3の元ソースを検証し、所有 `db.php` と有効化試験を許可したプラグイン1ファイルだけを読み取り専用で重ねる。固定の衝突候補と保留候補はこの入口に渡さない。元のfixtureから生成したPHPとRustの入力は、実行前後に照合する。[有効化前判定と通常PHP基準](../wp-plugin-admission-probe/README.ja.md)

MySQL・MariaDBのそれぞれで、非有効の描画、privateな有効化、有効後の描画、反復描画を別processで実行した。有効化ではWordPress標準の `activate_plugin()` を使い、テーマ応答を生成しない。`active_plugins` は独立SQLでも確認した。描画は通常のWordPressローダーを通り、wpdbの読取りと要求内キャッシュの条件を満たした場合だけフッターマーカーを出す。

source `2f62ff7` の両DB CIでは、全ヘッダー、本文、要求コンテキストが通常PHP基準に一致した。非有効時の本文は69,399 B、有効後は69,453 Bで、差はマーカー1件だけだった。反復応答も一致した。Linux 60 unit、233入力、生成Rust／PHP 10ファイル、4子processの回収と所有DBの正常停止を確認した。記録にはprivate応答やIPCの生データを含めない。[MySQL証跡](evidence/ci-42492425833-mysql/provenance.json)・[MariaDB証跡](evidence/ci-42492425833-mariadb/provenance.json)
