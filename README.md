# URL Shortener

A production-grade URL shortener built with **FastAPI + PostgreSQL + Redis**, deployed to **AWS** with a fully serverless analytics pipeline. Includes a web UI, real-time click analytics, rate limiting, observability, and infrastructure-as-code.

---

## Architecture

### AWS deployment (production)

```
Browser
   │
   ▼
Application Load Balancer
   │
   ▼
ECS Fargate (FastAPI)
   ├──► RDS PostgreSQL       — persistent URL storage
   ├──► ElastiCache Redis    — in-memory redirect cache (~5ms hits)
   └──► SQS (fire-and-forget on every redirect)
              │
              ▼
           Lambda             — processes click events asynchronously
              │
              ▼
           DynamoDB           — click analytics per short code
```

### Local development

```
Docker Compose
  ├── app        FastAPI on port 8000
  ├── postgres   PostgreSQL on port 5432
  ├── redis      Redis on port 6379
  ├── prometheus Metrics scraping on port 9090
  └── grafana    Dashboard on port 3000
```

---

## How it works

```
POST /api/v1/shorten  ──►  FastAPI  ──►  PostgreSQL (persist URL)
                                │
                           returns short code

GET  /{short_code}    ──►  FastAPI  ──►  Redis cache hit?  ──YES──►  302 redirect (~5ms)
                                              │                           │
                                              NO                    SQS message ──► Lambda ──► DynamoDB
                                              │
                                         PostgreSQL lookup ──►  write to Redis  ──►  302 redirect (~40ms)

GET  /api/v1/analytics/{code}  ──►  DynamoDB query  ──►  click count + recent visitors
```

**The key insight:** After the first visit to any short link, every subsequent redirect is served entirely from Redis — PostgreSQL is never touched again. This drives latency from ~40ms down to ~5ms.

Click analytics are written asynchronously: the redirect returns immediately and a background Lambda processes the event into DynamoDB, so analytics never add latency to the user's experience.

---

## AWS infrastructure (Terraform)

All infrastructure is provisioned as code in the `terraform/` directory.

| Resource | Service | Purpose |
|---|---|---|
| ECS Fargate | Compute | Runs the FastAPI container, auto-scales |
| ECR | Registry | Stores Docker images |
| RDS PostgreSQL 16 | Database | Persistent URL storage (db.t3.micro) |
| ElastiCache Redis 7 | Cache | In-memory redirect cache (cache.t3.micro) |
| Application Load Balancer | Networking | Health checks, traffic distribution |
| SQS | Messaging | Decouples click events from redirect path |
| Lambda (Python 3.12) | Serverless | Processes SQS click events |
| DynamoDB | NoSQL | Click analytics (PAY_PER_REQUEST billing) |
| CloudWatch | Observability | Container logs, 7-day retention |
| IAM | Security | Least-privilege roles per service |
| VPC | Networking | Public subnets (ALB + ECS), private subnets (RDS + Redis) |

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

Redis is fast because it stores everything **in memory**, with no disk I/O and no query parsing. The application caches each URL for 1 hour (`CACHE_TTL = 3600s`), so hot links stay warm automatically.

At 500 req/s with a warm cache the p99 latency drops by **~75%** compared to always hitting the database.

---

## Click analytics — serverless event pipeline

Every redirect fires a message to SQS without waiting for a response (fire-and-forget). A Lambda function polls SQS in batches of 10 and writes each click event to DynamoDB with the short code, timestamp, IP, and user agent.

```
GET /fM3acY
  → 302 redirect (immediate)
  → SQS.send_message (async task, does not block response)
       └── Lambda polls SQS
              └── DynamoDB.put_item({short_code, timestamp, ip, user_agent})

GET /api/v1/analytics/fM3acY
  → {"short_code": "fM3acY", "total_clicks": 42, "recent_clicks": [...]}
```

This architecture means analytics are **never on the critical path** — a SQS or Lambda failure has zero impact on redirect performance.

---

## Observability — what gets measured

### 1. Structured JSON logs (CloudWatch)
Every request emits a single JSON line:
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
Logs stream to CloudWatch (`/ecs/url-shortener`) with 7-day retention.

### 2. Prometheus metrics
The `/metrics` endpoint exposes:

| Metric | Type | What it tells you |
|---|---|---|
| `http_requests_total` | Counter | Request volume by endpoint and status code |
| `http_request_duration_seconds` | Histogram | Latency distribution (p50, p95, p99) |
| `cache_hits_total` | Counter | How often Redis served the redirect |
| `cache_misses_total` | Counter | How often PostgreSQL was needed |
| `urls_created_total` | Counter | Total short URLs ever created |
| `rate_limit_hits_total` | Counter | How many requests were blocked |

### 3. Grafana dashboard (local only)
Auto-provisioned at startup with 6 panels: request rate, P99/P50 latency, cache hit rate, error rate, rate limit hits, URLs created.

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

## Deploying to AWS

### Prerequisites
- AWS CLI configured (`aws configure`)
- Terraform >= 1.0
- Docker Desktop

### First deploy

```bash
# 1. Create your variables file
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars and set db_password

# 2. Initialise Terraform
terraform init

# 3. Create ECR first so you can push the image
terraform apply "-target=aws_ecr_repository.app"

# 4. Build and push the Docker image
cd ..
$token = aws ecr get-login-password --region us-east-1
docker login --username AWS --password $token 241431497665.dkr.ecr.us-east-1.amazonaws.com
docker build -t url-shortener .
docker tag url-shortener:latest 241431497665.dkr.ecr.us-east-1.amazonaws.com/url-shortener:latest
docker push 241431497665.dkr.ecr.us-east-1.amazonaws.com/url-shortener:latest

# 5. Deploy everything (RDS takes ~5 minutes)
cd terraform
terraform apply
```

### Redeploy after code changes

```bash
docker build -t url-shortener .
docker tag url-shortener:latest 241431497665.dkr.ecr.us-east-1.amazonaws.com/url-shortener:latest
docker push 241431497665.dkr.ecr.us-east-1.amazonaws.com/url-shortener:latest
aws ecs update-service --cluster url-shortener --service url-shortener --force-new-deployment --region us-east-1
```

### Tear down

```bash
cd terraform
terraform destroy
```

Type `yes`. This shuts down all AWS resources (~$44/month while running). When you need it again, `terraform apply` brings everything back in ~7 minutes.

### Commit and push changes

```bash
cd ..
git add .
git commit -m "your message"
git push
```

---

## Running locally

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL |
|---|---|
| App + UI | http://localhost:8000 |
| Swagger docs | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / admin) |

```bash
# Stop
docker compose down

# Stop and wipe all data
docker compose down -v
```

### Troubleshooting: app fails to start with DNS error

If `app-1` crashes on startup with `socket.gaierror: [Errno -2] Name or service not known`, the app container can't resolve the `postgres` hostname. This happens when stale containers from a previous run are left on a different Docker network.

```bash
docker compose down --remove-orphans
docker compose up --build
```

---

## Installation (local development)

### 1. Install Docker Desktop
Download from https://www.docker.com/products/docker-desktop

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

### Click analytics (AWS only)
```
GET http://localhost:8000/api/v1/analytics/aB3xYz
```
```json
{
  "short_code": "aB3xYz",
  "total_clicks": 42,
  "recent_clicks": [
    {"timestamp": "2024-01-15T10:23:45+00:00", "ip": "1.2.3.4", "user_agent": "Mozilla/5.0..."}
  ]
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
│   ├── utils.py           # Short code generator (base62, 6 chars)
│   └── static/
│       └── index.html     # Web UI
├── lambda/
│   └── click_processor.py # Lambda handler — SQS → DynamoDB
├── terraform/             # All AWS infrastructure as code
│   ├── main.tf            # Provider config
│   ├── vpc.tf             # VPC, subnets, routing
│   ├── ecs.tf             # ECS cluster, task, service
│   ├── rds.tf             # RDS PostgreSQL
│   ├── elasticache.tf     # ElastiCache Redis
│   ├── alb.tf             # Application Load Balancer
│   ├── sqs.tf             # SQS click events queue
│   ├── lambda.tf          # Lambda function + SQS trigger
│   ├── dynamodb.tf        # DynamoDB click events table
│   ├── ecr.tf             # ECR container registry
│   ├── iam.tf             # IAM roles and policies
│   └── security_groups.tf # Security groups
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
