# 固定WordPressの通常起動のnative基準

2026-11-03。固定WP 7.1.3を使い、独立した通常PHP比較hostで`wp-settings.php`全体を通す準備を行う。元の27ケース・追加15ケースの固定API比較と、通常WP起動を別の到達点として扱う。Rust接続・Wasm・HTTP listener・製品runtimeの成功をこの基準で代用しない。

元archiveの全core・theme・pluginを新しいignored siteへbyte一致で配置する。`wp-config.php`や資格情報ファイルは配置せず、入口がstdinから受けた一時設定を定数にして元`wp-settings.php`を実行する。`SHORTINIT`は使わない。

新しい所有DBで、schema内DDL＋CRUDだけを与えた専用installer accountから元`wp_install()`を実行する。その後、同じDBへ別のPHP process・通常CRUD accountで通常起動する。管理用資格情報はPHPへ渡さない。coreの公開option・default posts・既知12テーブルと、autoload optionの件数／最大値長を観測する。password hash・salt・user row・全option値・SQL traceは公開しない。

この比較fixtureではcron・外部HTTP・mail・更新・hard rewrite flushを事前に遮断する。原plugin APIのhookをbootstrap前に登録し、さらにCLIでmailとURL fopenを無効にする。これらは実送信やsource変更を起こさずに起動を比較するための明示的な条件であり、製品の機能廃止ではない。

```powershell
python experiments/wp-bootstrap-probe/prepare.py
python experiments/wp-bootstrap-probe/database_check.py --backend mysql --record
python experiments/wp-bootstrap-probe/database_check.py --backend mariadb --record
```

各実行はDBの版・datadir・PID・portを確認し、別admin接続から最終table・option・管理user件数を照合して正常停止と全所有child回収を確認する。従来の所有DB helperを再利用するため、空の`rp_wire_fixture`も同じfresh schemaに存在するが、WPの12テーブルや処理へ含めない。

## 実測結果

[MySQL 8.4.13](evidence/windows-mysql-native.json)と[MariaDB 11.8.9](evidence/windows-mariadb-native.json)で、元`wp_install()`と、その後の別PHP processによる通常起動が通過した。両段階で`init`・`wp_loaded`と通常class群のロード、`SHORTINIT=false`、既知12テーブル、公開option・default posts、物理切断を確認した。警告は0、install/read間の公開観測も一致した。別admin接続からtable全体・保存されたsite URL等・管理user件数を照合し、DB正常停止・全child回収まで通過した。

元source 3,782ファイル／467ディレクトリ／110,077,999 bytesは、各PHP段階とcontrollerで元archiveに再照合した。install時には標準mail 1回・HTTP 2回を事前hookで遮断し、通常readでは両方0回だった。CLIのmail無効・URL fopen無効も入口で検査する。全ソース準備、lint3件・接続前拒否10件は[非接続チェック](evidence/windows-nonconnecting-check.json)に保存した。

| autoload対象の集計 | 両DBの件数 | 最大option値 |
| --- | ---: | ---: |
| install後の観測 | 100 | 3,133 bytes |
| 通常起動後の観測 | 115 | 23,778 bytes |

この集計は通常WPを現在の小さなWasm RPC profile（16行・4KiB/cell）へそのまま接続できない根拠となる。件数変化の原因や書込みの経路は、この集計だけで断定しない。[実装所見](findings.ja.md)に次の補完点を記録する。今回の正常起動は通常PHPの比較基準であり、Rust DB接続・Wasm・HTTP要求のdispatch・theme renderの成功を意味しない。
