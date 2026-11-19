from typing import Protocol

from app.domains.knowledge.schema import RetrievalResult
from app.infra.llm.schema import (
    LLMTool,
    LLMToolCall,
    LLMToolResult,
)


class AgentTool(Protocol):
    definition: LLMTool

    sources: dict[int, RetrievalResult]

    async def execute(
        self,
        call: LLMToolCall,
    ) -> LLMToolResult:
        ...