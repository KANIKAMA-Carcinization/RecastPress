//! Candidate logical reservations shared by tasks in ONE process/pool.
//!
//! The host supplies an already allocated allowance. This module does not
//! measure memory, choose automatic formulas, coordinate processes/cgroups, or
//! connect existing cache/index/decoder allocations. Reservation charges must
//! include caller-managed overhead; these counters are not allocator/RSS bounds.

use std::sync::{Arc, Mutex, MutexGuard};

const KIND_COUNT: usize = 7;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ResourceKind {
    ResponseCache,
    InternalRows,
    Index,
    IndexBuild,
    ChangeBuffer,
    Rendering,
    ObjectCache,
}

impl ResourceKind {
    const fn index(self) -> usize {
        self as usize
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BudgetError {
    Exhausted,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BudgetStats {
    pub limit: usize,
    pub claimed: usize,
    pub available: usize,
    pub debt: usize,
    by_kind: [usize; KIND_COUNT],
}

impl BudgetStats {
    pub fn claimed_for(&self, kind: ResourceKind) -> usize {
        self.by_kind[kind.index()]
    }
}

#[derive(Debug)]
struct State {
    limit: usize,
    claimed: usize,
    by_kind: [usize; KIND_COUNT],
}

/// Clones share the same accounting state. Separate pools do not coordinate;
/// the host must not assign the same external allowance to independent pools.
#[derive(Clone, Debug)]
pub struct BudgetPool {
    state: Arc<Mutex<State>>,
}

impl BudgetPool {
    pub fn new(allowance: usize) -> Self {
        Self {
            state: Arc::new(Mutex::new(State {
                limit: allowance,
                claimed: 0,
                by_kind: [0; KIND_COUNT],
            })),
        }
    }

    fn lock(&self) -> Result<MutexGuard<'_, State>, BudgetError> {
        self.state.lock().map_err(|_| BudgetError::Unavailable)
    }

    pub fn stats(&self) -> Result<BudgetStats, BudgetError> {
        let state = self.lock()?;
        Ok(BudgetStats {
            limit: state.limit,
            claimed: state.claimed,
            available: state.limit.saturating_sub(state.claimed),
            debt: state.claimed.saturating_sub(state.limit),
            by_kind: state.by_kind,
        })
    }

    /// Shrinking records debt; it does not pretend existing allocations have
    /// been released. Existing holders must shrink/drop their reservations.
    pub fn set_limit(&self, allowance: usize) -> Result<(), BudgetError> {
        self.lock()?.limit = allowance;
        Ok(())
    }

    /// Zero-byte reservations add no charge and are allowed in a healthy pool,
    /// including while in debt. A reservation is move-only, never Clone/Copy.
    pub fn try_reserve(
        &self,
        kind: ResourceKind,
        bytes: usize,
    ) -> Result<Reservation, BudgetError> {
        let mut state = self.lock()?;
        if bytes > state.limit.saturating_sub(state.claimed) {
            return Err(BudgetError::Exhausted);
        }
        let total = state
            .claimed
            .checked_add(bytes)
            .ok_or(BudgetError::Exhausted)?;
        let category = state.by_kind[kind.index()]
            .checked_add(bytes)
            .ok_or(BudgetError::Exhausted)?;
        state.claimed = total;
        state.by_kind[kind.index()] = category;
        Ok(Reservation {
            pool: self.clone(),
            kind,
            bytes,
        })
    }
}

#[derive(Debug)]
pub struct Reservation {
    pool: BudgetPool,
    kind: ResourceKind,
    bytes: usize,
}

impl Reservation {
    pub fn kind(&self) -> ResourceKind {
        self.kind
    }
    pub fn bytes(&self) -> usize {
        self.bytes
    }

    /// All-or-nothing. Shrink and no-op work even while outstanding charges
    /// exceed the new limit; only growth needs available capacity.
    pub fn try_resize(&mut self, bytes: usize) -> Result<(), BudgetError> {
        let mut state = self.pool.lock()?;
        let (total, category) = if bytes > self.bytes {
            let growth = bytes - self.bytes;
            if growth > state.limit.saturating_sub(state.claimed) {
                return Err(BudgetError::Exhausted);
            }
            (
                state
                    .claimed
                    .checked_add(growth)
                    .ok_or(BudgetError::Exhausted)?,
                state.by_kind[self.kind.index()]
                    .checked_add(growth)
                    .ok_or(BudgetError::Exhausted)?,
            )
        } else {
            let release = self.bytes - bytes;
            (
                state
                    .claimed
                    .checked_sub(release)
                    .ok_or(BudgetError::Unavailable)?,
                state.by_kind[self.kind.index()]
                    .checked_sub(release)
                    .ok_or(BudgetError::Unavailable)?,
            )
        };
        state.claimed = total;
        state.by_kind[self.kind.index()] = category;
        self.bytes = bytes;
        Ok(())
    }
}

impl Drop for Reservation {
    fn drop(&mut self) {
        // A poisoned pool stays unavailable. Do not recover possibly partial
        // state or panic while unwinding; retained charges are conservative.
        let Ok(mut state) = self.pool.lock() else {
            return;
        };
        let Some(total) = state.claimed.checked_sub(self.bytes) else {
            return;
        };
        let Some(category) = state.by_kind[self.kind.index()].checked_sub(self.bytes) else {
            return;
        };
        state.claimed = total;
        state.by_kind[self.kind.index()] = category;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn poison_disables_reservation_and_drop_never_recovers_or_panics() {
        let pool = BudgetPool::new(10);
        let mut lease = pool.try_reserve(ResourceKind::IndexBuild, 5).unwrap();
        let state = Arc::clone(&pool.state);
        assert!(
            std::thread::spawn(move || {
                let _locked = state.lock().unwrap();
                panic!("injected ledger poisoning");
            })
            .join()
            .is_err()
        );
        assert_eq!(pool.stats(), Err(BudgetError::Unavailable));
        assert_eq!(pool.set_limit(100), Err(BudgetError::Unavailable));
        assert!(matches!(
            pool.try_reserve(ResourceKind::ResponseCache, 1),
            Err(BudgetError::Unavailable)
        ));
        assert_eq!(lease.try_resize(1), Err(BudgetError::Unavailable));
        assert_eq!(lease.bytes(), 5);
        assert!(std::panic::catch_unwind(|| drop(lease)).is_ok());
        assert_eq!(pool.stats(), Err(BudgetError::Unavailable));
    }
}
