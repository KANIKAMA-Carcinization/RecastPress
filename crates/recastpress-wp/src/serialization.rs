//! Selected WordPress serialization helpers with explicit value-domain limits.
//! No PHP objects or references are instantiated from serialized data.

use crate::value::{PhpKey, canonical_integer_key};
use crate::{CompatibilityError, value::PhpValue};

const MAX_SERIALIZED_ARRAY_DEPTH: usize = 64;

/// Caller-selected logical allowances, with no product defaults. String bytes
/// count payloads and byte keys (or the entire unchanged nonserialized input).
/// Entries count cumulative array pairs, not just the outer array. Depth counts
/// arrays only; zero permits scalar values. The compatibility ceiling is 64.
/// These limits exclude node/header/capacity overhead, input ownership, peak
/// allocations, RSS, concurrency and shared-pool accounting. Duplicate checks
/// may take quadratic time within the explicit entry allowance.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct UnserializeLimits {
    max_input_bytes: usize,
    max_array_entries: usize,
    max_owned_string_bytes: usize,
    max_array_depth: usize,
}

impl UnserializeLimits {
    pub fn new(
        max_input_bytes: usize,
        max_array_entries: usize,
        max_owned_string_bytes: usize,
        max_array_depth: usize,
    ) -> Result<Self, LimitedUnserializeError> {
        if max_array_depth > MAX_SERIALIZED_ARRAY_DEPTH {
            return Err(LimitedUnserializeError::InvalidLimits);
        }
        Ok(Self {
            max_input_bytes,
            max_array_entries,
            max_owned_string_bytes,
            max_array_depth,
        })
    }
}

/// Closed diagnostics never contain source bytes, array keys or decoded values.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LimitedUnserializeError {
    InvalidLimits,
    UnsupportedValue,
    InputLimit,
    EntryLimit,
    OwnedBytesLimit,
    DepthLimit,
    AllocationFailed,
}

impl std::fmt::Display for LimitedUnserializeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::InvalidLimits => "invalid unserialization limits",
            Self::UnsupportedValue => "unsupported serialized value",
            Self::InputLimit => "unserialization input limit exceeded",
            Self::EntryLimit => "unserialization entry limit exceeded",
            Self::OwnedBytesLimit => "unserialization string byte limit exceeded",
            Self::DepthLimit => "unserialization depth limit exceeded",
            Self::AllocationFailed => "unserialization allocation failed",
        })
    }
}

impl std::error::Error for LimitedUnserializeError {}

/// Decode the existing canonical byte-value subset with explicit host limits.
/// Raw input (including outer whitespace) is checked before recognition/copy.
/// Nonserialized input is returned byte-for-byte; recognized unsupported or
/// malformed values fail, never becoming Null/false. Decoding is one layer only.
/// No PHP objects/references, defaults, option lookup, metadata selection or
/// authorization are performed. Returned PhpValue's Debug is NOT redacted;
/// callers must keep values private. No partial value is returned on failure.
///
/// Limits are enforced before each owned string copy/array append; parsing is
/// sequential, so a limit can precede a later syntax error. No second complete
/// canonical serialization buffer is made on this path. Legacy APIs are intact.
pub fn maybe_unserialize_bytes_with_limits(
    raw: &[u8],
    limits: UnserializeLimits,
) -> Result<PhpValue, LimitedUnserializeError> {
    if raw.len() > limits.max_input_bytes {
        return Err(LimitedUnserializeError::InputLimit);
    }
    let mut decoder = Decoder {
        bytes: php_trim(raw),
        position: 0,
        policy: DecodePolicy::Bounded {
            limits,
            entries: 0,
            owned_bytes: 0,
        },
    };
    if !is_serialized_bytes(raw, true) {
        return decoder.copy_bytes(raw).map(PhpValue::Bytes);
    }
    let value = decoder.value(0)?;
    if decoder.position != decoder.bytes.len() {
        return Err(LimitedUnserializeError::UnsupportedValue);
    }
    Ok(value)
}

/// Supported maybe_serialize domains: modeled arrays without floats/objects or
/// references, plus unchanged non-array values and double-encoded recognized
/// byte strings. Existing serialized strings retain their original whitespace.
/// Array nesting is capped at 64 by host policy, not by a PHP equivalence claim.
pub fn maybe_serialize(value: &PhpValue) -> Result<PhpValue, CompatibilityError> {
    if matches!(value, PhpValue::Array(_)) || is_serialized(value, false)? {
        let mut result = Vec::new();
        serialize_into(value, 0, &mut result)?;
        Ok(PhpValue::Bytes(result))
    } else {
        Ok(value.clone())
    }
}

/// Decode only canonical serialized null/bool/integer/byte-string/array values.
/// Nonserialized inputs remain unchanged. Recognized but malformed,
/// noncanonical, float, object and reference encodings return an explicit error;
/// they are not replaced with PHP's unserialize failure sentinel.
pub fn maybe_unserialize(value: &PhpValue) -> Result<PhpValue, CompatibilityError> {
    if !is_serialized(value, true)? {
        return Ok(value.clone());
    }
    let raw = php_trim(string_bytes(value)?.ok_or(CompatibilityError::UnsupportedValue)?);
    let mut decoder = Decoder {
        bytes: raw,
        position: 0,
        policy: DecodePolicy::Legacy,
    };
    let decoded = decoder.value(0).map_err(|error| match error {
        LimitedUnserializeError::DepthLimit => CompatibilityError::RecursionLimit,
        _ => CompatibilityError::UnsupportedValue,
    })?;
    if decoder.position != raw.len() {
        return Err(CompatibilityError::UnsupportedValue);
    }
    let mut canonical = Vec::new();
    serialize_into(&decoded, 0, &mut canonical)?;
    if canonical != raw {
        return Err(CompatibilityError::UnsupportedValue);
    }
    Ok(decoded)
}

struct Decoder<'a> {
    bytes: &'a [u8],
    position: usize,
    policy: DecodePolicy,
}

enum DecodePolicy {
    Legacy,
    Bounded {
        limits: UnserializeLimits,
        entries: usize,
        owned_bytes: usize,
    },
}

impl<'a> Decoder<'a> {
    fn bounded(&self) -> bool {
        matches!(self.policy, DecodePolicy::Bounded { .. })
    }

    fn copy_bytes(&mut self, bytes: &[u8]) -> Result<Vec<u8>, LimitedUnserializeError> {
        let DecodePolicy::Bounded {
            limits,
            owned_bytes,
            ..
        } = &mut self.policy
        else {
            return Ok(bytes.to_vec());
        };
        let next = owned_bytes
            .checked_add(bytes.len())
            .filter(|next| *next <= limits.max_owned_string_bytes)
            .ok_or(LimitedUnserializeError::OwnedBytesLimit)?;
        let mut owned = Vec::new();
        owned
            .try_reserve_exact(bytes.len())
            .map_err(|_| LimitedUnserializeError::AllocationFailed)?;
        owned.extend_from_slice(bytes);
        *owned_bytes = next;
        Ok(owned)
    }

    fn admit_array(&mut self, depth: usize, count: usize) -> Result<(), LimitedUnserializeError> {
        match &mut self.policy {
            DecodePolicy::Legacy => {
                if depth >= MAX_SERIALIZED_ARRAY_DEPTH {
                    return Err(LimitedUnserializeError::DepthLimit);
                }
            }
            DecodePolicy::Bounded {
                limits, entries, ..
            } => {
                if depth >= limits.max_array_depth {
                    return Err(LimitedUnserializeError::DepthLimit);
                }
                *entries = entries
                    .checked_add(count)
                    .filter(|next| *next <= limits.max_array_entries)
                    .ok_or(LimitedUnserializeError::EntryLimit)?;
            }
        }
        Ok(())
    }

    fn take(&mut self, length: usize) -> Result<&'a [u8], LimitedUnserializeError> {
        let end = self
            .position
            .checked_add(length)
            .ok_or(LimitedUnserializeError::UnsupportedValue)?;
        let bytes = self
            .bytes
            .get(self.position..end)
            .ok_or(LimitedUnserializeError::UnsupportedValue)?;
        self.position = end;
        Ok(bytes)
    }

    fn expect(&mut self, expected: &[u8]) -> Result<(), LimitedUnserializeError> {
        if self.take(expected.len())? != expected {
            return Err(LimitedUnserializeError::UnsupportedValue);
        }
        Ok(())
    }

    fn until(&mut self, delimiter: u8) -> Result<&'a [u8], LimitedUnserializeError> {
        let length = self.bytes[self.position..]
            .iter()
            .position(|byte| *byte == delimiter)
            .ok_or(LimitedUnserializeError::UnsupportedValue)?;
        let result = self.take(length)?;
        self.expect(&[delimiter])?;
        Ok(result)
    }

    fn length(&mut self) -> Result<usize, LimitedUnserializeError> {
        let bytes = self.until(b':')?;
        if bytes.is_empty()
            || !bytes.iter().all(u8::is_ascii_digit)
            || (self.bounded() && bytes.len() > 1 && bytes[0] == b'0')
        {
            return Err(LimitedUnserializeError::UnsupportedValue);
        }
        std::str::from_utf8(bytes)
            .ok()
            .and_then(|value| value.parse().ok())
            .ok_or(LimitedUnserializeError::UnsupportedValue)
    }

    fn value(&mut self, depth: usize) -> Result<PhpValue, LimitedUnserializeError> {
        match self.take(1)?[0] {
            b'N' => {
                self.expect(b";")?;
                Ok(PhpValue::Null)
            }
            b'b' => {
                self.expect(b":")?;
                match self.until(b';')? {
                    b"0" => Ok(PhpValue::Bool(false)),
                    b"1" => Ok(PhpValue::Bool(true)),
                    _ => Err(LimitedUnserializeError::UnsupportedValue),
                }
            }
            b'i' => {
                self.expect(b":")?;
                let bytes = self.until(b';')?;
                if self.bounded() {
                    return canonical_integer_key(bytes)
                        .map(PhpValue::Integer)
                        .ok_or(LimitedUnserializeError::UnsupportedValue);
                }
                std::str::from_utf8(bytes)
                    .ok()
                    .and_then(|value| value.parse::<i64>().ok())
                    .map(PhpValue::Integer)
                    .ok_or(LimitedUnserializeError::UnsupportedValue)
            }
            b's' => {
                self.expect(b":")?;
                let length = self.length()?;
                self.expect(b"\"")?;
                let raw = self.take(length)?;
                // Legacy copies before the closing delimiter, as before.
                let legacy = if self.bounded() {
                    None
                } else {
                    Some(raw.to_vec())
                };
                self.expect(b"\";")?;
                let bytes = match legacy {
                    Some(bytes) => bytes,
                    None => self.copy_bytes(raw)?,
                };
                Ok(PhpValue::Bytes(bytes))
            }
            b'a' => {
                if depth >= MAX_SERIALIZED_ARRAY_DEPTH {
                    return Err(LimitedUnserializeError::DepthLimit);
                }
                self.expect(b":")?;
                let count = self.length()?;
                self.expect(b"{")?;
                // Each supported canonical key/value pair needs at least six
                // bytes (i:0;N;). Never reserve memory from an untrusted count.
                if count > (self.bytes.len() - self.position) / 6 {
                    return Err(LimitedUnserializeError::UnsupportedValue);
                }
                self.admit_array(depth, count)?;
                let mut array = crate::value::PhpArray::new();
                for _ in 0..count {
                    if !matches!(self.bytes.get(self.position), Some(b'i' | b's')) {
                        return Err(LimitedUnserializeError::UnsupportedValue);
                    }
                    let key = match self.value(depth + 1)? {
                        PhpValue::Integer(value) => PhpKey::Integer(value),
                        PhpValue::Bytes(value) if self.bounded() => {
                            // A canonical numeric string would be re-encoded
                            // as an integer key by legacy canonical comparison.
                            if canonical_integer_key(&value).is_some() {
                                return Err(LimitedUnserializeError::UnsupportedValue);
                            }
                            PhpKey::Bytes(value)
                        }
                        PhpValue::Bytes(value) => PhpKey::from_bytes(&value),
                        _ => return Err(LimitedUnserializeError::UnsupportedValue),
                    };
                    if self.bounded() && array.iter().any(|(previous, _)| previous == &key) {
                        return Err(LimitedUnserializeError::UnsupportedValue);
                    }
                    let value = self.value(depth + 1)?;
                    if self.bounded() {
                        array
                            .try_push_prevalidated(key, value)
                            .map_err(|_| LimitedUnserializeError::AllocationFailed)?;
                    } else {
                        array.insert(key, value);
                    }
                }
                self.expect(b"}")?;
                Ok(PhpValue::Array(array))
            }
            _ => Err(LimitedUnserializeError::UnsupportedValue),
        }
    }
}

fn serialize_integer(value: i64, output: &mut Vec<u8>) {
    output.extend_from_slice(b"i:");
    output.extend_from_slice(value.to_string().as_bytes());
    output.push(b';');
}

fn serialize_bytes(value: &[u8], output: &mut Vec<u8>) {
    output.extend_from_slice(b"s:");
    output.extend_from_slice(value.len().to_string().as_bytes());
    output.extend_from_slice(b":\"");
    output.extend_from_slice(value);
    output.extend_from_slice(b"\";");
}

fn serialize_into(
    value: &PhpValue,
    depth: usize,
    output: &mut Vec<u8>,
) -> Result<(), CompatibilityError> {
    match value {
        PhpValue::Null => output.extend_from_slice(b"N;"),
        PhpValue::Bool(value) => output.extend_from_slice(if *value { b"b:1;" } else { b"b:0;" }),
        PhpValue::Integer(value) => serialize_integer(*value, output),
        PhpValue::Bytes(value) => serialize_bytes(value, output),
        PhpValue::Array(array) => {
            if depth >= MAX_SERIALIZED_ARRAY_DEPTH {
                return Err(CompatibilityError::RecursionLimit);
            }
            output.extend_from_slice(b"a:");
            output.extend_from_slice(array.len().to_string().as_bytes());
            output.extend_from_slice(b":{");
            for (key, value) in array.iter() {
                match key {
                    PhpKey::Integer(value) => serialize_integer(*value, output),
                    PhpKey::Bytes(value) => serialize_bytes(value, output),
                }
                serialize_into(value, depth + 1, output)?;
            }
            output.push(b'}');
        }
        // Float serialization depends on PHP's serialize_precision and exact
        // spelling. Top-level maybe_serialize floats remain unchanged instead.
        PhpValue::Float(_) | PhpValue::Unsupported | PhpValue::Resource { .. } | PhpValue::ArgumentHandle(_) => {
            return Err(CompatibilityError::UnsupportedValue);
        }
    }
    Ok(())
}

fn string_bytes(value: &PhpValue) -> Result<Option<&[u8]>, CompatibilityError> {
    match value {
        PhpValue::Unsupported | PhpValue::Resource { .. } | PhpValue::ArgumentHandle(_) => Err(CompatibilityError::UnsupportedValue),
        PhpValue::Bytes(bytes) => Ok(Some(bytes)),
        _ => Ok(None),
    }
}

// PHP trim's default byte set includes NUL and vertical tab, but not form feed.
fn php_trim(bytes: &[u8]) -> &[u8] {
    let whitespace = |byte: &u8| matches!(byte, 0 | b' ' | b'\t' | b'\n' | b'\r' | 0x0b);
    let start = bytes
        .iter()
        .position(|byte| !whitespace(byte))
        .unwrap_or(bytes.len());
    let end = bytes
        .iter()
        .rposition(|byte| !whitespace(byte))
        .map_or(start, |index| index + 1);
    &bytes[start..end]
}

/// Match WP's shape checks, including permissive lengths/bodies and numeric
/// token spelling. strict is a typed boolean; PHP argument coercion is pending.
pub fn is_serialized(value: &PhpValue, strict: bool) -> Result<bool, CompatibilityError> {
    let Some(bytes) = string_bytes(value)? else {
        return Ok(false);
    };
    Ok(is_serialized_bytes(bytes, strict))
}

// Borrowed recognizer shared by the legacy typed API and bounded byte API.
fn is_serialized_bytes(bytes: &[u8], strict: bool) -> bool {
    let data = php_trim(bytes);
    if data == b"N;" {
        return true;
    }
    if data.len() < 4 || data[1] != b':' {
        return false;
    }
    if strict {
        if !matches!(data.last(), Some(b';' | b'}')) {
            return false;
        }
    } else {
        let semicolon = data.iter().position(|byte| *byte == b';');
        let brace = data.iter().position(|byte| *byte == b'}');
        if (semicolon.is_none() && brace.is_none())
            || semicolon.is_some_and(|index| index < 3)
            || brace.is_some_and(|index| index < 4)
        {
            return false;
        }
    }
    match data[0] {
        b's' | b'a' | b'O' | b'E' => {
            if data[0] == b's'
                && ((strict && data[data.len() - 2] != b'"') || (!strict && !data.contains(&b'"')))
            {
                return false;
            }
            let digits = data[2..]
                .iter()
                .take_while(|byte| byte.is_ascii_digit())
                .count();
            digits > 0 && data.get(2 + digits) == Some(&b':')
        }
        b'b' | b'i' | b'd' => {
            let digits = data[2..]
                .iter()
                .take_while(|byte| matches!(byte, b'0'..=b'9' | b'.' | b'E' | b'+' | b'-'))
                .count();
            digits > 0
                && data.get(2 + digits) == Some(&b';')
                && (!strict || 3 + digits == data.len())
        }
        _ => false,
    }
}

/// WP's string-specific recognizer deliberately does not validate a length
/// prefix. A positive result must not be treated as successful deserialization.
pub fn is_serialized_string(value: &PhpValue) -> Result<bool, CompatibilityError> {
    let Some(bytes) = string_bytes(value)? else {
        return Ok(false);
    };
    let data = php_trim(bytes);
    Ok(data.len() >= 4 && data[0] == b's' && data[1] == b':' && data.ends_with(b"\";"))
}

#[cfg(test)]
mod bounded_tests {
    use super::*;

    #[test]
    fn checked_cumulative_allowances_refuse_overflow_before_copy_or_append() {
        let limits = UnserializeLimits::new(usize::MAX, usize::MAX, usize::MAX, 64).unwrap();
        let mut decoder = Decoder {
            bytes: b"",
            position: 0,
            policy: DecodePolicy::Bounded {
                limits,
                entries: usize::MAX,
                owned_bytes: usize::MAX,
            },
        };
        assert_eq!(
            decoder.copy_bytes(b"x"),
            Err(LimitedUnserializeError::OwnedBytesLimit)
        );
        assert_eq!(
            decoder.admit_array(0, 1),
            Err(LimitedUnserializeError::EntryLimit)
        );
        assert!(decoder.copy_bytes(b"").unwrap().is_empty());
        assert!(decoder.admit_array(0, 0).is_ok());
    }
}
