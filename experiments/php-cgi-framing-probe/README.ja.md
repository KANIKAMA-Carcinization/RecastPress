# PHP CGI の出力サイズ・buffer・header_remove 比較

WordPressやDBを使わず、CGI出力だけを切り分ける独立診断。小さいJSONと約140 KBのJSON、bufferなしとhome候補と同じ2段buffer、`header_remove()`なしとありの組み合わせ8ケースを比較する。製品実装の修正やWordPress互換性の証明ではない。

各固定wrapperがcommonを読み、PHP 8.5.12／64 bit／cgi-fcgi／argc 0、GET・空body、opaque query `rp_request=1`と固定URIを検査する。payloadは固定ASCIIの64／70,000 bytesで、JSONにはhexとSHAを含める。nativeとWasmで同一のschema・key順・全body bytesを期待する。bufferありは外側bufferと4096 bytes callbackを作り、固定8,192 bytesをcallbackへ回収して空文字を返し、`ob_flush()`後に両bufferを破棄する。JSON送出前のContent-Type置換と、全header削除の有無だけを変える。

headerを残すケースの期待値は元の`X-Powered-By: PHP/8.5.12`とJSON Content-Typeの2件、削除するケースはJSON Content-Typeの1件。status・順序・全header・全bodyを正規化せず比較する。未知のstdout／stderr本文や環境の絶対パスは公開しない。

```powershell
python -B experiments/php-cgi-framing-probe/prepare.py
python -B experiments/php-cgi-framing-probe/check.py --record
```

prepareは有限9 PHPファイルとchecker／preparerのSHA、各期待body／CGI wireのlength・SHAとheader contractをignored `target/php-cgi-framing-probe/prepared.json`へ記録するだけで、PHP／Wasm／DB／networkは実行しない。

checkは固定native PHP distribution全78ファイル・元ZIP全78memberを前後検証し、専用INIを`PHPRC`で選び、追加scanを空にする。既存startup helperを固定SHAで読み取る。子プロセス取得関数は元sourceからの正確な2置換（10秒→20秒）だけで導出し、独立namespaceでstdin 0 bytes・取得中のstdout／stderr各3 MiB上限・実EOF・全8子のreapを確認する。元module globalsや旧probeは変更しない。清掃は新規所有ディレクトリ内の既知10ファイルだけを対象とし、再帰削除しない。

[nativeの実測](evidence/windows-native.json)では8ケースすべてで全CGI wireが期待bytesと一致した。小さいbodyは389～392 B、大きいbodyは140,264～140,267 B。headerを残す4ケースは2件、削除する4ケースは1件で、全子のEOF／正常終了／reap、source・distribution・INIの前後照合、所有scratch清掃も完了した。

native成功をWasmの実測へ読み替えない。Wasmでの同一module・同一fixture・同一CGI contextの実行は別途必要であり、このディレクトリにはWasm hostをまだ追加していない。
