//! Fixed, bounded change reader for the controller-owned dependency fixture.

#[allow(dead_code)]
#[path = "binlog_buffer.rs"]
mod binlog_buffer;

use anyhow::{Context, Result, anyhow, ensure};
use binlog_buffer::{ProbeBuffer, wire};
use futures_util::StreamExt;
use mysql_async::binlog::events::{EventData, RowsEventData};
use mysql_async::consts::ColumnType;
use mysql_async::{BinlogStream, BinlogStreamRequest, Conn, OptsBuilder, Row, prelude::Queryable};
use serde_json::{Value, json};
use std::time::Duration;
use tokio::{runtime::Runtime, time::timeout};

const ACCOUNT: &str = "rp_dependency_monitor";
const SCHEMA: &str = "rp_dependency_probe";
const TABLE: &[u8] = b"rp_dependency_fixture";
const SERVER_ID: u32 = 7;
const OLD: &[u8] = b"recastpress-shared-fixture";
const NEW: &[u8] = b"recastpress-shared-updated";
const DEADLINE: Duration = Duration::from_secs(10);
const MAX_EVENTS: usize = 16;
const MAX_EVENT_BYTES: usize = 16 * 1024;
const MAX_SPAN: u64 = 64 * 1024;
const MAX_POSITION: u64 = 64 * 1024 * 1024;

#[derive(Debug, Eq, PartialEq)]
struct Source {
    version: String,
    account: String,
    server_id: u32,
}

#[derive(Eq, PartialEq)]
struct Coordinate {
    file: String,
    position: u64,
}

impl Source {
    fn maria(&self) -> bool {
        self.version.starts_with("11.8.9-MariaDB")
    }
}

pub(crate) struct Monitor {
    runtime: Runtime,
    control: Conn,
    options: OptsBuilder,
    nonce: String,
    source: Source,
    checkpoint: Coordinate,
    replayed: bool,
}

async fn connect(options: OptsBuilder) -> Result<Conn> {
    Conn::new(options)
        .await
        .map_err(|_| anyhow!("dependency_monitor_connect"))
}

async fn source(connection: &mut Conn, nonce: &str) -> Result<Source> {
    type Profile = (String, String, u32, u8, String, String, String);
    let (version, account, server_id, enabled, format, image, checksum): Profile = connection
        .query_first("SELECT VERSION(), CURRENT_USER(), @@GLOBAL.server_id, @@GLOBAL.log_bin, @@GLOBAL.binlog_format, @@GLOBAL.binlog_row_image, @@GLOBAL.binlog_checksum")
        .await.map_err(|_| anyhow!("dependency_monitor_profile"))?
        .context("dependency_monitor_profile_missing")?;
    ensure!(
        account == "rp_dependency_monitor@%"
            && server_id == SERVER_ID
            && enabled == 1
            && format == "ROW"
            && image == "FULL"
            && checksum == "CRC32",
        "dependency_monitor_profile_mismatch"
    );
    let source = Source {
        version,
        account,
        server_id,
    };
    if !source.maria() {
        ensure!(source.version == "8.4.13", "dependency_monitor_version");
        let mysql_profile: (String, u8) = connection
            .query_first("SELECT @@GLOBAL.gtid_mode, CAST(@@GLOBAL.binlog_transaction_compression AS UNSIGNED)")
            .await
            .map_err(|_| anyhow!("dependency_monitor_mysql_profile"))?
            .context("dependency_monitor_mysql_profile_missing")?;
        ensure!(
            mysql_profile.0 == "OFF" && mysql_profile.1 == 0,
            "dependency_monitor_mysql_profile_mismatch"
        );
    }
    let owned: Option<Vec<u8>> = connection
        .query_first("SELECT nonce FROM rp_dependency_probe.rp_dependency_identity WHERE id=1")
        .await
        .map_err(|_| anyhow!("dependency_monitor_identity"))?;
    ensure!(
        owned.as_deref() == Some(nonce.as_bytes()),
        "dependency_monitor_identity_mismatch"
    );
    Ok(source)
}

async fn coordinate(connection: &mut Conn, source: &Source) -> Result<Coordinate> {
    let statement = if source.maria() {
        "SHOW MASTER STATUS"
    } else {
        "SHOW BINARY LOG STATUS"
    };
    let mut row: Row = connection
        .query_first(statement)
        .await
        .map_err(|_| anyhow!("dependency_monitor_coordinate"))?
        .context("dependency_monitor_coordinate_missing")?;
    let file = row
        .take_opt::<String, _>("File")
        .and_then(|value| value.ok())
        .context("dependency_monitor_source_file")?;
    let include = row
        .take_opt::<Vec<u8>, _>("Binlog_Do_DB")
        .and_then(|value| value.ok());
    let exclude = row
        .take_opt::<Vec<u8>, _>("Binlog_Ignore_DB")
        .and_then(|value| value.ok());
    ensure!(
        file.strip_prefix("probe-bin.")
            .is_some_and(|suffix| suffix.len() == 6
                && suffix.bytes().all(|byte| byte.is_ascii_digit())
                && suffix != "000000")
            && include.as_deref() == Some(SCHEMA.as_bytes())
            && exclude.as_deref() == Some(b""),
        "dependency_monitor_source_filter"
    );
    let position = row
        .take_opt::<u64, _>("Position")
        .and_then(|value| value.ok())
        .context("dependency_monitor_position")?;
    ensure!(
        (5..=MAX_POSITION).contains(&position),
        "dependency_monitor_position_bound"
    );
    Ok(Coordinate { file, position })
}

async fn dependency(connection: &mut Conn, expected: &[u8]) -> Result<()> {
    let value: Option<Vec<u8>> = connection
        .query_first("SELECT value FROM rp_dependency_probe.rp_dependency_fixture WHERE id=1")
        .await
        .map_err(|_| anyhow!("dependency_monitor_value"))?;
    ensure!(
        value.as_deref() == Some(expected),
        "dependency_monitor_value_mismatch"
    );
    Ok(())
}

impl Monitor {
    pub(crate) fn start(port: u16, password: &str, nonce: &str) -> Result<Self> {
        ensure!(
            port != 0
                && !password.is_empty()
                && password.len() <= 128
                && nonce.len() == 48
                && nonce
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)),
            "dependency_monitor_input"
        );
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|_| anyhow!("dependency_monitor_runtime"))?;
        let options = OptsBuilder::default()
            .ip_or_hostname("127.0.0.1")
            .tcp_port(port)
            .prefer_socket(false)
            .user(Some(ACCOUNT))
            .pass(Some(password))
            .db_name(Some(SCHEMA))
            .max_allowed_packet(Some(MAX_EVENT_BYTES));
        let (control, identity, checkpoint) = runtime.block_on(async {
            timeout(DEADLINE, async {
                let mut connection = connect(options.clone()).await?;
                let identity = source(&mut connection, nonce).await?;
                let position = coordinate(&mut connection, &identity).await?;
                dependency(&mut connection, OLD).await?;
                Ok::<_, anyhow::Error>((connection, identity, position))
            })
            .await
            .context("dependency_monitor_start_timeout")?
        })?;
        Ok(Self {
            runtime,
            control,
            options,
            nonce: nonce.to_owned(),
            source: identity,
            checkpoint,
            replayed: false,
        })
    }

    pub(crate) fn unchanged(&mut self) -> Result<()> {
        self.runtime.block_on(async {
            timeout(DEADLINE, async {
                ensure!(
                    source(&mut self.control, &self.nonce).await? == self.source,
                    "dependency_monitor_source_changed"
                );
                ensure!(
                    coordinate(&mut self.control, &self.source).await? == self.checkpoint,
                    "dependency_monitor_position_changed"
                );
                Ok(())
            })
            .await
            .context("dependency_monitor_unchanged_timeout")?
        })
    }

    pub(crate) fn replay(&mut self) -> Result<Value> {
        ensure!(!self.replayed, "dependency_monitor_replay_reused");
        let start = self.checkpoint.position;
        let (end, events, checksums) = self.runtime.block_on(async {
            timeout(DEADLINE, async {
                ensure!(
                    source(&mut self.control, &self.nonce).await? == self.source,
                    "dependency_monitor_source_changed"
                );
                let end = coordinate(&mut self.control, &self.source).await?;
                ensure!(
                    end.file == self.checkpoint.file
                        && end.position > start
                        && end.position - start <= MAX_SPAN,
                    "dependency_monitor_span"
                );
                let mut dump = connect(self.options.clone()).await?;
                ensure!(
                    source(&mut dump, &self.nonce).await? == self.source
                        && coordinate(&mut dump, &self.source).await? == end,
                    "dependency_monitor_dump_identity"
                );
                let mut stream = dump
                    .get_binlog_stream(
                        BinlogStreamRequest::new(7001)
                            .with_filename(end.file.as_bytes())
                            .with_pos(start)
                            .with_non_blocking(),
                    )
                    .await
                    .map_err(|_| anyhow!("dependency_monitor_dump_open"))?;
                let result = scan(
                    &mut stream,
                    &self.source,
                    self.nonce.as_bytes(),
                    start,
                    &end,
                )
                .await;
                let closed = stream
                    .close()
                    .await
                    .map_err(|_| anyhow!("dependency_monitor_dump_close"));
                let (events, checksums) = result?;
                closed?;
                dependency(&mut self.control, NEW).await?;
                ensure!(
                    source(&mut self.control, &self.nonce).await? == self.source
                        && coordinate(&mut self.control, &self.source).await? == end,
                    "dependency_monitor_final_source"
                );
                Ok::<_, anyhow::Error>((end, events, checksums))
            })
            .await
            .context("dependency_monitor_replay_timeout")?
        })?;
        self.checkpoint = end;
        self.replayed = true;
        Ok(
            json!({"source_file":self.checkpoint.file,"from_position":start,"through_position":self.checkpoint.position,"events":events,
            "checksums":checksums,"rows":1,"commits":1,"eof":true,"source_reverified":true}),
        )
    }

    pub(crate) fn finish(self) -> Result<()> {
        self.runtime.block_on(async {
            timeout(DEADLINE, self.control.disconnect())
                .await
                .context("dependency_monitor_close_timeout")?
                .map_err(|_| anyhow!("dependency_monitor_close"))
        })
    }
}

fn check_update(stream: &BinlogStream, event: &RowsEventData<'_>) -> Result<()> {
    let table = stream
        .get_tme(event.table_id())
        .context("dependency_monitor_missing_table_map")?;
    let column = |index| {
        table
            .get_column_type(index)
            .map_err(|_| anyhow!("dependency_monitor_column_type"))
    };
    ensure!(
        table.database_name_raw() == SCHEMA.as_bytes()
            && table.table_name_raw() == TABLE
            && table.columns_count() == 2
            && column(0)? == Some(ColumnType::MYSQL_TYPE_LONGLONG)
            && matches!(
                column(1)?,
                Some(ColumnType::MYSQL_TYPE_VARCHAR | ColumnType::MYSQL_TYPE_VAR_STRING)
            ),
        "dependency_monitor_wrong_table"
    );
    let mut rows = event.rows(table);
    let Some((Some(before), Some(after))) = rows
        .next()
        .transpose()
        .map_err(|_| anyhow!("dependency_monitor_row_decode"))?
    else {
        return Err(anyhow!("dependency_monitor_update_shape"));
    };
    wire::check_row(&before, OLD).map_err(|_| anyhow!("dependency_monitor_before_image"))?;
    wire::check_row(&after, NEW).map_err(|_| anyhow!("dependency_monitor_after_image"))?;
    ensure!(rows.next().is_none(), "dependency_monitor_extra_row");
    Ok(())
}

async fn scan(
    stream: &mut BinlogStream,
    source: &Source,
    epoch: &[u8],
    start: u64,
    end: &Coordinate,
) -> Result<(usize, usize)> {
    let file = end.file.as_bytes();
    let mut decoder = ProbeBuffer::resumed(epoch, file, start, source.maria());
    let (mut events, mut checksums, mut tables, mut rows) = (0, 0, 0, 0);
    while let Some(event) = stream.next().await {
        ensure!(
            events < MAX_EVENTS && decoder.applied == 0,
            "dependency_monitor_extra_event"
        );
        let mut event = event.map_err(|_| anyhow!("dependency_monitor_dump_event"))?;
        ensure!(
            event.data().len() <= MAX_EVENT_BYTES,
            "dependency_monitor_event_bytes"
        );
        if events == 0 && source.maria() {
            event = wire::normalize_maria_initial_rotate(event, file, start)
                .map_err(|_| anyhow!("dependency_monitor_initial_rotate"))?;
        }
        let checksum =
            wire::verify_crc(&event).map_err(|_| anyhow!("dependency_monitor_event_crc"))?;
        checksums += usize::from(checksum);
        let data = event
            .read_data()
            .map_err(|_| anyhow!("dependency_monitor_event_decode"))?
            .context("dependency_monitor_event_kind")?;
        if events >= 2 {
            ensure!(
                checksum && event.header().server_id() == source.server_id,
                "dependency_monitor_event_identity"
            );
        }
        match &data {
            EventData::TableMapEvent(table) => {
                ensure!(
                    tables == 0
                        && table.database_name_raw() == SCHEMA.as_bytes()
                        && table.table_name_raw() == TABLE,
                    "dependency_monitor_table_map"
                );
                tables += 1;
            }
            EventData::RowsEvent(event) => {
                ensure!(rows == 0 && tables == 1, "dependency_monitor_row_count");
                check_update(stream, event)?;
                rows += 1;
            }
            _ => {}
        }
        decoder
            .accept_projected(&event, &data, |batch| {
                if rows != 1
                    || batch.rows().len() != 1
                    || batch.from().offset() != start
                    || batch.through().offset() != end.position
                {
                    return Err("unexpected dependency commit boundary".into());
                }
                Ok(())
            })
            .map_err(|_| anyhow!("dependency_monitor_commit_boundary"))?;
        events += 1;
    }
    decoder
        .finish()
        .map_err(|_| anyhow!("dependency_monitor_incomplete_dump"))?;
    ensure!(
        decoder.checkpoint_offset() == end.position
            && decoder.applied == 1
            && rows == 1
            && tables == 1,
        "dependency_monitor_missing_commit"
    );
    Ok((events, checksums))
}
