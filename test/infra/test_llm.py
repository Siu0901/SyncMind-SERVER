from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest
from anthropic.types import Message
from openai.types.responses import Response, ResponseOutputMessage, ResponseUsage
from pydantic import SecretStr

from app.infra.llm import factory
from app.infra.llm.adapter.claude_adapter import AnthropicLLMAdapter
from app.infra.llm.adapter.openai_adapter import OpenAILLMAdapter
from app.infra.llm.exceptions import LLMRequestError, LLMUnavailableError
from app.infra.llm.schema import (
    LLMCompleted, LLMMessage, LLMRequest, LLMStopReason, LLMTextDelta,
)

pytestmark = pytest.mark.anyio


class FakeStream:
    def __init__(self, events, final=None):
        self.events = events
        self.final = final
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def __aiter__(self):
        for event in self.events:
            yield event

    async def get_final_message(self):
        return self.final


def request():
    return LLMRequest(messages=[LLMMessage(role="user", content="hello")], system="help", max_output_tokens=100)


def claude_response():
    return Message.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": "hello"}],
        "model": "claude-test", "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 2},
    })


def openai_response(status="completed", refusal=None):
    content = [{"type": "refusal", "refusal": refusal}] if refusal else [{"type": "output_text", "text": "hello", "annotations": []}]
    return Response.model_construct(
        id="resp_test", object="response", created_at=1,
        model="gpt-test", status=status,
        output=[ResponseOutputMessage.model_validate({
            "id": "msg_test", "type": "message", "role": "assistant",
            "status": "completed", "content": content,
        })],
        usage=ResponseUsage.model_construct(input_tokens=3, output_tokens=2, total_tokens=5),
        incomplete_details=Obj(reason="max_output_tokens") if status == "incomplete" else None,
    )


async def test_claude_generation_maps_request_and_response():
    create = AsyncMock(return_value=claude_response())
    adapter = AnthropicLLMAdapter(Obj(messages=Obj(create=create)), "claude-test", 200)
    result = await adapter.generate(request())
    assert (result.message.text, result.model, result.usage.input_tokens, result.stop_reason) == ("hello", "claude-test", 3, LLMStopReason.DONE)
    assert create.await_args.kwargs == {"model": "claude-test", "messages": [{"role": "user", "content": "hello"}], "system": "help", "max_tokens": 100}


async def test_claude_stream_completes_and_closes():
    stream = FakeStream([Obj(type="content_block_delta", delta=Obj(type="text_delta", text="hello")), Obj(type="message_stop")], claude_response())
    adapter = AnthropicLLMAdapter(Obj(messages=Obj(stream=lambda **kwargs: stream)), "claude-test", 200)
    events = [event async for event in adapter.stream(request())]
    assert events[0] == LLMTextDelta(text="hello")
    assert isinstance(events[-1], LLMCompleted)
    assert events[-1].response.message.text == "hello"
    assert stream.closed


async def test_claude_stream_truncation_is_error():
    stream = FakeStream([Obj(type="content_block_delta", delta=Obj(type="text_delta", text="partial"))], claude_response())
    adapter = AnthropicLLMAdapter(Obj(messages=Obj(stream=lambda **kwargs: stream)), "claude-test", 200)
    with pytest.raises(LLMUnavailableError):
        _ = [event async for event in adapter.stream(request())]
    assert stream.closed


async def test_openai_generation_maps_request_and_refusal():
    create = AsyncMock(return_value=openai_response(refusal="cannot help"))
    adapter = OpenAILLMAdapter(Obj(responses=Obj(create=create)), "gpt-test", 200)
    result = await adapter.generate(request())
    assert (result.message.text, result.stop_reason) == ("cannot help", LLMStopReason.REFUSED)
    assert create.await_args.kwargs == {"model": "gpt-test", "input": [{"role": "user", "content": "hello"}], "instructions": "help", "max_output_tokens": 100, "store": False, "include": ["reasoning.encrypted_content"]}


async def test_openai_incomplete_stream_is_reported():
    stream = FakeStream([Obj(type="response.output_text.delta", delta="hello"), Obj(type="response.incomplete", response=openai_response("incomplete"))])
    create = AsyncMock(return_value=stream)
    adapter = OpenAILLMAdapter(Obj(responses=Obj(create=create)), "gpt-test", 200)
    events = [event async for event in adapter.stream(request())]
    assert events[0] == LLMTextDelta(text="hello")
    assert events[-1].response.stop_reason == LLMStopReason.TRUNCATED
    assert create.await_args.kwargs["stream"] is True
    assert stream.closed


@pytest.mark.parametrize("events,error", [([], LLMUnavailableError), ([Obj(type="response.failed")], LLMRequestError)])
async def test_openai_stream_failure_is_not_completion(events, error):
    stream = FakeStream(events)
    adapter = OpenAILLMAdapter(Obj(responses=Obj(create=AsyncMock(return_value=stream))), "gpt-test", 200)
    with pytest.raises(error):
        _ = [event async for event in adapter.stream(request())]
    assert stream.closed


async def test_factory_selects_provider_and_validates_key(monkeypatch):
    settings = Obj(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY=SecretStr("test"), ANTHROPIC_LLM_MODEL="claude-haiku-5-5", OPENAI_API_KEY=SecretStr("test"), OPENAI_LLM_MODEL="gpt-test", LLM_TIMEOUT_SECONDS=10, LLM_MAX_RETRIES=0, LLM_MAX_OUTPUT_TOKENS=100)
    monkeypatch.setattr(factory, "get_settings", lambda: settings)
    anthropic = factory.create_llm()
    assert isinstance(anthropic, AnthropicLLMAdapter)
    await anthropic.close()
    settings.LLM_PROVIDER = "openai"
    openai = factory.create_llm()
    assert isinstance(openai, OpenAILLMAdapter)
    await openai.close()
    settings.LLM_PROVIDER = "anthropic"
    settings.ANTHROPIC_API_KEY = None
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        factory.create_llm()
    settings.LLM_PROVIDER = "unknown"
    with pytest.raises(ValueError, match="Unsupported"):
        factory.create_llm()


async def test_anthropic_sdk_stream_events():
    import json
    import anthropic
    import httpx2

    message = {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-test", "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 3, "output_tokens": 0}}
    events = [
        ("message_start", {"type": "message_start", "message": message}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    client = anthropic.AsyncAnthropic(api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(lambda req: httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"}))))
    adapter = AnthropicLLMAdapter(client, "claude-test", 100)
    try:
        result = [event async for event in adapter.stream(request())]
        assert result[0] == LLMTextDelta(text="hello")
        assert result[-1].response.message.text == "hello"
        assert result[-1].response.usage.output_tokens == 2
    finally:
        await adapter.close()


async def test_openai_sdk_stream_events():
    import json
    import httpx2
    import openai

    response = {"id": "resp_test", "object": "response", "created_at": 1, "status": "completed", "model": "gpt-test", "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "hello", "annotations": []}]}], "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
    events = [{"type": "response.output_text.delta", "delta": "hello", "item_id": "msg_test", "output_index": 0, "content_index": 0, "sequence_number": 1, "logprobs": []}, {"type": "response.completed", "response": response, "sequence_number": 2}]
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
    client = openai.AsyncOpenAI(api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(lambda req: httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"}))))
    adapter = OpenAILLMAdapter(client, "gpt-test", 100)
    try:
        result = [event async for event in adapter.stream(request())]
        assert result[0] == LLMTextDelta(text="hello")
        assert result[-1].response.message.text == "hello"
        assert result[-1].response.usage.output_tokens == 2
    finally:
        await adapter.close()
