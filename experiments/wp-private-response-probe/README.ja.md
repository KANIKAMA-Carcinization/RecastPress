# 非公開WP応答の独立デコード

既存workerの5 fields（status・headers_hex・body_bytes・body_hex・body_sha256）を、native期待値を読まずに検査する独立library。既存のcontext workerとHTTP候補は変更しない。これは次のworker分離に先立つ応答処理の抽出であり、PHP-Wasmの新worker実行や製品への組み込みを完了したものではない。

`decode()`は未知のbodyも非公開データとして保持する。`PrivateWpResponse`と`PrivateHeader`はDebug／Display／Serializeを実装せず、元header行とparsed fields、bodyは明示的な借用accessorだけで取得する。通常診断の`SafeSummary`にはstatus・header数・body bytes／SHA・304 representation lengthだけを含める。固定error codeにも入力やparserの詳細を混ぜない。

JSONの重複・未知field・型不一致を拒否し、wire上限、body 1 MiB、lowercase hex、実長と実SHA、header数と総16 KiBを確認する。元のCGI parserを読み取り再利用し、合成Statusと最終空行もheader予算に含める。元headerは最大63 fieldsとなる。bodyや同名headerの順序を変更しない。204／205／304の本文規則も元parserに従う。304のrepresentation lengthは未照合の値として保持し、HTTP送信前に選択した表現との照合が必要である。

デコード成功だけでは、実行成功・EOF・process回収・DB切断・source不変や公開許可を証明しない。今後のworker controllerがそれらを確認し、HTTP adapterが公開条件とtransport処理を決める。未知の応答を受け取れることと、未知の応答を公開できることを分ける。

## 検証

```powershell
python -B experiments/wp-private-response-probe/check.py
python -B experiments/wp-private-response-probe/check.py --verify-only
```

[Windows記録](evidence/windows.json)は16 unit（独自7＋既存CGI9）、両DBの保存済みnative／Wasm応答12件、未知のprivate応答1件・不正private入力2件で成功した。CLIはprivate本文やheaderを出力せず、summaryか固定errorだけを返す。12件は受入済みHTTP実行の記録を使ったcodec再生であり、今回PHP／Wasm／DB／HTTPを起動した意味ではない。

依存22 packagesは既存lockとversion・checksumを照合し、専用targetでlocked/offline buildする。旧bootstrap／theme／context／HTTPのWindows receiptとhost／unit binaryは前後一致した。旧hostの全入力をこの実験で再検証したという主張ではない。CIのLinux結果は別の記録として確認する。

初回は16 unitと12応答の試験後に、編集中のlibrary source変更を最終照合で検知した。[元の失敗記録](evidence/windows-source-changed-first.json)は変更せず保持し、凍結後の受入記録とは区別する。

次はこのlibraryを使う独立workerでprivateな完了応答を返し、native比較を検証adapterへ残す。既存HTTPの固定期待値gateは、その接続試験が成立するまで維持する。
