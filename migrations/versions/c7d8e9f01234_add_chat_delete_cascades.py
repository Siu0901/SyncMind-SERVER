"""Cascade conversation deletion through messages and citation links.

Revision ID: c7d8e9f01234
Revises: 8f3c2a6d91b4
Create Date: 2026-11-20
"""

from alembic import op


revision = "c7d8e9f01234"
down_revision = "8f3c2a6d91b4"
branch_labels = None
depends_on = None


_FOREIGN_KEYS = (
    (
        "chat_message",
        "conversation_id",
        "conversation",
        "chat_message_conversation_id_fkey",
    ),
    (
        "message_citation",
        "message_id",
        "chat_message",
        "message_citation_message_id_fkey",
    ),
)


def _replace_foreign_keys(ondelete: str | None) -> None:
    for table, column, parent, name in _FOREIGN_KEYS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            name,
            table,
            parent,
            [column],
            ["id"],
            ondelete=ondelete,
        )


def upgrade() -> None:
    _replace_foreign_keys("CASCADE")


def downgrade() -> None:
    _replace_foreign_keys(None)
