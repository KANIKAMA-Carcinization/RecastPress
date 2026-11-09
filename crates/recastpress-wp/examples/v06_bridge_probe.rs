//! Thin native CLI for the reusable v0.6 JSONL continuation peer.

#[path = "v06_bridge_peer/mod.rs"]
mod peer;

use std::io;

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let scenario = std::env::args().nth(1).unwrap_or_else(|| "surface".into());
    if let Err(error) = peer::run_peer(stdin.lock(), stdout.lock(), &scenario) {
        eprintln!("v06_bridge_probe: {error}");
        std::process::exit(2);
    }
}
