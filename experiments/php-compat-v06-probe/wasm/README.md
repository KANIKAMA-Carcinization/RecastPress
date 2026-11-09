# v0.6 PHP-WASIX surface probe

This independent workspace runs the fixed PHP surface oracle under the locally cached, fixed PHP 8.5.12 WASIX module. It does not execute WordPress, a database, the v0.6 Rust peer, or the interactive continuation bridge.

## Run

From the repository root:

```powershell
python experiments/php-compat-v06-probe/wasm/run.py
```

The entrypoint builds with `cargo build --offline --locked`, then starts the fixed host as a separate owned child through `scripts/owned_probe.py`. It reuses `target/php-wasix-wp-bootstrap-probe/cargo-target`, outside the source walk. The Rust host accepts no CLI arguments, hashes the Wasm, source files, and its own build inputs before running, mounts exactly four allowlisted PHP files as a read-only in-memory filesystem, disables inherited host environment/network/home, and invokes PHP CLI as `-n /fixture/<fixed-script>`. It checks the surface oracle's 15 literal assertions and the strict JSON oracle's independent assertions, then emits a JSON receipt to stdout. Diagnostics and phase markers go to stderr.

`run.py --self-test` checks phase classification with synthetic stderr containing cumulative phase markers. A generated failure receipt is evidence of an unsuccessful run, not a passing gate; the command returns nonzero. Each run writes a timestamped receipt rather than replacing earlier evidence. Receipt input hashes are recorded before and after execution. Raw child output stays under `target/` and the receipt exposes only relative locations and digests.

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

The fixed profile has not passed Windows x64 acceptance. The existing fixed host and this v0.6 profile were run offline and locked; compilation succeeded, but module compilation panicked in `cranelift-codegen-0.133.0/src/machinst/lower.rs:1107` (`index out of bounds: the len is 0 but the index is 0`) before PHP startup. PHP execution is recorded as `not_reached`. Failure receipt generation does not turn the failed run into a successful gate; the run exits nonzero. Timestamped receipts preserve each run separately; the earlier `evidence/windows-cranelift-failure.json` remains an unchanged historical observation.

Linux x86_64/LLVM execution is pending. The existing host's Linux LLVM CI result used an earlier fixed fixture and only establishes basic PHP execution; this v0.6 oracle has not yet run under Wasm. The live `bridge_driver.php` is interactive and is not part of this one-shot host run. No Wasm callback-to-Rust nested roundtrip is claimed.
