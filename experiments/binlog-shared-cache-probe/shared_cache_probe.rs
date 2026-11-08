use super::buffer_adapter::ProbeBuffer;
use super::shared_object_cache::SharedObjectCache;
use super::{
    check_row, close_fixture_connection, closed_socket_error, normalize_maria_initial_rotate,
    require_fixture_tls, verify_crc,
};
use futures_util::StreamExt;
use mysql_async::binlog::events::{Event, EventData, RowsEventData};
use mysql_async::{BinlogStream, BinlogStreamRequest, Conn, OptsBuilder, Row, prelude::Queryable};
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

fn decode_event(event: &Event) -> Result<EventData<'_>> {
    if event.data().len() > 1024 * 1024 {
        return Err("cache source event size bound exceeded".into());
    }
    let verified = verify_crc(event)?;
    let data = event.read_data()?.ok_or("unsupported cache source event")?;
    if matches!(data, EventData::RowsEvent(_) | EventData::XidEvent(_)) && !verified {
        return Err("cache commit or row has no verified checksum".into());
    }
    Ok(data)
}

fn check_update(
    stream: &BinlogStream,
    rows: &RowsEventData<'_>,
    before: &[u8],
    after: &[u8],
) -> Result<()> {
    let table = stream
        .get_tme(rows.table_id())
        .ok_or("cache table map missing")?;
    if table.database_name_raw() != b"recastpress_fixture"
        || table.table_name_raw() != b"rp_cache_probe"
    {
        return Err("unexpected cache source table".into());
    }
    let mut updates = rows.rows(table);
    let Some((Some(old), Some(new))) = updates.next().transpose()? else {
        return Err("expected an update row".into());
    };
    check_row(&old, before)?;
    check_row(&new, after)?;
    if updates.next().is_some() {
        return Err("expected exactly one cache update row".into());
    }
    Ok(())
}

type ConnectionIdentity = (u32, String, String, u16);

async fn connection_identity(connection: &mut Conn) -> Result<ConnectionIdentity> {
    Ok(connection
        .query_first("SELECT CONNECTION_ID(), USER(), @@datadir, @@port")
        .await?
        .ok_or("cache monitor connection identity missing")?)
}

async fn owned_connection(
    options: OptsBuilder,
    control: &ConnectionIdentity,
) -> Result<(Conn, u32)> {
    let mut connection = Conn::new(options).await?;
    require_fixture_tls(&mut connection).await?;
    let (id, account, datadir, port) = connection_identity(&mut connection).await?;
    if id == 0
        || id == control.0
        || account != control.1
        || datadir != control.2
        || port != control.3
    {
        return Err("cache recovery connection is not a distinct owned connection".into());
    }
    Ok((connection, id))
}

async fn open_live_dump(
    source: Conn,
    replica_id: u32,
    epoch: &[u8],
    file: &[u8],
    start: u64,
    maria: bool,
) -> Result<(BinlogStream, ProbeBuffer)> {
    let mut stream = source
        .get_binlog_stream(
            BinlogStreamRequest::new(replica_id)
                .with_filename(file)
                .with_pos(start),
        )
        .await?;
    let mut anchor = ProbeBuffer::resumed(epoch, file, start, maria);
    for initial in [true, false] {
        let mut event = timeout(Duration::from_secs(3), stream.next())
            .await?
            .ok_or("live monitor ended before its preamble")??;
        if initial && maria {
            event = normalize_maria_initial_rotate(event, file, start)?;
        }
        let data = decode_event(&event)?;
        if (initial && !matches!(data, EventData::RotateEvent(_)))
            || (!initial && !matches!(data, EventData::FormatDescriptionEvent(_)))
        {
            return Err("live monitor did not open at its expected anchor".into());
        }
        anchor.accept(&event, &data)?;
    }
    anchor.finish()?;
    if anchor.checkpoint_offset() != start {
        return Err("live cache monitor anchor did not stabilize".into());
    }
    Ok((stream, anchor))
}

async fn wait_for_live_dump(control: &mut Conn, source_id: u32, user: &str) -> Result<()> {
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
    .await?
}

async fn cut_live_dump(
    control: &mut Conn,
    mut stream: BinlogStream,
    source_id: u32,
    cache: &SharedObjectCache,
) -> Result<()> {
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
    Ok(())
}

async fn replay_gap_commit(
    stream: &mut BinlogStream,
    decoder: &mut ProbeBuffer,
    start: u64,
    through: u64,
    before: &[u8],
    after: &[u8],
) -> Result<()> {
    timeout(Duration::from_secs(3), async {
        let mut row_count = 0;
        for _ in 0..32 {
            let event = stream
                .next()
                .await
                .ok_or("recovery dump ended before commit")??;
            let data = decode_event(&event)?;
            if let EventData::RowsEvent(rows) = &data {
                check_update(stream, rows, before, after)?;
                row_count += 1;
                if row_count != 1 {
                    return Err("unexpected recovery row count".into());
                }
            }
            decoder.accept_projected(&event, &data, |batch| {
                if row_count != 1
                    || batch.rows().len() != 1
                    || batch.from().offset() != start
                    || batch.through().offset() != through
                {
                    return Err("unexpected recovery commit boundary".into());
                }
                Ok(())
            })?;
            if matches!(data, EventData::XidEvent(_)) {
                if decoder.checkpoint_offset() != through || decoder.applied != 1 {
                    return Err("recovery checkpoint did not reach the planned commit".into());
                }
                return decoder.finish();
            }
        }
        Err("recovery dump did not reach commit within frame bound".into())
    })
    .await?
}

async fn live_monitor_cut_and_recover(
    control: &mut Conn,
    options: OptsBuilder,
    epoch: &[u8],
    file: &[u8],
    end: u64,
    status: &str,
    maria: bool,
) -> Result<()> {
    let control_identity = connection_identity(control).await?;
    let user = control_identity
        .1
        .split_once('@')
        .ok_or("invalid fixture account")?
        .0;
    let (source, source_id) = owned_connection(options.clone(), &control_identity).await?;
    let (stream, _) = open_live_dump(source, 7005, epoch, file, end, maria).await?;
    wait_for_live_dump(control, source_id, user).await?;
    let cache = SharedObjectCache::new(BudgetPool::new(64));
    if !cache.tracking_started() {
        return Err("cannot start live cache monitoring".into());
    }
    let old_ticket = cache.ticket().ok_or("no live monitor cache ticket")?;
    let old = value(control).await?;
    if old != b"after" || !cache.fill(&old_ticket, &old) || cache.get().as_deref() != Some(&old) {
        return Err("cannot warm cache during live monitoring".into());
    }
    cut_live_dump(control, stream, source_id, &cache).await?;
    if cache.ticket().is_some()
        || cache.get().is_some()
        || cache.fill(&old_ticket, &old)
        || cache.charged_bytes() != 0
    {
        return Err("live cut did not fence the cache".into());
    }

    // Only the owned writer may mutate the fixture. Keep the retained end from
    // the previously verified XID and use global status solely as a stream fence.
    let (mut reader, reader_id) = owned_connection(options.clone(), &control_identity).await?;
    let (mut writer, writer_id) = owned_connection(options.clone(), &control_identity).await?;
    let autocommit: Option<u8> = reader.query_first("SELECT @@SESSION.autocommit").await?;
    if reader_id == writer_id
        || [reader_id, writer_id].contains(&source_id)
        || autocommit != Some(1)
        || value(&mut reader).await? != old
        || coordinates(control, status).await? != (file.to_vec(), end)
    {
        return Err("independent recovery SQL reader or retained checkpoint differs".into());
    }
    let expected = b"after-gap";
    writer.query_drop("BEGIN").await?;
    writer
        .exec_drop(
            "UPDATE rp_cache_probe SET value=? WHERE id=1",
            (expected.as_slice(),),
        )
        .await?;
    if writer.affected_rows() != 1
        || value(&mut writer).await? != expected
        || value(&mut reader).await? != old
    {
        return Err("independent reader did not isolate the gap update before commit".into());
    }
    writer.query_drop("COMMIT").await?;
    let (committed_file, through) = coordinates(control, status).await?;
    if committed_file != file
        || through <= end
        || value(&mut reader).await? != expected
        || cache.ticket().is_some()
        || cache.get().is_some()
    {
        return Err("gap commit did not become independently visible with the cache fenced".into());
    }
    close_fixture_connection(writer, "shared_cache_gap_writer").await?;

    let (resumed, resumed_id) = owned_connection(options, &control_identity).await?;
    if [source_id, reader_id, writer_id].contains(&resumed_id) {
        return Err("replacement cache dump is not a distinct connection".into());
    }
    let (mut resumed_stream, mut decoder) =
        open_live_dump(resumed, 7006, epoch, file, end, maria).await?;
    replay_gap_commit(
        &mut resumed_stream,
        &mut decoder,
        end,
        through,
        &old,
        expected,
    )
    .await?;
    wait_for_live_dump(control, resumed_id, user).await?;
    if coordinates(control, status).await? != (file.to_vec(), through)
        || cache.get().is_some()
        || cache.ticket().is_some()
        || cache.charged_bytes() != 0
        || !cache.tracking_reconciled()
    {
        return Err("cache re-enabled before the live dump reached the commit fence".into());
    }
    let recovered = cache.ticket().ok_or("no reconciled cache ticket")?;
    if recovered == old_ticket || cache.fill(&old_ticket, &old) || cache.get().is_some() {
        return Err("reconciled generation accepted a pre-cut fill".into());
    }
    let fresh = value(&mut reader).await?;
    if fresh != expected
        || !cache.fill(&recovered, &fresh)
        || cache.get().as_deref() != Some(&fresh)
    {
        return Err("fresh SQL value was not shared after recovery".into());
    }
    wait_for_live_dump(control, resumed_id, user).await?;
    cut_live_dump(control, resumed_stream, resumed_id, &cache).await?;
    if cache.ticket().is_some()
        || cache.get().is_some()
        || cache.fill(&recovered, &fresh)
        || cache.charged_bytes() != 0
    {
        return Err("replacement dump cut did not release the reconciled generation".into());
    }
    close_fixture_connection(reader, "shared_cache_recovery_reader").await?;
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
            if events > 128 {
                return Err("cache source event count bound exceeded".into());
            }
            let data = decode_event(&event)?;
            if let EventData::RowsEvent(rows) = &data {
                check_update(&stream, rows, &old_value, b"after")?;
                target_rows += 1;
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
    live_monitor_cut_and_recover(control, options, epoch, &file, end, status, maria).await?;
    Ok(serde_json::json!({
        "format_version": 3,
        "backend": if maria { "mariadb" } else { "mysql" },
        "commits": buffer.applied,
        "target_rows": target_rows,
        "rollback_preserved": true,
        "precommit_hit_preserved": true,
        "stale_fill_rejected": true,
        "refreshed_hit": true,
        "source_end_closed_cache": true,
        "live_cut_closed_cache": true,
        "gap_commit_replayed": true,
        "recovery_fresh_hit": true,
        "recovery_old_ticket_rejected": true,
        "final_charged_bytes": cache.charged_bytes(),
    }))
}
