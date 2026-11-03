# private contextをHTTP入力へ混ぜないnative要求基準

元のWordPress全体とnative PHP CGIで、匿名home `/` と存在しないpostの`/?p=2147483647`を描画する独立基準。旧証拠・元WP source・旧helperは変更しない。固定post IDの不存在を所有DBから独立確認し、canonical redirectや元のroutingを無効化しない。missingを別のfresh CGIで再実行し、status・全header・全HTML bytesの一致を要求する。

新規所有UUIDディレクトリへcase wrapper・bootstrap・context helperを全bytes一致でコピーし、同じディレクトリの`context.json`を固定basenameで読む。private JSONには限定DB資格情報・salt・source rootがあるが、stdin／php://input／HTTP body／環境selectorへ渡さない。request bodyは実際に0 bytes、CGI起動はGET・opaque query `rp_request=1`・Content-Typeなし。5所有ファイル（PHP3、context JSON、INI）を前後照合し、既知ファイルだけを個別に削除する。

PHPRCで専用INIファイルを明示し、追加INI scanを空にする。native CGIの`-n`／`-d`引数には依存しない。mysqli・mail無効・URL fopen無効・argc生成無効に加え、`display_errors=1`、`log_errors=0`を明示する。実INI一致と設定をPHPでも確認し、WPの元error handlerやwarningを抑制して合わせない。所有JSON wrapperは`header('Content-Type: application/json', true, 200)`で設定する。

WPの起動前にopaque query・private path環境・CONTENT／authを除去し、`$_SERVER`、GET、POST、COOKIE、REQUESTをcaseの公開要求へ戻す。DOCUMENT_ROOTのprivate getenv値も残さない。前後のpresenceとnullを区別したsnapshot、空のphp://input、display_errorsを照合する。元`wp_is_json_request()`は元load順で利用可能になったbootstrap後・dispatch後だけ呼ぶ。bootstrap前は関数未定義とnullを明示し、load.phpをpreloadしない。

所有DBは旧home／full-bootstrap helperを固定SHAで読み取り再利用し、最初に元native install/readを再現する。固定日時をseedした後にhome／missing／missing-repeatを実行し、各要求後に元source inventory、public DB状態、日時、autoload集計を確認する。固定PHP全78ファイルと元archiveも前後確認する。正常admin shutdown、全DB子と全3 CGIのreap、全scratch清掃を成功条件とする。公開記録に資格情報、実context本文、INI本文、絶対host pathを含めない。

```powershell
python -B experiments/wp-request-context-probe/prepare.py
python -B experiments/wp-request-context-probe/database_check.py --backend mysql --record
python -B experiments/wp-request-context-probe/database_check.py --backend mariadb --record
```

prepareはsourceと導出条件を確認するだけでPHP・DB・networkを実行しない。CGI captureは固定startup helperの関数から期限だけ180秒へ正確に導出し、独立registry、空stdin、取得中のstdout／stderr各3 MiB上限、実EOFとreapを使用する。DBとCGIの実行はcheckだけが行う。Wasm・Rust DB・HTTP listener・製品採用・任意plugin互換の実証ではない。

## 初回実測と未受入の条件

初回の両DB記録は元判定`passed: true`のまま保存する。homeはstatus200・3 headers・69,399 B、missingはstatus404・5 headers・58,177 Bで、反復404の全responseと両DB間の全responseも一致した。全CGI回収・正常DB停止・元source不変も確認できた。

ただし、追加レビューでhomeの`getenv('QUERY_STRING')`に入口用`rp_request=1`が残る除去漏れを確認した。初回snapshotは`$_SERVER`のqueryだけを検証しており、C環境のqueryを検証していない。初回の成功を要求context全体の受入と扱わず、元記録・元sourceを保持したうえで、環境queryの除去と明示照合を追加して再実行する。

## query分離後の両DB基準

source `afe8781`の[MySQL](evidence/windows-mysql-native.json)と[MariaDB](evidence/windows-mariadb-native.json)は両方成功した。homeの環境queryは未設定、missingは`p=2147483647`だけとなり、起動前・起動後・描画後の3 snapshotで完全一致した。WP起動後の元`wp_is_json_request()`はfalse、`display_errors`は1、実CGI本文と`php://input`は0 bytesを維持した。

homeはstatus200・全3 headers・HTML69,399 B（SHA `ef43f26984536cae43c4e703edef98dbaaf2bca79cc972a4373bf27f01cabdd1`）、missingはstatus404・全5 headers・HTML58,177 B（SHA `d2bda2fdf6bf12e075689fd3af5e87eba2ede1ea953d9f0d7085a63fc11f60bc`）。両DB間、404のfresh再実行、修正前後で全responseが一致し、homeは既存の元テーマ基準とも全response一致した。各DBで3 CGIのEOFとreap、各5 scratch filesの個別清掃、DB正常停止・全child回収、元WP全sourceとPHP全78 filesの前後一致を確認した。

初回の元記録は[MySQL](evidence/windows-mysql-native-before-5540cb224b0fc5e8.json)・[MariaDB](evidence/windows-mariadb-native-before-e0a6a8d7d5fab227.json)へ全bytesのまま保存している。今回成立したのはこの有限native要求基準であり、同じcontext／404のWasm実行や実HTTP listenerの受入は次段階である。

[独立offline監査](evidence/clean-context-offline-audit.json)では、40入力を35 Git blobs（固定source `afe8781`）と5 reference metadata pins（固定初回証跡）に分けて照合した。初回backupの全bytes、全6要求の公開JSON／CGI wireの再構成、厳密snapshot、元homeとの全応答一致と正常清掃の記録も照合した。13件のmemory内改変を拒否し、再監査の出力もbyte一致した。private context・INI本文や、native binary／archiveをofflineで再検証したという主張は含まない。

```powershell
python -B scripts/verify_request_context_native.py --record
```
