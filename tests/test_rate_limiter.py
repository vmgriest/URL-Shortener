"""
Unit tests for the token-bucket rate limiter.

No real Redis needed — we use fakeredis.
Install fakeredis: pip install fakeredis
"""

import pytest
import pytest_asyncio
import fakeredis.aioredis as fakeredis

from app.rate_limiter import TokenBucketRateLimiter


@pytest_asyncio.fixture
async def redis():
    """Fake async Redis instance, reset per test."""
    r = fakeredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


class TestTokenBucketRateLimiter:
    @pytest.mark.asyncio
    async def test_allows_requests_within_capacity(self, redis):
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        for _ in range(5):
            assert await limiter.is_allowed(redis, "user1") is True

    @pytest.mark.asyncio
    async def test_blocks_requests_exceeding_capacity(self, redis):
        limiter = TokenBucketRateLimiter(capacity=3, refill_rate=1.0)
        results = [await limiter.is_allowed(redis, "user1") for _ in range(5)]
        assert results[:3] == [True, True, True]
        assert results[3] is False
        assert results[4] is False

    @pytest.mark.asyncio
    async def test_different_users_have_separate_buckets(self, redis):
        limiter = TokenBucketRateLimiter(capacity=2, refill_rate=1.0)
        # Exhaust user1's bucket
        await limiter.is_allowed(redis, "user1")
        await limiter.is_allowed(redis, "user1")
        assert await limiter.is_allowed(redis, "user1") is False

        # user2 still has full bucket
        assert await limiter.is_allowed(redis, "user2") is True
        assert await limiter.is_allowed(redis, "user2") is True

    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self, redis):
        import time

        limiter = TokenBucketRateLimiter(capacity=2, refill_rate=100.0)

        # Drain all tokens
        await limiter.is_allowed(redis, "user1")
        await limiter.is_allowed(redis, "user1")
        assert await limiter.is_allowed(redis, "user1") is False

        # Manually backdate last_refill by 1 second so ~100 tokens refill
        await redis.hset("rate_limit:user1", "last_refill", str(time.time() - 1.0))

        # Now it should be allowed again
        assert await limiter.is_allowed(redis, "user1") is True
