from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict

from app.domains.chat.model import MessageRole
from app.domains.knowledge.schema import KnowledgeSource
from app.infra.llm.schema import (
    LLMResponse,
    LLMStopReason,
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


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class ConversationCreateRequest(ChatRequest):
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )


class ConversationUpdateRequest(ChatRequest):
    title: str = Field(
        min_length=1,
        max_length=255,
    )


class SendMessageRequest(ChatRequest):
    content: str = Field(
        min_length=1,
        max_length=8000,
    )


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    user_id: int
    title: str | None
    created_at: datetime
    updated_at: datetime


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: MessageRole
    content: str
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime


class MessageCitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: int
    chunk_id: int
    score: float | None
    citation_order: int


class SendMessageResponse(BaseModel):
    conversation_id: int
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse
    citations: list[KnowledgeSource]
    stop_reason: LLMStopReason