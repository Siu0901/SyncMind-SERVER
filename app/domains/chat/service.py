import re

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.chat.agent import ChatAgent
from app.domains.chat.exceptions import (
    ChatCitationError,
    ChatMessageNotFoundError,
    ConversationChangedError,
    ConversationNotFoundError,
)
from app.domains.chat.model import (
    ChatMessage,
    Conversation,
    MessageCitation,
    MessageRole,
)
from app.domains.chat.repository import (
    ChatMessageRepository,
    ConversationRepository,
    MessageCitationRepository,
)
from app.domains.chat.schema import (
    ChatMessageResponse,
    ConversationCreateRequest,
    ConversationResponse,
    ConversationUpdateRequest,
    MessageCitationResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from app.domains.knowledge.schema import KnowledgeSource
from app.domains.workspace.schema import WorkspaceAccessContext
from app.infra.llm.schema import LLMMessage


CITATION_PATTERN = re.compile(r"\[chunk:([^\]]*)\]")


class ChatService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        agent: ChatAgent,
        history_limit: int = 20,
    ):
        if (
            not 2 <= history_limit <= 100
            or history_limit % 2 != 0
        ):
            raise ValueError(
                "history_limit must be an even number "
                "between 2 and 100"
            )

        self.session_factory = session_factory
        self.agent = agent
        self.history_limit = history_limit

    async def create_conversation(
        self,
        access: WorkspaceAccessContext,
        data: ConversationCreateRequest,
    ) -> ConversationResponse:
        async with self.session_factory.begin() as session:
            repository = ConversationRepository(session)

            conversation = await repository.create(
                Conversation(
                    workspace_id=access.workspace_id,
                    user_id=access.user_id,
                    title=data.title,
                )
            )

            return ConversationResponse.model_validate(
                conversation
            )

    async def list_conversations(
        self,
        access: WorkspaceAccessContext,
        *,
        before_id: int | None = None,
        limit: int = 20,
    ) -> list[ConversationResponse]:
        async with self.session_factory() as session:
            repository = ConversationRepository(session)

            conversations = await repository.list_owned(
                workspace_id=access.workspace_id,
                user_id=access.user_id,
                before_id=before_id,
                limit=limit,
            )

            return [
                ConversationResponse.model_validate(item)
                for item in conversations
            ]

    async def get_conversation(
        self,
        access: WorkspaceAccessContext,
        conversation_id: int,
    ) -> ConversationResponse:
        async with self.session_factory() as session:
            conversation = await self._get_owned(
                session=session,
                access=access,
                conversation_id=conversation_id,
            )

            return ConversationResponse.model_validate(
                conversation
            )

    async def update_conversation(
        self,
        access: WorkspaceAccessContext,
        conversation_id: int,
        data: ConversationUpdateRequest,
    ) -> ConversationResponse:
        async with self.session_factory.begin() as session:
            conversation = await self._get_owned(
                session=session,
                access=access,
                conversation_id=conversation_id,
                for_update=True,
            )

            conversation.title = data.title

            await ConversationRepository(session).touch(
                conversation
            )

            return ConversationResponse.model_validate(
                conversation
            )

    async def delete_conversation(
        self,
        access: WorkspaceAccessContext,
        conversation_id: int,
    ) -> None:
        async with self.session_factory.begin() as session:
            deleted = await ConversationRepository(
                session
            ).delete_owned(
                workspace_id=access.workspace_id,
                user_id=access.user_id,
                conversation_id=conversation_id,
            )

            if not deleted:
                raise ConversationNotFoundError()

    async def list_messages(
        self,
        access: WorkspaceAccessContext,
        conversation_id: int,
        *,
        before_id: int | None = None,
        limit: int = 20,
    ) -> list[ChatMessageResponse]:
        async with self.session_factory() as session:
            await self._get_owned(
                session=session,
                access=access,
                conversation_id=conversation_id,
            )

            messages = await ChatMessageRepository(
                session
            ).list_owned(
                workspace_id=access.workspace_id,
                user_id=access.user_id,
                conversation_id=conversation_id,
                before_id=before_id,
                limit=limit,
            )

            return [
                ChatMessageResponse.model_validate(item)
                for item in messages
            ]

    async def get_message_citations(
        self,
        access: WorkspaceAccessContext,
        conversation_id: int,
        message_id: int,
    ) -> list[MessageCitationResponse]:
        async with self.session_factory() as session:
            message = await ChatMessageRepository(
                session
            ).get_owned(
                workspace_id=access.workspace_id,
                user_id=access.user_id,
                conversation_id=conversation_id,
                message_id=message_id,
            )

            if message is None:
                raise ChatMessageNotFoundError()

            citations = await MessageCitationRepository(
                session
            ).list_owned(
                workspace_id=access.workspace_id,
                user_id=access.user_id,
                conversation_id=conversation_id,
                message_id=message_id,
            )

            return [
                MessageCitationResponse.model_validate(item)
                for item in citations
            ]

    async def send_message(
        self,
        access: WorkspaceAccessContext,
        conversation_id: int,
        data: SendMessageRequest,
    ) -> SendMessageResponse:
        history, previous_message_id = await self._load_history(
            access=access,
            conversation_id=conversation_id,
        )

        result = await self.agent.run(
            workspace_id=access.workspace_id,
            question=data.content,
            history=history,
        )

        citations = self._select_citations(
            text=result.response.message.text,
            sources=result.sources,
        )

        async with self.session_factory.begin() as session:
            conversation = await self._get_owned(
                session=session,
                access=access,
                conversation_id=conversation_id,
                for_update=True,
            )

            messages_repo = ChatMessageRepository(session)
            citations_repo = MessageCitationRepository(session)

            latest = await messages_repo.list_owned(
                workspace_id=access.workspace_id,
                user_id=access.user_id,
                conversation_id=conversation_id,
                limit=1,
            )

            current_message_id = (
                latest[-1].id
                if latest
                else None
            )

            if current_message_id != previous_message_id:
                raise ConversationChangedError()

            chunk_ids = [
                source.chunk_id
                for source in citations
            ]

            available_ids = (
                await citations_repo.get_available_chunk_ids(
                    workspace_id=access.workspace_id,
                    chunk_ids=chunk_ids,
                )
            )

            if available_ids != set(chunk_ids):
                raise ChatCitationError()

            user_message = await messages_repo.create(
                ChatMessage(
                    conversation_id=conversation_id,
                    role=MessageRole.USER,
                    content=data.content,
                )
            )

            assistant_message = await messages_repo.create(
                ChatMessage(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=result.response.message.text,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                )
            )

            await citations_repo.create_many([
                MessageCitation(
                    message_id=assistant_message.id,
                    chunk_id=source.chunk_id,
                    citation_order=order,
                    score=None,
                )
                for order, source in enumerate(
                    citations,
                    start=1,
                )
            ])

            if conversation.title is None:
                conversation.title = data.content[:80]

            await ConversationRepository(session).touch(
                conversation
            )

            return SendMessageResponse(
                conversation_id=conversation_id,
                user_message=(
                    ChatMessageResponse.model_validate(
                        user_message
                    )
                ),
                assistant_message=(
                    ChatMessageResponse.model_validate(
                        assistant_message
                    )
                ),
                citations=citations,
                stop_reason=result.response.stop_reason,
            )

    async def _load_history(
        self,
        *,
        access: WorkspaceAccessContext,
        conversation_id: int,
    ) -> tuple[list[LLMMessage], int | None]:
        async with self.session_factory() as session:
            await self._get_owned(
                session=session,
                access=access,
                conversation_id=conversation_id,
            )

            messages = await ChatMessageRepository(session).list_owned(
                workspace_id=access.workspace_id,
                user_id=access.user_id,
                conversation_id=conversation_id,
                limit=self.history_limit,
            )

            previous_message_id = (
                messages[-1].id
                if messages
                else None
            )

            history = [
                LLMMessage(
                    role=message.role.value,
                    content=message.content,
                )
                for message in messages
            ]

            return history, previous_message_id

    @staticmethod
    async def _get_owned(
        *,
        session: AsyncSession,
        access: WorkspaceAccessContext,
        conversation_id: int,
        for_update: bool = False,
    ) -> Conversation:
        conversation = await (
            ConversationRepository(session)
            .get_owned(
                workspace_id=access.workspace_id,
                user_id=access.user_id,
                conversation_id=conversation_id,
                for_update=for_update,
            )
        )

        if conversation is None:
            raise ConversationNotFoundError()

        return conversation

    @staticmethod
    def _select_citations(
        *,
        text: str,
        sources: list[KnowledgeSource],
    ) -> list[KnowledgeSource]:
        source_map = {
            str(source.chunk_id): source
            for source in sources
        }

        referenced_ids = list(dict.fromkeys(
            CITATION_PATTERN.findall(text)
        ))

        if any(
            chunk_id not in source_map
            for chunk_id in referenced_ids
        ):
            raise ChatCitationError()

        return [
            source_map[chunk_id]
            for chunk_id in referenced_ids
        ]