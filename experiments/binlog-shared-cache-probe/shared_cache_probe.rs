use super::buffer_adapter::ProbeBuffer;
use super::shared_object_cache::SharedObjectCache;
use super::{
    check_row, close_fixture_connection, closed_socket_error, normalize_maria_initial_rotate,
    require_fixture_tls, verify_crc,
};
use futures_util::StreamExt;
use mysql_async::binlog::events::EventData;
use mysql_async::{BinlogStreamRequest, Conn, OptsBuilder, Row, prelude::Queryable};
use recastpress_core::resource_budget::BudgetPool;
use std::time::Duration;
use tokio::time::{sleep, timeout};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

async fn coordinates(connection: &mut Conn, status: &str) -> Result<(Vec<u8>, u64)> {
    let mut row: Row = connection
        .query_first(status)
        .await?
        .ok_or("no source status")?;
    Ok((
        row.take("File").ok_or("no source file")?,
        row.take("Position").ok_or("no source position")?,
    ))
}

async fn value(connection: &mut Conn) -> Result<Vec<u8>> {
    Ok(connection
        .query_first("SELECT value FROM rp_cache_probe WHERE id=1")
        .await?
        .ok_or("fixture cache row missing")?)
}

async fn connection_identity(connection: &mut Conn) -> Result<(u32, String, String, u16)> {
    Ok(connection
        .query_first("SELECT CONNECTION_ID(), USER(), @@datadir, @@port")
        .await?
        .ok_or("cache monitor connection identity missing")?)
}

async fn live_monitor_cut(
    control: &mut Conn,
    options: OptsBuilder,
    epoch: &[u8],
    file: &[u8],
    end: u64,
    maria: bool,
) -> Result<()> {
    let mut source = Conn::new(options).await?;
    require_fixture_tls(&mut source).await?;
    let (source_id, account, datadir, port) = connection_identity(&mut source).await?;
    let (control_id, control_account, control_datadir, control_port) =
        connection_identity(control).await?;
    if source_id == 0
        || source_id == control_id
        || (account.as_str(), datadir, port)
            != (control_account.as_str(), control_datadir, control_port)
    {
        return Err("live cache monitor is not a distinct owned connection".into());
    }
    let user = account.split_once('@').ok_or("invalid fixture account")?.0;
    let mut stream = source
        .get_binlog_stream(
            BinlogStreamRequest::new(7005)
                .with_filename(file)
                .with_pos(end),
        )
        .await?;
    let mut anchor = ProbeBuffer::resumed(epoch, file, end, maria);
    for initial in [true, false] {
        let mut event = timeout(Duration::from_secs(3), stream.next())
            .await?
            .ok_or("live monitor ended before its preamble")??;
        if initial && maria {
            event = normalize_maria_initial_rotate(event, file, end)?;
        }
        let _ = verify_crc(&event)?;
        let data = event
            .read_data()?
            .ok_or("unsupported live monitor preamble")?;
        if (initial && !matches!(data, EventData::RotateEvent(_)))
            || (!initial && !matches!(data, EventData::FormatDescriptionEvent(_)))
        {
            return Err("live monitor did not open at its expected anchor".into());
        }
        anchor.accept(&event, &data)?;
    }
    anchor.finish()?;
    let cache = SharedObjectCache::new(BudgetPool::new(64));
    if anchor.checkpoint_offset() != end || !cache.tracking_started() {
        return Err("live cache monitor anchor did not stabilize".into());
    }
    let ticket = cache.ticket().ok_or("no live monitor cache ticket")?;
    let payload = value(control).await?;
    if payload != b"after" || !cache.fill(&ticket, &payload) || cache.get().is_none() {
        return Err("cannot warm cache during live monitoring".into());
    }
    timeout(Duration::from_secs(3), async {
        loop {
            let rows: Vec<Row> = control.query("SHOW PROCESSLIST").await?;
            if rows.len() > 16 {
                return Err::<_, Box<dyn std::error::Error>>(
                    "cache processlist bound exceeded".into(),
                );
            }
            for mut row in rows {
                let id: u32 = row.take("Id").ok_or("cache processlist ID missing")?;
                if id != source_id {
                    continue;
                }
                let owner: String = row.take("User").ok_or("cache processlist user missing")?;
                let command: String = row
                    .take("Command")
                    .ok_or("cache processlist command missing")?;
                if owner != user {
                    return Err("live cache monitor process owner changed".into());
                }
                if command == "Binlog Dump" {
                    return Ok(());
                }
            }
            sleep(Duration::from_millis(20)).await;
        }
    })
    .await??;
    // Terminate only the captured, same-account live dump on this owned fixture.
    control
        .query_drop(format!("KILL CONNECTION {source_id}"))
        .await?;
    let lost = timeout(Duration::from_secs(3), stream.next()).await;
    cache.monitoring_lost();
    match lost? {
        None
        | Some(Err(mysql_async::Error::Driver(mysql_async::DriverError::ConnectionClosed))) => {}
        Some(Err(error)) if closed_socket_error(&error) => {}
        Some(Err(mysql_async::Error::Server(error))) if error.code == 1927 => {}
        _ => return Err("live binlog transport did not report connection loss".into()),
    }
    drop(stream);
    if cache.ticket().is_some()
        || cache.get().is_some()
        || cache.fill(&ticket, &payload)
        || cache.charged_bytes() != 0
        || value(control).await? != payload
    {
        return Err("live cut did not fence the cache while preserving the source".into());
    }
    Ok(())
}

pub(super) async fn run(
    control: &mut Conn,
    options: OptsBuilder,
    epoch: &[u8],
    status: &str,
    maria: bool,
) -> Result<serde_json::Value> {
    control.query_drop("CREATE TABLE rp_cache_probe (id BIGINT UNSIGNED PRIMARY KEY, value VARBINARY(64)) ENGINE=InnoDB").await?;
    control
        .query_drop("INSERT INTO rp_cache_probe VALUES (1, X'6265666F7265')")
        .await?;
    let (file, start) = coordinates(control, status).await?;
    let cache = SharedObjectCache::new(BudgetPool::new(64));
    if !cache.tracking_started() {
        return Err("cannot start bounded cache".into());
    }
    let old = cache.ticket().ok_or("no initial cache ticket")?;
    let old_value = value(control).await?;
    if old_value != b"before" || !cache.fill(&old, &old_value) {
        return Err("initial cache fill failed".into());
    }

    let mut writer = Conn::new(options.clone()).await?;
    require_fixture_tls(&mut writer).await?;
    writer.query_drop("BEGIN").await?;
    writer
        .query_drop("UPDATE rp_cache_probe SET value=X'726F6C6C6261636B' WHERE id=1")
        .await?;
    if value(&mut writer).await? != b"rollback" || value(control).await? != old_value {
        return Err("rollback fixture isolation mismatch".into());
    }
    writer.query_drop("ROLLBACK").await?;
    if coordinates(control, status).await? != (file.clone(), start)
        || value(control).await? != old_value
        || cache.ticket().as_ref() != Some(&old)
        || cache.get().as_deref() != Some(&old_value)
    {
        return Err("rollback disturbed the source or shared cache".into());
    }
    writer.query_drop("BEGIN").await?;
    writer
        .query_drop("UPDATE rp_cache_probe SET value=X'6166746572' WHERE id=1")
        .await?;
    writer.query_drop("COMMIT").await?;
    let (end_file, end) = coordinates(control, status).await?;
    close_fixture_connection(writer, "shared_cache_writer").await?;
    if end_file != file || end <= start {
        return Err("committed fixture endpoint invalid".into());
    }

    let mut source = Conn::new(options.clone()).await?;
    require_fixture_tls(&mut source).await?;
    let mut stream = source
        .get_binlog_stream(
            BinlogStreamRequest::new(7004)
                .with_filename(&file)
                .with_pos(start)
                .with_non_blocking(),
        )
        .await?;
    let mut buffer = ProbeBuffer::new(epoch, &file, start, maria);
    let mut target_rows = 0;
    let mut fresh = None;
    let scan: Result<()> = async {
        let mut events = 0;
        while let Some(event) = stream.next().await {
            let mut event = event?;
            if events == 0 && maria {
                event = normalize_maria_initial_rotate(event, &file, start)?;
            }
            events += 1;
            if events > 128 || event.data().len() > 1024 * 1024 {
                return Err("cache source event bound exceeded".into());
            }
            let checksum_verified = verify_crc(&event)?;
            let data = event.read_data()?.ok_or("unsupported cache source event")?;
            if matches!(data, EventData::RowsEvent(_) | EventData::XidEvent(_))
                && !checksum_verified
            {
                return Err("cache commit or row has no verified checksum".into());
            }
            if let EventData::RowsEvent(rows) = &data {
                let table = stream
                    .get_tme(rows.table_id())
                    .ok_or("cache table map missing")?;
                if table.database_name_raw() != b"recastpress_fixture"
                    || table.table_name_raw() != b"rp_cache_probe"
                {
                    return Err("unexpected cache source table".into());
                }
                for row in rows.rows(table) {
                    let (Some(before), Some(after)) = row? else {
                        return Err("expected an update row".into());
                    };
                    check_row(&before, &old_value)?;
                    check_row(&after, b"after")?;
                    target_rows += 1;
                }
            }
            buffer.accept_projected(&event, &data, |batch| {
                if target_rows != 1
                    || batch.rows().len() != 1
                    || fresh.is_some()
                    || !cache.committed_change()
                {
                    return Err("unexpected cache commit batch".into());
                }
                Ok(())
            })?;
            if matches!(data, EventData::XidEvent(_)) {
                if buffer.checkpoint_offset() != end
                    || buffer.applied != 1
                    || cache.get().is_some()
                    || cache.charged_bytes() != 0
                    || cache.fill(&old, &old_value)
                {
                    return Err("commit failed to fence the old fill".into());
                }
                let ticket = cache.ticket().ok_or("no post-commit ticket")?;
                let updated = value(control).await?;
                if ticket == old
                    || updated != b"after"
                    || !cache.fill(&ticket, &updated)
                    || cache.get().as_deref() != Some(&updated)
                {
                    return Err("fresh value was not shared after commit".into());
                }
                fresh = Some(ticket);
            } else if fresh.is_none()
                && (cache.get().as_deref() != Some(&old_value)
                    || cache.ticket().as_ref() != Some(&old))
            {
                return Err("uncommitted source frame changed the cache".into());
            }
        }
        buffer.finish()?;
        Ok(())
    }
    .await;
    cache.monitoring_lost();
    let closed = stream.close().await;
    scan?;
    closed?;
    let fresh = fresh.ok_or("finite source ended before the expected commit")?;
    if cache.ticket().is_some()
        || cache.get().is_some()
        || cache.fill(&fresh, b"after")
        || cache.charged_bytes() != 0
    {
        return Err("source end left the cache accessible".into());
    }
    live_monitor_cut(control, options, epoch, &file, end, maria).await?;
    Ok(serde_json::json!({
        "format_version": 2,
        "backend": if maria { "mariadb" } else { "mysql" },
        "commits": buffer.applied,
        "target_rows": target_rows,
        "rollback_preserved": true,
        "precommit_hit_preserved": true,
        "stale_fill_rejected": true,
        "refreshed_hit": true,
        "source_end_closed_cache": true,
        "live_cut_closed_cache": true,
        "final_charged_bytes": cache.charged_bytes(),
    }))
}
