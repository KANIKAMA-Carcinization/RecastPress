# PHP WASIX CGI framingの独立比較

受入済み通常PHPの[8ケース](../php-cgi-framing-probe/README.ja.md)を、同じ固定3patch PHP CGI／Wasmer 7.2.0・WASIX 0.702.0・Craneliftで比較する独立候補。WP・DB・HTTP listenerは使わず、通常PHPのfallbackも設けない。

元の9 PHPファイルを変更せずreadonly SourceFsへ載せる。small／large、出力bufferあり／なし、header_removeあり／なしの全8応答を、固定native証跡SHAと結び付けたstatus・全header・JSON・改行を含むCGI全bytesで比較する。moduleは1回だけcompileし、要求ごとにfresh WASI/runtime/permission channelを構成する。guestのnetwork・host環境引継ぎ・home mappingは無効。DB actorや資格情報は存在しない。

不一致時に公開するのは長さ・SHA・固定分類・期待出力との先頭／末尾一致長と改行数のみ。未知のraw stdout/stderr、HTML、headerを公開しない。失敗したケースも8件すべて実行し、EOF・終了・shutdown呼出し・channel終了・source/module前後照合を別に記録する。runtime shutdownの復帰を物理資源の完全回収の証明にしない。

```powershell
python -B experiments/php-wasix-cgi-framing-probe/prepare.py
python -B experiments/php-wasix-cgi-framing-probe/build_check.py
```

preparerはnative実行ファイルを必要としない。Windowsではbuildとpure/WAT unitだけを実行し、実PHP Wasmを実行しない。Linuxでは固定moduleのbuild・receipt準備後に次を実行する。

```sh
python -B experiments/php-wasix-cgi-framing-probe/run_check.py --record
```

Cargo manifestとlockはtheme probeのpackage名だけを変更し、同じ共有target cacheを使う。再利用した固定module検証関数のbodyを元hostとbyte照合する。buildの前後で旧bootstrapのreceipt・binary・unit binaryも再検証する。この独立比較の成功は元WPテーマのframingや製品runtimeの成立を意味しない。

Windowsでは26 unit（実WASIの固定WATと警告の安全な分類を含む）・191 source inputs・locked/offline build・4所有childの回収が通過した。[build記録](evidence/windows-build.json)は旧bootstrapのreceipt・binary・unit binaryの前後一致も保持する。実PHP Wasmの8ケース比較はLinux CIで別に実行する。
