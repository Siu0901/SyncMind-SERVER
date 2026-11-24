import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domains.chat.title import ConversationTitleGenerator
from app.infra.llm.exceptions import LLMUnavailableError
from app.infra.llm.schema import (
    LLMAssistantTurn, LLMProviderState, LLMResponse, LLMStopReason, LLMUsage,
)


pytestmark = pytest.mark.anyio


def response(text="검색 근거 살펴보기", stop_reason=LLMStopReason.DONE):
    return LLMResponse(
        message=LLMAssistantTurn(
            text=text, provider_state=LLMProviderState(provider="anthropic", blocks=[]),
        ),
        provider="anthropic", model="mock", usage=LLMUsage(), stop_reason=stop_reason,
    )


async def test_generates_title_through_llm_port_with_bounded_input():
    llm = SimpleNamespace(generate=AsyncMock(return_value=response('  "검색 근거\n살펴보기"  ')))
    title = await ConversationTitleGenerator(llm).generate(question="검색해 줘", answer="a" * 5000)

    assert title == "검색 근거 살펴보기"
    request = llm.generate.call_args.args[0]
    assert json.loads(request.messages[0].content) == {"question": "검색해 줘", "answer": "a" * 4000}
    assert request.tool_choice == "none"
    assert not request.tools
    assert request.max_output_tokens == 128


@pytest.mark.parametrize("text, stop_reason", [
    (" ", LLMStopReason.DONE),
    ("Partial title", LLMStopReason.TRUNCATED),
    ("I cannot help", LLMStopReason.REFUSED),
])
async def test_invalid_model_output_leaves_title_unset(text, stop_reason):
    llm = SimpleNamespace(generate=AsyncMock(return_value=response(text, stop_reason)))
    assert await ConversationTitleGenerator(llm).generate(question="Hi", answer="Hello") is None


async def test_provider_failure_leaves_title_unset():
    llm = SimpleNamespace(generate=AsyncMock(side_effect=LLMUnavailableError("Unavailable")))
    assert await ConversationTitleGenerator(llm).generate(question="Hi", answer="Hello") is None


async def test_title_timeout_cancels_model_and_returns_no_title():
    cancelled = asyncio.Event()

    async def generate(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    llm = SimpleNamespace(generate=generate)
    title = await ConversationTitleGenerator(llm, timeout_seconds=0.01).generate(question="Hi", answer="Hello")
    assert title is None
    assert cancelled.is_set()


async def test_external_cancellation_is_not_swallowed():
    llm = SimpleNamespace(generate=AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await ConversationTitleGenerator(llm).generate(question="Hi", answer="Hello")
