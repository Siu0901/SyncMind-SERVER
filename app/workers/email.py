import logging

from fastapi_mail import NameEmail

from app.domains.auth.email import EmailClient


logger = logging.getLogger(__name__)


async def send_verification_email(
    ctx,
    email: NameEmail,
    code: str,
) -> None:
    client = EmailClient()

    try:
        await client.send_verification_email(
            email=email,
            code=code,
        )

        logger.info(
            "Verification email sent | email=%s",
            email,
        )

    except Exception:
        logger.exception(
            "Verification email failed | email=%s",
            email,
        )

        raise