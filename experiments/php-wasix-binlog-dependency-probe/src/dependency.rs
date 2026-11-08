use crate::db::{Input, hex};
use crate::monitor::Monitor;
use crate::shared_object_cache::SharedObjectCache;
use anyhow::{Context, Result, ensure};
use mysql_async::{Conn, prelude::Queryable};
use recastpress_core::resource_budget::BudgetPool;
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

pub const FIXTURE_VALUE: &[u8] = b"recastpress-shared-fixture";
pub const UPDATED_VALUE: &[u8] = b"recastpress-shared-updated";

pub struct Dependency {
    cache: SharedObjectCache,
    reads: AtomicUsize,
    hits: AtomicUsize,
}

impl Dependency {
    pub async fn read(&self, conn: &mut Conn) -> Result<Value, &'static str> {
        if let Some(value) = self.cache.get() {
            self.hits.fetch_add(1, Ordering::Relaxed);
            return Ok(json!({"value_hex":hex(&value),"source":"hit"}));
        }
        let ticket = self.cache.ticket().ok_or("dependency_fence_unavailable")?;
        self.reads.fetch_add(1, Ordering::Relaxed);
        let value: Vec<u8> = conn
            .query_first("SELECT value FROM rp_dependency_probe.rp_dependency_fixture WHERE id=1")
            .await
            .map_err(|_| "dependency_sql_failed")?
            .ok_or("dependency_fixture_missing")?;
        if ![FIXTURE_VALUE, UPDATED_VALUE].contains(&value.as_slice())
            || !self.cache.fill(&ticket, &value)
        {
            return Err("dependency_fill_refused");
        }
        Ok(json!({"value_hex":hex(&value),"source":"miss_fill"}))
    }

    fn stats(&self, invalidations: usize) -> Value {
        json!({"dependency_sql_reads":self.reads.load(Ordering::Relaxed),
            "shared_hits":self.hits.load(Ordering::Relaxed),"charged_bytes":self.cache.charged_bytes(),
            "commit_invalidations":invalidations})
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MonitorConfig {
    port: u16,
    pub password: String,
    pub nonce: String,
}

impl MonitorConfig {
    pub fn parse(wire: &[u8]) -> Result<Self> {
        ensure!(wire.len() <= 512, "monitor_config_bound");
        let config: Self = serde_json::from_value(
            crate::strict_json::parse(wire).map_err(|_| anyhow::anyhow!("monitor_config_json"))?,
        )?;
        ensure!(
            config.port != 0
                && [&config.password, &config.nonce]
                    .iter()
                    .all(|value| value.len() == 48
                        && value
                            .bytes()
                            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())),
            "monitor_config_scope"
        );
        Ok(config)
    }
}

pub struct FixtureGuard {
    monitor: Monitor,
    endpoint: (u16, String, String, String, String),
    fence: Option<Value>,
    pub dependency: Arc<Dependency>,
}

impl FixtureGuard {
    pub fn start(input: &Input, config: &MonitorConfig) -> Result<Self> {
        ensure!(
            input.port == config.port
                && input.nonce == config.nonce
                && input.password != config.password,
            "dependency_monitor_scope"
        );
        let monitor = Monitor::start(config.port, &config.password, &config.nonce)?;
        let cache = SharedObjectCache::new(BudgetPool::new(64));
        ensure!(cache.tracking_started(), "dependency_guard_cache_start");
        Ok(Self {
            monitor,
            endpoint: (
                input.port,
                input.host.clone(),
                input.database.clone(),
                input.user.clone(),
                input.password.clone(),
            ),
            fence: None,
            dependency: Arc::new(Dependency {
                cache,
                reads: AtomicUsize::new(0),
                hits: AtomicUsize::new(0),
            }),
        })
    }

    pub fn prepare(&mut self, input: &Input, seq: usize) -> Result<()> {
        let (port, host, database, user, password) = &self.endpoint;
        ensure!(
            input.port == *port
                && input.host == *host
                && input.database == *database
                && input.user == *user
                && input.password == *password,
            "dependency_scope_changed"
        );
        if seq < 3 {
            return self.active();
        }
        ensure!(seq == 3 && self.fence.is_none(), "dependency_commit_phase");
        let cache = &self.dependency.cache;
        let old = cache.ticket().context("dependency_old_ticket")?;
        ensure!(
            cache.get().as_deref() == Some(FIXTURE_VALUE),
            "dependency_old_cache_missing"
        );
        let observation = self.monitor.replay();
        if observation.is_err() {
            cache.monitoring_lost();
        }
        let observation = observation?;
        ensure!(
            cache.committed_change()
                && cache.get().is_none()
                && cache.charged_bytes() == 0
                && !cache.fill(&old, FIXTURE_VALUE),
            "dependency_commit_not_fenced"
        );
        self.fence = Some(observation);
        Ok(())
    }

    pub fn active(&mut self) -> Result<()> {
        let result = self.monitor.unchanged();
        if result.is_err() {
            self.dependency.cache.monitoring_lost();
        }
        result
    }

    pub fn finish(self) -> Result<Value> {
        let Self {
            monitor,
            dependency,
            fence,
            ..
        } = self;
        dependency.cache.monitoring_lost();
        monitor.finish()?;
        let fence = fence.context("dependency_commit_not_observed")?;
        Ok(json!({"stats":dependency.stats(1),"fence":fence}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn monitor_config_is_closed_bounded_and_uses_lowercase_hex() {
        let valid = json!({"port": 3306,"password": "a".repeat(48),"nonce": "b".repeat(48)});
        let wire = serde_json::to_string(&valid).unwrap();
        assert!(MonitorConfig::parse(wire.as_bytes()).is_ok());
        assert!(
            MonitorConfig::parse(
                wire.replace("\"port\":3306", "\"port\":3306,\"port\":3306")
                    .as_bytes()
            )
            .is_err()
        );
        for (key, wrong) in [
            ("port", json!(0)),
            ("password", json!("A".repeat(48))),
            ("nonce", json!("b".repeat(47))),
            ("unexpected", json!(true)),
        ] {
            let mut candidate = valid.clone();
            candidate[key] = wrong;
            assert!(MonitorConfig::parse(&serde_json::to_vec(&candidate).unwrap()).is_err());
        }
        assert!(MonitorConfig::parse(&[b' '; 513]).is_err());
    }
}
