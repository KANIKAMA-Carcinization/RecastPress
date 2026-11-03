# 通常WordPress起動のnative基準と次の実装範囲

2026-11-03、RP開発者向け。通常PHP 8.5.12／mysqliによる固定WordPress 7.1.3のinstall・readが、MySQL 8.4.13とMariaDB 11.8.9-MariaDBで通過した。元の`wp-settings.php`全体を実行し、`SHORTINIT === false`、`init`／`wp_loaded`到達、元`wp_install()`によるinstallと、その後の別PHP processでの通常起動を確認している。これは次のRust接続・Wasm結合で比較するためのnative基準である。

根拠は[MySQLの原記録](evidence/windows-mysql-native.json)と[MariaDBの原記録](evidence/windows-mariadb-native.json)。本所見の作成時に、両記録の成功・scope・終了条件、各22入力ファイルのSHA-256、install／readのsource前後一致と下記の実数を読み取りで照合した。DBやPHPは再実行していない。

| DB | 段階 | autoload option件数 | option_value最大byte数 | 抑止mail／HTTP呼出数 |
|---|---|---:|---:|---:|
| MySQL 8.4.13 | install | 100 | 3,133 | 1／2 |
| MySQL 8.4.13 | read | 115 | 23,778 | 0／0 |
| MariaDB 11.8.9-MariaDB | install | 100 | 3,133 | 1／2 |
| MariaDB 11.8.9-MariaDB | read | 115 | 23,778 | 0／0 |

最大値はDBの`OCTET_LENGTH(option_value)`によるbyte数であり、文字数ではない。install時の観測とread時の観測で件数・最大値は異なるが、この集計だけから差分の原因、変更されたoption、read processでの書込有無は断定しない。ここでの「read」は起動後の公開情報観測を指し、DBのread-only transactionやSQL書込ゼロの証明ではない。個々のquery traceやoption値を公開する測定も行っていない。

全core・theme・pluginの元sourceは固定archiveから配置し、3,782ファイル／467ディレクトリ／110,077,999 bytesの完全inventoryを照合した。両DBのinstall・readの4実行でsource前後のfile inventory SHA-256は`afacbaee85821a793af5964b0b797d2aa2d0e6b27bf8cb76763dc3c0cb5d911c`で一致し、元sourceの変更・追加や`wp-config.php`作成は認めていない。全sourceのbyte一致は、全ファイルや全plugin機能が実行されたという意味ではない。[準備処理](prepare.py)と[PHP入口](fixtures/bootstrap.php)に検証範囲を固定している。

既知のWP12テーブル、公開optionのblogname・siteurl・home、公開post/pageの固定fieldを観測した。別admin接続でも最終table集合・公開option・管理user件数1を照合し、各PHPの物理切断、DBの正常shutdown、全所有childのreapが成立している。installerへ渡したのは当該schema内DDL＋CRUD権限、通常起動側はCRUD権限であり、DB管理資格情報はPHPへ渡していない。password・hash・salt・user row・全option値・SQL traceを公開記録へ追加していない。

外部送信を起こさない比較条件として、元plugin APIでmail・HTTPの短絡hookをbootstrap前に登録し、CLIの`mail`無効化と`allow_url_fopen=0`も検査した。cron・更新・hard rewrite flushも制限している。表の呼出数は抑止hookの観測であり、外部送信の成功回数ではない。この条件は比較fixtureの環境設定であり、製品のmail／HTTP互換性を検証したものではない。

現在の[Wasm固定RPC profile](../php-wasix-wpdb-probe/src/db.rs)は、結果16行・16列、cell 4,096 bytes、reply 64 KiBまでである。27ケース／CRUD15ケースの成功を、この上限のまま通常WP全体へ接続できた証拠にはできない。

- 上流の[autoload読込](../../.reference/wordpress-7.1.3/wp-includes/option.php)にはoptionをまとめて取得する経路がある。今回のautoload集計100／115件は16行を超える規模であり、通常起動へ進むには全件を扱う結果予算が必要になる。集計件数を、そのまま個々のRPCで観測した行数として報告してはいけない。
- install観測の最大3,133 bytesはcell上限内だが、read観測の23,778 bytesは4,096 bytesを超える。ASCIIだけの小さな固定corpusでは発見できなかった不足である。
- 値をhexで運ぶ場合、23,778 bytesの値だけで47,556文字になり、さらに他の行・column metadata・JSON枠が必要になる。現記録は結果全体のbyte数や実reply長を測定していないため、64 KiBで足りるとも、必ず超えるとも断定しない。全結果のmaterializationと符号化後replyを別々に予算化する必要がある。

現在の[静的source FS](../php-wasix-wpdb-probe/src/readonly_fs.rs)は、mount内で単一path要素の固定ファイルを扱い、`read_dir`を拒否する。有限のWP5／8ファイルを直接読む実験には使えたが、coreの階層やtheme探索をそのまま扱える構成ではない。独立した有限tree FSは次工程で試作中であり、このnative記録でWASIの階層探索まで検証したことにはしない。

次の縦断は、元PHPの処理を維持しながら次の順で狭く進める。

1. 固定全source inventoryから有限tree FSを構成し、WASIでの階層path・directory列挙・metadata・include探索を確認する。virtual rootからの逸脱、host pathへのfallback、source変更を拒否する。
2. DB結果の全行・cell・metadata・総raw bytes・materialization量・reply長に明示的な有限予算を設ける。超過はfailureとして記録し、無断のtruncate、SQLへの`LIMIT`追加、行数や戻り値の補正で成功にしない。driverの確保、Rust側保持、hex／JSON符号化で必要な容量を区別する。
3. UTF-8と上流wpdbの入力検証、NULL／binary／文字列型、column metadata、error code・SQLSTATE・messageの保持を比較する。現ASCII成功やString化されたERRから、非UTF-8 ERRのraw保持まで成立したとは扱わない。
4. 所有する`db.php`接続を用意し、既存の通常native基準と同じinstaller／通常起動をRust DB・Wasmへ段階的に接続する。上流のprepare・CRUD helper・bootstrapを保ち、未対応経路を明示する。

今回成立したのは、通常native PHP／mysqliでの限定環境下のfull bootstrap比較である。この実験ではRust DB接続、Wasm起動、HTTP listener、theme rendering、製品runtimeの採用、標準WP全体の互換性は未検証である。以前の27＋15ケースのWasm成功と組み合わせても、この境界を越えた成功へ付け替えない。
