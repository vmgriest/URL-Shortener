from prometheus_client import Counter, Histogram, REGISTRY, CONTENT_TYPE_LATEST, generate_latest
from fastapi import Response

# --- Counters ---
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

cache_hits_total = Counter(
    "cache_hits_total",
    "Total Redis cache hits",
    ["operation"],
)

cache_misses_total = Counter(
    "cache_misses_total",
    "Total Redis cache misses",
    ["operation"],
)

urls_created_total = Counter(
    "urls_created_total",
    "Total number of short URLs created",
)

rate_limit_hits_total = Counter(
    "rate_limit_hits_total",
    "Total requests blocked by rate limiter",
)

# --- Histograms ---
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)


def metrics_response() -> Response:
    """Return a Prometheus-format metrics response."""
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )
