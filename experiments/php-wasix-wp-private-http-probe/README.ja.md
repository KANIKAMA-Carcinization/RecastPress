# private workerを使うRust HTTP候補

受入済みの[private WP worker](../php-wasix-wp-private-worker-probe/README.ja.md)を要求ごとに起動する独立HTTP候補。元PHP-Wasm・WordPress dispatcher／theme・Rust DBで完成した応答を、構造decoderで検査して返す。HTTPサービスにWP応答のnative期待値の読取りや比較処理を持たせない。既存HTTP候補と製品CLIは維持する。

受付はloopbackのHTTP/1.1、Host `rp-fixture.invalid`、GET `/`／`/?p=2147483647`、本文0に限定する。method・Host・route・header・本文を確認してからprivateなAdmissionを発行し、応答の公開にはそのAdmissionと正常終了したworkerを必要とする。Cookie／Authorization等の固定7不正要求は実行前に拒否する。workerの`publication_authorized:false`は変更せず、HTTP側がこの有限な匿名fixture要求だけを許可する。

workerのschema・case・PHP／WASI終了・物理DB切断・actor終了・sealed RPC／managed access・固定protocolの3段階contextを確認する。[応答codec](../wp-private-response-probe/README.ja.md)で検査した実bodyと個別headerを使い、同名headerの値と順序を保つ。HTTP transportのContent-LengthとConnectionは完成bodyから決める。途中出力や失敗JSONを送信せず、失敗時は固定502応答を返す。

private stdoutは再帰的に重複キーを拒否して読む。元packet、HTML／header hex、context、操作行をサービスの最終JSONへコピーしない。閉じたworker_summaryには完了flag、件数、bytes／SHAとcontext identityだけを残す。検証controllerは実socketの全応答をnative基準へ照合し、summary全体のfield・型・値を検査してから公開証跡へ保存する。

workerのLinux receiptはcompileに結び付くWindows anchorの219入力／59 unitへ照合し、実worker binaryのSHAとreceiptを各起動の前後で確認する。219入力には比較用証跡も含まれるため、サービスから全sourceを再読取りする処理は置かず、`source_files_rehashed_by_service:false`を明記する。全入力の照合はbuild／controllerが担い、worker自身のcompiled PHP／WP source・module確認は維持する。従来のPHP module由来確認と、WP応答期待値の比較は区別する。

所有childのstdout／stderrを取得中に制限し、300秒期限・5秒kill/reapを維持する。client切断後も監督taskがpermitを保持して回収を続け、graceful shutdownはその終了を待つ。private stdinのEOFを終了capabilityとし、公開stop endpointは作らない。実行は3件・同時1件で、各nonceをRustで新規生成する。

```powershell
python -B experiments/php-wasix-wp-private-http-probe/run_check.py --self-test
python -B experiments/php-wasix-wp-private-http-probe/build_check.py
```

```sh
python3 experiments/php-wasix-wp-private-http-probe/run_check.py --backend mysql --record
```

controllerの40 pure checksは両DBの保存済みworker記録を使う合成正常2件と、不正な公開許可・未回収・未知field・context／bodyの不一致等38変異を確認する。Rust側の単体検証・HTTP observerの47 pure checks・Linuxでの実HTTP／PHP-Wasm／DB実行は別の受入として扱う。

[Windows build](evidence/windows-build.json)は232 inputs／12 unitで成功し、`--verify-only`でも再確認した。受付・Admission・未知のprivate応答・summaryの非公開境界・所有childの回収等を含むRust-only検証であり、Windowsで実PHP-WasmやHTTPを実行した結果ではない。旧bootstrap／theme／context／HTTP／private workerのreceipt・host／unit binaryは前後一致した。

取り込み検証は、成功・部分失敗の両方でサービス記録を元sourceの閉じたvalidatorへ通す。両DBの合成正常記録、13 JSON members／15 CI stagesを照合し、schema欠落・未知payload・型／終了状態・archive／API等78変異を拒否した。summaryだけを持つサービスstdoutは全JSON bytesへ結び付け、元private worker stdoutはサイズ／SHAの観測として扱う。

module共有、任意request／theme／plugin、認証・書込み、全process資源予算、portable配布や製品CLIへの接続は残件である。
