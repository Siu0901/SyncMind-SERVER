from fastapi import APIRouter, UploadFile, status

from app.domains.document.dependencies import (
    CurrentDocumentDep,
    DocumentServiceDep,
    DocumentUploadServiceDep,
)
from app.domains.document.schema import (
    DocumentChunkResponse,
    DocumentResponse,
    DocumentUpdateRequest,
    DocumentVersionResponse,
)
from app.domains.workspace.dependencies import (
    CurrentWorkSpaceDep,
    WorkspaceAdminDep,
)


document_router = APIRouter(
    prefix="/workspaces/{workspace_id}/documents",
    tags=["documents"],
)


@document_router.get(
    "/list",
    response_model=list[DocumentResponse],
)
async def get_documents(
    workspace: CurrentWorkSpaceDep,
    service: DocumentServiceDep,
):
    return await service.get_documents(workspace.id)


@document_router.get(
    "/{document_id}",
    response_model=DocumentResponse,
)
async def get_document(document: CurrentDocumentDep):
    return document


@document_router.patch(
    "/patch/{document_id}",
    response_model=DocumentResponse,
)
async def update_document(
    document: CurrentDocumentDep,
    _: WorkspaceAdminDep,
    data: DocumentUpdateRequest,
    service: DocumentServiceDep,
):
    return await service.update_document(document, data)

# 이거 나중에 qdrant 짤때 되면 거기 데이터도 삭제되게 해야됨
@document_router.delete("/delete/{document_id}")
async def delete_document(
    document: CurrentDocumentDep,
    _: WorkspaceAdminDep,
    service: DocumentServiceDep,
):
    await service.delete_document(document)


@document_router.get(
    "/{document_id}/versions",
    response_model=list[DocumentVersionResponse],
)
async def get_document_versions(
    document: CurrentDocumentDep,
    service: DocumentServiceDep,
):
    return await service.get_versions(document)


@document_router.get(
    "/{document_id}/chunks",
    response_model=list[DocumentChunkResponse],
)
async def get_document_chunks(
    document: CurrentDocumentDep,
    service: DocumentServiceDep,
):
    return await service.get_chunks(document)


@document_router.post(
    "/upload",
    response_model=DocumentResponse,
)
async def upload_document(
    workspace: CurrentWorkSpaceDep,
    _: WorkspaceAdminDep,
    file: UploadFile,
    service: DocumentUploadServiceDep,
):
    return await service.upload(
        workspace_id=workspace.id,
        file=file,
    )