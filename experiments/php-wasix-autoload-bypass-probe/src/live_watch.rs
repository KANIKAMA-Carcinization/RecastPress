use mysql_async::binlog::events::Event;
use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
};
use tokio::{sync::oneshot, task::JoinHandle};

use crate::options_cache::OptionsCache;

type Applied = Arc<Mutex<Option<Coordinate>>>;

pub(crate) struct GuardedCache {
    applied: Applied,
    observed: Coordinate,
    cache: Arc<OptionsCache>,
}

impl GuardedCache {
    pub(crate) fn current<T>(&self, use_cache: impl FnOnce(&OptionsCache) -> T) -> Option<T> {
        let applied = self.applied.lock().ok()?;
        (applied.as_ref() == Some(&self.observed)).then(|| use_cache(&self.cache))
    }
}

pub(crate) struct LiveWatch {
    applied: Applied,
    dump_id: u32,
    through: Option<oneshot::Sender<Coordinate>>,
    result: oneshot::Receiver<(usize, usize)>,
    task: JoinHandle<Result<()>>,
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
                let applied = Arc::new(Mutex::new(Some(checkpoint.clone())));
                let shared = applied.clone();
                let nonce = self.nonce.clone();
                let option_id = self.option_id;
                let (through, mut end) = oneshot::channel::<Coordinate>();
                let (done, result) = oneshot::channel();
                let task = tokio::spawn(async move {
                    let scan = async {
                        let through = tokio::select! {
                            end = &mut end => end.context("autoload_live_target_missing")?,
                            event = stream.next() => {
                                preamble.push_back(event.context("autoload_live_early_eof")??);
                                // One event may be buffered while the fixture holds application.
                                end.await.context("autoload_live_target_missing")?
                            }
                        };
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
                        *applied = Some(through);
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
                    if let Ok(mut applied) = shared.lock() {
                        *applied = None;
                    }
                    result
                });
                Ok(LiveWatch {
                    applied,
                    dump_id,
                    through: Some(through),
                    result,
                    task,
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
            applied.as_ref() == Some(&self.checkpoint),
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
    fn old_permit_cannot_use_the_cache_after_progress_or_loss() {
        let observed = Coordinate {
            file: "probe-bin.000001".into(),
            position: 100,
        };
        let applied = Arc::new(Mutex::new(Some(observed.clone())));
        let permit = GuardedCache {
            applied: applied.clone(),
            observed,
            cache: Arc::new(OptionsCache::new()),
        };
        assert_eq!(permit.current(|_| 1), Some(1));
        applied.lock().unwrap().as_mut().unwrap().position = 101;
        assert_eq!(
            permit.current(|_| panic!("old coordinate admitted")),
            None::<()>
        );
        *applied.lock().unwrap() = Some(Coordinate {
            file: "probe-bin.000002".into(),
            position: 100,
        });
        assert_eq!(
            permit.current(|_| panic!("different file admitted")),
            None::<()>
        );
        *applied.lock().unwrap() = None;
        assert_eq!(
            permit.current(|_| panic!("unavailable watcher admitted")),
            None::<()>
        );
    }
}
