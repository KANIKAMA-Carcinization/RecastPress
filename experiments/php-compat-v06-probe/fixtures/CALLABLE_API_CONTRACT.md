# callable_api wire contract v1

This is the fixed development profile for PHP standard hook wrappers backed by Rust. Existing scenarios are unchanged. The request generation is `61`, envelope version 1, request `surface-probe-1`. Both directions have independent contiguous seq numbers, strict decoded-key uniqueness, MAX_FRAME 1 MiB / MAX_TOTAL 32 MiB / depth 64 / nodes 65,536.

## Values, names, keys and handles

- All hex is lowercase/even length. PHP owns callable values, object identity, reference cells and Throwable objects. Descriptors carry `{request,kind,slot,generation,token}` with request `61`, kind `callable` or `throwable`, u32 slot, positive u32 generation, 16 lowercase hex token. They are request-local correlation metadata, not transport authority.
- Callable key is null for an ID the pinned `_wp_filter_build_unique_id` cannot build; otherwise exactly `{type:"int"|"string",hex:<bytes>}`. For int, hex encodes canonical signed-64 decimal ASCII; for string it encodes the actual PHP array key bytes and must not normalize to an integer key. PHP computes array-key coercion using PHP itself. All PHP callback kinds share the Rust CallbackId module `wp-hook-id`; callback component is `i:<decimal>` or `s:<hex>`. No case folding, eager is_callable check, callback-kind namespace or raw UTF-8 requirement for IDs.
- `hook_hex` encodes a string hook name. Rust may use injective internal names `b:<hex>` except literal raw `all` maps to the engine's reserved `all` binding. Decode these internal names for current_filter and callback metadata. An all listener receives the original raw hook name in argument 0, not the internal name.
- Filter arguments/results use canonical POD nodes: null `{type:"null"}`; bool `{type:"bool",value:bool}`; int `{type:"int",value:<canonical signed64 decimal>}`; float `{type:"float",bits:<16 lowercase big-endian IEEE hex>}`; string `{type:"string",hex:<bytes>}`; array `{type:"array",entries:[{key:<int|string POD node>,value:<POD>}...]}`. Enforce exact key sets, canonical numbers/hex and normalized unique PHP array keys. No caller-supplied expected results. This profile transports the existing PhpValue materialized domain; object/reference/resource-valued filter arguments are a remaining heap-value integration requirement and must fail explicitly rather than becoming null. The retained callable value itself may be a string, array, Closure, invokable or non-invokable object.
- Completion is exactly `{kind:"value",value:<context value>}` or `{kind:"php_throwable",handle:<Throwable descriptor>,class_hex:<PHP class bytes>,message_hex:<PHP message bytes>}`. Class/message bytes can be non-UTF8. PHP rethrows the registry-resolved original object; Rust never reconstructs it from class/message.

## Envelope and effect ancestry

Every envelope has exactly v/request/seq/kind/payload, plus parent_effect only when specified below. Root effects have no parent_effect. For each API CallCore under active PHP effect E, the API child operation returns to E. Its callable/release InvokePhp frames and their Resume frames use parent_effect=E. A destructor/callback CallCore inside one of those effects uses that new effect's ID as parent_effect. Rust receives no new root operation for sequential APIs.

Effect IDs after the root script may be generated uniquely by Rust; PHP validates and echoes them, not hardcodes their numbers. Never hold a Rust dispatcher borrow/lock while waiting on PHP.

## Root script and terminal exchange

1. PHP start_root (no parent): `{operation:"callable_api",request_generation:"61"}`.
2. Rust invoke_php (no parent): `{operation:"run_script",effect_id:"e-script"}`. Continuation root operation remains alive and script effect remains pending while the program calls APIs.
3. PHP performs zero or more nested API calls/effects. Script ends with resume (no parent): `{effect_id:"e-script",completion:{kind:"value",value:<program observation object>}}`. This fixed driver implements successful script completion only. Tested callback/invocation Throwables are caught by the program. An uncaught script Throwable currently fails through fail_bridge and owned-child cleanup, without a typed script Resume or observed Continuation abort.
4. For successful script completion, Rust releases remaining callback registrations through resumable control operations/effects while request state is still active. Mutations/destructors may reenter under their release effect. Repeat bounded cleanup if a destructor registers another callback; do not silently discard obligations.
5. Rust invokes PHP `{operation:"close_registry",effect_id:<id>}` with no parent. PHP closes the registry, checks old descriptors reject reads, and resumes no parent with `{effect_id:<id>,completion:{kind:"value",value:{closed:true,live_after_close:0,closed_read_rejected:true}}}`. No future application calls follow this terminal boundary.
6. Rust finishes root operation and sends return (no parent): `{operation:"callable_api",status:"ok"}`.
7. PHP sends request_finished (no parent): `{status:"ok",php_observation:<same program observation>,registry_observation:<same close observation>,transport:{frames_sent,frames_received,bytes_total}}`, then EOF. Transport stats are measured before sending this last frame.
8. Rust validates final observations/EOF, calls finish_request, proves late resume/CallCore reject, then emits summary. An uncaught root-script Throwable must never be reported as a successful finish. Its typed Throwable Resume/abort path remains a future requirement; the present driver fails and its owner reaps the children instead. Program cases catch their intentionally tested invocation errors, so the primary callable_api run terminates normally.

## API CallCore and Return

Every CallCore has parent_effect=current PHP effect, and the exact payload below. Its Return uses the same parent_effect and exact `{api:<same api>,completion:<Value or Throwable completion>}`. A Value completion contains one canonical POD node.

| API | Exact CallCore payload fields | Value result |
| --- | --- | --- |
| add_filter | api,hook_hex,key,priority,accepted_args,handle | bool true |
| remove_filter | api,hook_hex,key,priority | bool existed |
| has_filter | api,hook_hex,key,any,priority | bool or int priority |
| apply_filters | api,hook_hex,value,args | filtered POD value |
| current_filter | api | false or raw hook string |
| doing_filter | api,hook_hex | bool |
| did_filter | api,hook_hex | int count |

- api is the literal table name. `priority` in add/remove is signed64 int (fixture scope); `accepted_args` is PHP `(int)` conversion made only after a valid callable ID, matching WP ordering. Public PHP signatures/defaults mirror pinned source, without extra parameter/return types. add priority null is normalized to 0 as WP does. Other priority domains are not accepted silently as an integer.
- add_filter key null requires handle null and accepted_args null (no integer conversion), and does not retain/install a callback; outer API still returns true. With a key, handle is a freshly retained Callable slot. Rust registration owns exactly one retain obligation shared by callback/snapshot leases. Identity and descriptor are separate.
- remove_filter key null returns false. The callback need not be callable; only its pinned WP ID/key and priority determine removal.
- has_filter any=true iff PHP callback argument is exactly false (including default). Then key must be null and result is any callback registered. Otherwise null/falsey ID returns false. priority is int only if original argument is_int; all other original values (including default false, null or numeric string) become wire null, meaning search priorities and return the first match. A match at priority 0 is int zero, not false.
- apply_filters value is POD, args is a list of extra POD arguments (without the filter value). current_filter returns POD string or false. doing_filter hook_hex=null means any hook is running; string names, including empty string, remain distinct from null. did_filter returns canonical int count.

## Callable and release effects

A callable InvokePhp inside API child operation has its caller effect as parent_effect and exact payload `{operation:"callable",effect_id,handle,hook_hex,priority,call_mode,args}`. Args is the actual core-sliced POD argument list. call_mode is `zero`, `array` or `all`: WP uses call_user_func only for accepted_args===0 in a named filter, otherwise call_user_func_array; all listeners always receive their unsliced args via call_user_func_array. PHP resolves the Callable slot and executes the real callable. Drop adapter invocation temporaries once the PHP call returns/throws, before waiting for a Rust-directed final release. Resume has the same parent and exact `{effect_id,completion}`.

A release InvokePhp has caller effect as parent and exact `{operation:"release_callable",effect_id,handle}`. One handle per effect: PHP calls registry.release and can reenter through a destructor under this release effect. Resume is `{effect_id,completion:{kind:"value",value:{released:true}}}` or the common Throwable completion if destructor threw. With fresh Callable slot per registration, final release is true. Rust marks the old tuple release-pending/non-invokable before the call, permits the destructor to register a new generation of the same slot, and unregisters only the confirmed old exact tuple after completion. Do not reject valid generation reuse while release is pending.

Release timing is determined by pinned WP mutation/unwind phases. remove must yield after callback unset but before empty-priority cleanup/resort/global binding cleanup if that unset performs final release. Throwable releases abandoned invocation locals/snapshot ownership before forwarding the exception while preserving WP iteration/public stack state. The safe normal-iteration lifetime boundary also needs an actual WP oracle; do not assume bucket drain alone or after Dispatcher Complete reproduces PHP foreach locals.

## Peer result and evidence

The peer emits `V06_CALLABLES_RESULT <JSON>` and `V06_CALLABLES_OK continuation_peer=true` and returns that observed JSON to the Wasm host. Exact summary fields:

`{scenario,continuation_peer,frames_from_php,frames_from_rust,api_counts,callback_invocations,release_effects,late_resume_rejected,late_call_rejected,continuation_depth_after,state_after,php_observation,registry_observation}`

scenario is callable_api, continuation_peer true, api_counts has exactly the seven API names above with nonnegative integer counts, callback_invocations/release_effects are actual counts, final Continuation depth0/state Finished, both late rejection flags true, PHP program observation comes from the actual script completion and matches RequestFinished, registry_observation is the actual close observation. Keep PHP heap/program observations separate from Rust internal future state. Native and Wasm run the same shared PHP program; a separate pinned WP oracle runs that program with original plugin.php/WP_Hook and the results are compared independently.

## PHP entrypoints and measured fixture coverage


The isolated fixture defines the same untyped global entrypoints and defaults as the pinned WordPress source:

- _wp_filter_build_unique_id($hook_name, $callback, $priority): ?string
- add_filter($hook_name, $callback, $priority = 10, $accepted_args = 1)
- remove_filter($hook_name, $callback, $priority = 10)
- has_filter($hook_name, $callback = false, $priority = false)
- apply_filters($hook_name, $value, ...$args)
- current_filter()
- doing_filter($hook_name = null)
- did_filter($hook_name)

The wrapper keeps PHP callback values and identity calculation in PHP. add_filter does not call is_callable. A callback identity that cannot be built is not retained or installed, while add_filter still returns true. A buildable but invalid callback is retained and fails only if dispatched. accepted_args is cast to int only for a buildable identity. A null priority maps to zero in this profile; other noninteger priority domains are rejected explicitly.


The shared program is run against both the isolated adapter and original pinned plugin.php / WP_Hook. It observes function strings, static string/array identity, object-method removal through a fresh pair, invokable objects, case-sensitive names, same-ID replacement order, accepted-argument casting, the zero-argument call mode, all-listener unsliced arguments and raw hook names, PHP array-key coercion, falsey ID lookup, and delayed invalid-callable errors.

Lifetime observations include a self-removed invokable object whose external alias is dropped before a thrown callback unwinds, and an external remove by raw string matching an object callback identity. In the latter case the destructor observes the old callback already absent while the empty priority bucket still exists, reenters current_filter/has_filter/add_filter, installs a replacement, and returns before remove_filter. These tests keep public hook iteration state separate from callback-value ownership. They do not claim production WP dispatcher, heap reference, or worker-reuse acceptance.

The independent pinned oracle output is stored outside the repository at target/v06-callable-wp-expected.json. Native and Wasm peer summaries are checked against the actual shared-program result and this fixed-source oracle.

The fixed primary run measures 20 observation cases, 91 API calls, 17 callback invocations and 27 release effects: PHP frames = API calls + callback invocations + release effects + 4 = 139; Rust frames use the same sum + 3 = 138. The independent raw checker correlates registrations, active hook, priority, call mode, argument slicing and API result types, and rejects 21 mutations of the actual native transcript. Integer payloads have signed64 wire bounds; PHP_INT_MIN/MAX round-trips do not by themselves establish PHP_INT_SIZE.
