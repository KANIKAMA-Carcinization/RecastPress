# full WordPress bootstrap のPHP WASIX／Rust DB結合

固定3patch PHP CGI候補に、変更しないWordPress 7.1.3全core・theme・pluginと、所有する`wp-content/db.php`だけを重ねる新実験。通常native PHPの[MySQL](../wp-bootstrap-probe/evidence/windows-mysql-native.json)／[MariaDB](../wp-bootstrap-probe/evidence/windows-mariadb-native.json)のinstall・read公開観測を比較基準とする。元native source／記録と既存Wasm27＋CRUD15ケースは変更しない。

`prepare.py`は元full sourceの3,782ファイル／467ディレクトリ／110,077,999 bytesと元archiveを既存preparerで読み取り照合し、新PHP6ファイル・元native基準・許容差分をmetadataへ固定する。coreは`/wordpress`へ、追加`db.php`は同tree内の1ファイルだけとして重ねる。追加以外のcore bytesを変えず、`SHORTINIT`を使わず元`wp-settings.php`と元`wp_install()`を実行する計画である。PHPのsource inventoryも元3,782ファイルを検査し、追加dropinだけは別の固定SHAで照合する。

private immutable `/rp-bootstrap/context.json`には厳密schema・mode・nonce・8個のsalt・install時だけのadmin passwordを与える。DBの実host／port／user／passwordはguestへ渡さず、DB定数はdummy値である。`require_wp_db()`が所有dropinを発見し、元wpdbを継承するadapterをglobal `$wpdb`へ配置する。専有Rust actorの接続はinstaller時はschema限定DDL＋CRUD、read時はCRUD権限とし、同じ所有DBとsaltを使う別PHP／WASI processで比較する。管理資格情報はguestへ渡さない。

transportは既存の私的`/rp-wpdb/channel` JSONLとhello／query／escape／closeを維持する。新候補の有限上限は256行、32列、cell 64 KiB、raw cell合計256 KiB、reply 512 KiB、1,024 operations、request frame 256 KiB、SQL／escape入力64 KiB、総request 8 MiB／reply 16 MiB。元`get_posts()`の`wp_posts.*`は23列あるため、列上限だけ旧16から明示的に広げた。超過はfailureとし、truncate・`LIMIT`追加・戻り値や件数の補正はしない。これらは本実験の候補上限であり、WP全体や製品の受入値ではない。

旧ASCII corpusのbusinessをそのまま全WP対応と扱わない。新adapterは上流`wpdb::query()`のinvalid-text検証ブロックをbyte一致で取り込み、prepare・get_*・CRUD helper・table charset照会は元実装を継承する。queryは上流検証後のbytesをCOM_QUERYへ渡し、結果NULL／binary／数値textを変換しない。escapeはvalid UTF-8と実sessionのNO_BACKSLASH_ESCAPES状態に限定する。invalid UTF-8 escape、任意mysqli handle API、name以外の`get_col_info()`、非UTF-8 ERRのraw保持は未対応／未検証であり、汎用互換性を主張しない。server errorは1064限定を外すが、code／SQLSTATE／bounded messageの範囲を検査し、messageは私的`last_error`にとどめる。

元PHPに組み込んだmanaged-access patchのため、`file_exists()`／`is_readable()`には`/rp-p0-fs/access`も必要になる。Rust側は新profileで同じ有限readonly catalogへの純粋metadata照会providerを実装する。旧P0の固定path policyや証跡は変更せず、この新provider／full bootstrapの実行結果は別途受け入れる。

CGI入口はGET・`rp_bootstrap=1`・argc0を検査する。bootstrap前に元plugin APIでmail／HTTPを短絡し、cron・更新・hard rewrite flushも止め、mail関数無効／URL fopen無効を検査する。公開観測は元nativeと同じ公開options・post/pageの固定fields・既知12テーブル数・autoload件数／最大byte数・source identity・有限RPC統計だけである。SQL、RPC data、全options、user row、password hash、salt、private context、raw CGI body／stderrを公開記録へ出さない。失敗診断はsourceに固定したphase／failure code／classの明示allowlistだけとする。

```powershell
python -B experiments/wp-bootstrap-probe/prepare.py
python -B experiments/php-wasix-wp-bootstrap-probe/prepare.py
python -B experiments/php-wasix-wp-bootstrap-probe/build_check.py
```

Linux CIは所有MySQL／MariaDBの固定imageで実接続する。Windowsでのlint・接続前拒否やRust buildだけを、PHP WASIXでのinstall／read成功へ読み替えない。現時点で新fixtureのnative CGI lint6、private contextの有効2／拒否10、CGI guardの接続前拒否6と、準備metadataの検証を確認した。新結合の実guest／DB結果、HTTP listener、theme rendering、全plugin互換、製品runtimeの採用、全資源の即時回収は未検証である。
