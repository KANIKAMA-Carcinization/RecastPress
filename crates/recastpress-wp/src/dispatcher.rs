//! Named plugin.php-style dispatch for request-local native and resumable PHP callbacks.

use crate::{
    continuation::{Completion, PhpHandle, PhpHandleKind},
    hooks::{
        self, CallbackId, Context, ContextCallback, HookArgument, HookDiagnostic, HookError,
        HookPriority, HookScalar, HookStep, MAX_NESTING, Mode, PhpInvocation, PhpReleaseQueue,
        Storage,
    },
    value::{PhpKey, PhpValue},
};
use std::{
    collections::VecDeque,
    sync::{
        Arc, Weak,
        atomic::{AtomicU64, Ordering},
    },
};

static NEXT_DISPATCHER_ID: AtomicU64 = AtomicU64::new(1);

fn key_position<V>(entries: &[(PhpKey, V)], key: &PhpKey) -> Option<usize> {
    entries.iter().position(|(candidate, _)| candidate == key)
}

fn key_get<'a, V>(entries: &'a [(PhpKey, V)], key: &PhpKey) -> Option<&'a V> {
    key_position(entries, key).map(|index| &entries[index].1)
}

fn key_get_mut<'a, V>(entries: &'a mut [(PhpKey, V)], key: &PhpKey) -> Option<&'a mut V> {
    let index = key_position(entries, key)?;
    Some(&mut entries[index].1)
}

fn key_set<V>(entries: &mut Vec<(PhpKey, V)>, key: PhpKey, value: V) -> Option<V> {
    if let Some(existing) = key_get_mut(entries, &key) {
        return Some(std::mem::replace(existing, value));
    }
    entries.push((key, value));
    None
}

fn key_remove<V>(entries: &mut Vec<(PhpKey, V)>, key: &PhpKey) -> Option<V> {
    let index = key_position(entries, key)?;
    Some(entries.remove(index).1)
}

fn increment_key_counter(entries: &mut Vec<(PhpKey, u64)>, key: PhpKey) {
    if let Some(count) = key_get_mut(entries, &key) {
        *count = count.saturating_add(1);
    } else {
        entries.push((key, 1));
    }
}

pub type DispatcherCallback = ContextCallback<Dispatcher>;

#[derive(Debug)]
pub struct Dispatcher {
    bindings: HookBindingTable,
    slots: Vec<Option<Storage<Dispatcher>>>,
    current: Vec<HookScalar>,
    actions: Vec<(PhpKey, u64)>,
    filters: FilterCounterTable,
    active_calls: usize,
    diagnostics: Vec<HookDiagnostic>,
    dispatcher_id: u64,
    next_ticket: u64,
    resumable: Vec<ResumableDispatch>,
    advancing: Option<DispatchTicket>,
    next_target: u64,
    targets: Vec<TargetPin>,
    releases: PhpReleaseQueue,
    next_control: u64,
    control_stack: Vec<ActiveControl>,
}

#[derive(Clone, Debug, Default)]
struct HookBindingVersion {
    entries: Vec<(PhpKey, usize)>,
}

#[derive(Debug)]
struct HookBindingTable {
    current: Arc<HookBindingVersion>,
    versions: Vec<Weak<HookBindingVersion>>,
}

#[derive(Clone, Debug)]
struct HookBindingOperand {
    version: Arc<HookBindingVersion>,
}

#[derive(Clone, Debug, Default)]
struct FilterCounterVersion {
    entries: Vec<(PhpKey, u64)>,
}

#[derive(Debug)]
struct FilterCounterTable {
    current: Arc<FilterCounterVersion>,
}

#[derive(Clone, Debug)]
struct FilterCounterOperand {
    version: Arc<FilterCounterVersion>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FilterCounterOperandAccess {
    Isset,
    Read,
    WriteOne,
    Increment,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FilterCounterLookup {
    Value(u64),
    Missing,
    NullOperand,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FilterCounterMutation {
    Applied,
    Discarded,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FilterCounterResult {
    Lookup(FilterCounterLookup),
    Mutation(FilterCounterMutation),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum HookBindingLookupResult {
    Found(HookTarget),
    Missing,
    NullOperand,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum HookBindingRead {
    Found(usize),
    Missing,
    NullOperand,
}

impl HookBindingTable {
    fn new() -> Self {
        let current = Arc::new(HookBindingVersion::default());
        Self {
            versions: vec![Arc::downgrade(&current)],
            current,
        }
    }

    fn set(&mut self, key: PhpKey, value: usize) -> Option<usize> {
        self.mutate(|entries| key_set(entries, key, value))
    }

    fn remove(&mut self, key: &PhpKey) -> Option<usize> {
        self.mutate(|entries| key_remove(entries, key))
    }

    fn capture_operand(&self) -> HookBindingOperand {
        HookBindingOperand {
            version: Arc::clone(&self.current),
        }
    }

    fn read_operand(&self, operand: HookBindingOperand, key: &PhpKey) -> HookBindingRead {
        let current_version = Arc::ptr_eq(&self.current, &operand.version);
        let version_still_owned = Arc::strong_count(&operand.version) > 1;
        if !current_version && !version_still_owned {
            return HookBindingRead::NullOperand;
        }
        key_get(&operand.version.entries, key)
            .copied()
            .map_or(HookBindingRead::Missing, HookBindingRead::Found)
    }

    fn references_slot(&mut self, slot: usize) -> bool {
        self.versions.retain(|version| version.strong_count() > 0);
        if self.current.entries.iter().any(|(_, bound)| *bound == slot) {
            return true;
        }
        self.versions.iter().any(|version| {
            version
                .upgrade()
                .is_some_and(|version| version.entries.iter().any(|(_, bound)| *bound == slot))
        })
    }

    fn mutate<R>(&mut self, mutation: impl FnOnce(&mut Vec<(PhpKey, usize)>) -> R) -> R {
        let current_weak = Arc::downgrade(&self.current);
        self.versions
            .retain(|version| !version.ptr_eq(&current_weak));
        if Arc::strong_count(&self.current) > 1 {
            self.versions.push(current_weak);
            let next = Arc::new((*self.current).clone());
            self.current = next;
        } else {
            drop(current_weak);
        }
        let result = mutation(
            &mut Arc::get_mut(&mut self.current)
                .expect("unique hook binding table after copy-on-write")
                .entries,
        );
        self.versions.push(Arc::downgrade(&self.current));
        result
    }
}

impl FilterCounterTable {
    fn new() -> Self {
        let current = Arc::new(FilterCounterVersion::default());
        Self { current }
    }

    fn capture_operand(&self) -> FilterCounterOperand {
        FilterCounterOperand {
            version: Arc::clone(&self.current),
        }
    }

    fn read_operand(&self, operand: &FilterCounterOperand, key: &PhpKey) -> FilterCounterLookup {
        let current_version = Arc::ptr_eq(&self.current, &operand.version);
        let version_still_owned = Arc::strong_count(&operand.version) > 1;
        if !current_version && !version_still_owned {
            return FilterCounterLookup::NullOperand;
        }
        key_get(&operand.version.entries, key)
            .copied()
            .map_or(FilterCounterLookup::Missing, FilterCounterLookup::Value)
    }

    fn update_operand(
        &mut self,
        operand: FilterCounterOperand,
        key: PhpKey,
        access: FilterCounterOperandAccess,
    ) -> FilterCounterMutation {
        if !Arc::ptr_eq(&self.current, &operand.version) {
            return FilterCounterMutation::Discarded;
        }
        drop(operand);
        match access {
            FilterCounterOperandAccess::WriteOne => {
                self.mutate(|entries| {
                    key_set(entries, key, 1);
                });
            }
            FilterCounterOperandAccess::Increment => {
                self.mutate(|entries| increment_key_counter(entries, key));
            }
            FilterCounterOperandAccess::Isset | FilterCounterOperandAccess::Read => {
                unreachable!("lookup access cannot mutate the filter counter table")
            }
        }
        FilterCounterMutation::Applied
    }

    fn write_one_live(&mut self, key: PhpKey) {
        self.mutate(|entries| {
            key_set(entries, key, 1);
        });
    }

    fn increment_live(&mut self, key: PhpKey) {
        self.mutate(|entries| increment_key_counter(entries, key));
    }

    fn mutate<R>(&mut self, mutation: impl FnOnce(&mut Vec<(PhpKey, u64)>) -> R) -> R {
        if Arc::strong_count(&self.current) > 1 {
            self.current = Arc::new((*self.current).clone());
        }
        mutation(
            &mut Arc::get_mut(&mut self.current)
                .expect("unique filter counter table after copy-on-write")
                .entries,
        )
    }
}

impl std::ops::Deref for FilterCounterTable {
    type Target = [(PhpKey, u64)];

    fn deref(&self) -> &Self::Target {
        &self.current.entries
    }
}

impl std::ops::Deref for HookBindingTable {
    type Target = [(PhpKey, usize)];

    fn deref(&self) -> &Self::Target {
        &self.current.entries
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct DispatchTicket {
    dispatcher: u64,
    sequence: u64,
}

/// Opaque pin to the exact WP_Hook-equivalent object selected by a PHP API wrapper.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct HookTarget {
    dispatcher: u64,
    sequence: u64,
}

/// Captures `isset($target->callbacks[$priority])` after its PHP boundary effect.
#[derive(Clone, Debug, PartialEq)]
pub struct PrioritySnapshot {
    dispatcher: u64,
    target_sequence: u64,
    priority_key: PhpKey,
    existed: bool,
}

/// Captures the callback `isset` result after its PHP boundary effect.
#[derive(Clone, Debug, PartialEq)]
pub struct CallbackSnapshot {
    dispatcher: u64,
    target_sequence: u64,
    priority_key: PhpKey,
    callback: CallbackId,
    exists: bool,
}

impl CallbackSnapshot {
    /// Whether the callback was present at the PHP `isset` boundary.
    ///
    /// Later warning-handler reentry may change the live callback map, but the
    /// outer `remove_filter` call still returns this captured result.
    pub fn exists(&self) -> bool {
        self.exists
    }
}

#[derive(Debug)]
struct TargetPin {
    sequence: u64,
    key: PhpKey,
    slot: usize,
}

#[derive(Debug)]
pub struct ControlOperation {
    dispatcher: u64,
    sequence: u64,
    finish: ControlFinish,
    pending_release: Option<PhpHandle>,
    pending_releases: VecDeque<PhpHandle>,
    pending_throwable: Option<PhpHandle>,
    priority_read_pending: bool,
    priority_read_complete: bool,
    priority_read_missing: bool,
    priority_bucket_empty: Option<bool>,
    priority_missing_offset_needed: bool,
    priority_missing_offset_pending: bool,
    priority_unset_pending: bool,
    priority_unset_complete: bool,
    priority_refresh_complete: bool,
    completed: bool,
}

#[derive(Debug)]
struct ActiveControl {
    sequence: u64,
    waiting_release: bool,
    waiting_boundary: bool,
    pinned_slots: Vec<usize>,
}

#[derive(Debug)]
enum ControlFinish {
    Add {
        slot: usize,
        priority: HookPriority,
        priority_existed: bool,
    },
    Remove {
        key: PhpKey,
        slot: usize,
        priority: HookPriority,
        removed: bool,
        priority_read_checkpoint: bool,
    },
    RemoveAll {
        key: PhpKey,
        slot: Option<usize>,
    },
    WriteHook {
        slot: usize,
    },
    UnbindHook,
    ReleaseTarget,
    HookBindingLookup {
        operand: Option<HookBindingOperand>,
        result: Option<HookBindingLookupResult>,
    },
    FilterCounterOperand {
        access: FilterCounterOperandAccess,
        operand: Option<FilterCounterOperand>,
        result: Option<FilterCounterResult>,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ControlResult {
    Added(bool),
    Removed(bool),
    RemovedAll,
    HookWritten,
    HookUnbound,
    TargetReleased,
    HookBindingLookup(HookBindingLookupResult),
    FilterCounter(FilterCounterResult),
}

#[derive(Clone, Debug, PartialEq)]
pub enum ControlStep {
    ReleasePhp(PhpHandle),
    PriorityRead {
        priority: HookPriority,
        missing: bool,
    },
    PriorityMissingOffset(HookPriority),
    PriorityUnset(HookPriority),
    Complete(ControlResult),
    PhpThrowable(PhpHandle),
}

#[derive(Clone, Debug, PartialEq)]
pub enum DispatchStep {
    InvokePhp(PhpInvocation),
    ReleasePhp(PhpHandle),
    Boundary(DispatchBoundary),
    Complete(PhpValue),
    PhpThrowable(PhpHandle),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DispatchBoundary {
    FilterCounterWrite,
    FilterCounterIncrement,
    NamedHookIsset,
    NamedHookRead,
    MissingOffsetAll,
    NullForeachAll,
    MissingOffset,
    NullMethodApplyFilters,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DispatchPhase {
    CounterWrite,
    CounterIncrement,
    All,
    Named,
    NamedHookIsset,
    NamedHookRead,
    MissingOffsetAll,
    NullForeachAll,
    MissingOffset,
    NullMethodApplyFilters,
    Complete,
}

#[derive(Debug)]
struct ResumableDispatch {
    ticket: DispatchTicket,
    hook: HookArgument,
    value: PhpValue,
    extra: Vec<PhpValue>,
    base_depth: usize,
    current_pushes: usize,
    phase: DispatchPhase,
    slot: Option<usize>,
    frame_started: bool,
    waiting_for_php: bool,
    waiting_for_boundary: bool,
    waiting_for_release: bool,
    pending_releases: VecDeque<PhpHandle>,
    pending_release: Option<PhpHandle>,
    pending_throwable: Option<PhpHandle>,
    throwable_abandoned: bool,
    effectful: bool,
    binding_operand: Option<HookBindingOperand>,
}

impl Default for Dispatcher {
    fn default() -> Self {
        Self {
            bindings: HookBindingTable::new(),
            slots: Vec::new(),
            current: Vec::new(),
            actions: Vec::new(),
            filters: FilterCounterTable::new(),
            active_calls: 0,
            diagnostics: Vec::new(),
            dispatcher_id: NEXT_DISPATCHER_ID.fetch_add(1, Ordering::Relaxed),
            next_ticket: 1,
            resumable: Vec::new(),
            advancing: None,
            next_target: 1,
            targets: Vec::new(),
            releases: PhpReleaseQueue::default(),
            next_control: 1,
            control_stack: Vec::new(),
        }
    }
}

impl Context for Dispatcher {
    fn storage(&mut self, slot: usize) -> &mut Storage<Self> {
        self.slots[slot]
            .as_mut()
            .expect("active or registered hook object")
    }

    fn release_queue(&self) -> &PhpReleaseQueue {
        &self.releases
    }
}

impl Dispatcher {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn add(&mut self, name: &str, priority: i64, callback: DispatcherCallback) {
        self.add_typed(
            &HookArgument::bytes(name),
            HookPriority::integer(priority),
            callback,
        );
    }

    pub fn add_typed(
        &mut self,
        name: &HookArgument,
        priority: HookPriority,
        mut callback: DispatcherCallback,
    ) {
        callback.attach_release_queue(&self.releases);
        let slot = self.ensure_slot(name);
        self.storage(slot).add(priority, callback);
        self.capture_diagnostics(slot);
    }

    /// Begin adding a callable while preserving the PHP assignment/release boundary.
    pub fn begin_add(
        &mut self,
        name: &str,
        priority: i64,
        callback: DispatcherCallback,
    ) -> Result<ControlOperation, HookError> {
        self.begin_add_typed(
            &HookArgument::bytes(name),
            HookPriority::integer(priority),
            callback,
        )
    }

    pub fn begin_add_typed(
        &mut self,
        name: &HookArgument,
        priority: HookPriority,
        mut callback: DispatcherCallback,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let slot = self.ensure_slot(name);
        callback.attach_release_queue(&self.releases);
        let priority_existed = self.storage(slot).add_entry(&priority, callback);
        self.new_control(ControlFinish::Add {
            slot,
            priority,
            priority_existed,
        })
    }

    /// Begin removing one callback. A final release is yielded before bucket cleanup.
    pub fn begin_remove(
        &mut self,
        name: &str,
        priority: i64,
        id: &CallbackId,
    ) -> Result<ControlOperation, HookError> {
        self.begin_remove_typed(
            &HookArgument::bytes(name),
            HookPriority::integer(priority),
            id,
        )
    }

    pub fn begin_remove_typed(
        &mut self,
        name: &HookArgument,
        priority: HookPriority,
        id: &CallbackId,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let Some(&slot) = key_get(&self.bindings, &name.array_key) else {
            return self.new_control(ControlFinish::Remove {
                key: name.array_key.clone(),
                slot: usize::MAX,
                priority,
                removed: false,
                priority_read_checkpoint: false,
            });
        };
        let removed = self.storage(slot).remove_entry(&priority, id);
        self.new_control(ControlFinish::Remove {
            key: name.array_key.clone(),
            slot,
            priority,
            removed,
            priority_read_checkpoint: false,
        })
    }

    /// Begin removing a priority or all callbacks, yielding final releases before resorting.
    pub fn begin_remove_all(
        &mut self,
        name: &str,
        priority: Option<i64>,
    ) -> Result<ControlOperation, HookError> {
        self.begin_remove_all_typed(
            &HookArgument::bytes(name),
            priority.map(HookPriority::integer),
        )
    }

    pub fn begin_remove_all_typed(
        &mut self,
        name: &HookArgument,
        priority: Option<HookPriority>,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let slot = key_get(&self.bindings, &name.array_key).copied();
        if let Some(slot) = slot {
            self.storage(slot).remove_all_entries(priority.as_ref());
        }
        self.new_control(ControlFinish::RemoveAll {
            key: name.array_key.clone(),
            slot,
        })
    }

    pub fn advance_control(
        &mut self,
        operation: &mut ControlOperation,
    ) -> Result<ControlStep, HookError> {
        self.check_control_top(operation)?;
        if operation.pending_release.is_some() {
            return Err(HookError::PhpReleasePending);
        }
        if operation.priority_read_pending
            || operation.priority_missing_offset_pending
            || operation.priority_unset_pending
        {
            return Err(HookError::PhpPriorityAccessPending);
        }
        if matches!(
            &operation.finish,
            ControlFinish::HookBindingLookup { result: None, .. }
                | ControlFinish::FilterCounterOperand { result: None, .. }
        ) && self
            .control_stack
            .last()
            .is_some_and(|control| control.waiting_boundary)
        {
            return Err(HookError::PhpBoundaryPending);
        }
        operation.pending_releases.extend(self.releases.drain());
        if let Some(handle) = operation.pending_releases.pop_front() {
            operation.pending_release = Some(handle);
            self.control_stack
                .last_mut()
                .expect("active control")
                .waiting_release = true;
            return Ok(ControlStep::ReleasePhp(handle));
        }
        if let Some(handle) = operation.pending_throwable {
            self.control_stack.pop();
            operation.completed = true;
            self.collect_unused();
            return Ok(ControlStep::PhpThrowable(handle));
        }
        if !operation.priority_read_complete
            && let ControlFinish::Remove {
                priority,
                slot,
                removed: true,
                priority_read_checkpoint: true,
                ..
            } = &operation.finish
        {
            let missing = !self.storage_ref(*slot).has_priority(priority.key());
            operation.priority_read_missing = missing;
            operation.priority_read_complete = true;
            operation.priority_read_pending = true;
            self.control_stack
                .last_mut()
                .expect("active control")
                .waiting_boundary = true;
            return Ok(ControlStep::PriorityRead {
                priority: priority.clone(),
                missing,
            });
        }
        if operation.priority_missing_offset_needed {
            let ControlFinish::Remove { priority, .. } = &operation.finish else {
                unreachable!("priority missing-offset belongs to remove")
            };
            operation.priority_missing_offset_pending = true;
            operation.priority_missing_offset_needed = false;
            self.control_stack
                .last_mut()
                .expect("active control")
                .waiting_boundary = true;
            return Ok(ControlStep::PriorityMissingOffset(priority.clone()));
        }
        if operation.priority_bucket_empty == Some(true) && !operation.priority_unset_complete {
            let ControlFinish::Remove { priority, .. } = &operation.finish else {
                unreachable!("priority unset belongs to remove")
            };
            operation.priority_unset_pending = true;
            self.control_stack
                .last_mut()
                .expect("active control")
                .waiting_boundary = true;
            return Ok(ControlStep::PriorityUnset(priority.clone()));
        }
        if operation.priority_unset_complete && !operation.priority_refresh_complete {
            if let ControlFinish::Remove { slot, priority, .. } = &operation.finish
                && *slot != usize::MAX
            {
                self.storage(*slot).finish_unset_priority(priority);
                self.capture_diagnostics(*slot);
                self.collect_unused();
            }
            operation.priority_refresh_complete = true;
        }
        let result = match &operation.finish {
            ControlFinish::Add {
                slot,
                priority,
                priority_existed,
            } => {
                self.storage(*slot).finish_add(priority, *priority_existed);
                self.capture_diagnostics(*slot);
                ControlResult::Added(true)
            }
            ControlFinish::Remove {
                key,
                slot,
                priority,
                removed,
                priority_read_checkpoint,
                ..
            } => {
                if *slot != usize::MAX && *removed && !priority_read_checkpoint {
                    self.storage(*slot).finish_remove(priority);
                    self.capture_diagnostics(*slot);
                    self.unbind_empty(key, *slot);
                    self.collect_unused();
                }
                ControlResult::Removed(*removed)
            }
            ControlFinish::RemoveAll { key, slot } => {
                if let Some(slot) = slot {
                    self.storage(*slot).finish_remove_all();
                    self.capture_diagnostics(*slot);
                    self.unbind_empty(key, *slot);
                    self.collect_unused();
                }
                ControlResult::RemovedAll
            }
            ControlFinish::WriteHook { .. } => ControlResult::HookWritten,
            ControlFinish::UnbindHook => ControlResult::HookUnbound,
            ControlFinish::ReleaseTarget => ControlResult::TargetReleased,
            ControlFinish::HookBindingLookup { result, .. } => {
                ControlResult::HookBindingLookup(result.ok_or(HookError::InvalidControlOperation)?)
            }
            ControlFinish::FilterCounterOperand { result, .. } => {
                ControlResult::FilterCounter(result.ok_or(HookError::InvalidControlOperation)?)
            }
        };
        self.control_stack.pop();
        operation.completed = true;
        self.collect_unused();
        Ok(ControlStep::Complete(result))
    }

    pub fn resume_control_release(
        &mut self,
        operation: &mut ControlOperation,
        handle: PhpHandle,
    ) -> Result<(), HookError> {
        self.resume_control_release_completion(operation, handle, Completion::Value(PhpValue::Null))
    }

    pub fn resume_control_release_completion(
        &mut self,
        operation: &mut ControlOperation,
        handle: PhpHandle,
        completion: Completion<PhpValue>,
    ) -> Result<(), HookError> {
        self.check_control_top(operation)?;
        if operation.pending_release != Some(handle) {
            return Err(HookError::InvalidPhpCallableHandle);
        }
        if let Completion::PhpThrowable(thrown) = completion {
            if thrown.kind() != PhpHandleKind::Throwable {
                return Err(HookError::InvalidPhpThrowableHandle);
            }
            operation.pending_throwable = Some(thrown);
        }
        operation.pending_release = None;
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_release = false;
        Ok(())
    }

    /// Resume the PHP array-read checkpoint after a removed callback's release.
    /// PHP performs the actual `$callbacks[$priority]` read, including any warning
    /// handler reentry, before this cursor is allowed to clean up the bucket.
    pub fn resume_control_priority_read(
        &mut self,
        operation: &mut ControlOperation,
        completion: Completion<PhpValue>,
    ) -> Result<(), HookError> {
        self.check_control_top(operation)?;
        if !operation.priority_read_pending {
            return Err(HookError::NoPhpPriorityAccessPending);
        }
        if let Completion::PhpThrowable(thrown) = completion {
            if thrown.kind() != PhpHandleKind::Throwable {
                return Err(HookError::InvalidPhpThrowableHandle);
            }
            operation.pending_throwable = Some(thrown);
        } else if let ControlFinish::Remove { slot, priority, .. } = &operation.finish {
            if *slot != usize::MAX {
                match self
                    .storage_ref(*slot)
                    .priority_bucket_empty(priority.key())
                {
                    Some(empty) => operation.priority_bucket_empty = Some(empty),
                    None => operation.priority_missing_offset_needed = true,
                }
            } else {
                operation.priority_bucket_empty = Some(true);
            }
        }
        operation.priority_read_pending = false;
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = false;
        Ok(())
    }

    pub fn resume_control_priority_missing_offset(
        &mut self,
        operation: &mut ControlOperation,
        completion: Completion<PhpValue>,
    ) -> Result<(), HookError> {
        self.check_control_top(operation)?;
        if !operation.priority_missing_offset_pending {
            return Err(HookError::NoPhpPriorityAccessPending);
        }
        if let Completion::PhpThrowable(thrown) = completion {
            if thrown.kind() != PhpHandleKind::Throwable {
                return Err(HookError::InvalidPhpThrowableHandle);
            }
            operation.pending_throwable = Some(thrown);
        } else {
            operation.priority_bucket_empty = Some(true);
        }
        operation.priority_missing_offset_pending = false;
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = false;
        Ok(())
    }

    pub fn resume_control_priority_unset(
        &mut self,
        operation: &mut ControlOperation,
        completion: Completion<PhpValue>,
    ) -> Result<(), HookError> {
        self.check_control_top(operation)?;
        if !operation.priority_unset_pending {
            return Err(HookError::NoPhpPriorityAccessPending);
        }
        if let Completion::PhpThrowable(thrown) = completion {
            if thrown.kind() != PhpHandleKind::Throwable {
                return Err(HookError::InvalidPhpThrowableHandle);
            }
            operation.pending_throwable = Some(thrown);
        } else if let ControlFinish::Remove { slot, priority, .. } = &operation.finish {
            if *slot != usize::MAX {
                self.storage(*slot).unset_priority_bucket(priority);
            }
            operation.priority_unset_complete = true;
        }
        operation.priority_unset_pending = false;
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = false;
        Ok(())
    }

    fn begin_control_allowed(&self) -> Result<(), HookError> {
        if self.advancing.is_some() {
            return Err(HookError::ControlOperationOrder);
        }
        if self
            .control_stack
            .last()
            .is_some_and(|control| !control.waiting_release && !control.waiting_boundary)
        {
            return Err(HookError::ControlOperationOrder);
        }
        Ok(())
    }

    /// Begin a `$wp_filter[$hook]` operand before PHP converts the hook key.
    /// Nested Core calls may copy-on-write the live table while this cursor keeps
    /// the original version and its hook objects alive.
    pub fn begin_hook_binding_lookup(&mut self) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let operand = self.bindings.capture_operand();
        let operation = self.new_control(ControlFinish::HookBindingLookup {
            operand: Some(operand),
            result: None,
        })?;
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = true;
        Ok(operation)
    }

    /// Resume a hook-table operand after PHP has normalized the offset. A found
    /// object is pinned before retiring the old table version, so callback releases
    /// can be drained without losing the receiver selected by this fetch.
    pub fn resume_hook_binding_lookup(
        &mut self,
        operation: &mut ControlOperation,
        name: &HookArgument,
    ) -> Result<(), HookError> {
        self.check_control_top(operation)?;
        if !self
            .control_stack
            .last()
            .is_some_and(|control| control.waiting_boundary)
        {
            return Err(HookError::NoPhpBoundaryPending);
        }
        let operand = match &mut operation.finish {
            ControlFinish::HookBindingLookup { operand, result } if result.is_none() => {
                operand.take().ok_or(HookError::InvalidControlOperation)?
            }
            _ => return Err(HookError::InvalidControlOperation),
        };
        let result = match self.bindings.read_operand(operand, &name.array_key) {
            HookBindingRead::Found(slot) => {
                HookBindingLookupResult::Found(self.pin_target(name.array_key.clone(), slot)?)
            }
            HookBindingRead::Missing => HookBindingLookupResult::Missing,
            HookBindingRead::NullOperand => HookBindingLookupResult::NullOperand,
        };
        if let ControlFinish::HookBindingLookup {
            result: stored_result,
            ..
        } = &mut operation.finish
        {
            *stored_result = Some(result);
        }
        self.collect_unused();
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = false;
        Ok(())
    }

    /// Retire a hook-table operand when PHP key conversion throws. The original
    /// Throwable is returned only after any leases owned by its old table version
    /// have passed through the normal release cursor.
    pub fn cancel_hook_binding_lookup(
        &mut self,
        operation: &mut ControlOperation,
        thrown: PhpHandle,
    ) -> Result<(), HookError> {
        self.check_control_top(operation)?;
        if thrown.kind() != PhpHandleKind::Throwable {
            return Err(HookError::InvalidPhpThrowableHandle);
        }
        if !self
            .control_stack
            .last()
            .is_some_and(|control| control.waiting_boundary)
        {
            return Err(HookError::NoPhpBoundaryPending);
        }
        match &mut operation.finish {
            ControlFinish::HookBindingLookup { operand, result } if result.is_none() => {
                operand.take().ok_or(HookError::InvalidControlOperation)?;
            }
            _ => return Err(HookError::InvalidControlOperation),
        }
        operation.pending_throwable = Some(thrown);
        self.collect_unused();
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = false;
        Ok(())
    }

    /// Capture the `$wp_filters` operand before PHP converts the hook array key.
    /// The returned control cursor is single-use and participates in the same
    /// nested-operation ordering as hook binding lookups.
    pub fn begin_filter_counter_operand(
        &mut self,
        access: FilterCounterOperandAccess,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let operand = self.filters.capture_operand();
        let operation = self.new_control(ControlFinish::FilterCounterOperand {
            access,
            operand: Some(operand),
            result: None,
        })?;
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = true;
        Ok(operation)
    }

    /// Resolve a captured counter operand after PHP has completed key conversion.
    /// Stale sole-owner reads are NullOperand; a separately-held old version remains
    /// readable. A stale write/increment is reported as Discarded from the live map.
    pub fn resume_filter_counter_operand(
        &mut self,
        operation: &mut ControlOperation,
        name: &HookArgument,
    ) -> Result<(), HookError> {
        self.check_control_top(operation)?;
        if !self
            .control_stack
            .last()
            .is_some_and(|control| control.waiting_boundary)
        {
            return Err(HookError::NoPhpBoundaryPending);
        }
        let (access, operand) = match &mut operation.finish {
            ControlFinish::FilterCounterOperand {
                access,
                operand,
                result,
            } if result.is_none() => (
                *access,
                operand.take().ok_or(HookError::InvalidControlOperation)?,
            ),
            _ => return Err(HookError::InvalidControlOperation),
        };
        let result = match access {
            FilterCounterOperandAccess::Isset | FilterCounterOperandAccess::Read => {
                FilterCounterResult::Lookup(self.filters.read_operand(&operand, &name.array_key))
            }
            FilterCounterOperandAccess::WriteOne | FilterCounterOperandAccess::Increment => {
                FilterCounterResult::Mutation(self.filters.update_operand(
                    operand,
                    name.array_key.clone(),
                    access,
                ))
            }
        };
        if let ControlFinish::FilterCounterOperand {
            result: stored_result,
            ..
        } = &mut operation.finish
        {
            *stored_result = Some(result);
        }
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = false;
        Ok(())
    }

    /// Retire a suspended counter operand when PHP key conversion throws.
    pub fn cancel_filter_counter_operand(
        &mut self,
        operation: &mut ControlOperation,
        thrown: PhpHandle,
    ) -> Result<(), HookError> {
        self.check_control_top(operation)?;
        if thrown.kind() != PhpHandleKind::Throwable {
            return Err(HookError::InvalidPhpThrowableHandle);
        }
        if !self
            .control_stack
            .last()
            .is_some_and(|control| control.waiting_boundary)
        {
            return Err(HookError::NoPhpBoundaryPending);
        }
        match &mut operation.finish {
            ControlFinish::FilterCounterOperand {
                operand, result, ..
            } if result.is_none() => {
                operand.take().ok_or(HookError::InvalidControlOperation)?;
            }
            _ => return Err(HookError::InvalidControlOperation),
        }
        operation.pending_throwable = Some(thrown);
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = false;
        Ok(())
    }

    fn new_control(&mut self, finish: ControlFinish) -> Result<ControlOperation, HookError> {
        let sequence = self.next_control;
        self.next_control = self
            .next_control
            .checked_add(1)
            .ok_or(HookError::InvalidControlOperation)?;
        let pinned_slots = match &finish {
            ControlFinish::Add { slot, .. } => vec![*slot],
            ControlFinish::Remove { slot, .. } if *slot != usize::MAX => vec![*slot],
            ControlFinish::RemoveAll { slot, .. } => slot.iter().copied().collect(),
            ControlFinish::WriteHook { slot } => vec![*slot],
            ControlFinish::UnbindHook => Vec::new(),
            ControlFinish::ReleaseTarget => Vec::new(),
            ControlFinish::HookBindingLookup { .. } => Vec::new(),
            ControlFinish::FilterCounterOperand { .. } => Vec::new(),
            ControlFinish::Remove { .. } => Vec::new(),
        };
        self.control_stack.push(ActiveControl {
            sequence,
            waiting_release: false,
            waiting_boundary: false,
            pinned_slots,
        });
        Ok(ControlOperation {
            dispatcher: self.dispatcher_id,
            sequence,
            finish,
            pending_release: None,
            pending_releases: VecDeque::new(),
            pending_throwable: None,
            priority_read_pending: false,
            priority_read_complete: false,
            priority_read_missing: false,
            priority_bucket_empty: None,
            priority_missing_offset_needed: false,
            priority_missing_offset_pending: false,
            priority_unset_pending: false,
            priority_unset_complete: false,
            priority_refresh_complete: false,
            completed: false,
        })
    }

    fn check_control_top(&self, operation: &ControlOperation) -> Result<(), HookError> {
        if operation.dispatcher != self.dispatcher_id || operation.completed {
            return Err(HookError::InvalidControlOperation);
        }
        if self.control_stack.last().map(|control| control.sequence) != Some(operation.sequence) {
            return Err(HookError::ControlOperationOrder);
        }
        Ok(())
    }

    fn ensure_slot(&mut self, name: &HookArgument) -> usize {
        if let Some(&slot) = key_get(&self.bindings, &name.array_key) {
            return slot;
        }
        let slot = self.allocate_storage();
        self.bindings.set(name.array_key.clone(), slot);
        slot
    }

    fn allocate_storage(&mut self) -> usize {
        let slot = self
            .slots
            .iter()
            .position(Option::is_none)
            .unwrap_or(self.slots.len());
        if slot == self.slots.len() {
            self.slots.push(Some(Storage::default()));
        } else {
            self.slots[slot] = Some(Storage::default());
        }
        slot
    }

    /// Test the current global hook-array binding, including an empty hook object.
    pub fn hook_is_set(&self, name: &HookArgument) -> bool {
        key_get(&self.bindings, &name.array_key).is_some()
    }

    /// Model `$wp_filter[$hook] = new WP_Hook()` after the PHP write-offset
    /// boundary. The replacement becomes visible before old callbacks release.
    pub fn begin_write_hook(&mut self, name: &HookArgument) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let slot = self.allocate_storage();
        self.bindings.set(name.array_key.clone(), slot);
        self.collect_unused();
        self.new_control(ControlFinish::WriteHook { slot })
    }

    /// Remove the global binding after the PHP hook-unset boundary. This
    /// deliberately unbinds whatever object is live after handler reentry.
    pub fn begin_unbind_hook(
        &mut self,
        name: &HookArgument,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        self.bindings.remove(&name.array_key);
        self.collect_unused();
        self.new_control(ControlFinish::UnbindHook)
    }

    /// `None` means the global hook object is missing; otherwise reports whether
    /// its callbacks array itself is empty (distinct from has_callbacks).
    pub fn hook_callbacks_empty(&self, name: &HookArgument) -> Option<bool> {
        let slot = key_get(&self.bindings, &name.array_key).copied()?;
        Some(self.storage_ref(slot).callbacks_empty())
    }

    /// After the PHP read-offset boundary, pin whichever object is live now.
    pub fn capture_current_target(
        &mut self,
        name: &HookArgument,
    ) -> Result<Option<HookTarget>, HookError> {
        self.capture_target(name, false)
    }

    /// plugin.php's post-remove check reads the current binding, then unsets it
    /// only when that exact current object's callbacks array is empty.
    pub fn unbind_current_if_callbacks_empty(&mut self, name: &HookArgument) -> bool {
        let Some(slot) = key_get(&self.bindings, &name.array_key).copied() else {
            return false;
        };
        if !self.storage_ref(slot).callbacks_empty() {
            return false;
        }
        self.bindings.remove(&name.array_key);
        self.collect_unused();
        true
    }

    /// Capture the object selected by a PHP hook-array lookup. `create` mirrors
    /// add_filter's empty WP_Hook creation even when no callback ID can be built.
    pub fn capture_target(
        &mut self,
        name: &HookArgument,
        create: bool,
    ) -> Result<Option<HookTarget>, HookError> {
        let slot = if create {
            Some(self.ensure_slot(name))
        } else {
            key_get(&self.bindings, &name.array_key).copied()
        };
        let Some(slot) = slot else {
            return Ok(None);
        };
        Ok(Some(self.pin_target(name.array_key.clone(), slot)?))
    }

    fn pin_target(&mut self, key: PhpKey, slot: usize) -> Result<HookTarget, HookError> {
        let sequence = self.next_target;
        self.next_target = self
            .next_target
            .checked_add(1)
            .ok_or(HookError::InvalidHookTarget)?;
        self.targets.push(TargetPin {
            sequence,
            key,
            slot,
        });
        Ok(HookTarget {
            dispatcher: self.dispatcher_id,
            sequence,
        })
    }

    pub fn release_target(&mut self, target: HookTarget) -> Result<(), HookError> {
        if target.dispatcher != self.dispatcher_id {
            return Err(HookError::InvalidHookTarget);
        }
        let Some(index) = self
            .targets
            .iter()
            .position(|pin| pin.sequence == target.sequence)
        else {
            return Err(HookError::InvalidHookTarget);
        };
        self.targets.remove(index);
        self.collect_unused();
        Ok(())
    }

    pub fn begin_release_target(
        &mut self,
        target: HookTarget,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        if target.dispatcher != self.dispatcher_id {
            return Err(HookError::InvalidHookTarget);
        }
        let Some(index) = self
            .targets
            .iter()
            .position(|pin| pin.sequence == target.sequence)
        else {
            return Err(HookError::InvalidHookTarget);
        };
        self.targets.remove(index);
        self.collect_unused();
        self.new_control(ControlFinish::ReleaseTarget)
    }

    fn target_parts(&self, target: HookTarget) -> Result<(PhpKey, usize), HookError> {
        if target.dispatcher != self.dispatcher_id {
            return Err(HookError::InvalidHookTarget);
        }
        self.targets
            .iter()
            .find(|pin| pin.sequence == target.sequence)
            .map(|pin| (pin.key.clone(), pin.slot))
            .ok_or(HookError::InvalidHookTarget)
    }

    pub fn capture_priority_snapshot(
        &self,
        target: HookTarget,
        priority: &HookPriority,
    ) -> Result<PrioritySnapshot, HookError> {
        let (_, slot) = self.target_parts(target)?;
        Ok(PrioritySnapshot {
            dispatcher: self.dispatcher_id,
            target_sequence: target.sequence,
            priority_key: priority.key().clone(),
            existed: self.storage_ref(slot).has_priority(priority.key()),
        })
    }

    /// Test the callbacks array on the pinned WP_Hook object, even if the global
    /// hook binding has since been replaced or removed.
    pub fn target_callbacks_empty(&self, target: HookTarget) -> Result<bool, HookError> {
        let (_, slot) = self.target_parts(target)?;
        Ok(self.storage_ref(slot).callbacks_empty())
    }

    pub fn add_target(
        &mut self,
        target: HookTarget,
        priority: HookPriority,
        mut callback: DispatcherCallback,
    ) -> Result<(), HookError> {
        let (_, slot) = self.target_parts(target)?;
        callback.attach_release_queue(&self.releases);
        self.storage(slot).add(priority, callback);
        self.capture_diagnostics(slot);
        Ok(())
    }

    pub fn begin_add_target(
        &mut self,
        target: HookTarget,
        priority: HookPriority,
        callback: DispatcherCallback,
    ) -> Result<ControlOperation, HookError> {
        let snapshot = self.capture_priority_snapshot(target, &priority)?;
        self.begin_add_target_with_snapshot(target, priority, snapshot, callback)
    }

    pub fn begin_add_target_with_snapshot(
        &mut self,
        target: HookTarget,
        priority: HookPriority,
        snapshot: PrioritySnapshot,
        mut callback: DispatcherCallback,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let (_, slot) = self.target_parts(target)?;
        if snapshot.dispatcher != self.dispatcher_id
            || snapshot.target_sequence != target.sequence
            || snapshot.priority_key != *priority.key()
        {
            return Err(HookError::InvalidHookTarget);
        }
        callback.attach_release_queue(&self.releases);
        self.storage(slot).add_entry(&priority, callback);
        self.new_control(ControlFinish::Add {
            slot,
            priority,
            priority_existed: snapshot.existed,
        })
    }

    pub fn begin_remove_target(
        &mut self,
        target: HookTarget,
        priority: HookPriority,
        id: &CallbackId,
    ) -> Result<ControlOperation, HookError> {
        let snapshot = self.capture_callback_snapshot(target, &priority, id)?;
        self.begin_remove_target_with_snapshot(target, priority, id, snapshot)
    }

    pub fn capture_callback_snapshot(
        &self,
        target: HookTarget,
        priority: &HookPriority,
        id: &CallbackId,
    ) -> Result<CallbackSnapshot, HookError> {
        let (_, slot) = self.target_parts(target)?;
        Ok(CallbackSnapshot {
            dispatcher: self.dispatcher_id,
            target_sequence: target.sequence,
            priority_key: priority.key().clone(),
            callback: id.clone(),
            exists: self.storage_ref(slot).has_at(priority, id),
        })
    }

    pub fn begin_remove_target_with_snapshot(
        &mut self,
        target: HookTarget,
        priority: HookPriority,
        id: &CallbackId,
        snapshot: CallbackSnapshot,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let (key, slot) = self.target_parts(target)?;
        if snapshot.dispatcher != self.dispatcher_id
            || snapshot.target_sequence != target.sequence
            || snapshot.priority_key != *priority.key()
            || snapshot.callback != *id
        {
            return Err(HookError::InvalidHookTarget);
        }
        if snapshot.exists {
            self.storage(slot).remove_entry(&priority, id);
        }
        self.new_control(ControlFinish::Remove {
            key,
            slot,
            priority,
            removed: snapshot.exists,
            priority_read_checkpoint: true,
        })
    }

    pub fn has_callbacks_target(&self, target: HookTarget) -> Result<bool, HookError> {
        let (_, slot) = self.target_parts(target)?;
        Ok(self.storage_ref(slot).has_callbacks())
    }

    pub fn has_at_target(
        &self,
        target: HookTarget,
        priority: &HookPriority,
        id: &CallbackId,
    ) -> Result<bool, HookError> {
        let (_, slot) = self.target_parts(target)?;
        Ok(self.storage_ref(slot).has_at(priority, id))
    }

    pub fn priority_of_target(
        &self,
        target: HookTarget,
        id: &CallbackId,
    ) -> Result<Option<PhpKey>, HookError> {
        let (_, slot) = self.target_parts(target)?;
        Ok(self.storage_ref(slot).priority_of(id))
    }

    pub fn remove(&mut self, name: &str, priority: i64, id: &CallbackId) -> bool {
        self.remove_typed(
            &HookArgument::bytes(name),
            &HookPriority::integer(priority),
            id,
        )
    }
    pub fn remove_typed(
        &mut self,
        name: &HookArgument,
        priority: &HookPriority,
        id: &CallbackId,
    ) -> bool {
        let Some(&slot) = key_get(&self.bindings, &name.array_key) else {
            return false;
        };
        let removed = self.storage(slot).remove(priority.clone(), id);
        self.capture_diagnostics(slot);
        self.unbind_empty(&name.array_key, slot);
        self.collect_unused();
        removed
    }
    pub fn remove_all(&mut self, name: &str, priority: Option<i64>) {
        self.remove_all_typed(
            &HookArgument::bytes(name),
            priority.map(HookPriority::integer),
        );
    }
    pub fn remove_all_typed(&mut self, name: &HookArgument, priority: Option<HookPriority>) {
        if let Some(&slot) = key_get(&self.bindings, &name.array_key) {
            self.storage(slot).remove_all(priority);
            self.capture_diagnostics(slot);
            self.unbind_empty(&name.array_key, slot);
            self.collect_unused();
        }
    }
    fn unbind_empty(&mut self, key: &PhpKey, slot: usize) {
        if key_get(&self.bindings, key) == Some(&slot) && self.storage(slot).callbacks_empty() {
            self.bindings.remove(key);
        }
    }
    fn collect_unused(&mut self) {
        for (slot, state) in self.slots.iter_mut().enumerate() {
            if let Some(state) = state.as_mut() {
                self.diagnostics.extend(state.take_diagnostics());
            }
            if state.as_ref().is_some_and(|state| !state.is_active())
                && !self.bindings.references_slot(slot)
                && !self
                    .resumable
                    .iter()
                    .any(|dispatch| dispatch.slot == Some(slot))
                && !self.targets.iter().any(|pin| pin.slot == slot)
                && !self
                    .control_stack
                    .iter()
                    .any(|control| control.pinned_slots.contains(&slot))
            {
                *state = None;
            }
        }
    }
    fn capture_diagnostics(&mut self, slot: usize) {
        let pending = self.storage(slot).take_diagnostics();
        self.diagnostics.extend(pending);
    }
    fn storage_ref(&self, slot: usize) -> &Storage<Dispatcher> {
        self.slots[slot]
            .as_ref()
            .expect("active or registered hook object")
    }
    pub fn take_diagnostics(&mut self) -> Vec<HookDiagnostic> {
        std::mem::take(&mut self.diagnostics)
    }
    fn state_typed(&self, name: &HookArgument) -> Option<&Storage<Self>> {
        key_get(&self.bindings, &name.array_key).and_then(|&slot| self.slots[slot].as_ref())
    }
    pub fn has_callbacks(&self, name: &str) -> bool {
        self.has_callbacks_typed(&HookArgument::bytes(name))
    }
    pub fn has_callbacks_typed(&self, name: &HookArgument) -> bool {
        self.state_typed(name).is_some_and(Storage::has_callbacks)
    }
    pub fn has_at(&self, name: &str, priority: i64, id: &CallbackId) -> bool {
        self.has_at_typed(
            &HookArgument::bytes(name),
            &HookPriority::integer(priority),
            id,
        )
    }
    pub fn has_at_typed(
        &self,
        name: &HookArgument,
        priority: &HookPriority,
        id: &CallbackId,
    ) -> bool {
        self.state_typed(name)
            .is_some_and(|state| state.has_at(priority, id))
    }
    pub fn priority_of(&self, name: &str, id: &CallbackId) -> Option<i64> {
        self.priority_of_typed(&HookArgument::bytes(name), id)
            .and_then(|key| match key {
                PhpKey::Integer(value) => Some(value),
                PhpKey::Bytes(_) => None,
            })
    }
    pub fn priority_of_typed(&self, name: &HookArgument, id: &CallbackId) -> Option<PhpKey> {
        self.state_typed(name)
            .and_then(|state| state.priority_of(id))
    }
    pub fn current_priority(&self, name: &str) -> Option<i64> {
        self.current_priority_typed(&HookArgument::bytes(name))
            .and_then(|key| match key {
                PhpKey::Integer(value) => Some(value),
                PhpKey::Bytes(_) => None,
            })
    }
    pub fn current_priority_typed(&self, name: &HookArgument) -> Option<PhpKey> {
        self.state_typed(name).and_then(Storage::current_priority)
    }
    pub fn current_hook(&self) -> Option<&str> {
        match self.current_hook_typed()? {
            HookScalar::Bytes(bytes) => std::str::from_utf8(bytes).ok(),
            _ => None,
        }
    }
    pub fn current_hook_typed(&self) -> Option<&HookScalar> {
        self.current.last()
    }
    pub fn hook_stack_depth(&self) -> usize {
        self.current.len()
    }
    pub fn doing_hook(&self, name: Option<&str>) -> bool {
        self.doing_hook_typed(
            name.map(|name| {
                // Keep the temporary alive for the duration of the comparison.
                HookScalar::Bytes(name.as_bytes().to_vec())
            })
            .as_ref(),
        )
    }
    pub fn doing_hook_typed(&self, name: Option<&HookScalar>) -> bool {
        name.map_or(!self.current.is_empty(), |name| {
            self.current
                .iter()
                .any(|current| current.php_strict_eq(name))
        })
    }
    pub fn did_action(&self, name: &str) -> u64 {
        self.did_action_typed(&HookArgument::bytes(name))
    }
    pub fn did_action_typed(&self, name: &HookArgument) -> u64 {
        key_get(&self.actions, &name.array_key)
            .copied()
            .unwrap_or(0)
    }
    pub fn did_filter(&self, name: &str) -> u64 {
        self.did_filter_typed(&HookArgument::bytes(name))
    }
    pub fn did_filter_typed(&self, name: &HookArgument) -> u64 {
        key_get(&self.filters, &name.array_key)
            .copied()
            .unwrap_or(0)
    }
    pub fn apply_filters(
        &mut self,
        name: &str,
        value: PhpValue,
        extra: &[PhpValue],
    ) -> Result<PhpValue, HookError> {
        self.apply_filters_typed(&HookArgument::bytes(name), value, extra)
    }
    pub fn apply_filters_typed(
        &mut self,
        name: &HookArgument,
        value: PhpValue,
        extra: &[PhpValue],
    ) -> Result<PhpValue, HookError> {
        self.dispatch_typed(name, value, extra, false)
    }
    pub fn do_action(&mut self, name: &str, args: &[PhpValue]) -> Result<(), HookError> {
        self.do_action_typed(&HookArgument::bytes(name), args)
    }
    pub fn do_action_typed(
        &mut self,
        name: &HookArgument,
        args: &[PhpValue],
    ) -> Result<(), HookError> {
        self.dispatch_typed(name, PhpValue::Bytes(Vec::new()), args, true)
            .map(|_| ())
    }

    /// Start a filter whose PHP callbacks are advanced by the continuation caller.
    /// A child dispatch may begin only while its parent is suspended at a PHP callback.
    pub fn begin_filter(
        &mut self,
        name: &str,
        value: PhpValue,
        extra: &[PhpValue],
    ) -> Result<DispatchTicket, HookError> {
        self.begin_filter_typed(&HookArgument::bytes(name), value, extra)
    }

    pub fn begin_filter_typed(
        &mut self,
        name: &HookArgument,
        value: PhpValue,
        extra: &[PhpValue],
    ) -> Result<DispatchTicket, HookError> {
        if !self.resumable.is_empty()
            && !self.resumable.last().is_some_and(|dispatch| {
                (dispatch.waiting_for_php
                    || dispatch.waiting_for_boundary
                    || dispatch.waiting_for_release)
                    && dispatch.pending_throwable.is_none()
            })
        {
            return Err(HookError::DispatchParentNotPending);
        }
        if self.active_calls >= MAX_NESTING {
            return Err(HookError::NestingLimit);
        }
        let sequence = self.next_ticket;
        self.next_ticket = self
            .next_ticket
            .checked_add(1)
            .ok_or(HookError::InvalidDispatchTicket)?;
        let ticket = DispatchTicket {
            dispatcher: self.dispatcher_id,
            sequence,
        };
        self.filters.increment_live(name.array_key.clone());
        self.active_calls += 1;
        let all_key = PhpKey::Bytes(b"all".to_vec());
        let all_slot = key_get(&self.bindings, &all_key).copied();
        let base_depth = self.current.len();
        if all_slot.is_some() {
            self.current.push(name.original.clone());
        }
        self.resumable.push(ResumableDispatch {
            ticket,
            hook: name.clone(),
            value,
            extra: extra.to_vec(),
            base_depth,
            current_pushes: usize::from(all_slot.is_some()),
            phase: if all_slot.is_some() {
                DispatchPhase::All
            } else {
                DispatchPhase::Named
            },
            slot: all_slot,
            frame_started: false,
            waiting_for_php: false,
            waiting_for_boundary: false,
            waiting_for_release: false,
            pending_releases: VecDeque::new(),
            pending_release: None,
            pending_throwable: None,
            throwable_abandoned: false,
            effectful: false,
            binding_operand: None,
        });
        Ok(ticket)
    }

    /// Start a filter after PHP has completed the counter-key `isset` effect.
    /// The first yielded boundary performs the counter write or increment; the
    /// live counter is updated only after that PHP effect completes.
    pub fn begin_filter_effectful_typed(
        &mut self,
        name: &HookArgument,
        value: PhpValue,
        extra: &[PhpValue],
    ) -> Result<DispatchTicket, HookError> {
        if !self.resumable.is_empty()
            && !self.resumable.last().is_some_and(|dispatch| {
                (dispatch.waiting_for_php
                    || dispatch.waiting_for_boundary
                    || dispatch.waiting_for_release)
                    && dispatch.pending_throwable.is_none()
            })
        {
            return Err(HookError::DispatchParentNotPending);
        }
        if self.active_calls >= MAX_NESTING {
            return Err(HookError::NestingLimit);
        }
        let sequence = self.next_ticket;
        self.next_ticket = self
            .next_ticket
            .checked_add(1)
            .ok_or(HookError::InvalidDispatchTicket)?;
        let ticket = DispatchTicket {
            dispatcher: self.dispatcher_id,
            sequence,
        };
        self.active_calls += 1;
        let phase = if key_get(&self.filters, &name.array_key).is_some() {
            DispatchPhase::CounterIncrement
        } else {
            DispatchPhase::CounterWrite
        };
        self.resumable.push(ResumableDispatch {
            ticket,
            hook: name.clone(),
            value,
            extra: extra.to_vec(),
            base_depth: self.current.len(),
            current_pushes: 0,
            phase,
            slot: None,
            frame_started: false,
            waiting_for_php: false,
            waiting_for_boundary: false,
            waiting_for_release: false,
            pending_releases: VecDeque::new(),
            pending_release: None,
            pending_throwable: None,
            throwable_abandoned: false,
            effectful: true,
            binding_operand: None,
        });
        Ok(ticket)
    }

    /// Start a filter after the caller has completed the PHP counter isset and
    /// write/increment operands. This enters the existing all/named phases without
    /// incrementing the filter counter a second time.
    pub fn begin_filter_after_counter_typed(
        &mut self,
        name: &HookArgument,
        value: PhpValue,
        extra: &[PhpValue],
    ) -> Result<DispatchTicket, HookError> {
        if !self.resumable.is_empty()
            && !self.resumable.last().is_some_and(|dispatch| {
                (dispatch.waiting_for_php
                    || dispatch.waiting_for_boundary
                    || dispatch.waiting_for_release)
                    && dispatch.pending_throwable.is_none()
            })
        {
            return Err(HookError::DispatchParentNotPending);
        }
        if self.active_calls >= MAX_NESTING {
            return Err(HookError::NestingLimit);
        }
        let sequence = self.next_ticket;
        self.next_ticket = self
            .next_ticket
            .checked_add(1)
            .ok_or(HookError::InvalidDispatchTicket)?;
        let ticket = DispatchTicket {
            dispatcher: self.dispatcher_id,
            sequence,
        };
        self.active_calls += 1;
        let all_slot = key_get(&self.bindings, &PhpKey::Bytes(b"all".to_vec())).copied();
        let base_depth = self.current.len();
        if all_slot.is_some() {
            self.current.push(name.original.clone());
        }
        self.resumable.push(ResumableDispatch {
            ticket,
            hook: name.clone(),
            value,
            extra: extra.to_vec(),
            base_depth,
            current_pushes: usize::from(all_slot.is_some()),
            phase: if all_slot.is_some() {
                DispatchPhase::All
            } else {
                DispatchPhase::NamedHookIsset
            },
            slot: all_slot,
            frame_started: false,
            waiting_for_php: false,
            waiting_for_boundary: false,
            waiting_for_release: false,
            pending_releases: VecDeque::new(),
            pending_release: None,
            pending_throwable: None,
            throwable_abandoned: false,
            effectful: true,
            binding_operand: None,
        });
        Ok(ticket)
    }

    /// Advance native callbacks immediately; return only when PHP must run a closure or the filter completes.
    pub fn advance(&mut self, ticket: DispatchTicket) -> Result<DispatchStep, HookError> {
        self.check_top_ticket(ticket)?;
        if self.advancing == Some(ticket) {
            return Err(HookError::DispatchTicketBusy);
        }
        self.advancing = Some(ticket);
        let result = self.advance_inner(ticket);
        self.advancing = None;
        result
    }

    fn advance_inner(&mut self, ticket: DispatchTicket) -> Result<DispatchStep, HookError> {
        self.check_top_ticket(ticket)?;
        if self
            .resumable
            .last()
            .is_some_and(|dispatch| dispatch.waiting_for_release)
        {
            return Err(HookError::PhpReleasePending);
        }
        if let Some(dispatch) = self.resumable.last_mut()
            && let Some(handle) = dispatch.pending_releases.pop_front()
        {
            dispatch.waiting_for_release = true;
            dispatch.pending_release = Some(handle);
            return Ok(DispatchStep::ReleasePhp(handle));
        }
        if let Some(handle) = self
            .resumable
            .last()
            .and_then(|dispatch| dispatch.pending_throwable)
        {
            if !self
                .resumable
                .last()
                .is_some_and(|dispatch| dispatch.throwable_abandoned)
            {
                let slot = self
                    .resumable
                    .last()
                    .and_then(|dispatch| dispatch.frame_started.then_some(dispatch.slot).flatten());
                if let Some(slot) = slot {
                    hooks::abandon_frame_for_throwable(self, slot);
                }
                let releases = self.releases.drain();
                let dispatch = self.resumable.last_mut().expect("active dispatch");
                dispatch.throwable_abandoned = true;
                dispatch.pending_releases = releases;
                if let Some(release) = dispatch.pending_releases.pop_front() {
                    dispatch.waiting_for_release = true;
                    dispatch.pending_release = Some(release);
                    return Ok(DispatchStep::ReleasePhp(release));
                }
                self.retire_top_dispatch_for_throwable();
                return Ok(DispatchStep::PhpThrowable(handle));
            } else {
                self.retire_top_dispatch_for_throwable();
                return Ok(DispatchStep::PhpThrowable(handle));
            }
        }
        if let Some(dispatch) = self.resumable.last_mut() {
            dispatch.pending_releases.extend(self.releases.drain());
            if let Some(handle) = dispatch.pending_releases.pop_front() {
                dispatch.waiting_for_release = true;
                dispatch.pending_release = Some(handle);
                return Ok(DispatchStep::ReleasePhp(handle));
            }
        }
        if self
            .resumable
            .last()
            .is_some_and(|dispatch| dispatch.waiting_for_php)
        {
            return Err(HookError::PhpCallbackPending);
        }
        if self
            .resumable
            .last()
            .is_some_and(|dispatch| dispatch.waiting_for_boundary)
        {
            return Err(HookError::PhpBoundaryPending);
        }
        loop {
            let (phase, hook, value, extra, started, selected_slot) = {
                let dispatch = self.resumable.last().expect("validated dispatch ticket");
                (
                    dispatch.phase,
                    dispatch.hook.clone(),
                    dispatch.value.clone(),
                    dispatch.extra.clone(),
                    dispatch.frame_started,
                    dispatch.slot,
                )
            };
            let boundary = match phase {
                DispatchPhase::CounterWrite => Some(DispatchBoundary::FilterCounterWrite),
                DispatchPhase::CounterIncrement => Some(DispatchBoundary::FilterCounterIncrement),
                DispatchPhase::NamedHookIsset => Some(DispatchBoundary::NamedHookIsset),
                DispatchPhase::NamedHookRead => Some(DispatchBoundary::NamedHookRead),
                DispatchPhase::MissingOffsetAll => Some(DispatchBoundary::MissingOffsetAll),
                DispatchPhase::NullForeachAll => Some(DispatchBoundary::NullForeachAll),
                DispatchPhase::MissingOffset => Some(DispatchBoundary::MissingOffset),
                DispatchPhase::NullMethodApplyFilters => {
                    Some(DispatchBoundary::NullMethodApplyFilters)
                }
                DispatchPhase::Complete => {
                    let result = value.clone();
                    self.complete_top_dispatch();
                    return Ok(DispatchStep::Complete(result));
                }
                DispatchPhase::All | DispatchPhase::Named => None,
            };
            if let Some(boundary) = boundary {
                if matches!(
                    phase,
                    DispatchPhase::NamedHookIsset | DispatchPhase::NamedHookRead
                ) {
                    let operand = self.bindings.capture_operand();
                    self.resumable
                        .last_mut()
                        .expect("active dispatch")
                        .binding_operand = Some(operand);
                }
                self.resumable
                    .last_mut()
                    .expect("active dispatch")
                    .waiting_for_boundary = true;
                return Ok(DispatchStep::Boundary(boundary));
            }
            let slot = if started {
                selected_slot
            } else if phase == DispatchPhase::All {
                key_get(&self.bindings, &PhpKey::Bytes(b"all".to_vec())).copied()
            } else {
                key_get(&self.bindings, &hook.array_key).copied()
            };
            if phase == DispatchPhase::All && !started {
                let dispatch = self.resumable.last_mut().expect("active dispatch");
                dispatch.slot = slot;
            }
            let Some(slot) = slot else {
                if phase == DispatchPhase::All {
                    let dispatch = self.resumable.last_mut().expect("active dispatch");
                    dispatch.phase = if dispatch.effectful {
                        DispatchPhase::NamedHookIsset
                    } else {
                        DispatchPhase::Named
                    };
                    dispatch.slot = None;
                    continue;
                }
                let all_key = PhpKey::Bytes(b"all".to_vec());
                if key_get(&self.bindings, &all_key).is_some() {
                    self.pop_dispatch_hook_if_owned();
                }
                let value = self
                    .resumable
                    .last()
                    .expect("active dispatch")
                    .value
                    .clone();
                if self
                    .resumable
                    .last()
                    .is_some_and(|dispatch| dispatch.effectful)
                {
                    self.pop_dispatch_hook_if_owned();
                }
                self.complete_top_dispatch();
                return Ok(DispatchStep::Complete(value));
            };
            if !started {
                let all_key = PhpKey::Bytes(b"all".to_vec());
                if phase == DispatchPhase::Named
                    && !self
                        .resumable
                        .last()
                        .is_some_and(|dispatch| dispatch.effectful)
                    && key_get(&self.bindings, &all_key).is_none()
                {
                    self.current.push(hook.original.clone());
                    self.resumable
                        .last_mut()
                        .expect("active dispatch")
                        .current_pushes += 1;
                }
                let (frame_value, args, mode) = if phase == DispatchPhase::All {
                    let mut args = vec![hook.original.as_php_value(), value.clone()];
                    args.extend(extra);
                    (PhpValue::Bytes(Vec::new()), args, Mode::All)
                } else {
                    let mut args = vec![value.clone()];
                    args.extend(extra);
                    (value.clone(), args, Mode::Filter)
                };
                let frame_started = match hooks::start(self, slot, frame_value, args, mode) {
                    Ok(started) => started,
                    Err(error) => {
                        self.cancel_top_dispatch();
                        return Err(error);
                    }
                };
                let dispatch = self.resumable.last_mut().expect("active dispatch");
                dispatch.slot = Some(slot);
                dispatch.frame_started = frame_started;
                if !frame_started {
                    if phase == DispatchPhase::All {
                        dispatch.phase = if dispatch.effectful {
                            DispatchPhase::MissingOffsetAll
                        } else {
                            DispatchPhase::Named
                        };
                        dispatch.slot = None;
                        continue;
                    }
                    self.pop_dispatch_hook_if_owned();
                    self.complete_top_dispatch();
                    return Ok(DispatchStep::Complete(value));
                }
            }
            let active_slot = self
                .resumable
                .last()
                .and_then(|dispatch| dispatch.slot)
                .expect("started hook frame");
            match hooks::advance(self, active_slot) {
                Ok(HookStep::InvokePhp(mut invocation)) => {
                    invocation.hook = hook.original;
                    self.resumable
                        .last_mut()
                        .expect("active dispatch")
                        .waiting_for_php = true;
                    return Ok(DispatchStep::InvokePhp(invocation));
                }
                Ok(HookStep::ReleasePhp(releases)) => {
                    let dispatch = self.resumable.last_mut().expect("active dispatch");
                    dispatch.pending_releases.extend(releases);
                    let handle = dispatch
                        .pending_releases
                        .pop_front()
                        .expect("release event contains at least one handle");
                    dispatch.waiting_for_release = true;
                    dispatch.pending_release = Some(handle);
                    return Ok(DispatchStep::ReleasePhp(handle));
                }
                Ok(HookStep::Complete(output)) => {
                    let dispatch = self.resumable.last_mut().expect("active dispatch");
                    dispatch.frame_started = false;
                    dispatch.slot = None;
                    if phase == DispatchPhase::All {
                        dispatch.phase = if dispatch.effectful {
                            DispatchPhase::NamedHookIsset
                        } else {
                            DispatchPhase::Named
                        };
                    } else {
                        self.pop_dispatch_hook_if_owned();
                        self.complete_top_dispatch();
                        return Ok(DispatchStep::Complete(output));
                    }
                }
                Err(error) => {
                    self.cancel_top_dispatch();
                    return Err(error);
                }
            }
        }
    }

    /// Accept one PHP filter result for the closure invocation currently pending on this ticket.
    pub fn resume_php(&mut self, ticket: DispatchTicket, value: PhpValue) -> Result<(), HookError> {
        self.resume_php_completion(ticket, Completion::Value(value))
    }

    /// Confirm that PHP completed the release effect currently yielded for this filter.
    pub fn resume_php_release(
        &mut self,
        ticket: DispatchTicket,
        handle: PhpHandle,
    ) -> Result<(), HookError> {
        self.resume_php_release_completion(ticket, handle, Completion::Value(PhpValue::Null))
    }

    pub fn resume_php_release_completion(
        &mut self,
        ticket: DispatchTicket,
        handle: PhpHandle,
        completion: Completion<PhpValue>,
    ) -> Result<(), HookError> {
        self.check_top_ticket(ticket)?;
        if self.advancing == Some(ticket) {
            return Err(HookError::DispatchTicketBusy);
        }
        let dispatch = self
            .resumable
            .last_mut()
            .expect("validated dispatch ticket");
        if !dispatch.waiting_for_release {
            return Err(HookError::NoPhpReleasePending);
        }
        if dispatch.pending_release != Some(handle) {
            return Err(HookError::InvalidPhpCallableHandle);
        }
        if let Completion::PhpThrowable(thrown) = completion {
            if thrown.kind() != PhpHandleKind::Throwable {
                return Err(HookError::InvalidPhpThrowableHandle);
            }
            dispatch.pending_throwable = Some(thrown);
        }
        dispatch.pending_release = None;
        dispatch.waiting_for_release = false;
        Ok(())
    }

    /// Accept the full outcome of the PHP closure invocation pending on this ticket.
    /// A Throwable retires the internal frame while preserving the public WP hook stack.
    pub fn resume_php_completion(
        &mut self,
        ticket: DispatchTicket,
        completion: Completion<PhpValue>,
    ) -> Result<(), HookError> {
        self.check_top_ticket(ticket)?;
        if self.advancing == Some(ticket) {
            return Err(HookError::DispatchTicketBusy);
        }
        let dispatch = self.resumable.last().expect("validated dispatch ticket");
        if !dispatch.waiting_for_php || dispatch.pending_throwable.is_some() {
            return Err(HookError::NoPhpCallbackPending);
        }
        match completion {
            Completion::Value(value) => {
                let slot = dispatch.slot.ok_or(HookError::InvalidDispatchTicket)?;
                hooks::resume(self, slot, value)?;
                self.resumable
                    .last_mut()
                    .expect("active dispatch")
                    .waiting_for_php = false;
            }
            Completion::PhpThrowable(handle) => {
                if handle.kind() != PhpHandleKind::Throwable {
                    return Err(HookError::InvalidPhpThrowableHandle);
                }
                self.resumable
                    .last_mut()
                    .expect("active dispatch")
                    .pending_throwable = Some(handle);
                self.resumable
                    .last_mut()
                    .expect("active dispatch")
                    .waiting_for_php = false;
            }
        }
        Ok(())
    }

    /// Resume a PHP language-boundary effect for an effectful filter.
    ///
    /// Named hook boundaries accept a Null acknowledgment; the dispatcher resolves
    /// its saved array operand after PHP key-conversion reentry. The empty-all
    /// warning/foreach checkpoints also accept Null acknowledgments. A Throwable
    /// drops any outstanding operand before forwarding it through release cleanup.
    pub fn resume_boundary(
        &mut self,
        ticket: DispatchTicket,
        completion: Completion<PhpValue>,
    ) -> Result<(), HookError> {
        self.check_top_ticket(ticket)?;
        if self.advancing == Some(ticket) {
            return Err(HookError::DispatchTicketBusy);
        }
        let phase = {
            let dispatch = self.resumable.last().expect("validated dispatch ticket");
            if !dispatch.effectful || !dispatch.waiting_for_boundary {
                return Err(HookError::NoPhpBoundaryPending);
            }
            dispatch.phase
        };
        let value = match completion {
            Completion::Value(value) => value,
            Completion::PhpThrowable(handle) => {
                if handle.kind() != PhpHandleKind::Throwable {
                    return Err(HookError::InvalidPhpThrowableHandle);
                }
                let operand = {
                    let dispatch = self.resumable.last_mut().expect("active dispatch");
                    dispatch.pending_throwable = Some(handle);
                    dispatch.waiting_for_boundary = false;
                    dispatch.binding_operand.take()
                };
                drop(operand);
                self.collect_unused();
                return Ok(());
            }
        };
        match phase {
            DispatchPhase::CounterWrite => {
                self.filters
                    .write_one_live(self.resumable.last().unwrap().hook.array_key.clone());
                let all_exists = key_get(&self.bindings, &PhpKey::Bytes(b"all".to_vec())).is_some();
                let dispatch = self.resumable.last_mut().expect("active dispatch");
                if all_exists {
                    self.current.push(dispatch.hook.original.clone());
                    dispatch.current_pushes += 1;
                    dispatch.phase = DispatchPhase::All;
                    dispatch.slot =
                        key_get(&self.bindings, &PhpKey::Bytes(b"all".to_vec())).copied();
                } else {
                    dispatch.phase = DispatchPhase::NamedHookIsset;
                }
            }
            DispatchPhase::CounterIncrement => {
                self.filters
                    .increment_live(self.resumable.last().unwrap().hook.array_key.clone());
                let all_slot = key_get(&self.bindings, &PhpKey::Bytes(b"all".to_vec())).copied();
                let dispatch = self.resumable.last_mut().expect("active dispatch");
                if let Some(slot) = all_slot {
                    self.current.push(dispatch.hook.original.clone());
                    dispatch.current_pushes += 1;
                    dispatch.phase = DispatchPhase::All;
                    dispatch.slot = Some(slot);
                } else {
                    dispatch.phase = DispatchPhase::NamedHookIsset;
                }
            }
            DispatchPhase::NamedHookIsset => {
                if !matches!(value, PhpValue::Null) {
                    return Err(HookError::InvalidDispatchBoundaryValue);
                }
                let operand = self
                    .resumable
                    .last_mut()
                    .expect("active dispatch")
                    .binding_operand
                    .take()
                    .ok_or(HookError::InvalidDispatchTicket)?;
                let exists = matches!(
                    self.bindings
                        .read_operand(operand, &self.resumable.last().unwrap().hook.array_key),
                    HookBindingRead::Found(_)
                );
                self.collect_unused();
                let dispatch = self.resumable.last_mut().expect("active dispatch");
                if exists {
                    // WordPress decides whether to push the named hook before
                    // evaluating the later `$wp_filter[$hook]` read. That read
                    // may warn and reenter PHP, changing the live `all` binding,
                    // but it cannot change this earlier stack decision.
                    if key_get(&self.bindings, &PhpKey::Bytes(b"all".to_vec())).is_none() {
                        self.current.push(dispatch.hook.original.clone());
                        dispatch.current_pushes += 1;
                    }
                    dispatch.phase = DispatchPhase::NamedHookRead;
                } else {
                    if key_get(&self.bindings, &PhpKey::Bytes(b"all".to_vec())).is_some() {
                        self.current.pop();
                    }
                    dispatch.current_pushes = dispatch.current_pushes.saturating_sub(1);
                    dispatch.phase = DispatchPhase::Complete;
                }
            }
            DispatchPhase::NamedHookRead => {
                if !matches!(value, PhpValue::Null) {
                    return Err(HookError::InvalidDispatchBoundaryValue);
                }
                let hook_key = self.resumable.last().unwrap().hook.array_key.clone();
                let operand = self
                    .resumable
                    .last_mut()
                    .expect("active dispatch")
                    .binding_operand
                    .take()
                    .ok_or(HookError::InvalidDispatchTicket)?;
                match self.bindings.read_operand(operand, &hook_key) {
                    HookBindingRead::Found(slot) => {
                        self.resumable.last_mut().expect("active dispatch").slot = Some(slot);
                        self.resumable.last_mut().expect("active dispatch").phase =
                            DispatchPhase::Named;
                    }
                    HookBindingRead::Missing => {
                        self.resumable.last_mut().expect("active dispatch").phase =
                            DispatchPhase::MissingOffset;
                    }
                    HookBindingRead::NullOperand => {
                        self.resumable.last_mut().expect("active dispatch").phase =
                            DispatchPhase::NullMethodApplyFilters;
                    }
                }
                self.collect_unused();
            }
            DispatchPhase::MissingOffsetAll => {
                if !matches!(value, PhpValue::Null) {
                    return Err(HookError::InvalidDispatchBoundaryValue);
                }
                self.resumable.last_mut().expect("active dispatch").phase =
                    DispatchPhase::NullForeachAll;
            }
            DispatchPhase::NullForeachAll => {
                if !matches!(value, PhpValue::Null) {
                    return Err(HookError::InvalidDispatchBoundaryValue);
                }
                self.resumable.last_mut().expect("active dispatch").phase =
                    DispatchPhase::NamedHookIsset;
            }
            DispatchPhase::MissingOffset => {
                self.resumable.last_mut().expect("active dispatch").phase =
                    DispatchPhase::NullMethodApplyFilters;
            }
            DispatchPhase::NullMethodApplyFilters => {
                return Err(HookError::ExpectedPhpThrowable);
            }
            DispatchPhase::All | DispatchPhase::Named | DispatchPhase::Complete => {
                return Err(HookError::NoPhpBoundaryPending);
            }
        }
        self.resumable
            .last_mut()
            .expect("active dispatch")
            .waiting_for_boundary = false;
        Ok(())
    }

    /// Cancel the top resumable filter. A suspended parent remains pending and may be resumed later.
    pub fn cancel(&mut self, ticket: DispatchTicket) -> Result<(), HookError> {
        self.check_top_ticket(ticket)?;
        if self.advancing == Some(ticket) {
            return Err(HookError::DispatchTicketBusy);
        }
        self.cancel_top_dispatch();
        Ok(())
    }

    fn check_top_ticket(&self, ticket: DispatchTicket) -> Result<(), HookError> {
        if ticket.dispatcher != self.dispatcher_id
            || !self.resumable.iter().any(|entry| entry.ticket == ticket)
        {
            return Err(HookError::InvalidDispatchTicket);
        }
        if !self
            .resumable
            .last()
            .is_some_and(|entry| entry.ticket == ticket)
        {
            return Err(HookError::DispatchTicketOrder);
        }
        Ok(())
    }

    fn complete_top_dispatch(&mut self) {
        self.resumable.pop();
        self.active_calls = self.active_calls.saturating_sub(1);
        self.collect_unused();
    }

    fn pop_dispatch_hook_if_owned(&mut self) {
        if let Some(dispatch) = self.resumable.last_mut()
            && dispatch.current_pushes > 0
        {
            self.current.pop();
            dispatch.current_pushes -= 1;
        }
    }

    fn cancel_top_dispatch(&mut self) {
        let base_depth = self.cancel_top_dispatch_frame();
        self.current.truncate(base_depth);
        self.complete_top_dispatch();
    }

    fn retire_top_dispatch_for_throwable(&mut self) {
        self.complete_top_dispatch();
    }

    fn cancel_top_dispatch_frame(&mut self) -> usize {
        if let Some(dispatch) = self.resumable.last() {
            let base_depth = dispatch.base_depth;
            if dispatch.frame_started
                && let Some(slot) = dispatch.slot
                && let Some(state) = self.slots.get_mut(slot).and_then(Option::as_mut)
            {
                state.cancel_frame();
            }
            base_depth
        } else {
            self.current.len()
        }
    }

    fn dispatch_typed(
        &mut self,
        name: &HookArgument,
        value: PhpValue,
        extra: &[PhpValue],
        action: bool,
    ) -> Result<PhpValue, HookError> {
        if self.active_calls >= MAX_NESTING {
            return Err(HookError::NestingLimit);
        }
        self.active_calls += 1;
        let base_depth = self.current.len();
        if action {
            increment_key_counter(&mut self.actions, name.array_key.clone());
        } else {
            self.filters.increment_live(name.array_key.clone());
        }
        let result = (|| {
            let all_key = PhpKey::Bytes(b"all".to_vec());
            if let Some(&all) = key_get(&self.bindings, &all_key) {
                self.current.push(name.original.clone());
                let mut args = vec![name.original.as_php_value()];
                if !action {
                    args.push(value.clone());
                }
                args.extend_from_slice(extra);
                hooks::invoke(self, all, PhpValue::Bytes(Vec::new()), args, Mode::All)?;
            }
            let Some(&slot) = key_get(&self.bindings, &name.array_key) else {
                if key_get(&self.bindings, &all_key).is_some() {
                    self.current.pop();
                }
                return Ok(value);
            };
            if key_get(&self.bindings, &all_key).is_none() {
                self.current.push(name.original.clone());
            }
            let mut args = if action {
                Vec::new()
            } else {
                vec![value.clone()]
            };
            args.extend_from_slice(extra);
            if action && args.is_empty() {
                args.push(PhpValue::Bytes(Vec::new()));
            }
            let output = hooks::invoke(
                self,
                slot,
                value,
                args,
                if action { Mode::Action } else { Mode::Filter },
            )?;
            self.current.pop();
            Ok(output)
        })();
        self.active_calls -= 1;
        if result.is_err() {
            self.current.truncate(base_depth);
        }
        self.collect_unused();
        result
    }
}
