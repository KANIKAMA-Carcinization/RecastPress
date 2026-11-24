//! Named plugin.php-style dispatch for request-local native and resumable PHP callbacks.

mod control;
mod dispatch;

use self::dispatch::ResumableDispatch;
use crate::{
    continuation::PhpHandle,
    hooks::{
        CallbackId, CallbackMap, Context, ContextCallback, HookArgument, HookDiagnostic,
        HookPriority, HookScalar, PhpInvocation, PhpReleaseQueue, PriorityBucketState, Storage,
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
    priority_writes: Vec<PriorityWritePin>,
    next_priority_write: u64,
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
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PrioritySnapshot {
    dispatcher: u64,
    target_sequence: u64,
    priority_key: PhpKey,
    existed: bool,
}

/// Opaque single-use handle for the callback-map assignment operand captured
/// after accepted-argument conversion and before priority-key conversion.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct PriorityWriteToken {
    dispatcher: u64,
    sequence: u64,
}

#[derive(Debug)]
struct PriorityWritePin {
    sequence: u64,
    target_sequence: u64,
    slot: usize,
    priority: HookPriority,
    detached: Option<Arc<CallbackMap<Dispatcher>>>,
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
    priority_bucket_state: Option<PriorityBucketState>,
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
        priority_read_operand: Option<Arc<CallbackMap<Dispatcher>>>,
        priority_unset_operand: Option<Arc<CallbackMap<Dispatcher>>>,
    },
    RemoveExistence {
        slot: usize,
        target_sequence: u64,
        operand: Option<Arc<CallbackMap<Dispatcher>>>,
        priority: Option<HookPriority>,
        callback: Option<CallbackId>,
        result: Option<CallbackSnapshot>,
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
    PriorityExistence {
        slot: usize,
        target_sequence: u64,
        operand: Option<Arc<CallbackMap<Dispatcher>>>,
        priority: Option<HookPriority>,
        result: Option<PrioritySnapshot>,
    },
    PriorityWrite {
        slot: usize,
        target_sequence: u64,
        operand: Option<Arc<CallbackMap<Dispatcher>>>,
        priority: Option<HookPriority>,
        result: Option<PriorityWriteToken>,
    },
    PriorityWriteCommit {
        slot: usize,
        priority: HookPriority,
        priority_existed: bool,
        detached: bool,
    },
    PriorityWriteCancel,
}

#[derive(Clone, Debug, PartialEq)]
pub enum ControlResult {
    Added(bool),
    Removed(bool),
    RemovedAll,
    HookWritten,
    HookUnbound,
    TargetReleased,
    HookBindingLookup(HookBindingLookupResult),
    FilterCounter(FilterCounterResult),
    PrioritySnapshot(PrioritySnapshot),
    RemoveExistence(CallbackSnapshot),
    PriorityWrite(PriorityWriteToken),
    PriorityWriteCommitted,
    PriorityWriteCancelled,
}

#[derive(Clone, Debug, PartialEq)]
pub enum ControlStep {
    ReleasePhp(PhpHandle),
    PriorityRead {
        priority: HookPriority,
        missing: bool,
        state: PriorityBucketState,
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
    ActionCounterIncrement,
    NamedHookIsset,
    NamedHookRead,
    MissingOffsetAll,
    NullForeachAll,
    MissingOffset,
    NullMethodApplyFilters,
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
            priority_writes: Vec::new(),
            next_priority_write: 1,
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
    /// Drain request-scoped PHP handle releases created by direct registry mutations.
    /// Adapters must acknowledge every returned handle before closing the PHP request.
    pub fn take_php_releases(&mut self) -> Vec<PhpHandle> {
        self.releases.drain().into_iter().collect()
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
                    .any(|dispatch| dispatch.references_slot(slot))
                && !self.targets.iter().any(|pin| pin.slot == slot)
                && !self.priority_writes.iter().any(|pin| pin.slot == slot)
                && !self
                    .control_stack
                    .iter()
                    .any(|control| control.pinned_slots.contains(&slot))
            {
                *state = None;
            }
        }
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
}
