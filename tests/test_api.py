"""
Integration tests for the URL Shortener API.

These tests use FastAPI's TestClient with in-memory SQLite (via aiosqlite) and
a mock Redis so they run without Docker.

Run with:
    pytest tests/ -v
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


# ---------------------------------------------------------------------------
# In-memory SQLite engine for tests
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(autouse=True)
async def setup_database():
    """Create tables before each test, drop after."""
    import app.models  # noqa: F401 — registers models with Base
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    """AsyncClient with DB and Redis overrides."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)   # cache always misses
    mock_redis.set = AsyncMock(return_value=True)

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.main.get_redis", return_value=mock_redis), \
         patch("app.main.rate_limiter.is_allowed", return_value=True):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestHealth:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client: AsyncClient):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestShorten:
    @pytest.mark.asyncio
    async def test_shorten_valid_url(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/shorten",
            json={"url": "https://www.example.com/some/long/path"},
        )
        assert response.status_code == 201
        data = response.json()
        assert "short_code" in data
        assert len(data["short_code"]) == 6
        assert data["original_url"] == "https://www.example.com/some/long/path"
        assert data["short_url"].endswith(data["short_code"])

    @pytest.mark.asyncio
    async def test_shorten_invalid_url_returns_422(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/shorten",
            json={"url": "not-a-url"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_shorten_missing_body_returns_422(self, client: AsyncClient):
        response = await client.post("/api/v1/shorten", json={})
        assert response.status_code == 422


class TestRedirect:
    @pytest.mark.asyncio
    async def test_redirect_follows_to_original(self, client: AsyncClient):
        # Create a short URL
        create_resp = await client.post(
            "/api/v1/shorten",
            json={"url": "https://www.python.org"},
        )
        short_code = create_resp.json()["short_code"]

        # Redirect (don't follow so we can assert 302)
        redirect_resp = await client.get(f"/{short_code}", follow_redirects=False)
        assert redirect_resp.status_code == 302
        # Pydantic v2 normalises bare-domain URLs to include a trailing slash
        assert redirect_resp.headers["location"].rstrip("/") == "https://www.python.org"

    @pytest.mark.asyncio
    async def test_unknown_code_returns_404(self, client: AsyncClient):
        response = await client.get("/xxxxxx", follow_redirects=False)
        assert response.status_code == 404


class TestInfo:
    @pytest.mark.asyncio
    async def test_info_returns_url_data(self, client: AsyncClient):
        create_resp = await client.post(
            "/api/v1/shorten",
            json={"url": "https://www.github.com"},
        )
        short_code = create_resp.json()["short_code"]

        info_resp = await client.get(f"/api/v1/info/{short_code}")
        assert info_resp.status_code == 200
        data = info_resp.json()
        assert data["short_code"] == short_code
        assert data["original_url"].rstrip("/") == "https://www.github.com"
        assert "created_at" in data
        assert "hit_count" in data

    @pytest.mark.asyncio
    async def test_info_unknown_code_returns_404(self, client: AsyncClient):
        response = await client.get("/api/v1/info/xxxxxx")
        assert response.status_code == 404
