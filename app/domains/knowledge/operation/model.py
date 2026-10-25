from datetime import datetime
from enum import StrEnum

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, DateTime, Field, SQLModel, Text, func


class OperationStatus(StrEnum):
    UPLOADING = "uploading"
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    CLEANUP_REQUIRED = "cleanup_required"
    DELETED = "deleted"


class DocumentOperation(SQLModel, table=True):
    __tablename__ = "document_operation"

    document_id: int = Field(primary_key=True)

    workspace_id: int = Field(index=True)
    title: str = Field(max_length=500)

    ingestion_job_id: int | None = Field(default=None)

    status: str = Field(max_length=32, index=True)

    s3_keys: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False),
    )

    reason: str | None = Field(default=None, max_length=64)

    error_message: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )

    cleanup_error: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )

    cleanup_attempts: int = Field(default=0)

    next_run_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=True,
            index=True,
        ),
    )

    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
    )