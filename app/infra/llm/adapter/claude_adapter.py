from collections.abc import AsyncIterator

from anthropic import (
    APIError,
    AsyncAnthropic,
)

from app.infra.llm.exceptions import (
    LLMUnavailableError,
    raise_llm_error,
)
from app.infra.llm.schema import (
    LLMCompleted,
    LLMEvent,
    LLMRequest,
    LLMResponse,
    LLMTextDelta,
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


    def _params(self, request: LLMRequest) -> dict:
        params = {
            "model": self.model,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in request.messages
            ],
            "max_tokens": (
                request.max_output_tokens
                or self.max_output_tokens
            ),
        }

        if request.system:
            params["system"] = request.system

        return params


    @staticmethod
    def _response(message) -> LLMResponse:
        text = "".join(
            block.text
            for block in message.content
            if block.type == "text"
        )

        return LLMResponse(
            text=text,
            provider="anthropic",
            model=message.model,
            usage=LLMUsage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ),
            stop_reason=message.stop_reason,
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
                        "Anthropic stream ended before completion"
                    )

                message = await stream.get_final_message()

                yield LLMCompleted(
                    response=self._response(message)
                )

        except APIError as exc:
            raise_llm_error(exc, provider="Anthropic")


    async def close(self):
        await self.client.close()