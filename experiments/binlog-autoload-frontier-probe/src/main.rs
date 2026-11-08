#![forbid(unsafe_code)]
#[allow(dead_code)]
mod monitor;

use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};
use std::io::Read;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    port: u16,
    password: String,
    nonce: String,
    writer_password: String,
}

fn run() -> Result<Value> {
    let mut wire = Vec::new();
    std::io::stdin().take(513).read_to_end(&mut wire)?;
    ensure!(wire.len() <= 512, "frontier_input_bound");
    let input: Input = serde_json::from_slice(&wire).context("frontier_input")?;
    ensure!(input.writer_password.len() == 48, "frontier_writer_input");
    monitor::Monitor::start(input.port, &input.password, &input.nonce)?
        .run_live(input.port, &input.writer_password)
}

fn main() {
    match run() {
        Ok(report) => println!("{report}"),
        Err(error) => {
            let code = error.to_string();
            let safe =
                code.len() <= 100 && code.bytes().all(|b| b.is_ascii_lowercase() || b == b'_');
            println!(
                "{}",
                json!({"passed":false,"error":if safe { &code } else { "frontier_failed" }})
            );
            std::process::exit(1);
        }
    }
}
