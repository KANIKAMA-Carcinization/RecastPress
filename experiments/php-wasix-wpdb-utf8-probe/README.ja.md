# 有効UTF-8のPHP WASIX／Rust DB結合候補

受入済みnative基準の31観測・22 SQLを、元のwpdbとPHP WASIX／Rust DBで再現する独立候補。日本語・補助平面文字・quote／backslash／LIKE用文字・合成済みaccentと結合accentを扱う。無効UTF-8・任意charset・任意collation・全WordPress互換性を実証するものではない。

nativeの`corpus.php`全bytesと、full WordPress候補の4,096 operations版`adapter.php`全bytesをそのまま使う。元wpdbのprepare／metadata／文字検査／insert／update／get_results／deleteはPHPに残す。mbstringありのnative基準と同じ分岐を要求し、標準APIのtyped引数・戻り値・全SQL trace・公開stateを両DB基準へ一致させる。旧ASCII probeの条件や既存adapterを広げない。

公開元は[MySQL native基準](../wpdb-utf8-probe/evidence/windows-mysql-native.json)と[MariaDB native基準](../wpdb-utf8-probe/evidence/windows-mariadb-native.json)。前者の全JSON SHAは`c1d7a9e4d55c68b030830e1d8e883d52f42bae031df4bd3062f3bcf4d5370ada`、後者は`18c01e8105f7e41e407ebdd36f4e057233b3d808028d06b37168877e2ec2ba90`。31観測・全query trace・元WP source identity・入力bytes・query数・最終空tableは両基準間でも一致している。

guestには新所有5 PHPファイルを`/experiments/php-wasix-wpdb-utf8-probe/fixtures/`、元のWP8ファイルを`/.reference/wordpress-7.1.3/wp-includes/`へ有限readonly treeとして置く。private RPCは`/rp-wpdb/channel`。CGIはGET `/__rp_utf8.php?rp_request=1`、opaque query `rp_request=1`、argc 0・body 0・要求Content-Typeなしで起動し、JSON responseだけを返す。

immutable `/rp-utf8/context.json`はschema `recastpress.php-wasix-wpdb-utf8-context.v0.5`と48桁lowercase hex nonceの厳密2keysだけ。compact JSONを再encodeして照合し、重複key／非canonical表現を拒否する。DB接続先・資格情報・saltをguestへ渡さない。host入力・actorは既存のfull WordPress profileを読み取りで再利用する。nativeからの差分はCGI/context入口・owned include set・Wasm schemaとruntime／extension flagsだけを正確に導出する。

```powershell
python -B experiments/php-wasix-wpdb-utf8-probe/prepare.py
python -B experiments/php-wasix-wpdb-utf8-probe/prepare.py --verify-only
```

固定WordPress archiveが`.reference/wordpress-7.1.3.tar.gz`に必要。preparerは旧Wasm wpdbのarchive readerだけを読み取りで使い、新target内へ元8ファイルをreadonlyで置く。native PHP distributionやnative DBは不要で、Linuxでも同じsource preparationを行える。全13guest file identity・元source map・固定native基準とinclude setは`target/php-wasix-wpdb-utf8-probe/prepared.json`へ保存する。PHP、Wasm、DB、networkは実行しない。

Windowsの[build記録](evidence/windows-build.json)では35 pure unit・178 source inputs・locked/offline build・4所有childの正常回収が通過した。初回の32 unit／175 inputsの記録も保存している。旧bootstrapの31 unit／145 inputsとreceipt・binary・unit binaryも前後一致した。実PHP WasmはWindowsで実行しない。

```powershell
python -B experiments/php-wasix-wpdb-utf8-probe/build_check.py
```

Linux CIでは固定3patch moduleとhostをbuildし、digest固定の新規所有DBへ同じCRUD schemaを作って次を実行する。

```sh
python3 experiments/php-wasix-wpdb-utf8-probe/run_check.py --backend mysql --record
```

実Wasm／DBの受入は末尾のCI証跡に基づき、native基準やpure unitだけでRust DBとの一致を宣言しない。製品runtime、HTTP listener、TLS変更、資源上限の製品受入、raw非UTF-8 ERR、任意plugin対応は対象外のままである。

## platformごとのserver metadata

[固定metadata契約](server-metadata.json)はWindows nativeとLinux imageの実versionをそれぞれ全bytesで保存する。MySQLは両方`8.4.13`、MariaDBはWindowsが`11.8.9-MariaDB`、Linuxが`11.8.9-MariaDB-ubu2404`。suffixを削ったり、実結果を書き換えたりしない。固定Linux platform digest・image config digestと、受入済み旧Linux CRUD記録のpath／全SHAへ結び付ける。

Linux用の期待referenceは固定Windows native observationの新しいcloneを作り、`server_info_hex`だけを固定Linux値へ設定して導出する。比較する10項目は維持し、実行結果のversionも独立した`SELECT VERSION()`と完全一致を要求する。元native証拠・元Linux証拠と実結果のbytesは変更しない。31ケースの全値・SQL・公開stateとsession charset／collation／SQL modeは、そのまま比較する。

[初回CIのoffline監査](evidence/platform-metadata-audit-41632550317.json)は、固定native証拠・変更していないPHP source・公開RPC統計と正確なLinux versionから既知のPHP JSON／CGI wireを再構成し、公開captureのlength／SHAと照合した。MySQLの31,492 BとMariaDBの31,532 Bで一致し、sourceで決まるcompletion・統計・31ケース・22 SQLの差ではないことを確認した。HELLO replyも公開済みの固定metadataと有限候補でversion分類を確認し、connection identifier自体は記録しない。

```powershell
python -B scripts/verify_utf8_platform_metadata.py --record
```

監査はPHP／Wasm／DB／networkを実行しない。実guestのraw本文・wireを取得／公開せず、private request frame・nonceも再構成しない。初回の原判定（MySQL成功、MariaDB失敗）は維持し、監査で過去の失敗を成功へ変更しない。

## 実Wasmでの有効UTF-8受入

source `10eaa687600cc381f5015613e8ddefe359afd1ce`の[CI 41639151421](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/41639151421)は両DBで成功した。[MySQL証跡](evidence/ci-41639151421-mysql/provenance.json)・[MariaDB証跡](evidence/ci-41639151421-mariadb/provenance.json)を元のJSON bytesのまま保存し、各185 Git sources・1,066 source bindingsと照合して`overall_positive: true`を確認した。

31観測・22 SQLの全値／型／公開state、session条件と10比較項目が、明示的に導出したplatform別referenceと一致した。元のPHP corpus・adapter・Rust actor／RPC／collectorは変更していない。各52 RPC（hello 1・query 26・escape 24・close 1）、CGI status 200・正常JSON、両EOF・明示close・物理disconnect・actor exit/join・最終空table・所有containerの正常停止と削除を確認した。Linuxでも35 unit／178 inputsが通過し、旧hostの31 unit／145 inputsは維持された。

MySQLのCGI本文31,492 B、MariaDBの31,532 Bは初回公開captureのlength／SHAとも一致した。metadata契約は実結果を書き換えず、独立SQL versionとPHP versionの一致も要求する。無効UTF-8・text-only table／任意collation・byte上限境界・raw非UTF-8 ERR・HTTP listener・全wpdb互換は引き続き未受入である。

```powershell
python -B experiments/php-wasix-wpdb-utf8-probe/import_evidence.py --run 41639151421 --backend mysql --verify-only
python -B experiments/php-wasix-wpdb-utf8-probe/import_evidence.py --run 41639151421 --backend mariadb --verify-only
```

importerは別schemaのnested provenanceを流用せず、このprofileのflat module snapshot全15項目を同じjobのbuild／tool／patch／sourceへ照合する。取り込みはofflineで、元moduleやprivate RPC frameの再実行・再構成を行ったとは扱わない。
