# PHP-Wasmから使うRust要求内オブジェクトキャッシュ

固定WordPress 7.1.3の標準キャッシュを使い、2グループだけをRustの要求内ストアへ接続する。PHP側のキー正規化、複数APIの順序、他グループの標準実装を維持し、同じ56観測を比較する。ゲストとRustの通信には `/rp-object-cache/channel` の専用仮想ファイルを使う。PHPの子プロセスやネットワークを使わず、1回の要求で1ハンドル、1応答ずつ読み切ってから次へ進む。[通常PHP／Rustの基準](../wp-object-cache-rust-probe/README.ja.md)

source `023a8a7` のLinux CIでは、PHP-Wasmのcompile1回から2つの新規WASI要求を実行し、56観測がどちらも通常PHP基準と一致した。各要求でRust側32操作、送信2,271 B、返信1,105 Bを記録し、`finish` とcloseの後に未読応答・エントリー・予約バイトが0になった。CGIの正常終了とEOF、23 unit、69入力と生成4ファイルの照合も通過した。[成功証跡](evidence/ci-42495270303/provenance.json)

初回CIはPHPが返した終了応答の `ok: true` をホストの期待値に含めていなかったため、`guest_transport_mismatch` で停止した。元の失敗判定を保存し、修正版では完全な終了応答を照合した。[初回証跡](evidence/ci-42495113357/provenance.json)

対象は固定2グループの要求内キャッシュである。共有キャッシュの保持とDB変更による失効は、別の検証を経て接続する。
