use recastpress_wp::continuation::{
    Completion, Continuation, ContinuationError as E, LimitsProfile, PhpHandle, PhpHandleKind,
    RequestGeneration, RequestState,
};

fn machine(limits: LimitsProfile) -> Continuation<&'static str> {
    Continuation::new(RequestGeneration::new(7), limits).unwrap()
}

#[test]
fn nested_php_effect_callcore_returns_then_resumes_outer_in_order() {
    let mut c = machine(LimitsProfile::default());
    let root = c.start_root().unwrap();
    let outer_effect = c.invoke_php(root).unwrap();
    let child = c.begin_child(outer_effect).unwrap();
    let child_effect = c.invoke_php(child).unwrap();
    assert_eq!(
        c.resume(child_effect, Completion::Value("filtered option")),
        Ok(Completion::Value("filtered option"))
    );
    assert_eq!(c.finish_operation(child).unwrap(), Some(outer_effect));
    assert_eq!(
        c.resume(outer_effect, Completion::Value("callback result")),
        Ok(Completion::Value("callback result"))
    );
    assert_eq!(c.finish_operation(root).unwrap(), None);
    c.finish_request(root).unwrap();
    assert_eq!(c.state(), &RequestState::Finished);
}

#[test]
fn rejects_forged_order_duplicate_resume_and_wrong_parent() {
    let mut c = machine(LimitsProfile::default());
    let root = c.start_root().unwrap();
    let e1 = c.invoke_php(root).unwrap();
    let child = c.begin_child(e1).unwrap();
    let e2 = c.invoke_php(child).unwrap();
    assert_eq!(
        c.resume(e1, Completion::Value("out of order")),
        Err(E::ParentNotActive)
    );
    assert_eq!(
        c.resume(e2, Completion::Value("child done")),
        Ok(Completion::Value("child done"))
    );
    c.finish_operation(child).unwrap();
    assert_eq!(
        c.resume(e1, Completion::Value("parent done")),
        Ok(Completion::Value("parent done"))
    );
    assert_eq!(
        c.resume(e1, Completion::Value("twice")),
        Err(E::EffectAlreadyResumed)
    );
    assert_eq!(c.begin_child(e2), Err(E::EffectAlreadyResumed));
}

#[test]
fn abort_and_limits_are_terminal_and_finite() {
    let mut c = machine(LimitsProfile {
        max_depth: 1,
        max_operations: 1,
        max_handles: 1,
    });
    let root = c.start_root().unwrap();
    let effect = c.invoke_php(root).unwrap();
    assert_eq!(c.begin_child(effect), Err(E::DepthLimit));
    assert_eq!(c.start_root(), Err(E::AlreadyStarted));
    c.abort();
    assert_eq!(c.state(), &RequestState::Aborted);
    assert_eq!(
        c.resume(effect, Completion::Value("late")),
        Err(E::RequestClosed)
    );
    assert_eq!(c.finish_request(root), Err(E::RequestClosed));
}

#[test]
fn php_handles_are_request_scoped_and_bounded() {
    let generation = RequestGeneration::new(7);
    let mut c = machine(LimitsProfile {
        max_depth: 4,
        max_operations: 4,
        max_handles: 1,
    });
    let h = PhpHandle::new(generation, PhpHandleKind::Throwable, 2, 3, 99);
    c.register_handle(h).unwrap();
    c.register_handle(h).unwrap();
    assert_eq!(
        c.register_handle(PhpHandle::new(
            generation,
            PhpHandleKind::Throwable,
            4,
            1,
            100
        )),
        Err(E::HandleLimit)
    );
    assert_eq!(
        c.register_handle(PhpHandle::new(
            RequestGeneration::new(8),
            PhpHandleKind::Object,
            0,
            1,
            101
        )),
        Err(E::WrongRequest)
    );
    let root = c.start_root().unwrap();
    let effect = c.invoke_php(root).unwrap();
    assert_eq!(
        c.resume(
            effect,
            Completion::PhpThrowable(PhpHandle::new(generation, PhpHandleKind::Object, 9, 1, 102))
        ),
        Err(E::WrongHandleKind)
    );
    assert_eq!(c.finish_operation(root), Err(E::PendingEffect));
    assert_eq!(
        c.resume(effect, Completion::PhpThrowable(h)),
        Ok(Completion::PhpThrowable(h))
    );
    c.finish_operation(root).unwrap();
    c.finish_request(root).unwrap();
    assert_eq!(c.validate_handle(h), Err(E::RequestClosed));
}

#[test]
fn request_finish_requires_root_completion_and_has_distinct_shutdown_boundary() {
    let mut c = machine(LimitsProfile::default());
    let root = c.start_root().unwrap();
    assert_eq!(c.finish_request(root), Err(E::PendingEffect));
    let e = c.invoke_php(root).unwrap();
    assert_eq!(
        c.resume(e, Completion::Value("done")),
        Ok(Completion::Value("done"))
    );
    c.finish_operation(root).unwrap();
    assert_eq!(c.state(), &RequestState::RootReturned);
    // Root Return is not RequestFinished: PHP shutdown/output may still re-enter.
    let shutdown_effect = c.invoke_php(root).unwrap();
    assert_eq!(
        c.resume(shutdown_effect, Completion::Value("shutdown complete")),
        Ok(Completion::Value("shutdown complete"))
    );
    c.finish_operation(root).unwrap();
    c.finish_request(root).unwrap();
    assert_eq!(c.start_root(), Err(E::RequestClosed));
}

#[test]
fn ids_from_another_continuation_instance_are_not_accepted() {
    let generation = RequestGeneration::new(7);
    let mut a = Continuation::<()>::new(generation, LimitsProfile::default()).unwrap();
    let mut b = Continuation::<()>::new(generation, LimitsProfile::default()).unwrap();
    let foreign_root = a.start_root().unwrap();
    let local_root = b.start_root().unwrap();
    assert_ne!(foreign_root, local_root);
    assert_eq!(b.invoke_php(foreign_root), Err(E::WrongOperation));
}

#[test]
fn rejected_depth_does_not_leave_the_parent_suspended() {
    let mut c = machine(LimitsProfile {
        max_depth: 1,
        max_operations: 4,
        max_handles: 0,
    });
    let root = c.start_root().unwrap();
    let effect = c.invoke_php(root).unwrap();
    assert_eq!(c.begin_child(effect), Err(E::DepthLimit));
    assert_eq!(c.depth(), 1);
    assert_eq!(
        c.resume(effect, Completion::Value("parent still active")),
        Ok(Completion::Value("parent still active"))
    );
    c.finish_operation(root).unwrap();
    c.finish_request(root).unwrap();
}

#[test]
fn sequential_php_effects_cannot_bypass_the_request_limit() {
    let mut c = machine(LimitsProfile {
        max_depth: 2,
        max_operations: 2,
        max_handles: 0,
    });
    let root = c.start_root().unwrap();
    for _ in 0..2 {
        let effect = c.invoke_php(root).unwrap();
        assert_eq!(
            c.resume(effect, Completion::Value("done")),
            Ok(Completion::Value("done"))
        );
    }
    assert_eq!(c.invoke_php(root), Err(E::OperationLimit));
    c.finish_operation(root).unwrap();
    c.finish_request(root).unwrap();
}

#[test]
fn registered_handle_requires_the_exact_token_slot_generation_and_kind() {
    let mut c = machine(LimitsProfile::default());
    let request = RequestGeneration::new(7);
    let actual = PhpHandle::new(request, PhpHandleKind::Object, 2, 3, 99);
    assert_eq!(actual.slot(), 2);
    assert_eq!(actual.generation(), 3);
    assert_eq!(actual.token(), 99);
    c.register_handle(actual).unwrap();
    for invalid in [
        PhpHandle::new(request, PhpHandleKind::Object, 2, 3, 100),
        PhpHandle::new(request, PhpHandleKind::Object, 2, 4, 99),
        PhpHandle::new(request, PhpHandleKind::Object, 3, 3, 99),
        PhpHandle::new(request, PhpHandleKind::ReferenceCell, 2, 3, 99),
    ] {
        assert_eq!(c.validate_handle(invalid), Err(E::InvalidHandle));
    }
    assert_eq!(c.validate_handle(actual), Ok(()));
}

#[test]
fn abort_after_root_return_revokes_handles_and_prevents_request_finish() {
    let mut c = machine(LimitsProfile::default());
    let handle = PhpHandle::new(RequestGeneration::new(7), PhpHandleKind::Object, 1, 1, 99);
    c.register_handle(handle).unwrap();
    let root = c.start_root().unwrap();
    c.finish_operation(root).unwrap();
    assert_eq!(c.state(), &RequestState::RootReturned);
    c.abort();
    assert_eq!(c.validate_handle(handle), Err(E::RequestClosed));
    assert_eq!(c.finish_request(root), Err(E::RequestClosed));
    assert_eq!(c.depth(), 0);
}

#[test]
fn unregister_revokes_only_the_exact_handle_and_preserves_state_on_failure() {
    let request = RequestGeneration::new(7);
    let mut c = machine(LimitsProfile {
        max_depth: 2,
        max_operations: 2,
        max_handles: 2,
    });
    let handle = PhpHandle::new(request, PhpHandleKind::Object, 4, 8, 31);
    let other = PhpHandle::new(request, PhpHandleKind::Object, 5, 1, 32);
    c.register_handle(handle).unwrap();
    c.register_handle(other).unwrap();
    let root = c.start_root().unwrap();
    let effect = c.invoke_php(root).unwrap();

    assert_eq!(
        c.unregister_handle(PhpHandle::new(request, PhpHandleKind::Object, 4, 8, 99)),
        Err(E::InvalidHandle)
    );
    assert_eq!(
        c.unregister_handle(PhpHandle::new(
            RequestGeneration::new(8),
            PhpHandleKind::Object,
            4,
            8,
            31
        )),
        Err(E::WrongRequest)
    );
    assert_eq!(c.validate_handle(handle), Ok(()));
    assert_eq!(c.validate_handle(other), Ok(()));
    assert_eq!(
        c.resume(effect, Completion::Value("still pending")),
        Ok(Completion::Value("still pending"))
    );

    c.unregister_handle(handle).unwrap();
    assert_eq!(c.validate_handle(handle), Err(E::InvalidHandle));
    assert_eq!(c.validate_handle(other), Ok(()));
    assert_eq!(c.unregister_handle(handle), Err(E::InvalidHandle));
    c.finish_operation(root).unwrap();
    c.finish_request(root).unwrap();
    assert_eq!(c.unregister_handle(other), Err(E::RequestClosed));
}

#[test]
fn stale_throwable_rejection_keeps_effect_pending_for_a_valid_handle() {
    let request = RequestGeneration::new(7);
    let mut c = machine(LimitsProfile::default());
    let stale = PhpHandle::new(request, PhpHandleKind::Throwable, 6, 2, 41);
    let valid = PhpHandle::new(request, PhpHandleKind::Throwable, 6, 3, 42);
    c.register_handle(stale).unwrap();
    c.register_handle(valid).unwrap();
    let root = c.start_root().unwrap();
    let effect = c.invoke_php(root).unwrap();
    c.unregister_handle(stale).unwrap();

    assert_eq!(
        c.resume(effect, Completion::PhpThrowable(stale)),
        Err(E::InvalidHandle)
    );
    assert_eq!(
        c.resume(effect, Completion::PhpThrowable(valid)),
        Ok(Completion::PhpThrowable(valid))
    );
    c.finish_operation(root).unwrap();
    c.finish_request(root).unwrap();
}

#[test]
fn slot_reuse_with_new_generation_and_token_does_not_revive_unregistered_handle() {
    let request = RequestGeneration::new(7);
    let mut c = machine(LimitsProfile::default());
    let old = PhpHandle::new(request, PhpHandleKind::Object, 11, 4, 50);
    let replacement = PhpHandle::new(request, PhpHandleKind::Object, 11, 5, 51);
    c.register_handle(old).unwrap();
    c.unregister_handle(old).unwrap();
    c.register_handle(replacement).unwrap();

    assert_eq!(c.validate_handle(old), Err(E::InvalidHandle));
    assert_eq!(c.validate_handle(replacement), Ok(()));
}

#[test]
fn root_returned_handles_remain_live_until_request_finish() {
    let mut c = machine(LimitsProfile::default());
    let handle = PhpHandle::new(RequestGeneration::new(7), PhpHandleKind::Object, 9, 2, 71);
    c.register_handle(handle).unwrap();
    let root = c.start_root().unwrap();
    c.finish_operation(root).unwrap();
    assert_eq!(c.validate_handle(handle), Ok(()));
    c.finish_request(root).unwrap();
    assert_eq!(c.validate_handle(handle), Err(E::RequestClosed));
    assert_eq!(c.unregister_handle(handle), Err(E::RequestClosed));
}
