from typing import Annotated

from fastapi import Depends

from app.core.dependencies import SessionDep
from app.domains.document.exceptions import DocumentNotFoundError
from app.domains.document.model import Document
from app.domains.document.repository import (
    DocumentChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
)
from app.domains.document.service import DocumentService
from app.domains.workspace.dependencies import CurrentWorkSpaceDep


def get_document_repository(session: SessionDep) -> DocumentRepository:
    return DocumentRepository(session)

def get_document_version_repository(session: SessionDep) -> DocumentVersionRepository:
    return DocumentVersionRepository(session)

def get_document_chunk_repository(session: SessionDep) -> DocumentChunkRepository:
    return DocumentChunkRepository(session)

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


def get_document_service(
    session: SessionDep,
    document_repository: DocumentRepositoryDep,
    version_repository: DocumentVersionRepositoryDep,
    chunk_repository: DocumentChunkRepositoryDep
) -> DocumentService:
    return DocumentService(
        session=session,
        documents_repo=document_repository,
        versions_repo=version_repository,
        chunks_repo=chunk_repository
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