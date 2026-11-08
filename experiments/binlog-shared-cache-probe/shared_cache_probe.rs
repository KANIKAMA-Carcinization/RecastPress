use super::buffer_adapter::ProbeBuffer;
use super::shared_object_cache::SharedObjectCache;
use super::{
    check_row, close_fixture_connection, normalize_maria_initial_rotate, require_fixture_tls,
    verify_crc,
};
use futures_util::StreamExt;
use mysql_async::binlog::events::EventData;
use mysql_async::{BinlogStreamRequest, Conn, OptsBuilder, Row, prelude::Queryable};
use recastpress_core::resource_budget::BudgetPool;

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

    let mut source = Conn::new(options).await?;
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
    Ok(serde_json::json!({
        "format_version": 1,
        "backend": if maria { "mariadb" } else { "mysql" },
        "commits": buffer.applied,
        "target_rows": target_rows,
        "rollback_preserved": true,
        "precommit_hit_preserved": true,
        "stale_fill_rejected": true,
        "refreshed_hit": true,
        "source_end_closed_cache": true,
        "final_charged_bytes": cache.charged_bytes(),
    }))
}
