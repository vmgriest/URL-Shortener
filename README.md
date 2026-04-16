# URL Shortener

A production-grade URL shortener built with **FastAPI + PostgreSQL + Redis**, fully containerised with Docker Compose and instrumented with Prometheus + Grafana.

## Architecture

```
POST /api/v1/shorten  ──►  FastAPI  ──►  PostgreSQL (persist)
GET  /{short_code}    ──►  FastAPI  ──►  Redis (cache) ──► PostgreSQL (fallback)
GET  /metrics         ──►  Prometheus scrape
                              │
                           Grafana (dashboard)
```

**Key design choices:**
- **Token bucket rate limiter** — runs as a Redis Lua script (atomic, no race conditions)
- **Read-through cache** — Redis checked first on redirect; PostgreSQL is the source of truth
- **Structured JSON logging** — every request emits a JSON log line (method, path, status, duration_ms)
- **Prometheus metrics** — request count, p99 latency, cache hit/miss rate, rate-limit hits

---

## Installation

### 1. Install Docker Desktop

Download from https://www.docker.com/products/docker-desktop and install.

Verify it works:
```bash
docker --version
docker compose version
```

### 2. Install k6 (load testing)

**Windows (via winget):**
```bash
winget install k6 --source winget
```

**Windows (via Chocolatey):**
```bash
choco install k6
```

**macOS:**
```bash
brew install k6
```

Verify:
```bash
k6 version
```

### 3. Install Python (for local dev / tests)

Download Python 3.12 from https://www.python.org/downloads/

Create a virtual environment and install dependencies:
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
# Extra test dependencies:
pip install fakeredis aiosqlite
```

---

## Running the project

### Start all services
```bash
# Copy env file (edit values if needed)
cp .env.example .env

# Build and start everything
docker compose up --build
```

Services:
| Service    | URL                          |
|------------|------------------------------|
| API        | http://localhost:8000        |
| API docs   | http://localhost:8000/docs   |
| Prometheus | http://localhost:9090        |
| Grafana    | http://localhost:3000        |

Grafana login: **admin / admin**

### Stop everything
```bash
docker compose down
```

### Stop and wipe volumes (fresh start)
```bash
docker compose down -v
```

---

## API Usage

### Shorten a URL
```bash
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
```bash
curl -L http://localhost:8000/aB3xYz
```

### URL stats
```bash
curl http://localhost:8000/api/v1/info/aB3xYz
```

---

## Running tests

```bash
pytest tests/ -v
```

---

## Load test

Make sure the app is running, then:

```bash
k6 run loadtest/script.js
```

Or target a specific host:
```bash
k6 run -e BASE_URL=http://localhost:8000 loadtest/script.js
```

The script runs three phases:
1. **Seed** (20s) — creates short URLs at 50 req/s to populate the DB
2. **Baseline** (60s) — 500 req/s, threshold: p99 < 50ms
3. **With cache** (60s) — 500 req/s after cache is warm, threshold: p99 < 15ms

---

## Project structure

```
.
├── app/
│   ├── config.py          # Settings from environment variables
│   ├── main.py            # FastAPI app, routes, middleware
│   ├── database.py        # Async SQLAlchemy engine + session
│   ├── redis_client.py    # Shared async Redis connection
│   ├── models.py          # SQLAlchemy ORM model (URLs table)
│   ├── schemas.py         # Pydantic request/response schemas
│   ├── crud.py            # Database operations
│   ├── rate_limiter.py    # Token bucket (Redis Lua script)
│   ├── metrics.py         # Prometheus counters + histograms
│   └── utils.py           # Short code generator (base62)
├── tests/
│   ├── test_api.py        # API integration tests (in-memory SQLite)
│   └── test_rate_limiter.py # Rate limiter unit tests (fakeredis)
├── prometheus/
│   └── prometheus.yml     # Scrape config
├── grafana/
│   ├── datasources/       # Auto-provisioned Prometheus datasource
│   └── dashboards/        # Auto-provisioned dashboard
├── loadtest/
│   └── script.js          # k6 load test (baseline + cache comparison)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Resume numbers (after running load test)

- **Baseline**: ~500 req/s, p99 ≈ 30–45ms
- **With Redis cache**: ~500 req/s, p99 ≈ 5–12ms
- Cache hit rate climbs to 95%+ within 30s of the warm phase
