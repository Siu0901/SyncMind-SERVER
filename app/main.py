import logging
from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.core.config import get_settings
from app.core.database import (
    close_session,
    db_ping,
)
from app.core.redis import (
    close_redis,
    init_redis,
    redis_ping,
)
from app.core.qdrant import (
    init_qdrant,
    close_qdrant,
    ensure_collection,
    qdrant_ping,
)
from app.core.log import setup_logging
from app.core.exception.handlers import register_exception_handlers
from app.api import router


setup_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup")
    settings = get_settings()

    await init_redis()
    logger.info("Redis initialized")

    await init_qdrant()
    await ensure_collection()
    logger.info("Qdrant initialized")

    yield
    logger.info("Application shutdown")

    await close_session()
    await close_redis()
    await close_qdrant()


app = FastAPI(title="SyncMind", lifespan=lifespan)

app.include_router(router)

register_exception_handlers(app)


@app.get("/health")
async def health_check():
    db = await db_ping()
    redis = await redis_ping()
    qdrant = await qdrant_ping()

    healthy = db and redis and qdrant

    return {
        "status": "ok" if healthy else "error",
        "database": db,
        "redis": redis,
        "qdrant": qdrant,
    }