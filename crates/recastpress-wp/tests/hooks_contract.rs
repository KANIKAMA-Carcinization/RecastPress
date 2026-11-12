use recastpress_wp::{
    continuation::{PhpHandle, PhpHandleKind, RequestGeneration},
    hooks::{Callback, CallbackId, Hook, HookError},
    value::{PhpArray, PhpKey, PhpValue},
};
use std::sync::{Arc, Mutex};

#[test]
fn filters_pass_null_false_and_array_results_without_string_conversion() {
    let mut hook = Hook::new();
    hook.add(0, Callback::new(id("null"), 1, |_, _| Ok(PhpValue::Null)));
    hook.add(
        10,
        Callback::new(id("false"), 1, |_, args| {
            assert_eq!(args, [PhpValue::Null]);
            Ok(PhpValue::Bool(false))
        }),
    );
    hook.add(
        20,
        Callback::new(id("array"), 1, |_, args| {
            assert_eq!(args, [PhpValue::Bool(false)]);
            let mut array = PhpArray::new();
            array.insert(PhpKey::Integer(-2), PhpValue::Bytes(b"\0\xff".to_vec()));
            Ok(PhpValue::Array(array))
        }),
    );
    let mut expected = PhpArray::new();
    expected.insert(PhpKey::Integer(-2), PhpValue::Bytes(b"\0\xff".to_vec()));
    assert_eq!(
        hook.apply_filters(PhpValue::Integer(i64::MIN), &[])
            .unwrap(),
        PhpValue::Array(expected)
    );
    assert_eq!(hook.nesting_depth(), 0);
}

#[test]
fn action_array_return_does_not_replace_original_callback_arguments() {
    let mut original = PhpArray::new();
    original.insert(PhpKey::from_bytes(b"x"), PhpValue::Null);
    let original = PhpValue::Array(original);
    let expected = original.clone();
    let mut hook = Hook::new();
    hook.add(
        0,
        Callback::new(id("change"), 1, |_, args| {
            let PhpValue::Array(mut result) = args[0].clone() else {
                panic!("expected array argument")
            };
            result.insert(PhpKey::from_bytes(b"x"), PhpValue::Bool(true));
            Ok(PhpValue::Array(result))
        }),
    );
    hook.add(
        10,
        Callback::new(id("observe"), 2, move |_, args| {
            assert_eq!(args, [expected.clone(), PhpValue::Null]);
            Ok(PhpValue::Integer(1))
        }),
    );
    hook.do_action(&[original.clone(), PhpValue::Null]).unwrap();
    assert!(
        matches!(original,PhpValue::Array(ref array) if array.get(&PhpKey::from_bytes(b"x"))==Some(&PhpValue::Null))
    );
    assert_eq!(hook.nesting_depth(), 0);
}

fn id(name: &str) -> CallbackId {
    CallbackId {
        module: "fixture".into(),
        callback: name.into(),
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
fn value(value: &str) -> PhpValue {
    PhpValue::Bytes(value.as_bytes().to_vec())
}
fn suffix(name: &str, accepted: i64) -> Callback {
    let name = name.to_string();
    Callback::new(id(&name), accepted, move |_, args| {
        let mut body = match args.first() {
            Some(PhpValue::Bytes(bytes)) => bytes.clone(),
            _ => Vec::new(),
        };
        body.extend_from_slice(name.as_bytes());
        Ok(PhpValue::Bytes(body))
    })
}

#[test]
fn ordering_replacement_zero_priority_and_removal() {
    let mut hook = Hook::new();
    assert_eq!(hook.apply_filters(value("start"), &[]), Ok(value("start")));
    hook.add(10, suffix("a", 1));
    hook.add(0, suffix("zero", 1));
    hook.add(10, suffix("b", 1));
    hook.add(10, suffix("a", 1));
    assert_eq!(hook.priority_of(&id("zero")), Some(0));
    assert_eq!(hook.apply_filters(value(""), &[]), Ok(value("zeroab")));
    assert!(hook.remove(10, &id("a")));
    assert!(!hook.remove(10, &id("a")));
    assert!(hook.has_at(10, &id("b")));
    hook.remove_all(Some(10));
    assert_eq!(hook.apply_filters(value(""), &[]), Ok(value("zero")));
    hook.remove_all(None);
    assert!(!hook.has_callbacks());
}

#[test]
fn accepted_arguments_support_zero_negative_and_oversize_counts() {
    for (accepted, expected) in [
        (0, 0),
        (1, 1),
        (2, 2),
        (99, 3),
        (-1, 2),
        (-99, 0),
        (i64::MIN, 0),
    ] {
        let mut hook = Hook::new();
        hook.add(
            10,
            Callback::new(id("args"), accepted, move |_, args| {
                assert_eq!(args.len(), expected);
                Ok(value("ok"))
            }),
        );
        assert_eq!(
            hook.apply_filters(value("value"), &[value("extra1"), value("extra2")]),
            Ok(value("ok"))
        );
    }
}

#[test]
fn current_bucket_snapshot_and_future_priority_changes() {
    let mut hook = Hook::new();
    hook.add(
        10,
        Callback::new(id("mutate"), 1, |hook, args| {
            hook.remove(10, &id("same"));
            hook.remove(20, &id("removed"));
            hook.add(10, suffix("new_same", 1));
            hook.add(15, suffix("new_future", 1));
            hook.add(5, suffix("new_past", 1));
            Ok(args[0].clone())
        }),
    );
    hook.add(10, suffix("same", 1));
    hook.add(20, suffix("removed", 1));
    assert_eq!(
        hook.apply_filters(value(""), &[]),
        Ok(value("samenew_future"))
    );
    assert_eq!(hook.current_priority(), None);
}

#[test]
fn actions_ignore_return_values_when_passing_original_arguments() {
    let mut hook = Hook::new();
    let seen = Arc::new(Mutex::new(Vec::new()));
    for name in ["first", "second"] {
        let seen = Arc::clone(&seen);
        hook.add(
            10,
            Callback::new(id(name), 1, move |_, args| {
                seen.lock().unwrap().push(args[0].clone());
                Ok(value("changed"))
            }),
        );
    }
    hook.do_action(&[value("original")]).unwrap();
    assert_eq!(
        *seen.lock().unwrap(),
        vec![value("original"), value("original")]
    );
    assert_eq!(
        hook.apply_filters(value("after"), &[]),
        Ok(value("changed"))
    );
}

#[test]
fn recursive_filter_restores_outer_priority_and_cleans_up_callback_errors() {
    let mut hook = Hook::new();
    hook.add(
        10,
        Callback::new(id("nested"), 1, |hook, args| {
            assert_eq!(hook.current_priority(), Some(10));
            if hook.nesting_depth() == 1 {
                assert_eq!(
                    hook.apply_filters(value("nested"), &[]),
                    Ok(value("nestedtail"))
                );
                assert_eq!(hook.current_priority(), Some(10));
            }
            Ok(args[0].clone())
        }),
    );
    hook.add(20, suffix("tail", 1));
    assert_eq!(
        hook.apply_filters(value("outer"), &[]),
        Ok(value("outertail"))
    );
    hook.remove(10, &id("nested"));
    hook.add(
        30,
        Callback::new(id("fail"), 1, |_, _| {
            Err(HookError::CallbackFailed("fixture".into()))
        }),
    );
    assert!(hook.apply_filters(value("error"), &[]).is_err());
    assert_eq!(hook.nesting_depth(), 0);
    assert_eq!(hook.current_priority(), None);
}

#[test]
fn recursion_limit_is_bounded_and_restores_request_state() {
    let mut hook = Hook::new();
    hook.add(
        10,
        Callback::new(id("recurse"), 1, |hook, args| {
            hook.apply_filters(args[0].clone(), &[])
        }),
    );
    assert_eq!(
        hook.apply_filters(value("value"), &[]),
        Err(HookError::NestingLimit)
    );
    assert_eq!(hook.nesting_depth(), 0);
}

#[test]
fn php_callback_handle_kind_is_explicit_and_sync_hook_cleans_up_on_yield() {
    let wrong_kind = PhpHandle::new(RequestGeneration::new(51), PhpHandleKind::Object, 1, 1, 1);
    assert!(matches!(
        Callback::php(id("wrong_kind"), 1, wrong_kind),
        Err(HookError::InvalidPhpCallbackHandle)
    ));

    let mut hook = Hook::new();
    hook.add(10, Callback::php(id("php"), 1, closure_handle(1)).unwrap());
    assert_eq!(
        hook.apply_filters(value("start"), &[]),
        Err(HookError::PhpCallbackRequiresContinuation)
    );
    assert_eq!(hook.nesting_depth(), 0);
    assert_eq!(hook.current_priority(), None);
}
