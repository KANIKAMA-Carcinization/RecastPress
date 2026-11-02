# php-wasm → private file RPC → Rust → 実DB

2026-11-02。前回の[通常PHPでの結合](../wpdb-bridge-probe/README.ja.md)を、同じ3patch PHP CGI候補・Wasmer 7.2／WASIX 0.702・Craneliftへ接続する。旧source、Cargo lock、実測記録は変更せず、独立workspaceを追加した。

## 維持する比較基準

元の27 API corpusと、PHP adapterのquery/prepare/get_*に関する業務ロジックはbyte一致で維持する。変更はCGI入口、file channel transport、実行scopeの表示に限定し、[prepare.py](prepare.py)で固定変換を検査する。WP 7.1.3の固定archive全体を照合し、元5ファイルだけをreadonly配置する。新PHP4ファイルと合わせて9 guest mappingを検証する。

期待値は前回Windows native PHP 8.5.12の固定JSONであり、27観測のAPI・SQL・型・bytes/NULL・公開状態・counter・prepare・prefix・能力・SQL errorを再比較する。Linux DBイメージのraw server infoはOS/package suffixが異なり得るため、観測値を保存して実handshakeとPHP getterの一致を別に検査する。値を丸めて前回JSONへ置き換えない。同jobのnative PHPを比較用に実行する構成ではない。

## 要求専有の境界

guest network、host environment/home、DB資格情報はPHPへ供給しない。PHPは`/rp-wpdb/channel`だけを開き、JSONLのnonce・連番付き要求を送る。stdoutはCGI応答だけで、RPCとしてdispatchしない。DB actorは別threadで一つの物理接続を専有し、VFileはoneshot応答を非同期pollする。応答未着をEOFと扱わない。

frame 16KiB／reply 64KiB、総要求128KiB／総応答512KiB、64操作、16行・16列・cell 4096B、各出力stream 64KiBを上限とする。DB操作10秒、actor実行60秒、disconnect・join・runtime終了・EOF回収には別の有限期限を置く。同期guest全体のCPU取消し保証はなく、外側の所有process期限を必須とする。

終了時は、guest exit、完全CGI応答、両stream EOF、pending/unread/open channelなし、physical disconnect、actor exit/join、入力再照合を確認する。guest失敗時もchannelをsealしactor清掃を試み、未確認を成功にしない。全資源の即時回収は未証明である。

## Linuxの所有DB試験

[公式MySQL](https://hub.docker.com/_/mysql) 8.4.13と[公式MariaDB](https://hub.docker.com/_/mariadb) 11.8.9のlinux/amd64 manifestを公開Registryから取得し、応答bytesとdigestを照合して[固定](container-images.json)した。tagだけに依存せず、image config digestも確認する。publisher署名の検証とは呼ばない。

[linux_database.py](linux_database.py)は新しいUUID名・label・CIDのcontainerだけを所有し、fresh tmpfs datadirとloopback公開portを使う。版・datadir・port・PIDfile=1・image・所有labelを確認してから専用schema/userを作る。資格情報はprivate env/configだけに保存し、public recordへ含めない。停止・削除の前にも所有identityを再確認し、CLI作成中断時は厳密に同じname/label/imageのCIDだけを回収する。

## 検証の現在地

[Windowsのbuild-only記録](evidence/win32-build.json)で、locked/offline compile、16 unitの実列挙と全件通過、通常binary生成、child reap、source再照合を確認した。CGI lint・導出gate・不正queryの接続前拒否も確認した。この記録はDB／php-wasm実行の成功を意味しない。

実guestは[Linux CI](../../.github/workflows/php-wasix-wpdb.yml)で同jobの3patch moduleをbuildし、二つのDB profileへ実行する。JSON evidenceだけをuploadし、binary・nonce・password・private設定を配布しない。

初回[CI 41458933505](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/41458933505)は、両DBともPHPをCGI実行した後、mysqli拡張の入口条件で失敗した。通常PHPの比較hostと異なり、この固定WASIX候補にはmysqliが組み込まれている。0 RPC・0 API・Rust DB未接続のままで、所有DBの正常停止・削除は確認した。修正では組込み拡張の存在を実記録・固定profileとして検査し、接続後のwpdb handleがmysqliではなくRust RPC用opaque handleであることも実検査する。元の27ケースとadapterの業務ロジックは変更しない。

```powershell
python experiments/php-wasix-wpdb-probe/prepare.py
python experiments/php-wasix-wpdb-probe/run_check.py --build-only --record
```

通常WP bootstrap、実テーマ・plugin、非ASCII query/escape、列名以外のmysqli metadata、非UTF-8 ERR、TLS、永続cache、製品ABI／runtime採用はこの固定試験の範囲外である。
