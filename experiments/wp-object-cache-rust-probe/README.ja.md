# Rustで管理する要求内オブジェクトキャッシュ

WordPress 7.1.3標準キャッシュの56観測を、同じPHPコードで比較する。`rp-cache-multiple` と `rp-cache-integer` の2グループをRustの要求内ストアに接続し、他のグループはWordPress標準の `WP_Object_Cache` を使う。複数APIのループとPHP配列のキー順序は、上流の処理を使う。[通常PHPの比較基準](../wp-object-cache-contract-probe/README.ja.md)

Rustは、PHP側で正規化済みのキーと `null`・bool・整数・バイト列を受け取る。missと保存済みの `false`／`null` を区別し、条件付きadd／replace、削除、整数更新、グループ消去を閉じたprivate JSONLで扱う。`BudgetPool` の `ObjectCache` 予約を使い、1プロセスの論理上限を64 KiBとする。終了時にエントリーと予約を解放する。

この段階は通常PHPとRust子プロセスを使う固定グループの比較である。製品では共有キャッシュを接続する前に、PHP-Wasmのprivateチャネル、DB変更追跡、世代をまたぐ失効、保持容量をそれぞれ検証する。標準の浅いオブジェクトcloneなど、今回のRust型に含まれない値は、比較対象の別グループでWordPress標準実装を通る。

WindowsでPHPとRustを2組の独立プロセスとして起動した。各組で同じ56観測がnative基準と完全一致し、Rustは複数APIの14操作、整数グループの16操作、flush 2操作を処理した。`finish` 後は0エントリー・0バイト。全プロセスを回収し、試験用ディレクトリを削除した。locked/offline buildの4 unitでは、予約不足による更新拒否、group分離、整数更新と解放も検証した。[ビルド証跡](evidence/windows-build.json)・[実行証跡](evidence/windows-native.json)

```powershell
$digest = python -B experiments/wp-object-cache-rust-probe/build_check.py
python -B experiments/wp-object-cache-rust-probe/run_check.py --sha256 $digest
```
