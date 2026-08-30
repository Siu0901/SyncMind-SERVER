import enum
from datetime import datetime
from sqlmodel import (
    SQLModel,
    Field,
    BigInteger,
    Column,
    DateTime,
    func,
    String
)


class WorkspaceRole(enum.StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class WorkSpace(SQLModel, table=True):
    __tablename__ = "workspace"

    id: int =  Field(
        primary_key=True,
        sa_type=BigInteger
    )
    name: str = Field(max_length=50)

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


class WorkSpaceMember(SQLModel, table=True):
    __tablename__ = "workspace_member"

    workspace_id: int = Field(
        foreign_key="workspace.id",
        sa_type=BigInteger,
        primary_key=True
    )
    user_id: int = Field(
        foreign_key="user.id",
        sa_type=BigInteger,
        primary_key=True
    )

    role: WorkspaceRole = Field(
        default=WorkspaceRole.MEMBER,
        sa_column=Column(
            String(20),
            nullable=False
        )
    )

    joined_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=True
        )
    )