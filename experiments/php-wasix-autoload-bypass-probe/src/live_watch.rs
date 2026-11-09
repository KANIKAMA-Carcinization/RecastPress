use futures_util::Stream;
use mysql_async::binlog::events::Event;
use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
};
use tokio::{sync::oneshot, task::JoinHandle};

use crate::options_cache::OptionsCache;

struct WatchState {
    coordinate: Option<Coordinate>,
    pending: bool,
}
type Applied = Arc<Mutex<WatchState>>;

// A cancelled or panicking watcher must revoke its permits too, including if
// the spawned future is dropped before it is first polled.
struct WatchLease(Applied);

impl Drop for WatchLease {
    fn drop(&mut self) {
        let mut applied = self.0.lock().unwrap_or_else(|error| error.into_inner());
        applied.coordinate = None;
        applied.pending = true;
    }
}

// This is a bounded fixture buffer, not a production binlog consumer. Continue
// polling the dump while application is held so EOF/errors revoke the permit.
async fn wait_for_target<T, E, S>(
    stream: &mut S,
    end: &mut oneshot::Receiver<Coordinate>,
    buffered: &mut VecDeque<T>,
    size: impl Fn(&T) -> usize,
    mut on_event: impl FnMut(),
) -> Result<Coordinate>
where
    S: Stream<Item = std::result::Result<T, E>> + Unpin,
    E: std::error::Error + Send + Sync + 'static,
{
    const MAX_EVENTS: usize = 1024;
    const MAX_BYTES: usize = 1024 * 1024;
    let mut bytes = buffered
        .iter()
        .try_fold(0usize, |total, item| total.checked_add(size(item)))
        .context("autoload_live_buffer_overflow")?;
    ensure!(
        buffered.len() <= MAX_EVENTS && bytes <= MAX_BYTES,
        "autoload_live_buffer_limit"
    );
    loop {
        tokio::select! {
            target = &mut *end => return target.context("autoload_live_target_missing"),
            event = stream.next() => {
                let event = event.context("autoload_live_early_eof")??;
                bytes = bytes.checked_add(size(&event)).context("autoload_live_buffer_overflow")?;
                ensure!(buffered.len() < MAX_EVENTS && bytes <= MAX_BYTES, "autoload_live_buffer_limit");
                buffered.push_back(event);
                on_event();
            }
        }
    }
}

pub(crate) struct GuardedCache {
    applied: Applied,
    observed: Coordinate,
    cache: Arc<OptionsCache>,
}

impl GuardedCache {
    pub(crate) fn current<T>(&self, use_cache: impl FnOnce(&OptionsCache) -> T) -> Option<T> {
        let applied = self.applied.lock().ok()?;
        (!applied.pending && applied.coordinate.as_ref() == Some(&self.observed))
            .then(|| use_cache(&self.cache))
    }
}

pub(crate) struct LiveWatch {
    applied: Applied,
    dump_id: u32,
    through: Option<oneshot::Sender<Coordinate>>,
    result: oneshot::Receiver<(usize, usize)>,
    task: JoinHandle<Result<()>>,
    #[cfg(test)]
    received: Arc<std::sync::atomic::AtomicUsize>,
}

impl Monitor {
    pub(crate) fn live_watch(&mut self, cache: Arc<OptionsCache>) -> Result<LiveWatch> {
        self.runtime.block_on(async {
            timeout(DEADLINE, async {
                let mut dump = connect(self.options.clone()).await?;
                let identity = source(&mut dump, &self.nonce).await?;
                let checkpoint = self.checkpoint.clone();
                let dump_id = dump.id();
                ensure!(
                    dump_id != 0
                        && dump_id != self.control.id()
                        && identity == self.source
                        && coordinate(&mut dump, &identity).await? == checkpoint,
                    "autoload_live_dump_identity"
                );
                let mut stream = dump
                    .get_binlog_stream(
                        BinlogStreamRequest::new(7001)
                            .with_filename(checkpoint.file.as_bytes())
                            .with_pos(checkpoint.position),
                    )
                    .await?;
                let mut preamble =
                    blocking_preamble(&identity, &mut stream, &checkpoint, self.nonce.as_bytes())
                        .await?;
                let applied = Arc::new(Mutex::new(WatchState {
                    coordinate: Some(checkpoint.clone()),
                    pending: false,
                }));
                let shared = applied.clone();
                let received_state = applied.clone();
                let lease = WatchLease(shared.clone());
                #[cfg(test)]
                let received = Arc::new(std::sync::atomic::AtomicUsize::new(0));
                #[cfg(test)]
                let task_received = received.clone();
                let nonce = self.nonce.clone();
                let option_id = self.option_id;
                let (through, mut end) = oneshot::channel::<Coordinate>();
                let (done, result) = oneshot::channel();
                let task = tokio::spawn(async move {
                    let _lease = lease;
                    let scan = async {
                        let through = wait_for_target(
                            &mut stream,
                            &mut end,
                            &mut preamble,
                            |event| event.header().event_size() as usize,
                            || {
                                if let Ok(mut state) = received_state.lock() {
                                    state.pending = true;
                                }
                                #[cfg(test)]
                                task_received.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                            },
                        )
                        .await?;
                        let counts = scan_live(
                            &mut stream,
                            &identity,
                            nonce.as_bytes(),
                            checkpoint.position,
                            &through,
                            option_id,
                            preamble,
                        )
                        .await?;
                        let mut applied =
                            shared.lock().map_err(|_| anyhow!("autoload_live_lock"))?;
                        cache.disable();
                        applied.coordinate = Some(through);
                        applied.pending = false;
                        let _ = done.send(counts);
                        Ok(())
                    }
                    .await;
                    // The fixed fixture explicitly closes its owned dump in finish_live.
                    let result = match scan {
                        Ok(()) => match stream.next().await {
                            None | Some(Err(_)) => Ok(()),
                            Some(Ok(_)) => Err(anyhow!("autoload_live_extra_event")),
                        },
                        Err(error) => Err(error),
                    };
                    result
                });
                Ok(LiveWatch {
                    applied,
                    dump_id,
                    through: Some(through),
                    result,
                    task,
                    #[cfg(test)]
                    received,
                })
            })
            .await
            .context("autoload_live_start_deadline")?
        })
    }

    pub(crate) fn guarded_cache(
        &self,
        watch: &LiveWatch,
        mismatch: Option<&PositionMismatch>,
        cache: Arc<OptionsCache>,
    ) -> GuardedCache {
        let observed = mismatch.map_or(&self.checkpoint, |mismatch| &mismatch.observed);
        GuardedCache {
            applied: watch.applied.clone(),
            observed: observed.clone(),
            cache,
        }
    }

    pub(crate) fn live_at_start(&self, watch: &LiveWatch) -> Result<()> {
        let applied = watch
            .applied
            .lock()
            .map_err(|_| anyhow!("autoload_live_lock"))?;
        ensure!(
            applied.coordinate.as_ref() == Some(&self.checkpoint),
            "autoload_live_start_lost"
        );
        Ok(())
    }

    pub(crate) fn apply_live(
        &mut self,
        watch: &mut LiveWatch,
        mismatch: &PositionMismatch,
    ) -> Result<Value> {
        watch
            .through
            .take()
            .context("autoload_live_target_reused")?
            .send(mismatch.observed.clone())
            .map_err(|_| anyhow!("autoload_live_target_closed"))?;
        let (events, checksums) = self.runtime.block_on(async {
            timeout(DEADLINE, &mut watch.result)
                .await
                .context("autoload_live_apply_deadline")?
                .context("autoload_live_result_closed")
        })?;
        Ok(json!({"events":events,"checksums":checksums,"commits":1}))
    }

    pub(crate) fn finish_live(&mut self, mut watch: LiveWatch) -> Result<()> {
        self.runtime.block_on(async {
            timeout(DEADLINE, async {
                let stopped = self
                    .control
                    .query_drop(format!("KILL CONNECTION {}", watch.dump_id))
                    .await;
                drop(watch.through.take());
                stopped?;
                watch.task.await??;
                Ok(())
            })
            .await
            .context("autoload_live_close_deadline")?
        })
    }
}

#[cfg(test)]
mod live_gate_tests {
    use super::*;

    #[test]
    #[ignore = "requires an owned, pinned MySQL or MariaDB fixture"]
    fn owned_apply_buffered_event() -> Result<()> {
        use serde::Deserialize;
        use std::io::Read;
        use std::sync::atomic::Ordering;

        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Input {
            port: u16,
            password: String,
            nonce: String,
            writer_password: String,
        }
        let mut input = Vec::new();
        std::io::stdin().take(1025).read_to_end(&mut input)?;
        ensure!(input.len() <= 1024, "apply_input_bound");
        let input: Input = serde_json::from_slice(&input)?;
        ensure!(input.writer_password.len() == 48, "apply_writer_input");
        let mut monitor = Monitor::start(input.port, &input.password, &input.nonce)?;
        let cache = Arc::new(OptionsCache::new());
        let mut watch = monitor.live_watch(cache.clone())?;
        let old_permit = monitor.guarded_cache(&watch, None, cache.clone());
        let column = |name| {
            json!({"name_hex":name, "type":253, "flags":0,
            "character_set":45, "declared_length":191, "decimals":0})
        };
        let reply = json!({"status":2, "warnings":0, "affected_rows":"0", "insert_id":null,
            "columns":[column("6f7074696f6e5f6e616d65"), column("6f7074696f6e5f76616c7565")],
            "rows":[["72705f6175746f6c6f61645f70726f62655f6d61726b6572", "72702d6175746f6c6f61642d6f6c64"]]});
        cache.record_sql();
        cache.remember(2, &reply);
        ensure!(
            old_permit.current(|cache| cache.lookup(2)) == Some(Some(reply)),
            "apply_initial_hit"
        );
        monitor.runtime.block_on(async {
            timeout(DEADLINE, async {
                let mut writer = Conn::new(OptsBuilder::default().ip_or_hostname("127.0.0.1")
                    .tcp_port(input.port).prefer_socket(false).db_name(Some(SCHEMA))
                    .user(Some("rp_dependency_writer")).pass(Some(&input.writer_password))).await?;
                writer.exec_drop("UPDATE rp_options SET option_value=? WHERE option_id=? AND option_name='rp_autoload_probe_marker' AND option_value=?",
                    (NEW, monitor.option_id, OLD)).await?;
                ensure!(writer.affected_rows() == 1, "apply_writer_rows");
                writer.disconnect().await?;
                while watch.received.load(Ordering::SeqCst) == 0 {
                    tokio::time::sleep(Duration::from_millis(5)).await;
                }
                ensure!(old_permit.current(|cache| cache.lookup(2)).is_none(), "apply_pending_hit");
                Ok::<(), anyhow::Error>(())
            }).await.context("apply_deadline")?
        })?;
        let mismatch = monitor
            .unchanged()
            .expect_err("owned update must advance binlog")
            .downcast::<PositionMismatch>()?;
        monitor.verify_bypass(&mismatch)?;
        let applied = monitor.apply_live(&mut watch, &mismatch)?;
        ensure!(
            applied["commits"] == 1 && applied["events"].as_u64().is_some_and(|n| n > 0),
            "apply_commit_missing"
        );
        ensure!(old_permit.current(|_| true).is_none(), "apply_old_permit");
        let new_permit = monitor.guarded_cache(&watch, Some(&mismatch), cache.clone());
        ensure!(
            new_permit.current(|cache| cache.lookup(2)) == Some(None),
            "apply_cache_not_disabled"
        );
        let stats = cache.finish()?;
        ensure!(
            stats["hits"] == 1
                && stats["charged_bytes"] == 0
                && stats["retained_reply_bytes"]
                    .as_u64()
                    .is_some_and(|n| n > 0),
            "apply_cache_accounting"
        );
        monitor.finish_live(watch)?;
        monitor.finish()?;
        Ok(())
    }

    // Called only by the owned-database controller, which supplies private
    // credentials on stdin and tears down its uniquely created DB afterwards.
    #[test]
    #[ignore = "requires an owned, pinned MySQL or MariaDB fixture"]
    fn owned_disconnect_after_buffered_event() -> Result<()> {
        use serde::Deserialize;
        use std::io::Read;
        use std::sync::atomic::Ordering;

        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Input {
            port: u16,
            password: String,
            nonce: String,
            writer_password: String,
        }
        let mut input = Vec::new();
        std::io::stdin().take(1025).read_to_end(&mut input)?;
        ensure!(input.len() <= 1024, "disconnect_input_bound");
        let input: Input = serde_json::from_slice(&input)?;
        ensure!(input.writer_password.len() == 48, "disconnect_writer_input");
        let mut monitor = Monitor::start(input.port, &input.password, &input.nonce)?;
        let cache = Arc::new(OptionsCache::new());
        let mut watch = monitor.live_watch(cache.clone())?;
        let permit = monitor.guarded_cache(&watch, None, cache);
        ensure!(
            permit.current(|_| true) == Some(true),
            "disconnect_initial_permit"
        );
        monitor.runtime.block_on(async {
            timeout(DEADLINE, async {
                let mut writer = Conn::new(OptsBuilder::default().ip_or_hostname("127.0.0.1")
                    .tcp_port(input.port).prefer_socket(false).db_name(Some(SCHEMA))
                    .user(Some("rp_dependency_writer")).pass(Some(&input.writer_password))).await?;
                writer.exec_drop("UPDATE rp_options SET option_value=? WHERE option_id=? AND option_name='rp_autoload_probe_marker' AND option_value=?",
                    (NEW, monitor.option_id, OLD)).await?;
                ensure!(writer.affected_rows() == 1, "disconnect_writer_rows");
                writer.disconnect().await?;
                while watch.received.load(Ordering::SeqCst) == 0 {
                    tokio::time::sleep(Duration::from_millis(5)).await;
                }
                ensure!(permit.current(|_| true).is_none(), "disconnect_buffered_permit");
                monitor.control.query_drop(format!("KILL CONNECTION {}", watch.dump_id)).await?;
                let result = (&mut watch.task).await.context("disconnect_join")?;
                ensure!(result.is_err(), "disconnect_expected_stream_error");
                ensure!(permit.current(|_| true).is_none(), "disconnect_stale_permit");
                ensure!(autoload_id(&mut monitor.control, NEW).await? == monitor.option_id,
                    "disconnect_direct_read");
                Ok::<(), anyhow::Error>(())
            }).await.context("disconnect_deadline")?
        })?;
        drop(watch);
        monitor.finish()?;
        let mut restarted =
            Monitor::start_at_value(input.port, &input.password, &input.nonce, NEW)?;
        let restarted_cache = Arc::new(OptionsCache::new());
        ensure!(restarted.unchanged().is_ok(), "disconnect_restart_frontier");
        ensure!(
            restarted_cache.lookup(2).is_none(),
            "disconnect_restart_cache_not_empty"
        );
        ensure!(
            restarted_cache.finish()?["charged_bytes"] == 0,
            "disconnect_restart_cache_empty"
        );
        ensure!(
            permit.current(|_| true).is_none(),
            "disconnect_restart_old_permit"
        );
        restarted.finish()?;
        Ok(())
    }

    #[tokio::test]
    async fn cancelling_a_watcher_revokes_existing_permits() {
        let observed = Coordinate {
            file: "probe-bin.000001".into(),
            position: 100,
        };
        let applied = Arc::new(Mutex::new(WatchState {
            coordinate: Some(observed.clone()),
            pending: false,
        }));
        let permit = GuardedCache {
            applied: applied.clone(),
            observed,
            cache: Arc::new(OptionsCache::new()),
        };
        let lease = WatchLease(applied);
        let task = tokio::spawn(async move {
            let _lease = lease;
            std::future::pending::<()>().await;
        });
        task.abort();
        assert!(task.await.unwrap_err().is_cancelled());
        assert_eq!(permit.current(|_| 1), None);
    }

    #[tokio::test]
    async fn panicking_watcher_revokes_existing_permits() {
        let observed = Coordinate {
            file: "probe-bin.000001".into(),
            position: 100,
        };
        let applied = Arc::new(Mutex::new(WatchState {
            coordinate: Some(observed.clone()),
            pending: false,
        }));
        let permit = GuardedCache {
            applied: applied.clone(),
            observed,
            cache: Arc::new(OptionsCache::new()),
        };
        let lease = WatchLease(applied);
        let task = tokio::spawn(async move {
            let _lease = lease;
            panic!("synthetic watcher panic");
        });
        assert!(task.await.unwrap_err().is_panic());
        assert_eq!(permit.current(|_| 1), None);
    }

    #[tokio::test]
    async fn held_application_still_detects_stream_loss_and_bounds_buffering() {
        use futures_util::stream;
        use std::io;

        let (_sender, mut target) = oneshot::channel();
        let mut input = stream::iter(vec![Ok::<_, io::Error>(1u8)]);
        let mut buffered = VecDeque::new();
        let error = wait_for_target(&mut input, &mut target, &mut buffered, |_| 1, || {})
            .await
            .unwrap_err();
        assert!(error.to_string().contains("autoload_live_early_eof"));
        assert_eq!(buffered.len(), 1);

        let (_sender, mut target) = oneshot::channel();
        let mut input = stream::iter(vec![Err::<u8, _>(io::Error::other("dump failed"))]);
        let error = wait_for_target(&mut input, &mut target, &mut VecDeque::new(), |_| 1, || {})
            .await
            .unwrap_err();
        assert_eq!(error.to_string(), "dump failed");

        let (_sender, mut target) = oneshot::channel();
        let mut input = stream::iter((0..1025).map(|_| Ok::<_, io::Error>(1u8)));
        let mut buffered = VecDeque::new();
        let error = wait_for_target(&mut input, &mut target, &mut buffered, |_| 1, || {})
            .await
            .unwrap_err();
        assert!(error.to_string().contains("autoload_live_buffer_limit"));
        assert_eq!(buffered.len(), 1024);

        let (_sender, mut target) = oneshot::channel();
        let mut input = stream::iter(vec![Ok::<_, io::Error>(1u8)]);
        let mut buffered = VecDeque::new();
        let error = wait_for_target(
            &mut input,
            &mut target,
            &mut buffered,
            |_| 1024 * 1024 + 1,
            || {},
        )
        .await
        .unwrap_err();
        assert!(error.to_string().contains("autoload_live_buffer_limit"));
        assert!(buffered.is_empty());
    }

    #[tokio::test]
    async fn held_application_accepts_target_and_preserves_buffered_events() {
        use futures_util::stream;
        use std::io;

        let (sender, mut target) = oneshot::channel();
        let expected = Coordinate {
            file: "probe-bin.000001".into(),
            position: 101,
        };
        sender.send(expected.clone()).unwrap();
        let mut input = stream::pending::<std::result::Result<u8, io::Error>>();
        let mut buffered = VecDeque::from([7]);
        let actual = wait_for_target(&mut input, &mut target, &mut buffered, |_| 1, || {})
            .await
            .unwrap();
        assert_eq!(actual, expected);
        assert_eq!(buffered, VecDeque::from([7]));
    }

    #[test]
    fn old_permit_cannot_use_the_cache_after_progress_or_loss() {
        let observed = Coordinate {
            file: "probe-bin.000001".into(),
            position: 100,
        };
        let applied = Arc::new(Mutex::new(WatchState {
            coordinate: Some(observed.clone()),
            pending: false,
        }));
        let permit = GuardedCache {
            applied: applied.clone(),
            observed,
            cache: Arc::new(OptionsCache::new()),
        };
        assert_eq!(permit.current(|_| 1), Some(1));
        applied.lock().unwrap().pending = true;
        assert_eq!(
            permit.current(|_| panic!("pending event admitted")),
            None::<()>
        );
        applied.lock().unwrap().pending = false;
        applied
            .lock()
            .unwrap()
            .coordinate
            .as_mut()
            .unwrap()
            .position = 101;
        assert_eq!(
            permit.current(|_| panic!("old coordinate admitted")),
            None::<()>
        );
        applied.lock().unwrap().coordinate = Some(Coordinate {
            file: "probe-bin.000002".into(),
            position: 100,
        });
        assert_eq!(
            permit.current(|_| panic!("different file admitted")),
            None::<()>
        );
        applied.lock().unwrap().coordinate = None;
        assert_eq!(
            permit.current(|_| panic!("unavailable watcher admitted")),
            None::<()>
        );
    }
}
