use mysql_async::binlog::events::Event;
use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
};
use tokio::sync::oneshot;

const SQL: &str = "SELECT option_name, option_value FROM rp_options WHERE autoload IN ( 'yes', 'on', 'auto-on', 'auto' )";
type Rows = Vec<(Vec<u8>, Vec<u8>)>;

#[derive(Default)]
struct Applied {
    coordinate: Option<Coordinate>,
    entry: Option<Rows>,
}

#[derive(Default)]
struct Counts {
    frontier_queries: usize,
    uncached_selects: usize,
}

async fn stock_query(connection: &mut Conn) -> Result<Rows> {
    let rows: Rows = connection.query(SQL).await.context("frontier_stock_sql")?;
    ensure!(
        rows.len() == 2
            && rows
                .iter()
                .all(|(key, value)| key.len() <= 64 && value.len() <= 64),
        "frontier_fixture_rows"
    );
    Ok(rows)
}

fn marker_is(rows: &Rows, value: &[u8]) -> bool {
    rows.iter()
        .any(|(key, actual)| key == b"rp_autoload_probe_marker" && actual == value)
}

async fn request(
    connection: &mut Conn,
    source: &Source,
    applied: &Mutex<Applied>,
    counts: &mut Counts,
) -> Result<(Coordinate, &'static str, Rows)> {
    // This is the only frontier SQL of the request. Same source is established
    // at startup and by keeping this exact non-pooled connection.
    counts.frontier_queries += 1;
    let observed = coordinate(connection, source).await?;
    let path = {
        let state = applied.lock().map_err(|_| anyhow!("frontier_lock"))?;
        match &state.coordinate {
            Some(seen) if *seen == observed => {
                if let Some(rows) = &state.entry {
                    return Ok((observed, "hit", rows.clone()));
                }
                "uncached"
            }
            Some(_) => "mismatched",
            None => "unavailable",
        }
    };
    counts.uncached_selects += 1;
    Ok((observed, path, stock_query(connection).await?))
}

async fn blocking_preamble(
    source: &Source,
    stream: &mut BinlogStream,
    checkpoint: &Coordinate,
    nonce: &[u8],
) -> Result<VecDeque<Event>> {
    let file = checkpoint.file.as_bytes();
    let start = checkpoint.position;
    let mut decoder = ProbeBuffer::resumed(nonce, file, start, source.maria());
    let mut frames = VecDeque::with_capacity(2);
    // Same blocking Rotate/FDE handshake as binlog-shared-cache-probe.
    for initial in [true, false] {
        let mut event = stream.next().await.context("frontier_missing_preamble")??;
        if initial && source.maria() {
            event = wire::normalize_maria_initial_rotate(event, file, start)
                .map_err(|_| anyhow!("frontier_initial_rotate"))?;
        }
        wire::verify_crc(&event).map_err(|_| anyhow!("frontier_initial_crc"))?;
        let data = event.read_data()?.context("frontier_preamble_kind")?;
        ensure!(
            if initial {
                matches!(data, EventData::RotateEvent(_))
            } else {
                matches!(data, EventData::FormatDescriptionEvent(_))
            },
            "frontier_preamble_order"
        );
        decoder
            .accept(&event, &data)
            .map_err(|_| anyhow!("frontier_preamble_boundary"))?;
        frames.push_back(event);
    }
    decoder
        .finish()
        .map_err(|_| anyhow!("frontier_preamble_incomplete"))?;
    ensure!(
        decoder.checkpoint_offset() == start,
        "frontier_preamble_advanced"
    );
    Ok(frames)
}

impl Monitor {
    pub(crate) fn run_live(mut self, port: u16, writer_password: &str) -> Result<Value> {
        let report = self.runtime.block_on(async {
            timeout(Duration::from_secs(20), async {
                let initial = self.checkpoint.clone();
                let mut dump = connect(self.options.clone()).await?;
                let identity = source(&mut dump, &self.nonce).await?;
                let dump_id = dump.id();
                ensure!(identity == self.source && dump_id != 0 && dump_id != self.control.id()
                    && coordinate(&mut dump, &identity).await? == initial, "frontier_dump_identity");
                let mut stream = dump.get_binlog_stream(BinlogStreamRequest::new(7001)
                    .with_filename(initial.file.as_bytes()).with_pos(initial.position)).await?;
                let preamble = blocking_preamble(&identity, &mut stream, &initial, self.nonce.as_bytes()).await?;
                let old_rows = stock_query(&mut self.control).await?;
                ensure!(marker_is(&old_rows, OLD), "frontier_old_value");
                let applied = Arc::new(Mutex::new(Applied { coordinate: Some(initial.clone()), entry: Some(old_rows) }));
                let state = applied.clone();
                let nonce = self.nonce.clone();
                let option_id = self.option_id;
                let start = initial.position;
                let (permit, wait) = oneshot::channel::<Coordinate>();
                let (ready, reached) = oneshot::channel();
                let watcher = tokio::spawn(async move {
                    let result = async {
                        // The live stream is already open. The controller pauses
                        // application to create a deterministic mismatch window.
                        let through = wait.await.context("frontier_permit_closed")?;
                        let counts = scan_live(&mut stream, &identity, nonce.as_bytes(), start, &through, option_id, preamble).await?;
                        {
                            let mut state = state.lock().map_err(|_| anyhow!("frontier_lock"))?;
                            state.entry = None;
                            state.coordinate = Some(through);
                        }
                        ready.send(counts).map_err(|_| anyhow!("frontier_ready_closed"))?;
                        match stream.next().await {
                            None | Some(Err(mysql_async::Error::Driver(mysql_async::DriverError::ConnectionClosed))) => Ok(()),
                            Some(Err(mysql_async::Error::Server(error))) if error.code == 1927 => Ok(()),
                            Some(Err(mysql_async::Error::Io(mysql_async::IoError::Io(error))))
                                if matches!(error.kind(), std::io::ErrorKind::ConnectionReset | std::io::ErrorKind::BrokenPipe | std::io::ErrorKind::UnexpectedEof) => Ok(()),
                            _ => Err(anyhow!("frontier_unexpected_next_event")),
                        }
                    }.await;
                    *state.lock().map_err(|_| anyhow!("frontier_lock"))? = Applied::default();
                    result
                });

                let mut counts = Counts::default();
                let (seen, first, rows) = request(&mut self.control, &self.source, &applied, &mut counts).await?;
                ensure!(seen == initial && first == "hit" && marker_is(&rows, OLD), "frontier_initial_hit");
                let mut writer = Conn::new(OptsBuilder::default().ip_or_hostname("127.0.0.1").tcp_port(port)
                    .prefer_socket(false).db_name(Some(SCHEMA)).user(Some("rp_dependency_writer"))
                    .pass(Some(writer_password))).await?;
                ensure!(writer.id() != self.control.id(), "frontier_distinct_writer");
                writer.query_drop("START TRANSACTION").await?;
                writer.exec_drop("UPDATE rp_options SET option_value=? WHERE option_id=? AND option_name='rp_autoload_probe_marker' AND option_value=?",
                    (NEW, option_id, OLD)).await?;
                ensure!(writer.affected_rows() == 1, "frontier_writer_rows");
                writer.query_drop("COMMIT").await?;
                writer.disconnect().await?;

                let (through, lag, rows) = request(&mut self.control, &self.source, &applied, &mut counts).await?;
                ensure!(through.file == initial.file && through.position > start
                    && through.position - start <= MAX_SPAN && lag == "mismatched"
                    && marker_is(&rows, NEW), "frontier_lag_bypass");
                permit.send(through.clone()).map_err(|_| anyhow!("frontier_permit_closed"))?;
                let (events, checksums) = reached.await.context("frontier_watcher_ended")?;
                {
                    // Fixture-only refill from the independently read, quiescent
                    // new value. It is not part of the per-request frontier API.
                    let mut state = applied.lock().map_err(|_| anyhow!("frontier_lock"))?;
                    ensure!(state.coordinate.as_ref() == Some(&through) && state.entry.is_none(), "frontier_commit_not_applied");
                    state.entry = Some(rows);
                }
                let (seen, next, rows) = request(&mut self.control, &self.source, &applied, &mut counts).await?;
                ensure!(seen == through && next == "hit" && marker_is(&rows, NEW), "frontier_new_hit");
                // The control and dump were verified as the same dedicated
                // account on the newly owned instance. Kill only that dump ID.
                self.control.query_drop(format!("KILL CONNECTION {dump_id}")).await?;
                watcher.await.context("frontier_join")??;
                let (seen, stopped, rows) = request(&mut self.control, &self.source, &applied, &mut counts).await?;
                ensure!(seen == through && stopped == "unavailable" && marker_is(&rows, NEW), "frontier_unavailable_bypass");
                Ok(json!({"passed":true,"backend":if self.source.maria(){"mariadb"}else{"mysql"},
                    "source_file":initial.file,"from_position":start,"through_position":through.position,
                    "events":events,"checksums":checksums,"commits":1,"frontier_queries":counts.frontier_queries,
                    "paths":[first,lag,next,stopped],"uncached_selects":counts.uncached_selects,
                    "fixture_refill_reused_quiescent_fallback":true,"watcher_closed":true}))
            }).await.context("frontier_trial_timeout")?
        })?;
        self.finish()?;
        Ok(report)
    }
}
