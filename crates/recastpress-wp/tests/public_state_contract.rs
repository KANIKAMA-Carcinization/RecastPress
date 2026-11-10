use recastpress_wp::continuation::RequestGeneration;
use recastpress_wp::public_state::{PublicStateError, PublicStateTracker};

#[test]
fn observations_must_be_nonzero_and_strictly_increasing() {
    let mut state = PublicStateTracker::new(RequestGeneration::new(7));
    assert_eq!(
        state.observe(0),
        Err(PublicStateError::InvalidObservationNumber)
    );
    let first = state.observe(4).unwrap();
    assert_eq!(first.number(), 4);
    assert_eq!(
        state.observe(4),
        Err(PublicStateError::NonMonotonicObservation)
    );
    assert_eq!(
        state.observe(3),
        Err(PublicStateError::NonMonotonicObservation)
    );
    assert_eq!(state.observe(5).unwrap().number(), 5);

    let mut max_state = PublicStateTracker::new(RequestGeneration::new(8));
    assert_eq!(max_state.observe(u64::MAX).unwrap().number(), u64::MAX);
    assert_eq!(
        max_state.observe(u64::MAX),
        Err(PublicStateError::NonMonotonicObservation)
    );
}

#[test]
fn publish_consumes_view_and_requires_fresh_observation_after_ack() {
    let mut state = PublicStateTracker::new(RequestGeneration::new(9));
    let old_view = state.observe(1).unwrap();
    let latest_view = state.observe(2).unwrap();
    assert_ne!(old_view, latest_view);
    assert_eq!(
        state.begin_publish(&old_view),
        Err(PublicStateError::StaleObservation)
    );
    let permit = state.begin_publish(&latest_view).unwrap();

    assert_eq!(
        state.begin_publish(&latest_view),
        Err(PublicStateError::PublishPending)
    );
    assert_eq!(state.observe(3), Err(PublicStateError::PublishPending));
    state.acknowledge_publish(&permit).unwrap();
    assert_eq!(
        state.begin_publish(&latest_view),
        Err(PublicStateError::StaleObservation)
    );

    let fresh_view = state.observe(3).unwrap();
    let next_permit = state.begin_publish(&fresh_view).unwrap();
    state.acknowledge_publish(&next_permit).unwrap();
}

#[test]
fn rejects_wrong_request_and_invalid_or_replayed_write_acknowledgements() {
    let mut first = PublicStateTracker::new(RequestGeneration::new(1));
    let mut second = PublicStateTracker::new(RequestGeneration::new(2));
    let mut same_request = PublicStateTracker::new(RequestGeneration::new(1));
    let first_view = first.observe(1).unwrap();
    assert_eq!(
        second.begin_publish(&first_view),
        Err(PublicStateError::WrongRequest)
    );
    assert_eq!(
        same_request.begin_publish(&first_view),
        Err(PublicStateError::WrongTracker)
    );
    let same_request_view = same_request.observe(1).unwrap();
    assert_ne!(first_view, same_request_view);

    let permit = first.begin_publish(&first_view).unwrap();
    assert_eq!(
        second.acknowledge_publish(&permit),
        Err(PublicStateError::WrongRequest)
    );
    assert_eq!(
        same_request.acknowledge_publish(&permit),
        Err(PublicStateError::WrongTracker)
    );
    let wrong_scope_permit = same_request
        .begin_publish(&same_request_view)
        .expect("separate tracker accepts its own observation");
    assert_ne!(permit, wrong_scope_permit);
    assert_eq!(
        first.acknowledge_publish(&wrong_scope_permit),
        Err(PublicStateError::WrongTracker)
    );
    first.acknowledge_publish(&permit).unwrap();
    same_request
        .acknowledge_publish(&wrong_scope_permit)
        .unwrap();
    assert_eq!(
        first.acknowledge_publish(&permit),
        Err(PublicStateError::InvalidPermit)
    );
    assert_eq!(
        first.begin_publish(&first_view),
        Err(PublicStateError::StaleObservation)
    );
}

#[test]
fn close_rejects_all_future_observation_and_publication_operations() {
    let mut state = PublicStateTracker::new(RequestGeneration::new(3));
    let view = state.observe(1).unwrap();
    state.close();

    assert_eq!(state.observe(2), Err(PublicStateError::RequestClosed));
    assert_eq!(
        state.begin_publish(&view),
        Err(PublicStateError::RequestClosed)
    );
}

#[test]
fn close_with_pending_publish_rejects_late_acknowledgement() {
    let mut state = PublicStateTracker::new(RequestGeneration::new(4));
    let view = state.observe(1).unwrap();
    let permit = state.begin_publish(&view).unwrap();
    state.close();

    assert_eq!(
        state.acknowledge_publish(&permit),
        Err(PublicStateError::RequestClosed)
    );
}
