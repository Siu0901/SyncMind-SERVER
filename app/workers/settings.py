from app.core.redis import get_redis
from app.workers.email import (
    send_verification_email,
)


class WorkerSettings:
    functions = [
        send_verification_email,
    ]

    redis_settings = get_redis()

    max_tries = 3