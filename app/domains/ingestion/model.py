import enum
from typing import Optional
from datetime import datetime, timezone

from sqlmodel import (
    Field,
    SQLModel,
    Column,
    Text,
    DateTime,
    func,
)


class IngestionJobStatus(enum.StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestionStage(enum.StrEnum):
    DOWNLOADING = "downloading"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"


class IngestionJob(SQLModel, table=True):
    __tablename__ = "ingestion_job"

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
    )

    document_version_id: int = Field(
        foreign_key="document_version.id",
        index=True,
    )

    status: IngestionJobStatus = Field(
        default=IngestionJobStatus.QUEUED,
        index=True,
    )

    stage: Optional[IngestionStage] = Field(default=None)

    retry_count: int = Field(default=0)

    error_message: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )

    queued_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False
        )
    )
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)