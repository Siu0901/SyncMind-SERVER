from collections.abc import AsyncIterator
from typing import Protocol

from app.infra.llm.schema import LLMEvent, LLMRequest, LLMResponse


class LLMPort(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResponse:
        ...

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        ...

    async def close(self):
        ...
