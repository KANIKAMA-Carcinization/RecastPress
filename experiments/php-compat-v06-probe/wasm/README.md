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

The bridge adds a local `recastpress-wp` dependency with default authentication features disabled and a direct dependency on the already locked serde version. All 427 registry package identities remain unchanged. The one-shot map exposes only `surface_oracle.php`, `strict_json_oracle.php`, `dispatcher.php`, and `strict_json.php`, the files required by those two tests. The separate handle profile uses an expanded map only when explicitly selected. It does not expose the repository root, `.reference`, host environment, network, or database credentials. Earlier host profiles and their evidence are preserved.

## Interactive continuation bridge on Linux

```sh
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --self-test
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --verify-only
```

The fixed [bridge host](src/bin/bridge.rs) compiles the pinned PHP module once and starts fresh guest environments for the surface, throwable and recovery scenarios. It mounts the four one-shot oracle files plus `bridge_driver.php` and `recovery.php` read-only. A guest runner thread and the shared [Rust peer](../../../crates/recastpress-wp/examples/v06_bridge_peer/mod.rs) exchange JSONL through WASIX pipes. PHP can suspend a callback in `fgets`, call a child Rust operation, handle a nested PHP callback, and continue using the same object. This follows the continuation protocol without a host call recursively re-entering an active Wasm invocation.

The [Linux bridge receipt](evidence/bridge-20261109T233155.968907Z-success.json) passed both scenarios. Surface exchanged five PHP frames and four Rust frames, preserving the public object alias, nested name change, flag and global replacement. Throwable exchanged four frames each way, rethrew the same PHP `RuntimeException`, preserved the seven-step catch/finally trace and one callback invocation, recovered the child, and rejected duplicate resume and use after abort. Both guest environments returned successfully and their runner threads were joined. The [snapshot origin](evidence/wsl-bridge-origin-20261109T233202.341526Z.json) and before/after source and binary hashes bind this run to 83 input files in the repository. Observed module compilation was 115,162 ms and scenario execution together was 379 ms in the development profile; this is not a production performance acceptance result.

The [final Linux recovery receipt](evidence/bridge-20261110T005353.483934Z-success.json) exercises three interruptions while the root PHP effect is pending. PHP exits 23 after the host starts the callback, writes a 95-byte incomplete `call_core` frame and exits 24, or waits until the host closes the stdin pipe and exits 26. For each case, the runner confirms the guest exit and joins the guest and stderr threads. All six fault guest/stderr threads joined with empty stderr. It then starts a fresh PHP guest for the surface scenario using the same compiled module. The fresh guest reproduces the expected alias, public state, global replacement and byte growth. A new request starts in a fresh guest environment after each interruption. The [WSL origin receipt](evidence/wsl-bridge-origin-20261110T005400.560501Z.json) binds 84 inputs to the repository snapshot. The run took 2026-11-10 00:50:54–00:53:53 UTC (09:50:54–09:53:53 JST). Module compilation was 165,003 ms and scenario execution was 1,988 ms in the development profile, a single-run observation rather than performance acceptance.

Each recovery case also exercises an isolated Rust continuation. `finish_operation` reports `PendingEffect`. Abort leaves depth zero and state `Aborted`, and a late resume is rejected. This check covers that recovery continuation only. The shared peer's general error path drops its continuation without calling abort, so these fixtures do not establish general request cleanup.

The wrapper builds only `recastpress-php-compat-v06-wasm-bridge`, limits each captured stdout/stderr stream to 1 MiB, and owns process groups with build/host deadlines of 600/300 seconds. Guest pipe reads and joins are bounded. Raw diagnostics remain in run-specific private `target/` files; public receipts contain relative paths and digests. `--verify-only` requires the latest chronological receipt to have passed, matching source/binary hashes, successful and reaped build/host children, and both semantic results. It never falls back to an older success after a later failure. Self-tests reject mutated observations and cover same-second success/failure ordering.

## Metered trap and owned CPU-loop probe on Linux

```sh
python3 -B experiments/php-compat-v06-probe/wasm/trap_check.py --self-test
python3 -B experiments/php-compat-v06-probe/wasm/trap_check.py
python3 -B experiments/php-compat-v06-probe/wasm/trap_check.py --verify-only
```

This source-bound profile uses a one-file read-only map containing `trap_recovery.php` for the trap and CPU-loop guests. The normal and recovery surface guests use the bridge's six-file map. The metered host compiles the fixed PHP module once, runs a normal surface request, invokes a PHP callback that loops without yielding, then runs the same surface request in a fresh guest after the trap. Metering starts at 100,000,000 points and charges one point per original Wasm operator. The probe records the live metering globals before and after the run and requires the runner to capture a typed Wasmer `UnreachableCodeReached` runtime trap. WASIX reports that guest failure as process exit 45. An isolated Rust continuation must still be pending at the callback checkpoint, then abort to depth zero and reject a late resume.

The CPU-loop mode runs separately without metering. Its owned host emits a validated callback-loop checkpoint and stays live through a short grace interval. The supervisor confirms positive Linux process CPU ticks, sends SIGKILL to the owned host process group, and waits for it to be reaped. Only after that process exits does a new host process compile the fixed module and run a fresh surface request. The evidence covers disposal of this probe process and a fresh request in a new host. Product CPU-time, host-call budgets and worker reuse require separate acceptance. The default bridge's six-file map is unchanged.

The current [WSL receipt](evidence/trap-20261110T064648.976155Z-success.json) and [origin receipt](evidence/wsl-trap-origin-20261110T064654.367984Z.json) passed with 94 source-bound inputs matching the repository snapshot. The run took 2026-11-10 06:41:50–06:46:48 UTC (15:41–15:46 JST). Four owned processes were reaped. The metered host compiled the module in 139,096 ms, observed 99,999,963 points after PHP startup and then the exhausted `i32` global with flag 1. It captured `UnreachableCodeReached` and WASIX exit 45, joined both guest and stderr threads with empty stderr, and passed the normal surface scenarios before and after the trap on the same compiled module. The separate CPU-loop host accumulated 20 Linux CPU ticks over 205 ms, remained alive through the grace interval, then exited from SIGKILL 9 with return code -9 and was reaped. A new host process subsequently compiled the module and passed surface recovery. These are development-host observations.

The current [continuation regression receipt](evidence/bridge-20261110T065213.318053Z-success.json) passed surface, throwable, PHP exit 23, partial-frame exit 24 and pipe-close exit 26 cases, including all three fresh-guest surface recoveries. Its [origin receipt](evidence/wsl-bridge-origin-20261110T065217.409259Z.json) binds 93 inputs to the repository snapshot. Module compilation was 70,131 ms and scenario execution 955 ms.

## PHP handle registry bridge

```sh
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --handles --self-test
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --handles
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --handles --verify-only
```

This profile adds `handle_registry.php` and `handle_driver.php` to the default six-file readonly guest map. The registry rejects cloning and serialization to preserve its ownership state. Its independent PHP driver retains supported values in a request-local registry, deduplicates object identity through `WeakMap`, tracks retain counts, and uses bounded slot reuse with generation and an 8-byte `random_bytes` token. The token supports comparisons within the PoC registry. Transport authentication and production capability security require separate acceptance.

The scenario exercises root→child→nested→root→RequestFinished in five PHP and four Rust frames. It checks slot reuse and rejects forged request, kind, token, old generation, and handles issued by another registry for the same request. Release invalidates final-release metadata before PHP performs value destruction; close invalidates all entries before destruction. Destructor registry re-entry, destructor exceptions during release and close, explicit `gc_collect_cycles`, and external aliases/resources surviving close are included. The companion abort scenario checks the abort boundary.

The [Linux handle receipt](evidence/handles-20261110T064924.983449Z-success.json) passed both cases with all source inputs unchanged; the [93-input WSL origin receipt](evidence/wsl-handles-origin-20261110T064929.312693Z.json) matches the repository snapshot. Each case used five PHP and four Rust frames. `handles` ended `Finished` and `handles_abort` ended `Aborted`. Both guests completed with exit 0, both runner threads joined, and the owned build and host children were reaped. The read-only map had eight files. Module compilation took 77,847 ms and both fixtures ran in 280 ms in this development profile. Production dispatcher integration, GC scheduling, product permissions/capability security, and full WordPress runtime acceptance remain open.

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

The current [one-shot regression receipt](evidence/run-20261110T064142Z-success.json) passed the 15 surface assertions and 11 strict JSON assertions. Its [origin receipt](evidence/wsl-language-origin-20261110T064144.199131Z.json) binds 81 inputs to the current repository snapshot. Module compilation was 77,794 ms and fixture execution was 263 ms.

To repeat on Linux, prepare Rust 1.94.0 on `PATH`, the fixed artifact above, the C/C++ build dependencies used by the existing host profile, and the locked Cargo cache (or equivalent Cargo-vendored sources), then run `python3 -B experiments/php-compat-v06-probe/wasm/run.py` from the repository root. The runner remains offline and locked. Local WSL build caches are retained for subsequent probes.

LLVM execution remains unverified for these v0.6 fixtures. WordPress bootstrap, Rust Query and full v0.6 acceptance remain outside these probes.

## Rust public-state publish

The [Wasm publish receipt](evidence/publish-20261110T065049.704335Z-success.json) and its [WSL origin receipt](evidence/wsl-publish-origin-20261110T065053.821748Z.json) passed on the fixed PHP module. The origin binds 93 inputs, including the readonly fixture map, to the current repository snapshot. The guest returned successfully, reported completion, and its thread was joined. Both owned build and host children were reaped. Module compilation was 71,606 ms and fixture execution was 158 ms in this development profile.

Run these commands from the repository root to repeat or validate publication.

```sh
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --publish --self-test
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --publish
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --publish --verify-only
```

The publish guest uses a seven-file read-only map comprising the default six files and `publish_driver.php`.

The scenario exchanged 11 PHP frames and 10 Rust frames, using PHP-issued observations 1 through 10. Root publish updated count and array entry to 4 and 7. Child publish updated them to 5 and 11 before the nested callback. The root callback rebound the global before its child call. The nested callback set the array tail to 77, and the outer callback changed the unknown property to `php-outer`. Rust computed marker 104 from the latest heap values and PHP verified it. Same-object aliasing, property and array-entry references, copy-on-write preservation and nested state were observed. PHP rejected stale publish, replay and a full batch containing an invalid target before assignments. The verified write contract covers this closed fixture's inert object and integer assignments. Broader assignment semantics for userland magic methods and destructors remain unverified.

The Rust tracker covers PHP-issued observation metadata and publish ordering. The fixture exercises request-local registry validation. Production dispatcher integration, GC scheduling, automatic dirty tracking and WordPress Query integration remain future work.
