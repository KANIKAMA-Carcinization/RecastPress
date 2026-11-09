# PHP fixture contract

These fixtures measure PHP runtime behavior only. They do not load WordPress and do not implement or claim to port WordPress query, hook, or persistence logic. The fixed comparison source remains `.reference/wordpress-7.1.3` for later source-derived cases.

## One-shot surface fixture

Run from the repository root:

```powershell
Get-Content -Raw request.json | .reference/php-8.5.12/php.exe -n -d display_errors=0 experiments/php-compat-v06-probe/fixtures/dispatcher.php
.reference/php-8.5.12/php.exe -n -d display_errors=0 experiments/php-compat-v06-probe/fixtures/surface_oracle.php
```

`dispatcher.php` accepts exactly one UTF-8 JSON line on stdin and emits one JSON result line on stdout. Diagnostics go to stderr. Request schema (unknown fields rejected):

```json
{"v":1,"request":"fixture-1","seq":0,"kind":"probe","case":"surface"}
```

Success schema:

```json
{"v":1,"request":"fixture-1","seq":0,"kind":"probe_result","ok":true,"result":{"same_object_alias":true}}
```

The result includes explicit observations for object identity, reference-cell mutation, a global replacement, callback and nested callback ordering, type and expiry rejection for request-local handles, a catchable `Throwable`, parent dispatch with child override, and a non-UTF8 byte string represented as hex plus byte length. `surface_oracle.php` independently asserts literal expected PHP outcomes; it does not consume or repeat caller-supplied expected values.

## Interactive continuation bridge

Start the long-lived PHP side with:

```powershell
.reference/php-8.5.12/php.exe -n -d display_errors=0 experiments/php-compat-v06-probe/fixtures/bridge_driver.php
```

The Rust peer reads and writes newline-delimited JSON on the process stdin/stdout pipes. Each direction has its own sequence beginning at zero. `request` is fixed to `surface-probe-1` in this fixture. PHP stdout is reserved for protocol frames; PHP diagnostics belong on stderr. Each encoded line is limited to 1 MiB and the combined bytes in both directions to 32 MiB. Both peers limit depth to 64 and reject unknown frame fields, duplicate decoded keys, and mismatched request/sequence values. No frame field contains PHP source or is evaluated.

The expected exchange is:

```text
PHP -> Rust  {"v":1,"request":"surface-probe-1","seq":0,"kind":"start_root","payload":{"operation":"surface_probe"}}
Rust -> PHP  {"v":1,"request":"surface-probe-1","seq":0,"kind":"invoke_php","payload":{"effect_id":"e-root","callback":"probe_root_callback"}}
PHP -> Rust  {"v":1,"request":"surface-probe-1","seq":1,"kind":"call_core","parent_effect":"e-root","payload":{"operation":"observe_public","view":{"public_name":"changed-before-child","setting_flag":true,"alias_same_object":true,"global_replaced":true}}}
Rust -> PHP  {"v":1,"request":"surface-probe-1","seq":1,"kind":"invoke_php","payload":{"effect_id":"e-nested","callback":"nested_probe_callback"}}
PHP -> Rust  {"v":1,"request":"surface-probe-1","seq":2,"kind":"resume","parent_effect":"e-root","payload":{"effect_id":"e-nested","result":{"public_name":"changed-before-child:nested","alias_same_object":true}}}
Rust -> PHP  {"v":1,"request":"surface-probe-1","seq":2,"kind":"return","parent_effect":"e-root","payload":{"operation":"observe_public","observation":{"public_name_before":"changed-before-child","public_name_after":"changed-before-child:nested","name_growth_bytes":7,"setting_flag":true,"alias_same_object":true}}}
PHP -> Rust  {"v":1,"request":"surface-probe-1","seq":3,"kind":"resume","payload":{"effect_id":"e-root","result":{"child_return":{"operation":"observe_public","observation":{"public_name_before":"changed-before-child","public_name_after":"changed-before-child:nested","name_growth_bytes":7,"setting_flag":true,"alias_same_object":true}},"after_child":{"public_name":"changed-before-child:nested","setting_flag":true,"alias_same_object":true},"global_name":"global-replacement"}}}
Rust -> PHP  {"v":1,"request":"surface-probe-1","seq":3,"kind":"return","payload":{"operation":"surface_probe","observation":{"public_name_before":"changed-before-child","public_name_after":"changed-before-child:nested","name_growth_bytes":7,"setting_flag":true,"alias_same_object":true,"global_name":"global-replacement"}}}
PHP -> Rust  {"v":1,"request":"surface-probe-1","seq":4,"kind":"request_finished","payload":{"status":"ok","php_observation":{"same_object_alias":true,"public_name_after_resume":"changed-before-child:nested","global_replacement":"global-replacement","rust_child_observation":{"public_name_before":"changed-before-child","public_name_after":"changed-before-child:nested","name_growth_bytes":7,"setting_flag":true,"alias_same_object":true},"rust_root_observation":{"public_name_before":"changed-before-child","public_name_after":"changed-before-child:nested","name_growth_bytes":7,"setting_flag":true,"alias_same_object":true,"global_name":"global-replacement"}}}}
```

`effect_id` values are the Rust peer's local operation/effect mapping for this deterministic run; PHP checks allowlisted callback names and exact parent/effect correlation. The Rust peer derives byte growth and state observations from both live PHP snapshots. PHP independently computes those values from the retained heap and rejects a child/root Return that differs. A PHP callback's `CallCore` is sent before that callback resumes, and its returned state is re-read from the same PHP object.

## Limits

The one-shot fixture's PHP registry token binds request generation, slot, and value kind and checks stale/type-invalid tokens. It is an in-process demonstration, not a cryptographic cross-process authorization token or a production registry. The positive wire carries no PHP handle; undeclared handle fields are rejected. The throwable scenario carries only an allowlisted request-local string ID for an actual PHP Throwable retained in that PHP process; it does not serialize a pointer or authority token. JSON is only the control envelope; raw bytes are carried in tagged hex fields in the one-shot probe. No Wasm binary is executed by these commands.

## Throwable and abort scenario

The runner's `--self-test` and `--record` also start a separate native PHP/Rust pair with the `throwable` scenario. A registered nested PHP callback throws one `RuntimeException`; the PHP transport wrapper catches and retains that exact object in a request-local table, and sends only its allowlisted string ID, class, and message. Rust maps this ID to a typed `PhpHandleKind::Throwable`, resumes the nested effect, and returns a throwable completion to PHP. The PHP API wrapper rethrows the table's same object; the top-level catch compares object identity and checks the catch/finally trace and that the callback ran once.

Rust recovers the child before aborting the still-pending root. It then verifies that a second nested resume returns `EffectAlreadyResumed`, a root resume after abort returns `RequestClosed`, and `finish_request` after abort returns `RequestClosed`; the request depth falls to zero. PHP receives the explicit `Abort` frame, confirms the throwable's class/message/trace and the cleanup observations, and sends `RequestFinished` with `status=throwable`. This tests caller-owned routing using a deterministic fixture handle mapping. It does not pass an actual PHP heap pointer or prove Wasm exception propagation.

## Same protocol under PHP-Wasm

The [Linux bridge profile](../wasm/README.md) now executes both exchanges using the same shared Rust peer and the fixed `bridge_driver.php` under the pinned PHP-WASIX module. The native commands above still execute native PHP. The separate Wasm receipt establishes the fixed nested callback and same-object throwable cases; it does not establish WordPress query semantics, a production handle registry, intentional exit/trap recovery, or reusable worker isolation.
