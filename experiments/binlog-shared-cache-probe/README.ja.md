# 実DBのコミット観測による共有キャッシュ失効

Rustの共有キャッシュ候補を、所有するWindows上のMySQL 8.4.13とMariaDB 11.8.9で検証する。対象はInnoDBの固定1行を読むキャッシュキー1件で、値の論理予算は64バイト。DBは接続元とデータディレクトリを検証した使い捨てインスタンスを使う。MySQLは証明書と接続先名を検証したTLS、MariaDBはfixtureのループバック接続を使う。

別接続の未コミット更新が外から見えないことを確認し、ROLLBACK後に値・binlog座標・キャッシュ世代が維持されることを比較する。次の更新をCOMMITした後、同じ開始座標から有限binlogを再生する。CRC32と行の前後像を照合し、XIDを処理するまでは旧値のヒットを維持する。コミット済みバッチの適用時にエントリーと予算を解放し、旧世代で取得した値の再格納を拒否する。新世代でDBを読み直してヒットを確認する。ソースのEOF後は共有ヒットと格納を停止し、残予算はゼロになる。

この試験で確認する整合性は、単一プロセスが対象のコミットを観測した後のもの。固定1行の有限再生なので、継続監視での遅延上限、予期しない切断と再同期、任意のWordPressキーとDB行の対応付け、PHP-Wasmへの共有層の接続は別途検証する。

## 再現

既存のWindows DB fixture用のツールを用意してから、リポジトリルートで実行する。既存binlogソースとロックファイルは固定コミットから専用ディレクトリに派生し、既存の検証コードや記録を直接更新しない。

```powershell
python -B experiments/binlog-shared-cache-probe/windows_check.py --backend mysql
python -B experiments/binlog-shared-cache-probe/windows_check.py --backend mariadb
```

両DBでコミット1件・更新行1件を確認し、DBは正常終了した。実行記録は [MySQL](evidence/windows-mysql.json) と [MariaDB](evidence/windows-mariadb.json) を参照。
