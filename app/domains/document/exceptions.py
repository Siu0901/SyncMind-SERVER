from app.core.exception.exceptions import AppException


class DocumentNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            "Document not found",
            404,
        )


class DocumentVersionNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            "Document version not found",
            404,
        )


class DocumentNotReadyError(AppException):
    def __init__(self):
        super().__init__(
            "Document is not ready",
            409,
        )


class DuplicateDocumentError(AppException):
    def __init__(self):
        super().__init__(
            message="이미 동일한 문서가 존재합니다.",
            status_code=409,
        )