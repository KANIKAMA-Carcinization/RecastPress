# full WordPress bootstrap のPHP WASIX／Rust DB結合

固定3patch PHP CGI候補に、変更しないWordPress 7.1.3全core・theme・pluginと、所有する`wp-content/db.php`だけを重ねる新実験。通常native PHPの[MySQL](../wp-bootstrap-probe/evidence/windows-mysql-native.json)／[MariaDB](../wp-bootstrap-probe/evidence/windows-mariadb-native.json)のinstall・read公開観測を比較基準とする。元native source／記録と既存Wasm27＋CRUD15ケースは変更しない。

`prepare.py`は元full sourceの3,782ファイル／467ディレクトリ／110,077,999 bytesと元archiveを既存preparerで読み取り照合し、新PHP6ファイル・元native基準・許容差分をmetadataへ固定する。coreは`/wordpress`へ、追加`db.php`は同tree内の1ファイルだけとして重ねる。追加以外のcore bytesを変えず、`SHORTINIT`を使わず元`wp-settings.php`と元`wp_install()`を実行する。PHPのsource inventoryも元3,782ファイルを検査し、追加dropinだけは別の固定SHAで照合する。

private immutable `/rp-bootstrap/context.json`には厳密schema・mode・nonce・8個のsalt・install時だけのadmin passwordを与える。DBの実host／port／user／passwordはguestへ渡さず、DB定数はdummy値である。`require_wp_db()`が所有dropinを発見し、元wpdbを継承するadapterをglobal `$wpdb`へ配置する。専有Rust actorの接続はinstaller時はschema限定DDL＋CRUD、read時はCRUD権限とし、同じ所有DBとsaltを使う別PHP／WASI processで比較する。管理資格情報はguestへ渡さない。

transportは既存の私的`/rp-wpdb/channel` JSONLとhello／query／escape／closeを維持する。新候補の有限上限は256行、32列、cell 64 KiB、raw cell合計256 KiB、reply 512 KiB、4,096 operations、request frame 256 KiB、SQL／escape入力64 KiB、総request 8 MiB／reply 16 MiB。元`get_posts()`の`wp_posts.*`は23列あるため、列上限だけ旧16から明示的に広げた。超過はfailureとし、truncate・`LIMIT`追加・戻り値や件数の補正はしない。これらは本実験の候補上限であり、WP全体や製品の受入値ではない。

旧ASCII corpusのbusinessをそのまま全WP対応と扱わない。新adapterは上流`wpdb::query()`のinvalid-text検証ブロックをbyte一致で取り込み、prepare・get_*・CRUD helper・table charset照会は元実装を継承する。queryは上流検証後のbytesをCOM_QUERYへ渡し、結果NULL／binary／数値textを変換しない。escapeはvalid UTF-8と実sessionのNO_BACKSLASH_ESCAPES状態に限定する。invalid UTF-8 escape、任意mysqli handle API、name以外の`get_col_info()`、非UTF-8 ERRのraw保持は未対応／未検証であり、汎用互換性を主張しない。server errorは1064限定を外すが、code／SQLSTATE／bounded messageの範囲を検査し、messageは私的`last_error`にとどめる。

元PHPに組み込んだmanaged-access patchのため、`file_exists()`／`is_readable()`には`/rp-p0-fs/access`も必要になる。Rust側は新profileで同じ有限readonly catalogへの純粋metadata照会providerを実装する。旧P0の固定path policyや証跡は変更せず、この新provider／full bootstrapの実行結果は別途受け入れる。

CGI入口はGET・`rp_bootstrap=1`・argc0を検査する。bootstrap前に元plugin APIでmail／HTTPを短絡し、cron・更新・hard rewrite flushも止め、mail関数無効／URL fopen無効を検査する。公開観測は元nativeと同じ公開options・post/pageの固定fields・既知12テーブル数・autoload件数／最大byte数・source identity・有限RPC統計だけである。SQL、RPC data、全options、user row、password hash、salt、private context、raw CGI body／stderrを公開記録へ出さない。失敗診断はsourceに固定したphase／failure code／classの明示allowlistだけとする。

```powershell
python -B experiments/wp-bootstrap-probe/prepare.py
python -B experiments/php-wasix-wp-bootstrap-probe/prepare.py
python -B experiments/php-wasix-wp-bootstrap-probe/build_check.py
```

## 両DBでの通常起動

source `5c531b301036f90902abb13965753f8c9e369b8b` の[CI 41598717225](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/41598717225)で、同じ3patch CGI候補からMySQL／MariaDBへのinstallとfresh readが成功した。Windowsの31 unit・通常ビルドとPHP非接続検査に加え、各Linux jobでも31 unitと通常ビルドを確認した。元`wp-settings.php`・`wp_install()`・init／wp_loaded、所有dropinの読込、公開options/posts、既知12 table、sourceの実行前後一致、明示close・物理切断・actor終了/join・channel封印・EOF・所有containerの正常終了/削除を通過した。

| 観測（両DB共通） | install | fresh read |
| --- | --- | --- |
| hello／query／escape／close | 1／527／794／1 | 1／58／102／1 |
| 全RPC操作数 | 1,323 | 162 |
| 一結果の最大行数／列数 | 23／23 | 100／23 |
| 最大reply frame | 19,182 B | 64,975 B |
| 公開autoload集計 件数／最大値 | 100／3,133 B | 115／23,778 B |

公開観測19 fieldsは固定Windows native基準と一致した。同じjobでnative PHPを実行した比較ではない。autoload115件という集計と、実RPCで観測した最大100行は異なる観測である。今回の最大replyは64 KiB未満であり、別の合成115行実験の79,758 Bを通常WPの実replyとして扱わない。実結果の23列は旧16列を超える。read後のDBにあるautoload最大値23,778 Bは旧4 KiB/cellを超えるが、この集計だけでは、その値を今回の個々のRPCでreadしたかを断定しない。元SQLの変更・結果truncate・戻り値の補正はしていない。

[MySQLの取込記録](evidence/ci-41598717225-mysql/provenance.json)／[MariaDBの取込記録](evidence/ci-41598717225-mariadb/provenance.json)では、各586件のsource binding・149 Git blob、全6原JSON、API metadata、artifactのsize/SHA、3patch/source/tool/moduleの対応と元native比較を照合した。private SQL・結果・CGI bodyのdigestはCIによる観測として保持し、offlineで原bytesやnonceを復元・再計算したとはしない。バイナリや期限付き取得URLはGitへ含めない。

## 初回停止と上限の補完

source `0b9905f7b5fd499439f8cfb1dac167fe6bc77e5a` の[CI 41595534740](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/41595534740)では、両DBで通常bootstrap完了後、installerが初期1,024操作へ達して`rpc_channel_closed_or_budget`で停止した。内訳はhello1／query407／escape616／close0だった。MySQLはrequest259,279 B／reply205,203 B、MariaDBは259,287 B／205,266 Bで、byte・時間上限の到達ではなかった。

finallyからの明示closeも送れず、RPCは`rpc_close_missing`となった。actor側fallbackの物理切断・join・exit、未読応答なし、EOFとcontainer正常清掃は成立したが、成功したRPC contractへ変更しない。[MySQL原記録](evidence/ci-41595534740-mysql/provenance.json)／[MariaDB原記録](evidence/ci-41595534740-mariadb/provenance.json)を各559 binding・146 Git blobの照合付きで保持した。

その後、この新profileの操作数だけ4,096へ広げ、他の時間・frame・合計byte・行・列・cell・metadata上限と元WPのSQL処理を維持した。上記成功はこの有限候補での成立であり、製品上限の確定ではない。

## 残る境界

通常bootstrapとinstallerの成立までの実験であり、HTTP listener・theme HTML描画・実plugin・管理画面・REST・cron・全wpdb互換は別に確認する。native metadata/filemtimeの一致、非UTF-8 ERRのraw保持、統合時の全資源上限・即時回収、製品runtime採用は未受入である。次に[元テーマのnative CGI基準](../wp-request-probe/README.ja.md)と同じ要求をphp-wasmへ接続する。
