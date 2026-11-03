# 既存HTTP status lineとJSON応答setterのnative比較

固定の通常PHP 8.5.12 CGIで、既に`HTTP/1.1 200 OK`を設定した後の旧setterと新setter、既存404を新setterで200へ変更するケースを比べる。旧setterは`header_remove()`後に`http_response_code(200)`とContent-Type設定、新setterは`header_remove()`後に`header('Content-Type: application/json', true, 200)`を実行する。

新setterの2ケースはstatus・全header・JSON・改行を含むCGI全bytesを固定期待値と比較する。旧setterも同じ期待値との一致を記録するが、警告を発生させることを試験条件にしない。不一致時は長さ・SHA・固定警告counterだけを保存し、未知のraw出力やホストpathを公開しない。

```powershell
python -B experiments/php-cgi-status-line-probe/check.py --record
```

受入済みframing/startup helperを読み取りで再利用し、PHP配布78ファイルとarchive、source、所有INIを前後照合する。3個の所有子processを正常に回収し、一時領域は既知の5ファイルだけを個別削除する。WP・DB・Wasm・network・製品設定は実行／変更しない。このnative成功は固定WASIX forkでのsetter成功を証明しない。

初回native実測では、新setterの200→200と404→200はCGI全262 Bが期待値と一致した。旧setterは820 Bとなり、既存status line後の`http_response_code()`警告と、その後のheader変更警告を各1件観測した。旧setterの完全一致はfalseのまま保持し、全3応答が一致したとは扱わない。3子processの正常終了・EOF・回収と、配布物・source・INI不変、所有一時領域の清掃を確認した。
