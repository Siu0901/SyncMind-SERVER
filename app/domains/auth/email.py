from fastapi_mail import (
    ConnectionConfig,
    FastMail,
    MessageSchema,
    MessageType,
    NameEmail
)

from app.core.config import get_settings


settings = get_settings()


class EmailClient:
    def __init__(self) -> None:
        self.config = ConnectionConfig(
            MAIL_USERNAME=settings.SMTP_USERNAME,
            MAIL_PASSWORD=settings.SMTP_PASSWORD,
            MAIL_FROM=settings.SMTP_FROM_EMAIL,
            MAIL_PORT=settings.SMTP_PORT,
            MAIL_SERVER=settings.SMTP_HOST,

            MAIL_STARTTLS=True,
            MAIL_SSL_TLS=False,

            USE_CREDENTIALS=True,
            VALIDATE_CERTS=True,
        )

        self.mail = FastMail(self.config)

    async def send_verification_email(
        self,
        email: NameEmail,
        code: str,
    ):
        html = f"""
                <html>
                    <body>
                        <h2>SyncMind 이메일 인증</h2>

                        <p>
                            아래 인증 코드를 입력해주세요.
                        </p>

                        <h1>
                            {code}
                        </h1>

                        <p>
                            인증 코드는 5분 동안 유효합니다.
                        </p>
                    </body>
                </html>
                """

        message = MessageSchema(
            subject="SyncMind 이메일 인증",
            recipients=[email],
            body=html,
            subtype=MessageType.html,
        )

        await self.mail.send_message(message)