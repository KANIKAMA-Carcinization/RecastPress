# Native PHP CGI の query と起動設定

固定 Windows x64 PHP 8.5.12 の起動設定だけを調べる独立実験。WP、DB、Wasm、HTTP listener、mail／HTTP／socket送信を実行せず、既存probeや製品設定も変更しない。

`php-cgi.exe` の引数を空にし、`PHPRC` に新規所有ディレクトリの `php.ini` **ファイルの絶対パス**を渡す。`PHP_INI_SCAN_DIR` は空、他のPHP設定環境は継承しない。設定は固定distributionの `ext` ディレクトリ、`extension=mysqli`、`disable_functions=mail`、`allow_url_fopen=0`、`register_argc_argv=0`。`expose_php` は上書きせず元の既定動作を保持する。実際に読んだINIのパスは公開せず、所有ファイルとの一致boolと前後SHAだけを記録する。

query環境が未設定、空文字、`flag`、`rp=1` の4ケースはすべて同じ設定で成功した。mysqliロードtrue／mail関数なし／URL fopen無効／argc生成無効、所有INI一致true、追加INIなし。各GETで `php://stdin` の全18 bytes（NUL・非UTF-8 byteを含む）のlength／SHAが一致した。4つのquery自体や `$_GET` の違いは変更せず、それぞれの実値も検証する。

対照として、INI環境を渡さず `-n -d ...` で同じ設定を要求する2ケースも保存する。query未設定は要求設定に一致したが、空文字はmysqli未ロード／mail関数あり／URL fopen有効だった。この空queryの実測は `requested_profile_matched:false` の負の観測として維持する。実験全体の成功は、4つのINI方式と、この既知の対照差を再現したことを意味する。CLIオプション方式の空queryを成功に読み替えない。

全6プロセスで完全CGI JSON、元の `X-Powered-By: PHP/8.5.12` と `Content-Type: application/json`、stdout／stderr EOF、exit 0、空stderr、実子プロセスreapを要求する。1プロセス10秒、stdout／stderr各64 KiBの**取得中**上限、stdin 4 KiB以下。元ZIP・manifest・全78 distribution files／全78 ZIP membersとsourceを前後照合し、所有configの前後SHA、コピーしたfixtureの完全一致、専用一時ファイルの清掃を検証する。未知ファイルの再帰削除や、他プロセスへのkillは行わない。公開JSONは環境のキーだけで、絶対ホストパスやINI本文を含めない。

```powershell
python -B experiments/php-cgi-startup-probe/check.py --record
```

結果は [evidence/windows-native.json](evidence/windows-native.json)。再実行時は既存記録をSHA名の別ファイルへ保存する。これはnative CGIで成立した将来のgeneric request設定案であり、WasmのINI読込、製品request ABI、任意環境での起動互換性、OSのプロセスメモリ上限は未検証である。
