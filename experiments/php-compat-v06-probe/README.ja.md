# v0.6 native PHP continuation probe

この実験は、固定した PHP 8.5.12 のプロセスと Rust の `Continuation` 状態機械を、Python runner が所有する双方向 JSONL pipe で接続します。PHP callback が公開 object を変更した後に Rust child operation を呼び、Rust がその変更前後の名前・byte長の増分・flag・aliasを観測値から計算して返します。PHP fixture も同じ値を生きたPHP heapから独立に計算し、Rustの返値と照合します。子の完了後は同じPHP objectとglobalを再読します。追加のpublish scenarioでは、PHP発行の観測番号に基づきRustが公開状態を更新し、nested callback後の現在heapを再観測します。

## 実行

repository root から:

```powershell
python experiments/php-compat-v06-probe/run_check.py --self-test
python experiments/php-compat-v06-probe/run_check.py --record
python experiments/php-compat-v06-probe/run_check.py --verify-only
```

`--self-test` はsurface／strict JSON oracle、9種類のbridge（surface、throwable、publish、handles、handles_abort、hooks、hooks_catch、hooks_abort、callable_api）と、不正な相関・handle・sequence・snapshot・重複JSON key・depth超過の拒否を確認します。`--record` は[現行receipt](evidence/native-continuation-v1.json)へ187 source identities、全32 owned childrenの終了・回収、観測結果、ローカルraw captureのhashを記録します。`--verify-only` はsource・binary・captureと観測結果を再照合します。送受信ログは`target/php-compat-v06-probe/captures/`配下に上限付きで保持し、Gitには追加しません。成功・失敗のどちらでも所有childをkill／waitして回収します。

新しいThrowable hooksのraw capture検証は、開始handleとcallback ID、CallCore、InvokePhp、seq／親effect、同一Throwable completion、PHPのRequestFinishedと実bytes数を独立に照合します。実観測ログに18種類の不正変更を加え、catchとabortの両ケースですべて拒否されることも確認します。

native relayのstdout／stderrは専用のblocking readerで読み、待機中に固定間隔のpollを挟みません。異常時は所有childのkill／waitでreadを解除してからworkerをjoinします。実childを使う2試験で、小さなframeの即時転送・EOF drainと、待機中のreaderがchild回収後に終了することを検証します。通常の8 profileは40秒、数千frameを往復するcallable profileは180秒のdeadlineを持ち、1 MiB/frame・合計32 MiBの上限とcleanupは共通です。これはPoC runnerの上限であり、製品のCPU時間予算ではありません。

## PHP handle registry milestone

`fixtures/handle_registry.php` はPHP値を強参照し、`WeakMap` をobject identityのdedupeに使ってretain countを管理します。actual reference cell、PHPのcopy-on-write array、Closure、Throwable、resourceを扱い、registry自身のclone・serializeを拒否し、所有状態の複製を防ぎます。slotは世代番号と8-byte `random_bytes` tokenで再利用します。live handle数と累積issued数には上限があります。slot・generation・tokenはregistry内の相関と照合に使います。wire認証とproduction capability securityには別途受入が必要です。

releaseはfinal-release metadataを失効してからPHP自身の値解放へ進み、closeは全handleを先に失効します。fixtureはdestructorからのregistry再入、release／closeでdestructorが例外を投げた後も状態が一貫すること、明示的な `gc_collect_cycles`、close後に外部alias／resourceが残る場合を確認します。native `handles` と `handles_abort` は各PHP 5／Rust 4 frame（3,566／3,568 bytes）でroot→child→nested→rootを通り、`Finished`／`Aborted`を確認します。実行は全owned childを回収しました。従来のnative 5 bridge receiptは105 source identitiesとowned child 24件のsource-bound記録です。以前の6 bridge receiptは175 source identitiesとowned child 26件を記録した履歴です。Throwable hooks追加後のsource receiptではありません。Rustの `PhpHandle` getterは相関metadataを返し、`unregister_handle` はPHP側がfinal releaseを確認した後にRust mirrorを失効します。PHP値の実解放はPHPが担います。

one-shot の `dispatcher.php` は旧 `RequestRegistry` を除去し、この `HandleRegistry` を `retainObject`／`retainReference`／`resolve`／`close` で直接使用します。object aliasの同一handleは配列の `===` で確認し、reference cellのkindは `reference_cell`、wrong-kindとclose後の失効は `RegistryError` で検査します。既存15件のsurface oracle結果は維持しています。interactive surface／throwable bridgeもshared registryへ接続し、同じ `Throwable` をresolveして再throwし、`RequestFinished` の前にregistryをclose、close後の利用を拒否します。以前のnative 5 bridge receiptは105 source identitiesとowned child 24件のsource-bound記録です。以前の6 bridge receiptは175 source identitiesとowned child 26件を記録した履歴です。Throwable hooks追加後のsource receiptではありません。

独立 `handle_driver.php` はregistryのlifetime edge caseを検証します。ここで確認したのはfixtureへの統合です。production WordPress dispatcher、hook／Query、GC scheduling、製品権限・capability security、全WordPress runtimeの受入は今後の作業です。

## Resumable hooks / Rust Dispatcher native smoke

通常hooks scenarioはPHP側のhooks_driver.phpがHandleRegistryから実Closureをresolveして呼び、Rust側Dispatcherがpriority順、cursor、active bucket snapshot、child hook stackを維持します。従来のnormal-only native smokeはRust 1.94で7 PHP／6 Rust framesを完了し、root native priority 5、PHP root_a／root_b priority 10、native priority 20、child内のremove/add、Closure/object identityとregistry close後の拒否を確認しました。旧normal-only PHP-Wasm receiptは後述の通り履歴です。

Throwable hooks追加時の --self-test は従来8 scenarioすべて成功し、private raw captureの独立検証でもsequence、parent correlation、typed completionと同一Throwable、RequestFinished統計を照合しました。hooks_catchではchildの実RuntimeExceptionを同じobjectのままrootでcatchし、rootB／late／native継続を実行します（7 PHP／6 Rust frames）。最終bytesはseed:r5:php-root-caught:php-b:late:r20、nameはroot-before-child:child:root-after-child:b:late、native traceは[r5,c3,r20]です。hooks_abortでは同じThrowableをrootで処理せず伝播させ、rootB／late／r20は実行しません（5 PHP／4 Rust frames）。最終nameはroot-before-child:child、native traceは[r5,c3]です。両modeでrootB除去とlate登録自体は観測します。

Throwable後の公開WP hook stateはRust Continuationの状態と別に保持します。child内ではcurrent=child、depth 2、child priority 5です。catch後はrootの通常完了でcurrent=root、depth 1、child priority 5／root priority nullとなり、abort時はcurrent=child、depth 2、child priority 5／root priority 10を観測します。Continuation depth 0とFinished／AbortedはRust側の結果です。[固定WP oracle](wp_hooks_oracle.py)は元plugin.php／WP_Hookを別プロセスで実行し、normal／catch／abortの返り値・登録変更・最終公開stack・priorityをnative／Wasmの両結果と比較します。通常完了時のhook名と両priorityがnullになることも比較対象です。

Rust APIは`begin_filter`／`advance`／`resume_php`／`resume_php_completion`／`cancel`を使い、既存のnative callback entrypointを保ちます。PHPの外部Closure変数はRequestFinishedまで保持するfixtureなので、remove時の本番参照解放、WordPress callable identity、product hooksやQueryの受入は示しません。以前のsource-bound recordは175 identities／owned child 26件、通常hooksのPHP-Wasm receipt [hooks-20261112T163831.989293Z-success.json](wasm/evidence/hooks-20261112T163831.989293Z-success.json) と[WSL origin](wasm/evidence/wsl-hooks-origin-20261112T163836.899479Z.json)は98入力のnormal-only履歴です。従来の105 identities／owned child 24件receiptも歴史的記録として残します。

## PHP callable API profile / native integration

callable_driver.php は1つのPHP script effectの下で、固定WordPressの7 global API（add_filter、remove_filter、has_filter、apply_filters、current_filter、doing_filter、did_filter）をRust Dispatcherへ接続します。callable_program.php は実際のPHP callableを登録・呼出し、固定WordPress 7.1.3のplugin.php／WP_Hook oracleと従来20ケース、型変換・優先度・警告再入の20組、counter配列COWの18ケース、priority配列COW・解放中のThrowable連鎖の18ケース、remove_filter再入COWの8ケース、hook-unset再入とThrowable連鎖の3ケース（計87経路）を生成します。API identityは固定WPの _wp_filter_build_unique_id 規則に従い、文字列の大小文字を保ち、is_callableを登録前に呼びません。ID不能のcallbackは登録せずadd_filterはtrue、ID可能だが無効なcallbackはdispatch時にPHPの実Errorを返します。function string、static method string／array、object method、invokable object、Closureとcase違いを扱います。

c5c414e時点の履歴native bridgeはPHP 139／Rust 138 frames、7 API合計91 CallCore、17 callback invocation、27 release effectでした。従来20ケースはsame-ID replacementと順序、priority/accepted_args、zero-argument call mode、all listenerの未切詰め引数、raw hook nameと任意byte ID、PHP array-key変換、falsey ID、invalid callbackの遅延Errorを照合します。外部aliasを除いたobject callbackのremoveでは、registry release中のdestructorがremove完了前にhas_filter/current_filter/add_filterを再入できます。self-remove後にthrowするcaseでは、callback objectはparent catchより先にdestructされ、WP公開hook stateは別に残ります。固定WP oracleの期待値は20件すべて生成され、object IDの数値やThrowable byte列を期待JSONへ直接漏らさず、名前／messageはhexで比較します。

wire v2では、生のPHP引数を独立したargument handleとして保持し、Rustからのboundary effectごとに実際の配列アクセス・callback ID生成・整数castを実行します。警告handlerが再入・例外送出・hook再登録した場合も、その後のRust処理は該当時点の状態を読み、取得済みのreceiverを保持します。警告文の再生やPHP側のshadow dispatcherは使いません。argumentはAPI終了前に一括解放し、世代番号付きの確認後にRust mirrorを失効します。

wire v2のnative実測は全87経路が固定WPと一致しました。API合計1,503（add 285／remove 105／has 468／apply 149／current 65／doing 339／did 92）、callback 289、callable release 272、language boundary 5,282、argument release batch 1,438、PHP 8,788／Rust 8,787 frames、wire 5,641,225 bytesを記録しました。独立raw checkerは実際の送受信記録を読み、42種類の不正変更をすべて拒否します。引数の役割、slot世代の再使用、正規化結果、呼出し引数、foreach／null methodの相関と、RequestFinishedの統計も照合します。

外側のhook配列は型変換前にoperandの版を保持します。警告handler中の書き換えがcopy-on-writeを起こした場合、残る所有者に応じて旧版を参照するかnull operandとして扱い、解放とdestructor再入を処理してから続行します。キーが本当に存在しない場合のundefined-offset警告と、operandが失効して直接null methodになる場合を区別します。空のall objectでも元WPのdo-whileが実行する配列readとforeach(null)の警告を実PHPから発生させます。counter配列も型変換前にRust側でoperandの版を保持し、isset・read・write・incrementを別々に完了します。18ケースでは、警告handlerのnested applyによる加算の消失、初回代入の不成立、did_filterの0／実null、および後段hook lookup時には加算済みの値が残ることを、読取りだけのcontrolと比較します。実nullを0に置換せず、そのままPHPへ返します。三重の再入では、middleの古いreadは1を保ち、outerはnullになり、innerの加算がlive counterへ反映されるかをcontrolと比較します。18ケース以外のcounter所有権・書込み／unset条件は別途の受入対象です。

優先度の存在確認はkey変換前のcallback mapを保持し、警告handlerがbucketを削除しても元のisset結果を保ちます。実行順序はisset、accepted_argsの整数cast、priorityの書込みkey変換であり、ソース上の行番号順ではありません。書込み前のmapが警告再入で分離された場合、外側の登録をlive mapへ混入させず、元の存在確認結果を使ってlive mapのksort・array_keys・active iterationの補正を完了します。古いcallable leaseの解放とdestructor再入は、その補正より先に処理します。

remove_filterのpriority操作もWordPressの各PHP境界ごとにmap版を保持します。callback-map existenceは`class-wp-hook.php:212`相当のissetより前、bucket readはline 217相当のwarning前、unsetはline 218相当の境界前にそれぞれ別operandを固定します。bucketはMissing／Empty／NonEmptyを区別し、read warningで追加されたcallbackを後続unsetが消す場合と、unset warning中に追加されたcallbackが残る場合を測定しました。古いoperandの解放ACK後、priority順序はlive mapから再計算します。外側hook bindingは`plugin.php:328`相当のunset warning前に対象objectを保持し、warning handlerがadd_filterで差し込んだlive bindingを外側unsetが解除する動作を再現します。最終解放時にThrowable Aが保留されdestructorがBを投げるケースでは、PHPが生成した同一B objectと`B.getPrevious() === A`を確認します。

実行中のforeachは、既に呼び終えたcallbackも含め、bucket全体のsnapshotを末尾まで保持します。削除済みobjectのdestructorはsnapshotが終わってから、次の優先度のnative／PHP callbackへ進む前に実行します。例外中の解放には明示的なrelease_callable_unwind effectを使い、PHP自身のtry/finallyでpending Throwable Aの下で最終leaseを解放します。通常解放ならAを維持し、destructorがBを投げればPHPが作るprevious chainを持った同じBを返します。normal／unwindのどちらもcallable slotの最終解放ACKを要求します。これらは測定した有限のpriority／解放ケースであり、全callback property・PHP heapの受入は示しません。

filter valueと引数はnull、bool、signed-64 int、IEEE float bit pattern、byte string、ordered arrayのmaterialized PODに限ります。PHP object、reference、resourceを含むfilter value/argumentは明示的に拒否します。hook名とpriorityにはnull／bool／int／float／byte stringを扱い、正規化済みの配列キーと元のscalarを分離します。current_filterとall listenerは元のscalar、doing_filterはPHPの厳密比較を使い、NaNは自分自身とも一致しません。priorityの安定したSORT_NUMERIC順と、実行中のmin／比較・厳密一致による反復位置の補正を区別します。配列／objectの不正なhook・priorityは該当するPHPアクセスから実TypeErrorを返します。成功するresource hook／priority、WordPress global変数の直接読書き、完全なPHP heap、production WP dispatcherとhook／Queryの受入はこのfixtureに含みません。registry closure前は全callback releaseを実行し、registry close後の利用を拒否します。

### Action API family / isolated native and Wasm profile

7つのAction API（add_action、remove_action、has_action、do_action、current_action、doing_action、did_action）は、Filter APIの別名と共有hook registry／callback dispatcherを使いますが、`action_api`という別scenario・別receiptで測定します。Action callbackの戻り値は破棄し、`do_action`は常にnullを返します。`$wp_actions`は`$wp_filters`と独立し、listenerがないhookでもcallback実行前に回数が増えます。all listenerはaccepted_argsにかかわらずhook名と全引数を受け取り、named callbackの引数切詰め・ゼロ引数時の空文字placeholderはWP_Hookの規則に従います。通常のnested dispatchと、root dispatchからのall／named callback Throwable・catch後のstale stackを別々に照合します。さらに親Action callback内で子ActionのThrowableを同一objectとしてcatchして正常終了する経路を、固定WP／native／Wasm共通fixtureへ追加しました。catch中は子hookがcurrent／doing状態に残り、親dispatch終了後はWordPressと同じく子のstale tailを1回popして親hookをcurrent／doing状態に残し、stack depthは1、両Action counterは1です。

Action fixtureは固定された非数値の文字列hookを使い、固定WP 7.1.3、native PHP/Rust peer、PHP-WASIX/Rust peerで同じ観測を比較します。counter更新・読取りは実PHPの`$wp_actions`境界を呼び、Rust dispatcherのaction counterは更新成功後に進めます。固定WP oracleとの比較はFilter 87経路にAction APIの17 observation groupsを加えます。追加したglobal state groupは、unset／null／false／true／int／float／string／array／objectの9状態について`do_action`と`did_action`をそれぞれ測ります。さらに同一hookの警告handlerからnested `do_action`する`$wp_actions` COWを、live配列とby-value snapshotの両条件でAction bridgeへ接続しました。liveではnested加算が外側にも残り、snapshotではnested中のsnapshot readは旧値を保ちつつlive globalは更新されます。警告・listener順、counter read、最終値まで固定WP／native／Wasmで一致します。非文字列hook keyの変換警告順はこのAction profileで受入していません。現実装はdispatcher key lookupとPHP counter boundaryの両方でhook keyを評価するため、float hookの警告回数・Throwable位置をWPと同一と主張しません。

[$wp_actions scalar seed probe](wp_action_counter_seed_poc.py)、[PHP fixture](fixtures/wp_action_counter_seed_poc.php)、[target-only receipt](evidence/wp-action-counter-seed-poc-v1.json)では、固定WP 7.1.3／PHP 8.5.12の`++`挙動を測定した。別途Action bridgeでint 7→8→9、float 7.5→8.5→9.5、`PHP_INT_MAX`→float 2^63→同じfloat、bool `true`保持と二回の`E_WARNING`、string `a`→`b`→`c`と二回の`E_DEPRECATED`を接続した。診断→all listener→named callbackの順序、severity／message、counter値は固定WP／native／Wasmerで一致する。さらに[global state target-only probe](evidence/wp-action-global-state-poc-v1.json)の9状態×2 APIをAction bridgeへ接続した。unset／nullは`do_action`時に配列となり通常dispatchし、falseは`E_DEPRECATED`後に配列となりdispatchする。true／数値はError、stringはTypeError、stdClassはErrorで停止し、listenerとcurrent-action stackは進まない。`did_action`は非arrayのまま読める状態では0を返すが、stdClassではErrorになる。Action stackはcounter境界が成功してからpushする。結果のscopeは固定PHP 8.5.12／WP 7.1.3の観測値に限定し、別PHP版・handlerやproduction dispatcherの互換性を主張しない。同一hookの[Counter COW再入](evidence/wp-action-counter-cow-poc-v1.json)もlive／snapshot双方で固定WP／native／Wasm照合を完了し、7イベントと診断順、snapshot read、nested後と外側終了後のcounter値が一致した。最新Action bridge receipt、metrics、再検証コマンドは[Wasm profile案内](wasm/README.md)に記録する。

## 固定WordPress hook oracle

固定WordPressとPHPを準備済みのrepository rootで実行します。CIのnative runnerはこのWordPress oracleを自動実行しません。

```powershell
python -B experiments/php-compat-v06-probe/wp_hooks_oracle.py --record
python -B experiments/php-compat-v06-probe/wp_hooks_oracle.py --compare-bridges
python -B experiments/php-compat-v06-probe/wp_hooks_oracle.py --verify-only
```

[oracle receipt](evidence/wp-hooks-throwable-v1.json)は193入力を束ね、固定archive内の3ソースと実行に使うファイルの一致を確認します。PHPの実stdoutとreceipt、native／Wasm receiptのhashと3ケースの実観測値も照合します。継続のterminal stateはRust側の観測です。PHPはRequestFinished送信前に未来のFinished状態を報告しません。

Callable用の独立oracleは同じ共有PHP programを固定WPで実行し、87経路をnative／Wasmと直接比較します。[oracle receipt](evidence/wp-callable-api-v1.json)は193入力、実stdout、両bridge receiptのhashを結び付けます。

```powershell
python -B experiments/php-compat-v06-probe/wp_callable_oracle.py --record
python -B experiments/php-compat-v06-probe/wp_callable_oracle.py --compare-bridges
python -B experiments/php-compat-v06-probe/wp_callable_oracle.py --verify-only
```

## 証拠の範囲

native runnerは固定fixtureのPHPとRust continuation／dispatcherを往復します。別のWordPress oracleは元plugin.php／WP_Hookの限定ケースを実行します。WordPress bootstrap、production transport、DB、全WordPress runtimeの受入は対象外です。実PHP-Wasmの結果は別のWasm receiptで扱います。受信JSONは両peerで重複decoded keyを拒否し、PHPは `-n` で起動して利用者のini設定を読み込みません。throwable用の文字列IDは実際のThrowable objectを保持するPHP側request-local tableへ結び付きますが、RustへPHP heap pointerや本番用authority tokenを渡すwireではありません。通常bridgeではundeclaredな`php_handle` fieldをclosed schema violationとして拒否します。

publish scenarioはPHPが読み出しごとに発行した観測番号1〜10を用い、PHP→Rust 11 frame、Rust→PHP 10 frame、合計7,383 bytesを往復しました。Rustはrootでcount・array entryを4・7へpublishし、childで5・11へ更新します。root callbackはchild呼出し前にglobal objectを差し替え、nested PHP callbackはarray末尾を77にし、outer callbackはunknown propertyを`php-outer`に変えます。最後のmarker 104は現在のcount 5、array entries 11・77、replacement名から計算し、古いouter viewを再利用せずPHPの現heapで照合します。alias、property reference、array entry reference、copy-on-writeの`[2, 4]`維持も確認しました。stale observation、同じpublishのreplay、無効targetを含むbatchは代入前に拒否し、batch内の最初の有効writeも残らないことを確認します。

このPHP publish fixtureは閉じた対象と単純なproperty・array entryの代入を使います。Rust trackerはPHP発行の観測番号とpublishの順序を扱います。userland magic methodやdestructorを含む代入のatomicity、production PHP heap registry、token認証、汎用dirty検出には別途実装・検証が必要です。

固定PHP-WASIX artifactによる別の試行は [Wasm profile](wasm/README.md) に記録します。Windowsでのコンパイル失敗、Linuxでの固定oracle、nativeの双方向bridgeはそれぞれ別の証跡です。Linuxでは共通peerを使った実Wasmの正常系・例外系bridgeにも成功しました。WordPress本体、production handle、workerの終了・資源管理全体の受入は残っています。

## 共通ピア実装

Rust の JSONL protocol 処理と Continuation 操作は [共通peer](../../crates/recastpress-wp/examples/v06_bridge_peer/mod.rs) に切り出し、native CLI と PHP-Wasm host の両方が同じ `run_peer` を呼びます。native runner はプロセス間の PHP pipe を中継し、shared peer のフレーム検証と Continuation state machine を実行します。モジュール自体はデッドラインを設定せず、呼び出し側が deadline 付きストリームを提供します。
