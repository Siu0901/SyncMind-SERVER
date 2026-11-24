import json

from app.domains.chat.tools.base import AgentTool
from app.domains.knowledge.schema import KnowledgeSource
from app.infra.llm.schema import (
    LLMTool,
    LLMToolCall,
    LLMToolResult,
)


class ToolRegistry:
    def __init__(
        self,
        tools: list[AgentTool],
        max_calls: int = 10,
    ):
        if max_calls < 1:
            raise ValueError(
                "max_calls must be positive"
            )

        self.max_calls = max_calls
        self._call_count = 0

        self._tools: dict[str, AgentTool] = {}
        self._seen_calls: set[str] = set()

        for tool in tools:
            name = tool.definition.name

            if name in self._tools:
                raise ValueError(
                    f"Duplicate tool name: {name}"
                )

            self._tools[name] = tool

    @property
    def definitions(self) -> list[LLMTool]:
        return [
            tool.definition
            for tool in self._tools.values()
        ]

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def exhausted(self) -> bool:
        return self._call_count >= self.max_calls

    @property
    def sources(self) -> list[KnowledgeSource]:
        collected: dict[int, KnowledgeSource] = {}

        for tool in self._tools.values():
            collected.update(tool.sources)

        return list(collected.values())

    async def execute(
        self,
        call: LLMToolCall,
    ) -> LLMToolResult:
        if self.exhausted:
            return self._error_result(
                call=call,
                code="tool_limit_reached",
                message=(
                    "The tool execution limit has been reached. "
                    "Answer using the available evidence."
                ),
            )

        self._call_count += 1

        tool = self._tools.get(call.name)

        if tool is None:
            return self._error_result(
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

        if signature in self._seen_calls:
            return self._error_result(
                call=call,
                code="duplicate_tool_call",
                message=(
                    "This tool was already called "
                    "with the same arguments. "
                    "Use the previous result "
                    "or change the arguments."
                ),
            )

        self._seen_calls.add(signature)

        return await tool.execute(call)

    @staticmethod
    def _error_result(
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