# RecastPress v0.6 PHP互換界面の詳細設計

作成日：2026-11-10（日本時間）。設計版：v0.6。
[基本設計](architecture.ja.md)のPHP境界を定める。以下のprotocolと生成物は新規設計であり、既存のDB RPCやRust hooksで実証済みとは扱わない。

## 1 採用方式と成立条件

初期方式は、PHPの互換関数・class、PHP dispatcher、Rustの継続可能な状態機械を組み合わせる。PHPは元runtimeの値・参照・objectを保持する。RustはWP標準処理を進め、PHP実行が必要なら効果を返して待つ。

PHPの外側driverがroot操作を開始し、Rustから届く `InvokePhp` を実行する。callbackがRust APIを呼ぶと、そのPHP wrapper自身が子のexchange loopへ入り、子操作の完了後にcallbackへ戻る。Rustは外側frameをsuspendして子を実行する。hostが実行中のWasm instanceへ直接呼び戻す必要をなくす構成とする。

成立確認の優先順は、実PHP-Wasmでの入れ子往復、参照・公開状態、例外・終了、標準hook、Queryである。合成Rust callbackだけで成立としない。純PHP shimで言語表現や同期費用が成立しなければ、同じ意味の契約を保ってZend bridgeを比較する。native PHPや別コアへ暗黙に切り替えない。

## 2 要求・操作context

| 型 | 内容と不変条件 |
| --- | --- |
| RequestContext | site、role、request世代、release／profile、settings版、deadline、budget、取消状態 |
| OperationFrame | operation ID、親frame、起動したPHP effect、標準処理のstep、ローカル計算状態、戻り先 |
| EffectFrame | effect ID、発行元operation、期待するPHP処理、使用可能handle、完了待ち状態 |
| PhpHandle | request世代、型、slot番号、slot世代、偽造・取り違え防止のtoken |
| PublicView | object／globalの対象、読む領域、観測番号、取得した値またはhandle |
| DbSessionRef | Rust側でRequestContextへ結び付けたDB actor。PHP入力で任意sessionへ変更しない |
| Completion | 正常返値、PHP throwable、要求終端、実行異常、取消、protocol違反を区別 |

operation ID・request IDは認可tokenにしない。受信フレームのsiteやroleを信用せず、認証済みのworker channelに結び付いたcontextと一致するか確認する。

Rust frameは共有cache lock、DB actorの排他的guard、Wasm実行を保持するmutable borrowを残したままPHPを呼ばない。DBのSQL実行を完了・中断してからcallbackへ進み、未読resultを別操作へ奪わせない。transaction自体は同じ接続に残せる。

## 3 継続プロトコル

### 3.1 メッセージ

| 方向 | 種別 | 内容 |
| --- | --- | --- |
| PHP→Rust | StartRoot | 要求ごとに一回。固定profileのroot処理を開始 |
| PHP→Rust | CallCore | API／定義ID、引数列・参照slot、現在のeffect ID、必要な公開状態 |
| Rust→PHP | InvokePhp | callback、仮想method、include、言語操作、公開状態のread／writeのいずれか |
| PHP→Rust | Resume | effect IDと正常結果、更新した公開状態・参照、またはthrowable handle |
| Rust→PHP | Return | 呼出し元wrapperへ返す値、参照返値のslot、公開状態への適用命令 |
| 双方向 | TerminateRequest | `exit`等の要求終端。未完了frameを再開しない |
| Rust→PHP | Abort | 資源・期限・protocol違反等。追加の通常API呼出しを拒否 |
| PHP→Rust | RequestFinished | shutdownと出力処理の結果。root ReturnだけでHTTP完成としない |

`InvokePhp` は任意コード文字列のevalを受け取る操作にしない。登録済みcallable、固定された定義、コード束内の検証済みpath、許可した言語操作から選ぶ。callback由来の通常PHP実行は同じruntimeで行う。

### 3.2 呼出し規則

1. `CallCore` は、現在実行中のPHP effectまたはroot driverからだけ受け付ける。
2. 子operationは親effectを特定する。兄弟の結果、完了済みeffectへの二重Resume、順序違いを拒否する。
3. 同一要求のPHP実行は直列とし、一つのWasm instanceへ同時に二つのcallbackを入れない。
4. 子operationが完了するまで外側は停止。子の終了後はPHP callbackが続き、その完了で外側を再開する。
5. 戻り時は返値だけでなく参照引数、公開状態、出力・header、throwableを処理する。
6. 任意のframeで取消し・要求終端が成立したら、残るframeは通常の結果で再開しない。

```text
root Query A
  InvokePhp(pre_get_posts, A)
    PHP: A.query_varsを直接変更
    CallCore(get_option, parent=callback-A)
      InvokePhp(option filter)
      Return(option value)
    CallCore(Query B, parent=callback-A)
      ... Bを完了 ...
      Return(B)
    Resume(callback-A)
  Aを再観測し、SQL構築を続行
```

子の入口はcallback完了前の変更を取り込む。外側が子の実行前に持っていたsnapshotを、そのまま再利用しない。

### 3.3 transportと検査

初期は既存のprivateチャネルを使い、制御metadataを閉じたJSON schemaで送る。非UTF8文字列・DB値はbyte長付きのhex表現で保持し、JSONの数値へ64bit整数を無条件に載せない。値タグ、整数の正規十進表現、浮動小数点のbit表現、順序付き配列entry列を区別する。重複key、未知field、不正長、余分な末尾byteを拒否する。

各frameはprotocol版、request世代、sequence、operation／effect相関、種別、payload長を持つ。外側の受信byte上限とdecode後の要素数・byte数の上限を両方検査する。長さ宣言だけを信じて巨大allocationしない。partial frame、EOF、送信停止、逆方向呼出し待ちにも全体deadlineを適用する。

フレーム内容を通常ログへ出さない。別processのhost／共有サービスとのIPCには相互のrole・site・release確認を加える。loopbackであることを認証の代わりにしない。

高頻度APIのbinary化・複数値の一括同期は、同じcallback順序・観測点を維持した場合にだけ導入する。副作用や再入を含むAPIを勝手にbatch化しない。JSONLを製品の最終性能方式とは決めず、V06-1で境界回数・byte数・同期時間を測る。

### 3.4 上限profile

`LimitsProfile` は少なくともframeのencoded／decoded byte数、要求合計byte数、配列depth・要素数、handle数、frame深さ、operation数、callback時間、DB結果、出力、queue長、全体deadlineを有限値で持つ。期限を子操作で延長しない。

V06-1用の初期比較profile案は、encoded frame 1 MiB、要求合計32 MiB、handle 16,384、入れ子frame 64を上限とする。これは試験を有限にする設計上の開始値で、製品対応値や既存PoCの実測値ではない。巨大値の分割転送は順序・snapshotを保つ場合だけ許し、総量制限を回避させない。製品値は代表操作の分布と圧迫試験から受入前に固定する。

超過は `ResourceLimit` として明示し、値の切捨て、falseへの置換、元PHPによる再実行をしない。protocol版・limits profileの不一致は要求開始前に拒否する。

## 4 値、参照、handle

### 4.1 二つの値領域

| 領域 | 表現 | 利用条件 |
| --- | --- | --- |
| 値として閉じる部分 | null、bool、i64、float bits、bytes、順序付きkey/value | 参照・object identity・resource等に依存しないと確認した処理 |
| PHP言語状態を持つ部分 | PHP側のopaque handle | object、closure、参照cell、循環配列、resource、PHP例外等 |

既存 `PhpValue` は前者の限定実装として再利用する。後者を `Unsupported` やnullへ置換して処理を継続しない。RustはWP標準の判断に必要な値だけを観測し、PHP heap全体を複製しない。

### 4.2 handleと参照cell

PHP registryはobjectやcallableを強参照し、参照引数は実際のPHP参照としてslotへ結び付ける。同じobjectは同一identityとして扱う。変数の参照cellと、そのcellが現在指すobjectのIDは分ける。globalが別objectへ差し替わっても、元objectへの別aliasが同じidentityを保てるようにする。

配列は値渡しのcopy-on-writeと `&` による参照を区別する。参照を含む配列・循環構造は、JSONの木へ展開せずPHP内へ保持する。Rustからは `ReadSlot`、`ReadField`、`AssignSlot`、`AssignField` 等の明示操作で必要な観測・更新を要求する。変更は固定版の元コードと同じPHP代入・unset・配列更新として行い、全objectの再生成で代用しない。

引数を参照化するときは、元signatureが要求する参照だけをwrapperが取る。通常の値渡し引数を無条件に参照に変えない。参照返値もPHPの返値slotとして返し、コピーしたRust値を参照返値と表示しない。

PHP registryからslotを解放しても、PHPユーザーコードの別参照を破壊しない。再利用slotには世代を付ける。終了後・別request・型不一致・失効slot・権限外のhandleを拒否する。object／resourceのdestructorや言語副作用を、Rust側の任意のdrop順へ置き換えない。

### 4.3 言語操作

clone、PHP serializer、比較・cast等、PHP言語の意味が必要な操作は `InvokePhp(LanguageOp)` として元runtimeで行える。magic methodやwarning、throwable、再入が起き得る操作を純粋なcodecと見なさない。

WPの「いつclone・比較・serializeするか」はRustの標準アルゴリズムが決める。PHPがWP関数全体を実行する委譲とは区別する。短絡条件やserializeの呼出し回数も固定版と比較する。

共有cacheへ要求内handleを出さない。共有可能なbytesとmetadataへ変換する処理は、型・identity・clone・magic method・再構築の副作用を含む別契約とする。成立しない値は要求内の互換経路で扱い、永続object cache対応を偽装しない。

## 5 公開状態の同期

### 5.1 同期点

| 同期点 | 処理 |
| --- | --- |
| PHP→Rust API入口 | 対象objectと関係するglobals・登録表の必要領域を読む |
| Rust→PHPの前 | その時点で元PHPから見えるはずの更新をPHP実体へ適用 |
| PHP callbackから子APIへ | callback途中の直接変更・global差替え・aliasを観測 |
| PHP callback終了・throw時 | 変更を観測し、Rustが保持した古い公開snapshotを無効化 |
| Rust operation再開 | 依存する公開状態を再取得して標準処理を続ける |
| 要求終了 | 最終状態を診断用に必要範囲だけ確認しhandleを失効 |

公開propertyをprivateへ変更して `__get`／`__set` だけで捕捉する方式は採らない。PHPの通常の公開propertyに対する直接書込みや配列要素更新を、その仕組みだけで観測できないためである。[PHP公式 Overloading](https://www.php.net/manual/en/language.oop5.overloading.php)

### 5.2 観測領域と整合規則

API定義ごとに `read_set`、`publish_set`、`global_bindings`、`reference_slots` を列挙する。Queryではquery vars、条件フラグ、posts、件数、Loop位置、main queryとの関係等が対象になる。必要領域が不完全なら当該profileは未受入とする。

PHP shimには汎用の正しいdirty検出があると仮定しない。初期は必要領域を毎回読み直す。PHP側が単調な観測番号を発行し、古い観測に基づくRustのwrite命令を拒否する。観測番号は任意PHP書込みの自動検出番号ではなく、界面で読み直した順序を表す。

Rustが変更をpublishした後にPHP callbackが同じ値を変更した場合、元アルゴリズムの次の観測点ではそのPHP値を読む。外側frameがcallback前のsnapshotを後から一括writeして子の変更を消さない。元処理が保持するローカル変数のcopyと、公開状態へ張った参照は別にモデル化する。

未知の動的propertyはPHP内に保持し、型付きRust構造体への変換で落とさない。標準propertyが想定外の型になった場合も、勝手に既定値へ修復せず、元のwarning・例外・変換を比較する。property読取り自体がmagic methodを実行する場合、その実行も継続effectとして扱う。

## 6 関数・class・継承

生成するPHP宣言は、名前、namespace、parameter名と順序、既定値、参照、variadic、返値参照、visibility、static／final、継承関係、property初期値を固定sourceへ対応させる。PHPのnamed argumentsを位置だけの独自契約へ変えない。追加の型宣言で元の受理範囲を狭めない。

`WP_Query`、`WP_Post`、`WP_User`、`WP_Error`、`wpdb`、`WP_Hook`等はPHPから通常利用できる表現を持つ。独自subclass・overrideがある場合、元処理が仮想dispatchする箇所を `InvokePhp(VirtualMethod)` にする。標準の定義へ直接進む箇所と、拡張overrideを解決する箇所を区別する。

`parent::method()` から標準shimへ入った場合は、定義IDで標準Rust本体を指定する。もう一度同じoverrideを解決して無限再帰させない。`$this`、calling scope、protected呼出し、late static bindingを必要とする箇所は、元PHPのscopeを保持するstubで実行して比較する。

class identity、`instanceof`、列挙、clone、serialize、ArrayAccess／Iterator、通常のReflectionによる公開signatureを対象とする。元PHPの実装ファイル名・行番号・実装本文の一致は別の差分台帳へ記録する。class名が一致しただけでclass互換としない。

## 7 hooksとdispatcher

### 7.1 所有と登録

Rustが発火位置、実行frame、priority反復、実行中変更、再入、current hookと回数を管理する。PHPはcallable実体と公開 `$wp_filter`／`WP_Hook::$callbacks` を持つ。Rustの内部登録はPHP公開表現へ反映し、PHP直接変更は次の必要な観測点で取り込む。

標準callbackも元の名前・priority・accepted argsで登録する。PHP表面の同名関数からRust標準実装を呼べるようにし、`has_filter`、`remove_filter`、`all`、登録置換から見える登録を省略しない。

callback identityは元の `_wp_filter_build_unique_id` 相当をPHPの実object・closureに基づき解決する。Rustの文字列IDだけでobject method・closureを同一視しない。hashが同じでも異なるcallableを混同しない。

### 7.2 反復と直接編集

固定版の `WP_Hook` が持つcallbacks、priorities、active iterations、nesting、doing_actionの相互作用を移植する。callback中の追加・削除で反復をどう調整するかは、元のmethodごとに決める。actionとfilter、`all`、引数数、返値の扱いを共通の単純loopへ丸めない。

公開callbacksを直接編集した場合も観測する。ただし毎回priority表を作り直すことで元の内部状態を「修正」しない。元の公開配列編集が内部のcached priority等へ及ぼす実際の挙動、warningや例外も固定sourceと比較する。直接編集、通常APIによる登録変更、global全体の差替えを区別する。

引数は元の参照関係とaccepted argsに従って渡す。戻ったfilter値、参照引数、current hook stack、実行回数を元どおり更新する。例外時の内部frameの残り方も元sourceで確認し、独自の一律復元を互換と決めつけない。要求を失敗にする場合のhost資源回収とは分ける。

### 7.3 移行段階

V06-1では元PHP `WP_Hook` を比較用に接続してよい。そのprofileではhook機構は `PhpCoreDelegate` と表示する。V06-2でRust反復へ切り替え、元hook本体を利用不能にした比較を通す。既存Rust hooksは反復の部品・テストを再利用し、PHP参照・公開callbacksの全面対応が済んだとは扱わない。

## 8 loaderと実装manifest

`CoreImplementationManifest` はreleaseと一緒に固定し、少なくとも次を持つ。

| 項目 | 意味 |
| --- | --- |
| baseline | WP archive／source、PHP、Rust source、ABI・schemaのhash |
| symbol／definition ID | 関数・class・method・標準callbackの識別 |
| implementation_kind | RustCore／PhpSurface／PhpCoreDelegate／ThirdPartyRuntime／Unavailable |
| source_origin | 元path・symbol・hashと由来の記録 |
| php_surface | 生成する宣言・互換path・signature・property |
| rust_target | 標準処理と同期契約、効果の種類 |
| dependencies | 必要symbol、loader段階、standard callback登録、能力 |
| delegates | 一時委譲の理由・範囲・退役段階・比較ケース |
| verified_profile | 対象機能・拡張条件・環境・証跡。未検証状態を許可済みにしない |

元 `wp-settings.php` を読み終えてから同名関数・classを差し替える方式は採らない。compat loaderは依存順に宣言と選択した実装だけを読み込む。pluggable関数は元のplugin読込み・既存定義確認の順序を保ち、pluginが先に提供した実装を重複宣言しない。その分野は拡張overrideとして記録する。

同じ元ファイルに移植済みと未移植のsymbolが混在するときは、構文を理解した生成・分割を行い、元sourceとの対応を記録する。副作用、定数、classのstatic初期化、条件付き定義も依存に含める。正規表現の一括置換だけで安全な分割ができたとはしない。

直接 `require_once` されるcore pathには、同じ宣言・必要な副作用を提供するshimを置く。`ABSPATH`、`WPINC`、include済み状態、plugin pathの観測を比較する。元PHP本体へ迂回できるpathを製品mountに残さない。source本文への依存まで等価に見せるものではない。

依存symbol欠落、二重宣言、移植済み本体とdelegateの重複、未検証profile、ABI不一致は起動または有効化前に拒否する。移植済みアルゴリズムを無効にして元PHP本体へ自動退避する設定は設けない。

ここでいう検証済みprofileは、コア・API・言語動作・能力の契約を指す。個別pluginが過去の比較fixtureに入っていることを全件必須とするallowlistではない。拡張の受入判断には既決の衝突・能力policyを適用し、任意コードの全挙動を検証済みと保証する意味にも使わない。

## 9 出力・例外・終了

出力は一つのPHP要求内のoutput bufferとSAPI順序を維持する。callbackごとの出力文字列を最後に連結して、`ob_*`やheader確定の観測を変えない。Rust生成の本文断片も、元処理で出力される位置で同じ出力系へ渡す。HTTPへ送信する権限はRustに限定する。

| 結果 | protocolと外側の扱い |
| --- | --- |
| Return | 元signatureに従う値・参照返値。公開状態の反映後にPHPへ戻す |
| PhpThrow | throwableを同じruntimeのhandleで保持。wrapperが同じ例外をthrowし、PHPのcatch／finallyを継続可能にする |
| RequestExit | PHP実行が通常返値なしで終了。suspend中frameを中止し、shutdown・出力・headerの結果を検査 |
| Fatal／Trap | 正常なcallback結果を作らず要求失敗。内部診断と公開応答を分ける |
| Cancelled／Deadline | 新規effectを拒否し、PHP・DB・workerを協調停止。副作用の確定状態を別に記録 |
| ProtocolError | channelと要求をpoisonし、以後の操作を受け付けない |

PHPの `exit` やfatalで純PHPのfinallyが必ずhostへ通知できるとは仮定しない。外側hostは終了status、出力・制御チャネルの終了、shutdown完了情報を照合する。意図的終了とtrapの分類が現runtimeで成立することをV06-T03で確認し、区別できない異常を成功にしない。

未捕捉の例外・fatal後に、元WPを起動して同じ処理を再実行しない。`wp_die`やredirect後の終了を含め、標準の意図的終端と資源超過を比較する。PHPによるheader確定と外部への実送信は別の観測として扱う。

## 10 成立判定と拡張手段

V06-T02〜T05の実PHP-Wasm試験を全て通し、直接property変更、参照配列、global差替え、nested Query、override、例外・終了を含めて、返値・状態・順序が元WPと一致することを最初の出口にする。

同期量または言語表現の問題があれば、対象operationと失敗条件を固定し、薄いZend bridgeやguest内の補助実装を比較する。C拡張でもguest pointerをhostの安全な参照と見なさず、GC・root・zval・例外・Wasmの所有を別途検証する。採用時はPHP ABI・ビルド・由来・license台帳とrelease固定へ追加する。

APIの対応範囲を縮小する必要がある場合は、失敗した契約と通常の拡張への影響を示して設計差分にする。難所を未検証のまま「薄いshimで吸収済み」と記載しない。
