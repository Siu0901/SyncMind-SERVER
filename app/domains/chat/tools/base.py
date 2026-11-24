from typing import Protocol

from app.domains.knowledge.schema import KnowledgeSource
from app.infra.llm.schema import (
    LLMTool,
    LLMToolCall,
    LLMToolResult,
)


class AgentTool(Protocol):
    definition: LLMTool

    sources: dict[int, KnowledgeSource]

    async def execute(
        self,
        call: LLMToolCall,
    ) -> LLMToolResult:
        ...