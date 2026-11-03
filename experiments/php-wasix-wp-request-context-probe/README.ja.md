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

[CI 41742369145](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/41742369145)で両DBの実行結果を検証する。CI成功と元sourceへの証跡照合が完了するまで、同じcontext／404のWasm受入は未完了である。
