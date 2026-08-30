import enum
from datetime import datetime
from typing import Optional, Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import (
    Field,
    SQLModel,
    Column,
    UniqueConstraint,
    func,
    DateTime,
    Text
)


class DocumentStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class Document(SQLModel, table=True):
    __tablename__ = "document"

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "external_id",
            name="uq_document_source_external",
        ),
    )

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
    )
    workspace_id: int = Field(
        foreign_key="workspace.id",
        index=True,
    )
    source_id: Optional[int] = Field(
        default=None,
        foreign_key="source.id",
        index=True,
    )

    external_id: Optional[int] = Field(
        default=None,
        max_length=1024,
    )

    title: str = Field(max_length=500)

    status: DocumentStatus = Field(
        default=DocumentStatus.PENDING,
        index=True,
    )
    current_version: Optional[int] = Field(default=None)

    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False
        )
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False
        )
    )


class DocumentVersion(SQLModel, table=True):
    __tablename__ = "document_version"

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version",
            name="uq_document_version",
        ),
    )

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
    )

    document_id: int = Field(
        foreign_key="document.id",
        index=True,
    )

    version: int

    content_hash: str = Field(
        max_length=64,
        index=True,
    )

    # 직접 업로드 파일이나 원본 snapshot
    s3_key: Optional[int] = Field(
        default=None,
        max_length=1024,
    )

    mime_type: Optional[int] = Field(
        default=None,
        max_length=255,
    )

    file_size: Optional[int] = Field(default=None)

    # GitHub/Notion/Drive 원본의 마지막 수정 시각
    source_updated_at: Optional[datetime] = Field(default=None)

    is_active: bool = Field(
        default=False,
        index=True,
    )

    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False
        )
    )


class DocumentChunk(SQLModel, table=True):
    __tablename__ = "document_chunk"

    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "chunk_index",
            name="uq_document_version_chunk",
        ),
    )

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
    )

    document_version_id: int = Field(
        foreign_key="document_version.id",
        index=True,
    )

    chunk_index: int

    content: str = Field(
        sa_column=Column(Text, nullable=False),
    )

    token_count: Optional[int] = Field(default=None)
    page_number: Optional[int] = Field(default=None)
    section: Optional[str] = Field(
        default=None,
        max_length=500,
    )
    chunk_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "metadata",
            JSONB,
            nullable=False,
        ),
    )

    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False
        )
    )