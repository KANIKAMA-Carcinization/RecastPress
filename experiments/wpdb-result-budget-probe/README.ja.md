# 有限なDB結果materializationの独立probe

通常WPのnative起動で観測した115件・最大23,778 bytesのoption値を受け、全行・全値を保持する結果collectorの予算を独立して確認する。合成tableへの通常Rust driver接続であり、WP API、Wasm、RPC frame、製品の予算値の受入ではない。

既存mysql_async 0.37.1／mysql_common 0.37.3の固定2patch stageを読み取りで再照合し、独立Cargo lockとtargetで`--locked --offline` build・7 pure unitを実行する。元source・stage・lock・既存記録は変更しない。

候補上限は256行、16列、cell 65,536 bytes、cell raw合計262,144 bytes、識別子256 bytes、metadata識別子合計4,096 bytes、符号化結果524,288 bytes。metadata合計はschema・table・org_table・name・org_nameのraw byte長を数える。保持前にborrowした行全体を検査し、hexは固定上限のwriterへ直接出力する。超過時はtyped failureとし、部分行を返さない。driver自身のpacket／row確保やtask全体の資源回収は、このcollector予算で検証したことにしない。

Rootの所有DB helperが新UUIDのMySQL／MariaDBだけを起動し、257行の固定tableを作る。id1は23,778 bytesのNUL・`ff`を含むbinary、id2はNULL、id3は空bytes、以後は128 bytes。主SELECTの115行はraw cell合計39,048 bytesで、hexだけでも78,096文字になり旧64 KiB replyを超える。別native CLIでも115行の全値を固定fixtureへ照合する。SQLへの`LIMIT`追加・値や件数の補正はしない。

実行caseは、115行成功、0行でもmetadata保持、257行のrow拒否、4,096-byte cell拒否、raw exact 39,048成功／39,047拒否、先頭結果の実encoded長Nで成功／N-1で拒否、fresh connectionで115行回復の9件。1case1物理connection、poolなし、全9 IDの相違と各切断を確認する。成功結果の全行・NULL／空bytes・column順・metadata・実encoded bytes／SHAをcontrollerでも検査する。

接続・queryは各10秒、DB workは55秒、切断猶予を含む60秒の範囲、runtime終了猶予1秒、所有controllerはclosed stdinと70秒の子process期限を使う。結果超過後にconnectionを使い回さず、direct disconnectを期限付きで行う。無制限なserver drainや全資源の即時回収を保証するものではない。最終257行・payload合計56,290 bytes・最大23,778 bytesの不変、DB identity、正常admin shutdown、全所有childのreapが揃うまで成功としない。

```powershell
python -B experiments/wpdb-result-budget-probe/build_check.py --record
python -B experiments/wpdb-result-budget-probe/database_check.py --backend mysql --record
python -B experiments/wpdb-result-budget-probe/database_check.py --backend mariadb --record
```

stdinは固定schema・loopback・port・固定user/database・48桁hex secret・TLS disabled/nullの厳密8keysだけで、任意SQLは受け付けない。公開JSONにpasswordやそのhexを出さず、driverの任意Display／Debugも記録しない。失敗原記録はhash名の別ファイルへ保存し、成功で消さない。

2026-11-03の通常権限実測で、[MySQL 8.4.13](evidence/windows-mysql.json)と[MariaDB 11.8.9-MariaDB](evidence/windows-mariadb.json)の両方が9case通過した。115行はraw 39,048 bytes、encoded 79,758 bytes、metadata識別子合計169 bytes、0行結果はmetadataを保った390 bytes。raw 39,047／encoded 79,757／cell 4,096／257行の4拒否はtyped failure・result null・partial falseで、exact境界とfresh回復は全値一致した。各DBとも9つの物理connection IDが異なり、全切断・独立native全値比較・最終257行／56,290／23,778 bytes・正常shutdown／全所有childのreapを確認した。Rust DB workの観測時間はMySQL 209 ms、MariaDB 273 msであり、DB初期化やbuild時間を含む性能指標ではない。

[最終build記録](evidence/windows-build.json)はcontrollerを含む54入力、locked/offlineの7 pure unitとbinary identityを固定する。controller追加前のbuild記録もhash名で保存している。この結果で成立したのは合成shapeの有限結果処理であり、通常WP全体の必要予算、非ASCII／上流wpdb validation、ERR raw bytes、WASI traversalや製品接続は別に検証する。
