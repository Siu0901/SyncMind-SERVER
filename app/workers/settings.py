from app import models

import logging

from arq.connections import RedisSettings

from app.core.qdrant import (
    close_qdrant,
    init_qdrant,
)
from app.core.log import setup_logging
from app.core.config import get_settings
from app.core.s3 import S3Client
from app.workers.email import send_verification_email
from app.workers.ingestion import process_document


settings = get_settings()


async def startup(ctx):
    setup_logging()
    ctx["s3"] = S3Client()

    await init_qdrant()


async def shutdown(ctx):
    await close_qdrant()


class WorkerSettings:
    functions = [
        send_verification_email,
        process_document,
    ]

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    on_startup = startup
    on_shutdown = shutdown

    max_tries = 3
    job_timeout = 600