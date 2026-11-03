# SourceFs の固定 plain-WAT / WASI 接続試験

既存 `wp-source-fs-probe` library を path dependency で使い、Wasmer 7.2.0 / WASIX 0.702.0 / Cranelift に `/source` として map する独立 workspace。旧 source / lock / registry / stage / 証跡は変更しない。

固定 WAT は EH・thread・clock・network を使わず、memory は 2 pages / max 2、file drain loop は最大 64 回。FD3 の root prestat を実確認してから directory path_open と fd_readdir、深い file open / prefix32 bytes / 全 read / EOF、readonly FD write・write open・mkdir・unlink・rename 拒否、file / directory close を呼ぶ。実 errno、cookie付き列挙名、prefix と読取り総数を有限 binary frame にし、host が照合する。未実行 errno は UINT32_MAX として保持する。stdout / stderr は write 時点で各 65 KiB 以内、全 bytes と EOF を確認する。

小さい固定 tree の成功後だけ、旧 catalog loader の厳密派生で既存固定 WordPress site を読み込む。全 3782 files / 467 dirs / 110077999 bytes と manifest / 各 SHA / metadata / 直下一覧を検証してから、同じ WAT で wp-includes と class-wpdb.php を確認する。loader の元コードは `derivation_gate.py` で照合し、旧 loader を書き換えない。

```powershell
python experiments/wp-source-wasi-probe/check.py --record
```

checker は locked/offline build を owned child の有限期限で行い、全 child を回収する。実 WASI が失敗した場合も inner JSON と child stdout / stderr を byte 不変保存し、失敗を pure-unit 成功へ読み替えない。前 record を hash 名で保存してから最新 record を置く。固定 prepared source を旧 bootstrap.prepare.verify_prepared() で実行前後に read-only 検証する。

PHP / DB / HTTP は実行しない。既知の Windows PHP-EH panic や旧 crash / cancel 実験は試さない。host environment / home を guest へ渡さず、guest network は UnsupportedVirtualNetworking、HTTP client は無し。runtime shutdown は 2 秒の bounded return を観測するが、全 task の独立列挙・reap を保証する主張ではない。native filemtime / permission mode / metadata 全互換、製品 runtime 選定も未実証である。


Windows の初回実 WASI では directory open / readdir は成功したが、子 file open が NOENT (44) で失敗した。透過診断では WASIX が backing FS に `/wp-includes\class-wpdb.php` を渡し、POSIX 専用 SourceFs が InvalidInput を返すことを確認した。元 record / raw stdout / stderr は `evidence/*-before-<SHA prefix>.*` に byte 不変で保存し、失敗を成功に読み替えない。

この probe 内の `trace_fs.rs` は Windows の backing-call path 区切りだけを `/` に変換し、残る検証・有限 catalog・親 traversal と host prefix の拒否・全 mutation 拒否を凍結済み SourceFs に委譲する。Linux では区切りを変換しない。旧 library / registry / WASIX は変更していない。guest が直接送る backslash の厳密な拒否は、この Windows 結合では未検証・未保証であり、report に false として明記する。

修正後は pure 4 units と実 WASI の small / full catalog が成功した。full の `wp-includes` は 294 entries (`.` / `..` 含む、12721 bytes)、`class-wpdb.php` は prefix32 bytes と総121437 bytes / EOF を確認した。実 errno は readonly FD write が ACCES (2)、write open / mkdir / unlink / rename が PERM (63)、close 後 read が BADF (8)。source 全体と旧 proof / inputs の実行前後同一、owned command の回収を別々に検証する。全 core の PHP 実行、Linux 実行、native metadata 全互換の証明ではない。
