from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings


_pool: ArqRedis | None = None


def get_redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().REDIS_URL)


async def init_redis() -> ArqRedis:
    global _pool

    if _pool is None:
        _pool = await create_pool(
            get_redis_settings()
        )
    return _pool


def get_redis() -> ArqRedis:
    if _pool is None:
        raise Exception("redis not initialized")
    return _pool


async def close_redis() -> None:
    global _pool

    if _pool is not None:
        await _pool.close()
        _pool = None


async def redis_ping() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False