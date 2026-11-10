//! In-memory ordering contract for PHP-owned public state observations.
//!
//! PHP issues observation numbers when the boundary reads public state. This
//! tracker orders those reads and gates Rust publication; it does not observe
//! PHP writes automatically, contain public values, or provide token security.

use crate::continuation::RequestGeneration;
use std::fmt;
use std::sync::Arc;

/// A boundary-issued view marker. It is correlation state, not an authority token.
#[derive(Clone, Debug)]
pub struct PublicObservation {
    request: RequestGeneration,
    number: u64,
    scope: Arc<()>,
}

impl PartialEq for PublicObservation {
    fn eq(&self, other: &Self) -> bool {
        self.request == other.request
            && self.number == other.number
            && Arc::ptr_eq(&self.scope, &other.scope)
    }
}

impl Eq for PublicObservation {}

impl PublicObservation {
    pub fn request(&self) -> RequestGeneration {
        self.request
    }

    pub fn number(&self) -> u64 {
        self.number
    }
}

/// Permit for the single publish currently in flight.
#[derive(Clone, Debug)]
pub struct PublishPermit {
    request: RequestGeneration,
    observation_number: u64,
    scope: Arc<()>,
}

impl PartialEq for PublishPermit {
    fn eq(&self, other: &Self) -> bool {
        self.request == other.request
            && self.observation_number == other.observation_number
            && Arc::ptr_eq(&self.scope, &other.scope)
    }
}

impl Eq for PublishPermit {}

/// Errors from the public-state observation and publication sequence.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PublicStateError {
    InvalidObservationNumber,
    NonMonotonicObservation,
    WrongRequest,
    WrongTracker,
    StaleObservation,
    PublishPending,
    InvalidPermit,
    RequestClosed,
}

impl fmt::Display for PublicStateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}

impl std::error::Error for PublicStateError {}

/// Tracks observation freshness and permits one publish at a time.
///
/// A successful `begin_publish` consumes its observation. After the transport
/// completes the write operation (accepted or preflight-rejected),
/// `acknowledge_publish` must be followed by a fresh `observe` before another
/// publish. It does not assert that PHP applied the write. Unknown transport
/// outcomes should close the tracker and fail the request, never retry.
#[derive(Debug)]
pub struct PublicStateTracker {
    request: RequestGeneration,
    scope: Arc<()>,
    last_observation_number: Option<u64>,
    available_observation: Option<u64>,
    pending_publish: Option<u64>,
    closed: bool,
}

impl PublicStateTracker {
    pub fn new(request: RequestGeneration) -> Self {
        Self {
            request,
            scope: Arc::new(()),
            last_observation_number: None,
            available_observation: None,
            pending_publish: None,
            closed: false,
        }
    }

    /// Record a PHP-issued monotonic observation number.
    pub fn observe(&mut self, number: u64) -> Result<PublicObservation, PublicStateError> {
        self.ensure_open()?;
        if self.pending_publish.is_some() {
            return Err(PublicStateError::PublishPending);
        }
        if number == 0 {
            return Err(PublicStateError::InvalidObservationNumber);
        }
        if self
            .last_observation_number
            .is_some_and(|previous| number <= previous)
        {
            return Err(PublicStateError::NonMonotonicObservation);
        }

        self.last_observation_number = Some(number);
        self.available_observation = Some(number);
        Ok(PublicObservation {
            request: self.request,
            number,
            scope: Arc::clone(&self.scope),
        })
    }

    /// Consume the latest view and start one PHP publication.
    pub fn begin_publish(
        &mut self,
        observation: &PublicObservation,
    ) -> Result<PublishPermit, PublicStateError> {
        self.ensure_open()?;
        if observation.request != self.request {
            return Err(PublicStateError::WrongRequest);
        }
        if !Arc::ptr_eq(&observation.scope, &self.scope) {
            return Err(PublicStateError::WrongTracker);
        }
        if self.pending_publish.is_some() {
            return Err(PublicStateError::PublishPending);
        }
        if self.available_observation != Some(observation.number) {
            return Err(PublicStateError::StaleObservation);
        }

        self.available_observation = None;
        self.pending_publish = Some(observation.number);
        Ok(PublishPermit {
            request: self.request,
            observation_number: observation.number,
            scope: Arc::clone(&self.scope),
        })
    }

    /// Acknowledge transport completion, whether accepted or preflight-rejected.
    pub fn acknowledge_publish(&mut self, permit: &PublishPermit) -> Result<(), PublicStateError> {
        self.ensure_open()?;
        if permit.request != self.request {
            return Err(PublicStateError::WrongRequest);
        }
        if !Arc::ptr_eq(&permit.scope, &self.scope) {
            return Err(PublicStateError::WrongTracker);
        }
        if self.pending_publish != Some(permit.observation_number) {
            return Err(PublicStateError::InvalidPermit);
        }

        self.pending_publish = None;
        Ok(())
    }

    /// Close this request's public-state sequence permanently.
    pub fn close(&mut self) {
        self.closed = true;
        self.available_observation = None;
        self.pending_publish = None;
    }

    fn ensure_open(&self) -> Result<(), PublicStateError> {
        if self.closed {
            Err(PublicStateError::RequestClosed)
        } else {
            Ok(())
        }
    }
}
