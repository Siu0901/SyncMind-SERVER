from typing import Literal

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


class ConversationUpdateRequest(ChatRequest):
    title: str = Field(
        min_length=1,
        max_length=255,
    )


class SendMessageRequest(ChatRequest):
    conversation_id: int | None = Field(default=None, gt=0)
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
    title: str | None
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse
    citations: list[KnowledgeSource]
    stop_reason: LLMStopReason


class ChatStreamStarted(BaseModel):
    kind: Literal["started"] = "started"
    conversation_id: int


class ChatTurnStarted(BaseModel):
    kind: Literal["turn_start"] = "turn_start"
    turn_id: int


class ChatTextDelta(BaseModel):
    kind: Literal["text_delta"] = "text_delta"
    turn_id: int
    text: str


class ChatTurnEnded(BaseModel):
    kind: Literal["turn_end"] = "turn_end"
    turn_id: int
    stop_reason: LLMStopReason


class ChatToolStarted(BaseModel):
    kind: Literal["tool_start"] = "tool_start"
    turn_id: int
    tool_call_id: str
    tool_name: str


class ChatToolEnded(BaseModel):
    kind: Literal["tool_end"] = "tool_end"
    turn_id: int
    tool_call_id: str
    tool_name: str
    is_error: bool


class AgentCompleted(BaseModel):
    kind: Literal["agent_completed"] = "agent_completed"
    turn_id: int
    result: ChatAgentResult


class ChatStreamDone(BaseModel):
    kind: Literal["done"] = "done"
    turn_id: int
    response: SendMessageResponse


class ChatStreamError(BaseModel):
    kind: Literal["error"] = "error"
    code: str
    message: str
    status_code: int


type ChatProgressEvent = (
    ChatTurnStarted
    | ChatTextDelta
    | ChatTurnEnded
    | ChatToolStarted
    | ChatToolEnded
)

type ChatAgentEvent = ChatProgressEvent | AgentCompleted

type ChatStreamEvent = (
    ChatStreamStarted
    | ChatProgressEvent
    | ChatStreamDone
    | ChatStreamError
)
