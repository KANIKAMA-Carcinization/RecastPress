//! Selected PHP query-string grammar and WordPress public query variables.

use crate::{
    CompatibilityError,
    numbers::supported_integer,
    strings::decode_form_bytes,
    value::{PhpArray, PhpKey, PhpValue},
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PublicQueryKind {
    Home,
    Single,
    Page,
    Search,
    NotFound,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PublicQuery<'a> {
    pub kind: PublicQueryKind,
    pub post_id: u64,
    pub page_id: u64,
    pub paged: u64,
    pub is_paged: bool,
    pub search: &'a [u8],
}

/// Interpret the initial `WP_Query::parse_query` flags for an anonymous public
/// request with `show_on_front=posts` and no privacy-policy page. Input is the
/// query-variable array after request routing. Only `p`, `page_id`, `paged`,
/// `s` and the rewrite error are supported. Numeric strings are restricted to
/// the existing integer conversion profile. The result precedes `parse_query`
/// and `pre_get_posts` callbacks, SQL execution and missing-post resolution.
pub fn parse_public_query_vars(vars: &PhpArray) -> Result<PublicQuery<'_>, CompatibilityError> {
    for (key, _) in vars.iter() {
        if !matches!(key, PhpKey::Bytes(key) if matches!(key.as_slice(),
            b"p" | b"page_id" | b"paged" | b"s" | b"error"))
        {
            return Err(CompatibilityError::UnsupportedValue);
        }
    }

    let post = vars.get_bytes(b"p");
    let invalid_post = matches!(post, Some(PhpValue::Array(_)));
    let post = if invalid_post {
        0
    } else {
        query_integer(post)?
    };
    let page_id = query_absolute_integer(vars.get_bytes(b"page_id"))?;
    let paged = query_absolute_integer(vars.get_bytes(b"paged"))?;
    let search_var = vars.get_bytes(b"s");
    let search = match search_var {
        Some(PhpValue::Bytes(bytes)) if bytes.len() <= 1600 => bytes,
        None | Some(PhpValue::Null | PhpValue::Array(_) | PhpValue::Bytes(_)) => &[][..],
        _ => return Err(CompatibilityError::UnsupportedValue),
    };
    let rewrite_error = match vars.get_bytes(b"error") {
        None | Some(PhpValue::Null) => false,
        Some(PhpValue::Bytes(bytes)) if bytes.is_empty() => false,
        Some(PhpValue::Integer(404)) => true,
        Some(PhpValue::Bytes(bytes)) if bytes == b"404" => true,
        _ => return Err(CompatibilityError::UnsupportedValue),
    };
    let kind = if invalid_post || post < 0 || rewrite_error {
        PublicQueryKind::NotFound
    } else if post > 0 {
        PublicQueryKind::Single
    } else if page_id > 0 {
        PublicQueryKind::Page
    } else if search_var.is_some_and(|value| !matches!(value, PhpValue::Null)) {
        PublicQueryKind::Search
    } else {
        PublicQueryKind::Home
    };

    Ok(PublicQuery {
        kind,
        post_id: u64::try_from(post).unwrap_or(0),
        page_id,
        paged,
        is_paged: paged > 1 && kind != PublicQueryKind::NotFound,
        search,
    })
}

fn query_integer(value: Option<&PhpValue>) -> Result<i64, CompatibilityError> {
    value.map_or(Ok(0), supported_integer)
}

fn query_absolute_integer(value: Option<&PhpValue>) -> Result<u64, CompatibilityError> {
    let integer = match value {
        Some(PhpValue::Array(_)) => 0,
        other => query_integer(other)?,
    };
    integer
        .checked_abs()
        .map(|value| value as u64)
        .ok_or(CompatibilityError::UnsupportedValue)
}

/// PHP's default ampersand separator, at most 1,000 nonempty components, and
/// byte-string input only. Array notation, NUL in input/decoded keys, configurable
/// separators/limits and their diagnostics are explicitly unsupported.
pub fn parse_flat_query(input: &[u8]) -> Result<PhpArray, CompatibilityError> {
    parse_query(input, false)
}

/// Adds balanced bracket paths, empty-bracket append and at most 64 bracket
/// levels. Whitespace inside bracket components, malformed paths, NUL keys,
/// append overflow and PHP parser diagnostics remain explicitly unsupported.
pub fn parse_query_bytes(input: &[u8]) -> Result<PhpArray, CompatibilityError> {
    parse_query(input, true)
}

fn parse_query(input: &[u8], arrays: bool) -> Result<PhpArray, CompatibilityError> {
    if input.contains(&0) {
        return Err(CompatibilityError::UnsupportedValue);
    }
    let mut result = PhpArray::new();
    let mut count = 0;
    for part in input
        .split(|&byte| byte == b'&')
        .filter(|part| !part.is_empty())
    {
        count += 1;
        if count > 1000 {
            return Err(CompatibilityError::UnsupportedValue);
        }
        let split = part
            .iter()
            .position(|&byte| byte == b'=')
            .unwrap_or(part.len());
        let key = decode_form_bytes(&part[..split]);
        let Some(path) = key_path(&key, arrays)? else {
            continue;
        };
        let value = if split == part.len() {
            Vec::new()
        } else {
            decode_form_bytes(&part[split + 1..])
        };
        assign(&mut result, &path, PhpValue::Bytes(value))?;
    }
    Ok(result)
}

fn key_path(key: &[u8], arrays: bool) -> Result<Option<Vec<Option<PhpKey>>>, CompatibilityError> {
    if key.contains(&0) {
        return Err(CompatibilityError::UnsupportedValue);
    }
    let start = key
        .iter()
        .position(|&byte| byte != b' ')
        .unwrap_or(key.len());
    let key = &key[start..];
    let first = key
        .iter()
        .position(|&byte| byte == b'[')
        .unwrap_or(key.len());
    if key[..first].contains(&b']') {
        return Err(CompatibilityError::UnsupportedValue);
    }
    if first == 0 {
        return if key.is_empty() {
            Ok(None)
        } else {
            Err(CompatibilityError::UnsupportedValue)
        };
    }
    let base: Vec<_> = key[..first]
        .iter()
        .map(|&byte| {
            if matches!(byte, b'.' | b' ') {
                b'_'
            } else {
                byte
            }
        })
        .collect();
    let mut path = vec![Some(PhpKey::from_bytes(&base))];
    let mut position = first;
    while position < key.len() {
        if !arrays || key[position] != b'[' {
            return Err(CompatibilityError::UnsupportedValue);
        }
        let end = key[position + 1..]
            .iter()
            .position(|&byte| byte == b']')
            .map(|offset| position + 1 + offset)
            .ok_or(CompatibilityError::UnsupportedValue)?;
        let part = &key[position + 1..end];
        if part
            .iter()
            .any(|&byte| byte == b'[' || byte.is_ascii_whitespace())
        {
            return Err(CompatibilityError::UnsupportedValue);
        }
        path.push(if part.is_empty() {
            None
        } else {
            Some(PhpKey::from_bytes(part))
        });
        if path.len() > 65 {
            return Err(CompatibilityError::RecursionLimit);
        }
        position = end + 1;
    }
    Ok(Some(path))
}

fn assign(
    array: &mut PhpArray,
    path: &[Option<PhpKey>],
    value: PhpValue,
) -> Result<(), CompatibilityError> {
    let key = match &path[0] {
        Some(key) => key.clone(),
        None => {
            let maximum = array
                .iter()
                .filter_map(|(key, _)| match key {
                    PhpKey::Integer(value) => Some(*value),
                    _ => None,
                })
                .max();
            PhpKey::Integer(match maximum {
                None => 0,
                Some(maximum) => maximum
                    .checked_add(1)
                    .ok_or(CompatibilityError::UnsupportedValue)?,
            })
        }
    };
    if path.len() == 1 {
        array.insert(key, value);
        return Ok(());
    }
    if !matches!(array.get(&key), Some(PhpValue::Array(_))) {
        array.insert(key.clone(), PhpValue::Array(PhpArray::new()));
    }
    let Some(PhpValue::Array(child)) = array.get_mut(&key) else {
        unreachable!("initialized canonical array key")
    };
    assign(child, &path[1..], value)
}
