use crate::{
    db::Input,
    monitor::{Monitor, PositionMismatch},
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
    post_bypass_verified: bool,
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
        Ok(Self {
            monitor,
            port: input.port,
            mismatch: None,
            post_bypass_verified: false,
            cache: Arc::new(OptionsCache::new()),
            identity,
        })
    }

    pub(crate) fn prepare(
        &mut self,
        input: &Input,
        seq: usize,
    ) -> Result<Option<Arc<OptionsCache>>> {
        ensure!(
            input.port == self.port
                && input.host == "127.0.0.1"
                && input.database == "rp_wpdb_probe",
            "autoload_bypass_scope_changed"
        );
        if seq < 4 {
            self.active()?;
            return Ok(Some(self.cache.clone()));
        }
        ensure!(seq == 4 && self.mismatch.is_none(), "autoload_bypass_phase");
        let result = self.monitor.unchanged();
        let stats = self.cache.finish()?;
        ensure!(
            stats["charged_bytes"] == 0,
            "autoload_bypass_cache_not_released"
        );
        let error = result
            .err()
            .ok_or_else(|| anyhow!("autoload_bypass_position_unchanged"))?;
        self.mismatch = Some(error.downcast::<PositionMismatch>()?);
        Ok(None)
    }

    pub(crate) fn active(&mut self) -> Result<()> {
        let result = match &self.mismatch {
            Some(mismatch) => self.monitor.verify_bypass(mismatch),
            None => self.monitor.unchanged(),
        };
        if result.is_err() {
            self.cache.disable();
        }
        result?;
        self.post_bypass_verified = self.mismatch.is_some();
        Ok(())
    }

    pub(crate) fn finish(self) -> Result<Value> {
        let Self {
            monitor,
            cache,
            mismatch,
            post_bypass_verified,
            ..
        } = self;
        let stats = cache.finish();
        monitor.finish()?;
        ensure!(post_bypass_verified, "autoload_bypass_post_fence_missing");
        let mismatch = mismatch.context("autoload_bypass_position_mismatch_missing")?;
        Ok(json!({"stats":stats?, "position_mismatch":mismatch.observation()}))
    }
}
