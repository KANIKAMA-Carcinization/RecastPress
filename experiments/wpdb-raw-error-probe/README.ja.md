# wpdb raw ERR packet accessor の独立実験

既存の mysql_async 0.37.1 / mysql_common 0.37.3 の archive と、元の handshake 2 ファイル patch を毎回照合し、新しい `target/wpdb-raw-error-probe/workspace/source/` に全 source を配置する。既存 stage、registry source、元の lock は変更しない。

production code の追加は `Conn::last_error_packet()` だけである。既に `ConnInner` が所有する `mysql_common::packets::ServerError<'static>` への borrow を返す。既存の `ServerError` String API、ERR / OK 処理、接続処理は変更しない。message の非 UTF-8 bytes は accessor で取得できるが、元の String 表示は引き続き lossy となる。

`fixtures/raw_error_tests.rs` の private units は `Conn::empty()` を使用し、protocol 4.1 の固定 ERR payload を既存 `handle_packet()` に渡す。3 件で code / SQLSTATE / 非 UTF-8 と NUL bytes の保持、String API との差、次の ERR による置換、OK による clear を確認する。元の handshake policy 2 件も同じ executable で回帰確認する。DB 接続を必要とする上流 tests は実行しない。

実行:

```powershell
python experiments/wpdb-raw-error-probe/check.py --record
```

`Cargo.toml` / `Cargo.lock` は public API の compile witness 用である。`driver-workspace.toml` / `driver.Cargo.lock` は private driver units 用の独立 lock と workspace template であり、新 target に byte exact copy して使う。Cargo metadata 120 秒、各 build 1200 秒、test list 10 秒、test execution 30 秒の外側期限を設け、全 child を回収する。解決・build は locked / offline 固定で、network refill の入口はない。

公開 record は `evidence/windows-driver-units.json` に新規作成する。既存 record の上書きは拒否する。詳細 child stdout / stderr は ignored target に保持する。全 source、元 archive / source、既存 stage と lock を実行前後に再照合する。

実証範囲は固定 synthetic ERR payload の parsed fields と raw message bytes、及び compile 可能な借用 accessor に限る。transport frame 全体の保存、実 DB の ERR、PHP / wpdb 互換、標準 wpdb 全体、製品採用は実証していない。

LF 正規化前の成功 record は `evidence/windows-before-lf-driver-units.json` に byte 不変で保存した。初回 API lock 不整合は `evidence/windows-initial-api-lock-binding-failure.json` の原形を維持する。最新の `windows-driver-units.json` だけが現行 LF source と lock に対応する。正規化前の source stage は ignored target 内の `workspace-before-lf` に削除せず保存した。

The intermediate LF/context-bearing verification is preserved as `evidence/windows-lf-context-driver-units.json`. The final patch uses zero-context unified diff, with exact whole-file before/after hashes and canonical accessor/test reconstruction.
