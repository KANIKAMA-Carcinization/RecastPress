//! Development-only PHP value wire codec, not the production Wasm ABI.
use recastpress_wp::value::{PhpArray, PhpKey, PhpValue};
use serde_json::{Value, json};

pub fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("missing string {key}"))
}

fn bytes(value: &Value) -> Result<Vec<u8>, String> {
    let hex = text(value, "hex")?;
    if hex.len() % 2 != 0 || !hex.is_ascii() {
        return Err("invalid byte hex".into());
    }
    (0..hex.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).map_err(|_| "invalid byte hex".into()))
        .collect()
}

pub fn decode(value: &Value) -> Result<PhpValue, String> {
    match text(value, "type")? {
        "null" => Ok(PhpValue::Null),
        "bool" => value
            .get("value")
            .and_then(Value::as_bool)
            .map(PhpValue::Bool)
            .ok_or_else(|| "invalid bool".into()),
        "int" => Ok(PhpValue::Integer(
            text(value, "value")?.parse().map_err(|_| "invalid int")?,
        )),
        "float" => Ok(PhpValue::Float(match text(value, "value")? {
            "nan" => f64::NAN,
            "inf" => f64::INFINITY,
            "-inf" => f64::NEG_INFINITY,
            s => s.parse().map_err(|_| "invalid float")?,
        })),
        "string" => Ok(PhpValue::Bytes(bytes(value)?)),
        "array" => {
            let mut array = PhpArray::new();
            for entry in value
                .get("entries")
                .and_then(Value::as_array)
                .ok_or("invalid array")?
            {
                let key = match text(&entry["key"], "type")? {
                    "int" => PhpKey::Integer(
                        text(&entry["key"], "value")?
                            .parse()
                            .map_err(|_| "invalid int key")?,
                    ),
                    "string" => PhpKey::from_bytes(&bytes(&entry["key"])?),
                    _ => return Err("unsupported key".into()),
                };
                array.insert(key, decode(&entry["value"])?);
            }
            Ok(PhpValue::Array(array))
        }
        _ => Err("unsupported value".into()),
    }
}

pub fn encode(value: &PhpValue) -> Result<Value, String> {
    Ok(match value {
        PhpValue::Null => json!({"type": "null"}),
        PhpValue::Bool(value) => json!({"type": "bool", "value": value}),
        PhpValue::Integer(value) => json!({"type": "int", "value": value.to_string()}),
        PhpValue::Float(value) => {
            let bits = if value.is_nan() {
                "nan".into()
            } else {
                format!("{:016x}", value.to_bits())
            };
            json!({"type": "float", "bits": bits})
        }
        PhpValue::Bytes(value) => {
            json!({"type": "string", "hex": value.iter().map(|byte| format!("{byte:02x}")).collect::<String>()})
        }
        PhpValue::Array(array) => {
            let entries: Result<Vec<_>, String> = array
                .iter()
                .map(|(key, value)| {
                    let key = match key {
                        PhpKey::Integer(value) => encode(&PhpValue::Integer(*value))?,
                        PhpKey::Bytes(value) => encode(&PhpValue::Bytes(value.clone()))?,
                    };
                    Ok(json!({"key": key, "value": encode(value)?}))
                })
                .collect();
            json!({"type": "array", "entries": entries?})
        }
        PhpValue::Resource { .. } => return Err("cannot encode resource value".into()),
        PhpValue::ArgumentHandle(_) => {
            return Err("cannot encode opaque argument handle".into());
        }
        PhpValue::Unsupported => return Err("cannot encode unsupported value".into()),
    })
}
