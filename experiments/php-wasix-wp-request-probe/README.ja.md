# 匿名ホームのPHP WASIX／Rust DB結合候補

固定WordPress 7.1.3のfull bootstrap・元`wp()`・元template loader・default themeで匿名ホームを生成する新profile。通常PHP CGIの[MySQL基準](../wp-request-probe/evidence/windows-mysql-native.json)／[MariaDB基準](../wp-request-probe/evidence/windows-mariadb-native.json)からexact source変換し、旧native／Wasm probe・記録は変更しない。

nativeの両DB基準はstatus 200、全3 headers、HTML 69,399 bytes／SHA-256 `ef43f26984536cae43c4e703edef98dbaaf2bca79cc972a4373bf27f01cabdd1`で完全一致した。新profileでもHTMLの日時・属性・空白やheaderを正規化／削除して合わせず、全bytesを比較対象にする。新profile自体の実Wasm theme render成功は別のCI実測が必要である。

元3,782ファイル／467ディレクトリ／110,077,999 bytesのcore・theme・pluginを`/wordpress`に置き、所有`wp-content/db.php`1本だけをoverlayする。元全source inventoryをPHPでも前後確認し、追加dropinだけは独立した固定SHAで照合する。元dispatcher／template-loader／render-before-public-statisticsブロックはnativeからbyte一致で維持する。bounded HTML 1 MiB／JSON wrapper 3 MiB、正確なbuffer深さ、匿名確認、実status／headers採取、mail／HTTP／cron／更新／hard rewriteの制限も引き継ぐ。

外側CGI入口はGET `/__rp_request.php?rp_request=1`／query `rp_request=1`／argc0。これはPHP WASIXのoption処理を維持する非公開入口であり、private contextを検証した後、元nativeと同じ`/`・空query・http／HTTPS off・空cookies／認証contextへ戻してから元WPを実行する。非公開入口のqueryをホームの内容へ混ぜない。

immutable `/rp-request/context.json`は固定schema `recastpress.php-wasix-wp-request-context.v0.5`、mode `home`、nonce、8個のsaltの厳密4keysだけで、実DB接続先・DB資格情報・admin passwordは含めない。DB定数はdummy、actorは専有1接続とし、所有dropinが元wpdb継承adapterをglobal `$wpdb`へ置く。adapterは旧full-bootstrapの4,096 operations版と全byte一致、helpersの差分はcontext schema／home-only、db.phpはfixture prefixだけである。新しいSQL／escape／結果処理の互換性拡大をここへ紛れ込ませない。

結果・byte・時間上限は旧full-bootstrap候補を継承する。UTF-8 escape境界、非UTF-8 ERR raw保持、name以外のmysqli metadata API、native timestamp／permission metadata全互換、製品予算受入の未検証範囲も維持する。元3patch moduleのfile_exists等を支えるmanaged access、有限tree FS、private RPC channelはRust側で同じreadonly catalogへ接続する。

公開対象は固定公開観測、全合成HTMLとheaders、source identity、有限operation統計だけとする。DB query／RPC data／user row／password hash／salt／private context／raw stderrは公開しない。失敗時のphase／code／classはsource内JSON literal allowlistに限定し、部分HTMLを成功に読み替えない。HTTP listener、任意theme／plugin互換、製品runtime、全資源の即時回収の実証ではない。

```powershell
python -B experiments/wp-request-probe/prepare.py
python -B experiments/php-wasix-wp-bootstrap-probe/prepare.py
python -B experiments/php-wasix-wp-request-probe/prepare.py
```

preparerは元native／full-bootstrapのsourceと受入済みnative基準SHAを読み取りで固定するだけで、native binariesを必要とせず、PHP／Wasm／DB／networkは実行しない。native CGIを使うlint・context shape・不正CGI入口拒否の確認と、実guest／DB／全HTML比較は区別する。
