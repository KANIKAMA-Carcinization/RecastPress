use recastpress_wp::{
    continuation::{PhpHandle, PhpHandleKind, RequestGeneration},
    hooks::{Callback, CallbackId, Hook, HookError, HookPriority, HookScalar},
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
fn typed_priority_numeric_sort_matches_php_stability_and_integer_precision() {
    fn add_trace(
        hook: &mut Hook,
        key: HookPriority,
        label: &'static str,
        trace: Arc<Mutex<Vec<&'static str>>>,
    ) {
        hook.add_typed(
            key,
            Callback::new(id(label), 1, move |_, args| {
                trace.lock().unwrap().push(label);
                Ok(args[0].clone())
            }),
        );
    }

    let trace = Arc::new(Mutex::new(Vec::new()));
    let mut numeric_strings = Hook::new();
    add_trace(
        &mut numeric_strings,
        HookPriority::new(
            HookScalar::Bytes(b"abc".to_vec()),
            PhpKey::Bytes(b"abc".to_vec()),
        ),
        "abc",
        Arc::clone(&trace),
    );
    add_trace(
        &mut numeric_strings,
        HookPriority::integer(0),
        "zero",
        Arc::clone(&trace),
    );
    add_trace(
        &mut numeric_strings,
        HookPriority::new(
            HookScalar::Bytes(b"1e2".to_vec()),
            PhpKey::Bytes(b"1e2".to_vec()),
        ),
        "exp100",
        Arc::clone(&trace),
    );
    add_trace(
        &mut numeric_strings,
        HookPriority::integer(100),
        "int100",
        Arc::clone(&trace),
    );
    numeric_strings
        .apply_filters(PhpValue::Bytes(b"seed".to_vec()), &[])
        .unwrap();
    assert_eq!(*trace.lock().unwrap(), ["abc", "zero", "exp100", "int100"]);

    let mut large_tie = Hook::new();
    let large_tie_trace = Arc::new(Mutex::new(Vec::new()));
    add_trace(
        &mut large_tie,
        HookPriority::integer(9_007_199_254_740_993),
        "int_plus_one",
        Arc::clone(&large_tie_trace),
    );
    add_trace(
        &mut large_tie,
        HookPriority::new(
            HookScalar::Bytes(b"+9007199254740993".to_vec()),
            PhpKey::Bytes(b"+9007199254740993".to_vec()),
        ),
        "plus_string",
        Arc::clone(&large_tie_trace),
    );
    large_tie
        .apply_filters(PhpValue::Bytes(b"seed".to_vec()), &[])
        .unwrap();
    assert_eq!(
        *large_tie_trace.lock().unwrap(),
        ["int_plus_one", "plus_string"]
    );

    let mut exact_integers = Hook::new();
    let integer_trace = Arc::new(Mutex::new(Vec::new()));
    add_trace(
        &mut exact_integers,
        HookPriority::integer(9_007_199_254_740_993),
        "int_plus_one",
        Arc::clone(&integer_trace),
    );
    add_trace(
        &mut exact_integers,
        HookPriority::integer(9_007_199_254_740_992),
        "base",
        Arc::clone(&integer_trace),
    );
    exact_integers
        .apply_filters(PhpValue::Bytes(b"seed".to_vec()), &[])
        .unwrap();
    assert_eq!(*integer_trace.lock().unwrap(), ["base", "int_plus_one"]);

    let mut prefix = Hook::new();
    let prefix_trace = Arc::new(Mutex::new(Vec::new()));
    add_trace(
        &mut prefix,
        HookPriority::new(
            HookScalar::Bytes(b"12foo".to_vec()),
            PhpKey::Bytes(b"12foo".to_vec()),
        ),
        "prefix12",
        Arc::clone(&prefix_trace),
    );
    add_trace(
        &mut prefix,
        HookPriority::integer(12),
        "int12",
        Arc::clone(&prefix_trace),
    );
    prefix
        .apply_filters(PhpValue::Bytes(b"seed".to_vec()), &[])
        .unwrap();
    assert_eq!(*prefix_trace.lock().unwrap(), ["prefix12", "int12"]);

    let mut prefix_reverse = Hook::new();
    let prefix_reverse_trace = Arc::new(Mutex::new(Vec::new()));
    add_trace(
        &mut prefix_reverse,
        HookPriority::integer(12),
        "int12",
        Arc::clone(&prefix_reverse_trace),
    );
    add_trace(
        &mut prefix_reverse,
        HookPriority::new(
            HookScalar::Bytes(b"12foo".to_vec()),
            PhpKey::Bytes(b"12foo".to_vec()),
        ),
        "prefix12",
        Arc::clone(&prefix_reverse_trace),
    );
    prefix_reverse
        .apply_filters(PhpValue::Bytes(b"seed".to_vec()), &[])
        .unwrap();
    assert_eq!(*prefix_reverse_trace.lock().unwrap(), ["int12", "prefix12"]);

    let mut nonnumeric = Hook::new();
    let nonnumeric_trace = Arc::new(Mutex::new(Vec::new()));
    add_trace(
        &mut nonnumeric,
        HookPriority::new(
            HookScalar::Bytes(b"INF".to_vec()),
            PhpKey::Bytes(b"INF".to_vec()),
        ),
        "inf",
        Arc::clone(&nonnumeric_trace),
    );
    add_trace(
        &mut nonnumeric,
        HookPriority::integer(0),
        "zero",
        Arc::clone(&nonnumeric_trace),
    );
    nonnumeric
        .apply_filters(PhpValue::Bytes(b"seed".to_vec()), &[])
        .unwrap();
    assert_eq!(*nonnumeric_trace.lock().unwrap(), ["inf", "zero"]);
}

#[test]
fn mixed_integer_float_priority_cycles_match_wordpress_sort_order() {
    fn run(entries: Vec<(HookPriority, String)>) -> Vec<String> {
        let mut hook = Hook::new();
        let trace = Arc::new(Mutex::new(Vec::new()));
        for (index, (priority, label)) in entries.into_iter().enumerate() {
            let callback_trace = Arc::clone(&trace);
            let callback_label = label.clone();
            hook.add_typed(
                priority,
                Callback::new(id(&format!("mixed-{index}")), 1, move |_, args| {
                    callback_trace.lock().unwrap().push(callback_label.clone());
                    Ok(args[0].clone())
                }),
            );
        }
        hook.apply_filters(PhpValue::Bytes(b"seed".to_vec()), &[])
            .unwrap();
        trace.lock().unwrap().clone()
    }

    fn int_priority(priority: i64) -> HookPriority {
        HookPriority::integer(priority)
    }

    fn string_priority(bytes: Vec<u8>) -> HookPriority {
        HookPriority::new(HookScalar::Bytes(bytes.clone()), PhpKey::Bytes(bytes))
    }

    let base = 9_007_199_254_740_992_i64;
    assert_eq!(
        run(vec![
            (int_priority(base + 1), "plus".into()),
            (
                string_priority(format!("{base}.0").into_bytes()),
                "float_base".into()
            ),
            (int_priority(base), "base".into()),
        ]),
        ["plus", "float_base", "base"]
    );
    assert_eq!(
        run(vec![
            (int_priority(base), "base".into()),
            (
                string_priority(format!("{base}.0").into_bytes()),
                "float_base".into()
            ),
            (int_priority(base + 1), "plus".into()),
        ]),
        ["base", "float_base", "plus"]
    );
    assert_eq!(
        run(vec![
            (int_priority(base + 1), "plus".into()),
            (int_priority(base), "base".into()),
            (
                string_priority(format!("{base}.0").into_bytes()),
                "float_base".into()
            ),
        ]),
        ["base", "plus", "float_base"]
    );

    let mut mixed = Vec::new();
    for index in 0..12_i64 {
        let reversed = 11 - index;
        mixed.push((int_priority(base + reversed), format!("i{reversed}")));
        mixed.push((
            string_priority(format!("{}.0", base + index).into_bytes()),
            format!("f{index}"),
        ));
    }
    assert_eq!(
        run(mixed),
        [
            "f0", "f1", "i0", "i1", "f2", "i2", "f3", "f4", "f5", "i3", "i4", "i5", "i6", "f6",
            "i7", "i8", "i9", "f7", "f8", "f9", "i10", "f10", "i11", "f11",
        ]
    );
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
