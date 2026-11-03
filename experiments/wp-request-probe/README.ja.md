# 匿名ホームの通常PHP theme-render基準

固定WordPress 7.1.3の匿名ホーム1件を、PHP 8.5.12 CGI／通常mysqliで実行する独立実験。元のfull-bootstrap read fixtureから閉じた差分だけを導出し、元`wp-settings.php`全体の後に元`wp()`と`wp-includes/template-loader.php`を実行する。元default theme・core・pluginのbytesを変えず、HTML・status・headerを全体で観測する。404／login、HTTP listener、Rust接続、Wasm、製品runtimeは対象にしない。

controllerは新UUIDの所有DBで旧installerと旧readを先に実行し、従来のnative基準を確認する。その後、公開post／pageのpost_date／post_date_gmt／post_modified／post_modified_gmtと、commentのdate／date_gmtを`2026-01-01 00:00:00`へ明示的にseedして独立照合する。homeは別PHP process・CRUD account・同じsaltで実行する。元installer／read source・記録は変更しない。

home inputは旧readの厳密8keysを保ち、schemaだけ`recastpress.wp-request-native-input.v0.5`、modeを`home`とする。private JSONはGETの`php://stdin`から読み、`CONTENT_LENGTH`を送信bytesへ正確に合わせる。固定CGI routeは`/`、query空、scheme http／HTTPS off、cookie／認証contextなし。公開HTMLの条件として匿名状態と、元dispatch・template_redirect・wp_head／wp_footerへの到達を確認する。

PHP CGIは`QUERY_STRING`を明示emptyにしてもCLI optionを無視するため、このnative起動では環境変数自体を省略する。controllerはambient変数を拒否し、fixtureはqueryの既定値を空として検査、WordPressにも空を設定する。DB前pure観測で、これにより`-n`／mysqli読込／mail無効／URL fopen無効／argc0の固定profileと、URI `/`・GET stdin全bytes／SHA一致が成立することを確認した。query tokenの追加やHTMLの正規化では回避しない。

CLIの空な`headers_list()`で代用せず、固定`php-cgi.exe`の実`http_response_code()`と全`headers_list()`を、JSON wrapperへresetする前に採取する。`response`はstatus、全header lineのhex配列、HTMLのbyte数・全byteshex・SHA-256を持つ。実HTMLを正規化、日時置換、属性削除、expected縮小で合わせる処理はない。post等の日時を固定しても、current clock／salt依存値がHTMLへ含まれるかは別途実観測する。

HTML保持上限は1 MiB、外側JSONは3 MiB。抑止するchunked output callbackと外側safety bufferでHTMLをstdoutへ直接出さず、templateから戻った時にbuffer深さが正確に元＋2であることを確認する。失敗時は部分HTMLを公開しない。password／saltのliteralとhexの流出guard、元mail／HTTP短絡hook、cron／更新／hard rewrite flushの抑止、mail関数／URL fopen無効を維持する。外側のapplication/json応答は観測対象のtheme応答と分離する。

元public observationのAPIとautoload集計SQLは変更せず、renderの後に実行する。homeがcache等を書き込む可能性があるため、autoload件数や最大値を旧readの115／23,778へ固定しない。「home」はDB書込ゼロの主張ではない。3,782ファイル／467ディレクトリ／110,077,999 bytesの元全sourceを前後で照合し、追加・変更やwp-config作成がないことと、正常切断・所有DBの正常停止／child回収を別々に確認する。

```powershell
python -B experiments/wp-request-probe/prepare.py
python -B experiments/wp-request-probe/database_check.py --backend mysql --record
python -B experiments/wp-request-probe/database_check.py --backend mariadb --record
```

DB前のnative CGI pure確認で、GET stdinの全byte／SHA一致と、抑止bufferからheaderを送信せずJSONへresetできることを確認した。fixtureのlintと不正input拒否、full sourceの準備は実render成功とは分けて扱う。全HTMLを保存できても、実listenerのHTTP挙動や全theme／plugin互換、Wasmで同じHTMLが生成されることを証明するものではない。

## 両DBでの実測

[MySQL](evidence/windows-mysql-native.json)／[MariaDB](evidence/windows-mariadb-native.json)の両方で、旧install／read基準の再現、公開日時の実DB seed、fresh CGIでの元dispatch／元theme描画に成功した。inner応答はstatus200・header3行・HTML69,399 bytesで、両DBのstatus／全header／HTML全bytesが一致した。body SHA-256は`ef43f26984536cae43c4e703edef98dbaaf2bca79cc972a4373bf27f01cabdd1`であり、HTMLへの正規化や出力補正は行っていない。

元全3,782ファイル、PHP78ファイルと33入力の前後一致、物理切断、所有DBの正常shutdown・全child reapも確認した。これらはWindows native CGIの実測であり、Rust DB／php-wasm／HTTP listenerの成功へ読み替えない。次のWasm比較では、同じ元テーマ・固定fixtureデータ・公開応答を使う。

独立SQL集計では、旧read直後がautoload115件、home描画後が117件で、最大値は両段階とも23,778 Bだった。最大optionの名前は`_transient_wp_core_block_css_files`と確認し、値の本文は公開していない。元`wp-includes/blocks/index.php`は、WordPress版と相対CSS file一覧をこのtransientへ保存する。通常bootstrap時の大きな値の由来を、無断truncateやSQL変更で隠していない。

69,399 BのHTMLだけでも以前の64 KiB捕捉profileを超える。次のWasm描画profileは今回の1 MiB/body・3 MiB/wrapper候補等と整合させ、完全応答と超過拒否を確認する。WP全体の製品上限をこの一件から確定しない。
