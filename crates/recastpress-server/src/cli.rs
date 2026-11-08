//! Minimal explicit role selection; no environment credentials are read.

use std::{net::SocketAddr, path::PathBuf};

pub const USAGE: &str = "Usage: recastpress public [--listen 127.0.0.1:8080] --host HOST [--bundle FILE [--receipt FILE]]\n       recastpress management [--listen 127.0.0.1:8081]\n       recastpress check-bundle <path>\n       recastpress check-package <bundle> <receipt>\n       recastpress check-wordpress-release <directory> <manifest-sha256>\n       recastpress unpack-package <zip> <new-dir>\n       recastpress inspect-memory\n       recastpress prepare-post-package <input> <new-dir>\n       recastpress prepare-posts <input>\n       recastpress bundle-receipt <bundle>\nPublic bundles are experimental prebuilt pages. Management mutations are unavailable.";

#[derive(Debug, Eq, PartialEq)]
pub enum Role {
    Public,
    Management,
}

#[derive(Debug, Eq, PartialEq)]
pub struct Config {
    pub role: Role,
    pub listen: SocketAddr,
    pub host: Option<String>,
    pub bundle: Option<PathBuf>,
    pub receipt: Option<PathBuf>,
}

pub fn parse(args: impl IntoIterator<Item = String>) -> Result<Config, String> {
    let mut args = args.into_iter();
    let role = match args.next().as_deref() {
        Some("public") => Role::Public,
        Some("management") => Role::Management,
        _ => return Err(USAGE.into()),
    };
    let mut listen = None;
    let mut host = None;
    let mut bundle = None;
    let mut receipt = None;
    while let Some(flag) = args.next() {
        let value = args
            .next()
            .ok_or_else(|| format!("missing value for {flag}"))?;
        match flag.as_str() {
            "--listen" if listen.is_none() => {
                listen = Some(
                    value
                        .parse::<SocketAddr>()
                        .map_err(|_| "invalid listen address")?,
                )
            }
            "--host" if host.is_none() => host = Some(value),
            "--bundle" if bundle.is_none() => bundle = Some(PathBuf::from(value)),
            "--receipt" if receipt.is_none() => receipt = Some(PathBuf::from(value)),
            _ => return Err(format!("unknown or repeated argument: {flag}")),
        }
    }
    if role == Role::Management && (host.is_some() || bundle.is_some() || receipt.is_some()) {
        return Err("management does not accept public host, bundle or receipt".into());
    }
    if receipt.is_some() && bundle.is_none() {
        return Err("receipt requires --bundle".into());
    }
    if role == Role::Public && host.is_none() {
        return Err("public requires --host".into());
    }
    let default = if role == Role::Public {
        "127.0.0.1:8080"
    } else {
        "127.0.0.1:8081"
    };
    Ok(Config {
        role,
        listen: listen.unwrap_or_else(|| default.parse().expect("static listen address")),
        host,
        bundle,
        receipt,
    })
}
