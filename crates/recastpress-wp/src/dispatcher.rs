//! Named plugin.php-style dispatch for supported request-local native callbacks.

use crate::{
    hooks::{
        self, CallbackId, Context, ContextCallback, HookDiagnostic, HookError, HookStep,
        MAX_NESTING, Mode, PhpInvocation, Storage,
    },
    value::PhpValue,
};
use std::{
    collections::BTreeMap,
    sync::atomic::{AtomicU64, Ordering},
};

static NEXT_DISPATCHER_ID: AtomicU64 = AtomicU64::new(1);

pub type DispatcherCallback = ContextCallback<Dispatcher>;

#[derive(Debug)]
pub struct Dispatcher {
    bindings: BTreeMap<String, usize>,
    slots: Vec<Option<Storage<Dispatcher>>>,
    current: Vec<String>,
    actions: BTreeMap<String, u64>,
    filters: BTreeMap<String, u64>,
    active_calls: usize,
    diagnostics: Vec<HookDiagnostic>,
    dispatcher_id: u64,
    next_ticket: u64,
    resumable: Vec<ResumableDispatch>,
    advancing: Option<DispatchTicket>,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct DispatchTicket {
    dispatcher: u64,
    sequence: u64,
}

#[derive(Clone, Debug, PartialEq)]
pub enum DispatchStep {
    InvokePhp(PhpInvocation),
    Complete(PhpValue),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DispatchPhase {
    All,
    Named,
}

#[derive(Debug)]
struct ResumableDispatch {
    ticket: DispatchTicket,
    hook: String,
    value: PhpValue,
    extra: Vec<PhpValue>,
    base_depth: usize,
    current_pushes: usize,
    phase: DispatchPhase,
    slot: Option<usize>,
    frame_started: bool,
    waiting_for_php: bool,
}

impl Default for Dispatcher {
    fn default() -> Self {
        Self {
            bindings: BTreeMap::new(),
            slots: Vec::new(),
            current: Vec::new(),
            actions: BTreeMap::new(),
            filters: BTreeMap::new(),
            active_calls: 0,
            diagnostics: Vec::new(),
            dispatcher_id: NEXT_DISPATCHER_ID.fetch_add(1, Ordering::Relaxed),
            next_ticket: 1,
            resumable: Vec::new(),
            advancing: None,
        }
    }
}

impl Context for Dispatcher {
    fn storage(&mut self, slot: usize) -> &mut Storage<Self> {
        self.slots[slot]
            .as_mut()
            .expect("active or registered hook object")
    }
}

impl Dispatcher {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn add(&mut self, name: &str, priority: i64, callback: DispatcherCallback) {
        let slot = match self.bindings.get(name) {
            Some(&slot) => slot,
            None => {
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
                self.bindings.insert(name.into(), slot);
                slot
            }
        };
        self.storage(slot).add(priority, callback);
        self.capture_diagnostics(slot);
    }
    pub fn remove(&mut self, name: &str, priority: i64, id: &CallbackId) -> bool {
        let Some(&slot) = self.bindings.get(name) else {
            return false;
        };
        let removed = self.storage(slot).remove(priority, id);
        self.capture_diagnostics(slot);
        self.unbind_empty(name, slot);
        self.collect_unused();
        removed
    }
    pub fn remove_all(&mut self, name: &str, priority: Option<i64>) {
        if let Some(&slot) = self.bindings.get(name) {
            self.storage(slot).remove_all(priority);
            self.capture_diagnostics(slot);
            self.unbind_empty(name, slot);
            self.collect_unused();
        }
    }
    fn unbind_empty(&mut self, name: &str, slot: usize) {
        if !self.storage(slot).has_callbacks() {
            self.bindings.remove(name);
        }
    }
    fn collect_unused(&mut self) {
        for (slot, state) in self.slots.iter_mut().enumerate() {
            if let Some(state) = state.as_mut() {
                self.diagnostics.extend(state.take_diagnostics());
            }
            if state.as_ref().is_some_and(|state| !state.is_active())
                && !self.bindings.values().any(|bound| *bound == slot)
            {
                *state = None;
            }
        }
    }
    fn capture_diagnostics(&mut self, slot: usize) {
        let pending = self.storage(slot).take_diagnostics();
        self.diagnostics.extend(pending);
    }
    pub fn take_diagnostics(&mut self) -> Vec<HookDiagnostic> {
        std::mem::take(&mut self.diagnostics)
    }
    fn state(&self, name: &str) -> Option<&Storage<Self>> {
        self.bindings
            .get(name)
            .and_then(|&slot| self.slots[slot].as_ref())
    }
    pub fn has_callbacks(&self, name: &str) -> bool {
        self.state(name).is_some_and(Storage::has_callbacks)
    }
    pub fn has_at(&self, name: &str, priority: i64, id: &CallbackId) -> bool {
        self.state(name)
            .is_some_and(|state| state.has_at(priority, id))
    }
    pub fn priority_of(&self, name: &str, id: &CallbackId) -> Option<i64> {
        self.state(name).and_then(|state| state.priority_of(id))
    }
    pub fn current_hook(&self) -> Option<&str> {
        self.current.last().map(String::as_str)
    }
    pub fn hook_stack_depth(&self) -> usize {
        self.current.len()
    }
    pub fn doing_hook(&self, name: Option<&str>) -> bool {
        name.map_or(!self.current.is_empty(), |name| {
            self.current.iter().any(|current| current == name)
        })
    }
    pub fn did_action(&self, name: &str) -> u64 {
        self.actions.get(name).copied().unwrap_or(0)
    }
    pub fn did_filter(&self, name: &str) -> u64 {
        self.filters.get(name).copied().unwrap_or(0)
    }
    pub fn apply_filters(
        &mut self,
        name: &str,
        value: PhpValue,
        extra: &[PhpValue],
    ) -> Result<PhpValue, HookError> {
        self.dispatch(name, value, extra, false)
    }
    pub fn do_action(&mut self, name: &str, args: &[PhpValue]) -> Result<(), HookError> {
        self.dispatch(name, PhpValue::Bytes(Vec::new()), args, true)
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
        if !self.resumable.is_empty()
            && !self
                .resumable
                .last()
                .is_some_and(|dispatch| dispatch.waiting_for_php)
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
        let count = self.filters.entry(name.into()).or_default();
        *count = count.saturating_add(1);
        self.active_calls += 1;
        let all_slot = self.bindings.get("all").copied();
        let base_depth = self.current.len();
        if all_slot.is_some() {
            self.current.push(name.into());
        }
        self.resumable.push(ResumableDispatch {
            ticket,
            hook: name.into(),
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
            .is_some_and(|dispatch| dispatch.waiting_for_php)
        {
            return Err(HookError::PhpCallbackPending);
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
            let slot = if started {
                selected_slot
            } else if phase == DispatchPhase::All {
                self.bindings.get("all").copied()
            } else {
                self.bindings.get(&hook).copied()
            };
            if phase == DispatchPhase::All && !started {
                let dispatch = self.resumable.last_mut().expect("active dispatch");
                dispatch.slot = slot;
            }
            let Some(slot) = slot else {
                if phase == DispatchPhase::All {
                    let dispatch = self.resumable.last_mut().expect("active dispatch");
                    dispatch.phase = DispatchPhase::Named;
                    dispatch.slot = None;
                    continue;
                }
                if self.bindings.contains_key("all") {
                    self.pop_dispatch_hook_if_owned();
                }
                let value = self
                    .resumable
                    .last()
                    .expect("active dispatch")
                    .value
                    .clone();
                self.complete_top_dispatch();
                return Ok(DispatchStep::Complete(value));
            };
            if !started {
                if phase == DispatchPhase::Named && !self.bindings.contains_key("all") {
                    self.current.push(hook.clone());
                    self.resumable
                        .last_mut()
                        .expect("active dispatch")
                        .current_pushes += 1;
                }
                let (frame_value, args, mode) = if phase == DispatchPhase::All {
                    let mut args = vec![PhpValue::Bytes(hook.as_bytes().to_vec()), value.clone()];
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
                        dispatch.phase = DispatchPhase::Named;
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
                    invocation.hook = hook;
                    self.resumable
                        .last_mut()
                        .expect("active dispatch")
                        .waiting_for_php = true;
                    return Ok(DispatchStep::InvokePhp(invocation));
                }
                Ok(HookStep::Complete(output)) => {
                    let dispatch = self.resumable.last_mut().expect("active dispatch");
                    dispatch.frame_started = false;
                    dispatch.slot = None;
                    if phase == DispatchPhase::All {
                        dispatch.phase = DispatchPhase::Named;
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
        self.check_top_ticket(ticket)?;
        if self.advancing == Some(ticket) {
            return Err(HookError::DispatchTicketBusy);
        }
        let dispatch = self.resumable.last().expect("validated dispatch ticket");
        if !dispatch.waiting_for_php {
            return Err(HookError::NoPhpCallbackPending);
        }
        let slot = dispatch.slot.ok_or(HookError::InvalidDispatchTicket)?;
        hooks::resume(self, slot, value)?;
        self.resumable
            .last_mut()
            .expect("active dispatch")
            .waiting_for_php = false;
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
        if let Some(dispatch) = self.resumable.last() {
            let base_depth = dispatch.base_depth;
            if dispatch.frame_started
                && let Some(slot) = dispatch.slot
                && let Some(state) = self.slots.get_mut(slot).and_then(Option::as_mut)
            {
                state.cancel_frame();
            }
            self.current.truncate(base_depth);
        }
        self.complete_top_dispatch();
    }

    fn dispatch(
        &mut self,
        name: &str,
        value: PhpValue,
        extra: &[PhpValue],
        action: bool,
    ) -> Result<PhpValue, HookError> {
        if self.active_calls >= MAX_NESTING {
            return Err(HookError::NestingLimit);
        }
        self.active_calls += 1;
        let base_depth = self.current.len();
        let counts = if action {
            &mut self.actions
        } else {
            &mut self.filters
        };
        let count = counts.entry(name.into()).or_default();
        *count = count.saturating_add(1);
        let result = (|| {
            if let Some(&all) = self.bindings.get("all") {
                self.current.push(name.into());
                let mut args = vec![PhpValue::Bytes(name.as_bytes().to_vec())];
                if !action {
                    args.push(value.clone());
                }
                args.extend_from_slice(extra);
                hooks::invoke(self, all, PhpValue::Bytes(Vec::new()), args, Mode::All)?;
            }
            let Some(&slot) = self.bindings.get(name) else {
                if self.bindings.contains_key("all") {
                    self.current.pop();
                }
                return Ok(value);
            };
            if !self.bindings.contains_key("all") {
                self.current.push(name.into());
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
