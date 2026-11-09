# autoloadの共有結果を使えない場合のDB読み取り

標準WordPressのホームと3件の404を、同じRustホストで新規PHP-Wasm要求として処理する。最初の404で標準autoload SELECTを実行し、次の404で共有結果を返す。最後の404の前に、所有DBの別接続で `rp_options` の固定オプションを更新する。

監視接続が同じソースとログを確認しても、観測した位置が保留中の位置より進んでいれば、共有結果を破棄する。最後の404は通常のDB経路で同じSELECTを実行する。その経路で対象SQLが成功したこと、対象応答の変更、残りのRPCと標準PHPのページ応答の一致、追加ヒットと追加格納が0件、終了時の予約が0バイトであることを検証する。

今回の条件は、所有DBの同一ログで位置が有限の範囲だけ進んだ場合に限る。照合処理の別のエラーや、要求後の再照合に失敗した応答はエラーとして扱う。通信断からの復帰と再同期は別の試験とする。[コミットを再生して失効する試験](../php-wasix-autoload-binlog-probe/README.ja.md)

## 適用保留中の監視の補強

従来の固定試験では、最初のイベントを受信した後、適用先の指定までストリームの読み取りを停止していた。その間のEOFや受信エラーは、指定が届くまで検出できなかった。適用を保留している間も読み取りを続け、イベントを最大1,024件・ヘッダー上の合計サイズ1 MiBまで保持する。EOF、受信エラー、上限超過は監視taskの失敗とし、既存の終了処理で適用位置を無効にする。記録済みイベントは指定後の既存検証へ順番に渡す。

イベント受信後、適用済み位置が旧値のままでも、保留ありとして既存permitを使わない。検証済みの更新を適用したときはキャッシュの破棄と位置更新を同じロック内で行う。監視taskのfutureが破棄される際にも適用位置を無効化する。WindowsのオフラインRust試験では71件成功、所有DB専用の1件は通常実行時にスキップする。合成入力でEOF、受信エラー、保留上限、task中断・panicとpermit拒否を確認した。

所有MySQL 8.4.13とMariaDB 11.8.9の実測では、旧位置のpermitを取得し、別接続の更新に伴うbinlogイベントを少なくとも1件受信した後、適用前のpermitが使えないことを確認した。監視接続だけを切断してtaskの終了を待ち、その後もpermitを拒否し、監視制御接続で新しい値を直読した。監視制御接続を閉じ、新しい監視を更新済みの固定値で起動し直した。新しいbinlog位置の照合成功、空キャッシュ、旧permitの拒否も確認した。両DBの正常終了と所有プロセス・作業ディレクトリの回収を確認した。[MySQL証跡](evidence/windows-held-disconnect-v5-mysql.json)・[MariaDB証跡](evidence/windows-held-disconnect-v5-mariadb.json)・[実行入口](check_disconnect_owned.py)

別の所有DB試験では、固定autoload SELECTの有効な応答形を予約し、イベントを実binlogから受信した後に行とCOMMITを検証・適用した。保留中は旧permitが拒否され、適用後は旧permitが無効で、新permitからもキャッシュ応答が返らないこと、終了時に予約が0になることを両DBで確認した。DB・所有プロセスを正常回収した。[MySQL適用証跡](evidence/windows-held-apply-v4-mysql.json)・[MariaDB適用証跡](evidence/windows-held-apply-v4-mariadb.json) 実行入口は `python -B experiments/php-wasix-autoload-bypass-probe/check_disconnect_owned.py --case apply --mysql-client-for-mariadb --record`。

再起動試験の初回は固定初期値しか許可しない制約に当たり、再起動後の新しい値で開始できなかった。期待する固定値を明示できる起動関数を加え、未許可の値は引き続き拒否する。試験入口は `--case disconnect` で切断後の再起動も実施する。

この試験はPHP-Wasmやwpdb actorのDB直読経路を通していない。イベントの到着前に発行されたpermitの鮮度、silentな通信断の期限、実DB接続中のtask中断やpanic、同じprocess内での再接続・再同期、変更後のLinux両DB比較は未検証。ここで確認したのは監視制御接続と監視taskを終了した後、新規監視を作る再起動経路である。上限はイベントの申告サイズを対象とし、driver内の受信や全processメモリの上限ではない。既存Linux証跡はこの変更前の結果である。

MariaDBの初回2試行は管理接続のTLSエラー `SEC_E_NO_CREDENTIALS` で期限切れとなり、試験へ到達しなかった。所有DBは強制回収し、作業ディレクトリも削除した。再試験では、検証済みMySQL 8.4.13クライアントを管理操作に使用し、CAと接続先のTLS検証を維持した。監視・writerの接続は従来どおり所有DB向けの固定試験条件である。実行は `python -B experiments/php-wasix-autoload-bypass-probe/check_disconnect_owned.py --mysql-client-for-mariadb`。

Rustの通常テストは71件成功、所有DB試験2件は専用controllerからだけ実行するためskipされた。適用試験で使ったキャッシュ応答形はPHP-Wasmが返した実データではなく、形式検査用の固定fixtureである。既存PHP-Wasm経路へ監視・失効を接続したわけではない。

## 両DBの実測

source `04b4be1` のLinux CIで、同一ログの位置はMySQLで166729から167160、MariaDBで277458から277975へ進んだ。両DBとも初回のキャッシュ対象SQLは1回、共有ヒットは1回、格納した応答は87,682バイトだった。最後の404では予約を0バイトにしてから標準SELECTをDBで直接実行した。対象応答のダイジェストだけが変わり、残りのRPCと標準PHPのページ応答は一致した。Rustテスト66件、固定入力268件と生成ソース6件、所有DBの正常終了を照合した。[MySQL証跡](evidence/ci-42517825080-mysql/provenance.json)・[MariaDB証跡](evidence/ci-42517825080-mariadb/provenance.json)

source `b3bdc7b` のLinux CIでは、監視接続が既存のnonceとともに独立した識別値を読み、3件の404それぞれで実際にSQLを実行する接続から同じ値を取得した。Rustホスト内で、識別値、サーバー版、異なる接続IDを確認してからWordPressのDB要求を受け付けた。監視用nonceの参照権限は監視接続に持たせた。両DBでWordPressから見えるDBの操作列、標準PHPとのページ応答、共有ヒットと更新後のDB直読を照合した。固定入力269件、生成ソース6件、Rustテスト66件を確認した。[MySQL証跡](evidence/ci-42532767293-mysql/provenance.json)・[MariaDB証跡](evidence/ci-42532767293-mariadb/provenance.json)

ライブ監視との結合試験では、最初の404の前に専用のbinlog接続を開く。DB actorが対象SELECTを実行する時点で、要求が観測した位置と監視の適用済み位置を照合する。固定更新をコミットした後、監視側の適用を最後の404まで保留する。この要求は位置の不一致により通常SQLを使う。その後、同じbinlog接続で行とXIDを検証し、共有結果の破棄と適用位置の更新を一緒に行う。比較用の要求前後の照会は維持し、通信量や応答時間の測定は後続とする。
