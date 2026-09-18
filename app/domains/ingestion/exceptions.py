from app.core.exception.exceptions import AppException


class CorruptedPdfError(AppException):
    def __init__(self):
        super().__init__(
            "Invalid or corrupted PDF file",
            400,
        )


class EncryptedPdfError(AppException):
    def __init__(self):
        super().__init__(
            "Password-protected PDF is not supported",
            400,
        )


class EmptyPdfError(AppException):
    def __init__(self):
        super().__init__(
            "PDF has no pages",
            400,
        )


class PdfTooLargeError(AppException):
    def __init__(self, page_count: int, max_pages: int):
        super().__init__(
            f"PDF has too many pages ({page_count} > {max_pages})",
            413,
        )
        self.page_count = page_count
        self.max_pages = max_pages


class NoTextLayerError(AppException):
    def __init__(self, page_count: int, skipped_pages: int = 0):
        super().__init__(
            f"No extractable text found in PDF ({skipped_pages}/{page_count} pages skipped)",
            422,
        )
        self.page_count = page_count
        self.skipped_pages = skipped_pages


class PdfParseError(AppException):
    def __init__(self, reason: str | None = None):
        message = "Failed to extract content from PDF"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(
            message,
            422,
        )