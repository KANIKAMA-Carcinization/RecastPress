//! Synchronous and resumable dispatch of registered hooks.

use super::{
    DispatchBoundary, DispatchStep, DispatchTicket, Dispatcher, HookBindingOperand,
    HookBindingRead, HookTarget, increment_key_counter, key_get,
};
use crate::{
    continuation::{Completion, PhpHandle, PhpHandleKind},
    hooks::{self, HookArgument, HookError, HookStep, MAX_NESTING, Mode},
    value::{PhpKey, PhpValue},
};
use std::collections::VecDeque;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DispatchPhase {
    CounterWrite,
    CounterIncrement,
    ActionCounterIncrement,
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
pub(super) struct ResumableDispatch {
    ticket: DispatchTicket,
    hook: HookArgument,
    value: PhpValue,
    extra: Vec<PhpValue>,
    base_depth: usize,
    current_pushes: usize,
    owns_current_scope: bool,
    selected_target: bool,
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
    action: bool,
    binding_operand: Option<HookBindingOperand>,
}

impl ResumableDispatch {
    pub(super) fn references_slot(&self, slot: usize) -> bool {
        self.slot == Some(slot)
    }

    fn new(
        ticket: DispatchTicket,
        name: &HookArgument,
        value: PhpValue,
        extra: &[PhpValue],
        base_depth: usize,
        phase: DispatchPhase,
    ) -> Self {
        Self {
            ticket,
            hook: name.clone(),
            value,
            extra: extra.to_vec(),
            base_depth,
            current_pushes: 0,
            owns_current_scope: true,
            selected_target: false,
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
            effectful: false,
            action: false,
            binding_operand: None,
        }
    }
}

impl Dispatcher {
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
    /// A child dispatch may begin while its parent waits for a PHP effect without
    /// a pending Throwable.
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
        self.begin_dispatch_allowed()?;
        let ticket = self.reserve_dispatch_ticket()?;
        self.filters.increment_live(name.array_key.clone());
        let mut dispatch = ResumableDispatch::new(
            ticket,
            name,
            value,
            extra,
            self.current.len(),
            DispatchPhase::Named,
        );
        self.enter_all_hook_if_bound(&mut dispatch);
        Ok(self.push_resumable_dispatch(dispatch))
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
        self.begin_dispatch_allowed()?;
        let ticket = self.reserve_dispatch_ticket()?;
        let phase = if key_get(&self.filters, &name.array_key).is_some() {
            DispatchPhase::CounterIncrement
        } else {
            DispatchPhase::CounterWrite
        };
        let dispatch = ResumableDispatch {
            effectful: true,
            ..ResumableDispatch::new(ticket, name, value, extra, self.current.len(), phase)
        };
        Ok(self.push_resumable_dispatch(dispatch))
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
        self.begin_dispatch_allowed()?;
        let ticket = self.reserve_dispatch_ticket()?;
        let mut dispatch = ResumableDispatch {
            effectful: true,
            ..ResumableDispatch::new(
                ticket,
                name,
                value,
                extra,
                self.current.len(),
                DispatchPhase::NamedHookIsset,
            )
        };
        self.enter_all_hook_if_bound(&mut dispatch);
        Ok(self.push_resumable_dispatch(dispatch))
    }

    /// Dispatch only the callbacks of an already selected hook target.
    ///
    /// This is the seam for a PHP wrapper that has already performed
    /// `plugin.php` bookkeeping (filter counters, `all`, and
    /// `$wp_current_filter`) and selected the exact `WP_Hook` object. The Rust
    /// dispatcher owns callback ordering and filter-value threading, while the
    /// wrapper retains ownership of those surrounding WordPress globals.
    pub fn begin_filter_on_target_typed(
        &mut self,
        name: &HookArgument,
        target: HookTarget,
        value: PhpValue,
        extra: &[PhpValue],
    ) -> Result<DispatchTicket, HookError> {
        self.begin_dispatch_allowed()?;
        let (target_key, slot) = self.target_parts(target)?;
        if target_key != name.array_key {
            return Err(HookError::InvalidHookTarget);
        }
        let ticket = self.reserve_dispatch_ticket()?;
        let dispatch = ResumableDispatch {
            owns_current_scope: false,
            selected_target: true,
            slot: Some(slot),
            ..ResumableDispatch::new(
                ticket,
                name,
                value,
                extra,
                self.current.len(),
                DispatchPhase::Named,
            )
        };
        Ok(self.push_resumable_dispatch(dispatch))
    }

    /// Start an action after the caller has completed WordPress's `$wp_actions`
    /// preincrement. The action has no filter value, and callback results are ignored.
    pub fn begin_action_after_counter_typed(
        &mut self,
        name: &HookArgument,
        args: &[PhpValue],
    ) -> Result<DispatchTicket, HookError> {
        self.begin_action_typed_inner(name, args, false)
    }

    /// Start an action and yield its `$wp_actions` preincrement as a PHP
    /// boundary. The internal action count advances only after that boundary
    /// completes successfully, before any `all` or named callback runs.
    pub fn begin_action_effectful_typed(
        &mut self,
        name: &HookArgument,
        args: &[PhpValue],
    ) -> Result<DispatchTicket, HookError> {
        self.begin_action_typed_inner(name, args, true)
    }

    fn begin_action_typed_inner(
        &mut self,
        name: &HookArgument,
        args: &[PhpValue],
        effectful: bool,
    ) -> Result<DispatchTicket, HookError> {
        self.begin_dispatch_allowed()?;
        let ticket = self.reserve_dispatch_ticket()?;
        let phase = if effectful {
            DispatchPhase::ActionCounterIncrement
        } else {
            increment_key_counter(&mut self.actions, name.array_key.clone());
            DispatchPhase::Named
        };
        let mut dispatch = ResumableDispatch {
            effectful,
            action: true,
            ..ResumableDispatch::new(
                ticket,
                name,
                PhpValue::Null,
                args,
                self.current.len(),
                phase,
            )
        };
        if !effectful {
            self.enter_all_hook_if_bound(&mut dispatch);
        }
        Ok(self.push_resumable_dispatch(dispatch))
    }

    fn begin_dispatch_allowed(&self) -> Result<(), HookError> {
        if self.resumable.last().is_some_and(|dispatch| {
            !(dispatch.waiting_for_php
                || dispatch.waiting_for_boundary
                || dispatch.waiting_for_release)
                || dispatch.pending_throwable.is_some()
        }) {
            return Err(HookError::DispatchParentNotPending);
        }
        if self.active_calls >= MAX_NESTING {
            return Err(HookError::NestingLimit);
        }
        Ok(())
    }

    fn reserve_dispatch_ticket(&mut self) -> Result<DispatchTicket, HookError> {
        let sequence = self.next_ticket;
        self.next_ticket = self
            .next_ticket
            .checked_add(1)
            .ok_or(HookError::InvalidDispatchTicket)?;
        Ok(DispatchTicket {
            dispatcher: self.dispatcher_id,
            sequence,
        })
    }

    fn enter_all_hook_if_bound(&mut self, dispatch: &mut ResumableDispatch) {
        if let Some(&slot) = key_get(&self.bindings, &PhpKey::Bytes(b"all".to_vec())) {
            self.current.push(dispatch.hook.original.clone());
            dispatch.current_pushes = 1;
            dispatch.phase = DispatchPhase::All;
            dispatch.slot = Some(slot);
        }
    }

    fn push_resumable_dispatch(&mut self, dispatch: ResumableDispatch) -> DispatchTicket {
        let ticket = dispatch.ticket;
        self.active_calls += 1;
        self.resumable.push(dispatch);
        ticket
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
            let (phase, hook, value, extra, started, selected_slot, target_is_selected) = {
                let dispatch = self.resumable.last().expect("validated dispatch ticket");
                (
                    dispatch.phase,
                    dispatch.hook.clone(),
                    dispatch.value.clone(),
                    dispatch.extra.clone(),
                    dispatch.frame_started,
                    dispatch.slot,
                    dispatch.selected_target,
                )
            };
            let boundary = match phase {
                DispatchPhase::CounterWrite => Some(DispatchBoundary::FilterCounterWrite),
                DispatchPhase::CounterIncrement => Some(DispatchBoundary::FilterCounterIncrement),
                DispatchPhase::ActionCounterIncrement => {
                    Some(DispatchBoundary::ActionCounterIncrement)
                }
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
            let slot = if started || target_is_selected {
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
                        .is_some_and(|dispatch| dispatch.effectful || !dispatch.owns_current_scope)
                    && key_get(&self.bindings, &all_key).is_none()
                {
                    self.current.push(hook.original.clone());
                    self.resumable
                        .last_mut()
                        .expect("active dispatch")
                        .current_pushes += 1;
                }
                let action = self.resumable.last().expect("active dispatch").action;
                let (frame_value, args, mode) = if phase == DispatchPhase::All {
                    let mut args = vec![hook.original.as_php_value()];
                    if !action {
                        args.push(value.clone());
                    }
                    args.extend(extra);
                    (PhpValue::Bytes(Vec::new()), args, Mode::All)
                } else {
                    let mut args = if action {
                        Vec::new()
                    } else {
                        vec![value.clone()]
                    };
                    args.extend(extra);
                    if action && args.is_empty() {
                        args.push(PhpValue::Bytes(Vec::new()));
                    }
                    (
                        value.clone(),
                        args,
                        if action { Mode::Action } else { Mode::Filter },
                    )
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
            let action = self.resumable.last().expect("active dispatch").action;
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
                        return Ok(DispatchStep::Complete(if action {
                            PhpValue::Null
                        } else {
                            output
                        }));
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

    /// Return the Throwable currently suspended behind this ticket's release
    /// effect, if cleanup is already unwinding one.
    pub fn pending_dispatch_throwable(
        &self,
        ticket: DispatchTicket,
    ) -> Result<Option<PhpHandle>, HookError> {
        self.check_top_ticket(ticket)?;
        if self.advancing == Some(ticket) {
            return Err(HookError::DispatchTicketBusy);
        }
        let dispatch = self.resumable.last().expect("validated dispatch ticket");
        if !dispatch.waiting_for_release || dispatch.pending_release.is_none() {
            return Err(HookError::NoPhpReleasePending);
        }
        Ok(dispatch.pending_throwable)
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
            DispatchPhase::ActionCounterIncrement => {
                increment_key_counter(
                    &mut self.actions,
                    self.resumable.last().unwrap().hook.array_key.clone(),
                );
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
