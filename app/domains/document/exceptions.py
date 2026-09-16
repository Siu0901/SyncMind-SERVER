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