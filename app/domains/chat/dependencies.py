from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

from app.core.dependencies import SessionFactoryDep, RedisDep
from app.domains.chat.service import ChatService
from app.domains.chat.agent import ChatAgent
from app.domains.chat.schema import SendMessageRequest
from app.domains.chat.title import ConversationTitleGenerator
from app.domains.knowledge.dependencies import RetrievalServiceDep
from app.domains.workspace.dependencies import CurrentWorkspaceAccessDep
from app.infra.llm.factory import get_llm
from app.infra.llm.port import LLMPort


LLMDep = Annotated[LLMPort, Depends(get_llm)]

CurrentChatAccessDep = CurrentWorkspaceAccessDep


def get_chat_agent(
    llm: LLMDep,
    retrieval: RetrievalServiceDep,
) -> ChatAgent:
    return ChatAgent(llm=llm, retrieval=retrieval)

ChatAgentDep = Annotated[ChatAgent, Depends(get_chat_agent)]


def get_chat_service(
    session_factory: SessionFactoryDep,
    agent: ChatAgentDep,
    redis: RedisDep,
    llm: LLMDep,
) -> ChatService:
    return ChatService(
        session_factory=session_factory,
        agent=agent,
        redis=redis,
        title_generator=ConversationTitleGenerator(llm),
    )

ChatServiceDep = Annotated[
    ChatService,
    Depends(get_chat_service),
]


async def get_chat_message_stream(
    data: SendMessageRequest,
    access: CurrentChatAccessDep,
    service: ChatServiceDep,
) -> AsyncIterator[AsyncIterator[dict[str, str]]]:
    async with service.stream_message(access, data) as events:
        yield events


ChatMessageStreamDep = Annotated[
    AsyncIterator[dict[str, str]],
    Depends(get_chat_message_stream, scope="request"),
]
