# 監視接続と読み取り接続の同一性

使い捨ての Windows MySQL／MariaDB に2つの非プール接続を開く。制御側が所有する DB に公開のランダム識別子と監視用 nonce を別列で置く。読み取りアカウントは `id` と `binding` だけを参照できる。監視アカウントと読み取りアカウントは、異なる接続 ID で同じサーバープロファイルと公開識別子を観測した場合にだけキャッシュの許可を得る。

否定ケースでは読み取り接続が、意図的に別の識別子を設定した試験行を実際に読む。監視側の観測値と期待値は固定したまま、キャッシュの許可が出ないことを確認する。同じ読み取りアカウントによる監視用 nonce の取得も DB が拒否する。実際の別サーバーへの接続、PHP-Wasm、binlog の監視、キャッシュ内容の鮮度はこの試験の範囲外。

既存の Windows DB ツールとパッチ済みドライバーを用意したワークスペースで実行する。既存のネイティブ試験の Cargo マニフェストとロックファイルを作業用ディレクトリで再利用する。

```powershell
python -B experiments/binlog-source-identity-probe/run_check.py
```

MySQL 8.4.13とMariaDB 11.8.9で、一致時の許可、不一致時の拒否、読み取り接続からのnonce参照拒否、DBの正常終了を確認した。[MySQL証跡](evidence/windows-mysql.json)・[MariaDB証跡](evidence/windows-mariadb.json)
