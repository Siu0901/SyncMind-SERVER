from asyncio import CancelledError
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.dependencies.utils import get_dependant
from fastapi.dependencies.utils import get_dependant
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.database import get_session
from app.core.dependencies import get_session_factory
from app.domains.chat.agent import ChatAgent
from app.domains.chat.dependencies import ChatAgentDep, CurrentChatAccessDep
from app.domains.identity.auth.dependencies import get_current_user_id
from app.domains.identity.auth.exceptions import InactiveUserError, TokenInvalidError
from app.domains.knowledge.dependencies import get_retrieval_service
from app.domains.knowledge.router import search_documents
from app.domains.knowledge.schema import RetrievalResult
from app.domains.knowledge.services.retrieval import RetrievalService
from app.domains.workspace.dependencies import get_current_workspace_access
from app.domains.workspace.enums import WorkspaceRole
from app.domains.workspace.exceptions import (
    WorkspaceMemberNotFoundError,
    WorkspaceNotFoundError,
)
from app.infra.llm.schema import (
    LLMAssistantTurn,
    LLMProviderState,
    LLMResponse,
    LLMStopReason,
    LLMToolCall,
    LLMUsage,
)
from app.infra.reranking.port import RerankResult


pytestmark = pytest.mark.anyio


async def chat_scope_probe(access: CurrentChatAccessDep, agent: ChatAgentDep):
    pass


@pytest.mark.parametrize("endpoint", [search_documents, chat_scope_probe])
def test_search_and_chat_do_not_depend_on_request_scoped_sessions(endpoint):
    dependency = get_dependant(path="/workspaces/{workspace_id}", call=endpoint)

    def calls(node):
        result = [node.call]
        for child in node.dependencies:
            result.extend(calls(child))
        return result

    assert get_session not in calls(dependency)


class TrackingSessionFactory:
    def __init__(self, execute):
        self.execute = execute
        self.active = 0
        self.sessions = []

    @asynccontextmanager
    async def __call__(self):
        session = AsyncMock(spec=AsyncSession)
        session.exec.side_effect = self.execute
        self.sessions.append(session)
        self.active += 1
        try:
            yield session
        finally:
            await session.close()
            self.active -= 1


def candidate():
    return RetrievalResult(
        chunk_id=1,
        document_id=2,
        document_version_id=3,
        title="Policy",
        content="Evidence",
        retrieval_score=0.8,
    )


def retrieval_service(factory, reranker=None):
    async def embed(query):
        assert factory.active == 0
        return [0.1, 0.2]

    async def search(**kwargs):
        assert factory.active == 0
        return [candidate()]

    return RetrievalService(
        embedding=SimpleNamespace(embed_query=AsyncMock(side_effect=embed)),
        vector_repo=SimpleNamespace(hybrid_search=AsyncMock(side_effect=search)),
        session_factory=factory,
        reranker=reranker,
        rerank_candidate_limit=10,
    )


async def test_read_session_is_closed_before_reranking():
    async def execute(statement):
        assert factory.active == 1
        return SimpleNamespace(all=lambda: [3])

    factory = TrackingSessionFactory(execute)

    async def rerank(**kwargs):
        assert factory.active == 0
        factory.sessions[0].close.assert_awaited_once()
        return [RerankResult(chunk_id=1, score=0.9)]

    service = retrieval_service(
        factory,
        SimpleNamespace(rerank=AsyncMock(side_effect=rerank)),
    )
    results = await service.search(workspace_id=7, query="policy")

    assert results[0].rerank_score == 0.9
    assert factory.active == 0


@pytest.mark.parametrize("error", [RuntimeError("query failed"), CancelledError()])
async def test_failed_or_cancelled_query_closes_session(error):
    async def execute(statement):
        raise error

    factory = TrackingSessionFactory(execute)
    service = retrieval_service(factory)

    with pytest.raises(type(error)):
        await service.search(workspace_id=7, query="policy")

    assert factory.active == 0
    factory.sessions[0].close.assert_awaited_once()


async def test_empty_candidates_do_not_open_a_session():
    factory = TrackingSessionFactory(AsyncMock())
    service = retrieval_service(factory)
    service.vector_repo.hybrid_search = AsyncMock(return_value=[])

    assert await service.search(workspace_id=7, query="missing") == []
    assert factory.sessions == []


async def test_agent_releases_each_search_session_before_next_llm_call():
    async def execute(statement):
        assert factory.active == 1
        return SimpleNamespace(all=lambda: [3])

    factory = TrackingSessionFactory(execute)
    responses = []
    for index in range(3):
        calls = [] if index == 2 else [LLMToolCall(
            id=f"call_{index}",
            name="search_knowledge",
            arguments={"query": f"policy {index}"},
        )]
        responses.append(LLMResponse(
            message=LLMAssistantTurn(
                text="Answer [chunk:1]" if not calls else "",
                tool_calls=calls,
                provider_state=LLMProviderState(provider="anthropic", blocks=[]),
            ),
            provider="anthropic",
            model="test",
            usage=LLMUsage(input_tokens=5, output_tokens=2),
            stop_reason=LLMStopReason.TOOL_USE if calls else LLMStopReason.DONE,
        ))

    async def generate(request):
        assert factory.active == 0
        for session in factory.sessions:
            session.close.assert_awaited_once()
        return responses.pop(0)

    agent = ChatAgent(
        llm=SimpleNamespace(generate=AsyncMock(side_effect=generate)),
        retrieval=retrieval_service(factory),
    )
    result = await agent.run(workspace_id=7, question="What is the policy?")

    assert result.model_calls == 3
    assert result.tool_calls == 2
    assert len(factory.sessions) == 2
    assert factory.sessions[0] is not factory.sessions[1]
    assert result.sources == [candidate()]


@pytest.mark.parametrize("failure,expected_error", [
    (None, None),
    ("deleted_user", TokenInvalidError),
    ("inactive_user", InactiveUserError),
    ("missing_workspace", WorkspaceNotFoundError),
    ("missing_member", WorkspaceMemberNotFoundError),
])
async def test_workspace_access_closes_session_on_success_and_denial(failure, expected_error):
    values = iter([
        None if failure == "deleted_user" else SimpleNamespace(
            is_active=failure != "inactive_user",
        ),
        None if failure == "missing_workspace" else SimpleNamespace(id=7),
        None if failure == "missing_member" else SimpleNamespace(role=WorkspaceRole.MEMBER),
    ])

    async def execute(statement):
        assert factory.active == 1
        value = next(values)
        return SimpleNamespace(first=lambda: value)

    factory = TrackingSessionFactory(execute)
    if expected_error:
        with pytest.raises(expected_error):
            await get_current_workspace_access(7, 9, factory)
    else:
        access = await get_current_workspace_access(7, 9, factory)
        assert access.workspace_id == 7
        assert access.user_id == 9
        assert access.role == WorkspaceRole.MEMBER

    assert factory.active == 0
    factory.sessions[0].close.assert_awaited_once()


def test_search_and_chat_dependency_graphs_have_no_request_session():
    def chat_endpoint(access: CurrentChatAccessDep, agent: ChatAgentDep):
        pass

    def calls(dependency):
        yield dependency.call
        for child in dependency.dependencies:
            yield from calls(child)

    for endpoint in (search_documents, chat_endpoint):
        dependencies = set(calls(get_dependant(
            path="/workspaces/{workspace_id}",
            call=endpoint,
        )))
        assert get_session not in dependencies
        assert get_session_factory in dependencies


async def test_search_endpoint_releases_auth_session_before_retrieval(
    client, override_dependency,
):
    values = iter([
        SimpleNamespace(is_active=True),
        SimpleNamespace(id=7),
        SimpleNamespace(role=WorkspaceRole.MEMBER),
    ])

    async def execute(statement):
        if len(factory.sessions) == 1:
            value = next(values)
            return SimpleNamespace(first=lambda: value)
        return SimpleNamespace(all=lambda: [3])

    factory = TrackingSessionFactory(execute)
    override_dependency(get_current_user_id, lambda: 9)
    override_dependency(get_session_factory, lambda: factory)
    override_dependency(get_retrieval_service, lambda: retrieval_service(factory))

    response = await client.post(
        "/workspaces/7/documents/search",
        json={"query": "policy", "limit": 5},
    )

    assert response.status_code == 200
    assert response.json()[0]["chunk_id"] == 1
    assert factory.active == 0
    assert len(factory.sessions) == 2
    for session in factory.sessions:
        session.close.assert_awaited_once()
