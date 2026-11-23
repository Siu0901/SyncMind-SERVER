from asyncio import get_running_loop, timeout_at
from collections.abc import AsyncIterator
from contextlib import aclosing

from app.domains.chat.exceptions import (
    ChatAgentError,
    ChatAgentTimeoutError,
)
from app.domains.chat.prompt import (
    CHAT_AGENT_SYSTEM_PROMPT,
)
from app.domains.chat.schema import (
    AgentCompleted,
    ChatAgentEvent,
    ChatAgentResult,
    ChatTextDelta,
    ChatToolEnded,
    ChatToolStarted,
    ChatTurnEnded,
    ChatTurnStarted,
)
from app.domains.chat.tools.knowledge import (
    ReadAdjacentChunksTool,
    ReadDocumentTool,
    SearchKnowledgeTool,
)
from app.domains.chat.tools.registry import ToolRegistry
from app.domains.knowledge.services.retrieval import (
    RetrievalService,
)
from app.infra.llm.port import LLMPort
from app.infra.llm.schema import (
    LLMInput,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMStopReason,
    LLMUsage,
    LLMCompleted,
    LLMEvent,
    LLMTextDelta,
)


class ChatAgent:
    def __init__(
        self,
        llm: LLMPort,
        retrieval: RetrievalService,
        max_model_calls: int = 6,
        max_tool_calls: int = 10,
        timeout_seconds: float = 60.0,
    ):
        if (
            max_model_calls < 1
            or max_tool_calls < 1
            or timeout_seconds <= 0
        ):
            raise ValueError(
                "Agent execution limits must be positive"
            )

        self.llm = llm
        self.retrieval = retrieval
        self.max_model_calls = max_model_calls
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds

    async def stream(
        self,
        *,
        workspace_id: int,
        question: str,
        history: list[LLMMessage] | None = None,
    ) -> AsyncIterator[ChatAgentEvent]:
        registry = self._create_registry(
            workspace_id=workspace_id,
        )

        messages: list[LLMInput] = []
        messages.extend(history or [])
        messages.append(
            LLMMessage(
                role="user",
                content=question,
            )
        )

        responses: list[LLMResponse] = []

        deadline = (
            get_running_loop().time()
            + self.timeout_seconds
        )

        for step in range(self.max_model_calls):
            turn_id = step + 1

            force_answer = (
                step == self.max_model_calls - 1
                or registry.exhausted
                or not registry.definitions
            )

            yield ChatTurnStarted(turn_id=turn_id)

            request = LLMRequest(
                system=CHAT_AGENT_SYSTEM_PROMPT,
                messages=messages,
                tools=registry.definitions,
                tool_choice=(
                    "none" if force_answer else "auto"
                ),
            )

            response = None

            async with aclosing(
                self.llm.stream(request)
            ) as model_events:
                while True:
                    event = await self._next_event(
                        model_events,
                        deadline,
                    )

                    if event is None:
                        break

                    if isinstance(event, LLMTextDelta):
                        if response is not None:
                            raise ChatAgentError(
                                "Received text after model completion"
                            )

                        yield ChatTextDelta(
                            turn_id=turn_id,
                            text=event.text,
                        )

                    elif isinstance(event, LLMCompleted):
                        if response is not None:
                            raise ChatAgentError(
                                "Received duplicate model completion"
                            )

                        response = event.response

            if response is None:
                raise ChatAgentError(
                    "The model stream ended without completion"
                )

            responses.append(response)

            yield ChatTurnEnded(
                turn_id=turn_id,
                stop_reason=response.stop_reason,
            )

            if response.stop_reason in {
                LLMStopReason.DONE,
                LLMStopReason.REFUSED,
            }:
                if response.message.tool_calls:
                    raise ChatAgentError(
                        "Unexpected tool calls in a final response"
                    )

                if not response.message.text.strip():
                    raise ChatAgentError(
                        "The model returned an empty answer"
                    )

                yield AgentCompleted(
                    turn_id=turn_id,
                    result=ChatAgentResult(
                        response=response,
                        usage=self._total_usage(responses),
                        sources=registry.sources,
                        model_calls=len(responses),
                        tool_calls=registry.call_count,
                    ),
                )
                return

            if response.stop_reason == LLMStopReason.TRUNCATED:
                raise ChatAgentError(
                    "The model response was truncated"
                )

            if response.stop_reason != LLMStopReason.TOOL_USE:
                raise ChatAgentError(
                    "The model stopped unexpectedly"
                )

            if force_answer:
                raise ChatAgentError(
                    "The model requested tools "
                    "after tool use was disabled"
                )

            if not response.message.tool_calls:
                raise ChatAgentError(
                    "The model requested tool use "
                    "without tool calls"
                )

            messages.append(response.message)

            for call in response.message.tool_calls:
                yield ChatToolStarted(
                    turn_id=turn_id,
                    tool_call_id=call.id,
                    tool_name=call.name,
                )

                if get_running_loop().time() >= deadline:
                    raise ChatAgentTimeoutError()

                try:
                    async with timeout_at(deadline):
                        result = await registry.execute(call)

                except TimeoutError as exc:
                    raise ChatAgentTimeoutError() from exc

                messages.append(result)

                yield ChatToolEnded(
                    turn_id=turn_id,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    is_error=result.is_error,
                )

        raise ChatAgentError(
            "The agent did not produce a final answer"
        )

    @staticmethod
    async def _next_event(
        events: AsyncIterator[LLMEvent],
        deadline: float,
    ) -> LLMEvent | None:
        if get_running_loop().time() >= deadline:
            raise ChatAgentTimeoutError()

        try:
            async with timeout_at(deadline):
                return await anext(events)

        except StopAsyncIteration:
            return None

        except TimeoutError as exc:
            raise ChatAgentTimeoutError() from exc

    def _create_registry(self, workspace_id: int) -> ToolRegistry:
        return ToolRegistry(
            tools=[
                SearchKnowledgeTool(
                    retrieval=self.retrieval,
                    workspace_id=workspace_id,
                ),
                ReadDocumentTool(
                    retrieval=self.retrieval,
                    workspace_id=workspace_id,
                ),
                ReadAdjacentChunksTool(
                    retrieval=self.retrieval,
                    workspace_id=workspace_id,
                ),
            ],
            max_calls=self.max_tool_calls,
        )


    @staticmethod
    def _total_usage(
        responses: list[LLMResponse],
    ) -> LLMUsage:
        input_counts = [
            response.usage.input_tokens
            for response in responses
        ]

        output_counts = [
            response.usage.output_tokens
            for response in responses
        ]

        return LLMUsage(
            input_tokens=(
                sum(
                    count
                    for count in input_counts
                    if count is not None
                )
                if all(
                    count is not None
                    for count in input_counts
                )
                else None
            ),
            output_tokens=(
                sum(
                    count
                    for count in output_counts
                    if count is not None
                )
                if all(
                    count is not None
                    for count in output_counts
                )
                else None
            ),
        )
