# compile済みPHPを再利用するRust HTTPアダプター

RustのHTTP入口から、1つのPHP-Wasm workerで匿名のhome／404／反復404を実行する。workerの起動時にWasmを1回compileし、各要求でPHP／WASI、DB actor／接続、RPC、FS、要求context、CGI captureを新設する。受付範囲は固定Hostと2種類のGETに絞る。

アダプターは固定workerの実行ファイルを同じCIジョブのbuild receiptと照合する。固定Wasmをハッシュし、workerのREADYが同じSHAを示してから `127.0.0.1:0` で待ち受ける。設定とDB資格情報はprivate stdinから受け取り、HTTP側の受付条件を満たした要求だけを連番付きJSONLに変換する。

要求ごとのACKを検証してから応答を組み立てる。3件目はworkerのstdinを閉じ、最終summary、両出力のEOF、正常終了とreap、worker実行ファイルの再照合を確認してから返す。HTTP切断後も監督タスクがworkerと同時実行枠を保持する。要求またはIPCが失敗した場合は、そのsessionを廃棄する。親のstdin終了による停止でも、workerの終了処理をHTTP接続のdrainより先に行う。

アダプターは応答の形式と実行完了を検証し、native WordPressとの本文・origin headers・context比較は外側のcontrollerで行う。サービスの記録は閉じたsummaryとprivate frameのサイズ／SHAに限定する。Linux CIは元installerとfresh readから始め、固定7種のHTTP事前拒否、全3応答の実ソケット照合、独立DB照会、所有processとDBの終了を検証する。

Windowsのlocked/offline buildは250入力・14 unit・58 packageで成立した。旧7実験のbuild receiptと成果物を前後で照合した。Rust unitには、HTTP側が応答待ちを手放した後のworker回収を確認するmockが含まれる。controllerの16件は合成記録による受入条件の検査である。

```powershell
python -B experiments/php-wasix-wp-session-http-probe/build_check.py --verify-only
python -B experiments/php-wasix-wp-session-http-probe/run_check.py --self-test
```

```sh
python3 -B experiments/php-wasix-wp-session-http-probe/run_check.py --backend mysql --record
```

実行時に必要な固定入力とbuild手順は[Linux workflow](../../.github/workflows/php-wasix-wp-session-http.yml)に記録する。実DBを使う検証はLinuxで行う。

## 両DBの実HTTP受入

source `d558a23`のLinux CIはMySQL／MariaDBとも成功した。同じworker PIDでcompileは起動時の1回、要求中は0回。匿名homeの200と404を2件実ソケットで取得し、全origin headersとHTMLをnative WordPressに照合した。homeは69,399 B、404は58,177 Bで、3要求の公開contextも一致した。固定7種の不正要求は400で拒否した。[Linux CI](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/42481084326)

3件目の応答前に最終summary、EOF、workerの正常終了とreapを確認した。アダプターも正常終了し、所有DBは正常停止・削除した。各DBの公開15記録と17工程を検証し、2,490件のソース対応と269個のGitソースを照合した。private ACKはサイズとSHAを記録し、本文・資格情報・生のIPCは所有capture内に保持した。[MySQL証跡](evidence/ci-42481084326-mysql/provenance.json)・[MariaDB証跡](evidence/ci-42481084326-mariadb/provenance.json)
