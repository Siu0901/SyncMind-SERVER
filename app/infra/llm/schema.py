from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMRequest:
    messages: list[LLMMessage]
    system: str = ""
    max_output_tokens: int | None = None

    def __post_init__(self):
        if not self.messages:
            raise ValueError("At least one message is required")
        if any(message.role not in {"user", "assistant"} or not message.content.strip() for message in self.messages):
            raise ValueError("Messages must have a valid role and non-empty content")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    usage: LLMUsage
    stop_reason: str | None = None


@dataclass(frozen=True)
class LLMTextDelta:
    text: str


@dataclass(frozen=True)
class LLMCompleted:
    response: LLMResponse


LLMEvent = LLMTextDelta | LLMCompleted
