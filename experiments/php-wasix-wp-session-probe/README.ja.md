# compile1回・fresh WordPress要求3回のprivate session候補

受入済みprivate workerから、Engine／HashedModuleData／Moduleの寿命だけをセッションへ分離する。起動時に元moduleとsourceを確認して一度compileし、3要求では`Module::clone()`を渡す。PHP／WASI runtime・instance・SourceFs／context・DB actor／接続・RPC／managed access・captureは各要求で新設する。HTTP poolや応答cacheは追加しない。

起動完了のprivate READYはcompile1回とmodule SHAを返す。入力は24 KiB以内のJSONLで、閉じたschema・要求番号1～3・既存private worker入力を持つ。重複key、不正順序、nonce再利用、4番目のframe、余分なbyte、3件より前のEOFはセッションを失敗させる。3件完了後のEOFまでを正常終了の条件とし、失敗後は次の要求を実行しない。

各ACKはprivateな要求結果を返し、`module_compilations:0`／`compiled_module_reused:true`／`module_compiled_during_request:false`を明記する。旧workerのcompile1回という値に付け替えず、外側validatorの条件をゼロへ明示的に派生して終了処理・RPC・全native応答を比較する。原ACKを公開せず、検証したnative応答とcontext、閉じた診断項目だけを選ぶ。最終session summaryには要求packetやHTMLを含めない。

同一PIDでのcapture衝突を避け、PID＋起動時刻の所有ディレクトリと要求番号＋caseのファイル名を使う。Linuxではdirectory0700／file0600、既存fileを上書きしない。moduleはstartupのSHAへ要求前後・最終EOF後にも照合し、変更されたmoduleをcached codeのまま通さない。

親は所有processのregular-file captureを監督する。READY300秒・要求300秒・最終応答と終了の合計30秒を上限とし、期限／出力上限を超えた所有PIDをkill/reap5秒へ進める。未確認のchild handleをregistryに保持し、回収の確認前に完了としない。raw private ACK／入力は公開せず、サイズ／SHAだけを記録する。

```powershell
python -B experiments/php-wasix-wp-session-probe/run_check.py --self-test
python -B experiments/php-wasix-wp-session-probe/run_check.py --parent-self-test
python -B experiments/php-wasix-wp-session-probe/build_check.py
```

```sh
python3 experiments/php-wasix-wp-session-probe/run_check.py --backend mysql --record
```

[Windows build](evidence/windows-build.json)は241 inputs／64 unitで成功し、`--verify-only`も通過した。controllerの48 pure checksと所有Python mock3件も成功した。これらは実PHP-Wasm／DB／HTTPの受入を代替しない。旧6実験のreceipt・host／unit binaryを前後照合し、各source mapも再導出する。旧validator graphを再帰的にすべて実行したとは主張せず、非再帰検証の範囲をreceiptへ明記する。

Linux候補は元installer／fresh readの後、正常session1件のhome／missing／反復missingをnativeと比較する。正常sessionはcompile1・要求時compile0・3要求・所有PID1件として記録する。別sessionの不正初回番号／早期EOFも各compile1・guest要求0として確認し、正常sessionのcompile数へ混ぜない。

取り込み検証は14 JSON members／16 CI stagesを元Git sourceへ結び付ける。両DBの合成正常記録が通過し、compile・protocol・本文・終了処理・snapshot・API／ZIP／privacyの63変異を拒否した。途中失敗の保存済み要求にも公開項目・native・終了処理の検査を適用する。READYと最終summary、guest未実行の異常session stdoutは全bytesを再構成するが、元ACKと正常session全stdoutはprivate packetを含むためサイズ／SHAの観測だけを扱う。旧6 binaryをoffline importerで再読取りしたとは主張しない。

WASI shutdown_timeoutの帰還を同期CPU取消しや全資源の即時回収の証明とはしない。任意request／plugin、長時間pool、共有FS、応答cacheや製品CLIへの採用は残件である。
