# Throwable completions in resumable hooks

Version 1: fixed development-fixture contract. The public hook-stack assertions below follow the pinned WordPress 7.1.3 oracle. This is a development fixture contract, not production WordPress callback acceptance.

## Shared request and throwable identity

The existing `hooks` mode and its successful wire exchange remain unchanged. The new scenario names are `hooks_catch` and `hooks_abort`; each is the `start_root.payload.operation`. The envelope remains version 1, request `surface-probe-1`, with contiguous sequence numbers. The start payload keeps generation `51`, the existing `handles` and `ids` maps, and `value_hex="73656564"` (`seed`).

The PHP child Closure mutates the retained item name with `:child`, then throws one actual `RuntimeException` with fixed message `hooks child fixture throwable`. The PHP transport catch retains that same object in `HandleRegistry` and sends this closed completion shape:

```json
{"kind":"php_throwable","handle":{"request":"51","kind":"throwable","slot":0,"generation":1,"token":"<registry-issued token>"},"class":"RuntimeException","message":"hooks child fixture throwable"}
```

The descriptor's integer slot/generation and opaque token are the values issued by the live registry, not constants. Validate the exact key set and field types; JSON object key order is irrelevant. The same descriptor, class, and message are carried unchanged from child `resume` through child `return`, then through root `resume` and root `abort` in abort mode. PHP resolves the descriptor as kind `throwable`, verifies `===` with its external Throwable alias, and rethrows that exact object. The alias keeps the object alive after registry close. Tokens are request-local PoC metadata, not transport authority.

The child PHP-to-Rust `resume` has `parent_effect="e-root-a"` and exact payload keys `{effect_id, completion}`, with `effect_id="e-child"`. Rust validates `DispatchStep::PhpThrowable`, consumes only the child dispatch invocation without replaying it, and returns the typed completion to the suspended root PHP effect. The child Rust-to-PHP `return` has `parent_effect="e-root-a"` and exact payload keys `{operation, completion, current_hook, hook_depth, child_ticket_retired, parent_ticket_preserved}`; `operation="child_hook"`, `current_hook="child"`, `hook_depth=2`, `child_ticket_retired=true`, `parent_ticket_preserved=true`. The peer proves the child ticket cannot be advanced again (`InvalidDispatchTicket`) while root `advance` remains `PhpCallbackPending`; the child callback is not replayed. These public hook values intentionally retain WordPress's observable stack after an exception; internal ticket/continuation cleanup is tracked separately. The dispatcher retires the transport ticket but preserves the `WP_Hook` storage/iteration frame: `current_priority("child")` remains 5.

## `hooks_catch`

PHP root A resolves and rethrows the descriptor, catches the identical Throwable inside the root Closure, records one catch, then returns the deterministic recovery value `seed:r5:php-root-caught` with the live view `{name:"root-before-child:child:root-after-child", alias_same:true}`. Root B, late, and native r20 then run normally. The root `return` has the existing closed payload keys `{operation, value_hex, root_b_removed, late_registered, hook_depth, current_hook}` with operation `hooks_catch`, the final bytes below, both mutation flags true, public `hook_depth=1`, and `current_hook="root"`.

- PHP/Rust frame counts: 7 / 6.
- PHP trace, in order: `root_a:enter`, `child:enter`, `child:throw`, `child:transport_catch`, `child:finally`, `root_a:child_rethrow`, `root_a:caught_same_object`, `root_a:resume`, `root_a:exit`, `root_b`, `late`.
- Rust native trace: `r5`, `c3`, `r20`. The child exception interrupts the child filter before c7.
- Root and child filter counts: 1 / 1.
- Final bytes: `seed:r5:php-root-caught:php-b:late:r20` (`736565643a72353a7068702d726f6f742d6361756768743a7068702d623a6c6174653a723230`).
- Final item name: `root-before-child:child:root-after-child:b:late`.
- After normal root completion, public current hook is `root` and public depth is 1; Continuation depth is 0 and state is `Finished`. Do not claim public stack normalization to empty.

`request_finished` has exact payload keys `{status, php_observation, transport}`, with `status="ok"`. `php_observation` has exactly these keys: `alias_same`, `same_closure_handle`, `distinct_closures`, `parent_ticket_preserved`, `closed_read_rejected`, `registry_live_after_close`, `final_name`, `returned_value_hex`, `trace`, `throwable_class`, `throwable_message`, `child_throw_same_object`, `root_rethrow_same_object`, `root_caught_same_object`, `root_transport_catch_same_object`, `child_throw_count`, `root_rethrow_count`, `root_catch_count`, `root_transport_catch_count`, `external_throwable_alive_after_close`, `current_hook_after`, `hook_depth_after`. The new booleans are `[true,true,true,false]` in the listed order; counts are `[1,1,1,0]`. `returned_value_hex` is the final bytes above; `current_hook_after="root"`, `hook_depth_after=1`. Existing closure/object identity and close assertions remain enabled; all retained handles reject resolve after close and `registry_live_after_close=0`.

The catch peer result has exactly these top-level keys: `{scenario, continuation_peer, frames_from_php, frames_from_rust, final_value_hex, native_trace, root_b_removed, late_registered, root_filters, child_filters, hook_depth_after, continuation_depth_after, state_after, php_observation, current_hook_after, child_priority_after, root_priority_after, parent_ticket_preserved, child_ticket_retired, root_ticket_retired_by_throwable, abort_observation}`. Values specific to this scenario are `scenario="hooks_catch"`, final bytes as `final_value_hex`, `native_trace=["r5","c3","r20"]`, `root_filters=1`, `child_filters=1`, `hook_depth_after=1`, `current_hook_after="root"`, `child_priority_after=5`, `root_priority_after=null`, `continuation_depth_after=0`, `state_after="Finished"`, `parent_ticket_preserved=true`, `child_ticket_retired=true`, `root_ticket_retired_by_throwable=false`, and `abort_observation=null`.

## `hooks_abort`

After the same child completion and child return, PHP root A resolves and rethrows the identical Throwable without catching it locally. The outer root invocation wrapper catches only to transport the same completion. Its root `resume` has no `parent_effect` and exact payload keys `{effect_id, completion}`, with `effect_id="e-root-a"`. Rust validates the same Throwable handle and root `DispatchStep::PhpThrowable`, retires the root ticket, aborts the Continuation, and sends one root `abort`. It does not invoke root B, late, or native r20 and does not send a normal root `return` or call `finish_request`.

The `abort` payload has exact keys `{reason, completion, root_ticket_retired, root_b_removed, late_registered, current_hook, hook_depth, continuation_depth, state, late_resume_rejected, finish_request_after_abort_rejected}`. Values are `reason="php_throwable"`, the unchanged completion, `root_ticket_retired=true`, `root_b_removed=true`, `late_registered=true`, public `current_hook="child"`, public `hook_depth=2`, `continuation_depth=0`, `state="Aborted"`, and both rejection flags true. The child operation removes root B and registers late in both modes; abort proves these registration mutations do not invoke root B, late, or r20. The request-owned Dispatcher is dropped after abort; no stack normalization, worker reuse, or post-abort public-stack behavior is claimed.

- PHP/Rust frame counts: 5 / 4.
- PHP trace, in order: `root_a:enter`, `child:enter`, `child:throw`, `child:transport_catch`, `child:finally`, `root_a:child_rethrow`, `root_a:transport_catch`, `root_a:finally`.
- Rust native trace: `r5`, `c3`; root B, late, and r20 do not run.
- Root and child filter counts: 1 / 1.
- Final item name: `root-before-child:child`.
- After abort, public current hook remains `child` at depth 2; Continuation depth is 0 and state is `Aborted`.

PHP validates the abort's exact keys, same descriptor/class/message and state before closing the registry. It confirms the external Throwable alias still identifies the object after close, all retained handles reject reads, and live registry count is zero. It then sends exactly one `request_finished` with payload `{status:"throwable", php_observation, transport}` and reaches EOF. The PHP observation uses the same exact key set as `hooks_catch`; the new booleans are `[true,true,false,true]`, counts `[1,1,0,1]`, `returned_value_hex=null`, `final_name="root-before-child:child"`, `current_hook_after="child"`, `hook_depth_after=2`.

The abort peer result has exactly the same top-level keys listed for `hooks_catch`; scenario-specific values are `scenario="hooks_abort"`, `final_value_hex=null`, `native_trace=["r5","c3"]`, `root_b_removed=true`, `late_registered=true`, `root_filters=1`, `child_filters=1`, `hook_depth_after=2`, `current_hook_after="child"`, `child_priority_after=5`, `root_priority_after=10`, `continuation_depth_after=0`, `state_after="Aborted"`, `parent_ticket_preserved=true`, `child_ticket_retired=true`, `root_ticket_retired_by_throwable=true`, and `abort_observation` equal to the exact abort frame payload.

## Scope

This is a fixed PoC for typed Throwable completion across a nested PHP hook Closure. It does not establish production WordPress dispatcher integration, complete WordPress callable identity, final-reference release on removal, worker reuse, or product hook/Query acceptance. The existing normal `hooks` PHP/Rust message schema, frame counts, and expected values are retained. Its diagnostic peer summary additionally reports `current_hook_after`, `child_priority_after`, and `root_priority_after` from the actual Dispatcher; all three are null after normal completion.

Continuation depth and terminal state are Rust peer observations. In the caught case `Finished` is observed only after receiving PHP `request_finished` and calling `finish_request`; PHP does not report that future state as its own heap observation.
