//! Experimental HTTP adapter. Public output is sealed at process startup.
//! No PHP runtime, editable database, or management mutation route is linked here.

pub mod bundle;
pub mod bundle_receipt;
mod byte_ranges;
pub mod cgroup_membership_observation;
pub mod cgroup_memory_observation;
pub mod cli;
mod conditional_requests;
pub mod feeds;
pub mod http_cache;
pub mod memory_diagnostic;
pub mod memory_observation;
pub mod package_zip;
pub mod prepared_package;
pub mod prepared_posts;
pub mod process_memory_observation;
mod representation_tags;
pub mod wordpress_release;

use axum::{
    Router,
    body::{Body, Bytes},
    extract::{Request, State},
    http::{HeaderValue, Method, StatusCode, header},
    response::{IntoResponse, Response},
    routing::get,
};
use recastpress_core::publication::{
    MAX_TARGET_BYTES, PreparedSite, PublicReader, ReadError, publication_channel,
};
use recastpress_core::resource_budget::BudgetPool;
use std::{collections::BTreeMap, sync::Arc};

#[derive(Clone)]
struct PublicState {
    reader: PublicReader,
    host: String,
    ready: bool,
    cache: http_cache::ResponseCache,
    // Fixed with the same sealed reader. Keys/tag headers/Arc metadata are
    // unpooled, and no request recomputes a representation digest.
    tags: Arc<BTreeMap<String, representation_tags::RepresentationTag>>,
}

/// Seals a startup generation by dropping the only Publisher before HTTP starts.
/// Live publishing is intentionally unavailable until transport invalidation is
/// implemented; constructing a Body is not proof that its bytes have been sent.
pub fn public_router(site: Option<PreparedSite>, host: &str) -> Result<Router, &'static str> {
    public_router_with_cache(site, host).map(|(router, _)| router)
}

/// Returns a separate internal control capability for a future trusted allocator
/// and monitor. No monitor is presumed healthy and no memory budget is guessed.
pub fn public_router_with_cache(
    site: Option<PreparedSite>,
    host: &str,
) -> Result<(Router, http_cache::PublicCacheControl), &'static str> {
    public_router_with_cache_channel(site, host, http_cache::channel)
}

/// Opt-in logical shared-pool cache for the sealed startup generation only.
/// It starts suspended, just like the legacy constructors. Local allocation and
/// monitor reconciliation must still be supplied explicitly. The caller keeps
/// a pool clone to adjust its shared limit; control changes only local capacity.
/// Cache refusal falls back to an unpooled shared sealed-body owner without a
/// cache lease, not request denial.
/// Retained Body/Bytes owners preserve leases but cannot authorize freshness or
/// revoke bytes already handed to transport after monitor loss. Empty-body and
/// empty-slice optimizations can release owners early because no data remains.
pub fn public_router_with_budgeted_cache(
    site: Option<PreparedSite>,
    host: &str,
    pool: BudgetPool,
) -> Result<(Router, http_cache::PublicCacheControl), &'static str> {
    public_router_with_cache_channel(site, host, || http_cache::budgeted_channel(pool))
}

fn public_router_with_cache_channel(
    site: Option<PreparedSite>,
    host: &str,
    channel: impl FnOnce() -> (http_cache::ResponseCache, http_cache::PublicCacheControl),
) -> Result<(Router, http_cache::PublicCacheControl), &'static str> {
    let authority: axum::http::uri::Authority = host.parse().map_err(|_| "invalid public host")?;
    if host.contains('@')
        || host.is_empty()
        || authority.host().is_empty()
        || host
            .bytes()
            .any(|b| !b.is_ascii() || b.is_ascii_whitespace())
    {
        return Err("invalid public host");
    }
    let suffix = &host[authority.host().len()..];
    if !suffix.is_empty()
        && !suffix.strip_prefix(':').is_some_and(|port| {
            !port.is_empty()
                && port.bytes().all(|b| b.is_ascii_digit())
                && port.parse::<u16>().is_ok()
        })
    {
        return Err("invalid public host port");
    }
    let (publisher, reader) = publication_channel();
    let ready = site.is_some();
    let mut tags = BTreeMap::new();
    if let Some(site) = site {
        if site.targets().any(|target| {
            matches!(
                target.split('?').next(),
                Some("/health/live" | "/health/ready")
            )
        }) {
            return Err("public page conflicts with reserved health route");
        }
        let targets = site.targets().map(str::to_owned).collect::<Vec<_>>();
        let base = publisher
            .revision()
            .map_err(|_| "publication unavailable")?;
        publisher
            .publish(&base, site)
            .map_err(|_| "publication failed")?;
        for target in targets {
            let output = reader
                .read(&target)
                .map_err(|_| "validator source unavailable")?;
            if output.page().status() == 200 {
                tags.insert(
                    target,
                    representation_tags::RepresentationTag::from_page(output.page()),
                );
            }
        }
    }
    drop(publisher);
    let (cache, control) = channel();
    let router = Router::new()
        .route(
            "/health/live",
            get(|| async { plain(StatusCode::OK, "live\n") }),
        )
        .route("/health/ready", get(public_ready))
        .fallback(public_request)
        .with_state(PublicState {
            reader,
            host: host.to_ascii_lowercase(),
            ready,
            cache,
            tags: Arc::new(tags),
        });
    Ok((protect_router(router), control))
}

/// Management mutations stay unavailable until authentication is implemented.
pub fn management_router() -> Router {
    protect_router(
        Router::new()
            .route(
                "/health/live",
                get(|| async { plain(StatusCode::OK, "live\n") }),
            )
            .route(
                "/health/ready",
                get(|| async {
                    plain(
                        StatusCode::SERVICE_UNAVAILABLE,
                        "management authentication is not configured\n",
                    )
                }),
            )
            .fallback(|| async { plain(StatusCode::NOT_FOUND, "not found\n") }),
    )
}

async fn public_ready(State(state): State<PublicState>) -> Response {
    if state.ready {
        plain(StatusCode::OK, "ready\n")
    } else {
        plain(StatusCode::SERVICE_UNAVAILABLE, "no public generation\n")
    }
}

async fn public_request(State(state): State<PublicState>, request: Request) -> Response {
    if request.method() != Method::GET && request.method() != Method::HEAD {
        let mut response = plain(StatusCode::METHOD_NOT_ALLOWED, "method not allowed\n");
        response
            .headers_mut()
            .insert(header::ALLOW, HeaderValue::from_static("GET, HEAD"));
        return response;
    }
    let hosts = request.headers().get_all(header::HOST);
    let mut values = hosts.iter();
    let valid_host = values
        .next()
        .and_then(|h| h.to_str().ok())
        .is_some_and(|h| h.eq_ignore_ascii_case(&state.host))
        && values.next().is_none();
    if !valid_host {
        return plain(StatusCode::MISDIRECTED_REQUEST, "unrecognized host\n");
    }
    if request.uri().scheme().is_some() || request.uri().authority().is_some() {
        return plain(StatusCode::BAD_REQUEST, "origin-form target required\n");
    }
    let target = request.uri().path_and_query().map_or("/", |p| p.as_str());
    if target.len() > MAX_TARGET_BYTES {
        return plain(StatusCode::URI_TOO_LONG, "request target too long\n");
    }
    match state.reader.read(target) {
        Ok(output) => {
            let page = output.page();
            let tag = if page.status() == 200 {
                let Some(tag) = state.tags.get(target) else {
                    return plain(
                        StatusCode::SERVICE_UNAVAILABLE,
                        "public validator unavailable\n",
                    );
                };
                Some(tag)
            } else {
                None
            };
            let mut allow_range = false;
            if let Some(tag) = tag {
                match conditional_requests::evaluate(
                    request.headers(),
                    request.method(),
                    tag.opaque(),
                ) {
                    conditional_requests::Decision::Proceed {
                        allow_range: allowed,
                    } => allow_range = allowed,
                    conditional_requests::Decision::NotModified => {
                        return not_modified(tag, page.body().len());
                    }
                    conditional_requests::Decision::Failed => {
                        return empty_status(StatusCode::PRECONDITION_FAILED);
                    }
                    conditional_requests::Decision::BadRequest => {
                        return empty_status(StatusCode::BAD_REQUEST);
                    }
                    conditional_requests::Decision::HeaderTooLarge => {
                        return empty_status(StatusCode::REQUEST_HEADER_FIELDS_TOO_LARGE);
                    }
                }
            }
            let plan = byte_ranges::select(
                request.headers(),
                request.method(),
                page.status(),
                page.body().len(),
                allow_range,
            );
            let (body, status, length, content_range, content_type) = match plan {
                byte_ranges::Plan::Partial { start, end } => (
                    // Share the sealed payload owner without a cache lease.
                    // Offsets still address raw representation bytes. Owner
                    // metadata/transport allocations and copies remain unpooled.
                    Body::from(Bytes::from_owner(page.shared_body()).slice(start..end)),
                    StatusCode::PARTIAL_CONTENT,
                    end - start,
                    Some(format!("bytes {start}-{}/{}", end - 1, page.body().len())),
                    page.content_type(),
                ),
                byte_ranges::Plan::Unsatisfiable => (
                    Body::empty(),
                    StatusCode::RANGE_NOT_SATISFIABLE,
                    0,
                    Some(format!("bytes */{}", page.body().len())),
                    "text/plain; charset=utf-8",
                ),
                byte_ranges::Plan::Full => (
                    if request.method() == Method::HEAD || page.location().is_some() {
                        Body::empty()
                    } else {
                        state.cache.body(&request, &state.host, page)
                    },
                    StatusCode::from_u16(page.status()).expect("validated page status"),
                    page.body().len(),
                    None,
                    page.content_type(),
                ),
            };
            let mut response = Response::new(body);
            *response.status_mut() = status;
            response.headers_mut().insert(
                header::CONTENT_TYPE,
                HeaderValue::from_str(content_type).expect("validated page content type"),
            );
            response
                .headers_mut()
                .insert(header::CONTENT_LENGTH, HeaderValue::from(length));
            if let Some(content_range) = content_range {
                response.headers_mut().insert(
                    header::CONTENT_RANGE,
                    HeaderValue::from_str(&content_range).expect("numeric byte range"),
                );
            }
            if matches!(status, StatusCode::OK | StatusCode::PARTIAL_CONTENT)
                && let Some(tag) = tag
            {
                response.headers_mut().insert(header::ETAG, tag.header());
            }
            if let Some(location) = page.location() {
                response.headers_mut().insert(
                    header::LOCATION,
                    HeaderValue::from_str(location).expect("validated ASCII origin-form Location"),
                );
            }
            response
        }
        Err(ReadError::NotFound) => plain(StatusCode::NOT_FOUND, "not found\n"),
        Err(ReadError::InvalidTarget) => plain(StatusCode::BAD_REQUEST, "invalid request target\n"),
        Err(_) => plain(
            StatusCode::SERVICE_UNAVAILABLE,
            "public generation unavailable\n",
        ),
    }
}

fn plain(status: StatusCode, message: &'static str) -> Response {
    (status, message).into_response()
}

fn empty_status(status: StatusCode) -> Response {
    let mut response = Response::new(Body::empty());
    *response.status_mut() = status;
    response
        .headers_mut()
        .insert(header::CONTENT_LENGTH, HeaderValue::from_static("0"));
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/plain; charset=utf-8"),
    );
    response
}

fn not_modified(tag: &representation_tags::RepresentationTag, full_length: usize) -> Response {
    let mut response = Response::new(Body::empty());
    *response.status_mut() = StatusCode::NOT_MODIFIED;
    response.headers_mut().insert(header::ETAG, tag.header());
    // RFC9110 permits the original 200 length, not the empty 304 body length.
    // It must be explicit: locked Axum otherwise inserts Content-Length: 0.
    response
        .headers_mut()
        .insert(header::CONTENT_LENGTH, HeaderValue::from(full_length));
    response
}

fn protect(mut response: Response) -> Response {
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response.headers_mut().insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    response
}

fn protect_router(router: Router) -> Router {
    // Covers responses produced by routing itself, including health-route 405s.
    router.layer(axum::middleware::map_response(
        |response: Response| async move { protect(response) },
    ))
}
