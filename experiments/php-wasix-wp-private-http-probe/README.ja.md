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

## 比較を分離した実HTTPの両DB受入

source `25f93d75d9ed51f217604d56fcc01c4ed6925e6d`の[Linux CI 42027597880](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/42027597880)はMySQL／MariaDBとも成功した。[MySQL証跡](evidence/ci-42027597880-mysql/provenance.json)と[MariaDB証跡](evidence/ci-42027597880-mariadb/provenance.json)は、各13件の原JSONとAPI metadataを変更せず保存し、各4,344 source bindings・244 Git source blobsを元commitへ照合した。

| 実HTTP要求 | 両DBの結果 |
| --- | --- |
| home | 200・origin 3 headers・69,399 Bがnativeと一致 |
| missing | 404・origin 5 headers・58,177 Bがnativeと一致 |
| missing反復 | fresh workerで初回404の全応答と一致 |
| 固定7不正要求 | 全て400・固定本文で拒否、workerを起動しない |

元installer・fresh readも再通過した。HTTP要求3件は別workerで実行し、home145／404各74 RPC、context identity、PHP／WASI終了・CGI EOF、物理DB切断・actor終了を確認した。HTTP serverと全3 workerを正常に回収し、強制清掃なしで所有containerを正常停止・削除した。旧5 hostの成果物、元WP source、同じ3patch moduleの前後一致、12 unit／232 inputs、observer47 checks・controller40 checksも通過した。

サービスの`native_comparison_performed:false`と`source_files_rehashed_by_service:false`を保持し、workerの公開許可flagもfalseのままである。HTTP側だけが受付済みの匿名fixture要求を許可し、実workerの完成応答を返した。比較はcontrollerで行い、公開記録はsummary・native-normalized origin fields・bytes／SHAだけとする。サービスのsummary JSONは全byte identityへ照合するが、元private worker packet、HTTP wire、nonce／RPC bytesをofflineで再構成したという主張ではない。

要求3件ではcompile3回であり、module共有はまだ行っていない。次は1所有workerでcompile済みmoduleだけを再利用し、WASI／PHP状態・DB actor・RPC／FS／captureは都度新設する有限実験へ進める。任意request／theme／plugin、認証・書込み、全process資源予算、portable配布や製品CLIへの接続は残件である。
