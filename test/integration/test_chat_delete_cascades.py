import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlmodel import delete, text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.chat.model import (
    ChatMessage, Conversation, MessageCitation, MessageRole,
)
from app.domains.chat.repository import ConversationRepository
from app.domains.identity.user.model import User
from app.domains.knowledge.document.model import (
    Document, DocumentChunk, DocumentStatus, DocumentVersion,
)
from app.domains.workspace.model import WorkSpace


pytestmark = [pytest.mark.anyio, pytest.mark.integration]

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations/versions/c7d8e9f01234_add_chat_delete_cascades.py"
)


@pytest.fixture
async def connection(db_engine):
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


@pytest.fixture
async def session(connection):
    async with AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        yield session


async def run_migration(connection, *, downgrade=False):
    spec = importlib.util.spec_from_file_location("chat_cascades", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def run(sync_connection):
        context = MigrationContext.configure(sync_connection)
        with Operations.context(context):
            if downgrade:
                migration.downgrade()
            else:
                migration.upgrade()

    await connection.run_sync(run)


async def delete_actions(connection):
    rows = await connection.execute(text("""
        SELECT conname, confdeltype
        FROM pg_constraint
        WHERE conname IN (
            'chat_message_conversation_id_fkey',
            'message_citation_message_id_fkey'
        )
    """))
    return dict(rows.all())


async def seed(session):
    user = User(email=f"cascade-{uuid4().hex}@example.com", name="Cascade test")
    workspace = WorkSpace(name="Cascade test")
    session.add_all([user, workspace])
    await session.flush()

    document = Document(
        workspace_id=workspace.id,
        title="Evidence",
        status=DocumentStatus.READY,
        current_version=1,
    )
    conversation = Conversation(workspace_id=workspace.id, user_id=user.id)
    other = Conversation(workspace_id=workspace.id, user_id=user.id)
    session.add_all([document, conversation, other])
    await session.flush()

    version = DocumentVersion(
        document_id=document.id, version=1, content_hash="a" * 64, is_active=True,
    )
    question = ChatMessage(
        conversation_id=conversation.id, role=MessageRole.USER, content="Question",
    )
    answer = ChatMessage(
        conversation_id=conversation.id, role=MessageRole.ASSISTANT, content="Answer",
    )
    other_answer = ChatMessage(
        conversation_id=other.id, role=MessageRole.ASSISTANT, content="Other answer",
    )
    session.add_all([version, question, answer, other_answer])
    await session.flush()

    chunk = DocumentChunk(
        document_version_id=version.id, chunk_index=0, content="Shared evidence",
    )
    session.add(chunk)
    await session.flush()
    session.add_all([
        MessageCitation(message_id=answer.id, chunk_id=chunk.id, citation_order=1),
        MessageCitation(message_id=other_answer.id, chunk_id=chunk.id, citation_order=1),
    ])
    await session.flush()
    return SimpleNamespace(
        user_id=user.id, workspace_id=workspace.id,
        conversation_id=conversation.id, other_id=other.id,
        answer_id=answer.id, other_answer_id=other_answer.id, chunk_id=chunk.id,
    )


async def counts(session):
    result = await session.exec(text("""
        SELECT
            (SELECT count(*) FROM conversation),
            (SELECT count(*) FROM chat_message),
            (SELECT count(*) FROM message_citation),
            (SELECT count(*) FROM document_chunk)
    """))
    return tuple(result.one())


async def test_upgrade_preserves_data_and_changes_both_foreign_keys(connection, session):
    await seed(session)
    before = await counts(session)
    await run_migration(connection, downgrade=True)
    assert set((await delete_actions(connection)).values()) == {"a"}

    await run_migration(connection)

    actions = await delete_actions(connection)
    assert len(actions) == 2
    assert set(actions.values()) == {"c"}
    assert await counts(session) == before


async def test_repository_deletes_only_parent_and_db_cascades(connection, session):
    data = await seed(session)
    before = await counts(session)
    statements = []

    def record_delete(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("DELETE"):
            statements.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", record_delete)
    try:
        deleted = await ConversationRepository(session).delete_owned(
            workspace_id=data.workspace_id,
            user_id=data.user_id,
            conversation_id=data.conversation_id,
        )
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", record_delete)

    assert deleted
    assert len(statements) == 1
    assert statements[0].startswith("DELETE FROM conversation")
    assert await counts(session) == (
        before[0] - 1, before[1] - 2, before[2] - 1, before[3],
    )
    remaining = await session.exec(text(
        "SELECT message_id FROM message_citation WHERE chunk_id = :chunk_id"
    ).bindparams(chunk_id=data.chunk_id))
    assert remaining.scalars().all() == [data.other_answer_id]


async def test_direct_message_delete_cascades_to_citations(session):
    data = await seed(session)
    before = await counts(session)

    await session.exec(delete(ChatMessage).where(ChatMessage.id == data.answer_id))

    assert await counts(session) == (
        before[0], before[1] - 1, before[2] - 1, before[3],
    )


@pytest.mark.parametrize("wrong_scope", ["user", "workspace", "conversation"])
async def test_delete_cannot_cross_ownership_scope(session, wrong_scope):
    data = await seed(session)
    before = await counts(session)
    scope = dict(
        workspace_id=data.workspace_id,
        user_id=data.user_id,
        conversation_id=data.conversation_id,
    )
    scope[f"{wrong_scope}_id"] = -1

    assert not await ConversationRepository(session).delete_owned(**scope)
    assert await counts(session) == before


async def test_cascade_deletion_can_be_rolled_back(session):
    data = await seed(session)
    before = await counts(session)
    async with session.begin_nested() as transaction:
        assert await ConversationRepository(session).delete_owned(
            workspace_id=data.workspace_id,
            user_id=data.user_id,
            conversation_id=data.conversation_id,
        )
        await transaction.rollback()

    assert await counts(session) == before


async def test_downgrade_restores_restrictive_foreign_keys(connection, session):
    data = await seed(session)
    before = await counts(session)

    await run_migration(connection, downgrade=True)

    assert set((await delete_actions(connection)).values()) == {"a"}
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.exec(delete(Conversation).where(
                Conversation.id == data.conversation_id,
            ))
    assert await counts(session) == before
