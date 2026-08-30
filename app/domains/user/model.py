from typing import Optional

from datetime import datetime

from sqlmodel import (
    SQLModel,
    Field,
    BigInteger,
    DateTime,
    Column,
    func
)


class User(SQLModel, table=True):
    __tablename__ = "user"

    id: Optional[int] =  Field(
        primary_key=True,
        sa_type=BigInteger,
        default=None
    )
    email: str = Field(
        max_length=255,
        nullable=False,
        unique=True,
        index=True
    )

    password_hash: Optional[str] = Field(
        default=None,
        max_length=255,
    )

    name: str = Field(
        max_length=100,
        nullable=False
    )

    profile_image_url: Optional[str] = Field(
        default=None,
        max_length=2048,
    )

    email_verified: bool = Field(default=False)

    is_active: bool = Field(default=True)

    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        )
    )

    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )
    )