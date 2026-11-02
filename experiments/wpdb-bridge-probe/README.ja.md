# 元wpdb → PHP adapter → Rust → 実DBの小さな結合試験

2026-11-02。v0.5の要求専有DB境界を、前回の[独立driver／元wpdb基準](../wpdb-rpc-probe/README.ja.md)から結合する。既存仕様・前回のsource／lock／生記録は変更しない。新しい独立workspaceと新しいfixtureだけを追加する。

## 目的と境界

元WP 7.1.3のprepare/get_var/get_row/get_col/get_results・prefixをPHP側に残し、driverに依存する接続・query・escape・列名取得・closeを小さなsubclassで置き換える。Rustが要求ごとに一つの物理DB接続を所有する。mysqli handle/resultを模倣してpluginへ公開せず、再接続・pool・結果件数補正・全SQLのprepared化を入れない。

この試験のPHPは固定Windows PHP 8.5.12の比較用hostであり、製品のnative PHP fallbackではない。**php-wasmとの結合は後続**である。Rustが固定PHP childを起動し、専用loopback socketと要求nonceでRPCを渡す。PHPへDB資格情報は渡さず、stdoutはAPI観測結果だけに使う。製品のnetwork機能・公開ABIとしてこの比較用transportを採用しない。

## 元の27ケースの保存

[corpus.php](fixtures/corpus.php)は前回のnative fixtureから、prefix設定〜最終num_queriesまでを抽出した。driver固有のmysqli_errno／mysqli_sqlstate取得の2行だけを、実driver error snapshotを返すcallback一つへ固定置換した。それ以外のSQL、API呼出し、期待値をbyte一致で照合する。[build_check.py](build_check.py)は元fixture全体のSHAとこの変換を毎回確認する。

[native.php](fixtures/native.php)と[bridge.php](fixtures/bridge.php)は同じ本文を使う。比較は27観測の全field、戻り値のPHP型、bytes／NULL、API名・SQL・公開状態・num_queries、prepare結果・prefix・能力照会・server version・SQL error identityを対象とする。前回の固定native記録とも比較し、新しいnative基準だけへ期待値を置換しない。

接続時の文字コード・SQL modeは元directmysqliと同様に、公開query counterの外で実際に設定する。WordPressのincompatible_sql_modes filterはPHP側に残す。実sessionのcharset・collation・modeを両経路で照会し、helloで宣言したprofile値だけを成立の根拠にしない。

## 有限のRPCと所有権

`hello/query/escape/close`だけを受け付け、nonce、連番、exact keys、長さ、hex形式を検査する。要求16KiB、応答64KiB、総要求128KiB、総応答512KiB、64操作、16行・16列、cell 4096B、PHP stdout/stderr各65KiBを上限とする。SQL・列名・値をhexで渡し、数値列もSQL NULL以外は文字列bytesとして保持する。

Rustの実行予算60秒、接続／query 10秒、accept 10秒に加え、失敗後のdisconnect・PHP reap・capture joinには別の有限清掃期限を設ける。OS全体のhard wall-time保証とはしない。正常系はphysical disconnect、PHP exit/reap、stdout/stderr EOF、worker joinを確認する。失敗を成功へ書き換えず、外側harnessも捕捉したprocessだけを終了・reapする。

[owned_database.py](owned_database.py)は前回の固定tool照合・署名検証・process helperを再利用し、新しいUUIDのdatadir・loopback port・管理／制限userのsecret・CAを用意する。管理接続はCAと接続名を検証するTLS、比較対象のDB接続は非TLS。所有PID・datadir・port・固定版を確認してからfixtureを作る。秘密・nonce・binary・設定・ログはGitへ含めない。

## この段階で意図的に限定する範囲

queryとescapeはASCII、sessionはutf8mb4／utf8mb4_unicode_520_ciに限る。escapeはdriver既存のSQL literal生成と実last-OKのNO_BACKSLASH_ESCAPES状態を使い、placeholder escapeは元wpdbに残す。非ASCII入力を別表現へ変換して互換差を隠さない。列情報はget_col_info('name')だけを対象とし、mysqli max_length等は未対応として拒否する。errorは固定ASCIIの1064/42000だけを対象とし、raw非UTF-8 ERRの問題は継続する。

通常WP bootstrap、実テーマ・plugin、DDL権限、全wpdb API、再接続・commit結果不明、全障害下の回収、native TLS、php-wasm、永続cache、製品driver／公開ABIはまだ受入対象ではない。

## 再実行の順序

固定toolとdriver sourceは前回の準備済みcacheを使う。追加のdownloadや既存DBへの接続は行わない。

```powershell
python experiments/wpdb-bridge-probe/build_check.py --record
python experiments/wpdb-bridge-probe/database_check.py --backend mysql --variant native --record
python experiments/wpdb-bridge-probe/database_check.py --backend mysql --variant bridge --record
python experiments/wpdb-bridge-probe/database_check.py --backend mariadb --variant native --record
python experiments/wpdb-bridge-probe/database_check.py --backend mariadb --variant bridge --record
```

WindowsのMariaDB管理CLIは前回sandbox内のSchannelで失敗したため、同じ固定試験をsandbox外で実行する必要がある。TLS検証や期待値を緩めない。

## 実測結果

[最終build記録](evidence/windows-build.json)では、固定driver全sourceとPHP配布物78ファイル／元WP5ファイルを確認し、locked・offlineで通常binaryとtest binaryを生成した。入力exact keys・unknown/duplicate field・nonce/連番/hex/frame境界・通常PHP pathとcanonical fixtureの同一性という5 unitを実列挙して実行し、5 passed／0 failed／0 ignoredと所有childのreapを確認した。実行するPython moduleとPHP fixtureも含め23入力のSHAを固定した。

| DB | 共通native基準 | PHP → Rust → 実DB |
| --- | --- | --- |
| MySQL 8.4.13 | [27ケース通過](evidence/windows-mysql-native.json) | [27ケース・全観測項目一致](evidence/windows-mysql-bridge.json) |
| MariaDB 11.8.9 | [27ケース通過](evidence/windows-mariadb-native.json) | [27ケース・全観測項目一致](evidence/windows-mariadb-bridge.json) |

両経路のbootstrap num_queries=0、corpus内の実query=25、最終num_queries=25が一致した。27 API観測のうちcached get_colと列名照会はqueryを増加させない。各結果の型・値・SQL・公開状態・prepare／prefix／能力／server version／error identityを、前回の固定native記録と今回のfresh native記録の双方へ照合した。実sessionのcharset・collation・SQL modeもfresh比較で一致した。

Rust経由は両DBとも34 RPCで、hello 1、query 29（接続設定・実session照会4＋公開query 25）、escape 3、close 1だった。要求nonce・連番を含む実request frameのlen/SHAと、実replyのlen/SHAを外側harnessで再計算した。PHP側の送受信量・操作数、全queryの送信順とSQL bytes、setup応答、close応答も照合した。nonce自体は公開記録に保存しない。

全4profileで、独立した管理接続による最終DB内容、正常管理shutdown・server exit 0・全command childのreapを確認した。bridgeではさらにRust所有のPHP exit 0・reap、両streamのEOF、capture worker join、物理disconnect成功を確認した。資格情報とnonceの公開記録への混入も拒否する。

初回は[MySQL](evidence/windows-mysql-verbatim-include-failure.json)・[MariaDB](evidence/windows-mariadb-verbatim-include-failure.json)ともPHPのinclude集合検査で0 RPC・0 APIのまま失敗した。Rustのcanonical pathがWindowsのverbatim表記になり、PHP CLIのscript引数でrealpath／include集合が一致しなかった。同じloaderを通常表記／verbatim表記で起動するDB不要の診断で差を再現し、PHPへ渡すscript引数だけを通常の絶対pathへ変更した。そのpathが既に検証したcanonical fixtureと一致することをspawn前に再検証し、include集合の条件は維持した。初回buildとnative基準、unit名を照合するhelperの一度の失敗も別記録に残し、最終sourceへ生記録を付け替えない。

今回成立したのは、通常PHP上での限定wpdb adapterとRustの実DB結合である。php-wasm側のprivate FS transportへの接続はまだ行っていない。ASCII制限・列metadataの限定・raw ERR・full WP・製品ABI・製品採用については、前節の未受入範囲を維持する。
