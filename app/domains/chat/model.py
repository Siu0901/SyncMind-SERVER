import enum
from typing import Optional
from datetime import datetime

from sqlmodel import (
    Field,
    SQLModel,
    DateTime,
    Column,
    Text,
    func
)


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Conversation(SQLModel, table=True):
    __tablename__ = "conversation"

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
    )

    workspace_id: int = Field(
        foreign_key="workspace.id",
        index=True,
    )

    user_id: int = Field(
        foreign_key="user.id",
        index=True,
    )

    title: Optional[str] = Field(
        default=None,
        max_length=255,
    )

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


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_message"

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
    )

    conversation_id: int = Field(
        foreign_key="conversation.id",
        index=True,
    )

    role: MessageRole

    content: str = Field(sa_column=Column(Text, nullable=False))

    input_tokens: Optional[int] = Field(default=None)

    output_tokens: Optional[int] = Field(default=None)

    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False
        )
    )


class MessageCitation(SQLModel, table=True):
    __tablename__ = "message_citation"

    message_id: int = Field(
        foreign_key="chat_message.id",
        primary_key=True,
    )

    chunk_id: int = Field(
        foreign_key="document_chunk.id",
        primary_key=True,
    )

    score: Optional[float] = Field(default=None)

    citation_order: int