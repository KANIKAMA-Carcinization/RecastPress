//! Decode a private, complete WordPress response envelope without an oracle.
//!
//! Structural acceptance does not establish PHP execution, successful EOF,
//! process reaping, request cleanup, or permission to publish the response.
//! Callers must establish those facts separately. Borrowed bytes are private;
//! only [`SafeSummary`] is intended for routine public diagnostics.
//! No input, parser error text, original header or body is formatted by this API.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fmt;

#[path = "../../php-wasi-host-probe/src/cgi_response.rs"]
mod cgi_response;

pub const BODY_LIMIT: usize = 1024 * 1024;
pub const HEADER_LIMIT: usize = cgi_response::MAX_HEADER_BYTES;
/// One of the parser's 64 fields is reserved for the synthetic Status field.
pub const HEADER_COUNT_LIMIT: usize = cgi_response::MAX_HEADER_FIELDS - 1;
/// Bound the JSON envelope before deserialization, including hex expansion.
pub const WIRE_LIMIT: usize = 2 * BODY_LIMIT + 2 * HEADER_LIMIT + 4096;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ResponseError {
    WireLimit,
    JsonShape,
    Status,
    BodyLimit,
    Hex,
    BodyLength,
    BodyDigest,
    HeaderBudget,
    HeaderFraming,
    OriginStatus,
    CgiFrame,
}

impl ResponseError {
    pub fn code(self) -> &'static str {
        match self {
            Self::WireLimit => "response_wire_limit",
            Self::JsonShape => "response_json_shape",
            Self::Status => "response_status",
            Self::BodyLimit => "response_body_limit",
            Self::Hex => "response_hex",
            Self::BodyLength => "response_body_length",
            Self::BodyDigest => "response_body_digest",
            Self::HeaderBudget => "response_header_budget",
            Self::HeaderFraming => "response_header_framing",
            Self::OriginStatus => "response_origin_status",
            Self::CgiFrame => "response_cgi_frame",
        }
    }
}

impl fmt::Debug for ResponseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl fmt::Display for ResponseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for ResponseError {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    status: u16,
    headers_hex: Vec<String>,
    body_bytes: u64,
    body_hex: String,
    body_sha256: String,
}

/// An original normalized field retained privately, including duplicate values.
pub struct PrivateHeader {
    name: String,
    value: Vec<u8>,
}
impl PrivateHeader {
    pub fn name(&self) -> &str {
        &self.name
    }
    pub fn value(&self) -> &[u8] {
        &self.value
    }
}

/// Accepted structural response; intentionally has no Debug/Display/Serialize.
pub struct PrivateWpResponse {
    status: u16,
    raw_headers: Vec<Vec<u8>>,
    headers: Vec<PrivateHeader>,
    body: Vec<u8>,
    body_sha256: String,
    representation_length: Option<u64>,
}

/// Public metrics only. A digest is computed from actual decoded body bytes.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct SafeSummary {
    pub status: u16,
    /// Normalized origin fields; CGI Status and Content-Length are excluded.
    pub header_count: usize,
    pub body_bytes: usize,
    pub body_sha256: String,
    pub representation_length: Option<u64>,
}

impl PrivateWpResponse {
    pub fn status(&self) -> u16 {
        self.status
    }
    /// Original decoded lines, preserving field order, case, OWS and values.
    pub fn raw_headers(&self) -> &[Vec<u8>] {
        &self.raw_headers
    }
    /// Parser-normalized lowercase names and trimmed values, in original order.
    pub fn headers(&self) -> &[PrivateHeader] {
        &self.headers
    }
    pub fn body(&self) -> &[u8] {
        &self.body
    }
    /// Unverified selected-representation length from a 304 Content-Length.
    /// Validate it against the selected representation before HTTP emission.
    pub fn representation_length(&self) -> Option<u64> {
        self.representation_length
    }
    pub fn summary(&self) -> SafeSummary {
        SafeSummary {
            status: self.status,
            header_count: self.headers.len(),
            body_bytes: self.body.len(),
            body_sha256: self.body_sha256.clone(),
            representation_length: self.representation_length,
        }
    }
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        result.push(DIGITS[(byte >> 4) as usize] as char);
        result.push(DIGITS[(byte & 15) as usize] as char);
    }
    result
}

fn unhex(value: &str) -> Result<Vec<u8>, ResponseError> {
    if value.len() % 2 != 0 {
        return Err(ResponseError::Hex);
    }
    let nibble = |byte| match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        _ => Err(ResponseError::Hex),
    };
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| Ok(nibble(pair[0])? * 16 + nibble(pair[1])?))
        .collect()
}

/// Strictly decode the five-field JSON response. Unknown private content is
/// allowed; this function deliberately has no native fixture or route oracle.
pub fn decode(wire: &[u8]) -> Result<PrivateWpResponse, ResponseError> {
    if wire.len() > WIRE_LIMIT {
        return Err(ResponseError::WireLimit);
    }
    let envelope: Envelope = serde_json::from_slice(wire).map_err(|_| ResponseError::JsonShape)?;
    if !(200..=599).contains(&envelope.status) {
        return Err(ResponseError::Status);
    }
    if envelope.body_hex.len() > BODY_LIMIT * 2 {
        return Err(ResponseError::BodyLimit);
    }
    let body = unhex(&envelope.body_hex)?;
    if envelope.body_bytes != body.len() as u64 {
        return Err(ResponseError::BodyLength);
    }
    let digest = hex(&Sha256::digest(&body));
    if envelope.body_sha256 != digest {
        return Err(ResponseError::BodyDigest);
    }
    if envelope.headers_hex.len() > HEADER_COUNT_LIMIT {
        return Err(ResponseError::HeaderBudget);
    }
    let mut frame = format!("Status: {}\r\n", envelope.status).into_bytes();
    let mut header_bytes = frame.len() + 2; // Include the final empty CRLF line.
    let mut raw_headers = Vec::with_capacity(envelope.headers_hex.len());
    for encoded in &envelope.headers_hex {
        header_bytes = header_bytes
            .checked_add(encoded.len() / 2)
            .and_then(|n| n.checked_add(2))
            .filter(|n| *n <= HEADER_LIMIT)
            .ok_or(ResponseError::HeaderBudget)?;
        let line = unhex(encoded)?;
        if line.is_empty() || line.iter().any(|byte| *byte < 32 && *byte != 9 || *byte == 127) {
            return Err(ResponseError::HeaderFraming);
        }
        if line
            .split(|byte| *byte == b':')
            .next()
            .is_some_and(|name| name.eq_ignore_ascii_case(b"status"))
        {
            return Err(ResponseError::OriginStatus);
        }
        frame.extend_from_slice(&line);
        frame.extend_from_slice(b"\r\n");
        raw_headers.push(line);
    }
    frame.extend_from_slice(b"\r\n");
    frame.extend_from_slice(&body);
    let parsed = cgi_response::parse(&frame).map_err(|_| ResponseError::CgiFrame)?;
    Ok(PrivateWpResponse {
        status: parsed.status,
        raw_headers,
        headers: parsed
            .headers
            .into_iter()
            .map(|(name, value)| PrivateHeader { name, value })
            .collect(),
        body: parsed.body,
        body_sha256: digest,
        representation_length: parsed.representation_length,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};

    fn envelope(status: u16, headers: &[&[u8]], body: &[u8]) -> Value {
        json!({"status": status, "headers_hex": headers.iter().map(|h| hex(h)).collect::<Vec<_>>(),
            "body_bytes": body.len(), "body_hex": hex(body), "body_sha256": hex(&Sha256::digest(body))})
    }
    fn read(value: &Value) -> Result<PrivateWpResponse, ResponseError> {
        decode(&serde_json::to_vec(value).unwrap())
    }
    fn error(value: &Value) -> ResponseError {
        match read(value) {
            Err(error) => error,
            Ok(_) => panic!("malformed synthetic envelope accepted"),
        }
    }

    #[test]
    fn arbitrary_private_binary_and_original_headers_are_retained() {
        let body = b"unknown private body\0\xff\r\n\x80";
        let headers: &[&[u8]] = &[
            b"cOnTeNt-TyPe:\t application/octet-stream \t",
            b"Set-Cookie: private-first=one",
            b"X-Private: \x80opaque",
            b"Set-Cookie: private-second=two",
        ];
        let response = read(&envelope(201, headers, body)).unwrap();
        assert_eq!(response.status(), 201);
        assert_eq!(response.raw_headers().iter().map(Vec::as_slice).collect::<Vec<_>>(), headers.to_vec());
        assert_eq!(response.body(), body);
        assert_eq!(response.headers()[0].name(), "content-type");
        assert_eq!(response.headers()[0].value(), b"application/octet-stream");
        assert_eq!(response.headers()[1].name(), "set-cookie");
        assert_eq!(response.headers()[3].value(), b"private-second=two");
        let summary = serde_json::to_string(&response.summary()).unwrap();
        let body_hex = hex(body);
        for forbidden in ["unknown private body", "private-first", "private-second", "opaque", body_hex.as_str()] {
            assert!(!summary.contains(forbidden));
        }
        assert_eq!(response.summary().header_count, 4);
    }

    #[test]
    fn exact_typed_five_fields_reject_duplicates_unknown_missing_and_trailing_json() {
        let value = envelope(200, &[], b"");
        for key in ["status", "headers_hex", "body_bytes", "body_hex", "body_sha256"] {
            let mut changed = value.clone();
            changed.as_object_mut().unwrap().remove(key);
            assert_eq!(error(&changed), ResponseError::JsonShape);
            let wire = serde_json::to_string(&value).unwrap();
            let duplicate = format!("{{\"{key}\":{},{}", value[key], &wire[1..]);
            assert!(matches!(decode(duplicate.as_bytes()), Err(ResponseError::JsonShape)));
        }
        let mut changed = value.clone(); changed["private_unknown"] = json!(true);
        assert_eq!(error(&changed), ResponseError::JsonShape);
        for (key, values) in [("status", vec![json!(-1), json!(200.0), json!("200"), json!(true)]),
            ("body_bytes", vec![json!(-1), json!(0.0), json!("0"), json!(false)])] {
            for actual in values {
                let mut changed = value.clone(); changed[key] = actual;
                assert_eq!(error(&changed), ResponseError::JsonShape);
            }
        }
        let mut wire = serde_json::to_vec(&value).unwrap(); wire.extend_from_slice(b" {}");
        assert!(matches!(decode(&wire), Err(ResponseError::JsonShape)));
    }

    #[test]
    fn lowercase_hex_length_and_digest_are_all_checked() {
        let value = envelope(200, &[b"X-Fixed: value"], b"\xab");
        for actual in ["AB", "a", "zz"] {
            let mut changed = value.clone(); changed["body_hex"] = json!(actual);
            assert_eq!(error(&changed), ResponseError::Hex);
        }
        let mut changed = value.clone(); changed["body_bytes"] = json!(0);
        assert_eq!(error(&changed), ResponseError::BodyLength);
        let mut changed = value.clone(); changed["body_sha256"] = json!("0".repeat(64));
        assert_eq!(error(&changed), ResponseError::BodyDigest);
        let mut changed = value.clone(); changed["headers_hex"][0] = json!("4A");
        assert_eq!(error(&changed), ResponseError::Hex);
    }

    #[test]
    fn framing_singletons_status_and_hop_headers_cannot_smuggle() {
        for headers in [
            vec![b"Status: 404".as_slice()], vec![b"sTaTuS: 201".as_slice()],
            vec![b"X-Fixed: a\r\nStatus: 404".as_slice()], vec![b"X-Fixed: a\nX-Other: b".as_slice()],
            vec![b" folded: value".as_slice()], vec![b"Bad Name: value".as_slice()],
            vec![b"Content-Length: 0".as_slice(), b"Content-Length: 0".as_slice()],
            vec![b"Content-Type: a".as_slice(), b"Content-Type: b".as_slice()],
            vec![b"Content-Length: 1".as_slice()], vec![b"X-Private: a\0b".as_slice()],
        ] {
            assert!(read(&envelope(200, &headers, b"")).is_err());
        }
        for name in ["Connection", "Keep-Alive", "Proxy-Connection", "TE", "Trailer", "Transfer-Encoding", "Upgrade"] {
            let header = format!("{name}: private");
            assert_eq!(error(&envelope(200, &[header.as_bytes()], b"")), ResponseError::CgiFrame);
        }
        assert_eq!(error(&envelope(199, &[], b"")), ResponseError::Status);
        assert_eq!(error(&envelope(600, &[], b"")), ResponseError::Status);
    }

    #[test]
    fn empty_no_content_and_304_representation_length_follow_the_original_parser() {
        for status in [200, 204, 205, 304] {
            assert!(read(&envelope(status, &[], b"")).is_ok());
        }
        assert!(read(&envelope(204, &[b"Content-Length: 0"], b"")).is_err());
        for status in [204, 205, 304] {
            assert!(read(&envelope(status, &[], b"private")).is_err());
        }
        let response = read(&envelope(304, &[b"Content-Length: 12345", b"ETag: fixed"], b"")).unwrap();
        assert_eq!(response.representation_length(), Some(12345));
        assert_eq!(response.raw_headers().len(), 2);
        assert_eq!(response.headers().len(), 1);
        assert_eq!(response.summary().representation_length, Some(12345));
    }

    #[test]
    fn body_json_and_synthetic_status_header_budgets_have_exact_boundaries() {
        let body = vec![0xff; BODY_LIMIT];
        assert!(read(&envelope(200, &[], &body)).is_ok());
        let mut changed = envelope(200, &[], b""); changed["body_hex"] = json!("00".repeat(BODY_LIMIT+1));
        assert_eq!(error(&changed), ResponseError::BodyLimit);
        assert!(matches!(decode(&vec![b' '; WIRE_LIMIT+1]), Err(ResponseError::WireLimit)));
        let headers = vec![b"X-Fixed: a".as_slice(); HEADER_COUNT_LIMIT];
        assert!(read(&envelope(200, &headers, b"")).is_ok());
        let headers = vec![b"X-Fixed: a".as_slice(); HEADER_COUNT_LIMIT+1];
        assert_eq!(error(&envelope(200, &headers, b"")), ResponseError::HeaderBudget);
        // Status + CRLF = 13 bytes; final empty CRLF = 2 bytes.
        let exact = [b"X:".as_slice(), vec![b'a'; HEADER_LIMIT-19].as_slice()].concat();
        assert!(read(&envelope(200, &[&exact], b"")).is_ok());
        let mut oversized = exact; oversized.push(b'a');
        assert_eq!(error(&envelope(200, &[&oversized], b"")), ResponseError::HeaderBudget);
    }

    #[test]
    fn public_errors_and_summaries_never_format_unknown_private_input() {
        let secret = b"private-error-placeholder";
        let value = envelope(200, &[b"Transfer-Encoding: private-error-placeholder"], secret);
        let error = error(&value);
        assert_eq!(format!("{error}"), "response_cgi_frame");
        assert_eq!(format!("{error:?}"), "response_cgi_frame");
        assert!(!format!("{error:?}").contains(&hex(secret)));
        let summary = read(&envelope(200, &[], secret)).unwrap().summary();
        assert!(!format!("{summary:?}").contains("private-error-placeholder"));
        assert!(!serde_json::to_string(&summary).unwrap().contains(&hex(secret)));
    }
}
