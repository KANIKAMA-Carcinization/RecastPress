# WordPress標準のautoload SELECTをRustで共有する試験

通常のホーム要求を先に1回実行し、続く2つの新規404要求を同じ所有DBで比較する。PHPとWordPress標準キャッシュをそのまま実行する。Rust側は、固定プロファイルで `wp_load_alloptions()` が発行する完全一致のSELECTだけを共有対象とする。接続開始からそのSQLまでの固定操作列とautocommit状態を確認し、適用条件外の要求は通常のDB経路に渡す。

最初の404ではDBから得た結果を保存し、次の404では同じ結果を返す。最大128 KiBの論理予算で列メタデータ、行順、値、ステータスを保持し、固定SELECTと前後のRPCを照合する。所有DBの読み取りロックで比較中のオプションを固定し、別接続の更新が拒否されることを確認する。製品の共有層では別に検証したbinlog観測を接続する予定で、今回の実行範囲は固定404での結果と応答の互換比較に限る。[実binlogとの結合](../php-wasix-binlog-dependency-probe/README.ja.md)

Windowsで最初のRustオフラインテスト65件を通過した。初回Linux CIは、両DBで通常PHPとの3要求比較、準備済み404同士のRPC照合、別接続の更新拒否まで通過した。終了時の共有キャッシュ判定で停止したため、判定を保存し、再試験では接続状態とSELECTのインデックス診断ビットを区別する。終了時に失敗した場合は限定したカウンターと格納判定を記録する。[初回MySQL](evidence/ci-42511203154-mysql/provenance.json)・[初回MariaDB](evidence/ci-42511203154-mariadb/provenance.json)

通常WordPressの初回処理を済ませたWindowsの両DBでは、対象SELECTのステータスが `0x0002` から `0x0022` に変わり、差はインデックス診断の `0x0020` だった。修正版はそのビットも元の応答に含めて保持し、接続状態の照合を続ける。

source `69deba9` のLinux CIはMySQLとMariaDBの両方で通過した。標準の117行・2列を87,596バイトで格納し、2つの404で対象SQLの実行1回、共有ヒット1回を確認した。対象SQLの応答、後続のRPC、通常PHPのホームと404の応答が一致し、キャッシュ予算を解放した。Rustテスト66件、入力249件・生成2件、所有DBの正常終了を照合した。[MySQL証跡](evidence/ci-42513247456-mysql/provenance.json)・[MariaDB証跡](evidence/ci-42513247456-mariadb/provenance.json)
