# v0.6 PHP-WASIX surface and continuation probes

This independent workspace runs fixed language oracles and an interactive continuation bridge under the locally cached, fixed PHP 8.5.12 WASIX module. The bridge uses the same Rust peer as the native PHP probe. Neither profile executes WordPress or a database.

## Run

From the repository root, run the fixed Windows/Cranelift profile:

```powershell
python experiments/php-compat-v06-probe/wasm/run.py
```

The Cranelift profile explicitly disables optimization (`CraneliftOptLevel::None`) as a bounded compatibility experiment. `python .../run.py --llvm` builds with the existing locked LLVM feature, if the local toolchain is available; it does not fetch dependencies.

The entrypoint builds the explicit one-shot binary with `cargo build --offline --locked --bin recastpress-php-compat-v06-wasm-probe`, then starts the fixed host as a separate owned child through `scripts/owned_probe.py`. Windows reuses `target/php-wasix-wp-bootstrap-probe/cargo-target`; Linux defaults to `target/php-compat-v06-wasm-linux`. `RECASTPRESS_V06_TARGET_DIR` or `CARGO_TARGET_DIR` can select another target path only within repository `target/`. The Rust host accepts no CLI arguments, hashes the Wasm, source files, and its own build inputs before running, mounts exactly four allowlisted PHP files as a read-only in-memory filesystem, disables inherited host environment/network/home, and invokes PHP CLI as `-n /fixture/<fixed-script>`. It checks the surface oracle's 15 literal assertions and the strict JSON oracle's independent assertions, then emits a JSON receipt to stdout. Diagnostics and phase markers go to stderr.

`run.py --self-test` checks phase classification with synthetic stderr containing cumulative phase markers. A generated failure receipt is evidence of an unsuccessful run, not a passing gate; the command returns nonzero. Each run writes a timestamped receipt rather than replacing earlier evidence. Receipt input hashes, including `scripts/owned_probe.py`, and the built host binary are checked before and after execution; any change invalidates a nominal success. The receipt includes the host's actual oracle results. Build and host execution are separate owned child processes with bounded timeouts and reaping. Raw child output stays under `target/` and the receipt exposes only relative locations and digests.

The bridge adds a local `recastpress-wp` dependency with default authentication features disabled and a direct dependency on the already locked serde version. All 427 registry package identities remain unchanged. The one-shot map exposes only `surface_oracle.php`, `strict_json_oracle.php`, `dispatcher.php`, and `strict_json.php`, the files required by those two tests. It does not expose the repository root, `.reference`, host environment, network, or database credentials. Earlier host profiles and their evidence are preserved.

## Interactive continuation bridge on Linux

```sh
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --self-test
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --verify-only
```

The fixed [bridge host](src/bin/bridge.rs) compiles the pinned PHP module once and starts fresh guest environments for the surface, throwable and recovery scenarios. It mounts the four one-shot oracle files plus `bridge_driver.php` and `recovery.php` read-only. A guest runner thread and the shared [Rust peer](../../../crates/recastpress-wp/examples/v06_bridge_peer/mod.rs) exchange JSONL through WASIX pipes. PHP can suspend a callback in `fgets`, call a child Rust operation, handle a nested PHP callback, and continue using the same object. This follows the continuation protocol without a host call recursively re-entering an active Wasm invocation.

The [Linux bridge receipt](evidence/bridge-20261109T233155.968907Z-success.json) passed both scenarios. Surface exchanged five PHP frames and four Rust frames, preserving the public object alias, nested name change, flag and global replacement. Throwable exchanged four frames each way, rethrew the same PHP `RuntimeException`, preserved the seven-step catch/finally trace and one callback invocation, recovered the child, and rejected duplicate resume and use after abort. Both guest environments returned successfully and their runner threads were joined. The [snapshot origin](evidence/wsl-bridge-origin-20261109T233202.341526Z.json) and before/after source and binary hashes bind this run to 83 input files in the repository. Observed module compilation was 115,162 ms and scenario execution together was 379 ms in the development profile; this is not a production performance acceptance result.

The [final Linux recovery receipt](evidence/bridge-20261110T005353.483934Z-success.json) exercises three interruptions while the root PHP effect is pending. PHP exits 23 after the host starts the callback, writes a 95-byte incomplete `call_core` frame and exits 24, or waits until the host closes the stdin pipe and exits 26. For each case, the runner confirms the guest exit and joins the guest and stderr threads. All six fault guest/stderr threads joined with empty stderr. It then starts a fresh PHP guest for the surface scenario using the same compiled module. The fresh guest reproduces the expected alias, public state, global replacement and byte growth. A new request starts in the compiled module after each interruption; the prior PHP heap is not reused. The [WSL origin receipt](evidence/wsl-bridge-origin-20261110T005400.560501Z.json) binds 84 inputs to the repository snapshot. The run took 2026-11-10 00:50:54–00:53:53 UTC (09:50:54–09:53:53 JST). Module compilation was 165,003 ms and scenario execution was 1,988 ms in the development profile, a single-run observation rather than performance acceptance.

Each recovery case also exercises an isolated Rust continuation. `finish_operation` reports `PendingEffect`. Abort leaves depth zero and state `Aborted`, and a late resume is rejected. This check covers that recovery continuation only. The shared peer's general error path drops its continuation without calling abort, so these fixtures do not establish general request cleanup. CPU-bound guest cancellation, Wasm trap recovery, and production worker reuse remain unverified.

The wrapper builds only `recastpress-php-compat-v06-wasm-bridge`, limits each captured stdout/stderr stream to 1 MiB, and owns process groups with build/host deadlines of 600/300 seconds. Guest pipe reads and joins are bounded. Raw diagnostics remain in run-specific private `target/` files; public receipts contain relative paths and digests. `--verify-only` requires the latest chronological receipt to have passed, matching source/binary hashes, successful and reaped build/host children, and both semantic results. It never falls back to an older success after a later failure. Self-tests reject mutated observations and cover same-second success/failure ordering.

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

After adding the shared peer dependency, the [one-shot regression run](evidence/run-20261109T233548Z-success.json) again passed 15 + 11 checks. Its input set now also binds the root Cargo files and all local WP crate sources, totaling 80 files; the [new origin receipt](evidence/wsl-language-origin-20261109T233551.142657Z.json) verifies the repository/snapshot correspondence. This verifies the updated package configuration separately from the interactive bridge.

To repeat on Linux, prepare Rust 1.94.0 on `PATH`, the fixed artifact above, the C/C++ build dependencies used by the existing host profile, and the locked Cargo cache (or equivalent Cargo-vendored sources), then run `python3 -B experiments/php-compat-v06-probe/wasm/run.py` from the repository root. The runner remains offline and locked. Local WSL build caches are retained for subsequent probes.

LLVM execution remains unverified for these v0.6 fixtures. The Linux bridge covers the fixed surface and throwable roundtrips, plus exit, incomplete-frame and pipe-close recovery followed by a fresh guest. WordPress bootstrap, Rust Query and full v0.6 acceptance are outside these probes.
