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

## 実HTTP経由の両DB受入

source `3c4dbf1de2fc1cdb89e35bc3180ce15f31e1553d`の[Linux CI 41750117934](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/41750117934)はMySQL／MariaDBとも成功した。[MySQL証跡](evidence/ci-41750117934-mysql/provenance.json)と[MariaDB証跡](evidence/ci-41750117934-mariadb/provenance.json)は各11件の元JSONを変更せず保存し、各2,711 source bindings・215 Git source blobsを元commitへ照合した。HTTP hostは21 unit／206 inputs、context workerは58 unit／195 inputsである。

| 実HTTP要求 | 結果 | nativeとの照合 |
| --- | --- | --- |
| home | 200・origin 3 headers・69,399 B | 全bodyと同名headerの値・順序が一致 |
| missing | 404・origin 5 headers・58,177 B | 全bodyと同名headerの値・順序が一致 |
| missing反復 | fresh workerで404・58,177 B | 初回404と全応答が一致 |
| 固定7不正要求 | 全て400・固定拒否本文 | workerを起動せず拒否 |

各DBで元installer・fresh readも再通過し、HTTP要求3件ではfresh workerを各1回だけ起動した。3段階の公開context、home145／404各74 RPC、物理DB切断・actor終了・workerのEOF／reap、HTTP serverの正常終了／reap、所有DBの正常清掃、元sourceとmoduleの前後一致を確認した。clientは実socketからContent-LengthとDateを検査し、EOFまで完全応答を取得した。server内の応答構築記録だけで送信完了とは判定しない。

取り込み処理は固定`authorization`拒否ケースを認証headerと区別するが、名前の例外を適用する前に全観測項目・本文identity・固定要求identity・時刻窓を検証する。scalar等22種の不正形状と28種のcontract変異を拒否し、実証跡の両DB照合も通過した。公開証跡には生HTTP wireを保存しないため、そのdigestをofflineで再計算したとは主張しない。

今回の有限HTTP profileは製品CLIや任意request／認証／plugin、module cache、全process資源予算の受入にはしない。要求3件ではmoduleを3回compileする。次は新しい独立workerで完了済み応答のprivate IPCを抽出し、native期待値との比較を検証adapter側へ分ける。未知のHTMLの公開許可や製品HTTP経路への採用は、その後の明示した契約で確認する。
