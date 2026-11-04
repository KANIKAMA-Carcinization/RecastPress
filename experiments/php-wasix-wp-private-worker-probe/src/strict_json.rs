//! Recursive duplicate-key rejection before producing a serde_json::Value.
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Number, Value};
use std::fmt;

pub(crate) struct Unique(pub(crate) Value);
impl<'de> Deserialize<'de> for Unique {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct StrictVisitor;
        impl<'de> Visitor<'de> for StrictVisitor {
            type Value = Unique;
            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("duplicate-free JSON")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> Result<Unique, E> { Ok(Unique(Value::Bool(v))) }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Unique, E> { Ok(Unique(Value::Number(v.into()))) }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Unique, E> { Ok(Unique(Value::Number(v.into()))) }
            fn visit_f64<E: de::Error>(self, v: f64) -> Result<Unique, E> {
                Number::from_f64(v).map(|n| Unique(Value::Number(n))).ok_or_else(|| E::custom("json_number"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Unique, E> { Ok(Unique(Value::String(v.into()))) }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Unique, E> { Ok(Unique(Value::String(v))) }
            fn visit_none<E: de::Error>(self) -> Result<Unique, E> { Ok(Unique(Value::Null)) }
            fn visit_unit<E: de::Error>(self) -> Result<Unique, E> { Ok(Unique(Value::Null)) }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Unique, A::Error> {
                let mut values = Vec::new();
                while let Some(Unique(value)) = seq.next_element::<Unique>()? { values.push(value); }
                Ok(Unique(Value::Array(values)))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Unique, A::Error> {
                let mut values = Map::new();
                while let Some(key) = map.next_key::<String>()? {
                    if values.contains_key(&key) { return Err(de::Error::custom("duplicate_json_key")); }
                    let Unique(value) = map.next_value::<Unique>()?;
                    values.insert(key, value);
                }
                Ok(Unique(Value::Object(values)))
            }
        }
        deserializer.deserialize_any(StrictVisitor)
    }
}

pub(crate) fn parse(wire: &[u8]) -> Result<Value, &'static str> {
    serde_json::from_slice::<Unique>(wire).map(|value| value.0).map_err(|_| "php_json_shape")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn recursive_duplicate_keys_and_trailing_json_fail_before_value() {
        for wire in [r#"{"a":1,"a":2}"#, r#"{"response":{"status":200,"status":404}}"#,
            r#"{"array":[{"private-key":1,"private-key":2}]}"#, r#"{"a":1,"\u0061":2}"#,
            r#"{} {}"#] { assert_eq!(parse(wire.as_bytes()).unwrap_err(), "php_json_shape"); }
    }
    #[test]
    fn unique_nested_values_preserve_private_bytes_without_error_text() {
        let value = parse(br#"{"array":[null,true,-1,2,3.5,{"private":"opaque\u0000value"}]}"#).unwrap();
        assert_eq!(value["array"][5]["private"], "opaque\0value");
        assert_eq!(parse(b"private-secret-malformed").unwrap_err(), "php_json_shape");
    }
}
