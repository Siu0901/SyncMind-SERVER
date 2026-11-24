import re
import logging

from asyncio import get_running_loop, timeout_at
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager

from anyio import CancelScope, move_on_after
from arq.connections import ArqRedis
from redis.exceptions import RedisError

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.exception.exceptions import AppException
from app.domains.chat.agent import ChatAgent
from app.domains.chat.exceptions import (
    ChatCitationError,
    ChatMessageNotFoundError,
    ConversationChangedError,
    ConversationNotFoundError,
    ChatAgentError,
    ChatGenerationLeaseLostError,
    ChatStreamTimeoutError,
    ConversationBusyError,
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
    ConversationResponse,
    ConversationUpdateRequest,
    MessageCitationResponse,
    SendMessageRequest,
    SendMessageResponse,
    AgentCompleted,
    ChatAgentResult,
    ChatStreamDone,
    ChatStreamError,
    ChatStreamEvent,
    ChatStreamStarted,
)
from app.domains.knowledge.schema import KnowledgeSource
from app.domains.chat.title import ConversationTitleGenerator
from app.domains.workspace.schema import WorkspaceAccessContext
from app.infra.llm.schema import LLMMessage
from app.infra.llm.exceptions import LLMError


logger = logging.getLogger(__name__)


CITATION_PATTERN = re.compile(r"\[chunk:([^\]]*)\]")


class ChatService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        agent: ChatAgent,
        redis: ArqRedis,
        title_generator: ConversationTitleGenerator,
        history_limit: int = 20,
        stream_timeout_seconds: float = 90.0,
    ):
        if (
            not 2 <= history_limit <= 100
            or history_limit % 2 != 0
        ):
            raise ValueError(
                "history_limit must be an even number "
                "between 2 and 100"
            )

        if stream_timeout_seconds <= agent.timeout_seconds:
            raise ValueError(
                "Stream timeout must leave time "
                "for final validation and persistence"
            )

        self.session_factory = session_factory
        self.agent = agent
        self.redis = redis
        self.title_generator = title_generator
        self.history_limit = history_limit
        self.stream_timeout_seconds = stream_timeout_seconds

    async def _create_conversation(
        self,
        access: WorkspaceAccessContext,
    ) -> ConversationResponse:
        async with self.session_factory.begin() as session:
            repository = ConversationRepository(session)

            conversation = await repository.create(
                Conversation(
                    workspace_id=access.workspace_id,
                    user_id=access.user_id,
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

    @asynccontextmanager
    async def stream_message(
        self,
        access: WorkspaceAccessContext,
        data: SendMessageRequest,
    ) -> AsyncIterator[AsyncIterator[dict[str, str]]]:
        events = self._generate_message(access, data)
        body = None
        try:
            first = await anext(events)
            body = self._sse_events(first, events)
            yield body
        finally:
            with CancelScope(shield=True):
                with move_on_after(5):
                    if body is not None:
                        await body.aclose()
                    await events.aclose()

    @staticmethod
    async def _sse_events(
        first: ChatStreamEvent,
        events: AsyncIterator[ChatStreamEvent],
    ) -> AsyncIterator[dict[str, str]]:
        yield {"event": first.kind, "data": first.model_dump_json()}
        async for event in events:
            yield {"event": event.kind, "data": event.model_dump_json()}

    async def _generate_message(
        self,
        access: WorkspaceAccessContext,
        data: SendMessageRequest,
    ) -> AsyncIterator[ChatStreamEvent]:
        if data.conversation_id is None:
            conversation = await self._create_conversation(access)
        else:
            conversation = await self.get_conversation(access, data.conversation_id)

        conversation_id = conversation.id

        lease = self.redis.lock(
            name=(
                f"chat:generation:"
                f"{access.workspace_id}:{conversation_id}"
            ),
            timeout=self.stream_timeout_seconds + 30,
            blocking=False,
            thread_local=False,
        )

        if not await lease.acquire():
            raise ConversationBusyError()

        started = False

        deadline = (
            get_running_loop().time()
            + self.stream_timeout_seconds
        )

        try:
            async with timeout_at(deadline):
                history, previous_message_id = (
                    await self._load_history(
                        access=access,
                        conversation_id=conversation_id,
                    )
                )

                if not await lease.owned():
                    raise ChatGenerationLeaseLostError()

            started = True

            yield ChatStreamStarted(
                conversation_id=conversation_id,
            )

            completed = None

            async with aclosing(
                self.agent.stream(
                    workspace_id=access.workspace_id,
                    question=data.content,
                    history=history,
                )
            ) as agent_events:
                while True:
                    if get_running_loop().time() >= deadline:
                        raise ChatStreamTimeoutError()

                    try:
                        async with timeout_at(deadline):
                            event = await anext(agent_events)

                            if event.kind in {
                                "turn_start",
                                "tool_start",
                            }:
                                if not await lease.owned():
                                    raise ChatGenerationLeaseLostError()

                    except StopAsyncIteration:
                        break

                    if isinstance(event, AgentCompleted):
                        completed = event
                        break

                    yield event

            if completed is None:
                raise ChatAgentError(
                    "The agent stream ended without a final answer"
                )

            citations = self._select_citations(
                text=completed.result.response.message.text,
                sources=completed.result.sources,
            )

            if get_running_loop().time() >= deadline:
                raise ChatStreamTimeoutError()

            async with timeout_at(deadline):
                title = conversation.title
                if title is None:
                    title = await self.title_generator.generate(
                        question=history[0].content if history else data.content,
                        answer=completed.result.response.message.text,
                    )

                if not await lease.owned():
                    raise ChatGenerationLeaseLostError()

                saved = await self._save_turn(
                    access=access,
                    conversation_id=conversation_id,
                    data=data,
                    result=completed.result,
                    citations=citations,
                    previous_message_id=previous_message_id,
                    title=title,
                )

            yield ChatStreamDone(
                turn_id=completed.turn_id,
                response=saved,
            )

        except TimeoutError as exc:
            if not started:
                raise ChatStreamTimeoutError() from exc

            yield ChatStreamError(
                code="timeout",
                message="Chat stream execution timed out",
                status_code=504,
            )

        except AppException as exc:
            if not started:
                raise

            yield ChatStreamError(
                code="chat_failed",
                message=exc.message,
                status_code=exc.status_code,
            )

        except LLMError:
            logger.exception(
                "LLM stream failed | conversation_id=%s",
                conversation_id,
            )

            if not started:
                raise ChatAgentError(
                    "The model request failed"
                )

            yield ChatStreamError(
                code="llm_failed",
                message="The model request failed",
                status_code=502,
            )

        except Exception:
            logger.exception(
                "Chat stream failed | conversation_id=%s",
                conversation_id,
            )

            if not started:
                raise

            yield ChatStreamError(
                code="internal_error",
                message="An error occurred while generating the answer",
                status_code=500,
            )

        finally:
            with CancelScope(shield=True):
                with move_on_after(3):
                    try:
                        await lease.release()
                    except RedisError:
                        logger.warning(
                            "Failed to release chat generation lease "
                            "| conversation_id=%s",
                            conversation_id,
                            exc_info=True,
                        )


    async def _save_turn(
        self,
        *,
        access: WorkspaceAccessContext,
        conversation_id: int,
        data: SendMessageRequest,
        result: ChatAgentResult,
        citations: list[KnowledgeSource],
        previous_message_id: int | None,
        title: str | None,
    ) -> SendMessageResponse:
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
                latest[-1].id if latest else None
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
                conversation.title = title

            await ConversationRepository(session).touch(
                conversation
            )

            response = SendMessageResponse(
                conversation_id=conversation_id,
                title=conversation.title,
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

        return response


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