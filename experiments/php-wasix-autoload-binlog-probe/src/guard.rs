use crate::{
    db::Input,
    monitor::Monitor,
    options_cache::{GuardConfig, OptionsCache},
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::sync::Arc;

pub(crate) struct OptionsGuard {
    monitor: Monitor,
    port: u16,
    fence: Option<Value>,
    pub(crate) cache: Arc<OptionsCache>,
}

impl OptionsGuard {
    pub(crate) fn start(input: &Input, config: &GuardConfig) -> Result<Self> {
        ensure!(
            input.port == config.port
                && input.nonce == config.nonce
                && input.password != config.password
                && input.host == "127.0.0.1"
                && input.database == "rp_wpdb_probe",
            "autoload_binlog_guard_scope"
        );
        let monitor = Monitor::start(config.port, &config.password, &config.nonce)?;
        Ok(Self {
            monitor,
            port: input.port,
            fence: None,
            cache: Arc::new(OptionsCache::new()),
        })
    }

    pub(crate) fn prepare(&mut self, input: &Input, seq: usize) -> Result<()> {
        ensure!(
            input.port == self.port
                && input.host == "127.0.0.1"
                && input.database == "rp_wpdb_probe",
            "autoload_binlog_scope_changed"
        );
        if seq < 4 {
            return self.active();
        }
        ensure!(
            seq == 4 && self.fence.is_none(),
            "autoload_binlog_commit_phase"
        );
        let observation = self.monitor.replay();
        if observation.is_err() {
            self.cache.disable();
        }
        let observation = observation?;
        self.cache.invalidate()?;
        self.fence = Some(observation);
        Ok(())
    }

    pub(crate) fn active(&mut self) -> Result<()> {
        let result = self.monitor.unchanged();
        if result.is_err() {
            self.cache.disable();
        }
        result
    }

    pub(crate) fn finish(self) -> Result<Value> {
        let Self {
            monitor,
            cache,
            fence,
            ..
        } = self;
        let stats = cache.finish();
        monitor.finish()?;
        Ok(json!({"stats":stats?, "fence":fence.context("autoload_binlog_fence_missing")?}))
    }
}
