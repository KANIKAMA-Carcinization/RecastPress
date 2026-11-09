# v0.6 PHP-WASIX surface probe

This independent workspace runs the fixed PHP surface oracle under the locally cached, fixed PHP 8.5.12 WASIX module. It does not execute WordPress, a database, the v0.6 Rust peer, or the interactive continuation bridge.

## Run

From the repository root, run the fixed Windows/Cranelift profile:

```powershell
python experiments/php-compat-v06-probe/wasm/run.py
```

The Cranelift profile explicitly disables optimization (`CraneliftOptLevel::None`) as a bounded compatibility experiment. `python .../run.py --llvm` builds with the existing locked LLVM feature, if the local toolchain is available; it does not fetch dependencies.

The entrypoint builds with `cargo build --offline --locked`, then starts the fixed host as a separate owned child through `scripts/owned_probe.py`. Windows reuses `target/php-wasix-wp-bootstrap-probe/cargo-target`; Linux defaults to `target/php-compat-v06-wasm-linux`. `RECASTPRESS_V06_TARGET_DIR` or `CARGO_TARGET_DIR` can select another target path only within repository `target/`. The Rust host accepts no CLI arguments, hashes the Wasm, source files, and its own build inputs before running, mounts exactly four allowlisted PHP files as a read-only in-memory filesystem, disables inherited host environment/network/home, and invokes PHP CLI as `-n /fixture/<fixed-script>`. It checks the surface oracle's 15 literal assertions and the strict JSON oracle's independent assertions, then emits a JSON receipt to stdout. Diagnostics and phase markers go to stderr.

`run.py --self-test` checks phase classification with synthetic stderr containing cumulative phase markers. A generated failure receipt is evidence of an unsuccessful run, not a passing gate; the command returns nonzero. Each run writes a timestamped receipt rather than replacing earlier evidence. Receipt input hashes, including `scripts/owned_probe.py`, and the built host binary are checked before and after execution; any change invalidates a nominal success. The receipt includes the host's actual oracle results. Build and host execution are separate owned child processes with bounded timeouts and reaping. Raw child output stays under `target/` and the receipt exposes only relative locations and digests.

Cargo manifest/lock are copied from the existing fixed Wasmer/WASIX host probe and keep its dependency versions/features. The small readonly in-memory map exposes only `surface_oracle.php`, `strict_json_oracle.php`, `dispatcher.php`, and `strict_json.php`, the files required by those two tests. It does not expose the repository root, `.reference`, host environment, network, or database credentials. The existing host and its evidence are unchanged.

## Fixed inputs

| Input | Fixed identity |
| --- | --- |
| PHP Wasm | Locally cached artifact `.reference/php-wasix-8.5.12/php.wasm`, 39,727,549 bytes, SHA-256 `e64850fde91d98dff0bce2b95ed467591376586469b1f4d8bad5c8759c4edbe4` |
| PHP source lineage | PHP 8.5.12 artifact at the same fixed profile as `experiments/php-wasix-host-probe` (source commit noted there: `f4bfae79d5c19028349082f707930cf0951d23b9`) |
| Oracle fixture | `../fixtures/surface_oracle.php`, SHA-256 `206f97b74578014c24f7539b521745b3df2b507aaecc83c9c8f861b4a4be85e8` |
| Strict JSON fixture | `../fixtures/strict_json_oracle.php` plus `strict_json.php`, hashes are included in the runner receipt |

The Wasm module is the existing PHP-only CLI artifact. It is not a WordPress module and is not one of the separately patched CGI/RPC/WP-bootstrap artifacts; those PHP/WP probe artifacts have distinct hashes and maps. It runs PHP language behavior only. Native PHP oracle passed independently, but Wasm must pass its own embedded literal expectations.

## Status

The fixed profile has not passed Windows x64 acceptance. Both default Cranelift and explicit `CraneliftOptLevel::None` panicked during module compilation in `cranelift-codegen-0.133.0/src/machinst/lower.rs:1107` before PHP startup. The local dependency source points to the `TryCallExn` payload lookup. The [no-opt failure receipt](evidence/run-20261109T173431Z-cranelift_lowering_panic.json) and earlier default receipts remain unchanged. Changing optimization did not resolve this observed Windows failure.

The [Linux x86_64 Cranelift no-opt run](evidence/run-20261109T175143Z-success.json) passed both the 15-check surface oracle and the 11-check strict JSON oracle on Ubuntu 24.04 / WSL2. It used Rust 1.94.0, the locked offline dependencies, the same fixed PHP Wasm, and a byte-identical copy of the ten bound input files on an owned Linux temporary filesystem. The [origin receipt](evidence/wsl-origin-20261109T175145Z.json) checks that this snapshot matched the repository before and after execution and records the verified Rust component hashes. The build and host children exited successfully and were reaped. Observed module compilation was 124,595 ms and the two fixture executions together were 295 ms in this development host profile; these are observations from one run, not a production performance claim.

To repeat on Linux, prepare Rust 1.94.0 on `PATH`, the fixed artifact above, the C/C++ build dependencies used by the existing host profile, and the locked Cargo cache (or equivalent Cargo-vendored sources), then run `python3 -B experiments/php-compat-v06-probe/wasm/run.py` from the repository root. The runner remains offline and locked. Local WSL build caches are retained for subsequent probes.

LLVM execution remains unverified for this v0.6 fixture. The live `bridge_driver.php` is interactive and is not part of this one-shot host run. No Wasm callback-to-Rust nested roundtrip, WordPress bootstrap, Rust Query, or full v0.6 acceptance is claimed.
