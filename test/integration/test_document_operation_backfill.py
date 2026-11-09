"""Run the data migration against PostgreSQL without external side effects."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


pytestmark = pytest.mark.anyio

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations/versions/8f3c2a6d91b4_backfill_document_operations.py"
)


@pytest.fixture
async def connection(db_engine):
    # All seed rows and migration writes are rolled back, including records
    # without FKs that the shared repository cleanup cannot otherwise remove.
    async with db_engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn
        finally:
            await transaction.rollback()


async def run_migration(connection, *, downgrade=False):
    spec = importlib.util.spec_from_file_location(
        "document_operation_backfill", MIGRATION_PATH
    )
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


async def seed_document(
    connection,
    *,
    status="READY",
    job_status="COMPLETED",
    keys=("original.pdf",),
    removed=False,
    lease=None,
):
    workspace_id = await connection.scalar(
        text("INSERT INTO workspace (name) VALUES ('backfill-test') RETURNING id")
    )
    document_id = await connection.scalar(
        text("""
            INSERT INTO document (
                workspace_id, title, status, current_version, deleted_at
            ) VALUES (
                :workspace_id, 'legacy.pdf', :status, :current_version,
                CASE WHEN :removed THEN now() ELSE NULL END
            ) RETURNING id
        """),
        {
            "workspace_id": workspace_id,
            "status": status,
            "current_version": 1 if status == "READY" else None,
            "removed": removed,
        },
    )
    version_id = None
    for number, key in enumerate(keys, start=1):
        version_id = await connection.scalar(
            text("""
                INSERT INTO document_version (
                    document_id, version, content_hash, s3_key, is_active
                ) VALUES (
                    :document_id, :version, :content_hash, :s3_key, :active
                ) RETURNING id
            """),
            {
                "document_id": document_id,
                "version": number,
                "content_hash": f"{document_id:032x}{number:032x}",
                "s3_key": key,
                "active": status == "READY" and number == 1,
            },
        )

    job_id = None
    if job_status is not None:
        job_id = await connection.scalar(
            text("""
                INSERT INTO ingestion_job (
                    document_version_id, status, retry_count,
                    lease_expires_at, error_message
                ) VALUES (
                    :version_id, :status, 0, :lease, :error
                ) RETURNING id
            """),
            {
                "version_id": version_id,
                "status": job_status,
                "lease": lease,
                "error": "original failure" if job_status == "FAILED" else None,
            },
        )

    return document_id, job_id


async def get_operation(connection, document_id):
    result = await connection.execute(
        text("SELECT * FROM document_operation WHERE document_id = :id"),
        {"id": document_id},
    )
    return dict(result.mappings().one())


async def test_ready_documents_keep_all_distinct_keys_and_source_rows(connection):
    document_id, job_id = await seed_document(
        connection,
        keys=("b.pdf", "a.pdf", "b.pdf", None),
    )
    no_job_id, _ = await seed_document(connection, job_status=None, keys=(None,))
    before = await connection.scalar(text("""
        SELECT jsonb_build_object(
            'documents', (SELECT jsonb_agg(to_jsonb(d) ORDER BY id) FROM document d),
            'versions', (SELECT jsonb_agg(to_jsonb(v) ORDER BY id) FROM document_version v),
            'jobs', (SELECT jsonb_agg(to_jsonb(j) ORDER BY id) FROM ingestion_job j)
        )
    """))

    await run_migration(connection)

    operation = await get_operation(connection, document_id)
    assert operation["status"] == "ready"
    assert operation["ingestion_job_id"] == job_id
    assert operation["s3_keys"] == ["a.pdf", "b.pdf"]
    assert operation["reason"] is None
    assert operation["next_run_at"] is None
    assert operation["cleanup_attempts"] == 0
    assert (await get_operation(connection, no_job_id))["s3_keys"] == []

    after = await connection.scalar(text("""
        SELECT jsonb_build_object(
            'documents', (SELECT jsonb_agg(to_jsonb(d) ORDER BY id) FROM document d),
            'versions', (SELECT jsonb_agg(to_jsonb(v) ORDER BY id) FROM document_version v),
            'jobs', (SELECT jsonb_agg(to_jsonb(j) ORDER BY id) FROM ingestion_job j)
        )
    """))
    assert after == before


async def test_queued_and_processing_jobs_preserve_their_state(connection):
    queued_id, _ = await seed_document(
        connection, status="PENDING", job_status="QUEUED"
    )
    lease = datetime.now(timezone.utc) + timedelta(minutes=10)
    processing_id, _ = await seed_document(
        connection, status="PROCESSING", job_status="PROCESSING", lease=lease
    )
    expired_id, _ = await seed_document(
        connection,
        status="PROCESSING",
        job_status="PROCESSING",
        lease=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    no_lease_id, _ = await seed_document(
        connection, status="PROCESSING", job_status="PROCESSING"
    )

    await run_migration(connection)

    queued = await get_operation(connection, queued_id)
    processing = await get_operation(connection, processing_id)
    expired = await get_operation(connection, expired_id)
    no_lease = await get_operation(connection, no_lease_id)
    assert queued["status"] == "queued"
    assert queued["next_run_at"] <= datetime.now(timezone.utc)
    assert processing["status"] == "processing"
    assert processing["next_run_at"] == lease
    assert processing["reason"] is None
    assert expired["next_run_at"] <= datetime.now(timezone.utc)
    assert no_lease["next_run_at"] > datetime.now(timezone.utc)


async def test_failed_removed_and_unrecoverable_documents_need_cleanup(connection):
    failed_id, _ = await seed_document(
        connection, status="FAILED", job_status="FAILED"
    )
    removed_id, _ = await seed_document(connection, removed=True)
    interrupted_id, _ = await seed_document(
        connection, status="PENDING", job_status=None
    )
    retry_wait_id, _ = await seed_document(
        connection, status="PROCESSING", job_status="RETRY_WAIT"
    )

    await run_migration(connection)

    failed = await get_operation(connection, failed_id)
    assert failed["status"] == "cleanup_required"
    assert failed["reason"] == "PROCESSING_FAILED"
    assert failed["error_message"] == "original failure"
    assert failed["next_run_at"] <= datetime.now(timezone.utc)
    assert (await get_operation(connection, removed_id))["reason"] == "USER_DELETE"
    for document_id in [interrupted_id, retry_wait_id]:
        operation = await get_operation(connection, document_id)
        assert operation["status"] == "cleanup_required"
        assert operation["reason"] == "INTERRUPTED"


async def test_existing_operations_rerun_and_downgrade_are_non_destructive(connection):
    existing_id, job_id = await seed_document(connection)
    await connection.execute(
        text("""
            INSERT INTO document_operation (
                document_id, workspace_id, title, ingestion_job_id,
                status, s3_keys, reason, error_message, cleanup_error,
                cleanup_attempts, next_run_at
            ) SELECT
                id, workspace_id, title, :job_id,
                'cleanup_required', '["saved.pdf"]'::jsonb,
                'USER_DELETE', 'preserved error', 'cleanup failure',
                4, now() + interval '30 minutes'
            FROM document WHERE id = :id
        """),
        {"id": existing_id, "job_id": job_id},
    )
    existing = await get_operation(connection, existing_id)
    new_id, _ = await seed_document(connection)

    await run_migration(connection)
    first = await get_operation(connection, new_id)
    await run_migration(connection)
    await run_migration(connection, downgrade=True)

    assert await get_operation(connection, existing_id) == existing
    assert await get_operation(connection, new_id) == first
