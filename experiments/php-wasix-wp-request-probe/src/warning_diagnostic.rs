//! Fixed PHP warning categories only. Unknown bytes and paths never leave here.
use serde_json::{Value, json};
use std::collections::BTreeMap;

#[derive(Clone, Copy)]
#[allow(dead_code)] // Each independent host uses one of the closed source labels.
pub enum SourceLabel {
    HomeBootstrap,
    FramingCommon,
}
impl SourceLabel {
    fn name(self) -> &'static str {
        match self {
            Self::HomeBootstrap => "home_bootstrap",
            Self::FramingCommon => "framing_common",
        }
    }
}
pub struct KnownSource {
    pub path: &'static str,
    pub label: SourceLabel,
    pub line_count: usize,
}
fn count(haystack: &[u8], needle: &[u8]) -> usize {
    haystack.windows(needle.len()).filter(|bytes| *bytes == needle).count()
}
fn source_lines(prefix: &[u8], start: &[u8], end: &[u8], maximum: usize) -> Value {
    let mut counts = BTreeMap::<usize, usize>::new();
    if maximum == 0 || maximum > 1024 { return json!([]); }
    for offset in 0..prefix.len().saturating_sub(start.len()).saturating_add(1) {
        if !prefix[offset..].starts_with(start) { continue; }
        let tail = &prefix[offset + start.len()..];
        let digits = tail.iter().take_while(|byte| byte.is_ascii_digit()).count();
        if digits == 0 || digits > 4 || tail[0] == b'0' || !tail[digits..].starts_with(end) { continue; }
        let line = tail[..digits].iter().fold(0usize, |line, digit| line * 10 + usize::from(digit - b'0'));
        if line <= maximum { *counts.entry(line).or_default() += 1; }
    }
    json!(counts.into_iter().map(|(line, count)| json!({"line":line,"count":count})).collect::<Vec<_>>())
}
pub fn classify(prefix: &[u8], known_sources: &[KnownSource]) -> Value {
    let bounded = &prefix[..prefix.len().min(4096)];
    let mut sources = serde_json::Map::new();
    for source in known_sources.iter().take(2) {
        let output_start = format!("output started at {}:", source.path);
        let warning_start = format!("in <b>{}</b> on line <b>", source.path);
        sources.insert(source.label.name().to_owned(), json!({
            "output_started_at":source_lines(bounded, output_start.as_bytes(), b")", source.line_count),
            "warning_at":source_lines(bounded, warning_start.as_bytes(), b"</b>", source.line_count)}));
    }
    json!({"prefix_bytes_examined":bounded.len(),"prefix_truncated":prefix.len()>bounded.len(),
        "message_counts":{
            "cannot_modify_headers_already_sent":count(bounded,b"Cannot modify header information - headers already sent"),
            "cannot_set_status_headers_already_sent":count(bounded,b"Cannot set response code - headers already sent")},
        "builtin_counts":{
            "header_remove":count(bounded,b"header_remove()"),
            "http_response_code":count(bounded,b"http_response_code()"),
            "header":count(bounded,b"header()")},
        "source_lines":sources})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixed_warning_categories_and_exact_known_source_lines_are_numeric_only() {
        let source = KnownSource { path: "/owned/bootstrap.php", label: SourceLabel::HomeBootstrap, line_count: 200 };
        let prefix = b"<br />\n<b>Warning</b>: http_response_code(): Cannot set response code - headers already sent (output started at /owned/bootstrap.php:149) in <b>/owned/bootstrap.php</b> on line <b>168</b>\n<b>Warning</b>: header(): Cannot modify header information - headers already sent (output started at /owned/bootstrap.php:149) in <b>/owned/bootstrap.php</b> on line <b>169</b>\n";
        let value = classify(prefix, &[source]);
        assert_eq!(value["message_counts"]["cannot_modify_headers_already_sent"], 1);
        assert_eq!(value["message_counts"]["cannot_set_status_headers_already_sent"], 1);
        assert_eq!(value["builtin_counts"], json!({"header_remove":0,"http_response_code":1,"header":1}));
        assert_eq!(value["source_lines"]["home_bootstrap"]["output_started_at"], json!([{"line":149,"count":2}]));
        assert_eq!(value["source_lines"]["home_bootstrap"]["warning_at"], json!([{"line":168,"count":1},{"line":169,"count":1}]));
        assert!(!value.to_string().contains("/owned") && !value.to_string().contains("Cannot modify"));
    }
    #[test]
    fn unknown_paths_messages_and_invalid_line_numbers_are_never_published() {
        let source = KnownSource { path: "/owned/common.php", label: SourceLabel::FramingCommon, line_count: 90 };
        let prefix = b"private-secret-SQL in <b>/private/unknown.php</b> on line <b>12</b> output started at /owned/common.php:91) output started at /owned/common.php:0) output started at /owned/common.php:01) output started at /owned/common.php:19private) output started at /owned/common.php.extra:20) in <b>/owned/common.php</b> on line <b>99999999999999999999</b> in <b>/owned/common.php</b> on line <b>90</b> header_remove()";
        let value = classify(prefix, &[source]);
        assert_eq!(value["source_lines"]["framing_common"]["output_started_at"], json!([]));
        assert_eq!(value["source_lines"]["framing_common"]["warning_at"], json!([{"line":90,"count":1}]));
        assert_eq!(value["builtin_counts"]["header_remove"], 1);
        assert_eq!(value["message_counts"], json!({"cannot_modify_headers_already_sent":0,"cannot_set_status_headers_already_sent":0}));
        for private in ["private-secret", "/private", "/owned", "unknown.php"] { assert!(!value.to_string().contains(private)); }
    }
    #[test]
    fn empty_invalid_and_oversize_prefixes_stay_bounded_and_match_only_fixed_bytes() {
        for prefix in [b"".as_slice(), b"\0\xff\x80".as_slice()] {
            let value = classify(prefix, &[]);
            assert_eq!(value["message_counts"], json!({"cannot_modify_headers_already_sent":0,"cannot_set_status_headers_already_sent":0}));
            assert_eq!(value["source_lines"], json!({}));
            assert_eq!(value["prefix_bytes_examined"], prefix.len());
        }
        let mut prefix = vec![b'x';4096]; prefix.extend_from_slice(b"header_remove()");
        let value = classify(&prefix, &[]);
        assert_eq!(value["prefix_bytes_examined"],4096);
        assert_eq!(value["prefix_truncated"],true);
        assert_eq!(value["builtin_counts"]["header_remove"],0);
    }
}
