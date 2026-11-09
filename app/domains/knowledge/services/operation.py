from app.core.exception.exceptions import AppException
from app.domains.knowledge.operation.model import OperationStatus
from app.domains.knowledge.operation.repository import (
    DocumentOperationRepository,
)
from app.domains.knowledge.schema import DocumentOperationResponse


class DocumentOperationService:
    def __init__(self, repository: DocumentOperationRepository):
        self.repository = repository

    async def get_operation(
        self,
        document_id: int,
        workspace_id: int,
    ) -> DocumentOperationResponse:
        operation = await self.repository.get(
            document_id=document_id,
            workspace_id=workspace_id,
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

        return DocumentOperationResponse(
            document_id=operation.document_id,
            title=operation.title,
            status=status,
            message=message,
            cleanup_pending=(
                operation.status == OperationStatus.CLEANUP_REQUIRED
            ),
        )
