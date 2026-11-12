# v0.6 native PHP continuation probe

この実験は、固定した PHP 8.5.12 のプロセスと Rust の `Continuation` 状態機械を、Python runner が所有する双方向 JSONL pipe で接続します。PHP callback が公開 object を変更した後に Rust child operation を呼び、Rust がその変更前後の名前・byte長の増分・flag・aliasを観測値から計算して返します。PHP fixture も同じ値を生きたPHP heapから独立に計算し、Rustの返値と照合します。子の完了後は同じPHP objectとglobalを再読します。追加のpublish scenarioでは、PHP発行の観測番号に基づきRustが公開状態を更新し、nested callback後の現在heapを再観測します。

## 実行

repository root から:

```powershell
python experiments/php-compat-v06-probe/run_check.py --self-test
python experiments/php-compat-v06-probe/run_check.py --record
python experiments/php-compat-v06-probe/run_check.py --verify-only
```

`--self-test` は PHP one-shot oracle、strict JSON oracle、対話 bridge、publish scenario、handle registry／lifetime scenario、resumable hooks scenario、相関・handle field・sequence・snapshot・重複 JSON key・depth超過の拒否に加え、実 PHP `RuntimeException` の nested callback経由の伝播とabort後のresume拒否を確認します。PHP と Rust の両側で重複decoded key、UTF-8、depth、frame上限を検査します。`--record` は6種類のbridge（surface、throwable、publish、handles、handles_abort、hooks）とnegative caseを記録し、stale観測・replay・batch全体の事前検査、およびrelease／close後の失効を照合します。source-bound native receipt [native-continuation-v1.json](evidence/native-continuation-v1.json) はhooks scenarioを含む175 source identitiesとowned child 26件に更新済みで、Rust 1.94において`--record`と`--verify-only`の両方が成功しました。fixture、関連source、Rust artifactのhash照合も実行前後で通過しています。`--verify-only` は保存済みhooksを含む全bridge観測・source hash・negative inventory・process reap件数を照合します。runner はPHPとRust adapterの両processを直接起動し、pipeを上限付きで中継し、成功・失敗のどちらでも所有したchildをkill/waitして回収します。

## PHP handle registry milestone

`fixtures/handle_registry.php` はPHP値を強参照し、`WeakMap` をobject identityのdedupeに使ってretain countを管理します。actual reference cell、PHPのcopy-on-write array、Closure、Throwable、resourceを扱い、registry自身のclone・serializeを拒否し、所有状態の複製を防ぎます。slotは世代番号と8-byte `random_bytes` tokenで再利用します。live handle数と累積issued数には上限があります。slot・generation・tokenはregistry内の相関と照合に使います。wire認証とproduction capability securityには別途受入が必要です。

releaseはfinal-release metadataを失効してからPHP自身の値解放へ進み、closeは全handleを先に失効します。fixtureはdestructorからのregistry再入、release／closeでdestructorが例外を投げた後も状態が一貫すること、明示的な `gc_collect_cycles`、close後に外部alias／resourceが残る場合を確認します。native `handles` と `handles_abort` は各PHP 5／Rust 4 frame（3,566／3,568 bytes）でroot→child→nested→rootを通り、`Finished`／`Aborted`を確認します。実行は全owned childを回収しました。従来のnative 5 bridge receiptは105 source identitiesとowned child 24件のsource-bound記録です。hooks追加後の現行6 bridge receiptは175 source identitiesとowned child 26件を記録しています。Rustの `PhpHandle` getterは相関metadataを返し、`unregister_handle` はPHP側がfinal releaseを確認した後にRust mirrorを失効します。PHP値の実解放はPHPが担います。

one-shot の `dispatcher.php` は旧 `RequestRegistry` を除去し、この `HandleRegistry` を `retainObject`／`retainReference`／`resolve`／`close` で直接使用します。object aliasの同一handleは配列の `===` で確認し、reference cellのkindは `reference_cell`、wrong-kindとclose後の失効は `RegistryError` で検査します。既存15件のsurface oracle結果は維持しています。interactive surface／throwable bridgeもshared registryへ接続し、同じ `Throwable` をresolveして再throwし、`RequestFinished` の前にregistryをclose、close後の利用を拒否します。以前のnative 5 bridge receiptは105 source identitiesとowned child 24件のsource-bound記録です。hooks追加後の現行6 bridge receiptは175 source identitiesとowned child 26件で、`--record`／`--verify-only`成功を記録しています。

独立 `handle_driver.php` はregistryのlifetime edge caseを検証します。ここで確認したのはfixtureへの統合です。production WordPress dispatcher、hook／Query、GC scheduling、製品権限・capability security、全WordPress runtimeの受入は今後の作業です。

## Resumable hooks / Rust Dispatcher native smoke

hooks scenarioはPHP側の`hooks_driver.php`が`HandleRegistry`から実Closureをresolveして呼び、Rust側のDispatcherがpriority順、cursor、active bucket snapshot、child hook stackを維持します。native smokeはRust 1.94で7 PHP／6 Rust framesを完了しました。root native priority 5、PHP `root_a`／`root_b` priority 10、native priority 20の順序を保ち、`root_a`のchild hook中に`root_b`をremoveしてpriority 15の`late`を追加しても、`root_b`は既存bucketから実行され、`late`は次のpriorityで実行されます。childはnative 3、PHP 5、native 7で戻り、最終bytesは`seed:r5:c3:php-child:c7:php-root:php-b:late:r20`です。PHPのobject alias/name mutation、Closure identityとdistinct ID、親hook復帰、registry close後の全handle拒否も確認しました。

Rust APIは`begin_filter`／`advance`／`resume_php`／`cancel`を使い、既存のnative callback entrypointを保ちます。PHPの外部Closure変数は`RequestFinished`まで保持するfixtureなので、remove時の本番参照解放、WordPress callable identity、product hooksやQueryの受入は示しません。通常の`--record` runnerには6 bridgeとhooks scenarioが含まれます。Rust 1.94で更新済みsource-bound recordと`--verify-only`が成功し、WordPress sourceと関連testsを含む175 identities、owned-child構成26件を記録しました。[実PHP-Wasm hooks receipt](wasm/evidence/hooks-20261112T163831.989293Z-success.json)でも同じ7／6 frame、返り値、trace、aliasとclose後拒否が成功しました。[WSL origin](wasm/evidence/wsl-hooks-origin-20261112T163836.899479Z.json)は98入力のrepository／snapshot／実行後一致を記録します。従来の105 identities／owned child 24件receiptは歴史的記録として残します。

## 証拠の範囲

成功は native PHP と Rust の in-memory continuation protocol がこの小さな fixture で往復したことを示します。WordPress bootstrap、実WP API、production transport、DB、Wasm、PHP-Wasm 内への再入は実行していません。受信JSONは両peerで重複decoded keyを拒否し、PHPは `-n` で起動して利用者のini設定を読み込みません。throwable用の文字列IDは実際のThrowable objectを保持するPHP側request-local tableへ結び付きますが、RustへPHP heap pointerや本番用authority tokenを渡すwireではありません。通常bridgeではundeclaredな`php_handle` fieldをclosed schema violationとして拒否します。

publish scenarioはPHPが読み出しごとに発行した観測番号1〜10を用い、PHP→Rust 11 frame、Rust→PHP 10 frame、合計7,383 bytesを往復しました。Rustはrootでcount・array entryを4・7へpublishし、childで5・11へ更新します。root callbackはchild呼出し前にglobal objectを差し替え、nested PHP callbackはarray末尾を77にし、outer callbackはunknown propertyを`php-outer`に変えます。最後のmarker 104は現在のcount 5、array entries 11・77、replacement名から計算し、古いouter viewを再利用せずPHPの現heapで照合します。alias、property reference、array entry reference、copy-on-writeの`[2, 4]`維持も確認しました。stale observation、同じpublishのreplay、無効targetを含むbatchは代入前に拒否し、batch内の最初の有効writeも残らないことを確認します。

このPHP publish fixtureは閉じた対象と単純なproperty・array entryの代入を使います。Rust trackerはPHP発行の観測番号とpublishの順序を扱います。userland magic methodやdestructorを含む代入のatomicity、production PHP heap registry、token認証、汎用dirty検出には別途実装・検証が必要です。

固定PHP-WASIX artifactによる別の試行は [Wasm profile](wasm/README.md) に記録します。Windowsでのコンパイル失敗、Linuxでの固定oracle、nativeの双方向bridgeはそれぞれ別の証跡です。Linuxでは共通peerを使った実Wasmの正常系・例外系bridgeにも成功しました。WordPress本体、production handle、workerの終了・資源管理全体の受入は残っています。

## 共通ピア実装

Rust の JSONL protocol 処理と Continuation 操作は [共通peer](../../crates/recastpress-wp/examples/v06_bridge_peer/mod.rs) に切り出し、native CLI と PHP-Wasm host の両方が同じ `run_peer` を呼びます。native runner はプロセス間の PHP pipe を中継し、shared peer のフレーム検証と Continuation state machine を実行します。モジュール自体はデッドラインを設定せず、呼び出し側が deadline 付きストリームを提供します。
