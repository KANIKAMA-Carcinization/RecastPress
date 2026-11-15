//! Explicit PHP-compatible byte strings and ordered array keys for a limited
//! value domain. References, object/resource behavior, and scalar coercion remain
//! represented explicitly when a request-scoped bridge handle exists.

use crate::continuation::PhpHandle;

#[derive(Clone, Debug, PartialEq)]
pub enum PhpValue {
    Null,
    Bool(bool),
    Integer(i64),
    Float(f64),
    Bytes(Vec<u8>),
    Array(PhpArray),
    Resource { handle: PhpHandle, identity: PhpHandle, resource_type: String, resource_id: i64 },
    /// Signals a value whose behavior has no implementation in this prototype.
    Unsupported,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PhpKey {
    Integer(i64),
    Bytes(Vec<u8>),
}

/// Allocation-free canonical PHP integer-string key recognition. Out-of-range
/// decimals, leading zeroes, plus signs and negative zero remain byte keys.
pub(crate) fn canonical_integer_key(bytes: &[u8]) -> Option<i64> {
    let digits = bytes.strip_prefix(b"-").unwrap_or(bytes);
    if digits.is_empty()
        || !digits.iter().all(u8::is_ascii_digit)
        || (digits[0] == b'0' && (digits.len() != 1 || bytes[0] == b'-'))
    {
        return None;
    }
    std::str::from_utf8(bytes).ok()?.parse().ok()
}

impl PhpKey {
    /// PHP integer-string array keys are canonical signed decimal values within
    /// the 64-bit target range. "+1", "01", and "-0" remain string keys.
    pub fn from_bytes(bytes: &[u8]) -> Self {
        if let Ok(text) = std::str::from_utf8(bytes)
            && let Ok(integer) = text.parse::<i64>()
            && integer.to_string() == text
        {
            return Self::Integer(integer);
        }
        Self::Bytes(bytes.to_vec())
    }

    fn canonical(self) -> Self {
        match self {
            Self::Bytes(bytes) => Self::from_bytes(&bytes),
            key => key,
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct PhpArray(Vec<(PhpKey, PhpValue)>);

impl PhpArray {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn len(&self) -> usize {
        self.0.len()
    }
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
    pub fn iter(&self) -> impl Iterator<Item = (&PhpKey, &PhpValue)> {
        self.0.iter().map(|(key, value)| (key, value))
    }

    /// Looks up a key using the same canonical integer-string conversion as insert.
    pub fn get(&self, key: &PhpKey) -> Option<&PhpValue> {
        let key = key.clone().canonical();
        self.0
            .iter()
            .find(|(candidate, _)| *candidate == key)
            .map(|(_, value)| value)
    }

    /// Borrowed lookup for a raw PHP byte-string key, without cloning the key or
    /// formatting an integer. All stored keys already obey insert's conversion.
    pub(crate) fn get_bytes(&self, bytes: &[u8]) -> Option<&PhpValue> {
        let integer = canonical_integer_key(bytes);
        self.0
            .iter()
            .find(|(key, _)| match (key, integer) {
                (PhpKey::Integer(stored), Some(requested)) => *stored == requested,
                (PhpKey::Bytes(stored), None) => stored == bytes,
                _ => false,
            })
            .map(|(_, value)| value)
    }
    pub(crate) fn get_mut(&mut self, key: &PhpKey) -> Option<&mut PhpValue> {
        let key = key.clone().canonical();
        self.0
            .iter_mut()
            .find(|(candidate, _)| *candidate == key)
            .map(|(_, value)| value)
    }
    /// Replacing an existing key preserves its original position.
    pub fn insert(&mut self, key: PhpKey, value: PhpValue) -> Option<PhpValue> {
        let key = key.canonical();
        if let Some((_, existing)) = self.0.iter_mut().find(|(candidate, _)| *candidate == key) {
            return Some(std::mem::replace(existing, value));
        }
        self.0.push((key, value));
        None
    }

    /// Internal decoder construction only: the caller has already checked that
    /// the key is canonical and unique, and charged its pair/string allowances.
    /// This deliberately does not change public insert's key conversion rules.
    pub(crate) fn try_push_prevalidated(
        &mut self,
        key: PhpKey,
        value: PhpValue,
    ) -> Result<(), std::collections::TryReserveError> {
        self.0.try_reserve(1)?;
        self.0.push((key, value));
        Ok(())
    }
}
