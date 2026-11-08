use crate::db::{Input, hex};
use crate::shared_object_cache::SharedObjectCache;
use anyhow::{Context, Result, ensure};
use mysql_async::{Conn, OptsBuilder, prelude::Queryable};
use recastpress_core::resource_budget::BudgetPool;
use serde_json::{Value, json};
use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};

pub const FIXTURE_VALUE: &[u8] = b"recastpress-shared-fixture";

pub struct Dependency {
    cache: SharedObjectCache,
    reads: AtomicUsize,
    hits: AtomicUsize,
}

impl Dependency {
    pub async fn read(&self, conn: &mut Conn) -> Result<Value, &'static str> {
        if let Some(value) = self.cache.get() {
            self.hits.fetch_add(1, Ordering::Relaxed);
            return Ok(json!({"value_hex":hex(&value), "source":"hit"}));
        }
        let ticket = self.cache.ticket().ok_or("dependency_fence_unavailable")?;
        self.reads.fetch_add(1, Ordering::Relaxed);
        let value: Vec<u8> = conn
            .query_first("SELECT value FROM rp_dependency_fixture WHERE id=1")
            .await
            .map_err(|_| "dependency_sql_failed")?
            .ok_or("dependency_fixture_missing")?;
        if value != FIXTURE_VALUE || !self.cache.fill(&ticket, &value) {
            return Err("dependency_fill_refused");
        }
        Ok(json!({"value_hex":hex(&value), "source":"miss_fill"}))
    }

    pub fn stats(&self) -> Value {
        json!({
            "dependency_sql_reads":self.reads.load(Ordering::Relaxed),
            "shared_hits":self.hits.load(Ordering::Relaxed),
            "charged_bytes":self.cache.charged_bytes(),
        })
    }
}

// A row lock makes this first cross-request fixture stable while the real
// binlog observer remains a separate experiment.
pub struct FixtureGuard {
    runtime: tokio::runtime::Runtime,
    conn: Conn,
    endpoint: (u16, String, String, String, String),
    pub dependency: Arc<Dependency>,
}

impl FixtureGuard {
    pub fn start(input: &Input) -> Result<Self> {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()?;
        let opts = OptsBuilder::default()
            .ip_or_hostname("127.0.0.1")
            .tcp_port(input.port)
            .user(Some(input.user.clone()))
            .pass(Some(input.password.clone()))
            .db_name(Some(input.database.clone()))
            .prefer_socket(false)
            .multi_statements(false)
            .local_files(false)
            .max_allowed_packet(Some(1024 * 1024));
        let conn = runtime.block_on(async { tokio::time::timeout(Duration::from_secs(10), async {
            let mut conn = Conn::new(opts).await?;
            // Verify the random fixture identity before taking any row lock.
            let nonce: Option<Vec<u8>> = conn
                .query_first("SELECT fixture_nonce FROM rp_dependency_fixture WHERE id=1")
                .await?;
            ensure!(nonce.as_deref() == Some(input.nonce.as_bytes()), "dependency_fixture_identity");
            conn.query_drop("START TRANSACTION").await?;
            let nonce: Option<Vec<u8>> = conn
                .query_first("SELECT fixture_nonce FROM rp_dependency_fixture WHERE id=1 LOCK IN SHARE MODE")
                .await?;
            ensure!(nonce.as_deref() == Some(input.nonce.as_bytes()), "dependency_locked_identity");
            Ok::<_, anyhow::Error>(conn)
        }).await }).context("dependency_guard_deadline")??;
        let cache = SharedObjectCache::new(BudgetPool::new(64));
        ensure!(cache.tracking_started(), "dependency_guard_cache_start");
        Ok(Self {
            runtime,
            conn,
            endpoint: (
                input.port,
                input.host.clone(),
                input.database.clone(),
                input.user.clone(),
                input.password.clone(),
            ),
            dependency: Arc::new(Dependency {
                cache,
                reads: AtomicUsize::new(0),
                hits: AtomicUsize::new(0),
            }),
        })
    }

    pub fn verify(&mut self, input: &Input) -> Result<()> {
        let (port, host, database, user, password) = &self.endpoint;
        ensure!(
            input.port == *port
                && input.host == *host
                && input.database == *database
                && input.user == *user
                && input.password == *password,
            "dependency_scope_changed"
        );
        self.active()
    }

    pub fn active(&mut self) -> Result<()> {
        self.runtime.block_on(async {
            tokio::time::timeout(Duration::from_secs(3), self.conn.query_drop("DO 1")).await
        })??;
        ensure!(
            self.conn
                .last_ok_packet()
                .is_some_and(|packet| packet.status_flags().bits() & 1 == 1),
            "dependency_guard_transaction_missing"
        );
        Ok(())
    }

    pub fn finish(self) -> Result<Value> {
        let Self {
            runtime,
            mut conn,
            dependency,
            ..
        } = self;
        dependency.cache.monitoring_lost();
        runtime.block_on(async {
            tokio::time::timeout(Duration::from_secs(4), async {
                let rollback = conn.query_drop("ROLLBACK").await;
                let disconnect = conn.disconnect().await;
                rollback?;
                disconnect?;
                Ok::<_, mysql_async::Error>(())
            })
            .await
        })??;
        Ok(dependency.stats())
    }
}
