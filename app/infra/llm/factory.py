from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from app.core.config import get_settings
from app.infra.llm.adapter.claude_adapter import AnthropicLLMAdapter
from app.infra.llm.adapter.openai_adapter import OpenAILLMAdapter
from app.infra.llm.port import LLMPort


_llm: LLMPort | None = None


def create_llm() -> LLMPort:
    settings = get_settings()

    if (
        settings.LLM_TIMEOUT_SECONDS <= 0
        or settings.LLM_MAX_RETRIES < 0
        or settings.LLM_MAX_OUTPUT_TOKENS <= 0
    ):
        raise ValueError(
            "Invalid LLM timeout, retry count, "
            "or output token limit"
        )

    if settings.LLM_PROVIDER == "anthropic":
        key = settings.ANTHROPIC_API_KEY
        model = settings.ANTHROPIC_LLM_MODEL

        if (
            key is None
            or not key.get_secret_value().strip()
            or not model.strip()
        ):
            raise ValueError(
                "ANTHROPIC_API_KEY and "
                "ANTHROPIC_LLM_MODEL are required"
            )

        return AnthropicLLMAdapter(
            client=AsyncAnthropic(
                api_key=key.get_secret_value(),
                timeout=settings.LLM_TIMEOUT_SECONDS,
                max_retries=settings.LLM_MAX_RETRIES,
            ),
            model=model,
            max_output_tokens=(
                settings.LLM_MAX_OUTPUT_TOKENS
            ),
        )

    if settings.LLM_PROVIDER == "openai":
        key = settings.OPENAI_API_KEY.get_secret_value()
        model = settings.OPENAI_LLM_MODEL

        if not key.strip() or not model.strip():
            raise ValueError(
                "OPENAI_API_KEY and "
                "OPENAI_LLM_MODEL are required"
            )

        return OpenAILLMAdapter(
            client=AsyncOpenAI(
                api_key=key,
                timeout=settings.LLM_TIMEOUT_SECONDS,
                max_retries=settings.LLM_MAX_RETRIES,
            ),
            model=model,
            max_output_tokens=(
                settings.LLM_MAX_OUTPUT_TOKENS
            ),
        )

    raise ValueError(
        "Unsupported LLM provider: "
        f"{settings.LLM_PROVIDER}"
    )


def init_llm() -> LLMPort:
    global _llm
    if _llm is None:
        _llm = create_llm()
    return _llm


def get_llm() -> LLMPort:
    if _llm is None:
        raise RuntimeError("LLM is not initialized")
    return _llm


async def close_llm():
    global _llm
    if _llm is not None:
        llm, _llm = _llm, None
        await llm.close()
