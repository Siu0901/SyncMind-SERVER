import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.domains.chat.agent import ChatAgent
from app.domains.chat.exceptions import ConversationNotFoundError
from app.domains.chat.model import Conversation
from app.domains.chat.schema import ConversationUpdateRequest, SendMessageRequest
from app.domains.chat.service import ChatService
from app.domains.chat.title import ConversationTitleGenerator
from app.domains.identity.user.model import User
from app.domains.knowledge.document.model import (
    Document, DocumentChunk, DocumentStatus, DocumentVersion,
)
from app.domains.knowledge.schema import RetrievalResult
from app.domains.knowledge.services.retrieval import RetrievalService
from app.domains.workspace.enums import WorkspaceRole
from app.domains.workspace.model import WorkSpace
from app.domains.workspace.schema import WorkspaceAccessContext
from app.infra.llm.exceptions import LLMUnavailableError
from app.infra.llm.schema import (
    LLMAssistantTurn, LLMCompleted, LLMProviderState, LLMResponse,
    LLMStopReason, LLMTextDelta, LLMToolCall, LLMUsage,
)


pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def isolated_redis():
    client = Redis.from_url(get_settings().REDIS_URL)
    try:
        await client.ping()
    except RedisError:
        await client.aclose()
        pytest.skip("Redis is unavailable")

    prefix = f"test:chat:{uuid4().hex}:"
    keys = set()

    def lock(name, **kwargs):
        key = prefix + name
        keys.add(key)
        return client.lock(key, **kwargs)

    try:
        yield SimpleNamespace(client=client, lock=lock, keys=keys)
    finally:
        if keys:
            await client.delete(*keys)
        await client.aclose()


async def seed(session, *, create_conversation=True):
    user = User(email=f"stream-{uuid4().hex}@example.com", name="Stream test")
    workspace = WorkSpace(name="Stream test")
    session.add_all([user, workspace])
    await session.flush()

    conversation = Conversation(workspace_id=workspace.id, user_id=user.id)
    document = Document(
        workspace_id=workspace.id, title="Evidence", status=DocumentStatus.READY,
        current_version=1,
    )
    session.add(document)
    if create_conversation:
        session.add(conversation)
    await session.flush()
    version = DocumentVersion(
        document_id=document.id, version=1, content_hash="b" * 64, is_active=True,
    )
    session.add(version)
    await session.flush()
    chunk = DocumentChunk(document_version_id=version.id, chunk_index=0, content="Evidence")
    session.add(chunk)
    await session.commit()
    return SimpleNamespace(
        access=WorkspaceAccessContext(
            workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER,
        ),
        conversation_id=conversation.id,
        source=RetrievalResult(
            chunk_id=chunk.id, document_id=document.id, document_version_id=version.id,
            chunk_index=0, title="Evidence", content="Evidence", retrieval_score=0.8,
        ),
    )


def make_service(db_engine, data, redis):
    factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    retrieval = RetrievalService(
        embedding=SimpleNamespace(embed_query=AsyncMock(return_value=[0.1])),
        vector_repo=SimpleNamespace(hybrid_search=AsyncMock(return_value=[data.source])),
        session_factory=factory, reranker=None, rerank_candidate_limit=10,
    )
    plan = [
        ("search_knowledge", {"query": "Evidence"}),
        ("read_document", {"document_version_id": data.source.document_version_id}),
        ("read_adjacent_chunks", {"chunk_id": data.source.chunk_id}),
        None,
    ]
    closed = []
    model_requests = []

    async def stream(request):
        assert db_engine.pool.checkedout() == 0
        step = plan[len(model_requests) % len(plan)]
        model_requests.append(request)
        try:
            calls = [] if step is None else [LLMToolCall(
                id=f"call_{len(model_requests)}", name=step[0], arguments=step[1],
            )]
            answer = "" if calls else f"Evidence [chunk:{data.source.chunk_id}]"
            if answer:
                yield LLMTextDelta(text="Evidence ")
                yield LLMTextDelta(text=f"[chunk:{data.source.chunk_id}]")
            yield LLMCompleted(response=LLMResponse(
                message=LLMAssistantTurn(
                    text=answer, tool_calls=calls,
                    provider_state=LLMProviderState(provider="anthropic", blocks=[]),
                ),
                provider="anthropic", model="mock",
                usage=LLMUsage(input_tokens=5, output_tokens=2),
                stop_reason=LLMStopReason.TOOL_USE if calls else LLMStopReason.DONE,
            ))
        finally:
            closed.append(True)

    async def generate(request):
        assert db_engine.pool.checkedout() == 0
        return LLMResponse(
            message=LLMAssistantTurn(
                text="검색 근거 살펴보기",
                provider_state=LLMProviderState(provider="anthropic", blocks=[]),
            ),
            provider="anthropic", model="mock",
            usage=LLMUsage(input_tokens=5, output_tokens=2), stop_reason=LLMStopReason.DONE,
        )

    llm = SimpleNamespace(stream=stream, generate=AsyncMock(side_effect=generate), requests=model_requests)
    agent = ChatAgent(llm=llm, retrieval=retrieval)
    return ChatService(factory, agent, redis, ConversationTitleGenerator(llm)), closed


async def test_stream_search_read_save_and_commit_before_done(db_engine, db_session, isolated_redis):
    data = await seed(db_session)
    service, closed = make_service(db_engine, data, isolated_redis)
    names = []

    async with service.stream_message(
        data.access, SendMessageRequest(conversation_id=data.conversation_id, content="Explain the evidence"),
    ) as events:
        async for event in events:
            names.append(event["event"])
            payload = json.loads(event["data"])
            assert event["event"] != "agent_completed"
            if event["event"] == "done":
                assert db_engine.pool.checkedout() == 0
                assert payload["response"]["title"] == "검색 근거 살펴보기"
                async with service.session_factory() as observer:
                    messages = (await observer.exec(text(
                        "SELECT role, input_tokens, output_tokens FROM chat_message "
                        "WHERE conversation_id = :id ORDER BY id"
                    ).bindparams(id=data.conversation_id))).all()
                    assert [row[0] for row in messages] == ["USER", "ASSISTANT"]
                    assert tuple(messages[-1][1:]) == (20, 8)
                    citation = (await observer.exec(text(
                        "SELECT chunk_id FROM message_citation WHERE message_id = :id"
                    ).bindparams(id=payload["response"]["assistant_message"]["id"]))).one()
                    assert citation[0] == data.source.chunk_id

    assert names.count("tool_start") == 3
    assert names.count("text_delta") == 2
    assert names[-1] == "done"
    assert len(closed) == 4
    for key in isolated_redis.keys:
        assert not await isolated_redis.client.exists(key)


async def collect(service, access, request):
    async with service.stream_message(access, request) as events:
        return [json.loads(event["data"]) async for event in events]


async def test_first_message_creates_once_follow_up_reuses_history_and_title(db_engine, db_session, isolated_redis):
    data = await seed(db_session, create_conversation=False)
    service, closed = make_service(db_engine, data, isolated_redis)

    first = await collect(service, data.access, SendMessageRequest(content="Explain the evidence"))
    assert first[-1]["kind"] == "done"
    conversation_id = first[0]["conversation_id"]
    assert first[-1]["response"]["conversation_id"] == conversation_id
    assert first[-1]["response"]["title"] == "검색 근거 살펴보기"

    second = await collect(service, data.access, SendMessageRequest(
        conversation_id=conversation_id, content="Tell me more",
    ))
    assert second[-1]["kind"] == "done"
    assert second[0]["conversation_id"] == conversation_id
    assert second[-1]["response"]["title"] == "검색 근거 살펴보기"
    assert len(closed) == 8
    service.agent.llm.generate.assert_awaited_once()
    history = service.agent.llm.requests[4].messages
    assert [message.role for message in history] == ["user", "assistant", "user"]
    assert history[0].content == "Explain the evidence"
    assert history[-1].content == "Tell me more"

    async with service.session_factory() as observer:
        conversations = (await observer.exec(text("SELECT id, title FROM conversation"))).all()
        assert [tuple(row) for row in conversations] == [(conversation_id, "검색 근거 살펴보기")]
        messages = (await observer.exec(text(
            "SELECT conversation_id FROM chat_message ORDER BY id"
        ))).all()
        assert [row[0] for row in messages] == [conversation_id] * 4


async def test_manual_title_update_during_generation_is_preserved(db_engine, db_session, isolated_redis):
    data = await seed(db_session)
    service, _ = make_service(db_engine, data, isolated_redis)
    generate = service.agent.llm.generate.side_effect

    async def rename_during_generation(request):
        await service.update_conversation(
            data.access, data.conversation_id, ConversationUpdateRequest(title="직접 정한 제목"),
        )
        return await generate(request)

    service.agent.llm.generate.side_effect = rename_during_generation
    events = await collect(service, data.access, SendMessageRequest(
        conversation_id=data.conversation_id, content="Explain the evidence",
    ))
    assert events[-1]["kind"] == "done"
    assert events[-1]["response"]["title"] == "직접 정한 제목"
    saved = await service.get_conversation(data.access, data.conversation_id)
    assert saved.title == "직접 정한 제목"


async def test_failed_title_generation_saves_answer_and_retries_on_next_message(db_engine, db_session, isolated_redis):
    data = await seed(db_session, create_conversation=False)
    service, _ = make_service(db_engine, data, isolated_redis)
    generate = service.agent.llm.generate.side_effect
    service.agent.llm.generate.side_effect = LLMUnavailableError("Mock provider unavailable")

    first = await collect(service, data.access, SendMessageRequest(content="Explain the evidence"))
    assert first[-1]["kind"] == "done"
    assert first[-1]["response"]["title"] is None
    conversation_id = first[0]["conversation_id"]

    service.agent.llm.generate.side_effect = generate
    second = await collect(service, data.access, SendMessageRequest(
        conversation_id=conversation_id, content="Tell me more",
    ))
    assert second[-1]["kind"] == "done"
    assert second[-1]["response"]["title"] == "검색 근거 살펴보기"
    assert service.agent.llm.generate.await_count == 2
    async with service.session_factory() as observer:
        assert (await observer.exec(text("SELECT count(*) FROM conversation"))).scalar_one() == 1
        assert (await observer.exec(text("SELECT count(*) FROM chat_message"))).scalar_one() == 4


async def test_foreign_conversation_is_rejected_without_creating_a_new_one(db_engine, db_session, isolated_redis):
    data = await seed(db_session)
    service, closed = make_service(db_engine, data, isolated_redis)
    foreign_access = data.access.model_copy(update={"user_id": data.access.user_id + 1})

    with pytest.raises(ConversationNotFoundError):
        async with service.stream_message(foreign_access, SendMessageRequest(
            conversation_id=data.conversation_id, content="Explain the evidence",
        )):
            pytest.fail("A foreign conversation must fail before streaming")

    assert closed == []
    service.agent.llm.generate.assert_not_awaited()
    async with service.session_factory() as observer:
        assert (await observer.exec(text("SELECT count(*) FROM conversation"))).scalar_one() == 1
        assert (await observer.exec(text("SELECT count(*) FROM chat_message"))).scalar_one() == 0


async def test_closing_stream_before_answer_does_not_save_messages(db_engine, db_session, isolated_redis):
    data = await seed(db_session)
    service, closed = make_service(db_engine, data, isolated_redis)

    async with service.stream_message(
        data.access, SendMessageRequest(conversation_id=data.conversation_id, content="Explain the evidence"),
    ) as events:
        async for event in events:
            if event["event"] == "text_delta":
                break

    assert len(closed) == 4
    async with service.session_factory() as observer:
        count = (await observer.exec(text(
            "SELECT count(*) FROM chat_message WHERE conversation_id = :id"
        ).bindparams(id=data.conversation_id))).scalar_one()
        assert count == 0
    for key in isolated_redis.keys:
        assert not await isolated_redis.client.exists(key)
