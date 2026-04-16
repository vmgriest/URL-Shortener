"""Database operations for URLs."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models import URL
from app.utils import generate_short_code

MAX_RETRIES = 5  # max collisions before giving up


async def create_url(db: AsyncSession, original_url: str) -> URL:
    """Insert a new short URL, retrying on short-code collisions."""
    for _ in range(MAX_RETRIES):
        code = generate_short_code()
        existing = await db.execute(select(URL).where(URL.short_code == code))
        if existing.scalar_one_or_none() is None:
            url = URL(short_code=code, original_url=original_url)
            db.add(url)
            await db.commit()
            await db.refresh(url)
            return url
    raise RuntimeError("Failed to generate a unique short code after retries")


async def get_url_by_code(db: AsyncSession, short_code: str) -> URL | None:
    """Fetch a URL row by its short code."""
    result = await db.execute(select(URL).where(URL.short_code == short_code))
    return result.scalar_one_or_none()


async def increment_hit_count(db: AsyncSession, short_code: str) -> None:
    """Bump the hit counter for a short code (fire-and-forget friendly)."""
    await db.execute(
        update(URL)
        .where(URL.short_code == short_code)
        .values(hit_count=URL.hit_count + 1)
    )
    await db.commit()
