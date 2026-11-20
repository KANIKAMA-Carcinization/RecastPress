# WordPress bootstrap / PHP-Wasm hook デモ

## 目的

この PoC の目標は、固定した WordPress 7.1.3 を `wp-settings.php` から起動し、PHP-WASIX CGI で install と新規 runtime の read を行うことです。`get_option('blogname')` から公開 `apply_filters()` を通り、`option_blogname` の値が Rust `Dispatcher` を経由して戻ることを検証します。期待値は `RP Bootstrap Fixture|first|second` です。

WordPress core、theme、plugin のソースは変更しません。DB は専用 drop-in から Rust actor に接続し、PHP callback は request-scoped handle で識別します。hook channel は close ACK 後に余分な byte がなく EOF になること、handle が解放されることも確認対象です。

## 今すぐ再生できる Windows デモ

固定 PHP 8.5.12 CLI が配置済みの Windows checkout では、次のコマンドで native WordPress hook oracle を再生できます。

```powershell
python -B scripts/run_recastpress_native_hook_demo.py
```

初回の checkout では、固定 WordPress archive と PHP CLI を配置し、source と fixture の準備を先に行います。

```powershell
python -B experiments/wp-bootstrap-probe/prepare.py
python -B experiments/php-wasix-wp-bootstrap-probe/prepare.py
python -B experiments/php-wasix-wp-bootstrap-probe/prepare.py --verify-only
```

このコマンドは固定 PHP バイナリと archive、WordPress source manifest と hook source、fixture、149件の host build inputs を hash 検証してから、WordPress 7.1.3 の `plugin.php` / `WP_Hook` fixture を実行します。成功時は callback 順と最終値を表示し、実行証跡を `experiments/php-wasix-wp-bootstrap-probe/evidence/windows-native-hook-demo-run.json` に記録します。

これは発表用に動かせる native-only の fallback デモです。`wp-settings.php` の全体 bootstrap、Rust `Dispatcher`、PHP-Wasm、DB 接続は実行しません。完全統合デモの達成証拠として扱わないでください。

## 検証範囲と制限

PoC の hook シナリオは `option_blogname` に限定しています。WordPress の `WP_Hook` は final class のため、実装経路では公開 `apply_filters()` wrapper が選択 hook を専用 adapter に渡し、callback table を Rust `Dispatcher` に送ります。native oracle は同じ固定 WordPress 7.1.3 の `WP_Hook` を直接実行し、値・callback 順・filter state の比較基準を作ります。これは WordPress 本来の `WP_Hook::apply_filters()` の全挙動や任意 plugin の互換性を証明するものではありません。

対象 DB は MySQL と MariaDB です。HTTP listener、管理画面、theme/plugin の一般実行、全 `wpdb` API、製品 runtime への接続はこのデモの範囲外です。

## 完全統合の実行環境

完全な統合実行には Linux、Rust 1.94.0、PHP CLI/XML、固定 PHP-WASIX toolchain、Docker 互換の MySQL/MariaDB container runtime が必要です。`run_check.py` は Linux 専用で、native oracle に PHP CLI、DB 検証に container runtime を使います。Windows 上の Rust build は host gate の証拠であり、PHP-Wasm や DB の実行証拠ではありません。

準備と host gate は次のとおりです。

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

Linux では MySQL と MariaDB の統合実行を個別に行います。

```sh
python3 -B experiments/php-wasix-wp-bootstrap-probe/run_check.py --backend mysql --record
python3 -B experiments/php-wasix-wp-bootstrap-probe/run_check.py --backend mariadb --record
```

## 現在の証跡

Windows の隔離 clone と専用 target で `build_check.py` の locked/offline host gate が成功しています。[build receipt](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-build.json) は149 source inputs の前後 hash、固定 DB driver source の準備、4段階の build/test 成功を記録します。これは host 側の検証で、PHP-Wasm、DB、全体 bootstrap は実行していません。

固定 WordPress 7.1.3 archive から core source tree と専用 DB drop-in の準備も成功し、`prepare.py --verify-only` で入力一致を確認しました。[source preparation metadata](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-source-preparation.json) と[実行記録](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-source-preparation-run.json) は archive hash、3,782 files、467 directories、host build receipt と一致する33入力を記録します。source manifest 本体は repository に保存せず、サイズ・hash・集計を記録しています。

固定 PHP 8.5.12 CLI (`-n`) で WordPress 7.1.3 の `option_blogname` native hook oracle を実行しました。[native oracle receipt](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-native-hook-oracle.json) は固定 PHP binary と fixture の hash、callback 順、filter state reset、最終値を記録します。[再生ランナーの証跡](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-native-hook-demo-run.json) は上記 PowerShell コマンドの直近結果です。oracle は `plugin.php` と `WP_Hook` の限定的な native 確認で、全体 bootstrap、Rust `Dispatcher`、PHP-Wasm、DB 接続の証明ではありません。

固定 PHP 8.5.12 CLI (`-n -l`) で install/read、bootstrap、DB adapter/drop-in、hook adapter/registry、native oracle の9 fixtureすべてに構文エラーがないことも確認しました。[PHP fixture lint receipt](../experiments/php-wasix-wp-bootstrap-probe/evidence/windows-php-fixture-lint.json) は各 fixture と PHP binary の hash、終了コードを記録します。これは lint のみで、fixture の実行、PHP-Wasm、DB 接続の証明ではありません。

完全統合の MySQL/MariaDB receipt が成功するまでは、この PoC を「全体統合デモ済み」と表示しません。
