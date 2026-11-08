#![forbid(unsafe_code)]

use anyhow::{Context, Result, ensure};
use mysql_async::{Conn, Error, OptsBuilder, prelude::Queryable};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{io::Read, time::Duration};

const MARKER: &str = "`rp_options_guard_fixture`.`identity`";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    port: u16,
    actor_password: String,
    monitor_password: String,
    binding: String,
    wrong_binding: String,
    nonce: String,
}

#[derive(Debug, Eq, PartialEq)]
struct Instance {
    version: String,
    datadir: String,
    port: u16,
}

struct Peer {
    id: u32,
    account: String,
    instance: Instance,
    binding: Vec<u8>,
}

struct CachePermission;

fn permit(monitor: &Peer, actor: &Peer, expected: &[u8]) -> Option<CachePermission> {
    (monitor.id != actor.id
        && monitor.account == "rp_dependency_monitor@%"
        && actor.account == "rp_wpdb_probe@127.0.0.1"
        && monitor.instance == actor.instance
        && monitor.binding == expected
        && actor.binding == expected)
        .then_some(CachePermission)
}

async fn peer(connection: &mut Conn, marker_id: u8) -> Result<Peer> {
    let query = format!(
        "SELECT CONNECTION_ID(),CURRENT_USER(),VERSION(),@@datadir,@@port,binding FROM {MARKER} WHERE id={marker_id}"
    );
    let row: (u32, String, String, String, u16, Vec<u8>) = connection
        .query_first(query)
        .await
        .context("identity_peer_query")?
        .context("identity_marker_missing")?;
    let (id, account, version, datadir, port, binding) = row;
    ensure!(
        id != 0 && id == connection.id() && !datadir.is_empty() && port != 0,
        "identity_connection_shape"
    );
    Ok(Peer {
        id,
        account,
        instance: Instance {
            version,
            datadir,
            port,
        },
        binding,
    })
}

async fn trial(input: Input) -> Result<Value> {
    let options = |user, password: &str, database| {
        OptsBuilder::default()
            .ip_or_hostname("127.0.0.1")
            .prefer_socket(false)
            .tcp_port(input.port)
            .user(Some(user))
            .pass(Some(password))
            .db_name(Some(database))
            .local_files(false)
            .multi_statements(false)
    };
    let mut monitor = Conn::new(options(
        "rp_dependency_monitor",
        &input.monitor_password,
        "rp_options_guard_fixture",
    ))
    .await
    .context("identity_monitor_connect")?;
    let mut actor = Conn::new(options(
        "rp_wpdb_probe",
        &input.actor_password,
        "rp_wpdb_probe",
    ))
    .await
    .context("identity_actor_connect")?;
    let observed_monitor = peer(&mut monitor, 1).await?;
    let observed_actor = peer(&mut actor, 1).await?;
    ensure!(
        observed_monitor.instance.port == input.port,
        "identity_bound_loopback_port"
    );
    let sql = format!("SELECT nonce FROM {MARKER} WHERE id=1");
    let nonce: Option<Vec<u8>> = monitor
        .query_first(&sql)
        .await
        .context("identity_monitor_nonce")?;
    ensure!(
        nonce.as_deref() == Some(input.nonce.as_bytes()),
        "identity_monitor_nonce_mismatch"
    );
    ensure!(
        permit(&observed_monitor, &observed_actor, input.binding.as_bytes()).is_some(),
        "identity_match_refused"
    );
    // A second row supplies a real SQL result with a deliberately different
    // binding. Keep the trusted monitor observation and expected value fixed.
    let mismatched_actor = peer(&mut actor, 2).await?;
    ensure!(
        mismatched_actor.id == observed_actor.id
            && mismatched_actor.binding == input.wrong_binding.as_bytes()
            && permit(
                &observed_monitor,
                &mismatched_actor,
                input.binding.as_bytes()
            )
            .is_none(),
        "identity_mismatch_admitted"
    );
    let denied = actor.query_first::<Vec<u8>, _>(&sql).await;
    ensure!(
        matches!(denied, Err(Error::Server(error)) if matches!(error.code, 1142 | 1143)),
        "identity_actor_nonce_not_denied"
    );
    actor
        .disconnect()
        .await
        .context("identity_actor_disconnect")?;
    monitor
        .disconnect()
        .await
        .context("identity_monitor_disconnect")?;
    Ok(
        json!({"passed":true,"physical_connections":2,"distinct_connection_ids":true,
        "matching_cache_permit":true,"mismatch_cache_permit":false,"actor_nonce_read_denied":true,
        "actor_and_monitor_disconnected":true}),
    )
}

fn run() -> Result<Value> {
    let mut wire = Vec::new();
    std::io::stdin().take(1025).read_to_end(&mut wire)?;
    ensure!(wire.len() <= 1024, "identity_input_bound");
    let input: Input = serde_json::from_slice(&wire).context("identity_input_shape")?;
    let tokens = [
        &input.actor_password,
        &input.monitor_password,
        &input.binding,
        &input.wrong_binding,
        &input.nonce,
    ];
    ensure!(
        input.port != 0
            && tokens.iter().all(|token| token.len() == 48
                && token
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)))
            && tokens
                .iter()
                .enumerate()
                .all(|(index, token)| !tokens[..index].contains(token)),
        "identity_input_values"
    );
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async {
        tokio::time::timeout(Duration::from_secs(12), trial(input))
            .await
            .context("identity_deadline")?
    })
}

fn main() {
    match run() {
        Ok(report) => println!("{report}"),
        Err(error) => {
            let code = error.to_string();
            let safe = code.starts_with("identity_")
                && code.len() <= 80
                && code
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte == b'_');
            println!(
                "{}",
                json!({"passed":false,"error":if safe { &code } else { "identity_failed" }})
            );
            std::process::exit(1);
        }
    }
}
