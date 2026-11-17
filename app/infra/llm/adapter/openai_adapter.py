from collections.abc import AsyncIterator

from openai import (
    APIError,
    AsyncOpenAI,
)

from app.infra.llm.exceptions import (
    LLMRequestError,
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


    def _params(self, request: LLMRequest) -> dict:
        params = {
            "model": self.model,
            "input": [
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in request.messages
            ],
            "max_output_tokens": (
                request.max_output_tokens
                or self.max_output_tokens
            ),
            "store": False,
        }

        if request.system:
            params["instructions"] = request.system

        return params


    @staticmethod
    def _response(response) -> LLMResponse:
        if response.status not in {"completed", "incomplete"}:
            raise LLMRequestError(
                "OpenAI returned an unsuccessful response"
            )

        stop_reason = response.status

        if response.incomplete_details is not None:
            stop_reason = response.incomplete_details.reason

        refusals = [
            part.refusal
            for item in response.output
            if item.type == "message"
            for part in item.content
            if part.type == "refusal"
        ]

        if refusals:
            stop_reason = "refusal"

        usage = response.usage

        return LLMResponse(
            text=response.output_text + "".join(refusals),
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
                    "OpenAI stream ended before completion"
                )

            yield LLMCompleted(
                response=self._response(final_response)
            )

        except APIError as exc:
            raise_llm_error(exc, provider="OpenAI")


    async def close(self):
        await self.client.close()