//! Hook registration, removal, and PHP control effects.

use super::{
    ActiveControl, CallbackSnapshot, ControlFinish, ControlOperation, ControlResult, ControlStep,
    Dispatcher, DispatcherCallback, FilterCounterOperandAccess, FilterCounterResult,
    HookBindingLookupResult, HookBindingRead, HookTarget, PrioritySnapshot, PriorityWritePin,
    PriorityWriteToken, TargetPin, key_get,
};
use crate::{
    continuation::{Completion, PhpHandle, PhpHandleKind},
    hooks::{
        CallbackId, Context, HookArgument, HookError, HookPriority, HookScalar,
        PriorityBucketState, Storage,
    },
    value::{PhpKey, PhpValue},
};
use std::{collections::VecDeque, sync::Arc};

fn same_priority_capture(left: &HookPriority, right: &HookPriority) -> bool {
    left.key() == right.key()
        && match (&left.original, &right.original) {
            (HookScalar::Null, HookScalar::Null) => true,
            (HookScalar::Bool(left), HookScalar::Bool(right)) => left == right,
            (HookScalar::Integer(left), HookScalar::Integer(right)) => left == right,
            (HookScalar::Float(left), HookScalar::Float(right)) => {
                left.to_bits() == right.to_bits()
            }
            (HookScalar::Bytes(left), HookScalar::Bytes(right)) => left == right,
            (
                HookScalar::Resource { identity: left, .. },
                HookScalar::Resource {
                    identity: right, ..
                },
            ) => left == right,
            _ => false,
        }
}

impl Dispatcher {
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
                priority_read_operand: None,
                priority_unset_operand: None,
            });
        };
        let removed = self.storage(slot).remove_entry(&priority, id);
        self.new_control(ControlFinish::Remove {
            key: name.array_key.clone(),
            slot,
            priority,
            removed,
            priority_read_checkpoint: false,
            priority_read_operand: None,
            priority_unset_operand: None,
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
                | ControlFinish::PriorityExistence { result: None, .. }
                | ControlFinish::PriorityWrite { result: None, .. }
                | ControlFinish::RemoveExistence { result: None, .. }
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
        if !operation.priority_read_complete {
            let read = match &operation.finish {
                ControlFinish::Remove {
                    priority,
                    slot,
                    removed: true,
                    priority_read_checkpoint: true,
                    ..
                } => Some((priority.clone(), *slot)),
                _ => None,
            };
            if let Some((priority, slot)) = read {
                let operand = self.storage_ref(slot).callback_map();
                let state = Storage::<Dispatcher>::priority_bucket_state(&operand, priority.key());
                let missing = state == PriorityBucketState::Missing;
                operation.priority_read_complete = true;
                operation.priority_bucket_state = Some(state);
                operation.priority_read_pending = true;
                if let ControlFinish::Remove {
                    priority_read_operand,
                    ..
                } = &mut operation.finish
                {
                    *priority_read_operand = Some(operand);
                }
                self.control_stack
                    .last_mut()
                    .expect("active control")
                    .waiting_boundary = true;
                return Ok(ControlStep::PriorityRead {
                    priority,
                    missing,
                    state,
                });
            }
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
        if matches!(
            operation.priority_bucket_state,
            Some(PriorityBucketState::Empty | PriorityBucketState::Missing)
        ) && !operation.priority_unset_complete
        {
            let (priority, slot) = match &operation.finish {
                ControlFinish::Remove { priority, slot, .. } => (priority.clone(), *slot),
                _ => unreachable!("priority unset belongs to remove"),
            };
            if slot != usize::MAX {
                let operand = self.storage_ref(slot).callback_map();
                if let ControlFinish::Remove {
                    priority_unset_operand,
                    ..
                } = &mut operation.finish
                {
                    *priority_unset_operand = Some(operand);
                }
            }
            operation.priority_unset_pending = true;
            self.control_stack
                .last_mut()
                .expect("active control")
                .waiting_boundary = true;
            return Ok(ControlStep::PriorityUnset(priority));
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
            ControlFinish::PriorityExistence { result, .. } => ControlResult::PrioritySnapshot(
                result.clone().ok_or(HookError::InvalidControlOperation)?,
            ),
            ControlFinish::RemoveExistence { result, .. } => ControlResult::RemoveExistence(
                result.clone().ok_or(HookError::InvalidControlOperation)?,
            ),
            ControlFinish::PriorityWrite { result, .. } => {
                ControlResult::PriorityWrite(result.ok_or(HookError::InvalidControlOperation)?)
            }
            ControlFinish::PriorityWriteCommit {
                slot,
                priority,
                priority_existed,
                detached,
            } => {
                if *detached {
                    self.storage(*slot)
                        .finish_add_detached(priority, *priority_existed);
                } else {
                    self.storage(*slot).finish_add(priority, *priority_existed);
                }
                self.capture_diagnostics(*slot);
                ControlResult::PriorityWriteCommitted
            }
            ControlFinish::PriorityWriteCancel => ControlResult::PriorityWriteCancelled,
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

    /// Return the Throwable currently suspended behind this control cursor's
    /// release effect, if cleanup is already unwinding one.
    pub fn pending_control_throwable(
        &self,
        operation: &ControlOperation,
    ) -> Result<Option<PhpHandle>, HookError> {
        self.check_control_top(operation)?;
        if operation.pending_release.is_none()
            || !self
                .control_stack
                .last()
                .is_some_and(|control| control.waiting_release)
        {
            return Err(HookError::NoPhpReleasePending);
        }
        Ok(operation.pending_throwable)
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
        let priority = match &operation.finish {
            ControlFinish::Remove { priority, .. } => priority.clone(),
            _ => return Err(HookError::InvalidControlOperation),
        };
        if let Completion::PhpThrowable(thrown) = completion {
            if thrown.kind() != PhpHandleKind::Throwable {
                return Err(HookError::InvalidPhpThrowableHandle);
            }
            operation.pending_throwable = Some(thrown);
            if let ControlFinish::Remove {
                priority_read_operand,
                ..
            } = &mut operation.finish
            {
                drop(priority_read_operand.take());
            }
        } else if let ControlFinish::Remove {
            priority_read_operand,
            ..
        } = &mut operation.finish
        {
            let operand = priority_read_operand
                .as_ref()
                .ok_or(HookError::InvalidControlOperation)?;
            let state = Storage::<Dispatcher>::priority_bucket_state(operand, priority.key());
            operation.priority_bucket_state = Some(state);
            if state == PriorityBucketState::Missing {
                operation.priority_missing_offset_needed = true;
            } else {
                drop(priority_read_operand.take());
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
            if let ControlFinish::Remove {
                priority_read_operand,
                ..
            } = &mut operation.finish
            {
                drop(priority_read_operand.take());
            }
        } else {
            if let ControlFinish::Remove {
                priority_read_operand,
                ..
            } = &mut operation.finish
            {
                drop(priority_read_operand.take());
            }
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
            if let ControlFinish::Remove {
                priority_unset_operand,
                ..
            } = &mut operation.finish
            {
                drop(priority_unset_operand.take());
            }
        } else if let ControlFinish::Remove {
            slot,
            priority,
            priority_unset_operand,
            ..
        } = &mut operation.finish
        {
            if *slot != usize::MAX {
                let mut operand = priority_unset_operand
                    .take()
                    .ok_or(HookError::InvalidControlOperation)?;
                let detached = !Arc::ptr_eq(&operand, &self.storage_ref(*slot).callback_map());
                if detached {
                    Storage::<Dispatcher>::unset_priority_bucket_in_map(
                        Arc::make_mut(&mut operand),
                        priority.key(),
                    );
                    drop(operand);
                } else {
                    drop(operand);
                    self.storage(*slot).unset_priority_bucket(priority);
                }
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
            ControlFinish::PriorityExistence { slot, .. }
            | ControlFinish::PriorityWrite { slot, .. }
            | ControlFinish::RemoveExistence { slot, .. }
            | ControlFinish::PriorityWriteCommit { slot, .. } => vec![*slot],
            ControlFinish::PriorityWriteCancel => Vec::new(),
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
            priority_bucket_state: None,
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
        self.begin_release_target_inner(target, None)
    }

    /// Release a pinned receiver while an earlier PHP Throwable is already
    /// unwinding. Callable destructors run through the release cursor and may
    /// replace that Throwable, preserving PHP's `finally` behavior.
    pub fn begin_release_target_with_throwable(
        &mut self,
        target: HookTarget,
        thrown: PhpHandle,
    ) -> Result<ControlOperation, HookError> {
        if thrown.kind() != PhpHandleKind::Throwable {
            return Err(HookError::InvalidPhpThrowableHandle);
        }
        self.begin_release_target_inner(target, Some(thrown))
    }

    fn begin_release_target_inner(
        &mut self,
        target: HookTarget,
        thrown: Option<PhpHandle>,
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
        let mut operation = self.new_control(ControlFinish::ReleaseTarget)?;
        operation.pending_throwable = thrown;
        Ok(operation)
    }

    pub(super) fn target_parts(&self, target: HookTarget) -> Result<(PhpKey, usize), HookError> {
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

    /// Capture the callbacks-map version before PHP converts an add priority.
    /// The resulting snapshot preserves the line-101 `isset` result while PHP
    /// warning handlers are free to replace the live map.
    pub fn begin_priority_existence(
        &mut self,
        target: HookTarget,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let (_, slot) = self.target_parts(target)?;
        let operand = self.storage_ref(slot).callback_map();
        let operation = self.new_control(ControlFinish::PriorityExistence {
            slot,
            target_sequence: target.sequence,
            operand: Some(operand),
            priority: None,
            result: None,
        })?;
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = true;
        Ok(operation)
    }

    /// Resolve the pre-conversion callback-map operand after PHP key conversion.
    /// Releases from the old version are returned before `PrioritySnapshot`.
    pub fn resume_priority_existence(
        &mut self,
        operation: &mut ControlOperation,
        priority: HookPriority,
    ) -> Result<(), HookError> {
        self.check_control_top(operation)?;
        if !self
            .control_stack
            .last()
            .is_some_and(|control| control.waiting_boundary)
        {
            return Err(HookError::NoPhpBoundaryPending);
        }
        let (slot, target_sequence, operand) = match &mut operation.finish {
            ControlFinish::PriorityExistence {
                slot,
                target_sequence,
                operand,
                priority: stored_priority,
                result,
            } if result.is_none() => {
                if stored_priority.is_some() {
                    return Err(HookError::InvalidControlOperation);
                }
                (
                    *slot,
                    *target_sequence,
                    operand.take().ok_or(HookError::InvalidControlOperation)?,
                )
            }
            _ => return Err(HookError::InvalidControlOperation),
        };
        let existed = Storage::<Dispatcher>::map_has_priority(&operand, priority.key());
        let snapshot = PrioritySnapshot {
            dispatcher: self.dispatcher_id,
            target_sequence,
            priority_key: priority.key().clone(),
            existed,
        };
        drop(operand);
        if let ControlFinish::PriorityExistence {
            priority: stored_priority,
            result,
            ..
        } = &mut operation.finish
        {
            *stored_priority = Some(priority);
            *result = Some(snapshot);
        }
        self.capture_diagnostics(slot);
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = false;
        Ok(())
    }

    pub fn cancel_priority_existence(
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
            ControlFinish::PriorityExistence {
                operand, result, ..
            } if result.is_none() => {
                drop(operand.take().ok_or(HookError::InvalidControlOperation)?);
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

    /// Capture the callback-map version after accepted-argument conversion but
    /// before PHP converts the priority used by the assignment.
    pub fn begin_priority_write(
        &mut self,
        target: HookTarget,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let (_, slot) = self.target_parts(target)?;
        let operand = self.storage_ref(slot).callback_map();
        let operation = self.new_control(ControlFinish::PriorityWrite {
            slot,
            target_sequence: target.sequence,
            operand: Some(operand),
            priority: None,
            result: None,
        })?;
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = true;
        Ok(operation)
    }

    /// Resolve the post-cast write operand. A stale operand gets a detached
    /// single-use token so its callback assignment cannot leak into the live map.
    pub fn resume_priority_write(
        &mut self,
        operation: &mut ControlOperation,
        priority: HookPriority,
    ) -> Result<(), HookError> {
        self.check_control_top(operation)?;
        if !self
            .control_stack
            .last()
            .is_some_and(|control| control.waiting_boundary)
        {
            return Err(HookError::NoPhpBoundaryPending);
        }
        let sequence = self.next_priority_write;
        let next_sequence = sequence
            .checked_add(1)
            .ok_or(HookError::InvalidControlOperation)?;
        let (slot, target_sequence, operand) = match &mut operation.finish {
            ControlFinish::PriorityWrite {
                slot,
                target_sequence,
                operand,
                priority: stored_priority,
                result,
            } if result.is_none() => {
                if stored_priority.is_some() {
                    return Err(HookError::InvalidControlOperation);
                }
                (
                    *slot,
                    *target_sequence,
                    operand.take().ok_or(HookError::InvalidControlOperation)?,
                )
            }
            _ => return Err(HookError::InvalidControlOperation),
        };
        let detached = (!Arc::ptr_eq(&operand, &self.storage_ref(slot).callback_map()))
            .then(|| Arc::clone(&operand));
        drop(operand);
        self.next_priority_write = next_sequence;
        self.priority_writes.push(PriorityWritePin {
            sequence,
            target_sequence,
            slot,
            priority: priority.clone(),
            detached,
        });
        let token = PriorityWriteToken {
            dispatcher: self.dispatcher_id,
            sequence,
        };
        if let ControlFinish::PriorityWrite {
            priority: stored_priority,
            result,
            ..
        } = &mut operation.finish
        {
            *stored_priority = Some(priority);
            *result = Some(token);
        }
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = false;
        Ok(())
    }

    pub fn cancel_priority_write(
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
            ControlFinish::PriorityWrite {
                operand, result, ..
            } if result.is_none() => {
                drop(operand.take().ok_or(HookError::InvalidControlOperation)?);
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

    pub fn begin_add_target_with_priority_write(
        &mut self,
        target: HookTarget,
        priority: HookPriority,
        snapshot: PrioritySnapshot,
        token: PriorityWriteToken,
        mut callback: DispatcherCallback,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let (_, slot) = self.target_parts(target)?;
        if snapshot.dispatcher != self.dispatcher_id
            || snapshot.target_sequence != target.sequence
            || snapshot.priority_key != *priority.key()
            || token.dispatcher != self.dispatcher_id
        {
            return Err(HookError::InvalidHookTarget);
        }
        let Some(pin_index) = self.priority_writes.iter().position(|pin| {
            pin.sequence == token.sequence
                && pin.target_sequence == target.sequence
                && pin.slot == slot
                && same_priority_capture(&pin.priority, &priority)
        }) else {
            return Err(HookError::InvalidHookTarget);
        };
        let detached = self.priority_writes[pin_index].detached.is_some();
        let operation = self.new_control(ControlFinish::PriorityWriteCommit {
            slot,
            priority: priority.clone(),
            priority_existed: snapshot.existed,
            detached,
        })?;
        let pin = self.priority_writes.remove(pin_index);
        callback.attach_release_queue(&self.releases);
        if let Some(mut detached) = pin.detached {
            Storage::<Dispatcher>::add_entry_to_map(
                Arc::make_mut(&mut detached),
                &priority,
                callback,
            );
            drop(detached);
        } else {
            self.storage(slot).add_entry(&priority, callback);
        }
        Ok(operation)
    }

    pub fn cancel_priority_write_token(
        &mut self,
        token: PriorityWriteToken,
        thrown: PhpHandle,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        if thrown.kind() != PhpHandleKind::Throwable {
            return Err(HookError::InvalidPhpThrowableHandle);
        }
        if token.dispatcher != self.dispatcher_id {
            return Err(HookError::InvalidHookTarget);
        }
        let Some(index) = self
            .priority_writes
            .iter()
            .position(|pin| pin.sequence == token.sequence)
        else {
            return Err(HookError::InvalidHookTarget);
        };
        let mut operation = self.new_control(ControlFinish::PriorityWriteCancel)?;
        drop(self.priority_writes.remove(index));
        operation.pending_throwable = Some(thrown);
        Ok(operation)
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

    /// Capture the callback map before PHP's line-212 `isset` key conversion.
    /// The resolved bool remains the outer API result when warning-handler
    /// reentry changes the live callback map during that conversion.
    pub fn begin_remove_existence(
        &mut self,
        target: HookTarget,
    ) -> Result<ControlOperation, HookError> {
        self.begin_control_allowed()?;
        let (_, slot) = self.target_parts(target)?;
        let operand = self.storage_ref(slot).callback_map();
        let operation = self.new_control(ControlFinish::RemoveExistence {
            slot,
            target_sequence: target.sequence,
            operand: Some(operand),
            priority: None,
            callback: None,
            result: None,
        })?;
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = true;
        Ok(operation)
    }

    /// Resolve the line-212 callback existence result against the captured map.
    pub fn resume_remove_existence(
        &mut self,
        operation: &mut ControlOperation,
        priority: HookPriority,
        callback: &CallbackId,
    ) -> Result<(), HookError> {
        self.check_control_top(operation)?;
        if !self
            .control_stack
            .last()
            .is_some_and(|control| control.waiting_boundary)
        {
            return Err(HookError::NoPhpBoundaryPending);
        }
        let (slot, target_sequence, operand) = match &mut operation.finish {
            ControlFinish::RemoveExistence {
                slot,
                target_sequence,
                operand,
                priority: stored_priority,
                callback: stored_callback,
                result,
            } if result.is_none() => {
                if stored_priority.is_some() || stored_callback.is_some() {
                    return Err(HookError::InvalidControlOperation);
                }
                (
                    *slot,
                    *target_sequence,
                    operand.take().ok_or(HookError::InvalidControlOperation)?,
                )
            }
            _ => return Err(HookError::InvalidControlOperation),
        };
        let snapshot = CallbackSnapshot {
            dispatcher: self.dispatcher_id,
            target_sequence,
            priority_key: priority.key().clone(),
            callback: callback.clone(),
            exists: Storage::<Dispatcher>::map_has_at(&operand, priority.key(), callback),
        };
        drop(operand);
        if let ControlFinish::RemoveExistence {
            priority: stored_priority,
            callback: stored_callback,
            result,
            ..
        } = &mut operation.finish
        {
            *stored_priority = Some(priority);
            *stored_callback = Some(callback.clone());
            *result = Some(snapshot);
        }
        self.capture_diagnostics(slot);
        self.control_stack
            .last_mut()
            .expect("active control")
            .waiting_boundary = false;
        Ok(())
    }

    /// Cancel a line-212 callback-map operand after a PHP Throwable.
    pub fn cancel_remove_existence(
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
            ControlFinish::RemoveExistence {
                operand, result, ..
            } if result.is_none() => {
                drop(operand.take().ok_or(HookError::InvalidControlOperation)?);
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
            priority_read_operand: None,
            priority_unset_operand: None,
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

    fn capture_diagnostics(&mut self, slot: usize) {
        let pending = self.storage(slot).take_diagnostics();
        self.diagnostics.extend(pending);
    }
}
