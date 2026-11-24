# PHP-WASIX で WordPress を起動する

## 確認済みの動作

WordPress 7.1.3 の `wp-settings.php` と `wp_install()` を PHP-WASIX CGI で実行し、新規インストールと、別インスタンスでの読み取りを確認する。DB は Rust の接続処理を経由する。起動後の `get_option('blogname')` は実 `WP_Hook` と Rust Dispatcher を通り、`RP Bootstrap Fixture|first|second` を返す。検証用の MariaDB はループバックで起動し、試験終了時に停止する。ブラウザ向けの常駐 HTTP リスナーはこのデモの対象外。[進捗](v0.6-development.ja.md) · [実行証跡](../experiments/php-wasix-wp-bootstrap-probe/evidence/linux-local-wp-boot-mariadb.json)

## ローカルで再実行する

WordPress ソース、PHP-WASIX CGI、Rust ホストとビルド証跡を準備済みの Linux 作業ツリーで実行する。比較用 PHP 8.5.12 を `PATH` に置く。MariaDB は公式アーカイブの展開先、または Ubuntu パッケージを `usr/` 以下に展開したディレクトリを指定する。固定ソース、ツールとホストを準備する手順はワークフローにある。[起動ワークフロー](../.github/workflows/php-wasix-wp-bootstrap.yml) · [比較用 PHP 準備手順](../experiments/php-wasix-wp-bootstrap-probe/prepare_native_php.sh)

```sh
export PATH="$PWD/target/php-wasix-wp-bootstrap-probe/native-php:$PATH"
python3 -B scripts/run_local_wasix_wp_boot.py --mariadb-root /path/to/extracted/mariadb-11.8.9
```

既定の結果ファイルは `target/php-wasix-wp-bootstrap-probe/local-wp-boot.json`。成功時に `install` と `read` の両方で CGI 200、ネイティブ観測との一致、WP フックの状態リセット、DB 接続の切断を記録する。[ローカル起動ハーネス](../scripts/run_local_wasix_wp_boot.py)

固定 MySQL 8.4.13でも、公式署名を照合して展開した Linux バイナリと、その環境で必要な共有ライブラリを用意すれば同じ検査を実行できる。DB は一時領域に初期化し、ループバックで起動する。`--report` で MariaDB と別の結果ファイルを指定する。

```sh
python3 -B scripts/run_local_wasix_wp_boot.py --mysql-root /path/to/extracted/mysql-8.4.13 \
  --report target/php-wasix-wp-bootstrap-probe/local-wp-boot-mysql.json
```

## Docker を使った統合検証

起動ワークフローはダイジェストで固定した MySQL と MariaDB で実行する。現行の実 `WP_Hook` オーバーレイの結果は、対象コミットの run に紐づく `linux-mysql.json` と `linux-mariadb.json` で確認する。ローカル MariaDB の証跡では、独立したローカルプロセスでの起動を確認している。[GitHub Actions の実行一覧](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/workflows/php-wasix-wp-bootstrap.yml)

## 従来の CI 証跡

2026-11-20 の run 44649976739 では、従来の専用アダプターで MySQL と MariaDB の install/read が成功した。観測値は固定 WordPress のネイティブ基準と一致した。成功時のコード、取得済みの成果物と SHA-256、検証範囲は各証跡に記録している。[CI run](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44649976739) · [MySQL 証跡](https://github.com/KANIKAMA-Carcinization/RecastPress/blob/82a12c9ba6e7d01e494666b0f2898801bf11ed82/experiments/php-wasix-wp-bootstrap-probe/evidence/ci-44649976739-mysql/provenance.json) · [MariaDB 証跡](https://github.com/KANIKAMA-Carcinization/RecastPress/blob/82a12c9ba6e7d01e494666b0f2898801bf11ed82/experiments/php-wasix-wp-bootstrap-probe/evidence/ci-44649976739-mariadb/provenance.json)
