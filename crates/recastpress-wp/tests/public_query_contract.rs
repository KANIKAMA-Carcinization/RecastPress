use recastpress_wp::{
    CompatibilityError,
    query::{PublicQueryKind, parse_public_query_vars, parse_query_bytes},
    value::{PhpKey, PhpValue},
};

#[test]
fn public_route_flags_and_variables_match_wordpress_7_1_3() {
    use PublicQueryKind::{Home, NotFound, Page, Search, Single};

    // Captured at the end of WP_Query::parse_query with the latest-posts option.
    let cases = [
        ("", Home, 0, 0, 0, false, ""),
        ("p=12", Single, 12, 0, 0, false, ""),
        ("page_id=-7", Page, 0, 7, 0, false, ""),
        ("s=", Search, 0, 0, 0, false, ""),
        ("s=needle&paged=-3", Search, 0, 0, 3, true, "needle"),
        ("paged=1", Home, 0, 0, 1, false, ""),
        ("paged=2", Home, 0, 0, 2, true, ""),
        ("p=-2&paged=8", NotFound, 0, 0, 8, false, ""),
        ("p=4&error=404", NotFound, 4, 0, 0, false, ""),
        ("p[]=20", NotFound, 0, 0, 0, false, ""),
        ("s[]=needle", Search, 0, 0, 0, false, ""),
        ("p=3&page_id=4&s=needle", Single, 3, 4, 0, false, "needle"),
        ("page_id[]=7", Home, 0, 0, 0, false, ""),
    ];
    for (input, kind, post, page, paged, is_paged, search) in cases {
        let vars = parse_query_bytes(input.as_bytes()).unwrap();
        let actual = parse_public_query_vars(&vars).unwrap();
        assert_eq!(
            (
                actual.kind,
                actual.post_id,
                actual.page_id,
                actual.paged,
                actual.is_paged,
                actual.search
            ),
            (kind, post, page, paged, is_paged, search.as_bytes()),
            "{input}"
        );
    }
}

#[test]
fn null_defaults_and_search_limits_preserve_the_original_flag() {
    let mut vars = parse_query_bytes(b"").unwrap();
    for name in [b"p".as_slice(), b"s"] {
        vars.insert(PhpKey::from_bytes(name), PhpValue::Null);
    }
    assert_eq!(
        parse_public_query_vars(&vars).unwrap().kind,
        PublicQueryKind::Home
    );
    for (size, expected_size) in [(1600, 1600), (1601, 0)] {
        vars.insert(PhpKey::from_bytes(b"s"), PhpValue::Bytes(vec![b'x'; size]));
        let query = parse_public_query_vars(&vars).unwrap();
        assert_eq!(query.kind, PublicQueryKind::Search);
        assert_eq!(query.search.len(), expected_size);
    }
}

#[test]
fn unsupported_query_variables_and_coercions_are_reported() {
    for input in [
        "preview=1",
        "page_id=9223372036854775808",
        "p=1.5",
        "1=query",
    ] {
        let vars = parse_query_bytes(input.as_bytes()).unwrap();
        assert_eq!(
            parse_public_query_vars(&vars),
            Err(CompatibilityError::UnsupportedValue)
        );
    }
}
