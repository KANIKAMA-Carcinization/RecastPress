# native応答比較を持たないprivate WP worker候補

受入済みcontext workerを維持したまま、応答の実行とnative比較を分ける独立候補。元PHP-Wasm・元WP dispatcher／theme・所有db.php・Rust DB sessionを使い、固定home／missingを要求ごとにfresh PHP／WASI／actorで実行する。製品CLIや既存HTTP adapterへの接続は変更しない。

workerのprivate stdinは従来と同じcase・databaseを持つ閉じたwrapperで、schemaだけを`recastpress.php-wasix-wp-private-worker-input.v0.5`へ変える。成功時のprivate stdoutは`private_response`と7 fieldsの`private_contexts`を含む。元body／headersの保存は構造decoderで検査し、native応答ファイルはworkerで読まない。要求contextはこの有限profileの固定protocolとして検証する。

guestの元JSONを再帰的な重複キー拒否で読んでから、[独立codec](../wp-private-response-probe/README.ja.md)へ渡す。Valueへの変換で重複response fieldsが潰れた後に検査する経路にはしない。module／sourceの再照合、guest終了／stdout・stderr EOF、明示DB切断・actor終了、RPC終了、managed accessのpending bytes／handle／error回収を確認した後にだけprivate fieldsをcommitする。失敗時には両private fieldsを除去する。

`php_result`とcaptureの診断は未知のbodyやcontextを含めず、構造decoderのstatus／件数／bytes／SHAだけを返す。private出力自体もpublic応答ではない。公開許可・製品採用・native比較をworkerの成功とは扱わない。入力password／nonce／saltのrawとhexをprivate出力からも拒否する。

実行中のsource検証は、compileへ結び付く7 PHP fixtures・prepare script・元WP全catalog／manifestを対象にする。旧preparedの55 transitive inputsの一括読取りにはWP比較基準も含まれていたため、worker側のそのscanは外し、`compiled_fixtures_and_core_only`／`transitive_inputs_verified_by_worker:false`として記録する。全準備入力と比較基準の照合はbuild／controller側へ残す。managed moduleの由来を確かめる従来の検証と、WP応答の期待値比較は別である。

controllerは所有processの終了・captureを確認してprivate fieldsを受け取り、固定nativeの全応答と3 snapshotに一致してから元worker記録を公開証跡へ保存する。一致しない本文・contextはfailure artifactへ保存せず、サイズ／digestと限定した診断を残す。既存の31 fields・RPC・access・終了処理のvalidatorは、schema等3値とprivate fieldsの配置を明示した検証用viewへ適用する。元workerのverdictやJSONをそのviewで上書きしない。

```powershell
python -B experiments/php-wasix-wp-private-worker-probe/run_check.py --self-test
python -B experiments/php-wasix-wp-private-worker-probe/build_check.py
```

```sh
python3 experiments/php-wasix-wp-private-worker-probe/run_check.py --backend mysql --record
```

controllerの31 pure checksは、両DB・3ケースの合成view 6件、未知の完成body／contextや未完了actor等の拒否24件、nested duplicate拒否1件を確認する。実PHP-Wasm／DB／HTTPの実行結果とは区別する。WindowsではRust build／unitだけを検証し、実PHP-WasmはLinux CIで別途受け入れる。

[Windows build記録](evidence/windows-build.json)は59 unit／219 inputsで成功した。compile済みprivate codecと新workerのbuild・unitだけを実行し、source再照合と所有child回収も通過した。旧4 hostのreceipt・host／unit binaryは前後一致した。この結果で実PHP-WasmのLinux要求実行を代替しない。

依存lockは元contextの445 packagesにlocal codec一つだけを加え、registry packageの追加・version／source／checksumの変更はない。旧bootstrap・theme・context・HTTPのreceipt／host／unit binaryを前後照合し、新しいhost／unit名だけで共有build targetを使う。module cache・任意request／plugin・認証・書込み・全process資源予算や未知応答の公開条件は残件である。
