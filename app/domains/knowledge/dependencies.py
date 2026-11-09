from typing import Annotated, Optional

from fastapi import Depends

from app.core.config import get_settings

from app.core.dependencies import (
    SessionDep,
    RedisDep,
    S3ClientDep,
    QdrantDep
)
from app.domains.knowledge.document.exceptions import DocumentNotFoundError
from app.domains.knowledge.document.model import Document
from app.domains.knowledge.document.repository import (
    DocumentChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
)
from app.domains.knowledge.services.document import (
    DocumentService,
    DocumentUploadService,
)
from app.domains.knowledge.services.retrieval import RetrievalService
from app.domains.knowledge.services.operation import DocumentOperationService
from app.domains.knowledge.ingestion.repository import IngestionJobRepository
from app.domains.knowledge.vector_repository import QdrantVectorRepository
from app.domains.knowledge.services.lifecycle import DocumentLifecycleService
from app.domains.knowledge.operation.repository import DocumentOperationRepository
from app.domains.workspace.dependencies import CurrentWorkSpaceDep

from app.infra.embedding.factory import get_embedding
from app.infra.embedding.port import EmbeddingPort

from app.infra.reranking.factory import get_reranker
from app.infra.reranking.port import RerankerPort


settings = get_settings()


def get_document_repository(session: SessionDep) -> DocumentRepository:
    return DocumentRepository(session)

def get_document_version_repository(session: SessionDep) -> DocumentVersionRepository:
    return DocumentVersionRepository(session)

def get_document_chunk_repository(session: SessionDep) -> DocumentChunkRepository:
    return DocumentChunkRepository(session)

def get_vector_repository(client: QdrantDep) -> QdrantVectorRepository:
    return QdrantVectorRepository(client)

def get_ingestion_job_repository(session: SessionDep) -> IngestionJobRepository:
    return IngestionJobRepository(session)

def get_document_operation_repository(session: SessionDep) -> DocumentOperationRepository:
    return DocumentOperationRepository(session)

DocumentRepositoryDep = Annotated[
    DocumentRepository,
    Depends(get_document_repository)
]
DocumentVersionRepositoryDep = Annotated[
    DocumentVersionRepository,
    Depends(get_document_version_repository)
]
DocumentChunkRepositoryDep = Annotated[
    DocumentChunkRepository,
    Depends(get_document_chunk_repository)
]
QdrantVectorRepositoryDep = Annotated[
    QdrantVectorRepository,
    Depends(get_vector_repository),
]
EmbeddingDep = Annotated[
    EmbeddingPort,
    Depends(get_embedding),
]
RerankerDep = Annotated[
    Optional[RerankerPort],
    Depends(get_reranker),
]
IngestionJobRepositoryDep = Annotated[
    IngestionJobRepository,
    Depends(get_ingestion_job_repository),
]
DocumentOperationRepositoryDep = Annotated[
    DocumentOperationRepository,
    Depends(get_document_operation_repository),
]


def get_document_operation_service(
    repository: DocumentOperationRepositoryDep,
) -> DocumentOperationService:
    return DocumentOperationService(repository=repository)


DocumentOperationServiceDep = Annotated[
    DocumentOperationService,
    Depends(get_document_operation_service),
]


def get_document_lifecycle_service(
    session: SessionDep,
    redis: RedisDep,
    s3: S3ClientDep,
    vectors: QdrantVectorRepositoryDep,
) -> DocumentLifecycleService:
    return DocumentLifecycleService(
        session=session,
        s3=s3,
        vectors=vectors,
        redis=redis,
    )

DocumentLifecycleServiceDep = Annotated[
    DocumentLifecycleService,
    Depends(get_document_lifecycle_service),
]


def get_document_service(
    session: SessionDep,
    document_repository: DocumentRepositoryDep,
    version_repository: DocumentVersionRepositoryDep,
    chunk_repository: DocumentChunkRepositoryDep,
    vector_repository: QdrantVectorRepositoryDep,
    s3: S3ClientDep,
    lifecycle: DocumentLifecycleServiceDep,
) -> DocumentService:
    return DocumentService(
        session=session,
        documents_repo=document_repository,
        versions_repo=version_repository,
        chunks_repo=chunk_repository,
        vector_repo=vector_repository,
        s3=s3,
        lifecycle=lifecycle,
    )

DocumentServiceDep = Annotated[
    DocumentService,
    Depends(get_document_service),
]


async def get_current_document(
    document_id: int,
    workspace: CurrentWorkSpaceDep,
    documents_repo: DocumentRepositoryDep,
) -> Document:
    document = await documents_repo.get_by_id(
        workspace_id=workspace.id,
        document_id=document_id,
    )

    if document is None:
        raise DocumentNotFoundError()

    return document

CurrentDocumentDep = Annotated[
    Document,
    Depends(get_current_document),
]


def get_document_upload_service(
    session: SessionDep,
    redis: RedisDep,
    s3: S3ClientDep,
    documents_repo: DocumentRepositoryDep,
    versions_repo: DocumentVersionRepositoryDep,
    ingestion_repo: IngestionJobRepositoryDep,
    lifecycle: DocumentLifecycleServiceDep,
) -> DocumentUploadService:
    return DocumentUploadService(
        session=session,
        redis=redis,
        s3=s3,
        documents_repo=documents_repo,
        versions_repo=versions_repo,
        ingestion_repo=ingestion_repo,
        lifecycle=lifecycle,
    )

DocumentUploadServiceDep = Annotated[
    DocumentUploadService,
    Depends(get_document_upload_service),
]


def get_retrieval_service(
    embedding: EmbeddingDep,
    vector_repo: QdrantVectorRepositoryDep,
    documents_repo: DocumentRepositoryDep,
    reranker: RerankerDep,
) -> RetrievalService:
    return RetrievalService(
        embedding=embedding,
        vector_repo=vector_repo,
        documents_repo=documents_repo,
        reranker=reranker,
        rerank_candidate_limit=(
            settings.RERANK_CANDIDATE_LIMIT
        ),
    )

RetrievalServiceDep = Annotated[
    RetrievalService,
    Depends(get_retrieval_service),
]
