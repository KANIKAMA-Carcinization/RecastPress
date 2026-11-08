# WordPress標準オブジェクトキャッシュのnative基準

固定PHP 8.5.12の64 bit CLIで、WordPress 7.1.3の元 `wp_cache_*` と `WP_Object_Cache` を実行する。実験用エントリーはコア6ファイルだけを読み込み、ブログID切替を試せるよう `MULTISITE` を定義する。DB接続・外部キャッシュ・プラグイン有効化を伴う全体起動は別の工程で扱う。

56件の観測を厳密なPHP型で照合する。`false`／`null` の保存とmiss、空値、既存／欠落時のadd・replace・delete、複数APIの整数／文字列キーと結果順、増減の0下限・負offset・符号付き64 bit整数の上限、オブジェクトの浅いclone、ブログごとのgroupとglobal group、flush・supportsを含む。複数APIのPHP連想配列は、型と順序を保つキー・値の行に変換する。

各プロセスはlocal・global・非永続groupに値を残して終了し、次のPHPプロセスは同じキーをmissとして読む。2件の出力はバイト単位で一致し、正常終了・EOF・reapと読み込んだコア、PHP配布物、実験入力の前後一致を記録した。[Windowsの実行記録](evidence/windows-native.json)

```powershell
python -B experiments/wp-object-cache-contract-probe/native_check.py
```

固定PHP配布と既存の準備済みWordPress treeを利用する。新しい証跡を作成する場合は `--record` を指定する。既存の証跡ファイルを上書きしない。
