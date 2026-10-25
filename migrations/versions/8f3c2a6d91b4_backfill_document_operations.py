"""Backfill durable operation records for existing documents.

Revision ID: 8f3c2a6d91b4
Revises: 2ac4b53d3507
Create Date: 2026-10-25

"""

from alembic import op


revision = "8f3c2a6d91b4"
down_revision = "2ac4b53d3507"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve existing operations, including cleanup records whose document
    # has already been removed. Completed documents must never become cleanup
    # candidates merely because they predate the operation table.
    op.execute("""
        WITH legacy_documents AS (
            SELECT
                d.id AS document_id,
                d.workspace_id,
                d.title,
                latest_job.id AS ingestion_job_id,
                latest_job.error_message,
                latest_job.lease_expires_at,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            DISTINCT v.s3_key ORDER BY v.s3_key
                        )
                        FROM document_version v
                        WHERE v.document_id = d.id
                          AND v.s3_key IS NOT NULL
                    ),
                    '[]'::jsonb
                ) AS s3_keys,
                CASE
                    WHEN d.deleted_at IS NOT NULL
                        THEN 'cleanup_required'
                    WHEN d.status::text = 'READY'
                        THEN 'ready'
                    WHEN d.status::text = 'FAILED'
                        THEN 'cleanup_required'
                    WHEN latest_job.status::text = 'QUEUED'
                        THEN 'queued'
                    WHEN latest_job.status::text = 'PROCESSING'
                        THEN 'processing'
                    ELSE 'cleanup_required'
                END AS operation_status,
                CASE
                    WHEN d.deleted_at IS NOT NULL THEN 'USER_DELETE'
                    WHEN d.status::text = 'READY' THEN NULL
                    WHEN d.status::text = 'FAILED'
                        THEN 'PROCESSING_FAILED'
                    WHEN latest_job.status::text IN ('QUEUED', 'PROCESSING')
                        THEN NULL
                    WHEN latest_job.status::text = 'FAILED'
                        THEN 'PROCESSING_FAILED'
                    ELSE 'INTERRUPTED'
                END AS reason
            FROM document d
            LEFT JOIN LATERAL (
                SELECT
                    j.id,
                    j.status,
                    j.error_message,
                    j.lease_expires_at
                FROM ingestion_job j
                JOIN document_version v
                  ON v.id = j.document_version_id
                WHERE v.document_id = d.id
                ORDER BY j.id DESC
                LIMIT 1
            ) latest_job ON true
            WHERE NOT EXISTS (
                SELECT 1
                FROM document_operation existing
                WHERE existing.document_id = d.id
            )
        )
        INSERT INTO document_operation (
            document_id,
            workspace_id,
            title,
            ingestion_job_id,
            status,
            s3_keys,
            reason,
            error_message,
            cleanup_attempts,
            next_run_at
        )
        SELECT
            document_id,
            workspace_id,
            title,
            ingestion_job_id,
            operation_status,
            s3_keys,
            reason,
            CASE
                WHEN operation_status = 'cleanup_required'
                    THEN error_message
                ELSE NULL
            END,
            0,
            CASE
                WHEN operation_status = 'ready' THEN NULL
                WHEN operation_status = 'processing' THEN GREATEST(
                    now(),
                    COALESCE(
                        lease_expires_at,
                        now() + interval '15 minutes'
                    )
                )
                ELSE now()
            END
        FROM legacy_documents
        ON CONFLICT (document_id) DO NOTHING
    """)


def downgrade() -> None:
    # These rows can acquire live dispatch/cleanup progress after migration.
    # A data-only downgrade must not erase durable work or failure history.
    pass
