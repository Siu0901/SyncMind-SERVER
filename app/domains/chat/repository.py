from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.chat.model import (
    ChatMessage,
    Conversation,
    MessageCitation,
)
from app.domains.knowledge.document.model import (
    Document,
    DocumentChunk,
    DocumentStatus,
    DocumentVersion,
)


def _validate_limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise ValueError(
            "limit must be between 1 and 100"
        )


class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        conversation: Conversation,
    ) -> Conversation:
        self.session.add(conversation)

        await self.session.flush()
        await self.session.refresh(conversation)

        return conversation

    async def get_owned(
        self,
        *,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
        for_update: bool = False,
    ) -> Conversation | None:
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user_id,
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.exec(statement)

        return result.first()

    async def list_owned(
        self,
        *,
        workspace_id: int,
        user_id: int,
        before_id: int | None = None,
        limit: int = 20,
    ) -> list[Conversation]:
        _validate_limit(limit)

        statement = select(Conversation).where(
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user_id,
        )

        if before_id is not None:
            statement = statement.where(
                Conversation.id < before_id
            )

        statement = (
            statement
            .order_by(Conversation.id.desc())
            .limit(limit)
        )

        result = await self.session.exec(statement)

        return list(result.all())

    async def touch(
        self,
        conversation: Conversation,
    ) -> None:
        conversation.updated_at = datetime.now(
            timezone.utc
        )

        self.session.add(conversation)

        await self.session.flush()

    async def delete_owned(
        self,
        *,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
    ) -> bool:
        conversation = await self.get_owned(
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            for_update=True,
        )

        if conversation is None:
            return False

        await self.session.delete(conversation)
        await self.session.flush()

        return True


class ChatMessageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, message: ChatMessage) -> ChatMessage:
        self.session.add(message)

        await self.session.flush()
        await self.session.refresh(message)

        return message

    async def get_owned(
        self,
        *,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
        message_id: int,
    ) -> ChatMessage | None:
        statement = (
            select(ChatMessage)
            .join(
                Conversation,
                Conversation.id
                == ChatMessage.conversation_id,
            )
            .where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.user_id == user_id,
                ChatMessage.id == message_id,
            )
        )

        result = await self.session.exec(statement)

        return result.first()

    async def list_owned(
        self,
        *,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
        before_id: int | None = None,
        limit: int = 20,
    ) -> list[ChatMessage]:
        _validate_limit(limit)

        statement = (
            select(ChatMessage)
            .join(
                Conversation,
                Conversation.id
                == ChatMessage.conversation_id,
            )
            .where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.user_id == user_id,
            )
        )

        if before_id is not None:
            statement = statement.where(
                ChatMessage.id < before_id
            )

        statement = (
            statement
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )

        result = await self.session.exec(statement)

        return list(reversed(result.all()))


class MessageCitationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_many(self, citations: list[MessageCitation]):
        if not citations:
            return

        self.session.add_all(citations)

        await self.session.flush()

    async def list_owned(
        self,
        *,
        workspace_id: int,
        user_id: int,
        conversation_id: int,
        message_id: int,
    ) -> list[MessageCitation]:
        statement = (
            select(MessageCitation)
            .join(
                ChatMessage,
                ChatMessage.id
                == MessageCitation.message_id,
            )
            .join(
                Conversation,
                Conversation.id
                == ChatMessage.conversation_id,
            )
            .where(
                Conversation.workspace_id == workspace_id,
                Conversation.user_id == user_id,
                Conversation.id == conversation_id,
                ChatMessage.id == message_id,
            )
            .order_by(
                MessageCitation.citation_order
            )
        )

        result = await self.session.exec(statement)

        return list(result.all())


    async def get_available_chunk_ids(
        self,
        *,
        workspace_id: int,
        chunk_ids: list[int],
    ) -> set[int]:
        if not chunk_ids:
            return set()

        statement = (
            select(DocumentChunk.id)
            .join(
                DocumentVersion,
                DocumentVersion.id
                == DocumentChunk.document_version_id,
            )
            .join(
                Document,
                Document.id
                == DocumentVersion.document_id,
            )
            .where(
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
                Document.status == DocumentStatus.READY,
                DocumentVersion.is_active.is_(True),
                DocumentChunk.id.in_(chunk_ids),
            )
            .order_by(DocumentChunk.id)
            .with_for_update(
                read=True,
                of=DocumentChunk,
            )
        )

        result = await self.session.exec(statement)

        return set(result.all())
