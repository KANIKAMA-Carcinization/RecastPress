//! Request-local WP_Hook subset with a context-independent iteration engine.
//! Native callback identity, PHP references, and Wasm transport remain distinct.

use crate::{
    continuation::{PhpHandle, PhpHandleKind},
    value::{PhpKey, PhpValue},
};
use std::{
    cmp::Ordering,
    collections::VecDeque,
    fmt,
    sync::{Arc, Mutex, Weak},
};

pub(crate) const MAX_NESTING: usize = 64;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CallbackId {
    pub module: String,
    pub callback: String,
}

/// The original scalar value PHP supplied as a hook name.
#[derive(Clone, Debug, PartialEq)]
pub enum HookScalar {
    Null,
    Bool(bool),
    Integer(i64),
    Float(f64),
    Bytes(Vec<u8>),
}

impl HookScalar {
    pub fn as_php_value(&self) -> PhpValue {
        match self {
            Self::Null => PhpValue::Null,
            Self::Bool(value) => PhpValue::Bool(*value),
            Self::Integer(value) => PhpValue::Integer(*value),
            Self::Float(value) => PhpValue::Float(*value),
            Self::Bytes(value) => PhpValue::Bytes(value.clone()),
        }
    }

    pub fn php_strict_eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Null, Self::Null) => true,
            (Self::Bool(left), Self::Bool(right)) => left == right,
            (Self::Integer(left), Self::Integer(right)) => left == right,
            (Self::Float(left), Self::Float(right)) => left == right,
            (Self::Bytes(left), Self::Bytes(right)) => left == right,
            _ => false,
        }
    }

    fn strict_eq_key(&self, key: &PhpKey) -> bool {
        match (self, key) {
            (Self::Integer(left), PhpKey::Integer(right)) => left == right,
            (Self::Bytes(left), PhpKey::Bytes(right)) => left == right,
            _ => false,
        }
    }
}

/// Original hook name plus the key PHP computed for its hook arrays.
#[derive(Clone, Debug, PartialEq)]
pub struct HookArgument {
    pub original: HookScalar,
    pub array_key: PhpKey,
}

impl HookArgument {
    pub fn new(original: HookScalar, array_key: PhpKey) -> Self {
        Self {
            original,
            array_key,
        }
    }

    pub fn bytes(name: &str) -> Self {
        let bytes = name.as_bytes().to_vec();
        Self::new(HookScalar::Bytes(bytes.clone()), PhpKey::from_bytes(&bytes))
    }
}

/// A priority's exact PHP array key, after PHP's key conversion.
#[derive(Clone, Debug, PartialEq)]
pub struct HookPriority {
    pub original: HookScalar,
    key: PhpKey,
}

impl HookPriority {
    pub fn new(original: HookScalar, key: PhpKey) -> Self {
        Self { original, key }
    }

    pub fn integer(value: i64) -> Self {
        Self::new(HookScalar::Integer(value), PhpKey::Integer(value))
    }

    pub fn key(&self) -> &PhpKey {
        &self.key
    }

    pub fn into_parts(self) -> (HookScalar, PhpKey) {
        (self.original, self.key)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum HookError {
    NestingLimit,
    CallbackFailed(String),
    InvalidPhpCallbackHandle,
    InvalidPhpCallableHandle,
    InvalidPhpThrowableHandle,
    PhpCallbackRequiresContinuation,
    PhpCallbackPending,
    NoPhpCallbackPending,
    PhpBoundaryPending,
    NoPhpBoundaryPending,
    InvalidDispatchBoundaryValue,
    ExpectedPhpThrowable,
    InvalidDispatchTicket,
    DispatchTicketOrder,
    DispatchTicketBusy,
    DispatchParentNotPending,
    PhpReleasePending,
    NoPhpReleasePending,
    PhpPriorityAccessPending,
    NoPhpPriorityAccessPending,
    InvalidControlOperation,
    ControlOperationOrder,
    InvalidHookTarget,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum HookDiagnostic {
    UndefinedCurrentPriority { iteration: usize },
}

impl HookDiagnostic {
    pub fn message(&self) -> String {
        match self {
            Self::UndefinedCurrentPriority { iteration } => {
                format!("Undefined array key {iteration}")
            }
        }
    }
}

type CallbackFn<C> = dyn Fn(&mut C, &[PhpValue]) -> Result<PhpValue, HookError> + Send + Sync;

#[derive(Clone, Debug, Default)]
pub(crate) struct PhpReleaseQueue(Arc<Mutex<VecDeque<PhpHandle>>>);

impl PhpReleaseQueue {
    pub(crate) fn drain(&self) -> VecDeque<PhpHandle> {
        std::mem::take(
            &mut *self
                .0
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner),
        )
    }
}

#[derive(Debug)]
struct PhpCallableLease {
    handle: PhpHandle,
    queue: Weak<Mutex<VecDeque<PhpHandle>>>,
}

impl Drop for PhpCallableLease {
    fn drop(&mut self) {
        if let Some(queue) = self.queue.upgrade() {
            queue
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .push_back(self.handle);
        }
    }
}

#[derive(Clone)]
struct PhpCallableRef {
    handle: PhpHandle,
    lease: Option<Arc<PhpCallableLease>>,
}

impl PhpCallableRef {
    fn new(handle: PhpHandle) -> Self {
        Self {
            handle,
            lease: None,
        }
    }

    fn attach_lease(&mut self, queue: &PhpReleaseQueue) {
        if self.lease.is_none() {
            self.lease = Some(Arc::new(PhpCallableLease {
                handle: self.handle,
                queue: Arc::downgrade(&queue.0),
            }));
        }
    }
}

enum CallbackTarget<C> {
    Native(Arc<CallbackFn<C>>),
    PhpClosure(PhpCallableRef),
    PhpCallable(PhpCallableRef),
}

impl<C> Clone for CallbackTarget<C> {
    fn clone(&self) -> Self {
        match self {
            Self::Native(function) => Self::Native(Arc::clone(function)),
            Self::PhpClosure(target) => Self::PhpClosure(target.clone()),
            Self::PhpCallable(target) => Self::PhpCallable(target.clone()),
        }
    }
}

pub type Callback = ContextCallback<Hook>;

pub struct ContextCallback<C> {
    id: CallbackId,
    accepted_args: i64,
    target: CallbackTarget<C>,
}

impl<C> Clone for ContextCallback<C> {
    fn clone(&self) -> Self {
        Self {
            id: self.id.clone(),
            accepted_args: self.accepted_args,
            target: self.target.clone(),
        }
    }
}

impl<C> fmt::Debug for ContextCallback<C> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Callback")
            .field("id", &self.id)
            .field("accepted_args", &self.accepted_args)
            .field(
                "target",
                &match &self.target {
                    CallbackTarget::Native(_) => "native",
                    CallbackTarget::PhpClosure(_) => "php_closure",
                    CallbackTarget::PhpCallable(_) => "php_callable",
                },
            )
            .finish_non_exhaustive()
    }
}

impl<C> ContextCallback<C> {
    pub fn new(
        id: CallbackId,
        accepted_args: i64,
        function: impl Fn(&mut C, &[PhpValue]) -> Result<PhpValue, HookError> + Send + Sync + 'static,
    ) -> Self {
        Self {
            id,
            accepted_args,
            target: CallbackTarget::Native(Arc::new(function)),
        }
    }

    pub fn php(id: CallbackId, accepted_args: i64, handle: PhpHandle) -> Result<Self, HookError> {
        if handle.kind() != PhpHandleKind::Closure {
            return Err(HookError::InvalidPhpCallbackHandle);
        }
        Ok(Self {
            id,
            accepted_args,
            target: CallbackTarget::PhpClosure(PhpCallableRef::new(handle)),
        })
    }

    pub fn php_callable(
        id: CallbackId,
        accepted_args: i64,
        handle: PhpHandle,
    ) -> Result<Self, HookError> {
        if handle.kind() != PhpHandleKind::Callable {
            return Err(HookError::InvalidPhpCallableHandle);
        }
        Ok(Self {
            id,
            accepted_args,
            target: CallbackTarget::PhpCallable(PhpCallableRef::new(handle)),
        })
    }

    pub(crate) fn attach_release_queue(&mut self, queue: &PhpReleaseQueue) {
        match &mut self.target {
            CallbackTarget::PhpCallable(target) => {
                target.attach_lease(queue);
            }
            CallbackTarget::PhpClosure(_) | CallbackTarget::Native(_) => {}
        }
    }

    pub fn id(&self) -> &CallbackId {
        &self.id
    }

    pub fn php_handle(&self) -> Option<PhpHandle> {
        match &self.target {
            CallbackTarget::PhpClosure(target) | CallbackTarget::PhpCallable(target) => {
                Some(target.handle)
            }
            CallbackTarget::Native(_) => None,
        }
    }
}

struct Frame<C> {
    iteration: Vec<PhpKey>,
    cursor: usize,
    running_priority: Option<PhpKey>,
    bucket: Option<Vec<ContextCallback<C>>>,
    bucket_priority: Option<PhpKey>,
    callback_cursor: usize,
    value: PhpValue,
    args: Vec<PhpValue>,
    mode: Mode,
    waiting_for_php: bool,
    current_callback: Option<ContextCallback<C>>,
    current_callback_cursor: Option<usize>,
    finishing: bool,
}

impl<C> fmt::Debug for Frame<C> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Frame")
            .field("iteration", &self.iteration)
            .field("cursor", &self.cursor)
            .field("running_priority", &self.running_priority)
            .field("bucket", &self.bucket)
            .field("bucket_priority", &self.bucket_priority)
            .field("callback_cursor", &self.callback_cursor)
            .field("value", &self.value)
            .field("args", &self.args)
            .field("mode", &self.mode)
            .field("waiting_for_php", &self.waiting_for_php)
            .field("current_callback_cursor", &self.current_callback_cursor)
            .field("finishing", &self.finishing)
            .finish()
    }
}

impl<C> Frame<C> {
    fn current(&self) -> Option<&PhpKey> {
        self.iteration.get(self.cursor)
    }
}

/// A registered PHP closure requested by a resumable hook dispatch.
#[derive(Clone, Debug, PartialEq)]
pub struct PhpInvocation {
    pub callback: CallbackId,
    pub handle: PhpHandle,
    pub hook: HookScalar,
    pub priority: PhpKey,
    pub call_mode: PhpCallMode,
    pub args: Vec<PhpValue>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PhpCallMode {
    Zero,
    Array,
    All,
}

#[derive(Clone, Debug, PartialEq)]
pub enum HookStep {
    InvokePhp(PhpInvocation),
    ReleasePhp(Vec<PhpHandle>),
    Complete(PhpValue),
}

pub(crate) type CallbackMap<C> = Vec<(PhpKey, Vec<ContextCallback<C>>)>;

pub(crate) struct Storage<C> {
    callbacks: Arc<CallbackMap<C>>,
    priorities: Vec<PhpKey>,
    frames: Vec<Frame<C>>,
    doing_action: bool,
    diagnostics: Vec<HookDiagnostic>,
}

impl<C> Default for Storage<C> {
    fn default() -> Self {
        Self {
            callbacks: Arc::new(Vec::new()),
            priorities: Vec::new(),
            frames: Vec::new(),
            doing_action: false,
            diagnostics: Vec::new(),
        }
    }
}

impl<C> fmt::Debug for Storage<C> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Storage")
            .field("callbacks", &self.callbacks)
            .field("frames", &self.frames)
            .field("doing_action", &self.doing_action)
            .finish()
    }
}

impl<C> Storage<C> {
    fn callback_index(&self, priority: &PhpKey) -> Option<usize> {
        self.callbacks.iter().position(|(key, _)| key == priority)
    }

    fn callback_bucket(&self, priority: &PhpKey) -> Option<&Vec<ContextCallback<C>>> {
        self.callback_index(priority)
            .map(|index| &self.callbacks[index].1)
    }

    pub(crate) fn cancel_frame(&mut self) {
        self.frames.pop();
        if self.frames.is_empty() {
            self.doing_action = false;
        }
    }

    pub(crate) fn add_entry(
        &mut self,
        priority: &HookPriority,
        callback: ContextCallback<C>,
    ) -> bool {
        Self::add_entry_to_map(Arc::make_mut(&mut self.callbacks), priority, callback)
    }

    pub(crate) fn add_entry_to_map(
        map: &mut CallbackMap<C>,
        priority: &HookPriority,
        callback: ContextCallback<C>,
    ) -> bool {
        let existed = map.iter().any(|(key, _)| key == &priority.key);
        if !existed {
            map.push((priority.key.clone(), Vec::new()));
        }
        let callbacks = map
            .iter_mut()
            .find(|(key, _)| key == &priority.key)
            .map(|(_, callbacks)| callbacks)
            .expect("priority bucket was inserted");
        if let Some(existing) = callbacks
            .iter_mut()
            .find(|existing| existing.id == callback.id)
        {
            *existing = callback;
            existed
        } else {
            callbacks.push(callback);
            existed
        }
    }

    pub(crate) fn add(&mut self, priority: HookPriority, callback: ContextCallback<C>) {
        let existed = self.add_entry(&priority, callback);
        self.finish_add(&priority, existed);
    }

    pub(crate) fn finish_add(&mut self, priority: &HookPriority, priority_existed: bool) {
        if !priority_existed && self.callbacks.len() > 1 {
            Arc::make_mut(&mut self.callbacks)
                .sort_by(|(left, _), (right, _)| php_sort_numeric_order(left, right));
        }
        self.priorities = self.callbacks.iter().map(|(key, _)| key.clone()).collect();
        self.resort_active(Some(priority), priority_existed);
    }

    /// Complete the live side of WP_Hook::add_filter after an assignment whose
    /// captured callback-map operand had been detached by warning-handler COW.
    /// The callback row itself is not copied into the live map, but WP still
    /// rebuilds priorities and resorts the active iteration from live callbacks.
    pub(crate) fn finish_add_detached(&mut self, priority: &HookPriority, priority_existed: bool) {
        if !priority_existed && self.callbacks.len() > 1 {
            Arc::make_mut(&mut self.callbacks)
                .sort_by(|(left, _), (right, _)| php_sort_numeric_order(left, right));
        }
        self.priorities = self.callbacks.iter().map(|(key, _)| key.clone()).collect();
        self.resort_active(Some(priority), priority_existed);
    }

    pub(crate) fn remove_entry(&mut self, priority: &HookPriority, id: &CallbackId) -> bool {
        let Some(index) = self
            .callback_bucket(&priority.key)
            .and_then(|callbacks| callbacks.iter().position(|callback| callback.id == *id))
        else {
            return false;
        };
        let callbacks = Arc::make_mut(&mut self.callbacks)
            .iter_mut()
            .find(|(key, _)| key == &priority.key)
            .expect("priority bucket exists");
        drop(callbacks.1.remove(index));
        true
    }

    pub(crate) fn finish_remove(&mut self, priority: &HookPriority) -> bool {
        if self
            .callback_bucket(&priority.key)
            .is_some_and(Vec::is_empty)
            && self.unset_priority_bucket(priority)
        {
            self.priorities.retain(|key| key != &priority.key);
            self.resort_active(None, false);
            true
        } else {
            false
        }
    }

    /// Remove the live bucket while leaving the cached priorities/iterations
    /// untouched until WP_Hook's later array_keys/resort phase.
    pub(crate) fn unset_priority_bucket(&mut self, priority: &HookPriority) -> bool {
        if let Some(index) = self.callback_index(&priority.key) {
            drop(Arc::make_mut(&mut self.callbacks).remove(index));
            true
        } else {
            false
        }
    }

    pub(crate) fn remove(&mut self, priority: HookPriority, id: &CallbackId) -> bool {
        let removed = self.remove_entry(&priority, id);
        if removed {
            self.finish_remove(&priority);
        }
        removed
    }

    pub(crate) fn remove_all_entries(&mut self, priority: Option<&HookPriority>) -> bool {
        if self.callbacks.is_empty() {
            return false;
        }
        if let Some(priority) = priority {
            if let Some(index) = self.callback_index(&priority.key) {
                drop(Arc::make_mut(&mut self.callbacks).remove(index));
            }
            self.priorities.retain(|key| key != &priority.key);
        } else {
            drop(std::mem::replace(&mut self.callbacks, Arc::new(Vec::new())));
            self.priorities.clear();
        }
        true
    }

    pub(crate) fn finish_remove_all(&mut self) {
        self.priorities
            .retain(|key| self.callbacks.iter().any(|(candidate, _)| candidate == key));
        for (key, _) in self.callbacks.iter() {
            if !self.priorities.iter().any(|candidate| candidate == key) {
                self.priorities.push(key.clone());
            }
        }
        self.resort_active(None, false);
    }

    pub(crate) fn remove_all(&mut self, priority: Option<HookPriority>) {
        if self.remove_all_entries(priority.as_ref()) {
            self.finish_remove_all();
        }
    }

    pub(crate) fn has_callbacks(&self) -> bool {
        self.callbacks
            .iter()
            .any(|(_, callbacks)| !callbacks.is_empty())
    }
    pub(crate) fn callbacks_empty(&self) -> bool {
        self.callbacks.is_empty()
    }
    pub(crate) fn callback_map(&self) -> Arc<CallbackMap<C>> {
        Arc::clone(&self.callbacks)
    }
    pub(crate) fn map_has_priority(map: &CallbackMap<C>, priority: &PhpKey) -> bool {
        map.iter().any(|(key, _)| key == priority)
    }
    pub(crate) fn has_priority(&self, priority: &PhpKey) -> bool {
        self.callback_index(priority).is_some()
    }
    pub(crate) fn priority_bucket_empty(&self, priority: &PhpKey) -> Option<bool> {
        self.callback_bucket(priority).map(Vec::is_empty)
    }
    pub(crate) fn finish_unset_priority(&mut self, priority: &HookPriority) {
        self.priorities.retain(|key| key != &priority.key);
        self.resort_active(None, false);
    }
    pub(crate) fn is_active(&self) -> bool {
        !self.frames.is_empty()
    }
    pub(crate) fn has_at(&self, priority: &HookPriority, id: &CallbackId) -> bool {
        self.callback_bucket(&priority.key)
            .is_some_and(|callbacks| callbacks.iter().any(|callback| callback.id == *id))
    }
    pub(crate) fn priority_of(&self, id: &CallbackId) -> Option<PhpKey> {
        self.priorities.iter().find_map(|priority| {
            self.callback_bucket(priority)
                .is_some_and(|callbacks| callbacks.iter().any(|callback| callback.id == *id))
                .then(|| priority.clone())
        })
    }
    pub(crate) fn current_priority(&self) -> Option<PhpKey> {
        self.frames.first().and_then(Frame::current).cloned()
    }
    pub(crate) fn take_diagnostics(&mut self) -> Vec<HookDiagnostic> {
        std::mem::take(&mut self.diagnostics)
    }

    fn resort_active(&mut self, new_priority: Option<&HookPriority>, priority_existed: bool) {
        let priorities = self.priorities.clone();
        for (iteration, frame) in self.frames.iter_mut().enumerate() {
            if priorities.is_empty() {
                frame.iteration.clear();
                frame.cursor = 0;
                continue;
            }
            let Some(current) = frame.current().cloned() else {
                continue;
            };
            frame.iteration.clone_from(&priorities);
            frame.cursor = 0;
            let min = php_min_priority(&priorities);
            if php_priority_less(&current, &min) {
                frame.iteration.insert(0, current);
                continue;
            }
            while frame
                .current()
                .is_some_and(|priority| php_priority_less(priority, &current))
            {
                frame.cursor += 1;
            }
            if frame.running_priority.is_none() {
                self.diagnostics
                    .push(HookDiagnostic::UndefinedCurrentPriority { iteration });
            }
            if new_priority.is_some_and(|priority| {
                frame
                    .running_priority
                    .as_ref()
                    .is_some_and(|running| priority.original.strict_eq_key(running))
            }) && !priority_existed
            {
                let previous = if frame.cursor >= frame.iteration.len() {
                    frame.cursor = frame.iteration.len() - 1;
                    frame.current().cloned()
                } else if frame.cursor == 0 {
                    None
                } else {
                    frame.cursor -= 1;
                    frame.current().cloned()
                };
                if previous.is_none() {
                    frame.cursor = 0;
                } else if !new_priority.is_some_and(|priority| {
                    previous
                        .as_ref()
                        .is_some_and(|previous| priority.original.strict_eq_key(previous))
                }) {
                    frame.cursor += 1;
                }
            }
        }
    }
}

pub(crate) trait Context: Sized {
    fn storage(&mut self, slot: usize) -> &mut Storage<Self>;
    fn release_queue(&self) -> &PhpReleaseQueue;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Mode {
    Filter,
    Action,
    All,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum PhpNumber {
    Integer(i64),
    Float(f64),
    OverflowInteger(f64),
}

fn compare_integer_float(integer: i64, float: f64) -> Ordering {
    if float.is_nan() {
        return Ordering::Equal;
    }
    // PHP converts the integer operand to double when the other numeric
    // operand is a float, so adjacent integers can compare equal above 2^53.
    (integer as f64)
        .partial_cmp(&float)
        .unwrap_or(Ordering::Equal)
}

fn compare_php_numbers(left: PhpNumber, right: PhpNumber) -> Ordering {
    match (left, right) {
        (PhpNumber::Integer(left), PhpNumber::Integer(right)) => left.cmp(&right),
        (PhpNumber::Float(left), PhpNumber::Float(right)) => {
            left.partial_cmp(&right).unwrap_or(Ordering::Equal)
        }
        (PhpNumber::Integer(left), PhpNumber::Float(right)) => compare_integer_float(left, right),
        (PhpNumber::Float(left), PhpNumber::Integer(right)) => {
            compare_integer_float(right, left).reverse()
        }
        (PhpNumber::OverflowInteger(left), PhpNumber::OverflowInteger(right))
        | (PhpNumber::OverflowInteger(left), PhpNumber::Float(right))
        | (PhpNumber::Float(left), PhpNumber::OverflowInteger(right)) => {
            left.partial_cmp(&right).unwrap_or(Ordering::Equal)
        }
        (PhpNumber::Integer(left), PhpNumber::OverflowInteger(right)) => {
            compare_integer_float(left, right)
        }
        (PhpNumber::OverflowInteger(left), PhpNumber::Integer(right)) => {
            compare_integer_float(right, left).reverse()
        }
    }
}

fn php_number_as_float(number: PhpNumber) -> f64 {
    match number {
        PhpNumber::Integer(value) => value as f64,
        PhpNumber::Float(value) | PhpNumber::OverflowInteger(value) => value,
    }
}

/// PHP's ksort(..., SORT_NUMERIC) comparison is distinct from `<` used by
/// WP_Hook::resort_active_iterations.
fn php_sort_numeric_order(left: &PhpKey, right: &PhpKey) -> Ordering {
    // Zend's numeric sort keeps exact ordering for two integer keys, but when
    // either operand is a string it compares their double numeric values.
    // This matters above 2^53: a numeric string and a nearby integer may tie,
    // and PHP's stable sort then preserves insertion order.
    match (left, right) {
        (PhpKey::Integer(left), PhpKey::Integer(right)) => left.cmp(right),
        _ => numeric_sort_value(left)
            .partial_cmp(&numeric_sort_value(right))
            .unwrap_or(Ordering::Equal),
    }
}

fn numeric_sort_value(key: &PhpKey) -> f64 {
    match key {
        PhpKey::Integer(value) => *value as f64,
        PhpKey::Bytes(bytes) => numeric_prefix(bytes).map_or(0.0, |(start, end, _)| {
            std::str::from_utf8(&bytes[start..end])
                .ok()
                .and_then(|token| token.parse::<f64>().ok())
                .unwrap_or(0.0)
        }),
    }
}

fn numeric_string(bytes: &[u8]) -> Option<PhpNumber> {
    let (start, end, is_float) = numeric_prefix(bytes)?;
    if bytes[..start]
        .iter()
        .any(|byte| !is_php_ascii_whitespace(*byte))
        || bytes[end..]
            .iter()
            .any(|byte| !is_php_ascii_whitespace(*byte))
    {
        return None;
    }
    let token = std::str::from_utf8(&bytes[start..end]).ok()?;
    if !is_float {
        if let Ok(integer) = token.parse::<i64>() {
            return Some(PhpNumber::Integer(integer));
        }
        return token.parse::<f64>().ok().map(PhpNumber::OverflowInteger);
    }
    token.parse::<f64>().ok().map(PhpNumber::Float)
}

/// Parse PHP's decimal numeric prefix. `SORT_NUMERIC` casts non-numeric
/// strings by their leading number (`"12junk"` becomes 12), while regular
/// comparisons require the whole string to be numeric.
fn numeric_prefix(bytes: &[u8]) -> Option<(usize, usize, bool)> {
    let mut index = 0;
    while bytes
        .get(index)
        .is_some_and(|byte| is_php_ascii_whitespace(*byte))
    {
        index += 1;
    }
    let start = index;
    if bytes
        .get(index)
        .is_some_and(|byte| matches!(byte, b'+' | b'-'))
    {
        index += 1;
    }
    let integer_start = index;
    while bytes.get(index).is_some_and(u8::is_ascii_digit) {
        index += 1;
    }
    let integer_digits = index - integer_start;
    let mut is_float = false;
    if bytes.get(index) == Some(&b'.') {
        is_float = true;
        index += 1;
        while bytes.get(index).is_some_and(u8::is_ascii_digit) {
            index += 1;
        }
    }
    if integer_digits == 0 && !is_float {
        return None;
    }
    if is_float && index == integer_start + 1 {
        return None;
    }
    let mantissa_end = index;
    if bytes
        .get(index)
        .is_some_and(|byte| matches!(byte, b'e' | b'E'))
    {
        let mut exponent_index = index + 1;
        if bytes
            .get(exponent_index)
            .is_some_and(|byte| matches!(byte, b'+' | b'-'))
        {
            exponent_index += 1;
        }
        let exponent_digits = exponent_index;
        while bytes.get(exponent_index).is_some_and(u8::is_ascii_digit) {
            exponent_index += 1;
        }
        if exponent_index > exponent_digits {
            index = exponent_index;
            is_float = true;
        } else {
            index = mantissa_end;
        }
    }
    (index > start).then_some((start, index, is_float))
}

fn is_php_ascii_whitespace(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}

/// PHP 8 number/string and string/string ordering used by `<` and `min()` in
/// active WP hook iteration. Array-key identity remains strict `PhpKey` equality.
fn php_priority_less(left: &PhpKey, right: &PhpKey) -> bool {
    match (left, right) {
        (PhpKey::Integer(left), PhpKey::Integer(right)) => left < right,
        (PhpKey::Bytes(left), PhpKey::Bytes(right)) => {
            match (numeric_string(left), numeric_string(right)) {
                (Some(left_number), Some(right_number))
                    if (matches!(left_number, PhpNumber::OverflowInteger(_))
                        && matches!(right_number, PhpNumber::OverflowInteger(_))
                        && php_number_as_float(left_number)
                            == php_number_as_float(right_number))
                        || (php_number_as_float(left_number).is_infinite()
                            && php_number_as_float(left_number)
                                == php_number_as_float(right_number)) =>
                {
                    left < right
                }
                (Some(left), Some(right)) => compare_php_numbers(left, right) == Ordering::Less,
                _ => left < right,
            }
        }
        (PhpKey::Integer(left), PhpKey::Bytes(right)) => match numeric_string(right) {
            Some(right) => compare_php_numbers(PhpNumber::Integer(*left), right) == Ordering::Less,
            None => left.to_string().as_bytes() < right.as_slice(),
        },
        (PhpKey::Bytes(left), PhpKey::Integer(right)) => match numeric_string(left) {
            Some(left) => compare_php_numbers(left, PhpNumber::Integer(*right)) == Ordering::Less,
            None => left.as_slice() < right.to_string().as_bytes(),
        },
    }
}

fn php_min_priority(priorities: &[PhpKey]) -> PhpKey {
    let mut minimum = priorities.first().expect("nonempty priority list").clone();
    for priority in &priorities[1..] {
        if php_priority_less(priority, &minimum) {
            minimum = priority.clone();
        }
    }
    minimum
}

pub(crate) fn start<C: Context>(
    context: &mut C,
    slot: usize,
    value: PhpValue,
    args: Vec<PhpValue>,
    mode: Mode,
) -> Result<bool, HookError> {
    let storage = context.storage(slot);
    if storage.frames.len() >= MAX_NESTING {
        return Err(HookError::NestingLimit);
    }
    if mode == Mode::Action {
        storage.doing_action = true;
    }
    let Some(first) = storage.priorities.first().cloned() else {
        return Ok(false);
    };
    storage.frames.push(Frame {
        iteration: storage.priorities.clone(),
        cursor: 0,
        running_priority: if mode == Mode::All { None } else { Some(first) },
        bucket: None,
        bucket_priority: None,
        callback_cursor: 0,
        current_callback: None,
        current_callback_cursor: None,
        finishing: false,
        value,
        args,
        mode,
        waiting_for_php: false,
    });
    Ok(true)
}

/// Run native callbacks until completion or one PHP closure needs the caller.
pub(crate) fn advance<C: Context>(context: &mut C, slot: usize) -> Result<HookStep, HookError> {
    loop {
        let selected = {
            let storage = context.storage(slot);
            let Some(frame) = storage.frames.last_mut() else {
                return Err(HookError::InvalidDispatchTicket);
            };
            if frame.waiting_for_php {
                return Err(HookError::PhpCallbackPending);
            }
            if frame.finishing {
                let finished = storage.frames.pop().expect("active hook frame");
                if storage.frames.is_empty() && (finished.mode == Mode::Action) {
                    storage.doing_action = false;
                }
                return Ok(HookStep::Complete(finished.value));
            }
            if frame.bucket.is_none() && frame.bucket_priority.is_some() {
                frame.bucket_priority = None;
                frame.callback_cursor = 0;
                frame.cursor += 1;
                None
            } else if frame.bucket.is_none()
                && frame.bucket_priority.is_none()
                && frame.current().is_none()
            {
                frame.current_callback = None;
                frame.current_callback_cursor = None;
                frame.args.clear();
                frame.finishing = true;
                None
            } else {
                let priority = frame
                    .bucket_priority
                    .clone()
                    .or_else(|| frame.current().cloned())
                    .expect("active priority");
                frame.running_priority = if frame.mode == Mode::All {
                    None
                } else {
                    Some(priority.clone())
                };
                if frame.bucket.is_none() {
                    frame.current_callback = None;
                    frame.current_callback_cursor = None;
                    let snapshot = storage
                        .callbacks
                        .iter()
                        .find(|(key, _)| key == &priority)
                        .map(|(_, callbacks)| callbacks.clone())
                        .unwrap_or_default();
                    frame.bucket = Some(snapshot);
                    frame.bucket_priority = Some(priority.clone());
                    frame.callback_cursor = 0;
                }
                let bucket = frame.bucket.as_ref().expect("bucket snapshot");
                if frame.callback_cursor >= bucket.len() {
                    frame.bucket = None;
                    None
                } else {
                    let cursor = frame.callback_cursor;
                    if frame.current_callback_cursor != Some(cursor) {
                        frame.current_callback = None;
                        frame.current_callback_cursor = None;
                        let callback = bucket[cursor].clone();
                        frame.current_callback = Some(callback);
                        frame.current_callback_cursor = Some(cursor);
                    }
                    if frame.mode != Mode::All && !storage.doing_action {
                        frame.args[0] = frame.value.clone();
                    }
                    let callback = frame
                        .current_callback
                        .as_ref()
                        .expect("selected callback")
                        .clone();
                    let accepted = if frame.mode == Mode::All {
                        frame.args.len()
                    } else if callback.accepted_args >= 0 {
                        usize::try_from(callback.accepted_args)
                            .unwrap_or(usize::MAX)
                            .min(frame.args.len())
                    } else {
                        frame.args.len().saturating_sub(
                            usize::try_from(callback.accepted_args.unsigned_abs())
                                .unwrap_or(usize::MAX),
                        )
                    };
                    let call_mode = if frame.mode == Mode::All {
                        PhpCallMode::All
                    } else if callback.accepted_args == 0 {
                        PhpCallMode::Zero
                    } else {
                        PhpCallMode::Array
                    };
                    Some((
                        priority,
                        callback,
                        frame.args[..accepted].to_vec(),
                        call_mode,
                    ))
                }
            }
        };

        let releases = context.release_queue().drain();
        if !releases.is_empty() {
            return Ok(HookStep::ReleasePhp(releases.into_iter().collect()));
        }
        let Some((priority, callback, args, call_mode)) = selected else {
            continue;
        };

        match callback.target.clone() {
            CallbackTarget::PhpClosure(target) | CallbackTarget::PhpCallable(target) => {
                let storage = context.storage(slot);
                let frame = storage.frames.last_mut().expect("active hook frame");
                frame.waiting_for_php = true;
                return Ok(HookStep::InvokePhp(PhpInvocation {
                    callback: callback.id,
                    handle: target.handle,
                    hook: HookScalar::Bytes(Vec::new()),
                    priority,
                    call_mode,
                    args,
                }));
            }
            CallbackTarget::Native(function) => {
                let output = function(context, &args)?;
                let frame = context
                    .storage(slot)
                    .frames
                    .last_mut()
                    .expect("active hook frame");
                frame.value = output;
                frame.callback_cursor += 1;
            }
        }
    }
}

pub(crate) fn resume<C: Context>(
    context: &mut C,
    slot: usize,
    value: PhpValue,
) -> Result<(), HookError> {
    let frame = context
        .storage(slot)
        .frames
        .last_mut()
        .ok_or(HookError::InvalidDispatchTicket)?;
    if !frame.waiting_for_php {
        return Err(HookError::NoPhpCallbackPending);
    }
    frame.value = value;
    if frame
        .bucket
        .as_ref()
        .is_some_and(|bucket| frame.callback_cursor >= bucket.len())
    {
        return Err(HookError::InvalidDispatchTicket);
    }
    frame.waiting_for_php = false;
    frame.callback_cursor += 1;
    Ok(())
}

pub(crate) fn abandon_frame_for_throwable<C: Context>(context: &mut C, slot: usize) {
    let frame = context
        .storage(slot)
        .frames
        .last_mut()
        .expect("active hook frame");
    frame.bucket = None;
    frame.bucket_priority = None;
    frame.callback_cursor = 0;
    frame.current_callback = None;
    frame.current_callback_cursor = None;
    frame.value = PhpValue::Null;
    frame.args.clear();
    frame.waiting_for_php = false;
    frame.finishing = false;
}

pub(crate) fn invoke<C: Context>(
    context: &mut C,
    slot: usize,
    value: PhpValue,
    args: Vec<PhpValue>,
    mode: Mode,
) -> Result<PhpValue, HookError> {
    let started = start(context, slot, value.clone(), args, mode)?;
    let result = if !started {
        Ok(value)
    } else {
        let result = match advance(context, slot) {
            Ok(HookStep::Complete(value)) => Ok(value),
            Ok(HookStep::InvokePhp(_) | HookStep::ReleasePhp(_)) => {
                Err(HookError::PhpCallbackRequiresContinuation)
            }
            Err(error) => Err(error),
        };
        if result.is_err() {
            context.storage(slot).frames.pop();
        }
        result
    };
    let storage = context.storage(slot);
    if storage.frames.is_empty() && (mode == Mode::Action || result.is_err()) {
        storage.doing_action = false;
    }
    result
}

#[derive(Debug, Default)]
pub struct Hook {
    state: Storage<Hook>,
    releases: PhpReleaseQueue,
}

impl Context for Hook {
    fn storage(&mut self, slot: usize) -> &mut Storage<Self> {
        debug_assert_eq!(slot, 0);
        &mut self.state
    }

    fn release_queue(&self) -> &PhpReleaseQueue {
        &self.releases
    }
}

impl Hook {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn add(&mut self, priority: i64, callback: Callback) {
        self.add_typed(HookPriority::integer(priority), callback);
    }
    pub fn add_typed(&mut self, priority: HookPriority, mut callback: Callback) {
        callback.attach_release_queue(&self.releases);
        self.state.add(priority, callback);
    }
    pub fn remove(&mut self, priority: i64, id: &CallbackId) -> bool {
        self.remove_typed(&HookPriority::integer(priority), id)
    }
    pub fn remove_typed(&mut self, priority: &HookPriority, id: &CallbackId) -> bool {
        self.state.remove(priority.clone(), id)
    }
    pub fn remove_all(&mut self, priority: Option<i64>) {
        self.remove_all_typed(priority.map(HookPriority::integer));
    }
    pub fn remove_all_typed(&mut self, priority: Option<HookPriority>) {
        self.state.remove_all(priority);
    }
    pub fn has_callbacks(&self) -> bool {
        self.state.has_callbacks()
    }
    pub fn has_at(&self, priority: i64, id: &CallbackId) -> bool {
        self.has_at_typed(&HookPriority::integer(priority), id)
    }
    pub fn has_at_typed(&self, priority: &HookPriority, id: &CallbackId) -> bool {
        self.state.has_at(priority, id)
    }
    pub fn priority_of(&self, id: &CallbackId) -> Option<i64> {
        self.priority_of_typed(id).and_then(|key| match key {
            PhpKey::Integer(value) => Some(value),
            PhpKey::Bytes(_) => None,
        })
    }
    pub fn priority_of_typed(&self, id: &CallbackId) -> Option<PhpKey> {
        self.state.priority_of(id)
    }
    pub fn current_priority(&self) -> Option<i64> {
        self.current_priority_typed().and_then(|key| match key {
            PhpKey::Integer(value) => Some(value),
            PhpKey::Bytes(_) => None,
        })
    }
    pub fn current_priority_typed(&self) -> Option<PhpKey> {
        self.state.current_priority()
    }
    pub fn nesting_depth(&self) -> usize {
        self.state.frames.len()
    }
    pub fn take_diagnostics(&mut self) -> Vec<HookDiagnostic> {
        self.state.take_diagnostics()
    }
    pub fn take_php_releases(&mut self) -> Vec<PhpHandle> {
        self.releases.drain().into_iter().collect()
    }
    pub fn apply_filters(
        &mut self,
        value: PhpValue,
        extra: &[PhpValue],
    ) -> Result<PhpValue, HookError> {
        let mut args = vec![value.clone()];
        args.extend_from_slice(extra);
        invoke(self, 0, value, args, Mode::Filter)
    }
    pub fn do_action(&mut self, args: &[PhpValue]) -> Result<(), HookError> {
        invoke(
            self,
            0,
            PhpValue::Bytes(Vec::new()),
            args.to_vec(),
            Mode::Action,
        )
        .map(|_| ())
    }
}

#[cfg(test)]
mod priority_comparison_tests {
    use super::{
        PhpNumber, compare_php_numbers, numeric_prefix, numeric_sort_value, numeric_string,
        php_priority_less,
    };
    use crate::value::PhpKey;
    use std::cmp::Ordering;

    #[test]
    fn php_numeric_grammar_separates_sort_prefix_from_regular_comparison() {
        let prefix = PhpKey::Bytes(b"12foo".to_vec());
        assert_eq!(numeric_sort_value(&prefix), 12.0);
        assert_eq!(numeric_string(b"12foo"), None);
        assert!(php_priority_less(&PhpKey::Integer(12), &prefix));
        assert!(!php_priority_less(&prefix, &PhpKey::Integer(12)));

        assert_eq!(numeric_string(b" 12 \x0b"), Some(PhpNumber::Integer(12)));
        assert_eq!(numeric_string(b"1e+"), None);
        assert_eq!(numeric_sort_value(&PhpKey::Bytes(b"1e+".to_vec())), 1.0);
        assert_eq!(numeric_string(b"INF"), None);
        assert_eq!(numeric_string(b"NaN"), None);
        assert_eq!(numeric_sort_value(&PhpKey::Bytes(b"INF".to_vec())), 0.0);
        assert_eq!(
            numeric_sort_value(&PhpKey::Bytes(b"12\0tail".to_vec())),
            12.0
        );
        assert_eq!(numeric_string(b"12\0tail"), None);
        assert_eq!(numeric_prefix(b"\xff12"), None);
        assert!(php_priority_less(
            &PhpKey::Bytes(b"1e309".to_vec()),
            &PhpKey::Bytes(b"2e309".to_vec())
        ));
        assert!(php_priority_less(
            &PhpKey::Bytes(b"-1e309".to_vec()),
            &PhpKey::Bytes(b"-2e309".to_vec())
        ));
        assert!(php_priority_less(
            &PhpKey::Bytes(b"9223372036854775808".to_vec()),
            &PhpKey::Bytes(b"9223372036854775809".to_vec())
        ));
        assert!(php_priority_less(
            &PhpKey::Bytes(b"99999999999999999999".to_vec()),
            &PhpKey::Bytes(b"100000000000000000000000".to_vec())
        ));
        assert!(!php_priority_less(
            &PhpKey::Bytes(b"+1e309".to_vec()),
            &PhpKey::Bytes(b"-1e309".to_vec())
        ));
        assert_eq!(
            compare_php_numbers(
                numeric_string(b"9007199254740992.0").unwrap(),
                PhpNumber::Integer(9_007_199_254_740_993)
            ),
            Ordering::Equal
        );
    }

    #[test]
    fn php_regular_numeric_compare_uses_integer_or_binary64_rules_from_grammar() {
        assert_eq!(
            numeric_string(b"+9007199254740992"),
            Some(PhpNumber::Integer(9_007_199_254_740_992))
        );
        assert!(matches!(
            numeric_string(b"9007199254740992.0"),
            Some(PhpNumber::Float(_))
        ));
        assert_eq!(
            compare_php_numbers(
                PhpNumber::Integer(9_007_199_254_740_993),
                PhpNumber::Float(9_007_199_254_740_992.0)
            ),
            Ordering::Equal
        );
        assert_eq!(
            numeric_string(b"1e309"),
            Some(PhpNumber::Float(f64::INFINITY))
        );
        assert_eq!(
            numeric_string(b"-1e309"),
            Some(PhpNumber::Float(f64::NEG_INFINITY))
        );
    }
}
