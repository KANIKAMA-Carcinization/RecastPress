//! Typed, in-memory continuation protocol for modeling PHP/Rust re-entry.
//!
//! This is a state-machine contract only. It does not execute PHP, implement a
//! Rust WordPress query, or establish that a Wasm host can re-enter an instance.
//! `PhpHandle` is a request-scoped typed reference checked against this machine's
//! registered set; it is not a PHP registry, token issuer, slot allocator, GC,
//! generation updater, or proof that the referenced PHP value still exists.

use std::fmt;
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT_INSTANCE: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct RequestGeneration(u64);

impl RequestGeneration {
    pub const fn new(value: u64) -> Self {
        Self(value)
    }
    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct OperationId {
    instance: u64,
    sequence: u64,
}

impl OperationId {
    /// Opaque correlation fields for a transport adapter; never authorization.
    pub const fn correlation(self) -> (u64, u64) {
        (self.instance, self.sequence)
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct EffectId {
    instance: u64,
    sequence: u64,
}

impl EffectId {
    /// Opaque correlation fields for a transport adapter; never authorization.
    pub const fn correlation(self) -> (u64, u64) {
        (self.instance, self.sequence)
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum PhpHandleKind {
    Object,
    ReferenceCell,
    Closure,
    Callable,
    /// Raw PHP API parameter retained until its boundary operations complete.
    Argument,
    Array,
    Resource,
    Throwable,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct PhpHandle {
    request: RequestGeneration,
    kind: PhpHandleKind,
    slot: u32,
    generation: u32,
    token: u64,
}

impl PhpHandle {
    pub fn new(
        request: RequestGeneration,
        kind: PhpHandleKind,
        slot: u32,
        generation: u32,
        token: u64,
    ) -> Self {
        Self {
            request,
            kind,
            slot,
            generation,
            token,
        }
    }

    pub fn request(self) -> RequestGeneration {
        self.request
    }

    pub fn kind(self) -> PhpHandleKind {
        self.kind
    }

    /// Opaque wire-correlation metadata; these fields do not authenticate a handle.
    pub fn slot(self) -> u32 {
        self.slot
    }

    /// Opaque wire-correlation metadata; these fields do not authenticate a handle.
    pub fn generation(self) -> u32 {
        self.generation
    }

    /// Opaque wire-correlation metadata; these fields do not authenticate a handle.
    pub fn token(self) -> u64 {
        self.token
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LimitsProfile {
    pub max_depth: usize,
    pub max_operations: usize,
    pub max_handles: usize,
}

impl Default for LimitsProfile {
    fn default() -> Self {
        Self {
            max_depth: 64,
            max_operations: 16_384,
            max_handles: 16_384,
        }
    }
}

#[must_use = "the completion carries the PHP effect outcome, including throwable identity"]
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Completion<T> {
    Value(T),
    PhpThrowable(PhpHandle),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RequestState {
    Running,
    RootReturned,
    Finished,
    Aborted,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ContinuationError {
    InvalidLimits,
    AlreadyStarted,
    NotStarted,
    RequestClosed,
    WrongRequest,
    WrongOperation,
    WrongEffect,
    EffectNotPending,
    EffectAlreadyResumed,
    ParentNotActive,
    RootHasNoParent,
    OperationLimit,
    DepthLimit,
    HandleLimit,
    InvalidHandle,
    WrongHandleKind,
    NotAtRoot,
    PendingEffect,
}

impl fmt::Display for ContinuationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for ContinuationError {}

#[derive(Clone, Debug)]
struct OperationFrame {
    id: OperationId,
    parent_effect: Option<EffectId>,
    pending_effect: Option<EffectId>,
    suspended: bool,
}

#[derive(Clone, Debug)]
struct EffectFrame {
    id: EffectId,
    operation: OperationId,
    resumed: bool,
    child: Option<OperationId>,
}

/// Single-request continuation state. Methods only mutate in-memory protocol
/// state; no callback, PHP runtime, database guard, or shared lock is retained.
#[derive(Debug)]
pub struct Continuation<T> {
    request: RequestGeneration,
    instance: u64,
    limits: LimitsProfile,
    state: RequestState,
    started: bool,
    root_returned: bool,
    next_operation: u64,
    next_effect: u64,
    operations: usize,
    handles: Vec<PhpHandle>,
    stack: Vec<OperationFrame>,
    effects: Vec<EffectFrame>,
    _completion: std::marker::PhantomData<T>,
}

impl<T> Continuation<T> {
    pub fn new(
        request: RequestGeneration,
        limits: LimitsProfile,
    ) -> Result<Self, ContinuationError> {
        if limits.max_depth == 0 || limits.max_operations == 0 {
            return Err(ContinuationError::InvalidLimits);
        }
        Ok(Self {
            request,
            instance: NEXT_INSTANCE.fetch_add(1, Ordering::Relaxed),
            limits,
            state: RequestState::Running,
            started: false,
            next_operation: 1,
            next_effect: 1,
            operations: 0,
            root_returned: false,
            handles: Vec::new(),
            stack: Vec::new(),
            effects: Vec::new(),
            _completion: std::marker::PhantomData,
        })
    }

    pub fn state(&self) -> &RequestState {
        &self.state
    }
    pub fn request(&self) -> RequestGeneration {
        self.request
    }
    pub fn depth(&self) -> usize {
        self.stack.len()
    }

    fn ensure_running(&self) -> Result<(), ContinuationError> {
        match self.state {
            RequestState::Running | RequestState::RootReturned => Ok(()),
            RequestState::Finished | RequestState::Aborted => Err(ContinuationError::RequestClosed),
        }
    }
    fn alloc_operation(
        &mut self,
        parent_effect: Option<EffectId>,
    ) -> Result<OperationId, ContinuationError> {
        if self.operations >= self.limits.max_operations {
            return Err(ContinuationError::OperationLimit);
        }
        if self.stack.len() >= self.limits.max_depth {
            return Err(ContinuationError::DepthLimit);
        }
        let id = OperationId {
            instance: self.instance,
            sequence: self.next_operation,
        };
        self.next_operation = self
            .next_operation
            .checked_add(1)
            .ok_or(ContinuationError::OperationLimit)?;
        self.operations += 1;
        self.stack.push(OperationFrame {
            id,
            parent_effect,
            pending_effect: None,
            suspended: false,
        });
        Ok(id)
    }
    pub fn start_root(&mut self) -> Result<OperationId, ContinuationError> {
        self.ensure_running()?;
        if self.started {
            return Err(ContinuationError::AlreadyStarted);
        }
        self.started = true;
        self.alloc_operation(None)
    }
    /// Start a PHP effect from the active operation. The caller executes the
    /// returned effect in PHP without holding a Rust shared/exclusive guard.
    pub fn invoke_php(&mut self, operation: OperationId) -> Result<EffectId, ContinuationError> {
        self.ensure_running()?;
        let frame = self.stack.last_mut().ok_or(ContinuationError::NotStarted)?;
        if frame.id != operation || frame.suspended {
            return Err(ContinuationError::WrongOperation);
        }
        if frame.pending_effect.is_some() {
            return Err(ContinuationError::PendingEffect);
        }
        if self.effects.len() >= self.limits.max_operations {
            return Err(ContinuationError::OperationLimit);
        }
        let id = EffectId {
            instance: self.instance,
            sequence: self.next_effect,
        };
        self.next_effect = self
            .next_effect
            .checked_add(1)
            .ok_or(ContinuationError::OperationLimit)?;
        frame.pending_effect = Some(id);
        self.effects.push(EffectFrame {
            id,
            operation,
            resumed: false,
            child: None,
        });
        Ok(id)
    }
    /// `CallCore` from the currently executing PHP effect suspends its parent
    /// and pushes exactly one child operation.
    pub fn begin_child(
        &mut self,
        parent_effect: EffectId,
    ) -> Result<OperationId, ContinuationError> {
        self.ensure_running()?;
        let top = self.stack.last().ok_or(ContinuationError::NotStarted)?;
        let effect = self
            .effects
            .iter()
            .find(|e| e.id == parent_effect)
            .ok_or(ContinuationError::WrongEffect)?;
        if effect.resumed {
            return Err(ContinuationError::EffectAlreadyResumed);
        }
        if effect.child.is_some()
            || top.id != effect.operation
            || top.pending_effect != Some(parent_effect)
        {
            return Err(ContinuationError::ParentNotActive);
        }
        if self.stack.len() >= self.limits.max_depth {
            return Err(ContinuationError::DepthLimit);
        }
        if self.operations >= self.limits.max_operations {
            return Err(ContinuationError::OperationLimit);
        }
        let id = self.alloc_operation(Some(parent_effect))?;
        let parent_index = self.stack.len() - 2;
        self.stack[parent_index].suspended = true;
        self.effects
            .iter_mut()
            .find(|e| e.id == parent_effect)
            .expect("validated effect")
            .child = Some(id);
        Ok(id)
    }
    /// Complete the active child and return control to the parent PHP wrapper.
    pub fn return_child(&mut self, operation: OperationId) -> Result<EffectId, ContinuationError> {
        self.ensure_running()?;
        if self.stack.len() < 2 {
            return Err(ContinuationError::RootHasNoParent);
        }
        let child = self.stack.last().ok_or(ContinuationError::NotStarted)?;
        if child.id != operation || child.pending_effect.is_some() {
            return Err(ContinuationError::WrongOperation);
        }
        let parent_effect = child
            .parent_effect
            .ok_or(ContinuationError::RootHasNoParent)?;
        self.stack.pop();
        let effect = self
            .effects
            .iter_mut()
            .find(|e| e.id == parent_effect)
            .ok_or(ContinuationError::WrongEffect)?;
        if effect.child != Some(operation) {
            return Err(ContinuationError::ParentNotActive);
        }
        effect.child = None;
        let parent = self.stack.last_mut().ok_or(ContinuationError::NotStarted)?;
        if parent.pending_effect != Some(parent_effect) || !parent.suspended {
            return Err(ContinuationError::ParentNotActive);
        }
        parent.suspended = false;
        Ok(parent_effect)
    }
    /// Resume a PHP effect exactly once, only after any child CallCore returned,
    /// and return its accepted outcome unchanged to the caller.
    ///
    /// A `PhpThrowable` remains an opaque PHP handle here. The caller owns the
    /// policy for catching, rethrowing, or otherwise routing it; this state
    /// machine does not decide PHP exception semantics.
    pub fn resume(
        &mut self,
        effect_id: EffectId,
        completion: Completion<T>,
    ) -> Result<Completion<T>, ContinuationError> {
        self.ensure_running()?;
        let effect = self
            .effects
            .iter()
            .find(|e| e.id == effect_id)
            .ok_or(ContinuationError::WrongEffect)?;
        if effect.resumed {
            return Err(ContinuationError::EffectAlreadyResumed);
        }
        if effect.child.is_some() {
            return Err(ContinuationError::ParentNotActive);
        }
        if let Completion::PhpThrowable(handle) = &completion {
            if handle.kind != PhpHandleKind::Throwable {
                return Err(ContinuationError::WrongHandleKind);
            }
            self.validate_handle(*handle)?;
        }
        let effect = self
            .effects
            .iter_mut()
            .find(|e| e.id == effect_id)
            .expect("validated effect");
        let top = self.stack.last_mut().ok_or(ContinuationError::NotStarted)?;
        if top.id != effect.operation || top.pending_effect != Some(effect_id) || top.suspended {
            return Err(ContinuationError::WrongEffect);
        }
        effect.resumed = true;
        top.pending_effect = None;
        Ok(completion)
    }
    pub fn finish_operation(
        &mut self,
        operation: OperationId,
    ) -> Result<Option<EffectId>, ContinuationError> {
        self.ensure_running()?;
        let top = self.stack.last().ok_or(ContinuationError::NotStarted)?;
        if top.id != operation {
            return Err(ContinuationError::WrongOperation);
        }
        if top.pending_effect.is_some() {
            return Err(ContinuationError::PendingEffect);
        }
        if self.stack.len() == 1 {
            self.root_returned = true;
            self.state = RequestState::RootReturned;
            return Ok(None);
        }
        let _parent = top
            .parent_effect
            .ok_or(ContinuationError::RootHasNoParent)?;
        self.return_child(operation).map(Some)
    }
    /// Close the in-memory request after the caller has observed PHP's
    /// `RequestFinished` boundary, including shutdown and output processing.
    /// This method only checks the Rust state machine; it cannot observe or
    /// prove that PHP shutdown/output processing actually completed.
    pub fn finish_request(&mut self, root: OperationId) -> Result<(), ContinuationError> {
        self.ensure_running()?;
        if !self.started {
            return Err(ContinuationError::NotStarted);
        }
        if !self.root_returned {
            return Err(ContinuationError::PendingEffect);
        }
        if self.stack.len() != 1 || self.stack[0].id != root {
            return Err(ContinuationError::NotAtRoot);
        }
        if self.stack[0].pending_effect.is_some() {
            return Err(ContinuationError::PendingEffect);
        }
        self.stack.clear();
        self.handles.clear();
        self.state = RequestState::Finished;
        Ok(())
    }
    pub fn abort(&mut self) {
        if matches!(
            self.state,
            RequestState::Running | RequestState::RootReturned
        ) {
            self.stack.clear();
            self.handles.clear();
            self.state = RequestState::Aborted;
        }
    }
    pub fn register_handle(&mut self, handle: PhpHandle) -> Result<(), ContinuationError> {
        self.ensure_running()?;
        if handle.request != self.request {
            return Err(ContinuationError::WrongRequest);
        }
        if self.handles.contains(&handle) {
            return Ok(());
        }
        if self.handles.len() >= self.limits.max_handles {
            return Err(ContinuationError::HandleLimit);
        }
        self.handles.push(handle);
        Ok(())
    }
    /// Invalidate the exact registered mirror after the PHP registry confirms
    /// the PHP value's final release. PHP itself remains responsible for
    /// releasing the PHP value; this only removes Rust-side request metadata.
    pub fn unregister_handle(&mut self, handle: PhpHandle) -> Result<(), ContinuationError> {
        self.ensure_running()?;
        if handle.request != self.request {
            return Err(ContinuationError::WrongRequest);
        }
        let index = self
            .handles
            .iter()
            .position(|registered| *registered == handle)
            .ok_or(ContinuationError::InvalidHandle)?;
        self.handles.remove(index);
        Ok(())
    }
    pub fn validate_handle(&self, handle: PhpHandle) -> Result<(), ContinuationError> {
        self.ensure_running()?;
        if handle.request != self.request {
            return Err(ContinuationError::WrongRequest);
        }
        if self.handles.contains(&handle) {
            Ok(())
        } else {
            Err(ContinuationError::InvalidHandle)
        }
    }
}
