//! A one-reply cache scoped to the fixture's live, read-locked SQL connection.

use crate::{db::Input, result_budget::BoundedWriter};
use anyhow::{Context, Result, anyhow, ensure};
use mysql_async::{Conn, OptsBuilder, prelude::Queryable};
use recastpress_core::resource_budget::{BudgetPool, Reservation, ResourceKind};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{runtime::Runtime, time::timeout};

const REPLY_LIMIT: usize = 128 * 1024;
const DEADLINE: Duration = Duration::from_secs(10);
const HOST: &str = "127.0.0.1";
const DATABASE: &str = "rp_wpdb_probe";
const ACCOUNT: &str = "rp_options_guard";
const MARKER: &str =
    "SELECT CONNECTION_ID(), nonce FROM `rp_options_guard_fixture`.`identity` WHERE id=1";
const LOCK: &str =
    "LOCK TABLES `rp_wpdb_probe`.`rp_options` READ, `rp_options_guard_fixture`.`identity` READ";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct GuardConfig {
    port: u16,
    pub(crate) password: String,
    pub(crate) nonce: String,
}

fn lower_hex(value: &str) -> bool {
    value
        .bytes()
        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

impl GuardConfig {
    pub(crate) fn parse(wire: &[u8]) -> Result<Self> {
        ensure!(wire.len() <= 1024, "options_guard_input_bound");
        let input: Self =
            serde_json::from_slice(wire).map_err(|_| anyhow!("options_guard_input"))?;
        ensure!(
            input.port != 0
                && input.password.len() == 48
                && input.nonce.len() == 48
                && lower_hex(&input.password)
                && lower_hex(&input.nonce),
            "options_guard_input_shape"
        );
        Ok(input)
    }
}

fn safe_status(status: u16) -> bool {
    const AUTOCOMMIT: u16 = 0x0002;
    const UNSAFE: u16 = 0x0001 | 0x0008 | 0x2000;
    status & AUTOCOMMIT != 0 && status & UNSAFE == 0
}

fn valid_column(column: &Value, name: &str) -> bool {
    column.as_object().is_some_and(|column| column.len() == 6)
        && column["name_hex"] == name
        && [
            ("type", u64::from(u8::MAX)),
            ("flags", u64::from(u16::MAX)),
            ("character_set", u64::from(u16::MAX)),
            ("declared_length", u64::from(u32::MAX)),
            ("decimals", u64::from(u8::MAX)),
        ]
        .into_iter()
        .all(|(key, max)| column[key].as_u64().is_some_and(|value| value <= max))
}

fn valid_reply(status: u16, reply: &Value) -> bool {
    safe_status(status)
        && reply.as_object().is_some_and(|object| object.len() == 6)
        && reply["status"].as_u64() == Some(u64::from(status))
        && reply["warnings"].as_u64() == Some(0)
        && reply["affected_rows"] == "0"
        && reply.get("insert_id") == Some(&Value::Null)
        && reply["columns"].as_array().is_some_and(|columns| {
            columns.len() == 2
                && valid_column(&columns[0], "6f7074696f6e5f6e616d65")
                && valid_column(&columns[1], "6f7074696f6e5f76616c7565")
        })
        && reply["rows"].as_array().is_some_and(|rows| {
            !rows.is_empty()
                && rows.len() <= 256
                && rows.iter().all(|row| {
                    row.as_array().is_some_and(|cells| {
                        cells.len() == 2
                            && cells.iter().all(|cell| {
                                cell.is_null()
                                    || cell.as_str().is_some_and(|value| {
                                        value.len() <= REPLY_LIMIT
                                            && value.len() % 2 == 0
                                            && lower_hex(value)
                                    })
                            })
                    })
                })
        })
}

struct Entry {
    status: u16,
    reply: Box<[u8]>,
    _reservation: Reservation,
}

struct CacheState {
    active: bool,
    sql_reads: usize,
    hits: usize,
    retained_reply_bytes: usize,
    entry: Option<Entry>,
}

pub(crate) struct OptionsCache {
    state: Mutex<CacheState>,
    budget: BudgetPool,
}

impl OptionsCache {
    fn new() -> Self {
        Self {
            state: Mutex::new(CacheState {
                active: true,
                sql_reads: 0,
                hits: 0,
                retained_reply_bytes: 0,
                entry: None,
            }),
            budget: BudgetPool::new(REPLY_LIMIT),
        }
    }

    pub(crate) fn lookup(&self, status: u16) -> Option<Value> {
        let mut state = self.state.lock().ok()?;
        if !state.active {
            return None;
        }
        let entry = state
            .entry
            .as_ref()
            .filter(|entry| entry.status == status)?;
        let value = serde_json::from_slice(&entry.reply).ok()?;
        state.hits = state.hits.saturating_add(1);
        Some(value)
    }

    pub(crate) fn record_sql(&self) {
        if let Ok(mut state) = self.state.lock()
            && state.active
        {
            state.sql_reads = state.sql_reads.saturating_add(1);
        }
    }

    pub(crate) fn remember(&self, status: u16, reply: &Value) {
        let Ok(mut state) = self.state.lock() else {
            return;
        };
        if !state.active
            || state.sql_reads != 1
            || state.entry.is_some()
            || !valid_reply(status, reply)
        {
            return;
        }
        let Ok(mut writer) = BoundedWriter::new(REPLY_LIMIT) else {
            return;
        };
        if serde_json::to_writer(&mut writer, reply).is_err() {
            return;
        }
        let wire = writer.into_bytes().into_boxed_slice();
        let Ok(reservation) = self
            .budget
            .try_reserve(ResourceKind::ObjectCache, wire.len())
        else {
            return;
        };
        state.retained_reply_bytes = wire.len();
        state.entry = Some(Entry {
            status,
            reply: wire,
            _reservation: reservation,
        });
    }

    fn disable(&self) {
        // A poisoned cache cannot serve hits; release any existing reservation.
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        state.active = false;
        state.entry = None;
    }

    fn finish(&self) -> Result<Value> {
        self.disable();
        let state = self
            .state
            .lock()
            .map_err(|_| anyhow!("options_cache_poisoned"))?;
        let charged = self
            .budget
            .stats()
            .map_err(|_| anyhow!("options_cache_budget"))?
            .claimed_for(ResourceKind::ObjectCache);
        ensure!(
            state.sql_reads == 1
                && state.hits == 1
                && state.retained_reply_bytes > 0
                && charged == 0,
            "options_cache_completion"
        );
        Ok(
            json!({"sql_reads":state.sql_reads,"hits":state.hits,"charged_bytes":charged,
            "retained_reply_bytes":state.retained_reply_bytes}),
        )
    }
}

pub(crate) struct OptionsGuard {
    runtime: Runtime,
    connection: Option<Conn>,
    port: u16,
    nonce: String,
    id: u32,
    pub(crate) cache: Arc<OptionsCache>,
}

fn endpoint(input: &Input, port: u16) -> bool {
    input.host == HOST && input.database == DATABASE && input.user == DATABASE && input.port == port
}

async fn marker(connection: &mut Conn, id: u32, nonce: &str) -> Result<()> {
    let (seen_id, seen_nonce): (u32, Vec<u8>) = connection
        .query_first(MARKER)
        .await
        .map_err(|_| anyhow!("options_guard_marker_read"))?
        .context("options_guard_marker_missing")?;
    ensure!(
        id != 0 && seen_id == id && seen_nonce == nonce.as_bytes(),
        "options_guard_marker_mismatch"
    );
    Ok(())
}

impl OptionsGuard {
    pub(crate) fn start(input: &Input, config: &GuardConfig) -> Result<Self> {
        ensure!(
            endpoint(input, config.port) && input.nonce == config.nonce,
            "options_guard_actor_endpoint"
        );
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|_| anyhow!("options_guard_runtime"))?;
        let options = OptsBuilder::default()
            .ip_or_hostname(HOST)
            .tcp_port(config.port)
            .prefer_socket(false)
            .db_name(Some(DATABASE))
            .user(Some(ACCOUNT))
            .pass(Some(&config.password));
        let (connection, id) = runtime.block_on(async {
            timeout(DEADLINE, async {
                let mut connection = Conn::new(options)
                    .await
                    .map_err(|_| anyhow!("options_guard_connect"))?;
                let id = connection.id();
                let account: String = connection
                    .query_first("SELECT CURRENT_USER()")
                    .await
                    .map_err(|_| anyhow!("options_guard_account"))?
                    .context("options_guard_account_missing")?;
                ensure!(
                    account == "rp_options_guard@%",
                    "options_guard_account_mismatch"
                );
                marker(&mut connection, id, &config.nonce).await?;
                connection
                    .query_drop(LOCK)
                    .await
                    .map_err(|_| anyhow!("options_guard_lock"))?;
                marker(&mut connection, id, &config.nonce).await?;
                Ok::<_, anyhow::Error>((connection, id))
            })
            .await
            .context("options_guard_start_timeout")?
        })?;
        Ok(Self {
            runtime,
            connection: Some(connection),
            port: config.port,
            nonce: config.nonce.clone(),
            id,
            cache: Arc::new(OptionsCache::new()),
        })
    }

    pub(crate) fn verify(&mut self, input: &Input) -> Result<()> {
        if !endpoint(input, self.port) {
            self.cache.disable();
            return Err(anyhow!("options_guard_actor_endpoint"));
        }
        self.active()
    }

    pub(crate) fn active(&mut self) -> Result<()> {
        let result = self.runtime.block_on(async {
            let connection = self
                .connection
                .as_mut()
                .context("options_guard_connection_missing")?;
            timeout(DEADLINE, marker(connection, self.id, &self.nonce))
                .await
                .context("options_guard_marker_timeout")?
        });
        if result.is_err() {
            self.cache.disable();
        }
        result
    }

    fn close(&mut self) -> Result<()> {
        let Some(mut connection) = self.connection.take() else {
            return Ok(());
        };
        self.runtime.block_on(async {
            let unlocked = timeout(DEADLINE, connection.query_drop("UNLOCK TABLES"))
                .await
                .context("options_guard_unlock_timeout")
                .and_then(|result| result.map_err(|_| anyhow!("options_guard_unlock")));
            let disconnected = timeout(DEADLINE, connection.disconnect())
                .await
                .context("options_guard_close_timeout")
                .and_then(|result| result.map_err(|_| anyhow!("options_guard_close")));
            unlocked.and(disconnected)
        })
    }

    pub(crate) fn finish(mut self) -> Result<Value> {
        let summary = self.cache.finish();
        self.close()?;
        summary
    }
}

impl Drop for OptionsGuard {
    fn drop(&mut self) {
        self.cache.disable();
        let _ = self.close();
    }
}
