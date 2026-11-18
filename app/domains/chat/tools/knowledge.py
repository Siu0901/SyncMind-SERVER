import json

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

from app.domains.knowledge.schema import RetrievalResult
from app.domains.knowledge.services.retrieval import (
    RetrievalService,
)
from app.infra.llm.schema import (
    LLMTool,
    LLMToolCall,
    LLMToolResult,
)


class SearchKnowledgeInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    query: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "찾으려는 정보를 구체적으로 표현한 검색어"
        ),
    )

    limit: int = Field(
        default=5,
        ge=1,
        le=10,
        description="반환할 검색 결과의 최대 개수",
    )


class SearchKnowledgeTool:
    definition = LLMTool(
        name="search_knowledge",
        description=(
            "현재 워크스페이스의 문서에서 관련 정보를 검색한다. "
            "문서에 근거한 답변이 필요할 때 사용한다. "
            "결과에는 문서 제목, 내용, 위치, 식별자가 포함된다. "
            "결과가 부족하면 다른 검색어로 다시 검색할 수 있다. "
            "검색 점수는 관련도 정렬용이며 사실의 신뢰 확률이 아니다."
        ),
        parameters=SearchKnowledgeInput.model_json_schema(),
    )

    def __init__(
        self,
        retrieval: RetrievalService,
        workspace_id: int,
    ):
        self.retrieval = retrieval
        self.workspace_id = workspace_id
        self.sources: dict[int, RetrievalResult] = {}


    async def execute(self, call: LLMToolCall) -> LLMToolResult:
        if call.name != self.definition.name:
            return LLMToolResult(
                tool_call_id=call.id,
                content=json.dumps(
                    {
                        "code": "unknown_tool",
                        "message": (
                            f"Unsupported tool: {call.name}"
                        ),
                    },
                    ensure_ascii=False,
                ),
                is_error=True,
            )

        try:
            arguments = (
                SearchKnowledgeInput.model_validate(
                    call.arguments
                )
            )

        except ValidationError as exc:
            return LLMToolResult(
                tool_call_id=call.id,
                content=exc.json(
                    include_input=False,
                    include_url=False,
                ),
                is_error=True,
            )

        results = await self.retrieval.search(
            workspace_id=self.workspace_id,
            query=arguments.query,
            limit=arguments.limit,
        )

        for result in results:
            self.sources[result.chunk_id] = result

        return LLMToolResult(
            tool_call_id=call.id,
            content=json.dumps(
                {
                    "query": arguments.query,
                    "count": len(results),
                    "results": [
                        result.model_dump(mode="json")
                        for result in results
                    ],
                },
                ensure_ascii=False,
            ),
        )