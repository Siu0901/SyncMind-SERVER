from fastapi import APIRouter, UploadFile

from app.core.exception.exceptions import AppException
from app.domains.knowledge.operation.model import OperationStatus
from app.domains.knowledge.dependencies import (
    CurrentDocumentDep,
    DocumentServiceDep,
    DocumentUploadServiceDep,
    RetrievalServiceDep,
    DocumentOperationRepositoryDep
)
from app.domains.knowledge.schema import (
    SearchRequest,
    DocumentChunkResponse,
    DocumentResponse,
    DocumentUpdateRequest,
    DocumentVersionResponse,
    RetrievalResult,
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


@document_router.delete(
    "/delete/{document_id}",
    status_code=202,
)
async def delete_document(
    document: CurrentDocumentDep,
    _: WorkspaceAdminDep,
    service: DocumentServiceDep,
):
    await service.delete_document(document)
    return {"message": "문서 삭제 요청을 접수했습니다."}


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
    status_code=202,
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


@document_router.post(
    "/search",
    response_model=list[RetrievalResult],
)
async def search_documents(
    workspace: CurrentWorkSpaceDep,
    request: SearchRequest,
    service: RetrievalServiceDep,
):
    return await service.search(
        workspace_id=workspace.id,
        query=request.query,
        limit=request.limit,
    )


@document_router.get("/operations/{document_id}")
async def get_document_operation(
    document_id: int,
    workspace: CurrentWorkSpaceDep,
    repository: DocumentOperationRepositoryDep
):
    operation = await repository.get(
        document_id=document_id,
        workspace_id=workspace.id,
    )

    if operation is None:
        raise AppException("작업을 찾을 수 없습니다.", 404)

    if operation.reason == "USER_DELETE":
        status = (
            "deleted"
            if operation.status == OperationStatus.DELETED
            else "deleting"
        )
        message = None

    elif operation.reason is not None:
        status = "failed"
        message = "문서 처리에 실패했습니다."

    else:
        status = operation.status
        message = None

    return {
        "document_id": operation.document_id,
        "title": operation.title,
        "status": status,
        "message": message,
        "cleanup_pending": (
            operation.status == OperationStatus.CLEANUP_REQUIRED
        ),
    }