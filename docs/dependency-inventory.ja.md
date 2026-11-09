# 固定依存の宣言台帳

## 2026-11-10：v0.6系列の宣言台帳を再生成

v0.6実装開始に伴い、現在のCargo metadataをoffline・lockedで取得して `compatibility/dependency-inventory.json` を再生成し、`--check` の一致を確認した。本体167／binlog188／unique218。RecastPressのlocal package版は0.6.0へ移行し、既存のregistry packageの版・source・checksumは変更していない。

これは依存の宣言台帳の再観測である。保存済みのlicense／notice実ファイル観測、PHP静的依存・配布binaryの対応、追加のPHP-Wasm host試作の依存監査を一括して完了したものではない。以下はそれぞれの日付の履歴として維持する。

## 2026-10-30時点：mainのserde dev-edge追加後は再観測待ち

2026-10-30現在、prepared Cookie照合の専用example用にWPへ既存 `serde` のdev-dependency edgeを追加し、root lock／WP manifestが変わった。`target/auth-cookie-verify-adoption-audit/lock-delta.json` は163 registry version／source／checksum tupleの追加・変更０と、WP recordのserde edgeだけの差を記録する。これはlock差分監査であり、現在のnormal dependency graphや配布binary構成を実測した結果ではない。

保存済み `compatibility/dependency-inventory.json` は本体167／binlog188／unique218の過去記録だが、そのinput hashは旧root lock／旧WP manifestを指す。license file観測JSONも旧入力に束縛されており、今のsourceでcurrent成功とは呼ばない。新パッケージtupleが無いことを理由に、古いinput hashを手で差し替えない。今回のfocused Rust21／Python12／fmt／WP Clippy成功は、台帳・archive実再観測の成功ではない。

ユーザー指示の中断後、実行再開が許可されたら `dependency_inventory.py`、`dependency_license_evidence.py` を実metadata／同一archiveから生成し、両 `--check` で確認する。これらの生成はsource freeze gateの開始前に済ませる。現在はどちらも未再実行であり、normal graph／pure profile／法律・配布義務の採否もこの差分から決定しない。

## 以前の実観測と仕組み

宣言台帳・実ファイル観測は2026-10-27時点。R03に向けた配布準備用の機械的な記録で、ライセンス選択や配布条件の監査完了を示すものではない。

```powershell
python scripts/dependency_inventory.py
python scripts/dependency_inventory.py --check
```

本体workspaceと独立したbinlog試作でCargo metadataをoffline・lockedで取得し、それぞれのCargo.lockの名前・版・sourceと照合する。結果はcompatibility/dependency-inventory.jsonへ保存する。本体155パッケージ、試作188パッケージ、共通部分をまとめて208パッケージ。2026-10-27のdb→core依存追加前は試作187だったが、coreは元から本体に含まれるlocal packageのためunique208／registry203／local5は変わらない。対象には全target向けの解決結果、build／dev依存も含まれ、特定の配布バイナリーに組み込まれる一覧とは異なる。

宣言されたlicense式は変更・単純化せず記録する。ライセンスを選択したり、互換性を自動判定したりしない。registryパッケージにはlockfileのアーカイブSHA-256、localパッケージにはリポジトリー相対パスを記録する。キャッシュや利用者の絶対パスは保存しない。license_fileの明示指定がある場合は、パッケージ内の実ファイルだけを許可して相対パスとハッシュを保存する。

`--check`は現在の解決結果と保存内容を比較して古い台帳を拒否し、ファイルを書き換えない。通常のscripts/check.pyへ組み込んだ。異なるsource／版、欠落・不正なregistry checksum、パッケージ外のlicense_file、リポジトリー外local依存の拒否を試験した。

バンドルされたC・assembly・データ、推移的なNOTICE、ライセンス全文、ソース提供と成果物ごとの配布条件の確認は残る。lockfileのchecksumを記録することと、独立した署名・真正性検証も区別する。

## 固定archiveとlicense／notice実ファイルの観測

```powershell
python scripts/dependency_license_evidence.py
python scripts/dependency_license_evidence.py --check
python scripts/check.py --with-license-evidence
```

別の観測記録をcompatibility/dependency-license-evidence.jsonへ保存する。元の宣言台帳を再生成結果と型を区別して照合し、offline／locked metadataから同じname／version／sourceのcache位置を復元する。203 registryパッケージの圧縮archiveすべてで、実bytesのSHA-256がCargo.lockと一致した。その同じ固定bytesを展開せずstream解析し、候補364ファイルの内容が展開済みcacheの同じ相対pathと全件一致した。local5パッケージはlocal_not_archiveとして区別する。実行環境はCPython 3.13.14、生成と--check成功のログはtarget/dependency-license-generation.log。

全203 registryパッケージで.cargo-checksum.jsonは不在だったため、missingと明記し、lockに一致したarchive内bytesとの照合を根拠とする。存在する場合はpackage hashと候補file hashも照合し、不正値をarchiveへのfallbackで成功扱いしない。記録するのはpackage identity、archive hash、相対file path・選定理由・サイズ・cache／archive内容hashと照合状態で、cacheの絶対pathや認証情報は保存しない。script／test、元inventory、両lockfile・manifest等のprovenance hashも保存する。archive checksumの一致はソース全体の展開状態のattestationやpublisher署名検証ではない。

候補はbasenameのLICENSE／LICENCE／NOTICE／COPYING／COPYRIGHT／UNLICENSEと、そのhyphen・underscore・dot付きsuffixをcase-insensitiveで探し、nestedファイルと明示license_fileも含む。今回candidate_not_foundだったのはcrc-catalog 2.5.0、keyed_priority_queue 0.4.2、r-efi 5.3.0／6.0.0の4件。この状態は命名規則内で候補がなかったという観測であり、法的な欠落・不適合の判断ではない。

圧縮64MiB、展開128MiB、physical tar header32,768、header再帰32段、path4,096bytes、候補4,096、選択file1MiB、最終JSON16MiBの上限を持ち、超過・重複member・path逸脱・不一致を拒否する。選択されたarchive fileのlink／非regular fileは拒否するが、未選択linkは抽出・追跡・traverseしない範囲であり、archive内の全linkを禁止したという保証ではない。cache側の選択pathのsymlink／reparseと包含関係、生成先の親directory／既存file・linkも検査する。trustedで安定したcache／workspaceが前提で、通常path操作のTOCTOU／ABAを排除できない。

physical GNU／PAX headerの上限はprivate TarInfo._proc_member hookに依存する。現Python 3.13でPAX／nested境界を含む9試験を通過したが、3.12等は未検証。新runtimeでは合成archiveの境界回帰を再実行し、runtime情報を含む証跡を再生成する。--checkもarchiveと選択fileを再読・再照合して決定的JSON全体を比較し、変更や欠落を拒否する。通常gateへの実観測は--with-license-evidenceで明示的に追加する。

target/limited-index-and-license-check.logでworkspace Rust219件・Python77件、HTTP・Clippy・Linux向けcross-check、208パッケージのlicense観測再照合と既存証跡hash検査が成功した。これは法律判断、README／コメント等に埋め込まれた全noticeの発見、特定binaryの構成確定、連続監視、ソース全体のattestation、ライセンス選択・ソース提供／配布義務の確認完了を意味しない。

後続のtarget/budgeted-meta-view-and-loader-cache-final-check.logでも208パッケージの観測とcurrent provenance照合が成功した。workspace155／probe188、registry203／local5／unique208は上記のままで、新third-party依存は追加していない。gate全体はworkspace Rust238件＋独立experiment19件・Python86件、WP45 API・299,957ケース、3所有DB fixture各39項目、HTTP・Clippy・Linux向けcross-checkまで成功し、fixtureは回収済み。初回full-check末尾のschema loader tool hash不一致は、DDL実再生成（本文bytes／SHA不変）とDB起動前generator guardで補修してから最終完了を確認した。法律監査の範囲やnative Linux未実行等の制約は変わらない。
