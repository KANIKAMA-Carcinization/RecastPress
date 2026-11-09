use crate::{
    db::Input,
    monitor::{GuardedCache, LiveWatch, Monitor, PositionMismatch},
    options_cache::{GuardConfig, OptionsCache},
    source_identity::SourceIdentity,
};
use anyhow::{Context, Result, anyhow, ensure};
use serde_json::{Value, json};
use std::sync::Arc;

pub(crate) struct OptionsGuard {
    monitor: Monitor,
    port: u16,
    mismatch: Option<PositionMismatch>,
    watch: LiveWatch,
    live_commit: Option<Value>,
    pub(crate) cache: Arc<OptionsCache>,
    pub(crate) identity: SourceIdentity,
}

impl OptionsGuard {
    pub(crate) fn start(input: &Input, config: &GuardConfig) -> Result<Self> {
        ensure!(
            input.port == config.port
                && input.nonce == config.nonce
                && input.password != config.password
                && input.host == "127.0.0.1"
                && input.database == "rp_wpdb_probe",
            "autoload_bypass_guard_scope"
        );
        let mut monitor = Monitor::start(config.port, &config.password, &config.nonce)?;
        let identity = monitor.source_identity()?;
        let cache = Arc::new(OptionsCache::new());
        let watch = monitor.live_watch(cache.clone())?;
        Ok(Self {
            monitor,
            port: input.port,
            mismatch: None,
            watch,
            live_commit: None,
            cache,
            identity,
        })
    }

    pub(crate) fn prepare(&mut self, input: &Input, seq: usize) -> Result<Option<GuardedCache>> {
        ensure!(
            input.port == self.port
                && input.host == "127.0.0.1"
                && input.database == "rp_wpdb_probe",
            "autoload_bypass_scope_changed"
        );
        match seq {
            2 | 3 => self.monitor.unchanged()?,
            4 => {
                self.monitor.live_at_start(&self.watch)?;
                let error = self
                    .monitor
                    .unchanged()
                    .err()
                    .ok_or_else(|| anyhow!("autoload_bypass_position_unchanged"))?;
                self.mismatch = Some(error.downcast::<PositionMismatch>()?);
            }
            _ => return Err(anyhow!("autoload_bypass_phase")),
        }
        Ok(Some(self.monitor.guarded_cache(
            &self.watch,
            self.mismatch.as_ref(),
            self.cache.clone(),
        )))
    }

    pub(crate) fn active(&mut self) -> Result<()> {
        if let Some(mismatch) = &self.mismatch {
            self.monitor.verify_bypass(mismatch)?;
            self.live_commit = Some(self.monitor.apply_live(&mut self.watch, mismatch)?);
        } else {
            self.monitor.unchanged()?;
            self.monitor.live_at_start(&self.watch)?;
        }
        Ok(())
    }

    pub(crate) fn finish(self) -> Result<Value> {
        let Self {
            mut monitor,
            cache,
            mismatch,
            watch,
            live_commit,
            ..
        } = self;
        let stats = cache.finish();
        let closed = monitor.finish_live(watch);
        monitor.finish()?;
        closed?;
        let mismatch = mismatch.context("autoload_bypass_position_mismatch_missing")?;
        Ok(
            json!({"stats":stats?, "position_mismatch":mismatch.observation(),
            "live_commit":live_commit.context("autoload_live_commit_missing")?}),
        )
    }
}
