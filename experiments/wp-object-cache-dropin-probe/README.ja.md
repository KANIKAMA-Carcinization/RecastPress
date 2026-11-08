# WordPress オブジェクトキャッシュのローダー検証

Windows の PHP 8.5.12 で、固定した WordPress 7.1.3 の `wp_start_object_cache()` を実行する。Rust を使うケースでは標準 `cache.php` の `wp_cache_init()` の本体だけを差し替え、所有する一時ディレクトリに `object-cache.php` として配置する。公開 `wp_cache_*` ラッパー、親 `WP_Object_Cache`、Rust リレー、56件の観測コーパスは既存の固定入力を使う。

| ケース | `wp_using_ext_object_cache()` | 観測 |
| --- | --- | --- |
| 標準 | `false` | `WP_Object_Cache`、56件一致 |
| Rust drop-in（独立した2プロセス） | `true` | `WP_Object_Cache` を継承、各56件一致。Rust は2グループの30操作と2回の flush を受信 |
| 遅延フックだけを登録する shim | `false` | 標準ラッパーと `WP_Object_Cache`、56件一致。フィクスチャが `muplugins_loaded` を発火した時点でコールバック実行 |
| 標準 `cache.php` を drop-in より先に直接読み込む | `true` | 実際のクラスとラッパーは標準のまま。フラグ単独では drop-in の実行を判定できない |
| Rust drop-in 読み込み後に標準 `cache.php` を直接読み込む | `true` | `wp_cache_init()` の再宣言で PHP が終了 |
| Rust drop-in 上で `wp_cache_init()` を直接再実行する | `true` | 新しいキャッシュインスタンスでローカル値は消えるが、同じリレーにある Rust 側の値は残る |

`wp_start_object_cache()` の再呼び出しでは、同じオブジェクトを維持したままブログを切り替え、元のブログに戻るとローカル値を再取得できた。検証範囲はローダーと有限のリクエスト内操作で、WordPress 全体のブートストラップ、DB、永続キャッシュ、Wasm は対象外。

```powershell
python -B experiments/wp-object-cache-dropin-probe/run_check.py --sha256 361fd901ff84283127be0b5cb9f79b2d0410b597adba3eb8da5a179c357597d4
```

このコマンドは既存の `wp-object-cache-rust-probe` で受理した Windows 実行ファイルを使う。記録先にファイルが存在しない場合は `--record` で証跡を保存できる。[Windows 証跡](evidence/windows-native.json)
