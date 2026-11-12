//! Request-local WP_Hook subset with a context-independent iteration engine.
//! Native callback identity, PHP references, and Wasm transport remain distinct.

use crate::{
    continuation::{PhpHandle, PhpHandleKind},
    value::PhpValue,
};
use std::{
    collections::{BTreeMap, VecDeque},
    fmt,
    sync::{Arc, Mutex, Weak},
};

pub(crate) const MAX_NESTING: usize = 64;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CallbackId {
    pub module: String,
    pub callback: String,
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
    InvalidDispatchTicket,
    DispatchTicketOrder,
    DispatchTicketBusy,
    DispatchParentNotPending,
    PhpReleasePending,
    NoPhpReleasePending,
    InvalidControlOperation,
    ControlOperationOrder,
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
    iteration: Vec<i64>,
    cursor: usize,
    running_priority: Option<i64>,
    bucket: Option<Vec<Option<ContextCallback<C>>>>,
    bucket_priority: Option<i64>,
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
    fn current(&self) -> Option<i64> {
        self.iteration.get(self.cursor).copied()
    }
}

/// A registered PHP closure requested by a resumable hook dispatch.
#[derive(Clone, Debug, PartialEq)]
pub struct PhpInvocation {
    pub callback: CallbackId,
    pub handle: PhpHandle,
    pub hook: String,
    pub priority: i64,
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

pub(crate) struct Storage<C> {
    callbacks: BTreeMap<i64, Vec<ContextCallback<C>>>,
    frames: Vec<Frame<C>>,
    doing_action: bool,
    diagnostics: Vec<HookDiagnostic>,
}

impl<C> Default for Storage<C> {
    fn default() -> Self {
        Self {
            callbacks: BTreeMap::new(),
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
    pub(crate) fn cancel_frame(&mut self) {
        self.frames.pop();
        if self.frames.is_empty() {
            self.doing_action = false;
        }
    }

    pub(crate) fn add_entry(&mut self, priority: i64, callback: ContextCallback<C>) -> bool {
        let existed = self.callbacks.contains_key(&priority);
        let callbacks = self.callbacks.entry(priority).or_default();
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

    pub(crate) fn add(&mut self, priority: i64, callback: ContextCallback<C>) {
        let existed = self.add_entry(priority, callback);
        self.finish_add(priority, existed);
    }

    pub(crate) fn finish_add(&mut self, priority: i64, priority_existed: bool) {
        self.resort_active(Some(priority), priority_existed);
    }

    pub(crate) fn remove_entry(&mut self, priority: i64, id: &CallbackId) -> bool {
        let Some(callbacks) = self.callbacks.get_mut(&priority) else {
            return false;
        };
        let Some(index) = callbacks.iter().position(|callback| callback.id == *id) else {
            return false;
        };
        drop(callbacks.remove(index));
        true
    }

    pub(crate) fn finish_remove(&mut self, priority: i64) -> bool {
        if self.callbacks.get(&priority).is_some_and(Vec::is_empty) {
            self.callbacks.remove(&priority);
            self.resort_active(None, false);
            true
        } else {
            false
        }
    }

    pub(crate) fn remove(&mut self, priority: i64, id: &CallbackId) -> bool {
        let removed = self.remove_entry(priority, id);
        if removed {
            self.finish_remove(priority);
        }
        removed
    }

    pub(crate) fn remove_all_entries(&mut self, priority: Option<i64>) -> bool {
        if self.callbacks.is_empty() {
            return false;
        }
        if let Some(priority) = priority {
            drop(self.callbacks.remove(&priority));
        } else {
            drop(std::mem::take(&mut self.callbacks));
        }
        true
    }

    pub(crate) fn finish_remove_all(&mut self) {
        self.resort_active(None, false);
    }

    pub(crate) fn remove_all(&mut self, priority: Option<i64>) {
        if self.remove_all_entries(priority) {
            self.finish_remove_all();
        }
    }

    pub(crate) fn has_callbacks(&self) -> bool {
        self.callbacks
            .values()
            .any(|callbacks| !callbacks.is_empty())
    }
    pub(crate) fn is_active(&self) -> bool {
        !self.frames.is_empty()
    }
    pub(crate) fn has_at(&self, priority: i64, id: &CallbackId) -> bool {
        self.callbacks
            .get(&priority)
            .is_some_and(|callbacks| callbacks.iter().any(|callback| callback.id == *id))
    }
    pub(crate) fn priority_of(&self, id: &CallbackId) -> Option<i64> {
        self.callbacks.iter().find_map(|(priority, callbacks)| {
            callbacks
                .iter()
                .any(|callback| callback.id == *id)
                .then_some(*priority)
        })
    }
    pub(crate) fn current_priority(&self) -> Option<i64> {
        self.frames.first().and_then(Frame::current)
    }
    pub(crate) fn take_diagnostics(&mut self) -> Vec<HookDiagnostic> {
        std::mem::take(&mut self.diagnostics)
    }

    fn resort_active(&mut self, new_priority: Option<i64>, priority_existed: bool) {
        let priorities: Vec<_> = self.callbacks.keys().copied().collect();
        for (iteration, frame) in self.frames.iter_mut().enumerate() {
            if priorities.is_empty() {
                frame.iteration.clear();
                frame.cursor = 0;
                continue;
            }
            let Some(current) = frame.current() else {
                continue;
            };
            frame.iteration.clone_from(&priorities);
            frame.cursor = 0;
            if current < priorities[0] {
                frame.iteration.insert(0, current);
                continue;
            }
            while frame.current().is_some_and(|priority| priority < current) {
                frame.cursor += 1;
            }
            if frame.running_priority.is_none() {
                self.diagnostics
                    .push(HookDiagnostic::UndefinedCurrentPriority { iteration });
            }
            if new_priority.is_some() && new_priority == frame.running_priority && !priority_existed
            {
                let previous = if frame.cursor >= frame.iteration.len() {
                    frame.cursor = frame.iteration.len() - 1;
                    frame.current()
                } else if frame.cursor == 0 {
                    None
                } else {
                    frame.cursor -= 1;
                    frame.current()
                };
                if previous.is_none() {
                    frame.cursor = 0;
                } else if previous != new_priority {
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
    let Some((&first, _)) = storage.callbacks.first_key_value() else {
        return Ok(false);
    };
    storage.frames.push(Frame {
        iteration: storage.callbacks.keys().copied().collect(),
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
                    .or_else(|| frame.current())
                    .expect("active priority");
                frame.running_priority = if frame.mode == Mode::All {
                    None
                } else {
                    Some(priority)
                };
                if frame.bucket.is_none() {
                    frame.current_callback = None;
                    frame.current_callback_cursor = None;
                    frame.bucket = Some(
                        storage
                            .callbacks
                            .get(&priority)
                            .cloned()
                            .unwrap_or_default()
                            .into_iter()
                            .map(Some)
                            .collect(),
                    );
                    frame.bucket_priority = Some(priority);
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
                        let callback = bucket[cursor]
                            .as_ref()
                            .expect("unvisited snapshot callback")
                            .clone();
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
                    hook: String::new(),
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
                if let Some(bucket) = frame.bucket.as_mut() {
                    bucket[frame.callback_cursor] = None;
                }
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
    if let Some(bucket) = frame.bucket.as_mut() {
        let callback = bucket
            .get_mut(frame.callback_cursor)
            .ok_or(HookError::InvalidDispatchTicket)?;
        *callback = None;
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
    pub fn add(&mut self, priority: i64, mut callback: Callback) {
        callback.attach_release_queue(&self.releases);
        self.state.add(priority, callback);
    }
    pub fn remove(&mut self, priority: i64, id: &CallbackId) -> bool {
        self.state.remove(priority, id)
    }
    pub fn remove_all(&mut self, priority: Option<i64>) {
        self.state.remove_all(priority);
    }
    pub fn has_callbacks(&self) -> bool {
        self.state.has_callbacks()
    }
    pub fn has_at(&self, priority: i64, id: &CallbackId) -> bool {
        self.state.has_at(priority, id)
    }
    pub fn priority_of(&self, id: &CallbackId) -> Option<i64> {
        self.state.priority_of(id)
    }
    pub fn current_priority(&self) -> Option<i64> {
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
