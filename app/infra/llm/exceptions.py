from anthropic import (
    APIConnectionError as AnthropicConnectionError,
    APIStatusError as AnthropicStatusError,
)
from openai import (
    APIConnectionError as OpenAIConnectionError,
    APIStatusError as OpenAIStatusError,
)


class LLMError(Exception):
    pass


class LLMUnavailableError(LLMError):
    pass


class LLMRequestError(LLMError):
    pass


def raise_llm_error(
    exc: Exception,
    provider: str,
):
    if isinstance(
        exc,
        (AnthropicConnectionError, OpenAIConnectionError),
    ):
        raise LLMUnavailableError(
            f"{provider} is unavailable"
        ) from exc

    if isinstance(
        exc,
        (AnthropicStatusError, OpenAIStatusError),
    ):
        if exc.status_code in {408, 409, 429} or exc.status_code >= 500:
            raise LLMUnavailableError(
                f"{provider} is unavailable"
            ) from exc

    raise LLMRequestError(
        f"{provider} request failed"
    ) from exc