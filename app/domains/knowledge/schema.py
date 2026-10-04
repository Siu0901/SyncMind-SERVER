from typing import Optional

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.domains.knowledge.document.model import DocumentStatus


class SearchRequest(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=2000,
    )

    limit: int = Field(
        default=10,
        ge=1,
        le=20,
    )


class RetrievalResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: int
    document_id: int
    document_version_id: int

    title: str
    content: str

    page_number: Optional[int] = None
    section: Optional[str] = None

    retrieval_score: float
    rerank_score: Optional[float] = None

    @computed_field
    @property
    def score(self) -> float:
        if self.rerank_score is not None:
            return self.rerank_score

        return self.retrieval_score


class DocumentUpdateRequest(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=500,
    )


class DocumentResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    workspace_id: int
    source_id: Optional[int]

    external_id: Optional[str]

    title: str
    status: DocumentStatus

    current_version: Optional[int]

    created_at: datetime
    updated_at: datetime


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    document_id: int

    version: int
    content_hash: str

    s3_key: Optional[str]
    mime_type: Optional[str]
    file_size: Optional[int]

    source_updated_at: Optional[datetime]

    is_active: bool

    created_at: datetime


class DocumentChunkResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    document_version_id: int

    chunk_index: int

    content: str

    token_count: Optional[int]
    page_number: Optional[int]
    section: Optional[str]

    chunk_metadata: dict

    created_at: datetime


class DocumentCreateData(BaseModel):
    workspace_id: int

    title: str

    source_id: Optional[int] = None
    external_id: Optional[str] = None


class DocumentVersionCreateData(BaseModel):
    document_id: int

    content_hash: str

    s3_key: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None

    source_updated_at: Optional[datetime] = None
