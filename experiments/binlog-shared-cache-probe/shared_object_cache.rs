//! Single fixed key for the owned binlog fixture.

use std::sync::{Arc, Mutex};

use recastpress_core::resource_budget::{BudgetPool, Reservation, ResourceKind};

const MAX_VALUE_BYTES: usize = 64;

#[derive(Clone, Debug)]
pub(crate) struct Ticket(Arc<()>);

impl PartialEq for Ticket {
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}

impl Eq for Ticket {}

struct Entry {
    value: Vec<u8>,
    reservation: Reservation,
}

enum State {
    Reconciling,
    Tracking {
        ticket: Ticket,
        entry: Option<Entry>,
    },
    Unavailable,
}

pub(crate) struct SharedObjectCache {
    state: Mutex<State>,
    pool: BudgetPool,
}

impl SharedObjectCache {
    pub(crate) fn new(pool: BudgetPool) -> Self {
        Self {
            state: Mutex::new(State::Reconciling),
            pool,
        }
    }

    pub(crate) fn tracking_started(&self) -> bool {
        let Ok(mut state) = self.state.lock() else {
            return false;
        };
        if !matches!(*state, State::Reconciling) {
            return false;
        }
        *state = State::Tracking {
            ticket: Ticket(Arc::new(())),
            entry: None,
        };
        true
    }

    /// The fixture owner has checked continuity through a complete commit on
    /// the live replacement dump. The previous generation is never reused.
    pub(crate) fn tracking_reconciled(&self) -> bool {
        let Ok(mut state) = self.state.lock() else {
            return false;
        };
        if !matches!(*state, State::Unavailable) {
            return false;
        }
        *state = State::Tracking {
            ticket: Ticket(Arc::new(())),
            entry: None,
        };
        true
    }

    pub(crate) fn ticket(&self) -> Option<Ticket> {
        let state = self.state.lock().ok()?;
        match &*state {
            State::Tracking { ticket, .. } => Some(ticket.clone()),
            _ => None,
        }
    }

    pub(crate) fn get(&self) -> Option<Vec<u8>> {
        let state = self.state.lock().ok()?;
        match &*state {
            State::Tracking {
                entry: Some(entry), ..
            } => Some(entry.value.clone()),
            _ => None,
        }
    }

    pub(crate) fn fill(&self, ticket: &Ticket, value: &[u8]) -> bool {
        if value.len() > MAX_VALUE_BYTES {
            return false;
        }
        let Ok(mut state) = self.state.lock() else {
            return false;
        };
        let State::Tracking {
            ticket: current,
            entry,
        } = &mut *state
        else {
            return false;
        };
        if current != ticket {
            return false;
        }
        if let Some(entry) = entry {
            if entry.reservation.try_resize(value.len()).is_err() {
                return false;
            }
            entry.value = value.to_vec();
        } else {
            let Ok(reservation) = self
                .pool
                .try_reserve(ResourceKind::ObjectCache, value.len())
            else {
                return false;
            };
            *entry = Some(Entry {
                value: value.to_vec(),
                reservation,
            });
        }
        true
    }

    pub(crate) fn committed_change(&self) -> bool {
        let Ok(mut state) = self.state.lock() else {
            return false;
        };
        if !matches!(*state, State::Tracking { .. }) {
            return false;
        }
        *state = State::Tracking {
            ticket: Ticket(Arc::new(())),
            entry: None,
        };
        true
    }

    pub(crate) fn monitoring_lost(&self) {
        // Recover the guard solely to discard the entry and release its charge.
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        *state = State::Unavailable;
    }

    pub(crate) fn charged_bytes(&self) -> usize {
        let state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        match &*state {
            State::Tracking {
                entry: Some(entry), ..
            } => entry.reservation.bytes(),
            _ => 0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn commit_and_monitor_loss_reject_preexisting_fills() {
        let pool = BudgetPool::new(64);
        let cache = SharedObjectCache::new(pool.clone());
        assert!(cache.ticket().is_none());
        assert!(!cache.tracking_reconciled());
        assert!(cache.tracking_started());
        assert!(!cache.tracking_started());
        let initial = cache.ticket().unwrap();
        assert!(cache.fill(&initial, b"old"));
        assert_eq!(cache.get().as_deref(), Some(b"old".as_slice()));
        assert_eq!(cache.charged_bytes(), 3);
        assert!(cache.committed_change());
        assert!(!cache.fill(&initial, b"old"));
        assert!(cache.get().is_none());
        assert_eq!(pool.stats().unwrap().claimed, 0);
        let current = cache.ticket().unwrap();
        assert_ne!(initial, current);
        assert!(cache.fill(&current, b"new"));
        cache.monitoring_lost();
        assert!(cache.ticket().is_none());
        assert!(cache.get().is_none());
        assert!(!cache.fill(&current, b"new"));
        assert!(!cache.committed_change());
        assert!(!cache.tracking_started());
        assert_eq!(cache.charged_bytes(), 0);
        assert_eq!(pool.stats().unwrap().claimed, 0);
        assert!(cache.tracking_reconciled());
        assert!(!cache.tracking_reconciled());
        assert!(cache.get().is_none());
        assert!(!cache.fill(&current, b"new"));
        let reconciled = cache.ticket().unwrap();
        assert_ne!(current, reconciled);
        assert!(cache.fill(&reconciled, b"recovered"));
    }

    #[test]
    fn budget_refusal_preserves_entry_and_charge() {
        let cache = SharedObjectCache::new(BudgetPool::new(3));
        assert!(cache.tracking_started());
        let ticket = cache.ticket().unwrap();
        assert!(!cache.fill(&ticket, b"long"));
        assert!(cache.get().is_none());
        assert!(cache.fill(&ticket, b"old"));
        assert!(!cache.fill(&ticket, b"long"));
        assert!(!cache.fill(&ticket, &[0; MAX_VALUE_BYTES + 1]));
        assert_eq!(cache.charged_bytes(), 3);
        assert_eq!(cache.get().as_deref(), Some(b"old".as_slice()));
        assert!(cache.fill(&ticket, b"a"));
        assert_eq!(cache.charged_bytes(), 1);
    }
}
