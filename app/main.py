"""
URL Shortener — FastAPI entry point.

Endpoints:
  POST /api/v1/shorten          — create a short URL
  GET  /{short_code}            — redirect to the original URL
  GET  /api/v1/info/{short_code} — stats for a short URL
  GET  /health                  — liveness check
  GET  /metrics                 — Prometheus metrics
"""

import logging
import time
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pythonjsonlogger.jsonlogger import JsonFormatter
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, init_db, AsyncSessionLocal
from app.redis_client import get_redis, close_redis
from app.schemas import ShortenRequest, ShortenResponse, URLInfo
from app.crud import create_url, get_url_by_code, increment_hit_count
from app.rate_limiter import TokenBucketRateLimiter
from app.metrics import (
    http_requests_total,
    http_request_duration_seconds,
    cache_hits_total,
    cache_misses_total,
    urls_created_total,
    rate_limit_hits_total,
    metrics_response,
)

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("url_shortener")

# ---------------------------------------------------------------------------
# Rate limiter (shared instance, configured from env)
# ---------------------------------------------------------------------------
rate_limiter = TokenBucketRateLimiter(
    capacity=settings.rate_limit_capacity,
    refill_rate=settings.rate_limit_refill_rate,
)

CACHE_TTL = 3600  # seconds to cache redirects in Redis


# ---------------------------------------------------------------------------
# App lifespan: initialise DB tables and Redis on startup
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await get_redis()
    logger.info("startup complete", extra={"db": settings.database_url, "redis": settings.redis_url})
    yield
    await close_redis()
    logger.info("shutdown complete")


app = FastAPI(title="URL Shortener", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Middleware: metrics + structured request logging
# ---------------------------------------------------------------------------
@app.middleware("http")
async def observe_requests(request: Request, call_next):
    start = time.perf_counter()

    # --- Rate limiting (skip /health and /metrics) ---
    if request.url.path not in ("/health", "/metrics"):
        client_ip = request.client.host if request.client else "unknown"
        redis = await get_redis()
        allowed = await rate_limiter.is_allowed(redis, client_ip)
        if not allowed:
            rate_limit_hits_total.inc()
            from fastapi.responses import JSONResponse
            logger.warning("rate limited", extra={"ip": client_ip, "path": request.url.path})
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded. Try again shortly."},
            )

    response = await call_next(request)
    duration = time.perf_counter() - start

    # Normalise path for metric labels (avoid high cardinality from short codes)
    path = request.url.path
    if path.startswith("/api/v1/info/"):
        path = "/api/v1/info/{short_code}"
    elif path.startswith("/api/v1/"):
        pass  # keep as-is
    elif path not in ("/health", "/metrics"):
        path = "/{short_code}"

    http_requests_total.labels(
        method=request.method,
        endpoint=path,
        status_code=str(response.status_code),
    ).inc()
    http_request_duration_seconds.labels(
        method=request.method,
        endpoint=path,
    ).observe(duration)

    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round(duration * 1000, 2),
        },
    )
    return response


async def _increment_hit_bg(short_code: str) -> None:
    """Fire-and-forget hit counter using its own DB session."""
    async with AsyncSessionLocal() as db:
        await increment_hit_count(db, short_code)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok"}


@app.get("/metrics", tags=["ops"])
async def metrics():
    return metrics_response()


@app.post("/api/v1/shorten", response_model=ShortenResponse, status_code=status.HTTP_201_CREATED, tags=["urls"])
async def shorten_url(body: ShortenRequest, db: AsyncSession = Depends(get_db)):
    original = str(body.url)
    url_row = await create_url(db, original)
    urls_created_total.inc()
    short_url = f"{settings.base_url}/{url_row.short_code}"
    logger.info("url created", extra={"short_code": url_row.short_code, "original": original})
    return ShortenResponse(
        short_code=url_row.short_code,
        short_url=short_url,
        original_url=original,
    )


@app.get("/{short_code}", tags=["urls"])
async def redirect_url(short_code: str, db: AsyncSession = Depends(get_db)):
    redis = await get_redis()
    cache_key = f"url:{short_code}"

    # 1. Check Redis cache
    cached = await redis.get(cache_key)
    if cached:
        cache_hits_total.labels(operation="redirect").inc()
        logger.info("cache hit", extra={"short_code": short_code})
        asyncio.create_task(_increment_hit_bg(short_code))
        return RedirectResponse(url=cached, status_code=status.HTTP_302_FOUND)

    cache_misses_total.labels(operation="redirect").inc()

    # 2. Fall back to Postgres
    url_row = await get_url_by_code(db, short_code)
    if url_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short code not found")

    # 3. Populate cache and fire hit counter without blocking the redirect
    await redis.set(cache_key, url_row.original_url, ex=CACHE_TTL)
    asyncio.create_task(_increment_hit_bg(short_code))

    logger.info("cache miss — served from db", extra={"short_code": short_code})
    return RedirectResponse(url=url_row.original_url, status_code=status.HTTP_302_FOUND)


@app.get("/api/v1/info/{short_code}", response_model=URLInfo, tags=["urls"])
async def url_info(short_code: str, db: AsyncSession = Depends(get_db)):
    url_row = await get_url_by_code(db, short_code)
    if url_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short code not found")
    return url_row
