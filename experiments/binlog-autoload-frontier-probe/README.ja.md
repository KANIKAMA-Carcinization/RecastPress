# ライブ binlog 監視位置と autoload の読み取り

使い捨ての Windows MySQL／MariaDB に、WordPress と同じ4列の `rp_options` を作る。固定の autoload SELECT を対象に、同じ所有 DB へ事前に接続したライブ binlog と、各読み取りの `SHOW` による観測位置を照合する。binlog のデコーダーと行検証は受け入れ済み autoload 試験の固定ソースから派生する。

1. ライブ監視の開始位置と `SHOW` が一致し、旧値が共有ヒットする。
2. 別接続で固定行をコミットする。監視側の適用を止めている間は位置が不一致となり、通常 SELECT が新値を返す。
3. 同じライブ接続で対象の行とコミットを検証し、適用済み位置を更新する。書き込みを制御した環境で、直前の通常 SELECT の結果を再格納し、新値のヒットを確認する。
4. 同じ専用アカウントで所有する binlog 接続だけを切断する。監視側が切断を観測した後は通常 SELECT が新値を返す。

4回の読み取りについて位置確認4回と、通常 SELECT 2回を別々に数える。初期格納と監視接続の確認は別段階。再格納は他の書き込みがない固定条件でのみ行う。

位置確認と通常 SELECT は同じ所有接続で実行する。位置取得自体の障害、別の PHP-Wasm actor との同一ソース性、ファイル切り替え、再接続、位置取得後の書き込みに対する鮮度は試験範囲外。

```powershell
python -B experiments/wpdb-rpc-probe/prepare_driver.py
python -B experiments/binlog-autoload-frontier-probe/derive.py
cargo build --offline --locked --manifest-path experiments/binlog-autoload-frontier-probe/Cargo.toml --target-dir target/wpdb-rpc-probe/windows-cargo-target
python -B experiments/binlog-autoload-frontier-probe/run_check.py
```

MySQL の初回試験ではブロッキングダンプを通常終了させる構成で、所有 DB の正常終了の検証を通過しなかった。サーバーの回収と作業ディレクトリの削除は確認した。[初回試験の限定記録](evidence/windows-mysql-graceful-dump-close-attempt.json)

再試験ではMySQL 8.4.13で位置6002～6433、MariaDB 11.8.9で3058～3522を照合した。両DBで7イベントとコミット1件を適用し、位置確認4回と通常SELECT 2回を確認した。所有する監視接続の切断を観測し、DBの正常終了と作業ディレクトリの回収を確認した。[MySQL証跡](evidence/windows-mysql.json)・[MariaDB証跡](evidence/windows-mariadb.json)

切断を実際に発生させた試験では、両 DB で4経路が一致し、DB の正常終了と作業ディレクトリの削除を確認した。[MySQL](evidence/windows-mysql.json) · [MariaDB](evidence/windows-mariadb.json)
