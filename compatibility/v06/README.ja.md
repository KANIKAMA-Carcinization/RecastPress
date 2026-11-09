# v0.6 移植台帳の基点

[manifest.json](manifest.json) は、既存対応台帳の79 API・2 componentを継承した限定台帳である。WP全API・全ファイルの列挙完了や、v0.6のQuery・保存・認証の実装完了は示さない。

元の実装基点 `b3c819b`、設計 `ad2c286`、現在の製品版、元WP sourceのpath・line・hash、Rust実装・試験、過去の証跡と制限を分けて記録する。未移植の標準PHP処理は `PhpCoreDelegate` とし、互換宣言・PHP言語操作の `PhpSurface` と混同しない。

検査は次のコマンドで行う。

```powershell
python -B compatibility/v06/validate_manifest.py --self-test
```

既存registryとの継承内容、Cargoの製品版、必要な分野と一時委譲の出口、実装・試験・証跡の参照を確認する。12件の変異入力で、source hash・参照先・制限の改変、古い製品版、重複・欠落、証跡なしの完了昇格、JSON重複keyを拒否する。

このvalidatorは継承基点の整合検査であり、新しいAPIの差分実行やPHPの全意味を検証するものではない。新規移植の受入は、対応する比較・統合・元本体退役の証跡を追加する変更として行う。現在の進捗は [v0.6開発記録](../../docs/v0.6-development.ja.md)を参照する。
