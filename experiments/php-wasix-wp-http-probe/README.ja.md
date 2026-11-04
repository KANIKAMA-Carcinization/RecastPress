# Rust HTTPから元WordPressを実行する結合候補

固定loopback HTTP要求を受け、受入済みcontext workerを要求ごとに起動する独立候補。workerは元PHP Wasm・元WP dispatcher／theme・Rust DBで描画し、完全応答と終了処理を確認する。HTTP側は実worker結果のstatus・origin headers・bodyを取り出して返す。比較用JSON envelope、DB設定、RPC nonceを公開HTTP応答へ流さない。既存のbundle配信やnative PHPへ代替する経路はない。

公開profileはHost `rp-fixture.invalid`のGET `/` と`/?p=2147483647`、本文0に限定する。固定7不正要求をworker起動前に拒否し、home・404・404再実行の3件を実行する。接続先と資格情報は所有processのstdinの1 JSON行だけから受け取り、各workerのnonceはRustで新規生成する。公開入力で接続先・worker binary・任意SQLを指定できない。

workerは固定Linux build receipt・195 source inputs・58 unit・binary SHAへ結び付け、起動前後に照合する。HTTP側は既存のnative response/context contractとCGI parserを読み取りimportする。origin header名の大小文字とCGI OWSはparserの規則に従い、同名fieldの値と順序を保持する。HTTP transportだけがContent-Length、Date、Connectionを付け、observerは全body bytesとorigin fieldsをnative基準へ照合する。headerの全体順序をHTTPでの意味の一致条件とは扱わない。

private stdinのEOFをshutdown capabilityとし、公開stop endpointは作らない。workerの入出力は取得中に上限を守り、300秒期限と5秒のkill/reap処理を持つ。clientが切断してもworker監督taskを継続し、graceful shutdownはその終了を待つ。途中のguest出力や失敗JSONはHTTPへ送らず、固定error responseだけを返す。server側のresponse構築記録と、client側のEOF／全wire取得を別々に記録する。

[Windows build](evidence/windows-build.json)は21 unit／206 inputsで通過した。HTTP observerの47 pure checksも成功した。mock childによる終了／超過／期限／panic／待ち手取消しの検証を含むが、固定PHP Wasmや実HTTPのWindows受入を意味しない。旧bootstrap31／145、theme53／172、context58／195のreceipt・host／unit binaryは前後一致した。

```powershell
python -B experiments/php-wasix-wp-http-probe/build_check.py
```

```sh
python3 experiments/php-wasix-wp-http-probe/run_check.py --backend mysql --record
```

Linuxの実行結果と元sourceの照合は別途必要である。今回の有限HTTP profileは製品CLIや任意request／認証／plugin、module cache、全process資源予算の受入にはしない。
