# 固定 source tree の readonly virtual-fs 実験

`SourceFs::new(BTreeMap<String, Arc<[u8]>>, BTreeSet<String>)` は正規化済み POSIX relative catalog を検証して不変 tree を構築する。root は暗黙に存在し、明示 directory 集合に空文字の root を含めてもよい。空 directory を含め、root 以外の全 parent directory は明示が必要となる。重複 file key は BTreeMap の型段階で失われるため、archive / JSON を map に変換する呼び出し側も元入力の duplicate を拒否する必要がある。

固定上限は 5000 files / root を除く 5000 directories / 16 MiB per file / 256 MiB logical file bytes / 4096 path bytes。`with_limits` はこれらを狭めることだけを許す。同じ Arc を複数 file に使っても logical bytes は各 file 分を数える。file/dir 衝突、不正・非正規 catalog、欠損 parent と file parent を拒否する。

metadata と symlink_metadata は同じ immutable tree を読む。read_dir は直下の実 children だけを名前順に返し、空 directory は空一覧となる。guest lookup は POSIX `/` と `.` / `..` を mount 内で扱い、上への escape、missing / file の途中 traversal、Windows drive/UNC/prefix、backslash、colon、NUL/control、非 UTF-8 path を拒否する。host path / host FS fallback / symlink / writable overlay は実装しない。read-only open ごとに独立 cursor / close state を持ち、Fs clone は catalog の Arc を共有する。全 namespace mutation と content / timestamp / mmap / copy mutation API を拒否する。

directory は regular file として open しない。virtual-fs 0.702.0 の mem_fs も directory に FileOpener の handle を作らず NotAFile を返す。read-only 調査した WASIX 0.702.0 の `path_open2.rs` は `Kind::Dir` / `Kind::Root` を専用 FD として扱い、`fd_readdir.rs` が `.` と `..` を追加する。この library の read_dir も dot entries は返さない。今回の試験は virtual-fs trait の pure unit に限り、実際の directory FD / fd_readdir / WASI / PHP 起動の成功は実証していない。

timestamp はすべて 0、directory length は 0 に固定する。virtual-fs Metadata は Unix permission mode を表現しない。readonly は実際の open / write / mutation 拒否によって実装する。native WordPress の filemtime 等との値の一致、PHP の stat / is_writable 判定、metadata 全体の互換性は未検証である。

依存は virtual-fs `=0.702.0`（archive checksum `a1a93b14f49c6ab20c3d4bbdbf58043629474d9b8dedf98a131bb80655938950`）、tokio、VirtualFile の copy API を明示拒否する型のための既存 transitive shared-buffer `=0.1.4`。virtual-fs の default features を無効にし host-fs を有効化しない。sha2 は unit の既知 read hash 照合にのみ使う dev 依存。

```powershell
python experiments/wp-source-fs-probe/check.py --record
```

helper は既存 cache の archive / registry source と元 lock の pin を照合し、全 Cargo 操作を locked/offline、metadata 120 秒・build 各 1200 秒・unit list 10 秒・unit run 30 秒で監督する。11 unit は tree / metadata / enumeration、read hash / partial read / seek / EOF、mutation と close、clone cursor、namespace / non-UTF8、catalog と quotas を扱う。DB・HTTP・PHP・WASI・Wasm は実行しない。record は新 evidence 内だけに作成し、再記録時は前 record を hash 名で byte 不変保存する。

`catalog` feature の固定 native binary は任意引数を受け付けず、既存 `target/wp-bootstrap-probe/source-manifest.json` と `site/` だけを読む。checker が前後に旧 bootstrap.prepare.verify_prepared() を read-only で呼んで元 archive との全 byte 一致を確認したうえで、3782 files / 467 dirs / 110077999 bytes を同じ SourceFs へ構築する。全 metadata・全直下一覧と host tree の一致・深い class-wpdb.php の読み取り hash / EOF / close を検査する。payload は公開せず、manifest SHA と count / pass を記録する。serde_json と SHA2 の runtime 依存はこの optional feature に限定する。この probe は native virtual-fs trait の実 catalog 試験であり、directory FD や WASI / PHP / Wasm の実行ではない。

constructor の catalog 上限は、open handle 数・task 数・全 process memory / CPU / operation の資源 budget を提供するものではない。
