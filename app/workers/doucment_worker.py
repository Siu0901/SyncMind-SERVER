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
from app.domains.knowledge.task import process_document
from app.infra.embedding.factory import (
    close_embedding,
    init_embedding,
)


settings = get_settings()

log = logging.getLogger(__name__)


async def startup(ctx):
    setup_logging()

    ctx["s3"] = S3Client()
    ctx["embedding"] = init_embedding()

    await init_qdrant()

    log.info(
        "문서 워커 기동 — queue=%s",
        settings.document_queue_name,
    )


async def shutdown(ctx):
    await close_qdrant()
    await close_embedding()

    log.info("문서 워커 종료")


class DocumentWorkerSettings:
    functions = [
        process_document,
    ]

    queue_name = get_settings().document_queue_name
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    on_startup = startup
    on_shutdown = shutdown

    max_tries = 3
    job_timeout = 600
