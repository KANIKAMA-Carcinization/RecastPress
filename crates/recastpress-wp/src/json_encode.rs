//! Candidate wp_json_encode subset from WordPress 7.1.3 functions.php:4443.
//!
//! Only the successful first json_encode(value, flags, depth=512) path is
//! modeled: null/bool/i64, valid UTF-8 byte strings and ordered PHP arrays.
//! The default/with_flags APIs reject invalid UTF-8, floats and unsupported
//! values. The explicitly named utf8_no_mbstring API additionally models the
//! _wp_json_sanity_check (:4475)/_wp_json_convert_string (:4537) fallback only
//! for a fixed UTF8 blog_charset and absent mb_convert_encoding. Other charset
//! or mbstring paths, floats, unselected flags/nondefault PHP depth,
//! object/reference/resource behavior and Wasm are not implemented.
//! JsonFlags accepts only the nine explicitly modeled bits,
//! including PRETTY_PRINT's four-space indentation. The output cap counts all
//! escaped bytes, indentation and line breaks; empty containers stay compact.
//! The deprecated _wp_json_prepare_data (:4569) is not invoked by this function.
//!
//! The host imposes 64 array levels, counting a root array as one and counting
//! empty arrays. This differs from PHP's default depth 512. A first traversal
//! validates the entire domain/depth and measures output with checked usize
//! arithmetic; only then is the complete-output cap checked and the output
//! allocated/encoded. No partial output is returned. Failed output reservation
//! also yields the host OutputLimit error, rather than PHP's false sentinel.
//! These limits are not process-memory/shared-budget accounting. Default JSON
//! encoding is not HTML/script escaping or a security guarantee.

use crate::value::{PhpArray, PhpKey, PhpValue};
use std::fmt;

const MAX_ARRAY_DEPTH: usize = 64;

/// Selected PHP JSON bits. Construction rejects every unmodeled flag.
/// These options do not establish HTML/script escaping or a security guarantee.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct JsonFlags(u32);

impl JsonFlags {
    pub const HEX_TAG: Self = Self(1);
    pub const HEX_AMP: Self = Self(2);
    pub const HEX_APOS: Self = Self(4);
    pub const HEX_QUOT: Self = Self(8);
    pub const FORCE_OBJECT: Self = Self(16);
    pub const UNESCAPED_SLASHES: Self = Self(64);
    pub const PRETTY_PRINT: Self = Self(128);
    pub const UNESCAPED_UNICODE: Self = Self(256);
    pub const UNESCAPED_LINE_TERMINATORS: Self = Self(2048);

    pub fn from_bits(bits: u32) -> Result<Self, JsonEncodeError> {
        if bits & !2527 != 0 {
            return Err(JsonEncodeError::UnsupportedFlags);
        }
        Ok(Self(bits))
    }

    pub fn bits(self) -> u32 {
        self.0
    }

    fn contains(self, flag: Self) -> bool {
        self.0 & flag.0 != 0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum JsonEncodeError {
    UnsupportedValue,
    UnsupportedFlags,
    /// Host array-depth bound, not PHP's default depth failure.
    RecursionLimit,
    /// Host output byte bound, size overflow or failed output reservation.
    OutputLimit,
}

impl fmt::Display for JsonEncodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::UnsupportedValue => "JSON value is outside the supported input domain",
            Self::UnsupportedFlags => "JSON flags are outside the supported flag subset",
            Self::RecursionLimit => "JSON input exceeds the host array-depth bound",
            Self::OutputLimit => "JSON output exceeds the host byte or allocation bound",
        })
    }
}

impl std::error::Error for JsonEncodeError {}

/// Encode the valid-UTF8, nonfloat default-flags subset, returning complete bytes.
/// Domain/depth validation precedes the caller's complete-output byte cap.
pub fn wp_json_encode(
    value: &PhpValue,
    max_output_bytes: usize,
) -> Result<Vec<u8>, JsonEncodeError> {
    wp_json_encode_with_flags(value, JsonFlags::default(), max_output_bytes)
}

/// Encode the supported value domain with the selected PHP flag subset.
/// The host array-depth and complete-output bounds are independent of PHP flags.
pub fn wp_json_encode_with_flags(
    value: &PhpValue,
    flags: JsonFlags,
    max_output_bytes: usize,
) -> Result<Vec<u8>, JsonEncodeError> {
    let mut measure = Encoder::new(false, flags);
    measure.value(value, 0)?;
    if measure.length > max_output_bytes {
        return Err(JsonEncodeError::OutputLimit);
    }
    let mut encoder = Encoder::new(true, flags);
    encoder
        .output
        .try_reserve_exact(measure.length)
        .map_err(|_| JsonEncodeError::OutputLimit)?;
    encoder.value(value, 0)?;
    debug_assert_eq!(encoder.length, measure.length);
    Ok(encoder.output)
}

/// Explicit WordPress fallback profile: blog_charset recognizes UTF-8 and
/// mb_convert_encoding is absent. This does not infer or choose site settings.
/// The first successful encoding path is independent of those settings.
///
/// Validate the ORIGINAL graph's nonfloat domain and 64-container bound before
/// sanitizing. Otherwise key collisions could hide an unsupported value/depth.
/// With invalid UTF-8, convert all string keys/values and reinsert ordered keys,
/// retaining the first key position and last colliding value as WordPress does.
///
/// A temporary owned sanitized graph (including valid strings) is allocated
/// BEFORE the existing output-length/cap pass. Its allocations, peak memory and
/// RSS are outside the output cap and shared-budget accounting. No caller input
/// is changed, and no partial JSON is returned. The two existing APIs continue
/// rejecting invalid UTF-8; this fallback is opt-in through this function name.
pub fn wp_json_encode_utf8_no_mbstring(
    value: &PhpValue,
    flags: JsonFlags,
    max_output_bytes: usize,
) -> Result<Vec<u8>, JsonEncodeError> {
    if !validate_fallback_domain(value, 0)? {
        return wp_json_encode_with_flags(value, flags, max_output_bytes);
    }
    let sanitized = sanitize_utf8_graph(value)?;
    wp_json_encode_with_flags(&sanitized, flags, max_output_bytes)
}

fn validate_fallback_domain(value: &PhpValue, depth: usize) -> Result<bool, JsonEncodeError> {
    match value {
        PhpValue::Null | PhpValue::Bool(_) | PhpValue::Integer(_) => Ok(false),
        PhpValue::Bytes(bytes) => Ok(!crate::wp_is_valid_utf8_bytes(bytes)),
        PhpValue::Float(_) | PhpValue::Unsupported | PhpValue::Resource { .. } => Err(JsonEncodeError::UnsupportedValue),
        PhpValue::Array(array) => {
            if depth >= MAX_ARRAY_DEPTH {
                return Err(JsonEncodeError::RecursionLimit);
            }
            let mut invalid = false;
            for (key, value) in array.iter() {
                if let PhpKey::Bytes(bytes) = key {
                    invalid |= !crate::wp_is_valid_utf8_bytes(bytes);
                }
                // Do not short-circuit: later values still require validation.
                invalid |= validate_fallback_domain(value, depth + 1)?;
            }
            Ok(invalid)
        }
    }
}

fn sanitize_utf8_graph(value: &PhpValue) -> Result<PhpValue, JsonEncodeError> {
    match value {
        PhpValue::Null | PhpValue::Bool(_) | PhpValue::Integer(_) => Ok(value.clone()),
        PhpValue::Bytes(bytes) => Ok(PhpValue::Bytes(crate::wp_scrub_utf8_bytes(bytes))),
        PhpValue::Array(array) => {
            let mut sanitized = PhpArray::new();
            for (key, value) in array.iter() {
                let clean_key = match key {
                    PhpKey::Integer(integer) => PhpKey::Integer(*integer),
                    PhpKey::Bytes(bytes) => PhpKey::from_bytes(&crate::wp_scrub_utf8_bytes(bytes)),
                };
                sanitized.insert(clean_key, sanitize_utf8_graph(value)?);
            }
            Ok(PhpValue::Array(sanitized))
        }
        PhpValue::Float(_) | PhpValue::Unsupported | PhpValue::Resource { .. } => Err(JsonEncodeError::UnsupportedValue),
    }
}

struct Encoder {
    output: Vec<u8>,
    length: usize,
    write: bool,
    flags: JsonFlags,
}

impl Encoder {
    fn new(write: bool, flags: JsonFlags) -> Self {
        Self {
            output: Vec::new(),
            length: 0,
            write,
            flags,
        }
    }

    fn emit(&mut self, bytes: &[u8]) -> Result<(), JsonEncodeError> {
        self.length = self
            .length
            .checked_add(bytes.len())
            .ok_or(JsonEncodeError::OutputLimit)?;
        if self.write {
            self.output.extend_from_slice(bytes);
        }
        Ok(())
    }

    fn value(&mut self, value: &PhpValue, depth: usize) -> Result<(), JsonEncodeError> {
        match value {
            PhpValue::Null => self.emit(b"null"),
            PhpValue::Bool(false) => self.emit(b"false"),
            PhpValue::Bool(true) => self.emit(b"true"),
            PhpValue::Integer(value) => self.emit(value.to_string().as_bytes()),
            PhpValue::Bytes(value) => self.string(value),
            PhpValue::Array(array) => self.array(array, depth),
            PhpValue::Float(_) | PhpValue::Unsupported | PhpValue::Resource { .. } => Err(JsonEncodeError::UnsupportedValue),
        }
    }

    fn array(&mut self, array: &PhpArray, depth: usize) -> Result<(), JsonEncodeError> {
        if depth >= MAX_ARRAY_DEPTH {
            return Err(JsonEncodeError::RecursionLimit);
        }
        let list = !self.flags.contains(JsonFlags::FORCE_OBJECT)
            && array.iter().enumerate().all(|(offset, (key, _))| {
                matches!(key, PhpKey::Integer(integer) if i64::try_from(offset).ok() == Some(*integer))
            });
        let pretty = self.flags.contains(JsonFlags::PRETTY_PRINT);
        let child_depth = depth.checked_add(1).ok_or(JsonEncodeError::OutputLimit)?;
        self.emit(if list { b"[" } else { b"{" })?;
        for (offset, (key, value)) in array.iter().enumerate() {
            if offset != 0 {
                self.emit(b",")?;
            }
            if pretty {
                self.newline_indent(child_depth)?;
            }
            if !list {
                match key {
                    PhpKey::Integer(integer) => self.string(integer.to_string().as_bytes())?,
                    PhpKey::Bytes(bytes) => self.string(bytes)?,
                }
                self.emit(if pretty { b": " } else { b":" })?;
            }
            self.value(value, child_depth)?;
        }
        if pretty && !array.is_empty() {
            self.newline_indent(depth)?;
        }
        self.emit(if list { b"]" } else { b"}" })
    }

    fn newline_indent(&mut self, depth: usize) -> Result<(), JsonEncodeError> {
        let spaces = depth.checked_mul(4).ok_or(JsonEncodeError::OutputLimit)?;
        self.emit(b"\n")?;
        for _ in 0..spaces / 4 {
            self.emit(b"    ")?;
        }
        Ok(())
    }

    fn string(&mut self, value: &[u8]) -> Result<(), JsonEncodeError> {
        let text = std::str::from_utf8(value).map_err(|_| JsonEncodeError::UnsupportedValue)?;
        self.emit(b"\"")?;
        for character in text.chars() {
            match character {
                '<' if self.flags.contains(JsonFlags::HEX_TAG) => self.emit(b"\\u003C")?,
                '>' if self.flags.contains(JsonFlags::HEX_TAG) => self.emit(b"\\u003E")?,
                '&' if self.flags.contains(JsonFlags::HEX_AMP) => self.emit(b"\\u0026")?,
                '\'' if self.flags.contains(JsonFlags::HEX_APOS) => self.emit(b"\\u0027")?,
                '"' if self.flags.contains(JsonFlags::HEX_QUOT) => self.emit(b"\\u0022")?,
                '"' => self.emit(b"\\\"")?,
                '\\' => self.emit(b"\\\\")?,
                '/' if self.flags.contains(JsonFlags::UNESCAPED_SLASHES) => self.emit(b"/")?,
                '/' => self.emit(b"\\/")?,
                '\u{08}' => self.emit(b"\\b")?,
                '\t' => self.emit(b"\\t")?,
                '\n' => self.emit(b"\\n")?,
                '\u{0c}' => self.emit(b"\\f")?,
                '\r' => self.emit(b"\\r")?,
                '\u{20}'..='\u{7f}' => self.emit(&[character as u8])?,
                '\u{80}'..
                    if self.flags.contains(JsonFlags::UNESCAPED_UNICODE)
                        && (!matches!(character, '\u{2028}' | '\u{2029}')
                            || self.flags.contains(JsonFlags::UNESCAPED_LINE_TERMINATORS)) =>
                {
                    let mut bytes = [0; 4];
                    self.emit(character.encode_utf8(&mut bytes).as_bytes())?;
                }
                _ => {
                    let mut units = [0; 2];
                    for &unit in character.encode_utf16(&mut units).iter() {
                        const HEX: &[u8; 16] = b"0123456789abcdef";
                        self.emit(&[
                            b'\\',
                            b'u',
                            HEX[usize::from((unit >> 12) & 15)],
                            HEX[usize::from((unit >> 8) & 15)],
                            HEX[usize::from((unit >> 4) & 15)],
                            HEX[usize::from(unit & 15)],
                        ])?;
                    }
                }
            }
        }
        self.emit(b"\"")
    }
}
