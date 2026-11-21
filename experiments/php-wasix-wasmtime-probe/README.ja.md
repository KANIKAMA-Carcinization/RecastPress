# Windows WasmtimeでのPHP-WASIX起動可否

固定PHP-WASIX 8.5.12 moduleをローカルWindows上のWasmtime 36 / Craneliftで起動できるか判定する隔離probe。WASIX固有host関数はtrapとして登録し、固定`--version`以外のguest入力は受け付けない。

```text
python experiments/php-wasix-wasmtime-probe/record.py
```

2026-11-21の実行ではmodule compileが`Throw` exception-handling operatorをunsupportedとして拒否し、guestは開始しなかった。[source-bound receipt](evidence/windows-wasmtime-exception-rejection.json)に固定artifact hash、host/source inputs、stdout、failureを記録する。

この試行はWindows上のWasmer 7.2.0で記録済みのCranelift lowering panicとは別経路の診断。PHP-WASIX guest成功、hook adapter実行、Rust peer結合、WordPress bootstrap、product runtimeを示すものではない。
