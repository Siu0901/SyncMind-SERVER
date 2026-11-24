from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


type LLMProvider = Literal["anthropic", "openai"]


class LLMSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )


class LLMStopReason(StrEnum):
    DONE = "done"
    TOOL_USE = "tool_use"
    TRUNCATED = "truncated"
    REFUSED = "refused"
    OTHER = "other"


class LLMTool(LLMSchema):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    parameters: dict[str, JsonValue]


class LLMToolCall(LLMSchema):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, JsonValue]


class LLMMessage(LLMSchema):
    kind: Literal["message"] = "message"
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "Message content must not be blank"
            )
        return value


class LLMProviderState(LLMSchema):
    provider: LLMProvider
    blocks: list[dict[str, JsonValue]]


class LLMAssistantTurn(LLMSchema):
    kind: Literal["assistant_turn"] = "assistant_turn"
    text: str = ""
    tool_calls: list[LLMToolCall] = Field(
        default_factory=list
    )
    provider_state: LLMProviderState


class LLMToolResult(LLMSchema):
    kind: Literal["tool_result"] = "tool_result"
    tool_call_id: str = Field(min_length=1)
    content: str
    is_error: bool = False


type LLMInput = Annotated[
    LLMMessage | LLMAssistantTurn | LLMToolResult,
    Field(discriminator="kind"),
]


class LLMRequest(LLMSchema):
    messages: list[LLMInput] = Field(min_length=1)
    system: str = ""
    tools: list[LLMTool] = Field(default_factory=list)
    tool_choice: Literal["auto", "none", "required"] = "auto"
    max_output_tokens: int | None = Field(
        default=None,
        gt=0,
    )

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        names = [tool.name for tool in self.tools]

        if len(names) != len(set(names)):
            raise ValueError(
                "Tool names must be unique"
            )

        if self.tool_choice == "required" and not self.tools:
            raise ValueError(
                "Tools are required when tool_choice is required"
            )

        pending: set[str] = set()
        seen: set[str] = set()

        for message in self.messages:
            if isinstance(message, LLMToolResult):
                if message.tool_call_id not in pending:
                    raise ValueError(
                        "Unexpected or duplicate tool result: "
                        f"{message.tool_call_id}"
                    )

                pending.remove(message.tool_call_id)
                continue

            if pending:
                raise ValueError(
                    "All tool results must immediately follow "
                    "their assistant turn"
                )

            if isinstance(message, LLMAssistantTurn):
                for call in message.tool_calls:
                    if call.id in seen:
                        raise ValueError(
                            f"Duplicate tool call ID: {call.id}"
                        )

                    seen.add(call.id)
                    pending.add(call.id)

        if pending:
            raise ValueError(
                "Some tool calls have no results"
            )

        return self


class LLMUsage(LLMSchema):
    input_tokens: int | None = Field(
        default=None,
        ge=0,
    )
    output_tokens: int | None = Field(
        default=None,
        ge=0,
    )


class LLMResponse(LLMSchema):
    message: LLMAssistantTurn
    provider: LLMProvider
    model: str
    usage: LLMUsage
    stop_reason: LLMStopReason


class LLMTextDelta(LLMSchema):
    kind: Literal["text_delta"] = "text_delta"
    text: str


class LLMCompleted(LLMSchema):
    kind: Literal["completed"] = "completed"
    response: LLMResponse


type LLMEvent = Annotated[
    LLMTextDelta | LLMCompleted,
    Field(discriminator="kind"),
]