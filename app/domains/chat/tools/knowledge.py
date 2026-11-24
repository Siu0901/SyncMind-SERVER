import json

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

from app.domains.knowledge.schema import (
    KnowledgeReadResult,
    KnowledgeSource,
)
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
        self.sources: dict[int, KnowledgeSource] = {}


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


class ReadDocumentInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    document_version_id: int = Field(
        gt=0,
        description=(
            "검색 결과에서 확인한 document_version_id. "
            "버전 번호가 아니라 버전 레코드의 ID다."
        ),
    )

    start_chunk_index: int = Field(
        default=0,
        ge=0,
        description="읽기 시작할 청크 위치. 0부터 시작한다.",
    )

    limit: int = Field(
        default=5,
        ge=1,
        le=10,
        description="한 번에 읽을 청크의 최대 개수",
    )


class ReadAdjacentChunksInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    chunk_id: int = Field(
        gt=0,
        description="검색 결과에서 확인한 기준 청크 ID",
    )

    before: int = Field(
        default=1,
        ge=0,
        le=3,
        description="기준 청크 앞에서 읽을 청크 개수",
    )

    after: int = Field(
        default=1,
        ge=0,
        le=3,
        description="기준 청크 뒤에서 읽을 청크 개수",
    )


def _parse_read_arguments[M: BaseModel](
    call: LLMToolCall,
    schema: type[M],
) -> M | LLMToolResult:
    try:
        return schema.model_validate(call.arguments)

    except ValidationError as exc:
        return LLMToolResult(
            tool_call_id=call.id,
            content=exc.json(
                include_input=False,
                include_url=False,
            ),
            is_error=True,
        )


def _read_result(
    *,
    call: LLMToolCall,
    data: KnowledgeReadResult | None,
    sources: dict[int, KnowledgeSource],
) -> LLMToolResult:
    if data is None:
        return LLMToolResult(
            tool_call_id=call.id,
            content=json.dumps(
                {
                    "code": "knowledge_unavailable",
                    "message": (
                        "No readable chunks were found. "
                        "The item may be unavailable "
                        "or the requested range may be empty. "
                        "Search again or check the range."
                    ),
                },
                ensure_ascii=False,
            ),
            is_error=True,
        )

    for source in data.chunks:
        sources[source.chunk_id] = source

    return LLMToolResult(
        tool_call_id=call.id,
        content=data.model_dump_json(),
    )


class ReadDocumentTool:
    definition = LLMTool(
        name="read_document",
        description=(
            "검색에서 발견한 특정 문서 버전의 내용을 "
            "청크 순서대로 읽는다. "
            "검색 결과의 발췌만으로 설명이 부족하거나 "
            "문서의 다른 범위를 확인할 때 사용한다. "
            "document_version_id는 검색 결과에서 가져온다. "
            "has_more가 true이면 next_chunk_index부터 "
            "다음 범위를 읽을 수 있다."
        ),
        parameters=ReadDocumentInput.model_json_schema(),
    )

    def __init__(
        self,
        retrieval: RetrievalService,
        workspace_id: int,
    ):
        self.retrieval = retrieval
        self.workspace_id = workspace_id
        self.sources: dict[int, KnowledgeSource] = {}

    async def execute(
        self,
        call: LLMToolCall,
    ) -> LLMToolResult:
        arguments = _parse_read_arguments(
            call,
            ReadDocumentInput,
        )

        if isinstance(arguments, LLMToolResult):
            return arguments

        data = await self.retrieval.read_document(
            workspace_id=self.workspace_id,
            document_version_id=(
                arguments.document_version_id
            ),
            start_chunk_index=(
                arguments.start_chunk_index
            ),
            limit=arguments.limit,
        )

        return _read_result(
            call=call,
            data=data,
            sources=self.sources,
        )


class ReadAdjacentChunksTool:
    definition = LLMTool(
        name="read_adjacent_chunks",
        description=(
            "검색에서 발견한 청크의 앞뒤 문맥을 읽는다. "
            "문장이 중간에 끊겼거나 설명의 전후 관계를 "
            "확인할 때 사용한다. "
            "결과에는 기준 청크도 포함된다. "
            "has_more는 이 도구에서 항상 false이며, "
            "문서 전체의 끝을 의미하지 않는다."
        ),
        parameters=(
            ReadAdjacentChunksInput.model_json_schema()
        ),
    )

    def __init__(
        self,
        retrieval: RetrievalService,
        workspace_id: int,
    ):
        self.retrieval = retrieval
        self.workspace_id = workspace_id
        self.sources: dict[int, KnowledgeSource] = {}

    async def execute(
        self,
        call: LLMToolCall,
    ) -> LLMToolResult:
        arguments = _parse_read_arguments(
            call,
            ReadAdjacentChunksInput,
        )

        if isinstance(arguments, LLMToolResult):
            return arguments

        data = await self.retrieval.read_adjacent_chunks(
            workspace_id=self.workspace_id,
            chunk_id=arguments.chunk_id,
            before=arguments.before,
            after=arguments.after,
        )

        return _read_result(
            call=call,
            data=data,
            sources=self.sources,
        )