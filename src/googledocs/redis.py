"""Redis client factory and FastAPI dependency."""

from collections.abc import AsyncGenerator

from redis.asyncio import Redis

from googledocs.config import settings

redis_client: Redis | None = None


async def get_redis() -> AsyncGenerator[Redis]:
    """FastAPI dependency that yields the shared Redis client."""
    if redis_client is None:
        raise RuntimeError("Redis client not initialised — call init_redis() on startup")
    yield redis_client


async def init_redis() -> None:
    """Initialise the shared Redis client. Call from lifespan startup."""
    global redis_client  # noqa: PLW0603
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)


async def close_redis() -> None:
    """Close the shared Redis client. Call from lifespan shutdown."""
    global redis_client  # noqa: PLW0603
    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None
