# 有効なUTF-8のwpdb基準候補

元のWordPress wpdbとnative mysqliで、日本語BMP、emojiと補助平面漢字、apostrophe／backslash／`%`／`_`、合成済みaccentと結合accentの5種類を扱う独立基準。無効UTF-8・不正byte列・charset変更・任意collation・Rust／Wasm互換性は対象にしない。

旧CRUDと同じ所有`rp_crud_fixture`を使う。ASCIIの一意なlabel、VARCHARのnote、同じUTF-8 bytesを持つVARBINARYのpayloadをinsertし、numeric id順に読み、Unicode値でupdate、同値update、全delete後の空tableを確認する。accentの2種類を一意キーや文字列sortに使わず、正規化せずに区別する。

`prepare('%s')`、`esc_like`、FROMなしの`SELECT %s AS utf8_value`、継承した`insert`／`update`／`get_results(ARRAY_N)`／`delete`を31観測・22 SQL（元のschema照会1件を含む）として固定する。prepare結果内のランダムなpercent用tokenは元の`remove_placeholder_escape()`で戻すことをAPI名でも明示する。SQLは拒否／書換えず、元query filterのtoken解除後に全合成SQL bytesを記録する。

native起動は旧CRUDのコマンドから新entryへの置換と`extension=mbstring`追加だけを導出する。固定Wasm候補にあるmbstringと元wpdbの検証分岐を合わせるためであり、旧profileは変更しない。元wpdbはmbstringがない場合、有効UTF-8でも追加のDB変換queryを送る。この候補はmbstringありの基準である。VARBINARY列を含むtableのquery文字検査はbinary扱いになるため、text-only table全体のcharset互換性は実証しない。FROMなしのSELECTは接続utf8mb4で文字列往復を確認する。

```powershell
python -B experiments/wpdb-utf8-probe/prepare.py
python -B experiments/wpdb-utf8-probe/check.py --backend mysql --record
python -B experiments/wpdb-utf8-probe/check.py --backend mariadb --record
```

prepareは旧helper14ファイルの固定SHA、元entryからの正確な導出、固定schema、全期待typed値・SQLを検証し、ignored targetへ記録するだけでPHP・DB・networkを実行しない。checkは元のnative package gateで固定PHP全78ファイル（mbstring DLLを含む）と元WP8ファイルを前後確認する。旧ownership helperを読み取りで使い、新規所有MySQL／MariaDB、TLS検証付きadmin、別の限定CRUD role、正常admin shutdownと全子reapを要求する。native fixture接続のTLSは旧profile同様disabledであり、新たなTLS実証とはしない。

公開observationは全期待typed値・全SQL bytesとの照合後だけ保存する。不一致時の未知PHP本文やDBエラー本文は公開せず、固定失敗codeとlength／SHAだけを記録する。資格情報・秘密入力・DB logは所有ignored runに留める。元の仕様、旧probe、旧native証拠は変更しない。

## native実測

[MySQL](evidence/windows-mysql-native.json)／[MariaDB](evidence/windows-mariadb-native.json)とも31観測・22 SQLが成功した。日本語・絵文字・accent 2形をVARCHARとVARBINARYで区別して保持し、元prepare／escape／CRUDの戻り値・全SQL bytesを固定期待値に照合した。metadataの元SHOW FULL COLUMNSは1回で、最後の表は空になった。未使用wire fixtureも独立したadmin照会で空を確認した。

両DBで19 source inputs、固定PHP78／元WP8 sourceの前後照合、正常admin shutdown、全所有childの回収を確認した。MySQL記録SHAは`c1d7a9e4d55c68b030830e1d8e883d52f42bae031df4bd3062f3bcf4d5370ada`、MariaDBは`18c01e8105f7e41e407ebdd36f4e057233b3d808028d06b37168877e2ec2ba90`。Rust接続／Wasmでの同一corpus、無効・過長UTF-8、文字数／byte数の上限境界は次の比較対象である。
