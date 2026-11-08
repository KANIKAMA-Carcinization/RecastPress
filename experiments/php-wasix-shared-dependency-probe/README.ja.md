# PHP-Wasm要求をまたぐ固定依存行の読取り

通常のWordPress起動を1つのPHP-Wasmセッションから3つの新規要求で実行する。各要求のRust DB actorに、固定行を1件読む非公開コマンドを追加した。PHPは引数を渡さず、SQL、キャッシュキー、格納可否はRustが管理する。標準オブジェクトキャッシュのインスタンスと外部キャッシュフラグを維持し、ホーム応答を通常PHPの基準と比較する。

初回にSQLで値を読み、後続の2要求が共有ヒットすることを検証する。この試験では所有DBの固定行に共有ロックを保持し、別接続の更新が拒否されることも確認する。ライブbinlog監視の実測は別のWindows試験にあり、PHP-Wasmへの監視接続は後続の検証範囲となる。[binlogと共有キャッシュの実測](../binlog-shared-cache-probe/README.ja.md)

source `8a5e892` のLinux CIはMySQLとMariaDBの両方で通過した。各DBでPHP-Wasmを1回コンパイルし、3つの新規要求を実行した。固定行のSQLは1回、後続の2要求は共有ヒットだった。ホームのレスポンスと要求コンテキストは3回とも通常PHPと一致した。別接続の更新をロック中に拒否し、セッション終了後は予約ゼロ、所有DBは正常終了した。Rustテスト67件、入力251件、生成4件を照合した。[MySQL証跡](evidence/ci-42504791015-mysql/provenance.json)・[MariaDB証跡](evidence/ci-42504791015-mariadb/provenance.json)
