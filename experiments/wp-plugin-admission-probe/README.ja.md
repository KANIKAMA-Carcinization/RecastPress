# 固定WordPressプラグインの有効化前判定

v0.5のDB・キャッシュ専有領域に対する衝突判定を、版とバイト列を固定した3候補で確認する。候補のファイル、配置先、ハッシュを検査し、次の段階へ渡すバイト列を判定結果で制御する。

| 候補 | 判定 | 確認すること |
| --- | --- | --- |
| 標準APIを使う候補 | 所有試験での有効化へ進む | WordPressの通常フック・標準wpdb・要求内キャッシュを検証する |
| `db.php` と `object-cache.php` を配置する候補 | プラグイン全体を拒否 | 予定配置先がRP専有領域と衝突し、有効化側へファイルを渡さない |
| 設定で選ぶcallableへ `$wpdb` を渡す候補 | 保留 | callableの実装と接続への影響が未確定。確認済みの事実、未解決点、候補版・SHAをRP開発者へのローカル報告にまとめる |

```powershell
python -B experiments/wp-plugin-admission-probe/admission.py --self-test
```

判定は固定候補の配置計画と具体的な証拠を対象とする。配布アーカイブ全般の検査、未知のPHPコードの解析、本番サイトでの導入・有効化は後続工程で扱う。

## 通常PHPでの両DB基準

Windowsの所有MySQL・MariaDBで、複製したWordPress 7.1.3に標準API候補だけを配置し、`activate_plugin()` で有効化した。独立SQLで `active_plugins` の遷移を照合し、以降の要求では通常のWordPressローダーがプラグインを読み込んだ。`wp_footer` の固定マーカーは、有効化前は0件、有効化後の新規CGI要求2件では各1件だった。マーカーの出力には、wpdbの `SELECT 42` と、要求内キャッシュのmissからadd／getへの遷移を要求した。2件の応答はバイト単位で一致し、有効化前との差は固定マーカーだけだった。

拒否・保留候補は配置されず、原本のWordPressとPHP配布物は同一バイトを保った。所有DBの正常停止とCGI子4件の回収も確認した。複製ツリーの初回照合は74.448秒／84.922秒だったため、最初の既定30秒での停止を受け、試験専用PHPに120秒の上限を設定した。[MySQLの基準](evidence/windows-mysql-native.json)・[MariaDBの基準](evidence/windows-mariadb-native.json)

次は同じ候補と観測条件をPHP-Wasm／Rust DBへ接続する。通常PHPで得た基準を、pluginのWasm受入と分けて記録する。

PHP-Wasm／Rust DBでの有効化と描画も両DB CIで通過した。[PHP-Wasmの受入記録](../php-wasix-wp-plugin-admission-probe/README.ja.md)
