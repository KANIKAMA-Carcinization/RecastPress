//! Fixed private host for the request-local WordPress cache comparison.

use recastpress_core::resource_budget::{BudgetPool, Reservation, ResourceKind};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::{BTreeMap, btree_map::Entry};
use std::io::{self, BufRead, Write};

const BUDGET: usize = 64 * 1024;
const FRAME_LIMIT: usize = 16 * 1024;
const KEY_LIMIT: usize = 256;
const VALUE_LIMIT: usize = 4096;
const OPERATIONS_LIMIT: usize = 512;
const ENTRY_ALLOWANCE: usize = 128;

type Result<T> = std::result::Result<T, &'static str>;

#[derive(Clone, Copy, Deserialize, Eq, Ord, PartialEq, PartialOrd)]
enum Group {
    #[serde(rename = "rp-cache-multiple")]
    Multiple,
    #[serde(rename = "rp-cache-integer")]
    Integer,
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum WireValue {
    Null {},
    Bool { value: bool },
    Int { value: i64 },
    Bytes { value: String },
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Get {
        group: Group,
        key: String,
    },
    Delete {
        group: Group,
        key: String,
    },
    Set {
        group: Group,
        key: String,
        value: WireValue,
    },
    Add {
        group: Group,
        key: String,
        value: WireValue,
    },
    Replace {
        group: Group,
        key: String,
        value: WireValue,
    },
    Incr {
        group: Group,
        key: String,
        offset: i64,
    },
    Decr {
        group: Group,
        key: String,
        offset: i64,
    },
    FlushGroup {
        group: Group,
    },
    Flush {},
    Finish {},
}

#[derive(Eq, Ord, PartialEq, PartialOrd)]
enum Key {
    Int(i64),
    Bytes(Vec<u8>),
}

enum Stored {
    Null,
    Bool(bool),
    Int(i64),
    Bytes(Vec<u8>),
}

fn decode_hex(text: &str, limit: usize) -> Option<Vec<u8>> {
    fn digit(byte: u8) -> Option<u8> {
        match byte {
            b'0'..=b'9' => Some(byte - b'0'),
            b'a'..=b'f' => Some(byte - b'a' + 10),
            _ => None,
        }
    }
    if text.len() > 2 * limit || !text.len().is_multiple_of(2) {
        return None;
    }
    let mut bytes = Vec::with_capacity(text.len() / 2);
    for pair in text.as_bytes().chunks_exact(2) {
        bytes.push(digit(pair[0])? << 4 | digit(pair[1])?);
    }
    Some(bytes)
}

fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut text = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        text.push(HEX[usize::from(byte >> 4)] as char);
        text.push(HEX[usize::from(byte & 15)] as char);
    }
    text
}

fn canonical_int(bytes: &[u8]) -> Option<i64> {
    let text = std::str::from_utf8(bytes).ok()?;
    let number = text.parse::<i64>().ok()?;
    (number.to_string() == text).then_some(number)
}

impl Key {
    fn parse(wire: &str) -> Result<Self> {
        if let Some(text) = wire.strip_prefix("i:") {
            return canonical_int(text.as_bytes())
                .map(Self::Int)
                .ok_or("invalid_key");
        }
        let bytes = wire
            .strip_prefix("s:")
            .and_then(|text| decode_hex(text, KEY_LIMIT))
            .ok_or("invalid_key")?;
        // PHP has already canonicalized valid integer-string array keys.
        if bytes.iter().all(|byte| b" \t\r\n\0\x0b".contains(byte))
            || canonical_int(&bytes).is_some()
        {
            return Err("invalid_key");
        }
        Ok(Self::Bytes(bytes))
    }

    fn heap_bytes(&self) -> usize {
        match self {
            Self::Int(_) => 0,
            Self::Bytes(bytes) => bytes.capacity(),
        }
    }
}

impl Stored {
    fn decode(value: WireValue) -> Result<Self> {
        Ok(match value {
            WireValue::Null {} => Self::Null,
            WireValue::Bool { value } => Self::Bool(value),
            WireValue::Int { value } => Self::Int(value),
            WireValue::Bytes { value } => {
                Self::Bytes(decode_hex(&value, VALUE_LIMIT).ok_or("invalid_value")?)
            }
        })
    }

    fn encode(&self) -> Value {
        match self {
            Self::Null => json!({"kind":"null"}),
            Self::Bool(value) => json!({"kind":"bool","value":value}),
            Self::Int(value) => json!({"kind":"int","value":value}),
            Self::Bytes(value) => json!({"kind":"bytes","value":encode_hex(value)}),
        }
    }

    fn heap_bytes(&self) -> usize {
        match self {
            Self::Bytes(bytes) => bytes.capacity(),
            _ => 0,
        }
    }

    fn counter(&self) -> Result<i64> {
        match self {
            Self::Int(value) => Ok(*value),
            Self::Bytes(bytes) => match canonical_int(bytes) {
                Some(value) => Ok(value),
                None if numeric_string(bytes) => Err("unsupported_counter_number"),
                None => Ok(0),
            },
            Self::Null | Self::Bool(_) => Ok(0),
        }
    }
}

// Recognize numeric string syntax only to refuse PHP float/overflow/coercion
// cases outside this profile. Nonnumeric strings start a counter from zero.
fn numeric_string(bytes: &[u8]) -> bool {
    fn php_space(byte: &u8) -> bool {
        b" \t\r\n\x0b\x0c".contains(byte)
    }
    fn unsigned(bytes: &[u8]) -> (usize, &[u8]) {
        let count = bytes
            .iter()
            .take_while(|byte| byte.is_ascii_digit())
            .count();
        (count, &bytes[count..])
    }
    fn strip_sign(bytes: &[u8]) -> &[u8] {
        if matches!(bytes.first(), Some(b'+' | b'-')) {
            &bytes[1..]
        } else {
            bytes
        }
    }
    let leading = bytes
        .iter()
        .position(|byte| !php_space(byte))
        .unwrap_or(bytes.len());
    let bytes = &bytes[leading..];
    let end = bytes
        .iter()
        .rposition(|byte| !php_space(byte))
        .map_or(0, |at| at + 1);
    let (integer, tail) = unsigned(strip_sign(&bytes[..end]));
    let (fraction, tail) = match tail.strip_prefix(b".") {
        Some(tail) => unsigned(tail),
        None => (0, tail),
    };
    if integer + fraction == 0 {
        return false;
    }
    match tail.first() {
        Some(b'e' | b'E') => {
            let (exponent, remaining) = unsigned(strip_sign(&tail[1..]));
            exponent > 0 && remaining.is_empty()
        }
        _ => tail.is_empty(),
    }
}

struct CacheEntry {
    value: Stored,
    lease: Reservation,
}

#[derive(Clone, Copy)]
enum WriteMode {
    Set,
    Add,
    Replace,
}

struct Cache {
    entries: BTreeMap<(Group, Key), CacheEntry>,
    budget: BudgetPool,
}

impl Cache {
    fn new(limit: usize) -> Self {
        Self {
            entries: BTreeMap::new(),
            budget: BudgetPool::new(limit),
        }
    }

    fn charge(key: &Key, value: &Stored) -> usize {
        ENTRY_ALLOWANCE
            + std::mem::size_of::<(Group, Key)>()
            + std::mem::size_of::<CacheEntry>()
            + key.heap_bytes()
            + value.heap_bytes()
    }

    fn write(
        &mut self,
        group: Group,
        key: String,
        value: WireValue,
        mode: WriteMode,
    ) -> Result<Value> {
        let key = Key::parse(&key)?;
        let value = Stored::decode(value)?;
        let charge = Self::charge(&key, &value);
        let changed = match self.entries.entry((group, key)) {
            Entry::Occupied(mut slot) if !matches!(mode, WriteMode::Add) => {
                let entry = slot.get_mut();
                entry.lease.try_resize(charge).map_err(|_| "cache_budget")?;
                entry.value = value;
                true
            }
            Entry::Vacant(slot) if !matches!(mode, WriteMode::Replace) => {
                let lease = self
                    .budget
                    .try_reserve(ResourceKind::ObjectCache, charge)
                    .map_err(|_| "cache_budget")?;
                slot.insert(CacheEntry { value, lease });
                true
            }
            _ => false,
        };
        Ok(changed_reply(changed))
    }

    fn adjust(&mut self, group: Group, key: String, offset: i64, subtract: bool) -> Result<Value> {
        if group != Group::Integer {
            return Err("counter_group");
        }
        let key = Key::parse(&key)?;
        let charge = Self::charge(&key, &Stored::Int(0));
        let Some(entry) = self.entries.get_mut(&(group, key)) else {
            return Ok(json!({"ok":true,"value":null}));
        };
        let before = entry.value.counter()?;
        let after = if subtract {
            before.checked_sub(offset)
        } else {
            before.checked_add(offset)
        }
        .ok_or("counter_overflow")?
        .max(0);
        entry.lease.try_resize(charge).map_err(|_| "cache_budget")?;
        entry.value = Stored::Int(after);
        Ok(json!({"ok":true,"value":after}))
    }

    fn execute(&mut self, request: Request) -> Result<Value> {
        match request {
            Request::Get { group, key } => {
                let entry = self.entries.get(&(group, Key::parse(&key)?));
                Ok(
                    json!({"ok":true,"found":entry.is_some(),"value":entry.map(|entry| entry.value.encode())}),
                )
            }
            Request::Delete { group, key } => Ok(changed_reply(
                self.entries.remove(&(group, Key::parse(&key)?)).is_some(),
            )),
            Request::Set { group, key, value } => self.write(group, key, value, WriteMode::Set),
            Request::Add { group, key, value } => self.write(group, key, value, WriteMode::Add),
            Request::Replace { group, key, value } => {
                self.write(group, key, value, WriteMode::Replace)
            }
            Request::Incr { group, key, offset } => self.adjust(group, key, offset, false),
            Request::Decr { group, key, offset } => self.adjust(group, key, offset, true),
            Request::FlushGroup { group } => {
                let before = self.entries.len();
                self.entries.retain(|(current, _), _| *current != group);
                Ok(changed_reply(before != self.entries.len()))
            }
            Request::Flush {} => {
                let changed = !self.entries.is_empty();
                self.entries.clear();
                Ok(changed_reply(changed))
            }
            Request::Finish {} => Err("finish_handling"),
        }
    }

    fn finish(&mut self, operations: usize) -> Result<Value> {
        self.entries.clear();
        let stats = self.budget.stats().map_err(|_| "cache_budget")?;
        let charged_bytes = stats.claimed_for(ResourceKind::ObjectCache);
        if charged_bytes != 0 {
            return Err("cache_cleanup");
        }
        Ok(
            json!({"ok":true,"operations":operations,"entries":self.entries.len(),"charged_bytes":charged_bytes}),
        )
    }
}

fn changed_reply(changed: bool) -> Value {
    json!({"ok":true,"changed":changed})
}

fn read_frame(input: &mut impl BufRead, frame: &mut Vec<u8>) -> Result<()> {
    frame.clear();
    loop {
        let buffer = input.fill_buf().map_err(|_| "read_failed")?;
        if buffer.is_empty() {
            return Err(if frame.is_empty() {
                "finish_required"
            } else {
                "truncated_frame"
            });
        }
        let end = buffer.iter().position(|byte| *byte == b'\n');
        let count = end.map_or(buffer.len(), |position| position + 1);
        if frame.len() + count > FRAME_LIMIT {
            return Err("frame_limit");
        }
        frame.extend_from_slice(&buffer[..count]);
        input.consume(count);
        if end.is_some() {
            frame.pop();
            return Ok(());
        }
    }
}

fn send(output: &mut impl Write, reply: &Value) -> Result<()> {
    let frame = serde_json::to_vec(reply).map_err(|_| "reply_encode")?;
    if frame.len() >= FRAME_LIMIT {
        return Err("reply_limit");
    }
    output
        .write_all(&frame)
        .and_then(|()| output.write_all(b"\n"))
        .and_then(|()| output.flush())
        .map_err(|_| "write_failed")
}

fn run(input: &mut impl BufRead, output: &mut impl Write) -> Result<()> {
    let mut cache = Cache::new(BUDGET);
    let mut frame = Vec::with_capacity(FRAME_LIMIT);
    let mut operations = 0;
    loop {
        read_frame(input, &mut frame)?;
        let request = serde_json::from_slice(&frame).map_err(|_| "invalid_request")?;
        if matches!(request, Request::Finish {}) {
            return send(output, &cache.finish(operations)?);
        }
        if operations == OPERATIONS_LIMIT {
            return Err("operation_limit");
        }
        let reply = cache.execute(request)?;
        operations += 1;
        send(output, &reply)?;
    }
}

fn main() {
    let mut output = io::stdout().lock();
    let result = if std::env::args_os().count() == 1 {
        run(&mut io::stdin().lock(), &mut output)
    } else {
        Err("invalid_arguments")
    };
    if let Err(error) = result {
        if error != "write_failed" {
            let _ = send(&mut output, &json!({"ok":false,"error":error}));
        }
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn request(cache: &mut Cache, json: Value) -> Result<Value> {
        cache.execute(serde_json::from_value(json).unwrap())
    }

    #[test]
    fn false_and_null_are_hits_and_group_flush_is_isolated() {
        let mut cache = Cache::new(BUDGET);
        for (group, value) in [
            ("rp-cache-multiple", json!({"kind":"null"})),
            ("rp-cache-integer", json!({"kind":"bool","value":false})),
        ] {
            assert_eq!(
                request(
                    &mut cache,
                    json!({"op":"add","group":group,"key":"i:1","value":value})
                ),
                Ok(changed_reply(true))
            );
            assert_eq!(
                request(&mut cache, json!({"op":"get","group":group,"key":"i:1"})),
                Ok(json!({"ok":true,"found":true,"value":value}))
            );
        }
        assert_eq!(
            request(
                &mut cache,
                json!({"op":"flush_group","group":"rp-cache-multiple"})
            ),
            Ok(changed_reply(true))
        );
        assert_eq!(
            request(
                &mut cache,
                json!({"op":"get","group":"rp-cache-multiple","key":"i:1"})
            ),
            Ok(json!({"ok":true,"found":false,"value":null}))
        );
        assert_eq!(cache.entries.len(), 1);
        assert_eq!(
            cache.finish(5),
            Ok(json!({"ok":true,"operations":5,"entries":0,"charged_bytes":0}))
        );
    }

    #[test]
    fn replacement_budget_failure_keeps_old_value_and_charge() {
        let group = Group::Multiple;
        let key = "s:61".to_owned();
        let limit = Cache::charge(&Key::parse(&key).unwrap(), &Stored::Null);
        let mut cache = Cache::new(limit);
        assert_eq!(
            cache.write(group, key.clone(), WireValue::Null {}, WriteMode::Set),
            Ok(changed_reply(true))
        );
        assert_eq!(
            cache.write(
                group,
                key.clone(),
                WireValue::Int { value: 5 },
                WriteMode::Add
            ),
            Ok(changed_reply(false))
        );
        assert_eq!(
            cache.write(
                group,
                key,
                WireValue::Bytes { value: "ff".into() },
                WriteMode::Replace
            ),
            Err("cache_budget")
        );
        let old = cache
            .entries
            .get(&(group, Key::parse("s:61").unwrap()))
            .unwrap();
        assert_eq!(old.value.encode(), json!({"kind":"null"}));
        assert_eq!(old.lease.bytes(), limit);
        assert_eq!(cache.finish(1).unwrap()["charged_bytes"], 0);
    }

    #[test]
    fn integer_corpus_and_overflow_boundaries() {
        let mut cache = Cache::new(BUDGET);
        let group = Group::Integer;
        let key = "s:636f756e746572".to_owned();
        assert_eq!(
            cache.adjust(group, key.clone(), 1, false),
            Ok(json!({"ok":true,"value":null}))
        );
        for (text, expected) in [(b"10".as_slice(), 12), (b"text", 2)] {
            cache
                .write(
                    group,
                    key.clone(),
                    WireValue::Bytes {
                        value: encode_hex(text),
                    },
                    WriteMode::Set,
                )
                .unwrap();
            assert_eq!(
                cache.adjust(group, key.clone(), 2, false),
                Ok(json!({"ok":true,"value":expected}))
            );
            assert_eq!(
                cache.adjust(group, key.clone(), 25, true),
                Ok(json!({"ok":true,"value":0}))
            );
        }
        cache
            .write(
                group,
                key.clone(),
                WireValue::Int { value: i64::MAX },
                WriteMode::Set,
            )
            .unwrap();
        assert_eq!(cache.adjust(group, key, 1, false), Err("counter_overflow"));
        for bytes in [
            b" 1.5".as_slice(),
            b"1e3",
            b"9223372036854775808",
            b"+5",
            b"01",
        ] {
            assert_eq!(
                Stored::Bytes(bytes.to_vec()).counter(),
                Err("unsupported_counter_number")
            );
        }
    }

    #[test]
    fn closed_requests_and_bounded_transport() {
        for invalid in [
            r#"{"op":"finish","extra":0}"#,
            r#"{"op":"get","op":"finish","group":"rp-cache-multiple","key":"i:1"}"#,
            r#"{"op":"get","group":"other","key":"i:1"}"#,
            r#"{"op":"get","group":"rp-cache-multiple","key":"i:1","key":"i:2"}"#,
            r#"{"op":"set","group":"rp-cache-multiple","key":"i:1","value":{"kind":"null","value":false}}"#,
        ] {
            assert!(serde_json::from_str::<Request>(invalid).is_err());
        }
        for invalid in ["i:01", "i:-0", "s:31", "s:0", "s:FF", "s:", "s:2000"] {
            assert!(Key::parse(invalid).is_err());
        }
        for valid in ["i:1", "s:3031", "s:2b31", "s:0031", "s:ff"] {
            assert!(Key::parse(valid).is_ok());
        }
        let mut output = Vec::new();
        assert_eq!(
            run(
                &mut Cursor::new(b"{\"op\":\"flush\"}\n{\"op\":\"finish\"}\n"),
                &mut output
            ),
            Ok(())
        );
        assert_eq!(output, b"{\"changed\":false,\"ok\":true}\n{\"charged_bytes\":0,\"entries\":0,\"ok\":true,\"operations\":1}\n");
        assert_eq!(
            run(&mut Cursor::new(b"{\"op\":\"finish\"}"), &mut Vec::new()),
            Err("truncated_frame")
        );
        assert_eq!(
            run(
                &mut Cursor::new(vec![b'x'; FRAME_LIMIT + 1]),
                &mut Vec::new()
            ),
            Err("frame_limit")
        );
        assert_eq!(
            run(&mut Cursor::new(b""), &mut Vec::new()),
            Err("finish_required")
        );
    }
}
