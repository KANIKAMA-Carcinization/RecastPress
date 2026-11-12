# v0.6 native PHP continuation probe

この実験は、固定した PHP 8.5.12 のプロセスと Rust の `Continuation` 状態機械を、Python runner が所有する双方向 JSONL pipe で接続します。PHP callback が公開 object を変更した後に Rust child operation を呼び、Rust がその変更前後の名前・byte長の増分・flag・aliasを観測値から計算して返します。PHP fixture も同じ値を生きたPHP heapから独立に計算し、Rustの返値と照合します。子の完了後は同じPHP objectとglobalを再読します。追加のpublish scenarioでは、PHP発行の観測番号に基づきRustが公開状態を更新し、nested callback後の現在heapを再観測します。

## 実行

repository root から:

```powershell
python experiments/php-compat-v06-probe/run_check.py --self-test
python experiments/php-compat-v06-probe/run_check.py --record
python experiments/php-compat-v06-probe/run_check.py --verify-only
```

`--self-test` はsurface／strict JSON oracle、8種類のbridge（surface、throwable、publish、handles、handles_abort、hooks、hooks_catch、hooks_abort）と、不正な相関・handle・sequence・snapshot・重複JSON key・depth超過の拒否を確認します。`--record` は[現行receipt](evidence/native-continuation-v1.json)へ178 source identities、全30 owned childrenの終了・回収、観測結果、ローカルraw captureのhashを記録します。`--verify-only` はsource・binary・captureと観測結果を再照合します。送受信ログは`target/php-compat-v06-probe/captures/`配下に上限付きで保持し、Gitには追加しません。成功・失敗のどちらでも所有childをkill／waitして回収します。

新しいThrowable hooksのraw capture検証は、開始handleとcallback ID、CallCore、InvokePhp、seq／親effect、同一Throwable completion、PHPのRequestFinishedと実bytes数を独立に照合します。実観測ログに18種類の不正変更を加え、catchとabortの両ケースですべて拒否されることも確認します。

## PHP handle registry milestone

`fixtures/handle_registry.php` はPHP値を強参照し、`WeakMap` をobject identityのdedupeに使ってretain countを管理します。actual reference cell、PHPのcopy-on-write array、Closure、Throwable、resourceを扱い、registry自身のclone・serializeを拒否し、所有状態の複製を防ぎます。slotは世代番号と8-byte `random_bytes` tokenで再利用します。live handle数と累積issued数には上限があります。slot・generation・tokenはregistry内の相関と照合に使います。wire認証とproduction capability securityには別途受入が必要です。

releaseはfinal-release metadataを失効してからPHP自身の値解放へ進み、closeは全handleを先に失効します。fixtureはdestructorからのregistry再入、release／closeでdestructorが例外を投げた後も状態が一貫すること、明示的な `gc_collect_cycles`、close後に外部alias／resourceが残る場合を確認します。native `handles` と `handles_abort` は各PHP 5／Rust 4 frame（3,566／3,568 bytes）でroot→child→nested→rootを通り、`Finished`／`Aborted`を確認します。実行は全owned childを回収しました。従来のnative 5 bridge receiptは105 source identitiesとowned child 24件のsource-bound記録です。以前の6 bridge receiptは175 source identitiesとowned child 26件を記録した履歴です。Throwable hooks追加後のsource receiptではありません。Rustの `PhpHandle` getterは相関metadataを返し、`unregister_handle` はPHP側がfinal releaseを確認した後にRust mirrorを失効します。PHP値の実解放はPHPが担います。

one-shot の `dispatcher.php` は旧 `RequestRegistry` を除去し、この `HandleRegistry` を `retainObject`／`retainReference`／`resolve`／`close` で直接使用します。object aliasの同一handleは配列の `===` で確認し、reference cellのkindは `reference_cell`、wrong-kindとclose後の失効は `RegistryError` で検査します。既存15件のsurface oracle結果は維持しています。interactive surface／throwable bridgeもshared registryへ接続し、同じ `Throwable` をresolveして再throwし、`RequestFinished` の前にregistryをclose、close後の利用を拒否します。以前のnative 5 bridge receiptは105 source identitiesとowned child 24件のsource-bound記録です。以前の6 bridge receiptは175 source identitiesとowned child 26件を記録した履歴です。Throwable hooks追加後のsource receiptではありません。

独立 `handle_driver.php` はregistryのlifetime edge caseを検証します。ここで確認したのはfixtureへの統合です。production WordPress dispatcher、hook／Query、GC scheduling、製品権限・capability security、全WordPress runtimeの受入は今後の作業です。

## Resumable hooks / Rust Dispatcher native smoke

通常hooks scenarioはPHP側のhooks_driver.phpがHandleRegistryから実Closureをresolveして呼び、Rust側Dispatcherがpriority順、cursor、active bucket snapshot、child hook stackを維持します。従来のnormal-only native smokeはRust 1.94で7 PHP／6 Rust framesを完了し、root native priority 5、PHP root_a／root_b priority 10、native priority 20、child内のremove/add、Closure/object identityとregistry close後の拒否を確認しました。旧normal-only PHP-Wasm receiptは後述の通り履歴です。

今回のThrowable hooks --self-test は8 scenarioすべて成功し、private raw captureの独立検証でもsequence、parent correlation、typed completionと同一Throwable、RequestFinished統計を照合しました。hooks_catchではchildの実RuntimeExceptionを同じobjectのままrootでcatchし、rootB／late／native継続を実行します（7 PHP／6 Rust frames）。最終bytesはseed:r5:php-root-caught:php-b:late:r20、nameはroot-before-child:child:root-after-child:b:late、native traceは[r5,c3,r20]です。hooks_abortでは同じThrowableをrootで処理せず伝播させ、rootB／late／r20は実行しません（5 PHP／4 Rust frames）。最終nameはroot-before-child:child、native traceは[r5,c3]です。両modeでrootB除去とlate登録自体は観測します。

Throwable後の公開WP hook stateはRust Continuationの状態と別に保持します。child内ではcurrent=child、depth 2、child priority 5です。catch後はrootの通常完了でcurrent=root、depth 1、child priority 5／root priority nullとなり、abort時はcurrent=child、depth 2、child priority 5／root priority 10を観測します。Continuation depth 0とFinished／AbortedはRust側の結果です。[固定WP oracle](wp_hooks_oracle.py)は元plugin.php／WP_Hookを別プロセスで実行し、normal／catch／abortの返り値・登録変更・最終公開stack・priorityをnative／Wasmの両結果と比較します。通常完了時のhook名と両priorityがnullになることも比較対象です。

Rust APIは`begin_filter`／`advance`／`resume_php`／`resume_php_completion`／`cancel`を使い、既存のnative callback entrypointを保ちます。PHPの外部Closure変数はRequestFinishedまで保持するfixtureなので、remove時の本番参照解放、WordPress callable identity、product hooksやQueryの受入は示しません。以前のsource-bound recordは175 identities／owned child 26件、通常hooksのPHP-Wasm receipt [hooks-20261112T163831.989293Z-success.json](wasm/evidence/hooks-20261112T163831.989293Z-success.json) と[WSL origin](wasm/evidence/wsl-hooks-origin-20261112T163836.899479Z.json)は98入力のnormal-only履歴です。従来の105 identities／owned child 24件receiptも歴史的記録として残します。
## 固定WordPress hook oracle

固定WordPressとPHPを準備済みのrepository rootで実行します。CIのnative runnerはこのWordPress oracleを自動実行しません。

```powershell
python -B experiments/php-compat-v06-probe/wp_hooks_oracle.py --record
python -B experiments/php-compat-v06-probe/wp_hooks_oracle.py --compare-bridges
python -B experiments/php-compat-v06-probe/wp_hooks_oracle.py --verify-only
```

[oracle receipt](evidence/wp-hooks-throwable-v1.json)は184入力を束ね、固定archive内の3ソースと実行に使うファイルの一致を確認します。PHPの実stdoutとreceipt、native／Wasm receiptのhashと3ケースの実観測値も照合します。継続のterminal stateはRust側の観測です。PHPはRequestFinished送信前に未来のFinished状態を報告しません。

## 証拠の範囲

native runnerは固定fixtureのPHPとRust continuation／dispatcherを往復します。別のWordPress oracleは元plugin.php／WP_Hookの限定ケースを実行します。WordPress bootstrap、production transport、DB、全WordPress runtimeの受入は対象外です。実PHP-Wasmの結果は別のWasm receiptで扱います。受信JSONは両peerで重複decoded keyを拒否し、PHPは `-n` で起動して利用者のini設定を読み込みません。throwable用の文字列IDは実際のThrowable objectを保持するPHP側request-local tableへ結び付きますが、RustへPHP heap pointerや本番用authority tokenを渡すwireではありません。通常bridgeではundeclaredな`php_handle` fieldをclosed schema violationとして拒否します。

publish scenarioはPHPが読み出しごとに発行した観測番号1〜10を用い、PHP→Rust 11 frame、Rust→PHP 10 frame、合計7,383 bytesを往復しました。Rustはrootでcount・array entryを4・7へpublishし、childで5・11へ更新します。root callbackはchild呼出し前にglobal objectを差し替え、nested PHP callbackはarray末尾を77にし、outer callbackはunknown propertyを`php-outer`に変えます。最後のmarker 104は現在のcount 5、array entries 11・77、replacement名から計算し、古いouter viewを再利用せずPHPの現heapで照合します。alias、property reference、array entry reference、copy-on-writeの`[2, 4]`維持も確認しました。stale observation、同じpublishのreplay、無効targetを含むbatchは代入前に拒否し、batch内の最初の有効writeも残らないことを確認します。

このPHP publish fixtureは閉じた対象と単純なproperty・array entryの代入を使います。Rust trackerはPHP発行の観測番号とpublishの順序を扱います。userland magic methodやdestructorを含む代入のatomicity、production PHP heap registry、token認証、汎用dirty検出には別途実装・検証が必要です。

固定PHP-WASIX artifactによる別の試行は [Wasm profile](wasm/README.md) に記録します。Windowsでのコンパイル失敗、Linuxでの固定oracle、nativeの双方向bridgeはそれぞれ別の証跡です。Linuxでは共通peerを使った実Wasmの正常系・例外系bridgeにも成功しました。WordPress本体、production handle、workerの終了・資源管理全体の受入は残っています。

## 共通ピア実装

Rust の JSONL protocol 処理と Continuation 操作は [共通peer](../../crates/recastpress-wp/examples/v06_bridge_peer/mod.rs) に切り出し、native CLI と PHP-Wasm host の両方が同じ `run_peer` を呼びます。native runner はプロセス間の PHP pipe を中継し、shared peer のフレーム検証と Continuation state machine を実行します。モジュール自体はデッドラインを設定せず、呼び出し側が deadline 付きストリームを提供します。
