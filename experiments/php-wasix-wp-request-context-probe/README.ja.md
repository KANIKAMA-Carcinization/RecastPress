# clean home／404 contextのPHP WASIX結合候補

受入済みnative clean context基準のhomeと、存在しないpost `/?p=2147483647`の404をPHP WASIX／Rust DBへ結合する独立候補。元のPHP module・WP core・themeと、既存home adapterの全bytesは維持する。実Wasm／DBの成功は別の実行で確認し、source preparationやnative基準だけでは宣言しない。

新sourceの7 PHPファイルを、旧guest fixture mount `/experiments/php-wasix-wp-request-probe/fixtures`へ配置する。db.phpだけは`/wordpress/wp-content/db.php`へoverlayする。これにより既存managed access policy・readonly full source catalogを同じguest pathで再利用できる。元3,782 coreファイル・467 directories・110,077,999 bytesのinventoryと、所有dropin 1本を別々に前後確認する。

guest contextはimmutable `/rp-request/context.json`の厳密5keys、schema `recastpress.php-wasix-wp-request-context-guest.v0.5`、mode `home`、case `home`／`missing`、nonce、8 saltsだけである。実DB接続先・資格情報・host source rootは含めない。4 KiB上限、saltとnonceの48桁hex、canonical JSONと重複keyを検証する。hostの外側入力は固定3keysのschema／case／既存DB inputで、既存actorのDB input自体を変更しない。

非公開CGI入口はGET `/__rp_request.php?rp_request=1`、body 0、要求Content-Typeなし。nativeの`rp_context_public()`と`rp_context_snapshot()`は全bytes一致で継承し、GET／POST／COOKIE／REQUEST、SERVER、getenvのmethod／URI／query、private pathとCONTENT／authを公開要求へ戻す。homeのgetenv queryは未設定、missingは公開p queryだけ。php://inputの空bytes、JSON判定false、display_errors 1を検証し、bootstrap前の元JSON判定関数未定義はnullとして区別する。WP load順は変えず、load.phpをpreloadしない。

INI選択はguestの`-n`と固定host設定を検証するため、nativeの「所有INI fileを読んだ」というboolだけは異なる。display_errors 1、log_errors 0、mail無効、URL fopen無効、argc生成無効、元expose_php 1を要求する。nativeからの他の差分はguest context reader／verify、guest rootとdummy DB constants、所有dropin、Rust実行flag、閉じた診断allowlistだけを明示的に導出する。nativeのdispatcher・template loader・HTML捕捉blockは全bytes維持し、status・全headers・全HTML bytesと3段階のpublic snapshotを固定native証拠へ照合する。

```powershell
python -B experiments/php-wasix-wp-request-context-probe/prepare.py
python -B experiments/php-wasix-wp-request-context-probe/prepare.py --verify-only
```

preparerは既存Wasm homeのportable source metadataを読み取る。native contextのWindows専用inputs／package verifierは呼ばず、追跡済みPHP sourceと固定native証拠だけを読む。元full WP sourceとmanifestを用意した後ならLinuxでも同じ処理を行える。native binaries、PHP、Wasm、DB、networkを実行しない。

製品runtime、任意theme／plugin、HTTP listener、キャッシュ、raw非UTF-8 ERRやnative metadata全互換、全資源予算の製品受入は対象外のままである。旧仕様・旧証拠・旧実装は書き換えない。

## buildと実行の検証

source `f9f1f61`の[Windows build](evidence/windows-build.json)は58 unit／195 inputs・locked/offline buildが成功した。55 prepared inputs／7 fixturesも固定し、元起動hostの31 unit／145 inputsと元theme hostの53 unit／172 inputsについて、receipt・host binary・unit binaryの前後一致を確認した。unitには既存のplain WATによるwrite／EOF回帰も含む。固定PHP WasmのWindows実行を実証した記録ではない。

```powershell
python -B experiments/php-wasix-wp-request-context-probe/build_check.py
```

Linuxでは元installer・fresh readの後、home・missing・missing-repeatをそれぞれ新規所有host processで実行する。各hostでmoduleを1回compileし、PHP／WASI／actorをfreshにするため、要求3件のmodule compile数も3となる。module cacheやhost process間共有の実証にはしない。

```sh
python3 experiments/php-wasix-wp-request-context-probe/run_check.py --backend mysql --record
```

## 同じ公開contextと404の実Wasm受入

source `f9f1f6141a5ffe84097d4053dce40d3294e74e01`の[CI 41742369145](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/41742369145)はMySQL・MariaDBとも成功した。[MySQL証跡](evidence/ci-41742369145-mysql/provenance.json)と[MariaDB証跡](evidence/ci-41742369145-mariadb/provenance.json)の各10 public JSONを全bytesのまま保存し、各203 Git sources／1,883 source bindingsへ照合して`overall_positive: true`を確認した。

元installer・fresh readと、home・missing・missing-repeatの全5実行が通過した。新しい要求では元19項目・要求応答5項目・context関連7項目を比較し、公開contextの全3 snapshotもnativeと完全一致した。homeのCGI内応答はstatus200・全3 headers・HTML69,399 B、missingはstatus404・全5 headers・HTML58,177 B。両DBともhome 145 RPC（hello1／query50／escape93／close1）、missingと再実行は各74 RPC（hello1／query28／escape44／close1）で、反復404も全応答が一致した。HTML・header・query・Unicodeの正規化は加えていない。

元`wp_is_json_request()`は起動後・描画後にfalse、起動前は未定義／nullと確認した。display_errors 1・body 0・homeの環境query未設定／missingの公開queryのみは全3段階で一致した。両EOF・物理disconnect・actor exit/join・source/module不変、独立SQLによる不在post／公開DB状態／日時／autoload、所有containerの正常停止と削除も通過した。Linuxでも新58 unit／195 inputs、旧31／145と53／172の不変性を確認した。

```powershell
python -B experiments/php-wasix-wp-request-context-probe/import_evidence.py --run 41742369145 --backend mysql --verify-only
python -B experiments/php-wasix-wp-request-context-probe/import_evidence.py --run 41742369145 --backend mariadb --verify-only
```

取り込みは公開host JSONの再serializationとsource／tool／patch／module metadataを照合する。private nonce・RPC query/reply・元CGI本文のdigestをofflineで再計算したという主張ではない。製品runtime採用、HTTP listener、任意request／theme／plugin、共有module cacheと全資源予算は次の受入条件として残る。
