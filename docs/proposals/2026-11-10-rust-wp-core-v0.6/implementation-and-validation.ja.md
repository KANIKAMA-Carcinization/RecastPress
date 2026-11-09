# RecastPress v0.6 実装順と受入条件

作成日：2026-11-10（日本時間）。設計版：v0.6。
本書は[改訂設計](README.ja.md)を実装・検証する計画である。今回作成したのは文書であり、以下の段階は未実施。v0.5のP0〜P7や旧T01〜T44の結果を付け替えない。

## 1 実装の原則

最初に難しい互換境界を小さく成立させ、Rustの業務コアをまとまった操作で置き換える。HTTP・DB・PHP-Wasmの既存成果は再利用する。独立した文字列関数の移植件数や、元WP全体を動かすための適応だけを主工程にしない。

各段階は、対象source・機能・profile、実装主体、PHP委譲、比較ケース、既知差分を固定して判定する。合格した範囲から元PHPアルゴリズムを退役させ、未移植の標準処理と拡張のPHP実行を区別する。

新規の0.6実装へ入る最初の変更で、workspace・lock・CLI・release metadataを0.6.0へ揃える。独立実験の0.0.0は独立のまま扱い、過去の証跡の版・source hashを書き換えない。開発用のv0.6版番号を正式リリース完了の意味にしない。

## 2 段階と出口

| 段階 | 主な成果物 | 合格条件 | 不成立時の見直し |
| --- | --- | --- | --- |
| V06-0 基点固定 | 固定source、既存成果の再検証範囲、機能母集団・移植台帳、比較環境 | T01。通常PHP、v0.5 PHP-Wasm、Rust新経路を同条件で比較できる | 旧証跡を現在sourceの成功にせず、再利用部品ごとに再確認 |
| V06-1 PHP界面 | dispatcher、operation stack、handle・参照、公開状態、異常終端 | T02・T03・T05の境界部分を実PHP-Wasmで通す。境界費用を測る | Zend bridge等を同じ契約で比較。未成立のまま大量API移植へ進まない |
| V06-2 Rust公開コア | Rust hooks・bootstrap・rewrite・Query・Loop・template選択 | T04〜T08。直接変更とnested queryを使うPHP拡張が動き、元Query本体を利用不能にしても一致 | 標準property・hook・依存の不足を修正。元WP全体への自動委譲で埋めない |
| V06-3 共通読書き | options、posts、taxonomy、meta、comments、usersの標準読書き | T09・T10・T13。DB・hook・cache・外部副作用と途中失敗が一致 | 原子化やretryで差分を隠さず、標準手順と観測を修正 |
| V06-4 共通認証とAPI | session・nonce・capability・OIDC、標準REST・AJAX、管理操作の共通コア | T11・T12。公開・管理・APIで権限と失効が一貫し拡張変更点を保持 | 部品成功を認証完成にせず、credentialから操作まで縦断 |
| V06-5 標準機能拡大 | blocks、標準描画・admin view、media、i18n、cron、install・upgrade | T17。標準処理の委譲を分野別に退役し、classic／block theme・editorを比較 | 未移植を互換界面と呼び替えず残存表へ記録 |
| V06-6 製品統合・移植監査 | 最適化、拡張受入、移行、三環境、両DB、一体更新・復旧 | T14〜T16・T18〜T20を含む全採用profile。機能の残存と未提供が明確 | 未合格は開発preview。対象を黙って減らし正式対応にしない |

V06-2の最初の大きな到達点は、既存PHP themeと `pre_get_posts`／SQL filters／結果filterを使うpluginを、Rust Queryで動かすことである。homeだけに限定せず、記事・一覧・検索・ページ送り・404・nested Loopを含める。block themeが未移植の段階はそのprofileを未受入とする。

認証や保存の設計はV06-1から境界へ組み込む。工程の後段に置くことは、先行previewで未受入の管理機能を公開する許可ではない。拡張を動かす最初の段階から専有領域・site・roleの能力制限を適用する。

## 3 最適化の進め方

最適化は業務コアの移植と区別して段階を持つ。

| 段階 | 内容と必要条件 |
| --- | --- |
| C0 | 共有cache・索引なしの基準経路。要求内のWP cache意味は維持 |
| C1 | Rust要求内cacheを接続。値・clone・found・group・期限・要求終了を比較 |
| C2 | 共有object・autoload等の固定shapeとCDC。T14・T15の生成競合・監視異常・再同期が条件 |
| C3 | 応答cache・内部索引・複数process。追跡期限、自動資源、拡張適格性、実送信の受入が条件 |

共有が不成立でもC0のRustコアは進められる。C2のPoC再利用は所有fixtureで行い、並行更新・外部変更・codecの条件が未成立なら本番共有を停止する。高速経路の非対応だけを理由にWP標準処理をPHPへ戻さない。

## 4 比較環境

比較は、A：固定元WP＋通常PHP、B：v0.5の元WP＋PHP-Wasm＋Rust基盤、C：v0.6 Rustコア＋PHP互換界面の三つを使う。BはWasm・host固有の差分を切り分けるために残す。

source、DB初期snapshot、theme・plugin、PHP extension、設定、charset・collation、timezone・locale、時計・乱数入力を揃える。MySQLとMariaDBを別に記録し、Windowsのnative Rust試験、Linuxの実PHP-Wasm、三配備先の製品操作を区別する。

| 観測面 | 比較するもの |
| --- | --- |
| API | 返値・型・配列順、参照返値、warning・例外・WP_Error |
| PHP状態 | object identity、public property、alias・global差替え、clone・serialize |
| hooks | 名前、順序、priority、引数・参照、登録変更、再入、current hook・回数 |
| DB | SQL・接続設定・session、結果metadata、DB最終状態、途中hookからの可視性 |
| 副作用 | mail・外向きHTTP・job・file、試行順、失敗後の残存 |
| HTTP | status、header順序・重複、複数Cookie、body、redirect、意図的終了 |
| 運用 | 期限・回収、コード切替、source変更、backup・復元、再起動 |

時刻・nonce・乱数等は試験用providerか対応関係で比較し、何を制御・正規化したか記録する。認証差分・呼出し順・余分なDB更新を消す正規化をしない。比較用観測コード自身が変更する挙動も確認する。

## 5 受入ケース V06-T01〜T20

### V06-T01 基点と比較器

WP archive・対象ファイルhash、PHP／host／DB／compiler、lock、コード束、fixtureを固定する。元WPの実行とv0.5のhome／404を再現し、意図的にhook順、SQL、header、参照結果を変えると比較が失敗することを確認する。旧Cookie変更後の未完了な全体検証は個別に再実行し、過去79 APIの成功を現在source全体へ流用しない。

### V06-T02 値・参照・公開状態

bytes・非UTF8、整数境界、float bit、配列keyと順序、false／null／missingを比較する。objectへの複数alias、配列要素への参照、循環構造、globalの参照cellとobject差替え、未知の動的propertyを含める。直接書込みの直後に子Rust APIを呼び、その子と外側再開が新値を観測することを確認する。別要求・終了後・slot再利用・型違いのhandleは拒否する。

### V06-T03 継続・例外・終了

Rust→PHP→Rust→PHPを複数段に入れ子にし、相関と順序を確認する。二重Resume、親違い、順序違い、未知field、重複key、oversize、途中EOF、停止を拒否する。PHPのcatch／finally、同じthrowable、callback内 `exit`、`wp_die`、shutdown出力、fatal、trap、hostcall停止、取消しを実PHP-Wasmで比較する。異常時に成功返値や途中成功応答を捏造せず、副作用の再実行をしない。

### V06-T04 hooks

同priority順、重複callback置換、object method・static・closureの解除、accepted args 0／少数／過剰、`all`、参照引数、再入、実行中の追加・削除・全解除を比較する。公開callbacks直接編集と `$wp_filter` 差替え、preinitialized hooks、Iterator／ArrayAccessを含める。標準callbackをPHP拡張から解除でき、Rustの不可視な処理として残らないことを確認する。元Hook本体を利用不能にした実行でも一致させる。

### V06-T05 class・loader・実装選択

signature・named args、既定値、visibility、final／static、`instanceof`、constructor、subclass override、`parent::`、protected呼出し、clone・serializeを比較する。MU／通常plugin、親子theme、pluggable定義、直接require_once、条件付き定義、二重宣言・symbol欠落を検査する。移植済み元本体を別pathやdelegate経由で呼べないこと、未検証profileを起動しないことを確認する。

### V06-T06 標準Query

home、単一記事、固定ページ、post type、分類、author、date、search、ページ送り、feed、404を比較する。公開／非公開／予約／password保護、meta・tax・dateの複合条件、sort・offset・limit、IDs・件数、空・重複・NULL・collation境界を含める。Query flags、SQL、返値、WP_Post表現、件数を観測する。

### V06-T07 拡張付きQuery

`pre_get_posts`で直接propertyと参照配列を変更し、同じcallbackからsecondary query・options読書きへ再入する。clause・request filters、`posts_pre_query`短絡、結果filter、`suppress_filters`、query subclassのoverrideを比較する。未知SQL shapeは索引を使わず同じDBへ送り、Rust Query本体を継続する。元PHP Queryを利用不能にし、delegateが0であることを確認する。

### V06-T08 Loop・template

main／nested Loop、rewind、reset postdata、global差替え、条件タグ、template hierarchy・child theme、template filter、load_templateのscope・extract・args・require_once、前後hookを比較する。PHP templateがRust標準APIを呼び、内側終了後に元と同じ範囲の状態が残ることを確認する。

### V06-T09 options・transients

defaultの指定有無、false／miss、pre_option短絡、alloptions・notoptions、autoload各値、add／update／delete、期限境界を比較する。object clone・magic serializer・filtersからの再入、更新不要、0件更新、DB error、cache順序を観測する。元option本体のdelegateなしで同じDB・hook・返値を得る。

### V06-T10 保存と部分失敗

投稿公開・非公開化・削除・復元、slug競合、revision／autosave、分類・meta・comment・userの標準更新を比較する。各hookからDBを読み、子保存・外部送信・例外を入れる。SQL送信前、送信後、COMMIT後、cache更新後、通知後に失敗を入れ、元と同じ可視状態または明示した安全上の差分になることを確認する。途中からPHPで再実行せず、結果不明を確定失敗にしない。

### V06-T11 認証・権限・OIDC

username／email、raw passwordと入口正規化、hash形式、rehash、cookie・session token・nonce、role／meta capability、失効、認証filters・pluggableを縦断する。匿名・購読者・投稿者・編集者・管理者等の標準roleを公開・admin・RESTで照合する。OIDCのoff／任意／必須、一度限り許可の競合、制限session、期限内再開、偽装・再送・issuer／audience違い、回復・復元・mode変更を検査する。連携未確定の通常操作とPHP汎用実行を拒否する。

### V06-T12 REST・AJAX・管理

標準controller、schema・validation、permission・nonce、Cookie／header、WP_Error、pluginのroute・permission callbackを比較する。同じ投稿操作をadminとRESTで実行し、DB・権限・hooksが共通コアを通ることを確認する。PHP管理viewを残すprofileでも別の標準認証・保存コアを起動しない。

### V06-T13 DB・wpdb

標準API、prepare・escape、各返値mode、NULL・binary・非UTF8・大整数・重複列・0行metadata、raw ERR、公開wpdb状態を比較する。同一sessionのread-your-writes、transaction・savepoint・rollback、charset・sql_mode、一時表、lock、接続断・未読結果・清掃失敗を含める。mysqli直接依存と資格情報取得を拒否し、許可済み通常SQLと混同しない。

### V06-T14 追跡と再同期

sourceと読取り接続の同一性、snapshot＋開始位置、COMMIT／ROLLBACK、未処理イベント、生成中の並行更新、適用中の再格納競合、監視EOF・error・取消し・panic・buffer超過を比較する。rotation、ログ欠落、DB再起動・復元、source変更、複数processを含める。旧permit・ticketを無効化し、正常な正本DBから読めること、再同期確認前に共有へ戻らないことを実PHP-Wasm経路でも確認する。

### V06-T15 cache・索引・実送信

要求内cacheの型・clone・found・group・原子操作・期限を比較する。共有codecのmagic method・副作用と復元後の状態、応答cacheのCookie・Vary・query変種・書込み・hook副作用を確認する。全パージ中の生成、旧ETagの304、HEAD・Range、非公開化、通知欠落を実ソケットで観測する。索引はshape・session・collationの非対応時に通常SQLへ戻り、Rustコアを維持する。

### V06-T16 資源・隔離・回収

instance・frame・handle・DB結果・RPC・bodyの境界値と超過、同時要求、異なるsite／role、Wasm OOM・命令budget・hostcall停止・client切断を検査する。取消後の所有PID・actor・接続・file・予約を照合し、回収前に次要求へ再利用しない。自動縮小・復帰、未知の制限、共有cgroupの二重配分、更新時の余裕も確認する。

### V06-T17 標準機能全体

block theme・classic theme、標準／動的block、nested context、theme.json・style、shortcodes・widgets、翻訳・KSES・escapingを比較する。media upload・派生画像、通知mail・外向きHTTP、cron・予約公開、install・upgrade、editorの操作列を含める。標準PHP renderや管理viewが残る場合は未移植として報告する。

### V06-T18 拡張受入と移行

既知衝突、具体的な疑い、未対応能力、cache可否だけ不明を分ける。複合plugin、MU、theme、更新時だけ増える禁止処理、直接配置・復元を確認し、拒否・保留で既存正常版を保持する。報告資料の秘密除外、利用者確認、自動送信なし、報告だけで解除しないことを確認する。直接転送／file取込みの再開・最終同期・比較・切替・切戻しを両DBで通す。

### V06-T19 配布・更新・復旧

Compose・Railway・Lightsailで新規導入、再起動、秘密更新、code更新、DB・追跡障害、backup・別環境復元を行う。本体・PHP／host・互換層の独立差替えを拒否し、新旧workerとjob担当の切替を確認する。schema変更あり／なしの復旧、旧session・OIDC許可・cache・checkpointを検査する。由来・対応ソース・依存licenseの未完了を出荷済みにしない。

### V06-T20 元実装の退役と効果

移植台帳、loader manifest、読込みtrace、標準operation trace、delegateカウンターを照合する。対象経路の元PHPアルゴリズムを利用不能にしても同じ結果になることを確認する。別名コピーやhidden delegateを含めて監査する。機能母集団、残存PHP、未提供を一覧にし、移植率は機能・実行経路と定義を併記する。性能は次節の同条件で比較する。

## 6 性能・資源・運用の判定

計測を、A通常PHP、B v0.5経路、C v0.6経路のそれぞれで行う。最適化なし、要求内cacheのみ、共有あり、応答cacheありを分ける。cold、warm、全パージ後、更新・障害・復帰、同時要求を含める。

| 指標 | 判定のための観測 |
| --- | --- |
| 応答 | p50／p95／p99、throughput、error率、入力規模・同時数 |
| コア移植 | 元PHP標準処理の実行・委譲、Rust標準処理、拡張callback、core PHP読込み |
| 境界 | operation数、同期範囲、byte数、codec時間、callback・待機時間 |
| DB | 対象SQL・監視SQL、接続数、DB時間・転送量・負荷、変更追跡遅延 |
| 資源 | Rust／worker RSS、Wasm memory、共有範囲全体、論理予約、peak、取消後の残存 |
| 運用 | 導入・移行・更新・復元の時間、利用者の操作、RPO／RTO、停止時間 |

Rustへの移植の価値と性能改善を別に報告する。PHPの残存固定費や境界費用によって遅くなった条件も記録する。細かな往復・同期を減らす改善は、互換性の観測点を変えずに行う。SQL省略1回と多数の監視SQLを相殺せず、総費用で最適化を評価する。

数値合格値はV06-0〜1で測定計画を固定し、該当機能の製品有効化前に受入profileへ記載する。未測定の改善率・開発期間を約束しない。反映期限・復旧目標が未決の機能を、正式対応として有効化しない。

## 7 移行と退役

各分野は `Inventoried → Implementing → DifferentiallyVerified → Integrated → RetiredPhpBody` を追跡する。独立部品の比較成功と実要求への統合を区別する。

1. 対象symbol・公開状態・hooks・副作用・依存を列挙する。
2. 元PHP本体を比較系に保持し、RustとPHP表面を実装する。
3. 両DB・実PHP-Wasmで対象の正常・拡張・異常系を比較する。
4. manifestの処理主体を切り替え、同じコード束で統合試験する。
5. 元本体を製品mountから外し、利用不能にした比較とtrace監査を行う。
6. 標準PHP委譲のentryを削除し、履歴と由来を残す。

独立操作の開始前に元PHP委譲を選ぶ移行profileは許すが、callback開始・書込み後の再試行には使わない。関連分野の混在に互換根拠がなければ、そのprofileは有効化しない。委譲は対象・回数・理由・出口を表示する。

## 8 実装前・出荷前に閉じる項目

| 項目 | 本版の選択と残る作業 | 必要な時点 |
| --- | --- | --- |
| 継続方式 | PHP dispatcher方式を先行。実Wasmの入れ子・終了・参照の成立と必要ならZend bridge比較 | V06-1 |
| 同期契約 | PHP公開状態を境界ごとに観測。各APIのread／publish集合と元ローカル参照の対応を列挙 | 各分野の着手時 |
| loader | 生成宣言＋symbol単位選択。構文分割・条件定義・path・pluggable依存の抽出 | V06-1〜2 |
| 完全な機能母集団 | 単一サイトを初回profile。全API列挙とmultisite等の未提供台帳を完成 | V06-0、V06-6監査 |
| PHP値codec | 要求内handleを採用。共有object codec・magic methodの契約は実証して追加 | C1〜C2 |
| 独自table・DDL | 能力を分離。導入・更新時と通常要求で許すprofile・DB権限を固定 | 拡張を受入れる前 |
| 共有整合 | source・snapshot・COMMIT・pendingを結合。並行生成・再格納の証明と両DB試験 | C2前 |
| 反映・撤回 | 通常反映、非公開化、監視異常・process間失効の最大時間と保証範囲 | 本番共有前 |
| 資源値 | 有限の試験profileから実測。製品のdeadline・全上限・配分・回収期限を固定 | V06-1、V06-6 |
| OIDC移行 | 制限sessionと原子的消費。期限・回復・既存session・復元を固定 | V06-4 |
| WP対応版・保守 | 固定比較版と出荷版を区別。source更新差分、修正配布・自動更新条件を定義 | V06-0、出荷前 |
| backup／運用 | RPO・RTO・保持・停止目標、三環境と両DB、license・build由来の受入 | V06-6 |

既決の構成をもう一度選択する質問一覧にはしない。実装側で調査・測定して具体化し、互換範囲・利用者負担・停止条件を変える必要がある場合は、失敗条件と影響を設計差分として示す。

## 9 今回の文書作成時の確認

今回確認したのは、参照した設計・コード・保存済み証跡の範囲、文書間の責務・ID・相対リンク・版の整合性である。新しい互換界面、Rust Query、保存・認証の縦断試験、性能・配備試験は実行していない。既存PoCを再利用できる見込みと、v0.6の受入成功を区別する。
