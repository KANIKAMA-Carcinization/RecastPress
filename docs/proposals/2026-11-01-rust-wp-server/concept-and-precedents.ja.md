# RecastPress v0.5 コンセプト・先行事例と受容状況

作成日・調査日：2026-11-03（日本時間）<br>
対象：v0.5の[概要設計](README.ja.md)・[基本設計](architecture.ja.md)の背景資料<br>
位置付け：会話でのコンセプト明確化、外部調査、AIによる第三者レビューの記録

## 1 記録の区分

本書では、ユーザーが明示した製品コンセプト、出典が報告する事実、利用者の個別経験、AIの評価を分ける。先行例の成功・失敗や普及状況を、そのままRPの実装結果へ転用しない。

| 区分 | 扱い |
| --- | --- |
| ユーザーが明示したコンセプト | 第2章に記録し、概要・基本設計へ反映する |
| 外部記事・公式資料の報告 | 第3章と第4章。数値の対象・時点・比較条件を限定する |
| 利用者の声 | 第4章。個別の自己申告であり、賛否の比率や市場全体を代表しない |
| AIによる評価 | 第5章。出典の著者の主張や、ユーザーが採用した新たな仕様として扱わない |

設計v0.5・実装0.5.0の版は維持する。2026-11-01の技術方針、受入policy、旧v0.45の要求・試験・判断履歴を変更せず、実装完了やP0〜P7の通過を本書で宣言しない。

## 2 ユーザーが明示したコンセプト

2026-11-03の議論で、次の位置付けが明確になった。

1. WP本体までをモノリシックに一体化し、WP専用のアプリケーションサーバーを提供する。
2. FrankenPHPより用途を狭くし、安全で最適化された実行・運用環境を狙う。これは比較実験済みの安全性優位を主張するものではない。
3. 速度・資源最適化も目標とするが、安全性、統合運用、既存サイトの互換性と利用者負担の軽減より優先度を下げる。
4. Wasmは内部的な実装手段とし、利用者がその互換性問題を意識しなくて済むことを目標とする。
5. テーマ・プラグインの用途がWPへ定まっていることを、対象操作・必要能力・比較シナリオを絞れる利点と捉える。
6. Rust移植について、公開成功例を集めて採用一般の是非を繰り返し論証する優先度は低い。利益を具体化できる実装と運用を重視する。

概要・基本設計では、一体化を製品の責任範囲として具体化する。既存の複数プロセス・ワーカー・内部サービスの分離、外部DB・OS・Dockerの責任境界は維持する。受入済みコードの内部差分を製品側で吸収する目標と、導入前に説明する非対応・拒否・保留の契約を区別する。用途の限定を、任意のプラグインの実装を完全に判定できる前提や、全件allowlist制へ読み替えない。

## 3 3軸の先行事例調査

### 3.1 サーバーサイドWasmによる既存コードの互換実行

言語runtimeと既存コードをWasm環境へ持ち込み、ホストとの接続を実装する事例を対象とする。Wasm向けに新規開発された関数と、既存Webアプリ全体の互換実行は区別する。

| 出典 | 出典が報告する方式・成果 | 対象と限界 |
| --- | --- | --- |
| [Shopify: Introducing Ruvy](https://shopify.engineering/introducing-ruvy)（2023-10-18） | ruby.wasmを利用し、Rubyコードと必要ファイルをWasmへ同梱。VMとコードを事前初期化し、通常のruby.wasm構成に対して起動・実行を約20%改善した固定例を報告 | 小規模なRubyプログラムの比較。Rails等の既存アプリ全体の互換性や、native Rubyより20%速いという結果ではない |
| [Wasmer: Running PHP blazingly fast at the Edge with WebAssembly](https://wasmer.io/posts/running-php-blazingly-fast-at-the-edge-with-wasm)（2024-05-23） | WordPress・Symfony・Laravelの実行を紹介。例外処理、通信、仮想FSの修正と、OPcache導入によるWasm構成の約3倍の改善を報告 | 改善前後のWasm比較であり、native PHPの3倍という意味ではない。発表時点の永続化等の制限を、現在のサービス全体へ一般化しない |
| [WordPress.com: WordPress Studio Just Got Faster](https://wordpress.com/blog/2026/07/13/faster-wordpress-studio/)（2026-07-13） | Native PHPの標準化でページ読込み30〜50%高速化、メモリ使用量が従来の3分の1未満になったと報告。当時はSandboxも選択可能 | ローカル開発製品の公式報告。RPのRust直接実行や本番WPサイトに同じ差が出ることを示さない |
| [Python公式: WebAssembly platforms](https://docs.python.org/3/library/intro.html#webassembly-platforms) | Emscripten／WASI上のPython、POSIX API・プロセス・通信・ファイル等の制約とruntime／ビルド条件への依存を説明 | 言語runtimeの移植と、OS機能を含むアプリ互換性の範囲を確認する資料。サーバー採用率の資料ではない |

Studioの[現行runtime説明](https://developer.wordpress.com/docs/developer-tools/studio/php-runtimes/)では、1.24.0でSandboxを廃止し、Native PHPへ移行するとしている。7月の記事の「Sandboxを選択可能」は当時の状態として保持し、現在の選択肢の説明に流用しない。

### 3.2 WordPressの実行基盤・互換層・派生実装への移行

| 出典 | 出典が報告する方式・経験 | 対象と限界 |
| --- | --- | --- |
| [WpDotNet概要](https://docs.peachpie.io/scenarios/wordpress/overview/)・[拡張追加の説明](https://docs.peachpie.io/scenarios/wordpress/add-plugin-theme/) | WPのPHPソースをPeachPieで.NETへコンパイルし、ASP.NET Coreへ組み込む。拡張もコンパイル・解析し、不成立なら理由を表示して拒否する | WPをC#で手作業再実装した事例ではない。参照資料の提供時点・版を超える現行の全拡張対応は主張しない |
| [SQLite Database Integration](https://wordpress.org/plugins/sqlite-database-integration/) | wpdb APIを維持し、MySQLのSQL・意味をSQLite向けに変換・エミュレーションする。3.0では専用lexer・parserを備え、約2年・15人・160 PRの開発を報告 | RPはMySQL／MariaDBを正本に残すため、SQL方言変換を同じ規模で実装する決定ではない |
| [WordfenceとSQLite／Playgroundの互換性報告](https://wordpress.org/support/topic/sqlite-and-wordpress-playground-compatibility/) | wpdbのdbhがmysqliであるという前提でエラーが発生。直接利用を無効化する暫定策と型検査の追加を提案 | 個別の障害報告。担当者は開発・QAへ転送しており、フォーラムのResolved表示だけで修正完了とは判定しない |
| [ClassicPress: Help Test v2.0](https://www.classicpress.net/help-test-classicpress-v2/)（2023公開） | WP 6.2を基に再フォークし、拡張の互換範囲を拡大。省いたブロック関数にpolyfillを提供するが、全エラーを防げずFSEテーマは非対応と説明 | 同言語のフォークでも上流追従・機能差・拡張互換の管理が必要だった事例。この記事の既知制限を現行全版の状態とは扱わない |
| [FrankenPHPのWP統合](https://frankenphp.dev/docs/wordpress/) | WPのPHP資産を保持し、サーバーと配備基盤を変更する手順を案内 | WP本体の版・DB経路・キャッシュ等をRPのように専有・一体管理する製品と同一ではない |

### 3.3 Rustへの全面・部分移植

| 出典 | 出典が報告する理由・成果 | 出典が挙げる条件・注意 |
| --- | --- | --- |
| [Shopify: YJITのRust移植経験](https://shopify.engineering/porting-yjit-ruby-compiler-to-rust)（2022-05-11） | CRuby内のJITをCからRustへ約3か月で移植し、保守性・抽象化・コード構成を評価 | Cとの境界、ビルド、bindgen、所有権、unsafeの扱いに苦労。移植中は大幅な設計変更を控えた |
| [Discord: GoからRustへ](https://discord.com/blog/why-discord-is-switching-from-go-to-rust)（2020-02-04） | Read Statesという特定サービスの当時のGC・メモリモデルに起因する遅延を調査し、Rust化後の改善を報告 | 全システムのRust化を推奨していない。2020年の条件を現行Goとの一般比較にしない |
| [Dropbox: 同期エンジンの再構築](https://dropbox.tech/infrastructure/rewriting-the-heart-of-our-sync-engine)（2020-03-09） | 約4年でRust製Nucleusへ置換。データモデルとテスト可能性の根本的な変更を説明 | 多くの環境では悪い判断になり得ると明記。局所最適化・段階的改善を尽くしたか、要員や開発速度低下を受け入れられるか等を判断項目にした |
| [Joel Spolsky: Things You Should Never Do, Part I](https://www.joelonsoftware.com/2000/04/06/things-you-should-never-do-part-i/)（2000-04-06） | 全面書換えで修正の蓄積・暗黙仕様を失い、出荷が遅れる危険を批判。局所的な置換・改修を支持 | Rust固有の批判ではない。公開成功例だけを比較材料にしないための一般的な反論 |

段階的な置換については[Martin Fowler: Strangler Fig](https://martinfowler.com/bliki/StranglerFigApplication.html)（2024改稿）も参照した。旧機能を再利用して境界から置換する考え方の資料であり、PHPを継続利用するRPを全面置換計画へ変更する根拠にはしない。

## 4 2つの技術コンセプトの受容状況

ユーザーが調査対象として確認した2つは、既存PHP資産を保持したサーバー基盤刷新と、既存アプリのサーバーサイドWasmによる互換実行である。以下は2026-11-03に確認した公開資料の記録であり、RPに対する第三者の評価ではない。

### 4.1 PHP資産を保持したサーバー基盤刷新

| 観測するもの | 公開資料の事実 |
| --- | --- |
| 制度的な支援 | [PHP Foundationの2025-05-15発表](https://thephp.foundation/blog/2025/05/15/frankenphp/)は、FrankenPHPへの公式支援、保守・開発参加、PHP公式GitHubへの移管を表明。元メンテナーによる運営は継続する |
| フレームワーク統合 | 同発表はLaravel・Symfony・Yiiのworker統合を説明。[Laravel Octane公式資料](https://laravel.com/framework/docs/octane)にも選択肢として掲載されている |
| 商用サービス | [Laravel CloudのCompute説明](https://laravel.com/cloud/docs/compute#laravel-octane)は、利用者がOctaneを有効にした場合にFrankenPHPで提供すると明記。全利用者が標準で使うという意味ではない |
| 周辺製品 | [Tidewaysの対応一覧](https://support.tideways.com/documentation/compatibility-and-requirements.html)がFrankenPHPを対応SAPIとして掲載 |
| 注目度 | [公式GitHub](https://github.com/php/frankenphp)の取得時表示は約11.5k stars・508 forks。本番導入数や稼働サイト数ではない |
| 利用調査 | [Zend 2025年PHP Landscape Report](https://www.zend.com/system/files/report-zend-2025-php-landscape-report.pdf)の12ページは、FrankenPHPが自由記述で繰り返し挙がったと説明。Caddyの利用率をFrankenPHP単独の利用率へ読み替えない。調査は2024年10〜12月、561有効回答であり、現在の市場占有率ではない |
| 別の先行基盤 | [Temporal公式PHPサンプル](https://github.com/temporalio/samples-php)は、Activities／Workflowsの実行にRoadRunnerのアプリケーションサーバー・監督機能が必要と説明。PHP資産と別言語の基盤を組み合わせる方式の公式採用例 |

利用者の個別経験は次のとおり。

- [FrankenPHP — any reason why not?](https://www.reddit.com/r/PHP/comments/1lqpkfq/frankenphp_any_reason_why_not/)には、5つのWebアプリとSaaSでの本番利用、Dockerでの開発体験、新規案件での選択を評価する報告がある。同じ議論には設定の難しさや、WordPressをPHP-FPMへ戻した経験もある。
- [Symfonyアプリの移行経験](https://www.reddit.com/r/PHP/comments/1vo4ntm/we_moved_a_symfony_app_to_frankenphp_worker_mode/)は、性能以上にバイナリ・設定・証明書管理の簡素化を評価する。体感約20%という速度差は、本人が比較記録を保持していないと明記しており、測定結果として採用しない。

利用者投稿は選択された少数の自己申告であり、肯定・否定の比率を示さない。今回の資料からFrankenPHP単独の本番導入数・市場シェアは断定できない。

### 4.2 サーバーサイドWasmによる互換実行

| 観測するもの | 公開資料の事実と対象範囲 |
| --- | --- |
| 既存言語・Webフレームワークの商用実行 | [Cloudflare Python Workersの正式提供発表](https://blog.cloudflare.com/python-workers-ga/)（2026-09-21）は、PyodideでWasm化したCPythonを使い、FastAPI・Django・Flask等を実行すると説明。GAは提供者の正式サポートを示すが、導入社数や任意Pythonアプリの無変更動作を示さない |
| PHP用途の提供 | [WasmerのPHP/Wasm発表](https://wasmer.io/posts/running-php-blazingly-fast-at-the-edge-with-wasm)と[対応言語・フレームワーク](https://docs.wasmer.io/edge/learn/supported-frameworks-and-languages/)は、PHP・WordPress等の実行経路・テンプレートを提供。PHP用途の本番顧客数・稼働サイト数は今回の資料では不明 |
| WP実行への利用規模 | [WordPress公式の2025年振返り](https://wordpress.org/news/2026/01/wordpress-playground-speed-stability-momentum/)は、Playgroundを140万回利用、上位1,000プラグインの99%でインストール・有効化が成功したと報告。ブラウザー・CLIでのデモ、教育、評価、開発を含み、本番サーバー数や全機能互換率ではない |
| サーバー基盤での商用採用 | [Fastly Computeの公式説明](https://www.fastly.com/documentation/solutions/tutorials/compute/introduction-to-compute/1-introduction/)は、WasmtimeによるWasmの実行基盤を説明。既存CMS全体の互換実行とは別の用途 |
| 広い母集団の採用調査 | [CNCF 2025年調査・2026年公開](https://www.cncf.io/wp-content/uploads/2026/01/CNCF_Annual_Survey_Report_final.pdf)の12ページは、該当347回答の65%が本人・組織ともWasm経験なし、17%が組織に経験あり本人にはなし、5%が本人・組織ともデプロイ経験ありと報告。Wasm全般の設問であり、サーバー互換実行だけの採用率や本番稼働率ではない |

[Python Workers正式提供への利用者反応](https://www.reddit.com/r/CloudFlare/comments/1wmcmdb/python_workers_are_now_generally_available/)には、試用・移行への関心と、起動時間や無料枠での実用性への疑問がある。技術への賛否全体や、商用導入の規模を示す標本としては扱わない。

## 5 AIによる評価・議論の整理

本章はAIによる第三者レビューであり、出典の著者の主張と区別する。助言を新たな必須機能・承認手続き・受入policyへ自動昇格させない。

### 5.1 当初のレビューと評価軸の修正

当初のレビューは、PHP資産の再利用、Rust／PHPの責務分離、正本と派生データの区別、最適化停止時の通常経路、固定基準との差分比較を評価した。一方、PHP/Wasm・RPC・DB接続層の費用、キャッシュ適用率、拡張制限、更新・保守の負担を課題として挙げた。

その後のユーザー説明により、性能改善を主な成否判定に置いた評価の重心を修正した。RPの中核仮説を、WP本体まで責任範囲に含めることで、安全で予測可能な実行環境と一貫した運用を提供できることと捉える。速度差が小さい場合も、設定、診断、更新、復旧、互換性で得た利益を評価対象にする。

課題の存在は維持するが、解決すべき実装・運用条件として扱う。既決のRust採用やPHP再利用を一般論から繰り返し再審議する理由にはしない。内部Wasmの費用も、統合運用・隔離・能力制御・互換性を含む製品全体の価値と対応させて評価する。

### 5.2 先行例からの読み取り

- PHP資産を保持した基盤刷新は、FrankenPHPの組織的支援、フレームワーク・商用サービス・周辺製品への統合から、本番用の有力な選択肢として受容が進んでいると評価した。市場の主流という断定はしない。
- サーバーサイドWasmによる互換実行は、Cloudflare Python Workers等の商用先行例があり、用途ごとに受容が進む段階と評価した。PHP/Wasmでの本番サイト運用については、導入規模を確認できる公開証拠が限られた。
- 言語runtimeをWasmで動かす成功、WP本体の起動、個別プラグインの動作、製品運用の成立は、それぞれ確認範囲が異なる。3軸の成功例は互いの達成証明を代替しない。
- WPへの用途限定は、汎用PHP基盤より検証対象を具体化しやすいと評価した。同じ機能目的でも実装依存は異なるため、その利点を実際の操作・能力・比較ケースへ落とし込むことが重要と考えた。
- Rust移植の評価は、移植量や言語名より、所有・資源・障害回収・保守の改善へ対応させるのが適切と考えた。公開成功例の偏りを前提に、RP自身の実証を判断材料にする。

## 6 概要・基本設計への反映と残る具体化

概要・基本設計には、WP本体までの製品統合、価値の優先順位、内部Wasmと製品側の互換責任、Rust採用の位置付けを反映した。本書を外部調査の参照先とし、実装順・詳細契約・実証記録への入口は既存のままとする。

本体専有経路、受入policy、要求分離、失効、DB結果不明、同梱基盤更新の責任は引き続き具体化・検証する。安全性・互換性・運用負担、性能・資源・反映期限・復旧目標の具体的な合格値は[実装順と受入条件](implementation-and-validation.ja.md)の残件と対応させる。今回の文書更新で未決の値や方式を確定したことにはしない。
