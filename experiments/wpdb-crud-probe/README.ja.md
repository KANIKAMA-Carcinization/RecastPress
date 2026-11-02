# 標準wpdb更新ヘルパーの追加比較

2026-11-03。

元の27ケースとWasm実DBの成功記録を保持したまま、`insert/replace/update/delete`を上流PHPのまま呼ぶ追加corpusを用意する。生成SQLだけでなく、schema照会・query数・公開状態・戻り値を通常PHPとRust接続で比較する。

通常PHPは比較hostだけであり、製品のfallbackではない。今回の固定profileはASCIIの有効な入力、列名metadata、非TLS接続に限定する。非ASCII SQL・過長／不正文字列・raw ERR・通常WP起動・製品採用は別の確認とする。

新しいUUIDのMySQL 8.4.13／MariaDB 11.8.9だけを所有し、版・port・datadir・PIDfile・TLS付きadmin接続を確認してから固定tableを作る。native／bridgeともfresh DBを使う。旧実験のsource・lock・stage・証跡は書き換えない。

```powershell
python experiments/wpdb-crud-probe/build_check.py --record
python experiments/wpdb-crud-probe/database_check.py --backend mysql --variant native --record
python experiments/wpdb-crud-probe/database_check.py --backend mysql --variant bridge --record
python experiments/wpdb-crud-probe/database_check.py --backend mariadb --variant native --record
python experiments/wpdb-crud-probe/database_check.py --backend mariadb --variant bridge --record
```

実行結果は新しい`evidence/`へ保存する。失敗時の生観測とcleanup結果も保持し、全比較・所有processの正常回収が成立するまで成功にしない。

## 実測結果

MySQL 8.4.13／MariaDB 11.8.9の両方で、freshな通常PHP基準とRust接続の15ケースが通過した。API引数のPHP型（floatは8BのIEEE754 little endian）、戻り値、公開状態、各操作のquery差分、全SQL trace、sessionを同じDB種の基準と厳密比較した。`insert/update/delete/replace`の処理は元wpdbのままであり、元adapterのbusinessとtransportもそのまま読み込んでいる。

| 観測 | 両DBの結果 |
| --- | --- |
| 公開API | 15ケース、16 SQL。最初のINSERTは親の`SHOW FULL COLUMNS`を含む2 SQL |
| Rust RPC | 40件 = hello 1＋query 20（session準備4＋公開16）＋escape 18＋close 1 |
| 更新 | 同値UPDATE 0件、変更UPDATE 1件、既存行REPLACE 2件、NULL／空値・default／不足formatの動作が一致 |
| 最終確認 | helperで残行を削除し、別admin接続でもCOUNT 0 |
| 清掃 | 物理DB切断、PHPの正常終了と両stream EOF、capture worker終了、DB正常停止、全所有child回収 |

基準と比較結果は[MySQL native](evidence/windows-mysql-native.json)・[MySQL bridge](evidence/windows-mysql-bridge.json)・[MariaDB native](evidence/windows-mariadb-native.json)・[MariaDB bridge](evidence/windows-mariadb-bridge.json)。[build記録](evidence/windows-build.json)ではlocked/offlineの5 unitと通常binary生成、47入力・元driver stageの前後照合を確認した。元Rust hostからの変更は[固定変換](derivation_gate.py)で検査し、DB・TCP・session・query・escape・期限・清掃の処理を保持する。

初回の通常PHP実行は、最終build helperとの入力不一致をpreflightで拒否した。DB未起動の[失敗記録](evidence/windows-mysql-native-initial-build-binding-failure.json)と、更新前のbuild記録も残した。

この追加corpusをphp-wasmへ接続すること、非ASCII・過長・不正byte、診断が発生する入力、name以外のmetadata、raw ERR、通常WP bootstrapは残件である。NULL条件だけでplaceholderがなくなる呼出しの上流診断も今回の有効入力profileへ含めていない。元27ケースのWasm成功を、この15ケースのWasm成功として流用しない。
