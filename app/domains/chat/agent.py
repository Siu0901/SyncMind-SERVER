import json

from asyncio import timeout

from app.domains.chat.exceptions import (
    ChatAgentError,
    ChatAgentTimeoutError,
)
from app.domains.chat.prompt import (
    CHAT_AGENT_SYSTEM_PROMPT,
)
from app.domains.chat.schema import ChatAgentResult
from app.domains.chat.tools.knowledge import (
    SearchKnowledgeTool,
)
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
    LLMToolCall,
    LLMToolResult,
    LLMUsage,
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

    async def run(
        self,
        *,
        workspace_id: int,
        question: str,
        history: list[LLMMessage] | None = None,
    ) -> ChatAgentResult:
        try:
            async with timeout(self.timeout_seconds):
                return await self._run(
                    workspace_id=workspace_id,
                    question=question,
                    history=history,
                )

        except TimeoutError as exc:
            raise ChatAgentTimeoutError() from exc


    async def _run(
        self,
        *,
        workspace_id: int,
        question: str,
        history: list[LLMMessage] | None,
    ) -> ChatAgentResult:
        search_tool = SearchKnowledgeTool(
            retrieval=self.retrieval,
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
        seen_calls: set[str] = set()
        tool_calls = 0

        for step in range(self.max_model_calls):
            force_answer = (
                step == self.max_model_calls - 1
                or tool_calls >= self.max_tool_calls
            )

            response = await self.llm.generate(
                LLMRequest(
                    system=CHAT_AGENT_SYSTEM_PROMPT,
                    messages=messages,
                    tools=[search_tool.definition],
                    tool_choice=(
                        "none"
                        if force_answer
                        else "auto"
                    ),
                )
            )

            responses.append(response)

            if response.stop_reason in {
                LLMStopReason.DONE,
                LLMStopReason.REFUSED,
            }:
                if response.message.tool_calls:
                    raise ChatAgentError(
                        "Unexpected tool calls "
                        "in a final response"
                    )

                if not response.message.text.strip():
                    raise ChatAgentError(
                        "The model returned an empty answer"
                    )

                return ChatAgentResult(
                    response=response,
                    usage=self._total_usage(responses),
                    sources=list(
                        search_tool.sources.values()
                    ),
                    model_calls=len(responses),
                    tool_calls=tool_calls,
                )

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
                if tool_calls >= self.max_tool_calls:
                    result = self._tool_error(
                        call=call,
                        code="tool_limit_reached",
                        message=(
                            "The tool execution limit "
                            "has been reached. "
                            "Answer using the available evidence."
                        ),
                    )

                else:
                    tool_calls += 1

                    result = await self._execute_tool(
                        call=call,
                        search_tool=search_tool,
                        seen_calls=seen_calls,
                    )

                messages.append(result)

        raise ChatAgentError(
            "The agent did not produce a final answer"
        )

    async def _execute_tool(
        self,
        *,
        call: LLMToolCall,
        search_tool: SearchKnowledgeTool,
        seen_calls: set[str],
    ) -> LLMToolResult:
        if call.name != search_tool.definition.name:
            return self._tool_error(
                call=call,
                code="unknown_tool",
                message=f"Unknown tool: {call.name}",
            )

        signature = json.dumps(
            {
                "name": call.name,
                "arguments": call.arguments,
            },
            sort_keys=True,
            ensure_ascii=False,
        )

        if signature in seen_calls:
            return self._tool_error(
                call=call,
                code="duplicate_tool_call",
                message=(
                    "This tool was already called "
                    "with the same arguments. "
                    "Use the previous result "
                    "or change the search query."
                ),
            )

        seen_calls.add(signature)

        return await search_tool.execute(call)

    @staticmethod
    def _tool_error(
        *,
        call: LLMToolCall,
        code: str,
        message: str,
    ) -> LLMToolResult:
        return LLMToolResult(
            tool_call_id=call.id,
            content=json.dumps(
                {
                    "code": code,
                    "message": message,
                },
                ensure_ascii=False,
            ),
            is_error=True,
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