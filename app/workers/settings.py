from arq.connections import RedisSettings

from app.core.config import get_settings

from app.workers.email import send_verification_email


settings = get_settings()

class WorkerSettings:
    functions = [
        send_verification_email,
    ]

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    max_tries = 3