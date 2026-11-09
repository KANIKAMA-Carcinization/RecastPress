//! Experimental primitives derived from pinned WordPress 7.1.3 source.
//!
//! Source provenance and unimplemented domains are in compatibility/registry.json.
//! Unit tests are source-derived expectations, not a PHP differential test.
//! The value model is a supported subset, not a PHP interpreter or Rust ABI.

#[cfg(feature = "auth")]
pub mod auth_cookie;
pub mod auth_cookie_parse;
#[cfg(feature = "auth")]
pub mod auth_cookie_verify;
pub mod bad_protocol;
pub mod bad_protocol_once;
pub mod bad_protocol_outer;
pub mod charset;
pub mod colors;
pub mod continuation;
pub mod credential_input;
#[cfg(feature = "auth")]
pub mod credential_stage;
pub mod dates;
pub mod dispatcher;
pub mod email;
pub mod empty_filter_escaping;
pub mod entities;
pub mod entity_decode;
pub mod filtered;
pub mod form_attributes;
pub mod hooks;
pub mod hr_bytes;
pub mod html;
pub mod html_class;
pub mod html_replacement;
pub mod json_encode;
pub mod jsonp;
pub mod kses_slashes;
pub mod lists;
pub mod locale_mime;
pub mod media_type;
pub mod no_null;
pub mod numbers;
#[cfg(feature = "auth")]
pub mod password_dispatch;
#[cfg(feature = "auth")]
pub mod password_hash;
#[cfg(feature = "auth")]
pub mod password_legacy;
#[cfg(feature = "auth")]
pub mod password_rehash;
#[cfg(feature = "auth")]
pub mod password_verify;
pub mod post_meta_cached;
pub mod post_slug_truncate;
pub mod query;
pub mod remove_accents;
mod remove_accents_table;
pub mod request_media;
pub mod sanitize_text;
pub mod sanitize_title;
pub mod sanitize_user;
pub mod serialization;
pub mod specialchars;
pub mod strings;
pub mod strip_all_tags;
pub mod texturize;
pub mod title;
pub mod title_with_dashes;
pub mod unicode;
pub mod unique_ids;
pub mod url;
pub mod url_shorten;
pub mod utf8;
pub mod utf8_uri_encode;
pub mod uuid;
pub mod value;
pub mod zeroise;

#[cfg(feature = "auth")]
pub use auth_cookie::{
    AuthCookieError, AuthCookieLimits, PreparedAuthCookieUser, WpAuthCookie,
    wp_generate_auth_cookie_prepared_resolved_salt_empty_filters_bytes,
};
pub use auth_cookie_parse::{
    AuthCookieParseError, ParsedAuthCookie, wp_parse_auth_cookie_explicit_bytes,
};
#[cfg(feature = "auth")]
pub use auth_cookie_verify::{
    CookieRejectionStage, CookieSessionExpiration, CookieVerificationError,
    CookieVerificationLimits, CookieVerificationOutcome, PreparedCookieSession,
    PreparedCookieSessions, PreparedCookieVerificationUser, UnfinalizedCookieMatch,
    wp_validate_auth_cookie_prepared_get_non_ajax_resolved_salt_bytes,
};
pub use bad_protocol::{BadProtocolError, wp_kses_bad_protocol_once2_c_bytes};
pub use bad_protocol_once::{BadProtocolOnceError, wp_kses_bad_protocol_once_c_bytes};
pub use bad_protocol_outer::{BadProtocolOuterError, wp_kses_bad_protocol_c_bytes};
pub use charset::is_utf8_charset_bytes;
pub use colors::{
    maybe_hash_hex_color_bytes, sanitize_hex_color_bytes, sanitize_hex_color_no_hash_bytes,
};
pub use credential_input::{LoginLookupPlan, PreparedLoginInput};
#[cfg(feature = "auth")]
pub use credential_stage::{
    CredentialLookup, CredentialLookupKind, CredentialPhase, CredentialStage,
    CredentialStageOutcome, CredentialUnresolvedReason, EmailLookupRequest, LoginLookupRequest,
    RehashIntent, UnfinalizedCredentialMatch,
};
pub use dates::wp_checkdate_integers;
pub use email::is_email;
pub use empty_filter_escaping::{
    esc_attr_utf8_empty_filters_bytes, esc_html_utf8_empty_filters_bytes,
};
pub use entities::{
    EntityNormalizeError, wp_kses_normalize_entities_default_html_bytes,
    wp_kses_normalize_entities_default_xml_utf8_bytes,
};
pub use entity_decode::{EntityDecodeError, wp_kses_decode_entities_byte_values};
pub use filtered::{sanitize_key, wp_checkdate, wp_parse_args, wp_parse_str};
pub use form_attributes::{checked_return, disabled_return, selected_return, wp_readonly_return};
pub use hr_bytes::wp_convert_hr_to_bytes_default_units_bytes;
pub use html::wp_html_split_bytes;
pub use html_class::sanitize_html_class;
pub use html_replacement::wp_replace_in_html_tags_bytes;
pub use json_encode::{
    JsonEncodeError, JsonFlags, wp_json_encode, wp_json_encode_utf8_no_mbstring,
    wp_json_encode_with_flags,
};
pub use jsonp::wp_check_jsonp_callback;
pub use kses_slashes::{KsesSlashesError, wp_kses_stripslashes_bytes};
pub use lists::wp_list_pluck;
pub use lists::wp_parse_id_list;
pub use lists::wp_parse_id_list_integers;
pub use locale_mime::{sanitize_locale_name, sanitize_mime_type};
pub use media_type::wp_is_json_media_type_c_bytes;
pub use no_null::{NoNullError, SlashZeroMode, wp_kses_no_null_bytes};
pub use numbers::absint;
pub use numbers::absint_integer;
#[cfg(feature = "auth")]
pub use password_dispatch::{
    HostPasswordWorkLimits, PasswordDispatchError, wp_check_password_raw_empty_filters_bytes,
};
#[cfg(feature = "auth")]
pub use password_hash::{
    WpPasswordHash, WpPasswordHashError, WpPasswordHashOutcome,
    wp_hash_password_default_bcrypt_bytes,
};
#[cfg(feature = "auth")]
pub use password_legacy::{
    HostMaxPhpassCost, InvalidHostMaxPhpassCost, LegacyPasswordVerifyError,
    wp_check_password_legacy_md5_bytes, wp_check_password_phpass_portable_bytes,
};
#[cfg(feature = "auth")]
pub use password_rehash::wp_password_needs_rehash_default_bcrypt_bytes;
#[cfg(feature = "auth")]
pub use password_verify::{
    HostMaxBcryptCost, InvalidHostMaxBcryptCost, WpBcryptVerifyError,
    wp_check_password_wp_bcrypt_bytes,
};
pub use post_meta_cached::{
    PostMetaCachedError, PreparedPostMetaCache, get_post_meta_single_cached_no_filters,
};
pub use post_slug_truncate::{
    TruncatePostSlugError, truncate_post_slug_conditional_decoded_utf8_bytes,
};
pub use remove_accents::{RemoveAccentsError, remove_accents_utf8_explicit_locale_bytes};
pub use request_media::{wp_is_json_request_c_bytes, wp_is_xml_request_bytes};
pub use sanitize_text::{
    SanitizeTextError, SanitizeTextLimits,
    sanitize_text_field_utf8_site_no_open_angle_empty_filters_bytes,
    sanitize_textarea_field_utf8_site_no_open_angle_empty_filters_bytes,
};
pub use sanitize_title::{
    SanitizeTitleContext, SanitizeTitleError,
    sanitize_title_utf8_default_callback_explicit_locale_bytes,
};
pub use sanitize_user::{SanitizeUserError, sanitize_user_utf8_no_open_angle_empty_filters_bytes};
pub use serialization::{is_serialized, is_serialized_string, maybe_serialize, maybe_unserialize};
pub use specialchars::{SpecialCharsError, SpecialCharsQuoteStyle, wp_specialchars_utf8_bytes};
pub use strings::{
    normalize_whitespace_bytes, trailingslashit_bytes, untrailingslashit_bytes, wp_basename_bytes,
    wp_normalize_path_bytes, wp_slash, wp_unslash,
};
pub use strip_all_tags::{StripAllTagsError, wp_strip_all_tags_no_open_angle_bytes};
pub use texturize::{TexturizeError, TexturizeLimits, wptexturize_default_english_no_html_bytes};
pub use title::{
    PreparedTitlePost, TitleFormatError,
    get_the_title_prepared_frontend_default_english_no_html_bytes,
};
pub use title_with_dashes::{
    TitleDashContext, TitleDashError,
    sanitize_title_with_dashes_utf8_no_open_angle_no_mbstring_bytes,
};
pub use unicode::valid_unicode_i64;
pub use unique_ids::UniqueIds;
pub use url::wp_make_link_relative_bytes;
pub use url_shorten::url_shorten_bytes;
pub use utf8::{
    wp_check_invalid_utf8_utf8_no_mbstring_bytes, wp_has_noncharacters_bytes,
    wp_is_valid_utf8_bytes, wp_scrub_utf8_bytes,
};
pub use utf8_uri_encode::{Utf8UriEncodeError, utf8_uri_encode_utf8_bytes};
pub use uuid::wp_is_uuid;
pub use zeroise::{ZeroiseError, zeroise};

use value::{PhpArray, PhpKey, PhpValue};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CompatibilityError {
    UnsupportedValue,
    /// A host safety limit, not a WordPress/PHP equivalence claim.
    RecursionLimit,
}

/// WordPress wp_validate_boolean for supported values. Unlike parsing a textual
/// boolean, "off", "no", and whitespace are true; only case-insensitive "false"
/// receives the special string treatment before the PHP boolean cast.
pub fn wp_validate_boolean(value: &PhpValue) -> Result<bool, CompatibilityError> {
    match value {
        PhpValue::Null => Ok(false),
        PhpValue::Bool(value) => Ok(*value),
        PhpValue::Integer(value) => Ok(*value != 0),
        PhpValue::Float(value) if value.is_nan() => Err(CompatibilityError::UnsupportedValue),
        PhpValue::Float(value) => Ok(*value != 0.0),
        PhpValue::Bytes(value) => {
            Ok(!value.is_empty() && value != b"0" && !value.eq_ignore_ascii_case(b"false"))
        }
        PhpValue::Array(value) => Ok(!value.is_empty()),
        PhpValue::Unsupported => Err(CompatibilityError::UnsupportedValue),
    }
}

/// WordPress numeric-array detection accepts holes, negative keys, and an empty
/// array. It is not PHP array_is_list, which requires consecutive keys from zero.
pub fn wp_is_numeric_array(value: &PhpValue) -> bool {
    match value {
        PhpValue::Array(array) => array
            .iter()
            .all(|(key, _)| matches!(key, PhpKey::Integer(_))),
        _ => false,
    }
}

/// The string-input kernel of sanitize_key, including its filter call.
/// Non-string scalar coercion is not implemented; floats require PHP precision
/// and formatting contracts. Arbitrary PHP bytes are accepted without UTF-8 loss.
pub fn sanitize_key_bytes<T>(key: &[u8], filter: impl FnOnce(Vec<u8>, &[u8]) -> T) -> T {
    let sanitized = key
        .iter()
        .copied()
        .map(|b| b.to_ascii_lowercase())
        .filter(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'_' || *b == b'-')
        .collect();
    filter(sanitized, key)
}

/// wp_array_slice_assoc for integer/byte-string keys and modeled array values.
/// Requested key order is retained, duplicate keys keep their first position,
/// and missing or Null entries are omitted (the source uses PHP isset).
/// Boolean/float/object/reference key coercions and PHP references are pending.
pub fn wp_array_slice_assoc(
    input: &PhpArray,
    keys: &[PhpKey],
) -> Result<PhpArray, CompatibilityError> {
    let mut result = PhpArray::new();
    for key in keys {
        match input.get(key) {
            None | Some(PhpValue::Null) => {}
            Some(PhpValue::Unsupported) => return Err(CompatibilityError::UnsupportedValue),
            Some(value) => {
                result.insert(key.clone(), value.clone());
            }
        }
    }
    Ok(result)
}

/// wp_parse_list for byte strings, booleans, signed integers and modeled arrays.
/// The source regex has no
/// Unicode mode: only ASCII whitespace and commas delimit strings. Array input
/// keeps scalar values and original keys, rather than becoming a numbered list.
/// Root Null emits a PHP 8.5 E_DEPRECATED diagnostic in preg_split and remains
/// unsupported, as do root float formatting and PHP reference/object behavior.
pub fn wp_parse_list(input: &PhpValue) -> Result<PhpArray, CompatibilityError> {
    let mut result = PhpArray::new();
    match input {
        PhpValue::Bool(false) => {}
        PhpValue::Bool(true) => {
            result.insert(PhpKey::Integer(0), PhpValue::Bytes(b"1".to_vec()));
        }
        PhpValue::Integer(value) => {
            result.insert(
                PhpKey::Integer(0),
                PhpValue::Bytes(value.to_string().into_bytes()),
            );
        }
        PhpValue::Bytes(bytes) => {
            for (index, part) in bytes
                .split(|byte| matches!(byte, b'\t'..=b'\r' | b' ' | b','))
                .filter(|part| !part.is_empty())
                .enumerate()
            {
                let index =
                    i64::try_from(index).map_err(|_| CompatibilityError::UnsupportedValue)?;
                result.insert(PhpKey::Integer(index), PhpValue::Bytes(part.to_vec()));
            }
        }
        PhpValue::Array(array) => {
            for (key, value) in array.iter() {
                match value {
                    PhpValue::Null | PhpValue::Array(_) => {}
                    PhpValue::Unsupported => return Err(CompatibilityError::UnsupportedValue),
                    value => {
                        result.insert(key.clone(), value.clone());
                    }
                }
            }
        }
        _ => return Err(CompatibilityError::UnsupportedValue),
    }
    Ok(result)
}

/// Array-input kernel of wp_parse_args. Nonempty defaults trigger PHP
/// array_merge: integer keys append and are renumbered; string keys overwrite
/// without moving their original position. Empty defaults preserve args keys.
/// String query parsing, object properties, and reference semantics are pending.
pub fn wp_parse_args_arrays(
    args: &PhpArray,
    defaults: &PhpArray,
) -> Result<PhpArray, CompatibilityError> {
    if args
        .iter()
        .chain(defaults.iter())
        .any(|(_, value)| matches!(value, PhpValue::Unsupported))
    {
        return Err(CompatibilityError::UnsupportedValue);
    }
    if defaults.is_empty() {
        return Ok(args.clone());
    }
    let mut result = PhpArray::new();
    let mut next_integer = 0_i64;
    for (key, value) in defaults.iter().chain(args.iter()) {
        let key = match key {
            PhpKey::Integer(_) => {
                let key = PhpKey::Integer(next_integer);
                next_integer = next_integer
                    .checked_add(1)
                    .ok_or(CompatibilityError::UnsupportedValue)?;
                key
            }
            key => key.clone(),
        };
        result.insert(key, value.clone());
    }
    Ok(result)
}
