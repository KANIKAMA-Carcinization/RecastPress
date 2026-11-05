# native応答比較を持たないprivate WP worker候補

受入済みcontext workerを維持したまま、応答の実行とnative比較を分ける独立候補。元PHP-Wasm・元WP dispatcher／theme・所有db.php・Rust DB sessionを使い、固定home／missingを要求ごとにfresh PHP／WASI／actorで実行する。製品CLIや既存HTTP adapterへの接続は変更しない。

workerのprivate stdinは従来と同じcase・databaseを持つ閉じたwrapperで、schemaだけを`recastpress.php-wasix-wp-private-worker-input.v0.5`へ変える。成功時のprivate stdoutは`private_response`と7 fieldsの`private_contexts`を含む。元body／headersの保存は構造decoderで検査し、native応答ファイルはworkerで読まない。要求contextはこの有限profileの固定protocolとして検証する。

guestの元JSONを再帰的な重複キー拒否で読んでから、[独立codec](../wp-private-response-probe/README.ja.md)へ渡す。Valueへの変換で重複response fieldsが潰れた後に検査する経路にはしない。module／sourceの再照合、guest終了／stdout・stderr EOF、明示DB切断・actor終了、RPC終了、managed accessのpending bytes／handle／error回収を確認した後にだけprivate fieldsをcommitする。失敗時には両private fieldsを除去する。

`php_result`とcaptureの診断は未知のbodyやcontextを含めず、構造decoderのstatus／件数／bytes／SHAだけを返す。private出力自体もpublic応答ではない。公開許可・製品採用・native比較をworkerの成功とは扱わない。入力password／nonce／saltのrawとhexをprivate出力からも拒否する。

実行中のsource検証は、compileへ結び付く7 PHP fixtures・prepare script・元WP全catalog／manifestを対象にする。旧preparedの55 transitive inputsの一括読取りにはWP比較基準も含まれていたため、worker側のそのscanは外し、`compiled_fixtures_and_core_only`／`transitive_inputs_verified_by_worker:false`として記録する。全準備入力と比較基準の照合はbuild／controller側へ残す。managed moduleの由来を確かめる従来の検証と、WP応答の期待値比較は別である。

controllerは所有processの終了・captureを確認してprivate fieldsを受け取り、固定nativeの全応答と3 snapshotに一致してから、検証済みの項目だけを選んだ記録を公開証跡へ保存する。未知の追加fieldや不要な内部診断はコピーしない。元schema／verdictは保持し、`worker_fields_projected:true`／`original_worker_json_published:false`を明記する。一致しない本文・contextはfailure artifactへ保存せず、サイズ／digestと限定した診断を残す。既存の31 fields・RPC・access・終了処理のvalidatorは、schema等3値とprivate fieldsの配置を明示した検証用viewへ適用する。元workerのprivate stdoutは公開せず、その全bytesのサイズ／SHAだけを記録する。

```powershell
python -B experiments/php-wasix-wp-private-worker-probe/run_check.py --self-test
python -B experiments/php-wasix-wp-private-worker-probe/build_check.py
```

```sh
python3 experiments/php-wasix-wp-private-worker-probe/run_check.py --backend mysql --record
```

controllerの37 pure checksは、両DB・3ケースの合成view 6件、未知fieldの公開除外6件、未知の完成body／contextや未完了actor等の拒否24件、nested duplicate拒否1件を確認する。公開除外ではtop・PHP診断・capture・RPC・access・preparedへ同時に未知値を加えても、raw／hexのどちらも公開記録へ出ないことを確かめる。実PHP-Wasm／DB／HTTPの実行結果とは区別する。WindowsではRust build／unitだけを検証し、実PHP-WasmはLinux CIで別途受け入れる。

[Windows build記録](evidence/windows-build.json)は59 unit／219 inputsで成功した。compile済みprivate codecと新workerのbuild・unitだけを実行し、source再照合と所有child回収も通過した。旧4 hostのreceipt・host／unit binaryは前後一致した。この結果で実PHP-WasmのLinux要求実行を代替しない。

初回source `87f29f7`のCI 41769886268は、controllerが未知の追加fieldを丸ごと記録し得ることをレビューで確認し、実WP要求の試験前に停止した。[MySQL途中証跡](evidence/ci-41769886268-mysql/provenance.json)と[MariaDB途中証跡](evidence/ci-41769886268-mariadb/provenance.json)は各3件の原JSON・37 source bindingsを照合して保持する。元のrun／job判定はcancelledのままで、要求実行の受入ではない。修正版は公開項目の選択と37 pure checksを加え、別のCI sourceとして検証する。

取り込み処理も、後続段階の失敗に先立つ成功要求を含め、元sourceの公開処理とnative／終了処理の検査を通った記録だけを許可する。固定4 fieldsの検証adapter説明はデータを持たないmetadataとして区別する。両DBの合成正常記録が通過し、archive／API・部分失敗・非公開データ・metadataの型等84変異を拒否した。元の取消し証跡は補強後の`--verify-only`でも再確認し、上書きしない。

## 実PHP-Wasmでの両DB受入

source `209090a85d19bd3763569f5e18026f573985a7ba`の[Linux CI 41771428584](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/41771428584)はMySQL／MariaDBとも成功した。2026-11-04の実行結果を2026-11-05に照合し、[MySQL証跡](evidence/ci-41771428584-mysql/provenance.json)と[MariaDB証跡](evidence/ci-41771428584-mariadb/provenance.json)を保存した。各12件の原JSONを変更せず、各2,598 source bindings・230 Git source blobsを元commitへ結び付けた。

| 要求 | 両DBの結果 | RPC |
| --- | --- | --- |
| home | 200・全3 headers・69,399 Bがnativeと一致 | 145 |
| missing | 404・全5 headers・58,177 Bがnativeと一致 | 74 |
| missing反復 | fresh workerで初回404の全応答と一致 | 74 |

元installer・fresh readも再通過し、全5実行、公開contextの3 snapshot、59 unit／219 inputs、37 controller checksを確認した。旧4 hostのreceipt／binary不変、元WP source・3patch moduleの前後一致、物理DB切断・actor終了・CGI EOF／所有process回収・所有containerの正常清掃も通過した。要求3件はmoduleを3回compileし、共有compileや製品性能の受入にはしない。

workerの`native_comparison_performed`はfalseのまま、比較はcontrollerだけで行った。公開記録は検証済み項目の選択結果であり、元private stdoutは公開せず、そのサイズ／SHAを実行時の観測として保持する。射影から元stdoutやprivate nonce／RPC bytesをofflineで再構成したという主張ではない。

次はこのprivate workerを使う独立Rust HTTP adapterへ接続し、サービスの実行経路からnative応答比較を外す。既存の有限なHost／method／route／bodyの受付条件と終了監督を保ち、公開許可はHTTP側で明示する。今回の新worker試験自体はHTTP listener・任意request／plugin・製品CLIの受入ではない。

依存lockは元contextの445 packagesにlocal codec一つだけを加え、registry packageの追加・version／source／checksumの変更はない。旧bootstrap・theme・context・HTTPのreceipt／host／unit binaryを前後照合し、新しいhost／unit名だけで共有build targetを使う。module cache・任意request／plugin・認証・書込み・全process資源予算や未知応答の公開条件は残件である。
