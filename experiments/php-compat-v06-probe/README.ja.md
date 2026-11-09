# v0.6 native PHP continuation probe

この実験は、固定した PHP 8.5.12 のプロセスと Rust の `Continuation` 状態機械を、Python runner が所有する双方向 JSONL pipe で接続します。PHP callback が公開 object を変更した後に Rust child operation を呼び、Rust がその変更前後の名前・byte長の増分・flag・aliasを観測値から計算して返します。PHP fixture も同じ値を生きたPHP heapから独立に計算し、Rustの返値と照合します。子の完了後は同じPHP objectとglobalを再読します。

## 実行

repository root から:

```powershell
python experiments/php-compat-v06-probe/run_check.py --self-test
python experiments/php-compat-v06-probe/run_check.py --record
python experiments/php-compat-v06-probe/run_check.py --verify-only
```

`--self-test` は PHP one-shot oracle、strict JSON oracle、対話 bridge、相関・handle field・sequence・snapshot・重複 JSON key・depth超過の拒否に加え、実 PHP `RuntimeException` の nested callback経由の伝播とabort後のresume拒否を確認します。PHP と Rust の両側で重複decoded key、UTF-8、depth、frame上限を検査します。`--record` は同じ確認を行い、実装・fixture・固定 PHP distribution manifest 全files・実行ファイルの SHA-256 を含む `evidence/native-continuation-v1.json` を作成します。実行前後にsourceとRust artifact hashを照合してからreceiptを保存します。`--verify-only` は保存済みreceiptと現在のsource hash、成功・throwable観測値、negative inventory、process reap件数を照合します。runner は PHP と Rust adapter の両プロセスを直接起動し、pipeを上限付きで中継し、成功・失敗のどちらでも所有したchildをkill/waitして回収します。

## 証拠の範囲

成功は native PHP と Rust の in-memory continuation protocol がこの小さな fixture で往復したことを示します。WordPress bootstrap、実WP API、production transport、DB、Wasm、PHP-Wasm 内への再入は実行していません。受信JSONは両peerで重複decoded keyを拒否し、PHPは `-n` で起動して利用者のini設定を読み込みません。throwable用の文字列IDは実際のThrowable objectを保持するPHP側request-local tableへ結び付きますが、RustへPHP heap pointerや本番用authority tokenを渡すwireではありません。通常bridgeではundeclaredな`php_handle` fieldをclosed schema violationとして拒否します。

固定PHP-WASIX artifactによる別の試行は [Wasm profile](wasm/README.md) に記録します。Windowsでのコンパイル失敗、Linuxでの固定oracle成功、nativeの双方向bridge成功はそれぞれ別の証跡です。対話bridgeの実Wasm接続は引き続き未受入です。
