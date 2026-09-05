"""
리포지토리 통합 테스트용 DB 픽스처.

왜 별도 계층인가
    Repository 는 SQL 을 실제로 실행하는 계층이라 mock 으로 검증할 게 없다.
    unique 제약 / server_default / 트랜잭션 경계 같은 건 진짜 PostgreSQL 에서만 드러난다.
    (SQLite 는 BigInteger, server_default=now(), UniqueConstraint 동작이 달라서 대체재가 못 된다)

왜 기본 실행에서 빠지는가
    개발용 DB 에 테스트 데이터를 쓰는 사고를 막기 위해 '명시적 옵트인' 으로 만들었다.
    TEST_DATABASE_URL 이 설정돼 있을 때만 실행된다.

    # docker compose up -d 로 postgres 를 띄운 뒤, 테스트 전용 DB 를 하나 만들고
    createdb -h localhost -U syncmind syncmind_test
    TEST_DATABASE_URL='postgresql+psycopg://syncmind:changeme@localhost:5432/syncmind_test' \
        uv run pytest test/integration

    URL 은 반드시 async 드라이버(postgresql+psycopg)여야 한다.
"""

import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, text
from sqlmodel.ext.asyncio.session import AsyncSession

# 모든 테이블 메타데이터를 등록하기 위한 import (side effect 목적)
import app.models  # noqa: F401

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

# 테스트에서 실제로 건드리는 테이블. 매 테스트 후 비운다.
_TABLES_TO_CLEAN = ('"oauth_account"', '"user"')


@pytest.fixture(scope="session")
def anyio_backend():
    """세션 스코프 async 픽스처(db_engine)를 쓰려면 백엔드 픽스처도 세션 스코프여야 한다."""
    return "asyncio"


@pytest.fixture(scope="session")
async def db_engine():
    """테스트 DB 엔진. URL 이 없거나 접속 불가면 모듈 전체를 skip 한다."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL 이 없어 리포지토리 통합 테스트를 건너뜁니다.")

    engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_pre_ping=True)

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - 환경 문제
        await engine.dispose()
        pytest.skip(f"테스트 DB 에 접속할 수 없습니다: {exc}")

    # alembic 대신 메타데이터로 스키마를 만든다.
    # (마이그레이션 자체를 검증하고 싶다면 여기서 `alembic upgrade head` 를 돌리면 된다)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """
    테스트 1개 = 세션 1개.

    UserRepository.create() 가 내부에서 commit 을 하기 때문에 rollback 으로는
    정리가 안 된다. 그래서 테스트가 끝나면 TRUNCATE 로 확실히 비운다.
    """
    factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with factory() as session:
        yield session

    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                f"TRUNCATE TABLE {', '.join(_TABLES_TO_CLEAN)} "
                "RESTART IDENTITY CASCADE"
            )
        )
