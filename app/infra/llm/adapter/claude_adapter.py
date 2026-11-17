from collections.abc import AsyncIterator

from anthropic import APIError, AsyncAnthropic
from anthropic.types import Message

from app.infra.llm.exceptions import (
    LLMRequestError,
    LLMUnavailableError,
    raise_llm_error,
)
from app.infra.llm.schema import (
    LLMAssistantTurn,
    LLMCompleted,
    LLMEvent,
    LLMInput,
    LLMMessage,
    LLMProviderState,
    LLMRequest,
    LLMResponse,
    LLMStopReason,
    LLMTextDelta,
    LLMToolCall,
    LLMToolResult,
    LLMUsage,
)


class AnthropicLLMAdapter:
    def __init__(
        self,
        client: AsyncAnthropic,
        model: str,
        max_output_tokens: int,
    ):
        self.client = client
        self.model = model
        self.max_output_tokens = max_output_tokens


    @staticmethod
    def _messages(messages: list[LLMInput]) -> list[dict]:
        converted: list[dict] = []
        tool_results: list[dict] = []

        for message in messages:
            if isinstance(message, LLMToolResult):
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                    "is_error": message.is_error,
                })
                continue

            if tool_results:
                converted.append({
                    "role": "user",
                    "content": tool_results,
                })
                tool_results = []

            if isinstance(message, LLMMessage):
                converted.append({
                    "role": message.role,
                    "content": message.content,
                })

            elif isinstance(message, LLMAssistantTurn):
                state = message.provider_state

                if state.provider != "anthropic":
                    raise LLMRequestError(
                        "Anthropic cannot replay another "
                        "provider's assistant turn"
                    )

                converted.append({
                    "role": "assistant",
                    "content": state.blocks,
                })

        if tool_results:
            converted.append({
                "role": "user",
                "content": tool_results,
            })

        return converted


    def _params(self, request: LLMRequest) -> dict:
        params = {
            "model": self.model,
            "messages": self._messages(request.messages),
            "max_tokens": (
                request.max_output_tokens
                if request.max_output_tokens is not None
                else self.max_output_tokens
            ),
        }

        if request.system:
            params["system"] = request.system

        if request.tools:
            params["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in request.tools
            ]

            choice = {
                "auto": "auto",
                "none": "none",
                "required": "any",
            }

            params["tool_choice"] = {
                "type": choice[request.tool_choice],
            }

        return params


    @staticmethod
    def _response(message: Message) -> LLMResponse:
        reasons = {
            "end_turn": LLMStopReason.DONE,
            "stop_sequence": LLMStopReason.DONE,
            "tool_use": LLMStopReason.TOOL_USE,
            "max_tokens": LLMStopReason.TRUNCATED,
            "model_context_window_exceeded": (
                LLMStopReason.TRUNCATED
            ),
            "refusal": LLMStopReason.REFUSED,
        }

        stop_reason = reasons.get(
            message.stop_reason,
            LLMStopReason.OTHER,
        )

        text = "".join(
            block.text
            for block in message.content
            if block.type == "text"
        )

        tool_calls = []

        if stop_reason == LLMStopReason.TOOL_USE:
            tool_calls = [
                LLMToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                )
                for block in message.content
                if block.type == "tool_use"
            ]

            if not tool_calls:
                raise LLMRequestError(
                    "Anthropic returned tool_use "
                    "without tool calls"
                )

        return LLMResponse(
            message=LLMAssistantTurn(
                text=text,
                tool_calls=tool_calls,
                provider_state=LLMProviderState(
                    provider="anthropic",
                    blocks=[
                        block.model_dump(
                            mode="json",
                            exclude_none=True,
                        )
                        for block in message.content
                    ],
                ),
            ),
            provider="anthropic",
            model=message.model,
            usage=LLMUsage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ),
            stop_reason=stop_reason,
        )


    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            message = await self.client.messages.create(
                **self._params(request)
            )

            return self._response(message)

        except APIError as exc:
            raise_llm_error(exc, provider="Anthropic")


    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        try:
            async with self.client.messages.stream(
                **self._params(request)
            ) as stream:
                completed = False

                async for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                    ):
                        yield LLMTextDelta(
                            text=event.delta.text
                        )

                    elif event.type == "message_stop":
                        completed = True

                if not completed:
                    raise LLMUnavailableError(
                        "Anthropic stream ended "
                        "before completion"
                    )

                message = await stream.get_final_message()

            yield LLMCompleted(
                response=self._response(message)
            )

        except APIError as exc:
            raise_llm_error(exc, provider="Anthropic")


    async def close(self):
        await self.client.close()