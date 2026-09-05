"""
app/workers/email.py - arq 워커 잡 단위 테스트.

이 잡의 계약은 두 가지다.
    1. EmailClient 에 이메일/코드를 그대로 전달한다.
    2. 실패하면 예외를 삼키지 않고 다시 올린다
       -> arq 가 max_tries(=3) 만큼 재시도할 수 있어야 하기 때문. 삼키면 메일이 조용히 유실된다.

EmailClient 는 생성자에서 SMTP 설정을 만들기 때문에 클래스 자체를 patch 한다.
(실제 메일 발송은 절대 일어나면 안 된다)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.email import send_verification_email
from app.workers.settings import WorkerSettings

pytestmark = pytest.mark.anyio


@pytest.fixture
def fake_email_client():
    """app.workers.email 네임스페이스의 EmailClient 를 mock 으로 교체한다."""
    instance = MagicMock()
    instance.send_verification_email = AsyncMock()

    with patch("app.workers.email.EmailClient", return_value=instance) as factory:
        yield instance, factory


class TestSendVerificationEmail:
    async def test_success(self, fake_email_client):
        instance, _ = fake_email_client

        # 첫 인자 ctx 는 arq 가 넘겨주는 컨텍스트로, 이 잡에서는 사용하지 않는다.
        await send_verification_email({}, "user@example.com", "abc123")

        instance.send_verification_email.assert_awaited_once_with(
            email="user@example.com",
            code="abc123",
        )

    async def test_failure_is_reraised_for_retry(self, fake_email_client):
        """SMTP 오류 -> 예외를 그대로 올려서 arq 재시도를 유도한다."""
        instance, _ = fake_email_client
        instance.send_verification_email.side_effect = RuntimeError("smtp down")

        with pytest.raises(RuntimeError):
            await send_verification_email({}, "user@example.com", "abc123")


class TestWorkerSettings:
    def test_job_is_registered(self):
        """
        서비스가 enqueue_job("send_verification_email", ...) 로 잡을 던지기 때문에
        워커에 함수가 등록돼 있지 않으면 런타임에야 실패한다. 배선을 미리 확인한다.
        """
        names = {fn.__name__ for fn in WorkerSettings.functions}

        assert "send_verification_email" in names

    def test_retry_policy(self):
        assert WorkerSettings.max_tries == 3
