import json

from collections.abc import AsyncIterator

from openai import APIError, AsyncOpenAI
from openai.types.responses import Response
from pydantic import ValidationError

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


class OpenAILLMAdapter:
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        max_output_tokens: int,
    ):
        self.client = client
        self.model = model
        self.max_output_tokens = max_output_tokens


    @staticmethod
    def _messages(messages: list[LLMInput]) -> list[dict]:
        converted: list[dict] = []

        for message in messages:
            if isinstance(message, LLMMessage):
                converted.append({
                    "role": message.role,
                    "content": message.content,
                })

            elif isinstance(message, LLMAssistantTurn):
                state = message.provider_state

                if state.provider != "openai":
                    raise LLMRequestError(
                        "OpenAI cannot replay another "
                        "provider's assistant turn"
                    )

                converted.extend(state.blocks)

            elif isinstance(message, LLMToolResult):
                output = message.content

                if message.is_error:
                    output = json.dumps(
                        {
                            "is_error": True,
                            "content": message.content,
                        },
                        ensure_ascii=False,
                    )

                converted.append({
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": output,
                })

        return converted


    def _params(self, request: LLMRequest) -> dict:
        params = {
            "model": self.model,
            "input": self._messages(request.messages),
            "max_output_tokens": (
                request.max_output_tokens
                if request.max_output_tokens is not None
                else self.max_output_tokens
            ),
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }

        if request.system:
            params["instructions"] = request.system

        if request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    "strict": False,
                }
                for tool in request.tools
            ]

            params["tool_choice"] = request.tool_choice

        return params


    @staticmethod
    def _response(response: Response) -> LLMResponse:
        if response.status not in {
            "completed",
            "incomplete",
        }:
            raise LLMRequestError(
                "OpenAI returned an unsuccessful response"
            )

        texts: list[str] = []
        refused = False

        for item in response.output:
            if item.type != "message":
                continue

            for part in item.content:
                if part.type == "output_text":
                    texts.append(part.text)

                elif part.type == "refusal":
                    texts.append(part.refusal)
                    refused = True

        tool_calls: list[LLMToolCall] = []

        if response.status == "completed" and not refused:
            for item in response.output:
                if item.type != "function_call":
                    continue

                try:
                    arguments = json.loads(
                        item.arguments
                    )

                    tool_calls.append(
                        LLMToolCall(
                            id=item.call_id,
                            name=item.name,
                            arguments=arguments,
                        )
                    )

                except (
                    json.JSONDecodeError,
                    ValidationError,
                ) as exc:
                    raise LLMRequestError(
                        "OpenAI returned invalid "
                        "tool call arguments"
                    ) from exc

        if response.status == "incomplete":
            details = response.incomplete_details

            if (
                details is not None
                and details.reason == "content_filter"
            ):
                stop_reason = LLMStopReason.REFUSED
            else:
                stop_reason = LLMStopReason.TRUNCATED

        elif refused:
            stop_reason = LLMStopReason.REFUSED

        elif tool_calls:
            stop_reason = LLMStopReason.TOOL_USE

        else:
            stop_reason = LLMStopReason.DONE

        usage = response.usage

        return LLMResponse(
            message=LLMAssistantTurn(
                text="".join(texts),
                tool_calls=tool_calls,
                provider_state=LLMProviderState(
                    provider="openai",
                    blocks=[
                        item.model_dump(
                            mode="json",
                            exclude_none=True,
                        )
                        for item in response.output
                    ],
                ),
            ),
            provider="openai",
            model=response.model,
            usage=LLMUsage(
                input_tokens=(
                    usage.input_tokens
                    if usage is not None
                    else None
                ),
                output_tokens=(
                    usage.output_tokens
                    if usage is not None
                    else None
                ),
            ),
            stop_reason=stop_reason,
        )


    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            response = await self.client.responses.create(
                **self._params(request)
            )

            return self._response(response)

        except APIError as exc:
            raise_llm_error(exc, provider="OpenAI")


    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        try:
            stream = await self.client.responses.create(
                **self._params(request),
                stream=True,
            )

            final_response = None

            async with stream:
                async for event in stream:
                    if event.type in {
                        "response.output_text.delta",
                        "response.refusal.delta",
                    }:
                        yield LLMTextDelta(text=event.delta)

                    elif event.type in {
                        "response.failed",
                        "error",
                    }:
                        raise LLMRequestError(
                            "OpenAI stream failed"
                        )

                    elif event.type in {
                        "response.completed",
                        "response.incomplete",
                    }:
                        final_response = event.response

            if final_response is None:
                raise LLMUnavailableError(
                    "OpenAI stream ended "
                    "before completion"
                )

            yield LLMCompleted(
                response=self._response(final_response)
            )

        except APIError as exc:
            raise_llm_error(exc, provider="OpenAI")


    async def close(self):
        await self.client.close()