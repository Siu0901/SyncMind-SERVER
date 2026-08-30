import enum
from typing import Optional, Any
from datetime import datetime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import (
    SQLModel,
    Field,
    BigInteger,
    Column,
    DateTime,
    func,
)


class SourceProvider(enum.StrEnum):
    GITHUB = "github"
    NOTION = "notion"
    GOOGLE_DRIVE = "google_drive"


class SourceStatus(enum.StrEnum):
    ACTIVE = "active"
    SYNCING = "syncing"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class Source(SQLModel, table=True):
    id: Optional[int] = Field(
        primary_key=True,
        sa_type=BigInteger,
        default=None,
    )
    workspace_id: int = Field(
        foreign_key="workspace.id",
        index=True,
        sa_type=BigInteger
    )
    provider: SourceProvider = Field(index=True)
    name: str = Field(max_length=255)

    external_id: Optional[str] = Field(
        default=None,
        max_length=255
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )

    access_token_encrypted: Optional[str] = Field(default=None)
    refresh_token_encrypted: Optional[str] = Field(default=None)
    token_expires_at: Optional[datetime] = Field(default=None)

    status: SourceStatus = Field(
        default=SourceStatus.ACTIVE,
        index=True,
    )
    last_synced_at: Optional[str] = Field(default=None)


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
