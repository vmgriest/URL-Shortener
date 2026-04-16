# URL Shortener

A production-grade URL shortener built with **FastAPI + PostgreSQL + Redis**, fully containerised with Docker Compose and instrumented with Prometheus + Grafana.

Designed to demonstrate real-world backend engineering: caching strategy, rate limiting, observability, and measurable performance — with hard numbers to back it up.

---

## How it works

```
POST /api/v1/shorten  ──►  FastAPI  ──►  PostgreSQL (persist URL)
                                │
                           returns short code

GET  /{short_code}    ──►  FastAPI  ──►  Redis cache hit?  ──YES──►  302 redirect (~5ms)
                                              │
                                              NO
                                              │
                                         PostgreSQL lookup ──►  write to Redis  ──►  302 redirect (~40ms)

GET  /metrics         ──►  Prometheus scrapes every 10s
                                │
                            Grafana dashboard (live graphs)
```

**The key insight:** After the first visit to any short link, every subsequent redirect is served entirely from Redis — PostgreSQL is never touched again. This is what drives the latency from ~40ms down to ~5ms.

---

## What makes it fast — Redis as a read-through cache

Without Redis, every redirect hits PostgreSQL:

```
User clicks link → FastAPI → PostgreSQL (disk I/O + query) → redirect
                                  ~30–45ms
```

With Redis, only the *first* visit hits the database. Every visit after that:

```
User clicks link → FastAPI → Redis (in-memory lookup) → redirect
                                  ~5–12ms
```

Redis is fast because it stores everything **in memory**, with no disk I/O and no query parsing. A hash lookup in Redis takes microseconds. The application caches each URL for 1 hour (`CACHE_TTL = 3600s`), so hot links stay warm automatically.

At 500 req/s with a warm cache the p99 latency drops by **~75%** compared to always hitting the database.

---

## Observability — what gets measured

Every request is tracked across three layers:

### 1. Structured JSON logs
Every request emits a single JSON line to stdout:
```json
{
  "message": "request",
  "method": "GET",
  "path": "/7FQABs",
  "status": 302,
  "duration_ms": 4.7,
  "timestamp": "2024-01-15T10:23:45"
}
```
This makes logs searchable and parseable by any log aggregation tool (Datadog, Loki, CloudWatch).

### 2. Prometheus metrics
The `/metrics` endpoint exposes these counters and histograms:

| Metric | Type | What it tells you |
|---|---|---|
| `http_requests_total` | Counter | Request volume by endpoint and status code |
| `http_request_duration_seconds` | Histogram | Latency distribution (p50, p95, p99) |
| `cache_hits_total` | Counter | How often Redis served the redirect |
| `cache_misses_total` | Counter | How often PostgreSQL was needed |
| `urls_created_total` | Counter | Total short URLs ever created |
| `rate_limit_hits_total` | Counter | How many requests were blocked |

### 3. Grafana dashboard
Auto-provisioned at startup with 6 panels:

- **Request rate** — requests per second over time
- **P99 / P50 latency** — where the slowest 1% of requests land
- **Cache hit rate %** — rises toward 100% as links warm up
- **Error rate** — 4xx and 5xx responses per second
- **Rate limit hits** — blocked request volume
- **Total URLs created** — cumulative counter

---

## Rate limiting — token bucket algorithm

Each IP address gets a **bucket of 20 tokens** that refills at **10 tokens per second**.

- Every request costs 1 token
- If the bucket is empty → `429 Too Many Requests`
- Tokens refill continuously, so short bursts are allowed but sustained abuse is blocked

The bucket state is stored in Redis as a hash (`rate_limit:{ip}`), so it works correctly across multiple app instances and resets automatically after idle periods.

---

## Performance numbers (k6 load test)

Run `k6 run loadtest/script.js` to reproduce these results:

| Phase | Load | p50 | p99 | Cache hit rate |
|---|---|---|---|---|
| Baseline (no warm cache) | 500 req/s | ~15ms | ~40ms | 0% |
| Warm cache | 500 req/s | ~3ms | ~12ms | 95%+ |

The load test runs three phases automatically:
1. **Seed** (20s) — creates short URLs at 50 req/s to populate the database
2. **Baseline** (60s) — hammers redirects at 500 req/s before cache is warm
3. **Cache warm** (60s) — same load after Redis has cached all hot links

Thresholds are enforced: the test **fails** if p99 exceeds 50ms in the baseline phase or 15ms in the cache-warm phase.

---

## Installation

### 1. Install Docker Desktop
Download from https://www.docker.com/products/docker-desktop

Verify:
```bash
docker --version
docker compose version
```

### 2. Install k6
**Windows:**
```bash
winget install k6 --source winget
```
**macOS:**
```bash
brew install k6
```

### 3. Install Python (for running tests locally)
Download Python 3.12 from https://www.python.org/downloads/

```bash
python -m venv .venv

# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
pip install fakeredis aiosqlite
```

---

## Running the project

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL |
|---|---|
| API | http://localhost:8000 |
| Swagger docs | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / admin) |

```bash
# Stop
docker compose down

# Stop and wipe all data
docker compose down -v
```

---

## API

### Shorten a URL
```bash
# PowerShell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/shorten" `
  -ContentType "application/json" `
  -Body '{"url": "https://www.example.com/some/very/long/url"}'

# curl (macOS/Linux)
curl -X POST http://localhost:8000/api/v1/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.example.com/some/very/long/url"}'
```

Response:
```json
{
  "short_code": "aB3xYz",
  "short_url": "http://localhost:8000/aB3xYz",
  "original_url": "https://www.example.com/some/very/long/url"
}
```

### Redirect
```
GET http://localhost:8000/aB3xYz  →  302  →  original URL
```

### URL stats
```
GET http://localhost:8000/api/v1/info/aB3xYz
```
```json
{
  "short_code": "aB3xYz",
  "original_url": "https://www.example.com/some/very/long/url",
  "created_at": "2024-01-15T10:23:45",
  "hit_count": 142
}
```

---

## Running tests

```bash
pytest tests/ -v
```

12 tests covering: health check, URL creation, validation, redirects, 404 handling, and the full token bucket rate limiter (allow, block, separate buckets, token refill).

---

## Project structure

```
.
├── app/
│   ├── config.py          # Settings from environment variables
│   ├── main.py            # FastAPI app, routes, middleware
│   ├── database.py        # Async SQLAlchemy engine + session
│   ├── redis_client.py    # Shared async Redis connection pool
│   ├── models.py          # SQLAlchemy ORM model (urls table)
│   ├── schemas.py         # Pydantic request/response schemas
│   ├── crud.py            # Database operations
│   ├── rate_limiter.py    # Token bucket rate limiter (Redis-backed)
│   ├── metrics.py         # Prometheus counters + histograms
│   └── utils.py           # Short code generator (base62, 6 chars)
├── tests/
│   ├── test_api.py        # API integration tests (in-memory SQLite)
│   └── test_rate_limiter.py # Rate limiter unit tests (fakeredis)
├── prometheus/
│   └── prometheus.yml     # Scrape config (10s interval)
├── grafana/
│   ├── datasources/       # Auto-provisioned Prometheus datasource
│   └── dashboards/        # Auto-provisioned dashboard JSON
├── loadtest/
│   └── script.js          # k6 load test (3 phases, enforced thresholds)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
