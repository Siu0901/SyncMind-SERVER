from typing import Optional

from app.domains.ingestion.schema import ParsedDocument
from app.domains.ingestion.parsers import (
    pdf, markdown, text, docx, html
)


class DocumentParser:
    @staticmethod
    def parse(
        mime_type: Optional[str],
        data: bytes,
    ) -> ParsedDocument:
        # 일단은 pdf 하고 마크다운, text 파일만 하자
        if mime_type == "application/pdf":
            return pdf.PDFParser().parse(data)

        if mime_type == "text/plain":
            return text.TextParser().parse(data)

        if mime_type in {
            "text/markdown",
            "text/x-markdown",
        }:
            return markdown.MarkdownParser().parse(data)

        raise ValueError(f"Unsupported mime type: {mime_type}")