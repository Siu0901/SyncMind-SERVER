"""
app/workers/mail_worker.py - arq 워커 잡 단위 테스트.

이 잡의 계약은 두 가지다.
    1. EmailClient 에 이메일/코드를 그대로 전달한다.
    2. 실패하면 예외를 삼키지 않고 다시 올린다
       -> arq 가 max_tries(=3) 만큼 재시도할 수 있어야 하기 때문. 삼키면 메일이 조용히 유실된다.

작업에는 mock EmailClient 를 ctx 로 전달한다.
startup 테스트에서는 클래스 자체를 patch 해 실제 SMTP 설정을 만들지 않는다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.identity.auth.task import send_verification_email
from app.workers.mail_worker import MailWorkerSettings

pytestmark = pytest.mark.anyio


@pytest.fixture
def fake_email_client():
    """워커가 ctx 에 저장하는 EmailClient 의 mock."""
    instance = MagicMock()
    instance.send_verification_email = AsyncMock()

    return instance


class TestMailWorkerStartup:
    async def test_initializes_email_client(self, fake_email_client):
        ctx = {}

        with patch(
            "app.workers.mail_worker.EmailClient", return_value=fake_email_client
        ) as factory:
            await MailWorkerSettings.on_startup(ctx)

        factory.assert_called_once_with()
        assert ctx["email_client"] is fake_email_client


class TestSendVerificationEmail:
    async def test_success(self, fake_email_client):
        ctx = {"email_client": fake_email_client}

        await send_verification_email(ctx, "user@example.com", "abc123")

        fake_email_client.send_verification_email.assert_awaited_once_with(
            email="user@example.com",
            code="abc123",
        )

    async def test_failure_is_reraised_for_retry(self, fake_email_client):
        """SMTP 오류 -> 예외를 그대로 올려서 arq 재시도를 유도한다."""
        ctx = {"email_client": fake_email_client}
        fake_email_client.send_verification_email.side_effect = RuntimeError("smtp down")

        with pytest.raises(RuntimeError):
            await send_verification_email(ctx, "user@example.com", "abc123")


class TestMailWorkerSettings:
    def test_job_is_registered(self):
        """
        서비스가 enqueue_job("send_verification_email", ...) 로 잡을 던지기 때문에
        워커에 함수가 등록돼 있지 않으면 런타임에야 실패한다. 배선을 미리 확인한다.
        """
        names = {fn.__name__ for fn in MailWorkerSettings.functions}

        assert "send_verification_email" in names

    def test_retry_policy(self):
        assert MailWorkerSettings.max_tries == 3
