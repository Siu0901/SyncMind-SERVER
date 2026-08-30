import enum
from typing import Optional
from datetime import datetime

from sqlalchemy import Column, DateTime, UniqueConstraint, func
from sqlmodel import Field, SQLModel


class OAuthProvider(enum.StrEnum):
    GOOGLE = "google"
    GITHUB = "github"


class OAuthAccount(SQLModel, table=True):
    __tablename__ = "oauth_accounts"

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_oauth_provider_user",
        ),
        UniqueConstraint(
            "user_id",
            "provider",
            name="uq_user_oauth_provider",
        ),
    )

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
    )

    user_id: int = Field(
        foreign_key="users.id",
        index=True,
    )

    provider: OAuthProvider = Field(
        index=True,
    )

    provider_user_id: str = Field(
        max_length=255,
    )

    provider_email: Optional[str] = Field(
        default=None,
        max_length=255,
    )

    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        )
    )