use recastpress_wp::{
    continuation::{Completion, PhpHandle, PhpHandleKind, RequestGeneration},
    dispatcher::{
        ControlResult, ControlStep, DispatchBoundary, DispatchStep, Dispatcher, DispatcherCallback,
        FilterCounterLookup, FilterCounterMutation, FilterCounterOperandAccess,
        FilterCounterResult,
    },
    hooks::{CallbackId, HookArgument, HookError, HookScalar},
    value::{PhpArray, PhpKey, PhpValue},
};
use std::sync::{Arc, Mutex};

#[test]
fn typed_all_return_cannot_replace_or_mutate_named_filter_value() {
    let mut array = PhpArray::new();
    array.insert(PhpKey::from_bytes(b"x"), PhpValue::Null);
    let original = PhpValue::Array(array);
    let expected = original.clone();
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "all",
        0,
        DispatcherCallback::new(id("all_values"), 0, |_, args| {
            assert_eq!(args[0], value("alpha"));
            let PhpValue::Array(mut changed) = args[1].clone() else {
                panic!("expected typed array")
            };
            changed.insert(PhpKey::from_bytes(b"x"), PhpValue::Bool(true));
            Ok(PhpValue::Array(changed))
        }),
    );
    dispatcher.add(
        "alpha",
        10,
        DispatcherCallback::new(id("named_value"), 2, move |_, args| {
            assert_eq!(args, [expected.clone(), PhpValue::Null]);
            Ok(args[0].clone())
        }),
    );
    assert_eq!(
        dispatcher
            .apply_filters("alpha", original.clone(), &[PhpValue::Null])
            .unwrap(),
        original
    );
    assert_eq!(dispatcher.did_filter("alpha"), 1);
    assert_eq!(dispatcher.did_filter("all"), 0);
    assert_eq!(dispatcher.hook_stack_depth(), 0);
}

fn id(name: &str) -> CallbackId {
    CallbackId {
        module: "fixture".into(),
        callback: name.into(),
    }
}

fn finish_control(
    dispatcher: &mut Dispatcher,
    operation: &mut recastpress_wp::dispatcher::ControlOperation,
) -> ControlResult {
    match dispatcher.advance_control(operation).unwrap() {
        ControlStep::Complete(result) => result,
        step => panic!("unexpected control step: {step:?}"),
    }
}

fn closure_handle(slot: u32) -> PhpHandle {
    PhpHandle::new(
        RequestGeneration::new(51),
        PhpHandleKind::Closure,
        slot,
        1,
        u64::from(slot),
    )
}

fn callable_handle(slot: u32) -> PhpHandle {
    PhpHandle::new(
        RequestGeneration::new(51),
        PhpHandleKind::Callable,
        slot,
        1,
        u64::from(slot),
    )
}

fn throwable_handle(slot: u32) -> PhpHandle {
    PhpHandle::new(
        RequestGeneration::new(51),
        PhpHandleKind::Throwable,
        slot,
        1,
        u64::from(slot),
    )
}

fn drive_native_ticket(
    dispatcher: &mut Dispatcher,
    ticket: recastpress_wp::dispatcher::DispatchTicket,
) -> PhpValue {
    match dispatcher.advance(ticket).unwrap() {
        DispatchStep::Complete(value) => value,
        DispatchStep::InvokePhp(invocation) => {
            panic!("unexpected PHP callback: {}", invocation.callback.callback)
        }
        DispatchStep::ReleasePhp(handle) => panic!("unexpected PHP release: {handle:?}"),
        DispatchStep::Boundary(boundary) => panic!("unexpected PHP boundary: {boundary:?}"),
        DispatchStep::PhpThrowable(handle) => {
            panic!("unexpected PHP throwable handle: {handle:?}")
        }
    }
}

#[test]
fn effectful_filter_updates_counter_only_after_boundary_and_runs_named_phases() {
    let mut dispatcher = Dispatcher::new();
    let name = HookArgument::bytes("typed");
    let seen = Arc::new(Mutex::new(Vec::new()));
    let callback_seen = Arc::clone(&seen);
    dispatcher.add(
        "typed",
        10,
        DispatcherCallback::new(id("named"), 1, move |context, args| {
            callback_seen.lock().unwrap().push(args[0].clone());
            assert_eq!(context.did_filter("typed"), 1);
            assert_eq!(
                context.current_hook_typed(),
                Some(&HookScalar::Bytes(b"typed".to_vec()))
            );
            Ok(args[0].clone())
        }),
    );
    let ticket = dispatcher
        .begin_filter_effectful_typed(&name, value("seed"), &[])
        .unwrap();
    assert_eq!(dispatcher.did_filter("typed"), 0);
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::FilterCounterWrite))
    );
    assert_eq!(
        dispatcher.advance(ticket),
        Err(HookError::PhpBoundaryPending)
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(dispatcher.did_filter("typed"), 1);
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::NamedHookIsset))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::NamedHookRead))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Complete(value("seed")))
    );
    assert_eq!(*seen.lock().unwrap(), vec![value("seed")]);
    assert_eq!(dispatcher.hook_stack_depth(), 0);
}

#[test]
fn effectful_filter_all_completion_uses_live_named_isset_and_preserves_original_hook() {
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "all",
        1,
        DispatcherCallback::new(id("all"), 2, |context, args| {
            assert_eq!(args[0], PhpValue::Integer(7));
            assert_eq!(context.current_hook_typed(), Some(&HookScalar::Integer(7)));
            Ok(PhpValue::Null)
        }),
    );
    dispatcher.add(
        "7",
        2,
        DispatcherCallback::new(id("named"), 1, |context, args| {
            assert_eq!(args[0], value("seed"));
            assert_eq!(context.current_hook_typed(), Some(&HookScalar::Integer(7)));
            Ok(value("done"))
        }),
    );
    let name = HookArgument::new(HookScalar::Integer(7), PhpKey::Integer(7));
    assert_eq!(
        dispatcher.hook_callbacks_empty(&HookArgument::bytes("all")),
        Some(false)
    );
    let ticket = dispatcher
        .begin_filter_effectful_typed(&name, value("seed"), &[])
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::FilterCounterWrite))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::NamedHookIsset))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::NamedHookRead))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Complete(value("done")))
    );
    assert_eq!(dispatcher.did_filter_typed(&name), 1);
    assert_eq!(dispatcher.hook_stack_depth(), 0);
}

#[test]
fn active_priority_readdition_uses_original_priority_identity_not_only_array_key() {
    fn run_case(
        raw_priority: recastpress_wp::hooks::HookPriority,
        base: i64,
    ) -> (String, Vec<String>) {
        let hook = HookArgument::bytes("active-priority");
        let trace = Arc::new(Mutex::new(Vec::new()));
        let mut dispatcher = Dispatcher::new();
        let lower_trace = Arc::clone(&trace);
        dispatcher.add_typed(
            &hook,
            recastpress_wp::hooks::HookPriority::integer(0),
            DispatcherCallback::new(id("lower"), 1, move |_, args| {
                lower_trace.lock().unwrap().push("lower".to_owned());
                Ok(args[0].clone())
            }),
        );
        let trigger_trace = Arc::clone(&trace);
        let callback_hook = hook.clone();
        let callback_priority = raw_priority.clone();
        dispatcher.add_typed(
            &hook,
            recastpress_wp::hooks::HookPriority::integer(base),
            DispatcherCallback::new(id("trigger"), 1, move |dispatcher, args| {
                trigger_trace.lock().unwrap().push("trigger".to_owned());
                assert!(dispatcher.remove_typed(
                    &callback_hook,
                    &recastpress_wp::hooks::HookPriority::integer(base),
                    &id("trigger"),
                ));
                let replacement_trace = Arc::clone(&trigger_trace);
                dispatcher.add_typed(
                    &callback_hook,
                    callback_priority.clone(),
                    DispatcherCallback::new(id("replacement"), 1, move |_, args| {
                        replacement_trace
                            .lock()
                            .unwrap()
                            .push("replacement".to_owned());
                        Ok(args[0].clone())
                    }),
                );
                Ok(args[0].clone())
            }),
        );
        let tail_trace = Arc::clone(&trace);
        dispatcher.add_typed(
            &hook,
            recastpress_wp::hooks::HookPriority::integer(10),
            DispatcherCallback::new(id("tail"), 1, move |_, args| {
                tail_trace.lock().unwrap().push("tail".to_owned());
                Ok(args[0].clone())
            }),
        );
        let output = dispatcher
            .apply_filters_typed(&hook, value("seed"), &[])
            .unwrap();
        let PhpValue::Bytes(output) = output else {
            panic!("expected byte result")
        };
        let trace = trace.lock().unwrap().clone();
        (String::from_utf8(output).unwrap(), trace)
    }

    use recastpress_wp::hooks::HookPriority;
    let (integer_output, integer_trace) = run_case(HookPriority::integer(7), 7);
    assert_eq!(integer_output, "seed");
    assert_eq!(integer_trace, ["lower", "trigger", "tail"]);

    let (string_output, string_trace) = run_case(
        HookPriority::new(HookScalar::Bytes(b"7".to_vec()), PhpKey::Integer(7)),
        7,
    );
    assert_eq!(string_output, "seed");
    assert_eq!(string_trace, ["lower", "trigger"]);

    let (float_output, float_trace) = run_case(
        HookPriority::new(HookScalar::Float(7.9), PhpKey::Integer(7)),
        7,
    );
    assert_eq!(float_output, "seed");
    assert_eq!(float_trace, ["lower", "trigger"]);

    let (boolean_output, boolean_trace) = run_case(
        HookPriority::new(HookScalar::Bool(true), PhpKey::Integer(1)),
        1,
    );
    assert_eq!(boolean_output, "seed");
    assert_eq!(boolean_trace, ["lower", "trigger"]);
}

#[test]
fn effectful_empty_all_yields_missing_offset_and_null_foreach_boundaries() {
    let mut dispatcher = Dispatcher::new();
    let all = HookArgument::bytes("all");
    let mut write = dispatcher.begin_write_hook(&all).unwrap();
    assert!(matches!(
        dispatcher.advance_control(&mut write).unwrap(),
        recastpress_wp::dispatcher::ControlStep::Complete(_)
    ));
    let hook = HookArgument::bytes("empty-all-target");
    let ticket = dispatcher
        .begin_filter_effectful_typed(&hook, value("seed"), &[])
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::FilterCounterWrite))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::MissingOffsetAll))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::NullForeachAll))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::NamedHookIsset))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Complete(value("seed")))
    );
    assert_eq!(dispatcher.hook_stack_depth(), 0);
}

#[test]
fn nested_hook_table_operands_keep_old_receiver_until_outermost_resume() {
    use recastpress_wp::dispatcher::{ControlResult, ControlStep, HookBindingLookupResult};

    let mut dispatcher = Dispatcher::new();
    let hook = HookArgument::bytes("cow-target");
    let callable = callable_handle(341);
    let mut add = dispatcher
        .begin_add(
            "cow-target",
            10,
            DispatcherCallback::php_callable(id("old-cow-target"), 1, callable).unwrap(),
        )
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut add).unwrap(),
        ControlStep::Complete(ControlResult::Added(true))
    );

    let mut outer = dispatcher.begin_hook_binding_lookup().unwrap();
    let mut inner = dispatcher.begin_hook_binding_lookup().unwrap();
    let mut replace = dispatcher.begin_write_hook(&hook).unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut replace).unwrap(),
        ControlStep::Complete(ControlResult::HookWritten)
    );

    dispatcher
        .resume_hook_binding_lookup(&mut inner, &hook)
        .unwrap();
    let ControlStep::Complete(ControlResult::HookBindingLookup(HookBindingLookupResult::Found(
        old_target,
    ))) = dispatcher.advance_control(&mut inner).unwrap()
    else {
        panic!("inner operand must read the still-owned old table version")
    };
    dispatcher.release_target(old_target).unwrap();

    dispatcher
        .resume_hook_binding_lookup(&mut outer, &hook)
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut outer).unwrap(),
        ControlStep::ReleasePhp(callable)
    );
    dispatcher
        .resume_control_release(&mut outer, callable)
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut outer).unwrap(),
        ControlStep::Complete(ControlResult::HookBindingLookup(
            HookBindingLookupResult::NullOperand,
        ))
    );
}

#[test]
fn target_callbacks_empty_reads_the_pinned_object_after_binding_replacement() {
    use recastpress_wp::dispatcher::{ControlResult, ControlStep};

    let mut dispatcher = Dispatcher::new();
    let hook = HookArgument::bytes("stale-target-empty-check");
    dispatcher.add(
        "stale-target-empty-check",
        10,
        DispatcherCallback::new(id("old-target-callback"), 1, |_, args| Ok(args[0].clone())),
    );
    let old_target = dispatcher.capture_current_target(&hook).unwrap().unwrap();
    let mut replace = dispatcher.begin_write_hook(&hook).unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut replace).unwrap(),
        ControlStep::Complete(ControlResult::HookWritten)
    );

    assert_eq!(dispatcher.hook_callbacks_empty(&hook), Some(true));
    assert_eq!(dispatcher.target_callbacks_empty(old_target), Ok(false));
    dispatcher.release_target(old_target).unwrap();
}

#[test]
fn cancelled_hook_table_operand_drains_old_callable_before_original_throwable() {
    use recastpress_wp::dispatcher::{ControlResult, ControlStep};

    let mut dispatcher = Dispatcher::new();
    let hook = HookArgument::bytes("cow-cancel");
    let callable = callable_handle(342);
    let mut add = dispatcher
        .begin_add(
            "cow-cancel",
            10,
            DispatcherCallback::php_callable(id("cancelled-cow-target"), 1, callable).unwrap(),
        )
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut add).unwrap(),
        ControlStep::Complete(ControlResult::Added(true))
    );

    let mut lookup = dispatcher.begin_hook_binding_lookup().unwrap();
    let mut unbind = dispatcher.begin_unbind_hook(&hook).unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut unbind).unwrap(),
        ControlStep::Complete(ControlResult::HookUnbound)
    );
    let original = throwable_handle(343);
    dispatcher
        .cancel_hook_binding_lookup(&mut lookup, original)
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut lookup).unwrap(),
        ControlStep::ReleasePhp(callable)
    );

    let destructor_throw = throwable_handle(344);
    dispatcher
        .resume_control_release_completion(
            &mut lookup,
            callable,
            Completion::PhpThrowable(destructor_throw),
        )
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut lookup).unwrap(),
        ControlStep::PhpThrowable(destructor_throw)
    );
}

#[test]
fn effectful_named_hook_cow_read_returns_null_after_reentrant_all_insert() {
    let mut dispatcher = Dispatcher::new();
    let hook = HookArgument::new(HookScalar::Float(1.5), PhpKey::Integer(1));
    let all = HookArgument::bytes("all");
    dispatcher.add_typed(
        &hook,
        recastpress_wp::hooks::HookPriority::integer(10),
        DispatcherCallback::new(id("must-not-run-after-null-receiver"), 1, |_, _| {
            panic!("COW-invalidated receiver must not invoke the named callback")
        }),
    );

    let ticket = dispatcher
        .begin_filter_effectful_typed(&hook, value("seed"), &[])
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::FilterCounterWrite))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::NamedHookIsset))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::NamedHookRead))
    );

    // Models the warning handler for `$wp_filter[$hook]` creating an empty
    // `all` object after WordPress has already decided to push this hook.
    let mut write_all = dispatcher.begin_write_hook(&all).unwrap();
    assert!(matches!(
        dispatcher.advance_control(&mut write_all).unwrap(),
        recastpress_wp::dispatcher::ControlStep::Complete(_)
    ));
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(
            DispatchBoundary::NullMethodApplyFilters
        ))
    );
    dispatcher
        .resume_boundary(ticket, Completion::PhpThrowable(throwable_handle(321)))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::PhpThrowable(throwable_handle(321)))
    );
    assert_eq!(
        dispatcher.current_hook_typed(),
        Some(&HookScalar::Float(1.5))
    );
    assert_eq!(dispatcher.hook_stack_depth(), 1);
}
fn value(text: &str) -> PhpValue {
    PhpValue::Bytes(text.as_bytes().to_vec())
}
fn suffix(name: &str) -> DispatcherCallback {
    let name = name.to_string();
    DispatcherCallback::new(id(&name), 1, move |_, args| {
        let Some(PhpValue::Bytes(first)) = args.first() else {
            return Ok(value(&name));
        };
        let mut body = first.clone();
        body.extend_from_slice(name.as_bytes());
        Ok(PhpValue::Bytes(body))
    })
}

#[test]
fn absent_hooks_still_increment_separate_action_and_filter_counts() {
    let mut dispatcher = Dispatcher::new();
    assert_eq!(
        dispatcher.apply_filters("missing", value("original"), &[]),
        Ok(value("original"))
    );
    dispatcher.do_action("missing", &[]).unwrap();
    assert_eq!(dispatcher.did_filter("missing"), 1);
    assert_eq!(dispatcher.did_action("missing"), 1);
    assert_eq!(dispatcher.did_action("other"), 0);
    assert_eq!(dispatcher.current_hook(), None);
    assert!(!dispatcher.doing_hook(None));
}

#[test]
fn all_hook_receives_unsliced_original_arguments_and_cannot_replace_value() {
    let mut dispatcher = Dispatcher::new();
    let seen = Arc::new(Mutex::new(Vec::new()));
    let capture = Arc::clone(&seen);
    dispatcher.add(
        "all",
        10,
        DispatcherCallback::new(id("all"), 0, move |dispatcher, args| {
            assert_eq!(dispatcher.current_hook(), Some("target"));
            capture.lock().unwrap().push(args.to_vec());
            Ok(value("ignored"))
        }),
    );
    dispatcher.add("target", 0, suffix("tail"));
    assert_eq!(dispatcher.priority_of("target", &id("tail")), Some(0));
    assert_eq!(
        dispatcher.apply_filters("target", value("start"), &[value("extra")]),
        Ok(value("starttail"))
    );
    dispatcher.do_action("target", &[]).unwrap();
    assert_eq!(
        *seen.lock().unwrap(),
        vec![
            vec![value("target"), value("start"), value("extra")],
            vec![value("target")]
        ]
    );
    assert_eq!(dispatcher.current_hook(), None);
}

#[test]
fn nested_different_hooks_restore_the_call_stack() {
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "outer",
        10,
        DispatcherCallback::new(id("outer"), 1, |dispatcher, args| {
            assert_eq!(dispatcher.current_hook(), Some("outer"));
            let output = dispatcher.apply_filters("inner", args[0].clone(), &[])?;
            assert_eq!(dispatcher.current_hook(), Some("outer"));
            Ok(output)
        }),
    );
    dispatcher.add(
        "inner",
        10,
        DispatcherCallback::new(id("inner"), 1, |dispatcher, args| {
            assert_eq!(dispatcher.current_hook(), Some("inner"));
            assert!(dispatcher.doing_hook(Some("outer")));
            Ok(args[0].clone())
        }),
    );
    assert_eq!(
        dispatcher.apply_filters("outer", value("value"), &[]),
        Ok(value("value"))
    );
    assert_eq!(dispatcher.current_hook(), None);
    assert_eq!(dispatcher.did_filter("inner"), 1);
}

#[test]
fn deleting_and_readding_an_active_binding_keeps_old_and_new_objects_separate() {
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "target",
        10,
        DispatcherCallback::new(id("replace"), 1, |dispatcher, args| {
            dispatcher.remove_all("target", None);
            dispatcher.add("target", 20, suffix("new"));
            assert_eq!(
                dispatcher.apply_filters("target", value("nested"), &[]),
                Ok(value("nestednew"))
            );
            Ok(args[0].clone())
        }),
    );
    dispatcher.add("target", 10, suffix("old_peer"));
    dispatcher.add("target", 30, suffix("old_future"));
    assert_eq!(
        dispatcher.apply_filters("target", value("first"), &[]),
        Ok(value("firstold_peer"))
    );
    assert_eq!(
        dispatcher.apply_filters("target", value("second"), &[]),
        Ok(value("secondnew"))
    );
    assert!(!dispatcher.has_at("target", 30, &id("old_future")));
}

fn mutating_dispatcher(trace: Arc<Mutex<Vec<&'static str>>>) -> Dispatcher {
    let mut dispatcher = Dispatcher::new();
    let all_trace = Arc::clone(&trace);
    dispatcher.add(
        "all",
        10,
        DispatcherCallback::new(id("all_replace"), 0, move |dispatcher, _| {
            all_trace.lock().unwrap().push("all_old");
            assert_eq!(dispatcher.current_hook(), Some("target"));
            dispatcher.add(
                "all",
                0,
                DispatcherCallback::new(id("all_early"), 0, |_, _| Ok(value("ignored"))),
            );
            dispatcher.remove_all("all", None);
            let nested_trace = Arc::clone(&all_trace);
            dispatcher.add(
                "all",
                20,
                DispatcherCallback::new(id("all_new"), 0, move |dispatcher, _| {
                    assert_eq!(dispatcher.current_hook(), Some("target"));
                    nested_trace.lock().unwrap().push("all_new");
                    Ok(value("ignored"))
                }),
            );
            Ok(value("ignored"))
        }),
    );

    let target_trace = Arc::clone(&trace);
    dispatcher.add(
        "target",
        10,
        DispatcherCallback::new(id("target_replace"), 1, move |dispatcher, args| {
            target_trace.lock().unwrap().push("target_old");
            assert_eq!(dispatcher.current_hook(), Some("target"));
            dispatcher.remove_all("target", None);
            let next_trace = Arc::clone(&target_trace);
            dispatcher.add(
                "target",
                20,
                DispatcherCallback::new(id("target_new"), 1, move |dispatcher, args| {
                    assert_eq!(dispatcher.current_hook(), Some("target"));
                    next_trace.lock().unwrap().push("target_new");
                    let PhpValue::Bytes(mut output) = args[0].clone() else {
                        panic!("expected bytes")
                    };
                    output.extend_from_slice(b"new");
                    Ok(PhpValue::Bytes(output))
                }),
            );
            assert_eq!(
                dispatcher.apply_filters("target", value("nested"), &[]),
                Ok(value("nestednew"))
            );
            assert_eq!(dispatcher.current_hook(), Some("target"));
            Ok(args[0].clone())
        }),
    );
    dispatcher.add(
        "target",
        10,
        DispatcherCallback::new(id("old_peer"), 1, move |_, args| {
            trace.lock().unwrap().push("old_peer");
            let PhpValue::Bytes(mut output) = args[0].clone() else {
                panic!("expected bytes")
            };
            output.extend_from_slice(b"old_peer");
            Ok(PhpValue::Bytes(output))
        }),
    );
    dispatcher.add(
        "target",
        30,
        DispatcherCallback::new(id("old_future"), 1, |_, _| Ok(value("wrong"))),
    );
    dispatcher
}

#[test]
fn resumable_native_dispatch_matches_sync_all_and_named_binding_mutation() {
    let sync_trace = Arc::new(Mutex::new(Vec::new()));
    let mut sync = mutating_dispatcher(Arc::clone(&sync_trace));
    let sync_first = sync.apply_filters("target", value("first"), &[]).unwrap();
    let sync_second = sync.apply_filters("target", value("second"), &[]).unwrap();
    let sync_diagnostics = sync.take_diagnostics();
    let sync_counts = (
        sync.did_filter("target"),
        sync.did_filter("all"),
        sync.hook_stack_depth(),
    );

    let async_trace = Arc::new(Mutex::new(Vec::new()));
    let mut resumable = mutating_dispatcher(Arc::clone(&async_trace));
    let first = resumable
        .begin_filter("target", value("first"), &[])
        .unwrap();
    let async_first = drive_native_ticket(&mut resumable, first);
    let second = resumable
        .begin_filter("target", value("second"), &[])
        .unwrap();
    let async_second = drive_native_ticket(&mut resumable, second);

    assert_eq!(
        (async_first, async_second),
        (sync_first.clone(), sync_second.clone())
    );
    assert_eq!(*async_trace.lock().unwrap(), *sync_trace.lock().unwrap());
    assert_eq!(resumable.take_diagnostics(), sync_diagnostics);
    assert_eq!(
        (
            resumable.did_filter("target"),
            resumable.did_filter("all"),
            resumable.hook_stack_depth()
        ),
        sync_counts
    );
    assert_eq!(sync_first, value("firstold_peer"));
    assert_eq!(sync_second, value("secondnew"));
    assert_eq!(
        *async_trace.lock().unwrap(),
        vec![
            "all_old",
            "target_old",
            "all_new",
            "target_new",
            "old_peer",
            "all_new",
            "target_new"
        ]
    );
    assert_eq!(resumable.current_hook(), None);
}

#[test]
fn all_hook_permanent_removal_current_stack_sync_vs_resumable_reproduction() {
    for keep_named in [true, false] {
        let sync_trace = Arc::new(Mutex::new(Vec::new()));
        let sync_current = Arc::new(Mutex::new(Vec::new()));
        let mut sync = Dispatcher::new();
        let all_trace = Arc::clone(&sync_trace);
        sync.add(
            "all",
            10,
            DispatcherCallback::new(id("remove_all"), 0, move |dispatcher, _| {
                all_trace.lock().unwrap().push("all");
                dispatcher.remove_all("all", None);
                if !keep_named {
                    dispatcher.remove_all("target", None);
                }
                Ok(value("ignored"))
            }),
        );
        let named_trace = Arc::clone(&sync_trace);
        let named_current = Arc::clone(&sync_current);
        sync.add(
            "target",
            10,
            DispatcherCallback::new(id("named"), 1, move |dispatcher, args| {
                named_trace.lock().unwrap().push("named");
                named_current.lock().unwrap().push((
                    dispatcher.current_hook().map(str::to_owned),
                    dispatcher.hook_stack_depth(),
                ));
                Ok(args[0].clone())
            }),
        );
        let sync_value = sync.apply_filters("target", value("input"), &[]).unwrap();
        let sync_observation = (
            sync_value,
            sync_trace.lock().unwrap().clone(),
            sync.current_hook().map(str::to_owned),
            sync.hook_stack_depth(),
            sync.take_diagnostics(),
            sync_current.lock().unwrap().clone(),
        );

        let async_trace = Arc::new(Mutex::new(Vec::new()));
        let async_current = Arc::new(Mutex::new(Vec::new()));
        let mut resumable = Dispatcher::new();
        let all_trace = Arc::clone(&async_trace);
        resumable.add(
            "all",
            10,
            DispatcherCallback::new(id("remove_all"), 0, move |dispatcher, _| {
                all_trace.lock().unwrap().push("all");
                dispatcher.remove_all("all", None);
                if !keep_named {
                    dispatcher.remove_all("target", None);
                }
                Ok(value("ignored"))
            }),
        );
        let named_trace = Arc::clone(&async_trace);
        let named_current = Arc::clone(&async_current);
        resumable.add(
            "target",
            10,
            DispatcherCallback::new(id("named"), 1, move |dispatcher, args| {
                named_trace.lock().unwrap().push("named");
                named_current.lock().unwrap().push((
                    dispatcher.current_hook().map(str::to_owned),
                    dispatcher.hook_stack_depth(),
                ));
                Ok(args[0].clone())
            }),
        );
        let ticket = resumable
            .begin_filter("target", value("input"), &[])
            .unwrap();
        let async_value = drive_native_ticket(&mut resumable, ticket);
        let async_observation = (
            async_value,
            async_trace.lock().unwrap().clone(),
            resumable.current_hook().map(str::to_owned),
            resumable.hook_stack_depth(),
            resumable.take_diagnostics(),
            async_current.lock().unwrap().clone(),
        );

        assert_eq!(sync_observation.0, async_observation.0);
        assert_eq!(sync_observation.1, async_observation.1);
        assert_eq!(sync_observation.2, async_observation.2);
        assert_eq!(sync_observation.3, async_observation.3);
        assert_eq!(sync_observation.4, async_observation.4);
        assert_eq!(sync_observation.2, Some("target".into()));
        assert_eq!(sync_observation.3, 1);
        assert_eq!(async_observation.2, Some("target".into()));
        assert_eq!(async_observation.3, 1);
        assert_eq!(sync_observation.5, async_observation.5);
        assert_eq!(
            sync_observation.5,
            if keep_named {
                vec![(Some("target".into()), 2)]
            } else {
                Vec::new()
            }
        );
    }
}

#[test]
fn php_callback_yields_snapshot_mutates_nested_filter_and_resumes_in_order() {
    let mut dispatcher = Dispatcher::new();
    let native_trace = Arc::new(Mutex::new(Vec::new()));
    dispatcher.add(
        "root",
        5,
        DispatcherCallback::new(id("r5"), 1, |_, args| {
            let PhpValue::Bytes(mut output) = args[0].clone() else {
                panic!("expected bytes")
            };
            output.extend_from_slice(b":r5");
            Ok(PhpValue::Bytes(output))
        }),
    );
    dispatcher.add(
        "root",
        10,
        DispatcherCallback::php(id("root_a"), 1, closure_handle(1)).unwrap(),
    );
    dispatcher.add(
        "root",
        10,
        DispatcherCallback::php(id("root_b"), 1, closure_handle(2)).unwrap(),
    );
    dispatcher.add(
        "root",
        20,
        DispatcherCallback::new(id("r20"), 1, |_, args| {
            let PhpValue::Bytes(mut output) = args[0].clone() else {
                panic!("expected bytes")
            };
            output.extend_from_slice(b":r20");
            Ok(PhpValue::Bytes(output))
        }),
    );
    for (priority, callback, suffix) in [(3, "c3", b":c3".as_slice()), (7, "c7", b":c7".as_slice())]
    {
        let suffix = suffix.to_vec();
        let trace = Arc::clone(&native_trace);
        dispatcher.add(
            "child",
            priority,
            DispatcherCallback::new(id(callback), 1, move |_, args| {
                trace.lock().unwrap().push(callback);
                let PhpValue::Bytes(mut output) = args[0].clone() else {
                    panic!("expected bytes")
                };
                output.extend_from_slice(&suffix);
                Ok(PhpValue::Bytes(output))
            }),
        );
    }
    dispatcher.add(
        "child",
        5,
        DispatcherCallback::php(id("child"), 1, closure_handle(3)).unwrap(),
    );

    let root = dispatcher.begin_filter("root", value("seed"), &[]).unwrap();
    assert_eq!(
        dispatcher.advance(root),
        Ok(DispatchStep::InvokePhp(recv_invocation(
            "root_a", "root", 10, "seed:r5", 1
        )))
    );
    assert_eq!(dispatcher.advance(root), Err(HookError::PhpCallbackPending));
    assert!(dispatcher.remove("root", 10, &id("root_b")));
    dispatcher.add(
        "root",
        15,
        DispatcherCallback::php(id("late"), 1, closure_handle(4)).unwrap(),
    );
    let child = dispatcher
        .begin_filter("child", value("seed:r5"), &[])
        .unwrap();
    assert_eq!(
        dispatcher.advance(root),
        Err(HookError::DispatchTicketOrder)
    );
    assert_eq!(
        dispatcher.advance(child),
        Ok(DispatchStep::InvokePhp(recv_invocation(
            "child",
            "child",
            5,
            "seed:r5:c3",
            3
        )))
    );
    assert!(
        dispatcher
            .resume_php(child, value("seed:r5:c3:php-child"))
            .is_ok()
    );
    assert_eq!(
        dispatcher.resume_php(child, value("duplicate")),
        Err(HookError::NoPhpCallbackPending)
    );
    let child_result = drive_native_ticket(&mut dispatcher, child);
    assert_eq!(child_result, value("seed:r5:c3:php-child:c7"));
    assert_eq!(dispatcher.current_hook(), Some("root"));
    let root_a_result = bytes_suffix(&child_result, b":php-root");
    dispatcher.resume_php(root, root_a_result).unwrap();
    assert_eq!(
        dispatcher.resume_php(root, value("duplicate")),
        Err(HookError::NoPhpCallbackPending)
    );
    assert_eq!(
        dispatcher.advance(root),
        Ok(DispatchStep::InvokePhp(recv_invocation(
            "root_b",
            "root",
            10,
            "seed:r5:c3:php-child:c7:php-root",
            2
        )))
    );
    dispatcher
        .resume_php(root, value("seed:r5:c3:php-child:c7:php-root:php-b"))
        .unwrap();
    assert_eq!(
        dispatcher.advance(root),
        Ok(DispatchStep::InvokePhp(recv_invocation(
            "late",
            "root",
            15,
            "seed:r5:c3:php-child:c7:php-root:php-b",
            4
        )))
    );
    dispatcher
        .resume_php(root, value("seed:r5:c3:php-child:c7:php-root:php-b:late"))
        .unwrap();
    assert_eq!(
        drive_native_ticket(&mut dispatcher, root),
        value("seed:r5:c3:php-child:c7:php-root:php-b:late:r20")
    );
    assert!(!dispatcher.has_at("root", 10, &id("root_b")));
    assert!(dispatcher.has_at("root", 15, &id("late")));
    assert_eq!(*native_trace.lock().unwrap(), vec!["c3", "c7"]);
    assert_eq!(dispatcher.did_filter("root"), 1);
    assert_eq!(dispatcher.did_filter("child"), 1);
    assert_eq!(dispatcher.hook_stack_depth(), 0);
    assert_eq!(dispatcher.current_hook(), None);
    assert_eq!(
        dispatcher.advance(root),
        Err(HookError::InvalidDispatchTicket)
    );
}

#[test]
fn resumable_tickets_reject_foreign_order_replay_and_cancel_child_restores_parent() {
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "root",
        10,
        DispatcherCallback::php(id("root"), 1, closure_handle(1)).unwrap(),
    );
    dispatcher.add(
        "child",
        5,
        DispatcherCallback::php(id("child"), 1, closure_handle(2)).unwrap(),
    );
    let root = dispatcher.begin_filter("root", value("root"), &[]).unwrap();
    assert!(matches!(
        dispatcher.advance(root),
        Ok(DispatchStep::InvokePhp(_))
    ));

    let mut foreign = Dispatcher::new();
    assert_eq!(foreign.advance(root), Err(HookError::InvalidDispatchTicket));
    assert_eq!(foreign.cancel(root), Err(HookError::InvalidDispatchTicket));

    let child = dispatcher
        .begin_filter("child", value("child"), &[])
        .unwrap();
    assert!(matches!(
        dispatcher.advance(child),
        Ok(DispatchStep::InvokePhp(_))
    ));
    assert_eq!(
        dispatcher.advance(root),
        Err(HookError::DispatchTicketOrder)
    );
    dispatcher.cancel(child).unwrap();
    assert_eq!(
        dispatcher.advance(child),
        Err(HookError::InvalidDispatchTicket)
    );
    dispatcher.resume_php(root, value("root-return")).unwrap();
    assert_eq!(
        drive_native_ticket(&mut dispatcher, root),
        value("root-return")
    );
    assert_eq!(
        dispatcher.cancel(root),
        Err(HookError::InvalidDispatchTicket)
    );
    assert_eq!(dispatcher.current_hook(), None);
    assert_eq!(dispatcher.hook_stack_depth(), 0);

    let cancelled = dispatcher
        .begin_filter("root", value("cancel"), &[])
        .unwrap();
    assert!(matches!(
        dispatcher.advance(cancelled),
        Ok(DispatchStep::InvokePhp(_))
    ));
    dispatcher.cancel(cancelled).unwrap();
    assert_eq!(
        dispatcher.advance(cancelled),
        Err(HookError::InvalidDispatchTicket)
    );
    assert_eq!(dispatcher.current_hook(), None);
    assert_eq!(dispatcher.hook_stack_depth(), 0);
}

#[test]
fn throwable_completion_cancels_child_and_restores_parent_pending() {
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "root",
        10,
        DispatcherCallback::php(id("root"), 1, closure_handle(21)).unwrap(),
    );
    dispatcher.add(
        "child",
        5,
        DispatcherCallback::php(id("child"), 1, closure_handle(22)).unwrap(),
    );
    let root = dispatcher.begin_filter("root", value("root"), &[]).unwrap();
    assert!(matches!(
        dispatcher.advance(root),
        Ok(DispatchStep::InvokePhp(_))
    ));
    assert_eq!(dispatcher.current_priority("root"), Some(10));
    let child = dispatcher
        .begin_filter("child", value("child"), &[])
        .unwrap();
    assert!(matches!(
        dispatcher.advance(child),
        Ok(DispatchStep::InvokePhp(_))
    ));
    assert_eq!(dispatcher.current_priority("child"), Some(5));

    let mut foreign = Dispatcher::new();
    assert_eq!(
        foreign.resume_php_completion(child, Completion::PhpThrowable(throwable_handle(25))),
        Err(HookError::InvalidDispatchTicket)
    );
    assert_eq!(
        dispatcher.resume_php_completion(root, Completion::PhpThrowable(throwable_handle(26))),
        Err(HookError::DispatchTicketOrder)
    );
    let wrong_kind = closure_handle(23);
    assert_eq!(
        dispatcher.resume_php_completion(child, Completion::PhpThrowable(wrong_kind)),
        Err(HookError::InvalidPhpThrowableHandle)
    );
    assert_eq!(
        dispatcher.advance(child),
        Err(HookError::PhpCallbackPending)
    );
    assert_eq!(dispatcher.current_hook(), Some("child"));
    assert_eq!(dispatcher.hook_stack_depth(), 2);
    assert_eq!(dispatcher.current_priority("root"), Some(10));
    assert_eq!(dispatcher.current_priority("child"), Some(5));

    let thrown = throwable_handle(24);
    dispatcher
        .resume_php_completion(child, Completion::PhpThrowable(thrown))
        .unwrap();
    assert_eq!(
        dispatcher.resume_php(child, value("duplicate")),
        Err(HookError::NoPhpCallbackPending)
    );
    assert_eq!(
        dispatcher.resume_php_completion(child, Completion::PhpThrowable(throwable_handle(27))),
        Err(HookError::NoPhpCallbackPending)
    );
    assert_eq!(
        dispatcher.begin_filter("grandchild", value("grandchild"), &[]),
        Err(HookError::DispatchParentNotPending)
    );
    assert_eq!(dispatcher.did_filter("grandchild"), 0);
    assert_eq!(dispatcher.current_hook(), Some("child"));
    assert_eq!(dispatcher.hook_stack_depth(), 2);
    assert_eq!(
        dispatcher.advance(child),
        Ok(DispatchStep::PhpThrowable(thrown))
    );
    assert_eq!(
        dispatcher.advance(child),
        Err(HookError::InvalidDispatchTicket)
    );
    assert_eq!(dispatcher.current_hook(), Some("child"));
    assert_eq!(dispatcher.hook_stack_depth(), 2);
    assert!(dispatcher.doing_hook(Some("root")));
    assert!(dispatcher.doing_hook(Some("child")));
    assert_eq!(dispatcher.current_priority("root"), Some(10));
    assert_eq!(dispatcher.current_priority("child"), Some(5));
    assert_eq!(dispatcher.did_filter("child"), 1);
    assert_eq!(dispatcher.advance(root), Err(HookError::PhpCallbackPending));

    let reentry = dispatcher
        .begin_filter("child", value("reentry"), &[])
        .unwrap();
    assert!(matches!(
        dispatcher.advance(reentry),
        Ok(DispatchStep::InvokePhp(_))
    ));
    dispatcher.resume_php(reentry, value("reentered")).unwrap();
    assert_eq!(
        dispatcher.advance(reentry),
        Ok(DispatchStep::Complete(value("reentered")))
    );
    assert_eq!(dispatcher.current_priority("child"), Some(5));
    assert_eq!(dispatcher.did_filter("child"), 2);

    dispatcher.resume_php(root, value("caught-child")).unwrap();
    assert_eq!(
        drive_native_ticket(&mut dispatcher, root),
        value("caught-child")
    );
    assert_eq!(dispatcher.current_hook(), Some("root"));
    assert_eq!(dispatcher.hook_stack_depth(), 1);
    assert_eq!(dispatcher.current_priority("root"), None);
    assert_eq!(dispatcher.current_priority("child"), Some(5));
    assert!(dispatcher.doing_hook(Some("root")));
    assert!(!dispatcher.doing_hook(Some("child")));
}

#[test]
fn root_throwable_consumes_only_its_dispatch_ticket() {
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "root",
        10,
        DispatcherCallback::php(id("root"), 1, closure_handle(31)).unwrap(),
    );
    dispatcher.add(
        "child",
        5,
        DispatcherCallback::php(id("child"), 1, closure_handle(33)).unwrap(),
    );
    let root = dispatcher.begin_filter("root", value("root"), &[]).unwrap();
    assert!(matches!(
        dispatcher.advance(root),
        Ok(DispatchStep::InvokePhp(_))
    ));
    assert_eq!(dispatcher.current_priority("root"), Some(10));
    let child = dispatcher
        .begin_filter("child", value("child"), &[])
        .unwrap();
    assert!(matches!(
        dispatcher.advance(child),
        Ok(DispatchStep::InvokePhp(_))
    ));
    assert_eq!(dispatcher.current_priority("child"), Some(5));
    let child_thrown = throwable_handle(32);
    dispatcher
        .resume_php_completion(child, Completion::PhpThrowable(child_thrown))
        .unwrap();
    assert_eq!(
        dispatcher.advance(child),
        Ok(DispatchStep::PhpThrowable(child_thrown))
    );
    assert_eq!(dispatcher.current_hook(), Some("child"));
    assert_eq!(dispatcher.hook_stack_depth(), 2);
    assert!(dispatcher.doing_hook(Some("root")));
    assert!(dispatcher.doing_hook(Some("child")));
    assert_eq!(dispatcher.current_priority("root"), Some(10));
    assert_eq!(dispatcher.current_priority("child"), Some(5));
    let thrown = throwable_handle(34);
    dispatcher
        .resume_php_completion(root, Completion::PhpThrowable(thrown))
        .unwrap();
    assert_eq!(
        dispatcher.advance(root),
        Ok(DispatchStep::PhpThrowable(thrown))
    );
    assert_eq!(
        dispatcher.advance(root),
        Err(HookError::InvalidDispatchTicket)
    );
    assert_eq!(
        dispatcher.resume_php_completion(root, Completion::PhpThrowable(thrown)),
        Err(HookError::InvalidDispatchTicket)
    );
    assert_eq!(dispatcher.current_hook(), Some("child"));
    assert_eq!(dispatcher.hook_stack_depth(), 2);
    assert!(dispatcher.doing_hook(Some("root")));
    assert!(dispatcher.doing_hook(Some("child")));
    assert_eq!(dispatcher.current_priority("root"), Some(10));
    assert_eq!(dispatcher.current_priority("child"), Some(5));
}

#[test]
fn same_ticket_reentry_is_rejected_while_native_sync_nested_dispatch_still_works() {
    let ticket_cell = Arc::new(Mutex::new(None));
    let callback_ticket = Arc::clone(&ticket_cell);
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "outer",
        10,
        DispatcherCallback::new(id("outer"), 1, move |dispatcher, args| {
            let ticket = callback_ticket.lock().unwrap().expect("ticket installed");
            assert_eq!(
                dispatcher.advance(ticket),
                Err(HookError::DispatchTicketBusy)
            );
            assert_eq!(
                dispatcher.cancel(ticket),
                Err(HookError::DispatchTicketBusy)
            );
            assert_eq!(
                dispatcher.resume_php(ticket, value("wrong")),
                Err(HookError::DispatchTicketBusy)
            );
            assert_eq!(
                dispatcher.apply_filters("inner", args[0].clone(), &[]),
                Ok(value("outer"))
            );
            assert_eq!(dispatcher.current_hook(), Some("outer"));
            Ok(args[0].clone())
        }),
    );
    dispatcher.add(
        "inner",
        10,
        DispatcherCallback::new(id("inner"), 1, |_, args| Ok(args[0].clone())),
    );
    let ticket = dispatcher
        .begin_filter("outer", value("outer"), &[])
        .unwrap();
    *ticket_cell.lock().unwrap() = Some(ticket);
    assert_eq!(drive_native_ticket(&mut dispatcher, ticket), value("outer"));
    assert_eq!(dispatcher.did_filter("outer"), 1);
    assert_eq!(dispatcher.did_filter("inner"), 1);
    assert_eq!(dispatcher.current_hook(), None);
}

#[test]
fn resumable_dispatch_safely_skips_removed_all_binding_before_first_advance() {
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "all",
        10,
        DispatcherCallback::new(id("all"), 0, |_, _| Ok(value("ignored"))),
    );
    dispatcher.add("target", 10, suffix("target"));
    let ticket = dispatcher
        .begin_filter("target", value("start"), &[])
        .unwrap();
    dispatcher.remove_all("all", None);
    // Reuse the freed slot for another hook, then install a new all binding.
    dispatcher.add(
        "other",
        10,
        DispatcherCallback::new(id("other"), 0, |_, _| Ok(value("wrong"))),
    );
    let all_calls = Arc::new(Mutex::new(0));
    let seen = Arc::clone(&all_calls);
    dispatcher.add(
        "all",
        10,
        DispatcherCallback::new(id("new_all"), 0, move |_, _| {
            *seen.lock().unwrap() += 1;
            Ok(value("ignored"))
        }),
    );
    assert_eq!(
        drive_native_ticket(&mut dispatcher, ticket),
        value("starttarget")
    );
    assert_eq!(*all_calls.lock().unwrap(), 1);
    assert_eq!(
        dispatcher.apply_filters("other", value("other"), &[]),
        Ok(value("wrong"))
    );
    assert_eq!(dispatcher.current_hook(), None);
    assert_eq!(dispatcher.hook_stack_depth(), 0);
}

fn bytes_suffix(value: &PhpValue, suffix: &[u8]) -> PhpValue {
    let PhpValue::Bytes(mut output) = value.clone() else {
        panic!("expected bytes")
    };
    output.extend_from_slice(suffix);
    PhpValue::Bytes(output)
}

fn recv_invocation(
    callback: &str,
    hook: &str,
    priority: i64,
    input: &str,
    slot: u32,
) -> recastpress_wp::hooks::PhpInvocation {
    recastpress_wp::hooks::PhpInvocation {
        callback: id(callback),
        handle: closure_handle(slot),
        hook: HookScalar::Bytes(hook.as_bytes().to_vec()),
        priority: PhpKey::Integer(priority),
        call_mode: recastpress_wp::hooks::PhpCallMode::Array,
        args: vec![value(input)],
    }
}

#[test]
fn callable_control_remove_yields_final_release_before_cleanup_and_allows_reentry() {
    use recastpress_wp::dispatcher::{ControlResult, ControlStep};
    use recastpress_wp::hooks::ContextCallback;

    let mut dispatcher = Dispatcher::new();
    let callable = callable_handle(901);
    let mut add = dispatcher
        .begin_add(
            "callable-hook",
            10,
            DispatcherCallback::php_callable(id("object-callable"), 0, callable).unwrap(),
        )
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut add).unwrap(),
        ControlStep::Complete(ControlResult::Added(true))
    );
    let mut remove = dispatcher
        .begin_remove("callable-hook", 10, &id("object-callable"))
        .unwrap();
    assert!(!dispatcher.has_callbacks("callable-hook"));
    assert_eq!(
        dispatcher.advance_control(&mut remove).unwrap(),
        ControlStep::ReleasePhp(callable)
    );
    assert_eq!(
        dispatcher.advance_control(&mut remove),
        Err(HookError::PhpReleasePending)
    );
    assert_eq!(
        dispatcher.resume_control_release(&mut remove, callable_handle(999)),
        Err(HookError::InvalidPhpCallableHandle)
    );

    let replacement = callable_handle(902);
    let mut nested_add = dispatcher
        .begin_add(
            "callable-hook",
            10,
            ContextCallback::php_callable(id("replacement"), 1, replacement).unwrap(),
        )
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut nested_add).unwrap(),
        ControlStep::Complete(ControlResult::Added(true))
    );
    assert_eq!(
        dispatcher.resume_control_release(&mut remove, callable),
        Ok(())
    );
    assert_eq!(
        dispatcher.advance_control(&mut remove).unwrap(),
        ControlStep::Complete(ControlResult::Removed(true))
    );
    assert!(dispatcher.has_callbacks("callable-hook"));
}

#[test]
fn nested_control_cleanup_pins_storage_until_parent_resumes() {
    use recastpress_wp::dispatcher::{ControlResult, ControlStep};

    let mut dispatcher = Dispatcher::new();
    for (callback_id, slot) in [("outer", 905), ("inner", 906)] {
        let mut add = dispatcher
            .begin_add(
                "nested-removal",
                10,
                DispatcherCallback::php_callable(id(callback_id), 1, callable_handle(slot))
                    .unwrap(),
            )
            .unwrap();
        assert_eq!(
            dispatcher.advance_control(&mut add).unwrap(),
            ControlStep::Complete(ControlResult::Added(true))
        );
    }

    let mut outer = dispatcher
        .begin_remove("nested-removal", 10, &id("outer"))
        .unwrap();
    let outer_handle = callable_handle(905);
    assert_eq!(
        dispatcher.advance_control(&mut outer).unwrap(),
        ControlStep::ReleasePhp(outer_handle)
    );

    let mut inner = dispatcher
        .begin_remove("nested-removal", 10, &id("inner"))
        .unwrap();
    let inner_handle = callable_handle(906);
    assert_eq!(
        dispatcher.advance_control(&mut inner).unwrap(),
        ControlStep::ReleasePhp(inner_handle)
    );
    dispatcher
        .resume_control_release(&mut inner, inner_handle)
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut inner).unwrap(),
        ControlStep::Complete(ControlResult::Removed(true))
    );
    assert!(!dispatcher.has_callbacks("nested-removal"));

    dispatcher
        .resume_control_release(&mut outer, outer_handle)
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut outer).unwrap(),
        ControlStep::Complete(ControlResult::Removed(true))
    );
    assert!(!dispatcher.has_callbacks("nested-removal"));
}

#[test]
fn parent_control_cleanup_does_not_unbind_reentrant_replacement_slot() {
    use recastpress_wp::dispatcher::{ControlResult, ControlStep};

    let mut dispatcher = Dispatcher::new();
    for (callback_id, slot) in [("outer", 915), ("inner", 916)] {
        let mut add = dispatcher
            .begin_add(
                "rebound-hook",
                10,
                DispatcherCallback::php_callable(id(callback_id), 1, callable_handle(slot))
                    .unwrap(),
            )
            .unwrap();
        assert_eq!(
            dispatcher.advance_control(&mut add).unwrap(),
            ControlStep::Complete(ControlResult::Added(true))
        );
    }
    let mut outer = dispatcher
        .begin_remove("rebound-hook", 10, &id("outer"))
        .unwrap();
    let outer_handle = callable_handle(915);
    assert_eq!(
        dispatcher.advance_control(&mut outer).unwrap(),
        ControlStep::ReleasePhp(outer_handle)
    );
    let mut inner = dispatcher
        .begin_remove("rebound-hook", 10, &id("inner"))
        .unwrap();
    let inner_handle = callable_handle(916);
    assert_eq!(
        dispatcher.advance_control(&mut inner).unwrap(),
        ControlStep::ReleasePhp(inner_handle)
    );
    dispatcher
        .resume_control_release(&mut inner, inner_handle)
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut inner).unwrap(),
        ControlStep::Complete(ControlResult::Removed(true))
    );

    let replacement = callable_handle(917);
    let mut add = dispatcher
        .begin_add(
            "rebound-hook",
            10,
            DispatcherCallback::php_callable(id("replacement"), 1, replacement).unwrap(),
        )
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut add).unwrap(),
        ControlStep::Complete(ControlResult::Added(true))
    );
    dispatcher
        .resume_control_release(&mut outer, outer_handle)
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut outer).unwrap(),
        ControlStep::Complete(ControlResult::Removed(true))
    );
    assert!(dispatcher.has_at("rebound-hook", 10, &id("replacement")));
}

#[test]
fn callable_removed_during_invocation_releases_after_snapshot_finishes() {
    use recastpress_wp::dispatcher::{ControlResult, ControlStep};
    use recastpress_wp::hooks::PhpCallMode;

    let mut dispatcher = Dispatcher::new();
    let callable = callable_handle(911);
    let mut add = dispatcher
        .begin_add(
            "snapshot-hook",
            5,
            DispatcherCallback::php_callable(id("snapshot-callable"), 0, callable).unwrap(),
        )
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut add).unwrap(),
        ControlStep::Complete(ControlResult::Added(true))
    );
    let mut add_next = dispatcher
        .begin_add(
            "snapshot-hook",
            5,
            DispatcherCallback::php_callable(id("next-snapshot-callable"), 1, callable_handle(912))
                .unwrap(),
        )
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut add_next).unwrap(),
        ControlStep::Complete(ControlResult::Added(true))
    );
    let ticket = dispatcher
        .begin_filter("snapshot-hook", value("seed"), &[])
        .unwrap();
    let DispatchStep::InvokePhp(invocation) = dispatcher.advance(ticket).unwrap() else {
        panic!("expected PHP invocation")
    };
    assert_eq!(invocation.call_mode, PhpCallMode::Zero);
    assert_eq!(invocation.handle, callable);

    let mut remove = dispatcher
        .begin_remove("snapshot-hook", 5, &id("snapshot-callable"))
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut remove).unwrap(),
        ControlStep::Complete(ControlResult::Removed(true))
    );
    assert!(dispatcher.has_callbacks("snapshot-hook"));
    dispatcher.resume_php(ticket, value("returned")).unwrap();
    assert_eq!(
        dispatcher.advance(ticket).unwrap(),
        DispatchStep::ReleasePhp(callable)
    );
    assert_eq!(dispatcher.current_priority("snapshot-hook"), Some(5));
    assert_eq!(
        dispatcher.resume_php_release(ticket, callable_handle(999)),
        Err(HookError::InvalidPhpCallableHandle)
    );
    assert_eq!(
        dispatcher.advance(ticket),
        Err(HookError::PhpReleasePending)
    );
    dispatcher.resume_php_release(ticket, callable).unwrap();
    assert_eq!(
        dispatcher.resume_php_release(ticket, callable),
        Err(HookError::NoPhpReleasePending)
    );
    let DispatchStep::InvokePhp(next) = dispatcher.advance(ticket).unwrap() else {
        panic!("expected next snapshot callback")
    };
    assert_eq!(next.handle, callable_handle(912));
    dispatcher.resume_php(ticket, value("next-result")).unwrap();
    assert_eq!(
        dispatcher.advance(ticket).unwrap(),
        DispatchStep::Complete(value("next-result"))
    );
}

#[test]
fn callable_modes_preserve_wp_zero_negative_and_all_dispatch_rules() {
    use recastpress_wp::dispatcher::{ControlResult, ControlStep};
    use recastpress_wp::hooks::PhpCallMode;

    let mut dispatcher = Dispatcher::new();
    for (name, hook, accepted, slot) in [
        ("all-callback", "all", 0, 921),
        ("negative-callback", "modes", -1, 922),
    ] {
        let mut add = dispatcher
            .begin_add(
                hook,
                10,
                DispatcherCallback::php_callable(id(name), accepted, callable_handle(slot))
                    .unwrap(),
            )
            .unwrap();
        assert_eq!(
            dispatcher.advance_control(&mut add).unwrap(),
            ControlStep::Complete(ControlResult::Added(true))
        );
    }
    let ticket = dispatcher
        .begin_filter("modes", value("seed"), &[])
        .unwrap();
    let DispatchStep::InvokePhp(all) = dispatcher.advance(ticket).unwrap() else {
        panic!("expected all callback")
    };
    assert_eq!(all.handle, callable_handle(921));
    assert_eq!(all.call_mode, PhpCallMode::All);
    assert_eq!(all.args, [value("modes"), value("seed")]);
    dispatcher.resume_php(ticket, value("ignored")).unwrap();
    let DispatchStep::InvokePhp(named) = dispatcher.advance(ticket).unwrap() else {
        panic!("expected named callback")
    };
    assert_eq!(named.handle, callable_handle(922));
    assert_eq!(named.call_mode, PhpCallMode::Array);
    assert!(named.args.is_empty());
    dispatcher.resume_php(ticket, value("done")).unwrap();
    assert_eq!(
        dispatcher.advance(ticket).unwrap(),
        DispatchStep::Complete(value("done"))
    );
}

#[test]
fn callable_release_throwable_preserves_partial_remove_state() {
    use recastpress_wp::dispatcher::{ControlResult, ControlStep};

    let mut dispatcher = Dispatcher::new();
    let callable = callable_handle(931);
    let mut add = dispatcher
        .begin_add(
            "throwing-remove",
            5,
            DispatcherCallback::php_callable(id("throwing"), 1, callable).unwrap(),
        )
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut add).unwrap(),
        ControlStep::Complete(ControlResult::Added(true))
    );
    let mut remove = dispatcher
        .begin_remove("throwing-remove", 5, &id("throwing"))
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut remove).unwrap(),
        ControlStep::ReleasePhp(callable)
    );
    let thrown = throwable_handle(932);
    dispatcher
        .resume_control_release_completion(&mut remove, callable, Completion::PhpThrowable(thrown))
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut remove).unwrap(),
        ControlStep::PhpThrowable(thrown)
    );
    assert!(!dispatcher.has_callbacks("throwing-remove"));

    let mut add_again = dispatcher
        .begin_add(
            "throwing-remove",
            5,
            DispatcherCallback::php_callable(id("next"), 1, callable_handle(933)).unwrap(),
        )
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut add_again).unwrap(),
        ControlStep::Complete(ControlResult::Added(true))
    );
    assert!(dispatcher.has_callbacks("throwing-remove"));
}

#[test]
fn callable_release_throwable_abandons_dispatch_but_preserves_wp_frame_state() {
    use recastpress_wp::dispatcher::{ControlResult, ControlStep};

    let mut dispatcher = Dispatcher::new();
    let first = callable_handle(941);
    for (callback_id, handle) in [("first", first), ("second", callable_handle(942))] {
        let mut add = dispatcher
            .begin_add(
                "release-throw-hook",
                5,
                DispatcherCallback::php_callable(id(callback_id), 1, handle).unwrap(),
            )
            .unwrap();
        assert_eq!(
            dispatcher.advance_control(&mut add).unwrap(),
            ControlStep::Complete(ControlResult::Added(true))
        );
    }
    let ticket = dispatcher
        .begin_filter("release-throw-hook", value("seed"), &[])
        .unwrap();
    assert!(matches!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::InvokePhp(_))
    ));
    let mut remove = dispatcher
        .begin_remove("release-throw-hook", 5, &id("first"))
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut remove).unwrap(),
        ControlStep::Complete(ControlResult::Removed(true))
    );
    dispatcher.resume_php(ticket, value("returned")).unwrap();
    assert_eq!(
        dispatcher.advance(ticket).unwrap(),
        DispatchStep::ReleasePhp(first)
    );
    let thrown = throwable_handle(943);
    dispatcher
        .resume_php_release_completion(ticket, first, Completion::PhpThrowable(thrown))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket).unwrap(),
        DispatchStep::PhpThrowable(thrown)
    );
    assert_eq!(dispatcher.current_hook(), Some("release-throw-hook"));
    assert_eq!(dispatcher.hook_stack_depth(), 1);
    assert_eq!(dispatcher.current_priority("release-throw-hook"), Some(5));
    assert!(dispatcher.has_callbacks("release-throw-hook"));
}

#[test]
fn all_can_register_a_previously_absent_target_before_dispatch() {
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "all",
        10,
        DispatcherCallback::new(id("register"), 0, |dispatcher, _| {
            dispatcher.add("target", 10, suffix("new"));
            Ok(value("ignored"))
        }),
    );
    assert_eq!(
        dispatcher.apply_filters("target", value("value"), &[]),
        Ok(value("valuenew"))
    );
    assert!(dispatcher.remove("target", 10, &id("new")));
    assert!(!dispatcher.remove("target", 10, &id("new")));
    assert!(!dispatcher.has_callbacks("target"));
}

#[test]
fn recursion_limit_and_callback_failure_restore_request_stack() {
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "loop",
        10,
        DispatcherCallback::new(id("loop"), 1, |dispatcher, args| {
            dispatcher.apply_filters("loop", args[0].clone(), &[])
        }),
    );
    assert_eq!(
        dispatcher.apply_filters("loop", value("value"), &[]),
        Err(HookError::NestingLimit)
    );
    assert_eq!(dispatcher.current_hook(), None);
    dispatcher.remove_all("loop", None);
    dispatcher.add("loop", 10, suffix("recovered"));
    assert_eq!(
        dispatcher.apply_filters("loop", value("value"), &[]),
        Ok(value("valuerecovered"))
    );
}

#[test]
fn synchronous_php_yield_error_cleans_nested_dispatch_but_preserves_parent() {
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "outer",
        10,
        DispatcherCallback::new(id("outer"), 1, |dispatcher, args| {
            assert_eq!(
                dispatcher.apply_filters("php_inner", args[0].clone(), &[]),
                Err(HookError::PhpCallbackRequiresContinuation)
            );
            assert_eq!(dispatcher.current_hook(), Some("outer"));
            assert_eq!(dispatcher.hook_stack_depth(), 1);
            Ok(args[0].clone())
        }),
    );
    dispatcher.add(
        "php_inner",
        10,
        DispatcherCallback::php(id("php"), 1, closure_handle(8)).unwrap(),
    );
    assert_eq!(
        dispatcher.apply_filters("outer", value("ok"), &[]),
        Ok(value("ok"))
    );
    assert_eq!(dispatcher.current_hook(), None);
    assert_eq!(dispatcher.hook_stack_depth(), 0);
}

#[test]
fn all_priority_mutation_reports_reference_warning_before_object_collection() {
    use recastpress_wp::hooks::HookDiagnostic;
    let mut dispatcher = Dispatcher::new();
    dispatcher.add(
        "all",
        10,
        DispatcherCallback::new(id("mutate_all"), 0, |dispatcher, _| {
            dispatcher.add("all", 0, suffix("new"));
            dispatcher.remove_all("all", None);
            Ok(value("ignored"))
        }),
    );
    assert_eq!(
        dispatcher.apply_filters("alpha", value("value"), &[]),
        Ok(value("value"))
    );
    assert_eq!(
        dispatcher.take_diagnostics(),
        vec![HookDiagnostic::UndefinedCurrentPriority { iteration: 0 }]
    );
    assert!(dispatcher.take_diagnostics().is_empty());
}

#[test]
fn filter_counter_operand_reads_null_after_sole_guard_is_invalidated() {
    let mut dispatcher = Dispatcher::new();
    dispatcher
        .apply_filters("target", value("setup"), &[])
        .unwrap();
    let target = HookArgument::bytes("target");

    let mut operand = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Isset)
        .unwrap();
    dispatcher
        .apply_filters("other", value("nested"), &[])
        .unwrap();
    dispatcher
        .resume_filter_counter_operand(&mut operand, &target)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut operand),
        ControlResult::FilterCounter(FilterCounterResult::Lookup(
            FilterCounterLookup::NullOperand
        ))
    );
    assert_eq!(dispatcher.did_filter("target"), 1);

    let missing = HookArgument::bytes("never-counted");
    let mut missing_read = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Read)
        .unwrap();
    dispatcher
        .resume_filter_counter_operand(&mut missing_read, &missing)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut missing_read),
        ControlResult::FilterCounter(FilterCounterResult::Lookup(FilterCounterLookup::Missing))
    );
    assert_eq!(dispatcher.did_filter("other"), 1);

    let mut read = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Read)
        .unwrap();
    dispatcher
        .apply_filters("third", value("nested"), &[])
        .unwrap();
    dispatcher
        .resume_filter_counter_operand(&mut read, &target)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut read),
        ControlResult::FilterCounter(FilterCounterResult::Lookup(
            FilterCounterLookup::NullOperand
        ))
    );
    assert_eq!(dispatcher.did_filter("target"), 1);
}

#[test]
fn filter_counter_stale_increment_and_write_are_discarded_from_live_table() {
    let mut dispatcher = Dispatcher::new();
    dispatcher
        .apply_filters("target", value("setup"), &[])
        .unwrap();
    let target = HookArgument::bytes("target");

    let mut increment = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Increment)
        .unwrap();
    dispatcher
        .apply_filters("other", value("nested"), &[])
        .unwrap();
    dispatcher
        .resume_filter_counter_operand(&mut increment, &target)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut increment),
        ControlResult::FilterCounter(FilterCounterResult::Mutation(
            FilterCounterMutation::Discarded
        ))
    );
    assert_eq!(dispatcher.did_filter("target"), 1);
    assert_eq!(dispatcher.did_filter("other"), 1);

    let missing = HookArgument::bytes("missing");
    let mut isset = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Isset)
        .unwrap();
    dispatcher
        .resume_filter_counter_operand(&mut isset, &missing)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut isset),
        ControlResult::FilterCounter(FilterCounterResult::Lookup(FilterCounterLookup::Missing))
    );

    let mut write = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::WriteOne)
        .unwrap();
    dispatcher
        .apply_filters("other", value("nested again"), &[])
        .unwrap();
    dispatcher
        .resume_filter_counter_operand(&mut write, &missing)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut write),
        ControlResult::FilterCounter(FilterCounterResult::Mutation(
            FilterCounterMutation::Discarded
        ))
    );
    assert_eq!(dispatcher.did_filter("missing"), 0);
    assert_eq!(dispatcher.did_filter("other"), 2);
}

#[test]
fn nested_filter_counter_operands_keep_the_old_version_until_the_outer_cursor_retires() {
    let mut dispatcher = Dispatcher::new();
    dispatcher
        .apply_filters("target", value("setup"), &[])
        .unwrap();
    let target = HookArgument::bytes("target");

    let mut outer = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Isset)
        .unwrap();
    let mut inner = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Isset)
        .unwrap();
    assert_eq!(
        dispatcher.resume_filter_counter_operand(&mut outer, &target),
        Err(HookError::ControlOperationOrder)
    );
    dispatcher
        .apply_filters("other", value("nested"), &[])
        .unwrap();
    dispatcher
        .resume_filter_counter_operand(&mut inner, &target)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut inner),
        ControlResult::FilterCounter(FilterCounterResult::Lookup(FilterCounterLookup::Value(1)))
    );
    dispatcher
        .resume_filter_counter_operand(&mut outer, &target)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut outer),
        ControlResult::FilterCounter(FilterCounterResult::Lookup(
            FilterCounterLookup::NullOperand
        ))
    );
    assert_eq!(dispatcher.did_filter("target"), 1);
    assert_eq!(dispatcher.did_filter("other"), 1);
}

#[test]
fn stale_counter_increment_does_not_change_an_old_version_kept_by_nested_reads() {
    let mut dispatcher = Dispatcher::new();
    dispatcher
        .apply_filters("target", value("setup"), &[])
        .unwrap();
    let target = HookArgument::bytes("target");

    let mut outer_read = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Read)
        .unwrap();
    let mut middle_read = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Read)
        .unwrap();
    let mut inner_increment = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Increment)
        .unwrap();
    dispatcher
        .apply_filters("other", value("nested write"), &[])
        .unwrap();
    dispatcher
        .resume_filter_counter_operand(&mut inner_increment, &target)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut inner_increment),
        ControlResult::FilterCounter(FilterCounterResult::Mutation(
            FilterCounterMutation::Discarded
        ))
    );

    dispatcher
        .resume_filter_counter_operand(&mut middle_read, &target)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut middle_read),
        ControlResult::FilterCounter(FilterCounterResult::Lookup(FilterCounterLookup::Value(1)))
    );
    dispatcher
        .resume_filter_counter_operand(&mut outer_read, &target)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut outer_read),
        ControlResult::FilterCounter(FilterCounterResult::Lookup(
            FilterCounterLookup::NullOperand
        ))
    );
    assert_eq!(dispatcher.did_filter("target"), 1);
    assert_eq!(dispatcher.did_filter("other"), 1);
}

#[test]
fn filter_counter_boundary_throwable_cancels_cursor_and_rejects_replay_or_foreign_use() {
    let mut dispatcher = Dispatcher::new();
    let mut foreign = Dispatcher::new();
    let name = HookArgument::bytes("target");
    let mut operand = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Isset)
        .unwrap();
    assert_eq!(
        foreign.resume_filter_counter_operand(&mut operand, &name),
        Err(HookError::InvalidControlOperation)
    );
    let thrown = throwable_handle(91);
    assert_eq!(
        dispatcher.cancel_filter_counter_operand(&mut operand, closure_handle(92)),
        Err(HookError::InvalidPhpThrowableHandle)
    );
    dispatcher
        .cancel_filter_counter_operand(&mut operand, thrown)
        .unwrap();
    assert_eq!(
        dispatcher.advance_control(&mut operand),
        Ok(ControlStep::PhpThrowable(thrown))
    );
    assert_eq!(
        dispatcher.resume_filter_counter_operand(&mut operand, &name),
        Err(HookError::InvalidControlOperation)
    );
    let mut next = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Isset)
        .unwrap();
    dispatcher
        .resume_filter_counter_operand(&mut next, &name)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut next),
        ControlResult::FilterCounter(FilterCounterResult::Lookup(FilterCounterLookup::Missing))
    );
}

#[test]
fn counted_effectful_dispatch_does_not_increment_counter_twice() {
    let mut dispatcher = Dispatcher::new();
    let name = HookArgument::bytes("target");
    let mut isset = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::Isset)
        .unwrap();
    dispatcher
        .resume_filter_counter_operand(&mut isset, &name)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut isset),
        ControlResult::FilterCounter(FilterCounterResult::Lookup(FilterCounterLookup::Missing))
    );
    let mut write = dispatcher
        .begin_filter_counter_operand(FilterCounterOperandAccess::WriteOne)
        .unwrap();
    dispatcher
        .resume_filter_counter_operand(&mut write, &name)
        .unwrap();
    assert_eq!(
        finish_control(&mut dispatcher, &mut write),
        ControlResult::FilterCounter(FilterCounterResult::Mutation(
            FilterCounterMutation::Applied
        ))
    );
    let ticket = dispatcher
        .begin_filter_after_counter_typed(&name, value("counted"), &[])
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Boundary(DispatchBoundary::NamedHookIsset))
    );
    dispatcher
        .resume_boundary(ticket, Completion::Value(PhpValue::Null))
        .unwrap();
    assert_eq!(
        dispatcher.advance(ticket),
        Ok(DispatchStep::Complete(value("counted")))
    );
    assert_eq!(dispatcher.did_filter("target"), 1);
}
