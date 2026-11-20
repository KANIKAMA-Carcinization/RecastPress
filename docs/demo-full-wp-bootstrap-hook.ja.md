# フル WordPress bootstrap と PHP-Wasm hook デモ

## デモの目的

固定した WordPress 7.1.3 を通常の `wp-settings.php` bootstrap で起動し、PHP-WASIX CGI から install と新規 runtime での read を行います。`get_option('blogname')` が WordPress の公開 `apply_filters()` 経路を通り、`option_blogname` の値が Rust `Dispatcher` に渡って戻ることを確認します。期待値は `RP Bootstrap Fixture|first|second` です。

fixture は WordPress core、theme、plugin のソースを変更しません。DB は専用 drop-in から Rust actor に接続します。PHP callback は request-scoped handle で識別します。hook 用 channel は close ACK 後に余剰 byte がなく EOF になることを確認して閉じ、handle 解放まで検査します。

## 検証範囲と制限

この PoC は選択した `option_blogname` hook に限定しています。`WP_Hook` は final class のため、この経路では公開 `apply_filters()` wrapper が選択 hook を専用 adapter に渡し、その adapter が callback table を Rust `Dispatcher` に渡します。したがって、WordPress 本来の `WP_Hook::apply_filters()` の反復処理や、任意 plugin の hook 互換性を実証するものではありません。native oracle は同じ固定 WordPress 7.1.3 の `WP_Hook` を直接実行し、値・callback 順・filter state の比較基準を作ります。

対象 DB は MySQL と MariaDB です。HTTP listener、管理画面、theme/plugin の一般実行、全 `wpdb` API、製品 runtime への接続はこのデモの範囲外です。

## 実行環境

完全な統合実行には Linux、Rust 1.94.0、PHP CLI/XML、固定 PHP-WASIX toolchain、Docker 互換の MySQL/MariaDB container runtime が必要です。`run_check.py` は Linux 専用で、native oracle に PHP CLI、DB 検証に container runtime を使います。Windows 上の Rust build や既存の別 PoC receipt は、この統合経路の実行証明にはなりません。

CI では `.github/workflows/php-wasix-wp-bootstrap.yml` が固定依存を取得し、PHP-Wasm module と Rust host を構築して、MySQL と MariaDB の両方で install/read を実行します。現在の実装をデモ済みと判断するには、その実装を含む同一 commit について両 backend が成功し、成果物で次を確認する必要があります。

- `passed: true`、`source_reverified_after: true`
- `native_hook_oracle_match: true`、event order `rp_boot_hook_first` → `rp_boot_hook_second`
- hook 結果 `RP Bootstrap Fixture|first|second`
- hook channel が close 済みで reply bytes を drain し、pending/unread bytes と live handles が 0
- CGI の stdout/stderr EOF が確認され、response が正常終了し、最終 DB state が独立検証済み

## 実行手順

リポジトリ root で、固定ソースと toolchain を用意し、host の unit/build gate を通します。

```sh
python3 experiments/php-wasix-cgi-build/prepare.py --record
python3 experiments/php-wasix-managed-access-probe/build.py --record
python3 experiments/wp-bootstrap-probe/prepare.py
python3 experiments/php-wasix-wp-bootstrap-probe/prepare.py
rustup toolchain install 1.94.0 --profile minimal --no-self-update
cargo fetch --locked --manifest-path experiments/binlog-probe/Cargo.toml
python3 experiments/wpdb-rpc-probe/prepare_driver.py
cargo fetch --locked --manifest-path experiments/php-wasix-wp-bootstrap-probe/Cargo.toml
python3 -B experiments/php-wasix-wp-bootstrap-probe/build_check.py
```

前段の固定 PHP/dependency checkout と WordPress 7.1.3 archive の取得は workflow の `Check out fixed PHP source`、`Check out fixed WASIX dependencies`、`Prepare full pinned WordPress` 手順に合わせます。`prepare_driver.py` は Rust DB driver 用の固定 source tree と lock を準備します。各準備 gate が失敗したら、後続の runner を起動せず失敗理由を解消してください。

Linux 上で MySQL と MariaDB の統合実行を個別に行います。

```sh
python3 -B experiments/php-wasix-wp-bootstrap-probe/run_check.py --backend mysql --record
python3 -B experiments/php-wasix-wp-bootstrap-probe/run_check.py --backend mariadb --record
```

出力 JSON の `passed`、`observations.install`、`observations.read` と `validations` を確認します。実行後も source identity と prepared inputs が一致し、container が正常停止・削除されていることが受け入れ条件です。build receipt が現在の source identity と一致しない場合は、先に `build_check.py` を実行してください。

## 現在の証跡

Windows の隔離 clone と専用 target で `build_check.py` の完全 gate が成功しました。[build receipt](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-build.json) は149 source inputs の前後 hash 一致、固定 DB driver source の準備、34 unit の inventory 一致と実行、通常 host binary build を記録します。以前の共有 target で検出した古い staged DB driver seed lock の失敗 receipt は[診断履歴](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-build-before-4a37bdfe4afff245.json)として保存しています。[ローカル host receipt](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-local-host-validation.json) は別の隔離 build による詳細記録です。この Windows gate は host 側の確認であり、PHP-Wasm、native PHP oracle、MySQL/MariaDB は実行していません。

過去の CI receipt は、それぞれの記録済み commit と source hash の範囲でのみ有効です。hook adapter の現在の差分を含む MySQL/MariaDB の成功 receipt が存在しない間は、この統合デモを実行確認済みと表示しません。失敗 run と build gate の失敗 receipt は診断履歴として成功証跡と区別して保管します。
隔離 clone では、固定 WordPress 7.1.3 archive から完全な core source tree と owned DB drop-in の preparation も成功し、`prepare.py --verify-only` まで通りました。[source preparation metadata](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-source-preparation.json) と[実行概要](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-source-preparation-run.json)は、35,368,461 byte の archive hash、3,782 files・467 directories の source inventory、build receipt と一致する33入力を記録します。これは source/overlay 準備の証跡で、PHP、Wasm、DB、native oracle、guest read-only enforcement の実行証明ではありません。
検証の argv・終了コード・stdout/stderr hash は実行概要に記録しています。650 KB の per-file source manifest 本体はリポジトリに保存せず、そのサイズと SHA-256、集計 inventory を記録しています。
固定PHP 8.5.12 CLI (`-n`) で `run_check.py` の native hook oracle も別に実行し、WordPress 7.1.3 の `option_blogname` に対する2 callbackの順序、priority、filter state reset と最終値を確認しました。[native oracle receipt](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-native-hook-oracle.json) は固定PHP binaryとfixtureの hash、実行結果を記録しています。これは `plugin.php` と `WP_Hook` の限定的なnative確認であり、`wp-settings.php` full bootstrap、Rust `Dispatcher`、PHP-Wasm、DB接続の証明ではありません。
