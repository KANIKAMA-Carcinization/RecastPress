# v0.6 PHP-WASIX surface and continuation probes

This independent workspace runs fixed language oracles and an interactive continuation bridge under the locally cached, fixed PHP 8.5.12 WASIX module. The bridge uses the same Rust peer as the native PHP probe. Neither profile executes WordPress or a database.

## Run

From the repository root, run the fixed Windows/Cranelift profile:

```powershell
python experiments/php-compat-v06-probe/wasm/run.py
```

The Cranelift profile explicitly disables optimization (`CraneliftOptLevel::None`) as a bounded compatibility experiment. `python .../run.py --llvm` builds with the existing locked LLVM feature, if the local toolchain is available; it does not fetch dependencies.

The entrypoint builds the explicit one-shot binary with `cargo build --offline --locked --bin recastpress-php-compat-v06-wasm-probe`, then starts the fixed host as a separate owned child through `scripts/owned_probe.py`. Windows reuses `target/php-wasix-wp-bootstrap-probe/cargo-target`; Linux defaults to `target/php-compat-v06-wasm-linux`. `RECASTPRESS_V06_TARGET_DIR` or `CARGO_TARGET_DIR` can select another target path only within repository `target/`. The Rust host accepts no CLI arguments, hashes the Wasm, source files, and its own build inputs before running, mounts exactly five allowlisted PHP files as a read-only in-memory filesystem, disables inherited host environment/network/home, and invokes PHP CLI as `-n /fixture/<fixed-script>`. It checks the surface oracle's 15 literal assertions and the strict JSON oracle's independent assertions, then emits a JSON receipt to stdout. Diagnostics and phase markers go to stderr.

`run.py --self-test` checks phase classification with synthetic stderr containing cumulative phase markers. A generated failure receipt is evidence of an unsuccessful run, not a passing gate; the command returns nonzero. Each run writes a timestamped receipt rather than replacing earlier evidence. Receipt input hashes, including `scripts/owned_probe.py`, and the built host binary are checked before and after execution; any change invalidates a nominal success. The receipt includes the host's actual oracle results. Build and host execution are separate owned child processes with bounded timeouts and reaping. Raw child output stays under `target/` and the receipt exposes only relative locations and digests.

## Registry-integrated profile snapshot status

The fixture maps are one-shot: five files; default continuation bridge: seven files; publish and handles: eight files; hooks: eight files; trap-only guest: one file. The native one-shot and interactive surface/throwable fixtures use the shared `HandleRegistry`. The current hooks profile runs normal, caught-Throwable and root-abort guests on one compiled module. Current-source measurements for all six profiles are listed at the end; earlier measurements remain historical.

The historical [registry-integrated one-shot receipt](evidence/run-20261112T154442Z-success.json) passed the 15-check surface oracle and 11-check strict JSON oracle. Its [WSL language origin receipt](evidence/wsl-language-origin-20261112T154444.848614Z.json) confirms the 82-input snapshot matched the repository. It was observed at 2026-11-12 15:44 UTC (2026-11-13 00:44 JST); module compilation took 103,063 ms and fixture execution 328 ms.

The historical [registry-integrated continuation receipt](evidence/bridge-20261112T154638.831791Z-success.json) passed surface, throwable, PHP exit 23, partial-frame exit 24 and pipe-close exit 26 scenarios, with a fresh successful surface guest after each interruption. Its [WSL bridge origin receipt](evidence/wsl-bridge-origin-20261112T154644.102176Z.json) confirms the 93-input snapshot matched the repository. Source, captured results and origin were independently checked. It was observed at 2026-11-12 15:46 UTC (2026-11-13 00:46 JST); module compilation took 96,012 ms and scenarios took 1,043 ms. These are development-host observations, not performance acceptance results.

The bridge adds a local `recastpress-wp` dependency with default authentication features disabled and a direct dependency on the already locked serde version. All 427 registry package identities remain unchanged. The five-file one-shot map exposes `surface_oracle.php`, `strict_json_oracle.php`, `dispatcher.php`, `strict_json.php`, and the shared `handle_registry.php` required by the integrated dispatcher. Handle and hooks profiles use expanded maps only when explicitly selected. It does not expose the repository root, `.reference`, host environment, network, or database credentials. Earlier host profiles and their evidence are preserved.

## Interactive continuation bridge on Linux

```sh
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --self-test
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --verify-only
```

The fixed [bridge host](src/bin/bridge.rs) compiles the pinned PHP module once and starts fresh guest environments for the surface, throwable and recovery scenarios. It mounts the five one-shot files plus `bridge_driver.php` and `recovery.php` read-only (seven files total). A guest runner thread and the shared [Rust peer](../../../crates/recastpress-wp/examples/v06_bridge_peer/mod.rs) exchange JSONL through WASIX pipes. PHP can suspend a callback in `fgets`, call a child Rust operation, handle a nested PHP callback, and continue using the same object. This follows the continuation protocol without a host call recursively re-entering an active Wasm invocation.

An earlier historical [Linux bridge receipt](evidence/bridge-20261109T233155.968907Z-success.json) passed both scenarios. Surface exchanged five PHP frames and four Rust frames, preserving the public object alias, nested name change, flag and global replacement. Throwable exchanged four frames each way, rethrew the same PHP `RuntimeException`, preserved the seven-step catch/finally trace and one callback invocation, recovered the child, and rejected duplicate resume and use after abort. Both guest environments returned successfully and their runner threads were joined. The [snapshot origin](evidence/wsl-bridge-origin-20261109T233202.341526Z.json) and before/after source and binary hashes bind this run to 83 input files in the repository. Observed module compilation was 115,162 ms and scenario execution together was 379 ms in the development profile; this is not a production performance acceptance result.

An earlier historical [final Linux recovery receipt](evidence/bridge-20261110T005353.483934Z-success.json) exercised three interruptions while the root PHP effect is pending. PHP exits 23 after the host starts the callback, writes a 95-byte incomplete `call_core` frame and exits 24, or waits until the host closes the stdin pipe and exits 26. For each case, the runner confirms the guest exit and joins the guest and stderr threads. All six fault guest/stderr threads joined with empty stderr. It then starts a fresh PHP guest for the surface scenario using the same compiled module. The fresh guest reproduces the expected alias, public state, global replacement and byte growth. A new request starts in a fresh guest environment after each interruption. The [WSL origin receipt](evidence/wsl-bridge-origin-20261110T005400.560501Z.json) binds 84 inputs to the repository snapshot. The run took 2026-11-10 00:50:54–00:53:53 UTC (09:50:54–09:53:53 JST). Module compilation was 165,003 ms and scenario execution was 1,988 ms in the development profile, a single-run observation rather than performance acceptance.

Each recovery case also exercises an isolated Rust continuation. `finish_operation` reports `PendingEffect`. Abort leaves depth zero and state `Aborted`, and a late resume is rejected. This check covers that recovery continuation only. The shared peer's general error path drops its continuation without calling abort, so these fixtures do not establish general request cleanup.

The wrapper builds only `recastpress-php-compat-v06-wasm-bridge`, limits each captured stdout/stderr stream to 1 MiB, and owns process groups with build/host deadlines of 600/300 seconds. Guest pipe reads and joins are bounded. Raw diagnostics remain in run-specific private `target/` files; public receipts contain relative paths and digests. `--verify-only` requires the latest chronological receipt to have passed, matching source/binary hashes, successful and reaped build/host children, and both semantic results. It never falls back to an older success after a later failure. Self-tests reject mutated observations and cover same-second success/failure ordering.

## Metered trap and owned CPU-loop probe on Linux

```sh
python3 -B experiments/php-compat-v06-probe/wasm/trap_check.py --self-test
python3 -B experiments/php-compat-v06-probe/wasm/trap_check.py
python3 -B experiments/php-compat-v06-probe/wasm/trap_check.py --verify-only
```

This source-bound profile uses a one-file read-only map containing `trap_recovery.php` for the trap and CPU-loop guests. The normal and recovery surface guests use the bridge's seven-file map. The metered host compiles the fixed PHP module once, runs a normal surface request, invokes a PHP callback that loops without yielding, then runs the same surface request in a fresh guest after the trap. Metering starts at 100,000,000 points and charges one point per original Wasm operator. The probe records the live metering globals before and after the run and requires the runner to capture a typed Wasmer `UnreachableCodeReached` runtime trap. WASIX reports that guest failure as process exit 45. An isolated Rust continuation must still be pending at the callback checkpoint, then abort to depth zero and reject a late resume.

The CPU-loop mode runs separately without metering. Its owned host emits a validated callback-loop checkpoint and stays live through a short grace interval. The supervisor confirms positive Linux process CPU ticks, sends SIGKILL to the owned host process group, and waits for it to be reaped. Only after that process exits does a new host process compile the fixed module and run a fresh surface request. The evidence covers disposal of this probe process and a fresh request in a new host. Product CPU-time, host-call budgets and worker reuse require separate acceptance. The default bridge's seven-file map is unchanged.

The historical [Linux trap receipt](evidence/trap-20261112T155655.201656Z-success.json) and [94-input WSL origin receipt](evidence/wsl-trap-origin-20261112T155702.703613Z.json) passed with the source snapshot matched; source, binary, private captures and origin were independently checked. The same compiled module passed normal→trap→fresh normal, and a fresh recovery host also passed. After PHP startup the metered guest had 99,999,963 points, then exhausted the `i32` global with flag 1, captured typed `UnreachableCodeReached`, and exited WASIX 45. The CPU-loop host recorded 21 ticks (96,926→96,947) over 202 ms, stayed live through grace, then was killed with SIGKILL (return -9); all four owned processes were reaped. The run took 2026-11-12 15:50:33–15:56:55 UTC (2026-11-13 00:50–00:56 JST); module compilation took 187,791 ms. These are development-host observations, not a product CPU-time limit. The historical run took 2026-11-10 06:41:50–06:46:48 UTC (15:41–15:46 JST). Four owned processes were reaped. The metered host compiled the module in 139,096 ms, observed 99,999,963 points after PHP startup and then the exhausted `i32` global with flag 1. It captured `UnreachableCodeReached` and WASIX exit 45, joined both guest and stderr threads with empty stderr, and passed the normal surface scenarios before and after the trap on the same compiled module. The separate CPU-loop host accumulated 20 Linux CPU ticks over 205 ms, remained alive through the grace interval, then exited from SIGKILL 9 with return code -9 and was reaped. A new host process subsequently compiled the module and passed surface recovery. These are development-host observations.

An earlier historical [continuation regression receipt](evidence/bridge-20261110T065213.318053Z-success.json) passed surface, throwable, PHP exit 23, partial-frame exit 24 and pipe-close exit 26 cases, including all three fresh-guest surface recoveries. Its [origin receipt](evidence/wsl-bridge-origin-20261110T065217.409259Z.json) binds 93 inputs to the repository snapshot. Module compilation was 70,131 ms and scenario execution 955 ms.

## PHP handle registry bridge

```sh
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --handles --self-test
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --handles
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --handles --verify-only
```

This eight-file profile uses the default seven-file map plus `handle_driver.php`; the shared `handle_registry.php` is already in the one-shot map. The registry rejects cloning and serialization to preserve its ownership state. Its independent PHP driver retains supported values in a request-local registry, deduplicates object identity through `WeakMap`, tracks retain counts, and uses bounded slot reuse with generation and an 8-byte `random_bytes` token. The token supports comparisons within the PoC registry. Transport authentication and production capability security require separate acceptance.

The scenario exercises root→child→nested→root→RequestFinished in five PHP and four Rust frames. It checks slot reuse and rejects forged request, kind, token, old generation, and handles issued by another registry for the same request. Release invalidates final-release metadata before PHP performs value destruction; close invalidates all entries before destruction. Destructor registry re-entry, destructor exceptions during release and close, explicit `gc_collect_cycles`, and external aliases/resources surviving close are included. The companion abort scenario checks the abort boundary.

The historical [Linux handles receipt](evidence/handles-20261112T155019.918373Z-success.json) and [93-input WSL origin receipt](evidence/wsl-handles-origin-20261112T155025.515200Z.json) passed with source snapshot matching; source, binary, private captures and origin were checked. Each case used five PHP and four Rust frames: `handles` finished, `handles_abort` aborted, and both reported zero live handles after close. Observed at 2026-11-12 15:50 UTC (2026-11-13 00:50 JST), module compilation took 97,440 ms and both scenarios 323 ms. The prior [historical handles receipt](evidence/handles-20261110T064924.983449Z-success.json) remains available. Production dispatcher integration, GC scheduling, product permissions/capability security, and full WordPress runtime acceptance remain open.

## Resumable hooks profile

```sh
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --hooks --self-test
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --hooks
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --hooks --verify-only
```

The hooks profile mounts the default seven-file bridge map plus hooks_driver.php (eight files total). It resolves and invokes real PHP Closures through HandleRegistry; the fixture keeps the external Closure aliases alive through RequestFinished. The earlier native and PHP-Wasm hooks observations cover the normal seven-PHP/six-Rust-frame path only. The [PHP-Wasm receipt](evidence/hooks-20261112T163831.989293Z-success.json) and its [98-input WSL origin](evidence/wsl-hooks-origin-20261112T163836.899479Z.json) are historical for the pre-Throwable hooks source; compilation took 118,740 ms and execution 174 ms on that development host. They do not verify the current Throwable scenarios.

The current native --self-test passed all eight scenarios, including hooks_catch and hooks_abort; an independent private-capture check confirmed frame sequence, parent correlation, typed completion/Throwable identity and RequestFinished statistics. In catch mode the same real child RuntimeException is caught by the root Closure and normal rootB/late/native dispatch continues (7 PHP/6 Rust frames; final bytes seed:r5:php-root-caught:php-b:late:r20). In abort mode the same Throwable propagates, retires the root ticket and aborts the Continuation; rootB/late/r20 do not execute (5 PHP/4 Rust frames). Both cases retain the expected parent-registration mutation observations. The public hook stack and retained WP iteration priorities are observed separately from Continuation state: after child throw the current hook is child, depth 2, child priority 5; catch finishes at root/depth 1 with child priority 5 and root priority null; abort remains child/depth 2 with child priority 5 and root priority 10. Rust reports Continuation depth 0 and Finished/Aborted separately.

The [Wasm v2 receipt](evidence/hooks-20261112T173452.858642Z-success.json) passed all three modes, with [100 bound origin inputs](evidence/wsl-hooks-origin-20261112T173458.519571Z.json). Module compilation took 99,073 ms and all guest executions 544 ms on this development host. The native eight-bridge record binds 178 inputs and 30 reaped children. The separate [pinned WordPress oracle receipt](../evidence/wp-hooks-throwable-v1.json) binds 184 inputs and compares all three native/Wasm outcomes with actual plugin.php/WP_Hook: final bytes, object name, registration mutation, current hook, stack depth and retained priorities match. Continuation terminal state is a Rust observation after the actual PHP terminal frame, rather than a PHP report of a future state. The fixture does not establish production removal final-reference release, complete WordPress callable identity, product hooks or Query acceptance. Production dispatcher integration remains a later milestone.
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

An earlier historical [one-shot regression receipt](evidence/run-20261110T064142Z-success.json) passed the 15 surface assertions and 11 strict JSON assertions. Its [origin receipt](evidence/wsl-language-origin-20261110T064144.199131Z.json) binds 81 inputs to the current repository snapshot. Module compilation was 77,794 ms and fixture execution was 263 ms.

To repeat on Linux, prepare Rust 1.94.0 on `PATH`, the fixed artifact above, the C/C++ build dependencies used by the existing host profile, and the locked Cargo cache (or equivalent Cargo-vendored sources), then run `python3 -B experiments/php-compat-v06-probe/wasm/run.py` from the repository root. The runner remains offline and locked. Local WSL build caches are retained for subsequent probes.

LLVM execution remains unverified for these v0.6 fixtures. WordPress bootstrap, Rust Query and full v0.6 acceptance remain outside these probes.

## Rust public-state publish

The historical [Wasm publish receipt](evidence/publish-20261112T154827.798767Z-success.json) and [93-input WSL origin receipt](evidence/wsl-publish-origin-20261112T154832.729463Z.json) passed with source snapshot matching; source, binary, private captures and origin were checked. Observed at 2026-11-12 15:48 UTC (2026-11-13 00:48 JST), module compilation took 92,060 ms and execution 158 ms. It exchanged 11 PHP and 10 Rust frames, used observations 1–10, and preserved final marker 104. The prior [historical publish receipt](evidence/publish-20261110T065049.704335Z-success.json) remains available.

Run these commands from the repository root to repeat or validate publication.

```sh
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --publish --self-test
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --publish
python3 -B experiments/php-compat-v06-probe/wasm/bridge_check.py --publish --verify-only
```

The publish guest uses an eight-file read-only map comprising the default seven files and `publish_driver.php`.

The scenario exchanged 11 PHP frames and 10 Rust frames, using PHP-issued observations 1 through 10. Root publish updated count and array entry to 4 and 7. Child publish updated them to 5 and 11 before the nested callback. The root callback rebound the global before its child call. The nested callback set the array tail to 77, and the outer callback changed the unknown property to `php-outer`. Rust computed marker 104 from the latest heap values and PHP verified it. Same-object aliasing, property and array-entry references, copy-on-write preservation and nested state were observed. PHP rejected stale publish, replay and a full batch containing an invalid target before assignments. The verified write contract covers this closed fixture's inert object and integer assignments. Broader assignment semantics for userland magic methods and destructors remain unverified.

The Rust tracker covers PHP-issued observation metadata and publish ordering. The fixture exercises request-local registry validation. Production dispatcher integration, GC scheduling, automatic dirty tracking and WordPress Query integration remain future work.

## Current-source regression receipts

All six profiles below ran against the frozen typed-Throwable hooks source. The origin records bind repository, Linux snapshot and post-run identities; independent local verification checked host binaries and bounded stdout/stderr captures. Earlier receipts remain historical.

| Profile | Receipt | Origin inputs |
| --- | --- | --- |
| run | [passed](evidence/run-20261112T173818Z-success.json) | [82 inputs](evidence/wsl-language-origin-20261112T173820.135428Z.json) |
| bridge | [passed](evidence/bridge-20261112T174005.465552Z-success.json) | [100 inputs](evidence/wsl-bridge-origin-20261112T174010.986504Z.json) |
| publish | [passed](evidence/publish-20261112T174203.243538Z-success.json) | [100 inputs](evidence/wsl-publish-origin-20261112T174208.877821Z.json) |
| handles | [passed](evidence/handles-20261112T174355.050422Z-success.json) | [100 inputs](evidence/wsl-handles-origin-20261112T174400.301060Z.json) |
| hooks | [passed](evidence/hooks-20261112T173452.858642Z-success.json) | [100 inputs](evidence/wsl-hooks-origin-20261112T173458.519571Z.json) |
| trap | [passed](evidence/trap-20261112T175008.246021Z-success.json) | [101 inputs](evidence/wsl-trap-origin-20261112T175014.840259Z.json) |

The hooks profile covers normal/catch/abort with 7/6, 7/6 and 5/4 PHP/Rust frames. The default bridge still covers surface/throwable and exit/partial-frame/pipe-close recovery; publish, handle lifetime, metered trap and owned CPU-loop recovery passed again. Build/host processes were reaped. These measurements do not establish product CPU budgets or worker reuse.
