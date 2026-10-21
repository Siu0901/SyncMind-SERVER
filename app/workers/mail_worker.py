from app import models

import logging

from arq.connections import RedisSettings

from app.core.log import setup_logging
from app.core.config import get_settings

from app.domains.identity.auth.email import EmailClient
from app.domains.identity.auth.task import send_verification_email


settings = get_settings()


log = logging.getLogger(__name__)


async def startup(ctx):
    setup_logging()

    ctx["email_client"] = EmailClient()

    log.info(
        "메일 워커 기동 — queue=%s",
        settings.mail_queue_name,
    )


async def shutdown(ctx):
    log.info("메일 워커 종료")


class MailWorkerSettings:
    functions = [
        send_verification_email,
    ]

    queue_name = settings.mail_queue_name
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    on_startup = startup
    on_shutdown = shutdown

    max_tries = 3
    job_timeout = 600
