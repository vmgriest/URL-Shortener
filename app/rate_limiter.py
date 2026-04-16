"""
Token bucket rate limiter backed by Redis.

Each IP gets a bucket of `capacity` tokens that refills at `refill_rate`
tokens per second. Implemented with plain Redis hash commands so it works
with both real Redis and fakeredis in tests — no Lua required.

The read-then-write is not strictly atomic, but for a rate limiter the worst
case is a handful of extra requests slipping through under extreme concurrency,
which is acceptable.
"""

import time
import redis.asyncio as aioredis


class TokenBucketRateLimiter:
    def __init__(self, capacity: int = 20, refill_rate: float = 10.0):
        """
        Args:
            capacity:    Maximum tokens per bucket (burst size).
            refill_rate: Tokens added per second.
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._ttl = int(capacity / refill_rate) + 60

    async def is_allowed(self, redis: aioredis.Redis, identifier: str) -> bool:
        """Return True if the request is within the rate limit."""
        key = f"rate_limit:{identifier}"
        now = time.time()

        bucket = await redis.hmget(key, "tokens", "last_refill")
        tokens = float(bucket[0]) if bucket[0] is not None else float(self.capacity)
        last_refill = float(bucket[1]) if bucket[1] is not None else now

        elapsed = max(0.0, now - last_refill)
        new_tokens = min(float(self.capacity), tokens + elapsed * self.refill_rate)

        if new_tokens < 1:
            return False

        await redis.hset(key, mapping={"tokens": new_tokens - 1, "last_refill": now})
        await redis.expire(key, self._ttl)
        return True
