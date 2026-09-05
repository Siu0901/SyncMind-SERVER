"""
전역 테스트 설정 + 공용 픽스처.

[중요 1] 환경변수 선주입
    app.core.config.Settings 는 필수 필드가 전부 required 라서,
    app.core.database / app.core.security / oauth.google / oauth.github 가
    "import 시점"에 get_settings() 를 호출하는 구조다.
    즉 app 패키지를 import 하기 전에 환경변수가 준비돼 있어야 한다.
    pydantic-settings 의 우선순위는 [os.environ] > [.env 파일] 이므로,
    아래에서 os.environ 을 먼저 채워두면 .env 없이도(CI 등) 테스트가 돈다.
    setdefault 를 쓰기 때문에 로컬에서 이미 export 한 값이 있으면 그 값이 유지된다.

[중요 2] 비동기 테스트 러너
    현재 프로젝트에 pytest-asyncio 가 설치돼 있지 않다.
    대신 httpx 가 끌고 오는 anyio 에 pytest 플러그인이 포함돼 있어서
    각 테스트 모듈 상단의 `pytestmark = pytest.mark.anyio` + 아래 anyio_backend
    픽스처 조합으로 async 테스트가 실행된다.
    (pytest-asyncio 를 추가하고 asyncio_mode="auto" 로 갈 거면
     각 모듈의 pytestmark 만 지우면 된다.)

[중요 3] lifespan 미실행
    httpx 의 ASGITransport 는 lifespan 을 실행하지 않는다.
    따라서 app.core.redis._pool 은 계속 None 이고 get_redis() 는 예외를 던진다.
    API 테스트에서는 반드시 dependency_overrides 로 redis/session 을 갈아끼워야 한다.
    (override_dependency 픽스처 참고)
"""

import os

# ---------------------------------------------------------------------------
# app import 보다 반드시 먼저 실행돼야 하는 블록 (E402 경고는 의도된 것)
# ---------------------------------------------------------------------------
_TEST_ENV = {
    # db : create_async_engine 이 import 시점에 만들어지지만 실제 커넥션은 지연되므로
    #      존재하지 않는 DB 를 가리켜도 무방하다. 단 드라이버(psycopg)는 설치돼 있어야 한다.
    "DATABASE_URL": "postgresql+psycopg://test:test@localhost:5432/test",
    "DB_ECHO": "False",
    "DB_POOL_SIZE": "5",
    "DB_MAX_OVERFLOW": "0",
    # redis / qdrant : 실제로 붙지 않는다 (전부 mock)
    "REDIS_URL": "redis://localhost:6379/15",
    "QDRANT_URL": "http://localhost:6333",
    "QDRANT_API_KEY": "",
    "QDRANT_COLLECTION": "test_documents",
    "EMBEDDING_DIMENSION": "8",
    # token : 테스트 내에서만 유효하면 되는 값
    "SECRET_KEY": "test-secret-key-for-unit-tests-32bytes-minimum",
    "JWT_ALGORITHM": "HS256",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
    "REFRESH_TOKEN_EXPIRE_DAY": "14",
    # oauth - google
    "GOOGLE_CLIENT_ID": "test-google-client-id",
    "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
    "GOOGLE_REDIRECT_URI": "http://testserver/auth/oauth/google/callback",
    "GOOGLE_AUTH_URL": "https://accounts.google.test/o/oauth2/v2/auth",
    "GOOGLE_TOKEN_URL": "https://oauth2.google.test/token",
    "GOOGLE_USERINFO_URL": "https://openidconnect.google.test/v1/userinfo",
    # oauth - github
    "GITHUB_CLIENT_ID": "test-github-client-id",
    "GITHUB_CLIENT_SECRET": "test-github-client-secret",
    "GITHUB_REDIRECT_URI": "http://testserver/auth/oauth/github/callback",
    "GITHUB_AUTH_URL": "https://github.test/login/oauth/authorize",
    "GITHUB_TOKEN_URL": "https://github.test/login/oauth/access_token",
    "GITHUB_USER_URL": "https://api.github.test/user",
    "GITHUB_EMAILS_URL": "https://api.github.test/user/emails",
    "FRONTEND_OAUTH_CALLBACK_URL": "http://localhost:3000/oauth/callback",
    # email : EmailClient 는 전부 patch 하므로 실제 발송되지 않는다
    "SMTP_PASSWORD": "test-smtp-password",
    "SMTP_FROM_EMAIL": "noreply@syncmind-test.com",
    "SMTP_USERNAME": "syncmind-test",
    "SMTP_PORT": "587",
    "SMTP_HOST": "smtp.syncmind-test.com",
}

for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

# ---------------------------------------------------------------------------

import json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402
from arq.connections import ArqRedis  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import AuthManager  # noqa: E402
from app.domains.auth.oauth.base import OAuthClient  # noqa: E402
from app.domains.auth.oauth.factory import OAuthClientFactory  # noqa: E402
from app.domains.auth.oauth.service import OAuthService  # noqa: E402
from app.domains.auth.repository import OAuthAccountRepository  # noqa: E402
from app.domains.auth.service import AuthService  # noqa: E402
from app.domains.user.model import User  # noqa: E402
from app.domains.user.repository import UserRepository  # noqa: E402


# ===========================================================================
# 러너 / 설정
# ===========================================================================
@pytest.fixture
def anyio_backend():
    """anyio 플러그인이 asyncio 백엔드로만 돌도록 고정 (trio 미설치)."""
    return "asyncio"


@pytest.fixture(scope="session")
def settings():
    """위에서 주입한 환경변수로 만들어진 Settings 싱글턴."""
    return get_settings()


# ===========================================================================
# 보안 / 유저 관련 공용 픽스처
# ===========================================================================
@pytest.fixture(scope="session")
def auth_manager() -> AuthManager:
    """
    AuthManager 는 상태가 없고 argon2 해싱이 느리기 때문에 session 스코프로 재사용한다.
    서비스 테스트에서 굳이 mock 하지 않는다 - 토큰/해시는 '진짜'여야 검증이 의미 있다.
    """
    return AuthManager()


@pytest.fixture(scope="session")
def raw_password() -> str:
    return "password123!"


@pytest.fixture(scope="session")
def hashed_password(auth_manager: AuthManager, raw_password: str) -> str:
    """argon2 해싱은 비싸므로 세션당 한 번만 계산해서 돌려쓴다."""
    return auth_manager.hash_password(raw_password)


# password_hash 인자를 "안 넘김" 과 "명시적으로 None(=소셜 전용 계정)" 으로 구분하기 위한 센티널
_UNSET = object()


@pytest.fixture
def make_user(hashed_password: str):
    """
    User 팩토리.

    created_at / updated_at 은 DB server_default 라서 파이썬에서 만든 인스턴스에는
    값이 없다. UserResponse(pydantic) 검증에 필요하므로 여기서 강제로 채워준다.

    password_hash=None 을 명시하면 '비밀번호 없는 소셜 전용 계정' 이 만들어진다.
    """

    def _make_user(
        user_id: int = 1,
        email: str = "user@example.com",
        *,
        password_hash: str | None = _UNSET,
        name: str = "테스터",
        is_active: bool = True,
        email_verified: bool = True,
        profile_image_url: str | None = None,
    ) -> User:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        user = User(
            id=user_id,
            email=email,
            password_hash=(
                hashed_password if password_hash is _UNSET else password_hash
            ),
            name=name,
            profile_image_url=profile_image_url,
            email_verified=email_verified,
            is_active=is_active,
        )
        user.created_at = now
        user.updated_at = now

        return user

    return _make_user


# ===========================================================================
# 인프라 mock (redis / session / repository)
# ===========================================================================
@pytest.fixture
def mock_redis() -> AsyncMock:
    """
    ArqRedis mock.

    spec 을 걸어 오타난 메서드 호출을 잡는다.
    (spec 이 걸린 mock 은 spec 에 없는 속성을 읽거나 대입하면 AttributeError 를 낸다)

    다만 redis-py 의 커맨드 메서드들은 `async def` 가 아니라
    'awaitable 을 돌려주는 일반 def' 라서, AsyncMock(spec=...) 이 이들을
    동기 MagicMock 으로 만들어버린다 -> `await` 시 TypeError.
    그래서 실제로 쓰는 커맨드는 AsyncMock 으로 명시적으로 덮어쓴다.

    또 AsyncMock 의 기본 반환값은 MagicMock(=truthy) 이라서
    `if not session:` / `if data is None:` 같은 분기가 의도와 반대로 타는 사고가 잦다.
    그래서 조회 계열은 전부 '비어 있는 상태'를 기본값으로 잡아둔다.
    """
    redis = AsyncMock(spec=ArqRedis)

    # 조회 계열 기본값 = 데이터 없음
    redis.get = AsyncMock(return_value=None)
    redis.hgetall = AsyncMock(return_value={})
    redis.smembers = AsyncMock(return_value=set())

    # 쓰기 계열
    redis.setex = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.hset = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.sadd = AsyncMock(return_value=1)

    # arq 잡 큐
    redis.enqueue_job = AsyncMock(return_value=None)

    return redis


@pytest.fixture
def mock_session() -> AsyncMock:
    """AsyncSession mock. add() 는 동기 메서드라 spec 덕분에 자동으로 MagicMock 이 된다."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def mock_user_repo() -> AsyncMock:
    """UserRepository mock. 기본값은 '해당 유저 없음'."""
    repo = AsyncMock(spec=UserRepository)

    repo.get_by_email.return_value = None
    repo.get_by_id.return_value = None

    # create() 는 DB 가 채워주는 PK 를 흉내내서 id 를 붙여준다.
    async def _create(user: User) -> User:
        if user.id is None:
            user.id = 1
        return user

    repo.create.side_effect = _create

    return repo


@pytest.fixture
def mock_oauth_repo() -> AsyncMock:
    """OAuthAccountRepository mock. 기본값은 '연결된 소셜 계정 없음'."""
    repo = AsyncMock(spec=OAuthAccountRepository)

    repo.get_by_provider_identity.return_value = None
    repo.get_by_user_provider.return_value = None
    repo.create.side_effect = lambda account: account

    return repo


@pytest.fixture
def mock_oauth_client() -> MagicMock:
    """
    OAuthClient mock.

    MagicMock(spec=...) 은 async def 멤버를 자동으로 AsyncMock 으로 만들어준다.
    → create_authorization_url 은 동기, get_user_info 는 비동기로 동작한다.
    """
    client = MagicMock(spec=OAuthClient)
    client.create_authorization_url.return_value = "https://provider.test/authorize?state=x"
    return client


@pytest.fixture
def mock_oauth_factory(mock_oauth_client: MagicMock) -> MagicMock:
    factory = MagicMock(spec=OAuthClientFactory)
    factory.get.return_value = mock_oauth_client
    return factory


# ===========================================================================
# 서비스 픽스처
# ===========================================================================
@pytest.fixture
def auth_service(
    mock_session: AsyncMock,
    mock_redis: AsyncMock,
    auth_manager: AuthManager,
    mock_user_repo: AsyncMock,
) -> AuthService:
    return AuthService(
        session=mock_session,
        redis=mock_redis,
        auth_manager=auth_manager,
        users_repo=mock_user_repo,
    )


@pytest.fixture
def oauth_service(
    mock_session: AsyncMock,
    mock_redis: AsyncMock,
    auth_manager: AuthManager,
    mock_user_repo: AsyncMock,
    mock_oauth_repo: AsyncMock,
    mock_oauth_factory: MagicMock,
) -> OAuthService:
    return OAuthService(
        session=mock_session,
        redis=mock_redis,
        auth_manager=auth_manager,
        users_repo=mock_user_repo,
        oauth_accounts=mock_oauth_repo,
        oauth_factory=mock_oauth_factory,
    )


# ===========================================================================
# redis 응답 헬퍼
# ===========================================================================
@pytest.fixture
def redis_session_value():
    """
    hgetall 이 돌려주는 세션 해시를 만들어준다.

    실제 redis 는 decode_responses 설정에 따라 str/bytes 를 둘 다 돌려줄 수 있고
    서비스 코드에도 그 분기(read 내부 함수)가 있어서, 두 형태 모두 테스트한다.
    """

    def _make(user_id: int, access_jti: str, refresh_jti: str, *, as_bytes: bool = False):
        raw = {
            "user_id": str(user_id),
            "access_jti": access_jti,
            "refresh_jti": refresh_jti,
        }

        if not as_bytes:
            return raw

        return {k.encode(): v.encode() for k, v in raw.items()}

    return _make


@pytest.fixture
def register_payload(auth_manager: AuthManager):
    """회원가입 대기 상태로 redis 에 저장돼 있는 JSON 문자열을 만들어준다."""

    def _make(
        code: str = "abc123",
        password_hash: str = "hashed",
        name: str = "테스터",
        *,
        as_bytes: bool = False,
    ):
        raw = json.dumps(
            {
                "code": code,
                "password_hash": password_hash,
                "name": name,
            }
        )

        return raw.encode() if as_bytes else raw

    return _make


# ===========================================================================
# API 테스트용
# ===========================================================================
@pytest.fixture
def app():
    """
    FastAPI 앱 인스턴스.

    모듈 전역 싱글턴이라 dependency_overrides 오염이 테스트 간에 전파된다.
    → override_dependency 픽스처에서 매 테스트 종료 시 clear() 한다.
    """
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture
def override_dependency(app):
    """
    app.dependency_overrides 를 안전하게 등록/정리해주는 헬퍼.

        override_dependency(get_auth_service, lambda: fake_service)
    """
    def _override(dependency, replacement):
        app.dependency_overrides[dependency] = replacement

    yield _override

    app.dependency_overrides.clear()


@pytest.fixture
async def client(app):
    """
    ASGI 직결 클라이언트 (실제 소켓 없음).

    raise_app_exceptions 기본값(True) 이라 핸들링되지 않은 예외는 그대로 터진다.
    500 핸들러 자체를 검증할 때는 아래 client_no_raise 를 쓴다.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
async def client_no_raise(app):
    """
    Starlette 의 ServerErrorMiddleware 는 500 응답을 만든 뒤 예외를 재-raise 한다.
    500 핸들러(handlers.py) 응답 본문을 확인하려면 재-raise 를 꺼야 한다.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        yield ac


# ===========================================================================
# 마커 등록
# ===========================================================================
def pytest_configure(config):
    """
    pyproject.toml 을 건드리지 않고 커스텀 마커를 등록한다.
    (pytest 설정을 pyproject 로 옮기게 되면 이 함수 대신
     [tool.pytest.ini_options] markers 항목으로 옮기면 된다)
    """
    config.addinivalue_line(
        "markers",
        "integration: 실제 PostgreSQL 이 필요한 테스트. "
        "TEST_DATABASE_URL 환경변수가 있을 때만 실행된다.",
    )
