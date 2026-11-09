use recastpress_server::{
    bundle,
    cli::{self, Role},
    management_router, public_router,
};
use std::io::{Read, Write};

mod shutdown;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let os_args: Vec<_> = std::env::args_os().skip(1).collect();
    if os_args
        .first()
        .is_some_and(|arg| arg == "preview-wordpress")
    {
        launch_wordpress_preview(&os_args);
        let _ = std::io::stderr()
            .lock()
            .write_all(b"WordPress preview launch failed\n");
        std::process::exit(2);
    }
    if os_args
        .first()
        .is_some_and(|arg| arg == "check-wordpress-release")
    {
        let result = check_wordpress_release_command(&os_args).and_then(|wire| {
            let mut stdout = std::io::stdout().lock();
            stdout
                .write_all(&wire)
                .and_then(|()| stdout.flush())
                .map_err(|_| ())
        });
        if result.is_err() {
            let _ = std::io::stderr()
                .lock()
                .write_all(b"WordPress release check failed\n");
            std::process::exit(2);
        }
        return Ok(());
    }
    if os_args
        .first()
        .is_some_and(|argument| argument == "unpack-package")
    {
        if unpack_package_command(&os_args)
            .and_then(|()| write_unpack_success(std::io::stdout().lock()))
            .is_err()
        {
            // Completed output files remain if stdout delivery fails. Bytes
            // already delivered cannot be retracted; stderr may itself fail.
            let _ = std::io::stderr()
                .lock()
                .write_all(b"package unpack failed\n");
            std::process::exit(2);
        }
        return Ok(());
    }
    if os_args
        .first()
        .is_some_and(|argument| argument == "check-package")
    {
        if check_package_command(&os_args)
            .and_then(|wire| write_check_package_report(std::io::stdout().lock(), &wire))
            .is_err()
        {
            // Closed one-shot failure, including stdout delivery errors. Bytes
            // already written cannot be retracted; stderr may itself fail.
            let _ = std::io::stderr()
                .lock()
                .write_all(b"package check failed\n");
            std::process::exit(2);
        }
        return Ok(());
    }
    if os_args
        .first()
        .is_some_and(|argument| argument == "prepare-post-package")
    {
        match prepare_post_package_command(&os_args).and_then(|()| {
            std::io::stdout()
                .lock()
                .write_all(b"post package prepared\n")
                .map_err(|_| ())
        }) {
            Ok(()) => {}
            Err(()) => {
                eprintln!("post package preparation failed");
                std::process::exit(2);
            }
        }
        return Ok(());
    }
    if os_args
        .first()
        .is_some_and(|argument| argument == "prepare-posts")
    {
        match prepare_posts_command(&os_args) {
            Ok(wire) => std::io::stdout().lock().write_all(&wire)?,
            Err(()) => {
                eprintln!("post preparation failed");
                std::process::exit(2);
            }
        }
        return Ok(());
    }
    if os_args
        .first()
        .is_some_and(|argument| argument == "bundle-receipt")
    {
        match bundle_receipt_command(&os_args) {
            Ok(wire) => std::io::stdout().lock().write_all(&wire)?,
            Err(()) => {
                eprintln!("bundle receipt failed");
                std::process::exit(2);
            }
        }
        return Ok(());
    }
    if os_args
        .first()
        .is_some_and(|argument| argument == "check-bundle")
    {
        match check_bundle_command(&os_args) {
            Ok(report) => println!("{report}"),
            Err(()) => {
                // JSON/parser/OS errors may contain user data. This one-shot
                // command exposes only a fixed diagnostic; serve errors below
                // retain their existing behavior.
                eprintln!("bundle check failed");
                std::process::exit(2);
            }
        }
        return Ok(());
    }
    if os_args
        .first()
        .is_some_and(|argument| argument == "inspect-memory")
    {
        match inspect_memory_command(&os_args) {
            Ok((wire, partial)) => {
                println!("{wire}");
                if partial {
                    std::process::exit(3);
                }
                return Ok(());
            }
            Err(()) => {
                eprintln!("memory inspection failed");
                std::process::exit(2);
            }
        }
    }
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args == ["--help"] || args == ["-h"] {
        println!("{}", cli::USAGE);
        return Ok(());
    }
    serve(args)
}

// Construct the existing runtime only for the long-running server path.
#[tokio::main]
async fn serve(args: Vec<String>) -> Result<(), Box<dyn std::error::Error>> {
    let config = cli::parse(args).map_err(std::io::Error::other)?;
    let router = match config.role {
        Role::Public => {
            let site = match (config.bundle.as_deref(), config.receipt.as_deref()) {
                (Some(payload), Some(receipt)) => Some(
                    recastpress_server::bundle_receipt::load_verified(payload, receipt)
                        .map_err(std::io::Error::other)?,
                ),
                (Some(payload), None) => {
                    Some(bundle::load(payload).map_err(std::io::Error::other)?)
                }
                (None, None) => None,
                (None, Some(_)) => {
                    return Err(std::io::Error::other("receipt requires bundle").into());
                }
            };
            public_router(site, config.host.as_deref().expect("required by parser"))
                .map_err(std::io::Error::other)?
        }
        Role::Management => management_router(),
    };
    let shutdown = shutdown::install()?;
    let listener = tokio::net::TcpListener::bind(config.listen).await?;
    eprintln!(
        "RecastPress {} {:?} listening on {} (experimental)",
        env!("CARGO_PKG_VERSION"),
        config.role,
        listener.local_addr()?
    );
    axum::serve(listener, router)
        .with_graceful_shutdown(shutdown)
        .await?;
    Ok(())
}

fn one_path<'a>(args: &'a [std::ffi::OsString], expected: &str) -> Result<&'a std::path::Path, ()> {
    let [command, path] = args else {
        return Err(());
    };
    if command != expected || path.is_empty() {
        return Err(());
    }
    Ok(std::path::Path::new(path))
}

fn check_wordpress_release_command(args: &[std::ffi::OsString]) -> Result<Vec<u8>, ()> {
    let [command, directory, sha256] = args else {
        return Err(());
    };
    if command != "check-wordpress-release" || directory.is_empty() {
        return Err(());
    }
    let verified = recastpress_server::wordpress_release::verify(
        std::path::Path::new(directory),
        sha256.to_str().ok_or(())?,
    )
    .map_err(|_| ())?;
    let mut wire = serde_json::to_vec(&verified).map_err(|_| ())?;
    wire.push(b'\n');
    Ok(wire)
}

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
fn launch_wordpress_preview(args: &[std::ffi::OsString]) {
    use std::{os::unix::process::CommandExt, path::Path, process::Command};

    let [_, directory, digest, capture] = args else {
        return;
    };
    let Some(digest) = digest.to_str() else {
        return;
    };
    let directory = Path::new(directory);
    let capture = Path::new(capture);
    if !capture
        .symlink_metadata()
        .is_ok_and(|metadata| metadata.is_dir())
        || recastpress_server::wordpress_release::verify(directory, digest).is_err()
    {
        return;
    }
    let (Ok(root), Ok(capture)) = (directory.canonicalize(), capture.canonicalize()) else {
        return;
    };
    // Replace this process so the parent keeps ownership of the same PID and
    // private stdin while the HTTP adapter supervises the worker.
    let _ = Command::new(root.join("bin/http"))
        .args([
            root.as_os_str(),
            std::ffi::OsStr::new(digest),
            capture.as_os_str(),
        ])
        .env_clear()
        .exec();
}

#[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
fn launch_wordpress_preview(_args: &[std::ffi::OsString]) {}

fn unpack_package_paths(
    args: &[std::ffi::OsString],
) -> Result<(&std::path::Path, &std::path::Path), ()> {
    let [command, input, output] = args else {
        return Err(());
    };
    if command != "unpack-package" || input.is_empty() || output.is_empty() {
        return Err(());
    }
    Ok((std::path::Path::new(input), std::path::Path::new(output)))
}

fn unpack_package_command(args: &[std::ffi::OsString]) -> Result<(), ()> {
    let (input, output) = unpack_package_paths(args)?;
    recastpress_server::package_zip::unpack_package(input, output).map_err(|_| ())
}

fn write_unpack_success(mut output: impl Write) -> Result<(), ()> {
    output
        .write_all(b"package unpacked\n")
        .and_then(|()| output.flush())
        .map_err(|_| ())
}

fn check_package_paths(
    args: &[std::ffi::OsString],
) -> Result<(&std::path::Path, &std::path::Path), ()> {
    let [command, payload, receipt] = args else {
        return Err(());
    };
    if command != "check-package" || payload.is_empty() || receipt.is_empty() {
        return Err(());
    }
    Ok((std::path::Path::new(payload), std::path::Path::new(receipt)))
}

fn check_package_command(args: &[std::ffi::OsString]) -> Result<Vec<u8>, ()> {
    let (payload, receipt) = check_package_paths(args)?;
    let report =
        recastpress_server::bundle_receipt::check_verified(payload, receipt).map_err(|_| ())?;
    let mut wire = serde_json::to_vec(&report).map_err(|_| ())?;
    if wire.len().checked_add(1).is_none_or(|length| length > 1024) {
        return Err(());
    }
    wire.push(b'\n');
    Ok(wire)
}

fn write_check_package_report(mut output: impl Write, wire: &[u8]) -> Result<(), ()> {
    output
        .write_all(wire)
        .and_then(|()| output.flush())
        .map_err(|_| ())
}

fn bundle_receipt_command(args: &[std::ffi::OsString]) -> Result<Vec<u8>, ()> {
    recastpress_server::bundle_receipt::receipt_from_file(one_path(args, "bundle-receipt")?)
        .map_err(|_| ())
}

fn prepare_post_package_command(args: &[std::ffi::OsString]) -> Result<(), ()> {
    let [command, input, output] = args else {
        return Err(());
    };
    if command != "prepare-post-package" || input.is_empty() || output.is_empty() {
        return Err(());
    }
    recastpress_server::prepared_package::prepare_post_package(
        std::path::Path::new(input),
        std::path::Path::new(output),
    )
    .map_err(|_| ())
}

fn prepare_posts_command(args: &[std::ffi::OsString]) -> Result<Vec<u8>, ()> {
    use recastpress_server::prepared_posts::{MAX_POSTS_INPUT_BYTES, prepare_bundle};
    let file = std::fs::File::open(one_path(args, "prepare-posts")?).map_err(|_| ())?;
    let mut input = Vec::new();
    file.take(MAX_POSTS_INPUT_BYTES + 1)
        .read_to_end(&mut input)
        .map_err(|_| ())?;
    prepare_bundle(
        &input,
        bundle::MAX_BUNDLE_BYTES as usize,
        bundle::MAX_BUNDLE_BYTES as usize,
    )
    .map_err(|_| ())
}

fn check_bundle_path(args: &[std::ffi::OsString]) -> Result<&std::path::Path, ()> {
    let [command, path] = args else {
        return Err(());
    };
    if command != "check-bundle" || path.is_empty() {
        return Err(());
    }
    Ok(std::path::Path::new(path))
}

fn check_bundle_command(args: &[std::ffi::OsString]) -> Result<String, ()> {
    let report = bundle::check(check_bundle_path(args)?).map_err(|_| ())?;
    let wire = serde_json::to_string(&report).map_err(|_| ())?;
    // Include the trailing LF in the development protocol's 1024-byte bound.
    // Keys and seven admitted MIME labels cannot carry raw content.
    if wire.len().checked_add(1).is_none_or(|length| length > 1024) {
        return Err(());
    }
    Ok(wire)
}

#[cfg(all(test, unix))]
mod check_bundle_path_tests {
    use super::check_bundle_path;
    use std::{
        ffi::OsString,
        os::unix::ffi::{OsStrExt, OsStringExt},
    };

    #[test]
    fn check_bundle_keeps_non_utf8_native_path_without_string_conversion() {
        let raw = b"native-path-\xff.json";
        let args = [
            OsString::from("check-bundle"),
            OsString::from_vec(raw.to_vec()),
        ];
        assert_eq!(
            check_bundle_path(&args).unwrap().as_os_str().as_bytes(),
            raw
        );
        assert!(check_bundle_path(&args[..1]).is_err());
    }
}

fn inspect_memory_command(args: &[std::ffi::OsString]) -> Result<(String, bool), ()> {
    let [command] = args else {
        return Err(());
    };
    if command != "inspect-memory" {
        return Err(());
    }
    let report = recastpress_server::memory_diagnostic::inspect_memory().map_err(|_| ())?;
    let partial = report.is_partial();
    let wire = report.to_json().map_err(|_| ())?;
    Ok((wire, partial))
}

#[cfg(test)]
mod unpack_package_tests {
    use super::{unpack_package_paths, write_unpack_success};
    use std::{
        ffi::OsString,
        io::{self, Write},
    };

    #[test]
    fn unpack_requires_exactly_two_nonempty_native_paths() {
        let args = [
            "unpack-package".into(),
            "input.zip".into(),
            "new-dir".into(),
        ];
        let (input, output) = unpack_package_paths(&args).unwrap();
        assert_eq!(input, std::path::Path::new("input.zip"));
        assert_eq!(output, std::path::Path::new("new-dir"));
        for length in 0..3 {
            assert!(unpack_package_paths(&args[..length]).is_err());
        }
        let mut extra = args.to_vec();
        extra.push("extra".into());
        assert!(unpack_package_paths(&extra).is_err());
        for index in 0..3 {
            let mut changed = args.clone();
            changed[index] = OsString::new();
            assert!(unpack_package_paths(&changed).is_err());
        }
        let mut other = args;
        other[0] = "prepare-post-package".into();
        assert!(unpack_package_paths(&other).is_err());
    }

    #[test]
    fn unpack_success_requires_complete_write_and_flush_without_io_details() {
        struct Delivery {
            bytes: Vec<u8>,
            remaining: usize,
            fail_flush: bool,
        }
        impl Write for Delivery {
            fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
                if self.remaining == 0 {
                    return Err(io::Error::other("PRIVATE_WRITE_DIAGNOSTIC"));
                }
                let count = bytes.len().min(self.remaining);
                self.bytes.extend_from_slice(&bytes[..count]);
                self.remaining -= count;
                Ok(count)
            }
            fn flush(&mut self) -> io::Result<()> {
                if self.fail_flush {
                    Err(io::Error::other("PRIVATE_FLUSH_DIAGNOSTIC"))
                } else {
                    Ok(())
                }
            }
        }
        for (remaining, fail_flush, expected) in [
            (1, false, b"p".as_slice()),
            (usize::MAX, true, b"package unpacked\n".as_slice()),
        ] {
            let mut output = Delivery {
                bytes: Vec::new(),
                remaining,
                fail_flush,
            };
            assert_eq!(write_unpack_success(&mut output), Err(()));
            assert_eq!(output.bytes, expected);
        }
        let mut output = Vec::new();
        assert_eq!(write_unpack_success(&mut output), Ok(()));
        assert_eq!(output, b"package unpacked\n");
    }

    #[cfg(unix)]
    #[test]
    fn unpack_native_paths_preserve_non_utf8_bytes() {
        use std::os::unix::ffi::{OsStrExt, OsStringExt};
        let args = [
            OsString::from("unpack-package"),
            OsString::from_vec(b"input-\xff.zip".to_vec()),
            OsString::from_vec(b"new-\xfe".to_vec()),
        ];
        let (input, output) = unpack_package_paths(&args).unwrap();
        assert_eq!(input.as_os_str().as_bytes(), b"input-\xff.zip");
        assert_eq!(output.as_os_str().as_bytes(), b"new-\xfe");
    }
}

#[cfg(test)]
mod check_package_tests {
    use super::{check_package_paths, write_check_package_report};
    use std::{
        ffi::OsString,
        io::{self, Write},
    };

    #[test]
    fn package_requires_exactly_two_nonempty_native_paths() {
        let args = [
            OsString::from("check-package"),
            OsString::from("bundle.json"),
            OsString::from("receipt.json"),
        ];
        let (payload, receipt) = check_package_paths(&args).unwrap();
        assert_eq!(payload, std::path::Path::new("bundle.json"));
        assert_eq!(receipt, std::path::Path::new("receipt.json"));
        for length in 0..3 {
            assert!(check_package_paths(&args[..length]).is_err());
        }
        let mut extra = args.to_vec();
        extra.push("unexpected".into());
        assert!(check_package_paths(&extra).is_err());
        for index in 0..3 {
            let mut changed = args.clone();
            changed[index] = OsString::new();
            assert!(check_package_paths(&changed).is_err());
        }
        let mut other_command = args;
        other_command[0] = "public".into();
        assert!(check_package_paths(&other_command).is_err());
    }

    #[test]
    fn package_report_delivery_requires_complete_write_and_flush_without_io_details() {
        struct Delivery {
            bytes: Vec<u8>,
            remaining: usize,
            fail_flush: bool,
        }
        impl Write for Delivery {
            fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
                if self.remaining == 0 {
                    return Err(io::Error::other("PRIVATE_WRITE_DIAGNOSTIC"));
                }
                let count = bytes.len().min(self.remaining);
                self.bytes.extend_from_slice(&bytes[..count]);
                self.remaining -= count;
                Ok(count)
            }
            fn flush(&mut self) -> io::Result<()> {
                if self.fail_flush {
                    Err(io::Error::other("PRIVATE_FLUSH_DIAGNOSTIC"))
                } else {
                    Ok(())
                }
            }
        }
        for (remaining, fail_flush, expected_length) in [(1, false, 1), (usize::MAX, true, 3)] {
            let mut output = Delivery {
                bytes: Vec::new(),
                remaining,
                fail_flush,
            };
            assert_eq!(write_check_package_report(&mut output, b"{}\n"), Err(()));
            assert_eq!(output.bytes.len(), expected_length);
        }
        let mut output = Vec::new();
        assert_eq!(write_check_package_report(&mut output, b"{}\n"), Ok(()));
        assert_eq!(output, b"{}\n");
    }

    #[cfg(unix)]
    #[test]
    fn package_native_paths_keep_non_utf8_bytes_without_string_conversion() {
        use std::os::unix::ffi::{OsStrExt, OsStringExt};
        let args = [
            OsString::from("check-package"),
            OsString::from_vec(b"payload-\xff".to_vec()),
            OsString::from_vec(b"receipt-\xfe".to_vec()),
        ];
        let (payload, receipt) = check_package_paths(&args).unwrap();
        assert_eq!(payload.as_os_str().as_bytes(), b"payload-\xff");
        assert_eq!(receipt.as_os_str().as_bytes(), b"receipt-\xfe");
    }
}
