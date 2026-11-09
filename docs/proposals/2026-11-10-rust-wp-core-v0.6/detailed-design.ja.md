# RecastPress v0.6 コア・データ・運用の詳細設計

作成日：2026-11-10（日本時間）。設計版：v0.6。
[基本設計](architecture.ja.md)と[PHP互換界面](php-compatibility.ja.md)を実装単位へ分解する。標準動作の正本は固定WPソースと比較環境であり、本書の処理一覧は元sourceの分岐・hookを省略する許可ではない。

## 1 モジュールと共通契約

既存crateを土台とし、まず論理moduleを分ける。以下の新module名は設計名であり、今回crateの新設や移動は行わない。

| 配置候補 | 論理module | 契約 |
| --- | --- | --- |
| recastpress-server | ingress、supervisor、response | HTTP、worker監督、最終送信判定 |
| recastpress-wp | runtime、bootstrap、bridge、hooks | WP状態機械、継続、同期と標準hook |
| recastpress-wp | rewrite、wp_query、loop、template | 標準の要求解釈・検索・描画先選択 |
| recastpress-wp | options、posts、taxonomy、meta、users、comments | WP標準の読書き・validation・副作用順序 |
| recastpress-wp | authentication、capabilities、rest、blocks、admin、jobs | 共通認証、標準API・描画・管理・job |
| recastpress-db | session、raw_result、change_tracking、lookup | DB接続・値・可視性。WPの業務判断を低levelのCASへ押し込まない |
| recastpress-core | cache、resource_budget、revision | 共有owner、epoch、予約と回収 |
| PHPコード束 | generated surface、dispatcher、language_ops | 元signature・class表現・PHP言語操作・拡張実行 |

標準operationは `advance(frame, event) → Continue / NeedPhp / NeedDb / Complete / Terminate / Fail` の状態機械として実装する。公開状態と関係しないpure計算は通常関数を使える。同期点や副作用を越えるローカル値には、copyかPHP参照か、再観測が必要かを定義する。

operationのエラーは、WP互換の返値・WP_Error、PHP throwable、hostの失敗、結果不明を区別する。すべての失敗を `false` に統一しない。warning・deprecated・noticeは元の発生位置と抑止条件に対応させ、内部の診断ログとは分ける。

## 2 bootstrapと要求解釈

### 2.1 bootstrap plan

固定した `wp-settings.php` と関連入口から、宣言、定数、標準登録、drop-in接続、plugin読込み、pluggable解決、theme読込み、各初期化hookの順序表を作る。MU-plugin、network対象の有無、通常pluginの順序、親子theme、locale、current user等の初期化位置を分岐として持つ。

Rustは順序と標準登録を実行し、PHPへは必要な宣言・拡張includeを依頼する。include時にpluginが登録・照会・更新するAPIも同じ継続方式へ入る。`plugins_loaded`、`setup_theme`、`after_setup_theme`、`init`、`wp_loaded`等の代表hookだけを揃えてbootstrap互換とせず、実行された全段階を比較する。

定数・global・登録済みpost type／taxonomy／route／block等は要求内contextへ関連付ける。公開registryをPHPで直接変更できる領域は同期対象に含める。前要求のplugin登録を流用せず、コード束の不変定義と要求固有の動的登録を区別する。

### 2.2 WP mainとrewrite

`WP::main` 相当は固定sourceの `init → parse_request → 条件付きquery_posts / handle_404 / register_globals → send_headers → wp action` の順序を維持する。正規URLへのredirect、preview、feed、robots・favicon等の入口は対応する標準hook・分岐と一緒に移す。

rewriteは、登録・flush・保存されたrules、public／private query vars、permalink、pathinfo、query stringと重複値、extra vars、`request` filters、`parse_request` actionを対象版に合わせる。URL decodeやqueryの型を独自に正規化しない。

元PHPの正規表現や文字列動作をRustの別engineへそのまま置換して同等としない。まず対応patternを比較し、必要なPHP言語primitiveを界面から使える。rulesの選択・WP変数への解釈はRustが担う。未対応patternを理由に元WPのmain全体を再実行しない。

## 3 WP_Query

### 3.1 QueryContext

QueryごとにPHP `WP_Query` object handle、定義ID、main queryとの関係、query入力、parse状態、SQL構築状態、結果処理、Loop位置を持つ。同じobjectをPHPに見せ続け、callbackのたびに新しいproxy objectへ交換しない。

公開viewには少なくとも `query`、`query_vars`、条件フラグ、`request`、`posts`、`post`、`post_count`、`found_posts`、`max_num_pages`、`current_post`、Loop関連状態、queried objectを含める。可視propertyの全母集団を固定sourceから列挙し、この一覧だけで網羅済みとしない。`WP_Meta_Query`、`WP_Tax_Query`、`WP_Date_Query`等の公開表現・methodも同じ依存台帳へ含める。

### 3.2 処理の状態

| 段階 | Rustが実行すること | PHPとの観測点 |
| --- | --- | --- |
| Parse | 引数解析、既定値、flags、標準parse処理 | `parse_query`、override・公開query vars |
| BeforeQuery | `pre_get_posts`、変更後のvars再取得・補充、hash等の状態更新 | 同じobjectへの参照、直接変更、nested query |
| Build | post type／status、meta・tax・date、search、order、limit、fields、件数等のSQL構築 | `suppress_filters`条件と各clause filter |
| FinalSql | request用filters、SQLの公開、短絡とcacheの標準分岐 | SQLを変更するfilters、`posts_pre_query`等 |
| Execute | 対象sessionでSQLまたは検証済み高速経路 | wpdb公開状態、SQL filtersとraw結果 |
| Materialize | IDs／WP_Post等、password・status等の後段処理、件数、関連cacheの準備 | `posts_results`、件数関連filter、公開結果 |
| Finish | 元と同じ分岐で最終results、flags、Loop初期化 | `the_posts`等、返値型・順序・identity |

これは実装を分ける単位である。hook順序・短絡・cache hit時の省略・`fields`別の返値・`split_the_query`等は元sourceの実行graphを移し、表の一本道へ単純化しない。`posts_pre_query`が返した結果は元と同じ処理へ入れ、無条件にDBを実行しない。

### 3.3 SQLと拡張

標準SQL生成をRustへ移す。拡張のJOIN・WHERE・GROUP BY・ORDER BY・LIMIT等の変更と、結果filterをそのまま適用する。SQLをRustの狭い検索条件へ逆変換できなくても、通常DBへ同じSQLを送ってRust Queryを続ける。

最初は索引を使わず、観測されるSQL・引数・charset・順序・件数を比較する。後から高速経路を追加する場合も、hookやSQL公開状態を維持し、物理SQL省略を別の診断計数にする。query varsだけを共有キーにしてfilterの変更や副作用を落とさない。

同じDB行が常に同一PHP objectであるという独自identity規則は追加しない。WPがcache、変換、filter、cloneを通して実際に共有・作成するidentityを比較する。`WP_Post`等のproperty変更と、後続の標準APIが取り直す永続値の違いも維持する。

## 4 Loopとtemplate

Rustが `have_posts`、`the_post`、rewind、setup／reset postdata、main／secondary query、条件タグを処理する。PHP側の `$wp_query`、`$wp_the_query`、`$post`、関連globalsを元と同じ時点で更新する。nested Loop終了時は、そのAPIが復元する範囲だけを復元し、元WPが残す変更まで一律に巻き戻さない。

template階層、候補順序、theme／child themeの解決、標準filters、template redirect・includeの順序をRustへ移す。PHP template実行は既存コードをincludeする。`load_template`相当のscope、globals、query varsの `extract`、`$args`、require／require_once、前後hookをPHP表面で再現する。[公式 load_template](https://developer.wordpress.org/reference/functions/load_template/)

include pathはコード束の許可領域へ解決する。PHPのinclude変数scopeやtheme pathを独自のevalへ置換しない。template中の標準APIはRustへ入り、拡張callbackと未移植標準処理の委譲を別に計測する。

classic themeとblock themeを別profileとして受入れる。classic themeの表示成功をもってblock theme対応としない。標準の本文整形・escaping・翻訳・KSESは必要な分野からRustへ接続し、既存pure APIの対象外入力を黙って受理しない。

## 5 options・transients

### 5.1 get_option

Rustが入力・deprecated key・special option・install分岐を扱い、`pre_option_{name}`と`pre_option`、厳密なfalseによる短絡、defaultの指定有無、alloptions／notoptions／個別cache／DB、復元と最終filterの順序を固定sourceに合わせる。

保存されたfalse、未取得、存在しないoption、defaultとして渡したfalseを区別する。返値の型はDBから得たときと、同じ要求でcacheした直後で異なり得るため、単一のRust型へ統一しない。autoload共有PoCのSQL結果を、そのまま `get_option` 全体の返値cacheとみなさない。

### 5.2 update_option

固定 `option.php` の手順を次の状態へ移す。

1. option入力、旧名転送、保護対象を確認する。
2. object値なら元と同じ時点でPHP cloneを依頼する。
3. `sanitize_option`、旧値取得、名前別と共通の更新filterを実行する。
4. 値の厳密比較と必要時のserialize比較で更新不要を判定する。PHP言語操作の短絡・実行回数を保つ。
5. 元のdefault filterとの比較から、必要なら `add_option` の標準手順へ進む。
6. 保存用serialize、更新前action、autoload決定・再評価、同じsessionでUPDATEを行う。
7. 更新結果に応じて、notoptions、alloptions、個別cacheを元と同じ順序で変更する。
8. 成功後に名前別action、`updated_option`を元と同じ引数順で呼ぶ。

filterとactionから再入した読書きは同じDB sessionとPHP objectを見る。clone・serializeのmagic methodが呼ぶAPIも子operationとして処理する。最初のserialize結果を勝手に使い回して、元が再度呼ぶmagic methodを省略しない。[公式 update_option](https://developer.wordpress.org/reference/functions/update_option/)

add／delete、site option、transientの期限、永続object cache有無による分岐、autoload値の対象版ごとの差を別operationとして実装する。応答cacheのTTLなし方針をtransientの期限へ適用しない。

## 6 投稿・分類・meta・コメント・ユーザーの保存

標準の保存は、単純なrow更新より広い状態機械とする。入力のslash／unslash、既定値、sanitize・validation、権限を検査する入口、slug・status・日時、関連taxonomy・meta、revision、cache、通知、標準actionsを固定sourceの順に移す。

| 対象 | 分離する実装単位 |
| --- | --- |
| posts／pages | insert・update・delete・trash・restore、status transition、slug、revision・autosave、予約公開 |
| taxonomy | term・relationship・count、階層、重複・collation、term hooks・cache |
| metadata | 登録schema、sanitize・auth filters、重複行、previous value、add／update／delete |
| comments | 受付、validation、moderation・spam・trash、count、関連cache・通知 |
| users | account更新、password・session失効、roles・capabilities・user meta、hooks |

各operationに「直前の公開状態」「SQL／副作用」「直後の観測」「失敗時に残る状態」を記録する。`wp_insert_post`等の低level APIへ、元は入口側が行うcapability検査を無条件に追加しない。HTTP／REST／admin入口が元と同じ認可・nonce検査を行い、拡張固有APIはその拡張の責任を維持する。

元の複数SQL・hookを外側の一つのtransactionで囲まない。原子性を改善したい場合は互換性の変更提案へ分離する。保存中callbackが別保存を行う場合も同じoperation stackを使い、外側の古いsnapshotで子の変更を上書きしない。

DB送信前失敗、確定DBエラー、更新済み、結果不明を区別する。更新後のaction失敗を「DB更新されなかった」と表示しない。GETで起動された保存・通知も副作用として記録し、その応答を共有保存しない。

## 7 認証・権限・session・OIDC

### 7.1 共通のRust認証コア

credential入力、ユーザーlookup、password／cookie検証、rehash、session token、current user、role／capability、nonce、回復・失効を一つのRustコアへ接続する。既存crypto・cookie・lookup・CASは部品として再利用し、独立した別認証を同時に正本としない。

`wp_authenticate`等の入口の正規化と、低level password verifyのraw bytesを区別する。authenticate、current user、`map_meta_cap`、`user_has_cap`等の変更点、pluggableな上書き、WP_Userの公開状態をPHP界面へ接続する。nonceを単独の認可証明にしない。

公開・管理・RESTで同じユーザー・session・失効状態を使う。管理の導入・更新・本体設定は、Rustが標準capabilityと専有policyを検査する。PHPから渡したuser IDや管理画面フラグだけで許可しない。受入済み認証pluginの判断が安全になる保証を、Rust採用に付け加えない。

### 7.2 OIDC

全ユーザー向け標準機能、初期off、off／任意／必須要求、OIDC限定、MFA非実装を継承する。v0.6の接続方式はAuthorization Code FlowとPKCEを基本とし、state・nonce・要求との対応、issuer・audience・署名・時刻・redirect URI・一回性をRustで検証する。外部Identityはissuerとsubjectで関連付け、メール一致だけで既存ユーザーへ連携しない。[OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html)、[OAuth Security BCP](https://www.rfc-editor.org/rfc/rfc9700.html)

必須要求への移行時は、一度限りのpassword許可をユーザー単位で永続管理する。正常なpassword確認後に許可の消費と連携専用sessionの発行を原子的に関連付け、同時要求の二重消費を防ぐ。連携専用sessionはOIDC手続きだけを許し、通常編集・plugin起動・WP汎用APIへ到達させない。手続きUIとcallbackはRustの専用routeで処理する。

有効期限内の再開では新しいOIDC transactionを発行する。連携確定後に通常sessionへ切り替え、旧手続きsessionを失効する。復元・mode変更・IdP停止で一度限りの許可を自動再発行しない。具体的な手続き期限・回復権限・既存sessionの扱いは、V06-4の受入前にprofileへ固定する。

## 8 標準block・API・管理・周辺機能

| 分野 | Rustへ移す処理 | PHPに残る実行／移行時の扱い |
| --- | --- | --- |
| blocks | parse、registry、context、標準render、theme.json、style・interactivity連携の標準規則 | pluginのrender callback。未移植標準renderはdelegateとして列挙 |
| shortcodes／widgets | 標準parser・登録・呼出し順と標準実装 | 拡張callback・widget subclass |
| REST | route解決、schema・validation、標準controller、標準permission、error／response | plugin route・permission callback、PHP request／response object |
| AJAX／XML-RPC | 対象profileの標準dispatch・標準handler | plugin callback。XML-RPC未提供時は明示して母集団に残す |
| admin／editor | menu・標準操作・権限・一覧・保存、標準viewの生成 | 当初のPHP view、plugin admin page。既存JS／CSSは利用可能 |
| media | upload規則、metadata、派生画像、添付状態、標準API | image codec等のライブラリ呼出しと拡張filter |
| HTTP／mail | WPのrequest・error・notification契約と呼出し順 | 必要なPHP言語・第三者library、plugin transport callback |
| cron | schedule・due判定・標準job、job ownership、標準副作用 | plugin job callback |
| install／upgrade | schema照合・標準migration、版・一体更新の制御 | plugin activation・upgrade hook、未移植標準処理を明示委譲 |

標準の管理viewをPHPへ一時的に残す場合も、保存・権限の判断をRustへ通す。元の管理入口を丸ごと起動し別のWPコアを立てない。残したviewの標準ロジックは後段で移す。block themeとeditorが使うREST・標準blocks・権限を一つの代表操作列で確認する。

cronと外部副作用はjob ID、起因、試行、lease・fence、結果分類を持つ。外部mail・HTTPまでexactly-onceとしない。期限切れjobを再実行するときは、操作先で二重実行を避けられる条件と結果照合を持つ。旧workerが生き残る場合はlease期限だけで排他済みとしない。

### 8.1 blockと標準コンテンツ処理

`BlockRenderContext` はparsed block、親、継承context、attributes、inner blocks／inner content、登録済みblock定義、style収集状態を持つ。PHPから見える `WP_Block`・`WP_Block_Type` 等は同じidentityの表面を用意し、拡張による登録変更と公開propertyの利用を同期する。

Rustのparserはblock comment構文、attributes、freeform、入れ子、壊れた入力の回復を固定版と比較する。parser class差替え等の拡張入口も対応させ、標準parserを移したことだけで拡張parserのPHP実行を禁止しない。

renderは、元の短絡条件に従い `pre_render_block`、data変更、context変更、子block、標準または拡張render、最終出力filtersを進める。全hookを毎blockで無条件に呼ぶ独自loopにはしない。標準blockの処理はRust、拡張の `render_callback` はPHPへdispatchする。query系blockは同じRust Query・Loopへ入り、親のpost・contextを元の規則で復元する。

theme.jsonの継承・merge、styleの優先順と重複抑止、block supports、動的CSS・script登録、interactivity用metadataを個別に移植する。登録された標準callbackをpluginが外した結果を保つ。escaping・KSES・i18nは出力位置に合わせ、filter前後を入れ替えない。元標準renderへのdelegateを使うblockは識別子ごとに記録し、block engine全体の移植完了に含めない。

### 8.2 REST・AJAXのdispatch

REST要求は元のpath・method・headers・bodyを保持し、`WP_REST_Request` のparameter source、default、sanitize・validate、遅延parse等の観測を再現する。route表はPHPの登録・変更を取り込み、標準controllerか拡張handlerかをmanifestで識別する。

固定 `WP_REST_Server` のdispatch graphを移し、pre-dispatch短絡、route／method解決、入力検査、認証、before-callback filter、permission callback、handler、after-callback filter、response変換、post-dispatch・出力を元の位置で実行する。認証と入力検査の順を本書の列挙から独自に組み替えない。permission callbackが返すfalse／null／WP_Error等の違いも比較する。

標準controllerはRustの同じ読書き・認証コアを呼ぶ。plugin handler・permission callbackと、必要なPHP request／response methodは継続effectとして実行する。内部REST呼出しやbatchも子operationとし、外側のcurrent user・globals・DB sessionを独立した別WPへ切り替えない。

標準AJAX actionはnonce・capability・出力／終了を含むhandlerとして移す。plugin actionはPHPで実行する。JSON出力、複数headers、REST errorのstatus・data、意図的終了を通常HTML失敗応答へ一律変換しない。

### 8.3 管理画面とeditor

管理要求は、標準認証と対象capability、admin初期化・menu・screen、action選択、入力検査、標準処理、redirectまたはview生成の状態を持つ。現在画面、menu、screen option等の公開状態とadmin hooksはPHPに見える表現へ反映する。

標準の一覧・編集・設定の判断はRustへ移す。list tableの拡張、meta box、plugin page、enqueue等のPHP callbackを元の順序で呼ぶ。PHP viewを残す段階はview module単位のdelegateとし、元admin bootstrap・認証・保存の全体を再起動しない。

editorのJS／CSSは既存資産を利用できる。REST schema、autosave、revision、media、block registry、権限、nonceと画面の関係を一つの編集操作で比較する。PHP viewの標準ロジックを退役させた後も、pluginが管理画面へ出力するPHPは拡張実行として残せる。

### 8.4 media・HTTP・mail

media operationは、入口の権限・nonce、upload error、file名・MIME・size・path検査、管理領域への配置、attachment保存、metadata・subsize生成、cache・hooksの各状態を持つ。WP固有の判断はRustが担い、画像decode／encode等は能力を限定したlibraryへ渡す。採用libraryと元PHP実装の画質・metadata・orientation等の差は個別に比較する。

DBとfileの一括原子性は仮定しない。どのfileが配置され、どのattachment・metadataが確定したかを記録し、途中失敗では元のerrorと残存状態を比較する。再実行・孤立file回収を行う場合は、同じfileを別要求が使っていないことを所有記録で確認する。uploadsをPHP実行領域にしない。

WP HTTP APIとmail APIは、既定値・filters・短絡、transport選択、headers・body・redirect・timeout、返値・WP_Error、前後actionsを標準operationとして移す。実通信・配送は許可能力を通す。ネットワーク権限を持つpluginにも本体DB・管理endpointの迂回経路を与えない。外部timeoutを確定未送信と見なして自動再送しない。

### 8.5 cron・install・upgrade

cronはWP互換のschedule記録と公開APIを保ち、登録・変更・取消し・期限判定・標準jobをRustへ移す。PHP引数のserialize・同一性やschedule filtersを比較し、plugin job callbackは同じPHP界面で実行する。独自job tableだけへ移してWP APIからの観測を失わせない。

job起動を担当する本体serviceは、実行claimとfenceを持つ。callbackからの再schedule、要求経由の起動、worker再起動と期限超過を検査する。claim喪失後に進行中の外部送信を撤回できるとはしないため、二重実行の可能性・結果不明・照合をjob状態へ残す。

install／upgradeは対象schemaとDB版を照合し、変更計画、必要な権限、backup対応、実行、版記録、post-checkの順で進める。WPのDB versionやpluginが観測するinstall状態を保つ。pluginの `dbDelta` 等を含む許可DDLは管理能力の契約で扱う。部分DDL成功・暗黙COMMITを記録し、単なるコード差戻しで復旧済みにしない。

## 9 DB sessionとwpdb

### 9.1 一つの接続経路

標準RustコアとPHP `$wpdb` facadeは同じ要求のDB actorを使う。PHPが更新後にRustが読む場合も別接続へ振り分けない。wpdbのprepare・escape・結果変換・公開計数等の標準処理も最終的にはRustへ移し、PHPは入口と値の表現に収束させる。v0.5の元wpdb接続層の再利用中は未移植部分を明示する。

sessionはcharset、collation、sql_mode、time_zone、autocommit、isolation、savepoint、一時表、user変数、locks、insert ID、warning・未読結果を管理する。入れ子の標準operationも同じsessionへ直列化する。終了時の完全なresetを確認できない接続は破棄する。

raw結果はNULLとbytesを区別し、column metadata、行順、重複列、数値文字列・大整数・binary・非UTF8・raw ERRを保つ。`last_result`、`last_error`、`last_query`、`num_rows`、`num_queries`等の公開状態をその標準APIの時点で更新する。

### 9.2 権限と失敗

通常のwpdb読書きは許可する。生のmysqli handle・driver result・本体制御状態・他サイト・DB管理権限への到達を拒否する。独自tableの登録、DDL、外部DB・stored routine等は能力profileで扱い、元の未決を標準SQL対応済みへ含めない。

DDLの暗黙COMMIT、transaction可視性、`SELECT FOR UPDATE`、接続依存の処理をSQL先頭語だけで分類しない。未知SQLは許可能力と共有可否を別に判定する。共有同等性が不明なら共有を抑止し、許可済みSQLは通常DBへ送る。

| DB結果 | 扱い |
| --- | --- |
| NotSent | DBへ送信していない。外側callbackの副作用も確認して再試行可否を決める |
| ConfirmedSuccess | affected rows、insert ID、transaction状態を確定 |
| ConfirmedDbError | DBの確定エラーと公開wpdb状態を返す |
| OutcomeUnknown | 送信後切断等。成功／失敗を推測せず、更新SQLを自動再送しない |
| CleanupFailed | 接続を隔離・破棄。確定済み更新を取り消したとは扱わない |

上流のwpdb再接続・再実行とRust retryを二重に走らせない。危険な再送を止めることで元と異なる箇所は、試験と互換差分へ記録する。

## 10 キャッシュ・索引・変更追跡

### 10.1 三つの層

| 層 | 内容 | 制約 |
| --- | --- | --- |
| 要求内object cache | WP cache APIの値・found・group等 | PHP値のclone・参照・identityを元と比較。共有handleを作らない |
| 共有object／DB結果・内部索引 | 対応codecと依存が明確な派生物 | source・session・schema・code・epoch・追跡・原子操作を検査 |
| 共有応答cache | 検証済み匿名GET／HEAD等の完成応答 | 個人状態・副作用・不明な依存を除外。TTLなし・変更時全パージ |

wp_cacheのfalse／miss、参照found、force、group、non-persistent group、add／replace／incr／decr、複数件API、flush系、supports、明示期限をそれぞれ移植する。atomic操作をPHPのget→setで代用しない。共有codecが未対応の値は、対応していない共有機能をsupportsで申告しない。

### 10.2 共有利用許可

共有サービスは `source identity + 接続profile + schema + epoch + 適用位置 + pending状態 + 観測時刻 + release` に結び付いた許可を発行する。受信イベントが未適用、EOF・受信error・buffer超過、watcher取消し・panicでは既存許可も失効させる。旧permitを取得済みの処理も、使用直前に確認する。

位置が一致することだけを任意並行更新下の最新性証明にしない。共有値生成時のsnapshotと観測位置、COMMIT適用、生成中の更新、再格納競合について、対象DB profileごとに証明と試験を持つ。既存のread lock付き試験や固定1行の成功は、その前提のまま再利用する。

安全な生成・利用条件が成立しない間は共有をoffにし、Rustコアの通常SQLを実行する。最適化を止めてもWPのQuery・保存・hooksはRust経路のまま動く。共有のために毎要求追加する位置照会・接続も総DB費用へ計上する。

### 10.3 変更と復帰

```text
Initializing → Building → CatchingUp → Healthy
Healthy → Suspended → Rebuilding → CatchingUp
任意状態 → SourceUnknown
```

自身の変更SQLを送信する前に共有生成・利用を抑止する。COMMIT確認後にepochを進め、古い生成ticketの再格納を拒否する。rollbackでもPHPが行ったcache操作や外部副作用は元どおり扱う。結果不明は抑止を維持する。

外部更新はsource、schema、整合snapshotと開始位置を対応付け、行ベースbinlogをCOMMIT単位で適用する。rotation、重複、ログ消失、DB再起動・復元、接続先変更、DDLを検査する。buffer上限はdriver内部allocationやRSS全体の上限と区別する。

watcher再起動時は旧permit・cacheを破棄する。ログを継続できる場合も、データとcheckpointの対応を検証してから復帰する。ログ欠落ならfresh snapshotへ進む。単に新しい接続を作れたことを再同期完了にしない。

### 10.4 応答の全パージと送信

キーはsite、検証済みHost、元path・query、必要な表現header・locale、release／profile、settings、epochを含む。query順序・重複・空値・encodingを同等性なしに統合しない。認証・個人Cookie・nonce・preview・private／no-store・Set-Cookie・書込み・未追跡依存・必要な実行副作用を持つ経路は保存しない。

DB・file・コード・設定の変更で全キー・GET変種を論理失効する。生成時と保存時と送信許可時にepoch等を照合し、304、Range、HEADにも同じ失効条件を適用する。HTTPへ渡したbytesとメモリ上の失効は別に扱う。外部browser／CDNの保持は[HTTP Caching](https://www.rfc-editor.org/rfc/rfc9111.html)に従う別契約とする。

本番共有を有効にする前に、通常更新の反映、非公開化後の新規送信停止、監視喪失検出、process間失効の最大時間と保証範囲を固定する。未決のままTTLなし共有を本番へ出さない。標準APIの互換性を、この外部追跡遅延によって無条件に緩めない。

### 10.5 内部索引

対象shape、schema、collation、session、snapshot、filtersを検査した限定lookupから始める。任意SQLの独自実行engineにしない。未対応queryや未知filterは通常DBを使う。効果と同等性が確認できた索引だけを有効化し、正本schemaへの自動index追加とは区別する。

## 11 有限資源と実行監督

共通予算に、PHP/Wasm、Rust frame・snapshot、handle registry、DB result、通信buffer、生成中body、共有cache、索引、更新時の二重起動を含める。計上済みの共有bodyを参照ごとに二重計上せず、最後のownerまで予約を保持する。

自動配分は、観測可能な実効制限から基盤・通常実行の必要量、変動と誤差、更新・再構築予約を引く。同じcgroupや祖先制限の余裕を各processへ二重配分しない。RSSと論理予算、未知の親制限、別hostのDBを分けて表示する。管理者の手動cache容量設定は設けない。

圧迫時は新規共有保存・索引構築・worker増設を止め、派生物を減らし、新規要求を有限queueで制限する。余裕が継続したとき段階的に復帰する。cacheを消したことだけでWasm heapや全RSSが解放されたとしない。

演算予算、Wasm memory、hostcall待機、DB deadline、要求deadline、出力上限を別に管理する。hostcall停止はWasm命令budgetだけで回収できないため、外側監督で所有workerを終了・reapする。取消し後のDB結果確認と共有抑止を継続する。

初期はmoduleだけ再利用し、要求instanceを新設する。PHP heap・DB poolを再利用する場合は、globals、hooks、locale、timezone、superglobals、header、output buffer、shutdown、resource・一時file・未読結果等を別途検査する。

## 12 拡張受入と専有領域

通常のWP hooks、公開object、custom post type、taxonomy、shortcode、REST、wpdb、cache利用は受入対象とする。本体所有のloader、DB・cache接続、共有epoch、内部設定・秘密、コード有効版の置換・迂回は拒否する。

移行、取得、導入、有効化、更新、復元、起動、直接配置の各入口で同じpolicyを適用する。MU-plugin、drop-in、theme・child theme・追加includeを含める。静的検査だけで任意PHPの非衝突を完全に証明するとはしない。

| 判定 | 動作 |
| --- | --- |
| 条件を満たす | 対象hashとprofileを固定して有効化可能 |
| 既知の衝突・未対応能力 | 理由と代替を示して拒否。複合衝突は初回は拡張全体を拒否 |
| 具体的な衝突の疑いが残る | 候補を実行せず保留。開発者への報告資料・再検査へ |
| 応答cache可否だけ不明 | 対象経路、限定できなければサイトの応答cacheを停止し通常動的処理 |

完全な人手審査・全pluginのallowlist制を追加しない。秘密を除いた診断を生成し利用者が確認して投稿する。自動送信、GitHub token登録、報告しただけの保留解除はしない。報告先は現originに対応する [RecastPress Issues](https://github.com/KANIKAMA-Carcinization/RecastPress/issues) を配布前に確認して固定する。

## 13 永続状態・更新・移行・復旧

| 記録 | 主な内容 |
| --- | --- |
| release／core manifest | 本体・互換定義・loader・WP資産・PHP／host・ABI・codec・profile・hash |
| site settings | 設定版、OIDC・能力policy、自動機能のon／off。秘密は別の保護領域 |
| extension review | hash、policy版、受入／拒否／保留、理由、再判定・報告対応 |
| migration session | 入力source、転送／file取込み、snapshot、差分同期、比較、切替状態 |
| job／audit | actor、操作、起因、結果、lease・fence、試行。秘密本文を含めない |
| OIDC link procedure | 許可消費、制限session、transaction、期限、失効・回復 |
| change checkpoint | source、schema、適用位置、派生データ版、整合確認状態 |

独自状態はWP互換tableの意味を変えない別領域へ置く。物理DDLと鍵管理は実装時にmigrationとして版管理する。単なるchecksumを配布元の真正性・署名の代わりにしない。

移行は既存サイトからの直接転送とfile取込みを維持し、診断、拡張受入、初期同期、再開、最終同期、元WPとのHTTP／DB比較、切替、切戻しを持つ。元サイトと移行先の二重更新を制御し、切替時の書込み主体を明確にする。siteurl等の変換、serialized値内のURL、media、認証設定は変換記録と比較を持つ。

本体・互換層・同梱基盤は一つの検証済みreleaseとして切り替える。新旧workerを一時二重起動し、profile・health確認後に新規要求を切替え旧側を排出する。jobs・書込み・追跡の担当をfencingし、旧側が独立に更新し続けないようにする。

backupはDB、uploads、コード束、設定、独自状態、鍵の復旧手順を対応付ける。schema変更なしのコード差戻しと、変更ありのDB復元を区別する。復元後はcache・索引・旧permitを破棄し、OIDC一度限り許可とjob結果を照合する。RPO・RTO・保持・停止上限は受入前に固定する。

配備目標はLinux Compose、Railway、LightsailとMySQL／MariaDBを継承する。cross compileやCIの固定実行を、三環境での新規導入・更新・backup・復元・障害受入の代用にしない。

## 14 診断と計測

要求ごとに、Rust標準operation、PHP拡張callback、PHP言語操作、標準PHP委譲、境界回数・転送byte・同期時間、物理SQL・監視SQL、共有利用、出力・回収を分けて集計する。移植済み分野でdelegateが実行されたらprofile違反として検出する。

通常ログにpassword、Cookie、Authorization、OIDC code・token、記事本文、raw SQL、例外の任意本文、絶対pathを含めない。詳細調査は別権限・保存期間・公開範囲を持つ。未計測を0、unknownを正常、再接続を整合回復として表示しない。

追加1画面は、設定と実稼働、共有停止理由、自動算出予算と観測元、worker状態、拡張判定、release・移植profile、更新・復旧の次の操作を示す。内部ABI調整や未移植の穴埋めを通常の利用者作業へ転嫁しない。
