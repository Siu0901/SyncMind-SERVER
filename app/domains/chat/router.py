from typing import Annotated

from fastapi import APIRouter, Path, Query, Response
from sse_starlette import EventSourceResponse

from app.domains.chat.dependencies import (
    ChatMessageStreamDep,
    ChatServiceDep,
    CurrentChatAccessDep,
)
from app.domains.chat.schema import (
    ChatMessageResponse,
    ConversationResponse,
    ConversationUpdateRequest,
    MessageCitationResponse,
)


chat_router = APIRouter(
    prefix="/workspaces/{workspace_id}/conversations",
    tags=["chat"],
)


@chat_router.get(
    "",
    response_model=list[ConversationResponse],
)
async def list_conversations(
    access: CurrentChatAccessDep,
    service: ChatServiceDep,
    before_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await service.list_conversations(
        access,
        before_id=before_id,
        limit=limit,
    )


@chat_router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
)
async def get_conversation(
    conversation_id: Annotated[int, Path(gt=0)],
    access: CurrentChatAccessDep,
    service: ChatServiceDep,
):
    return await service.get_conversation(
        access,
        conversation_id,
    )


@chat_router.patch(
    "/{conversation_id}",
    response_model=ConversationResponse,
)
async def update_conversation(
    conversation_id: Annotated[int, Path(gt=0)],
    data: ConversationUpdateRequest,
    access: CurrentChatAccessDep,
    service: ChatServiceDep,
):
    return await service.update_conversation(
        access,
        conversation_id,
        data,
    )


@chat_router.delete(
    "/{conversation_id}",
    status_code=204,
    response_class=Response,
)
async def delete_conversation(
    conversation_id: Annotated[int, Path(gt=0)],
    access: CurrentChatAccessDep,
    service: ChatServiceDep,
):
    return await service.delete_conversation(
        access,
        conversation_id,
    )


@chat_router.get(
    "/{conversation_id}/messages",
    response_model=list[ChatMessageResponse],
)
async def list_messages(
    conversation_id: Annotated[int, Path(gt=0)],
    access: CurrentChatAccessDep,
    service: ChatServiceDep,
    before_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await service.list_messages(
        access,
        conversation_id,
        before_id=before_id,
        limit=limit,
    )


@chat_router.get(
    "/{conversation_id}/messages/{message_id}/citations",
    response_model=list[MessageCitationResponse],
)
async def get_message_citations(
    conversation_id: Annotated[int, Path(gt=0)],
    message_id: Annotated[int, Path(gt=0)],
    access: CurrentChatAccessDep,
    service: ChatServiceDep,
):
    return await service.get_message_citations(
        access,
        conversation_id,
        message_id,
    )


@chat_router.post(
    "/messages",
    response_class=EventSourceResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {}
            }
        }
    },
)
async def send_message(events: ChatMessageStreamDep):
    return EventSourceResponse(
        events,
        ping=15,
        send_timeout=10
    )
