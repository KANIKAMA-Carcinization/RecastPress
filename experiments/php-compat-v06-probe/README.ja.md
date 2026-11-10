# v0.6 native PHP continuation probe

この実験は、固定した PHP 8.5.12 のプロセスと Rust の `Continuation` 状態機械を、Python runner が所有する双方向 JSONL pipe で接続します。PHP callback が公開 object を変更した後に Rust child operation を呼び、Rust がその変更前後の名前・byte長の増分・flag・aliasを観測値から計算して返します。PHP fixture も同じ値を生きたPHP heapから独立に計算し、Rustの返値と照合します。子の完了後は同じPHP objectとglobalを再読します。追加のpublish scenarioでは、PHP発行の観測番号に基づきRustが公開状態を更新し、nested callback後の現在heapを再観測します。

## 実行

repository root から:

```powershell
python experiments/php-compat-v06-probe/run_check.py --self-test
python experiments/php-compat-v06-probe/run_check.py --record
python experiments/php-compat-v06-probe/run_check.py --verify-only
```

`--self-test` は PHP one-shot oracle、strict JSON oracle、対話 bridge、publish scenario、相関・handle field・sequence・snapshot・重複 JSON key・depth超過の拒否に加え、実 PHP `RuntimeException` の nested callback経由の伝播とabort後のresume拒否を確認します。PHP と Rust の両側で重複decoded key、UTF-8、depth、frame上限を検査します。`--record` はpublish scenarioも実行し、stale観測・replay・batch全体の事前検査を含むnegative caseを照合します。native receipt [native-continuation-v1.json](evidence/native-continuation-v1.json) には実装・fixture・固定 PHP distribution manifest 全files・実行ファイルの SHA-256 が入り、実行前後にsourceとRust artifact hashを照合します。`--verify-only` は保存済みreceiptと現在のsource hash、成功・throwable・publish観測値、negative inventory、process reap件数を照合します。runner は PHP と Rust adapter の両プロセスを直接起動し、pipeを上限付きで中継し、成功・失敗のどちらでも所有したchildをkill/waitして回収します。

## 証拠の範囲

成功は native PHP と Rust の in-memory continuation protocol がこの小さな fixture で往復したことを示します。WordPress bootstrap、実WP API、production transport、DB、Wasm、PHP-Wasm 内への再入は実行していません。受信JSONは両peerで重複decoded keyを拒否し、PHPは `-n` で起動して利用者のini設定を読み込みません。throwable用の文字列IDは実際のThrowable objectを保持するPHP側request-local tableへ結び付きますが、RustへPHP heap pointerや本番用authority tokenを渡すwireではありません。通常bridgeではundeclaredな`php_handle` fieldをclosed schema violationとして拒否します。

publish scenarioはPHPが読み出しごとに発行した観測番号1〜10を用い、PHP→Rust 11 frame、Rust→PHP 10 frame、合計7,383 bytesを往復しました。Rustはrootでcount・array entryを4・7へpublishし、childで5・11へ更新します。root callbackはchild呼出し前にglobal objectを差し替え、nested PHP callbackはarray末尾を77にし、outer callbackはunknown propertyを`php-outer`に変えます。最後のmarker 104は現在のcount 5、array entries 11・77、replacement名から計算し、古いouter viewを再利用せずPHPの現heapで照合します。alias、property reference、array entry reference、copy-on-writeの`[2, 4]`維持も確認しました。stale observation、同じpublishのreplay、無効targetを含むbatchは代入前に拒否し、batch内の最初の有効writeも残らないことを確認します。

このPHP publish fixtureは閉じた対象と単純なproperty・array entryの代入を使います。Rust trackerはPHP発行の観測番号とpublishの順序を扱います。userland magic methodやdestructorを含む代入のatomicity、production PHP heap registry、token認証、汎用dirty検出には別途実装・検証が必要です。

固定PHP-WASIX artifactによる別の試行は [Wasm profile](wasm/README.md) に記録します。Windowsでのコンパイル失敗、Linuxでの固定oracle、nativeの双方向bridgeはそれぞれ別の証跡です。Linuxでは共通peerを使った実Wasmの正常系・例外系bridgeにも成功しました。WordPress本体、production handle、workerの終了・資源管理全体の受入は残っています。

## 共通ピア実装

Rust の JSONL protocol 処理と Continuation 操作は [共通peer](../../crates/recastpress-wp/examples/v06_bridge_peer/mod.rs) に切り出し、native CLI と PHP-Wasm host の両方が同じ `run_peer` を呼びます。native runner はプロセス間の PHP pipe を中継し、shared peer のフレーム検証と Continuation state machine を実行します。モジュール自体はデッドラインを設定せず、呼び出し側が deadline 付きストリームを提供します。
