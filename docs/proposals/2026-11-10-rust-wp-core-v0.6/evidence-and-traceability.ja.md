# RecastPress v0.6 成果の統合と設計・試験の追跡

作成日：2026-11-10（日本時間）。設計版：v0.6。
基点は `b3c819b`。本書は既存成果をv0.6へどう接続するかを示す。過去の証跡は記載されたsource・入力・環境に対する結果であり、新しい互換界面やRust業務コアの合格証跡ではない。

## 1 設計の由来と変更の優先関係

| 資料 | 本版への反映 |
| --- | --- |
| [WPコア移植・PHP互換界面の検討](../2026-11-09-wp-core-rust-boundary.ja.md) | 推奨C案、PHP可変状態の保持、継続・再入、Queryを最初の大きな到達点にする構成 |
| [v0.5概要](../2026-11-01-rust-wp-server/README.ja.md)・[基本](../2026-11-01-rust-wp-server/architecture.ja.md)・[詳細](../2026-11-01-rust-wp-server/detailed-design.ja.md) | WP専用の一体運用、PHP資産再利用、DB・cache・追跡・隔離・拡張受入・一体更新 |
| [v0.5実装・検証計画](../2026-11-01-rust-wp-server/implementation-and-validation.ja.md) | 両DB・三環境、導入・移行・OIDC・復旧、正式受入条件の継承 |
| [成果と今後の課題](../../v0.5-results-and-next-steps.ja.md)・[進捗](../../v0.5-development.ja.md) | 実行可能な有限経路、未達の管理・認証・CDC・資源・配布条件 |
| [旧v0.45詳細](../../design.ja.md) | WPコアをRustへ移す元の目的、機能・運用要件の母集団。公開PHP禁止・利用者によるテーマ移植は継承しない |
| [キャッシュ要件](../../cache-and-settings.ja.md)・[DB自動最適化](../../database-auto-optimization.ja.md)・[認証・SSO](../../authentication.ja.md) | 全自動予算、全パージ、監視不成立時停止、正本維持、OIDCの既決条件。旧実行境界は本版で置換 |

現在の設計方針は本v0.6文書群を優先する。旧文書の方針・未実施表示・中断表示は、その日付の履歴として保持する。本版作成で旧P0〜P7や旧要求番号を上書きしない。

## 2 既存成果を再利用する単位

| ID | 成果と参照先 | 確認済みの範囲 | v0.6への統合と追加条件 |
| --- | --- | --- | --- |
| E01 | [PHP-Wasm実行・3patch候補](../../v0.5-runtime-candidate.ja.md)、[結合実験](../../../experiments/php-wasix-managed-access-probe/README.ja.md) | Linux x86_64、固定PHP 8.5.12、Rust Wasmer／WASIX、CGI・Fiber・IO・readonly・固定資源異常の比較 | PHP拡張実行層の候補。新dispatcher・入れ子継続、全hostcall・RSS・取消し・配布をT03・T16・T19で検証 |
| E02 | [fresh要求session](../../../experiments/php-wasix-wp-session-probe/README.ja.md) | module compile1回、同一所有PID、fresh PHP／WASI・DB actor等でhome／404／反復404 | module再利用の寿命設計を継承。PHP heap再利用や任意pluginの要求分離の証明にはしない |
| E03 | [Rust HTTP結合](../../../experiments/php-wasix-wp-session-http-probe/README.ja.md)、[preview release](../../../evidence/wordpress-preview-release/ci-42488374173/provenance.json) | 両DBの固定応答とcontext、移動したリリース配置からの実起動 | ingress・監督・配置検査を再利用。新loader・profile・RustコアをT06〜T08・T19で接続 |
| E04 | [通常WP bootstrap](../../../experiments/php-wasix-wp-bootstrap-probe/README.ja.md) | 元installer・全体bootstrap・fresh readの固定19公開項目 | 元WPの比較基準として保存。Rust bootstrap完了の根拠にはしない |
| E05 | [wpdb標準API](../../../experiments/php-wasix-wpdb-probe/README.ja.md)、[DB結果集](../../v0.5-db-results.ja.md) | 固定27ケース、更新ヘルパー15ケースを両DBで比較 | DB actor・raw結果・接続口を再利用。wpdb標準ロジックのRust化、session全体・metadata等はT13 |
| E06 | [UTF-8比較](../../../experiments/php-wasix-wpdb-utf8-probe/README.ja.md)、[raw ERR](../../../experiments/php-wasix-wpdb-raw-error-probe/README.ja.md) | 有効UTF-8の31観測・22 SQL、固定エラー本文と後続状態の比較 | byte保持・結果比較のfixtureへ統合。無効文字列・任意collation・全DB errorの保証へ拡張しない |
| E07 | [結果の有限予算](../../../experiments/wpdb-result-budget-probe/README.ja.md) | 合成形状、metadataと各上限、fresh接続での復帰 | raw／encoded・行・cell予算の部品。PHP/Wasm/RPC、driver allocationと全RSSをT13・T16で統合 |
| E08 | [Rust要求内cache](../../../experiments/wp-object-cache-rust-probe/README.ja.md)、[Wasm結合](../../../experiments/php-wasix-wp-object-cache-probe/README.ja.md) | 固定2グループ、56観測、限られたscalar値、終了後0 entry・0予約 | cache API・予算ownerの部品。object・参照・clone等は元PHPが扱った範囲を区別しT02・T15で追加 |
| E09 | [通常bootstrapとcache](../../../experiments/php-wasix-wp-cache-bootstrap-probe/README.ja.md) | 固定groupのRust要求内cacheと通常WPの状態・応答 | Rust bootstrap／optionsへ接続し直す。固定group以外まで移植済みにしない |
| E10 | [autoload共有](../../../experiments/php-wasix-autoload-cache-probe/README.ja.md) | 元autoload SELECTの要求間SQL省略。初期共有試験は所有DBの読取りlock付き | 共有結果・予約・比較手法を継承。任意並行更新での安全なfillはT14の別条件 |
| E11 | [binlog COMMIT失効](../../../experiments/php-wasix-autoload-binlog-probe/README.ja.md) | 両DBの固定option更新、SQL2回・共有hit1回・失効の比較 | COMMIT適用・epoch・通常SQLへの切替へ統合。`update_option`の業務手順を移植した成果とは扱わない |
| E12 | [接続同一性](../../../experiments/binlog-source-identity-probe/README.ja.md)、[ライブ位置](../../../experiments/binlog-autoload-frontier-probe/README.ja.md) | 所有DBの識別値・別接続・位置照合、適用待ち・切断時の直読 | source・session・利用許可の照合を再利用。製品複数source・復元・並行fillへ拡張 |
| E13 | [PHP-WasmのDB直読とlive watcher](../../../experiments/php-wasix-autoload-bypass-probe/README.ja.md) | Linux両DBでSELECT前の位置照合・適用保留・DB直読。Windowsでbuffer中の切断・旧permit拒否・検証適用・空cacheでの監視再起動 | pending・失効・回収の規則を統合。Windows補強後のLinux再比較、PHP actorの障害迂回、同process再接続は未受入 |
| E14 | [共有cache復旧](../../../experiments/binlog-shared-cache-probe/README.ja.md) | Windows固定1行のROLLBACK／COMMIT、切断直読、保持済みログから再生、旧ticket拒否 | checkpoint・epochの部品。任意table・rotation・ログ欠落・長期運用はT14 |
| E15 | [対応台帳](../../../compatibility/registry.json)、[互換履歴](../../compatibility-development.ja.md) | 79 API、WP_Hookとnamed dispatcherの2 component。限定入力・Rust値モデルの実装 | 必要分野へ再統合。過去519,014比較等の履歴とCookie変更後の未完了全体検証を区別 |
| E16 | [値モデル](../../../crates/recastpress-wp/src/value.rs)、[hooks](../../../crates/recastpress-wp/src/hooks.rs) | byte string、順序付き配列等と要求内反復のsubset。objects・references・PHP callable全面対応は未実装 | 純値と反復の部品を再利用し、handle・公開状態・再入を新設。T02〜T05が追加条件 |
| E17 | [password dispatch](../../password-dispatch-development.ja.md)、[credential stage](../../credential-stage-development.ja.md)、[cookie照合](../../auth-cookie-verification-development.ja.md) | hash・lookup・入力前処理・cookie等の有限部品。認証済みprincipal・HTTP認証・session lifecycle等は未接続 | 共通認証へ組込む。T11で入力から権限・失効・OIDCまで縦断し、部品成功を完成としない |
| E18 | [HTTP cache](../../http-cache-development.ja.md)、[共有body](../../shared-public-body-development.ja.md)、[資源予算](../../../crates/recastpress-core/src/resource_budget.rs) | 世代ticket、logical budget、共有owner・body、有限のHTTP adapter | 新動的応答・全パージ・実送信へ接続。sealed bundleを全WP要求の必須形式にしない |
| E19 | [memory診断](../../memory-diagnostic-development.ja.md)、[RSS](../../process-memory-development.ja.md) | 観測・unknownの表現等の部品。完全自動allocatorではない | 共有資源範囲・圧迫・復帰と全体予算をT16で実装 |
| E20 | [拡張受入](../../../experiments/wp-plugin-admission-probe/README.ja.md)、[Wasmでの有効化](../../../experiments/php-wasix-wp-plugin-admission-probe/README.ja.md) | 固定候補の受入・全体拒否・保留、固定1pluginの標準有効化と応答比較 | 新loader・互換profileへ接続。任意plugin判定・全導入経路の完成とはしない |
| E21 | [source FS](../../../experiments/wp-source-fs-probe/README.ja.md)、[WASI source探索](../../../experiments/wp-source-wasi-probe/README.ja.md) | 固定catalog、readonly、WASI列挙・読取り等の有限試験 | 元WP比較環境と新shimコード束のmountへ再利用。全metadata・PHP探索・host権限は別比較 |
| E22 | [依存台帳](../../dependency-inventory.ja.md)、[PHP依存調査](../../../experiments/php-wasix-cgi-build/dependency-source-findings.ja.md)、[NOTICE](../../../NOTICE.md) | 宣言・archive・link観測・由来の記録 | 移植と生成shimの出典、対応ソース、PHP静的依存・追加bridgeの配布条件を継承。完了していないlicense／build確認を完了にしない |

E13のWindows補強には、[切断MySQL](../../../experiments/php-wasix-autoload-bypass-probe/evidence/windows-held-disconnect-v5-mysql.json)・[切断MariaDB](../../../experiments/php-wasix-autoload-bypass-probe/evidence/windows-held-disconnect-v5-mariadb.json)・[適用MySQL](../../../experiments/php-wasix-autoload-bypass-probe/evidence/windows-held-apply-v4-mysql.json)・[適用MariaDB](../../../experiments/php-wasix-autoload-bypass-probe/evidence/windows-held-apply-v4-mariadb.json)を対応させる。先行の失敗・途中版も元の判定で保存し、成功に付け替えない。

直前のcheckpoint作成時には、autoload-bypass実験のlocked／offline Rust試験が71成功・2 ignored、追加Python2ファイルの構文と追加JSON8ファイルのparseを確認した。これは新PHP-Wasm縦断試験やv0.6の検証ではない。

## 3 設計・既存成果・受入の対応

| 要件 | 主な設計 | 再利用する成果 | 新たな受入 |
| --- | --- | --- | --- |
| V06-R01 意味のRust移植 | 基本5〜6、コア詳細2〜8 | E03〜E06、E15 | T01、T06、T20 |
| V06-R02 PHP状態・再入 | 界面2〜6、9 | E01〜E02、E16 | T02、T03、T05 |
| V06-R03 hooks | 界面7 | E15〜E16 | T04 |
| V06-R04 Query・Loop | コア詳細2〜4 | E03〜E06、E15 | T06〜T08 |
| V06-R05 標準保存 | コア詳細5〜6 | E05〜E11 | T09、T10 |
| V06-R06 共通認証 | コア詳細7〜8 | E17 | T11、T12 |
| V06-R07 DB・結果不明 | コア詳細6、9 | E05〜E07、E14 | T10、T13、T14 |
| V06-R08 共有整合 | コア詳細10 | E08〜E14、E18 | T14、T15 |
| V06-R09 資源・隔離 | 界面3、9、コア詳細11 | E01〜E03、E07、E18〜E19 | T16 |
| V06-R10 標準機能全体 | コア詳細8、実装段階V06-5〜6 | E04、E15 | T12、T17、T20 |
| V06-R11 製品条件継承 | コア詳細7、12〜13 | E17、E20〜E22 | T05、T18、T19 |
| V06-R12 証跡と移植台帳 | 本書、実装・受入4、7 | 全既存fixtureとsource記録 | T01、T20 |

表のT番号はすべて本版のV06-T番号を指す。既存成果を再利用するときは、source変更が結果へ影響するかを確認し、必要な再実行を行う。

## 4 移植台帳の形式

機能ごとに次のentryを作り、API台帳と実要求traceを関連付ける。

```text
feature_id / baseline_source / symbols / observable_state
standard_hooks / database_effects / external_effects
implementation_kind / rust_target / php_surface
temporary_delegate: reason / scope / exit_stage
dependencies / supported_profiles / explicit_differences
differential_evidence / integration_evidence / retirement_evidence
status / source_revision / next_missing_condition
```

未着手のentryや未列挙の母集団を残せるが、未列挙を移植済み扱いにしない。API数、元ファイル数、業務分野、代表操作の実行経路を別々の指標として報告する。機能の重みを定義していない単一の「移植率％」は出さない。

PHP残存は、互換宣言・言語操作、拡張、未移植標準処理、第三者library、未提供に分類する。管理view・標準block等の業務処理をPHPに残している場合は、そのentryの退役条件と委譲回数を記録する。

## 5 参照sourceと外部一次資料

ローカル固定参照は `.reference/wordpress-7.1.3/` のmanifestに基づく。archive SHA-256は `d2a09acb6a15e3b9c471d72557753c266d6f41a79ed19bfe04cbfe49e283b2a5`。対応台帳にも同じ固定版が記録されている。今回はarchive全体の再hashや全coreの行単位監査は行っていない。

本改訂では特に `WP::main`、`WP_Query::get_posts`のparse・pre_get_posts・SQL／結果filters、`get_option`／`update_option`の手順をローカルsourceで照合した。block描画・REST dispatch・cron・attachmentの関連入口とhook位置も確認したが、全処理graphの監査ではない。検討書が扱った公開property・参照・継承・loaderの難所を引き継いだ。新しいRust実装が同じ意味になる証明は今後の比較で行う。

外部資料は2026-11-10に以下の一次資料を確認した。オンラインのWP sourceを固定版の正本に置き換えない。各資料は言語・公開契約・外部標準を補う。

- [PHP References: What References Do](https://www.php.net/manual/en/language.references.whatdo.php)：参照cellと値コピーを区別する根拠。
- [PHP Overloading](https://www.php.net/manual/en/language.oop5.overloading.php)：公開propertyをmagic methodだけで捕捉しない設計の根拠。
- [WordPress pre_get_posts](https://developer.wordpress.org/reference/hooks/pre_get_posts/)：Query objectとmain queryの観測を確認。
- [WordPress WP_Hook](https://developer.wordpress.org/reference/classes/wp_hook/)：公開callbacks、反復中変更、Iterator／ArrayAccessの調査先。
- [WordPress update_option](https://developer.wordpress.org/reference/functions/update_option/)：標準更新のclone・filter・DB・cache・actionの照合先。
- [WordPress load_template](https://developer.wordpress.org/reference/functions/load_template/)：template scope・globals・query varsの調査先。
- [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html)：Code Flow、ID Tokenと外部identityの契約。
- [RFC 9700 OAuth Security BCP](https://www.rfc-editor.org/rfc/rfc9700.html)：認証flow・PKCE・redirectと再送対策の基準。
- [RFC 9111 HTTP Caching](https://www.rfc-editor.org/rfc/rfc9111.html)：外部HTTP cacheの保存・再利用条件。

既存資料にある外部リンクや依存版を、すべて今回再確認したとは扱わない。新しい依存・PHP bridgeの採用、第三者への報告送信、実配備、既存DBの変更は行っていない。
