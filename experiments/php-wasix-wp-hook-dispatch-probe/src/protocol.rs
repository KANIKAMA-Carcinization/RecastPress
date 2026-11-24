//! JSON line framing and PHP handle descriptors for the hook probe.

use anyhow::{Context, Result, ensure};
use recastpress_wp::continuation::{PhpHandle, PhpHandleKind, RequestGeneration};
use serde_json::{Value, json};
use std::{
    io::{BufRead, Read, Write},
    time::Instant,
};

const REQUEST: &str = "surface-probe-1";
const GENERATION: u64 = 81;
pub(super) const HANDLE_REQUEST: &str = "81";
const FRAME_LIMIT: usize = 1024 * 1024;
const TOTAL_LIMIT: usize = 32 * 1024 * 1024;

pub(super) fn read_frame(
    reader: &mut impl BufRead,
    deadline: Instant,
    total: &mut usize,
    expected_seq: u64,
) -> Result<Value> {
    ensure!(Instant::now() < deadline, "PHP-WASIX bridge deadline");
    let mut line = Vec::new();
    let count = reader
        .take((FRAME_LIMIT + 1) as u64)
        .read_until(b'\n', &mut line)?;
    ensure!(
        count > 0 && count <= FRAME_LIMIT && line.last() == Some(&b'\n'),
        "invalid or oversized bridge frame"
    );
    *total = total
        .checked_add(count)
        .context("bridge byte counter overflow")?;
    ensure!(*total <= TOTAL_LIMIT, "bridge total byte limit");
    let value: Value =
        serde_json::from_slice(&line[..line.len() - 1]).context("decode bridge JSON")?;
    ensure!(
        value.as_object().is_some_and(|o| o.len() == 5),
        "bridge frame object shape"
    );
    ensure!(
        value["v"] == 1 && value["request"] == REQUEST && value["seq"] == expected_seq,
        "bridge version/request/sequence mismatch"
    );
    Ok(value)
}

pub(super) fn send_frame(
    writer: &mut impl Write,
    seq: u64,
    kind: &str,
    payload: Value,
    total: &mut usize,
    seq_state: &mut u64,
) -> Result<()> {
    ensure!(seq == *seq_state, "outgoing sequence mismatch");
    let bytes = serde_json::to_vec(
        &json!({"v":1,"request":REQUEST,"seq":seq,"kind":kind,"payload":payload}),
    )?;
    ensure!(bytes.len() + 1 <= FRAME_LIMIT, "outgoing frame too large");
    *total = total
        .checked_add(bytes.len() + 1)
        .context("bridge byte counter overflow")?;
    ensure!(*total <= TOTAL_LIMIT, "bridge total byte limit");
    writer.write_all(&bytes)?;
    writer.write_all(b"\n")?;
    writer.flush()?;
    *seq_state += 1;
    Ok(())
}

pub(super) fn expect_frame(frame: &Value, seq: u64, kind: &str, keys: &[&str]) -> Result<()> {
    ensure!(
        frame["seq"] == seq && frame["kind"] == kind,
        "unexpected frame: received seq={:?} kind={:?}; expected seq={seq} kind={kind}",
        frame["seq"],
        frame["kind"]
    );
    let payload = frame["payload"]
        .as_object()
        .context("payload must be object")?;
    ensure!(
        payload.len() == keys.len() && keys.iter().all(|key| payload.contains_key(*key)),
        "payload fields mismatch for {kind}"
    );
    Ok(())
}

pub(super) fn handle_descriptor(kind: &str, handle: PhpHandle) -> Value {
    json!({
        "request": HANDLE_REQUEST,
        "kind": kind,
        "slot": handle.slot(),
        "generation": handle.generation(),
        "token": format!("{:016x}", handle.token()),
    })
}

pub(super) fn release_php_handle(
    reader: &mut impl BufRead,
    writer: &mut impl Write,
    deadline: Instant,
    incoming: (&mut usize, &mut u64),
    outgoing: (&mut usize, &mut u64),
    descriptor: &Value,
    mismatch: &str,
) -> Result<()> {
    let (total_out, out_seq) = outgoing;
    send_frame(
        writer,
        *out_seq,
        "release_php",
        json!({"handle": descriptor}),
        total_out,
        out_seq,
    )?;
    let (total_in, in_seq) = incoming;
    let ack = read_frame(reader, deadline, total_in, *in_seq)?;
    expect_frame(&ack, *in_seq, "release_ack", &["handle"])?;
    ensure!(&ack["payload"]["handle"] == descriptor, "{mismatch}");
    *in_seq += 1;
    Ok(())
}

pub(super) fn php_string(value: &Value) -> Result<Vec<u8>> {
    Ok(value
        .as_str()
        .context("expected PHP string bytes encoded as UTF-8")?
        .as_bytes()
        .to_vec())
}

pub(super) fn handle(desc: &Value) -> Result<PhpHandle> {
    handle_kind(desc, "callable", PhpHandleKind::Callable)
}

pub(super) fn argument_handle(desc: &Value) -> Result<PhpHandle> {
    handle_kind(desc, "argument", PhpHandleKind::Argument)
}

pub(super) fn handle_kind(
    desc: &Value,
    expected_kind: &str,
    kind: PhpHandleKind,
) -> Result<PhpHandle> {
    let fields = desc
        .as_object()
        .context("handle descriptor must be object")?;
    ensure!(
        fields.len() == 5 && fields["kind"] == expected_kind && fields["request"] == "81",
        "handle descriptor schema"
    );
    let slot = u32::try_from(fields["slot"].as_u64().context("handle slot")?)?;
    let generation = u32::try_from(fields["generation"].as_u64().context("handle generation")?)?;
    let token_text = fields["token"].as_str().context("handle token")?;
    ensure!(
        token_text.len() == 16 && token_text.bytes().all(|b| b.is_ascii_hexdigit()),
        "handle token encoding"
    );
    let token = u64::from_str_radix(token_text, 16)?;
    Ok(PhpHandle::new(
        RequestGeneration::new(GENERATION),
        kind,
        slot,
        generation,
        token,
    ))
}

#[cfg(test)]
mod tests {
    use super::{GENERATION, REQUEST, handle_descriptor, release_php_handle};
    use recastpress_wp::continuation::{PhpHandle, PhpHandleKind, RequestGeneration};
    use serde_json::{Value, json};
    use std::{
        io::Cursor,
        time::{Duration, Instant},
    };

    fn descriptor() -> Value {
        let handle = PhpHandle::new(
            RequestGeneration::new(GENERATION),
            PhpHandleKind::Callable,
            4,
            2,
            0xab,
        );
        handle_descriptor("callable", handle)
    }

    fn ack(descriptor: &Value) -> Value {
        json!({
            "v": 1, "request": REQUEST, "seq": 7, "kind": "release_ack",
            "payload": {"handle": descriptor},
        })
    }

    fn exchange(reply: &Value, descriptor: &Value) -> (anyhow::Result<()>, u64) {
        let mut wire = serde_json::to_vec(reply).unwrap();
        wire.push(b'\n');
        let mut reader = Cursor::new(&wire);
        let mut writer = Vec::new();
        let (mut total_in, mut in_seq) = (0, 7);
        let (mut total_out, mut out_seq) = (0, 11);
        let result = release_php_handle(
            &mut reader,
            &mut writer,
            Instant::now() + Duration::from_secs(5),
            (&mut total_in, &mut in_seq),
            (&mut total_out, &mut out_seq),
            descriptor,
            "release ACK identity mismatch",
        );
        assert_eq!(total_in, wire.len());
        assert_eq!(total_out, writer.len());
        assert_eq!(out_seq, 12);
        assert_eq!(writer.last(), Some(&b'\n'));
        assert_eq!(
            serde_json::from_slice::<Value>(&writer).unwrap(),
            json!({
                "v": 1, "request": REQUEST, "seq": 11, "kind": "release_php",
                "payload": {"handle": descriptor},
            })
        );
        (result, in_seq)
    }

    #[test]
    fn matching_release_ack_advances_incoming_sequence() {
        let descriptor = descriptor();
        let (result, in_seq) = exchange(&ack(&descriptor), &descriptor);
        result.unwrap();
        assert_eq!(in_seq, 8);
    }

    #[test]
    fn invalid_release_acks_leave_incoming_sequence_pending() {
        let descriptor = descriptor();
        for (path, replacement, expected_error) in [
            ("/seq", json!(8), "bridge version/request/sequence mismatch"),
            ("/kind", json!("resume_value"), "unexpected frame"),
            (
                "/payload",
                json!({}),
                "payload fields mismatch for release_ack",
            ),
            (
                "/payload",
                json!({"handle": descriptor, "unexpected": true}),
                "payload fields mismatch for release_ack",
            ),
            (
                "/payload/handle/token",
                json!("00000000000000ac"),
                "release ACK identity mismatch",
            ),
        ] {
            let mut reply = ack(&descriptor);
            *reply.pointer_mut(path).unwrap() = replacement;
            let (result, in_seq) = exchange(&reply, &descriptor);
            let error = result.unwrap_err().to_string();
            assert!(error.contains(expected_error), "{path}: {error}");
            assert_eq!(in_seq, 7, "{path}");
        }
    }
}
