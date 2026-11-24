import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import LockNotOwnedError

from app.domains.chat.dependencies import get_chat_service
from app.domains.chat.exceptions import ConversationNotFoundError
from app.domains.chat.schema import (
    AgentCompleted, ChatAgentResult, ChatMessageResponse, ChatTextDelta, ConversationResponse,
    ChatTurnEnded, ChatTurnStarted, SendMessageRequest, SendMessageResponse,
)
from app.domains.chat.service import ChatService
from app.domains.workspace.dependencies import get_current_workspace_access
from app.domains.workspace.enums import WorkspaceRole
from app.domains.workspace.schema import WorkspaceAccessContext
from app.infra.llm.exceptions import LLMUnavailableError
from app.infra.llm.schema import (
    LLMAssistantTurn, LLMProviderState, LLMResponse, LLMStopReason, LLMUsage,
)


pytestmark = pytest.mark.anyio
PATH = "/workspaces/7/conversations/messages"
ACCESS = WorkspaceAccessContext(workspace_id=7, user_id=9, role=WorkspaceRole.MEMBER)


class MemoryRedis:
    def __init__(self):
        self.owners = {}

    def lock(self, name, **kwargs):
        token = object()

        async def acquire():
            if name in self.owners:
                return False
            self.owners[name] = token
            return True

        async def owned():
            return self.owners.get(name) is token

        async def release():
            if not await owned():
                raise LockNotOwnedError("Lease is no longer owned")
            del self.owners[name]

        return SimpleNamespace(acquire=acquire, owned=owned, release=release)


class StreamingAgent:
    timeout_seconds = 1

    def __init__(self):
        self.closed = False
        self.started = False
        self.failure = None
        self.wait_for_completion = None

    async def stream(self, **kwargs):
        self.started = True
        try:
            yield ChatTurnStarted(turn_id=1)
            yield ChatTextDelta(turn_id=1, text="Hello")
            if self.wait_for_completion is not None:
                await self.wait_for_completion.wait()
            if self.failure is not None:
                raise self.failure
            yield ChatTextDelta(turn_id=1, text=" world")
            yield ChatTurnEnded(turn_id=1, stop_reason=LLMStopReason.DONE)
            yield AgentCompleted(
                turn_id=1,
                result=ChatAgentResult(
                    response=LLMResponse(
                        message=LLMAssistantTurn(
                            text="Hello world",
                            provider_state=LLMProviderState(
                                provider="anthropic",
                                blocks=[{"private": "provider-only-state"}],
                            ),
                        ),
                        provider="anthropic", model="mock",
                        usage=LLMUsage(input_tokens=5, output_tokens=2),
                        stop_reason=LLMStopReason.DONE,
                    ),
                    usage=LLMUsage(input_tokens=5, output_tokens=2),
                    model_calls=1, tool_calls=0,
                ),
            )
        finally:
            self.closed = True


@pytest.fixture
def streaming_service(override_dependency):
    agent = StreamingAgent()
    redis = MemoryRedis()
    title_generator = SimpleNamespace(generate=AsyncMock(return_value="인사 나누기"))
    service = ChatService(
        session_factory=MagicMock(), agent=agent, redis=redis,
        title_generator=title_generator,
        stream_timeout_seconds=2,
    )
    now = datetime.now(timezone.utc)
    conversation = ConversationResponse(
        id=11, workspace_id=7, user_id=9, title=None, created_at=now, updated_at=now,
    )
    service.get_conversation = AsyncMock(return_value=conversation)
    service._create_conversation = AsyncMock(return_value=conversation)
    service._load_history = AsyncMock(return_value=([], None))
    committed = []

    async def save(**kwargs):
        committed.append(True)
        now = datetime.now(timezone.utc)
        common = dict(conversation_id=11, created_at=now, input_tokens=None, output_tokens=None)
        return SendMessageResponse(
            conversation_id=11,
            title=kwargs["title"],
            user_message=ChatMessageResponse(id=1, role="user", content="Hi", **common),
            assistant_message=ChatMessageResponse(id=2, role="assistant", content="Hello world", **common),
            citations=[], stop_reason=LLMStopReason.DONE,
        )

    service._save_turn = AsyncMock(side_effect=save)
    override_dependency(get_current_workspace_access, lambda: ACCESS)
    override_dependency(get_chat_service, lambda: service)
    return SimpleNamespace(service=service, agent=agent, redis=redis, committed=committed)


def parse_sse(body):
    events = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


async def test_sse_events_are_json_and_done_follows_persistence(client, streaming_service):
    response = await client.post(PATH, json={"conversation_id": 11, "content": "Hi"})
    events = parse_sse(response.text)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert [name for name, _ in events] == [
        "started", "turn_start", "text_delta", "text_delta", "turn_end", "done",
    ]
    assert events[2][1]["text"] == "Hello"
    assert events[-1][1]["response"]["assistant_message"]["id"] == 2
    assert events[-1][1]["response"]["title"] == "인사 나누기"
    assert streaming_service.committed == [True]
    assert "provider-only-state" not in response.text
    assert "agent_completed" not in response.text
    assert streaming_service.redis.owners == {}
    assert streaming_service.agent.closed
    streaming_service.service._create_conversation.assert_not_awaited()


async def test_first_message_creates_conversation_and_returns_id(client, streaming_service):
    response = await client.post(PATH, json={"content": "Hi"})
    events = parse_sse(response.text)

    assert response.status_code == 200
    assert events[0] == ("started", {"kind": "started", "conversation_id": 11})
    assert events[-1][1]["response"]["title"] == "인사 나누기"
    streaming_service.service._create_conversation.assert_awaited_once_with(ACCESS)
    streaming_service.service.get_conversation.assert_not_awaited()
    streaming_service.service.title_generator.generate.assert_awaited_once_with(
        question="Hi", answer="Hello world",
    )


async def test_follow_up_keeps_existing_title_without_generating_again(client, streaming_service):
    streaming_service.service.get_conversation.return_value.title = "기존 제목"
    response = await client.post(PATH, json={"conversation_id": 11, "content": "Hi"})
    events = parse_sse(response.text)

    assert events[-1][1]["response"]["title"] == "기존 제목"
    streaming_service.service._create_conversation.assert_not_awaited()
    streaming_service.service.title_generator.generate.assert_not_awaited()


async def test_title_failure_does_not_discard_the_answer(client, streaming_service):
    streaming_service.service.title_generator.generate.return_value = None
    response = await client.post(PATH, json={"conversation_id": 11, "content": "Hi"})
    events = parse_sse(response.text)

    assert events[-1][0] == "done"
    assert events[-1][1]["response"]["title"] is None
    assert streaming_service.committed == [True]


async def test_missing_conversation_fails_before_sse_headers(client, streaming_service):
    streaming_service.service.get_conversation.side_effect = ConversationNotFoundError()
    response = await client.post(PATH, json={"conversation_id": 11, "content": "Hi"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert not streaming_service.agent.started
    assert streaming_service.redis.owners == {}
    streaming_service.service._create_conversation.assert_not_awaited()


async def test_busy_conversation_returns_http_409_without_touching_other_lease(client, streaming_service):
    streaming_service.redis.owners["chat:generation:7:11"] = "another-request"
    response = await client.post(PATH, json={"conversation_id": 11, "content": "Hi"})

    assert response.status_code == 409
    assert not streaming_service.agent.started
    assert streaming_service.redis.owners["chat:generation:7:11"] == "another-request"
    streaming_service.service._load_history.assert_not_awaited()


async def test_preflight_failure_after_acquisition_releases_lease(client, streaming_service):
    streaming_service.service._load_history.side_effect = ConversationNotFoundError()
    response = await client.post(PATH, json={"conversation_id": 11, "content": "Hi"})

    assert response.status_code == 404
    assert streaming_service.redis.owners == {}
    assert not streaming_service.agent.started


async def test_provider_error_after_partial_text_is_sse_error(client, streaming_service):
    streaming_service.agent.failure = LLMUnavailableError("private provider error")
    response = await client.post(PATH, json={"conversation_id": 11, "content": "Hi"})
    events = parse_sse(response.text)

    assert response.status_code == 200
    assert any(name == "text_delta" for name, _ in events)
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "llm_failed"
    assert "private provider error" not in response.text
    assert not any(name == "done" for name, _ in events)
    streaming_service.service._save_turn.assert_not_awaited()
    assert streaming_service.redis.owners == {}
    assert streaming_service.agent.closed


async def test_save_failure_does_not_emit_done(client, streaming_service):
    streaming_service.service._save_turn.side_effect = RuntimeError("private db error")
    response = await client.post(PATH, json={"conversation_id": 11, "content": "Hi"})
    events = parse_sse(response.text)

    assert events[-1][0] == "error"
    assert events[-1][1]["status_code"] == 500
    assert not any(name == "done" for name, _ in events)
    assert "private db error" not in response.text
    assert streaming_service.redis.owners == {}


async def test_prepared_stream_is_closed_even_if_body_is_never_consumed(streaming_service):
    async with streaming_service.service.stream_message(
        ACCESS, SendMessageRequest(conversation_id=11, content="Hi"),
    ):
        assert streaming_service.redis.owners
        assert not streaming_service.agent.started

    assert streaming_service.redis.owners == {}
    streaming_service.service._save_turn.assert_not_awaited()


async def test_text_is_sent_before_completion_and_disconnect_cleans_up(app, streaming_service):
    streaming_service.agent.wait_for_completion = asyncio.Event()
    disconnected = asyncio.Event()
    saw_text = asyncio.Event()
    request_sent = False
    messages = []

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b'{"conversation_id":11,"content":"Hi"}', "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and b"event: text_delta" in message.get("body", b""):
            assert not streaming_service.committed
            saw_text.set()

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": PATH, "raw_path": PATH.encode(), "query_string": b"",
        "root_path": "", "headers": [(b"content-type", b"application/json")],
        "server": ("test", 80), "client": ("test", 1234),
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(saw_text.wait(), timeout=2)
        assert not task.done()
        disconnected.set()
        await asyncio.wait_for(task, timeout=2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert streaming_service.agent.closed
    assert streaming_service.redis.owners == {}
    streaming_service.service._save_turn.assert_not_awaited()
    assert not any(b"event: done" in message.get("body", b"") for message in messages)


async def test_invalid_request_is_rejected_without_acquiring_a_lease(client, streaming_service):
    response = await client.post(PATH, json={"content": "   "})
    assert response.status_code == 422
    assert streaming_service.redis.owners == {}
    streaming_service.service.get_conversation.assert_not_awaited()
    streaming_service.service._create_conversation.assert_not_awaited()


@pytest.mark.parametrize("conversation_id", [0, -1, "invalid"])
async def test_invalid_conversation_id_never_creates_a_conversation(client, streaming_service, conversation_id):
    response = await client.post(PATH, json={"conversation_id": conversation_id, "content": "Hi"})
    assert response.status_code == 422
    streaming_service.service._create_conversation.assert_not_awaited()
