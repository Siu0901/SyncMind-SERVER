from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.knowledge.services.retrieval import RetrievalService


pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_retrieval_read_returns_connection_to_pool(db_engine):
    factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    service = RetrievalService(
        embedding=AsyncMock(),
        vector_repo=AsyncMock(),
        session_factory=factory,
        reranker=None,
        rerank_candidate_limit=10,
    )
    baseline = db_engine.pool.checkedout()
    checked_out_during_query = []

    def record_checkout(*args):
        checked_out_during_query.append(db_engine.pool.checkedout())

    event.listen(db_engine.sync_engine, "after_cursor_execute", record_checkout)
    try:
        await service._get_searchable_version_ids(
            workspace_id=999_999,
            version_ids=[999_999],
        )
    finally:
        event.remove(db_engine.sync_engine, "after_cursor_execute", record_checkout)

    assert checked_out_during_query
    assert all(count == baseline + 1 for count in checked_out_during_query)
    assert db_engine.pool.checkedout() == baseline
