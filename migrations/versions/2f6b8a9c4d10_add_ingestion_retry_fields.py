"""Add ingestion retry and execution ownership fields.

Revision ID: 2f6b8a9c4d10
Revises: 89556cfeeca8
Create Date: 2026-10-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "2f6b8a9c4d10"
down_revision: Union[str, Sequence[str], None] = "89556cfeeca8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE ingestionjobstatus ADD VALUE 'RETRY_WAIT'")

    op.add_column(
        "ingestion_job",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_job",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_job",
        sa.Column("execution_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "ingestion_job",
        sa.Column("error_code", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value. Preserve waiting jobs as QUEUED
    # before recreating the original enum type.
    op.execute(
        "UPDATE ingestion_job SET status = 'QUEUED' "
        "WHERE status::text = 'RETRY_WAIT'"
    )
    op.alter_column(
        "ingestion_job",
        "status",
        type_=sa.Text(),
        existing_nullable=False,
        postgresql_using="status::text",
    )

    original_status = postgresql.ENUM(
        "QUEUED", "PROCESSING", "COMPLETED", "FAILED",
        name="ingestionjobstatus",
    )
    original_status.drop(op.get_bind(), checkfirst=False)
    original_status.create(op.get_bind(), checkfirst=False)

    op.alter_column(
        "ingestion_job",
        "status",
        type_=original_status,
        existing_nullable=False,
        postgresql_using="status::ingestionjobstatus",
    )

    op.drop_column("ingestion_job", "error_code")
    op.drop_column("ingestion_job", "execution_token")
    op.drop_column("ingestion_job", "lease_expires_at")
    op.drop_column("ingestion_job", "next_retry_at")
