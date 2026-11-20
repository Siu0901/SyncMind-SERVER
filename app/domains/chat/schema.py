from pydantic import BaseModel, Field

from app.domains.knowledge.schema import KnowledgeSource
from app.infra.llm.schema import (
    LLMResponse,
    LLMUsage,
)


class ChatAgentResult(BaseModel):
    response: LLMResponse

    usage: LLMUsage

    sources: list[KnowledgeSource] = Field(
        default_factory=list
    )

    model_calls: int
    tool_calls: int