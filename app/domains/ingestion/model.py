from typing import Optional
from datetime import datetime

from sqlmodel import (
    Field,
    SQLModel,
    Column,
    Text,
    DateTime,
    func,
)

from app.domains.ingestion.enums import (
    IngestionJobStatus,
    IngestionStage,
)


class IngestionJob(SQLModel, table=True):
    __tablename__ = "ingestion_job"

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
    )

    document_version_id: int = Field(
        foreign_key="document_version.id",
        index=True,
        ondelete="CASCADE",
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